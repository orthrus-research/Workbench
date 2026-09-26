"""Portable environment choices with exact profile locks and local binding.

The share document contains no machine path. Core binds it to a matching
Configuration V1 manifest and workspace only on the receiving host. When the
manifest is missing, Core can generate an immutable local one from the exact
intent after checking the target suite's profile documents. Project bytes,
optional packages, fixtures and Java archives remain unresolved inputs. A
separate read-only feasibility report identifies missing acquisition locks.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping
from urllib.parse import urlsplit

from . import configuration as configuration_source
from .configuration import (
    CONFIGURATION_PATH,
    SCHEMA as CONFIGURATION_SCHEMA,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .environment_resolution import resolve_environment
from .runtime_java import (
    JavaRuntimeError,
    ensure_java_runtime,
    host_platform,
    load_java_runtime_policy,
    select_managed_java_policy,
)
from .setup_cli import _workspace
from .portable_managed_tools import (
    PortableToolLockError, build_managed_tool_lock,
    inspect_locked_managed_tools, matches_local_managed_tool_policy,
    validate_managed_tool_lock,
)
from .storage.registered import CoreDurableResources
from .storage.registered import ResourceCatalog
from .user_preferences import (
    UserPreferencesError,
    bind_workspace_selection,
    load_workspaces,
    resolve_expression,
    resolve_java_path,
    resolve_selection_path,
)


SHARE_FORMAT = "workbench-environment-share-v1"
SHARE_FORMAT_V2 = "workbench-environment-share-v2"
SHARE_FORMAT_V3 = "workbench-environment-share-v3"
INTENT_FORMAT = "workbench-environment-intent-v1"
LOCK_FORMAT = "workbench-environment-lock-v1"
LOCK_FORMAT_V2 = "workbench-environment-lock-v2"
LOCK_FORMAT_V3 = "workbench-environment-lock-v3"
PLAN_FORMAT = "workbench-environment-import-plan-v1"
PLAN_FORMAT_V2 = "workbench-environment-import-plan-v2"
PLAN_FORMAT_V3 = "workbench-environment-import-plan-v3"
PLAN_FORMAT_V4 = "workbench-environment-import-plan-v4"
RESULT_FORMAT = "workbench-environment-import-result-v1"
RESULT_FORMAT_V2 = "workbench-environment-import-result-v2"
RESULT_FORMAT_V3 = "workbench-environment-import-result-v3"
RESULT_FORMAT_V4 = "workbench-environment-import-result-v4"
FEASIBILITY_FORMAT = "workbench-environment-feasibility-v1"
FEASIBILITY_FORMAT_V2 = "workbench-environment-feasibility-v2"
MAX_SHARE_BYTES = 256 * 1024
MAX_SOURCE_LOCK_BYTES = 256 * 1024
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
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
    bind_project_source_lock: bool = False,
    bind_managed_tools: bool = False,
) -> dict[str, Any]:
    """Describe one saved workspace without exporting any local path choice."""

    values = dict(os.environ if environment is None else environment)
    if type(bind_project_source_lock) is not bool:
        raise ReconstructionError("project source-lock binding choice must be Boolean")
    if type(bind_managed_tools) is not bool or bind_managed_tools and not bind_project_source_lock:
        raise ReconstructionError("managed-tool lock requires an exact project source lock")
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
    lock_body = {
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
    }
    if bind_project_source_lock:
        project = _project_source_feasibility(suite, configuration)
        if project["state"] != "local-lock-unbound":
            raise ReconstructionError(
                f"selected pack variant has no usable exact project source lock: {project['state']}"
            )
        lock_body.update({
            "format": LOCK_FORMAT_V2,
            "schema_version": 2,
            "project_source_lock": project["candidate"],
        })
    if bind_managed_tools:
        try:
            managed_tool_lock = build_managed_tool_lock(lock_body["host_variant"])
        except PortableToolLockError as exc:
            raise ReconstructionError(str(exc)) from exc
        lock_body.update({
            "format": LOCK_FORMAT_V3,
            "schema_version": 3,
            "managed_tool_lock": managed_tool_lock,
        })
    lock = _seal(lock_body, "workbench-environment-lock", "lock_id")
    return _seal({
        "format": SHARE_FORMAT_V3 if bind_managed_tools else SHARE_FORMAT_V2 if bind_project_source_lock else SHARE_FORMAT,
        "schema_version": 3 if bind_managed_tools else 2 if bind_project_source_lock else 1,
        "intent": intent,
        "lock": lock,
    }, "workbench-environment-share", "share_id")


def validate_share(value: object) -> dict[str, Any]:
    """Strictly read the portable bytes before consulting local state."""

    share = _exact(value, {"format", "schema_version", "intent", "lock", "share_id"}, "share")
    version = share["schema_version"]
    if type(version) is not int or type(share["format"]) is not str or (share["format"], version) not in {
        (SHARE_FORMAT, 1), (SHARE_FORMAT_V2, 2), (SHARE_FORMAT_V3, 3),
    }:
        raise ReconstructionError("unsupported environment share schema")
    binds_project_source = version >= 2
    binds_managed_tools = version == 3
    intent = _exact(share["intent"], {"format", "schema_version", "selection", "java", "intent_id"}, "intent")
    lock_fields = {
        "format", "schema_version", "intent_id", "selection_digest", "pack_profile",
        "platform_profile", "java_policy", "host_variant", "unresolved_inputs", "lock_id",
    }
    if binds_project_source:
        lock_fields.add("project_source_lock")
    if binds_managed_tools:
        lock_fields.add("managed_tool_lock")
    lock = _exact(share["lock"], lock_fields, "lock")
    if intent["format"] != INTENT_FORMAT or type(intent["schema_version"]) is not int or intent["schema_version"] != 1:
        raise ReconstructionError("unsupported environment intent schema")
    if type(lock["schema_version"]) is not int or (lock["format"], lock["schema_version"]) != (
        (LOCK_FORMAT_V3, 3) if binds_managed_tools else
        (LOCK_FORMAT_V2, 2) if binds_project_source else (LOCK_FORMAT, 1)
    ):
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
    if binds_project_source:
        project = _exact(lock["project_source_lock"], {
            "relative_path", "sha256", "repository", "revision", "tree",
        }, "project source lock")
        relative = project["relative_path"]
        if type(relative) is not str or "\\" in relative:
            raise ReconstructionError("project source lock path is invalid")
        source_path = PurePosixPath(relative)
        profile_parent = PurePosixPath(selection["pack_document"]).parent
        if (
            source_path.as_posix() != relative
            or not source_path.is_relative_to(profile_parent)
            or source_path == profile_parent or source_path.suffix != ".json"
            or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) is None
                   for part in source_path.parts)
            or type(project["sha256"]) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", project["sha256"]) is None
            or not _git_repository(project["repository"])
            or type(project["revision"]) is not str
            or _GIT_ID.fullmatch(project["revision"]) is None
            or type(project["tree"]) is not str
            or _GIT_ID.fullmatch(project["tree"]) is None
        ):
            raise ReconstructionError("project source lock is incomplete or non-portable")
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
    if binds_managed_tools:
        try:
            tool_lock = validate_managed_tool_lock(lock["managed_tool_lock"])
        except PortableToolLockError as exc:
            raise ReconstructionError(f"portable managed-tool lock is invalid: {exc}") from exc
        if tool_lock["host_variant"] != host:
            raise ReconstructionError("managed-tool lock targets another host variant")
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


def _reject_json_constant(token: str) -> None:
    raise ReconstructionError(f"non-finite source-lock value: {token}")


def _git_repository(value: object) -> bool:
    if type(value) is not str or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https" and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and parsed.path.startswith("/") and parsed.path.endswith(".git")
            and not parsed.query and not parsed.fragment
        )
    except ValueError:
        return False


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


def _configuration_bytes(selection: Mapping[str, str]) -> bytes:
    """Encode only the selected portable authority; no local binding defaults."""

    quoted = lambda value: json.dumps(value, ensure_ascii=False)
    return (
        f'schema = {quoted(CONFIGURATION_SCHEMA)}\n\n'
        '[selection]\n'
        f'pack_document = {quoted(selection["pack_document"])}\n'
        f'pack_variant = {quoted(selection["pack_variant"])}\n'
        f'platform_document = {quoted(selection["platform_document"])}\n\n'
        '[bindings]\n'
    ).encode("utf-8")


def _configuration_from_intent(
    suite: Path, path: Path, payload: bytes, selection: Mapping[str, str],
) -> configuration_source.WorkbenchConfiguration:
    """Inspect profile authority in memory before publishing a manifest."""

    source = configuration_source
    pack_relative = source._profile_relative_path(selection["pack_document"], "/selection/pack_document")
    platform_relative = source._profile_relative_path(selection["platform_document"], "/selection/platform_document")
    pack_source = source._source_snapshot(
        source._suite_profile_path(suite, pack_relative, "/selection/pack_document"),
        maximum=source.MAX_PROFILE_BYTES, label="pack profile", relative_path=pack_relative,
    )
    platform_source = source._source_snapshot(
        source._suite_profile_path(suite, platform_relative, "/selection/platform_document"),
        maximum=source.MAX_PROFILE_BYTES, label="platform profile", relative_path=platform_relative,
    )
    pack_values = source._yaml_object(pack_source, "pack profile")
    platform_values = source._yaml_object(platform_source, "platform profile")
    pack_schema = source._profile_schema(pack_values, "/pack_document/schema_version")
    platform_schema = source._profile_schema(platform_values, "/platform_document/schema_version")
    pack_id = source._embedded_id(
        pack_values, "profile_family_id", source._PACK_PROFILE_ID,
        "/pack_document/profile_family_id",
    )
    platform_id = source._embedded_id(
        platform_values, "profile_id", source._PLATFORM_PROFILE_ID,
        "/platform_document/profile_id",
    )
    variants = pack_values.get("profiles")
    if type(variants) is not dict:
        raise WorkbenchConfigurationError("/pack_document/profiles: must be a table")
    variant = variants.get(selection["pack_variant"])
    if type(variant) is not dict:
        raise WorkbenchConfigurationError("/selection/pack_variant: must name a variant in the selected pack document")
    if variant.get("platform_profile_id") != platform_id:
        raise WorkbenchConfigurationError("selected pack variant does not bind the selected platform profile")
    pack = source.ProfileSnapshot(
        pack_source, pack_id, pack_schema, source._freeze(pack_values),
    )
    platform = source.ProfileSnapshot(
        platform_source, platform_id, platform_schema, source._freeze(platform_values),
    )
    manifest = source.SourceSnapshot(
        path=path,
        relative_path=path.relative_to(suite).as_posix() if path.is_relative_to(suite) else None,
        source_bytes=payload,
        sha256=sha256(payload).hexdigest(),
    )
    return source.WorkbenchConfiguration(
        schema=CONFIGURATION_SCHEMA,
        manifest=manifest,
        pack_document=pack,
        pack_variant=selection["pack_variant"],
        platform_document=platform,
        selection_digest=source._selection_digest(
            pack=pack, pack_variant=selection["pack_variant"], platform=platform,
        ),
        binding_declarations=(),
    )


def _configuration_blockers(
    suite: Path, configuration: configuration_source.WorkbenchConfiguration,
    portable: Mapping[str, Any],
    *, include_project_source: bool = True,
    include_managed_tools: bool = True,
) -> list[str]:
    intent = portable["intent"]["selection"]
    lock = portable["lock"]
    blockers: list[str] = []
    if (
        configuration.selection_digest != lock["selection_digest"]
        or configuration.pack_document.source.relative_path != intent["pack_document"]
        or configuration.pack_variant != intent["pack_variant"]
        or configuration.platform_document.source.relative_path != intent["platform_document"]
        or configuration.pack_profile_id != lock["pack_profile"]["profile_id"]
        or configuration.pack_document.source.sha256 != lock["pack_profile"]["sha256"]
        or configuration.platform_profile_id != lock["platform_profile"]["profile_id"]
        or configuration.platform_document.source.sha256 != lock["platform_profile"]["sha256"]
    ):
        blockers.append("target suite profile selection or document bytes differ from the exact lock")
    try:
        default_policy = load_java_runtime_policy(suite, configuration=configuration)
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
    if include_managed_tools and portable["format"] == SHARE_FORMAT_V3:
        if not matches_local_managed_tool_policy(lock["managed_tool_lock"]):
            blockers.append("target managed-tool policy differs from the exact portable lock")
    if include_project_source and portable["format"] in {SHARE_FORMAT_V2, SHARE_FORMAT_V3}:
        project = _project_source_feasibility(suite, configuration)
        if (
            project["state"] != "local-lock-unbound"
            or project["candidate"] != lock["project_source_lock"]
        ):
            blockers.append("target project source lock differs from the exact portable lock")
    return blockers


def _generated_manifest_resource(
    catalog: ResourceCatalog, workspace: Path, path: Path, digest: str,
) -> str | None:
    """Reopen only a committed Core publication at the reserved local path."""

    rows = [
        row for row in catalog.inventory(workspace=workspace)["resources"]
        if row["path"] == str(path) and row["status"] == "committed"
        and row["owner_id"] == "workbench-core" and row["role"] == "artifacts"
        and row["sha256"] == "sha256:" + digest
    ]
    return rows[0]["resource_id"] if len(rows) == 1 else None


def _project_source_feasibility(
    suite: Path, configuration: configuration_source.WorkbenchConfiguration | None,
) -> dict[str, Any]:
    """Inspect a local source-lock candidate without treating it as portable authority."""

    missing = {
        "state": "selected-profile-unavailable", "candidate": None,
        "required": ["selected-profile", "portable-source-lock-sha256"],
    }
    if configuration is None:
        return missing
    variant = configuration.pack_document.values["profiles"][configuration.pack_variant]
    reference = variant.get("source_lock")
    if reference is None:
        return {
            "state": "missing-variant-lock", "candidate": None,
            "required": ["pack-source-repository", "pack-source-revision", "pack-source-tree",
                         "portable-source-lock-sha256"],
        }
    if (type(reference) is not str or not reference or "\\" in reference
            or "\x00" in reference):
        return {
            "state": "invalid-lock-reference", "candidate": None,
            "required": ["safe-variant-source-lock", "portable-source-lock-sha256"],
        }
    relative = PurePosixPath(reference)
    if (relative.is_absolute() or relative.as_posix() != reference
            or relative.suffix != ".json"
            or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) is None
                   for part in relative.parts)):
        return {
            "state": "invalid-lock-reference", "candidate": None,
            "required": ["safe-variant-source-lock", "portable-source-lock-sha256"],
        }
    profile_relative = PurePosixPath(configuration.pack_document.source.relative_path)
    selected_relative = (profile_relative.parent / relative).as_posix()
    try:
        path = configuration_source._suite_profile_path(
            suite, selected_relative, "/pack_document/profiles/source_lock",
        )
        snapshot = configuration_source._source_snapshot(
            path, maximum=MAX_SOURCE_LOCK_BYTES, label="pack source lock",
            relative_path=selected_relative,
        )
    except WorkbenchConfigurationError:
        return {
            "state": "referenced-lock-unavailable", "candidate": None,
            "required": ["exact-source-lock-file", "portable-source-lock-sha256"],
        }
    try:
        document = json.loads(
            snapshot.source_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs,
            parse_constant=_reject_json_constant,
        )
        pack = document["pack"]
        sources = document["sources"]
        valid = (
            type(document) is dict
            and set(document) == {"format", "schema_version", "lock_id", "pack", "sources"}
            and document["format"] == "workbench-source-lock-v3"
            and type(document["schema_version"]) is int
            and document["schema_version"] == 3
            and type(document["lock_id"]) is str
            and bool(document["lock_id"])
            and type(pack) is dict
            and set(pack) == {"source_id", "snapshot_id", "version", "repository", "revision", "tree"}
            and all(type(pack[key]) is str and bool(pack[key]) for key in (
                "source_id", "snapshot_id", "version",
            ))
            and _git_repository(pack["repository"])
            and type(pack["revision"]) is str
            and _GIT_ID.fullmatch(pack["revision"]) is not None
            and type(pack["tree"]) is str
            and _GIT_ID.fullmatch(pack["tree"]) is not None
            and type(sources) is list
            and bool(sources)
            and all(
                type(item) is dict
                and set(item) == {"source_id", "repository", "revision", "tree", "license", "scope"}
                and all(type(item[key]) is str and bool(item[key]) for key in (
                    "source_id", "license", "scope",
                ))
                and _git_repository(item["repository"])
                and type(item["revision"]) is str
                and _GIT_ID.fullmatch(item["revision"]) is not None
                and type(item["tree"]) is str
                and _GIT_ID.fullmatch(item["tree"]) is not None
                for item in sources
            )
            and snapshot.source_bytes == _canonical(document) + b"\n"
        )
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        valid = False
    if not valid:
        return {
            "state": "referenced-lock-invalid", "candidate": None,
            "required": ["valid-exact-source-lock", "portable-source-lock-sha256"],
        }
    return {
        "state": "local-lock-unbound",
        "candidate": {
            "relative_path": selected_relative,
            "sha256": "sha256:" + snapshot.sha256,
            "repository": pack["repository"],
            "revision": pack["revision"],
            "tree": pack["tree"],
        },
        "required": ["portable-source-lock-sha256"],
    }


def assess_reconstruction_feasibility(
    suite_root: Path, share: Mapping[str, Any], *, workspace_name: str,
    workspace: Path | str, config_path: Path | str = CONFIGURATION_PATH,
    java_home: Path | str | None = None, acquire_managed_java: bool = False,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Report exact inputs still needed before a clean-root acquisition can run.

    This never acquires bytes or alters earlier share, plan, and import identities.
    A V1 target-side lock file remains a candidate because the share does not
    bind its hash; V2 can prove the exact lock while project bytes remain absent.
    """

    portable = validate_share(dict(share))
    suite = Path(suite_root).expanduser().resolve()
    plan = plan_import(
        suite, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=config_path, java_home=java_home,
        acquire_managed_java=acquire_managed_java, environment=environment, host=host,
    )
    selection = portable["intent"]["selection"]
    configuration = None
    try:
        candidate = _configuration_from_intent(
            suite, Path(plan["profile_config"]), _configuration_bytes(selection), selection,
        )
        if not _configuration_blockers(
            suite, candidate, portable, include_project_source=False,
            include_managed_tools=False,
        ):
            configuration = candidate
    except WorkbenchConfigurationError:
        pass
    project = _project_source_feasibility(suite, configuration)
    if portable["format"] in {SHARE_FORMAT_V2, SHARE_FORMAT_V3} and project["state"] == "local-lock-unbound":
        project = {
            **project,
            "state": (
                "portable-lock-bound"
                if project["candidate"] == portable["lock"]["project_source_lock"]
                else "local-lock-drifted"
            ),
            "required": (
                ["workspace-project-bytes"]
                if project["candidate"] == portable["lock"]["project_source_lock"]
                else ["matching-source-lock-file"]
            ),
        }
    java_mode = portable["intent"]["java"]["mode"]
    java_state = (
        "local-binding-unchecked" if java_home is not None else "local-binding-missing"
    ) if java_mode == "local-binding-required" else "managed-archive-unresolved"
    body = {
        "format": FEASIBILITY_FORMAT_V2 if portable["format"] == SHARE_FORMAT_V3 else FEASIBILITY_FORMAT,
        "schema_version": 2 if portable["format"] == SHARE_FORMAT_V3 else 1,
        "share_id": portable["share_id"],
        "import_plan_id": plan["plan_id"],
        "import_state": plan["state"],
        "import_blockers": list(plan["blockers"]),
        "state": "local-import-blocked" if plan["state"] == "blocked" else "exact-inputs-required",
        "project_source": project,
        "optional_module_packages": {
            "state": "missing-package-lock",
            "required": ["module-identity", "package-sha256"],
        },
        "profile_fixture_tools": {
            "state": "missing-profile-fixture-lock" if portable["format"] == SHARE_FORMAT_V3 else "missing-artifact-lock",
            "required": (["fixture-identity", "fixture-sha256"]
                         if portable["format"] == SHARE_FORMAT_V3 else ["artifact-identity", "artifact-sha256"]),
        },
        "java": {
            "state": java_state,
            "feature_version": portable["lock"]["java_policy"]["feature_version"],
            "reviewed_managed_acquisition": acquire_managed_java,
        },
        "unresolved_inputs": list(plan["unresolved_inputs"]),
    }
    if portable["format"] == SHARE_FORMAT_V3:
        local = resolve_environment(suite, workspace=Path(plan["workspace"]), environment=environment)
        if plan["host_variant"] != portable["lock"]["host_variant"]:
            tool_state = {"state": "unsupported-platform", "required": ["matching-managed-tool-host"]}
        else:
            tool_state = inspect_locked_managed_tools(
                portable["lock"]["managed_tool_lock"], state_root=local.state_root,
            )
        body["managed_tools"] = tool_state
    return _seal(body, "workbench-environment-feasibility", "feasibility_id")


def export_share(
    suite_root: Path, workspace_name: str, *, environment: Mapping[str, str] | None = None,
    bind_project_source_lock: bool = False,
    bind_managed_tools: bool = False,
) -> dict[str, Any]:
    """Publish the share through Core's registered immutable artifact service."""

    values = dict(os.environ if environment is None else environment)
    source_registry = load_workspaces(environment=values)
    share = build_share(
        suite_root, workspace_name, environment=values,
        bind_project_source_lock=bind_project_source_lock,
        bind_managed_tools=bind_managed_tools,
    )
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
    acquire_managed_java: bool = False,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Check a target suite and local binding without writing any state."""

    portable = validate_share(dict(share))
    if type(acquire_managed_java) is not bool:
        raise ReconstructionError("managed Java acquisition choice must be Boolean")
    if acquire_managed_java and portable["intent"]["java"]["mode"] == "local-binding-required":
        raise ReconstructionError("a user-supplied Java path cannot be acquired as managed Java")
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
    registry = load_workspaces(environment=values)
    resolution = resolve_environment(suite, workspace=selected_workspace, environment=values)
    if resolution.record["workspaces"]["record_id"] != registry["record_id"]:
        raise ReconstructionError("user workspaces changed during import planning")
    selection = portable["intent"]["selection"]
    manifest_payload = _configuration_bytes(selection)
    manifest_digest = sha256(manifest_payload).hexdigest()
    requested_path = configuration_source._manifest_path(suite, config_path)
    generated_path = (
        Path(resolution.locations["artifacts"])
        / "environment-configurations"
        / sha256(os.fsencode(selected_workspace)).hexdigest()
        / f"{manifest_digest}.toml"
    )
    target_configuration = None
    manifest_action = "unavailable"
    manifest_resource_id = None
    requested_present = requested_path.exists() or requested_path.is_symlink()
    generate = not requested_present
    if requested_present:
        try:
            target_configuration = load_workbench_configuration(suite, requested_path)
            manifest_action = "existing"
            if (
                Path(config_path) == CONFIGURATION_PATH
                and _configuration_blockers(
                    suite, target_configuration, portable, include_managed_tools=False,
                )
            ):
                # Keep the suite's valid default for its existing users. The
                # import may select a separate reviewed local manifest.
                target_configuration = None
                generate = True
        except WorkbenchConfigurationError as exc:
            blockers.append(f"matching local Configuration V1 manifest is unavailable: {exc}")
    if generate:
        if generated_path.exists() or generated_path.is_symlink():
            try:
                target_configuration = load_workbench_configuration(suite, generated_path)
                if target_configuration.manifest.sha256 != manifest_digest:
                    raise ReconstructionError("generated Configuration V1 manifest bytes changed")
                manifest_resource_id = _generated_manifest_resource(
                    ResourceCatalog(resolution.configuration_home),
                    selected_workspace, generated_path, manifest_digest,
                )
                if manifest_resource_id is None:
                    raise ReconstructionError("generated Configuration V1 manifest is outside Core custody")
                manifest_action = "reuse-generated"
            except (WorkbenchConfigurationError, ReconstructionError) as exc:
                blockers.append(f"matching local Configuration V1 manifest is unavailable: {exc}")
        else:
            try:
                target_configuration = _configuration_from_intent(
                    suite, generated_path, manifest_payload, selection,
                )
                manifest_action = "create"
            except WorkbenchConfigurationError as exc:
                blockers.append(f"matching target profile documents are unavailable: {exc}")
    if target_configuration is not None:
        blockers.extend(_configuration_blockers(suite, target_configuration, portable))
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
    old = next((entry for entry in registry["entries"] if entry["name"] == workspace_name), None)
    for entry in registry["entries"]:
        registered = resolve_expression(entry["path"], environment=values)
        if entry["name"] != workspace_name and registered == selected_workspace:
            blockers.append("another named workspace already uses this directory")
    if old is not None and resolve_expression(old["path"], environment=values) != selected_workspace:
        blockers.append("workspace name is already bound to another directory")
    local_config = str(target_configuration.manifest.path) if target_configuration else str(requested_path)
    same = (
        old is not None
        and registry["schema_version"] == 3
        and old["profile_config"] == local_config
        and old["java_home"] == selected_java
        and old["managed_java_feature"] == portable["intent"]["java"]["feature_version"]
    )
    action = "reuse" if same else "update" if old is not None else "create"
    source_bound = portable["format"] in {SHARE_FORMAT_V2, SHARE_FORMAT_V3}
    tool_bound = portable["format"] == SHARE_FORMAT_V3
    plan_body = {
        "format": (
            PLAN_FORMAT_V4 if tool_bound else PLAN_FORMAT_V3 if source_bound
            else PLAN_FORMAT_V2 if acquire_managed_java else PLAN_FORMAT
        ),
        "schema_version": 4 if tool_bound else 3 if source_bound else 2 if acquire_managed_java else 1,
        "share_id": portable["share_id"],
        "lock_id": portable["lock"]["lock_id"],
        "workspace_name": workspace_name,
        "workspace": str(selected_workspace),
        "requested_profile_config": str(requested_path),
        "profile_config": local_config,
        "profile_selection_digest": (
            target_configuration.selection_digest if target_configuration else None
        ),
        "configuration_manifest_action": manifest_action,
        "configuration_manifest_sha256": (
            target_configuration.manifest.sha256 if target_configuration else None
        ),
        "configuration_manifest_resource_id": manifest_resource_id,
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
    if acquire_managed_java:
        plan_body["acquire_managed_java"] = True
    if source_bound:
        plan_body["project_source_lock"] = portable["lock"]["project_source_lock"]
    if tool_bound:
        plan_body["managed_tool_lock"] = portable["lock"]["managed_tool_lock"]
    return _seal(plan_body, "workbench-environment-import-plan", "plan_id")


def _acquire_locked_managed_java(
    suite: Path,
    configuration: configuration_source.WorkbenchConfiguration,
    *,
    state_root: Path,
    feature: int | None,
    policy_lock: Mapping[str, Any],
    variant_lock: Mapping[str, str],
) -> dict[str, str]:
    """Acquire only the reviewed profile release, then identify its verified receipt."""

    selected_policy = select_managed_java_policy(
        load_java_runtime_policy(suite, configuration=configuration), feature,
    )
    if selected_policy["policy_sha256"] != policy_lock["selected_policy_sha256"]:
        raise ReconstructionError("managed Java policy changed before acquisition")
    acquired = ensure_java_runtime(
        suite, configuration=configuration, state_root=state_root,
        candidates=(), managed_feature_version=feature,
    )
    receipt = acquired.get("receipt")
    if (
        acquired.get("format") != "workbench-java-runtime-result-v2"
        or acquired.get("source") != "managed"
        or acquired.get("outcome") not in {"provisioned", "reused"}
        or type(receipt) is not dict
        or receipt.get("format") != "workbench-java-runtime-receipt-v2"
        or receipt.get("state") != "ready"
        or receipt.get("policy") != selected_policy
        or receipt.get("host") != host_platform()
        or type(receipt.get("runtime_id")) is not str
        or type(receipt.get("target")) is not dict
        or type(receipt["target"].get("receipt_uri")) is not str
    ):
        raise ReconstructionError("managed Java acquisition did not return the locked runtime")
    if _host_variant(receipt["host"]) != variant_lock:
        raise ReconstructionError("managed Java acquisition returned another host variant")
    return {
        "outcome": acquired["outcome"],
        "runtime_id": receipt["runtime_id"],
        "receipt_uri": receipt["target"]["receipt_uri"],
        "policy_sha256": selected_policy["policy_sha256"],
    }


def apply_import(
    suite_root: Path,
    share: Mapping[str, Any],
    *,
    expected_plan_id: str,
    workspace_name: str,
    workspace: Path | str,
    config_path: Path | str = CONFIGURATION_PATH,
    java_home: Path | str | None = None,
    acquire_managed_java: bool = False,
    environment: Mapping[str, str] | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Bind the exact reviewed share to a local workspace and retain a receipt."""

    values = dict(os.environ if environment is None else environment)
    portable = validate_share(dict(share))
    plan = plan_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=config_path, java_home=java_home,
        acquire_managed_java=acquire_managed_java, environment=values,
        host=host,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("environment import plan changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("environment import is blocked: " + "; ".join(plan["blockers"]))
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("environment import resolution changed after review")
    source_bound = portable["format"] in {SHARE_FORMAT_V2, SHARE_FORMAT_V3}
    tool_bound = portable["format"] == SHARE_FORMAT_V3
    prepared = {
        "format": (
            "workbench-environment-import-attempt-v4" if tool_bound
            else "workbench-environment-import-attempt-v3" if source_bound
            else "workbench-environment-import-attempt-v2"
            if acquire_managed_java else "workbench-environment-import-attempt-v1"
        ),
        "schema_version": 4 if tool_bound else 3 if source_bound else 2 if acquire_managed_java else 1,
        "state": "prepared",
        "share": portable,
        "plan_id": plan["plan_id"],
        "expected_workspaces_record_id": plan["expected_workspaces_record_id"],
        "workspace_name": workspace_name,
        "configuration_manifest": {
            "action": plan["configuration_manifest_action"],
            "path": plan["profile_config"],
            "sha256": plan["configuration_manifest_sha256"],
            "resource_id": plan["configuration_manifest_resource_id"],
        },
    }
    if acquire_managed_java:
        prepared["acquire_managed_java"] = True
    if source_bound:
        prepared["project_source_lock"] = portable["lock"]["project_source_lock"]
    if tool_bound:
        prepared["managed_tool_lock"] = portable["lock"]["managed_tool_lock"]
    prepared_payload = _canonical(prepared) + b"\n"
    prepared_ref = service.publish_bytes(
        "evidence", "environment-import-attempt.json", prepared_payload,
        domain_id=portable["share_id"],
    )
    admitted = plan_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=config_path, java_home=java_home,
        acquire_managed_java=acquire_managed_java, environment=values,
        host=host,
    )
    if admitted["plan_id"] != plan["plan_id"] or admitted["state"] != "ready":
        raise ReconstructionError("environment inputs changed after the prepared attempt")
    manifest_resource_id = plan["configuration_manifest_resource_id"]
    if plan["configuration_manifest_action"] == "create":
        manifest_payload = _configuration_bytes(portable["intent"]["selection"])
        manifest_path = Path(plan["profile_config"])
        if (
            sha256(manifest_payload).hexdigest() != plan["configuration_manifest_sha256"]
            or not manifest_path.is_relative_to(service.locations["artifacts"])
        ):
            raise ReconstructionError("generated Configuration V1 manifest differs from the reviewed plan")
        manifest_ref = service.publish_bytes(
            "artifacts", manifest_path.name, manifest_payload,
            requested_path=manifest_path, domain_id=portable["intent"]["intent_id"],
            references=(prepared_ref.resource_id,),
        )
        if manifest_ref.path != manifest_path or service.read_bytes(manifest_ref.resource_id) != manifest_payload:
            raise ReconstructionError("generated Configuration V1 manifest did not reopen exactly")
        manifest_resource_id = manifest_ref.resource_id
    elif plan["configuration_manifest_action"] == "reuse-generated":
        if (
            manifest_resource_id is None
            or service.read_bytes(manifest_resource_id)
            != _configuration_bytes(portable["intent"]["selection"])
        ):
            raise ReconstructionError("generated Configuration V1 manifest is unavailable under Core custody")
    try:
        verified_configuration = load_workbench_configuration(
            Path(suite_root), plan["profile_config"],
        )
    except WorkbenchConfigurationError as exc:
        raise ReconstructionError(f"Configuration V1 manifest changed before binding: {exc}") from exc
    if (
        verified_configuration.manifest.sha256 != plan["configuration_manifest_sha256"]
        or _configuration_blockers(Path(suite_root), verified_configuration, portable)
    ):
        raise ReconstructionError("Configuration V1 manifest or target profiles changed before binding")
    if resolve_environment(
        suite_root, workspace=Path(plan["workspace"]), environment=values,
    ).record["resolution_id"] != plan["environment_resolution_id"]:
        raise ReconstructionError("environment inputs changed before binding")
    managed_java = None
    if acquire_managed_java:
        resolved = resolve_environment(
            suite_root, workspace=Path(plan["workspace"]), environment=values,
        )
        managed_java = _acquire_locked_managed_java(
            Path(suite_root), verified_configuration,
            state_root=resolved.state_root,
            feature=plan["managed_java_feature"],
            policy_lock=portable["lock"]["java_policy"],
            variant_lock=portable["lock"]["host_variant"],
        )
        try:
            current_configuration = load_workbench_configuration(
                Path(suite_root), plan["profile_config"],
            )
            changed = (
                current_configuration.manifest.sha256 != plan["configuration_manifest_sha256"]
                or _configuration_blockers(Path(suite_root), current_configuration, portable)
            )
        except WorkbenchConfigurationError as exc:
            raise ReconstructionError("Configuration V1 manifest changed during Java acquisition") from exc
        if changed:
            raise ReconstructionError("Configuration V1 manifest or target profiles changed during Java acquisition")
        if resolve_environment(
            suite_root, workspace=Path(plan["workspace"]), environment=values,
        ).record["resolution_id"] != plan["environment_resolution_id"]:
            raise ReconstructionError("environment inputs changed during Java acquisition")
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
        "format": (
            RESULT_FORMAT_V4 if tool_bound
            else RESULT_FORMAT_V3 if source_bound
            else RESULT_FORMAT_V2 if managed_java is not None else RESULT_FORMAT
        ),
        "schema_version": 4 if tool_bound else 3 if source_bound else 2 if managed_java is not None else 1,
        "outcome": (
            "reused"
            if plan["action"] == "reuse" and plan["configuration_manifest_action"] != "create"
            else "bound"
        ),
        "share": portable,
        "plan_id": plan["plan_id"],
        "attempt_resource_id": prepared_ref.resource_id,
        "workspace_id": entry["workspace_id"],
        "workspaces_record_id": registry["record_id"],
        "profile_selection_digest": plan["profile_selection_digest"],
        "configuration_manifest": {
            "path": plan["profile_config"],
            "sha256": plan["configuration_manifest_sha256"],
            "resource_id": manifest_resource_id,
        },
        "unresolved_inputs": [
            item for item in plan["unresolved_inputs"]
            if managed_java is None or item != "managed-java-archive"
        ],
        "scope": (
            "Project bytes and non-Java managed dependencies require separate acquisition."
            if managed_java is not None
            else "Local selections only; project bytes and managed dependencies require separate acquisition."
        ),
    }
    if managed_java is not None:
        receipt["managed_java"] = managed_java
    if source_bound:
        receipt["project_source_lock"] = portable["lock"]["project_source_lock"]
    if tool_bound:
        receipt["managed_tool_lock"] = portable["lock"]["managed_tool_lock"]
    payload = _canonical(receipt) + b"\n"
    complete_service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    references = (prepared_ref.resource_id,)
    if manifest_resource_id is not None:
        references += (manifest_resource_id,)
    reference = complete_service.publish_bytes(
        "evidence", "environment-reconstruction.json", payload,
        domain_id=portable["share_id"], references=references,
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


def plan_project_import(*args, **kwargs) -> dict[str, Any]:
    """Review the separate V2-share exact project-byte acquisition."""

    from .environment_project_import import plan_project_import as operation
    return operation(*args, **kwargs)


def apply_project_import(*args, **kwargs) -> dict[str, Any]:
    """Acquire or reuse only the reviewed Core-managed project input."""

    from .environment_project_import import apply_project_import as operation
    return operation(*args, **kwargs)


def plan_tool_import(*args, **kwargs) -> dict[str, Any]:
    """Review the separate V3-share Core-managed tool byte acquisition."""

    from .environment_tool_import import plan_tool_import as operation
    return operation(*args, **kwargs)


def apply_tool_import(*args, **kwargs) -> dict[str, Any]:
    """Acquire or reuse only the reviewed Prism and Packwiz bytes."""

    from .environment_tool_import import apply_tool_import as operation
    return operation(*args, **kwargs)


def plan_environment_composition(*args, **kwargs) -> dict[str, Any]:
    """Review prior V3 selection, project and tool results together."""

    from .environment_composition import plan_environment_composition as operation
    return operation(*args, **kwargs)


def apply_environment_composition(*args, **kwargs) -> dict[str, Any]:
    """Retain a Core-linked result for three independently verified inputs."""

    from .environment_composition import apply_environment_composition as operation
    return operation(*args, **kwargs)


def plan_environment_input_composition(*args, **kwargs) -> dict[str, Any]:
    """Review five separately retained V3 environment inputs."""

    from .environment_composition import plan_environment_input_composition as operation
    return operation(*args, **kwargs)


def apply_environment_input_composition(*args, **kwargs) -> dict[str, Any]:
    """Retain a linked result for five reviewed environment inputs."""

    from .environment_composition import apply_environment_input_composition as operation
    return operation(*args, **kwargs)


def reopen_environment_input_composition(*args, **kwargs) -> dict[str, Any]:
    """Reopen a linked V2 result and its five surviving inputs."""

    from .environment_composition import reopen_environment_input_composition as operation
    return operation(*args, **kwargs)


def plan_wheel_import(*args, **kwargs) -> dict[str, Any]:
    """Review exact optional wheel bytes for separate Core custody."""

    from .environment_wheel_import import plan_wheel_import as operation
    return operation(*args, **kwargs)


def apply_wheel_import(*args, **kwargs) -> dict[str, Any]:
    """Retain the reviewed wheel bytes without installing packages."""

    from .environment_wheel_import import apply_wheel_import as operation
    return operation(*args, **kwargs)


def reopen_wheel_import(*args, **kwargs) -> dict[str, Any]:
    """Reopen a retained wheel import result and its managed byte tree."""

    from .environment_wheel_import import reopen_wheel_import as operation
    return operation(*args, **kwargs)


def plan_package_closure(*args, **kwargs) -> dict[str, Any]:
    """Review an exact offline dependency closure without installing it."""

    from .environment_package_closure import plan_package_closure as operation
    return operation(*args, **kwargs)


def plan_package_import(*args, **kwargs) -> dict[str, Any]:
    """Review Core custody of an exact offline package wheelhouse."""

    from .environment_package_import import plan_package_import as operation
    return operation(*args, **kwargs)


def apply_package_import(*args, **kwargs) -> dict[str, Any]:
    """Retain a reviewed package closure without installing it."""

    from .environment_package_import import apply_package_import as operation
    return operation(*args, **kwargs)


def reopen_package_import(*args, **kwargs) -> dict[str, Any]:
    """Reopen the exact Core-managed package closure tree."""

    from .environment_package_import import reopen_package_import as operation
    return operation(*args, **kwargs)


def plan_package_install_preflight(*args, **kwargs) -> dict[str, Any]:
    """Review an isolated install target without creating or installing it."""

    from .environment_package_install_plan import plan_package_install_preflight as operation
    return operation(*args, **kwargs)


def apply_package_install(*args, **kwargs) -> dict[str, Any]:
    """Install only Core-retained wheel bytes at the reviewed isolated path."""

    from .environment_package_install import apply_package_install as operation
    return operation(*args, **kwargs)


def reconcile_package_install(*args, **kwargs) -> dict[str, Any]:
    """Reconcile completed install evidence or classify an incomplete attempt."""

    from .environment_package_install import reconcile_package_install as operation
    return operation(*args, **kwargs)


def reopen_package_install(*args, **kwargs) -> dict[str, Any]:
    """Reopen the prepared Core install and completed evidence."""

    from .environment_package_install import reopen_package_install as operation
    return operation(*args, **kwargs)


def admit_package_install(*args, **kwargs) -> dict[str, Any]:
    """Admit current installed distribution bytes against retained pip and wheels."""

    from .environment_package_admission import admit_package_install as operation
    return operation(*args, **kwargs)


def reopen_package_admission(*args, **kwargs) -> dict[str, Any]:
    """Recheck an exact Core-installed package admission result."""

    from .environment_package_admission import reopen_package_admission as operation
    return operation(*args, **kwargs)


def plan_environment_package_composition(*args, **kwargs) -> dict[str, Any]:
    """Review retained V3 inputs together with admitted installed packages."""

    from .environment_package_composition import plan_environment_package_composition as operation
    return operation(*args, **kwargs)


def plan_fixture_import(*args, **kwargs) -> dict[str, Any]:
    """Review the admitted profile fixture for separate Core custody."""

    from .environment_fixture_import import plan_fixture_import as operation
    return operation(*args, **kwargs)


def apply_fixture_import(*args, **kwargs) -> dict[str, Any]:
    """Retain exact owner fixture and preflight tool bytes."""

    from .environment_fixture_import import apply_fixture_import as operation
    return operation(*args, **kwargs)


def reopen_fixture_import(*args, **kwargs) -> dict[str, Any]:
    """Reopen a retained fixture result and its managed byte tree."""

    from .environment_fixture_import import reopen_fixture_import as operation
    return operation(*args, **kwargs)


__all__ = [
    "ReconstructionError", "apply_import", "assess_reconstruction_feasibility",
    "build_share", "export_share", "load_share", "plan_import", "validate_share",
    "plan_project_import", "apply_project_import", "plan_tool_import", "apply_tool_import",
    "plan_environment_composition", "apply_environment_composition",
    "plan_environment_input_composition", "apply_environment_input_composition",
    "reopen_environment_input_composition",
    "plan_wheel_import", "apply_wheel_import", "reopen_wheel_import",
    "plan_package_closure",
    "plan_package_import", "apply_package_import", "reopen_package_import",
    "plan_package_install_preflight",
    "apply_package_install", "reconcile_package_install", "reopen_package_install",
    "admit_package_install", "reopen_package_admission",
    "plan_environment_package_composition",
    "plan_fixture_import", "apply_fixture_import", "reopen_fixture_import",
]
