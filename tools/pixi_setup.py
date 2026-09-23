#!/usr/bin/env python3

"""Attest Workbench setup inputs and a post-launch, custody-staged Pixi probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
for _source in ("api/src", "core/src"):
    sys.path.insert(0, str(ROOT / _source))
SOURCE_ROOTS = (
    "api/src",
    "core/src",
    "modules/worldgen-qualifier/src",
    "modules/worldgen-cockpit/src",
    "modules/subsurface-studio/src",
    "modules/runtime-explorer/src",
    "modules/pack-program-studio/src",
    "modules/process-studio/src",
    "modules/workbench-shell/src",
    "modules/project-intelligence/src",
    "modules/crucible/src",
    "modules/blueprints/src",
    "modules/atlas/src",
)
for relative in SOURCE_ROOTS:
    source = ROOT / relative
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_core.pixi_config_guard import (  # noqa: E402
    PixiProjectConfigError,
    require_project_local_config_absent,
)
from workbench_core.executable_custody import (  # noqa: E402
    ExecutableCustodyError,
    stage_executable,
)
from workbench_core.pixi_lock import (  # noqa: E402
    PixiLockError,
    bind_pixi_inputs,
    build_environment_closure_from_bound_inputs,
    parse_pixi_lock_bytes,
    parse_pixi_manifest_bytes,
)


class PixiSetupError(ValueError):
    """Raised when the Pixi environment evidence cannot be verified for use."""


_EXACT_VERSION = re.compile(r"==([0-9]+(?:\.[0-9]+){2})")
_PIXI_VERSION_LINE = re.compile(
    r"^pixi (?P<version>[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?)$"
)
_MAX_PIXI_EXECUTABLE_BYTES = 256 * 1024 * 1024
_PLATFORM_TARGETS = {
    ("linux", "x86_64"): ("linux-x86-64", "linux-64"),
    ("linux", "amd64"): ("linux-x86-64", "linux-64"),
    ("linux", "aarch64"): ("linux-arm64", "linux-aarch64"),
    ("linux", "arm64"): ("linux-arm64", "linux-aarch64"),
    ("windows", "x86_64"): ("windows-x86-64", "win-64"),
    ("windows", "amd64"): ("windows-x86-64", "win-64"),
    ("windows", "aarch64"): ("windows-arm64", "win-arm64"),
    ("windows", "arm64"): ("windows-arm64", "win-arm64"),
    ("darwin", "x86_64"): ("macos-x86-64", "osx-64"),
    ("darwin", "amd64"): ("macos-x86-64", "osx-64"),
    ("darwin", "aarch64"): ("macos-arm64", "osx-arm64"),
    ("darwin", "arm64"): ("macos-arm64", "osx-arm64"),
}


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PixiSetupError(f"{label} must be a table/object")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if (
        type(value) is not list
        or not value
        or any(type(item) is not str for item in value)
    ):
        raise PixiSetupError(f"{label} must be a non-empty string list")
    return value


def _input_record(path: Path, payload: bytes) -> dict[str, Any]:
    """Describe bytes already retained by the shared custody reader."""

    digest = hashlib.sha256(payload).hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "identity": f"sha256:{digest}",
        "size": len(payload),
    }


def _parse_manifest(payload: bytes) -> dict[str, Any]:
    try:
        return _object(parse_pixi_manifest_bytes(payload), "pixi.toml")
    except PixiLockError as exc:
        raise PixiSetupError(f"repository pixi.toml is invalid: {exc}") from exc


def _parse_lock(payload: bytes) -> dict[str, Any]:
    try:
        return _object(parse_pixi_lock_bytes(payload), "pixi.lock")
    except PixiLockError as exc:
        raise PixiSetupError(f"repository pixi.lock is invalid: {exc}") from exc


def _exact_version(requirement: object, label: str) -> str:
    if type(requirement) is not str:
        raise PixiSetupError(f"{label} must be an exact version requirement")
    matched = _EXACT_VERSION.fullmatch(requirement)
    if matched is None:
        raise PixiSetupError(f"{label} must use the exact `==X.Y.Z` form")
    return matched.group(1)


def _environment_contract(
    manifest: dict[str, Any],
    environment_name: str,
) -> dict[str, Any]:
    workspace = _object(manifest.get("workspace"), "pixi.toml [workspace]")
    project_name = workspace.get("name")
    if type(project_name) is not str or not project_name:
        raise PixiSetupError("pixi.toml [workspace].name must be a non-empty string")
    requires_pixi = workspace.get("requires-pixi")
    _exact_version(requires_pixi, "pixi.toml [workspace].requires-pixi")

    environments = _object(manifest.get("environments", {}), "pixi.toml [environments]")
    if environment_name == "default":
        environment = {}
    else:
        if environment_name not in environments:
            raise PixiSetupError(
                "Pixi environment is not declared by this repository: "
                + environment_name
            )
        environment = _object(
            environments[environment_name],
            f"pixi.toml environment {environment_name!r}",
        )

    features_value = environment.get("features", [])
    if type(features_value) is not list or any(
        type(item) is not str for item in features_value
    ):
        raise PixiSetupError(
            f"pixi.toml environment {environment_name!r} features must be a string list"
        )
    feature_names = list(features_value)
    no_default_feature = environment.get("no-default-feature", False)
    if type(no_default_feature) is not bool:
        raise PixiSetupError(
            f"pixi.toml environment {environment_name!r} "
            "no-default-feature must be boolean"
        )

    feature_table = _object(manifest.get("feature", {}), "pixi.toml [feature]")
    selected_features: list[tuple[str, dict[str, Any]]] = []
    for feature_name in feature_names:
        if feature_name not in feature_table:
            raise PixiSetupError(
                f"Pixi environment {environment_name!r} selects unknown feature "
                f"{feature_name!r}"
            )
        selected_features.append(
            (
                feature_name,
                _object(
                    feature_table[feature_name],
                    f"pixi.toml feature {feature_name!r}",
                ),
            )
        )

    python_requirements: list[str] = []
    if not no_default_feature:
        dependencies = _object(
            manifest.get("dependencies", {}), "pixi.toml [dependencies]"
        )
        if "python" in dependencies:
            python_requirements.append(str(dependencies["python"]))
    for feature_name, feature in selected_features:
        dependencies = _object(
            feature.get("dependencies", {}),
            f"pixi.toml feature {feature_name!r} dependencies",
        )
        if "python" in dependencies:
            python_requirements.append(str(dependencies["python"]))
    if not python_requirements:
        raise PixiSetupError(
            f"Pixi environment {environment_name!r} does not declare a "
            "Python requirement"
        )
    if len(set(python_requirements)) != 1:
        raise PixiSetupError(
            f"Pixi environment {environment_name!r} has ambiguous Python requirements"
        )
    python_requirement = python_requirements[0]
    python_version = _exact_version(
        python_requirement,
        f"Pixi environment {environment_name!r} Python requirement",
    )

    platform_rows = workspace.get("platforms")
    if type(platform_rows) is not list or not platform_rows:
        raise PixiSetupError("pixi.toml [workspace].platforms must be a non-empty list")
    declared_platforms: dict[str, str] = {}
    for index, value in enumerate(platform_rows):
        row = _object(value, f"pixi.toml workspace platform row {index}")
        name = row.get("name")
        subdir = row.get("platform")
        if type(name) is not str or type(subdir) is not str or not name or not subdir:
            raise PixiSetupError(
                f"pixi.toml workspace platform row {index} needs name and platform"
            )
        if name in declared_platforms:
            raise PixiSetupError(f"pixi.toml repeats workspace platform {name!r}")
        declared_platforms[name] = subdir

    allowed_platforms = set(declared_platforms)
    for feature_name, feature in selected_features:
        if "platforms" not in feature:
            continue
        feature_platforms = set(
            _string_list(
                feature["platforms"], f"pixi.toml feature {feature_name!r} platforms"
            )
        )
        unknown_platforms = feature_platforms.difference(declared_platforms)
        if unknown_platforms:
            raise PixiSetupError(
                f"Pixi feature {feature_name!r} references unknown workspace "
                "platforms: " + ", ".join(sorted(unknown_platforms))
            )
        allowed_platforms.intersection_update(feature_platforms)
    if not allowed_platforms:
        raise PixiSetupError(
            f"Pixi environment {environment_name!r} has no declared platforms"
        )
    return {
        "project_name": project_name,
        "requires_pixi": requires_pixi,
        "features": feature_names,
        "uses_default_feature": not no_default_feature,
        "python_requirement": python_requirement,
        "python_version": python_version,
        "declared_platforms": declared_platforms,
        "allowed_platforms": allowed_platforms,
    }


def _runtime_facts() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "prefix": str(Path(sys.prefix).resolve()),
        "executable": str(Path(sys.executable).resolve()),
    }


def _verified_pixi_tool(requirement: str) -> dict[str, Any]:
    """Probe a private copy of the current PIXI_EXE path after task launch.

    Pixi does not expose a launch-time executable handle to the task, so this
    observation intentionally makes no claim about the inode that launched it.
    """

    expected = _exact_version(requirement, "pixi.toml [workspace].requires-pixi")
    declared = os.environ.get("PIXI_EXE")
    if not declared:
        raise PixiSetupError(
            "PIXI_EXE is absent; setup must be launched by the required Pixi executable"
        )
    try:
        with stage_executable(
            Path(declared),
            logical_name="pixi",
            maximum=_MAX_PIXI_EXECUTABLE_BYTES,
        ) as executable:
            try:
                completed = subprocess.run(
                    [str(executable.path), "--version"],
                    cwd=ROOT,
                    env=dict(os.environ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                    timeout=15,
                )
            except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
                raise PixiSetupError(
                    f"PIXI_EXE version cannot be verified: {exc}"
                ) from exc
            matched = _PIXI_VERSION_LINE.fullmatch(completed.stdout.strip())
            if (
                completed.returncode != 0
                or completed.stderr
                or matched is None
                or matched.group("version") != expected
            ):
                raise PixiSetupError(
                    f"PIXI_EXE must report exactly `pixi {expected}` from pixi.toml"
                )
            executable.require_unchanged()
            return {
                "path": str(executable.source_path),
                "version": matched.group("version"),
                "sha256": executable.sha256,
                "size": executable.size,
                "required_constraint": requirement,
                "identity_scope": "post-launch-custody-copy",
                "launch_time_identity_claimed": False,
            }
    except ExecutableCustodyError as exc:
        raise PixiSetupError(f"PIXI_EXE custody failed: {exc}") from exc


def _runtime_target(runtime: dict[str, str]) -> tuple[str, str]:
    key = (runtime["system"].casefold(), runtime["machine"].casefold())
    target = _PLATFORM_TARGETS.get(key)
    if target is None:
        raise PixiSetupError(
            "this host is not a declared Workbench Pixi platform: "
            f"{runtime['system']} {runtime['machine']}"
        )
    return target


def _verify_lock_environment(
    lock: dict[str, Any],
    *,
    environment_name: str,
    platform_subdir: str,
    python_version: str,
) -> dict[str, Any]:
    lock_version = lock.get("version")
    if type(lock_version) is not int:
        raise PixiSetupError("pixi.lock version must be an integer")
    platform_rows = lock.get("platforms")
    if type(platform_rows) is not list:
        raise PixiSetupError("pixi.lock platforms must be a list")
    matching = []
    for index, value in enumerate(platform_rows):
        row = _object(value, f"pixi.lock platform row {index}")
        if row.get("subdir") == platform_subdir:
            matching.append(row)
    if len(matching) != 1 or type(matching[0].get("name")) is not str:
        raise PixiSetupError(
            f"pixi.lock does not uniquely define platform {platform_subdir!r}"
        )
    lock_platform = matching[0]["name"]

    environments = _object(lock.get("environments"), "pixi.lock environments")
    if environment_name not in environments:
        raise PixiSetupError(
            f"pixi.lock does not contain environment {environment_name!r}"
        )
    environment = _object(
        environments[environment_name],
        f"pixi.lock environment {environment_name!r}",
    )
    packages = _object(
        environment.get("packages"),
        f"pixi.lock environment {environment_name!r} packages",
    )
    if lock_platform not in packages:
        raise PixiSetupError(
            f"pixi.lock environment {environment_name!r} has no "
            f"{platform_subdir} closure"
        )
    package_rows = packages[lock_platform]
    if type(package_rows) is not list:
        raise PixiSetupError(
            f"pixi.lock environment {environment_name!r} platform packages "
            "must be a list"
        )
    python_prefix = f"python-{python_version}-"
    matching_python = []
    for value in package_rows:
        row = _object(value, "pixi.lock package row")
        conda_url = row.get("conda")
        if type(conda_url) is not str:
            continue
        filename = Path(urlparse(conda_url).path).name.casefold()
        if filename.startswith(python_prefix.casefold()):
            matching_python.append(conda_url)
    if len(matching_python) != 1:
        raise PixiSetupError(
            f"pixi.lock environment {environment_name!r} does not uniquely pin "
            f"Python {python_version} for {platform_subdir}"
        )
    return {
        "schema_version": lock_version,
        "platform_key": lock_platform,
        "python_package": matching_python[0],
    }


def _verified_pixi_environment(
    environment_name: str, manifest_path: str
) -> dict[str, Any]:
    expected_manifest_path = ROOT / "pixi.toml"
    try:
        expected_manifest = expected_manifest_path.resolve(strict=True)
        observed_manifest = Path(manifest_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise PixiSetupError(f"PIXI_PROJECT_MANIFEST is unavailable: {exc}") from exc
    if observed_manifest != expected_manifest:
        raise PixiSetupError(
            "PIXI_PROJECT_MANIFEST does not identify this repository's exact "
            "pixi.toml: "
            f"{observed_manifest}"
        )

    try:
        inputs = bind_pixi_inputs(expected_manifest_path, ROOT / "pixi.lock")
    except PixiLockError as exc:
        raise PixiSetupError(f"repository Pixi inputs are unsafe: {exc}") from exc
    manifest_record = _input_record(inputs.manifest_path, inputs.manifest_bytes)
    lock_record = _input_record(inputs.lock_path, inputs.lock_bytes)
    manifest = _parse_manifest(inputs.manifest_bytes)
    lock = _parse_lock(inputs.lock_bytes)
    contract = _environment_contract(manifest, environment_name)
    runtime = _runtime_facts()

    if runtime["implementation"] != "CPython":
        raise PixiSetupError(
            "the selected Pixi environment requires CPython; observed "
            + runtime["implementation"]
        )
    if runtime["python"] != contract["python_version"]:
        raise PixiSetupError(
            f"Pixi environment {environment_name!r} requires Python "
            f"{contract['python_version']} from pixi.toml; observed {runtime['python']}"
        )
    platform_name, platform_subdir = _runtime_target(runtime)
    if platform_name not in contract["allowed_platforms"]:
        raise PixiSetupError(
            f"Pixi environment {environment_name!r} is not declared for "
            f"{platform_name}"
        )
    if contract["declared_platforms"].get(platform_name) != platform_subdir:
        raise PixiSetupError(
            f"pixi.toml platform {platform_name!r} does not map to {platform_subdir!r}"
        )

    expected_prefix = (ROOT / ".pixi" / "envs" / environment_name).resolve()
    observed_prefix = Path(runtime["prefix"]).resolve()
    if observed_prefix != expected_prefix:
        raise PixiSetupError(
            f"Python is not running from this repository's {environment_name!r} "
            f"Pixi prefix: expected {expected_prefix}, observed {observed_prefix}"
        )
    executable = Path(runtime["executable"]).resolve()
    try:
        executable.relative_to(expected_prefix)
    except ValueError as exc:
        raise PixiSetupError(
            f"Python executable is outside the verified Pixi prefix: {executable}"
        ) from exc

    lock_environment = _verify_lock_environment(
        lock,
        environment_name=environment_name,
        platform_subdir=platform_subdir,
        python_version=contract["python_version"],
    )
    try:
        expected_closure = build_environment_closure_from_bound_inputs(
            inputs,
            environment=environment_name,
            platform=platform_name,
        )
    except PixiLockError as exc:
        raise PixiSetupError(f"repository Pixi closure is invalid: {exc}") from exc
    pixi_tool = _verified_pixi_tool(contract["requires_pixi"])
    return {
        "project": {
            "name": contract["project_name"],
            "root": str(ROOT.resolve()),
            "manifest": manifest_record,
            "lock": lock_record,
        },
        "environment": {
            "name": environment_name,
            "features": contract["features"],
            "uses_default_feature": contract["uses_default_feature"],
            "prefix": str(observed_prefix),
        },
        "runtime": {
            **runtime,
            "platform_name": platform_name,
            "platform_subdir": platform_subdir,
        },
        "requirements": {
            "pixi": contract["requires_pixi"],
            "python": contract["python_requirement"],
        },
        "tool": pixi_tool,
        "lock_environment": lock_environment,
        "expected_closure": expected_closure,
        "verification": {
            "repository_manifest": True,
            "manifest_and_lock_identities_bound": True,
            "environment_declared": True,
            "repository_environment_prefix": True,
            "python_matches_manifest": True,
            "platform_matches_manifest": True,
            "environment_platform_present_in_lock": True,
            "python_present_in_lock": True,
            "expected_closure_derived_from_bound_inputs": True,
            "pixi_executable_version": True,
            "pixi_launch_time_executable_identity": False,
            "complete_environment_closure": False,
        },
        "invocation": {
            "locked": {
                "status": "not-observable",
                "claimed": False,
                "reason": "Pixi does not expose the launch flag to this task process",
            },
            "no_config": {
                "status": "not-observable",
                "claimed": False,
                "reason": "Pixi does not expose the launch flag to this task process",
            },
        },
    }


def _json_command(
    arguments: Sequence[str],
    *,
    timeout: float,
    accepted_returncodes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools/workbench.py"), *arguments],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PixiSetupError(f"Workbench command could not complete: {exc}") from exc
    if completed.returncode not in accepted_returncodes:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PixiSetupError(
            f"Workbench command failed ({completed.returncode}): {detail}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PixiSetupError("Workbench command returned invalid JSON") from exc
    if type(value) is not dict:
        raise PixiSetupError("Workbench command returned a non-object result")
    return value


def _default_workspace() -> Path:
    initial = os.environ.get("INIT_CWD")
    candidate = Path(initial) if initial else Path.cwd()
    return candidate.expanduser().resolve()


def _java_result() -> dict[str, Any]:
    try:
        from workbench_core.runtime_java import ensure_java_runtime

        return ensure_java_runtime(ROOT)
    except (OSError, ValueError) as exc:
        raise PixiSetupError(
            f"exact Cleanroom Java provisioning failed: {exc}"
        ) from exc


def build_setup_result(
    workspace: Path,
    *,
    provision_java: bool,
) -> dict[str, Any]:
    environment_name = os.environ.get("PIXI_ENVIRONMENT_NAME")
    manifest = os.environ.get("PIXI_PROJECT_MANIFEST")
    if not environment_name or not manifest:
        raise PixiSetupError(
            "this setup must run inside Pixi; use "
            "`pixi run --locked --no-config setup`"
        )
    if not workspace.is_dir():
        raise PixiSetupError(f"workspace is not a directory: {workspace}")

    try:
        require_project_local_config_absent(ROOT)
    except PixiProjectConfigError as error:
        raise PixiSetupError(str(error)) from error

    pixi = _verified_pixi_environment(environment_name, manifest)
    version = _json_command(("--version", "--json"), timeout=30.0)
    java = _java_result() if provision_java else None
    doctor = _json_command(
        ("doctor", str(workspace), "--json"),
        timeout=60.0,
        accepted_returncodes=(0, 1),
    )
    doctor_summary = doctor.get("summary")
    doctor_status = (
        doctor_summary.get("status") if isinstance(doctor_summary, dict) else None
    )
    if doctor_status not in {"ready", "attention", "blocked"}:
        raise PixiSetupError("Workspace Doctor returned an unknown status")
    runtime = pixi["runtime"]
    return {
        "format": "workbench-pixi-setup-result-v2",
        "schema_version": 2,
        "outcome": doctor_status,
        "workspace": str(workspace),
        "environment": {
            "name": environment_name,
            "manifest": pixi["project"]["manifest"]["path"],
            "python": runtime["python"],
            "implementation": runtime["implementation"],
            "system": runtime["system"],
            "machine": runtime["machine"],
        },
        "pixi": pixi,
        "workbench": version,
        "java": java,
        "doctor": doctor,
        "claims": {
            "package_conformance_complete": False,
            "release_qualified": False,
            "support_claimed": False,
        },
    }


def _human(result: dict[str, Any]) -> str:
    environment = result["environment"]
    pixi = result["pixi"]
    runtime = pixi["runtime"]
    project = pixi["project"]
    java = result["java"]
    heading = {
        "ready": "Workbench is ready with verified Pixi inputs and runtime.",
        "attention": (
            "Workbench's Pixi inputs and runtime are verified, but the selected "
            "workspace needs attention."
        ),
        "blocked": (
            "Workbench's Pixi inputs and runtime are verified, but the selected "
            "workspace is blocked."
        ),
    }[result["outcome"]]
    lines = [
        heading,
        "  Environment: "
        f"{environment['name']} ({runtime['platform_name']} / "
        f"{runtime['platform_subdir']})",
        f"  Python: {environment['implementation']} {environment['python']}",
        f"  Host: {environment['system']} {environment['machine']}",
        f"  Prefix: {pixi['environment']['prefix']}",
        "  Manifest: "
        f"{project['manifest']['path']} ({project['manifest']['identity']})",
        f"  Lock: {project['lock']['path']} ({project['lock']['identity']})",
        f"  Expected closure: {pixi['expected_closure']['closure_id']}",
        "  Pixi (post-launch custody copy; launch-time bytes not claimed): "
        f"{pixi['tool']['version']} ({pixi['tool']['path']}; "
        f"sha256:{pixi['tool']['sha256']})",
        "  Pixi invocation flags: --locked and --no-config are not observable; "
        "neither is claimed by this receipt.",
        "  Complete environment closure: not verified by setup",
        f"  Workspace inspected: {result['workspace']}",
    ]
    if java is None:
        lines.append(
            "  Cleanroom Java: skipped "
            "(run `pixi run --locked --no-config setup` to provision it)"
        )
    else:
        lines.append(
            "  Cleanroom Java: "
            + str(java.get("outcome", "available"))
            + " (exact profile-selected Temurin)"
        )
    lines.extend(
        (
            "",
            "Next: `pixi run --locked --no-config console` for the guided command surface,",
            "or `pixi run --locked --no-config workbench --help` for direct CLI use.",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the Pixi-provisioned Workbench runtime, provision the exact "
            "Cleanroom Java candidate, and inspect a target workspace."
        )
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=_default_workspace(),
        help="workspace to inspect; defaults to the Pixi task working directory",
    )
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="verify only the core and defer the profile-selected Java download",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_setup_result(
            args.workspace.expanduser().resolve(),
            provision_java=not args.core_only,
        )
    except PixiSetupError as exc:
        print(f"Workbench setup failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(_human(result))
    return 1 if result["outcome"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
