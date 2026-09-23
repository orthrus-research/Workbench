"""W01 graph materialization, composition, custody, and bounded reads.

Crucible owns capture-health, occurrence, stability, execution, storage, and
query mechanics.  Atlas-owned branch builders are explicit callables supplied
by the caller.  The composition validator rejects owner/category swaps and
never turns an experimental profile result into support or action authority.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import MappingProxyType
from typing import Any

from workbench_crucible_kernel import (
    ShardKernelConfig,
    assemble_incremental_graph_partitions,
    verify_partition_assembly_equivalence,
)
from workbench_crucible_materializer import (
    BUILTIN_GRAPH_HANDLER_ID,
    BUILTIN_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID,
    BUILTIN_WORLDGEN_GENERATIVE_CAPABILITY_ID,
    BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID,
    BUILTIN_WORLDGEN_REALIZED_CAPABILITY_ID,
    BUILTIN_WORLDGEN_STABILITY_CAPABILITY_ID,
    MaterializationInput,
    MaterializationResult,
    RecipeExecutionBounds,
    RecipeImplementationRegistration,
    WORLDGEN_GRAPH_BUNDLE_VALUE_KIND,
    execute_materialization,
    seal_recipe_execution_binding,
)
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id, parse_canonical_json
from workbench_crucible import ValidatedRecord, seal_record, semantic_root_v2
from workbench_crucible.synthetic import build_synthetic_publication

from .capture import (
    SameRunJoinReceipt,
    StabilityReceipt,
    load_same_run_join_receipt,
    load_stability_receipt,
)
from .identity import WorldgenExecutionEnvelope, load_execution_envelope


GRAPH_FAMILY_ID = "workbench.worldgen"
GRAPH_SET_FAMILY_ID = "workbench.worldgen.graph-set.v1"
WORLDGEN_PURPOSE_ID = "crucible.m4.w01.proving"
WORLDGEN_EVIDENCE_KIND = "worldgen.proof-bundle"
WORLDGEN_SUBJECT_SCHEMA_ID = (
    "workbench://schemas/crucible/worldgen-subject-identity-v1.schema.json"
)

CATEGORY_CAPTURE_HEALTH = "crucible.worldgen.capture-health.v1"
CATEGORY_OCCURRENCE = "crucible.worldgen.occurrence.v1"
CATEGORY_STABILITY = "crucible.worldgen.stability.v1"
CATEGORY_GENERATIVE = "atlas.worldgen.generative.v1"
CATEGORY_REALIZED = "atlas.worldgen.realized.v1"

RESOLUTION_EXACT = "worldgen.exact.v1"
RESOLUTION_POSITION = "worldgen.position.v1"
RESOLUTION_CHUNK = "worldgen.chunk.v1"
RESOLUTION_REGION = "worldgen.region.v1"
RESOLUTION_OPERATIONAL = "worldgen.operational.v1"

_CAPABILITY_BY_CATEGORY = {
    CATEGORY_CAPTURE_HEALTH: BUILTIN_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID,
    CATEGORY_OCCURRENCE: BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID,
    CATEGORY_STABILITY: BUILTIN_WORLDGEN_STABILITY_CAPABILITY_ID,
    CATEGORY_GENERATIVE: BUILTIN_WORLDGEN_GENERATIVE_CAPABILITY_ID,
    CATEGORY_REALIZED: BUILTIN_WORLDGEN_REALIZED_CAPABILITY_ID,
}
_BRANCH_BY_CATEGORY = {
    CATEGORY_CAPTURE_HEALTH: "capture_health",
    CATEGORY_OCCURRENCE: "occurrence",
    CATEGORY_STABILITY: "stability",
    CATEGORY_GENERATIVE: "generative",
    CATEGORY_REALIZED: "realized",
}
_OWNER_LABEL_BY_CATEGORY = {
    CATEGORY_CAPTURE_HEALTH: "crucible-worldgen-capture",
    CATEGORY_OCCURRENCE: "crucible-worldgen-capture",
    CATEGORY_STABILITY: "crucible-worldgen-capture",
    CATEGORY_GENERATIVE: "atlas-worldgen-semantics",
    CATEGORY_REALIZED: "atlas-worldgen-semantics",
}
_MEMBER_SPECS = (
    (CATEGORY_CAPTURE_HEALTH, RESOLUTION_OPERATIONAL),
    (CATEGORY_OCCURRENCE, RESOLUTION_EXACT),
    (CATEGORY_OCCURRENCE, RESOLUTION_CHUNK),
    (CATEGORY_OCCURRENCE, RESOLUTION_REGION),
    (CATEGORY_STABILITY, RESOLUTION_CHUNK),
    (CATEGORY_STABILITY, RESOLUTION_REGION),
    (CATEGORY_GENERATIVE, RESOLUTION_OPERATIONAL),
    (CATEGORY_REALIZED, RESOLUTION_POSITION),
    (CATEGORY_REALIZED, RESOLUTION_CHUNK),
    (CATEGORY_REALIZED, RESOLUTION_REGION),
)


class WorldgenGraphError(ValueError):
    """A W01 graph proof is malformed, incompatible, or incomplete."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorldgenGraphError(message)


def _authority(label: str) -> dict[str, str]:
    return {
        "authority_adapter_id": content_id(
            "authority-adapter", {"adapter": "worldgen-v2", "owner": label}
        ),
        "owner_authority_id": content_id("authority", {"owner": label}),
        "owner_revision_id": content_id(
            "owner-revision", {"owner": label, "version": 1}
        ),
    }


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _canonical_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = parse_canonical_json(raw)
    except Exception as exc:
        raise WorldgenGraphError(f"{label} is not canonical JSON: {path}") from exc
    _require(type(value) is dict, f"{label} must be a JSON object")
    return value


def _json_mapping(path: Path, label: str) -> dict[str, Any]:
    """Load an identity-bearing external JSON file without rewriting its bytes."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorldgenGraphError(f"{label} is not valid JSON: {path}") from exc
    _require(type(value) is dict, f"{label} must be a JSON object")
    return value


def _content_record(path: Path, *, kind: str, label: str) -> dict[str, Any]:
    value = _canonical_mapping(path, label)
    _require(value.get("kind") == kind, f"{label} kind differs")
    body = dict(value)
    supplied = body.pop("id", None)
    _require(supplied == content_id(kind, body), f"{label} identity differs")
    return value


def _profile_support_binding(envelope: WorldgenExecutionEnvelope) -> dict[str, Any]:
    pack = envelope.to_dict()["context_ref"]["profile_scope"]["pack"]
    return {
        "profile_adapter_id": pack["profile_adapter_id"],
        "profile_authority": pack["profile_authority"],
        "profile_revision_id": content_id(
            "profile-revision",
            {
                "adapter": "crucible-graph-profile-binding-v1",
                "pack_profile_revision_id": pack["pack_profile_revision_id"],
            },
        ),
        "support_decision_ids": pack["support_decision_ids"],
    }


@dataclass(frozen=True, slots=True)
class WorldgenProofBundle:
    """Validated retained controls and exact V1/profile inputs for W01."""

    envelope_a: WorldgenExecutionEnvelope
    envelope_b: WorldgenExecutionEnvelope
    join_a: SameRunJoinReceipt
    join_b: SameRunJoinReceipt
    stability: StabilityReceipt
    capture_a: Mapping[str, Any]
    causal_a: Mapping[str, Any]
    terminal_a: Mapping[str, Any]
    terminal_b: Mapping[str, Any]
    action_policy: Mapping[str, Any]
    action_policy_bytes: bytes
    action_policy_sha256: str
    stale_pattern: Mapping[str, Any]
    stale_pattern_sha256: str
    current_feature_source_sha256: str
    raw_v1_schema_sha256: str
    source_artifacts: tuple[Mapping[str, str], ...]

    @property
    def context_ref_id(self) -> str:
        return self.envelope_a.context_ref_id

    @property
    def profile_support_binding(self) -> dict[str, Any]:
        return _profile_support_binding(self.envelope_a)


def _source_artifact(name: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    ordered = sorted(rows, key=lambda row: row["row_id"].encode("utf-8"))
    raw = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in ordered)
    _require(0 < len(raw) <= 32 * 1024, f"source artifact {name} exceeds its bound")
    return {
        "bytes_base64": base64.b64encode(raw).decode("ascii"),
        "name": name,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _site_source_row(site: Mapping[str, Any], control: str) -> dict[str, Any]:
    _require(control in {"a", "b"}, "site source row has an invalid control")
    chunk = site["chunk"]
    value: dict[str, Any] = {
        "row_id": f"row.site-{control}.{chunk['x']}.{chunk['z']}",
        "chunk": chunk,
        "decision": site["decision"],
        "eligibility_draw": site["eligibility_draw"],
        "site_state": site["site_state"],
    }
    if site["decision"] == "placed":
        value.update(
            {
                "base_position": site["base_position"],
                "optional_top_draw": site["optional_top_draw"],
                "writes": [
                    {
                        "after_state": row["causal"]["after_state"],
                        "final_state": row["final_state"]["palette_state"],
                        "invoked": row["causal"]["invoked"],
                        "join_state": row["join_state"],
                        "position": row["causal"]["position"],
                        "role": row["causal"]["role"],
                    }
                    for row in site["writes"]
                ],
            }
        )
    return value


def _stability_source_row(site: Mapping[str, Any]) -> dict[str, Any]:
    chunk = site["chunk"]
    return {
        **dict(site),
        "row_id": f"row.stability.{chunk['x']}.{chunk['z']}",
    }


def load_worldgen_proof_bundle(
    *,
    control_a_root: Path,
    control_b_root: Path,
    stability_receipt_path: Path,
    action_policy_path: Path,
    stale_pattern_path: Path,
    current_feature_source_path: Path,
    raw_v1_schema_path: Path,
) -> WorldgenProofBundle:
    """Load the exact two controls and fail closed on every identity seam."""

    def artifact(root: Path, name: str) -> Path:
        return root / "artifacts" / "worldgen-v2" / name

    envelope_a = load_execution_envelope(artifact(control_a_root, "execution-envelope-v2.json").read_bytes())
    envelope_b = load_execution_envelope(artifact(control_b_root, "execution-envelope-v2.json").read_bytes())
    join_a = load_same_run_join_receipt(artifact(control_a_root, "same-run-join-receipt-v2.json").read_bytes())
    join_b = load_same_run_join_receipt(artifact(control_b_root, "same-run-join-receipt-v2.json").read_bytes())
    stability = load_stability_receipt(stability_receipt_path.read_bytes())
    capture_a = _content_record(
        artifact(control_a_root, "population-capture-audit-v2.json"),
        kind="worldgen-population-capture-audit",
        label="control-A capture audit",
    )
    causal_a = _content_record(
        artifact(control_a_root, "causal-trace-receipt-v2.json"),
        kind="worldgen-causal-trace-receipt",
        label="control-A causal receipt",
    )
    terminal_a = _content_record(
        artifact(control_a_root, "execution-terminal-v2.json"),
        kind="worldgen-execution-terminal",
        label="control-A terminal",
    )
    terminal_b = _content_record(
        artifact(control_b_root, "execution-terminal-v2.json"),
        kind="worldgen-execution-terminal",
        label="control-B terminal",
    )
    _require(envelope_a.run_plan_id == envelope_b.run_plan_id, "controls use different run plans")
    _require(envelope_a.envelope_id != envelope_b.envelope_id, "controls reuse an execution envelope")
    for left, right, label in (
        (envelope_a.envelope_id, join_a.envelope_id, "control-A join"),
        (envelope_b.envelope_id, join_b.envelope_id, "control-B join"),
        (envelope_a.run_plan_id, join_a.run_plan_id, "control-A plan"),
        (envelope_b.run_plan_id, join_b.run_plan_id, "control-B plan"),
    ):
        _require(left == right, f"{label} differs")
    _require(
        tuple(sorted((envelope_a.envelope_id, envelope_b.envelope_id), key=lambda item: item.encode("utf-8")))
        == tuple(stability.to_dict()["control_execution_envelope_ids"]),
        "stability receipt names another control pair",
    )
    _require(
        tuple(sorted((join_a.receipt_id, join_b.receipt_id), key=lambda item: item.encode("utf-8")))
        == tuple(stability.to_dict()["control_join_receipt_ids"]),
        "stability receipt names another join pair",
    )
    _require(stability.run_plan_id == envelope_a.run_plan_id, "stability run plan differs")
    _require(stability.outcome == "stable-within-envelope", "fresh controls are not stable within the envelope")
    for envelope, join, terminal, label in (
        (envelope_a, join_a, terminal_a, "control A"),
        (envelope_b, join_b, terminal_b, "control B"),
    ):
        _require(terminal["execution_envelope_id"] == envelope.envelope_id, f"{label} terminal envelope differs")
        _require(terminal["process_outcome"] == "complete" and terminal["process_exit_code"] == 0, f"{label} did not complete")
        _require(join.receipt_id in terminal["producer_receipt_ids"], f"{label} terminal omits its join")
        _require(join.strata_receipt_id in terminal["producer_receipt_ids"], f"{label} terminal omits Strata")
    _require(capture_a["execution_envelope_id"] == envelope_a.envelope_id, "capture audit envelope differs")
    _require(causal_a["execution_envelope_id"] == envelope_a.envelope_id, "causal receipt envelope differs")
    _require(capture_a["id"] == join_a.capture_audit_id, "join names another capture audit")
    _require(causal_a["id"] == join_a.causal_receipt_id, "join names another causal receipt")
    _require(capture_a["dropped_record_count"] == 0 and capture_a["open_span_count"] == 0, "capture is incomplete")

    action_policy_bytes = action_policy_path.read_bytes()
    action_policy = _json_mapping(action_policy_path, "profile action policy")
    action_policy_sha256, _ = _hash_file(action_policy_path)
    _require(
        action_policy.get("format") == "workbench-supersymmetry-worldgen-action-policy-draft-v1"
        and action_policy.get("state") == "experimental-draft",
        "profile action policy is not the experimental W01 draft",
    )
    run_plan = envelope_a.to_dict()["run_plan"]
    scope = action_policy.get("scope", {})
    bounds = action_policy.get("resource_bounds", {})
    _require(
        scope.get("generator_id") == run_plan["generator_id"]
        and scope.get("world_type") == run_plan["world_type"]
        and scope.get("physical_side") == "dedicated-server"
        and bounds.get("minecraft_watchdog_max_tick_time_ms") == run_plan["max_tick_time_ms"]
        and bounds.get("scan_timeout_seconds") == run_plan["scan_timeout_seconds"],
        "profile action policy does not bind the executed scope and resource limits",
    )

    stale_pattern = _json_mapping(stale_pattern_path, "retained V1 pattern")
    stale_pattern_sha256, _ = _hash_file(stale_pattern_path)
    current_feature_source_sha256, _ = _hash_file(current_feature_source_path)
    raw_v1_schema_sha256, _ = _hash_file(raw_v1_schema_path)
    _require(stale_pattern.get("schema_version") == 1, "retained pattern is not V1")
    _require(stale_pattern.get("active_stage") == "generate.base", "retained V1 stage conflict disappeared")
    stale_feature = next(
        (
            row
            for row in stale_pattern.get("source_inventory", [])
            if row.get("path", "").endswith("PrototypeFeature.java")
        ),
        None,
    )
    _require(
        stale_feature is not None
        and stale_feature.get("sha256") != current_feature_source_sha256,
        "retained V1 feature source conflict disappeared",
    )

    control_rows = [
        {
            "row_id": "row.action-policy",
            "policy_sha256": action_policy_sha256,
            "state": action_policy["state"],
            "scope": scope,
        },
        {
            "row_id": "row.capture-a",
            "capture_audit_id": capture_a["id"],
            "dropped_record_count": capture_a["dropped_record_count"],
            "hook_health": [
                {
                    "health_state": row["health_state"],
                    "hook_id": row["hook_id"],
                    "observed_injection_count": row["observed_injection_count"],
                }
                for row in capture_a["hook_health"]
            ],
            "open_span_count": capture_a["open_span_count"],
            "open_write_count": capture_a["open_write_count"],
            "population_root_count": capture_a["population_root_count"],
            "raw_sha256": capture_a["raw_sha256"],
            "record_count": capture_a["record_count"],
            "selector": capture_a["selector"],
            "terminal_write_count": capture_a["terminal_write_count"],
        },
        {
            "row_id": "row.causal-a",
            "causal_receipt_id": causal_a["id"],
            "feature_count": causal_a["feature_count"],
            "gate_count": causal_a["gate_count"],
            "launch_log_sha256": causal_a["launch_log_sha256"],
            "placed_count": causal_a["placed_count"],
            "rejected_count": causal_a["rejected_count"],
        },
        {
            "row_id": "row.control-a",
            "context_ref_id": envelope_a.context_ref_id,
            "execution_envelope_id": envelope_a.envelope_id,
            "input_binding_id": envelope_a.input_binding_id,
            "runtime_epoch_id": envelope_a.runtime_epoch_id,
            "world_epoch_id": envelope_a.world_epoch_id,
            "world_instance_id": envelope_a.world_instance_id,
        },
        {
            "row_id": "row.control-b",
            "context_ref_id": envelope_b.context_ref_id,
            "execution_envelope_id": envelope_b.envelope_id,
            "input_binding_id": envelope_b.input_binding_id,
            "runtime_epoch_id": envelope_b.runtime_epoch_id,
            "world_epoch_id": envelope_b.world_epoch_id,
            "world_instance_id": envelope_b.world_instance_id,
        },
        {
            "row_id": "row.current-source",
            "active_stage": "populate.custom",
            "feature_source_sha256": current_feature_source_sha256,
        },
        {
            "row_id": "row.join-a",
            "capture_audit_id": join_a.capture_audit_id,
            "causal_receipt_id": join_a.causal_receipt_id,
            "execution_envelope_id": join_a.envelope_id,
            "receipt_id": join_a.receipt_id,
            "scan_sha256": join_a.scan_sha256,
            "strata_receipt_id": join_a.strata_receipt_id,
            "world_epoch_id": join_a.to_dict()["world_epoch_id"],
        },
        {
            "row_id": "row.join-b",
            "capture_audit_id": join_b.capture_audit_id,
            "causal_receipt_id": join_b.causal_receipt_id,
            "execution_envelope_id": join_b.envelope_id,
            "receipt_id": join_b.receipt_id,
            "scan_sha256": join_b.scan_sha256,
            "strata_receipt_id": join_b.strata_receipt_id,
            "world_epoch_id": join_b.to_dict()["world_epoch_id"],
        },
        {
            "row_id": "row.placeholder",
            "value": "closed-unselected-worldgen-branch",
        },
        {
            "row_id": "row.route",
            "active_stage": "populate.custom",
            "eligibility_bound": 10,
            "rng_algorithm": "java.util.Random.v1",
            "rule_id": "world-studio.boulder.v1",
            "seed_derivation_id": "world-studio.stage-random.v1",
        },
        {
            "row_id": "row.stability",
            "outcome": stability.outcome,
            "receipt_id": stability.receipt_id,
            "run_plan_id": stability.run_plan_id,
        },
        {
            "row_id": "row.stale-pattern",
            "active_stage": stale_pattern["active_stage"],
            "feature_source_sha256": stale_feature["sha256"],
            "pattern_id": stale_pattern["pattern_id"],
            "retained_v1_sha256": stale_pattern_sha256,
        },
        {
            "row_id": "row.v1-observatory-schema",
            "schema_sha256": raw_v1_schema_sha256,
        },
    ]
    source_artifacts = (
        _source_artifact("00-worldgen-controls.ndjson", control_rows),
        _source_artifact(
            "01-worldgen-sites.ndjson",
            [
                *[_site_source_row(site, "a") for site in join_a.sites],
                *[_site_source_row(site, "b") for site in join_b.sites],
            ],
        ),
        _source_artifact("02-worldgen-stability.ndjson", [_stability_source_row(site) for site in stability.sites]),
    )
    _require(
        sum(len(base64.b64decode(row["bytes_base64"])) for row in source_artifacts)
        <= 48 * 1024,
        "combined W01 source artifacts exceed the worker bound",
    )
    return WorldgenProofBundle(
        envelope_a,
        envelope_b,
        join_a,
        join_b,
        stability,
        capture_a,
        causal_a,
        terminal_a,
        terminal_b,
        action_policy,
        action_policy_bytes,
        action_policy_sha256,
        stale_pattern,
        stale_pattern_sha256,
        current_feature_source_sha256,
        raw_v1_schema_sha256,
        source_artifacts,
    )


def _ordered(rows: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: item[key].encode("utf-8"))


def _subject(key: str, logical_key: str, identity: Mapping[str, Any], *rows: str) -> dict[str, Any]:
    return {
        "identity": dict(identity),
        "logical_key": logical_key,
        "source_row_ids": sorted(set(rows), key=lambda item: item.encode("utf-8")),
        "subject_key": key,
    }


def _property(
    logical_key: str,
    subject_key: str,
    property_key: str,
    state: str,
    value: Any,
    *rows: str,
) -> dict[str, Any]:
    return {
        "logical_key": logical_key,
        "property_key": property_key,
        "property_state": state,
        "source_row_ids": sorted(set(rows), key=lambda item: item.encode("utf-8")),
        "subject_key": subject_key,
        "value": value,
    }


def _capture_health_branch(proof: WorldgenProofBundle) -> dict[str, Any]:
    audit = proof.capture_a
    hooks = [
        {
            "health_state": row["health_state"],
            "hook_id": row["hook_id"],
            "observed_injection_count": row["observed_injection_count"],
        }
        for row in audit["hook_health"]
    ]
    subjects = [
        _subject(
            "health.capture",
            "node:capture-health",
            {
                "capture_audit_id": audit["id"],
                "capture_mode": audit["capture_mode"],
                "dropped_record_count": audit["dropped_record_count"],
                "event_listener_count": audit["event_listener_count"],
                "execution_envelope_id": audit["execution_envelope_id"],
                "hook_health": hooks,
                "open_span_count": audit["open_span_count"],
                "open_write_count": audit["open_write_count"],
                "record_count": audit["record_count"],
                "seal_state": "complete",
                "stop_state": proof.terminal_a["process_outcome"],
                "terminal_residue": "none",
            },
            "row.capture-a",
            "row.control-a",
        ),
        _subject(
            "health.resources",
            "node:capture-resource-bounds",
            {
                "minecraft_watchdog_max_tick_time_ms": proof.action_policy["resource_bounds"]["minecraft_watchdog_max_tick_time_ms"],
                "raw_size_bytes": audit["raw_size_bytes"],
                "scan_timeout_seconds": proof.action_policy["resource_bounds"]["scan_timeout_seconds"],
            },
            "row.action-policy",
            "row.capture-a",
        ),
        _subject(
            "health.selector",
            "node:capture-selector",
            {
                "population_root_count": audit["population_root_count"],
                "selector": audit["selector"],
                "selector_state": "closed",
            },
            "row.capture-a",
        ),
        _subject(
            "health.terminal",
            "node:capture-terminal",
            {
                "process_exit_code": proof.terminal_a["process_exit_code"],
                "process_outcome": proof.terminal_a["process_outcome"],
                "producer_receipt_ids": proof.terminal_a["producer_receipt_ids"],
                "terminal_id": proof.terminal_a["id"],
            },
            "row.control-a",
            "row.join-a",
        ),
    ]
    properties = [
        _property(
            "property:capture-health-completion",
            "health.capture",
            "worldgen.capture-completion",
            "known",
            "complete-within-selector",
            "row.capture-a",
        ),
        _property(
            "property:capture-health-loss",
            "health.capture",
            "worldgen.capture-loss",
            "known",
            {"dropped": 0, "open_spans": 0, "open_writes": 0},
            "row.capture-a",
        ),
    ]
    return {
        "emit_evidence_links": True,
        "partition_key": "worldgen.capture-health.operational",
        "properties": _ordered(properties, "logical_key"),
        "relations": [],
        "subjects": _ordered(subjects, "subject_key"),
    }


def _occurrence_branch(
    proof: WorldgenProofBundle,
    resolution_id: str,
    *,
    capture_health_graph_revision_id: str | None,
) -> dict[str, Any]:
    subjects: list[dict[str, Any]] = []
    if resolution_id in {RESOLUTION_EXACT, RESOLUTION_CHUNK}:
        for site in proof.join_a.sites:
            chunk = site["chunk"]
            row_id = f"row.site-a.{chunk['x']}.{chunk['z']}"
            key = f"occurrence.site.{chunk['x']}.{chunk['z']}"
            identity: dict[str, Any] = {
                "capture_audit_id": proof.join_a.capture_audit_id,
                "capture_health_graph_revision_id": capture_health_graph_revision_id,
                "causal_receipt_id": proof.join_a.causal_receipt_id,
                "chunk": chunk,
                "decision": site["decision"],
                "eligibility_draw": site["eligibility_draw"],
                "execution_envelope_id": proof.envelope_a.envelope_id,
                "feature_invocation_state": "completed",
                "gate_decision": "allowed",
                "listener_and_write_capture_state": "complete",
                "resolution_id": resolution_id,
                "same_run_join_receipt_id": proof.join_a.receipt_id,
                "site_state": site["site_state"],
                "world_epoch_id": proof.envelope_a.world_epoch_id,
            }
            if resolution_id == RESOLUTION_EXACT and site["decision"] == "placed":
                identity.update(
                    {
                        "base_position": site["base_position"],
                        "optional_top_draw": site["optional_top_draw"],
                        "writes": [
                            {
                                "invoked": row["causal"]["invoked"],
                                "join_state": row["join_state"],
                                "position": row["causal"]["position"],
                                "role": row["causal"]["role"],
                            }
                            for row in site["writes"]
                        ],
                    }
                )
            subjects.append(_subject(key, f"node:{key}", identity, row_id, "row.capture-a", "row.causal-a"))
        partition = "worldgen.occurrence.exact" if resolution_id == RESOLUTION_EXACT else "worldgen.occurrence.chunk"
    elif resolution_id == RESOLUTION_REGION:
        placed = sum(site["decision"] == "placed" for site in proof.join_a.sites)
        rejected = len(proof.join_a.sites) - placed
        subjects.append(
            _subject(
                "occurrence.region",
                "node:occurrence-region",
                {
                    "capture_health_graph_revision_id": capture_health_graph_revision_id,
                    "capture_state": "complete-within-selector",
                    "execution_envelope_id": proof.envelope_a.envelope_id,
                    "placed_site_count": placed,
                    "rejected_site_count": rejected,
                    "selected_site_count": len(proof.join_a.sites),
                    "selector": proof.capture_a["selector"],
                    "world_epoch_id": proof.envelope_a.world_epoch_id,
                },
                "row.capture-a",
                "row.causal-a",
                "row.join-a",
            )
        )
        partition = "worldgen.occurrence.region"
    else:
        raise WorldgenGraphError(f"unsupported occurrence resolution {resolution_id}")
    return {
        "emit_evidence_links": False,
        "partition_key": partition,
        "properties": [],
        "relations": [],
        "subjects": _ordered(subjects, "subject_key"),
    }


def _stability_branch(proof: WorldgenProofBundle, resolution_id: str) -> dict[str, Any]:
    subjects: list[dict[str, Any]] = []
    if resolution_id == RESOLUTION_CHUNK:
        for site in proof.stability.sites:
            chunk = site["chunk"]
            key = f"stability.site.{chunk['x']}.{chunk['z']}"
            subjects.append(
                _subject(
                    key,
                    f"node:{key}",
                    {
                        **dict(site),
                        "control_execution_envelope_ids": proof.stability.to_dict()["control_execution_envelope_ids"],
                        "run_plan_id": proof.stability.run_plan_id,
                    },
                    f"row.stability.{chunk['x']}.{chunk['z']}",
                    "row.control-a",
                    "row.control-b",
                )
            )
        partition = "worldgen.stability.chunk"
    elif resolution_id == RESOLUTION_REGION:
        subjects.append(
            _subject(
                "stability.region",
                "node:stability-region",
                {
                    "control_execution_envelope_ids": proof.stability.to_dict()["control_execution_envelope_ids"],
                    "control_join_receipt_ids": proof.stability.to_dict()["control_join_receipt_ids"],
                    "distinct_fresh_worlds": proof.envelope_a.world_instance_id != proof.envelope_b.world_instance_id,
                    "outcome": proof.stability.outcome,
                    "run_plan_id": proof.stability.run_plan_id,
                    "site_count": len(proof.stability.sites),
                    "stable_site_count": sum(row["comparison"] == "stable" for row in proof.stability.sites),
                },
                "row.control-a",
                "row.control-b",
                "row.stability",
            )
        )
        partition = "worldgen.stability.region"
    else:
        raise WorldgenGraphError(f"unsupported stability resolution {resolution_id}")
    return {
        "emit_evidence_links": False,
        "partition_key": partition,
        "properties": [],
        "relations": [],
        "subjects": _ordered(subjects, "subject_key"),
    }


def _placeholder_branch(name: str) -> dict[str, Any]:
    return {
        "emit_evidence_links": False,
        "partition_key": f"worldgen.placeholder.{name.replace('_', '-')}",
        "properties": [],
        "relations": [],
        "subjects": [
            _subject(
                f"placeholder.{name}",
                f"node:placeholder-{name}",
                {"placeholder_for_unselected_closed_branch": name},
                "row.placeholder",
            )
        ],
    }


def _reseed(value: Mapping[str, Any], **changes: Any) -> ValidatedRecord:
    candidate = dict(value)
    candidate.pop("id", None)
    candidate.update(changes)
    return seal_record(candidate)


def _manifest(value: Mapping[str, Any], field: str, domain: str) -> ValidatedRecord:
    candidate = dict(value)
    candidate.pop("id", None)
    candidate[field] = ""
    projection = dict(candidate)
    projection.pop(field)
    candidate[field] = semantic_root_v2(domain, projection)
    return seal_record(candidate)


def _descriptor_for_row(
    template: ValidatedRecord,
    row: ValidatedRecord,
    *,
    admission: bool,
    owner: Mapping[str, str],
) -> tuple[ValidatedRecord, bytes]:
    raw = row.canonical_bytes + b"\n"
    digest = hashlib.sha256(raw).hexdigest()
    key = row.to_dict()["candidate_record_id"] if admission else row.id
    descriptor = _reseed(
        template.to_dict(),
        object_id=f"workbench-blob-v2:sha256:{digest}",
        sha256=digest,
        byte_length=len(raw),
        canonical_item_count=1,
        minimum_record_key=key,
        maximum_record_key=key,
        total_order_authority=dict(owner),
        total_order_policy_id=content_id(
            "policy", {"owner": owner["owner_authority_id"], "policy": "worldgen-evidence-order-v1"}
        ),
    )
    return descriptor, raw


def _effective_source(
    base: MaterializationInput,
    *,
    recipe: ValidatedRecord,
    payload: Mapping[str, Any],
    context_ref_id: str,
    profile_binding: Mapping[str, Any],
) -> MaterializationInput:
    raw = canonical_json_bytes(dict(payload))
    digest = hashlib.sha256(raw).hexdigest()
    payload_descriptor = _reseed(
        base.payload_descriptor.to_dict(),
        object_id=f"workbench-blob-v2:sha256:{digest}",
        sha256=digest,
        byte_length=len(raw),
        described_schema_id=(
            "workbench://schemas/crucible/worldgen-materializer-bundle-v1.schema.json"
        ),
        semantic_role="worldgen-materializer-evidence-payload",
    )
    capture_owner = _authority("crucible-worldgen-capture")
    evidence = _reseed(
        base.evidence.to_dict(),
        authority_owner=capture_owner,
        capture_condition={
            "completion": "complete",
            "coverage": {
                "coverage_object_descriptor_id": None,
                "omitted_at_least": 1,
                "state": "bounded",
            },
            "loss": "none",
            "truncated": False,
        },
        context_ref_id=context_ref_id,
        evidence_kind=WORLDGEN_EVIDENCE_KIND,
        limitations=[
            {
                "code": "bounded-worldgen-selector",
                "detail": "The evidence is complete only inside the sealed W01 selector.",
            },
            {
                "code": "experimental-profile",
                "detail": "This evidence does not claim tested support or semantic admission.",
            },
        ],
        payload_object_descriptor_id=payload_descriptor.id,
        producer_id=content_id(
            "producer", {"producer": "crucible-worldgen-v2-materializer-adapter"}
        ),
        source_bindings=[
            {
                "locator": {"kind": "byte-range", "length": len(raw), "start": 0},
                "object_descriptor_id": payload_descriptor.id,
                "role": "primary",
            }
        ],
        transport_normalizer_id=content_id(
            "transport-normalizer", {"adapter": "worldgen-proof-bundle-v1"}
        ),
    )
    admission = _reseed(
        base.admission.to_dict(),
        candidate_record_id=evidence.id,
        context_ref_id=context_ref_id,
        decision_owner=capture_owner,
        limitations=[],
        policy_ids=[
            content_id(
                "policy", {"policy": "worldgen-proof-evidence-admission-v1"}
            )
        ],
        profile_adapter_bindings=[
            {
                "profile_adapter_id": profile_binding["profile_adapter_id"],
                "profile_authority": profile_binding["profile_authority"],
            }
        ],
        validator_ids=[
            content_id("validator", {"validator": "worldgen-proof-bundle-v1"})
        ],
    )
    evidence_descriptor, evidence_raw = _descriptor_for_row(
        base.evidence_partition_descriptor,
        evidence,
        admission=False,
        owner=capture_owner,
    )
    admission_descriptor, admission_raw = _descriptor_for_row(
        base.admission_partition_descriptor,
        admission,
        admission=True,
        owner=capture_owner,
    )
    revision = base.evidence_set_revision.to_dict()
    evidence_partition = dict(revision["evidence_partitions"][0])
    evidence_partition.update(
        {
            "object_descriptor_id": evidence_descriptor.id,
            "minimum_record_key": evidence.id,
            "maximum_record_key": evidence.id,
            "partition_key": "worldgen",
            "semantic_root": semantic_root_v2(
                "evidence-set-revision/effective-evidence/partition",
                {
                    "partition_key": "worldgen",
                    "record_ids": [evidence.id],
                    "shard_ordinal": evidence_partition["shard_ordinal"],
                },
            ),
        }
    )
    admission_partition = dict(revision["admission_partitions"][0])
    admission_partition.update(
        {
            "object_descriptor_id": admission_descriptor.id,
            "minimum_record_key": evidence.id,
            "maximum_record_key": evidence.id,
            "partition_key": "worldgen",
            "semantic_root": semantic_root_v2(
                "evidence-set-revision/effective-admissions/partition",
                {
                    "partition_key": "worldgen",
                    "record_ids": [admission.id],
                    "shard_ordinal": admission_partition["shard_ordinal"],
                },
            ),
        }
    )

    def summary(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: item[key]
            for key in ("partition_key", "record_count", "semantic_root", "shard_ordinal")
        }

    revision.update(
        {
            "admission_partitions": [admission_partition],
            "admission_selection_policy_id": content_id(
                "policy", {"policy": "worldgen-admission-selection-v1"}
            ),
            "authority_owner": capture_owner,
            "context_ref_id": context_ref_id,
            "effective_admission_root": semantic_root_v2(
                "evidence-set-revision/effective-admissions",
                [summary(admission_partition)],
            ),
            "effective_evidence_root": semantic_root_v2(
                "evidence-set-revision/effective-evidence",
                [summary(evidence_partition)],
            ),
            "evidence_partitions": [evidence_partition],
            "ledger_heads": [
                {
                    "head_record_id": content_id(
                        "ledger-entry", {"admission_record_id": admission.id, "ordinal": 1}
                    ),
                    "ledger_namespace": "worldgen.proof",
                    "ordinal": 1,
                }
            ],
            "scope_selector_id": content_id(
                "selector", {"selector": "worldgen-proof-bounded-window-v1"}
            ),
        }
    )
    revision = _manifest(
        revision,
        "semantic_root",
        "evidence-set-revision/aggregate",
    )
    return MaterializationInput(
        recipe=recipe,
        evidence_set_revision=revision,
        evidence_partition_descriptor=evidence_descriptor,
        evidence_partition_bytes=evidence_raw,
        admission_partition_descriptor=admission_descriptor,
        admission_partition_bytes=admission_raw,
        evidence=evidence,
        admission=admission,
        payload_descriptor=payload_descriptor,
        payload_bytes=raw,
    )


def _adapt_recipe(
    template: ValidatedRecord,
    *,
    category_id: str,
    resolution_id: str,
    profile_binding: Mapping[str, Any],
) -> ValidatedRecord:
    value = template.to_dict()
    value.pop("id", None)
    owner = _authority(_OWNER_LABEL_BY_CATEGORY[category_id])
    implementation_id = content_id(
        "implementation",
        {
            "capability_id": _CAPABILITY_BY_CATEGORY[category_id],
            "category_id": category_id,
            "implementation": "closed-worldgen-materializer-v1",
            "resolution_id": resolution_id,
        },
    )
    source_step = next(
        item
        for item in value["derivation_steps"]
        if item["step_key"] == "synthetic.direct"
    )
    step = parse_canonical_json(canonical_json_bytes(source_step))
    step["implementation_id"] = implementation_id
    step["step_key"] = "worldgen.direct"
    for contract in step["input_contracts"]:
        if contract["input_mode"] == "evidence":
            contract["evidence_kind_ids"] = [WORLDGEN_EVIDENCE_KIND]
            contract["input_contract_key"] = "worldgen.evidence"
        else:
            contract["input_contract_key"] = "worldgen.local-records"
            for source in contract["source_contracts"]:
                source["contract_key"] = "worldgen.main"
                source["record_kinds"] = ["edge", "evidence-link", "node", "property"]
    step["output_contracts"] = [
        {
            "output_contract_key": "worldgen.main",
            "output_record_kinds": ["edge", "evidence-link", "node", "property"],
        }
    ]
    step_body = dict(step)
    step_body.pop("derivation_step_id", None)
    step["derivation_step_id"] = content_id("derivation-step", step_body)
    main_output = next(
        item
        for item in value["output_contracts"]
        if item["output_contract_key"] == "synthetic.main"
    )
    main_output = parse_canonical_json(canonical_json_bytes(main_output))
    main_output.update(
        {
            "category_id": category_id,
            "graph_family_id": GRAPH_FAMILY_ID,
            "output_contract_key": "worldgen.main",
            "resolution_id": resolution_id,
        }
    )
    main_output["subject_contracts"] = [
        {
            **main_output["subject_contracts"][0],
            "node_kind": "worldgen.subject",
            "subject_kind": "worldgen-subject",
        }
    ]
    value.update(
        {
            "accepted_inputs": {
                "evidence_kind_ids": [WORLDGEN_EVIDENCE_KIND],
                "graph_input_contracts": [],
                "scope_compatibility_rule_id": content_id(
                    "policy", {"owner": owner["owner_authority_id"], "policy": "worldgen-exact-context-v1"}
                ),
            },
            "authority_owner": owner,
            "derivation_steps": [step],
            "full_build_conformance_case_ids": [
                content_id(
                    "conformance-case",
                    {"case": "worldgen-clean-build-v1", "category_id": category_id, "resolution_id": resolution_id},
                )
            ],
            "implementation": {
                **value["implementation"],
                "implementation_id": implementation_id,
            },
            "incremental_equivalence_case_ids": [
                content_id(
                    "conformance-case",
                    {"case": "worldgen-incremental-equivalence-v1", "category_id": category_id, "resolution_id": resolution_id},
                )
            ],
            "incremental_publication_enabled": True,
            "ontology_ids": [
                content_id(
                    "ontology", {"ontology": "worldgen-v1", "owner": owner["owner_authority_id"]}
                )
            ],
            "output_contracts": [main_output],
            "profile_adapter_bindings": [
                {
                    "profile_adapter_id": profile_binding["profile_adapter_id"],
                    "profile_authority": profile_binding["profile_authority"],
                }
            ],
            "recipe_name": f"{category_id}.{resolution_id}",
            "recipe_owner": owner,
            "semantic_version": "1.0.0",
            "tool_ids": [
                content_id(
                    "tool", {"tool": "worldgen-closed-materializer-v1", "category_id": category_id}
                )
            ],
        }
    )
    value["policies"] = {
        key: content_id(
            "policy",
            {"owner": owner["owner_authority_id"], "policy": f"worldgen-{key}-v1"},
        )
        for key in value["policies"]
    }
    return seal_record(value)


def _registration(
    recipe: ValidatedRecord,
    *,
    capability_id: str,
    publication: Any,
) -> RecipeImplementationRegistration:
    value = recipe.to_dict()
    step = value["derivation_steps"][0]
    graph = publication.records_by_id[publication.aliases["graph-revision"]].to_dict()
    return RecipeImplementationRegistration(
        seal_recipe_execution_binding(
            recipe,
            derivation_step_id=step["derivation_step_id"],
            output_contract_key="worldgen.main",
            handler_id=BUILTIN_GRAPH_HANDLER_ID,
            capability_id=capability_id,
            subject_identity_schema_id=WORLDGEN_SUBJECT_SCHEMA_ID,
            materializer_component_id=graph["materializer"]["component_id"],
            materializer_implementation_id=graph["materializer"]["implementation_id"],
            custodian_component_id=graph["custodian"]["component_id"],
            custodian_implementation_id=graph["custodian"]["implementation_id"],
            bounds=RecipeExecutionBounds(
                1024 * 1024,
                64 * 1024,
                64 * 1024,
                8,
                30,
            ),
        )
    )


def _source_rows(proof: WorldgenProofBundle) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for artifact in proof.source_artifacts:
        raw = base64.b64decode(artifact["bytes_base64"], validate=True)
        for encoded in raw.splitlines():
            row = parse_canonical_json(encoded)
            result[row["row_id"]] = row
    return result


def _selected_source_artifacts(
    proof: WorldgenProofBundle,
    branches: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    required: set[str] = set()
    for branch in branches.values():
        for collection in ("subjects", "relations", "properties"):
            for row in branch[collection]:
                required.update(row["source_row_ids"])
    available = _source_rows(proof)
    _require(required <= set(available), "one branch cites an unavailable source row")
    return [
        _source_artifact(
            "00-worldgen-selected-source.ndjson",
            [available[row_id] for row_id in sorted(required, key=lambda item: item.encode("utf-8"))],
        )
    ]


def _payload(
    proof: WorldgenProofBundle,
    *,
    selected_branch: str,
    branch: Mapping[str, Any],
) -> dict[str, Any]:
    branches = {
        name: (_placeholder_branch(name) if name != selected_branch else dict(branch))
        for name in (
            "capture_health",
            "generative",
            "occurrence",
            "realized",
            "stability",
        )
    }
    return {
        "branches": branches,
        "source_artifacts": _selected_source_artifacts(proof, branches),
        "value_kind": WORLDGEN_GRAPH_BUNDLE_VALUE_KIND,
    }


@dataclass(frozen=True, slots=True)
class WorldgenGraphMember:
    category_id: str
    resolution_id: str
    recipe: ValidatedRecord
    source: MaterializationInput
    registration: RecipeImplementationRegistration
    result: MaterializationResult
    dependency_manifest: ValidatedRecord
    graph_revision: ValidatedRecord
    input_revision_id: str
    input_revision_bytes: bytes


@dataclass(frozen=True, slots=True)
class WorldgenIncrementalReceipt:
    """Derived W01 member impact and complete clean-equality receipt."""

    receipt_id: str
    previous_graph_set_revision_id: str
    graph_set_revision_id: str
    changed_inputs: tuple[Mapping[str, Any], ...]
    rebuilt_members: tuple[tuple[str, str], ...]
    reused_members: tuple[tuple[str, str], ...]
    removed_members: tuple[tuple[str, str], ...]
    clean_identity_sha256: str

    def body(self) -> dict[str, Any]:
        def coordinate(value: tuple[str, str]) -> dict[str, str]:
            return {"category_id": value[0], "resolution_id": value[1]}

        return {
            "canonicalizer": CANONICALIZER_ID,
            "changed_inputs": [dict(item) for item in self.changed_inputs],
            "clean_identity_sha256": self.clean_identity_sha256,
            "format": "workbench-worldgen-incremental-receipt-v1",
            "graph_set_revision_id": self.graph_set_revision_id,
            "kind": "worldgen-incremental-receipt",
            "previous_graph_set_revision_id": (
                self.previous_graph_set_revision_id
            ),
            "rebuilt_members": [
                coordinate(item) for item in self.rebuilt_members
            ],
            "removed_members": [
                coordinate(item) for item in self.removed_members
            ],
            "reused_members": [
                coordinate(item) for item in self.reused_members
            ],
            "schema_version": 1,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes({**self.body(), "id": self.receipt_id})


@dataclass(frozen=True, slots=True)
class WorldgenGraphBuild:
    """One complete immutable W01 candidate before physical publication."""

    members: tuple[WorldgenGraphMember, ...]
    graph_set_revision: ValidatedRecord
    compatibility_descriptor: ValidatedRecord
    compatibility_bytes: bytes
    index_id: str
    index_bytes: bytes
    build_receipt_id: str
    build_receipt_bytes: bytes
    reused_members: tuple[tuple[str, str], ...]
    retained_records: Mapping[str, bytes]
    retained_blobs: Mapping[str, bytes]
    incremental_receipt: WorldgenIncrementalReceipt | None = None

    def identity(self) -> tuple[Any, ...]:
        return (
            tuple(sorted(self.retained_records.items())),
            tuple(sorted(self.retained_blobs.items())),
            self.graph_set_revision.canonical_bytes,
            self.compatibility_descriptor.canonical_bytes,
            self.compatibility_bytes,
            self.index_bytes,
            self.build_receipt_bytes,
        )

    @property
    def identity_sha256(self) -> str:
        digest = hashlib.sha256()
        for key, value in sorted(self.retained_records.items()):
            digest.update(key.encode("utf-8"))
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        for key, value in sorted(self.retained_blobs.items()):
            digest.update(key.encode("utf-8"))
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.hexdigest()


def _seal_member(
    *,
    result: MaterializationResult,
    recipe: ValidatedRecord,
    source: MaterializationInput,
    profile_support_binding: Mapping[str, Any],
    dependency_template: ValidatedRecord,
    graph_template: ValidatedRecord,
) -> tuple[ValidatedRecord, ValidatedRecord]:
    fields = result.publication_fields()
    dependency_value = dependency_template.to_dict()
    dependency_value.pop("id", None)
    dependency_value.update(fields["dependency"])
    dependency = _manifest(
        dependency_value,
        "semantic_root",
        "dependency-manifest/aggregate",
    )

    graph_value = graph_template.to_dict()
    graph_value.pop("id", None)
    graph_value.update(fields["graph"])
    owner = _authority(_OWNER_LABEL_BY_CATEGORY[result.category_id])
    graph_value.update(
        {
            "authority_owner": owner,
            "category_id": result.category_id,
            "completeness": "bounded",
            "conflict_record_ids": [],
            "context_ref_id": result.context_ref_id,
            "coverage": {
                "coverage_object_descriptor_id": None,
                "omitted_at_least": 1,
                "state": "bounded",
            },
            "dependency_manifest_id": dependency.id,
            "evidence_set_revision_ids": [result.evidence_set_revision_id],
            "evidence_state": "known",
            "frontier_record_ids": [],
            "graph_family_id": result.graph_family_id,
            "input_graph_bindings": [],
            "limitations": [
                {
                    "code": "bounded-worldgen-selector",
                    "detail": "The revision is complete only for the sealed W01 selector and resolution.",
                },
                {
                    "code": "experimental-profile",
                    "detail": "The revision is visible but does not claim stable support or admission.",
                },
            ],
            "ontology_ids": [
                content_id(
                    "ontology",
                    {"ontology": "worldgen-v1", "owner": owner["owner_authority_id"]},
                )
            ],
            "parameter_values_object_descriptor_id": result.parameter_values_object_descriptor_id,
            "parent_graph_revision_ids": [],
            "profile_support_bindings": [dict(profile_support_binding)],
            "purpose_id": WORLDGEN_PURPOSE_ID,
            "recipe_id": recipe.id,
            "recipe_implementation_id": result.implementation_id,
            "recipe_owner": owner,
            "resolution_id": result.resolution_id,
            "semantic_validation_requirement_ids": [
                content_id(
                    "validation-requirement",
                    {
                        "category_id": result.category_id,
                        "requirement": "worldgen-w01-closed-input-v1",
                        "resolution_id": result.resolution_id,
                    },
                )
            ],
            "support_state": "experimental",
            "tool_ids": [
                content_id(
                    "tool",
                    {"category_id": result.category_id, "tool": "worldgen-closed-materializer-v1"},
                )
            ],
        }
    )
    graph = _manifest(
        graph_value,
        "aggregate_semantic_root",
        "graph-revision/aggregate",
    )
    _require(graph.to_dict()["authority_owner"] == owner, "graph owner was changed during sealing")
    _require(
        graph.to_dict()["category_id"] == result.category_id
        and graph.to_dict()["resolution_id"] == result.resolution_id,
        "graph category or resolution was changed during sealing",
    )
    return dependency, graph


def _compatibility_object(
    *,
    template: ValidatedRecord,
    members: Sequence[WorldgenGraphMember],
) -> tuple[ValidatedRecord, bytes, tuple[dict[str, Any], ...]]:
    by_key = {
        (member.category_id, member.resolution_id): member.graph_revision.id
        for member in members
    }
    recipes: list[dict[str, Any]] = []
    for category, chain in (
        (CATEGORY_OCCURRENCE, (RESOLUTION_REGION, RESOLUTION_CHUNK, RESOLUTION_EXACT)),
        (CATEGORY_REALIZED, (RESOLUTION_REGION, RESOLUTION_CHUNK, RESOLUTION_POSITION)),
        (CATEGORY_STABILITY, (RESOLUTION_REGION, RESOLUTION_CHUNK)),
    ):
        for coarse, fine in zip(chain, chain[1:]):
            if (category, coarse) not in by_key or (category, fine) not in by_key:
                continue
            body = {
                "category_id": category,
                "coarse_graph_revision_id": by_key[(category, coarse)],
                "coarse_resolution_id": coarse,
                "fine_graph_revision_id": by_key[(category, fine)],
                "fine_resolution_id": fine,
                "mapping_state": "deterministic-projection",
                "strengthening_allowed": False,
            }
            recipes.append(
                {
                    **body,
                    "recipe_id": content_id("worldgen-resolution-recipe", body),
                }
            )
    recipes.sort(key=canonical_json_bytes)
    value = {
        "format": "workbench-worldgen-compatibility-constraints-v1",
        "graph_family_id": GRAPH_FAMILY_ID,
        "owner_matrix": {
            CATEGORY_CAPTURE_HEALTH: "crucible-worldgen-capture",
            CATEGORY_GENERATIVE: "atlas-worldgen-semantics",
            CATEGORY_OCCURRENCE: "crucible-worldgen-capture",
            CATEGORY_REALIZED: "atlas-worldgen-semantics",
            CATEGORY_STABILITY: "crucible-worldgen-capture",
        },
        "refinement_recipes": recipes,
        "schema_version": 1,
    }
    raw = canonical_json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    descriptor = _reseed(
        template.to_dict(),
        object_id=f"workbench-blob-v2:sha256:{digest}",
        sha256=digest,
        byte_length=len(raw),
        canonical_item_count=1,
        described_record_kind=None,
        described_schema_id=(
            "workbench://schemas/crucible/worldgen-compatibility-constraints-v1.schema.json"
        ),
        maximum_record_key=None,
        media_type="application/json",
        minimum_record_key=None,
        representation="canonical-json-value",
        semantic_role="worldgen-graph-set-compatibility-constraints",
        total_order_authority=None,
        total_order_policy_id=None,
    )
    return descriptor, raw, tuple(recipes)


def _seal_graph_set(
    *,
    members: Sequence[WorldgenGraphMember],
    template: ValidatedRecord,
    context_ref_id: str,
    profile_support_binding: Mapping[str, Any],
    compatibility_descriptor_id: str,
) -> ValidatedRecord:
    composition_owner = _authority("workbench-worldgen-composition")
    compatibility_rule_id = content_id(
        "policy", {"policy": "worldgen-owner-and-context-compatibility-v1"}
    )
    evidence_ids = sorted(
        {member.result.evidence_set_revision_id for member in members},
        key=lambda item: item.encode("utf-8"),
    )
    graph_members: list[dict[str, Any]] = []
    for member in members:
        expected_owner = _authority(_OWNER_LABEL_BY_CATEGORY[member.category_id])
        _require(
            member.graph_revision.to_dict()["authority_owner"] == expected_owner,
            f"owner/category mismatch for {member.category_id}",
        )
        decision_body = {
            "category_id": member.category_id,
            "graph_revision_id": member.graph_revision.id,
            "resolution_id": member.resolution_id,
            "rule_id": compatibility_rule_id,
        }
        graph_members.append(
            {
                "alignment": {
                    "compatibility_constraints_object_descriptor_id": compatibility_descriptor_id,
                    "compatibility_decision_id": content_id(
                        "compatibility-decision", decision_body
                    ),
                    "compatibility_rule_id": compatibility_rule_id,
                    "context_result": "exact",
                    "decision_authority": composition_owner,
                    "evidence_result": "policy-compatible",
                    "member_context_ref_id": member.result.context_ref_id,
                    "member_evidence_set_revision_ids": [
                        member.result.evidence_set_revision_id
                    ],
                    "outcome": "compatible",
                    "target_context_ref_id": context_ref_id,
                    "target_evidence_set_revision_ids": evidence_ids,
                },
                "category_id": member.category_id,
                "graph_revision_id": member.graph_revision.id,
                "resolution_id": member.resolution_id,
            }
        )
    graph_members.sort(key=canonical_json_bytes)
    value = template.to_dict()
    value.pop("id", None)
    value.update(
        {
            "compatibility_constraints_object_descriptor_id": compatibility_descriptor_id,
            "compatibility_rule_authority": composition_owner,
            "compatibility_rule_id": compatibility_rule_id,
            "completeness": "bounded",
            "composition_owner": composition_owner,
            "conflict_record_ids": [],
            "context_ref_id": context_ref_id,
            "coverage": {
                "coverage_object_descriptor_id": None,
                "omitted_at_least": 1,
                "state": "bounded",
            },
            "evidence_set_revision_ids": evidence_ids,
            "graph_set_family_id": GRAPH_SET_FAMILY_ID,
            "join_graphs": [],
            "limitations": [
                {
                    "code": "bounded-worldgen-selector",
                    "detail": "The graph set does not make a whole-world determinism claim.",
                },
                {
                    "code": "experimental-profile",
                    "detail": "The graph set is visible for proving work but is not stable support.",
                },
            ],
            "members": graph_members,
            "profile_support_bindings": [dict(profile_support_binding)],
            "purpose_id": WORLDGEN_PURPOSE_ID,
            "refinement_graphs": [],
            "support_state": "experimental",
        }
    )
    return _manifest(
        value,
        "aggregate_semantic_root",
        "graph-set-revision/aggregate",
    )


def _member_index(member: WorldgenGraphMember) -> list[dict[str, Any]]:
    descriptors = {
        descriptor.id: descriptor.to_dict()
        for descriptor in member.result.object_descriptors
    }
    blobs = {blob.object_id: blob.data for blob in member.result.blobs}
    entries: list[dict[str, Any]] = []
    for record in member.result.graph_records:
        value = record.to_dict()
        body = value["body"]
        entry: dict[str, Any] = {
            "category_id": member.category_id,
            "graph_record_id": record.id,
            "graph_revision_id": member.graph_revision.id,
            "logical_key": value["logical_key"],
            "record_kind": body["record_kind"],
            "resolution_id": member.resolution_id,
        }
        if body["record_kind"] == "node":
            descriptor = descriptors[body["subject_identity_object_descriptor_id"]]
            entry["identity"] = parse_canonical_json(blobs[descriptor["object_id"]])
            entry["subject_id"] = body["subject_id"]
        elif body["record_kind"] == "property":
            descriptor = descriptors[body["value_object_descriptor_id"]]
            entry["property_id"] = body["property_id"]
            entry["value"] = parse_canonical_json(blobs[descriptor["object_id"]])
        elif body["record_kind"] == "edge":
            entry.update(
                {
                    "direction": body["direction"],
                    "predicate_id": body["predicate_id"],
                    "source_subject_id": body["source_subject_id"],
                    "target_subject_id": body["target_subject_id"],
                }
            )
        elif body["record_kind"] == "evidence-link":
            entry.update(
                {
                    "supported_graph_record_id": body["supported_graph_record_id"],
                    "supporting_evidence_record_id": body["supporting_evidence_record_id"],
                }
            )
        entries.append(entry)
    return entries


def _worldgen_member_input_revision(
    proof: WorldgenProofBundle,
    *,
    category_id: str,
    resolution_id: str,
    recipe: ValidatedRecord,
    registration: RecipeImplementationRegistration,
    include_stability: bool,
    eligibility_bound: int,
    capture_health_graph_revision_id: str | None,
) -> tuple[str, bytes]:
    """Seal the exact owner-declared upstream closure for one W01 member."""

    dependencies: list[dict[str, str]]
    parameters: dict[str, Any] = {}
    if category_id == CATEGORY_CAPTURE_HEALTH:
        dependencies = [
            {"role": "capture-audit", "value": proof.capture_a["id"]},
            {
                "role": "control-a-envelope",
                "value": proof.envelope_a.envelope_id,
            },
            {"role": "control-a-terminal", "value": proof.terminal_a["id"]},
            {
                "role": "profile-action-policy-sha256",
                "value": proof.action_policy_sha256,
            },
        ]
    elif category_id == CATEGORY_OCCURRENCE:
        _require(
            type(capture_health_graph_revision_id) is str,
            "occurrence input revision requires capture-health closure",
        )
        dependencies = [
            {
                "role": "capture-health-graph",
                "value": capture_health_graph_revision_id,
            },
            {"role": "control-a-join", "value": proof.join_a.receipt_id},
        ]
    elif category_id == CATEGORY_STABILITY:
        dependencies = [
            {"role": "stability-receipt", "value": proof.stability.receipt_id}
        ]
    elif category_id == CATEGORY_GENERATIVE:
        dependencies = [
            {
                "role": "current-feature-source-sha256",
                "value": proof.current_feature_source_sha256,
            },
            {
                "role": "retained-pattern-sha256",
                "value": proof.stale_pattern_sha256,
            },
        ]
        parameters = {
            "active_stage": "populate.custom",
            "eligibility_bound": eligibility_bound,
        }
    elif category_id == CATEGORY_REALIZED:
        dependencies = [
            {"role": "control-a-join", "value": proof.join_a.receipt_id}
        ]
        if include_stability:
            dependencies.append(
                {"role": "control-b-join", "value": proof.join_b.receipt_id}
            )
        parameters = {"include_stability": include_stability}
    else:  # pragma: no cover - closed table above
        raise WorldgenGraphError(
            f"unknown worldgen member input category {category_id}"
        )
    dependencies.sort(key=canonical_json_bytes)
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "category_id": category_id,
        "context_ref_id": proof.context_ref_id,
        "dependencies": dependencies,
        "execution_binding_id": (
            registration.execution_binding.execution_binding_id
        ),
        "format": "workbench-worldgen-member-input-revision-v1",
        "kind": "worldgen-member-input-revision",
        "parameters": parameters,
        "profile_support_binding": proof.profile_support_binding,
        "recipe_id": recipe.id,
        "resolution_id": resolution_id,
        "schema_version": 1,
    }
    revision_id = content_id("worldgen-member-input-revision", body)
    return revision_id, canonical_json_bytes({**body, "id": revision_id})


def _validated_reused_worldgen_member(
    prior: WorldgenGraphMember,
    *,
    input_revision_id: str,
    input_revision_bytes: bytes,
    recipe: ValidatedRecord,
    registration: RecipeImplementationRegistration,
    profile_support_binding: Mapping[str, Any],
    dependency_template: ValidatedRecord,
    graph_template: ValidatedRecord,
) -> WorldgenGraphMember:
    """Reopen every unaffected shard and cross-bind its publication closure."""

    _require(
        type(prior) is WorldgenGraphMember
        and prior.input_revision_id == input_revision_id
        and prior.input_revision_bytes == input_revision_bytes
        and prior.recipe.canonical_bytes == recipe.canonical_bytes
        and prior.registration.execution_binding.canonical_bytes
        == registration.execution_binding.canonical_bytes,
        "unaffected worldgen member input or registry proof differs",
    )
    result = prior.result
    _require(
        type(result) is MaterializationResult
        and prior.source.recipe.id == recipe.id
        and result.recipe_id == recipe.id
        and result.execution_binding_id
        == registration.execution_binding.execution_binding_id
        and result.category_id == prior.category_id
        and result.resolution_id == prior.resolution_id
        and result.context_ref_id
        == prior.source.evidence.to_dict()["context_ref_id"]
        and result.evidence_set_revision_id
        == prior.source.evidence_set_revision.id
        and result.evidence_record_id == prior.source.evidence.id
        and result.admission_record_id == prior.source.admission.id,
        "unaffected worldgen member result differs from its source proof",
    )
    for record in (
        *result.graph_records,
        *result.object_descriptors,
        prior.dependency_manifest,
        prior.graph_revision,
    ):
        value = parse_canonical_json(record.canonical_bytes)
        _require(
            type(value) is dict
            and value == record.to_dict()
            and _reseed(value).canonical_bytes == record.canonical_bytes,
            "unaffected worldgen member contains a malformed record",
        )
    for blob in result.blobs:
        _require(
            blob.object_id
            == f"workbench-blob-v2:sha256:{hashlib.sha256(blob.data).hexdigest()}",
            "unaffected worldgen member contains a malformed object",
        )
    validated_assembly = assemble_incremental_graph_partitions(
        (),
        impacted_partition_groups=(),
        prior_assembly=result.assembly,
        config=ShardKernelConfig(
            result.record_schema_object_descriptor_id,
            result.assembly.total_order_policy,
            result.assembly.partition_policy,
            registration.records_per_shard,
        ),
        logical_key_port=lambda _record: (_ for _ in ()).throw(
            AssertionError("empty reuse validation called logical-key port")
        ),
        partition_key_port=lambda _record, _key: (_ for _ in ()).throw(
            AssertionError("empty reuse validation called partition port")
        ),
    )
    verify_partition_assembly_equivalence(
        result.assembly, validated_assembly
    )
    _require(
        {
            record_id
            for shard in validated_assembly.shards
            for record_id in shard.record_ids
        }
        == {record.id for record in result.graph_records},
        "unaffected worldgen member shard closure differs",
    )
    validated_result = replace(result, assembly=validated_assembly)
    dependency, graph = _seal_member(
        result=validated_result,
        recipe=recipe,
        source=prior.source,
        profile_support_binding=profile_support_binding,
        dependency_template=dependency_template,
        graph_template=graph_template,
    )
    _require(
        dependency.canonical_bytes == prior.dependency_manifest.canonical_bytes
        and graph.canonical_bytes == prior.graph_revision.canonical_bytes,
        "unaffected worldgen member publication closure differs",
    )
    return WorldgenGraphMember(
        prior.category_id,
        prior.resolution_id,
        recipe,
        prior.source,
        registration,
        validated_result,
        dependency,
        graph,
        input_revision_id,
        input_revision_bytes,
    )


AtlasGenerativePort = Callable[..., Mapping[str, Any]]
AtlasRealizedPort = Callable[..., Mapping[str, Any]]


def _build_worldgen_graph_set_candidate(
    proof: WorldgenProofBundle,
    *,
    atlas_generative_port: AtlasGenerativePort,
    atlas_realized_port: AtlasRealizedPort,
    prior_build: WorldgenGraphBuild | None = None,
    include_stability: bool = True,
    eligibility_bound: int = 10,
) -> WorldgenGraphBuild:
    """Build one clean or impacted-only W01 candidate through closed ports."""

    _require(type(proof) is WorldgenProofBundle, "proof must be a validated WorldgenProofBundle")
    _require(callable(atlas_generative_port) and callable(atlas_realized_port), "Atlas recipe ports are required")
    publication = build_synthetic_publication()
    base_recipe = publication.records_by_id[publication.aliases["materialization-recipe"]]
    base_source = MaterializationInput(
        recipe=base_recipe,
        evidence_set_revision=publication.records_by_id[publication.aliases["evidence-set-revision"]],
        evidence_partition_descriptor=publication.records_by_id[publication.aliases["evidence-shard"]],
        evidence_partition_bytes=publication.blobs_by_id[publication.record("evidence-shard")["object_id"]],
        admission_partition_descriptor=publication.records_by_id[publication.aliases["admission-shard"]],
        admission_partition_bytes=publication.blobs_by_id[publication.record("admission-shard")["object_id"]],
        evidence=publication.records_by_id[publication.aliases["evidence-record"]],
        admission=publication.records_by_id[publication.aliases["admission-record"]],
        payload_descriptor=publication.records_by_id[publication.aliases["evidence-payload"]],
        payload_bytes=publication.blobs_by_id[publication.record("evidence-payload")["object_id"]],
    )
    dependency_template = publication.records_by_id[publication.aliases["dependency-manifest"]]
    graph_template = publication.records_by_id[publication.aliases["graph-revision"]]
    graph_set_template = publication.records_by_id[publication.aliases["graph-set-revision"]]
    compatibility_template = publication.records_by_id[
        graph_set_template.to_dict()["compatibility_constraints_object_descriptor_id"]
    ]
    if prior_build is None:
        prior_by_key: dict[tuple[str, str], WorldgenGraphMember] = {}
    else:
        _require(
            type(prior_build) is WorldgenGraphBuild,
            "prior worldgen build must be an exact graph build",
        )
        prior_by_key = {
            (member.category_id, member.resolution_id): member
            for member in prior_build.members
        }
        _require(
            len(prior_by_key) == len(prior_build.members)
            and set(prior_by_key) <= set(_MEMBER_SPECS),
            "prior worldgen member closure is duplicated or unknown",
        )
    members: list[WorldgenGraphMember] = []
    capture_health_graph_revision_id: str | None = None
    for category_id, resolution_id in _MEMBER_SPECS:
        if category_id == CATEGORY_STABILITY and not include_stability:
            continue
        recipe = _adapt_recipe(
            base_recipe,
            category_id=category_id,
            resolution_id=resolution_id,
            profile_binding=proof.profile_support_binding,
        )
        registration = _registration(
            recipe,
            capability_id=_CAPABILITY_BY_CATEGORY[category_id],
            publication=publication,
        )
        input_revision_id, input_revision_bytes = (
            _worldgen_member_input_revision(
                proof,
                category_id=category_id,
                resolution_id=resolution_id,
                recipe=recipe,
                registration=registration,
                include_stability=include_stability,
                eligibility_bound=eligibility_bound,
                capture_health_graph_revision_id=(
                    capture_health_graph_revision_id
                ),
            )
        )
        prior_member = prior_by_key.get((category_id, resolution_id))
        if (
            prior_member is not None
            and prior_member.input_revision_id == input_revision_id
        ):
            member = _validated_reused_worldgen_member(
                prior_member,
                input_revision_id=input_revision_id,
                input_revision_bytes=input_revision_bytes,
                recipe=recipe,
                registration=registration,
                profile_support_binding=proof.profile_support_binding,
                dependency_template=dependency_template,
                graph_template=graph_template,
            )
            members.append(member)
            if category_id == CATEGORY_CAPTURE_HEALTH:
                capture_health_graph_revision_id = member.graph_revision.id
            continue
        if category_id == CATEGORY_CAPTURE_HEALTH:
            branch = _capture_health_branch(proof)
        elif category_id == CATEGORY_OCCURRENCE:
            branch = _occurrence_branch(
                proof,
                resolution_id,
                capture_health_graph_revision_id=capture_health_graph_revision_id,
            )
        elif category_id == CATEGORY_STABILITY:
            branch = _stability_branch(proof, resolution_id)
        elif category_id == CATEGORY_GENERATIVE:
            branch = dict(
                atlas_generative_port(
                    active_stage="populate.custom",
                    source_sha256=proof.current_feature_source_sha256,
                    stale_pattern=proof.stale_pattern,
                    stale_pattern_sha256=proof.stale_pattern_sha256,
                    resolution_id=resolution_id,
                    eligibility_bound=eligibility_bound,
                )
            )
        elif category_id == CATEGORY_REALIZED:
            branch = dict(
                atlas_realized_port(
                    join_receipts=[
                        proof.join_a.to_dict(),
                        *(
                            [proof.join_b.to_dict()]
                            if include_stability
                            else []
                        ),
                    ],
                    resolution_id=resolution_id,
                )
            )
        else:  # pragma: no cover - closed table above
            raise WorldgenGraphError(f"unknown worldgen category {category_id}")
        owner = _authority(_OWNER_LABEL_BY_CATEGORY[category_id])
        payload = _payload(
            proof,
            selected_branch=_BRANCH_BY_CATEGORY[category_id],
            branch=branch,
        )
        source = _effective_source(
            base_source,
            recipe=recipe,
            payload=payload,
            context_ref_id=proof.context_ref_id,
            profile_binding=proof.profile_support_binding,
        )
        result = execute_materialization(
            source,
            registrations={registration.implementation_id: registration},
            prior_shards=(),
        )
        _require(result.authority_owner.to_dict() == owner, "materializer returned another authority owner")
        _require(
            result.category_id == category_id and result.resolution_id == resolution_id,
            "materializer returned another category or resolution",
        )
        dependency, graph = _seal_member(
            result=result,
            recipe=recipe,
            source=source,
            profile_support_binding=proof.profile_support_binding,
            dependency_template=dependency_template,
            graph_template=graph_template,
        )
        member = WorldgenGraphMember(
            category_id,
            resolution_id,
            recipe,
            source,
            registration,
            result,
            dependency,
            graph,
            input_revision_id,
            input_revision_bytes,
        )
        members.append(member)
        if category_id == CATEGORY_CAPTURE_HEALTH:
            capture_health_graph_revision_id = graph.id
    _require(capture_health_graph_revision_id is not None, "capture-health member was not built")

    compatibility_descriptor, compatibility_bytes, refinement_recipes = _compatibility_object(
        template=compatibility_template,
        members=members,
    )
    graph_set = _seal_graph_set(
        members=members,
        template=graph_set_template,
        context_ref_id=proof.context_ref_id,
        profile_support_binding=proof.profile_support_binding,
        compatibility_descriptor_id=compatibility_descriptor.id,
    )
    entries = [entry for member in members for entry in _member_index(member)]
    entries.sort(key=canonical_json_bytes)
    index_body = {
        "format": "workbench-worldgen-stored-query-index-v1",
        "graph_set_revision_id": graph_set.id,
        "kind": "worldgen-stored-query-index",
        "members": [
            {
                "category_id": member.category_id,
                "graph_revision_id": member.graph_revision.id,
                "resolution_id": member.resolution_id,
            }
            for member in members
        ],
        "raw_archive_dependencies": [],
        "records": entries,
        "refinement_recipes": list(refinement_recipes),
        "schema_version": 1,
    }
    index_id = content_id("worldgen-stored-query-index", index_body)
    index_bytes = canonical_json_bytes({**index_body, "id": index_id})

    reused_members = tuple(
        sorted(
            (
                (member.category_id, member.resolution_id)
                for member in members
                if len(member.result.assembly.reused_coordinates)
                == len(member.result.assembly.shards)
                and bool(member.result.assembly.shards)
            ),
            key=lambda item: canonical_json_bytes(list(item)),
        )
    )
    receipt_body = {
        "canonicalizer": CANONICALIZER_ID,
        "control_execution_envelope_ids": proof.stability.to_dict()["control_execution_envelope_ids"],
        "format": "workbench-worldgen-graph-build-receipt-v1",
        "graph_revision_ids": sorted(
            [member.graph_revision.id for member in members],
            key=lambda item: item.encode("utf-8"),
        ),
        "graph_set_revision_id": graph_set.id,
        "index_id": index_id,
        "kind": "worldgen-graph-build-receipt",
        "member_count": len(members),
        "refinement_recipe_ids": [row["recipe_id"] for row in refinement_recipes],
        "run_plan_id": proof.envelope_a.run_plan_id,
        "schema_version": 1,
        "source_bindings": {
            "action_policy_sha256": proof.action_policy_sha256,
            "current_feature_source_sha256": proof.current_feature_source_sha256,
            "raw_v1_schema_sha256": proof.raw_v1_schema_sha256,
            "retained_v1_pattern_sha256": proof.stale_pattern_sha256,
            "stability_receipt_id": proof.stability.receipt_id,
        },
    }
    build_receipt_id = content_id("worldgen-graph-build-receipt", receipt_body)
    build_receipt_bytes = canonical_json_bytes(
        {**receipt_body, "id": build_receipt_id}
    )

    records: dict[str, bytes] = {
        graph_set.id: graph_set.canonical_bytes,
        compatibility_descriptor.id: compatibility_descriptor.canonical_bytes,
    }
    blobs: dict[str, bytes] = {
        compatibility_descriptor.to_dict()["object_id"]: compatibility_bytes,
    }
    for member in members:
        for record in (
            member.recipe,
            member.source.evidence_set_revision,
            member.source.evidence_partition_descriptor,
            member.source.admission_partition_descriptor,
            member.source.evidence,
            member.source.admission,
            member.source.payload_descriptor,
            member.dependency_manifest,
            member.graph_revision,
            *member.result.graph_records,
            *member.result.object_descriptors,
            *(shard.object_descriptor for shard in member.result.assembly.shards),
        ):
            existing = records.setdefault(record.id, record.canonical_bytes)
            _require(existing == record.canonical_bytes, f"record identity collision at {record.id}")
        source_blobs = (
            (
                member.source.evidence_partition_descriptor.to_dict()["object_id"],
                member.source.evidence_partition_bytes,
            ),
            (
                member.source.admission_partition_descriptor.to_dict()["object_id"],
                member.source.admission_partition_bytes,
            ),
            (
                member.source.payload_descriptor.to_dict()["object_id"],
                member.source.payload_bytes,
            ),
        )
        for object_id, raw in (
            *source_blobs,
            *((blob.object_id, blob.data) for blob in member.result.blobs),
            *((shard.object_descriptor.to_dict()["object_id"], shard.ndjson_bytes) for shard in member.result.assembly.shards),
        ):
            existing = blobs.setdefault(object_id, raw)
            _require(existing == raw, f"blob identity collision at {object_id}")
    records[index_id] = index_bytes
    records[build_receipt_id] = build_receipt_bytes
    return WorldgenGraphBuild(
        tuple(members),
        graph_set,
        compatibility_descriptor,
        compatibility_bytes,
        index_id,
        index_bytes,
        build_receipt_id,
        build_receipt_bytes,
        reused_members,
        MappingProxyType(dict(sorted(records.items()))),
        MappingProxyType(dict(sorted(blobs.items()))),
    )


def build_worldgen_graph_set(
    proof: WorldgenProofBundle,
    *,
    atlas_generative_port: AtlasGenerativePort,
    atlas_realized_port: AtlasRealizedPort,
    prior_build: WorldgenGraphBuild | None = None,
    include_stability: bool = True,
    eligibility_bound: int = 10,
) -> WorldgenGraphBuild:
    """Build W01, deriving impact and requiring complete clean equivalence."""

    candidate = _build_worldgen_graph_set_candidate(
        proof,
        atlas_generative_port=atlas_generative_port,
        atlas_realized_port=atlas_realized_port,
        prior_build=prior_build,
        include_stability=include_stability,
        eligibility_bound=eligibility_bound,
    )
    if prior_build is None:
        return candidate
    clean = _build_worldgen_graph_set_candidate(
        proof,
        atlas_generative_port=atlas_generative_port,
        atlas_realized_port=atlas_realized_port,
        include_stability=include_stability,
        eligibility_bound=eligibility_bound,
    )
    _require(
        candidate.identity() == clean.identity()
        and candidate.identity_sha256 == clean.identity_sha256,
        "incremental worldgen candidate differs from independent clean build",
    )
    current_by_key = {
        (member.category_id, member.resolution_id): member
        for member in candidate.members
    }
    prior_by_key = {
        (member.category_id, member.resolution_id): member
        for member in prior_build.members
    }
    changed_inputs: list[dict[str, Any]] = []
    for key in sorted(
        set(current_by_key) | set(prior_by_key),
        key=lambda item: canonical_json_bytes(list(item)),
    ):
        prior_member = prior_by_key.get(key)
        current_member = current_by_key.get(key)
        old_id = (
            None if prior_member is None else prior_member.input_revision_id
        )
        new_id = (
            None if current_member is None else current_member.input_revision_id
        )
        if old_id == new_id:
            continue
        changed_inputs.append(
            {
                "category_id": key[0],
                "current_input_revision_id": new_id,
                "previous_input_revision_id": old_id,
                "reason": (
                    "member-added"
                    if old_id is None
                    else "member-removed"
                    if new_id is None
                    else "declared-input-revision-changed"
                ),
                "resolution_id": key[1],
            }
        )
    reused = candidate.reused_members
    rebuilt = tuple(
        key
        for key in sorted(
            current_by_key,
            key=lambda item: canonical_json_bytes(list(item)),
        )
        if key not in set(reused)
    )
    removed = tuple(
        key
        for key in sorted(
            set(prior_by_key) - set(current_by_key),
            key=lambda item: canonical_json_bytes(list(item)),
        )
    )
    receipt_values = {
        "previous_graph_set_revision_id": prior_build.graph_set_revision.id,
        "graph_set_revision_id": candidate.graph_set_revision.id,
        "changed_inputs": tuple(changed_inputs),
        "rebuilt_members": rebuilt,
        "reused_members": reused,
        "removed_members": removed,
        "clean_identity_sha256": clean.identity_sha256,
    }
    provisional = WorldgenIncrementalReceipt(receipt_id="", **receipt_values)
    receipt_id = content_id(
        "worldgen-incremental-receipt", provisional.body()
    )
    receipt = WorldgenIncrementalReceipt(
        receipt_id=receipt_id, **receipt_values
    )
    return replace(candidate, incremental_receipt=receipt)


@dataclass(frozen=True, slots=True)
class WorldgenPublicationReceipt:
    graph_set_revision_id: str
    manifest_id: str
    marker_id: str
    prior_graph_set_revision_id: str | None
    canonical_bytes: bytes


class WorldgenGraphStore:
    """Small marker-last content-addressed store for the bounded W01 slice."""

    def __init__(self, root: Path):
        if not isinstance(root, Path) or not root.is_absolute():
            raise WorldgenGraphError("worldgen graph store requires an absolute Path")
        self.root = root
        self.records = root / "objects" / "records"
        self.blobs = root / "objects" / "blobs"
        self.manifests = root / "manifests"
        self.references = root / "references"
        for directory in (self.records, self.blobs, self.manifests, self.references):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _record_path(self, record_id: str) -> Path:
        return self.records / f"{self._key(record_id)}.json"

    def _blob_path(self, object_id: str) -> Path:
        return self.blobs / f"{self._key(object_id)}.bin"

    def _manifest_path(self, graph_set_revision_id: str) -> Path:
        return self.manifests / f"{self._key(graph_set_revision_id)}.json"

    @property
    def current_path(self) -> Path:
        return self.references / "current.json"

    @staticmethod
    def _write_immutable(path: Path, raw: bytes) -> None:
        if path.exists():
            _require(path.read_bytes() == raw, f"immutable object differs at {path}")
            return
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                _require(path.read_bytes() == raw, f"immutable object raced with different bytes at {path}")
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace(path: Path, raw: bytes) -> None:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _current(self) -> dict[str, Any] | None:
        if not self.current_path.exists():
            return None
        try:
            value = parse_canonical_json(self.current_path.read_bytes())
        except Exception as exc:
            raise WorldgenGraphError("current worldgen reference is corrupt") from exc
        _require(
            type(value) is dict
            and value.get("format") == "workbench-worldgen-reference-marker-v1"
            and value.get("id")
            == content_id(
                "worldgen-reference-marker",
                {key: item for key, item in value.items() if key != "id"},
            ),
            "current worldgen reference identity differs",
        )
        return value

    def publish(
        self,
        build: WorldgenGraphBuild,
        *,
        failpoint: str | None = None,
    ) -> WorldgenPublicationReceipt:
        """Write all immutable data, then atomically move one current marker."""

        _require(type(build) is WorldgenGraphBuild, "publish requires a WorldgenGraphBuild")
        allowed_failpoints = {None, "after-objects", "after-manifest", "before-marker"}
        _require(failpoint in allowed_failpoints, "unknown worldgen publication failpoint")
        prior = self._current()
        prior_graph_set_id = None if prior is None else prior["graph_set_revision_id"]
        for record_id, raw in build.retained_records.items():
            self._write_immutable(self._record_path(record_id), raw)
        for object_id, raw in build.retained_blobs.items():
            self._write_immutable(self._blob_path(object_id), raw)
        if failpoint == "after-objects":
            raise WorldgenGraphError("fault injection after immutable objects")

        manifest_body = {
            "blob_ids": sorted(build.retained_blobs, key=lambda item: item.encode("utf-8")),
            "build_receipt_id": build.build_receipt_id,
            "format": "workbench-worldgen-store-manifest-v1",
            "graph_set_revision_id": build.graph_set_revision.id,
            "index_id": build.index_id,
            "kind": "worldgen-store-manifest",
            "record_ids": sorted(build.retained_records, key=lambda item: item.encode("utf-8")),
            "schema_version": 1,
        }
        manifest_id = content_id("worldgen-store-manifest", manifest_body)
        manifest_bytes = canonical_json_bytes({**manifest_body, "id": manifest_id})
        self._write_immutable(
            self._manifest_path(build.graph_set_revision.id),
            manifest_bytes,
        )
        if failpoint == "after-manifest":
            raise WorldgenGraphError("fault injection after worldgen manifest")

        if prior is not None and prior["graph_set_revision_id"] == build.graph_set_revision.id:
            _require(
                prior["manifest_id"] == manifest_id,
                "idempotent worldgen publication found another manifest",
            )
            receipt_body = {
                "format": "workbench-worldgen-publication-receipt-v1",
                "graph_set_revision_id": build.graph_set_revision.id,
                "kind": "worldgen-publication-receipt",
                "manifest_id": manifest_id,
                "marker_id": prior["id"],
                "prior_graph_set_revision_id": prior["prior_graph_set_revision_id"],
                "schema_version": 1,
            }
            receipt_id = content_id("worldgen-publication-receipt", receipt_body)
            receipt_bytes = canonical_json_bytes({**receipt_body, "id": receipt_id})
            self._write_immutable(self._record_path(receipt_id), receipt_bytes)
            return WorldgenPublicationReceipt(
                build.graph_set_revision.id,
                manifest_id,
                prior["id"],
                prior["prior_graph_set_revision_id"],
                receipt_bytes,
            )

        marker_body = {
            "format": "workbench-worldgen-reference-marker-v1",
            "generation": 0 if prior is None else prior["generation"] + 1,
            "graph_set_revision_id": build.graph_set_revision.id,
            "kind": "worldgen-reference-marker",
            "manifest_id": manifest_id,
            "prior_graph_set_revision_id": prior_graph_set_id,
            "schema_version": 1,
        }
        marker_id = content_id("worldgen-reference-marker", marker_body)
        marker_bytes = canonical_json_bytes({**marker_body, "id": marker_id})
        if failpoint == "before-marker":
            raise WorldgenGraphError("fault injection before worldgen marker")
        self._replace(self.current_path, marker_bytes)
        receipt_body = {
            "format": "workbench-worldgen-publication-receipt-v1",
            "graph_set_revision_id": build.graph_set_revision.id,
            "kind": "worldgen-publication-receipt",
            "manifest_id": manifest_id,
            "marker_id": marker_id,
            "prior_graph_set_revision_id": prior_graph_set_id,
            "schema_version": 1,
        }
        receipt_id = content_id("worldgen-publication-receipt", receipt_body)
        receipt_bytes = canonical_json_bytes({**receipt_body, "id": receipt_id})
        self._write_immutable(self._record_path(receipt_id), receipt_bytes)
        return WorldgenPublicationReceipt(
            build.graph_set_revision.id,
            manifest_id,
            marker_id,
            prior_graph_set_id,
            receipt_bytes,
        )

    def resolve_current(self) -> dict[str, Any]:
        marker = self._current()
        _require(marker is not None, "worldgen graph store has no current revision")
        manifest_path = self._manifest_path(marker["graph_set_revision_id"])
        _require(manifest_path.is_file(), "current worldgen manifest is absent")
        manifest = parse_canonical_json(manifest_path.read_bytes())
        body = dict(manifest)
        supplied = body.pop("id", None)
        _require(
            supplied == marker["manifest_id"]
            and supplied == content_id("worldgen-store-manifest", body),
            "current worldgen manifest identity differs",
        )
        for record_id in manifest["record_ids"]:
            path = self._record_path(record_id)
            _require(path.is_file(), f"current worldgen record is absent: {record_id}")
            raw = path.read_bytes()
            try:
                value = parse_canonical_json(raw)
            except Exception as exc:
                raise WorldgenGraphError(
                    f"current worldgen record is not canonical: {record_id}"
                ) from exc
            _require(
                type(value) is dict
                and value.get("id") == record_id
                and type(value.get("kind")) is str,
                f"current worldgen record identity differs: {record_id}",
            )
            record_body = dict(value)
            record_body.pop("id")
            _require(
                content_id(value["kind"], record_body) == record_id,
                f"current worldgen record content ID differs: {record_id}",
            )
        for object_id in manifest["blob_ids"]:
            path = self._blob_path(object_id)
            _require(path.is_file(), f"current worldgen blob is absent: {object_id}")
            prefix = "workbench-blob-v2:sha256:"
            _require(
                object_id.startswith(prefix)
                and hashlib.sha256(path.read_bytes()).hexdigest()
                == object_id[len(prefix) :],
                f"current worldgen blob digest differs: {object_id}",
            )
        return {"manifest": manifest, "marker": marker}

    def read_record(self, record_id: str) -> bytes:
        path = self._record_path(record_id)
        _require(path.is_file(), f"worldgen record is absent: {record_id}")
        return path.read_bytes()


@dataclass(frozen=True, slots=True)
class WorldgenQueryResult:
    query_id: str
    graph_set_revision_id: str
    result_count: int
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return parse_canonical_json(self.canonical_bytes)


class WorldgenStoredQueryService:
    """Revision-pinned bounded queries over the stored derived index only."""

    def __init__(
        self,
        store: WorldgenGraphStore,
        *,
        graph_set_revision_id: str,
        maximum_results: int = 64,
    ):
        _require(type(store) is WorldgenGraphStore, "query service requires a WorldgenGraphStore")
        _require(type(maximum_results) is int and 1 <= maximum_results <= 256, "query result bound is invalid")
        current = store.resolve_current()
        _require(
            current["manifest"]["graph_set_revision_id"] == graph_set_revision_id,
            "query pin is not the current exact graph set",
        )
        index_id = current["manifest"]["index_id"]
        index = parse_canonical_json(store.read_record(index_id))
        body = dict(index)
        supplied = body.pop("id", None)
        _require(
            supplied == index_id
            and supplied == content_id("worldgen-stored-query-index", body)
            and index["graph_set_revision_id"] == graph_set_revision_id
            and index["raw_archive_dependencies"] == [],
            "stored worldgen index identity or closure differs",
        )
        self._store = store
        self._graph_set_revision_id = graph_set_revision_id
        self._maximum_results = maximum_results
        self._index = index
        self.raw_archive_open_count = 0

    def query(self, request: Mapping[str, Any]) -> WorldgenQueryResult:
        _require(type(request) is dict, "query request must be an object")
        query_kind = request.get("query")
        _require(
            query_kind
            in {
                "capture-health",
                "drilldown",
                "known-absence",
                "site",
                "stale-pattern-conflicts",
            },
            "query kind is unavailable",
        )
        records = self._index["records"]
        if query_kind == "capture-health":
            rows = [
                row
                for row in records
                if row["category_id"] == CATEGORY_CAPTURE_HEALTH
                and row.get("logical_key") == "node:capture-health"
            ]
        elif query_kind == "drilldown":
            category_id = request.get("category_id")
            rows = [
                row
                for row in self._index["refinement_recipes"]
                if row["category_id"] == category_id
            ]
        elif query_kind == "stale-pattern-conflicts":
            rows = [
                row
                for row in records
                if row["category_id"] == CATEGORY_GENERATIVE
                and (
                    row.get("identity", {}).get("admission_state")
                    == "blocked-by-conflict"
                    or row.get("property_id") == "worldgen.admission-state"
                )
            ]
        elif query_kind == "known-absence":
            rows = [
                row
                for row in records
                if (
                    row["category_id"] == CATEGORY_OCCURRENCE
                    and row["resolution_id"] == RESOLUTION_EXACT
                    and row.get("identity", {}).get("site_state") == "known-absent"
                )
                or (
                    row["category_id"] == CATEGORY_REALIZED
                    and row["resolution_id"] == RESOLUTION_POSITION
                    and row.get("identity", {}).get("join_state")
                    == "known-absent-by-closed-decision"
                )
            ]
        else:
            category_id = request.get("category_id")
            resolution_id = request.get("resolution_id")
            chunk = request.get("chunk")
            _require(
                type(category_id) is str
                and type(resolution_id) is str
                and type(chunk) is dict
                and set(chunk) == {"x", "z"}
                and type(chunk["x"]) is int
                and type(chunk["z"]) is int,
                "site query scope is incomplete",
            )
            rows = [
                row
                for row in records
                if row["category_id"] == category_id
                and row["resolution_id"] == resolution_id
                and row.get("identity", {}).get("chunk") == chunk
            ]
        rows = sorted(rows, key=canonical_json_bytes)
        truncated = len(rows) > self._maximum_results
        selected = rows[: self._maximum_results]
        result_body = {
            "format": "workbench-worldgen-query-result-v1",
            "graph_set_revision_id": self._graph_set_revision_id,
            "kind": "worldgen-query-result",
            "query": dict(request),
            "raw_archive_open_count": self.raw_archive_open_count,
            "results": selected,
            "schema_version": 1,
            "truncated": truncated,
        }
        query_id = content_id("worldgen-query-result", result_body)
        raw = canonical_json_bytes({**result_body, "id": query_id})
        return WorldgenQueryResult(query_id, self._graph_set_revision_id, len(selected), raw)


def benchmark_worldgen_queries(
    service: WorldgenStoredQueryService,
    requests: Sequence[Mapping[str, Any]],
    *,
    repetitions: int = 20,
) -> dict[str, Any]:
    """Measure the declared warm W01 query suite without raw capture access."""

    try:
        import resource
    except ImportError as error:
        raise WorldgenGraphError("peak RSS benchmarking is unavailable on this host") from error

    _require(type(repetitions) is int and repetitions >= 20, "query benchmark requires at least 20 repetitions")
    _require(bool(requests), "query benchmark requires requests")
    durations_ns: list[int] = []
    result_ids: list[str] = []
    for _ in range(repetitions):
        start = time.monotonic_ns()
        for request in requests:
            result_ids.append(service.query(request).query_id)
        durations_ns.append(time.monotonic_ns() - start)
    peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    maximum_repetition_nanoseconds = max(durations_ns)
    _require(
        maximum_repetition_nanoseconds < 5_000_000_000,
        "warm worldgen query repetition exceeded five seconds",
    )
    _require(
        peak_rss_kib < 4 * 1024 * 1024,
        "worldgen query process exceeded four GiB peak RSS",
    )
    _require(
        service.raw_archive_open_count == 0,
        "worldgen query service opened a raw archive",
    )
    receipt_body = {
        "format": "workbench-worldgen-query-benchmark-v1",
        "graph_set_revision_id": service._graph_set_revision_id,
        "kind": "worldgen-query-benchmark",
        "maximum_repetition_nanoseconds": maximum_repetition_nanoseconds,
        "peak_rss_kib": peak_rss_kib,
        "raw_archive_open_count": service.raw_archive_open_count,
        "repetitions": repetitions,
        "request_count_per_repetition": len(requests),
        "result_root": semantic_root_v2("worldgen-query-benchmark/results", result_ids),
        "schema_version": 1,
    }
    receipt_id = content_id("worldgen-query-benchmark", receipt_body)
    return {**receipt_body, "id": receipt_id}


def evaluate_worldgen_action(
    *,
    policy: bytes | None,
    action: str,
    envelope: WorldgenExecutionEnvelope,
    guardrails: Sequence[str] = (),
) -> dict[str, Any]:
    """Evaluate one exact profile-owned action rule without inventing support."""

    envelope = load_execution_envelope(envelope.canonical_bytes)
    if policy is None:
        disposition = "unavailable"
        reason = "missing-profile-owned-action-policy"
        policy_sha256 = None
    else:
        _require(type(policy) is bytes and bool(policy), "action policy must be exact non-empty bytes")
        try:
            policy_value = json.loads(policy.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WorldgenGraphError("action policy bytes are invalid JSON") from exc
        _require(type(policy_value) is dict, "action policy must be an object")
        policy_sha256 = hashlib.sha256(policy).hexdigest()
        policy_registry = next(
            (
                row
                for row in envelope.to_dict()["identity_registry"]
                if row.get("kind") == "policy"
                and row.get("semantic_key")
                == "supersymmetry-worldgen-action-policy-draft-v1"
            ),
            None,
        )
        _require(
            policy_registry is not None
            and policy_registry.get("declared_policy_id")
            == f"supersymmetry-worldgen-action-policy:sha256:{policy_sha256}",
            "action policy bytes are not the exact policy bound by the envelope",
        )
        policy_binding = next(
            (
                row
                for row in envelope.to_dict()["input_binding"]["policy_bindings"]
                if row.get("input_key") == "worldgen.action-policy"
            ),
            None,
        )
        _require(
            policy_binding is not None
            and policy_binding.get("policy_id") == policy_registry.get("id"),
            "InputBinding does not bind the exact action policy record",
        )
        run_plan = envelope.to_dict()["run_plan"]
        scope = policy_value.get("scope", {})
        _require(
            scope.get("world_type") == run_plan["world_type"]
            and scope.get("generator_id") == run_plan["generator_id"]
            and scope.get("physical_side") == "dedicated-server",
            "action policy scope differs from the execution envelope",
        )
        rule = next(
            (row for row in policy_value.get("rules", []) if row.get("action") == action),
            None,
        )
        if rule is None:
            disposition = "unavailable"
            reason = "action-not-declared-by-profile"
        elif rule["disposition"] == "allow-with-guardrails":
            required = set(rule.get("requires", []))
            supplied = set(guardrails)
            disposition = "allow" if required <= supplied else "unavailable"
            reason = (
                "exact-profile-rule-and-guardrails"
                if disposition == "allow"
                else "missing-required-guardrails"
            )
        else:
            disposition = rule["disposition"]
            reason = "exact-profile-rule"
    body = {
        "action": action,
        "context_ref_id": envelope.context_ref_id,
        "disposition": disposition,
        "execution_envelope_id": envelope.envelope_id,
        "format": "workbench-worldgen-action-gate-receipt-v1",
        "guardrails": sorted(set(guardrails), key=lambda item: item.encode("utf-8")),
        "input_binding_id": envelope.input_binding_id,
        "kind": "worldgen-action-gate-receipt",
        "policy_sha256": policy_sha256,
        "reason": reason,
        "schema_version": 1,
    }
    return {**body, "id": content_id("worldgen-action-gate-receipt", body)}


def evaluate_synthetic_tested_support_gate(
    *,
    support_decision_id: str,
    support_state: str,
    action: str = "synthetic-tested-only-action",
) -> dict[str, Any]:
    """Exercise the generic positive tested-support path independently of W01."""

    _require(
        type(support_decision_id) is str
        and support_decision_id.startswith("support-decision:sha256:")
        and len(support_decision_id) == len("support-decision:sha256:") + 64,
        "synthetic support decision identity is invalid",
    )
    _require(type(action) is str and bool(action), "synthetic action is invalid")
    disposition = "allow" if support_state == "tested-supported" else "unavailable"
    body = {
        "action": action,
        "disposition": disposition,
        "format": "workbench-synthetic-tested-support-gate-receipt-v1",
        "kind": "synthetic-tested-support-gate-receipt",
        "reason": (
            "exact-tested-support-decision"
            if disposition == "allow"
            else "tested-support-decision-required"
        ),
        "schema_version": 1,
        "support_decision_id": support_decision_id,
        "support_state": support_state,
    }
    return {
        **body,
        "id": content_id("synthetic-tested-support-gate-receipt", body),
    }
