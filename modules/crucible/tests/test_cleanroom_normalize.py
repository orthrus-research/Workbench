#!/usr/bin/env python3

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    new_run,
    validate_bundle,
)
from workbench_crucible_observatory.cleanroom_normalize import (  # noqa: E402
    StrictActorBinder,
    normalize_cleanroom_raw,
)
from workbench_crucible_observatory.cleanroom_raw import (  # noqa: E402
    RawAdmission,
    admit_cleanroom_raw,
)


PLAN_PATH = (
    REPOSITORY_ROOT
    / "profiles/platforms/cleanroom/candidates/0.6.8-alpha"
    / "worldgen-observatory-probe-plan-v1.json"
)
RAW_SCHEMA_PATH = (
    PLAN_PATH.parent
    / "worldgen-observatory-fixture/src/main/resources"
    / "workbench-worldgen-observatory-raw-v1.schema.json"
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def actor(
    mod_id: str | None,
    class_name: str | None,
    method_name: str | None,
    method_descriptor: str | None,
    *,
    binding: str = "runtime_class",
    source: str | None = None,
    namespace: str | None = "jvm_descriptor",
    transformed: str | None = None,
) -> dict[str, Any]:
    return {
        "binding": binding,
        "mod_id": mod_id,
        "class_name": class_name,
        "method_name": method_name,
        "method_descriptor": method_descriptor,
        "mapping_namespace": namespace,
        "code_source_sha256": source,
        "transformed_class_sha256": transformed,
    }


def inventory_row(value: dict[str, Any]) -> dict[str, str]:
    return {
        field: value[field]
        for field in (
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
        )
    }


class RawRows:
    def __init__(self, capture_id: str = "capture-fixture-1") -> None:
        self.capture_id = capture_id
        self.rows: list[dict[str, Any]] = []
        self.thread_sequences: dict[tuple[str, int], int] = defaultdict(int)
        self.stack: list[tuple[str, dict[str, Any], tuple[int, int, int]]] = []
        self.trace = "cleanroom-worldgen-trace:fixture"
        self.root = "cleanroom-worldgen-trigger:fixture"

    def add(
        self,
        record_type: str,
        payload: dict[str, Any],
        raw_actor: dict[str, Any],
        *,
        scope: tuple[int | None, int | None, int | None] = (0, 64, 64),
        state: str = "observed",
        exception_class: str | None = None,
        exception_message_sha256: str | None = None,
        new_span: str | None = None,
    ) -> dict[str, Any]:
        thread = ("Server thread", 1)
        if new_span is not None:
            span_id = new_span
            parent = self.stack[-1][0] if self.stack else None
            trace = self.trace
            root = self.root
        elif self.stack:
            span_id = self.stack[-1][0]
            parent = self.stack[-2][0] if len(self.stack) > 1 else None
            trace = self.trace
            root = self.root
        else:
            span_id = None
            parent = None
            trace = None
            root = None
        sequence = len(self.rows)
        row = {
            "format": "workbench-cleanroom-worldgen-observatory-raw-v1",
            "record_type": record_type,
            "sequence": sequence,
            "capture_id": self.capture_id,
            "scope": {
                "dimension_id": scope[0],
                "chunk_x": scope[1],
                "chunk_z": scope[2],
            },
            "causality": {
                "trace_id": trace,
                "root_trigger_id": root,
                "span_id": span_id,
                "parent_span_id": parent,
            },
            "actor": raw_actor,
            "order": {
                "thread_name": thread[0],
                "thread_id": thread[1],
                "thread_sequence": self.thread_sequences[thread],
                "lamport": sequence,
                "monotonic_ns": sequence,
            },
            "outcome": {
                "state": state,
                "exception_class": exception_class,
                "exception_message_sha256": exception_message_sha256,
            },
            "coverage": {
                "mode": "lossless-fixture",
                "detail_state": "complete",
                "dropped_record_count": 0,
            },
            "payload": payload,
        }
        self.thread_sequences[thread] += 1
        self.rows.append(row)
        return row

    def enter(
        self,
        span_id: str,
        span_kind: str,
        operation_id: str,
        raw_actor: dict[str, Any],
    ) -> None:
        self.add(
            "span_enter",
            {
                "span_kind": span_kind,
                "operation_id": operation_id,
                "arguments_sha256": digest("arguments:" + span_id),
            },
            raw_actor,
            state="entered",
            new_span=span_id,
        )
        self.stack.append((span_id, raw_actor, (0, 64, 64)))

    def returned(self, span_kind: str) -> None:
        span_id, raw_actor, _ = self.stack[-1]
        self.add(
            "span_return",
            {"span_kind": span_kind, "result_sha256": digest("result:" + span_id)},
            raw_actor,
            state="returned",
        )
        self.stack.pop()


class CleanroomNormalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.workbench = actor(
            "workbench_worldgen_observatory",
            "dev.workbench.worldgenobservatory.probe.ProbeRuntime",
            "normalize",
            "(RawAdmission)Bundle",
            binding="workbench",
            source=digest("observer-jar"),
            namespace="normalizer-v1",
        )
        self.generator = actor(
            "synthetic_worldgen",
            "dev.workbench.syntheticworldgen.SyntheticWorldGenerator",
            "generateChunk",
            "(II)Lnet/minecraft/world/chunk/Chunk;",
            source=digest("fixture-jar"),
        )
        self.event_bus = actor(
            "minecraft_or_forge",
            "net.minecraftforge.fml.common.eventhandler.EventBus",
            "post",
            "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
            source=digest("forge-jar"),
            namespace="mcp-stable_39",
        )
        self.listener = actor(
            "synthetic_worldgen",
            "dev.workbench.syntheticworldgen.DecorationListener",
            "onPopulate",
            "(Lnet/minecraftforge/event/terraingen/PopulateChunkEvent;)V",
            binding="event_listener",
            source=digest("fixture-jar"),
        )
        self.chunk = actor(
            "minecraft_or_forge",
            "net.minecraft.world.chunk.Chunk",
            "setBlockState",
            "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)"
            "Lnet/minecraft/block/state/IBlockState;",
            binding="exact_target",
            source=digest("minecraft-jar"),
            namespace="mcp-stable_39",
        )
        self.world_writer = actor(
            "synthetic_worldgen",
            "dev.workbench.syntheticworldgen.SyntheticWorldGenerator",
            "generate",
            "(Ljava/util/Random;IILnet/minecraft/world/World;)V",
            binding="stack_source_method",
            source=digest("fixture-jar"),
        )
        self.inventory = [
            inventory_row(value)
            for value in (
                self.workbench,
                self.generator,
                self.event_bus,
                self.listener,
                self.chunk,
                self.world_writer,
            )
        ]
        self.class_dumps = {
            value["class_name"]: digest("transformed:" + value["class_name"])
            for value in (
                self.workbench,
                self.generator,
                self.event_bus,
                self.listener,
                self.chunk,
                self.world_writer,
            )
        }
        for hook in self.plan["hooks"]:
            self.class_dumps.setdefault(
                hook["target_class"],
                digest("transformed:" + hook["target_class"]),
            )

    def run_manifest(self, label: str) -> dict[str, Any]:
        return new_run(
            capture_mode="lossless-fixture",
            capture_plan_sha256=digest("capture-plan"),
            fixture_id="crucible-fixture:cleanroom-normalizer-" + label,
            fixture_sha256=digest("fixture"),
            environment={
                "minecraft_version": "1.12.2",
                "platform_profile_id": "workbench-platform:cleanroom:0.6.8-alpha",
                "platform_profile_sha256": digest("candidate"),
                "pack_profile_id": None,
                "pack_profile_sha256": None,
                "snapshot_id": "snapshot:normalizer-test",
                "physical_side": "DEDICATED_SERVER",
                "runtime_java": "25.0.4+7",
                "mapping_namespace": "mcp-stable_39",
                "transformed_runtime_sha256": digest("runtime"),
                "mod_set_sha256": digest("mods"),
                "configuration_set_sha256": digest("configuration"),
            },
            world={
                "world_instance_id": "crucible-world:" + label,
                "world_seed_sha256": digest("123456789"),
                "world_type": "wb_observe",
                "generator_options_sha256": digest("{}"),
                "dimension_ids": [0],
            },
        )

    def admission(self, rows: RawRows, *, completed: bool) -> RawAdmission:
        with tempfile.TemporaryDirectory() as temporary:
            raw_path = Path(temporary) / "capture.ndjson"
            raw_path.write_text(
                "".join(
                    json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                    for row in rows.rows
                ),
                encoding="utf-8",
            )
            return admit_cleanroom_raw(
                raw_path,
                schema_path=RAW_SCHEMA_PATH,
                probe_plan_path=PLAN_PATH,
                requested_mode="lossless-fixture",
                allow_incomplete=not completed,
            )

    def started_rows(self, capture_id: str) -> RawRows:
        rows = RawRows(capture_id)
        rows.add(
            "capture_control",
            {
                "control": "start",
                "capture_control_id": rows.capture_id + ":driver",
                "controller": "dedicated_server_fixture_v1",
                "requested_mode": "lossless-fixture",
                "world_seed_sha256": digest("123456789"),
                "route_order": "forward",
                "selector_sha256": digest("selector"),
                "route_sha256": digest("route"),
                "selected_chunks": ["64,64"],
            },
            self.workbench,
            scope=(0, None, None),
            state="entered",
        )
        return rows

    def add_probe_health(
        self,
        rows: RawRows,
        hook_index: int,
        *,
        new_span: str | None = None,
    ) -> dict[str, Any]:
        hook = [
            value for value in self.plan["hooks"] if value["canonical_role"] is not None
        ][hook_index]
        return rows.add(
            "probe_health",
            {
                "hook_id": hook["raw_hook_id"],
                "health_state": "reached",
                "target_class": hook["target_class"],
                "target_method": hook["target_method"],
                "target_descriptor": hook["target_descriptor"],
                "original_class_sha256": digest("original:" + hook["raw_hook_id"]),
                "transformed_class_sha256": digest(
                    "transformed:" + hook["raw_hook_id"]
                ),
                "expected_injection_count": hook["expected_cardinality"],
                "observed_injection_count": hook["expected_cardinality"],
            },
            self.workbench,
            scope=(None, None, None),
            new_span=new_span,
        )

    def complete_rows(self) -> RawRows:
        rows = RawRows()
        rows.add(
            "capture_control",
            {
                "control": "start",
                "capture_control_id": rows.capture_id + ":driver",
                "controller": "dedicated_server_fixture_v1",
                "requested_mode": "lossless-fixture",
                "world_seed_sha256": digest("123456789"),
                "route_order": "forward",
                "selector_sha256": digest("selector"),
                "route_sha256": digest("route"),
                "selected_chunks": ["64,64"],
            },
            self.workbench,
            scope=(0, None, None),
            state="entered",
        )
        for hook in self.plan["hooks"]:
            if hook["canonical_role"] is None:
                continue
            rows.add(
                "probe_health",
                {
                    "hook_id": hook["raw_hook_id"],
                    "health_state": "reached",
                    "target_class": hook["target_class"],
                    "target_method": hook["target_method"],
                    "target_descriptor": hook["target_descriptor"],
                    "original_class_sha256": digest(
                        "original:" + hook["raw_hook_id"]
                    ),
                    "transformed_class_sha256": digest(
                        "transformed:" + hook["raw_hook_id"]
                    ),
                    "expected_injection_count": 1,
                    "observed_injection_count": 1,
                },
                self.workbench,
                scope=(None, None, None),
            )
        rows.enter(
            "cleanroom-worldgen-span:generator",
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        rows.add(
            "chunk_access",
            {
                "access_kind": "generate",
                "api_id": "cleanroom-worldgen:cps.generate_chunk_call",
                "current_generation_chunk_x": 64,
                "current_generation_chunk_z": 64,
                "requested_chunk_x": 64,
                "requested_chunk_z": 64,
                "loaded_before": False,
                "result": "generated",
            },
            self.generator,
        )
        rows.enter(
            "cleanroom-worldgen-span:event-post",
            "generation_phase",
            "cleanroom-worldgen:event_bus.post",
            self.event_bus,
        )
        before = digest("event-before")
        after = digest("event-after")
        rows.add(
            "event_dispatch",
            {
                "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
                "bus_id": "forge-bus:event_bus",
                "boundary": "post_enter",
                "listener_ordinal": None,
                "listener": None,
                "state_before_sha256": before,
                "state_after_sha256": before,
                "cancelled_before": False,
                "cancelled_after": False,
                "result_before": "DEFAULT",
                "result_after": "DEFAULT",
            },
            self.event_bus,
        )
        rows.enter(
            "cleanroom-worldgen-span:listener",
            "event_listener",
            "cleanroom-worldgen:event_bus.listener_invoke",
            self.listener,
        )
        for boundary, state_before, state_after in (
            ("listener_enter", before, before),
            ("listener_return", before, after),
        ):
            rows.add(
                "event_dispatch",
                {
                    "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
                    "bus_id": "forge-bus:event_bus",
                    "boundary": boundary,
                    "listener_ordinal": 0,
                    "listener": "stable-listener-name",
                    "state_before_sha256": state_before,
                    "state_after_sha256": state_after,
                    "cancelled_before": False,
                    "cancelled_after": False,
                    "result_before": "DEFAULT",
                    "result_after": "DEFAULT",
                },
                self.listener,
            )
        rows.returned("event_listener")
        rows.add(
            "event_dispatch",
            {
                "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
                "bus_id": "forge-bus:event_bus",
                "boundary": "post_return",
                "listener_ordinal": None,
                "listener": None,
                "state_before_sha256": before,
                "state_after_sha256": after,
                "cancelled_before": False,
                "cancelled_after": False,
                "result_before": "DEFAULT",
                "result_after": "DEFAULT",
            },
            self.event_bus,
        )
        rows.returned("generation_phase")
        chain = "cleanroom-worldgen-write:fixture"
        rows.add(
            "block_write",
            {
                "hook_id": "cleanroom-worldgen:write.chunk_storage",
                "write_chain_id": chain,
                "chain_depth": 1,
                "channel": "chunk_storage",
                "position_x": 1028,
                "position_y": 64,
                "position_z": 1028,
                "generation_chunk_x": 64,
                "generation_chunk_z": 64,
                "target_chunk_x": 64,
                "target_chunk_z": 64,
                "before_state_sha256": digest("air"),
                "requested_state_sha256": digest("marker"),
                "after_state_sha256": digest("marker"),
                "flags": None,
                "terminal": True,
            },
            self.chunk,
        )
        rows.add(
            "block_write",
            {
                "hook_id": "cleanroom-worldgen:write.world_api",
                "write_chain_id": chain,
                "chain_depth": 0,
                "channel": "world_api",
                "position_x": 1028,
                "position_y": 64,
                "position_z": 1028,
                "generation_chunk_x": 64,
                "generation_chunk_z": 64,
                "target_chunk_x": 64,
                "target_chunk_z": 64,
                "before_state_sha256": digest("air"),
                "requested_state_sha256": digest("marker"),
                "after_state_sha256": digest("marker"),
                "flags": 2,
                "terminal": False,
            },
            self.world_writer,
        )
        ambiguous = actor(
            "synthetic_worldgen",
            "dev.workbench.syntheticworldgen.SyntheticWorldGenerator",
            "generate",
            None,
            binding="stack_candidate",
            source=digest("fixture-jar"),
            namespace="jvm_stack",
        )
        rows.add(
            "decision",
            {
                "stage_id": "forge.world_generators.synthetic",
                "rule_id": digest("synthetic-decision"),
                "input_state_sha256": digest("decision-input"),
                "decision": "marker=selected",
                "output_state_sha256": digest("decision-output"),
                "world_seed_sha256": digest("123456789"),
            },
            ambiguous,
        )
        rows.add(
            "rng_observation",
            {
                "stage_id": "forge.world_generators.synthetic",
                "lane_id": "fixture",
                "stream_id": "worldgen-rng:synthetic-marker",
                "algorithm": "workbench.rng-lanes.mix64.v1",
                "operation": "derive_named_seed_without_consuming_runtime_random",
                "parameters_sha256": digest("rng-parameters"),
                "observed_seed": 42,
                "result_sha256": digest("42"),
                "rolling_digest_sha256": digest("rolling"),
                "world_seed_sha256": digest("123456789"),
            },
            self.world_writer,
        )
        rows.add(
            "checkpoint",
            {
                "checkpoint_id": "forge.world_generators.final",
                "stage_id": "forge.world_generators.final",
                "canonicalization_id": "chunk-block-state-registry-name-metadata-yzx-v1",
                "semantic_state_sha256": digest("semantic-chunk"),
                "world_seed_sha256": digest("123456789"),
            },
            self.workbench,
        )
        rows.returned("generator_call")
        rows.add(
            "fixture_driver",
            {
                "action": "complete",
                "completion_marker": "dedicated_server_fixture_complete_v1",
                "world_seed_sha256": digest("123456789"),
                "route_order": "forward",
                "route_sha256": digest("route"),
                "result_identity_sha256": digest("driver-result"),
                "selected_chunks": ["64,64"],
                "save_state": "flushed",
                "shutdown_state": "requested",
            },
            self.workbench,
            scope=(0, None, None),
            state="returned",
        )
        rows.add(
            "capture_control",
            {
                "control": "stop",
                "capture_control_id": rows.capture_id + ":driver",
                "controller": "dedicated_server_fixture_v1",
                "requested_mode": "lossless-fixture",
                "route_order": "forward",
                "route_sha256": digest("route"),
                "completion_state": "complete",
                "open_span_count": 0,
                "open_write_count": 0,
            },
            self.workbench,
            scope=(0, None, None),
            state="returned",
        )
        return rows

    def normalize(self, rows: RawRows, *, completed: bool, label: str) -> dict[str, Any]:
        return normalize_cleanroom_raw(
            self.admission(rows, completed=completed),
            run=self.run_manifest(label),
            selection_id="crucible-selection:chunks-64-64",
            probe_plan=self.plan,
            actor_inventory=self.inventory,
            class_dump_sha256=self.class_dumps,
            workbench_identity=self.workbench,
            monotonic_ns=lambda: 17,
            checkpoint_domains=("block_states", "biomes"),
        )

    def test_normalizes_balanced_capture_with_exact_and_ambiguous_actors(self) -> None:
        rows = self.complete_rows()
        bundle = self.normalize(rows, completed=True, label="complete")
        self.assertEqual(
            bundle,
            self.normalize(rows, completed=True, label="complete"),
        )
        validate_bundle(bundle, require_completed=True)
        self.assertEqual("completed", bundle["publication"]["state"])
        self.assertEqual("capture_control", bundle["records"][0]["record_type"])
        self.assertEqual("capture_control", bundle["records"][-1]["record_type"])

        health = next(
            row
            for row in bundle["records"]
            if row["record_type"] == "probe_health"
            and row["payload"]["hook_id"]
            == "worldgen-hook:chunk-primer-set-block-state"
        )
        self.assertEqual("workbench", health["actor"]["binding"])
        self.assertEqual("worldgen-hook:chunk-primer-set-block-state", health["payload"]["hook_id"])
        self.assertEqual(
            "net.minecraft.world.chunk.ChunkPrimer", health["payload"]["target_class"]
        )
        self.assertEqual(
            self.class_dumps["net.minecraft.world.chunk.ChunkPrimer"],
            health["payload"]["transformed_class_sha256"],
        )
        raw_primer_health = next(
            row
            for row in rows.rows
            if row["record_type"] == "probe_health"
            and row["payload"]["hook_id"]
            == "cleanroom-worldgen:write.chunk_primer"
        )
        self.assertNotEqual(
            raw_primer_health["payload"]["transformed_class_sha256"],
            health["payload"]["transformed_class_sha256"],
        )

        generator_span = next(
            row
            for row in bundle["records"]
            if row["record_type"] == "span_enter"
            and row["payload"]["span_kind"] == "generator_call"
        )
        listener_span = next(
            row
            for row in bundle["records"]
            if row["record_type"] == "span_enter"
            and row["payload"]["span_kind"] == "event_listener"
        )
        self.assertEqual("exact", generator_span["actor"]["binding"])
        self.assertEqual("exact", listener_span["actor"]["binding"])
        self.assertEqual(
            generator_span["causality"]["trace_id"],
            listener_span["causality"]["trace_id"],
        )
        self.assertIsNotNone(listener_span["causality"]["parent_span_id"])

        post_return = next(
            row
            for row in bundle["records"]
            if row["record_type"] == "event_dispatch"
            and row["payload"]["boundary"] == "post_return"
        )
        self.assertEqual(
            post_return["payload"]["state_after_sha256"],
            post_return["payload"]["state_before_sha256"],
        )
        self.assertNotIn("listener", post_return["payload"])

        writes = [row for row in bundle["records"] if row["record_type"] == "block_write"]
        terminal = next(row for row in writes if row["payload"]["terminal"])
        self.assertEqual("net.minecraft.world.chunk.Chunk", terminal["actor"]["class_name"])
        self.assertEqual("exact", terminal["actor"]["binding"])
        self.assertNotIn("hook_id", terminal["payload"])
        self.assertNotIn("requested_state_sha256", terminal["payload"])

        decision = next(row for row in bundle["records"] if row["record_type"] == "decision")
        self.assertEqual("ambiguous", decision["actor"]["binding"])
        self.assertEqual(["synthetic_worldgen"], decision["actor"]["candidate_mod_ids"])
        self.assertEqual("selected", decision["payload"]["decision"])
        self.assertEqual(1, len(bundle["semantic_fingerprints"]))
        self.assertEqual(
            ["biomes", "block_states"],
            bundle["semantic_fingerprints"][0]["included_domains"],
        )

    def test_accepts_immediate_root_and_nested_probe_health_span_preambles(self) -> None:
        root_rows = self.started_rows("capture-root-preamble")
        root_span = "cleanroom-worldgen-span:root-preamble"
        self.add_probe_health(root_rows, 0, new_span=root_span)
        root_rows.enter(
            root_span,
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        root_rows.returned("generator_call")
        root_bundle = self.normalize(root_rows, completed=False, label="root-preamble")
        root_types = [record["record_type"] for record in root_bundle["records"]]
        self.assertLess(root_types.index("probe_health"), root_types.index("span_enter"))

        nested_rows = self.started_rows("capture-nested-preamble")
        parent_span = "cleanroom-worldgen-span:preamble-parent"
        child_span = "cleanroom-worldgen-span:preamble-child"
        nested_rows.enter(
            parent_span,
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        # Existing health on the current span remains an ordinary in-span row.
        self.add_probe_health(nested_rows, 0)
        self.add_probe_health(nested_rows, 1, new_span=child_span)
        nested_rows.enter(
            child_span,
            "generation_phase",
            "cleanroom-worldgen:chunk.populate_owned",
            self.generator,
        )
        nested_rows.returned("generation_phase")
        nested_rows.returned("generator_call")
        nested_bundle = self.normalize(
            nested_rows, completed=False, label="nested-preamble"
        )
        self.assertEqual(
            2,
            sum(
                record["record_type"] == "probe_health"
                for record in nested_bundle["records"]
            ),
        )

    def test_rejects_root_probe_health_preamble_tampering(self) -> None:
        root_span = "cleanroom-worldgen-span:root-preamble"

        intervening = self.started_rows("capture-root-preamble-intervening")
        self.add_probe_health(intervening, 0, new_span=root_span)
        self.add_probe_health(intervening, 1)
        intervening.enter(
            root_span,
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        with self.assertRaisesRegex(CaptureValidationError, "immediately followed"):
            self.normalize(intervening, completed=False, label="root-intervening")

        mismatched = self.started_rows("capture-root-preamble-mismatched")
        self.add_probe_health(mismatched, 0, new_span=root_span)
        mismatched.enter(
            root_span + ":different",
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        with self.assertRaisesRegex(CaptureValidationError, "does not match"):
            self.normalize(mismatched, completed=False, label="root-mismatched")

        wrong_thread = self.started_rows("capture-root-preamble-thread")
        self.add_probe_health(wrong_thread, 0, new_span=root_span)
        wrong_thread.enter(
            root_span,
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        wrong_thread.rows[-1]["order"].update(
            thread_name="Worldgen worker", thread_id=2, thread_sequence=0
        )
        with self.assertRaisesRegex(CaptureValidationError, "same thread"):
            self.normalize(wrong_thread, completed=False, label="root-thread")

        unfinished = self.started_rows("capture-root-preamble-end")
        self.add_probe_health(unfinished, 0, new_span=root_span)
        with self.assertRaisesRegex(CaptureValidationError, "end of capture"):
            self.normalize(unfinished, completed=False, label="root-end")

    def test_rejects_nested_probe_health_preamble_tampering(self) -> None:
        def nested_rows(capture_id: str) -> tuple[RawRows, dict[str, Any]]:
            rows = self.started_rows(capture_id)
            rows.enter(
                "cleanroom-worldgen-span:preamble-parent",
                "generator_call",
                "cleanroom-worldgen:cps.generate_chunk_call",
                self.generator,
            )
            pending = self.add_probe_health(
                rows, 0, new_span="cleanroom-worldgen-span:preamble-child"
            )
            return rows, pending

        wrong_parent, pending = nested_rows("capture-nested-preamble-parent")
        pending["causality"]["parent_span_id"] = None
        with self.assertRaisesRegex(CaptureValidationError, "parent.*current span"):
            self.normalize(wrong_parent, completed=False, label="nested-parent")

        wrong_trace, pending = nested_rows("capture-nested-preamble-trace")
        pending["causality"]["trace_id"] = "cleanroom-worldgen-trace:tampered"
        with self.assertRaisesRegex(CaptureValidationError, "changed.*trace or root"):
            self.normalize(wrong_trace, completed=False, label="nested-trace")

        mismatched_enter, _ = nested_rows("capture-nested-preamble-enter")
        mismatched_enter.enter(
            "cleanroom-worldgen-span:preamble-child",
            "generation_phase",
            "cleanroom-worldgen:chunk.populate_owned",
            self.generator,
        )
        mismatched_enter.rows[-1]["causality"]["root_trigger_id"] = (
            "cleanroom-worldgen-trigger:tampered"
        )
        with self.assertRaisesRegex(CaptureValidationError, "does not match"):
            self.normalize(mismatched_enter, completed=False, label="nested-enter")

    def test_missing_raw_stop_publishes_crash_residue_with_open_span(self) -> None:
        rows = RawRows("capture-crash-1")
        rows.add(
            "capture_control",
            {
                "control": "start",
                "capture_control_id": rows.capture_id + ":driver",
                "controller": "dedicated_server_fixture_v1",
                "requested_mode": "lossless-fixture",
                "world_seed_sha256": digest("123456789"),
                "route_order": "forward",
                "selector_sha256": digest("selector"),
                "route_sha256": digest("route"),
                "selected_chunks": ["64,64"],
            },
            self.workbench,
            scope=(0, None, None),
            state="entered",
        )
        rows.enter(
            "cleanroom-worldgen-span:crashed-generator",
            "generator_call",
            "cleanroom-worldgen:cps.generate_chunk_call",
            self.generator,
        )
        bundle = self.normalize(rows, completed=False, label="crash")
        validate_bundle(bundle)
        self.assertEqual("incomplete", bundle["publication"]["state"])
        self.assertEqual("process_crash", bundle["publication"]["crash_residue"]["reason"])
        self.assertEqual(1, len(bundle["summary"]["open_span_ids"]))
        self.assertIsNone(bundle["publication"]["completion_seal"])

    def test_actor_binding_uses_independent_final_transformed_class_dump(self) -> None:
        binder = StrictActorBinder(self.inventory, self.class_dumps)
        exact = binder.bind(self.generator)
        self.assertEqual("exact", exact["binding"])
        runtime_lead = dict(self.generator)
        runtime_lead["mod_id"] = None
        inferred_from_unique_inventory = binder.bind(runtime_lead)
        self.assertEqual("exact", inferred_from_unique_inventory["binding"])
        self.assertEqual(
            "synthetic_worldgen", inferred_from_unique_inventory["mod_id"]
        )
        conflicting = dict(self.generator)
        conflicting["transformed_class_sha256"] = digest("foreign-transform")
        self.assertNotEqual(
            conflicting["transformed_class_sha256"],
            self.class_dumps[self.generator["class_name"]],
        )
        bound_after_later_transformers = binder.bind(conflicting)
        self.assertEqual("exact", bound_after_later_transformers["binding"])
        self.assertEqual(
            self.class_dumps[self.generator["class_name"]],
            bound_after_later_transformers["transformed_class_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
