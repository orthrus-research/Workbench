from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_strata_observation.observation import (  # noqa: E402
    CANONICALIZATION_ID as STRATA_CANONICALIZATION_ID,
    RECEIPT_FORMAT as STRATA_RECEIPT_FORMAT,
    RECEIPT_PREFIX as STRATA_RECEIPT_PREFIX,
    _BOUNDARIES as STRATA_BOUNDARIES,
    canonical_json_bytes as strata_json_bytes,
)
from workbench_api.canonical import canonical_json_bytes
from workbench_crucible_worldgen import (  # noqa: E402
    CATEGORY_CAPTURE_HEALTH,
    CATEGORY_GENERATIVE,
    CATEGORY_OCCURRENCE,
    CATEGORY_REALIZED,
    CATEGORY_STABILITY,
    RESOLUTION_CHUNK,
    RESOLUTION_EXACT,
    RESOLUTION_OPERATIONAL,
    RESOLUTION_POSITION,
    RESOLUTION_REGION,
    WorldgenCaptureError,
    WorldgenGraphError,
    WorldgenGraphStore,
    WorldgenIdentityError,
    WorldgenStoredQueryService,
    audit_population_capture,
    benchmark_worldgen_queries,
    build_worldgen_graph_set,
    compare_fresh_controls,
    evaluate_synthetic_tested_support_gate,
    evaluate_worldgen_action,
    extract_causal_trace,
    join_same_run,
    load_execution_envelope,
    load_same_run_join_receipt,
    load_stability_receipt,
    load_worldgen_proof_bundle,
    seal_execution_envelope,
    seal_execution_terminal,
)
from workbench_crucible_worldgen.capture import (  # noqa: E402
    CAUSAL_FORMAT,
    CAUSAL_PREFIX,
    _JavaRandom,
    _expected_stage_seed,
    _state_digest,
)


RAW_SCHEMA = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
    "worldgen-observatory-fixture/src/main/resources/"
    "workbench-worldgen-observatory-raw-v1.schema.json"
)
PROBE_PLAN = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
    "worldgen-observatory-probe-plan-v1.json"
)
CAUSAL_SCHEMA = ROOT / "modules/crucible/schemas/worldgen-causal-trace-v2.schema.json"
ACTION_POLICY = (
    ROOT
    / "profiles/packs/supersymmetry/worldgen/"
    "supersymmetry-worldgen-action-policy-draft-v1.json"
)
STALE_PATTERN = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
    "worldgen-prototype-pattern-v1.json"
)
CURRENT_FEATURE = (
    ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
    "worldgen-prototype-fixture/src/main/java/dev/workbench/"
    "worldgenprototype/world/PrototypeFeature.java"
)
REQUIRED_HOOKS = {
    "cleanroom-worldgen:chunk.populate_neighbors",
    "cleanroom-worldgen:chunk.populate_owned",
    "cleanroom-worldgen:chunk.generator_populate_call",
    "cleanroom-worldgen:event_bus.post",
    "cleanroom-worldgen:event_bus.listener_invoke",
    "cleanroom-worldgen:write.world_api",
    "cleanroom-worldgen:write.chunk_storage",
}
NONZERO = "1" * 64
SEED = 8675309
REGION = (32, 32, 4, 4)
GENERATOR = "dev.workbench.worldgenprototype.world.PrototypeChunkGenerator"


def atlas_generative_test_port(
    *,
    active_stage: str,
    source_sha256: str,
    stale_pattern: Mapping[str, Any],
    stale_pattern_sha256: str,
    resolution_id: str,
    eligibility_bound: int,
) -> dict[str, Any]:
    """Supply the minimum Atlas-owned shape needed by Crucible conformance."""

    subject_key = "generative.synthetic-conflict"
    source_rows = ["row.current-source", "row.route", "row.stale-pattern"]
    return {
        "emit_evidence_links": True,
        "partition_key": "worldgen.generative.synthetic-operational",
        "properties": [
            {
                "logical_key": "property:synthetic-generative-admission",
                "property_key": "worldgen.admission-state",
                "property_state": "conflicting",
                "source_row_ids": source_rows,
                "subject_key": subject_key,
                "value": {
                    "active_stage_conflict": True,
                    "source_sha256_conflict": True,
                },
            }
        ],
        "relations": [],
        "subjects": [
            {
                "identity": {
                    "active_stage": active_stage,
                    "admission_state": "blocked-by-conflict",
                    "eligibility_bound": eligibility_bound,
                    "resolution_id": resolution_id,
                    "source_sha256": source_sha256,
                    "stale_pattern_sha256": stale_pattern_sha256,
                    "stale_stage": stale_pattern.get("active_stage"),
                },
                "logical_key": "node:synthetic-generative-conflict",
                "source_row_ids": source_rows,
                "subject_key": subject_key,
            }
        ],
    }


def atlas_realized_test_port(
    *,
    join_receipts: Sequence[Mapping[str, Any]],
    resolution_id: str,
) -> dict[str, Any]:
    """Supply a bounded realized owner-port result without production Atlas code."""

    partition_by_resolution = {
        RESOLUTION_POSITION: "worldgen.realized.synthetic-position",
        RESOLUTION_CHUNK: "worldgen.realized.synthetic-chunk",
        RESOLUTION_REGION: "worldgen.realized.synthetic-region",
    }
    source_rows = [
        "row.join-a",
        *(["row.join-b"] if len(join_receipts) == 2 else []),
    ]
    identity: dict[str, Any] = {
        "control_count": len(join_receipts),
        "execution_envelope_ids": [
            receipt["execution_envelope_id"] for receipt in join_receipts
        ],
        "resolution_id": resolution_id,
        "world_epoch_ids": [receipt["world_epoch_id"] for receipt in join_receipts],
    }
    if resolution_id == RESOLUTION_POSITION:
        identity["join_state"] = "known-absent-by-closed-decision"
    return {
        "emit_evidence_links": False,
        "partition_key": partition_by_resolution[resolution_id],
        "properties": [],
        "relations": [],
        "subjects": [
            {
                "identity": identity,
                "logical_key": f"node:synthetic-realized-{resolution_id}",
                "source_row_ids": source_rows,
                "subject_key": f"realized.synthetic.{resolution_id}",
            }
        ],
    }


def artifact_rows() -> list[dict[str, object]]:
    rows = []
    for role in ("prototype", "observatory", "strata", "policy"):
        rows.append(
            {
                "role": role,
                "sha256": hashlib.sha256(role.encode()).hexdigest(),
                "size_bytes": len(role),
            }
        )
    return rows


def envelope(
    nonce: str,
    *,
    action_policy_sha256: str = "2" * 64,
    profile_id: str = "workbench-pack:supersymmetry:worldgen-iteration-v2",
):
    return seal_execution_envelope(
        workspace_key="worldgen-v2-test",
        profile_id=profile_id,
        platform_profile_id="workbench-platform:cleanroom:0.6.8-alpha",
        execution_nonce=nonce,
        seed=SEED,
        dimension=0,
        region=REGION,
        world_type="wb_proto",
        generator_id=GENERATOR,
        max_tick_time_ms=-1,
        scan_timeout_seconds=600,
        artifacts=artifact_rows(),
        action_policy_id=(
            "supersymmetry-worldgen-action-policy:sha256:"
            + action_policy_sha256
        ),
    )


def state(name: str) -> dict[str, object]:
    return {"metadata": 0, "registry_name": name}


def causal_rows(execution_envelope) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    ordinal = 0
    min_x, min_z, width, height = REGION
    for chunk_x in range(min_x, min_x + width):
        for chunk_z in range(min_z, min_z + height):
            records.append(
                {
                    "chunk_x": chunk_x,
                    "chunk_z": chunk_z,
                    "decision": "allowed",
                    "dimension": 0,
                    "execution_envelope_id": execution_envelope.envelope_id,
                    "format": CAUSAL_FORMAT,
                    "occurrence_ordinal": ordinal,
                    "record_type": "gate-decision",
                    "rule_id": "forge.populate.custom",
                    "schema_version": 2,
                    "seed": SEED,
                    "stage_id": "populate.custom",
                }
            )
            ordinal += 1
            stage_seed = _expected_stage_seed(SEED, chunk_x, chunk_z)
            random = _JavaRandom(stage_seed)
            eligibility = random.next_int(10)
            common = {
                "chunk_x": chunk_x,
                "chunk_z": chunk_z,
                "dimension": 0,
                "eligibility_accepted": eligibility == 0,
                "eligibility_bound": 10,
                "eligibility_draw": eligibility,
                "execution_envelope_id": execution_envelope.envelope_id,
                "format": CAUSAL_FORMAT,
                "occurrence_ordinal": ordinal,
                "record_type": "feature-decision",
                "rng_algorithm": "java.util.Random.v1",
                "rule_id": "world-studio.boulder.v1",
                "schema_version": 2,
                "seed": SEED,
                "seed_derivation_id": "world-studio.stage-random.v1",
                "stage_id": "populate.custom",
                "stage_seed": stage_seed,
            }
            if eligibility != 0:
                records.append(
                    {
                        **common,
                        "base_position": None,
                        "optional_top_draw": None,
                        "outcome": "rejected-by-eligibility-draw",
                        "writes": [],
                        "x_draw": None,
                        "z_draw": None,
                    }
                )
            else:
                x_draw = random.next_int(8)
                z_draw = random.next_int(8)
                optional = random.next_boolean()
                base = {
                    "x": chunk_x * 16 + 4 + x_draw,
                    "y": 70,
                    "z": chunk_z * 16 + 4 + z_draw,
                }
                air = state("minecraft:air")
                mossy = state("minecraft:mossy_cobblestone")
                cobble = state("minecraft:cobblestone")
                records.append(
                    {
                        **common,
                        "base_position": base,
                        "optional_top_draw": optional,
                        "outcome": "placed",
                        "writes": [
                            {
                                "after_state": mossy,
                                "before_state": air,
                                "invoked": True,
                                "outcome": "written",
                                "position": base,
                                "requested_state": mossy,
                                "result": True,
                                "role": "mandatory-base",
                            },
                            {
                                "after_state": cobble,
                                "before_state": air,
                                "invoked": True,
                                "outcome": "written",
                                "position": {**base, "x": base["x"] + 1},
                                "requested_state": cobble,
                                "result": True,
                                "role": "mandatory-east",
                            },
                            {
                                "after_state": cobble if optional else air,
                                "before_state": air,
                                "invoked": optional,
                                "outcome": (
                                    "written" if optional else "rejected-by-optional-draw"
                                ),
                                "position": {**base, "y": base["y"] + 1},
                                "requested_state": cobble,
                                "result": optional,
                                "role": "optional-top",
                            },
                        ],
                        "x_draw": x_draw,
                        "z_draw": z_draw,
                    }
                )
            ordinal += 1
    return records


def actor() -> dict[str, object]:
    return {
        "binding": "workbench",
        "class_name": "dev.workbench.worldgenobservatory.probe.ProbeRuntime",
        "code_source_sha256": NONZERO,
        "mapping_namespace": "java_source",
        "method_descriptor": "()V",
        "method_name": "test",
        "mod_id": "workbench_worldgen_observatory",
        "transformed_class_sha256": None,
    }


def raw_row(
    sequence: int,
    execution_envelope,
    record_type: str,
    payload: dict[str, object],
    *,
    scope: dict[str, int | None] | None = None,
    causality: dict[str, str | None] | None = None,
    outcome: str = "observed",
) -> dict[str, object]:
    return {
        "actor": actor(),
        "capture_id": execution_envelope.envelope_id,
        "causality": causality
        or {
            "parent_span_id": None,
            "root_trigger_id": None,
            "span_id": None,
            "trace_id": None,
        },
        "coverage": {
            "detail_state": "complete",
            "dropped_record_count": 0,
            "mode": "trace",
        },
        "format": "workbench-cleanroom-worldgen-observatory-raw-v1",
        "order": {
            "lamport": sequence,
            "monotonic_ns": sequence,
            "thread_id": 1,
            "thread_name": "Server thread",
            "thread_sequence": sequence,
        },
        "outcome": {
            "exception_class": None,
            "exception_message_sha256": None,
            "state": outcome,
        },
        "payload": payload,
        "record_type": record_type,
        "scope": scope or {"chunk_x": None, "chunk_z": None, "dimension_id": None},
        "sequence": sequence,
    }


def raw_rows(execution_envelope, decisions) -> list[dict[str, object]]:
    plan = json.loads(PROBE_PLAN.read_text(encoding="utf-8"))
    planned = {row["raw_hook_id"]: row for row in plan["hooks"]}
    rows: list[dict[str, object]] = []

    def add(record_type, payload, **keywords):
        rows.append(raw_row(len(rows), execution_envelope, record_type, payload, **keywords))

    for hook_id in sorted(REQUIRED_HOOKS):
        hook = planned[hook_id]
        add(
            "probe_health",
            {
                "expected_injection_count": hook["expected_cardinality"],
                "health_state": "reached",
                "hook_id": hook_id,
                "observed_injection_count": hook["expected_cardinality"],
                "original_class_sha256": NONZERO,
                "target_class": hook["target_class"],
                "target_descriptor": hook["target_descriptor"],
                "target_method": hook["target_method"],
                "transformed_class_sha256": "3" * 64,
            },
        )
    min_x, min_z, width, height = REGION
    for chunk_x in range(min_x, min_x + width):
        for chunk_z in range(min_z, min_z + height):
            span_id = f"cleanroom-worldgen-span:{len(rows)}"
            causality = {
                "parent_span_id": None,
                "root_trigger_id": span_id,
                "span_id": span_id,
                "trace_id": f"cleanroom-worldgen-trace:{len(rows)}",
            }
            scope = {"chunk_x": chunk_x, "chunk_z": chunk_z, "dimension_id": 0}
            add(
                "span_enter",
                {
                    "arguments_sha256": "4" * 64,
                    "operation_id": "cleanroom-worldgen:chunk.populate_neighbors",
                    "span_kind": "generation_phase",
                },
                scope=scope,
                causality=causality,
                outcome="entered",
            )
            add(
                "span_return",
                {"result_sha256": "5" * 64, "span_kind": "generation_phase"},
                scope=scope,
                causality=causality,
                outcome="returned",
            )
    add(
        "event_dispatch",
        {
            "boundary": "listener_return",
            "bus_id": "forge-bus:event_bus",
            "cancelled_after": False,
            "cancelled_before": False,
            "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent",
            "listener": "fixture-listener",
            "listener_ordinal": 0,
            "result_after": "DEFAULT",
            "result_before": "DEFAULT",
            "state_after_sha256": "6" * 64,
            "state_before_sha256": "7" * 64,
        },
    )
    for decision in decisions:
        if decision["record_type"] != "feature-decision" or decision["outcome"] != "placed":
            continue
        for write in decision["writes"]:
            if not write["invoked"]:
                continue
            position = write["position"]
            add(
                "block_write",
                {
                    "after_state_sha256": _state_digest(write["after_state"]),
                    "before_state_sha256": _state_digest(write["before_state"]),
                    "chain_depth": 1,
                    "channel": "chunk_storage",
                    "flags": None,
                    "generation_chunk_x": decision["chunk_x"],
                    "generation_chunk_z": decision["chunk_z"],
                    "hook_id": "cleanroom-worldgen:write.chunk_storage",
                    "position_x": position["x"],
                    "position_y": position["y"],
                    "position_z": position["z"],
                    "requested_state_sha256": _state_digest(write["requested_state"]),
                    "target_chunk_x": position["x"] // 16,
                    "target_chunk_z": position["z"] // 16,
                    "terminal": True,
                    "write_chain_id": f"cleanroom-worldgen-write:{len(rows)}",
                },
                scope={
                    "chunk_x": decision["chunk_x"],
                    "chunk_z": decision["chunk_z"],
                    "dimension_id": 0,
                },
            )
    return rows


def dense_scan(decisions) -> dict[str, object]:
    chunks: dict[tuple[int, int], dict[str, object]] = {}
    min_x, min_z, width, height = REGION
    for chunk_x in range(min_x, min_x + width):
        for chunk_z in range(min_z, min_z + height):
            chunks[(chunk_x, chunk_z)] = {
                "chunkX": chunk_x,
                "chunkZ": chunk_z,
                "omittedAirSections": [],
                "sections": [
                    {
                        "indices": [0] * 4096,
                        "palette": [
                            "minecraft:air",
                            "minecraft:mossy_cobblestone",
                            "minecraft:cobblestone",
                        ],
                        "ySection": 4,
                    }
                ],
            }
    for decision in decisions:
        if decision["record_type"] != "feature-decision" or decision["outcome"] != "placed":
            continue
        for write in decision["writes"]:
            if not write["invoked"]:
                continue
            position = write["position"]
            chunk = chunks[(position["x"] // 16, position["z"] // 16)]
            section = chunk["sections"][0]
            index = (
                (position["y"] & 15) * 256
                + (position["z"] & 15) * 16
                + (position["x"] & 15)
            )
            section["indices"][index] = (
                1 if write["requested_state"]["registry_name"].endswith("mossy_cobblestone") else 2
            )
    return {"denseBlockMap": {"chunks": list(chunks.values())}}


def strata_receipt(execution_envelope, launch_digest: str, scan_digest: str):
    min_x, min_z, width, height = REGION
    value = {
        "adapter": {},
        "artifacts": {
            "launch_log": {"sha256": launch_digest},
            "scan": {"sha256": scan_digest},
        },
        "boundaries": dict(STRATA_BOUNDARIES),
        "canonicalization_id": STRATA_CANONICALIZATION_ID,
        "capture": {
            "chunk_generator_class": GENERATOR,
            "chunk_window": {
                "chunkSizeX": width,
                "chunkSizeZ": height,
                "haloChunks": 1,
                "minChunkX": min_x,
                "minChunkZ": min_z,
            },
            "dimension_id": 0,
            "terrain_type": "wb_proto",
            "world_seed": SEED,
        },
        "checks": {},
        "format": STRATA_RECEIPT_FORMAT,
        "receipt_id": "",
        "runtime": {},
        "schema_version": 1,
    }
    material = deepcopy(value)
    material.pop("receipt_id")
    value["receipt_id"] = STRATA_RECEIPT_PREFIX + hashlib.sha256(
        strata_json_bytes(material)
    ).hexdigest()
    return value


def produce_join(
    root: Path,
    nonce: str,
    *,
    action_policy_sha256: str = "2" * 64,
):
    execution_envelope = envelope(
        nonce,
        action_policy_sha256=action_policy_sha256,
    )
    decisions = causal_rows(execution_envelope)
    log_path = root / f"{nonce}.log"
    log_path.write_text(
        "".join(
            f"[INFO] {CAUSAL_PREFIX} {json.dumps(row, separators=(',', ':'))}\n"
            for row in decisions
        ),
        encoding="utf-8",
    )
    causal = extract_causal_trace(
        log_path,
        envelope=execution_envelope,
        causal_schema_path=CAUSAL_SCHEMA,
    )
    raw_path = root / f"{nonce}.raw.ndjson"
    raw_path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in raw_rows(execution_envelope, decisions)),
        encoding="utf-8",
    )
    audit = audit_population_capture(
        raw_path,
        envelope=execution_envelope,
        raw_schema_path=RAW_SCHEMA,
        probe_plan_path=PROBE_PLAN,
        requested_mode="trace",
    )
    scan_path = root / f"{nonce}.scan.json"
    scan_path.write_bytes(canonical_json_bytes(dense_scan(decisions)))
    scan_digest = hashlib.sha256(scan_path.read_bytes()).hexdigest()
    receipt_path = root / f"{nonce}.strata-receipt.json"
    receipt_path.write_bytes(
        canonical_json_bytes(
            strata_receipt(execution_envelope, causal.launch_log_sha256, scan_digest)
        )
    )
    joined = join_same_run(
        envelope=execution_envelope,
        audit=audit,
        causal=causal,
        strata_receipt_path=receipt_path,
        strata_scan_path=scan_path,
    )
    return execution_envelope, audit, causal, joined


class CrucibleWorldgenV2Tests(unittest.TestCase):
    def test_benchmark_refuses_missing_host_measurement_before_querying(self):
        from unittest.mock import patch
        with patch.dict("sys.modules", {"resource": None}):
            with self.assertRaisesRegex(WorldgenGraphError, "benchmarking is unavailable"):
                benchmark_worldgen_queries(None, [])

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def graph_proof(self):
        policy_bytes = ACTION_POLICY.read_bytes()
        policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
        first_envelope, first_audit, first_causal, first_join = produce_join(
            self.root,
            "graph-control-a",
            action_policy_sha256=policy_sha256,
        )
        second_envelope, second_audit, second_causal, second_join = produce_join(
            self.root,
            "graph-control-b",
            action_policy_sha256=policy_sha256,
        )
        stability = compare_fresh_controls(first_join, second_join)
        self.assertEqual(
            load_stability_receipt(stability.canonical_bytes),
            stability,
        )

        control_roots = []
        for label, execution_envelope, audit, causal, joined in (
            (
                "control-a",
                first_envelope,
                first_audit,
                first_causal,
                first_join,
            ),
            (
                "control-b",
                second_envelope,
                second_audit,
                second_causal,
                second_join,
            ),
        ):
            control_root = self.root / label
            artifact_root = control_root / "artifacts/worldgen-v2"
            artifact_root.mkdir(parents=True)
            terminal = seal_execution_terminal(
                execution_envelope,
                process_exit_code=0,
                process_outcome="complete",
                producer_receipt_ids=[
                    audit.audit_id,
                    causal.receipt_id,
                    joined.receipt_id,
                    joined.strata_receipt_id,
                ],
                launch_log_sha256=causal.launch_log_sha256,
                raw_capture_sha256=audit.raw_sha256,
            )
            for name, raw in (
                ("execution-envelope-v2.json", execution_envelope.canonical_bytes),
                ("population-capture-audit-v2.json", audit.canonical_bytes),
                ("causal-trace-receipt-v2.json", causal.canonical_bytes),
                ("same-run-join-receipt-v2.json", joined.canonical_bytes),
                ("execution-terminal-v2.json", terminal.canonical_bytes),
            ):
                (artifact_root / name).write_bytes(raw)
            control_roots.append(control_root)
        stability_path = self.root / "worldgen-stability-receipt-v2.json"
        stability_path.write_bytes(stability.canonical_bytes)
        return load_worldgen_proof_bundle(
            control_a_root=control_roots[0],
            control_b_root=control_roots[1],
            stability_receipt_path=stability_path,
            action_policy_path=ACTION_POLICY,
            stale_pattern_path=STALE_PATTERN,
            current_feature_source_path=CURRENT_FEATURE,
            raw_v1_schema_path=RAW_SCHEMA,
        )

    def test_execution_envelope_is_closed_and_separates_fresh_controls(self) -> None:
        first = envelope("control-a")
        second = envelope("control-b")
        self.assertEqual(load_execution_envelope(first.canonical_bytes), first)
        self.assertEqual(first.run_plan_id, second.run_plan_id)
        self.assertNotEqual(first.envelope_id, second.envelope_id)
        self.assertNotEqual(first.runtime_epoch_id, second.runtime_epoch_id)
        self.assertNotEqual(first.world_instance_id, second.world_instance_id)
        changed = json.loads(first.canonical_bytes)
        changed["run_plan"]["seed"] += 1
        with self.assertRaises(WorldgenIdentityError):
            load_execution_envelope(canonical_json_bytes(changed))

    def test_synthetic_same_run_join_closes_rng_writes_and_final_state(self) -> None:
        execution_envelope, audit, causal, joined = produce_join(self.root, "control-a")
        self.assertEqual(audit.population_root_count, 16)
        self.assertEqual(
            sum(row["outcome"] == "placed" for row in causal.features.values()),
            3,
        )
        realized = [site for site in joined.sites if site["site_state"] == "realized"]
        self.assertEqual(len(realized), 3)
        self.assertTrue(
            any(
                write["join_state"] == "known-absent-by-closed-decision"
                for site in realized
                for write in site["writes"]
            )
        )
        self.assertEqual(load_same_run_join_receipt(joined.canonical_bytes), joined)
        self.assertTrue(
            all(
                write["join_state"] == "matched-final-state"
                for site in realized
                for write in site["writes"]
                if write["causal"]["role"] in {"mandatory-base", "mandatory-east"}
            )
        )
        terminal = seal_execution_terminal(
            execution_envelope,
            process_exit_code=0,
            process_outcome="complete",
            producer_receipt_ids=[audit.audit_id, causal.receipt_id, joined.receipt_id],
            launch_log_sha256=causal.launch_log_sha256,
            raw_capture_sha256=audit.raw_sha256,
        )
        self.assertNotIn(terminal.terminal_id, terminal.to_dict()["producer_receipt_ids"])

    def test_two_fresh_controls_compare_stable_without_collapsing_identity(self) -> None:
        first_envelope, _, _, first = produce_join(self.root, "control-a")
        second_envelope, _, _, second = produce_join(self.root, "control-b")
        stability = compare_fresh_controls(first, second)
        self.assertEqual(stability.outcome, "stable-within-envelope")
        self.assertEqual(len(stability.sites), 16)
        self.assertEqual(first_envelope.run_plan_id, second_envelope.run_plan_id)
        self.assertNotEqual(first_envelope.envelope_id, second_envelope.envelope_id)
        self.assertTrue(all(site["comparison"] == "stable" for site in stability.sites))

    def test_rng_replay_rejects_a_changed_draw(self) -> None:
        execution_envelope = envelope("tampered-causal")
        rows = causal_rows(execution_envelope)
        feature = next(row for row in rows if row["record_type"] == "feature-decision")
        feature["eligibility_draw"] = (feature["eligibility_draw"] + 1) % 10
        path = self.root / "tampered.log"
        path.write_text(
            "".join(f"{CAUSAL_PREFIX} {json.dumps(row)}\n" for row in rows),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(WorldgenCaptureError, "eligibility draw differs"):
            extract_causal_trace(
                path,
                envelope=execution_envelope,
                causal_schema_path=CAUSAL_SCHEMA,
            )

    def test_capture_audit_requires_every_hook_to_finish_reached(self) -> None:
        execution_envelope = envelope("missing-health")
        decisions = causal_rows(execution_envelope)
        rows = raw_rows(execution_envelope, decisions)
        for row in rows:
            if (
                row["record_type"] == "probe_health"
                and row["payload"]["hook_id"] == "cleanroom-worldgen:write.world_api"
            ):
                row["payload"]["health_state"] = "applied_not_reached"
        path = self.root / "missing-health.raw.ndjson"
        path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(WorldgenCaptureError, "did not finish reached"):
            audit_population_capture(
                path,
                envelope=execution_envelope,
                raw_schema_path=RAW_SCHEMA,
                probe_plan_path=PROBE_PLAN,
                requested_mode="trace",
            )

    def test_graph_set_is_owner_separated_reproducible_and_queryable(self) -> None:
        retained_v1 = STALE_PATTERN.read_bytes()
        retained_v1_sha256 = hashlib.sha256(retained_v1).hexdigest()
        proof = self.graph_proof()
        keywords = {
            "atlas_generative_port": atlas_generative_test_port,
            "atlas_realized_port": atlas_realized_test_port,
        }
        first = build_worldgen_graph_set(proof, **keywords)
        second = build_worldgen_graph_set(proof, **keywords)
        third = build_worldgen_graph_set(proof, **keywords)
        self.assertEqual(first.identity(), second.identity())
        self.assertEqual(first.identity(), third.identity())
        self.assertEqual(
            {
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
            },
            {(row.category_id, row.resolution_id) for row in first.members},
        )
        owners = {
            row.category_id: row.graph_revision.to_dict()["authority_owner"]
            for row in first.members
        }
        self.assertEqual(owners[CATEGORY_GENERATIVE], owners[CATEGORY_REALIZED])
        self.assertEqual(
            owners[CATEGORY_CAPTURE_HEALTH], owners[CATEGORY_OCCURRENCE]
        )
        self.assertEqual(owners[CATEGORY_OCCURRENCE], owners[CATEGORY_STABILITY])
        self.assertNotEqual(owners[CATEGORY_GENERATIVE], owners[CATEGORY_OCCURRENCE])
        self.assertEqual(retained_v1, STALE_PATTERN.read_bytes())
        self.assertEqual(retained_v1_sha256, proof.stale_pattern_sha256)

        store = WorldgenGraphStore((self.root / "query-store").resolve())
        store.publish(first)
        service = WorldgenStoredQueryService(
            store,
            graph_set_revision_id=first.graph_set_revision.id,
        )
        health = service.query({"query": "capture-health"})
        stale = service.query({"query": "stale-pattern-conflicts"})
        absence = service.query({"query": "known-absence"})
        site = service.query(
            {
                "category_id": CATEGORY_OCCURRENCE,
                "chunk": {"x": 33, "z": 33},
                "query": "site",
                "resolution_id": RESOLUTION_EXACT,
            }
        )
        drilldown = service.query(
            {"category_id": CATEGORY_REALIZED, "query": "drilldown"}
        )
        self.assertEqual(health.result_count, 1)
        self.assertEqual(stale.result_count, 2)
        self.assertGreaterEqual(absence.result_count, 2)
        self.assertEqual(site.result_count, 1)
        self.assertEqual(drilldown.result_count, 2)
        benchmark = benchmark_worldgen_queries(
            service,
            [
                {"query": "capture-health"},
                {"query": "known-absence"},
                {"query": "stale-pattern-conflicts"},
            ],
            repetitions=20,
        )
        self.assertEqual(benchmark["raw_archive_open_count"], 0)
        self.assertLess(benchmark["maximum_repetition_nanoseconds"], 5_000_000_000)
        self.assertLess(benchmark["peak_rss_kib"], 4 * 1024 * 1024)

    def test_incremental_recovery_policy_and_isolation_gates(self) -> None:
        proof = self.graph_proof()
        keywords = {
            "atlas_generative_port": atlas_generative_test_port,
            "atlas_realized_port": atlas_realized_test_port,
        }
        full = build_worldgen_graph_set(proof, **keywords)
        control_a_only = build_worldgen_graph_set(
            proof,
            include_stability=False,
            **keywords,
        )
        appended = build_worldgen_graph_set(
            proof,
            prior_build=control_a_only,
            **keywords,
        )
        self.assertEqual(full.identity(), appended.identity())
        self.assertEqual(len(appended.reused_members), 5)
        self.assertIsNotNone(appended.incremental_receipt)
        self.assertEqual(
            5, len(appended.incremental_receipt.rebuilt_members)
        )
        self.assertEqual(
            5, len(appended.incremental_receipt.changed_inputs)
        )

        variant_clean = build_worldgen_graph_set(
            proof,
            eligibility_bound=11,
            **keywords,
        )
        variant_incremental = build_worldgen_graph_set(
            proof,
            eligibility_bound=11,
            prior_build=full,
            **keywords,
        )
        self.assertEqual(variant_clean.identity(), variant_incremental.identity())
        self.assertEqual(len(variant_incremental.reused_members), 9)
        self.assertIsNotNone(variant_incremental.incremental_receipt)
        self.assertEqual(
            ((CATEGORY_GENERATIVE, RESOLUTION_OPERATIONAL),),
            variant_incremental.incremental_receipt.rebuilt_members,
        )
        self.assertEqual(
            1, len(variant_incremental.incremental_receipt.changed_inputs)
        )
        self.assertNotIn(
            (CATEGORY_GENERATIVE, RESOLUTION_OPERATIONAL),
            variant_incremental.reused_members,
        )

        current_generative = next(
            member
            for member in variant_clean.members
            if (member.category_id, member.resolution_id)
            == (CATEGORY_GENERATIVE, RESOLUTION_OPERATIONAL)
        )
        tampered_members = tuple(
            replace(
                member,
                input_revision_id=current_generative.input_revision_id,
                input_revision_bytes=current_generative.input_revision_bytes,
            )
            if (member.category_id, member.resolution_id)
            == (CATEGORY_GENERATIVE, RESOLUTION_OPERATIONAL)
            else member
            for member in full.members
        )
        with self.assertRaisesRegex(
            WorldgenGraphError, "differs from independent clean build"
        ):
            build_worldgen_graph_set(
                proof,
                eligibility_bound=11,
                prior_build=replace(full, members=tampered_members),
                **keywords,
            )

        incremental_store_root = (
            self.root / "incremental-publication-equivalence"
        ).resolve()
        clean_store_root = (
            self.root / "clean-publication-equivalence"
        ).resolve()
        incremental_store = WorldgenGraphStore(incremental_store_root)
        clean_store = WorldgenGraphStore(clean_store_root)
        self.assertEqual(
            incremental_store.publish(full).canonical_bytes,
            clean_store.publish(full).canonical_bytes,
        )
        self.assertEqual(
            incremental_store.publish(variant_incremental).canonical_bytes,
            clean_store.publish(variant_clean).canonical_bytes,
        )
        incremental_files = sorted(
            path.relative_to(incremental_store_root)
            for path in incremental_store_root.rglob("*")
            if path.is_file()
        )
        clean_files = sorted(
            path.relative_to(clean_store_root)
            for path in clean_store_root.rglob("*")
            if path.is_file()
        )
        self.assertEqual(incremental_files, clean_files)
        self.assertEqual(
            [
                incremental_store_root.joinpath(path).read_bytes()
                for path in incremental_files
            ],
            [
                clean_store_root.joinpath(path).read_bytes()
                for path in clean_files
            ],
        )

        store = WorldgenGraphStore((self.root / "recovery-store").resolve())
        store.publish(full)
        for failpoint in ("after-objects", "after-manifest", "before-marker"):
            with self.subTest(failpoint=failpoint):
                with self.assertRaises(WorldgenGraphError):
                    store.publish(variant_clean, failpoint=failpoint)
                self.assertEqual(
                    store.resolve_current()["marker"]["graph_set_revision_id"],
                    full.graph_set_revision.id,
                )

        self.assertEqual(
            evaluate_worldgen_action(
                policy=None,
                action="read-query-visualize",
                envelope=proof.envelope_a,
            )["disposition"],
            "unavailable",
        )
        for action in ("read-query-visualize", "plan-review", "rollback"):
            self.assertEqual(
                evaluate_worldgen_action(
                    policy=proof.action_policy_bytes,
                    action=action,
                    envelope=proof.envelope_a,
                )["disposition"],
                "allow",
            )
        self.assertEqual(
            evaluate_worldgen_action(
                policy=proof.action_policy_bytes,
                action="apply",
                envelope=proof.envelope_a,
                guardrails=(
                    "disposable-staging-world",
                    "exact-context-ref",
                    "exact-input-binding",
                    "reversible-change-set",
                    "rollback-plan",
                ),
            )["disposition"],
            "allow",
        )
        for action in (
            "apply-to-protected-world",
            "promote-stable-support",
            "publish-authoritative-worldgen-semantics",
        ):
            self.assertEqual(
                evaluate_worldgen_action(
                    policy=proof.action_policy_bytes,
                    action=action,
                    envelope=proof.envelope_a,
                )["disposition"],
                "block",
            )
        tampered_policy = proof.action_policy_bytes.replace(
            b'"state": "experimental-draft"',
            b'"state": "tampered"',
        )
        with self.assertRaisesRegex(WorldgenGraphError, "exact policy bound"):
            evaluate_worldgen_action(
                policy=tampered_policy,
                action="read-query-visualize",
                envelope=proof.envelope_a,
            )
        self.assertEqual(
            evaluate_synthetic_tested_support_gate(
                support_decision_id="support-decision:sha256:" + "a" * 64,
                support_state="tested-supported",
            )["disposition"],
            "allow",
        )

        alternate = envelope(
            "alternate-profile",
            action_policy_sha256=proof.action_policy_sha256,
            profile_id="workbench-pack:alternate-worldgen-conformance-v1",
        )
        self.assertEqual(
            alternate.to_dict()["run_plan"]["seed"],
            proof.envelope_a.to_dict()["run_plan"]["seed"],
        )
        self.assertEqual(
            alternate.to_dict()["run_plan"]["region"],
            proof.envelope_a.to_dict()["run_plan"]["region"],
        )
        self.assertNotEqual(alternate.context_ref_id, proof.envelope_a.context_ref_id)
        self.assertNotEqual(alternate.input_binding_id, proof.envelope_a.input_binding_id)
        self.assertNotEqual(alternate.world_instance_id, proof.envelope_a.world_instance_id)
        self.assertEqual(
            full.graph_set_revision.to_dict()["context_ref_id"],
            proof.envelope_a.context_ref_id,
        )


if __name__ == "__main__":
    unittest.main()
