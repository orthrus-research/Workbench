#!/usr/bin/env python3

"""Current Blueprints authority and material-fluid example regression checks."""

from __future__ import annotations

import json
import unittest

import yaml

from _support import WORKBENCH_ROOT


PROFILE_PATH = WORKBENCH_ROOT / "profiles/packs/supersymmetry/profile.yaml"
EXPERIMENTAL_REGISTRY_PATH = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/blueprints/experimental/standards/registry.json"
)
CURRENT_EXAMPLE_PATH = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/blueprints/examples/"
    "material-fluid-recipe-radon.json"
)


class CurrentMaterialFluidExampleTest(unittest.TestCase):
    def test_profile_has_no_implicit_stable_registry(self) -> None:
        self.assertFalse(
            (WORKBENCH_ROOT / "modules/blueprints/standards/registry.json").exists()
        )
        old_assets = WORKBENCH_ROOT / "modules/blueprints/assets"
        self.assertFalse(
            any(path.is_file() for path in old_assets.rglob("*"))
            if old_assets.exists()
            else False
        )
        profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(profile["blueprints"]["stable_registry"])
        self.assertEqual(
            "blueprints/experimental/standards/registry.json",
            profile["blueprints"]["experimental_registry"],
        )

        registry = json.loads(
            EXPERIMENTAL_REGISTRY_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(1, len(registry["standards"]))
        standard = registry["standards"][0]
        self.assertEqual("material-backed-fluid", standard["standard_key"])
        self.assertEqual("0.1.0", standard["version"])

    def test_radon_example_is_current_non_identity_bearing_and_bounded(self) -> None:
        example = json.loads(CURRENT_EXAMPLE_PATH.read_text(encoding="utf-8"))

        self.assertEqual("workbench-blueprints-current-example-v1", example["format"])
        self.assertEqual("mutable-current-example", example["record_class"])
        self.assertIs(example["identity_bearing"], False)
        self.assertNotIn("id", example)
        self.assertEqual(
            {
                "color": "0x4a90e2",
                "duration": 20,
                "input_amount": 1000,
                "input_fluid": "radon",
                "material_id": 20008,
                "name": "Workbench Probe Coolant",
                "output_amount": 1000,
                "recipe_map": "MIXER",
                "recipe_map_registry_name": "mixer",
                "recipe_script": "groovy/postInit/chemistry/Catalysts.groovy",
                "registry_name": "workbench_probe_coolant",
                "symbol": "WorkbenchProbeCoolant",
                "translation": "Workbench Probe Coolant",
                "voltage_tier": "LV",
            },
            example["request"],
        )
        self.assertEqual(
            "workbench-developer-material-fluid-recipe-plan:sha256:"
            "f699faa325d7c24bf6961db048b7f5e9df8cc49b895e3b50d904d54afb902b48",
            example["observed_run"]["plan_id"],
        )
        self.assertEqual(
            "workbench-developer-material-fluid-recipe-run:sha256:"
            "1bd17a3b62c796f73d187d94252752d808644f0837a733c290e897a5272772b7",
            example["observed_run"]["run_id"],
        )
        self.assertEqual(
            {
                "fluid_registration": "observed",
                "fml_client_load": "observed",
                "groovy_compilation": "observed",
                "localization": "observed",
                "material_registration": "observed",
                "recipe_registration": "observed",
            },
            example["observed_run"]["assertions"],
        )
        self.assertNotIn("stable_standard_context", example)
        self.assertEqual(
            {
                "eut": 30,
                "exact_recipe_match_count": 1,
                "fluid_name": "workbench_probe_coolant",
                "localized_name": "Workbench Probe Coolant",
                "manager_phase": "FROZEN",
                "material_resource": "susy:workbench_probe_coolant",
                "recipe_map_registry_name": "mixer",
            },
            example["observed_run"]["observed_facts"],
        )
        self.assertTrue(
            all(value is False for value in example["authority_boundary"].values())
        )


if __name__ == "__main__":
    unittest.main()
