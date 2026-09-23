#!/usr/bin/env python3

"""Validate the canonical IDE clients with locked tools."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

from provision_ide_toolchains import LOCK_PATH, ProvisionFailure, provision, provision_node, provision_npm


ROOT = Path(__file__).resolve().parents[1]
VSCODE_ROOT = ROOT / "clients/vscode"
INTELLIJ_ROOT = ROOT / "clients/intellij-community"
sys.path.insert(0, str(ROOT / "tools"))
from build_release_clients import (  # noqa: E402
    VSCODE_ARTIFACT_NAME,
    VSCODE_STAGE_FILES,
    verify_vscode,
)
from validation_diagnostics import DiagnosticRun, default_directory  # noqa: E402
from native_distribution import source_identity  # noqa: E402

DIAGNOSTICS = None
_CLIENT_DIAGNOSTICS = threading.local()


def current_diagnostics():
    return getattr(_CLIENT_DIAGNOSTICS, "run", DIAGNOSTICS)


class IdeValidationFailure(RuntimeError):
    pass


def capture(command: list[str], *, env: dict[str, str] | None = None) -> str:
    diagnostics = current_diagnostics()
    if diagnostics is not None:
        result = diagnostics.command(command, cwd=ROOT, env=env)
        return result.stdout + result.stderr
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode:
        raise IdeValidationFailure(
            f"command failed with exit {completed.returncode}: "
            + " ".join(command)
            + "\n"
            + completed.stdout
        )
    return completed.stdout


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> None:
    diagnostics = current_diagnostics()
    if diagnostics is not None:
        diagnostics.command(command, cwd=cwd, env=env, timeout=1800)
        return
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise IdeValidationFailure(
            f"command failed with exit {completed.returncode}: "
            + " ".join(command)
        )


def require_version(executable: str, expected: str) -> None:
    resolved = shutil.which(executable)
    if resolved is None:
        raise IdeValidationFailure(f"required executable is unavailable: {executable}")
    output = capture([resolved, "--version"]).strip()
    actual = output.removeprefix("v")
    if actual != expected:
        raise IdeValidationFailure(
            f"{executable} version {actual!r} does not match locked {expected!r}"
        )


def run_vscode_extension_host(environment: dict[str, str], *, npm_command=None, installed_core=None,
                              shared_workspace=None, shared_state_root=None) -> None:
    """Build the exact public VSIX boundary, then exercise its host matrix."""

    vsce = VSCODE_ROOT / "node_modules/.bin/vsce"
    if not vsce.is_file():
        raise IdeValidationFailure("locked VSCE executable was not installed")
    staging_root = ROOT / ".workbench/tmp/ide-validation-v1"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="workbench-vscode-stage-", dir=staging_root
    ) as directory:
        stage = Path(directory)
        for name in VSCODE_STAGE_FILES:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(VSCODE_ROOT / name, destination)
        shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE")
        shutil.copyfile(ROOT / "NOTICE.md", stage / "NOTICE.md")
        artifact = stage / VSCODE_ARTIFACT_NAME
        run(
            [
                str(vsce),
                "package",
                "--no-dependencies",
                "--allow-missing-repository",
                "--out",
                str(artifact),
            ],
            cwd=stage,
            env=environment,
        )
        try:
            verify_vscode(artifact)
        except Exception as error:
            raise IdeValidationFailure(
                f"the validation VSIX failed the public package boundary: {error}"
            ) from error
        arguments = [*(npm_command or ["npm"]), "run", "test:integration", "--", str(artifact.resolve(strict=True))]
        if installed_core is not None:
            arguments.append(str(installed_core))
        if shared_workspace is not None:
            arguments.extend([str(shared_workspace), str(shared_state_root)])
        host_environment = dict(environment)
        diagnostics = current_diagnostics()
        if diagnostics is not None:
            host_environment["WORKBENCH_TEST_MATRIX_RESULT"] = str(diagnostics.directory / "vscode-host-matrix.json")
        run(
            arguments,
            cwd=VSCODE_ROOT,
            env=host_environment,
        )


def validate_vscode(
    lock: dict[str, object],
    *,
    full: bool,
    non_adversarial: bool,
    environment: dict[str, str] | None = None,
    node: Path | None = None,
    npm_cli: Path | None = None,
    installed_core: Path | None = None,
    shared_workspace: Path | None = None,
    shared_state_root: Path | None = None,
) -> None:
    npm_command = [str(node), str(npm_cli)] if node and npm_cli else ["npm"]
    if node and npm_cli:
        for command, expected in (([str(node)], lock["node"]["version"]), (npm_command, lock["npm"]["version"])):
            if capture([*command, "--version"], env=environment).strip().removeprefix("v") != expected:
                raise IdeValidationFailure("provisioned Node/npm version differs from the toolchain lock")
    else:
        require_version("node", str(lock["node"]["version"]))
        require_version("npm", str(lock["npm"]["version"]))
    run([*npm_command, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=VSCODE_ROOT, env=environment)
    if not non_adversarial:
        run([*npm_command, "test"], cwd=VSCODE_ROOT, env=environment)
    run([*npm_command, "run", "test:package"], cwd=VSCODE_ROOT, env=environment)
    if full and not non_adversarial:
        run_vscode_extension_host(environment or os.environ.copy(), npm_command=npm_command,
                                  installed_core=installed_core, shared_workspace=shared_workspace,
                                  shared_state_root=shared_state_root)


def validate_intellij(
    lock: dict[str, object],
    *,
    java_home: Path,
    java_platform_home: Path,
    gradle_home: Path,
    full: bool,
    non_adversarial: bool,
    environment: dict[str, str] | None = None,
) -> None:
    environment = dict(environment) if environment is not None else os.environ.copy()
    environment["JAVA_HOME"] = str(java_home)
    environment["GRADLE_USER_HOME"] = str(ROOT / ".workbench/gradle-home/ide-validation-v1")
    environment["PATH"] = str(java_home / "bin") + os.pathsep + environment["PATH"]

    java_output = capture([str(java_home / "bin/java"), "-version"], env=environment)
    java = lock["java"]
    if str(java["version"]) not in java_output or str(java["runtime_build"]) not in java_output:
        raise IdeValidationFailure("provisioned Java does not match the IDE toolchain lock")

    java_platform_output = capture(
        [str(java_platform_home / "bin/java"), "-version"], env=environment
    )
    java_platform = lock["java_platform"]
    if (
        str(java_platform["version"]) not in java_platform_output
        or str(java_platform["runtime_build"]) not in java_platform_output
    ):
        raise IdeValidationFailure(
            "provisioned platform Java does not match the IDE toolchain lock"
        )

    gradle = lock["gradle"]
    executable = gradle_home / "bin/gradle"
    gradle_output = capture([str(executable), "--version"], env=environment)
    if f"Gradle {gradle['version']}" not in gradle_output:
        raise IdeValidationFailure("provisioned Gradle does not match the IDE toolchain lock")

    tasks = (
        ["compileJava", "compileTestJava", "buildPlugin", "verifyPluginStructure"]
        if non_adversarial
        else ["unitTest", "buildPlugin", "verifyPluginStructure"]
    )
    if full:
        tasks.append("verifyPlugin")
    run(
        [
            str(executable),
            "--no-daemon",
            "--console=plain",
            "--stacktrace",
            f"-Dorg.gradle.java.installations.paths={java_platform_home}",
            "-Dorg.gradle.java.installations.auto-detect=false",
            *tasks,
        ],
        cwd=INTELLIJ_ROOT,
        env=environment,
    )

def client_environment(root: Path, environment: dict[str, str]) -> dict[str, str]:
    result = dict(environment)
    for variable, name in (("WORKBENCH_CONFIG_HOME", "config"), ("XDG_CONFIG_HOME", "config"),
                           ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                           ("TMPDIR", "tmp"), ("TEMP", "tmp"), ("TMP", "tmp")):
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        result[variable] = str(path)
    return result


def run_clients(diagnostics, checks, *, jobs):
    """Keep each client's logs, process ownership and phase nesting separate."""
    children = {name: DiagnosticRun(diagnostics.directory / name, name, ("checks",)) for name in checks}
    diagnostics.document["client_reports"] = {name: f"{name}/report.json" for name in children}

    def execute(name, check):
        child = children[name]
        with diagnostics.phase(name), child:
            _CLIENT_DIAGNOSTICS.run = child
            try:
                with child.phase("checks"):
                    check()
            finally:
                del _CLIENT_DIAGNOSTICS.run

    executor = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="ide-client")
    try:
        futures = [executor.submit(execute, name, check) for name, check in checks.items()]
        for future in as_completed(futures):
            future.result()
    except BaseException:
        for child in children.values():
            child.cancel()
        raise
    finally:
        executor.shutdown(wait=True)


def main(argv=None) -> int:
    global DIAGNOSTICS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "also run the JetBrains Plugin Verifier and, unless paired with "
            "--non-adversarial, the VS Code Extension Host"
        ),
    )
    parser.add_argument(
        "--non-adversarial",
        action="store_true",
        help=(
            "compile, package, and verify without executing unit, integration, "
            "simulation, mutation, fuzz, or other negative-path suites"
        ),
    )
    parser.add_argument("--diagnostics", type=Path, help="new directory for bounded phase logs")
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1, help="run the two isolated IDE clients sequentially or in parallel")
    parser.add_argument("--installed-core", type=Path, help="exercise the installed Core workspace/session journey in both VS Code hosts")
    parser.add_argument("--shared-workspace", type=Path)
    parser.add_argument("--shared-state-root", type=Path)
    args = parser.parse_args(argv)
    if args.installed_core and (not args.full or args.non_adversarial):
        parser.error("--installed-core requires full adversarial host validation")
    if (args.shared_workspace is None) != (args.shared_state_root is None) or (args.shared_workspace and not args.installed_core):
        parser.error("shared qualification requires workspace, state root and installed Core")

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    source = source_identity(ROOT)
    try:
        with DiagnosticRun(args.diagnostics or default_directory(ROOT, "ide"), "ide",
                           ("provision", "vscode", "intellij"), metadata={"toolchain_lock": lock, "full": args.full,
                            "non_adversarial": args.non_adversarial, "installed_core_journey": args.installed_core is not None,
                            "source_sha256": source, "jobs": args.jobs}) as diagnostics:
            DIAGNOSTICS = diagnostics
            with diagnostics.phase("provision"):
                java_home, java_platform_home, gradle_home = provision()
                node_home, npm_home = provision_node(), provision_npm()
            environment = os.environ.copy()
            environment["PATH"] = str(node_home / "bin") + os.pathsep + environment.get("PATH", "")
            with tempfile.TemporaryDirectory(prefix="workbench-ide-clients-") as temporary:
                vscode_environment = client_environment(Path(temporary) / "vscode", environment)
                intellij_environment = client_environment(Path(temporary) / "intellij", environment)
                checks = {
                    "vscode": lambda: validate_vscode(lock, full=args.full, non_adversarial=args.non_adversarial,
                                    environment=vscode_environment, node=node_home / "bin/node", npm_cli=npm_home / "bin/npm-cli.js",
                                    installed_core=args.installed_core, shared_workspace=args.shared_workspace,
                                    shared_state_root=args.shared_state_root),
                    "intellij": lambda: validate_intellij(lock, java_home=java_home, java_platform_home=java_platform_home,
                                      gradle_home=gradle_home, full=args.full, non_adversarial=args.non_adversarial,
                                      environment=intellij_environment),
                }
                run_clients(diagnostics, checks, jobs=args.jobs)
            if source_identity(ROOT) != source:
                raise IdeValidationFailure("validation source changed during IDE checks")
    finally:
        DIAGNOSTICS = None
    label = (
        "non-adversarial IDE validation"
        if args.non_adversarial
        else "IDE validation"
    )
    print(f"{label} passed.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IdeValidationFailure, ProvisionFailure) as exc:
        print(f"IDE VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
