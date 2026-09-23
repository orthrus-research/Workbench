"""Bounded, IDE-neutral snapshots of validated Feature Studio results.

The snapshot is a read-only presentation projection.  It never substitutes for
the Feature Studio result or its owner artifacts: projection starts by running
the complete result validator, and retained refresh starts from the selected
physical result or material-flow receipt again.
"""

from __future__ import annotations

from urllib.request import url2pathname

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, NoReturn
from urllib.parse import urlparse

from .feature_studio import (
    FeatureStudioError,
    _actions,
    _canonical_bytes,
    _content_id,
    _read_json_uri,
    inspect_retained_feature,
    validate_feature_result,
)


SNAPSHOT_FORMAT = "workbench-feature-studio-snapshot-v2"
_RESULT_ORIGIN = "feature-studio-result-file"
_RECEIPT_ORIGIN = "material-flow-receipt-v2"
_VALUE_ORIGIN = "feature-studio-result-value"
_RETAINED_REFRESH_REASON = "feature-studio.snapshot-retained-source"
_VALUE_REFRESH_REASON = "feature-studio.snapshot-value-has-no-retained-source"
_SNAPSHOT_RANGE_COORDINATES = (
    "unified-diff-lines-one-based-zero-for-empty-inclusive"
)


def _fail(message: str) -> NoReturn:
    raise FeatureStudioError(message)


def _lexical_file_uri(path: Path | str) -> str:
    lexical = Path(path).expanduser()
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    return lexical.as_uri()


def _is_local_file_uri(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "file"
        and parsed.netloc in {"", "localhost"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and Path(url2pathname(parsed.path)).is_absolute()
    )


def _origin(
    kind: str,
    *,
    uri: str | None = None,
    raw: bytes | None = None,
) -> dict[str, Any]:
    retained = kind in {_RESULT_ORIGIN, _RECEIPT_ORIGIN}
    if retained != (uri is not None and raw is not None):
        _fail("Feature Studio snapshot origin is partial")
    return {
        "kind": kind,
        "uri": uri,
        "sha256": None if raw is None else sha256(raw).hexdigest(),
        "size": None if raw is None else len(raw),
        "refresh": {
            "state": "available" if retained else "unavailable",
            "reason_code": (
                _RETAINED_REFRESH_REASON if retained else _VALUE_REFRESH_REASON
            ),
        },
    }


def _owner_id(
    artifact: Mapping[str, Any],
    result: Mapping[str, Any],
) -> str | None:
    role = artifact["role"]
    field_by_role = {
        "material-fluid-plan": "plan_id",
        "material-fluid-receipt": "receipt_id",
        "blueprint-stage-receipt": "stage_id",
        "runtime-observation-session": "session_id",
        "runtime-final-launch-receipt": "launch_id",
        "runtime-materialization-receipt": "materialization_id",
        "feature-studio-export-receipt": "receipt_id",
    }
    field = field_by_role.get(role)
    if field is None:
        return None
    canonical_json = artifact.get("canonical_json")
    if isinstance(canonical_json, str):
        canonical = json.loads(canonical_json)
        owner_id = canonical.get(field) if isinstance(canonical, Mapping) else None
    else:
        context = result["context"]
        owner_id = {
            "runtime-final-launch-receipt": context["final_launch_id"],
        }.get(role)
    if not isinstance(owner_id, str) or not owner_id:
        _fail(f"Feature Studio snapshot owner {role} lacks its exact identity")
    return owner_id


def _owner_links(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for artifact in result["owner_artifacts"]:
        links.append(
            {
                "role": artifact["role"],
                "format": artifact["format"],
                "owner_id": _owner_id(artifact, result),
                "projection_encoding": artifact["projection_encoding"],
                "canonical_sha256": artifact["canonical_sha256"],
                "canonical_size": artifact["canonical_size"],
                "retained": artifact["retained"],
            }
        )
    return links


def _identities(result: Mapping[str, Any]) -> dict[str, Any]:
    context = result["context"]
    semantic = result["semantic_equivalence"]
    export = result["export"]
    owner_ids = {
        artifact["role"]: _owner_id(artifact, result)
        for artifact in result["owner_artifacts"]
    }
    return {
        "result_id": result["result_id"],
        "semantic_projection_id": semantic["projection_id"],
        "flow_plan_id": result["plan"]["flow_plan_id"],
        "blueprint_plan_id": result["plan"]["blueprint_plan_id"],
        "source_snapshot_id": context["source_snapshot_id"],
        "material_flow_receipt_id": owner_ids.get("material-fluid-receipt"),
        "blueprint_stage_id": owner_ids.get("blueprint-stage-receipt"),
        "candidate_id": semantic["candidate_id"],
        "staged_tree_id": semantic["staged_tree_id"],
        "pack_profile_family_id": context["pack_profile_family_id"],
        "platform_profile_id": context["platform_profile_id"],
        "pack_profile_id": context["pack_profile_id"],
        "profile_assessment_id": context["profile_assessment_id"],
        "crucible_snapshot_id": context["crucible_snapshot_id"],
        "runtime_session_id": context["runtime_session_id"],
        "runtime_plan_id": context["runtime_plan_id"],
        "materialization_id": context["materialization_id"],
        "final_launch_id": context["final_launch_id"],
        "export_receipt_id": None if export is None else export["receipt_id"],
    }


def _availability(result: Mapping[str, Any]) -> dict[str, Any]:
    context = result["context"]
    return {
        "result_state": result["state"],
        "plan_state": result["plan"]["state"],
        "platform_profile_state": context["platform_profile_state"],
        "source_state": context["evidence_layers"]["source"],
        "planned_state": context["evidence_layers"]["planned"],
        "runtime_state": context["evidence_layers"]["runtime"],
    }


def _source_locations(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in result["source_locations"]:
        row = dict(source)
        row["range"] = {
            **source["range"],
            "coordinate_system": _SNAPSHOT_RANGE_COORDINATES,
        }
        row["planned_range"] = {
            **source["planned_range"],
            "coordinate_system": _SNAPSHOT_RANGE_COORDINATES,
        }
        rows.append(row)
    return rows


def _project_validated_result(
    result: Mapping[str, Any],
    *,
    origin: Mapping[str, Any],
) -> dict[str, Any]:
    material = {
        "format": SNAPSHOT_FORMAT,
        "schema_version": 2,
        "canonicalizer": "workbench-canonical-json-v2",
        "origin": dict(origin),
        "result": {
            "result_id": result["result_id"],
            "format": result["format"],
            "operation": result["operation"],
            "state": result["state"],
            "semantic_projection_id": result["semantic_equivalence"][
                "projection_id"
            ],
        },
        "workspace_label": result["workspace_label"],
        "identities": _identities(result),
        "availability": _availability(result),
        "context": result["context"],
        "feature": result["feature"],
        "source_locations": _source_locations(result),
        "planned_changes": result["planned_changes"],
        "plan": result["plan"],
        "assertions": result["assertions"],
        "timings": result["timings"],
        "actions": result["actions"],
        "review_binding": result["review_binding"],
        "owner_links": _owner_links(result),
        "export": result["export"],
        "limitations": result["limitations"],
    }
    return {
        "snapshot_id": _content_id("feature-studio-snapshot", material),
        **material,
    }


def _validate_snapshot_schema_value(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the snapshot schema with its exact Feature result definitions."""

    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry, Resource

        schemas = _module_resource_root(__file__, 'workbench-shell') / "schemas"
        schema = json.loads(
            (schemas / "feature-studio-snapshot-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        result_schema = json.loads(
            (schemas / "feature-studio-result-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        registry = Registry().with_resource(
            result_schema["$id"], Resource.from_contents(result_schema)
        )
        detached = json.loads(_canonical_bytes(snapshot))
        errors = sorted(
            Draft202012Validator(
                schema,
                registry=registry,
                format_checker=FormatChecker(),
            ).iter_errors(detached),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        _fail(f"Feature Studio snapshot schema is unavailable: {exc}")
    if errors:
        first = errors[0]
        location = "/" + "/".join(str(part) for part in first.absolute_path)
        _fail(f"Feature Studio snapshot is invalid at {location}: {first.message}")
    return detached


def project_feature_snapshot(
    result: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    """Project one in-memory, owner-validated result for read-only clients.

    Value-backed snapshots are deterministic and valid, but honestly report
    that they cannot be refreshed without the result value being supplied
    again.  Use :func:`open_feature_snapshot` for retained refresh custody.
    """

    validated = validate_feature_result(result, suite_root=suite_root)
    snapshot = _project_validated_result(
        validated,
        origin=_origin(_VALUE_ORIGIN),
    )
    return validate_feature_snapshot(snapshot)


def open_feature_snapshot(
    suite_root: Path | str,
    *,
    result_path: Path | str | None = None,
    receipt_path: Path | str | None = None,
) -> dict[str, Any]:
    """Open exactly one bounded retained result or material-flow V2 receipt."""

    if (result_path is None) == (receipt_path is None):
        _fail("Feature Studio snapshot open requires exactly one retained selection")
    if result_path is not None:
        path, raw, value = _read_json_uri(
            _lexical_file_uri(result_path), "retained Feature Studio result"
        )
        validated = validate_feature_result(value, suite_root=suite_root)
        origin = _origin(_RESULT_ORIGIN, uri=path.as_uri(), raw=raw)
    else:
        path, raw, receipt = _read_json_uri(
            _lexical_file_uri(receipt_path), "retained material-flow receipt"
        )
        if (
            receipt.get("format") != "workbench-material-fluid-flow-receipt-v2"
            or receipt.get("schema_version") != 2
        ):
            _fail("Feature Studio snapshot receipt selection requires material-flow V2")
        result = inspect_retained_feature(suite_root, path)
        validated = validate_feature_result(result, suite_root=suite_root)
        origin = _origin(_RECEIPT_ORIGIN, uri=path.as_uri(), raw=raw)
    snapshot = _project_validated_result(validated, origin=origin)
    return validate_feature_snapshot(snapshot)


def refresh_feature_snapshot(
    suite_root: Path | str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-open the exact retained origin named by one validated snapshot."""

    value = validate_feature_snapshot(snapshot)
    origin = value["origin"]
    if origin["refresh"]["state"] != "available" or origin["uri"] is None:
        _fail("Feature Studio value-backed snapshot has no retained refresh source")
    parsed = urlparse(origin["uri"])
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        _fail("Feature Studio snapshot refresh source is not a local file")
    path, raw, _retained_value = _read_json_uri(
        origin["uri"], "Feature Studio snapshot refresh source"
    )
    if (
        sha256(raw).hexdigest() != origin["sha256"]
        or len(raw) != origin["size"]
    ):
        _fail("Feature Studio snapshot retained refresh source drifted")
    if origin["kind"] == _RESULT_ORIGIN:
        refreshed = open_feature_snapshot(suite_root, result_path=path)
    elif origin["kind"] == _RECEIPT_ORIGIN:
        refreshed = open_feature_snapshot(suite_root, receipt_path=path)
    else:
        _fail("Feature Studio snapshot refresh kind is unsupported")
    if refreshed["origin"] != origin:
        _fail("Feature Studio snapshot retained refresh origin changed")
    return refreshed


def refresh_feature_snapshot_file(
    suite_root: Path | str,
    snapshot_path: Path | str,
) -> dict[str, Any]:
    """Refresh one bounded retained snapshot file through its immutable origin."""

    _path, _raw, snapshot = _read_json_uri(
        _lexical_file_uri(snapshot_path), "retained Feature Studio snapshot"
    )
    return refresh_feature_snapshot(suite_root, snapshot)


def validate_feature_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach one closed Feature Studio snapshot V2."""

    if not isinstance(snapshot, Mapping):
        _fail("Feature Studio snapshot is not an object")
    value = _validate_snapshot_schema_value(snapshot)
    material = {key: item for key, item in value.items() if key != "snapshot_id"}
    if value["snapshot_id"] != _content_id("feature-studio-snapshot", material):
        _fail("Feature Studio snapshot identity is invalid")

    result = value["result"]
    context = value["context"]
    plan = value["plan"]
    identities = value["identities"]
    export = value["export"]
    expected_identities = {
        "result_id": result["result_id"],
        "semantic_projection_id": result["semantic_projection_id"],
        "flow_plan_id": plan["flow_plan_id"],
        "blueprint_plan_id": plan["blueprint_plan_id"],
        "source_snapshot_id": context["source_snapshot_id"],
        "material_flow_receipt_id": identities["material_flow_receipt_id"],
        "blueprint_stage_id": identities["blueprint_stage_id"],
        "candidate_id": identities["candidate_id"],
        "staged_tree_id": identities["staged_tree_id"],
        "pack_profile_family_id": context["pack_profile_family_id"],
        "platform_profile_id": context["platform_profile_id"],
        "pack_profile_id": context["pack_profile_id"],
        "profile_assessment_id": context["profile_assessment_id"],
        "crucible_snapshot_id": context["crucible_snapshot_id"],
        "runtime_session_id": context["runtime_session_id"],
        "runtime_plan_id": context["runtime_plan_id"],
        "materialization_id": context["materialization_id"],
        "final_launch_id": context["final_launch_id"],
        "export_receipt_id": None if export is None else export["receipt_id"],
    }
    if identities != expected_identities:
        _fail("Feature Studio snapshot identity links disagree")
    expected_availability = {
        "result_state": result["state"],
        "plan_state": plan["state"],
        "platform_profile_state": context["platform_profile_state"],
        "source_state": context["evidence_layers"]["source"],
        "planned_state": context["evidence_layers"]["planned"],
        "runtime_state": context["evidence_layers"]["runtime"],
    }
    if value["availability"] != expected_availability:
        _fail("Feature Studio snapshot availability links disagree")
    operation_states = {
        "inspect": {"ready", "incomplete", "complete"},
        "plan": {"ready"},
        "verify": {"ready", "incomplete", "complete"},
        "explain": {"ready"},
        "export": {"ready", "complete"},
    }
    observed_runtime = (
        result["operation"] in {"inspect", "verify"}
        and result["state"] == "complete"
    )
    profile_runtime_values = (
        context["platform_profile_id"],
        context["pack_profile_id"],
        context["profile_assessment_format"],
        context["profile_assessment_id"],
        context["crucible_snapshot_id"],
        context["runtime_session_id"],
        context["runtime_plan_id"],
        context["materialization_id"],
        context["final_launch_id"],
    )
    if (
        observed_runtime
        and (
            any(item is None for item in profile_runtime_values)
            or context["pack_profile_family_id"] != context["pack_profile_id"]
        )
        or not observed_runtime
        and any(item is not None for item in profile_runtime_values)
    ):
        _fail(
            "Feature Studio snapshot observed-runtime profile/runtime "
            "identity tuple disagrees"
        )
    if (
        result["state"] not in operation_states[result["operation"]]
        or value["review_binding"]["flow_plan_id"] != plan["flow_plan_id"]
        or value["review_binding"]["source_snapshot_id"]
        != context["source_snapshot_id"]
        or value["review_binding"]["state"] == "catalog-v2-bound"
        and value["review_binding"]["command_id"]
        != "feature-studio." + result["operation"]
        or observed_runtime
        != (context["platform_profile_state"] == "profile-observation-bound")
        or observed_runtime
        != (context["evidence_layers"]["runtime"] == "observed")
        or (result["operation"] == "export" and result["state"] == "complete")
        != (export is not None)
        or value["actions"] != _actions()
    ):
        _fail("Feature Studio snapshot result links disagree")
    changes = {
        change["relative_path"]: change
        for change in value["planned_changes"]
    }
    if len(changes) != len(value["planned_changes"]):
        _fail("Feature Studio snapshot repeats a planned source path")
    source_keys: set[tuple[str, int]] = set()
    source_paths: set[str] = set()
    for source in value["source_locations"]:
        source_key = (source["relative_path"], source["hunk_index"])
        if source_key in source_keys:
            _fail("Feature Studio snapshot repeats a planned source hunk")
        source_keys.add(source_key)
        source_paths.add(source["relative_path"])
        change = changes.get(source["relative_path"])
        if (
            source["workspace_uri"] != context["workspace_uri"]
            or change is None
            or source["operation"] != change["operation"]
            or source["source_sha256"] != change["source_sha256"]
            or source["planned_sha256"] != change["planned_sha256"]
        ):
            _fail("Feature Studio snapshot source and plan links disagree")
        for source_range in (source["range"], source["planned_range"]):
            start = source_range["start_line"]
            end = source_range["end_line"]
            if (start == 0) != (end == 0) or start > 0 and end < start:
                _fail("Feature Studio snapshot source range is invalid")
    if source_paths != set(changes):
        _fail("Feature Studio snapshot source paths are incomplete")
    links = {link["role"]: link for link in value["owner_links"]}
    roles = [link["role"] for link in value["owner_links"]]
    if observed_runtime:
        expected_roles = {
            "material-fluid-plan",
            "material-fluid-receipt",
            "blueprint-stage-receipt",
            "runtime-observation-session",
            "runtime-final-launch-receipt",
            "runtime-materialization-receipt",
        }
    elif result["state"] == "incomplete":
        expected_roles = {"material-fluid-plan", "material-fluid-receipt"}
    elif result["operation"] == "export" and result["state"] == "complete":
        expected_roles = {
            "material-fluid-plan",
            "feature-studio-export-receipt",
        }
    else:
        expected_roles = {"material-fluid-plan"}
    if len(roles) != len(set(roles)) or set(roles) != expected_roles:
        _fail("Feature Studio snapshot owner links are incomplete")
    expected_owner_ids = {
        "material-fluid-plan": plan["flow_plan_id"],
        "material-fluid-receipt": identities["material_flow_receipt_id"],
        "blueprint-stage-receipt": identities["blueprint_stage_id"],
        "runtime-observation-session": context["runtime_session_id"],
        "runtime-final-launch-receipt": context["final_launch_id"],
        "runtime-materialization-receipt": context["materialization_id"],
        "feature-studio-export-receipt": identities["export_receipt_id"],
    }
    if any(
        links[role]["owner_id"] != expected_owner_ids[role]
        for role in expected_roles
    ):
        _fail("Feature Studio snapshot owner identities disagree")
    if (
        ("material-fluid-receipt" in links)
        != (identities["material_flow_receipt_id"] is not None)
        or ("blueprint-stage-receipt" in links)
        != (identities["blueprint_stage_id"] is not None)
    ):
        _fail("Feature Studio snapshot owner identity availability disagrees")

    origin = value["origin"]
    if (
        origin["refresh"]["state"] == "available"
        and not _is_local_file_uri(origin["uri"])
    ):
        _fail("Feature Studio snapshot retained origin is not a local file")
    if any(
        link["retained"] is not None
        and not _is_local_file_uri(link["retained"]["uri"])
        for link in value["owner_links"]
    ):
        _fail("Feature Studio snapshot owner link is not a local file")
    if origin["kind"] == _RECEIPT_ORIGIN:
        receipt_links = [
            link
            for link in value["owner_links"]
            if link["role"] == "material-fluid-receipt"
        ]
        if (
            len(receipt_links) != 1
            or receipt_links[0]["retained"] is None
            or receipt_links[0]["retained"]["uri"] != origin["uri"]
            or receipt_links[0]["retained"]["sha256"] != origin["sha256"]
            or receipt_links[0]["retained"]["size"] != origin["size"]
        ):
            _fail("Feature Studio snapshot receipt origin is rebound")
    return value


__all__ = [
    "SNAPSHOT_FORMAT",
    "open_feature_snapshot",
    "project_feature_snapshot",
    "refresh_feature_snapshot",
    "refresh_feature_snapshot_file",
    "validate_feature_snapshot",
]
