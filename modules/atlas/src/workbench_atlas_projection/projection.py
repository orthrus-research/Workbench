"""Canonical SOURCE/RUNTIME/PLAYABLE projection with retained authority."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from typing import Any, Mapping, Sequence

from workbench_crucible_stage_snapshot import validate_stage_snapshot
from workbench_api.source_declarations import validate_source_declarations


PROJECTION_FORMAT = "workbench-atlas-semantic-projection-v1"
PROJECTION_SCHEMA_VERSION = 1


class AtlasProjectionError(ValueError):
    """Inputs cannot support a truthful Atlas semantic projection."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def canonical_semantic_identity(descriptor: Mapping[str, Any]) -> str:
    normalized = _semantic_descriptor(descriptor)
    return _content_id("workbench-atlas-semantic:sha256:", normalized)


def build_projection(
    source_feed: Mapping[str, Any],
    runtime_snapshot: Mapping[str, Any],
    adapter: Mapping[str, Any],
) -> dict[str, Any]:
    """Link source and observed runtime inputs, then derive PLAYABLE facts."""

    source = validate_source_declarations(source_feed)
    runtime = validate_stage_snapshot(runtime_snapshot)
    adapter_value = _validate_adapter(adapter)
    source_binding = source["binding"]
    runtime_binding = runtime["binding"]
    pack_profile_id = adapter_value["pack_profile_id"]
    if source_binding["pack_profile_id"] != pack_profile_id:
        raise AtlasProjectionError("source feed does not match the Atlas adapter")
    if runtime_binding["pack_profile_id"] != pack_profile_id:
        raise AtlasProjectionError("runtime snapshot does not match the Atlas adapter")
    if source_binding["platform_profile_id"] != runtime_binding["platform_profile_id"]:
        raise AtlasProjectionError("source and runtime platform profiles differ")

    source_records = [_source_record(row) for row in source["declarations"]]
    runtime_records = [
        *(_runtime_record(row) for row in runtime["registry"]),
        *(_runtime_record(row) for row in runtime["effects"]),
    ]
    playable_records, diagnostics = _derive_playability(
        source_records, runtime_records, adapter_value
    )
    value: dict[str, Any] = {
        "format": PROJECTION_FORMAT,
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "projection_id": "",
        "authority": {
            "owner": "Atlas",
            "claim": "linked evidence and derived semantic interpretation",
            "source_owner": "Pack Program Studio",
            "runtime_owner": "Crucible",
            "construction_owner": "Blueprints",
        },
        "binding": {
            "pack_profile_id": pack_profile_id,
            "platform_profile_id": source_binding["platform_profile_id"],
            "adapter_id": adapter_value["adapter_id"],
            "source_declaration_set_id": source["declaration_set_id"],
            "runtime_snapshot_id": runtime["snapshot_id"],
            "runtime_stage": runtime_binding["stage"],
        },
        "layers": {
            "SOURCE": {
                "authority": "Pack Program Studio",
                "evidence_state": "static-candidate",
                "records": source_records,
            },
            "RUNTIME": {
                "authority": "Crucible",
                "evidence_state": "runtime-observed",
                "records": runtime_records,
            },
            "PLAYABLE": {
                "authority": "Atlas",
                "evidence_state": "derived",
                "records": playable_records,
            },
        },
        "diagnostics": diagnostics,
        "summary": {
            "status": "blocked" if any(row["severity"] == "error" for row in diagnostics) else "ready",
            "source_records": len(source_records),
            "runtime_records": len(runtime_records),
            "playable_records": len(playable_records),
            "errors": sum(row["severity"] == "error" for row in diagnostics),
            "warnings": sum(row["severity"] == "warning" for row in diagnostics),
            "diagnostic_codes": dict(
                sorted(
                    {
                        code: sum(row["code"] == code for row in diagnostics)
                        for code in {row["code"] for row in diagnostics}
                    }.items()
                )
            ),
        },
        "limitations": [
            "SOURCE records remain static candidates and RUNTIME records remain Crucible-owned observations.",
            "PLAYABLE records are Atlas derivations bounded by the selected pack adapter, progression order, declaration feed, and exact runtime snapshot.",
            "This V1 evaluates required-node reachability, fluid-capacity feasibility, and duplicate observed registration effects only.",
        ],
    }
    value["projection_id"] = _projection_identity(value)
    return validate_projection(value)


def validate_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AtlasProjectionError("Atlas projection must be an object")
    required = {
        "format",
        "schema_version",
        "projection_id",
        "authority",
        "binding",
        "layers",
        "diagnostics",
        "summary",
        "limitations",
    }
    if set(value) != required:
        raise AtlasProjectionError("Atlas projection has unexpected keys")
    if value["format"] != PROJECTION_FORMAT or value["schema_version"] != 1:
        raise AtlasProjectionError("unsupported Atlas projection format")
    if value["projection_id"] != _projection_identity(value):
        raise AtlasProjectionError("Atlas projection identity does not match content")
    layers = value["layers"]
    if not isinstance(layers, Mapping) or set(layers) != {"SOURCE", "RUNTIME", "PLAYABLE"}:
        raise AtlasProjectionError("Atlas projection must contain exactly three state layers")
    expected = {
        "SOURCE": ("Pack Program Studio", "static-candidate"),
        "RUNTIME": ("Crucible", "runtime-observed"),
        "PLAYABLE": ("Atlas", "derived"),
    }
    known_records: set[str] = set()
    for layer_name, (authority, evidence_state) in expected.items():
        layer = layers[layer_name]
        if not isinstance(layer, Mapping):
            raise AtlasProjectionError(f"{layer_name} layer is malformed")
        if layer.get("authority") != authority or layer.get("evidence_state") != evidence_state:
            raise AtlasProjectionError(f"{layer_name} authority or evidence state drifted")
        records = layer.get("records")
        if not isinstance(records, list):
            raise AtlasProjectionError(f"{layer_name} records must be a list")
        for record in records:
            if not isinstance(record, Mapping):
                raise AtlasProjectionError(f"{layer_name} record is malformed")
            if record.get("evidence_state") != evidence_state:
                raise AtlasProjectionError(f"{layer_name} record evidence state drifted")
            descriptor = record.get("semantic_descriptor")
            if record.get("semantic_id") != canonical_semantic_identity(descriptor):
                raise AtlasProjectionError("canonical semantic identity does not match descriptor")
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                raise AtlasProjectionError("layer record identity is missing")
            if record_id in known_records:
                raise AtlasProjectionError("layer record identities are duplicate")
            known_records.add(record_id)
            provenance = record.get("provenance")
            if not isinstance(provenance, Mapping) or provenance.get("authority") != authority:
                raise AtlasProjectionError(f"{layer_name} record provenance lost its owner")
    diagnostics = value["diagnostics"]
    if not isinstance(diagnostics, list):
        raise AtlasProjectionError("Atlas diagnostics must be a list")
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping):
            raise AtlasProjectionError("Atlas diagnostic is malformed")
        for pointer in diagnostic.get("evidence", []):
            if pointer.get("record_id") not in known_records:
                raise AtlasProjectionError("Atlas diagnostic references unknown evidence")
    summary = value["summary"]
    if not isinstance(summary, Mapping):
        raise AtlasProjectionError("Atlas projection summary is malformed")
    if summary.get("source_records") != len(layers["SOURCE"]["records"]):
        raise AtlasProjectionError("Atlas SOURCE count is stale")
    if summary.get("runtime_records") != len(layers["RUNTIME"]["records"]):
        raise AtlasProjectionError("Atlas RUNTIME count is stale")
    if summary.get("playable_records") != len(layers["PLAYABLE"]["records"]):
        raise AtlasProjectionError("Atlas PLAYABLE count is stale")
    return dict(value)


def _source_record(row: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = _semantic_descriptor(row["semantic_descriptor"])
    return {
        "record_id": row["source_declaration_id"],
        "semantic_id": canonical_semantic_identity(descriptor),
        "semantic_descriptor": descriptor,
        "evidence_state": "static-candidate",
        "attributes": dict(row["attributes"]),
        "lifecycle": dict(row["lifecycle"]),
        "provenance": dict(row["provenance"]),
        "source_effect_id": row["source_effect_id"],
    }


def _runtime_record(row: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = _semantic_descriptor(row["semantic_descriptor"])
    observed_state = (
        dict(row["registry_state"])
        if row["record_kind"] == "registry-observation"
        else dict(row["effect_state"])
    )
    return {
        "record_id": row["runtime_record_id"],
        "record_kind": row["record_kind"],
        "semantic_id": canonical_semantic_identity(descriptor),
        "semantic_descriptor": descriptor,
        "evidence_state": "runtime-observed",
        "stage": row["stage"],
        "operation": row.get("operation"),
        "observed_state": observed_state,
        "provenance": dict(row["provenance"]),
    }


def _derive_playability(
    source_records: Sequence[Mapping[str, Any]],
    runtime_records: Sequence[Mapping[str, Any]],
    adapter: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    registry_by_semantic: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    effects_by_semantic: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in runtime_records:
        target = registry_by_semantic if row["record_kind"] == "registry-observation" else effects_by_semantic
        target[row["semantic_id"]].append(row)
    diagnostics: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    source_by_semantic: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in source_records:
        source_by_semantic[row["semantic_id"]].append(row)
        attributes = row["attributes"]
        if attributes.get("semantic_role") != "recipe":
            continue
        statuses[row["semantic_id"]] = "playable"
        intended_stage = attributes.get("intended_stage")
        for requirement in attributes.get("requirements", []):
            descriptor = requirement.get("semantic_descriptor")
            requirement_id = canonical_semantic_identity(descriptor)
            observations = registry_by_semantic.get(requirement_id, [])
            if not _reachable_at_stage(observations, intended_stage, adapter["progression_order"]):
                statuses[row["semantic_id"]] = "blocked"
                diagnostics.append(
                    _diagnostic(
                        code="PROGRESSION_UNREACHABLE",
                        semantic_id=row["semantic_id"],
                        message=(
                            f"{_label(row)} requires {_descriptor_label(descriptor)}, which is not reachable at {intended_stage}."
                        ),
                        source_records=[row],
                        runtime_records=observations,
                        details={
                            "requirement_semantic_id": requirement_id,
                            "requirement": descriptor,
                            "intended_stage": intended_stage,
                            "observed_reachable_stages": sorted(
                                {
                                    stage
                                    for observation in observations
                                    for stage in observation["observed_state"].get("reachable_stages", [])
                                }
                            ),
                        },
                    )
                )
        machine = attributes.get("machine_feasibility")
        if isinstance(machine, Mapping):
            diagnostic = _machine_diagnostic(
                row, machine, registry_by_semantic, adapter["progression_order"]
            )
            if diagnostic is not None:
                statuses[row["semantic_id"]] = "blocked"
                diagnostics.append(diagnostic)

    duplicate_operations = set(adapter["checks"]["duplicate_effect_operations"])
    for semantic_id, effects in effects_by_semantic.items():
        observed = [row for row in effects if row.get("operation") in duplicate_operations]
        if len(observed) < 2:
            continue
        statuses[semantic_id] = "conflicted"
        diagnostics.append(
            _diagnostic(
                code="DUPLICATE_REGISTRATION",
                semantic_id=semantic_id,
                message=f"{_descriptor_label(observed[0]['semantic_descriptor'])} has {len(observed)} observed registration effects in one stage.",
                source_records=source_by_semantic.get(semantic_id, []),
                runtime_records=observed,
                details={
                    "effect_count": len(observed),
                    "stage": observed[0]["stage"],
                    "operations": [row["operation"] for row in observed],
                },
            )
        )

    all_semantics = sorted({row["semantic_id"] for row in [*source_records, *runtime_records]})
    playable = []
    for semantic_id in all_semantics:
        sources = source_by_semantic.get(semantic_id, [])
        runtimes = [
            *registry_by_semantic.get(semantic_id, []),
            *effects_by_semantic.get(semantic_id, []),
        ]
        descriptor = (sources or runtimes)[0]["semantic_descriptor"]
        status = statuses.get(semantic_id, _default_status(runtimes))
        evidence = [
            *({"layer": "SOURCE", "record_id": row["record_id"]} for row in sources),
            *({"layer": "RUNTIME", "record_id": row["record_id"]} for row in runtimes),
        ]
        payload = {
            "semantic_id": semantic_id,
            "semantic_descriptor": descriptor,
            "status": status,
            "evidence": evidence,
        }
        playable.append(
            {
                "record_id": _content_id("workbench-atlas-playable-record:sha256:", payload),
                **payload,
                "evidence_state": "derived",
                "provenance": {
                    "authority": "Atlas",
                    "method": "workbench-atlas-semantic-projection-v1",
                    "inputs": evidence,
                },
            }
        )
    diagnostics.sort(key=lambda row: (row["code"], row["semantic_id"], row["diagnostic_id"]))
    return playable, diagnostics


def _reachable_at_stage(
    observations: Sequence[Mapping[str, Any]],
    intended_stage: Any,
    progression_order: Sequence[str],
) -> bool:
    if not isinstance(intended_stage, str) or intended_stage not in progression_order:
        return False
    intended_index = progression_order.index(intended_stage)
    for observation in observations:
        state = observation["observed_state"]
        if not state.get("registered"):
            continue
        for stage in state.get("reachable_stages", []):
            if stage in progression_order and progression_order.index(stage) <= intended_index:
                return True
    return False


def _machine_diagnostic(
    source: Mapping[str, Any],
    requirement: Mapping[str, Any],
    registry_by_semantic: Mapping[str, Sequence[Mapping[str, Any]]],
    progression_order: Sequence[str],
) -> dict[str, Any] | None:
    intended_stage = source["attributes"].get("intended_stage")
    required_capacity = requirement.get("required_fluid_capacity_mb")
    candidates = requirement.get("candidate_machine_descriptors", [])
    if not isinstance(required_capacity, int) or required_capacity <= 0:
        raise AtlasProjectionError("machine fluid-capacity requirement must be positive")
    if intended_stage not in progression_order:
        raise AtlasProjectionError("recipe intended stage is not in the adapter progression order")
    observations = []
    for descriptor in candidates:
        observations.extend(registry_by_semantic.get(canonical_semantic_identity(descriptor), []))
    executable = []
    for row in observations:
        state = row["observed_state"]
        stage = state.get("progression_stage")
        capacity = state.get("max_fluid_input_mb")
        if state.get("registered") and stage in progression_order and isinstance(capacity, int) and capacity >= required_capacity:
            executable.append(row)
    intended_index = progression_order.index(intended_stage)
    if any(progression_order.index(row["observed_state"]["progression_stage"]) <= intended_index for row in executable):
        return None
    available_at_stage = [
        row["observed_state"].get("max_fluid_input_mb")
        for row in observations
        if row["observed_state"].get("progression_stage") == intended_stage
        and isinstance(row["observed_state"].get("max_fluid_input_mb"), int)
    ]
    first_stage = min(
        (row["observed_state"]["progression_stage"] for row in executable),
        key=progression_order.index,
        default=None,
    )
    return _diagnostic(
        code="RECIPE_MACHINE_MISMATCH",
        semantic_id=source["semantic_id"],
        message=(
            f"{_label(source)} requires {required_capacity:,} mB but the best {intended_stage} candidate holds {max(available_at_stage, default=0):,} mB."
        ),
        source_records=[source],
        runtime_records=observations,
        details={
            "intended_stage": intended_stage,
            "required_fluid_capacity_mb": required_capacity,
            "available_fluid_capacity_mb": max(available_at_stage, default=0),
            "first_executable_stage": first_stage,
        },
    )


def _diagnostic(
    *,
    code: str,
    semantic_id: str,
    message: str,
    source_records: Sequence[Mapping[str, Any]],
    runtime_records: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = [
        *({"layer": "SOURCE", "record_id": row["record_id"]} for row in source_records),
        *({"layer": "RUNTIME", "record_id": row["record_id"]} for row in runtime_records),
    ]
    payload = {
        "code": code,
        "severity": "error",
        "semantic_id": semantic_id,
        "message": message,
        "evidence": evidence,
        "details": dict(details),
    }
    return {
        "diagnostic_id": _content_id("workbench-atlas-diagnostic:sha256:", payload),
        **payload,
    }


def _default_status(runtime_records: Sequence[Mapping[str, Any]]) -> str:
    registry = [row for row in runtime_records if row["record_kind"] == "registry-observation"]
    if not registry:
        return "not-observed"
    if any(row["observed_state"].get("reachable_stages") for row in registry):
        return "reachable"
    if any(row["observed_state"].get("registered") for row in registry):
        return "registered-not-proven-reachable"
    return "absent"


def _label(record: Mapping[str, Any]) -> str:
    attributes = record.get("attributes", {})
    return str(attributes.get("label") or _descriptor_label(record["semantic_descriptor"]))


def _descriptor_label(descriptor: Mapping[str, Any]) -> str:
    key = descriptor.get("key", {})
    for name in ("id", "name", "recipe_id", "registry_id", "material"):
        if name in key:
            return str(key[name])
    return json.dumps(key, sort_keys=True, separators=(",", ":"))


def _semantic_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"domain", "kind", "key"}:
        raise AtlasProjectionError("semantic descriptor must contain domain, kind, and key")
    if not isinstance(value["domain"], str) or not value["domain"]:
        raise AtlasProjectionError("semantic descriptor domain is missing")
    if not isinstance(value["kind"], str) or not value["kind"]:
        raise AtlasProjectionError("semantic descriptor kind is missing")
    if not isinstance(value["key"], Mapping) or not value["key"]:
        raise AtlasProjectionError("semantic descriptor key is missing")
    return {"domain": value["domain"], "kind": value["kind"], "key": dict(value["key"])}


def _validate_adapter(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AtlasProjectionError("Atlas profile adapter must be an object")
    required = {"format", "schema_version", "adapter_id", "pack_profile_id", "progression_order", "checks", "limitations"}
    if set(value) != required:
        raise AtlasProjectionError("Atlas profile adapter has unexpected keys")
    if value["format"] != "workbench-atlas-profile-adapter-v1" or value["schema_version"] != 1:
        raise AtlasProjectionError("unsupported Atlas profile adapter")
    order = value["progression_order"]
    if not isinstance(order, list) or not order or len(order) != len(set(order)):
        raise AtlasProjectionError("Atlas profile progression order is invalid")
    checks = value["checks"]
    if not isinstance(checks, Mapping) or not isinstance(checks.get("duplicate_effect_operations"), list):
        raise AtlasProjectionError("Atlas profile check policy is invalid")
    return dict(value)


def _projection_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("projection_id", None)
    return _content_id("workbench-atlas-semantic-projection:sha256:", payload)
