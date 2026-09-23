"""Shell orchestration for Atlas-owned retained-worldgen observation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .bootstrap import inspect_project
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)


AUDIT_FORMAT = "workbench-runtime-worldgen-audit-v1"
PROFILE_FORMAT = "workbench-runtime-worldgen-audit-profile-v1"
MAX_PROFILE_BYTES = 1024 * 1024


class RuntimeWorldgenAuditError(ValueError):
    """Raised when a worldgen audit cannot produce a trusted result."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_regular(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeWorldgenAuditError(
            f"{label} is not a regular file: {path}"
        )
    try:
        if path.stat().st_size > MAX_PROFILE_BYTES:
            raise RuntimeWorldgenAuditError(
                f"{label} exceeds the {MAX_PROFILE_BYTES}-byte audit limit"
            )
        with path.open("rb") as source:
            raw = source.read(MAX_PROFILE_BYTES + 1)
    except OSError as exc:
        raise RuntimeWorldgenAuditError(
            f"{label} cannot be read: {path}"
        ) from exc
    if len(raw) > MAX_PROFILE_BYTES:
        raise RuntimeWorldgenAuditError(
            f"{label} exceeds the {MAX_PROFILE_BYTES}-byte audit limit"
        )
    return raw


def _utf8(raw: bytes, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeWorldgenAuditError(
            f"{label} is not valid UTF-8"
        ) from exc


def _safe_profile_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeWorldgenAuditError(
            f"{label} must be a non-empty string"
        )
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeWorldgenAuditError(
            f"{label} escapes its pack profile"
        )
    if any(character in value for character in "*?["):
        raise RuntimeWorldgenAuditError(
            f"{label} cannot contain a glob"
        )
    return value


def _load_profile(
    configuration: WorkbenchConfiguration,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile_path = configuration.pack_document.source.path
    profile = configuration.pack_document.values
    declaration = profile.get("worldgen_audit")
    if not isinstance(declaration, Mapping):
        raise RuntimeWorldgenAuditError(
            "pack profile does not declare worldgen_audit"
        )
    relative = _safe_profile_relative(
        declaration.get("spec"),
        "worldgen audit spec",
    )
    profile_root = profile_path.parent.resolve()
    spec_path = (profile_root / relative).resolve()
    if not spec_path.is_relative_to(profile_root):
        raise RuntimeWorldgenAuditError(
            "worldgen audit spec escapes its pack profile"
        )
    raw_spec = _read_regular(spec_path, "worldgen audit profile")
    try:
        spec = json.loads(_utf8(raw_spec, "worldgen audit profile"))
    except json.JSONDecodeError as exc:
        raise RuntimeWorldgenAuditError(
            "worldgen audit profile is not valid JSON"
        ) from exc
    if (
        not isinstance(spec, dict)
        or spec.get("format") != PROFILE_FORMAT
        or spec.get("schema_version") != 1
        or not isinstance(spec.get("profile_id"), str)
    ):
        raise RuntimeWorldgenAuditError(
            "worldgen audit profile has an unsupported shape"
        )
    return spec, {
        "uri": spec_path.as_uri(),
        "sha256": sha256(raw_spec).hexdigest(),
        "size": len(raw_spec),
        "profile_id": spec["profile_id"],
        "maturity": str(declaration.get("maturity", "unresolved")),
    }


def _atlas_authority(suite_root: Path) -> tuple[Any, type[ValueError]]:
    atlas_source = suite_root / "modules/atlas/src"
    source_text = str(atlas_source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from workbench_atlas.runtime_worldgen_observation import (
            AtlasWorldgenObservationError,
            observe_runtime_worldgen,
        )
    except ImportError as exc:
        raise RuntimeWorldgenAuditError(
            f"Atlas worldgen observation authority is unavailable: {exc}"
        ) from exc
    return observe_runtime_worldgen, AtlasWorldgenObservationError


def audit_project_worldgen(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    runtime_root: Path | str,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Select the profile and transport one Atlas-owned observation."""

    suite = Path(suite_root).expanduser().resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeWorldgenAuditError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeWorldgenAuditError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    context = inspect_project(
        suite,
        workspace,
        configuration=active_configuration,
    )
    spec, spec_identity = _load_profile(active_configuration)
    observe, atlas_error = _atlas_authority(suite)
    try:
        observation = observe(
            workspace,
            runtime_root=runtime_root,
            profile=spec,
        )
    except atlas_error as exc:
        raise RuntimeWorldgenAuditError(str(exc)) from exc
    runtime = Path(runtime_root).expanduser().resolve()
    workspace_context = context["workspace_context"]
    report: dict[str, Any] = {
        "format": AUDIT_FORMAT,
        "schema_version": 1,
        "audit_id": "",
        "operation_class": "read-only",
        **observation,
        "source": {
            "workspace": {
                "root_uri": workspace_context["workspace"]["root_uri"],
                "revision": workspace_context["workspace"]["revision"],
            },
            "project": {
                "name": workspace_context["project"]["name"],
                "version": workspace_context["project"]["version"],
                "minecraft_version": workspace_context["project"][
                    "minecraft_version"
                ],
            },
            "pack_profile": {
                "profile_family_id": workspace_context["pack"][
                    "profile_family_id"
                ],
                "selected_profile": workspace_context["pack"][
                    "selected_profile"
                ],
            },
            "runtime_root_uri": runtime.as_uri(),
            "runtime_binding": {
                "state": "unverified",
                "basis": "user-selected-runtime-root",
            },
            "audit_profile": spec_identity,
        },
    }
    identity_payload = dict(report)
    identity_payload.pop("audit_id")
    report["audit_id"] = "sha256:" + sha256(
        _canonical_bytes(identity_payload)
    ).hexdigest()
    return report
