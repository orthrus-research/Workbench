#!/usr/bin/env python3

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory.bundle import (  # noqa: E402
    BundleBuilder,
    CaptureValidationError,
    _new_validated_bundle_publication,
    _write_validated_bundle,
    canonical_json_bytes,
    exact_actor,
    new_run,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    CLEANROOM_BUNDLE_PROJECTION_PREFIX,
    CleanroomBundleProjection,
    CleanroomExecution,
    CleanroomProjectionExecution,
    FIXED_CHUNK_SET,
    FIXED_WORLD_SEED_SHA256,
    FORWARD_ROUTE,
    FORWARD_ROUTE_SHA256,
    MATRIX_EVALUATION_SCHEMA,
    PROJECTION_MATRIX_EVALUATION_SCHEMA,
    REQUIRED_CASES,
    REVERSE_ROUTE,
    REVERSE_ROUTE_SHA256,
    SELECTOR_SHA256,
    evaluate_cleanroom_matrix,
    evaluate_cleanroom_projection_matrix,
    load_cleanroom_bundle_projection,
    load_fixture_result,
    parse_cleanroom_bundle_projection,
    parse_fixture_result,
    project_cleanroom_bundle,
    write_cleanroom_bundle_projection,
    _write_validated_cleanroom_bundle_projection,
)
from workbench_crucible_observatory import cleanroom_matrix as cleanroom_matrix_module  # noqa: E402


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fixture_document(
    *,
    order: str = "forward",
    semantic_variant: str = "stable",
    inventory: list[dict[str, str]] | None = None,
) -> dict:
    route = FORWARD_ROUTE if order == "forward" else REVERSE_ROUTE
    return {
        "schema": "workbench.worldgen-observatory.fixture-result.v1",
        "fixture": "dedicated_server_fixed_region_v1",
        "completion_state": "complete",
        "save_state": "flushed",
        "shutdown_state": "requested",
        "world_seed_sha256": FIXED_WORLD_SEED_SHA256,
        "dimension": 0,
        "route_order": order,
        "selector_sha256": SELECTOR_SHA256,
        "route_sha256": (
            FORWARD_ROUTE_SHA256 if order == "forward" else REVERSE_ROUTE_SHA256
        ),
        "selected_chunks": [
            {
                "chunk_x": chunk_x,
                "chunk_z": chunk_z,
                "semantic_state_sha256": digest(
                    f"semantic:{chunk_x}:{chunk_z}:"
                    + (semantic_variant if (chunk_x, chunk_z) == (64, 64) else "stable")
                ),
            }
            for chunk_x, chunk_z in route
        ],
        "runtime_mod_inventory": inventory
        if inventory is not None
        else [
            {
                "mod_id": "alpha_fixture",
                "source_sha256": digest("shared-fixture-jar"),
                "mod_class_name": "dev.workbench.fixture.AlphaMod",
            },
            {
                "mod_id": "beta_fixture",
                "source_sha256": "unavailable",
                "mod_class_name": "unavailable",
            },
        ],
    }


def fixture_bytes(**kwargs: object) -> bytes:
    return (
        json.dumps(
            fixture_document(**kwargs),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def run_manifest(label: str) -> dict:
    return new_run(
        capture_mode="lossless-fixture",
        capture_plan_sha256=digest("exact-cleanroom-fixture-plan"),
        fixture_id=f"crucible-fixture:{label}",
        fixture_sha256=digest("dedicated-server-fixed-region-v1"),
        environment={
            "minecraft_version": "1.12.2",
            "platform_profile_id": "workbench-platform:cleanroom:0.6.8-alpha",
            "platform_profile_sha256": digest("candidate-lock"),
            "pack_profile_id": None,
            "pack_profile_sha256": None,
            "snapshot_id": "snapshot:cleanroom-matrix-test",
            "physical_side": "DEDICATED_SERVER",
            "runtime_java": "25.0.4+7",
            "mapping_namespace": "mcp-stable_39",
            "transformed_runtime_sha256": digest("transformed-runtime"),
            "mod_set_sha256": digest("fixture-mod-set"),
            "configuration_set_sha256": digest("fixture-configuration"),
        },
        world={
            "world_instance_id": f"crucible-world:{label}",
            "world_seed_sha256": FIXED_WORLD_SEED_SHA256,
            "world_type": "wb_observe",
            "generator_options_sha256": digest("{}"),
            "dimension_ids": [0],
        },
    )


COOPERATIVE_ACTOR = exact_actor(
    mod_id="workbench_worldgen_observer",
    code_source_sha256=digest("fixture-jar"),
    class_name="dev.workbench.worldgenobservatory.world.ObservingChunkGenerator",
    method_name="generateChunk",
    method_descriptor="(II)Lnet/minecraft/world/chunk/Chunk;",
    mapping_namespace="mcp-stable_39",
    transformed_class_sha256=digest("transformed-cooperative-generator"),
)


def completed_bundle(
    label: str,
    *,
    route: tuple[tuple[int, int], ...] = FORWARD_ROUTE,
    rng_variant: str = "stable",
    checkpoint_variant: str = "stable",
) -> dict:
    builder = BundleBuilder(
        run_manifest(label),
        selection_id="crucible-selection:fixed-region-64-65",
        actor=COOPERATIVE_ACTOR,
        monotonic_ns=lambda: 1,
    )
    builder.start()
    comparison_scope = {
        "dimension_id": 0,
        "chunks": [list(coordinate) for coordinate in sorted(FIXED_CHUNK_SET)],
    }
    for chunk_x, chunk_z in route:
        local_rng_variant = (
            rng_variant if (chunk_x, chunk_z) == (64, 64) else "stable"
        )
        builder.record(
            "rng_observation",
            {
                "detail": "summary",
                "stream_id": f"worldgen-rng:chunk-{chunk_x}-{chunk_z}",
                "algorithm_class": "workbench.rng-lanes.mix64.v1",
                "operation": "derive_named_seed_without_consuming_runtime_random",
                "call_ordinal": None,
                "parameters_sha256": digest(f"parameters:{chunk_x}:{chunk_z}"),
                "result_sha256": digest(
                    f"rng:{chunk_x}:{chunk_z}:{local_rng_variant}"
                ),
                "rolling_digest": digest(
                    f"rolling:{chunk_x}:{chunk_z}:{local_rng_variant}"
                ),
            },
            dimension_id=0,
            chunk=(chunk_x, chunk_z),
            actor=COOPERATIVE_ACTOR,
        )
        local_checkpoint_variant = (
            checkpoint_variant if (chunk_x, chunk_z) == (64, 64) else "stable"
        )
        builder.checkpoint(
            checkpoint_id=f"worldgen-checkpoint:chunk-{chunk_x}-{chunk_z}-final",
            stage_id="worldgen-stage:final",
            canonicalization_id="worldgen-canonicalizer:block-state-yzx-v1",
            semantic_state_sha256=digest(
                f"checkpoint:{chunk_x}:{chunk_z}:{local_checkpoint_variant}"
            ),
            comparison_scope=comparison_scope,
            dimension_id=0,
            chunk=(chunk_x, chunk_z),
            included_domains=["block_states"],
            actor=COOPERATIVE_ACTOR,
        )
    return builder.complete()


def crash_bundle(label: str = "crash") -> dict:
    builder = BundleBuilder(
        run_manifest(label),
        selection_id="crucible-selection:fixed-region-64-65",
        actor=COOPERATIVE_ACTOR,
        monotonic_ns=lambda: 1,
    )
    builder.start()
    return builder.incomplete(
        reason="process_crash",
        recoverable=True,
        diagnostic={"last_transport_state": "capture_started"},
    )


def matrix_inputs(
    *,
    off_variant: str = "stable",
    second_rng_variant: str = "stable",
    second_checkpoint_variant: str = "stable",
    crash: dict | None = None,
) -> tuple[dict[str, CleanroomExecution], dict[str, str]]:
    executions = {
        "exec-forward-a": CleanroomExecution(
            "exec-forward-a",
            True,
            "forward",
            parse_fixture_result(fixture_bytes()),
            completed_bundle("forward-a"),
        ),
        "exec-forward-b": CleanroomExecution(
            "exec-forward-b",
            True,
            "forward",
            parse_fixture_result(fixture_bytes()),
            completed_bundle(
                "forward-b",
                rng_variant=second_rng_variant,
                checkpoint_variant=second_checkpoint_variant,
            ),
        ),
        "exec-observer-off": CleanroomExecution(
            "exec-observer-off",
            False,
            "forward",
            parse_fixture_result(fixture_bytes(semantic_variant=off_variant)),
            None,
        ),
        "exec-reverse": CleanroomExecution(
            "exec-reverse",
            True,
            "reverse",
            parse_fixture_result(fixture_bytes(order="reverse")),
            completed_bundle("reverse", route=REVERSE_ROUTE),
        ),
        "exec-crash": CleanroomExecution(
            "exec-crash",
            True,
            "forward",
            None,
            crash_bundle() if crash is None else crash,
        ),
    }
    aliases = {
        "aa-1": "exec-forward-a",
        "aa-2": "exec-forward-b",
        "observer-off": "exec-observer-off",
        "observer-on": "exec-forward-a",
        "order-forward": "exec-forward-a",
        "order-reverse": "exec-reverse",
        "restart-1": "exec-forward-a",
        "restart-2": "exec-forward-b",
        "crash-before-seal": "exec-crash",
    }
    return executions, aliases


def projected_matrix_inputs(
    **kwargs: object,
) -> tuple[dict[str, CleanroomProjectionExecution], dict[str, str]]:
    full, aliases = matrix_inputs(**kwargs)
    projected: dict[str, CleanroomProjectionExecution] = {}
    for execution_id, execution in full.items():
        projection = (
            None
            if execution.canonical_bundle is None
            else project_cleanroom_bundle(
                execution.canonical_bundle,
                execution_id=execution_id,
            )
        )
        projected[execution_id] = CleanroomProjectionExecution(
            execution_id=execution_id,
            observer_enabled=execution.observer_enabled,
            route_order=execution.route_order,
            fixture_result=execution.fixture_result,
            bundle_projection=projection,
        )
    return projected, aliases


def reseal_projection_document(document: dict) -> None:
    material = deepcopy(document)
    material.pop("projection_id")
    document["projection_id"] = CLEANROOM_BUNDLE_PROJECTION_PREFIX + hashlib.sha256(
        canonical_json_bytes(material)
    ).hexdigest()


class FixtureResultAdmissionTests(unittest.TestCase):
    def test_admits_exact_driver_result_and_canonicalizes_route_independently(self) -> None:
        forward = parse_fixture_result(fixture_bytes())
        reverse = parse_fixture_result(fixture_bytes(order="reverse"))
        self.assertEqual("forward", forward.route_order)
        self.assertEqual("reverse", reverse.route_order)
        self.assertEqual(FORWARD_ROUTE_SHA256, forward.route_sha256)
        self.assertEqual(REVERSE_ROUTE_SHA256, reverse.route_sha256)
        self.assertEqual(forward.semantic_rows(), reverse.semantic_rows())
        self.assertEqual(forward.semantic_map_sha256(), reverse.semantic_map_sha256())
        self.assertEqual(4, len(forward.selected_chunks))
        self.assertEqual(2, len(forward.runtime_mod_inventory))

    def test_load_preserves_exact_artifact_digest(self) -> None:
        encoded = fixture_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            path.write_bytes(encoded)
            result = load_fixture_result(path)
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), result.artifact_sha256)

    def test_rejects_duplicate_keys_nonfinite_and_unknown_fields(self) -> None:
        valid = fixture_bytes().decode("utf-8")
        duplicate = '{"schema":"duplicate",' + valid[1:]
        nonfinite = valid.replace('"dimension":0', '"dimension":NaN')
        unknown_value = fixture_document()
        unknown_value["unknown"] = True
        unknown = json.dumps(unknown_value, separators=(",", ":"))
        for name, encoded, message in (
            ("duplicate", duplicate, "duplicate JSON key"),
            ("nonfinite", nonfinite, "non-finite"),
            ("unknown", unknown, "fields mismatch"),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(CaptureValidationError, message):
                    parse_fixture_result(encoded)

    def test_rejects_marker_seed_dimension_route_and_chunk_tampering(self) -> None:
        mutations = []
        incomplete = fixture_document()
        incomplete["completion_state"] = "incomplete"
        mutations.append((incomplete, "not complete"))
        unsaved = fixture_document()
        unsaved["save_state"] = "saved"
        mutations.append((unsaved, "not flushed"))
        no_shutdown = fixture_document()
        no_shutdown["shutdown_state"] = "complete"
        mutations.append((no_shutdown, "shutdown request"))
        wrong_seed = fixture_document()
        wrong_seed["world_seed_sha256"] = digest("other-seed")
        mutations.append((wrong_seed, "fixed-seed"))
        boolean_dimension = fixture_document()
        boolean_dimension["dimension"] = False
        mutations.append((boolean_dimension, "integer zero"))
        wrong_selector = fixture_document()
        wrong_selector["selector_sha256"] = digest("other-selector")
        mutations.append((wrong_selector, "selector digest"))
        wrong_route = fixture_document()
        wrong_route["route_sha256"] = digest("other-route")
        mutations.append((wrong_route, "route digest"))
        duplicate_chunk = fixture_document()
        duplicate_chunk["selected_chunks"][1]["chunk_x"] = 64
        mutations.append((duplicate_chunk, "coordinate is duplicated"))
        uppercase_semantic = fixture_document()
        uppercase_semantic["selected_chunks"][0]["semantic_state_sha256"] = "A" * 64
        mutations.append((uppercase_semantic, "lowercase SHA-256"))
        for value, message in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(CaptureValidationError, message):
                    parse_fixture_result(json.dumps(value, separators=(",", ":")))

    def test_requires_sorted_unique_inventory_and_explicit_unavailable(self) -> None:
        unsorted = fixture_document()
        unsorted["runtime_mod_inventory"].reverse()
        duplicate = fixture_document()
        duplicate["runtime_mod_inventory"].append(
            deepcopy(duplicate["runtime_mod_inventory"][0])
        )
        bad_source = fixture_document()
        bad_source["runtime_mod_inventory"][1]["source_sha256"] = "UNKNOWN"
        bad_class = fixture_document()
        bad_class["runtime_mod_inventory"][1]["mod_class_name"] = "Unknown"
        for name, value, message in (
            ("unsorted", unsorted, "sort order"),
            ("duplicate", duplicate, "mod_id is duplicated"),
            ("source", bad_source, "explicit unavailable"),
            ("class", bad_class, "Java binary name"),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(CaptureValidationError, message):
                    parse_fixture_result(json.dumps(value, separators=(",", ":")))


class CleanroomBundleProjectionTests(unittest.TestCase):
    def test_internal_projection_reuses_the_matching_atomic_write_receipt(self) -> None:
        bundle = completed_bundle("trusted-projection")
        publication = _new_validated_bundle_publication(
            bundle,
            require_completed=True,
        )
        other = _new_validated_bundle_publication(
            completed_bundle("foreign-projection"),
            require_completed=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_receipt = _write_validated_bundle(
                root / "bundle.json",
                publication,
            )
            other_receipt = _write_validated_bundle(
                root / "other-bundle.json",
                other,
            )
            with mock.patch.object(
                cleanroom_matrix_module,
                "validate_bundle",
                side_effect=AssertionError("internal projection revalidated"),
            ), mock.patch.object(
                cleanroom_matrix_module,
                "canonical_json_sha256",
                side_effect=AssertionError("internal projection rehashed"),
            ):
                projection = _write_validated_cleanroom_bundle_projection(
                    root / "projection.json",
                    publication,
                    bundle_receipt,
                    execution_id="exec-trusted-projection",
                )

            self.assertEqual(
                bundle_receipt.canonical_json_sha256,
                projection.source_bundle_sha256,
            )
            self.assertEqual(
                projection,
                load_cleanroom_bundle_projection(root / "projection.json"),
            )
            with self.assertRaisesRegex(CaptureValidationError, "does not cite"):
                _write_validated_cleanroom_bundle_projection(
                    root / "foreign-projection.json",
                    publication,
                    other_receipt,
                    execution_id="exec-foreign-projection",
                )

    def test_completed_and_crash_projection_round_trip_atomically(self) -> None:
        bundle = completed_bundle("projected")
        projection = project_cleanroom_bundle(bundle, execution_id="exec-projected")
        self.assertIsInstance(projection, CleanroomBundleProjection)
        self.assertEqual("completed", projection.publication_state)
        self.assertTrue(projection.projection_id.startswith(CLEANROOM_BUNDLE_PROJECTION_PREFIX))
        self.assertEqual(
            hashlib.sha256(canonical_json_bytes(bundle)).hexdigest(),
            projection.source_bundle_sha256,
        )
        checkpoint_actor = projection.checkpoint_rows[0]["actor"]
        for field in (
            "binding",
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
            "transformed_class_sha256",
        ):
            self.assertEqual(COOPERATIVE_ACTOR[field], checkpoint_actor[field])

        encoded = canonical_json_bytes(projection.as_dict()) + b"\n"
        self.assertEqual(
            projection,
            parse_cleanroom_bundle_projection(encoded),
        )
        self.assertLess(len(encoded), len(canonical_json_bytes(bundle)))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "projection.json"
            written = write_cleanroom_bundle_projection(
                path,
                bundle,
                execution_id="exec-projected",
            )
            loaded = load_cleanroom_bundle_projection(path)
        self.assertEqual(projection, written)
        self.assertEqual(projection, loaded)

        crash = project_cleanroom_bundle(
            crash_bundle("projected-crash"),
            execution_id="exec-projected-crash",
        )
        self.assertEqual("incomplete", crash.publication_state)
        self.assertIsNone(crash.publication["completion_seal"])
        self.assertEqual("process_crash", crash.publication["crash_residue"]["reason"])
        self.assertEqual((), crash.checkpoint_rows)
        self.assertEqual((), crash.rng_rows)
        self.assertIsNone(crash.checkpoint_summary_sha256)
        self.assertIsNone(crash.rng_summary_sha256)

    def test_strict_projection_admission_rejects_tampering(self) -> None:
        projection = project_cleanroom_bundle(
            completed_bundle("projection-tamper"),
            execution_id="exec-projection-tamper",
        )
        valid = projection.as_dict()

        duplicate = '{"schema":"duplicate",' + json.dumps(valid)[1:]
        with self.assertRaisesRegex(CaptureValidationError, "duplicate JSON key"):
            parse_cleanroom_bundle_projection(duplicate)

        mutations: list[tuple[str, dict, str, bool]] = []
        unknown = deepcopy(valid)
        unknown["unknown"] = True
        mutations.append(("unknown", unknown, "fields mismatch", True))
        missing_actor_field = deepcopy(valid)
        missing_actor_field["checkpoint_rows"][0]["actor"].pop("method_descriptor")
        mutations.append(("actor", missing_actor_field, "actor fields mismatch", True))
        unsealed = deepcopy(valid)
        unsealed["publication"]["completion_seal"] = None
        mutations.append(("unsealed", unsealed, "lacks a seal", True))
        foreign_source = deepcopy(valid)
        foreign_source["source_bundle_sha256"] = "A" * 64
        mutations.append(("source", foreign_source, "source digest", True))
        stale_identity = deepcopy(valid)
        stale_identity["execution_id"] = "exec-other"
        mutations.append(("identity", stale_identity, "content identity", False))
        for name, document, message, reseal in mutations:
            with self.subTest(name=name):
                if reseal:
                    reseal_projection_document(document)
                with self.assertRaisesRegex(CaptureValidationError, message):
                    parse_cleanroom_bundle_projection(
                        canonical_json_bytes(document)
                    )

        crash = project_cleanroom_bundle(
            crash_bundle("projection-crash-tamper"),
            execution_id="exec-projection-crash-tamper",
        ).as_dict()
        crash["checkpoint_rows"] = deepcopy(valid["checkpoint_rows"])
        reseal_projection_document(crash)
        with self.assertRaisesRegex(CaptureValidationError, "contains checkpoints"):
            parse_cleanroom_bundle_projection(canonical_json_bytes(crash))

    def test_projection_matrix_matches_full_bundle_decisions(self) -> None:
        full, aliases = matrix_inputs()
        projected, projected_aliases = projected_matrix_inputs()
        full_evaluation = evaluate_cleanroom_matrix(full, aliases)
        projected_evaluation = evaluate_cleanroom_projection_matrix(
            projected, projected_aliases
        )
        self.assertEqual(
            PROJECTION_MATRIX_EVALUATION_SCHEMA,
            projected_evaluation["schema"],
        )
        self.assertEqual("passed", projected_evaluation["matrix_state"])
        self.assertEqual(full_evaluation["global_checks"], projected_evaluation["global_checks"])
        self.assertEqual(full_evaluation["comparisons"], projected_evaluation["comparisons"])
        self.assertEqual(
            projected_evaluation,
            evaluate_cleanroom_projection_matrix(projected, projected_aliases),
        )
        by_execution = {
            row["execution_id"]: row for row in projected_evaluation["executions"]
        }
        self.assertIsNone(by_execution["exec-observer-off"]["bundle_projection_id"])
        self.assertIsNotNone(by_execution["exec-forward-a"]["bundle_projection_id"])
        self.assertIsNotNone(by_execution["exec-crash"]["source_bundle_sha256"])

        divergent_full, divergent_aliases = matrix_inputs(
            second_rng_variant="projected-rng-divergence"
        )
        divergent_projected, divergent_projected_aliases = projected_matrix_inputs(
            second_rng_variant="projected-rng-divergence"
        )
        full_failure = evaluate_cleanroom_matrix(divergent_full, divergent_aliases)
        projected_failure = evaluate_cleanroom_projection_matrix(
            divergent_projected,
            divergent_projected_aliases,
        )
        self.assertEqual("failed", projected_failure["matrix_state"])
        self.assertEqual(full_failure["comparisons"], projected_failure["comparisons"])

    def test_projection_matrix_rejects_foreign_or_wrong_state_projection(self) -> None:
        projected, aliases = projected_matrix_inputs()
        forward = projected["exec-forward-a"]
        projected["exec-forward-a"] = CleanroomProjectionExecution(
            execution_id=forward.execution_id,
            observer_enabled=True,
            route_order="forward",
            fixture_result=forward.fixture_result,
            bundle_projection=projected["exec-forward-b"].bundle_projection,
        )
        with self.assertRaisesRegex(CaptureValidationError, "execution identity mismatch"):
            evaluate_cleanroom_projection_matrix(projected, aliases)

        projected, aliases = projected_matrix_inputs()
        forward = projected["exec-forward-a"]
        projected["exec-forward-a"] = CleanroomProjectionExecution(
            execution_id=forward.execution_id,
            observer_enabled=True,
            route_order="forward",
            fixture_result=forward.fixture_result,
            bundle_projection=projected["exec-crash"].bundle_projection,
        )
        with self.assertRaisesRegex(
            CaptureValidationError,
            "execution identity mismatch|publication state mismatch",
        ):
            evaluate_cleanroom_projection_matrix(projected, aliases)


class CleanroomMatrixEvaluationTests(unittest.TestCase):
    def test_passes_aa_neutrality_reverse_restart_and_crash_matrix(self) -> None:
        executions, aliases = matrix_inputs()
        evaluation = evaluate_cleanroom_matrix(executions, aliases)
        self.assertEqual(MATRIX_EVALUATION_SCHEMA, evaluation["schema"])
        self.assertEqual("evaluation_only", evaluation["evidence_class"])
        self.assertEqual("passed", evaluation["matrix_state"])
        self.assertEqual(list(REQUIRED_CASES), evaluation["required_cases"])
        self.assertTrue(all(evaluation["global_checks"].values()))
        self.assertTrue(all(row["passed"] for row in evaluation["comparisons"]))
        self.assertEqual(evaluation, evaluate_cleanroom_matrix(executions, aliases))

        forward_row = next(
            row
            for row in evaluation["executions"]
            if row["execution_id"] == "exec-forward-a"
        )
        self.assertEqual(
            ["aa-1", "observer-on", "order-forward", "restart-1"],
            forward_row["case_aliases"],
        )
        crash_row = next(
            row
            for row in evaluation["executions"]
            if row["execution_id"] == "exec-crash"
        )
        self.assertEqual("incomplete", crash_row["publication_state"])
        self.assertIsNone(crash_row["fixture_result_sha256"])

    def test_reports_semantic_and_rng_differences_without_forging_success(self) -> None:
        executions, aliases = matrix_inputs(off_variant="observer-changed-output")
        semantic_failure = evaluate_cleanroom_matrix(executions, aliases)
        self.assertEqual("failed", semantic_failure["matrix_state"])
        observer = next(
            row
            for row in semantic_failure["comparisons"]
            if row["comparison_id"] == "observer-neutrality"
        )
        self.assertFalse(observer["semantic_equal"])
        self.assertEqual([{"chunk_x": 64, "chunk_z": 64}], observer["differing_chunks"])

        executions, aliases = matrix_inputs(second_rng_variant="rng-diverged")
        rng_failure = evaluate_cleanroom_matrix(executions, aliases)
        self.assertEqual("failed", rng_failure["matrix_state"])
        aa = next(
            row
            for row in rng_failure["comparisons"]
            if row["comparison_id"] == "aa-determinism"
        )
        self.assertTrue(aa["semantic_equal"])
        self.assertFalse(aa["rng_equal"])

        executions, aliases = matrix_inputs(
            second_checkpoint_variant="checkpoint-diverged"
        )
        checkpoint_failure = evaluate_cleanroom_matrix(executions, aliases)
        self.assertEqual("failed", checkpoint_failure["matrix_state"])
        aa = next(
            row
            for row in checkpoint_failure["comparisons"]
            if row["comparison_id"] == "aa-determinism"
        )
        self.assertTrue(aa["semantic_equal"])
        self.assertFalse(aa["checkpoint_equal"])

    def test_rejects_alias_cheating_completed_crash_and_unsealed_observer_on(self) -> None:
        executions, aliases = matrix_inputs()
        aliases["aa-2"] = "exec-forward-a"
        with self.assertRaisesRegex(CaptureValidationError, "two distinct executions"):
            evaluate_cleanroom_matrix(executions, aliases)

        executions, aliases = matrix_inputs(crash=completed_bundle("not-a-crash"))
        with self.assertRaisesRegex(CaptureValidationError, "not incomplete"):
            evaluate_cleanroom_matrix(executions, aliases)

        executions, aliases = matrix_inputs()
        forward = executions["exec-forward-a"]
        executions["exec-forward-a"] = CleanroomExecution(
            forward.execution_id,
            forward.observer_enabled,
            forward.route_order,
            forward.fixture_result,
            crash_bundle("unsealed-normal"),
        )
        with self.assertRaisesRegex(CaptureValidationError, "capture is incomplete"):
            evaluate_cleanroom_matrix(executions, aliases)

    def test_rejects_bundle_tampering_and_incompatible_aliases(self) -> None:
        executions, aliases = matrix_inputs()
        forward = executions["exec-forward-a"]
        tampered_bundle = deepcopy(forward.canonical_bundle)
        checkpoint = next(
            record
            for record in tampered_bundle["records"]
            if record["record_type"] == "checkpoint"
        )
        checkpoint["payload"]["semantic_state_sha256"] = digest("tampered")
        executions["exec-forward-a"] = CleanroomExecution(
            forward.execution_id,
            True,
            "forward",
            forward.fixture_result,
            tampered_bundle,
        )
        with self.assertRaises(CaptureValidationError):
            evaluate_cleanroom_matrix(executions, aliases)

        executions, aliases = matrix_inputs()
        aliases["observer-on"] = "exec-reverse"
        with self.assertRaisesRegex(CaptureValidationError, "incompatible conditions"):
            evaluate_cleanroom_matrix(executions, aliases)


if __name__ == "__main__":
    unittest.main()
