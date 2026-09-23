from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[5]
for source in (
    ROOT / "modules/pack-program-studio/src",
    ROOT / "modules/atlas/src",
    ROOT / "profiles/packs/supersymmetry/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_profile_supersymmetry.gtceu_material_semantics import (  # noqa: E402
    GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2,
    GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
)
from workbench_profile_supersymmetry.gtceu_material_declarations import (  # noqa: E402
    GTCEU_MATERIAL_DECLARATION_POLICY_V2,
)
from workbench_pack_program_studio.material_declarations import (  # noqa: E402
    normalize_material_declarations_v2,
)
from workbench_pack_program_studio.material_expressions import (  # noqa: E402
    evaluate_expression,
    parse_import_scope,
)


class GtceuMaterialSemanticsTests(unittest.TestCase):
    def test_generated_runtime_policy_uses_public_contract_with_legacy_compatibility(self) -> None:
        from workbench_api.material_classification import (
            MaterialClassificationPolicy,
            MaterialPolicyValidationError,
        )
        from workbench_material_semantics.classification import (
            MaterialClassificationPolicy as LegacyMaterialClassificationPolicy,
        )
        from workbench_material_semantics.errors import RuntimeGraphQueryError

        self.assertIs(MaterialClassificationPolicy, LegacyMaterialClassificationPolicy)
        self.assertIs(MaterialPolicyValidationError, RuntimeGraphQueryError)
        with self.assertRaises(RuntimeGraphQueryError):
            LegacyMaterialClassificationPolicy("", "profile", (), (), ())
        runtime = GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2
        self.assertIs(type(runtime), MaterialClassificationPolicy)
        # Exact pre-extraction identity of the existing composed profile policy.
        self.assertEqual(
            "ca99669be987de872c1feae535a696de40ab35b972bfc415ba97b8ab11aee975",
            runtime.sha256,
        )

    def test_presets_and_addon_flags_compile_into_one_policy(self) -> None:
        semantics = GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2
        presets = semantics.presets_by_qualified_symbol()
        self.assertEqual(
            (
                "generate_plate",
                "generate_rod",
                "generate_long_rod",
                "generate_bolt_screw",
            ),
            presets[
                "gregtech.api.unification.material.Materials.EXT2_METAL"
            ].flag_names,
        )
        flags = semantics.flags_by_name()
        self.assertEqual(
            ("generate_catalyst_pellet",),
            flags["generate_catalyst_bed"].required_flags,
        )
        runtime = GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2
        self.assertEqual(
            ("hip_pressed",),
            dict(runtime.flag_dependencies)["superalloy"],
        )
        self.assertEqual(64, len(semantics.sha256))

    def test_wire_rule_retains_exact_threshold_and_java_coercion(self) -> None:
        rule = GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2.conditional_flag_rules[0]
        self.assertEqual(("ingot", "wire"), rule.required_properties)
        self.assertEqual(8192, rule.conditions[0].expected)
        self.assertEqual("signed-int32", rule.conditions[0].java_coercion)
        self.assertFalse(rule.conditions[1].expected)
        self.assertFalse(rule.conditions[1].default)
        self.assertEqual(("generate_foil",), rule.added_flags)

    def test_v2_resolves_imported_presets_addon_flags_and_wire_rule(self) -> None:
        source = """
        import gregtech.api.GTValues
        import static gregtech.api.unification.material.Materials.*
        import static supersymmetry.api.unification.material.info.SuSyMaterialFlags.*

        X = new Material.Builder(20000, SuSyUtility.susyId('x'))
            .ingot()
            .cableProperties(GTValues.V[GTValues.IV], 1, 1)
            .flags(EXT2_METAL, SUPERALLOY)
            .build()
        """
        result = normalize_material_declarations_v2(
            {"groovy/material/Test.groovy": source},
            GTCEU_MATERIAL_DECLARATION_POLICY_V2,
            GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        )
        attributes = result["declarations"][0]["attributes"]
        core = attributes["declared_material_core"]
        self.assertEqual(
            ["generate_bolt_screw", "generate_long_rod", "generate_plate", "generate_rod", "superalloy"],
            core["declared_flag_names"],
        )
        self.assertEqual("applied", core["conditional_flag_rules"][0]["state"])
        self.assertIn(
            "generate_foil",
            core["expected_verified_closure"]["flags"]["names"],
        )
        self.assertIn(
            "hip_pressed",
            core["expected_verified_closure"]["flags"]["names"],
        )
        self.assertEqual("exact-static", attributes["normalization_status"])
        self.assertEqual(
            "workbench-pack-material-declaration-normalizer-v2",
            result["binding"]["source_kind"],
        )

    def test_constant_evaluator_applies_java_signed_int32_semantics(self) -> None:
        scope = parse_import_scope("import gregtech.api.GTValues")
        exact = evaluate_expression(
            "GTValues.V[GTValues.IV]",
            GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
            scope,
        )
        self.assertEqual(8192, exact.value)

    def test_wire_rule_excludes_superconductors_and_frontiers_unknown_voltage(self) -> None:
        superconductor = """
        import static gregtech.api.unification.material.Materials.*
        X = new Material.Builder(20001, SuSyUtility.susyId('supercon'))
            .ingot().cableProperties(8192, 1, 1, true).build()
        """
        exact = normalize_material_declarations_v2(
            {"groovy/material/Supercon.groovy": superconductor},
            GTCEU_MATERIAL_DECLARATION_POLICY_V2,
            GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        )["declarations"][0]["attributes"]
        self.assertEqual(
            "not-applied",
            exact["declared_material_core"]["conditional_flag_rules"][0]["state"],
        )
        self.assertNotIn(
            "generate_foil",
            exact["declared_material_core"]["expected_verified_closure"]["flags"]["names"],
        )

        dynamic = """
        X = new Material.Builder(20002, SuSyUtility.susyId('dynamic_wire'))
            .ingot().cableProperties(dynamicVoltage, 1, 1).build()
        """
        frontier = normalize_material_declarations_v2(
            {"groovy/material/DynamicWire.groovy": dynamic},
            GTCEU_MATERIAL_DECLARATION_POLICY_V2,
            GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        )["declarations"][0]["attributes"]
        self.assertEqual("frontier-static", frontier["normalization_status"])
        self.assertTrue(
            any(
                code.startswith("flag:value-rule-unresolved:")
                for code in frontier["uncertainties"]
            )
        )
        self.assertEqual(
            "unknown",
            frontier["declared_material_core"]["conditional_flag_rules"][0]["state"],
        )


if __name__ == "__main__":
    unittest.main()
