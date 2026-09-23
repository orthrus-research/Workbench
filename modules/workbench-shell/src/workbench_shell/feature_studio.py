"""Feature Studio composition over existing Workbench owner APIs.

Feature Studio is a developer-facing projection.  It preserves owner artifacts
byte-for-byte and never substitutes Shell policy for Atlas, Blueprints, profile,
or Crucible decisions.
"""

from __future__ import annotations

from urllib.request import url2pathname

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import ctypes
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping, NoReturn, Sequence
from urllib.parse import urlparse
from datetime import datetime

from .material_fluid_flow import (
    MaterialFluidFlowError,
    execute_material_fluid_trial,
    material_fluid_feature_profile,
    require_material_fluid_profile,
    plan_material_fluid_trial,
    validate_retained_material_fluid_receipt,
    validate_retained_material_fluid_success,
)


RESULT_FORMAT = "workbench-feature-studio-result-v2"
REQUEST_FORMAT = "workbench-feature-studio-request-v2"
CAPABILITY_FORMAT = "workbench-feature-studio-capability-projection-v2"
_FEATURE_KIND = "material-backed-fluid"


class FeatureStudioError(ValueError):
    """Raised when Feature Studio cannot preserve its owner-bound contract."""


def _fail(message: str) -> NoReturn:
    raise FeatureStudioError(message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        from workbench_api.canonical import canonical_json_bytes

        return canonical_json_bytes(value)
    except ModuleNotFoundError as exc:
        _fail("Feature Studio canonical custody authority is unavailable")
    except (TypeError, ValueError) as exc:
        _fail(f"Feature Studio value is outside canonical JSON V2: {exc}")


def _content_id(kind: str, value: Any) -> str:
    try:
        from workbench_api.canonical import content_id

        return content_id(kind, value)
    except ModuleNotFoundError:
        _fail("Feature Studio content-identity authority is unavailable")
    except (TypeError, ValueError) as exc:
        _fail(f"Feature Studio cannot seal {kind}: {exc}")


def _canonical_artifact(
    role: str,
    value: Mapping[str, Any],
    *,
    uri: str | None = None,
) -> dict[str, Any]:
    try:
        raw = _canonical_bytes(value)
    except (TypeError, ValueError) as exc:
        _fail(f"Feature Studio owner artifact is not canonical JSON: {exc}")
    artifact: dict[str, Any] = {
        "role": role,
        "format": value.get("format"),
        "projection_encoding": "workbench-canonical-json-v2",
        "canonical_sha256": sha256(raw).hexdigest(),
        "canonical_size": len(raw),
        "canonical_json": raw.decode("utf-8"),
        "retained": None,
    }
    if uri is not None:
        path, retained_raw, retained_value = _read_json_uri(uri, "owner artifact")
        if retained_value != value:
            _fail("Feature Studio retained owner artifact differs from its owner value")
        artifact["retained"] = {
            "uri": path.as_uri(),
            "sha256": sha256(retained_raw).hexdigest(),
            "size": len(retained_raw),
        }
    return artifact


def _retained_only_artifact(
    role: str,
    value: Mapping[str, Any],
    *,
    uri: str,
) -> dict[str, Any]:
    """Link legacy owner JSON whose semantic domain permits V2-forbidden values."""

    path, retained_raw, retained_value = _read_json_uri(uri, "owner artifact")
    if retained_value != value:
        _fail("Feature Studio retained owner artifact differs from its owner value")
    return {
        "role": role,
        "format": value.get("format"),
        "projection_encoding": "retained-owner-json-bytes",
        "canonical_sha256": None,
        "canonical_size": None,
        "canonical_json": None,
        "retained": {
            "uri": path.as_uri(),
            "sha256": sha256(retained_raw).hexdigest(),
            "size": len(retained_raw),
        },
    }


def _read_json_uri(uri: Any, label: str) -> tuple[Path, bytes, Mapping[str, Any]]:
    path, raw = _read_bytes_uri(uri, label)
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"Feature Studio {label} is not canonicalizable JSON: {exc}")
    if not isinstance(value, Mapping):
        _fail(f"Feature Studio {label} is not an object")
    return path, raw, value


def _read_bytes_uri(
    uri: Any,
    label: str,
    *,
    maximum_bytes: int = 128 * 1024 * 1024,
) -> tuple[Path, bytes]:
    if not isinstance(uri, str):
        _fail(f"Feature Studio {label} URI is unavailable")
    parsed = urlparse(uri)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"Feature Studio {label} must be a local retained file")
    lexical = Path(url2pathname(parsed.path))
    if not lexical.is_absolute():
        _fail(f"Feature Studio {label} path is not absolute")
    cursor = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            _fail(f"Feature Studio {label} traverses a symbolic link")
    try:
        path = lexical.resolve(strict=True)
    except OSError as exc:
        _fail(f"Feature Studio {label} is unavailable: {exc}")
    if not path.is_file():
        _fail(f"Feature Studio {label} is not a regular file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
    except OSError as exc:
        _fail(f"Feature Studio cannot read {label}: {exc}")
    if len(raw) > maximum_bytes:
        _fail(f"Feature Studio {label} exceeds its byte bound")
    return path, raw


def _workspace_from_uri(value: Any) -> Path:
    if not isinstance(value, str):
        _fail("Feature Studio owner plan lacks a workspace URI")
    parsed = urlparse(value)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        _fail("Feature Studio supports only local owner-plan workspaces")
    workspace = Path(url2pathname(parsed.path)).resolve()
    if not workspace.is_dir() or workspace.is_symlink():
        _fail("Feature Studio owner-plan workspace is unavailable or unsafe")
    return workspace


def _source_locations(
    plan: Mapping[str, Any],
    suite_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    source = plan.get("source")
    operations = plan.get("operations")
    if not isinstance(source, Mapping) or not isinstance(operations, list):
        _fail("Feature Studio owner plan lacks exact source operations")
    workspace = _workspace_from_uri(source.get("workspace_uri"))
    rows: list[dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            _fail("Feature Studio source operation is not an object")
        relative_value = operation.get("path")
        if not isinstance(relative_value, str) or not relative_value:
            _fail("Feature Studio source operation lacks a relative path")
        relative = Path(relative_value)
        if relative.is_absolute() or ".." in relative.parts:
            _fail("Feature Studio source operation path is unsafe")
        target = workspace.joinpath(*relative.parts)
        if target.is_symlink() or not target.is_file():
            _fail(f"Feature Studio source target is unavailable: {relative_value}")
        raw = target.read_bytes()
        digest = sha256(raw).hexdigest()
        if digest != operation.get("before_sha256"):
            _fail(f"Feature Studio source target drifted: {relative_value}")
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            _fail(f"Feature Studio source target is not UTF-8: {relative_value}: {exc}")
        line_count = max(1, len(text.splitlines()))
        diff = operation.get("diff")
        if not isinstance(diff, str):
            _fail("Feature Studio source operation lacks its owner diff")
        hunks = re.findall(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", diff, re.MULTILINE)
        if not hunks:
            _fail(f"Feature Studio source operation lacks an exact hunk: {relative_value}")
        for hunk_index, (old_start_raw, old_count_raw, new_start_raw, new_count_raw) in enumerate(hunks):
            old_start = int(old_start_raw)
            old_count = int(old_count_raw or "1")
            new_start = int(new_start_raw)
            new_count = int(new_count_raw or "1")
            end_line = old_start if old_count == 0 else old_start + old_count - 1
            if old_start > line_count + 1 or end_line > line_count:
                _fail(f"Feature Studio source hunk is outside its owner file: {relative_value}")
            rows.append(
                {
                    "workspace_uri": workspace.as_uri(),
                    "relative_path": relative.as_posix(),
                    "hunk_index": hunk_index,
                    "range": {
                        "start_line": old_start,
                        "end_line": end_line,
                        "coordinate_system": "unified-diff-lines-zero-for-empty-inclusive",
                    },
                    "planned_range": {
                        "start_line": new_start,
                        "end_line": new_start if new_count == 0 else new_start + new_count - 1,
                        "coordinate_system": "unified-diff-lines-zero-for-empty-inclusive",
                    },
                    "source_sha256": digest,
                    "planned_sha256": operation.get("content_sha256"),
                    "operation": operation.get("operation"),
                }
            )
    rows.sort(key=lambda row: (row["relative_path"].encode("utf-8"), row["hunk_index"]))
    profile = _profile_projection(plan, suite_root)
    expected_paths = {
        row["relative_path"] for row in profile["source_owners"]
    }
    if {row["relative_path"] for row in rows} != expected_paths:
        _fail("Feature Studio material-backed-fluid workspace must bind its three exact owners")
    return rows


def _profile_projection(
    plan: Mapping[str, Any],
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    profile = material_fluid_feature_profile(suite_root)
    blueprint = plan.get("blueprint")
    if (
        not isinstance(blueprint, Mapping)
        or blueprint.get("profile_family_id") != profile["profile_family_id"]
    ):
        _fail("Feature Studio owner plan does not match its pack profile authority")
    return profile


def _feature(
    plan: Mapping[str, Any],
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    profile = _profile_projection(plan, suite_root)
    blueprint = plan.get("blueprint")
    if not isinstance(blueprint, Mapping):
        _fail("Feature Studio owner plan lacks its Blueprint")
    blueprint_value = blueprint.get("blueprint")
    if not isinstance(blueprint_value, Mapping):
        _fail("Feature Studio owner plan lacks Blueprint metadata")
    parameters = blueprint_value.get("effective_parameters")
    if not isinstance(parameters, Mapping):
        _fail("Feature Studio owner plan lacks effective parameters")
    required = {
        "color",
        "material_id",
        "name",
        "registry_name",
        "symbol_name",
        "translation",
    }
    if set(parameters) != required:
        _fail("Feature Studio owner plan has an unexpected parameter projection")
    return {
        "feature_kind": profile["feature_kind"],
        "name": parameters["name"],
        "registry_name": parameters["registry_name"],
        "material_id": parameters["material_id"],
        "symbol_name": parameters["symbol_name"],
        "color": parameters["color"],
        "translation": parameters["translation"],
        "planned_material_resource": (
            profile["registry_namespace"] + ":" + parameters["registry_name"]
        ),
        "planned_fluid_registry_name": parameters["registry_name"],
    }


def _context(
    plan: Mapping[str, Any],
    owner_receipt: Mapping[str, Any] | None = None,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    blueprint = plan["blueprint"]
    observed = (
        isinstance(owner_receipt, Mapping)
        and owner_receipt.get("outcome") == "runtime-completed"
    )
    retained_profile_observation = (
        owner_receipt.get("profile_observation")
        if isinstance(owner_receipt, Mapping)
        and isinstance(owner_receipt.get("profile_observation"), Mapping)
        else {}
    )
    profile_observation = retained_profile_observation if observed else {}
    profile = (
        profile_observation.get("profile")
        if isinstance(profile_observation.get("profile"), Mapping)
        else {}
    )
    crucible_snapshot = (
        profile_observation.get("crucible_snapshot")
        if isinstance(profile_observation.get("crucible_snapshot"), Mapping)
        else {}
    )
    retained_runtime = (
        owner_receipt.get("runtime")
        if isinstance(owner_receipt, Mapping)
        and isinstance(owner_receipt.get("runtime"), Mapping)
        else {}
    )
    runtime = retained_runtime if observed else {}
    feature_profile = _profile_projection(plan, suite_root)
    return {
        "workspace_uri": plan["source"]["workspace_uri"],
        "source_revision": plan["source"]["revision"],
        "source_snapshot_id": plan["source_snapshot"]["snapshot_id"],
        "pack_profile_family_id": blueprint["profile_family_id"],
        "platform_profile_id": profile.get("platform_profile_id"),
        "pack_profile_id": profile.get("pack_profile_id"),
        "profile_assessment_format": profile_observation.get("format"),
        "profile_assessment_id": profile_observation.get("assessment_id"),
        "crucible_snapshot_id": crucible_snapshot.get("snapshot_id"),
        "runtime_session_id": runtime.get("session_id"),
        "runtime_plan_id": runtime.get("runtime_plan_id"),
        "materialization_id": runtime.get("materialization_id"),
        "final_launch_id": runtime.get("final_launch_id"),
        "runtime_launcher": plan["execution"]["launcher"],
        "runtime_target_policy": plan["execution"]["target_policy"],
        "physical_side": feature_profile["physical_side"],
        "platform_profile_state": "profile-observation-bound" if observed else "pending-verification",
        "evidence_layers": {
            "source": "observed-by-owner-census",
            "planned": "ready",
            "runtime": "observed" if observed else "pending",
        },
    }


def _assertion_rows(assertions: Any) -> list[dict[str, Any]]:
    if not isinstance(assertions, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    for key in sorted(assertions, key=lambda item: str(item).encode("utf-8")):
        value = assertions[key]
        if not isinstance(key, str) or not isinstance(value, Mapping):
            _fail("Feature Studio owner assertion projection is malformed")
        state = value.get("state")
        if not isinstance(state, str):
            _fail("Feature Studio owner assertion lacks a state")
        row = {"assertion_key": key, "state": state}
        meaning = value.get("meaning")
        if isinstance(meaning, str):
            row["meaning"] = meaning
        rows.append(row)
    return rows


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(f"Feature Studio timing source lacks {label}")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        _fail(f"Feature Studio timing source has an invalid {label}: {exc}")


def _duration_row(
    key: str,
    start: Any,
    end: Any,
    *,
    source_role: str,
    start_pointer: str,
    end_pointer: str,
) -> dict[str, Any]:
    milliseconds = int(
        (_parse_timestamp(end, "end timestamp") - _parse_timestamp(start, "start timestamp")).total_seconds()
        * 1000
    )
    if milliseconds < 0:
        _fail("Feature Studio timing source runs backwards")
    return {
        "timing_key": key,
        "value": milliseconds,
        "unit": "milliseconds",
        "source_role": source_role,
        "start_pointer": start_pointer,
        "end_pointer": end_pointer,
        "identity_role": "operational-only",
    }


def _session_timings(session: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        _duration_row(
            "runtime-session-total",
            session.get("started_at"),
            session.get("observed_at"),
            source_role="runtime-observation-session",
            start_pointer="/started_at",
            end_pointer="/observed_at",
        )
    ]
    launch = session.get("launch")
    process = launch.get("process_observation") if isinstance(launch, Mapping) else None
    if isinstance(process, Mapping):
        rows.append(
            _duration_row(
                "projected-process-attached-to-exit",
                process.get("attached_at"),
                process.get("observed_at"),
                source_role="runtime-observation-session",
                start_pointer="/launch/process_observation/attached_at",
                end_pointer="/launch/process_observation/observed_at",
            )
        )
    return rows


def _runtime_artifacts(
    receipt: Mapping[str, Any],
    reviewed_plan: Mapping[str, Any],
    suite_root: Path | str,
    *,
    receipt_uri: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if receipt.get("outcome") != "runtime-completed":
        return [], []
    try:
        validated = validate_retained_material_fluid_success(
            receipt,
            reviewed_plan,
            suite_root,
            receipt_uri=receipt_uri,
        )
    except MaterialFluidFlowError as exc:
        _fail(f"Feature Studio rejected retained runtime custody: {exc}")
    session = validated["session"]
    final_launch = validated["final_launch_receipt"]
    return (
        [
            _canonical_artifact(
                "blueprint-stage-receipt",
                validated["blueprint_stage"]["receipt"],
                uri=validated["blueprint_stage"]["receipt_uri"],
            ),
            _canonical_artifact(
                "runtime-observation-session",
                session,
                uri=validated["session_uri"],
            ),
            _retained_only_artifact(
                "runtime-final-launch-receipt",
                final_launch,
                uri=validated["final_launch_uri"],
            ),
            _canonical_artifact(
                "runtime-materialization-receipt",
                validated["materialization_receipt"],
                uri=validated["materialization_uri"],
            ),
        ],
        _session_timings(session),
    )


def _review_binding(
    expected_operation: str,
    *,
    flow_plan_id: str,
    source_snapshot_id: str,
) -> dict[str, Any]:
    names = {
        "catalog_digest": "WORKBENCH_CONSOLE_CATALOG_DIGEST",
        "action_digest": "WORKBENCH_CONSOLE_ACTION_DIGEST",
        "review_digest": "WORKBENCH_CONSOLE_REVIEW_DIGEST",
        "command_id": "WORKBENCH_CONSOLE_COMMAND_ID",
    }
    values = {key: os.environ.get(environment) for key, environment in names.items()}
    if all(value is None for value in values.values()):
        return {
            "state": "direct-cli",
            "catalog_digest": None,
            "action_digest": None,
            "review_digest": None,
            "command_id": None,
            "flow_plan_id": flow_plan_id,
            "source_snapshot_id": source_snapshot_id,
        }
    if any(value is None for value in values.values()):
        _fail("Feature Studio received a partial console review binding")
    for key in ("catalog_digest", "action_digest", "review_digest"):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", values[key] or "") is None:
            _fail(f"Feature Studio received an invalid {key.replace('_', ' ')}")
    if values["command_id"] != f"feature-studio.{expected_operation}":
        _fail("Feature Studio received a review binding for another operation")
    return {
        "state": "catalog-v2-bound",
        **values,
        "flow_plan_id": flow_plan_id,
        "source_snapshot_id": source_snapshot_id,
    }


def feature_studio_capabilities() -> dict[str, Any]:
    """Return the closed current capability projection."""

    bindings = (
        ("inspect", "feature-studio.inspect", "operation/plan", "read-only", "embedded"),
        ("plan", "feature-studio.plan", "operation/plan", "read-only", "embedded"),
        ("verify", "feature-studio.verify", "operation/commit", "mutating", "embedded"),
        ("explain", "feature-studio.explain", "operation/plan", "read-only", "embedded"),
        ("export", "feature-studio.export", "operation/commit", "writes-output", "embedded"),
    )
    descriptors = []
    for operation, action_id, protocol_method, risk, state in bindings:
        material = {
            "canonicalizer": "workbench-canonical-json-v2",
            "operation": operation,
            "catalog_action_id": action_id,
            "protocol_method": protocol_method,
            "request_schema_id": "workbench://schemas/workbench-shell/feature-studio-request-v2.schema.json",
            "result_schema_id": "workbench://schemas/workbench-shell/feature-studio-result-v2.schema.json",
            "risk": risk,
            "embedded_state": state,
        }
        descriptors.append({"descriptor_id": _content_id("feature-studio-capability-binding", material), **material})
    material = {
        "format": CAPABILITY_FORMAT,
        "schema_version": 2,
        "canonicalizer": "workbench-canonical-json-v2",
        "descriptors": descriptors,
        "limitations": [
            "The embedded API does not provide durable jobs, cancellation, reattachment, or subscriptions; use Service V3 when those controls are required.",
        ],
    }
    return {"projection_id": _content_id("feature-studio-capability-projection", material), **material}


def _actions() -> list[dict[str, Any]]:
    capabilities = feature_studio_capabilities()["descriptors"]
    return [
        {
            "action_id": row["catalog_action_id"],
            "operation": row["operation"],
            "risk": row["risk"],
            "state": row["embedded_state"],
            "descriptor_id": row["descriptor_id"],
            "availability_reason": "feature-studio.embedded-owner-composition",
        }
        for row in capabilities
    ]


def _semantic_projection(
    plan: Mapping[str, Any],
    *,
    assertions: Any,
    owner_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stage = owner_receipt.get("blueprint_stage") if owner_receipt else None
    retained_profile_observation = (
        owner_receipt.get("profile_observation")
        if isinstance(owner_receipt, Mapping)
        and isinstance(owner_receipt.get("profile_observation"), Mapping)
        else None
    )
    profile_observation = (
        retained_profile_observation
        if isinstance(owner_receipt, Mapping)
        and owner_receipt.get("outcome") == "runtime-completed"
        and isinstance(retained_profile_observation, Mapping)
        and retained_profile_observation.get("state") == "observed"
        else None
    )
    profile_semantics = None
    if profile_observation is not None:
        profile = (
            profile_observation.get("profile")
            if isinstance(profile_observation.get("profile"), Mapping)
            else {}
        )
        checks = profile_observation.get("checks")
        observed = profile_observation.get("observed")
        failed_checks = profile_observation.get("failed_checks")
        profile_semantics = {
            "state": profile_observation.get("state"),
            "platform_profile_id": profile.get("platform_profile_id"),
            "pack_profile_id": profile.get("pack_profile_id"),
            "checks": dict(checks) if isinstance(checks, Mapping) else {},
            "observed": dict(observed) if isinstance(observed, Mapping) else {},
            "failed_checks": list(failed_checks) if isinstance(failed_checks, list) else [],
        }
    projection = {
        "source_snapshot_id": plan["source_snapshot"]["snapshot_id"],
        "flow_plan_id": plan["plan_id"],
        "blueprint_plan_id": plan["blueprint"]["plan_id"],
        "feature": _feature(plan),
        "assertions": _assertion_rows(assertions),
        "candidate_id": stage.get("candidate_id") if isinstance(stage, Mapping) else None,
        "staged_tree_id": stage.get("tracked_tree_id") if isinstance(stage, Mapping) else None,
        "profile_state": (
            profile_observation.get("state")
            if isinstance(profile_observation, Mapping)
            else None
        ),
        "profile_semantics": profile_semantics,
    }
    return {"projection_id": _content_id("feature-studio-semantic-projection", projection), **projection}


def _result(
    operation: str,
    plan: Mapping[str, Any],
    *,
    state: str,
    owner_artifacts: Sequence[Mapping[str, Any]],
    assertions: Any,
    explanation: Sequence[Mapping[str, Any]] = (),
    timings: Sequence[Mapping[str, Any]] = (),
    export: Mapping[str, Any] | None = None,
    owner_receipt: Mapping[str, Any] | None = None,
    limitations: Sequence[str] = (),
    review_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    semantic = _semantic_projection(
        plan,
        assertions=assertions,
        owner_receipt=owner_receipt,
    )
    material = {
        "format": RESULT_FORMAT,
        "schema_version": 2,
        "canonicalizer": "workbench-canonical-json-v2",
        "operation": operation,
        "state": state,
        "workspace_label": "Materials & Recipes",
        "context": _context(plan, owner_receipt),
        "feature": _feature(plan),
        "source_locations": _source_locations(plan),
        "planned_changes": [
            {
                "operation": row["operation"],
                "relative_path": row["path"],
                "source_sha256": row["before_sha256"],
                "planned_sha256": row["content_sha256"],
                "planned_size": row["size"],
                "unified_diff": row["diff"],
            }
            for row in plan["operations"]
        ],
        "plan": {
            "flow_plan_id": plan["plan_id"],
            "blueprint_plan_id": plan["blueprint"]["plan_id"],
            "state": plan["state"],
        },
        "assertions": _assertion_rows(assertions),
        "timings": list(timings),
        "actions": _actions(),
        "review_binding": dict(
            review_binding
            if review_binding is not None
            else _review_binding(
                operation,
                flow_plan_id=plan["plan_id"],
                source_snapshot_id=plan["source_snapshot"]["snapshot_id"],
            )
        ),
        "owner_artifacts": [dict(row) for row in owner_artifacts],
        "semantic_equivalence": semantic,
        "explanation": [dict(row) for row in explanation],
        "export": None if export is None else dict(export),
        "limitations": list(limitations),
    }
    return {"result_id": _content_id("feature-studio-result", material), **material}


def _plan(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None,
    symbol: str | None,
    launcher: str,
) -> dict[str, Any]:
    return plan_material_fluid_trial(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )


def inspect_feature(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
) -> dict[str, Any]:
    plan = _plan(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )
    return _result(
        "inspect",
        plan,
        state="ready",
        owner_artifacts=(_canonical_artifact("material-fluid-plan", plan),),
        assertions=plan["assertions"],
        limitations=(
            "Inspection is a read-only projection of the owner plan.",
        ),
    )


def inspect_retained_feature(
    suite_root: Path | str,
    receipt_path: Path | str,
) -> dict[str, Any]:
    """Open one exact material-flow V2 receipt as a Feature Studio workspace."""

    selected = Path(receipt_path).expanduser().resolve()
    path, _raw, receipt = _read_json_uri(selected.as_uri(), "material-flow receipt")
    if (
        receipt.get("format") != "workbench-material-fluid-flow-receipt-v2"
        or receipt.get("schema_version") != 2
    ):
        _fail("Feature Studio receipt selection requires material-flow receipt V2")
    identity = dict(receipt)
    receipt_id = identity.pop("receipt_id", None)
    legacy_identity = "sha256:" + sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if receipt_id != legacy_identity:
        _fail("Feature Studio retained material-flow receipt identity is invalid")
    target = receipt.get("target")
    if not isinstance(target, Mapping) or target.get("receipt_uri") != path.as_uri():
        _fail("Feature Studio retained material-flow receipt target is rebound")
    plan_binding = receipt.get("plan")
    source = receipt.get("source")
    if not isinstance(plan_binding, Mapping) or not isinstance(source, Mapping):
        _fail("Feature Studio retained material-flow receipt lacks its plan binding")
    parameters = plan_binding.get("effective_parameters")
    if not isinstance(parameters, Mapping):
        _fail("Feature Studio retained material-flow receipt lacks parameters")
    workspace = _workspace_from_uri(source.get("workspace_uri"))
    selected_plan: Mapping[str, Any] | None = None
    errors: list[str] = []
    for launcher in ("prism", "multimc"):
        try:
            candidate = _plan(
                suite_root,
                workspace,
                name=parameters["name"],
                color=parameters["color"],
                translation=parameters["translation"],
                symbol=parameters["symbol_name"],
                launcher=launcher,
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if candidate.get("plan_id") == plan_binding.get("plan_id"):
            selected_plan = candidate
            break
    if selected_plan is None:
        _fail(
            "Feature Studio cannot revalidate the retained owner plan: "
            + "; ".join(errors)[:2000]
        )
    if (
        selected_plan["blueprint"]["plan_id"]
        != plan_binding.get("blueprint_plan_id")
        or _feature(selected_plan) != {
            "feature_kind": _profile_projection(selected_plan)["feature_kind"],
            "name": parameters["name"],
            "registry_name": parameters["registry_name"],
            "material_id": parameters["material_id"],
            "symbol_name": parameters["symbol_name"],
            "color": parameters["color"],
            "translation": parameters["translation"],
            "planned_material_resource": (
                _profile_projection(selected_plan)["registry_namespace"]
                + ":"
                + parameters["registry_name"]
            ),
            "planned_fluid_registry_name": parameters["registry_name"],
        }
    ):
        _fail("Feature Studio retained receipt differs from the revalidated owner plan")
    artifacts = [
        _canonical_artifact("material-fluid-plan", selected_plan),
        _canonical_artifact("material-fluid-receipt", receipt, uri=path.as_uri()),
    ]
    runtime_artifacts, timings = _runtime_artifacts(
        receipt,
        selected_plan,
        suite_root,
        receipt_uri=path.as_uri(),
    )
    artifacts.extend(runtime_artifacts)
    return _result(
        "inspect",
        selected_plan,
        state="complete" if receipt.get("state") == "complete" else "incomplete",
        owner_artifacts=artifacts,
        assertions=receipt.get("assertions"),
        timings=timings,
        owner_receipt=receipt,
        limitations=tuple(receipt.get("limitations", ())),
    )


def plan_feature(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
) -> dict[str, Any]:
    plan = _plan(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )
    return _result(
        "plan",
        plan,
        state="ready",
        owner_artifacts=(_canonical_artifact("material-fluid-plan", plan),),
        assertions=plan["assertions"],
        limitations=tuple(plan.get("limitations", ())),
    )


def preview_feature(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    operation: str,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
) -> dict[str, Any]:
    if operation not in {"verify", "export"}:
        _fail("Feature Studio preview supports only verify or export")
    plan = _plan(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )
    return _result(
        operation,
        plan,
        state="ready",
        owner_artifacts=(_canonical_artifact("material-fluid-plan", plan),),
        assertions=plan["assertions"],
        limitations=(
            f"This is an inert {operation} preview; no state was changed.",
            *tuple(plan.get("limitations", ())),
        ),
    )
def explain_feature(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
) -> dict[str, Any]:
    plan = _plan(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )
    explanation = (
        {
            "kind": "owner-state",
            "title": "Construction plan is ready",
            "detail": "Blueprints selected a collision-free ID and produced three exact source updates.",
            "authority": "Blueprints",
            "documentation": "docs/architecture/FEATURE-STUDIO.md",
        },
        {
            "kind": "evidence-state",
            "title": "Runtime assertions are still pending",
            "detail": "Compilation, FML load, material, fluid, and localization remain independent until disposable verification completes.",
            "authority": "Crucible and the Supersymmetry profile adapter",
            "documentation": "docs/architecture/FEATURE-STUDIO.md",
        },
        {
            "kind": "next-action",
            "title": "Review before verification",
            "detail": "Review the exact diff and profile compatibility projection, then invoke feature-studio.verify with the same plan ID.",
            "authority": "Workbench Shell orchestration",
            "documentation": "modules/workbench-shell/README.md",
        },
    )
    return _result(
        "explain",
        plan,
        state="ready",
        owner_artifacts=(_canonical_artifact("material-fluid-plan", plan),),
        assertions=plan["assertions"],
        explanation=explanation,
        limitations=(
            "Explanations teach and navigate; they do not authorize construction or upgrade evidence.",
        ),
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _safe_export_destination(
    value: Path | str,
    *,
    workspace: Path,
) -> tuple[Path, Path]:
    lexical = Path(os.path.abspath(Path(value).expanduser()))
    if lexical.name in {"", ".", ".."}:
        _fail("Feature Studio export destination is invalid")
    if lexical.exists() or lexical.is_symlink():
        _fail("Feature Studio export destination already exists")
    parent = lexical.parent
    cursor = Path(parent.anchor)
    for part in parent.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            _fail("Feature Studio export parent contains a symbolic link")
        if not cursor.is_dir():
            _fail("Feature Studio export parent is unavailable or unsafe")
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        _fail(f"Feature Studio export parent is unavailable: {exc}")
    destination = resolved_parent / lexical.name
    if _paths_overlap(destination, workspace):
        _fail("Feature Studio export must not overlap the developer workspace")
    return destination, resolved_parent


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _publish_directory_no_replace(
    source: Path,
    destination: Path,
    *,
    parent_fd: int,
) -> None:
    """Atomically publish ``source`` without replacing an existing entry."""

    if os.name == "nt":
        # Windows rename is already create-new: it fails when the destination
        # exists, including when the existing entry is an empty directory.
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(
            errno.ENOSYS,
            "atomic no-replace directory publication is unavailable",
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            parent_fd,
            os.fsencode(source.name),
            parent_fd,
            os.fsencode(destination.name),
            1,  # Linux RENAME_NOREPLACE.
        )
        != 0
    ):
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(destination),
        )


def export_feature(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    output_root: Path | str,
    expected_plan_id: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
) -> dict[str, Any]:
    plan = _plan(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )
    if expected_plan_id != plan["plan_id"]:
        _fail("Feature Studio plan changed after review; export was not written")
    operation_binding = _review_binding(
        "export",
        flow_plan_id=plan["plan_id"],
        source_snapshot_id=plan["source_snapshot"]["snapshot_id"],
    )
    workspace = _workspace_from_uri(plan["source"]["workspace_uri"])
    destination, parent = _safe_export_destination(
        output_root, workspace=workspace
    )
    patch_bytes = "".join(row["diff"] for row in plan["operations"]).encode("utf-8")
    receipt_material = {
        "format": "workbench-feature-studio-export-receipt-v1",
        "schema_version": 1,
        "canonicalizer": "workbench-canonical-json-v2",
        "flow_plan_id": plan["plan_id"],
        "blueprint_plan_id": plan["blueprint"]["plan_id"],
        "source_snapshot_id": plan["source_snapshot"]["snapshot_id"],
        "operation_binding": operation_binding,
        "patch_sha256": sha256(patch_bytes).hexdigest(),
        "patch_size": len(patch_bytes),
        "paths": [row["path"] for row in plan["operations"]],
        "target": {
            "directory_uri": destination.as_uri(),
            "patch_uri": (destination / "feature.patch").as_uri(),
            "receipt_uri": (destination / "receipt.json").as_uri(),
        },
    }
    receipt = {"receipt_id": _content_id("feature-studio-export-receipt", receipt_material), **receipt_material}
    temporary = Path(tempfile.mkdtemp(prefix=".feature-studio-export-", dir=parent))
    parent_fd: int | None = None
    try:
        parent_fd = _open_directory(parent)
        _write_fsynced(temporary / "feature.patch", patch_bytes)
        _write_fsynced(
            temporary / "receipt.json", _canonical_bytes(receipt) + b"\n"
        )
        temporary_fd = _open_directory(temporary)
        try:
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        _publish_directory_no_replace(
            temporary,
            destination,
            parent_fd=parent_fd,
        )
        os.fsync(parent_fd)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            _fail("Feature Studio export destination appeared during publication")
        _fail(f"Feature Studio could not publish its export: {exc}")
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        if temporary.exists():
            shutil.rmtree(temporary)
    export = {
        "state": "written",
        "receipt_id": receipt["receipt_id"],
        "directory_uri": destination.as_uri(),
        "patch_uri": (destination / "feature.patch").as_uri(),
        "receipt_uri": (destination / "receipt.json").as_uri(),
        "patch_sha256": receipt["patch_sha256"],
        "patch_size": receipt["patch_size"],
    }
    return _result(
        "export",
        plan,
        state="complete",
        owner_artifacts=(
            _canonical_artifact("material-fluid-plan", plan),
            _canonical_artifact("feature-studio-export-receipt", receipt, uri=export["receipt_uri"]),
        ),
        assertions=plan["assertions"],
        export=export,
        limitations=(
            "The export is a reviewed patch bundle; it did not modify the developer workspace.",
            "Applying the patch remains outside Feature Studio.",
        ),
        review_binding=operation_binding,
    )


def verify_feature(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    expected_plan_id: str,
    translation: str | None = None,
    symbol: str | None = None,
    launcher: str = "prism",
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    packwiz_executable: Path | str | None = None,
    seed_roots: Sequence[Path | str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    timeout_seconds: float = 600.0,
    attach_timeout: float = 120.0,
    session_timeout: float = 21_600.0,
    state_root: Path | str | None = None,
) -> dict[str, Any]:
    plan = _plan(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
    )
    if plan["plan_id"] != expected_plan_id:
        _fail("Feature Studio plan changed after review; verification was not started")
    operation_binding = _review_binding(
        "verify",
        flow_plan_id=plan["plan_id"],
        source_snapshot_id=plan["source_snapshot"]["snapshot_id"],
    )
    owner_result = execute_material_fluid_trial(
        suite_root,
        workspace_root,
        name=name,
        color=color,
        translation=translation,
        symbol=symbol,
        launcher=launcher,
        launcher_executable=launcher_executable,
        launcher_root=launcher_root,
        launcher_profile=launcher_profile,
        launcher_java=launcher_java,
        launcher_java_state=launcher_java_state,
        packwiz_executable=packwiz_executable,
        seed_roots=seed_roots,
        memory_mib=memory_mib,
        offline_name=offline_name,
        timeout_seconds=timeout_seconds,
        attach_timeout=attach_timeout,
        session_timeout=session_timeout,
        state_root=state_root,
        expected_plan_id=expected_plan_id,
    )
    receipt = owner_result.get("receipt")
    if not isinstance(receipt, Mapping):
        _fail("Feature Studio verification owner returned no receipt")
    receipt_uri = None
    target = receipt.get("target")
    if isinstance(target, Mapping) and isinstance(target.get("receipt_uri"), str):
        receipt_uri = target["receipt_uri"]
    if receipt_uri is None:
        _fail("Feature Studio verification owner returned no retained receipt URI")
    runtime_artifacts, timings = _runtime_artifacts(
        receipt,
        plan,
        suite_root,
        receipt_uri=receipt_uri,
    )
    artifacts = [
        _canonical_artifact("material-fluid-plan", plan),
        _canonical_artifact("material-fluid-receipt", receipt, uri=receipt_uri),
    ]
    artifacts.extend(runtime_artifacts)
    return _result(
        "verify",
        plan,
        state="complete" if owner_result.get("outcome") == "runtime-completed" else "incomplete",
        owner_artifacts=artifacts,
        assertions=receipt.get("assertions"),
        timings=timings,
        owner_receipt=receipt,
        limitations=tuple(receipt.get("limitations", ())),
        review_binding=operation_binding,
    )


def _validate_schema_value(
    value: Mapping[str, Any],
    *,
    schema_name: str,
    label: str,
) -> dict[str, Any]:
    try:
        from jsonschema import Draft202012Validator, FormatChecker

        schema_path = _module_resource_root(__file__, 'workbench-shell') / "schemas" / schema_name
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        detached = json.loads(_canonical_bytes(value))
        validator = Draft202012Validator(
            schema, format_checker=FormatChecker()
        )
        errors = sorted(
            validator.iter_errors(detached),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"Feature Studio {label} schema is unavailable: {exc}")
    if errors:
        first = errors[0]
        location = "/" + "/".join(str(part) for part in first.absolute_path)
        _fail(f"Feature Studio {label} is invalid at {location}: {first.message}")
    return detached


def validate_feature_request(
    request: Mapping[str, Any],
    *,
    expected_operation: str | None = None,
) -> dict[str, Any]:
    """Validate and detach one closed embedded request."""

    if not isinstance(request, Mapping):
        _fail("Feature Studio request is not an object")
    value = _validate_schema_value(
        request,
        schema_name="feature-studio-request-v2.schema.json",
        label="request",
    )
    if expected_operation is not None and value["operation"] != expected_operation:
        _fail("Feature Studio request belongs to another capability operation")
    return value


def validate_feature_result(
    result: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    """Validate one result and its typed content identity for IDE consumers."""

    if not isinstance(result, Mapping):
        _fail("Feature Studio result is not an object")
    suite = (
        Path(suite_root).expanduser().resolve()
        if suite_root is not None
        else _repository_resource_root(__file__)
    )
    value = _validate_schema_value(
        result,
        schema_name="feature-studio-result-v2.schema.json",
        label="result",
    )
    material = {key: item for key, item in value.items() if key != "result_id"}
    if value["result_id"] != _content_id("feature-studio-result", material):
        _fail("Feature Studio result identity is invalid")
    semantic = value["semantic_equivalence"]
    semantic_material = {
        key: item for key, item in semantic.items() if key != "projection_id"
    }
    if semantic["projection_id"] != _content_id(
        "feature-studio-semantic-projection", semantic_material
    ):
        _fail("Feature Studio semantic projection identity is invalid")
    if value["actions"] != _actions():
        _fail("Feature Studio result action projection drifted")
    context = value["context"]
    plan = value["plan"]
    review = value["review_binding"]
    if (
        review["flow_plan_id"] != plan["flow_plan_id"]
        or review["source_snapshot_id"] != context["source_snapshot_id"]
        or semantic["flow_plan_id"] != plan["flow_plan_id"]
        or semantic["source_snapshot_id"] != context["source_snapshot_id"]
        or semantic["blueprint_plan_id"] != plan["blueprint_plan_id"]
        or semantic["feature"] != value["feature"]
        or semantic["assertions"] != value["assertions"]
    ):
        _fail("Feature Studio result plan, review, context, and semantic bindings disagree")

    artifact_values: dict[str, Mapping[str, Any]] = {}
    artifact_records: dict[str, Mapping[str, Any]] = {}
    for artifact in value["owner_artifacts"]:
        role = artifact["role"]
        if role in artifact_values:
            _fail("Feature Studio result repeats an owner artifact role")
        artifact_records[role] = artifact
        retained = artifact["retained"]
        retained_value: Mapping[str, Any] | None = None
        if retained is not None:
            _path, retained_raw, retained_value = _read_json_uri(
                retained["uri"], f"{role} retained artifact"
            )
            if (
                sha256(retained_raw).hexdigest() != retained["sha256"]
                or len(retained_raw) != retained["size"]
            ):
                _fail(f"Feature Studio retained {role} artifact identity drifted")
        if artifact["projection_encoding"] == "workbench-canonical-json-v2":
            try:
                canonical_value = json.loads(artifact["canonical_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                _fail(f"Feature Studio canonical {role} artifact is invalid: {exc}")
            canonical_raw = _canonical_bytes(canonical_value)
            if (
                canonical_raw.decode("utf-8") != artifact["canonical_json"]
                or sha256(canonical_raw).hexdigest()
                != artifact["canonical_sha256"]
                or len(canonical_raw) != artifact["canonical_size"]
                or not isinstance(canonical_value, Mapping)
                or canonical_value.get("format") != artifact["format"]
                or retained_value is not None
                and retained_value != canonical_value
            ):
                _fail(f"Feature Studio canonical {role} artifact identity drifted")
            artifact_values[role] = canonical_value
        else:
            if (
                retained_value is None
                or retained_value.get("format") != artifact["format"]
            ):
                _fail(f"Feature Studio retained-only {role} artifact is invalid")
            artifact_values[role] = retained_value

    owner_plan = artifact_values.get("material-fluid-plan")
    if not isinstance(owner_plan, Mapping):
        _fail("Feature Studio result lacks its owner plan artifact")
    expected_changes = [
        {
            "operation": row["operation"],
            "relative_path": row["path"],
            "source_sha256": row["before_sha256"],
            "planned_sha256": row["content_sha256"],
            "planned_size": row["size"],
            "unified_diff": row["diff"],
        }
        for row in owner_plan.get("operations", [])
        if isinstance(row, Mapping)
    ]
    expected_plan_projection = {
        "flow_plan_id": owner_plan.get("plan_id"),
        "blueprint_plan_id": owner_plan.get("blueprint", {}).get("plan_id"),
        "state": owner_plan.get("state"),
    }
    if (
        owner_plan.get("plan_id") != plan["flow_plan_id"]
        or owner_plan.get("source_snapshot", {}).get("snapshot_id")
        != context["source_snapshot_id"]
        or owner_plan.get("blueprint", {}).get("plan_id")
        != plan["blueprint_plan_id"]
        or owner_plan.get("source", {}).get("workspace_uri")
        != context["workspace_uri"]
        or owner_plan.get("source", {}).get("revision")
        != context["source_revision"]
        or expected_changes != value["planned_changes"]
        or value["plan"] != expected_plan_projection
        or value["source_locations"] != _source_locations(owner_plan, suite)
        or value["feature"] != _feature(owner_plan, suite)
    ):
        _fail("Feature Studio result differs from its owner plan artifact")

    owner_receipt_for_context: Mapping[str, Any] | None = None
    if value["operation"] in {"inspect", "verify"} and value["state"] == "incomplete":
        if set(artifact_values) != {
            "material-fluid-plan",
            "material-fluid-receipt",
        }:
            _fail("Feature Studio incomplete result lacks its exact owner receipt")
        flow_receipt = artifact_values["material-fluid-receipt"]
        flow_receipt_retained = artifact_records["material-fluid-receipt"].get(
            "retained"
        )
        if not isinstance(flow_receipt_retained, Mapping):
            _fail("Feature Studio incomplete owner receipt lacks physical custody")
        try:
            validate_retained_material_fluid_receipt(
                flow_receipt,
                owner_plan,
                receipt_uri=flow_receipt_retained.get("uri"),
            )
        except MaterialFluidFlowError as exc:
            _fail(f"Feature Studio incomplete owner receipt is invalid: {exc}")
        owner_receipt_for_context = flow_receipt
        flow_stage = flow_receipt.get("blueprint_stage")
        expected_candidate_id = (
            flow_stage.get("candidate_id")
            if isinstance(flow_stage, Mapping)
            else None
        )
        expected_staged_tree_id = (
            flow_stage.get("tracked_tree_id")
            if isinstance(flow_stage, Mapping)
            else None
        )
        if (
            flow_receipt.get("assertions") is None
            or _assertion_rows(flow_receipt["assertions"]) != value["assertions"]
            or semantic.get("candidate_id") != expected_candidate_id
            or semantic.get("staged_tree_id") != expected_staged_tree_id
            or semantic.get("profile_state") is not None
            or semantic.get("profile_semantics") is not None
            or value["timings"]
            or value["limitations"] != list(flow_receipt.get("limitations", ()))
        ):
            _fail("Feature Studio incomplete result differs from its owner receipt")

    if value["operation"] in {"inspect", "verify"} and value["state"] == "complete":
        required_roles = {
            "material-fluid-plan",
            "material-fluid-receipt",
            "blueprint-stage-receipt",
            "runtime-observation-session",
            "runtime-final-launch-receipt",
            "runtime-materialization-receipt",
        }
        if set(artifact_values) != required_roles:
            _fail("Feature Studio completed verification lacks its exact owner chain")
        flow_receipt = artifact_values["material-fluid-receipt"]
        owner_receipt_for_context = flow_receipt
        stage_receipt = artifact_values["blueprint-stage-receipt"]
        session = artifact_values["runtime-observation-session"]
        final_launch = artifact_values["runtime-final-launch-receipt"]
        materialization = artifact_values["runtime-materialization-receipt"]
        flow_receipt_retained = artifact_records["material-fluid-receipt"].get(
            "retained"
        )
        if not isinstance(flow_receipt_retained, Mapping):
            _fail("Feature Studio completed owner receipt lacks physical custody")
        try:
            validated_owner = validate_retained_material_fluid_success(
                flow_receipt,
                owner_plan,
                suite,
                receipt_uri=flow_receipt_retained.get("uri"),
            )
        except MaterialFluidFlowError as exc:
            _fail(f"Feature Studio completed owner chain is invalid: {exc}")
        validated_stage = validated_owner.get("blueprint_stage")
        expected_owner_chain = {
            "blueprint-stage-receipt": (
                validated_stage.get("receipt")
                if isinstance(validated_stage, Mapping)
                else None,
                validated_stage.get("receipt_uri")
                if isinstance(validated_stage, Mapping)
                else None,
            ),
            "runtime-observation-session": (
                validated_owner.get("session"),
                validated_owner.get("session_uri"),
            ),
            "runtime-final-launch-receipt": (
                validated_owner.get("final_launch_receipt"),
                validated_owner.get("final_launch_uri"),
            ),
            "runtime-materialization-receipt": (
                validated_owner.get("materialization_receipt"),
                validated_owner.get("materialization_uri"),
            ),
        }
        for role, (expected_value, expected_uri) in expected_owner_chain.items():
            retained = artifact_records[role].get("retained")
            if (
                artifact_values[role] != expected_value
                or not isinstance(retained, Mapping)
                or retained.get("uri") != expected_uri
            ):
                _fail(
                    "Feature Studio completed artifacts differ from the "
                    "owner-validated custody chain"
                )
        if value["timings"] != _session_timings(validated_owner["session"]):
            _fail("Feature Studio timings differ from the owner-validated session")
        flow_stage = flow_receipt.get("blueprint_stage")
        flow_runtime = flow_receipt.get("runtime")
        profile = flow_receipt.get("profile_observation")
        profile_values = semantic.get("profile_semantics")
        if (
            not isinstance(flow_stage, Mapping)
            or not isinstance(flow_runtime, Mapping)
            or not isinstance(profile, Mapping)
            or not isinstance(profile_values, Mapping)
            or semantic.get("candidate_id") is None
            or semantic.get("staged_tree_id") is None
            or flow_stage.get("candidate_id") != semantic["candidate_id"]
            or flow_stage.get("tracked_tree_id") != semantic["staged_tree_id"]
            or stage_receipt.get("stage_id") != flow_stage.get("stage_id")
            or stage_receipt.get("blueprint", {}).get("candidate_id")
            != semantic["candidate_id"]
            or stage_receipt.get("target", {}).get("tracked_tree_id")
            != semantic["staged_tree_id"]
            or flow_receipt.get("assertions") is None
            or _assertion_rows(flow_receipt["assertions"]) != value["assertions"]
            or flow_runtime.get("session_id") != context["runtime_session_id"]
            or flow_runtime.get("runtime_plan_id") != context["runtime_plan_id"]
            or flow_runtime.get("materialization_id") != context["materialization_id"]
            or flow_runtime.get("final_launch_id") != context["final_launch_id"]
            or session.get("session_id") != context["runtime_session_id"]
            or final_launch.get("launch_id") != context["final_launch_id"]
            or final_launch.get("plan_id") != context["runtime_plan_id"]
            or final_launch.get("materialization_id") != context["materialization_id"]
            or materialization.get("materialization_id")
            != context["materialization_id"]
            or profile.get("format") != context["profile_assessment_format"]
            or profile.get("assessment_id") != context["profile_assessment_id"]
            or profile.get("crucible_snapshot", {}).get("snapshot_id")
            != context["crucible_snapshot_id"]
            or profile.get("profile", {}).get("platform_profile_id")
            != context["platform_profile_id"]
            or profile.get("profile", {}).get("pack_profile_id")
            != context["pack_profile_id"]
            or profile_values.get("platform_profile_id")
            != context["platform_profile_id"]
            or profile_values.get("pack_profile_id") != context["pack_profile_id"]
            or context["pack_profile_family_id"] != context["pack_profile_id"]
        ):
            _fail("Feature Studio completed owner, runtime, and profile chain disagrees")

    if "material-fluid-receipt" not in artifact_values:
        if (
            value["assertions"]
            != _assertion_rows(owner_plan.get("assertions"))
            or semantic.get("candidate_id") is not None
            or semantic.get("staged_tree_id") is not None
            or semantic.get("profile_state") is not None
            or semantic.get("profile_semantics") is not None
        ):
            _fail("Feature Studio pending result differs from its owner plan state")

    if value["operation"] == "export" and value["state"] == "complete":
        if set(artifact_values) != {
            "material-fluid-plan",
            "feature-studio-export-receipt",
        }:
            _fail("Feature Studio completed export lacks its exact owner artifacts")
        export_value = value["export"]
        export_receipt = artifact_values["feature-studio-export-receipt"]
        export_artifact = artifact_records["feature-studio-export-receipt"]
        if not isinstance(export_value, Mapping) or not isinstance(
            export_receipt, Mapping
        ):
            _fail("Feature Studio completed export is malformed")
        export_material = {
            key: item
            for key, item in export_receipt.items()
            if key != "receipt_id"
        }
        target = export_receipt.get("target")
        retained = export_artifact.get("retained")
        expected_paths = [
            row.get("path")
            for row in owner_plan.get("operations", [])
            if isinstance(row, Mapping)
        ]
        if (
            export_receipt.get("format")
            != "workbench-feature-studio-export-receipt-v1"
            or export_receipt.get("schema_version") != 1
            or export_receipt.get("canonicalizer")
            != "workbench-canonical-json-v2"
            or export_receipt.get("receipt_id")
            != _content_id("feature-studio-export-receipt", export_material)
            or export_receipt.get("flow_plan_id") != plan["flow_plan_id"]
            or export_receipt.get("blueprint_plan_id")
            != plan["blueprint_plan_id"]
            or export_receipt.get("source_snapshot_id")
            != context["source_snapshot_id"]
            or export_receipt.get("operation_binding") != review
            or export_receipt.get("paths") != expected_paths
            or not isinstance(target, Mapping)
            or not isinstance(retained, Mapping)
            or target.get("directory_uri") != export_value.get("directory_uri")
            or target.get("patch_uri") != export_value.get("patch_uri")
            or target.get("receipt_uri") != export_value.get("receipt_uri")
            or retained.get("uri") != export_value.get("receipt_uri")
            or export_receipt.get("receipt_id") != export_value.get("receipt_id")
            or export_receipt.get("patch_sha256")
            != export_value.get("patch_sha256")
            or export_receipt.get("patch_size") != export_value.get("patch_size")
        ):
            _fail("Feature Studio export receipt binding drifted")
        patch_path, patch_raw = _read_bytes_uri(
            export_value["patch_uri"], "export patch"
        )
        receipt_path, receipt_raw = _read_bytes_uri(
            export_value["receipt_uri"], "export receipt"
        )
        try:
            directory = _request_file_path(
                export_value["directory_uri"], "export directory"
            ).resolve(strict=True)
        except OSError as exc:
            _fail(f"Feature Studio export directory is unavailable: {exc}")
        if (
            not directory.is_dir()
            or patch_path.parent != directory
            or receipt_path.parent != directory
            or sha256(patch_raw).hexdigest() != export_value["patch_sha256"]
            or len(patch_raw) != export_value["patch_size"]
            or sha256(receipt_raw).hexdigest() != retained["sha256"]
            or len(receipt_raw) != retained["size"]
        ):
            _fail("Feature Studio export bytes differ from the sealed result")

    expected_context = _context(
        owner_plan,
        owner_receipt_for_context,
        suite,
    )
    if context != expected_context:
        _fail("Feature Studio result context differs from its owner authorities")
    return value


def _request_file_path(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        _fail(f"Feature Studio {label} is unavailable")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"Feature Studio {label} must be a local file URI")
    return Path(url2pathname(parsed.path))


def execute_feature_request(
    suite_root: Path | str,
    request: Mapping[str, Any],
    *,
    expected_operation: str | None = None,
) -> dict[str, Any]:
    """Dispatch one validated request through the embedded owner composition."""

    require_material_fluid_profile()
    value = validate_feature_request(
        request, expected_operation=expected_operation
    )
    operation = value["operation"]
    if value["selection_mode"] == "retained-receipt":
        if operation != "inspect":
            _fail("Feature Studio retained receipt selection supports only inspect")
        result = inspect_retained_feature(
            suite_root,
            _request_file_path(value["receipt_uri"], "receipt URI"),
        )
        return validate_feature_result(result, suite_root=suite_root)

    workspace = _request_file_path(value["workspace_uri"], "workspace URI")
    common = {
        "name": value["name"],
        "color": value["color"],
        "translation": value["translation"],
        "symbol": value["symbol"],
        "launcher": value["launcher"],
    }
    if operation == "inspect":
        result = inspect_feature(suite_root, workspace, **common)
    elif operation == "plan":
        result = plan_feature(suite_root, workspace, **common)
    elif operation == "explain":
        result = explain_feature(suite_root, workspace, **common)
    elif operation == "export":
        result = export_feature(
            suite_root,
            workspace,
            output_root=_request_file_path(value["export_uri"], "export URI"),
            expected_plan_id=value["reviewed_plan_id"],
            **common,
        )
    else:
        runtime = value["runtime"]
        if not isinstance(runtime, Mapping):
            _fail("Feature Studio verify request lacks its runtime inputs")
        result = verify_feature(
            suite_root,
            workspace,
            launcher_executable=_request_file_path(
                runtime["launcher_executable_uri"], "launcher executable URI"
            ),
            launcher_root=_request_file_path(
                runtime["launcher_root_uri"], "launcher root URI"
            ),
            launcher_profile=runtime["launcher_profile"],
            launcher_java=(
                None
                if runtime["launcher_java_uri"] is None
                else _request_file_path(
                    runtime["launcher_java_uri"], "launcher Java URI"
                )
            ),
            launcher_java_state=(
                None
                if runtime["launcher_java_state_uri"] is None
                else _request_file_path(
                    runtime["launcher_java_state_uri"],
                    "launcher Java state URI",
                )
            ),
            packwiz_executable=(
                None
                if runtime["packwiz_uri"] is None
                else _request_file_path(runtime["packwiz_uri"], "Packwiz URI")
            ),
            seed_roots=tuple(
                _request_file_path(item, "seed URI")
                for item in runtime["seed_uris"]
            ),
            memory_mib=runtime["memory_mib"],
            offline_name=runtime["offline_name"],
            timeout_seconds=runtime["launch_timeout_milliseconds"] / 1000,
            attach_timeout=runtime["attach_timeout_milliseconds"] / 1000,
            session_timeout=runtime["session_timeout_milliseconds"] / 1000,
            state_root=(
                None
                if runtime["state_root_uri"] is None
                else _request_file_path(runtime["state_root_uri"], "state root URI")
            ),
            expected_plan_id=value["reviewed_plan_id"],
            **common,
        )
    return validate_feature_result(result, suite_root=suite_root)


__all__ = [
    "FeatureStudioError",
    "execute_feature_request",
    "export_feature",
    "explain_feature",
    "feature_studio_capabilities",
    "inspect_feature",
    "inspect_retained_feature",
    "plan_feature",
    "preview_feature",
    "verify_feature",
    "validate_feature_request",
    "validate_feature_result",
]
