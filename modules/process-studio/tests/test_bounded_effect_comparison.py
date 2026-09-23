"""Focused tests for Process Studio's bounded observed-effect comparison."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

import workbench_process_studio as process_studio
from workbench_api.canonical import content_id
from workbench_crucible_stage_snapshot import (
    build_stage_snapshot,
    effect_observation,
    registry_observation,
)
from workbench_process_studio import (
    BoundedEffectComparisonError,
    ComparisonBounds,
    DEFAULT_BOUNDS,
    SYNTHETIC_FIXTURE_ADAPTERS,
    build_change_envelope,
    compare_bounded_effects,
    project_effect_snapshot,
    validate_change_envelope_shape,
    validate_observed_effect_comparison_shape,
    validate_source_bound_comparison,
)
import workbench_process_studio.cli as cli_module
from workbench_process_studio.cli import build_parser, main as cli_main


STAGE = "post-init"
CONTEXT = "fixture-context-v1"
PACK = "fixture-pack-v1"
PLATFORM = "cleanroom-0.3-fixture"
SIDE = "server"
MODULE_ROOT = Path(__file__).resolve().parents[1]

_ADAPTER_BY_FAMILY = {
    adapter.family: adapter for adapter in SYNTHETIC_FIXTURE_ADAPTERS
}


def _legacy_canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reseal_content(
    value: dict[str, object], *, id_key: str, prefix: str
) -> dict[str, object]:
    payload = deepcopy(value)
    payload.pop(id_key, None)
    kind = prefix.removesuffix(":sha256:")
    value[id_key] = content_id(kind, payload)
    return value


def _reseal_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    """Recompute the permissive Crucible V1 identity after hostile mutation."""

    payload = deepcopy(snapshot)
    payload.pop("snapshot_id", None)
    snapshot["snapshot_id"] = (
        "workbench-crucible-stage-snapshot:sha256:"
        + hashlib.sha256(_legacy_canonical_bytes(payload)).hexdigest()
    )
    return snapshot


def _coverage(
    *families: str,
    state: str = "complete",
    overrides: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for family in sorted(families):
        adapter = _ADAPTER_BY_FAMILY[family]
        row = {
            "family": adapter.family,
            "semantic_owner_id": adapter.semantic_owner_id,
            "adapter_id": adapter.adapter_id,
            "semantic_version": adapter.semantic_version,
            "correlation_policy_id": adapter.correlation_policy_id,
            "fingerprint_policy_id": adapter.fingerprint_policy_id,
            "state": state,
        }
        row.update((overrides or {}).get(family, {}))
        rows.append(row)
    return rows


def _capture(
    *,
    context: str = CONTEXT,
    side: str = SIDE,
    coverage: list[dict[str, str]] | None = None,
    capture_id: str = "capture-fixture-v1",
    declared_change_envelope_id: str | None = None,
) -> dict[str, object]:
    return {
        "capture_id": capture_id,
        "comparison_context_id": context,
        "physical_side": side,
        "declared_change_envelope_id": declared_change_envelope_id,
        "effect_coverage": (
            _coverage("forge-crafting", "gt-recipe")
            if coverage is None
            else coverage
        ),
    }


def _snapshot(
    *,
    effects: list[dict[str, object]] | None = None,
    registry: list[dict[str, object]] | None = None,
    context: str = CONTEXT,
    pack: str = PACK,
    platform: str = PLATFORM,
    side: str = SIDE,
    stage: str = STAGE,
    coverage: list[dict[str, str]] | None = None,
    capture_id: str = "capture-fixture-v1",
    declared_change_envelope_id: str | None = None,
    capture: dict[str, object] | None = None,
) -> dict[str, object]:
    selected_capture = (
        _capture(
            context=context,
            side=side,
            coverage=coverage,
            capture_id=capture_id,
            declared_change_envelope_id=declared_change_envelope_id,
        )
        if capture is None
        else capture
    )
    return build_stage_snapshot(
        pack_profile_id=pack,
        platform_profile_id=platform,
        stage=stage,
        registry=registry or [],
        effects=effects or [],
        capture=selected_capture,
    )


def _gt_descriptor(
    recipe_id: str | None,
    *,
    recipe_map: str = "assembler",
    correlation: dict[str, object] | None = None,
    opaque_key: str | None = None,
    declare_correlation: bool = True,
) -> dict[str, object]:
    key: dict[str, object] = {}
    if recipe_id is not None:
        key.update({"recipe_map": recipe_map, "recipe_id": recipe_id})
    declared = correlation
    if declared is None and recipe_id is not None:
        declared = {"recipe_id": recipe_id, "recipe_map": recipe_map}
    if declare_correlation and declared is not None:
        key["correlation"] = declared
    if opaque_key is not None:
        key["opaque"] = opaque_key
    return {"domain": "gt-recipe", "kind": "finite-recipe", "key": key}


def _forge_descriptor(
    registry_name: str, *, declare_correlation: bool = True
) -> dict[str, object]:
    key: dict[str, object] = {"registry_name": registry_name}
    if declare_correlation:
        key["correlation"] = {"registry_name": registry_name}
    return {
        "domain": "forge-crafting",
        "kind": "crafting-recipe",
        "key": key,
    }


def _effect(
    descriptor: dict[str, object],
    state: dict[str, object],
    *,
    operation: str = "register",
    stage: str = STAGE,
    source: str = "fixture",
) -> dict[str, object]:
    return effect_observation(
        stage=stage,
        semantic_descriptor=descriptor,
        operation=operation,
        effect_state=state,
        provenance={"source": source},
    )


def _registry(
    descriptor: dict[str, object],
    state: dict[str, object],
    *,
    stage: str = STAGE,
    source: str = "fixture",
) -> dict[str, object]:
    return registry_observation(
        stage=stage,
        semantic_descriptor=descriptor,
        registry_state=state,
        provenance={"source": source},
    )


def _classifications(result: dict[str, object]) -> list[str]:
    return [row["classification"] for row in result["comparisons"]]


def _compare(
    baseline: dict[str, object],
    candidate: dict[str, object],
    **kwargs: object,
) -> dict[str, object]:
    return compare_bounded_effects(
        baseline,
        candidate,
        adapters=SYNTHETIC_FIXTURE_ADAPTERS,
        **kwargs,
    )


def _project(snapshot: dict[str, object]) -> dict[str, object]:
    return project_effect_snapshot(
        snapshot,
        adapters=SYNTHETIC_FIXTURE_ADAPTERS,
    )


def _expectation(
    row: dict[str, object],
    *,
    baseline_fingerprint: str | None | object = ...,
    candidate_fingerprint: str | None | object = ...,
) -> dict[str, object]:
    baseline_values = row["baseline_semantic_fingerprints"]
    candidate_values = row["candidate_semantic_fingerprints"]
    before = (
        (baseline_values[0] if baseline_values else None)
        if baseline_fingerprint is ...
        else baseline_fingerprint
    )
    after = (
        (candidate_values[0] if candidate_values else None)
        if candidate_fingerprint is ...
        else candidate_fingerprint
    )
    return {
        "family": row["family"],
        "record_kind": row["record_kind"],
        "descriptor_kind": row["descriptor_kind"],
        "operation": row["operation"],
        "correlation": deepcopy(row["correlation"]),
        "baseline_semantic_fingerprint": before,
        "candidate_semantic_fingerprint": after,
    }


def _envelope(
    baseline: dict[str, object],
    expectations: list[dict[str, object]],
    *,
    allow_unlisted_changes: bool = False,
    context: str = CONTEXT,
    pack: str = PACK,
    platform: str = PLATFORM,
    side: str = SIDE,
    stage: str = STAGE,
) -> dict[str, object]:
    return build_change_envelope(
        owner_id="fixture-change-owner-v1",
        baseline_snapshot_id=baseline["snapshot_id"],
        baseline_capture_id=baseline["binding"]["capture"]["capture_id"],
        context_id=context,
        pack_profile_id=pack,
        platform_profile_id=platform,
        physical_side=side,
        stage=stage,
        expectations=expectations,
        state="closed",
        allow_unlisted_changes=allow_unlisted_changes,
        limitations=("Fixture expectations do not claim causality.",),
    )


class BoundedComparisonClassificationTests(unittest.TestCase):
    def test_synthetic_adapters_are_explicit_opt_in_not_api_defaults(self) -> None:
        for function in (project_effect_snapshot, compare_bounded_effects):
            with self.subTest(function=function.__name__):
                self.assertIs(
                    inspect.signature(function).parameters["adapters"].default,
                    inspect.Parameter.empty,
                )
        snapshot = _snapshot(coverage=_coverage("gt-recipe"))

        with self.assertRaises(TypeError):
            project_effect_snapshot(snapshot)

    def test_recipe_fixture_adapters_require_explicit_correlation(self) -> None:
        baseline = _snapshot(
            effects=[
                _effect(
                    _gt_descriptor("legacy-fallback", declare_correlation=False),
                    {"eu": 8},
                ),
                _effect(
                    _forge_descriptor(
                        "fixture:legacy_fallback",
                        declare_correlation=False,
                    ),
                    {"shape": "x"},
                ),
            ],
            capture_id="fallback-before",
        )
        candidate = _snapshot(capture_id="fallback-after")

        result = _compare(baseline, candidate)

        self.assertEqual(_classifications(result), ["unresolved", "unresolved"])
        self.assertTrue(
            all(
                row["reason"] == "semantic-projection-unresolved"
                for row in result["comparisons"]
            )
        )

    def test_gt_and_forge_classifications_and_deterministic_identity(self) -> None:
        baseline = _snapshot(
            effects=[
                _effect(_gt_descriptor("gt-removed"), {"eu": 8}),
                _effect(_gt_descriptor("gt-modified"), {"eu": 8}),
                _effect(_gt_descriptor("gt-unchanged"), {"eu": 8}),
                _effect(_forge_descriptor("fixture:forge-modified"), {"shape": "a"}),
            ],
            registry=[
                _registry(_forge_descriptor("fixture:forge-unchanged"), {"present": True})
            ],
            capture_id="capture-baseline",
        )
        candidate = _snapshot(
            effects=[
                _effect(_gt_descriptor("gt-added"), {"eu": 8}),
                _effect(_gt_descriptor("gt-modified"), {"eu": 16}),
                _effect(_gt_descriptor("gt-unchanged"), {"eu": 8}),
                _effect(_forge_descriptor("fixture:forge-modified"), {"shape": "b"}),
                _effect(_forge_descriptor("fixture:forge-added"), {"shape": "x"}),
            ],
            registry=[
                _registry(_forge_descriptor("fixture:forge-unchanged"), {"present": True})
            ],
            capture_id="capture-candidate",
        )

        first = _compare(baseline, candidate)
        second = _compare(baseline, candidate)

        self.assertEqual(first, second)
        self.assertEqual(first["comparison_id"], second["comparison_id"])
        self.assertEqual(
            set(_classifications(first)),
            {"added", "removed", "modified", "unchanged"},
        )
        self.assertEqual(first["summary"]["by_classification"]["added"], 2)
        self.assertEqual(first["summary"]["by_classification"]["modified"], 2)
        self.assertEqual(first["summary"]["by_classification"]["removed"], 1)
        self.assertEqual(first["summary"]["by_classification"]["unchanged"], 2)
        self.assertEqual(first["summary"]["state"], "comparison-only")
        self.assertIsNone(first["change_envelope"])

    def test_complete_empty_universe_is_comparable(self) -> None:
        complete_empty = _coverage("forge-crafting", "gt-recipe")
        baseline = _snapshot(coverage=complete_empty, capture_id="empty-before")
        candidate = _snapshot(coverage=complete_empty, capture_id="empty-after")

        result = _compare(baseline, candidate)

        self.assertEqual(result["comparisons"], [])
        self.assertEqual(result["summary"]["comparisons"], 0)
        self.assertEqual(result["summary"]["state"], "comparison-only")
        self.assertEqual(result["summary"]["reverse_diff_invariant"], "satisfied")

    def test_zero_record_unobservable_families_need_no_loaded_adapter(self) -> None:
        for state in ("unavailable", "failed", "not-observed"):
            coverage = _coverage("gt-recipe", state=state)
            baseline = _snapshot(
                coverage=coverage,
                capture_id=f"{state}-before",
            )
            candidate = _snapshot(
                coverage=coverage,
                capture_id=f"{state}-after",
            )
            with self.subTest(state=state):
                projection = project_effect_snapshot(baseline, adapters=())
                result = compare_bounded_effects(
                    baseline,
                    candidate,
                    adapters=(),
                )
                self.assertEqual(projection["records"], [])
                self.assertEqual(result["comparisons"], [])
                self.assertEqual(result["summary"]["state"], "comparison-only")

    def test_identical_snapshot_or_capture_identity_is_incomparable(self) -> None:
        snapshot = _snapshot(capture_id="same-snapshot")
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            _compare(snapshot, deepcopy(snapshot))
        self.assertEqual(caught.exception.code, "incomparable.identical-snapshot")

        baseline = _snapshot(capture_id="same-capture")
        candidate = _snapshot(
            effects=[_effect(_gt_descriptor("new"), {"eu": 8})],
            capture_id="same-capture",
        )
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            _compare(baseline, candidate)
        self.assertEqual(caught.exception.code, "incomparable.identical-capture")

    def test_missing_stable_correlation_is_unresolved_not_removed(self) -> None:
        baseline = _snapshot(
            effects=[
                _effect(
                    _gt_descriptor(None, opaque_key="not-an-admitted-correspondence"),
                    {"eu": 8},
                )
            ],
            capture_id="missing-correlation-before",
        )
        candidate = _snapshot(capture_id="missing-correlation-after")

        result = _compare(baseline, candidate)

        self.assertEqual(_classifications(result), ["unresolved"])
        self.assertEqual(
            result["comparisons"][0]["reason"],
            "semantic-projection-unresolved",
        )
        self.assertEqual(result["summary"]["structural_changes"], 0)
        self.assertEqual(result["summary"]["uncertain"], 1)

    def test_duplicate_correlation_is_ambiguous(self) -> None:
        descriptor = _gt_descriptor("duplicate")
        baseline = _snapshot(
            effects=[
                _effect(descriptor, {"eu": 8}, source="first"),
                _effect(descriptor, {"eu": 16}, source="second"),
            ],
            capture_id="duplicate-before",
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 8})],
            capture_id="duplicate-after",
        )

        result = _compare(baseline, candidate)

        self.assertEqual(_classifications(result), ["ambiguous"])
        self.assertEqual(result["comparisons"][0]["reason"], "non-unique-correlation")
        self.assertEqual(len(result["comparisons"][0]["baseline_source_record_ids"]), 2)

    def test_reverse_diff_flips_only_addition_and_removal(self) -> None:
        baseline = _snapshot(
            effects=[
                _effect(_gt_descriptor("removed"), {"eu": 8}),
                _effect(_gt_descriptor("modified"), {"eu": 8}),
                _effect(_gt_descriptor("unchanged"), {"eu": 8}),
            ],
            capture_id="reverse-before",
        )
        candidate = _snapshot(
            effects=[
                _effect(_gt_descriptor("added"), {"eu": 8}),
                _effect(_gt_descriptor("modified"), {"eu": 16}),
                _effect(_gt_descriptor("unchanged"), {"eu": 8}),
            ],
            capture_id="reverse-after",
        )

        forward = _compare(baseline, candidate)
        backward = _compare(candidate, baseline)
        forward_by_key = {
            json.dumps(row["correlation"], sort_keys=True): row["classification"]
            for row in forward["comparisons"]
        }
        backward_by_key = {
            json.dumps(row["correlation"], sort_keys=True): row["classification"]
            for row in backward["comparisons"]
        }
        reverse = {
            "added": "removed",
            "removed": "added",
            "modified": "modified",
            "unchanged": "unchanged",
        }

        self.assertEqual(set(forward_by_key), set(backward_by_key))
        for key, classification in forward_by_key.items():
            self.assertEqual(backward_by_key[key], reverse[classification])


class ChangeEnvelopeAssessmentTests(unittest.TestCase):
    def test_declared_envelope_fingerprints_match_observed_pair(self) -> None:
        descriptor = _gt_descriptor("modified")
        baseline_effect = _effect(descriptor, {"eu": 8})
        planned_candidate_effect = _effect(descriptor, {"eu": 16})
        baseline = _snapshot(
            effects=[baseline_effect],
            capture_id="conform-before",
        )
        adapter = _ADAPTER_BY_FAMILY["gt-recipe"]
        expected_before = adapter.project(baseline_effect).semantic_fingerprint
        expected_after = adapter.project(planned_candidate_effect).semantic_fingerprint
        expectation = {
            "family": "gt-recipe",
            "record_kind": "effect-observation",
            "descriptor_kind": "finite-recipe",
            "operation": "register",
            "correlation": {
                "recipe_id": "modified",
                "recipe_map": "assembler",
            },
            "baseline_semantic_fingerprint": expected_before,
            "candidate_semantic_fingerprint": expected_after,
        }
        envelope = _envelope(
            baseline,
            [expectation],
        )
        self.assertNotIn("candidate_snapshot_id", envelope["binding"])
        self.assertNotIn(
            "candidate_snapshot_id",
            inspect.signature(build_change_envelope).parameters,
        )

        candidate = _snapshot(
            effects=[planned_candidate_effect],
            capture_id="conform-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )

        result = _compare(
            baseline,
            candidate,
            change_envelope=envelope,
        )

        assessment = result["envelope_assessment"]
        self.assertEqual(result["summary"]["state"], "matches-declared-envelope")
        self.assertEqual(len(assessment["expected_and_observed_comparison_ids"]), 1)
        self.assertEqual(assessment["unexpected_observed_comparison_ids"], [])
        self.assertEqual(assessment["expected_not_observed"], [])
        self.assertEqual(assessment["unsupported_or_unobservable"], [])
        self.assertEqual(assessment["expectation_mismatches"], [])
        self.assertEqual(result["change_envelope"], envelope)

    def test_empty_coverage_can_never_support_envelope_conformance(self) -> None:
        baseline = _snapshot(coverage=[], capture_id="empty-envelope-before")
        envelope = _envelope(baseline, [])
        candidate = _snapshot(
            coverage=[],
            capture_id="empty-envelope-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )

        with self.assertRaises(BoundedEffectComparisonError) as caught:
            compare_bounded_effects(
                baseline,
                candidate,
                adapters=(),
                change_envelope=envelope,
            )

        self.assertEqual(caught.exception.code, "incomparable.empty-coverage")

    def test_open_envelope_is_not_eligible_for_comparison(self) -> None:
        baseline = _snapshot(
            coverage=_coverage("gt-recipe"),
            capture_id="open-envelope-before",
        )
        envelope = build_change_envelope(
            owner_id="fixture-change-owner-v1",
            baseline_snapshot_id=baseline["snapshot_id"],
            baseline_capture_id=baseline["binding"]["capture"]["capture_id"],
            context_id=CONTEXT,
            pack_profile_id=PACK,
            platform_profile_id=PLATFORM,
            physical_side=SIDE,
            stage=STAGE,
            expectations=[],
            state="open",
        )
        candidate = _snapshot(
            coverage=_coverage("gt-recipe"),
            capture_id="open-envelope-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )

        with self.assertRaises(BoundedEffectComparisonError) as caught:
            _compare(baseline, candidate, change_envelope=envelope)

        self.assertEqual(caught.exception.code, "incomparable.change-envelope-open")

    def test_candidate_capture_must_declare_exact_closed_envelope(self) -> None:
        baseline = _snapshot(
            coverage=_coverage("gt-recipe"),
            capture_id="declaration-before",
        )
        envelope = _envelope(baseline, [])
        candidates = (
            _snapshot(
                coverage=_coverage("gt-recipe"),
                capture_id="declaration-missing",
            ),
            _snapshot(
                coverage=_coverage("gt-recipe"),
                capture_id="declaration-wrong",
                declared_change_envelope_id=(
                    "workbench-process-studio-effect-change-envelope-v2:sha256:"
                    + "0" * 64
                ),
            ),
        )
        for candidate in candidates:
            with self.subTest(
                declared=candidate["binding"]["capture"][
                    "declared_change_envelope_id"
                ]
            ):
                with self.assertRaises(BoundedEffectComparisonError) as caught:
                    _compare(baseline, candidate, change_envelope=envelope)
                self.assertEqual(
                    caught.exception.code,
                    "incomparable.candidate-envelope",
                )

    def test_mismatch_report_keeps_all_four_observable_categories_distinct(self) -> None:
        baseline = _snapshot(
            effects=[
                _effect(_gt_descriptor("matched"), {"eu": 8}),
                _effect(_gt_descriptor("wrong-class"), {"eu": 8}),
                _effect(_gt_descriptor("unlisted"), {"eu": 8}),
            ],
            capture_id="categories-before",
        )
        candidate = _snapshot(
            effects=[
                _effect(_gt_descriptor("matched"), {"eu": 16}),
                _effect(_gt_descriptor("wrong-class"), {"eu": 16}),
            ],
            capture_id="categories-after",
        )
        comparison = _compare(baseline, candidate)
        rows = {
            row["correlation"]["recipe_id"]: row
            for row in comparison["comparisons"]
        }
        missing = {
            "family": "gt-recipe",
            "record_kind": "effect-observation",
            "descriptor_kind": "finite-recipe",
            "operation": "register",
            "correlation": {
                "recipe_id": "not-observed",
                "recipe_map": "assembler",
            },
            "baseline_semantic_fingerprint": None,
            "candidate_semantic_fingerprint": "a" * 64,
        }
        envelope = _envelope(
            baseline,
            [
                _expectation(rows["matched"]),
                _expectation(
                    rows["wrong-class"],
                    baseline_fingerprint=None,
                ),
                missing,
            ],
        )

        candidate = _snapshot(
            effects=[
                _effect(_gt_descriptor("matched"), {"eu": 16}),
                _effect(_gt_descriptor("wrong-class"), {"eu": 16}),
            ],
            capture_id="categories-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )

        result = _compare(
            baseline,
            candidate,
            change_envelope=envelope,
        )

        assessment = result["envelope_assessment"]
        self.assertEqual(result["summary"]["state"], "mismatch")
        self.assertEqual(len(assessment["expected_and_observed_comparison_ids"]), 1)
        self.assertEqual(len(assessment["unexpected_observed_comparison_ids"]), 1)
        self.assertEqual(len(assessment["expected_not_observed"]), 1)
        self.assertEqual(assessment["unsupported_or_unobservable"], [])
        self.assertEqual(len(assessment["expectation_mismatches"]), 1)

    def test_incomplete_coverage_is_unsupported_and_unresolved(self) -> None:
        coverage = _coverage("gt-recipe", state="partial")
        descriptor = _gt_descriptor("partial")
        baseline = _snapshot(
            effects=[_effect(descriptor, {"eu": 8})],
            coverage=coverage,
            capture_id="partial-before",
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            coverage=coverage,
            capture_id="partial-after",
        )
        comparison = _compare(baseline, candidate)
        envelope = _envelope(
            baseline,
            [_expectation(comparison["comparisons"][0])],
        )

        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            coverage=coverage,
            capture_id="partial-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )

        result = _compare(
            baseline,
            candidate,
            change_envelope=envelope,
        )

        self.assertEqual(result["summary"]["state"], "unresolved")
        unsupported = result["envelope_assessment"]["unsupported_or_unobservable"]
        self.assertTrue(unsupported)
        self.assertTrue(any(item["reason"] == "coverage-partial" for item in unsupported))
        self.assertEqual(result["envelope_assessment"]["expected_not_observed"], [])


class SchemaContractTests(unittest.TestCase):
    def test_closed_schemas_admit_semantically_valid_documents(self) -> None:
        descriptor = _gt_descriptor("schema-fixture")
        baseline = _snapshot(
            effects=[_effect(descriptor, {"eu": 8})],
            capture_id="schema-before",
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            capture_id="schema-after",
        )
        projection = _project(baseline)
        comparison_only = _compare(baseline, candidate)
        envelope = _envelope(
            baseline,
            [_expectation(comparison_only["comparisons"][0])],
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            capture_id="schema-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )
        comparison = _compare(
            baseline,
            candidate,
            change_envelope=envelope,
        )
        documents = {
            "bounded-effect-snapshot-projection-v2.schema.json": projection,
            "effect-change-envelope-v2.schema.json": envelope,
            "bounded-observed-effect-comparison-v2.schema.json": comparison,
        }
        for schema_name, document in documents.items():
            with self.subTest(schema=schema_name):
                self.assertEqual(
                    document["canonicalizer"],
                    process_studio.CANONICALIZER_ID,
                )
                schema = json.loads(
                    (MODULE_ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
                )
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(document)
        for document, id_key in (
            (projection, "projection_id"),
            (envelope, "envelope_id"),
            (comparison, "comparison_id"),
        ):
            payload = deepcopy(document)
            actual = payload.pop(id_key)
            self.assertEqual(actual, content_id(document["format"], payload))
        self.assertTrue(
            all(
                row["canonicalizer"] == process_studio.CANONICALIZER_ID
                for row in comparison["comparisons"]
            )
        )

    def test_empty_unresolved_rows_are_consistent_with_closed_schemas(self) -> None:
        baseline = _snapshot(
            effects=[
                _effect(
                    _gt_descriptor(
                        None,
                        opaque_key="no-admitted-correlation",
                    ),
                    {"eu": 8},
                )
            ],
            capture_id="unresolved-schema-before",
        )
        candidate = _snapshot(capture_id="unresolved-schema-after")
        projection = _project(baseline)
        comparison = _compare(baseline, candidate)
        self.assertEqual(projection["records"][0]["projection_state"], "unresolved")
        self.assertIsNone(projection["records"][0]["correlation"])
        self.assertEqual(comparison["comparisons"][0]["classification"], "unresolved")
        self.assertIsNone(comparison["comparisons"][0]["correlation"])
        documents = {
            "bounded-effect-snapshot-projection-v2.schema.json": projection,
            "bounded-observed-effect-comparison-v2.schema.json": comparison,
        }
        for schema_name, document in documents.items():
            with self.subTest(schema=schema_name):
                schema = json.loads(
                    (MODULE_ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
                )
                Draft202012Validator(schema).validate(document)


class TrustBoundaryTests(unittest.TestCase):
    def test_projection_only_comparison_is_not_a_public_trusted_entry_point(self) -> None:
        self.assertFalse(hasattr(process_studio, "compare_effect_projections"))
        self.assertNotIn("compare_effect_projections", process_studio.__all__)

    def test_fake_envelope_identity_and_reopened_envelope_are_rejected(self) -> None:
        baseline = _snapshot(
            coverage=_coverage("gt-recipe"),
            capture_id="envelope-integrity-before",
        )
        envelope = _envelope(baseline, [])
        fake_id = deepcopy(envelope)
        fake_id["envelope_id"] = (
            "workbench-process-studio-effect-change-envelope-v2:sha256:" + "0" * 64
        )
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            validate_change_envelope_shape(fake_id)
        self.assertEqual(caught.exception.code, "envelope.identity")

        reopened = deepcopy(envelope)
        reopened["hostile_extra"] = True
        _reseal_content(
            reopened,
            id_key="envelope_id",
            prefix="workbench-process-studio-effect-change-envelope-v2:sha256:",
        )
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            validate_change_envelope_shape(reopened)
        self.assertEqual(caught.exception.code, "envelope.invalid")

    def test_resealed_modified_to_unchanged_row_fails_shape_semantics(self) -> None:
        descriptor = _gt_descriptor("row-semantics")
        baseline = _snapshot(
            effects=[_effect(descriptor, {"eu": 8})],
            capture_id="row-semantics-before",
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            capture_id="row-semantics-after",
        )
        attacked = deepcopy(_compare(baseline, candidate))
        row = attacked["comparisons"][0]
        self.assertEqual(row["classification"], "modified")
        row["classification"] = "unchanged"
        row["reason"] = "semantic-fingerprint-equal"
        _reseal_content(
            row,
            id_key="comparison_id",
            prefix="workbench-process-studio-effect-comparison-v2:sha256:",
        )
        attacked["summary"]["by_classification"]["modified"] = 0
        attacked["summary"]["by_classification"]["unchanged"] = 1
        attacked["summary"]["structural_changes"] = 0
        _reseal_content(
            attacked,
            id_key="comparison_id",
            prefix=(
                "workbench-process-studio-bounded-observed-effect-comparison-v2:sha256:"
            ),
        )

        with self.assertRaises(BoundedEffectComparisonError) as caught:
            validate_observed_effect_comparison_shape(attacked)
        self.assertEqual(caught.exception.code, "result.row-semantics")

    def test_resealed_mismatch_to_match_fails_assessment_rederivation(self) -> None:
        descriptor = _gt_descriptor("assessment-semantics")
        baseline = _snapshot(
            effects=[_effect(descriptor, {"eu": 8})],
            capture_id="assessment-semantics-before",
        )
        observed_candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            capture_id="assessment-semantics-after-unbound",
        )
        row = _compare(baseline, observed_candidate)["comparisons"][0]
        wrong_fingerprint = "0" * 64
        if row["candidate_semantic_fingerprints"] == [wrong_fingerprint]:
            wrong_fingerprint = "1" * 64
        envelope = _envelope(
            baseline,
            [
                _expectation(
                    row,
                    candidate_fingerprint=wrong_fingerprint,
                )
            ],
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            capture_id="assessment-semantics-after",
            declared_change_envelope_id=envelope["envelope_id"],
        )
        attacked = deepcopy(
            _compare(baseline, candidate, change_envelope=envelope)
        )
        self.assertEqual(attacked["summary"]["state"], "mismatch")
        assessment = attacked["envelope_assessment"]
        assessment["state"] = "matches-declared-envelope"
        assessment["expected_and_observed_comparison_ids"] = [
            attacked["comparisons"][0]["comparison_id"]
        ]
        assessment["expectation_mismatches"] = []
        attacked["summary"]["state"] = "matches-declared-envelope"
        _reseal_content(
            attacked,
            id_key="comparison_id",
            prefix=(
                "workbench-process-studio-bounded-observed-effect-comparison-v2:sha256:"
            ),
        )

        with self.assertRaises(BoundedEffectComparisonError) as caught:
            validate_observed_effect_comparison_shape(attacked)
        self.assertEqual(caught.exception.code, "result.assessment-semantics")

    def test_exact_envelope_id_syntax_is_required_at_every_projection_boundary(self) -> None:
        malformed_ids = (
            "other-envelope-v2:sha256:" + "0" * 64,
            "workbench-process-studio-effect-change-envelope-v2:sha256:" + "A" * 64,
            "workbench-process-studio-effect-change-envelope-v2:sha256:" + "0" * 63,
        )
        for index, malformed in enumerate(malformed_ids):
            with self.subTest(malformed=malformed):
                snapshot = _snapshot(
                    capture_id=f"bad-envelope-id-{index}",
                    declared_change_envelope_id=malformed,
                )
                with self.assertRaises(BoundedEffectComparisonError) as caught:
                    _project(snapshot)
                self.assertEqual(caught.exception.code, "envelope.id-syntax")

        projection = _project(_snapshot(capture_id="projection-id-syntax"))
        projection["binding"]["declared_change_envelope_id"] = malformed_ids[0]
        _reseal_content(
            projection,
            id_key="projection_id",
            prefix="workbench-process-studio-effect-snapshot-projection-v2:sha256:",
        )
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            process_studio.validate_effect_snapshot_projection_shape(projection)
        self.assertEqual(caught.exception.code, "envelope.id-syntax")
        projection_schema = json.loads(
            (
                MODULE_ROOT
                / "schemas"
                / "bounded-effect-snapshot-projection-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(
            list(Draft202012Validator(projection_schema).iter_errors(projection))
        )

    def test_shape_valid_result_reopening_fails_source_bound_replay(self) -> None:
        descriptor = _gt_descriptor("source-bound")
        baseline = _snapshot(
            effects=[_effect(descriptor, {"eu": 8})],
            capture_id="source-bound-before",
        )
        candidate = _snapshot(
            effects=[_effect(descriptor, {"eu": 16})],
            capture_id="source-bound-after",
        )
        genuine = _compare(baseline, candidate)
        self.assertEqual(
            validate_source_bound_comparison(
                genuine,
                baseline_snapshot=baseline,
                candidate_snapshot=candidate,
                adapters=SYNTHETIC_FIXTURE_ADAPTERS,
            ),
            genuine,
        )

        reopened = deepcopy(genuine)
        row = reopened["comparisons"][0]
        replacement = "f" * 64
        if row["baseline_semantic_fingerprints"] == [replacement]:
            replacement = "e" * 64
        row["candidate_semantic_fingerprints"] = [replacement]
        _reseal_content(
            row,
            id_key="comparison_id",
            prefix="workbench-process-studio-effect-comparison-v2:sha256:",
        )
        _reseal_content(
            reopened,
            id_key="comparison_id",
            prefix=(
                "workbench-process-studio-bounded-observed-effect-comparison-v2:sha256:"
            ),
        )
        # Shape validation has no source authority and therefore cannot replay it.
        self.assertEqual(
            validate_observed_effect_comparison_shape(reopened),
            reopened,
        )
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            validate_source_bound_comparison(
                reopened,
                baseline_snapshot=baseline,
                candidate_snapshot=candidate,
                adapters=SYNTHETIC_FIXTURE_ADAPTERS,
            )
        self.assertEqual(caught.exception.code, "result.source-binding")


class AlignmentAndPolicyTests(unittest.TestCase):
    def _assert_incomparable(
        self,
        expected_code: str,
        baseline: dict[str, object],
        candidate: dict[str, object],
    ) -> None:
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            _compare(baseline, candidate)
        self.assertEqual(caught.exception.code, expected_code)

    def test_alignment_mismatches_fail_closed(self) -> None:
        cases = {
            "incomparable.context_id": (
                _snapshot(context="before", capture_id="context-before"),
                _snapshot(context="after", capture_id="context-after"),
            ),
            "incomparable.pack_profile_id": (
                _snapshot(pack="pack-before", capture_id="pack-before"),
                _snapshot(pack="pack-after", capture_id="pack-after"),
            ),
            "incomparable.platform_profile_id": (
                _snapshot(platform="platform-before", capture_id="platform-before"),
                _snapshot(platform="platform-after", capture_id="platform-after"),
            ),
            "incomparable.physical_side": (
                _snapshot(side="server", capture_id="side-before"),
                _snapshot(side="client", capture_id="side-after"),
            ),
            "incomparable.stage": (
                _snapshot(stage="pre-init", capture_id="stage-before"),
                _snapshot(stage="post-init", capture_id="stage-after"),
            ),
            "incomparable.coverage": (
                _snapshot(
                    coverage=_coverage("gt-recipe"),
                    capture_id="coverage-before",
                ),
                _snapshot(
                    coverage=_coverage("forge-crafting", "gt-recipe"),
                    capture_id="coverage-after",
                ),
            ),
        }
        for code, (baseline, candidate) in cases.items():
            with self.subTest(code=code):
                self._assert_incomparable(code, baseline, candidate)

    def test_adapter_encoder_or_policy_mismatch_is_rejected(self) -> None:
        coverage = _coverage(
            "gt-recipe",
            overrides={
                "gt-recipe": {
                    "correlation_policy_id": "hostile-policy:v999",
                }
            },
        )
        snapshot = _snapshot(coverage=coverage)

        with self.assertRaises(BoundedEffectComparisonError) as caught:
            _project(snapshot)

        self.assertEqual(caught.exception.code, "adapter.policy-mismatch")


class StrictCrucibleProjectionTests(unittest.TestCase):
    def _assert_rejected(
        self,
        expected_code: str,
        snapshot: dict[str, object],
    ) -> None:
        with self.assertRaises(BoundedEffectComparisonError) as caught:
            _project(snapshot)
        self.assertEqual(caught.exception.code, expected_code)

    def test_mutated_exact_authority_is_rejected_after_valid_reseal(self) -> None:
        snapshot = _snapshot()
        snapshot["authority"]["claim"] = "hostile widened runtime truth"
        _reseal_snapshot(snapshot)

        self._assert_rejected("snapshot.authority", snapshot)

    def test_stale_effects_by_operation_is_rejected_after_valid_reseal(self) -> None:
        snapshot = _snapshot(
            effects=[_effect(_gt_descriptor("summary"), {"eu": 8})]
        )
        snapshot["summary"]["effects_by_operation"] = {}
        _reseal_snapshot(snapshot)

        self._assert_rejected("snapshot.summary", snapshot)

    def test_extra_and_empty_capture_are_rejected(self) -> None:
        extra = _capture()
        extra["hostile_extra"] = "smuggled"
        cases = (
            _snapshot(capture=extra),
            _snapshot(capture={}),
        )
        for snapshot in cases:
            with self.subTest(capture=snapshot["binding"]["capture"]):
                self._assert_rejected("snapshot.capture", snapshot)

    def test_empty_observation_state_is_rejected(self) -> None:
        snapshot = _snapshot(
            effects=[_effect(_gt_descriptor("empty-state"), {})]
        )

        self._assert_rejected("snapshot.state", snapshot)

    def test_floating_point_state_is_rejected_by_closed_projection(self) -> None:
        snapshot = _snapshot(
            effects=[_effect(_gt_descriptor("float-state"), {"chance": 0.5})]
        )

        self._assert_rejected("json.float", snapshot)

    def test_cycles_depth_and_unsafe_integers_are_hard_rejected(self) -> None:
        cyclic = _snapshot(
            effects=[_effect(_gt_descriptor("cycle"), {"eu": 8})]
        )
        cyclic_state = cyclic["effects"][0]["effect_state"]
        cyclic_state["cycle"] = cyclic_state

        nested: dict[str, object] = {}
        cursor = nested
        for _ in range(80):
            child: dict[str, object] = {}
            cursor["next"] = child
            cursor = child
        too_deep = _snapshot(
            effects=[_effect(_gt_descriptor("deep"), nested)]
        )
        unsafe_integer = _snapshot(
            effects=[
                _effect(
                    _gt_descriptor("unsafe-int"),
                    {"eu": 1 << 63},
                )
            ]
        )
        cases = (
            ("json.cycle", cyclic),
            ("json.depth", too_deep),
            ("json.integer", unsafe_integer),
        )
        for code, snapshot in cases:
            with self.subTest(code=code):
                self._assert_rejected(code, snapshot)

    def test_requested_bounds_cannot_raise_hard_maxima(self) -> None:
        hard = DEFAULT_BOUNDS.as_dict()
        for field in hard:
            values = dict(hard)
            values[field] += 1
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    ComparisonBounds(**values)

    def test_empty_and_non_string_operations_are_rejected(self) -> None:
        empty = _snapshot(
            effects=[
                _effect(
                    _gt_descriptor("empty-operation"),
                    {"eu": 8},
                    operation="",
                )
            ]
        )
        non_string_record = effect_observation(
            stage=STAGE,
            semantic_descriptor=_gt_descriptor("non-string-operation"),
            operation=7,  # type: ignore[arg-type]
            effect_state={"eu": 8},
            provenance={"source": "fixture"},
        )
        non_string = _snapshot(effects=[non_string_record])

        for snapshot in (empty, non_string):
            with self.subTest(operation=snapshot["effects"][0]["operation"]):
                self._assert_rejected("value.string", snapshot)


class CliTests(unittest.TestCase):
    def test_public_parser_uses_envelope_flag(self) -> None:
        option_strings = {
            option
            for action in build_parser()._actions
            for option in action.option_strings
        }
        self.assertIn("--envelope", option_strings)
        self.assertNotIn("--change-envelope", option_strings)

    def test_main_supports_keyword_only_root_and_comparison_only_json(self) -> None:
        signature = inspect.signature(cli_main)
        self.assertEqual(signature.parameters["root"].kind, inspect.Parameter.KEYWORD_ONLY)
        baseline = _snapshot(capture_id="cli-before")
        candidate = _snapshot(capture_id="cli-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            (root / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "baseline.json",
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                        "--json",
                    ],
                    root=root,
                )

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["summary"]["state"], "comparison-only")

    def test_cli_requires_explicit_fixture_adapter_opt_in(self) -> None:
        baseline = _snapshot(capture_id="cli-no-fixture-before")
        candidate = _snapshot(capture_id="cli-no-fixture-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            (root / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "baseline.json",
                        "--candidate",
                        "candidate.json",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("adapter.unavailable", stderr.getvalue())

    def test_cli_refuses_duplicate_json_object_keys(self) -> None:
        candidate = _snapshot(capture_id="duplicate-json-candidate")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            (root / "baseline.json").write_text(
                '{"format":"x","format":"y"}', encoding="utf-8"
            )
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "baseline.json",
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("json.invalid", stderr.getvalue())
        self.assertIn("duplicate object key", stderr.getvalue())

    def test_cli_refuses_lone_unicode_surrogate_without_traceback(self) -> None:
        candidate = _snapshot(capture_id="surrogate-json-candidate")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            (root / "baseline.json").write_bytes(b'{"value":"\\ud800"}')
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "baseline.json",
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("json.invalid", stderr.getvalue())
        self.assertIn("surrogate code point", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    def test_cli_refuses_symbolic_link_input(self) -> None:
        baseline = _snapshot(capture_id="symlink-before")
        candidate = _snapshot(capture_id="symlink-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            (root / "real-baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (root / "baseline.json").symlink_to(root / "real-baseline.json")
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "baseline.json",
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("input.symlink", stderr.getvalue())

    @unittest.skipUnless(
        cli_module._FD_RELATIVE_READ_AVAILABLE and hasattr(os, "symlink"),
        "fd-relative symbolic-link custody unavailable",
    )
    def test_cli_refuses_symbolic_link_input_parent(self) -> None:
        baseline = _snapshot(capture_id="parent-symlink-before")
        candidate = _snapshot(capture_id="parent-symlink-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            actual = root / "actual"
            actual.mkdir()
            (actual / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (root / "linked").symlink_to(actual, target_is_directory=True)
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "linked/baseline.json",
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("input.symlink", stderr.getvalue())

    def test_cli_refuses_input_outside_pinned_root(self) -> None:
        baseline = _snapshot(capture_id="outside-root-before")
        candidate = _snapshot(capture_id="outside-root-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            container = Path(temporary)
            root = container / "root"
            root.mkdir()
            outside = container / "outside.json"
            outside.write_text(json.dumps(baseline), encoding="utf-8")
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        os.fspath(outside),
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("input.unavailable", stderr.getvalue())
        self.assertIn("outside the pinned CLI root", stderr.getvalue())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs unavailable")
    def test_cli_refuses_fifo_input_without_opening_it(self) -> None:
        candidate = _snapshot(capture_id="fifo-candidate")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            try:
                os.mkfifo(root / "baseline.json")
            except OSError as exc:
                self.skipTest(f"filesystem cannot create a FIFO: {exc}")
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = cli_main(
                    [
                        "--baseline",
                        "baseline.json",
                        "--candidate",
                        "candidate.json",
                        "--fixture-adapters",
                    ],
                    root=root,
                )

        self.assertEqual(code, 2)
        self.assertIn("input.type", stderr.getvalue())

    def test_cli_rejects_input_namespace_swap_during_read(self) -> None:
        baseline = _snapshot(capture_id="namespace-before")
        replacement = _snapshot(capture_id="namespace-replacement")
        candidate = _snapshot(capture_id="namespace-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            baseline_path = root / "baseline.json"
            replacement_path = root / "replacement.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            replacement_path.write_text(json.dumps(replacement), encoding="utf-8")
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            original_read = os.read
            swapped = False

            def racing_read(descriptor: int, length: int) -> bytes:
                nonlocal swapped
                chunk = original_read(descriptor, length)
                if not swapped:
                    swapped = True
                    os.replace(replacement_path, baseline_path)
                return chunk

            stderr = io.StringIO()
            with mock.patch.object(cli_module.os, "read", side_effect=racing_read):
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    code = cli_main(
                        [
                            "--baseline",
                            "baseline.json",
                            "--candidate",
                            "candidate.json",
                            "--fixture-adapters",
                        ],
                        root=root,
                    )

        self.assertTrue(swapped)
        self.assertEqual(code, 2)
        self.assertIn("input.changed", stderr.getvalue())

    @unittest.skipUnless(
        cli_module._FD_RELATIVE_READ_AVAILABLE
        and hasattr(os, "link")
        and hasattr(os, "symlink"),
        "fd-relative namespace custody unavailable",
    )
    def test_cli_rejects_input_parent_swap_even_when_file_inode_is_retained(self) -> None:
        baseline = _snapshot(capture_id="parent-swap-before")
        candidate = _snapshot(capture_id="parent-swap-after")
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            current = root / "current"
            retired = root / "retired"
            attacker = root / "attacker"
            current.mkdir()
            attacker.mkdir()
            baseline_path = current / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            os.link(baseline_path, attacker / "baseline.json")
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            original_read = os.read
            swapped = False

            def racing_read(descriptor: int, length: int) -> bytes:
                nonlocal swapped
                chunk = original_read(descriptor, length)
                if not swapped:
                    swapped = True
                    current.rename(retired)
                    current.symlink_to(attacker, target_is_directory=True)
                return chunk

            stderr = io.StringIO()
            with mock.patch.object(cli_module.os, "read", side_effect=racing_read):
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    code = cli_main(
                        [
                            "--baseline",
                            "current/baseline.json",
                            "--candidate",
                            "candidate.json",
                            "--fixture-adapters",
                        ],
                        root=root,
                    )

        self.assertTrue(swapped)
        self.assertEqual(code, 2)
        self.assertIn("input.changed", stderr.getvalue())

    def test_cli_output_publication_never_clobbers_concurrent_winner(self) -> None:
        baseline = _snapshot(capture_id="output-race-before")
        candidate = _snapshot(capture_id="output-race-after")
        winner = b"concurrent-winner-must-remain\n"
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            (root / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            output_path = root / "comparison.json"
            original_link = os.link
            raced = False

            def racing_link(source: object, destination: object, *args: object, **kwargs: object) -> None:
                nonlocal raced
                if not raced:
                    raced = True
                    output_path.write_bytes(winner)
                original_link(source, destination, *args, **kwargs)

            stderr = io.StringIO()
            with mock.patch.object(cli_module.os, "link", side_effect=racing_link):
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    code = cli_main(
                        [
                            "--baseline",
                            "baseline.json",
                            "--candidate",
                            "candidate.json",
                            "--fixture-adapters",
                            "--output",
                            "comparison.json",
                        ],
                        root=root,
                    )

            self.assertTrue(raced)
            self.assertEqual(code, 2)
            self.assertIn("output.exists", stderr.getvalue())
            self.assertEqual(output_path.read_bytes(), winner)
            self.assertEqual(list(root.glob(".comparison.json.*.tmp")), [])

    @unittest.skipUnless(
        cli_module._FD_RELATIVE_PUBLICATION_AVAILABLE
        and hasattr(os, "symlink"),
        "fd-relative publication custody unavailable",
    )
    def test_cli_rejects_output_parent_swap_without_publishing_attacker_bytes(self) -> None:
        baseline = _snapshot(capture_id="output-parent-swap-before")
        candidate = _snapshot(capture_id="output-parent-swap-after")
        attacker_bytes = b"attacker-controlled-output\n"
        with tempfile.TemporaryDirectory(dir=MODULE_ROOT) as temporary:
            root = Path(temporary)
            reports = root / "reports"
            retired = root / "retired-reports"
            attacker = root / "attacker"
            reports.mkdir()
            attacker.mkdir()
            (root / "baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8"
            )
            (root / "candidate.json").write_text(
                json.dumps(candidate), encoding="utf-8"
            )
            original_link = os.link
            swapped = False

            def racing_link(
                source: object,
                destination: object,
                *args: object,
                **kwargs: object,
            ) -> None:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    reports.rename(retired)
                    reports.symlink_to(attacker, target_is_directory=True)
                    (attacker / Path(os.fspath(source)).name).write_bytes(
                        attacker_bytes
                    )
                original_link(source, destination, *args, **kwargs)

            stderr = io.StringIO()
            with mock.patch.object(cli_module.os, "link", side_effect=racing_link):
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    code = cli_main(
                        [
                            "--baseline",
                            "baseline.json",
                            "--candidate",
                            "candidate.json",
                            "--fixture-adapters",
                            "--output",
                            "reports/comparison.json",
                        ],
                        root=root,
                    )

            self.assertTrue(swapped)
            self.assertEqual(code, 2)
            self.assertIn("output.changed", stderr.getvalue())
            self.assertFalse((attacker / "comparison.json").exists())
            self.assertNotEqual(
                (retired / "comparison.json").read_bytes(),
                attacker_bytes,
            )
            self.assertEqual(
                list(retired.glob(".workbench-process-studio-*.tmp")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
