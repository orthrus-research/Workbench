from __future__ import annotations

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
)
from workbench_profile_supersymmetry.gtceu_material_program import (  # noqa: E402
    GTCEU_MATERIAL_PROGRAM_POLICY_V2,
    normalize_supersymmetry_material_program_v2,
)
from workbench_profile_supersymmetry.gtceu_material_semantics import (  # noqa: E402
    GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
)
from workbench_pack_program_studio.material_declarations import (  # noqa: E402
    normalize_material_declarations_v2,
)
from workbench_pack_program_studio.material_program import (  # noqa: E402
    normalize_material_program_v2,
)


def _program(sources: dict[str, str]) -> dict[str, object]:
    declarations = normalize_material_declarations_v2(
        sources,
        GTCEU_MATERIAL_DECLARATION_POLICY_V2,
        GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
    )
    return normalize_material_program_v2(
        sources,
        declarations,
        GTCEU_MATERIAL_DECLARATION_POLICY_V2,
        GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
        GTCEU_MATERIAL_PROGRAM_POLICY_V2,
    )


class GtceuMaterialProgramV2Tests(unittest.TestCase):
    def test_profile_entrypoint_selects_the_material_program_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "material/Only.groovy"
            source.parent.mkdir(parents=True)
            source.write_text(
                "Only = new Material.Builder(20000, SuSyUtility.susyId('only')).dust().build()",
                encoding="utf-8",
            )
            unrelated = root / "postInit/Unrelated.groovy"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text(
                "Ignored = new Material.Builder(20001, SuSyUtility.susyId('ignored')).dust().build()",
                encoding="utf-8",
            )
            result = normalize_supersymmetry_material_program_v2(root)
        registrations = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-registration"
        ]
        self.assertEqual(1, len(registrations))
        self.assertEqual(
            "susy:only",
            registrations[0]["attributes"]["identity"]["resource_location"],
        )

    def test_finite_generator_specializes_identity_composition_and_guard(self) -> None:
        source = """
            import static gregtech.api.unification.material.Materials.*
            import static gregtech.api.unification.material.info.MaterialFlags.*

            class GeneratedMaterials {
                private static Material generate(
                    Material material, int id, boolean addIngot
                ) {
                    def builder = new Material.Builder(
                        id, SuSyUtility.susyId("hot_" + material.toString())
                    ).dust().components(material)
                    if (addIngot) {
                        builder.ingot()
                    }
                    return builder.build()
                }

                static register() {
                    HotHydrogen = generate(Hydrogen, 20000, true)
                        .addFlags(GENERATE_FOIL)
                }
            }
        """
        result = _program({"material/GeneratedMaterials.groovy": source})
        templates = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-generator-template"
        ]
        registrations = [
            row for row in result["declarations"]
            if row["attributes"].get("source_role") == "expanded-registration"
        ]
        mutations = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-mutation"
        ]
        self.assertEqual(1, len(templates))
        self.assertEqual(1, len(registrations))
        registration = registrations[0]["attributes"]
        self.assertEqual("susy:hot_hydrogen", registration["identity"]["resource_location"])
        self.assertEqual(20000, registration["identity"]["numeric_id"])
        self.assertEqual(
            ["dust", "ingot"],
            registration["declared_material_core"]["declared_property_keys"],
        )
        component = registration["declared_material_core"]["composition"]["components"][0]
        self.assertEqual("gregtech:hydrogen", component["material_resource_location"])
        self.assertEqual(1, component["amount"])
        self.assertEqual(1, len(mutations))
        self.assertEqual(
            ["generate_foil", "generate_plate"],
            mutations[0]["attributes"]["constraints"]["must_include_flags"],
        )

    def test_ordered_direct_mutations_resolve_target_and_lifecycle(self) -> None:
        source = """
            package classes
            import static gregtech.api.unification.material.Materials.*
            import static gregtech.api.unification.material.info.MaterialFlags.*

            class ChangeFlags {
                static init() {
                    Steel.addFlags(GENERATE_RING)
                    Steel.setProperty(PropertyKey.ORE, new OreProperty())
                    setupFluidType(Steel, FluidStorageKeys.LIQUID)
                }
            }
        """
        result = _program({"classes/ChangeFlags.groovy": source})
        mutations = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-mutation"
        ]
        self.assertEqual(3, len(mutations))
        self.assertEqual(
            ["gregtech:steel"] * 3,
            [row["attributes"]["target"]["resource_location"] for row in mutations],
        )
        self.assertEqual(
            ["material-event"] * 3,
            [row["lifecycle"]["stage"] for row in mutations],
        )
        self.assertEqual(
            ["add-flags", "set-property", "helper-mutation"],
            [row["attributes"]["operation"] for row in mutations],
        )
        self.assertEqual(
            ["liquid"],
            mutations[2]["attributes"]["constraints"]["requested_fluid_storage_keys"],
        )

    def test_receiver_helpers_are_retained_as_material_mutations(self) -> None:
        source = """
            package classes
            import static gregtech.api.unification.material.Materials.*

            class ChangeFlags {
                static init() {
                    Silver.addFluidPipes(1234, 50, false, false, true, false, true)
                    Scheelite.setupSlurries()
                    Tantalum.addBlastProperty(3293, "MID", 480, 240, -1, -1)
                }
            }
        """
        result = _program({"classes/ChangeFlags.groovy": source})
        mutations = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-mutation"
        ]
        self.assertEqual(
            ["gregtech:silver", "gregtech:scheelite", "gregtech:tantalum"],
            [row["attributes"]["target"]["resource_location"] for row in mutations],
        )
        self.assertEqual(
            [["fluid_pipe"], ["fluid"], ["blast"]],
            [
                row["attributes"]["constraints"]["must_include_properties"]
                for row in mutations
            ],
        )
        self.assertEqual(
            ["impure_slurry", "slurry"],
            mutations[1]["attributes"]["constraints"]["requested_fluid_storage_keys"],
        )

    def test_registry_helper_uses_profile_runtime_case_normalization(self) -> None:
        source = """
            class CaseMaterials {
                static register() {
                    AB2 = new Material.Builder(
                        20004, SuSyUtility.susyId('AB_2_metal_alloy')
                    ).dust().build()
                    AB2.setFormula('AB2', true)
                }
            }
        """
        result = _program({"material/CaseMaterials.groovy": source})
        rows = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] != "material-generator-template"
        ]
        self.assertEqual(
            ["susy:ab_2_metal_alloy", "susy:ab_2_metal_alloy"],
            [
                row["attributes"].get(
                    "target", row["attributes"].get("identity")
                )["resource_location"]
                for row in rows
            ],
        )

    def test_unimported_receiver_is_retained_as_a_mutation_frontier(self) -> None:
        sources = {
            "material/Defined.groovy": """
                import static material.SuSyMaterials.*
                MissingImport = new Material.Builder(
                    20001, SuSyUtility.susyId('missing_import')
                ).dust().build()
            """,
            "classes/NoImport.groovy": """
                import static gregtech.api.unification.material.info.MaterialFlags.*
                class NoImport {
                    static init() { MissingImport.addFlags(GENERATE_PLATE) }
                }
            """,
        }
        result = _program(sources)
        frontier = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-mutation"
        ]
        self.assertEqual(1, len(frontier))
        self.assertEqual(
            "frontier-static", frontier[0]["attributes"]["resolution_state"]
        )
        self.assertEqual(
            ["mutation:target-unresolved"],
            frontier[0]["attributes"]["uncertainties"],
        )
        self.assertIsNone(
            frontier[0]["attributes"]["target"]["resource_location"]
        )

    def test_expanded_registration_participates_in_identity_collisions(self) -> None:
        source = """
            class CollidingMaterials {
                private static Material generate(int id) {
                    return new Material.Builder(
                        id, SuSyUtility.susyId('generated_collision')
                    ).dust().build()
                }
                static register() {
                    DirectCollision = new Material.Builder(
                        20003, SuSyUtility.susyId('direct_collision')
                    ).dust().build()
                    GeneratedCollision = generate(20003)
                }
            }
        """
        result = _program({"material/CollidingMaterials.groovy": source})
        registrations = [
            row for row in result["declarations"]
            if row["semantic_descriptor"]["kind"] == "material-registration"
        ]
        self.assertEqual(2, len(registrations))
        for row in registrations:
            self.assertEqual("frontier-static", row["attributes"]["normalization_status"])
            self.assertIn(
                "identity:duplicate-numeric-id", row["attributes"]["issues"]
            )
            self.assertTrue(row["attributes"]["collision_candidates"])


if __name__ == "__main__":
    unittest.main()
