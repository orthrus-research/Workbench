from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import unittest

from workbench_profile_supersymmetry.material_fluid_recipe_observation import (
    MARKER_PREFIX,
    MaterialFluidRecipeObservationError,
    MaterialFluidRecipeProbeSpec,
    build_material_fluid_recipe_probe,
    build_material_fluid_recipe_probe_overlay,
    interpret_material_fluid_recipe_observation,
    validate_material_fluid_recipe_assessment,
)


SPEC = MaterialFluidRecipeProbeSpec(
    registry_name="workbench_pilot_coolant",
    symbol_name="WorkbenchPilotCoolant",
    material_id=20008,
    color_rgb=0x425D73,
    translation="Workbench Pilot Coolant",
    recipe_map_alias="BR",
    recipe_map_registry_name="batch_reactor",
    input_fluid="steam",
    input_amount=750,
    output_amount=500,
    duration=200,
    voltage_tier="MV",
    source_plan_id="sha256:" + "a" * 64,
)


def _actual() -> dict[str, object]:
    return {
        "chanced_fluid_output_count": 0,
        "chanced_item_output_count": 0,
        "color_rgb": 0x425D73,
        "duration": 200,
        "eut": 120,
        "exact_recipe_match_count": 1,
        "find_recipe_identity": True,
        "fluid_input_count": 1,
        "fluid_name": "workbench_pilot_coolant",
        "fluid_output_count": 1,
        "forge_registry_roundtrip": True,
        "groovy_recipe": True,
        "groovy_target_count": 1,
        "has_flammable_flag": True,
        "has_fluid_property": True,
        "input_amount": 750,
        "input_fluid": "steam",
        "item_input_count": 0,
        "item_output_count": 0,
        "localized_name": "Workbench Pilot Coolant",
        "manager_phase": "FROZEN",
        "material_id": 20008,
        "material_resource": "susy:workbench_pilot_coolant",
        "output_amount": 500,
        "output_fluid": "workbench_pilot_coolant",
        "recipe_map_alias_identity": True,
        "recipe_map_alias_registry_name": "batch_reactor",
        "recipe_map_registry_name": "batch_reactor",
    }


def _derived(actual: dict[str, object], error_kind: str | None) -> dict[str, bool]:
    return {
        "color_rgb": actual["color_rgb"] == SPEC.color_rgb,
        "duration": actual["duration"] == SPEC.duration,
        "eut": actual["eut"] == SPEC.expected_eut,
        "find_recipe_identity": actual["find_recipe_identity"] is True,
        "fluid_input": (
            actual["fluid_input_count"] == 1
            and actual["input_fluid"] == SPEC.input_fluid
            and actual["input_amount"] == SPEC.input_amount
        ),
        "fluid_name": actual["fluid_name"] == SPEC.registry_name,
        "fluid_output": (
            actual["fluid_output_count"] == 1
            and actual["chanced_fluid_output_count"] == 0
            and actual["output_fluid"] == SPEC.registry_name
            and actual["output_amount"] == SPEC.output_amount
        ),
        "forge_registry_roundtrip": actual["forge_registry_roundtrip"] is True,
        "groovy_origin": actual["groovy_recipe"] is True,
        "has_flammable_flag": actual["has_flammable_flag"] is True,
        "has_fluid_property": actual["has_fluid_property"] is True,
        "localized_name": actual["localized_name"] == SPEC.translation,
        "manager_frozen": actual["manager_phase"] == "FROZEN",
        "material_id": actual["material_id"] == SPEC.material_id,
        "material_resource": actual["material_resource"] == SPEC.material_resource,
        "no_item_io": (
            actual["item_input_count"] == 0
            and actual["item_output_count"] == 0
            and actual["chanced_item_output_count"] == 0
        ),
        "probe_execution": error_kind is None,
        "recipe_map_alias_binding": (
            actual["recipe_map_alias_identity"] is True
            and actual["recipe_map_alias_registry_name"]
            == SPEC.recipe_map_registry_name
        ),
        "recipe_map_registry_name": (
            actual["recipe_map_registry_name"] == SPEC.recipe_map_registry_name
        ),
        "unique_exact_groovy_recipe": actual["exact_recipe_match_count"] == 1,
    }


def _marker(
    actual: dict[str, object] | None = None,
    *,
    error_kind: str | None = None,
    contradict: str | None = None,
) -> bytes:
    observed = _actual() if actual is None else actual
    checks = _derived(observed, error_kind)
    if contradict is not None:
        checks[contradict] = not checks[contradict]
    value = {
        "actual": observed,
        "checks": checks,
        "error_kind": error_kind,
        "format": "workbench-material-fluid-recipe-observation-v1",
        "probe_id": SPEC.probe_id,
        "source_plan_id": SPEC.source_plan_id,
        "stage": "postInit",
        "state": "observed" if all(checks.values()) else "mismatch",
    }
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=")
    return b"[INFO] unrelated\n[INFO] " + MARKER_PREFIX.encode() + token + b"\n"


def _capture(log: bytes) -> dict[str, object]:
    return {
        "groovy_log_sha256": sha256(log).hexdigest(),
        "groovy_log_uri": "file:///capture/groovy.log",
        "launch_id": "sha256:" + "b" * 64,
        "launch_receipt_sha256": "c" * 64,
        "launch_receipt_uri": "file:///capture/runtime-launch-v3.json",
        "materialization_id": "sha256:" + "d" * 64,
        "materialization_receipt_sha256": "e" * 64,
        "materialization_receipt_size": 1024,
        "materialization_receipt_uri": "file:///capture/materialization.json",
        "payload": {
            "file_count": 1,
            "total_bytes": 1,
            "tree_sha256": "sha256:" + "f" * 64,
        },
        "probe_id": SPEC.probe_id,
        "probe_overlay_id": build_material_fluid_recipe_probe_overlay(SPEC)[
            "patch_id"
        ],
        "probe_script_sha256": sha256(
            build_material_fluid_recipe_probe(SPEC)
        ).hexdigest(),
        "session_outcome": "completed",
        "session_receipt_sha256": "1" * 64,
        "session_receipt_size": 1024,
        "session_receipt_uri": "file:///capture/runtime-observation-v1.json",
    }


def _without_selected_recipe(count: int) -> dict[str, object]:
    actual = _actual()
    actual.update(
        {
            "chanced_fluid_output_count": None,
            "chanced_item_output_count": None,
            "duration": None,
            "eut": None,
            "exact_recipe_match_count": count,
            "find_recipe_identity": False,
            "fluid_input_count": None,
            "fluid_output_count": None,
            "groovy_recipe": None,
            "groovy_target_count": count,
            "input_amount": None,
            "input_fluid": None,
            "item_input_count": None,
            "item_output_count": None,
            "output_amount": None,
            "output_fluid": None,
        }
    )
    return actual


class MaterialFluidRecipeObservationTests(unittest.TestCase):
    def test_probe_binds_plan_map_recipe_and_material_without_construction(self) -> None:
        script = build_material_fluid_recipe_probe(SPEC).decode("utf-8")
        for token in (
            "RecipeMap.getByName(expected.recipe_map_registry_name)",
            "Recipemaps.BR",
            "aliasMap.is(recipeMap)",
            "VA[MV]",
            "isGroovyRecipe()",
            "getInputs().isEmpty()",
            "getOutputs().isEmpty()",
            "getChancedOutputs().getChancedEntries().isEmpty()",
            "getFluidInputs()",
            "getFluidOutputs()",
            "findRecipe(",
            "foundRecipe.is(exactRecipes[0])",
            "susy:workbench_pilot_coolant",
            MARKER_PREFIX,
        ):
            self.assertIn(token, script)
        for forbidden in (
            "new Material.Builder",
            "new FluidBuilder",
            "recipeBuilder(",
            "buildAndRegister(",
            "registerFluid(",
        ):
            self.assertNotIn(forbidden, script)
        self.assertEqual(1, script.count(MARKER_PREFIX))
        self.assertEqual(script.encode(), build_material_fluid_recipe_probe(SPEC))

        overlay = build_material_fluid_recipe_probe_overlay(SPEC)
        self.assertEqual(
            ".minecraft/groovy/postInit/utils/"
            "ZzzzWorkbenchMaterialFluidRecipeAssertion.groovy",
            overlay["target"]["path"],
        )
        self.assertTrue(overlay["target"]["must_be_absent"])
        self.assertEqual("MaterialFluidRecipeAssertion.groovy", overlay["source"]["path"])
        with self.assertRaises(MaterialFluidRecipeObservationError):
            build_material_fluid_recipe_probe_overlay(SPEC, "AnotherProbe.groovy")
        content_bound = replace(
            SPEC,
            source_plan_id=(
                "workbench-developer-material-fluid-recipe-plan:sha256:"
                + "2" * 64
            ),
        )
        self.assertIn(
            content_bound.source_plan_id,
            build_material_fluid_recipe_probe(content_bound).decode("utf-8"),
        )

    def test_valid_marker_produces_revalidatable_profile_assessment(self) -> None:
        log = _marker()
        assessment = interpret_material_fluid_recipe_observation(
            SPEC, log, _capture(log)
        )
        self.assertEqual("observed", assessment["state"])
        self.assertEqual([], assessment["failed_checks"])
        self.assertTrue(all(assessment["checks"].values()))
        self.assertEqual(
            {
                "fluid_registration": "observed",
                "groovy_compilation": "observed",
                "localization": "observed",
                "material_registration": "observed",
                "recipe_registration": "observed",
            },
            assessment["developer_assertions"],
        )
        self.assertEqual("Atlas", assessment["authority"]["owner"])
        self.assertEqual(
            assessment,
            validate_material_fluid_recipe_assessment(SPEC, assessment),
        )

    def test_wrong_map_is_a_recipe_failure_without_erasing_material_facts(self) -> None:
        actual = _without_selected_recipe(0)
        actual.update(
            {
                "groovy_target_count": 0,
                "recipe_map_alias_identity": False,
                "recipe_map_alias_registry_name": "chemical_reactor",
                "recipe_map_registry_name": "chemical_reactor",
            }
        )
        log = _marker(actual)
        assessment = interpret_material_fluid_recipe_observation(
            SPEC, log, _capture(log)
        )
        self.assertEqual("mismatch", assessment["state"])
        self.assertEqual(
            "failed", assessment["developer_assertions"]["recipe_registration"]
        )
        self.assertEqual(
            "observed", assessment["developer_assertions"]["material_registration"]
        )
        self.assertIn("recipe_map_alias_binding", assessment["failed_checks"])
        self.assertIn("recipe_map_registry_name", assessment["failed_checks"])

    def test_zero_and_duplicate_exact_recipes_fail_closed(self) -> None:
        for count in (0, 2):
            log = _marker(_without_selected_recipe(count))
            with self.subTest(count=count):
                assessment = interpret_material_fluid_recipe_observation(
                    SPEC, log, _capture(log)
                )
                self.assertEqual("mismatch", assessment["state"])
                self.assertIn(
                    "unique_exact_groovy_recipe", assessment["failed_checks"]
                )
                self.assertEqual(
                    "failed",
                    assessment["developer_assertions"]["recipe_registration"],
                )

    def test_exact_recipe_field_mismatch_is_diagnosed(self) -> None:
        actual = _actual()
        actual.update(
            {
                "duration": 199,
                "exact_recipe_match_count": 0,
                "find_recipe_identity": False,
            }
        )
        log = _marker(actual)
        assessment = interpret_material_fluid_recipe_observation(
            SPEC, log, _capture(log)
        )
        self.assertEqual("mismatch", assessment["state"])
        self.assertIn("duration", assessment["failed_checks"])
        self.assertIn("unique_exact_groovy_recipe", assessment["failed_checks"])
        self.assertEqual(
            "failed", assessment["developer_assertions"]["recipe_registration"]
        )

    def test_missing_duplicate_contradictory_and_tampered_records_are_rejected(self) -> None:
        ordinary = b"ordinary log\n"
        with self.assertRaises(MaterialFluidRecipeObservationError):
            interpret_material_fluid_recipe_observation(
                SPEC, ordinary, _capture(ordinary)
            )
        duplicate = _marker() + _marker()
        with self.assertRaises(MaterialFluidRecipeObservationError):
            interpret_material_fluid_recipe_observation(
                SPEC, duplicate, _capture(duplicate)
            )
        contradiction = _marker(contradict="duration")
        with self.assertRaises(MaterialFluidRecipeObservationError):
            interpret_material_fluid_recipe_observation(
                SPEC, contradiction, _capture(contradiction)
            )

        log = _marker()
        assessment = interpret_material_fluid_recipe_observation(
            SPEC, log, _capture(log)
        )
        for mutation in ("identity", "assertion", "profile", "capture"):
            tampered = deepcopy(assessment)
            if mutation == "identity":
                tampered["assessment_id"] = (
                    "workbench-atlas-material-fluid-recipe-assessment:sha256:"
                    + "f" * 64
                )
            elif mutation == "assertion":
                tampered["developer_assertions"]["recipe_registration"] = "failed"
            elif mutation == "profile":
                tampered["profile"]["pack_profile_id"] = "wrong"
            else:
                tampered["capture"]["probe_id"] = "wrong"
            with self.subTest(mutation=mutation), self.assertRaises(
                MaterialFluidRecipeObservationError
            ):
                validate_material_fluid_recipe_assessment(SPEC, tampered)


if __name__ == "__main__":
    unittest.main()
