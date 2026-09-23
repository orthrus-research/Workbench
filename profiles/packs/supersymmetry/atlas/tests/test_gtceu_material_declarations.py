from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[5]
for source in (
    ROOT / "modules/pack-program-studio/src",
    ROOT / "modules/atlas/src",
    ROOT / "profiles/packs/supersymmetry/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_profile_supersymmetry.gtceu_material_declarations import (  # noqa: E402
    GTCEU_MATERIAL_DECLARATION_POLICY_V2,
    normalize_supersymmetry_material_declarations_v2,
)
from workbench_profile_supersymmetry.gtceu_material_semantics import (  # noqa: E402
    GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
)
from workbench_pack_program_studio.material_declarations import (  # noqa: E402
    normalize_material_declarations_v2,
)
from workbench_pack_program_studio.model import canonical_bytes  # noqa: E402


def _attributes(source: str) -> dict[str, object]:
    result = normalize_material_declarations_v2(
        {"groovy/material/Test.groovy": source},
        GTCEU_MATERIAL_DECLARATION_POLICY_V2,
        GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
    )
    return result["declarations"][0]["attributes"]


class GtceuMaterialDeclarationV2Tests(unittest.TestCase):
    def test_established_v2_record_identity_is_stable(self) -> None:
        result = normalize_material_declarations_v2(
            {
                "groovy/material/Test.groovy": (
                    "X = new Material.Builder(20000, "
                    "SuSyUtility.susyId('x')).dust().build()"
                )
            },
            GTCEU_MATERIAL_DECLARATION_POLICY_V2,
            GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        )
        self.assertEqual(
            "d6ed8e72050c5ceba541a4ff140f9405aeffa842d5894ba91bde771486414019",
            GTCEU_MATERIAL_DECLARATION_POLICY_V2.sha256,
        )
        self.assertEqual(
            "workbench-pack-material-source-program-v2:sha256:"
            "11c4a68eab0c03b1115df032e9f61069846bf725cc991a987b12386378e96f9f",
            result["binding"]["program_id"],
        )
        self.assertEqual(
            "workbench-pack-source-declarations:sha256:"
            "31bbdd9ef71b1c6006ac288b838bdb2439930d1409348a3c8795b72b615c2ad8",
            result["declaration_set_id"],
        )
        self.assertEqual(
            "workbench-pack-source-declaration:sha256:"
            "a99fd7b55dcaeaa7a384d5b78790cf171ffdec487c80f190d41abd140a273405",
            result["declarations"][0]["source_declaration_id"],
        )
        self.assertEqual(
            "dadf8ec55eca872972bf9911af5aa4d9a465ba5fc683ae6d842cf038b8d6d709",
            hashlib.sha256(canonical_bytes(result)).hexdigest(),
        )

    def test_material_core_is_independent_of_fluid_storage_relationship(self) -> None:
        gas = _attributes(
            "X = new Material.Builder(20000, SuSyUtility.susyId('x')).gas().build()"
        )
        liquid = _attributes(
            "X = new Material.Builder(20000, SuSyUtility.susyId('x')).liquid().build()"
        )
        self.assertEqual(
            gas["declared_material_core_sha256"],
            liquid["declared_material_core_sha256"],
        )
        self.assertEqual(
            ["fluid"],
            gas["declared_material_core"]["expected_verified_closure"]["properties"]["keys"],
        )
        self.assertEqual(
            "gas",
            gas["form_lens"]["fluid_storage_relationships"][0]["storage_key"],
        )
        self.assertEqual(
            "liquid",
            liquid["form_lens"]["fluid_storage_relationships"][0]["storage_key"],
        )

    def test_expected_closure_adds_properties_and_property_implied_flags(self) -> None:
        attributes = _attributes(
            "P = new Material.Builder(20001, SuSyUtility.susyId('p')).polymer().build()"
        )
        closure = attributes["declared_material_core"]["expected_verified_closure"]
        self.assertEqual(["dust", "ingot", "polymer"], closure["properties"]["keys"])
        self.assertEqual(
            ["disable_decomposition", "flammable", "no_smashing"],
            closure["flags"]["names"],
        )
        self.assertEqual("exact-static", closure["state"])

    def test_any_dependency_uses_pinned_runtime_fallback(self) -> None:
        tool = _attributes(
            "T = new Material.Builder(20002, SuSyUtility.susyId('t')).toolStats(stats).build()"
        )
        self.assertEqual(
            ["dust", "ingot", "tool"],
            tool["declared_material_core"]["expected_verified_closure"]["properties"]["keys"],
        )
        gem_tool = _attributes(
            "T = new Material.Builder(20002, SuSyUtility.susyId('t')).gem().toolStats(stats).build()"
        )
        self.assertEqual(
            ["dust", "gem", "tool"],
            gem_tool["declared_material_core"]["expected_verified_closure"]["properties"]["keys"],
        )

    def test_duplicate_gas_is_a_source_issue_with_both_exact_operations(self) -> None:
        attributes = _attributes(
            """
            // .gas() in a comment is not an operation
            X = new Material.Builder(20003, SuSyUtility.susyId('x'))
                .gas(new FluidBuilder().acidic())
                .gas()
                .build()
            """
        )
        self.assertIn("form:duplicate-storage-key:gas", attributes["issues"])
        relationships = attributes["form_lens"]["fluid_storage_relationships"]
        self.assertEqual(["gas", "gas"], [row["storage_key"] for row in relationships])
        self.assertIn("gas(new FluidBuilder().acidic())", attributes["operations"][0]["source_text"])

    def test_dotted_closure_operation_retains_balanced_body_and_chain(self) -> None:
        attributes = _attributes(
            """
            X = new Material.Builder(20003, SuSyUtility.susyId('blast'))
                .ingot()
                .blast { builder -> builder.temp(3200) }
                .build()
            """
        )
        blast = attributes["operations"][1]
        self.assertTrue(blast["closure_form"])
        self.assertIn("builder.temp(3200)", blast["source_text"])
        self.assertTrue(attributes["builder_complete"])
        self.assertNotIn("operation:arity:blast", attributes["issues"])

    def test_empty_dynamic_and_colliding_declarations_remain_distinct(self) -> None:
        empty = _attributes(
            "E = new Material.Builder(20004, SuSyUtility.susyId('empty')).build()"
        )
        self.assertEqual(
            ["empty"],
            empty["declared_material_core"]["expected_verified_closure"]["properties"]["keys"],
        )
        source = """
        A = new Material.Builder(20005, SuSyUtility.susyId('a')).dust().build()
        B = new Material.Builder(20005, SuSyUtility.susyId(dynamicName)).mystery().build()
        """
        result = normalize_material_declarations_v2(
            {"groovy/material/Collision.groovy": source},
            GTCEU_MATERIAL_DECLARATION_POLICY_V2,
            GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        )
        self.assertEqual(2, len(result["declarations"]))
        for row in result["declarations"]:
            self.assertIn("identity:duplicate-numeric-id", row["attributes"]["issues"])
        dynamic = result["declarations"][1]["attributes"]
        self.assertEqual("frontier-static", dynamic["normalization_status"])
        self.assertIn("identity:registry-name-unresolved", dynamic["uncertainties"])
        self.assertIn("operation:unsupported:mystery", dynamic["uncertainties"])
        self.assertEqual(
            "lower-bound-static",
            dynamic["declared_material_core"]["expected_verified_closure"]["state"],
        )

    def test_profile_entrypoint_executes_over_exact_fixture_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            material = root / "groovy/material/Test.groovy"
            material.parent.mkdir(parents=True)
            material.write_text(
                "X = new Material.Builder(20006, SuSyUtility.susyId('x')).dust().build()",
                encoding="utf-8",
            )
            result = normalize_supersymmetry_material_declarations_v2(root)
        self.assertEqual(1, result["summary"]["declarations"])
        self.assertEqual("Pack Program Studio", result["authority"]["owner"])
        self.assertEqual("none", result["authority"]["runtime_authority"])
        self.assertEqual(
            "workbench-pack-material-declaration-normalizer-v2",
            result["binding"]["source_kind"],
        )


if __name__ == "__main__":
    unittest.main()
