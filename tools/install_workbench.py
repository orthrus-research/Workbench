#!/usr/bin/env python3
"""Install a trusted native wheelhouse into a new dedicated environment.

No elevated privileges, source checkout, shell profile changes, package index,
or existing-environment mutation. Failed installs are retained for inspection.
Use the returned executable path directly; publish only reviewed assemblies.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from verify_wheelhouse import verify, WheelhouseError


def _windows_long_paths_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def _check_windows_paths(wheelhouse, destination, manifest):
    """Refuse an impossible legacy-Windows destination before creating it."""
    if _windows_long_paths_enabled():
        return
    for row in manifest["wheels"]:
        try:
            with zipfile.ZipFile(wheelhouse / "wheels" / row["filename"]) as archive:
                for name in archive.namelist():
                    target = destination / "Lib" / "site-packages" / Path(name)
                    candidates = [target]
                    if target.suffix == ".py":
                        candidates.append(target.parent / "__pycache__" /
                                          f"{target.stem}.{sys.implementation.cache_tag}.pyc")
                    if any(len(str(path).encode("utf-16-le")) // 2 >= 260 for path in candidates):
                        raise WheelhouseError(
                            "installation destination is too long for this Windows configuration: "
                            "a bundled file would exceed the current path limit; choose a shorter "
                            "destination or enable Windows long-path support")
        except zipfile.BadZipFile as exc:
            raise WheelhouseError("cannot inspect installation paths in an invalid wheel") from exc


def install(wheelhouse: Path, destination: Path, *, command_runner=None):
    run_command = command_runner or subprocess.run
    wheelhouse = wheelhouse.absolute()
    manifest = verify(wheelhouse)
    # Verification admits the one supported bootstrap before any destination
    # directory is created; the private copy is verified again before use.
    pip_filename = next(row["filename"] for row in manifest["wheels"] if row["name"] == "pip")
    expected = {"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": sys.platform, "machine": platform.machine()}
    if manifest["target"] != expected:
        raise WheelhouseError("wheelhouse target differs from this Python/OS/architecture")
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise WheelhouseError("supported Python versions are 3.12 through 3.14")
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise WheelhouseError("destination must be new; existing environments and user data are never overwritten")
    if any(path.is_symlink() for path in destination.parents):
        raise WheelhouseError("destination parents must not be symlinks")
    if os.name == "nt":
        _check_windows_paths(wheelhouse, destination, manifest)
    destination.mkdir(parents=True)
    receipt = destination / "workbench-install.json"
    manifest_identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    state = {"format": "workbench-native-install-v1", "state": "installing", "target": expected, "native_versions": manifest["native_versions"], "wheelhouse_manifest_sha256": manifest_identity}
    receipt.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        venv.EnvBuilder(with_pip=False, clear=False, symlinks=False).create(destination)
        python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "PIP_"))}
        # --isolated ignores user settings, but pip still reads global/site
        # configuration unless its special config-file override is disabled.
        environment["PIP_CONFIG_FILE"] = os.devnull
        with tempfile.TemporaryDirectory(prefix="workbench-install-") as temporary:
            private = Path(temporary) / "wheelhouse"
            # Copy only admitted paths then verify the exact copied bytes before
            # pip can see them. Source changes cannot redirect the installation.
            (private / "wheels").mkdir(parents=True)
            for name in ("wheelhouse.json", "requirements.lock"):
                shutil.copyfile(wheelhouse / name, private / name)
            for row in manifest["wheels"]:
                shutil.copyfile(wheelhouse / "wheels" / row["filename"], private / "wheels" / row["filename"])
            if verify(private) != manifest:
                raise WheelhouseError("source assembly changed during admission")
            bootstrap = private / "wheels" / pip_filename
            run_command([str(python), "-I", "-c", "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')", str(bootstrap), "--isolated", "install", "--no-index", "--only-binary=:all:", "--require-hashes", "--find-links", str(private / "wheels"), "-r", str(private / "requirements.lock")], env=environment, check=True)
        run_command([str(python), "-I", "-m", "pip", "--isolated", "check"], env=environment, check=True)
        executable = destination / ("Scripts/workbench.exe" if os.name == "nt" else "bin/workbench")
        if "workbench-core" in manifest["native_versions"]:
            run_command([str(executable), "version", "--json"], env=environment, check=True)
        tui_executable = destination / ("Scripts/workbench-tui.exe" if os.name == "nt" else "bin/workbench-tui")
        if "workbench-tui" in manifest["native_versions"]:
            if not tui_executable.is_file():
                raise WheelhouseError("installed Textual client has no workbench-tui launcher")
            # Argument parsing is noninteractive and does not launch the TUI or
            # read user settings; it also verifies the installed import closure.
            run_command([str(tui_executable), "--help"], env=environment, check=True)
        state.update(
            state="installed",
            executable=str(executable) if executable.exists() else None,
            tui_executable=str(tui_executable) if "workbench-tui" in manifest["native_versions"] else None,
        )
        receipt.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return state
    except BaseException:
        state["state"] = "failed"
        receipt.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, help="new diagnostic directory when running from the source tooling")
    args = parser.parse_args(argv)
    try:
        if args.diagnostics is None:
            result = install(args.wheelhouse, args.destination)
        else:
            # Optional: exported standalone installers remain standard-library
            # only and do not require the repository's diagnostic helper.
            from validation_diagnostics import DiagnosticRun
            with DiagnosticRun(args.diagnostics, "native-install", ("install",)) as diagnostics:
                with diagnostics.phase("install"):
                    result = install(args.wheelhouse, args.destination,
                                     command_runner=lambda command, **options: diagnostics.command(command, cwd=Path.cwd(), env=options.get("env"), timeout=600))
                diagnostics.document["metadata"].update(result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"native install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
