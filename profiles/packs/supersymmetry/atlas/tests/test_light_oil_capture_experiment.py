from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[5]
for path in (
    ROOT / "modules/atlas/src/workbench_atlas",
    ROOT / "profiles/packs/supersymmetry/atlas/src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import light_oil_capture_experiment as experiment  # noqa: E402


def _header() -> dict[str, int]:
    return {
        "sqlite_schema_format": experiment.EXPECTED_SQLITE_SCHEMA_FORMAT,
        "sqlite_user_version": experiment.EXPECTED_PROJECTION_IDENTITY[
            "sqlite_user_version"
        ],
        "sqlite_application_id": experiment.EXPECTED_PROJECTION_IDENTITY[
            "sqlite_application_id"
        ],
        "page_size": 4096,
        "page_count": 8_166_377,
    }


def _chain() -> dict[str, object]:
    return {
        "target": {},
        "profile_scope": [],
        "roots": ["root"],
        "subproblems": [{}],
        "routes": [{}],
        "cycles": [],
        "unresolved_leaves": [],
        "truncation": {
            "truncated": False,
            "reasons": [],
            "limits": experiment.CHAIN_OPTIONS.limits(),
        },
    }


class _Reader:
    def __init__(self, harness: "_Harness") -> None:
        self.harness = harness
        self.closed = False

    def projection_identity(self) -> dict[str, object]:
        return copy.deepcopy(self.harness.projection_identity)

    def close(self) -> None:
        self.closed = True


class _Domain:
    def __init__(self, harness: "_Harness") -> None:
        self.harness = harness

    def producers(self, selector: object, **_: object) -> object:
        return self.harness.query(selector, "producer")

    def consumers(self, selector: object, **_: object) -> object:
        return self.harness.query(selector, "consumer")


class _Harness:
    def __init__(self) -> None:
        self.projection_identity = copy.deepcopy(
            experiment.EXPECTED_PROJECTION_IDENTITY
        )
        self.header = _header()
        self.chain_result = _chain()
        self.selector_disagreement = False
        self.recipe_drift = False
        self.amount_drift = False

    @staticmethod
    def _target(selector: object) -> experiment.TargetSpec:
        value = getattr(selector, "key_value")
        if value in {
            experiment.DILUTED_MATERIAL_KEY,
            experiment.DILUTED_FLUID_KEY,
            experiment.DILUTED_VARIANT_ID,
        }:
            return experiment.DILUTED_LIGHT_OIL
        return experiment.LIGHT_OIL

    @staticmethod
    def _target_node(
        selector: object,
        target: experiment.TargetSpec,
    ) -> tuple[str, str]:
        key_kind = getattr(selector, "key_kind")
        if key_kind == "material-resource-location":
            return target.material_id, "material"
        if key_kind == "fluid-name":
            return target.fluid_id, "fluid"
        return target.variant_id, "fluid_variant"

    def query(self, selector: object, direction: str) -> object:
        target = self._target(selector)
        target_id, target_kind = self._target_node(selector, target)
        owner_ids = (
            target.producer_ids
            if direction == "producer"
            else target.consumer_ids
        )
        owner_kind = (
            target.producer_kind
            if direction == "producer"
            else target.consumer_kind
        )
        channel = (
            target.producer_channel
            if direction == "producer"
            else target.consumer_channel
        )
        quantity = dict(
            target.producer_quantity
            if direction == "producer"
            else target.consumer_quantity
        )
        items = []
        for index, original_owner_id in enumerate(owner_ids):
            owner_id = original_owner_id
            if (
                self.recipe_drift
                and target is experiment.LIGHT_OIL
                and direction == "consumer"
                and index == 0
            ):
                owner_id = original_owner_id + ":drift"
            attributes = {"channel": channel, **quantity}
            if (
                self.amount_drift
                and target is experiment.DILUTED_LIGHT_OIL
                and direction == "producer"
                and index == 0
            ):
                attributes["amount"] = experiment.DILUTED_AMOUNT_MB + 1
            slot_id = f"slot:{target.label}:{direction}:{index}"
            if (
                self.selector_disagreement
                and target is experiment.LIGHT_OIL
                and getattr(selector, "key_kind") == "fluid-name"
                and direction == "producer"
            ):
                slot_id += ":disagrees"
            predicate = "produces" if direction == "producer" else "consumes"
            items.append(
                {
                    "owner": {"id": owner_id, "kind": owner_kind},
                    "owner_relationship": {
                        "subject": owner_id,
                        "object": slot_id,
                        "predicate": predicate,
                    },
                    "slot": {"id": slot_id, "attributes": attributes},
                    "matched_alternatives": [
                        {"node": {"id": target.variant_id}}
                    ],
                }
            )
        resolved = SimpleNamespace(
            selector=selector,
            gaps=(),
            targets=(
                SimpleNamespace(
                    scope=experiment.ProfileScope(
                        experiment.PROFILE,
                        experiment.PHYSICAL_SIDE,
                    ),
                    node={"id": target_id, "kind": target_kind},
                ),
            ),
        )
        page = SimpleNamespace(
            items=tuple(items),
            total=len(items),
            truncated=False,
        )
        return SimpleNamespace(resolution=resolved, page=page)

    def reader(self, _: object) -> _Reader:
        return _Reader(self)

    def domain(self, _: object) -> _Domain:
        return _Domain(self)

    def chain_query(self, _: object) -> object:
        return SimpleNamespace(
            chain=lambda *_args, **_kwargs: copy.deepcopy(self.chain_result)
        )


class LightOilCaptureExperimentTests(unittest.TestCase):
    def _run(
        self,
        harness: _Harness,
        *,
        repetitions: int = 1,
    ) -> experiment.ExperimentResult:
        chain_digest = hashlib.sha256(
            experiment.canonical_json_bytes(harness.chain_result)
        ).hexdigest()
        chain_bytes = len(experiment.canonical_json_bytes(harness.chain_result))
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "RuntimeGraphReader",
                    side_effect=harness.reader,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "RuntimeGraphDomainQuery",
                    side_effect=harness.domain,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "RuntimeGraphChainQuery",
                    side_effect=harness.chain_query,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "validate_process_chain_result",
                )
            )
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "_read_sqlite_header",
                    return_value=copy.deepcopy(harness.header),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "EXPECTED_CHAIN_RESULT_SHA256",
                    chain_digest,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    experiment,
                    "EXPECTED_CHAIN_RESULT_BYTES",
                    chain_bytes,
                )
            )
            return experiment.run_experiment(
                ROOT / ".workbench/fixture.sqlite",
                repetitions=repetitions,
            )

    def test_capture_keeps_light_oil_distinct_from_diluted_checkpoint(self) -> None:
        result = self._run(_Harness())
        semantic = result.semantic

        self.assertEqual("Light Oil", semantic["target"]["name"])
        self.assertEqual(
            experiment.LIGHT_OIL_MATERIAL_KEY,
            semantic["target"]["material_key"],
        )
        light = semantic["light_oil_capture"]
        diluted = semantic["diluted_light_oil_downstream_checkpoint"]
        self.assertEqual("light-oil", light["target"])
        self.assertEqual("diluted-light-oil", diluted["target"])
        self.assertEqual(
            [(1, 2), (1, 2), (1, 2)],
            [
                (row["producer_count"], row["consumer_count"])
                for row in light["routes"]
            ],
        )
        self.assertEqual(
            [(2, 1), (2, 1), (2, 1)],
            [
                (row["producer_count"], row["consumer_count"])
                for row in diluted["routes"]
            ],
        )
        link = semantic["downstream_recipe_links"]
        self.assertEqual(experiment.MIXER_RECIPE_ID, link["explicit_mixer_recipe_id"])
        self.assertEqual(1000, link["input_amount_mb"])
        self.assertEqual(1100, link["output_amount_mb"])
        traversal = semantic["bounded_traversal"]
        self.assertTrue(traversal["bounded_result_closed"])
        self.assertFalse(traversal["full_recipe_traversal_claim"])
        self.assertFalse(traversal["realized_extraction_observed"])

    def test_projection_identity_and_sqlite_schema_fail_closed(self) -> None:
        identity_drift = _Harness()
        identity_drift.projection_identity["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            experiment.LightOilCaptureError,
            "exact EVID-9089 projection identity",
        ):
            self._run(identity_drift)

        schema_drift = _Harness()
        schema_drift.header["sqlite_schema_format"] = 3
        with self.assertRaisesRegex(
            experiment.LightOilCaptureError,
            "application, user, schema, or page identity",
        ):
            self._run(schema_drift)

    def test_selector_disagreement_fails_closed(self) -> None:
        harness = _Harness()
        harness.selector_disagreement = True
        with self.assertRaisesRegex(
            experiment.LightOilCaptureError,
            "selectors disagree",
        ):
            self._run(harness)

    def test_recipe_and_amount_drift_fail_closed(self) -> None:
        recipe_drift = _Harness()
        recipe_drift.recipe_drift = True
        with self.assertRaisesRegex(
            experiment.LightOilCaptureError,
            "exact owner identities differ",
        ):
            self._run(recipe_drift)

        amount_drift = _Harness()
        amount_drift.amount_drift = True
        with self.assertRaisesRegex(
            experiment.LightOilCaptureError,
            "owner or quantity differs",
        ):
            self._run(amount_drift)

    def test_truncation_must_match_the_independently_validated_result(self) -> None:
        harness = _Harness()
        harness.chain_result["truncation"] = {
            "truncated": True,
            "reasons": ["max-routes"],
            "limits": experiment.CHAIN_OPTIONS.limits(),
        }
        with self.assertRaisesRegex(
            experiment.LightOilCaptureError,
            "counts, reasons, truncation, or canonical digest differs",
        ):
            self._run(harness)

    def test_timings_and_rss_are_excluded_from_stable_semantic_identity(self) -> None:
        result = self._run(_Harness(), repetitions=2)

        self.assertNotIn(b"wall_ns", result.semantic_bytes)
        self.assertNotIn(b"cpu_ns", result.semantic_bytes)
        self.assertNotIn(b"max_rss", result.semantic_bytes)
        repetitions = result.timing_document["repetitions"]
        self.assertEqual(
            ["first-reader-in-process", "reopened-reader"],
            [row["measurement_class"] for row in repetitions],
        )
        self.assertEqual(
            [result.semantic_sha256, result.semantic_sha256],
            [row["semantic_sha256"] for row in repetitions],
        )
        self.assertIn("not OS-cold", result.timing_document["measurement_note"])
        self.assertIn(
            "full projection SHA-256",
            result.timing_document["measurement_note"],
        )
        self.assertIn("bounded_chain", repetitions[0]["phases"])
        self.assertIn("max_rss", repetitions[0])
        self.assertIn(
            "fresh CLI computes full SHA-256",
            repetitions[0]["projection_identity_cache_qualification"],
        )
        self.assertIn(
            "process-local-full-SHA-256 identity cache",
            repetitions[1]["projection_identity_cache_qualification"],
        )
        self.assertIsNot(
            result.semantic,
            result.output_document["semantic"],
        )

    def test_write_snapshots_a_valid_result_before_both_atomic_writes(self) -> None:
        result = self._run(_Harness())
        output = ROOT / ".workbench/atlas/valid-result.json"
        sidecar = experiment.timing_sidecar_path(output)

        with mock.patch.object(experiment, "_atomic_write") as writer:
            observed_output, observed_sidecar = experiment.write_result(
                result,
                output,
            )

        self.assertEqual(output.resolve(), observed_output)
        self.assertEqual(sidecar.resolve(), observed_sidecar)
        self.assertEqual(
            [sidecar.resolve(), output.resolve()],
            [call.args[0] for call in writer.call_args_list],
        )
        self.assertEqual(
            experiment.canonical_json_bytes(result.timing_document),
            writer.call_args_list[0].args[1],
        )
        self.assertEqual(
            experiment.canonical_json_bytes(result.output_document),
            writer.call_args_list[1].args[1],
        )

    def test_write_rejects_mutated_or_mismatched_result_snapshots(self) -> None:
        mutated_semantic = self._run(_Harness())
        mutated_semantic.semantic["finding"] = "mutated-after-validation"
        with mock.patch.object(experiment, "_atomic_write") as writer:
            with self.assertRaisesRegex(
                experiment.LightOilCaptureError,
                "semantic snapshot differs",
            ):
                experiment.write_result(
                    mutated_semantic,
                    ROOT / ".workbench/atlas/mutated-semantic.json",
                )
            writer.assert_not_called()

        mutated_wrapper = self._run(_Harness())
        mutated_wrapper.output_document["semantic"]["finding"] = (
            "mutated-wrapper"
        )
        with mock.patch.object(experiment, "_atomic_write") as writer:
            with self.assertRaisesRegex(
                experiment.LightOilCaptureError,
                "output wrapper differs",
            ):
                experiment.write_result(
                    mutated_wrapper,
                    ROOT / ".workbench/atlas/mutated-wrapper.json",
                )
            writer.assert_not_called()

        mutated_timing = self._run(_Harness())
        mutated_timing.timing_document["semantic_sha256"] = "0" * 64
        with mock.patch.object(experiment, "_atomic_write") as writer:
            with self.assertRaisesRegex(
                experiment.LightOilCaptureError,
                "timing sidecar differs",
            ):
                experiment.write_result(
                    mutated_timing,
                    ROOT / ".workbench/atlas/mutated-timing.json",
                )
            writer.assert_not_called()

        non_json_semantic = self._run(_Harness())
        non_json_semantic.semantic["invalid"] = {"not", "json"}
        with mock.patch.object(experiment, "_atomic_write") as writer:
            with self.assertRaisesRegex(
                experiment.LightOilCaptureError,
                "not bounded canonical JSON",
            ):
                experiment.write_result(
                    non_json_semantic,
                    ROOT / ".workbench/atlas/non-json-semantic.json",
                )
            writer.assert_not_called()

    def test_output_guard_allows_only_explicit_json_below_workbench_storage(self) -> None:
        accepted = experiment._guard_output_path(
            ROOT / ".workbench/atlas/light-oil.json"
        )
        self.assertEqual(
            (ROOT / ".workbench/atlas/light-oil.json").resolve(),
            accepted,
        )
        for rejected in (
            ROOT / "light-oil.json",
            ROOT / ".workbench",
            ROOT / ".workbench/atlas/light-oil.txt",
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(experiment.LightOilCaptureError):
                    experiment._guard_output_path(rejected)


if __name__ == "__main__":
    unittest.main()
