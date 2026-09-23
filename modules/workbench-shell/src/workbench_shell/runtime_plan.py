"""Read-only runtime planning over exact project and profile context."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from workbench_api.state_paths import default_suite_state_root


SIDES = frozenset({"client", "server"})
CLIENT_LAUNCHERS = frozenset({"prism", "multimc"})
SERVER_LAUNCHER = "dedicated-server"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXISTING_FIXTURE_WARNING = (
    "the deterministic fixture target already exists; provisioning "
    "must verify or replace it explicitly"
)


class RuntimePlanError(ValueError):
    """Raised when a runtime request cannot be planned truthfully."""


def _required_string(record: dict[str, Any], field: str, label: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise RuntimePlanError(f"{label} lacks {field}")
    return value


def _artifact(
    profile: Mapping[str, Any],
    artifact_id: str,
) -> dict[str, str]:
    artifacts = profile.get("runtime_artifacts")
    if not isinstance(artifacts, Mapping):
        raise RuntimePlanError("platform profile lacks runtime_artifacts")
    record = artifacts.get(artifact_id)
    if not isinstance(record, Mapping):
        raise RuntimePlanError(
            f"platform profile lacks runtime artifact: {artifact_id}"
        )
    url = _required_string(record, "url", f"runtime artifact {artifact_id}")
    digest = _required_string(
        record,
        "sha256",
        f"runtime artifact {artifact_id}",
    )
    resolved = url != "unresolved" and bool(SHA256_RE.fullmatch(digest))
    return {
        "id": artifact_id,
        "url": url,
        "sha256": digest,
        "state": "resolved" if resolved else "unresolved",
    }


def _blocker(
    blocker_id: str,
    reason: str,
    resolution: str,
) -> dict[str, str]:
    return {
        "id": blocker_id,
        "reason": reason,
        "resolution": resolution,
    }


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_runtime_plan(
    context: dict[str, Any],
    platform_profile: Mapping[str, Any],
    *,
    state_root: Path | str,
    side: str = "client",
    launcher: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic plan and never mutate its intended targets."""

    if side not in SIDES:
        raise RuntimePlanError(f"unsupported runtime side: {side}")
    selected_launcher = launcher
    if side == "client":
        selected_launcher = selected_launcher or "prism"
        if selected_launcher not in CLIENT_LAUNCHERS:
            raise RuntimePlanError(
                "client launcher must be prism or multimc"
            )
        bootstrap_artifact_id = "cleanroom_client"
    else:
        selected_launcher = selected_launcher or SERVER_LAUNCHER
        if selected_launcher != SERVER_LAUNCHER:
            raise RuntimePlanError(
                "server launcher must be dedicated-server"
            )
        bootstrap_artifact_id = "cleanroom_server"

    workspace = context.get("workspace")
    project = context.get("project")
    platform = context.get("platform")
    pack = context.get("pack")
    if not all(
        isinstance(record, dict)
        for record in (workspace, project, platform, pack)
    ):
        raise RuntimePlanError("workspace context is incomplete")
    assert isinstance(workspace, dict)
    assert isinstance(project, dict)
    assert isinstance(platform, dict)
    assert isinstance(pack, dict)

    cleanroom_version = _required_string(
        platform,
        "cleanroom_version",
        "workspace platform context",
    )
    java = platform_profile.get("java")
    if not isinstance(java, Mapping):
        raise RuntimePlanError("platform profile lacks java")
    runtime_java = _required_string(java, "runtime", "platform profile java")
    packwiz_installer = _artifact(
        platform_profile,
        "packwiz_installer",
    )
    cleanroom_bootstrap = _artifact(
        platform_profile,
        bootstrap_artifact_id,
    )

    blockers: list[dict[str, str]] = []
    index = project.get("index")
    if not isinstance(index, dict):
        raise RuntimePlanError("project context lacks Packwiz index")
    if cleanroom_version == "unresolved":
        blockers.append(
            _blocker(
                "cleanroom-version-unresolved",
                "the selected platform profile has no exact Cleanroom version",
                "record the tested Cleanroom version in the platform profile",
            )
        )
    if runtime_java == "unresolved":
        blockers.append(
            _blocker(
                "runtime-java-unresolved",
                "the selected platform profile has no runtime Java identity",
                "record the Java runtime required by the selected Cleanroom build",
            )
        )
    for artifact in (packwiz_installer, cleanroom_bootstrap):
        if artifact["state"] == "unresolved":
            blockers.append(
                _blocker(
                    f"{artifact['id'].replace('_', '-')}-unresolved",
                    f"the {artifact['id']} artifact is not hash-locked",
                    "record its immutable URL and SHA-256 in the platform profile",
                )
            )

    warnings: list[str] = []
    if index.get("matches_declared_hash") is not True:
        warnings.append(
            "the local Packwiz index differs from pack.toml; source "
            "materialization will refresh a disposable tracked-file copy "
            "without changing the checkout"
        )
    if workspace.get("dirty") is True:
        warnings.append(
            "the source workspace is dirty; local materialization copies "
            "current Git-tracked bytes and excludes untracked files"
        )
    loaders = project.get("loaders")
    if isinstance(loaders, list):
        source_loaders = [
            loader.get("id")
            for loader in loaders
            if isinstance(loader, dict)
            and isinstance(loader.get("id"), str)
        ]
        if any(loader != "cleanroom" for loader in source_loaders):
            warnings.append(
                "the source pack declares a legacy loader while the selected "
                "runtime target is Cleanroom"
            )

    identity_input = {
        "workspace": {
            "root_uri": workspace.get("root_uri"),
            "revision": workspace.get("revision"),
            "dirty_entries": workspace.get("dirty_entries"),
        },
        "project": {
            "manifest_sha256": project.get("manifest_sha256"),
            "index_actual_sha256": index.get("actual_sha256"),
        },
        "platform": {
            "profile_id": platform.get("profile_id"),
            "document_sha256": platform.get("document_sha256"),
            "cleanroom_version": cleanroom_version,
            "runtime_java": runtime_java,
        },
        "pack": {
            "profile_family_id": pack.get("profile_family_id"),
            "selected_profile": pack.get("selected_profile"),
            "document_sha256": pack.get("document_sha256"),
        },
        "request": {
            "side": side,
            "launcher": selected_launcher,
        },
        "artifacts": [packwiz_installer, cleanroom_bootstrap],
    }
    digest = _canonical_sha256(identity_input)
    plan_id = f"sha256:{digest}"

    state_path = Path(state_root).expanduser().resolve()
    fixture_root = (
        state_path
        / "fixtures"
        / _slug(_required_string(project, "name", "project context"))
        / side
        / digest[:16]
    )
    intended_writes = [
        {
            "root_uri": fixture_root.as_uri(),
            "purpose": "disposable-runtime-fixture",
        },
        {
            "root_uri": (fixture_root / "receipts").as_uri(),
            "purpose": "runtime-plan-and-result-records",
        },
    ]
    if fixture_root.exists():
        warnings.append(EXISTING_FIXTURE_WARNING)

    return {
        "format": "workbench-runtime-plan-v1",
        "schema_version": 1,
        "plan_id": plan_id,
        "operation_class": "read-only",
        "state": "ready" if not blockers else "blocked",
        "request": {
            "side": side,
            "launcher": selected_launcher,
        },
        "workspace": {
            "root_uri": workspace.get("root_uri"),
            "revision": workspace.get("revision"),
            "dirty": workspace.get("dirty"),
        },
        "project": {
            "name": project.get("name"),
            "version": project.get("version"),
            "manifest_sha256": project.get("manifest_sha256"),
            "minecraft_version": project.get("minecraft_version"),
            "loaders": project.get("loaders"),
            "index": dict(index),
        },
        "target": {
            "fixture_root_uri": fixture_root.as_uri(),
            "platform_profile_id": platform.get("profile_id"),
            "cleanroom_version": cleanroom_version,
            "runtime_java": runtime_java,
            "pack_profile_id": pack.get("profile_family_id"),
            "selected_pack_profile": pack.get("selected_profile"),
        },
        "artifacts": [packwiz_installer, cleanroom_bootstrap],
        "intended_writes": intended_writes,
        "steps": [
            {
                "id": "prepare-fixture",
                "summary": "create a fresh disposable target",
            },
            {
                "id": "provision-cleanroom",
                "summary": f"provision exact Cleanroom {side} bootstrap",
            },
            {
                "id": "install-packwiz",
                "summary": (
                    "refresh a disposable tracked-file copy and install its "
                    "Packwiz payload without editing launcher metadata"
                ),
            },
            {
                "id": "verify-inputs",
                "summary": "verify managed inputs and retained identities",
            },
            {
                "id": "launch-checkpoint",
                "summary": f"launch through {selected_launcher} and retain the result",
            },
        ],
        "blockers": blockers,
        "warnings": warnings,
    }


def plan_project_runtime(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    side: str = "client",
    launcher: str | None = None,
    state_root: Path | str | None = None,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Inspect a project and produce its executable V1 runtime preview."""

    suite = Path(suite_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimePlanError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimePlanError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    inspection = inspect_project(
        suite,
        workspace_root,
        configuration=active_configuration,
    )
    context = inspection["workspace_context"]
    return build_runtime_plan(
        context,
        active_configuration.platform_document.values,
        state_root=state,
        side=side,
        launcher=launcher,
    )
