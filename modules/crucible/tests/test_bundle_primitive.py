#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    BundleBuilder,
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
    load_bundle,
    new_run,
    unbound_actor,
    validate_bundle,
    write_bundle,
)
from workbench_crucible_observatory.bundle import (  # noqa: E402
    _CANONICAL_JSON_BUFFER_CHAR_LIMIT,
    _ValidatedBundlePublication,
    _iter_canonical_json_bytes,
    _new_validated_bundle_publication,
    _write_validated_bundle,
)
from workbench_crucible_observatory import bundle as bundle_module  # noqa: E402


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_manifest(label: str = "test") -> dict:
    return new_run(
        capture_mode="lossless-fixture",
        capture_plan_sha256=digest("plan"),
        fixture_id=f"crucible-fixture:{label}",
        fixture_sha256=digest("fixture"),
        environment={
            "minecraft_version": "1.12.2",
            "platform_profile_id": "workbench-platform:cleanroom:0.6.8-alpha",
            "platform_profile_sha256": digest("candidate"),
            "pack_profile_id": None,
            "pack_profile_sha256": None,
            "snapshot_id": "snapshot:bundle-test",
            "physical_side": "DEDICATED_SERVER",
            "runtime_java": "25.0.4+7",
            "mapping_namespace": "mcp-stable_39",
            "transformed_runtime_sha256": digest("runtime"),
            "mod_set_sha256": digest("mods"),
            "configuration_set_sha256": digest("config"),
        },
        world={
            "world_instance_id": f"crucible-world:{label}",
            "world_seed_sha256": digest("1234"),
            "world_type": "wb_observe",
            "generator_options_sha256": digest("{}"),
            "dimension_ids": [0],
        },
    )


def completed_bundle() -> dict:
    builder = BundleBuilder(
        run_manifest(),
        selection_id="crucible-selection:test",
        monotonic_ns=lambda: 7,
    )
    builder.start()
    span = builder.enter_span(
        span_kind="generator_call",
        operation_id="worldgen-call:generate-chunk",
        arguments={"x": 0, "z": 0},
        dimension_id=0,
        chunk=(0, 0),
    )
    builder.checkpoint(
        checkpoint_id="worldgen-checkpoint:0-0-base",
        stage_id="worldgen-stage:010-base",
        canonicalization_id="worldgen-canonicalizer:block-state-v1",
        semantic_state={"0,64,0": "minecraft:stone"},
        comparison_scope={"dimension": 0, "chunks": [[0, 0]]},
        dimension_id=0,
        chunk=(0, 0),
        included_domains=["block_states"],
    )
    builder.return_span(
        span,
        span_kind="generator_call",
        result={"generated": True},
        dimension_id=0,
        chunk=(0, 0),
    )
    return builder.complete()


class BundlePrimitiveTests(unittest.TestCase):
    def test_canonical_encoding_matches_compact_sorted_json(self) -> None:
        value = {"z": ["snowman ☃", 1.25], "a": {"ok": True}}
        expected = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertEqual(expected, canonical_json_bytes(value))

    def test_canonical_encoding_batches_without_changing_nested_unicode_bytes(self) -> None:
        row = {
            "emoji": "snowman ☃ rocket 🚀",
            "nested": [{"enabled": True, "value": None}, [1, 2, 3]],
        }
        value = {
            "rows": [row for _ in range(18_000)],
            "suffix": "boundary π",
        }
        expected = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertGreater(len(expected), _CANONICAL_JSON_BUFFER_CHAR_LIMIT)

        chunks = list(_iter_canonical_json_bytes(value))

        self.assertGreater(len(chunks), 1)
        self.assertEqual(expected, b"".join(chunks))
        self.assertEqual(
            hashlib.sha256(expected).hexdigest(),
            canonical_json_sha256(value),
        )

    def test_internal_validated_writer_is_byte_identical_and_digests_its_stream(self) -> None:
        bundle = completed_bundle()
        publication = _new_validated_bundle_publication(
            bundle,
            require_completed=True,
        )
        expected = canonical_json_bytes(bundle) + b"\n"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "validated-bundle.json"

            receipt = _write_validated_bundle(path, publication)

            self.assertEqual(expected, path.read_bytes())
            self.assertEqual(
                hashlib.sha256(expected[:-1]).hexdigest(),
                receipt.canonical_json_sha256,
            )
            self.assertEqual(hashlib.sha256(expected).hexdigest(), receipt.file_sha256)
            self.assertEqual(len(expected), receipt.byte_count)
            self.assertEqual(bundle["run"]["run_id"], receipt.run_id)
            self.assertEqual("completed", receipt.publication_state)
            self.assertEqual(
                bundle["publication"]["completion_seal"]["capture_id"],
                receipt.publication_id,
            )
            self.assertEqual(len(bundle["records"]), receipt.record_count)

    def test_internal_validated_writer_rejects_forgery_and_preserves_target_on_failure(self) -> None:
        bundle = completed_bundle()
        with self.assertRaisesRegex(CaptureValidationError, "capability"):
            _ValidatedBundlePublication(bundle, capability=object())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "validated-bundle.json"
            path.write_bytes(b"prior-valid-output\n")
            with self.assertRaisesRegex(CaptureValidationError, "capability"):
                _write_validated_bundle(path, bundle)  # type: ignore[arg-type]
            self.assertEqual(b"prior-valid-output\n", path.read_bytes())

            publication = _new_validated_bundle_publication(
                bundle,
                require_completed=True,
            )

            def fail_after_one_chunk(_value: Any) -> Any:
                yield b'{"partial":'
                raise OSError("injected stream failure")

            with mock.patch.object(
                bundle_module,
                "_iter_canonical_json_bytes",
                side_effect=fail_after_one_chunk,
            ):
                with self.assertRaisesRegex(OSError, "injected stream failure"):
                    _write_validated_bundle(path, publication)

            self.assertEqual(b"prior-valid-output\n", path.read_bytes())
            self.assertEqual([], list(path.parent.glob(path.name + ".*.tmp")))

    def test_scalable_publish_can_transfer_the_record_graph(self) -> None:
        builder = BundleBuilder(
            run_manifest("transfer"),
            selection_id="crucible-selection:test",
            monotonic_ns=lambda: 5,
            copy_on_publish=False,
        )
        builder.start()
        builder.checkpoint(
            checkpoint_id="worldgen-checkpoint:transfer",
            stage_id="worldgen-stage:010-base",
            canonicalization_id="worldgen-canonicalizer:block-state-v1",
            semantic_state={},
            comparison_scope={"dimension": 0, "chunks": [[0, 0]]},
            dimension_id=0,
            chunk=(0, 0),
            included_domains=["block_states"],
        )
        retained_records = builder.records
        bundle = builder.complete()
        self.assertIs(retained_records, bundle["records"])
        validate_bundle(bundle, require_completed=True)

    def test_listener_validation_does_not_rescan_records_per_span(self) -> None:
        builder = BundleBuilder(
            run_manifest("listener-index"),
            selection_id="crucible-selection:test",
            monotonic_ns=lambda: 6,
        )
        builder.start()
        for index in range(40):
            span = builder.enter_span(
                span_kind="event_listener",
                operation_id=f"forge-listener:test-{index}",
                arguments={"ordinal": index},
                dimension_id=0,
                chunk=(0, 0),
            )
            state = digest(f"state-{index}")
            builder.record(
                "event_dispatch",
                {
                    "event_class": "net.minecraftforge.event.terraingen.OreGenEvent",
                    "bus_id": "forge-bus:ore-gen",
                    "boundary": "listener_return",
                    "listener_ordinal": index,
                    "state_before_sha256": state,
                    "state_after_sha256": state,
                    "cancelled_before": False,
                    "cancelled_after": False,
                },
                dimension_id=0,
                chunk=(0, 0),
            )
            builder.return_span(
                span,
                span_kind="event_listener",
                result={},
                dimension_id=0,
                chunk=(0, 0),
            )
        builder.checkpoint(
            checkpoint_id="worldgen-checkpoint:listener-index",
            stage_id="worldgen-stage:010-base",
            canonicalization_id="worldgen-canonicalizer:block-state-v1",
            semantic_state={},
            comparison_scope={"dimension": 0, "chunks": [[0, 0]]},
            dimension_id=0,
            chunk=(0, 0),
            included_domains=["block_states"],
        )
        bundle = builder.complete()

        class CountingRecords(list):
            iterations = 0

            def __iter__(self):  # type: ignore[override]
                self.iterations += 1
                return super().__iter__()

        records = CountingRecords(bundle["records"])
        bundle["records"] = records
        validate_bundle(bundle, require_completed=True)
        self.assertLess(records.iterations, 20)

    def test_builder_seals_balanced_zero_drop_capture(self) -> None:
        bundle = completed_bundle()
        validate_bundle(bundle, require_completed=True)
        self.assertEqual("completed", bundle["publication"]["state"])
        self.assertEqual([], bundle["summary"]["open_span_ids"])
        self.assertEqual(0, bundle["summary"]["dropped_record_count"])
        self.assertTrue(
            bundle["publication"]["completion_seal"]["sealed_after_stop_record"]
        )

    def test_component_tampering_invalidates_seal(self) -> None:
        bundle = completed_bundle()
        tampered = deepcopy(bundle)
        tampered["records"][2]["payload"]["semantic_state_sha256"] = digest(
            "tampered"
        )
        with self.assertRaisesRegex(
            CaptureValidationError,
            "fingerprint semantic digest mismatch",
        ):
            validate_bundle(tampered, require_completed=True)

    def test_closed_schema_rejects_undeclared_payload_fields(self) -> None:
        tampered = deepcopy(completed_bundle())
        checkpoint = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["payload"]["undeclared"] = True
        with self.assertRaisesRegex(
            CaptureValidationError,
            "closed capture schema violation.*Additional properties",
        ):
            validate_bundle(tampered)

    def test_unsealed_summary_fields_are_still_deterministic(self) -> None:
        tampered = deepcopy(completed_bundle())
        tampered["summary"]["coverage_state"] = "sampled"
        with self.assertRaisesRegex(
            CaptureValidationError,
            "summary coverage state is not derivable",
        ):
            validate_bundle(tampered)

        tampered = deepcopy(completed_bundle())
        tampered["summary"]["limitations"] = ["invented after sealing"]
        with self.assertRaisesRegex(
            CaptureValidationError,
            "summary limitations are not derivable",
        ):
            validate_bundle(tampered)

    def test_records_cannot_cite_inactive_spans_or_change_terminal_kind(self) -> None:
        tampered = deepcopy(completed_bundle())
        checkpoint = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["causality"]["span_id"] = "crucible-span:missing"
        with self.assertRaisesRegex(
            CaptureValidationError,
            "cites a nonexistent or inactive span",
        ):
            validate_bundle(tampered)

        tampered = deepcopy(completed_bundle())
        checkpoint = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["causality"]["span_id"] = None
        checkpoint["causality"]["parent_span_id"] = None
        with self.assertRaisesRegex(
            CaptureValidationError,
            "detached from its active span",
        ):
            validate_bundle(tampered)

        tampered = deepcopy(completed_bundle())
        terminal = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "span_return"
        )
        terminal["payload"]["span_kind"] = "feature"
        with self.assertRaisesRegex(
            CaptureValidationError,
            "span terminal kind mismatch",
        ):
            validate_bundle(tampered)

        tampered = deepcopy(completed_bundle())
        terminal = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "span_return"
        )
        terminal["scope"]["chunk"] = {"x": 1, "z": 0}
        with self.assertRaisesRegex(
            CaptureValidationError,
            "span terminal scope mismatch",
        ):
            validate_bundle(tampered)

    def test_block_scope_probe_actor_and_ambiguous_actor_fail_closed(self) -> None:
        tampered = deepcopy(completed_bundle())
        checkpoint = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["record_type"] = "block_write"
        checkpoint["payload"] = {
            "write_chain_id": "worldgen-write:scope-mismatch",
            "channel": "chunk_storage",
            "position": [100, 65, 100],
            "generation_chunk": {"x": 0, "z": 0},
            "target_chunk": {"x": 0, "z": 0},
            "before_state_sha256": digest("air"),
            "after_state_sha256": digest("stone"),
            "flags": None,
            "terminal": True,
        }
        with self.assertRaisesRegex(
            CaptureValidationError,
            "block target chunk disagrees with its position",
        ):
            validate_bundle(tampered)

        tampered = deepcopy(completed_bundle())
        checkpoint = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["record_type"] = "probe_health"
        checkpoint["payload"] = {
            "hook_id": "worldgen-hook:spoof",
            "health_state": "reached",
            "target_class": "example.Target",
            "target_method": "target",
            "target_descriptor": "()V",
            "original_class_sha256": digest("original"),
            "transformed_class_sha256": digest("transformed"),
            "expected_injection_count": 1,
            "observed_injection_count": 1,
        }
        with self.assertRaisesRegex(
            CaptureValidationError,
            "probe health is not emitted by Workbench",
        ):
            validate_bundle(tampered)

        tampered = deepcopy(completed_bundle())
        checkpoint = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["actor"] = unbound_actor(candidate_mod_ids=["candidate_mod"])
        checkpoint["actor"]["code_source_sha256"] = digest("unsupported")
        with self.assertRaisesRegex(
            CaptureValidationError,
            "closed capture schema violation|unresolved actor claims code_source_sha256",
        ):
            validate_bundle(tampered)

    def test_nested_spans_inherit_trace_and_throws_retain_exception(self) -> None:
        builder = BundleBuilder(
            run_manifest("nested"),
            selection_id="crucible-selection:test",
            monotonic_ns=lambda: 11,
        )
        builder.start()
        parent = builder.enter_span(
            span_kind="generation_phase",
            operation_id="worldgen-stage:parent",
            arguments={},
            dimension_id=0,
            chunk=(0, 0),
        )
        child = builder.enter_span(
            span_kind="generator_call",
            operation_id="worldgen-call:child",
            arguments={},
            dimension_id=0,
            chunk=(0, 0),
        )
        builder.checkpoint(
            checkpoint_id="worldgen-checkpoint:nested",
            stage_id="worldgen-stage:parent",
            canonicalization_id="worldgen-canonicalizer:block-state-v1",
            semantic_state={},
            comparison_scope={"dimension": 0, "chunks": [[0, 0]]},
            dimension_id=0,
            chunk=(0, 0),
            included_domains=["block_states"],
        )
        builder.return_span(
            child,
            span_kind="generator_call",
            result={},
            dimension_id=0,
            chunk=(0, 0),
        )
        builder.return_span(
            parent,
            span_kind="generation_phase",
            result={},
            dimension_id=0,
            chunk=(0, 0),
        )
        tampered = deepcopy(builder.complete())
        child_enter = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "span_enter"
            and record["causality"]["span_id"] == child
        )
        child_enter["causality"]["trace_id"] = "crucible-trace:detached"
        with self.assertRaisesRegex(
            CaptureValidationError,
            "nested span changed trace or root",
        ):
            validate_bundle(tampered)

        builder = BundleBuilder(
            run_manifest("throw"),
            selection_id="crucible-selection:test",
            monotonic_ns=lambda: 13,
        )
        builder.start()
        span = builder.enter_span(
            span_kind="generator_call",
            operation_id="worldgen-call:throw",
            arguments={},
            dimension_id=0,
            chunk=(0, 0),
        )
        builder.checkpoint(
            checkpoint_id="worldgen-checkpoint:throw",
            stage_id="worldgen-stage:010-base",
            canonicalization_id="worldgen-canonicalizer:block-state-v1",
            semantic_state={},
            comparison_scope={"dimension": 0, "chunks": [[0, 0]]},
            dimension_id=0,
            chunk=(0, 0),
            included_domains=["block_states"],
        )
        builder.throw_span(
            span,
            span_kind="generator_call",
            throwable_class="java.lang.IllegalStateException",
            throwable_message="fixture failure",
            dimension_id=0,
            chunk=(0, 0),
        )
        tampered = deepcopy(builder.complete())
        terminal = next(
            record
            for record in tampered["records"]
            if record["record_type"] == "span_throw"
        )
        terminal["outcome"]["exception_class"] = None
        terminal["outcome"]["exception_message_sha256"] = None
        with self.assertRaisesRegex(
            CaptureValidationError,
            "closed capture schema violation|threw without exception identity",
        ):
            validate_bundle(tampered)

    def test_crash_with_open_span_has_residue_and_no_seal(self) -> None:
        builder = BundleBuilder(
            run_manifest("crash"),
            selection_id="crucible-selection:test",
            monotonic_ns=lambda: 9,
        )
        builder.start()
        span = builder.enter_span(
            span_kind="generator_call",
            operation_id="worldgen-call:crash",
            arguments={},
            dimension_id=0,
            chunk=(0, 0),
        )
        bundle = builder.incomplete(
            reason="process_crash",
            recoverable=True,
            diagnostic={"exit": 137},
        )
        validate_bundle(bundle)
        self.assertIsNone(bundle["publication"]["completion_seal"])
        self.assertEqual([span], bundle["summary"]["open_span_ids"])
        self.assertEqual(
            [span],
            bundle["publication"]["crash_residue"]["open_span_ids"],
        )
        with self.assertRaisesRegex(CaptureValidationError, "capture is incomplete"):
            validate_bundle(bundle, require_completed=True)

    def test_atomic_file_round_trip_is_strictly_validated(self) -> None:
        bundle = completed_bundle()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bundle.json"
            write_bundle(path, bundle)
            self.assertEqual(bundle, load_bundle(path, require_completed=True))


if __name__ == "__main__":
    unittest.main()
