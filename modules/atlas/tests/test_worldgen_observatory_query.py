#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stderr
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "modules/crucible/src"))
sys.path.insert(0, str(ROOT / "modules/atlas/src"))

from workbench_atlas_worldgen import (  # noqa: E402
    admit_worldgen_bundle,
    first_divergence,
    which_handler_changed_event,
    who_wrote_block,
)
from workbench_atlas_worldgen.exact_suite import (  # noqa: E402
    DEFAULT_EVENT_BUS,
    ExactQuerySuiteError,
    run_exact_query_suite,
    select_event_span_id,
)
from workbench_crucible_observatory.bundle import (  # noqa: E402
    BundleBuilder,
    exact_actor,
    new_run,
    write_bundle,
)


CLI_PATH = ROOT / "modules/atlas/tools/query_worldgen_observatory.py"
CLI_SPEC = importlib.util.spec_from_file_location(
    "atlas_worldgen_query_cli_test_module",
    CLI_PATH,
)
assert CLI_SPEC is not None and CLI_SPEC.loader is not None
query_cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(query_cli)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fixture_bundle(
    label: str,
    *,
    rng_result: str = "4",
    incomplete: bool = False,
    capture_mode: str = "lossless-fixture",
    included_domains: tuple[str, ...] = ("block_states",),
    outside_rng_result: str | None = None,
    failed_probe_method: str | None = None,
    complete_event_dispatch: bool = True,
    interleaved_writes: bool = False,
    base_semantic_variant: str = "stone",
    other_scope_semantic: str | None = None,
    nested_event_dispatch: bool = False,
    distinct_terminal_actor: bool = False,
    exact_post_actor: bool = False,
    post_actor_transformed_variant: str = "event-bus-transformed-class",
) -> tuple[dict, str, str]:
    platform_digest = digest("cleanroom-0.6.8-alpha-candidate-lock")
    comparison_scope = {"dimension": 0, "chunks": [[0, 0]]}
    run = new_run(
        capture_mode=capture_mode,
        capture_plan_sha256=digest("lossless-selected-chunk-0-0"),
        fixture_id=f"crucible-fixture:{label}",
        fixture_sha256=digest("synthetic-query-fixture-v1"),
        environment={
            "minecraft_version": "1.12.2",
            "platform_profile_id": "workbench-platform:cleanroom:0.6.8-alpha",
            "platform_profile_sha256": platform_digest,
            "pack_profile_id": None,
            "pack_profile_sha256": None,
            "snapshot_id": "snapshot:query-test",
            "physical_side": "DEDICATED_SERVER",
            "runtime_java": "25.0.4+7",
            "mapping_namespace": "mcp-stable_39",
            "transformed_runtime_sha256": digest("synthetic-transformed-runtime"),
            "mod_set_sha256": digest("query-test-mod-set"),
            "configuration_set_sha256": digest("query-test-config"),
        },
        world={
            "world_instance_id": f"crucible-world:{label}",
            "world_seed_sha256": digest("-571123474424848392"),
            "world_type": "wb_observe",
            "generator_options_sha256": digest("{}"),
            "dimension_ids": [0],
        },
    )
    probe_actor = exact_actor(
        mod_id="workbench_worldgen_observer",
        code_source_sha256=digest("observer-code"),
        class_name="dev.workbench.observer.Probe",
        method_name="observe",
        method_descriptor="()V",
        mapping_namespace="mcp-stable_39",
        transformed_class_sha256=digest("probe-transformed-class"),
        workbench=True,
    )
    generator_actor = exact_actor(
        mod_id="workbench_worldgen_fixture",
        code_source_sha256=digest("fixture-code"),
        class_name="dev.workbench.worldgenobservatory.world.ObservingChunkGenerator",
        method_name="generateChunk",
        method_descriptor="(II)Lnet/minecraft/world/chunk/Chunk;",
        mapping_namespace="mcp-stable_39",
        transformed_class_sha256=digest("generator-transformed-class"),
    )
    handler_actor = exact_actor(
        mod_id="unrelated_event_handler",
        code_source_sha256=digest("handler-code"),
        class_name="example.UnrelatedTerrainHandler",
        method_name="onPopulate",
        method_descriptor="(Lnet/minecraftforge/event/terraingen/PopulateChunkEvent;)V",
        mapping_namespace="mcp-stable_39",
        transformed_class_sha256=digest("handler-transformed-class"),
    )
    forge_post_actor = exact_actor(
        mod_id="forge",
        code_source_sha256=digest("forge-code"),
        class_name="net.minecraftforge.fml.common.eventhandler.EventBus",
        method_name="post",
        method_descriptor=(
            "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z"
        ),
        mapping_namespace="mcp-stable_39",
        transformed_class_sha256=digest(post_actor_transformed_variant),
    )
    builder = BundleBuilder(
        run,
        selection_id="crucible-selection:chunk-0-0",
        actor=probe_actor,
        monotonic_ns=lambda: 0,
    )
    builder.start()
    for hook_id, target_class, target_method, target_descriptor in (
        (
            "worldgen-hook:chunk-primer-set-block-state",
            "net.minecraft.world.chunk.ChunkPrimer",
            "setBlockState",
            "(IIILnet/minecraft/block/state/IBlockState;)V",
        ),
        (
            "worldgen-hook:world-set-block-state",
            "net.minecraft.world.World",
            "setBlockState",
            "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;I)Z",
        ),
        (
            "worldgen-hook:chunk-set-block-state",
            "net.minecraft.world.chunk.Chunk",
            "setBlockState",
            "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)Lnet/minecraft/block/state/IBlockState;",
        ),
        (
            "worldgen-hook:event-bus-post",
            "net.minecraftforge.fml.common.eventhandler.EventBus",
            "post",
            "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
        ),
        (
            "worldgen-hook:event-listener-invoke",
            "net.minecraftforge.fml.common.eventhandler.EventBus",
            "post",
            "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
        ),
    ):
        builder.record(
            "probe_health",
            {
                "hook_id": hook_id,
                "health_state": (
                    "failed" if target_method == failed_probe_method else "reached"
                ),
                "target_class": target_class,
                "target_method": target_method,
                "target_descriptor": target_descriptor,
                "original_class_sha256": digest("original:" + target_class),
                "transformed_class_sha256": digest("transformed:" + target_class),
                "expected_injection_count": 1,
                "observed_injection_count": (
                    0 if target_method == failed_probe_method else 1
                ),
            },
            actor=probe_actor,
        )
    phase_span = builder.enter_span(
        span_kind="generation_phase",
        operation_id="worldgen-stage:010-base",
        arguments={"chunk": [0, 0]},
        dimension_id=0,
        chunk=(0, 0),
        actor=generator_actor,
    )
    builder.record(
        "rng_observation",
        {
            "detail": "call",
            "stream_id": "worldgen-rng:base-height",
            "algorithm_class": "fixture.SplitMix64",
            "operation": "nextInt",
            "call_ordinal": 0,
            "parameters_sha256": digest("bound=4"),
            "result_sha256": digest(rng_result),
            "rolling_digest": digest("rolling:" + rng_result),
        },
        dimension_id=0,
        chunk=(0, 0),
        actor=generator_actor,
    )
    builder.record(
        "block_write",
        {
            "write_chain_id": "worldgen-write:base-0-0-1-64-1",
            "channel": "chunk_primer",
            "position": [1, 64, 1],
            "generation_chunk": {"x": 0, "z": 0},
            "target_chunk": {"x": 0, "z": 0},
            "before_state_sha256": digest("minecraft:air"),
            "after_state_sha256": digest("minecraft:stone"),
            "flags": None,
            "terminal": True,
        },
        dimension_id=0,
        chunk=(0, 0),
        actor=generator_actor,
    )
    builder.checkpoint(
        checkpoint_id="worldgen-checkpoint:0-0-base",
        stage_id="worldgen-stage:010-base",
        canonicalization_id="worldgen-canonicalizer:block-state-v1",
        semantic_state={"1,64,1": "minecraft:" + base_semantic_variant},
        comparison_scope=comparison_scope,
        dimension_id=0,
        chunk=(0, 0),
        included_domains=included_domains,
        actor=probe_actor,
    )
    primary_comparison_scope_sha256 = builder.semantic_fingerprints[-1]["scope"][
        "comparison_scope_sha256"
    ]
    if other_scope_semantic is not None:
        builder.checkpoint(
            checkpoint_id="worldgen-checkpoint:0-0-other-scope",
            stage_id="worldgen-stage:010-base",
            canonicalization_id="worldgen-canonicalizer:block-state-v1",
            semantic_state={"other": other_scope_semantic},
            comparison_scope={"dimension": 0, "chunks": [[0, 0]], "lane": "other"},
            dimension_id=0,
            chunk=(0, 0),
            included_domains=included_domains,
            actor=probe_actor,
        )
    builder.return_span(
        phase_span,
        span_kind="generation_phase",
        result={"chunk": [0, 0]},
        dimension_id=0,
        chunk=(0, 0),
        actor=generator_actor,
    )
    if outside_rng_result is not None:
        outside_span = builder.enter_span(
            span_kind="generation_phase",
            operation_id="worldgen-stage:010-base",
            arguments={"chunk": [1, 0]},
            dimension_id=0,
            chunk=(1, 0),
            actor=generator_actor,
        )
        builder.record(
            "rng_observation",
            {
                "detail": "call",
                "stream_id": "worldgen-rng:base-height",
                "algorithm_class": "fixture.SplitMix64",
                "operation": "nextInt",
                "call_ordinal": 0,
                "parameters_sha256": digest("bound=4"),
                "result_sha256": digest(outside_rng_result),
                "rolling_digest": digest("outside-rolling:" + outside_rng_result),
            },
            dimension_id=0,
            chunk=(1, 0),
            actor=generator_actor,
        )
        builder.return_span(
            outside_span,
            span_kind="generation_phase",
            result={"chunk": [1, 0]},
            dimension_id=0,
            chunk=(1, 0),
            actor=generator_actor,
        )

    event_post_actor = forge_post_actor if exact_post_actor else probe_actor
    event_span = builder.enter_span(
        span_kind="feature",
        operation_id="forge-event:populate-pre",
        arguments={"chunk": [0, 0]},
        dimension_id=0,
        chunk=(0, 0),
        actor=event_post_actor,
    )
    initial_event_state = digest("cancel=false;result=DEFAULT")
    changed_event_state = digest("cancel=false;result=ALLOW")
    if complete_event_dispatch:
        builder.record(
            "event_dispatch",
            {
                "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
                "bus_id": "forge-bus:event",
                "boundary": "post_enter",
                "listener_ordinal": None,
                "state_before_sha256": initial_event_state,
                "state_after_sha256": initial_event_state,
                "cancelled_before": False,
                "cancelled_after": False,
            },
            dimension_id=0,
            chunk=(0, 0),
            actor=event_post_actor,
        )
        listener_span = builder.enter_span(
            span_kind="event_listener",
            operation_id="forge-listener:unrelated-populate-handler",
            arguments={"listener_ordinal": 0},
            dimension_id=0,
            chunk=(0, 0),
            actor=handler_actor,
        )
        if nested_event_dispatch:
            nested_span = builder.enter_span(
                span_kind="feature",
                operation_id="forge-event:nested-post",
                arguments={"nested": True},
                dimension_id=0,
                chunk=(0, 0),
                actor=probe_actor,
            )
            nested_state = digest("nested-event-state")
            for boundary in ("post_enter", "post_return"):
                builder.record(
                    "event_dispatch",
                    {
                        "event_class": "example.NestedWorldgenEvent",
                        "bus_id": "forge-bus:event",
                        "boundary": boundary,
                        "listener_ordinal": None,
                        "state_before_sha256": nested_state,
                        "state_after_sha256": nested_state,
                        "cancelled_before": False,
                        "cancelled_after": False,
                    },
                    dimension_id=0,
                    chunk=(0, 0),
                    actor=probe_actor,
                )
            builder.return_span(
                nested_span,
                span_kind="feature",
                result={"nested": True},
                dimension_id=0,
                chunk=(0, 0),
                actor=probe_actor,
            )
        builder.record(
            "event_dispatch",
            {
                "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
                "bus_id": "forge-bus:event",
                "boundary": "listener_enter",
                "listener_ordinal": 0,
                "state_before_sha256": initial_event_state,
                "state_after_sha256": initial_event_state,
                "cancelled_before": False,
                "cancelled_after": False,
            },
            dimension_id=0,
            chunk=(0, 0),
            actor=handler_actor,
        )
    builder.record(
        "event_dispatch",
        {
            "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
            "bus_id": "forge-bus:event",
            "boundary": "listener_return",
            "listener_ordinal": 0,
            "state_before_sha256": initial_event_state,
            "state_after_sha256": changed_event_state,
            "cancelled_before": False,
            "cancelled_after": False,
        },
        dimension_id=0,
        chunk=(0, 0),
        actor=handler_actor,
    )
    if complete_event_dispatch:
        builder.return_span(
            listener_span,
            span_kind="event_listener",
            result={"cancelled": False, "result": "ALLOW"},
            dimension_id=0,
            chunk=(0, 0),
            actor=handler_actor,
        )
        builder.record(
            "event_dispatch",
            {
                "event_class": "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre",
                "bus_id": "forge-bus:event",
                "boundary": "post_return",
                "listener_ordinal": None,
                "state_before_sha256": changed_event_state,
                "state_after_sha256": changed_event_state,
                "cancelled_before": False,
                "cancelled_after": False,
            },
            dimension_id=0,
            chunk=(0, 0),
            actor=event_post_actor,
        )
    builder.return_span(
        event_span,
        span_kind="feature",
        result={"cancelled": False, "result": "ALLOW"},
        dimension_id=0,
        chunk=(0, 0),
        actor=event_post_actor,
    )

    decorator_span = builder.enter_span(
        span_kind="feature",
        operation_id="forge-world-generator:unrelated-synthetic",
        arguments={"chunk": [0, 0]},
        dimension_id=0,
        chunk=(0, 0),
        actor=handler_actor,
    )
    chain = "worldgen-write:synthetic-0-0-3-65-3"
    for channel, terminal in (("world_api", False), ("chunk_storage", True)):
        builder.record(
            "block_write",
            {
                "write_chain_id": chain,
                "channel": channel,
                "position": [3, 65, 3],
                "generation_chunk": {"x": 0, "z": 0},
                "target_chunk": {"x": 0, "z": 0},
                "before_state_sha256": digest("minecraft:air"),
                "after_state_sha256": digest("minecraft:gold_block"),
                "flags": 2,
                "terminal": terminal,
            },
            dimension_id=0,
            chunk=(0, 0),
            actor=(
                generator_actor
                if distinct_terminal_actor and terminal
                else handler_actor
            ),
        )
    if interleaved_writes:
        interleaved_position = [4, 66, 4]
        common = {
            "position": interleaved_position,
            "generation_chunk": {"x": 0, "z": 0},
            "target_chunk": {"x": 0, "z": 0},
            "before_state_sha256": digest("minecraft:air"),
            "after_state_sha256": digest("minecraft:diamond_block"),
            "flags": 2,
        }
        for write_chain_id, channel, terminal, actor in (
            ("worldgen-write:interleaved-a", "world_api", False, handler_actor),
            ("worldgen-write:interleaved-b", "world_api", False, generator_actor),
            ("worldgen-write:interleaved-b", "chunk_storage", True, generator_actor),
            ("worldgen-write:interleaved-a", "chunk_storage", True, handler_actor),
        ):
            builder.record(
                "block_write",
                {
                    "write_chain_id": write_chain_id,
                    "channel": channel,
                    **common,
                    "terminal": terminal,
                },
                dimension_id=0,
                chunk=(0, 0),
                actor=actor,
            )
    builder.return_span(
        decorator_span,
        span_kind="feature",
        result={"placed": True},
        dimension_id=0,
        chunk=(0, 0),
        actor=handler_actor,
    )
    builder.checkpoint(
        checkpoint_id="worldgen-checkpoint:0-0-final",
        stage_id="worldgen-stage:090-final",
        canonicalization_id="worldgen-canonicalizer:block-state-v1",
        semantic_state={
            "1,64,1": "minecraft:stone",
            "3,65,3": "minecraft:gold_block",
        },
        comparison_scope=comparison_scope,
        dimension_id=0,
        chunk=(0, 0),
        included_domains=included_domains,
        actor=probe_actor,
    )
    if incomplete:
        dangling = builder.enter_span(
            span_kind="generator_call",
            operation_id="worldgen-call:crash-target",
            arguments={},
            dimension_id=0,
            chunk=(0, 0),
            actor=generator_actor,
        )
        assert dangling
        return (
            builder.incomplete(
                reason="process_crash",
                recoverable=True,
                diagnostic={"signal": "synthetic"},
            ),
            event_span,
            primary_comparison_scope_sha256,
        )
    bundle = builder.complete()
    return (
        bundle,
        event_span,
        primary_comparison_scope_sha256,
    )


class WorldgenObservatoryQueryTests(unittest.TestCase):
    def test_admitted_bundle_is_not_revalidated_for_each_query(self) -> None:
        bundle, event_span, scope = fixture_bundle("admitted-once")
        admitted = admit_worldgen_bundle(bundle)
        with patch(
            "workbench_atlas_worldgen.query.validate_bundle"
        ) as validator:
            self.assertEqual(
                "answered",
                who_wrote_block(
                    admitted,
                    dimension_id=0,
                    x=3,
                    y=65,
                    z=3,
                )["status"],
            )
            self.assertEqual(
                "answered",
                which_handler_changed_event(
                    admitted,
                    event_span_id=event_span,
                )["status"],
            )
            self.assertEqual(
                "equal",
                first_divergence(
                    admitted,
                    admitted,
                    comparison_scope_sha256=scope,
                )["status"],
            )
        validator.assert_not_called()

    def test_who_wrote_block_collapses_nested_write_channels(self) -> None:
        bundle, _, _ = fixture_bundle("writes")
        answer = who_wrote_block(bundle, dimension_id=0, x=3, y=65, z=3)
        self.assertEqual("answered", answer["status"])
        self.assertEqual(1, len(answer["result"]["write_chains"]))
        self.assertEqual(
            ["world_api", "chunk_storage"],
            [
                item["channel"]
                for item in answer["result"]["write_chains"][0]["channels"]
            ],
        )
        self.assertEqual(
            "unrelated_event_handler",
            answer["result"]["final_writer"]["actor"]["mod_id"],
        )

    def test_handler_query_finds_exact_state_mutation(self) -> None:
        bundle, event_span, _ = fixture_bundle("event")
        answer = which_handler_changed_event(bundle, event_span_id=event_span)
        self.assertEqual("answered", answer["status"])
        self.assertEqual(1, answer["result"]["changed_handler_count"])
        self.assertTrue(answer["result"]["handlers"][0]["changed"])
        self.assertEqual(
            "unrelated_event_handler",
            answer["result"]["handlers"][0]["actor"]["mod_id"],
        )

    def test_handler_query_accepts_the_exact_forge_event_bus_post_actor(self) -> None:
        bundle, event_span, _ = fixture_bundle(
            "event-exact-forge-post",
            exact_post_actor=True,
        )
        answer = which_handler_changed_event(bundle, event_span_id=event_span)
        self.assertEqual("answered", answer["status"])
        self.assertEqual(1, answer["result"]["changed_handler_count"])

    def test_a_a_is_equal_and_rng_mutation_is_first_divergence(self) -> None:
        left, _, scope = fixture_bundle("left")
        same, _, _ = fixture_bundle("right")
        equal = first_divergence(
            left,
            same,
            comparison_scope_sha256=scope,
        )
        self.assertEqual("equal", equal["status"])

        changed, _, _ = fixture_bundle(
            "changed",
            rng_result="5",
            base_semantic_variant="dirt",
        )
        divergence = first_divergence(
            left,
            changed,
            comparison_scope_sha256=scope,
        )
        self.assertEqual("diverged", divergence["status"])
        first = divergence["result"]["first_difference"]
        self.assertEqual("rng_observation", first["record_type"])
        self.assertEqual("worldgen-stage:010-base", first["stage_id"])

    def test_divergence_does_not_treat_exact_transform_receipts_as_semantics(
        self,
    ) -> None:
        left, _, scope = fixture_bundle(
            "transform-left",
            exact_post_actor=True,
            post_actor_transformed_variant="mixin-session-left",
        )
        right, _, _ = fixture_bundle(
            "transform-right",
            exact_post_actor=True,
            post_actor_transformed_variant="mixin-session-right",
        )
        answer = first_divergence(
            left,
            right,
            comparison_scope_sha256=scope,
        )
        self.assertEqual("equal", answer["status"])

    def test_divergence_is_bounded_to_matching_fingerprint_cohort(self) -> None:
        left, _, scope = fixture_bundle("scope-left", outside_rng_result="4")
        right, _, _ = fixture_bundle("scope-right", outside_rng_result="5")
        answer = first_divergence(
            left,
            right,
            comparison_scope_sha256=scope,
        )
        self.assertEqual("equal", answer["status"])

        mismatched_domains, _, _ = fixture_bundle(
            "scope-domains",
            included_domains=("biomes",),
        )
        answer = first_divergence(
            left,
            mismatched_domains,
            comparison_scope_sha256=scope,
        )
        self.assertEqual("unavailable", answer["status"])

        overlap_left, _, overlap_scope = fixture_bundle(
            "overlap-left",
            other_scope_semantic="left",
        )
        overlap_right, _, _ = fixture_bundle(
            "overlap-right",
            other_scope_semantic="right",
        )
        answer = first_divergence(
            overlap_left,
            overlap_right,
            comparison_scope_sha256=overlap_scope,
        )
        self.assertEqual("equal", answer["status"])

    def test_lossy_modes_and_unreached_probes_cannot_close_queries(self) -> None:
        trace, _, _ = fixture_bundle("trace", capture_mode="trace")
        answer = who_wrote_block(trace, dimension_id=0, x=3, y=65, z=3)
        self.assertEqual("unavailable", answer["status"])

        failed_probe, _, _ = fixture_bundle(
            "probe-failed",
            failed_probe_method="setBlockState",
        )
        answer = who_wrote_block(
            failed_probe,
            dimension_id=0,
            x=3,
            y=65,
            z=3,
        )
        self.assertEqual("unavailable", answer["status"])

    def test_handler_query_rejects_an_unclosed_dispatch(self) -> None:
        bundle, event_span, _ = fixture_bundle(
            "event-unclosed",
            complete_event_dispatch=False,
        )
        answer = which_handler_changed_event(bundle, event_span_id=event_span)
        self.assertEqual("invalid-evidence", answer["status"])

    def test_handler_query_isolated_nested_posts_and_binds_listener_actor(self) -> None:
        nested, nested_event_span, _ = fixture_bundle(
            "event-nested",
            nested_event_dispatch=True,
        )
        answer = which_handler_changed_event(
            nested,
            event_span_id=nested_event_span,
        )
        self.assertEqual("answered", answer["status"])
        self.assertEqual(1, answer["result"]["changed_handler_count"])

        spoofed = deepcopy(nested)
        spoofed_terminal = next(
            record
            for record in spoofed["records"]
            if record["record_type"] == "event_dispatch"
            and record["payload"]["boundary"] == "listener_return"
        )
        spoofed_terminal["actor"]["mod_id"] = "spoof_listener"
        spoofed_terminal["actor"]["code_source_sha256"] = digest("spoof-code")
        answer = which_handler_changed_event(
            spoofed,
            event_span_id=nested_event_span,
        )
        self.assertEqual("invalid-evidence", answer["status"])

        contradictory = deepcopy(nested)
        listener_terminal = next(
            record
            for record in contradictory["records"]
            if record["record_type"] == "event_dispatch"
            and record["payload"]["boundary"] == "listener_return"
        )
        listener_terminal["payload"]["boundary"] = "listener_throw"
        listener_terminal["outcome"]["state"] = "threw"
        listener_terminal["outcome"][
            "exception_class"
        ] = "java.lang.IllegalStateException"
        listener_terminal["outcome"]["exception_message_sha256"] = digest(
            "listener failure"
        )
        answer = which_handler_changed_event(
            contradictory,
            event_span_id=nested_event_span,
        )
        self.assertEqual("invalid-evidence", answer["status"])
        self.assertIn("contradicts", " ".join(answer["limitations"]))

    def test_final_writer_uses_latest_terminal_across_interleaved_chains(self) -> None:
        bundle, _, _ = fixture_bundle(
            "interleaved",
            interleaved_writes=True,
        )
        answer = who_wrote_block(bundle, dimension_id=0, x=4, y=66, z=4)
        self.assertEqual("answered", answer["status"])
        self.assertEqual(
            "worldgen-write:interleaved-a",
            answer["result"]["final_writer"]["write_chain_id"],
        )
        self.assertEqual(
            "unrelated_event_handler",
            answer["result"]["final_writer"]["actor"]["mod_id"],
        )

    def test_writer_separates_logical_initiator_from_terminal_storage_actor(self) -> None:
        bundle, _, _ = fixture_bundle(
            "writer-boundaries",
            distinct_terminal_actor=True,
        )
        answer = who_wrote_block(bundle, dimension_id=0, x=3, y=65, z=3)
        self.assertEqual("answered", answer["status"])
        final_writer = answer["result"]["final_writer"]
        self.assertEqual(
            "unrelated_event_handler",
            final_writer["initiating_actor"]["mod_id"],
        )
        self.assertEqual(
            "workbench_worldgen_fixture",
            final_writer["terminal_actor"]["mod_id"],
        )
        self.assertEqual(final_writer["initiating_actor"], final_writer["actor"])

    def test_writer_causal_path_retains_actor_and_cites_span_enters(self) -> None:
        bundle, _, _ = fixture_bundle(
            "writer-causal-path",
            distinct_terminal_actor=True,
        )
        answer = who_wrote_block(bundle, dimension_id=0, x=3, y=65, z=3)
        self.assertEqual("answered", answer["status"])
        path = answer["result"]["final_writer"]["causal_path"]
        self.assertTrue(path)
        self.assertTrue(all("actor" in step for step in path))
        self.assertTrue(
            {step["enter_ordinal"] for step in path}
            <= set(answer["evidence_ordinals"])
        )
        self.assertIn(
            "unrelated_event_handler",
            {step["actor"]["mod_id"] for step in path},
        )

    def test_single_query_cli_writes_the_answer_and_rejects_input_alias(self) -> None:
        bundle, _, _ = fixture_bundle("single-query-cli")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "bundle.json"
            answer_path = root / "answer.json"
            write_bundle(bundle_path, bundle)
            exit_code = query_cli.main(
                [
                    "--bundle",
                    str(bundle_path),
                    "--output",
                    str(answer_path),
                    "who-wrote-block",
                    "--dimension-id",
                    "0",
                    "--position",
                    "3",
                    "65",
                    "3",
                ]
            )
            self.assertEqual(0, exit_code)
            self.assertEqual("answered", json.loads(answer_path.read_text())["status"])
            with redirect_stderr(io.StringIO()):
                self.assertEqual(
                    2,
                    query_cli.main(
                        [
                            "--bundle",
                            str(bundle_path),
                            "--output",
                            str(bundle_path),
                            "who-wrote-block",
                            "--dimension-id",
                            "0",
                            "--position",
                            "3",
                            "65",
                            "3",
                        ]
                    ),
                )

    def test_block_absence_is_unavailable_without_selector_closure(self) -> None:
        bundle, _, _ = fixture_bundle("write-absence")
        answer = who_wrote_block(bundle, dimension_id=0, x=7, y=70, z=7)
        self.assertEqual("unavailable", answer["status"])
        self.assertIn("machine-readable selector", " ".join(answer["limitations"]))

    def test_incomplete_and_tampered_captures_never_answer(self) -> None:
        incomplete, _, _ = fixture_bundle("crash", incomplete=True)
        answer = who_wrote_block(incomplete, dimension_id=0, x=3, y=65, z=3)
        self.assertEqual("incomplete-evidence", answer["status"])

        complete, _, _ = fixture_bundle("tamper")
        corrupt = deepcopy(complete)
        rng_record = next(
            record
            for record in corrupt["records"]
            if record["record_type"] == "rng_observation"
        )
        rng_record["payload"]["result_sha256"] = digest("tampered")
        answer = who_wrote_block(corrupt, dimension_id=0, x=3, y=65, z=3)
        self.assertEqual("invalid-evidence", answer["status"])

        injected_identity = deepcopy(complete)
        injected_identity["publication"]["completion_seal"][
            "capture_id"
        ] = "attacker-controlled"
        answer = who_wrote_block(
            injected_identity,
            dimension_id=0,
            x=3,
            y=65,
            z=3,
        )
        self.assertEqual("invalid-evidence", answer["status"])
        self.assertRegex(
            answer["capture_ids"][0],
            r"^crucible-worldgen-invalid:sha256:[0-9a-f]{64}$",
        )

    def test_exact_suite_uses_sequential_spools_and_rejects_crash_for_all_queries(
        self,
    ) -> None:
        primary, event_span, scope = fixture_bundle("suite-primary")
        comparison, _, _ = fixture_bundle("suite-comparison")
        crash, _, _ = fixture_bundle("suite-crash", incomplete=True)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary_path = root / "primary.json"
            comparison_path = root / "comparison.json"
            crash_path = root / "crash.json"
            write_bundle(primary_path, primary)
            write_bundle(comparison_path, comparison)
            write_bundle(crash_path, crash)
            report = run_exact_query_suite(
                primary_bundle_path=primary_path,
                comparison_bundle_path=comparison_path,
                crash_bundle_path=crash_path,
                dimension_id=0,
                block_position=(3, 65, 3),
                event_span_id=event_span,
                comparison_checkpoint_id="worldgen-checkpoint:0-0-base",
                comparison_chunk=(0, 0),
                expected_divergence_status="equal",
                expected_writer_mod_id="unrelated_event_handler",
                expected_handler_mod_id="unrelated_event_handler",
                scratch_directory=root / "scratch",
            )
        self.assertTrue(report["passed"])
        self.assertEqual(
            "equal",
            report["answers"]["first_divergence"]["status"],
        )
        self.assertEqual(scope, report["inputs"]["comparison_scope_sha256"])
        self.assertEqual(
            {"incomplete-evidence"},
            {
                answer["status"]
                for answer in report["crash_rejections"].values()
            },
        )
        self.assertEqual(
            "ephemeral-derived-non-authoritative",
            report["authority"]["sorting_spool"],
        )

    def test_event_selector_requires_a_bounded_exact_occurrence(self) -> None:
        bundle, event_span, _ = fixture_bundle("event-selector")
        admitted = admit_worldgen_bundle(bundle)
        self.assertEqual(
            event_span,
            select_event_span_id(
                admitted,
                event_class=(
                    "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre"
                ),
                bus_id=DEFAULT_EVENT_BUS.replace("ore_gen_bus", "event"),
                dimension_id=0,
                chunk_x=0,
                chunk_z=0,
                occurrence=0,
            ),
        )
        with self.assertRaisesRegex(ExactQuerySuiteError, "resolved 1 occurrences"):
            select_event_span_id(
                admitted,
                event_class=(
                    "net.minecraftforge.event.terraingen.PopulateChunkEvent$Pre"
                ),
                bus_id="forge-bus:event",
                dimension_id=0,
                chunk_x=0,
                chunk_z=0,
                occurrence=1,
            )

    def test_exact_suite_spool_preserves_the_first_changed_occurrence(self) -> None:
        primary, event_span, scope = fixture_bundle("suite-divergence-primary")
        comparison, _, _ = fixture_bundle(
            "suite-divergence-comparison",
            rng_result="5",
            base_semantic_variant="dirt",
        )
        crash, _, _ = fixture_bundle("suite-divergence-crash", incomplete=True)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "primary": root / "primary.json",
                "comparison": root / "comparison.json",
                "crash": root / "crash.json",
            }
            for label, bundle in (
                ("primary", primary),
                ("comparison", comparison),
                ("crash", crash),
            ):
                write_bundle(paths[label], bundle)
            report = run_exact_query_suite(
                primary_bundle_path=paths["primary"],
                comparison_bundle_path=paths["comparison"],
                crash_bundle_path=paths["crash"],
                dimension_id=0,
                block_position=(3, 65, 3),
                event_span_id=event_span,
                comparison_scope_sha256=scope,
                expected_divergence_status="diverged",
                scratch_directory=root / "scratch",
            )
        self.assertTrue(report["passed"])
        first = report["answers"]["first_divergence"]["result"][
            "first_difference"
        ]
        self.assertEqual("rng_observation", first["record_type"])


if __name__ == "__main__":
    unittest.main()
