"""Portable environment choices with exact profile locks and local binding.

The share document contains no machine path. Core binds it to an existing,
matching Configuration V1 manifest and workspace only on the receiving host.
Project bytes, optional packages, fixtures and Java archives are outside this
first reconstruction slice and remain visible as unresolved inputs.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping

from .configuration import (
    CONFIGURATION_PATH,
    SCHEMA as CONFIGURATION_SCHEMA,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .environment_resolution import resolve_environment
from .runtime_java import (
    JavaRuntimeError,
    host_platform,
    load_java_runtime_policy,
    select_managed_java_policy,
)
from .setup_cli import _workspace
from .storage.registered import CoreDurableResources
from .user_preferences import (
    UserPreferencesError,
    bind_workspace_selection,
    load_workspaces,
    resolve_expression,
    resolve_java_path,
    resolve_selection_path,
)


SHARE_FORMAT = "workbench-environment-share-v1"
INTENT_FORMAT = "workbench-environment-intent-v1"
LOCK_FORMAT = "workbench-environment-lock-v1"
PLAN_FORMAT = "workbench-environment-import-plan-v1"
RESULT_FORMAT = "workbench-environment-import-result-v1"
MAX_SHARE_BYTES = 256 * 1024
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_MODES = frozenset({"profile-default", "managed-feature", "local-binding-required"})
_UNRESOLVED = (
    "workspace-project-bytes",
    "optional-module-packages",
    "profile-fixture-and-tool-bytes",
)


class ReconstructionError(ValueError):
    """An environment share, local binding, or reviewed plan is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(body: dict[str, Any], prefix: str, field: str) -> dict[str, Any]:
    return {**body, field: prefix + ":sha256:" + sha256(_canonical(body)).hexdigest()}


def _exact(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ReconstructionError(f"{label} has unsupported fields")
    return value


def _profile_path(value: object, label: str) -> str:
    if type(value) is not str or not value.startswith("profiles/") or "\\" in value:
        raise ReconstructionError(f"{label} must be a suite-relative profile path")
    path = PurePosixPath(value)
    if (
        path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
        or path.suffix not in {".yaml", ".yml"}
    ):
        raise ReconstructionError(f"{label} must be a canonical profile path")
    return value


def _host_variant(host: Mapping[str, str]) -> dict[str, str]:
    if (
        type(host.get("os")) is not str
        or type(host.get("architecture")) is not str
        or host["os"] not in {"linux", "windows", "mac"}
        or host["architecture"] not in {"x64", "aarch64", "ppc64le", "s390x", "riscv64"}
    ):
        raise ReconstructionError("unsupported local host variant")
    return {"os": host["os"], "architecture": host["architecture"]}


def build_share(
    suite_root: Path,
    workspace_name: str,
    *,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Describe one saved workspace without exporting any local path choice."""

    values = dict(os.environ if environment is None else environment)
    if type(workspace_name) is not str or _NAME.fullmatch(workspace_name) is None:
        raise ReconstructionError("choose a named workspace to export")
    registry = load_workspaces(environment=values)
    row = next((entry for entry in registry["entries"] if entry["name"] == workspace_name), None)
    if row is None:
        raise ReconstructionError(f"workspace is not registered: {workspace_name}")
    suite = Path(suite_root).expanduser().resolve()
    configuration_path = (
        resolve_selection_path(row["profile_config"], environment=values)
        if row.get("profile_config") is not None
        else CONFIGURATION_PATH
    )
    try:
        configuration = load_workbench_configuration(suite, configuration_path)
        default_policy = load_java_runtime_policy(suite, configuration=configuration)
    except (WorkbenchConfigurationError, JavaRuntimeError) as exc:
        raise ReconstructionError(f"selected profile cannot be exported: {exc}") from exc

    if row.get("java_home") is not None:
        java = {"mode": "local-binding-required", "feature_version": None}
        selected_policy = None
    elif row.get("managed_java_feature") is not None:
        feature = row["managed_java_feature"]
        java = {"mode": "managed-feature", "feature_version": feature}
        selected_policy = select_managed_java_policy(default_policy, feature)
    else:
        java = {"mode": "profile-default", "feature_version": None}
        selected_policy = default_policy
    intent = _seal({
        "format": INTENT_FORMAT,
        "schema_version": 1,
        "selection": {
            "configuration_schema": CONFIGURATION_SCHEMA,
            "pack_document": configuration.pack_document.source.relative_path,
            "pack_variant": configuration.pack_variant,
            "platform_document": configuration.platform_document.source.relative_path,
        },
        "java": java,
    }, "workbench-environment-intent", "intent_id")
    unresolved = list(_UNRESOLVED)
    if selected_policy is None:
        unresolved.append("local-java-home")
    else:
        unresolved.append("managed-java-archive")
    lock = _seal({
        "format": LOCK_FORMAT,
        "schema_version": 1,
        "intent_id": intent["intent_id"],
        "selection_digest": configuration.selection_digest,
        "pack_profile": {
            "profile_id": configuration.pack_profile_id,
            "sha256": configuration.pack_document.source.sha256,
        },
        "platform_profile": {
            "profile_id": configuration.platform_profile_id,
            "sha256": configuration.platform_document.source.sha256,
        },
        "java_policy": {
            "profile_policy_sha256": default_policy["policy_sha256"],
            "selected_policy_sha256": (
                selected_policy["policy_sha256"] if selected_policy is not None else None
            ),
            "feature_version": (
                selected_policy["feature_version"] if selected_policy is not None else None
            ),
            "runtime_identity": (
                selected_policy["runtime_identity"] if selected_policy is not None else None
            ),
            "release_name": (
                selected_policy["release_name"] if selected_policy is not None else None
            ),
        },
        "host_variant": _host_variant(host_platform() if host is None else host),
        "unresolved_inputs": unresolved,
    }, "workbench-environment-lock", "lock_id")
    return _seal({
        "format": SHARE_FORMAT,
        "schema_version": 1,
        "intent": intent,
        "lock": lock,
    }, "workbench-environment-share", "share_id")


def validate_share(value: object) -> dict[str, Any]:
    """Strictly read the portable bytes before consulting local state."""

    share = _exact(value, {"format", "schema_version", "intent", "lock", "share_id"}, "share")
    if share["format"] != SHARE_FORMAT or type(share["schema_version"]) is not int or share["schema_version"] != 1:
        raise ReconstructionError("unsupported environment share schema")
    intent = _exact(share["intent"], {"format", "schema_version", "selection", "java", "intent_id"}, "intent")
    lock = _exact(share["lock"], {
        "format", "schema_version", "intent_id", "selection_digest", "pack_profile",
        "platform_profile", "java_policy", "host_variant", "unresolved_inputs", "lock_id",
    }, "lock")
    if intent["format"] != INTENT_FORMAT or type(intent["schema_version"]) is not int or intent["schema_version"] != 1:
        raise ReconstructionError("unsupported environment intent schema")
    if lock["format"] != LOCK_FORMAT or type(lock["schema_version"]) is not int or lock["schema_version"] != 1:
        raise ReconstructionError("unsupported environment lock schema")
    selection = _exact(intent["selection"], {
        "configuration_schema", "pack_document", "pack_variant", "platform_document",
    }, "intent selection")
    if selection["configuration_schema"] != CONFIGURATION_SCHEMA:
        raise ReconstructionError("unsupported configuration schema in environment intent")
    _profile_path(selection["pack_document"], "pack document")
    _profile_path(selection["platform_document"], "platform document")
    if type(selection["pack_variant"]) is not str or re.fullmatch(r"[a-z0-9][a-z0-9._-]*", selection["pack_variant"]) is None:
        raise ReconstructionError("invalid pack variant in environment intent")
    java = _exact(intent["java"], {"mode", "feature_version"}, "intent Java choice")
    if type(java["mode"]) is not str or java["mode"] not in _MODES:
        raise ReconstructionError("unsupported Java choice in environment intent")
    if java["mode"] == "managed-feature":
        if type(java["feature_version"]) is not int or java["feature_version"] != 8:
            raise ReconstructionError("unsupported managed Java feature")
    elif java["feature_version"] is not None:
        raise ReconstructionError("Java feature belongs only to a managed choice")
    for label in ("pack_profile", "platform_profile"):
        profile = _exact(lock[label], {"profile_id", "sha256"}, label)
        if type(profile["profile_id"]) is not str or not profile["profile_id"]:
            raise ReconstructionError(f"invalid {label} identity")
        if type(profile["sha256"]) is not str or _SHA256.fullmatch(profile["sha256"]) is None:
            raise ReconstructionError(f"invalid {label} digest")
    policy = _exact(lock["java_policy"], {
        "profile_policy_sha256", "selected_policy_sha256", "feature_version",
        "runtime_identity", "release_name",
    }, "Java policy lock")
    if type(policy["profile_policy_sha256"]) is not str or _SHA256.fullmatch(policy["profile_policy_sha256"]) is None:
        raise ReconstructionError("invalid profile Java policy digest")
    if java["mode"] == "local-binding-required":
        if any(policy[field] is not None for field in (
            "selected_policy_sha256", "feature_version", "runtime_identity", "release_name"
        )):
            raise ReconstructionError("local Java choice cannot claim a verified runtime")
    else:
        if (
            type(policy["selected_policy_sha256"]) is not str
            or _SHA256.fullmatch(policy["selected_policy_sha256"]) is None
            or type(policy["feature_version"]) is not int
            or type(policy["runtime_identity"]) is not str
            or type(policy["release_name"]) is not str
        ):
            raise ReconstructionError("managed Java policy lock is incomplete")
        if java["mode"] == "managed-feature" and policy["feature_version"] != java["feature_version"]:
            raise ReconstructionError("managed Java choice differs from its lock")
    if type(lock["selection_digest"]) is not str or _SHA256.fullmatch(lock["selection_digest"]) is None:
        raise ReconstructionError("invalid profile selection digest")
    host = _exact(lock["host_variant"], {"os", "architecture"}, "host variant")
    _host_variant(host)
    unresolved = lock["unresolved_inputs"]
    if (
        type(unresolved) is not list
        or not unresolved
        or any(type(item) is not str or re.fullmatch(r"[a-z][a-z0-9-]*", item) is None for item in unresolved)
        or len(set(unresolved)) != len(unresolved)
    ):
        raise ReconstructionError("invalid unresolved input list")
    expected_unresolved = list(_UNRESOLVED) + [
        "local-java-home" if java["mode"] == "local-binding-required"
        else "managed-java-archive"
    ]
    if unresolved != expected_unresolved:
        raise ReconstructionError("environment lock omits or changes unresolved inputs")
    for record, field, prefix in (
        (intent, "intent_id", "workbench-environment-intent"),
        (lock, "lock_id", "workbench-environment-lock"),
        (share, "share_id", "workbench-environment-share"),
    ):
        body = {key: item for key, item in record.items() if key != field}
        if record[field] != _seal(body, prefix, field)[field]:
            raise ReconstructionError(f"{field} does not match the portable record")
    if lock["intent_id"] != intent["intent_id"]:
        raise ReconstructionError("environment lock refers to another intent")
    if len(_canonical(share)) > MAX_SHARE_BYTES:
        raise ReconstructionError("environment share exceeds the byte limit")
    return share


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReconstructionError(f"duplicate environment share field: {key}")
        result[key] = value
    return result


def load_share(path: Path | str) -> dict[str, Any]:
    selected = Path(path)
    try:
        info = selected.lstat()
    except OSError as exc:
        raise ReconstructionError(f"environment share is unavailable: {selected}") from exc
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_SHARE_BYTES:
        raise ReconstructionError("environment share must be a bounded regular file")
    try:
        descriptor = os.open(
            selected, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= MAX_SHARE_BYTES:
                raise ReconstructionError("environment share must be a bounded regular file")
            chunks: list[bytes] = []
            remaining = MAX_SHARE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if remaining == 0:
                raise ReconstructionError("environment share exceeds the byte limit")
        finally:
            os.close(descriptor)
        value = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReconstructionError("environment share is not valid UTF-8 JSON") from exc
    return validate_share(value)


def _resource_host(suite_root: Path, workspace: Path, environment: Mapping[str, str] | None) -> CoreDurableResources:
    resolved = resolve_environment(suite_root, workspace=workspace, environment=environment)
    return CoreDurableResources(
        workspace=resolved.workspace,
        configuration_home=resolved.configuration_home,
        locations=resolved.locations,
        owner_id="workbench-core",
        policy_id=resolved.record["resolution_id"],
        location_sources={role: item["source"] for role, item in resolved.record["locations"].items()},
    )


def export_share(
    suite_root: Path, workspace_name: str, *, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish the share through Core's registered immutable artifact service."""

    values = dict(os.environ if environment is None else environment)
    source_registry = load_workspaces(environment=values)
    share = build_share(suite_root, workspace_name, environment=values)
    registry = load_workspaces(environment=values)
    if registry["record_id"] != source_registry["record_id"]:
        raise ReconstructionError("source workspace choices changed during export")
    row = next(entry for entry in registry["entries"] if entry["name"] == workspace_name)
    workspace = resolve_expression(row["path"], environment=values)
    service = _resource_host(Path(suite_root), workspace, values)
    payload = _canonical(share) + b"\n"
    reference = service.publish_bytes(
        "artifacts", "environment-share.json", payload,
        domain_id=share["share_id"],
    )
    if service.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("published environment share did not reopen exactly")
    return {
        "format": "workbench-environment-share-export-v1",
        "schema_version": 1,
        "share": share,
        "resource": {
            "resource_id": reference.resource_id,
            "store_id": reference.store_id,
            "path": str(reference.path),
            "sha256": reference.sha256,
        },
    }


def plan_import(
    suite_root: Path,
    share: Mapping[str, Any],
    *,
    workspace_name: str,
    workspace: Path | str,
    config_path: Path | str = CONFIGURATION_PATH,
    java_home: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Check a target suite and local binding without writing any state."""

    portable = validate_share(dict(share))
    if type(workspace_name) is not str or _NAME.fullmatch(workspace_name) is None:
        raise ReconstructionError("workspace name must be a lowercase slug")
    suite = Path(suite_root).expanduser().resolve()
    values = dict(os.environ if environment is None else environment)
    blockers: list[str] = []
    try:
        selected_workspace = _workspace(resolve_expression(str(workspace), environment=values))
    except (UserPreferencesError, ValueError) as exc:
        raise ReconstructionError(f"local workspace is invalid: {exc}") from exc
    if not selected_workspace.is_dir():
        blockers.append("local workspace directory is missing; create or acquire it before import")
    try:
        target_configuration = load_workbench_configuration(suite, config_path)
    except WorkbenchConfigurationError as exc:
        target_configuration = None
        blockers.append(f"matching local Configuration V1 manifest is unavailable: {exc}")
    if target_configuration is not None:
        intent = portable["intent"]["selection"]
        lock = portable["lock"]
        if (
            target_configuration.selection_digest != lock["selection_digest"]
            or target_configuration.pack_document.source.relative_path != intent["pack_document"]
            or target_configuration.pack_variant != intent["pack_variant"]
            or target_configuration.platform_document.source.relative_path != intent["platform_document"]
            or target_configuration.pack_profile_id != lock["pack_profile"]["profile_id"]
            or target_configuration.pack_document.source.sha256 != lock["pack_profile"]["sha256"]
            or target_configuration.platform_profile_id != lock["platform_profile"]["profile_id"]
            or target_configuration.platform_document.source.sha256 != lock["platform_profile"]["sha256"]
        ):
            blockers.append("target suite profile selection or document bytes differ from the exact lock")
        try:
            default_policy = load_java_runtime_policy(suite, configuration=target_configuration)
            feature = portable["intent"]["java"]["feature_version"]
            selected_policy = (
                None if portable["intent"]["java"]["mode"] == "local-binding-required"
                else select_managed_java_policy(default_policy, feature)
            )
            policy_lock = lock["java_policy"]
            if (
                default_policy["policy_sha256"] != policy_lock["profile_policy_sha256"]
                or (selected_policy["policy_sha256"] if selected_policy else None)
                != policy_lock["selected_policy_sha256"]
                or (selected_policy["feature_version"] if selected_policy else None)
                != policy_lock["feature_version"]
                or (selected_policy["runtime_identity"] if selected_policy else None)
                != policy_lock["runtime_identity"]
                or (selected_policy["release_name"] if selected_policy else None)
                != policy_lock["release_name"]
            ):
                blockers.append("target Java policy differs from the exact lock")
        except JavaRuntimeError as exc:
            blockers.append(f"target Java policy is unavailable: {exc}")
    try:
        selected_host = _host_variant(host_platform() if host is None else host)
    except (JavaRuntimeError, ReconstructionError) as exc:
        selected_host = None
        blockers.append(f"unsupported platform binding: {exc}")
    if selected_host is not None and selected_host != portable["lock"]["host_variant"]:
        blockers.append("unsupported platform binding: the exact lock targets another OS or architecture")

    mode = portable["intent"]["java"]["mode"]
    if mode == "local-binding-required":
        if java_home is None:
            selected_java = None
            blockers.append("local Java home binding is required")
        else:
            try:
                selected_java = str(resolve_java_path(str(java_home), environment=values))
            except UserPreferencesError as exc:
                raise ReconstructionError(f"local Java binding is invalid: {exc}") from exc
    else:
        if java_home is not None:
            raise ReconstructionError("a managed Java choice cannot include a local Java path")
        selected_java = None
    registry = load_workspaces(environment=values)
    resolution = resolve_environment(suite, workspace=selected_workspace, environment=values)
    if resolution.record["workspaces"]["record_id"] != registry["record_id"]:
        raise ReconstructionError("user workspaces changed during import planning")
    old = next((entry for entry in registry["entries"] if entry["name"] == workspace_name), None)
    for entry in registry["entries"]:
        registered = resolve_expression(entry["path"], environment=values)
        if entry["name"] != workspace_name and registered == selected_workspace:
            blockers.append("another named workspace already uses this directory")
    if old is not None and resolve_expression(old["path"], environment=values) != selected_workspace:
        blockers.append("workspace name is already bound to another directory")
    local_config = str(target_configuration.manifest.path) if target_configuration else str(Path(config_path))
    same = (
        old is not None
        and registry["schema_version"] == 3
        and old["profile_config"] == local_config
        and old["java_home"] == selected_java
        and old["managed_java_feature"] == portable["intent"]["java"]["feature_version"]
    )
    action = "reuse" if same else "update" if old is not None else "create"
    plan_body = {
        "format": PLAN_FORMAT,
        "schema_version": 1,
        "share_id": portable["share_id"],
        "lock_id": portable["lock"]["lock_id"],
        "workspace_name": workspace_name,
        "workspace": str(selected_workspace),
        "profile_config": local_config,
        "profile_selection_digest": (
            target_configuration.selection_digest if target_configuration else None
        ),
        "java_home": selected_java,
        "managed_java_feature": portable["intent"]["java"]["feature_version"],
        "host_variant": selected_host,
        "expected_workspaces_record_id": registry["record_id"],
        "environment_resolution_id": resolution.record["resolution_id"],
        "action": action,
        "unresolved_inputs": portable["lock"]["unresolved_inputs"],
        "blockers": blockers,
        "state": "blocked" if blockers else "ready",
    }
    return _seal(plan_body, "workbench-environment-import-plan", "plan_id")


def apply_import(
    suite_root: Path,
    share: Mapping[str, Any],
    *,
    expected_plan_id: str,
    workspace_name: str,
    workspace: Path | str,
    config_path: Path | str = CONFIGURATION_PATH,
    java_home: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Bind the exact reviewed share to a local workspace and retain a receipt."""

    values = dict(os.environ if environment is None else environment)
    portable = validate_share(dict(share))
    plan = plan_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=config_path, java_home=java_home, environment=values,
        host=host,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("environment import plan changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("environment import is blocked: " + "; ".join(plan["blockers"]))
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    prepared = {
        "format": "workbench-environment-import-attempt-v1",
        "schema_version": 1,
        "state": "prepared",
        "share": portable,
        "plan_id": plan["plan_id"],
        "expected_workspaces_record_id": plan["expected_workspaces_record_id"],
        "workspace_name": workspace_name,
    }
    prepared_payload = _canonical(prepared) + b"\n"
    prepared_ref = service.publish_bytes(
        "evidence", "environment-import-attempt.json", prepared_payload,
        domain_id=portable["share_id"],
    )
    admitted = plan_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=config_path, java_home=java_home, environment=values,
        host=host,
    )
    if admitted["plan_id"] != plan["plan_id"] or admitted["state"] != "ready":
        raise ReconstructionError("environment inputs changed after the prepared attempt")
    registry = bind_workspace_selection(
        workspace_name, plan["workspace"],
        profile_config=plan["profile_config"],
        java_home=plan["java_home"],
        managed_java_feature=plan["managed_java_feature"],
        expected_record_id=plan["expected_workspaces_record_id"],
        environment=values,
    )
    entry = next(row for row in registry["entries"] if row["name"] == workspace_name)
    receipt = {
        "format": RESULT_FORMAT,
        "schema_version": 1,
        "outcome": "reused" if plan["action"] == "reuse" else "bound",
        "share": portable,
        "plan_id": plan["plan_id"],
        "attempt_resource_id": prepared_ref.resource_id,
        "workspace_id": entry["workspace_id"],
        "workspaces_record_id": registry["record_id"],
        "profile_selection_digest": plan["profile_selection_digest"],
        "unresolved_inputs": plan["unresolved_inputs"],
        "scope": "Local selections only; project bytes and managed dependencies require separate acquisition.",
    }
    payload = _canonical(receipt) + b"\n"
    complete_service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    reference = complete_service.publish_bytes(
        "evidence", "environment-reconstruction.json", payload,
        domain_id=portable["share_id"], references=(prepared_ref.resource_id,),
    )
    if complete_service.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("environment reconstruction receipt did not reopen exactly")
    return {
        **receipt,
        "resource": {
            "resource_id": reference.resource_id,
            "store_id": reference.store_id,
            "path": str(reference.path),
            "sha256": reference.sha256,
        },
    }


__all__ = [
    "ReconstructionError", "apply_import", "build_share", "export_share",
    "load_share", "plan_import", "validate_share",
]
