"""Read-only Core review before an isolated install from retained wheel bytes.

This module chooses a stable, new destination and checks aggregate installation
targets. It does not create an environment or grant installed-package authority.
"""

from __future__ import annotations

import configparser
from email.parser import BytesParser
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import stat
import sys
import sysconfig
from typing import Any, Mapping
import zipfile

from .environment_package_import import reopen_package_import
from .environment_reconstruction import ReconstructionError, _canonical, _seal
from .environment_resolution import resolve_environment
from .host_filesystem import private_path


FORMAT = "workbench-environment-package-install-preflight-v1"
_SUPPORTED_FILESYSTEMS = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs"})
_SCRIPT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_MAX_TARGETS = 250_000
_MAX_LAUNCHERS = 4096
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024


def _executable(path_text: str) -> dict[str, Any]:
    original = Path(path_text)
    if not original.is_absolute():
        raise ReconstructionError("package install needs an absolute executing Python")
    try:
        path = original.resolve(strict=True)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= _MAX_EXECUTABLE_BYTES:
            raise ReconstructionError("executing Python is not a bounded ordinary file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if not stat.S_ISREG(opened.st_mode) or any(
                getattr(opened, field) != getattr(before, field) for field in fields
            ):
                raise ReconstructionError("executing Python changed before review")
            digest = sha256()
            size = 0
            while block := os.read(descriptor, 1024 * 1024):
                size += len(block)
                if size > _MAX_EXECUTABLE_BYTES:
                    raise ReconstructionError("executing Python exceeds the review bound")
                digest.update(block)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        visible = path.stat()
        if (original.resolve(strict=True) != path or size != before.st_size
                or any(getattr(before, field) != getattr(after, field)
                       or getattr(before, field) != getattr(visible, field)
                       for field in fields)):
            raise ReconstructionError("executing Python changed during review")
    except OSError as exc:
        raise ReconstructionError(f"executing Python cannot be reviewed: {exc}") from exc
    return {"invocation": str(original), "realpath": str(path),
            "sha256": "sha256:" + digest.hexdigest(), "size": size}


def _interpreter() -> dict[str, Any]:
    current = _executable(sys.executable)
    base = _executable(getattr(sys, "_base_executable", None) or sys.executable)
    return {
        "executing": current, "base": base,
        "python": ".".join(map(str, sys.version_info[:3])),
        "cache_tag": sys.implementation.cache_tag,
        "platform": sys.platform, "machine": platform.machine(),
        "sysconfig_platform": sysconfig.get_platform(),
    }


def _mount_type(path: Path) -> str | None:
    """Find the most specific mounted filesystem for an existing Linux parent."""

    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    selected: tuple[int, str] | None = None
    for line in lines:
        left, separator, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if not separator or len(fields) < 5 or not tail:
            continue
        mountpoint = re.sub(
            r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4],
        )
        mount = Path(mountpoint)
        if path == mount or path.is_relative_to(mount):
            score = len(mount.parts)
            if selected is None or score > selected[0]:
                selected = score, tail[0]
    return None if selected is None else selected[1]


def _scheme(destination: Path) -> dict[str, Path]:
    values = {key: str(destination) for key in
              ("base", "platbase", "installed_base", "installed_platbase")}
    try:
        paths = sysconfig.get_paths(scheme="venv", vars=values)
    except (KeyError, ValueError) as exc:
        raise ReconstructionError(f"executing Python has no supported venv scheme: {exc}") from exc
    expected = {
        "purelib": destination / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
        "platlib": destination / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
        "scripts": destination / "bin",
    }
    if any(Path(paths.get(key, "")) != value for key, value in expected.items()):
        raise ReconstructionError("executing Python has an unqualified isolated install layout")
    return expected


def _wheel_targets(wheelhouse: Path, rows: list[dict[str, Any]],
                   destination: Path, paths: Mapping[str, Path]) -> dict[str, Any]:
    files: dict[str, str] = {}
    directories: set[str] = set()
    launchers: list[dict[str, str]] = []
    member_count = 0

    def relative_target(path: Path) -> str:
        if not path.is_relative_to(destination):
            raise ReconstructionError("wheel target escapes the isolated environment")
        return path.relative_to(destination).as_posix()

    def add_directory(path: Path) -> None:
        while path != destination:
            key = relative_target(path)
            if key in files:
                raise ReconstructionError(f"wheel directory collides with a file: {key}")
            directories.add(key)
            path = path.parent

    def add_file(path: Path, owner: str) -> None:
        key = relative_target(path)
        if key in files or key in directories:
            raise ReconstructionError(f"wheel installation target collides: {key}")
        parent = path.parent
        while parent != destination:
            add_directory(parent)
            parent = parent.parent
        files[key] = owner
        if len(files) > _MAX_TARGETS:
            raise ReconstructionError("wheelhouse exceeds the aggregate target bound")

    for row in sorted(rows, key=lambda item: item["name"]):
        wheel = wheelhouse / "wheels" / row["filename"]
        try:
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                scheme_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
                if len(scheme_names) != 1 or archive.getinfo(scheme_names[0]).file_size > 65536:
                    raise ReconstructionError("wheel has no bounded installation scheme")
                metadata_root = scheme_names[0].rsplit("/", 1)[0]
                wheel_header = BytesParser().parsebytes(archive.read(scheme_names[0]))
                purelib = wheel_header.get_all("Root-Is-Purelib", [])
                if len(purelib) != 1 or purelib[0].lower() not in {"true", "false"}:
                    raise ReconstructionError("wheel has an invalid installation scheme")
                root = paths["purelib" if purelib[0].lower() == "true" else "platlib"]
                data_root = metadata_root.removesuffix(".dist-info") + ".data"
                for member in archive.infolist():
                    member_count += 1
                    if member_count > _MAX_TARGETS:
                        raise ReconstructionError("wheelhouse exceeds the aggregate member bound")
                    raw = member.filename
                    trimmed = raw[:-1] if member.is_dir() and raw.endswith("/") else raw
                    parts = trimmed.split("/")
                    if (not trimmed or trimmed.startswith("/") or "\\" in trimmed
                            or ":" in trimmed or "\0" in trimmed
                            or any(part in {"", ".", ".."} for part in parts)
                            or any(part == "__pycache__" for part in parts)
                            or trimmed.endswith((".pyc", ".pyo"))
                            or stat.S_ISLNK(member.external_attr >> 16)):
                        raise ReconstructionError("wheel has a noncanonical installation member")
                    if parts[0].endswith(".data"):
                        if parts[0] != data_root:
                            raise ReconstructionError("wheel has another owner's data scheme")
                        if member.is_dir() and len(parts) < 3:
                            continue
                        if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                            raise ReconstructionError("wheel needs an unsupported data installation scheme")
                        target = paths[parts[1]].joinpath(*parts[2:])
                    else:
                        if parts[0].endswith(".dist-info") and parts[0] != metadata_root:
                            raise ReconstructionError("wheel contains another metadata owner")
                        target = root.joinpath(*parts)
                    if member.is_dir():
                        add_directory(target)
                    else:
                        add_file(target, row["name"])
                entry_name = metadata_root + "/entry_points.txt"
                if entry_name in names:
                    if archive.getinfo(entry_name).file_size > 65536:
                        raise ReconstructionError("wheel entry points exceed the review bound")
                    parser = configparser.ConfigParser(interpolation=None, strict=True)
                    parser.optionxform = str
                    parser.read_string(archive.read(entry_name).decode("utf-8"))
                    if parser.defaults():
                        raise ReconstructionError("wheel entry points use unsupported defaults")
                    for group in ("console_scripts", "gui_scripts"):
                        if not parser.has_section(group):
                            continue
                        for name in parser[group]:
                            if (_SCRIPT_NAME.fullmatch(name) is None
                                    or name in {"python", "python3", f"python{sys.version_info.major}.{sys.version_info.minor}",
                                                "activate", "activate.csh", "activate.fish", "Activate.ps1"}
                                    or (name in {"pip", "pip3", f"pip{sys.version_info.major}.{sys.version_info.minor}"}
                                        and row["name"] != "pip")
                                    or (name == "workbench" and row["name"] != "workbench-core")
                                    or (name == "workbench-tui" and row["name"] != "workbench-tui")):
                                raise ReconstructionError("wheel declares a reserved or unsafe launcher")
                            add_file(paths["scripts"] / name, row["name"])
                            launchers.append({"name": name, "owner": row["name"], "group": group})
                            if len(launchers) > _MAX_LAUNCHERS:
                                raise ReconstructionError("wheelhouse exceeds the launcher review bound")
        except (OSError, ValueError, UnicodeError, configparser.Error, zipfile.BadZipFile, KeyError) as exc:
            if isinstance(exc, ReconstructionError):
                raise
            raise ReconstructionError(f"wheel installation targets cannot be reviewed: {exc}") from exc
    if set(files) & directories:
        raise ReconstructionError("wheel files and directories collide")
    inventory = [{"target": key, "owner": files[key]} for key in sorted(files)]
    return {
        "wheel_count": len(rows), "member_count": member_count,
        "file_target_count": len(files), "directory_target_count": len(directories),
        "launchers": sorted(launchers, key=lambda row: (row["name"], row["owner"], row["group"])),
        "target_inventory_sha256": "sha256:" + sha256(_canonical(inventory)).hexdigest(),
    }


def plan_package_install_preflight(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review an absent stable destination and every retained wheel target."""

    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    if not sys.platform.startswith("linux") or os.name != "posix":
        raise ReconstructionError("isolated install preflight supports Linux/WSL only")
    retained = reopen_package_import(
        suite_root, share, candidate, closure_plan, workspace=local.workspace,
        result_resource_id=package_result_resource_id, environment=values,
    )
    reviewed = dict(closure_plan)
    if (retained["closure_plan_id"] != reviewed["plan_id"]
            or retained["unresolved_inputs"] != reviewed["unresolved_inputs"]
            or "optional-module-packages" not in retained["unresolved_inputs"]):
        raise ReconstructionError("retained package result has another review scope")
    interpreter = _interpreter()
    if (reviewed["target"] != {
            "python": ".".join(map(str, sys.version_info[:2])),
            "platform": sys.platform, "machine": platform.machine(),
    }):
        raise ReconstructionError("retained package closure targets another interpreter or host")
    install_root = local.locations["evidence"]
    if not install_root.is_absolute() or any(
        part.is_symlink() or getattr(part, "is_junction", lambda: False)()
        for part in (install_root, *install_root.parents)
    ):
        raise ReconstructionError("isolated install evidence root traverses a redirect")
    blockers: list[str] = []
    if not private_path(install_root, directory=True):
        blockers.append("Core evidence root is not an existing owner-private directory")
    elif not os.access(install_root, os.W_OK | os.X_OK):
        blockers.append("Core evidence root cannot create a private isolated destination")
    filesystem = _mount_type(install_root) if install_root.is_dir() else None
    if filesystem not in _SUPPORTED_FILESYSTEMS:
        blockers.append("Core evidence root has an unqualified Linux/WSL filesystem")
    destination_key = sha256(_canonical({
        "closure_plan_id": reviewed["plan_id"],
        "base_interpreter": interpreter["base"],
    })).hexdigest()
    destination = install_root / ("environment-" + destination_key)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ReconstructionError(f"isolated install destination cannot be inventoried: {exc}") from exc
    else:
        blockers.append("stable isolated install destination already exists; review recovery before reuse")
    paths = _scheme(destination)
    targets = _wheel_targets(Path(retained["tree_path"]), reviewed["wheels"], destination, paths)
    reopened = reopen_package_import(
        suite_root, share, candidate, closure_plan, workspace=local.workspace,
        result_resource_id=package_result_resource_id, environment=values,
    )
    if reopened != retained:
        raise ReconstructionError("retained package tree changed during install preflight")
    return _seal({
        "format": FORMAT, "schema_version": 1,
        "share_id": retained["share_id"], "candidate_id": retained["candidate_id"],
        "closure_plan_id": reviewed["plan_id"],
        "package_result_resource_id": package_result_resource_id,
        "package_tree_id": retained["tree_id"],
        "package_tree_content_sha256": retained["tree_content_sha256"],
        "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "state_root": str(local.state_root), "install_root": str(install_root),
        "filesystem": filesystem,
        "destination": str(destination),
        "installation_paths": {key: str(value) for key, value in paths.items()},
        "interpreter": interpreter, "targets": targets,
        "restart_policy": "stable-direct-destination; existing-path-requires-reviewed-recovery",
        "unresolved_inputs": retained["unresolved_inputs"],
        "coverage": "read-only-isolated-install-preflight-only",
        "blockers": blockers, "state": "blocked" if blockers else "reviewed",
    }, "workbench-environment-package-install-preflight", "plan_id")


__all__ = ["plan_package_install_preflight"]
