#!/usr/bin/env python3

"""Regression checks for mutable, revision-bound first-release examples."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REVISION = re.compile(r"^[0-9a-f]{40}$")


def _load(name: str) -> dict[str, object]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class ReleaseExamplesTest(unittest.TestCase):
    def test_recipe_change_is_one_exact_pr1451_mixer_addition(self) -> None:
        example = _load("recipe-change-copper-sulfate-solution.json")
        self.assertEqual("workbench-blueprints-current-example-v1", example["format"])
        self.assertIs(example["identity_bearing"], False)
        self.assertNotIn("id", example)
        self.assertEqual(
            {
                "mutation": "add",
                "script": "groovy/postInit/chemistry/ChemistryOverhaul.groovy",
                "recipe_map": "MIXER",
                "item_inputs": [
                    {"kind": "ore", "name": "dustCopperSulfate", "amount": 6}
                ],
                "fluid_inputs": [{"name": "water", "amount": 1000}],
                "item_outputs": [],
                "fluid_outputs": [
                    {"name": "copper_sulfate_solution", "amount": 1000}
                ],
                "duration": 60,
                "voltage_tier": "LV",
            },
            example["request"],
        )
        source = example["source_reference"]
        self.assertEqual(1451, source["pull_request"])
        self.assertEqual(
            "b1b1a0f4c282a0d4a6ca068c07fd32521c83b935",
            source["effective_base_revision"],
        )
        self.assertEqual(
            "25d53ad77e458399c8e58e45ba8c21ddc44c99d5",
            source["result_revision"],
        )
        self.assertEqual(2, len(example["excluded_pull_request_changes"]))
        self.assertEqual(
            "required-not-observed", example["runtime_observation"]["state"]
        )
        self.assertTrue(
            all(
                REVISION.fullmatch(source[key]) is not None
                for key in (
                    "effective_base_revision",
                    "result_revision",
                    "before_blob_sha1",
                    "after_blob_sha1",
                )
            )
        )

    def test_quest_change_is_one_exact_pr1978_prerequisite_edge(self) -> None:
        example = _load("quest-for-process-gas-atomizer.json")
        self.assertEqual("workbench-blueprints-current-example-v1", example["format"])
        self.assertIs(example["identity_bearing"], False)
        self.assertNotIn("id", example)
        self.assertEqual(
            {
                "add_prerequisite_id": 90454916,
                "description": None,
                "quest_id": 336324327,
                "requirement_type": "IMPLICIT",
                "title": None,
            },
            example["request"],
        )
        source = example["source_reference"]
        self.assertEqual(1978, source["pull_request"])
        self.assertEqual(
            "b4338ef203ef40452a62e48019dd8f7b21bf496e",
            source["effective_base_revision"],
        )
        self.assertEqual(
            "ef7d3f4f65d12d5fdf3d8f83607a5c2da18c6ea9",
            source["result_revision"],
        )
        delta = example["historical_delta"]
        self.assertEqual(
            "ee7985c317eca7c9ebdbb1d43aad4744b574586c",
            delta["localization_reference"]["base_and_result_blob_sha1"],
        )
        self.assertEqual([757], delta["before"]["preRequisites:11"])
        self.assertEqual([757, 90454916], delta["after"]["preRequisites:11"])
        self.assertEqual([1, 1], delta["after"]["preRequisiteTypes:7"])
        self.assertIs(delta["localization_changed"], False)
        self.assertIs(
            example["authority_boundary"]["pre_existing_graph_health_certified"],
            False,
        )
        self.assertEqual(
            "required-not-observed", example["runtime_observation"]["state"]
        )
        self.assertTrue(
            all(
                REVISION.fullmatch(source[key]) is not None
                for key in (
                    "effective_base_revision",
                    "result_revision",
                    "before_blob_sha1",
                    "after_blob_sha1",
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
