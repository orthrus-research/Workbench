#!/usr/bin/env python3

"""Focused rendering checks for ready Supersymmetry wizard patterns."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from _support import SOURCE_ROOT, WORKBENCH_ROOT


if str(SOURCE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT.parent))

from workbench_blueprints import registration_catalog  # noqa: E402
from workbench_blueprints import registration_render  # noqa: E402


PROFILE_ROOT = WORKBENCH_ROOT / "profiles/packs/supersymmetry"
CATALOG_PATH = PROFILE_ROOT / "registration/catalog-v1.json"


def _target(root: Path) -> None:
    material = root / "groovy/material"
    material.mkdir(parents=True)
    (material / "SuSyMaterials.groovy").write_text(
        "package material\n\n"
        "class SuSyMaterials {\n\n"
        "    // Petrochem Materials\n\n"
        "    public static Material ExistingFluid\n\n"
        "    // First Degree Materials A\n"
        "}\n",
        encoding="utf-8",
    )
    (material / "PetrochemistryMaterials.groovy").write_text(
        "package material\n\n"
        "import static material.SuSyMaterials.*\n\n"
        "class PetrochemistryMaterials {\n\n"
        "    static void register() {\n\n"
        "        ExistingFluid = new Material.Builder(20000, "
        "SuSyUtility.susyId('existing_fluid'))\n"
        "                .liquid()\n"
        "                .color(0x111111)\n"
        "                .flags(FLAMMABLE)\n"
        "                .build()\n"
        "    }\n"
        "}",
        encoding="utf-8",
    )
    language = root / "resources/langfiles/lang/en_us.lang"
    language.parent.mkdir(parents=True)
    language.write_text(
        "# Fluids\n\n"
        "susy.material.existing_fluid=Existing Fluid\n"
        "\n# Thermodynamics\n",
        encoding="utf-8",
    )

    prepost = root / "groovy/prePostInit"
    prepost.mkdir(parents=True)
    (prepost / "Recipemaps.groovy").write_text(
        "package prePostInit\n\n"
        "class Recipemaps {\n"
        "    static final def MIXER = recipemap('mixer')\n"
        "    static final def BR = recipemap('batch_reactor')\n"
        "}\n",
        encoding="utf-8",
    )
    (prepost / "oreDict.groovy").write_text(
        "package prePostInit;\n\n"
        "ore('dustExisting').add(metaitem('dustExisting'))\n",
        encoding="utf-8",
    )
    postinit = root / "groovy/postInit/chemistry"
    postinit.mkdir(parents=True)
    (postinit / "Probe.groovy").write_text(
        "import static prePostInit.Recipemaps.*\n"
        "import static gregtech.api.GTValues.*\n\n"
        "MIXER.recipeBuilder()\n"
        "    .fluidInputs(fluid('water') * 1000)\n"
        "    .fluidOutputs(fluid('distilled_water') * 1000)\n"
        "    .duration(20)\n"
        "    .EUt(VA[LV])\n"
        "    .buildAndRegister()\n",
        encoding="utf-8",
    )


class RegistrationRenderTest(unittest.TestCase):

    def setUp(self) -> None:
        self.catalog = registration_catalog.load_registration_catalog(
            CATALOG_PATH
        )

    def test_material_uses_central_declaration_category_and_language(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _target(root)
            rendered = registration_render.render_registration(
                self.catalog,
                "material-backed-fluid",
                PROFILE_ROOT,
                root,
                {"name": "Pilot Coolant", "color": "0x425d73"},
                facts={
                    "material_census": {
                        "census_id": "atlas-material-census:sha256:" + "1" * 64,
                        "uncertainties": [],
                        "collisions": [],
                        "observations": [{
                            "material_id": 20000,
                            "registry_name": "existing_fluid",
                        }],
                        "occupied_values": [20000],
                    }
                },
            )

            self.assertEqual(rendered["effective_answers"]["material_id"], 20001)
            self.assertEqual(
                [row["path"] for row in rendered["operations"]],
                [
                    "groovy/material/PetrochemistryMaterials.groovy",
                    "groovy/material/SuSyMaterials.groovy",
                    "resources/langfiles/lang/en_us.lang",
                ],
            )
            text = {
                row["path"]: row["content"].decode("utf-8")
                for row in rendered["operations"]
            }
            self.assertIn(
                "PilotCoolant = new Material.Builder(20001, "
                "SuSyUtility.susyId('pilot_coolant'))",
                text["groovy/material/PetrochemistryMaterials.groovy"],
            )
            self.assertNotIn("eventManager.listen", "\n".join(text.values()))

    def test_machine_recipe_uses_existing_alias_and_repeated_builder_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _target(root)
            rendered = registration_render.render_registration(
                self.catalog,
                "machine-recipe",
                PROFILE_ROOT,
                root,
                {
                    "script": "groovy/postInit/chemistry/Probe.groovy",
                    "recipe_map": "BR",
                    "item_inputs": [
                        {"kind": "ore", "name": "dustSulfur", "amount": 2},
                        {
                            "kind": "metaitem",
                            "name": "dustSodiumHydroxide",
                            "amount": 1,
                        },
                    ],
                    "fluid_inputs": [{"name": "water", "amount": 1000}],
                    "item_outputs": [{
                        "kind": "item",
                        "name": "minecraft:clay_ball",
                        "metadata": 0,
                        "amount": 4,
                    }],
                    "fluid_outputs": [],
                    "duration": 100,
                    "voltage_tier": "LV",
                },
            )

            updated = rendered["operations"][0]["content"].decode("utf-8")
            self.assertIn(
                "BR.recipeBuilder()\n"
                "    .inputs(ore('dustSulfur') * 2)\n"
                "    .inputs(metaitem('dustSodiumHydroxide'))\n"
                "    .fluidInputs(fluid('water') * 1000)\n"
                "    .outputs(item('minecraft:clay_ball', 0) * 4)\n"
                "    .duration(100)\n"
                "    .EUt(VA[LV])\n"
                "    .buildAndRegister()\n",
                updated,
            )
            self.assertEqual(
                rendered["evidence"]["recipe_map_registry_name"],
                "batch_reactor",
            )

    def test_crlf_owners_render_only_crlf_update_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _target(root)
            for path in root.rglob("*"):
                if path.is_file():
                    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

            composed = registration_render.render_material_fluid_recipe(
                self.catalog,
                PROFILE_ROOT,
                root,
                material_answers={
                    "name": "Pilot Coolant",
                    "color": "0x425d73",
                },
                recipe_answers={
                    "script": "groovy/postInit/chemistry/Probe.groovy",
                    "recipe_map": "MIXER",
                    "item_inputs": [],
                    "fluid_inputs": [{"name": "steam", "amount": 500}],
                    "item_outputs": [],
                    "output_amount": 250,
                    "duration": 320,
                    "voltage_tier": "MV",
                },
                material_census={
                    "census_id": "atlas-material-census:sha256:" + "1" * 64,
                    "uncertainties": [],
                    "collisions": [],
                    "observations": [
                        {
                            "material_id": 20000,
                            "registry_name": "existing_fluid",
                        }
                    ],
                    "occupied_values": [20000],
                },
            )
            ore_dictionary = registration_render.render_registration(
                self.catalog,
                "ore-dictionary-entry",
                PROFILE_ROOT,
                root,
                {
                    "ore_name": "dustWorkbenchProbe",
                    "ingredient": {
                        "kind": "metaitem",
                        "name": "dustSodiumHydroxide",
                    },
                },
            )

            operations = [
                *composed["operations"],
                *ore_dictionary["operations"],
            ]
            self.assertEqual(
                {operation["path"] for operation in operations},
                {
                    "groovy/material/PetrochemistryMaterials.groovy",
                    "groovy/material/SuSyMaterials.groovy",
                    "groovy/postInit/chemistry/Probe.groovy",
                    "groovy/prePostInit/oreDict.groovy",
                    "resources/langfiles/lang/en_us.lang",
                },
            )
            for operation in operations:
                with self.subTest(path=operation["path"]):
                    content = operation["content"]
                    self.assertIn(b"\r\n", content)
                    without_crlf = content.replace(b"\r\n", b"")
                    self.assertNotIn(b"\r", without_crlf)
                    self.assertNotIn(b"\n", without_crlf)

    def test_rejects_mixed_or_bare_cr_registration_owner_line_endings(
        self,
    ) -> None:
        cases = (
            (
                "machine-recipe",
                Path("groovy/postInit/chemistry/Probe.groovy"),
                {
                    "script": "groovy/postInit/chemistry/Probe.groovy",
                    "recipe_map": "MIXER",
                    "item_inputs": [],
                    "fluid_inputs": [{"name": "water", "amount": 1000}],
                    "item_outputs": [],
                    "fluid_outputs": [
                        {"name": "distilled_water", "amount": 1000}
                    ],
                    "duration": 20,
                    "voltage_tier": "LV",
                },
                "recipe owning script has mixed or bare-CR line endings",
            ),
            (
                "ore-dictionary-entry",
                Path("groovy/prePostInit/oreDict.groovy"),
                {
                    "ore_name": "dustWorkbenchProbe",
                    "ingredient": {
                        "kind": "metaitem",
                        "name": "dustSodiumHydroxide",
                    },
                },
                "ore dictionary owner has mixed or bare-CR line endings",
            ),
        )
        for pattern_key, relative, answers, expected in cases:
            for variant in ("mixed", "bare-cr"):
                with self.subTest(pattern=pattern_key, variant=variant):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        _target(root)
                        path = root / relative
                        content = path.read_bytes()
                        if variant == "mixed":
                            content = content.replace(b"\n", b"\r\n", 1)
                        else:
                            content = content.replace(b"\n", b"\r")
                        path.write_bytes(content)

                        with self.assertRaises(
                            registration_render.RegistrationRenderError
                        ) as raised:
                            registration_render.render_registration(
                                self.catalog,
                                pattern_key,
                                PROFILE_ROOT,
                                root,
                                answers,
                            )
                        self.assertEqual(str(raised.exception), expected)

    def test_material_fluid_recipe_composes_four_existing_owner_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _target(root)
            before_ore_dictionary = (
                root / "groovy/prePostInit/oreDict.groovy"
            ).read_bytes()
            rendered = registration_render.render_material_fluid_recipe(
                self.catalog,
                PROFILE_ROOT,
                root,
                material_answers={
                    "name": "Pilot Coolant",
                    "color": "0x425d73",
                },
                recipe_answers={
                    "script": "groovy/postInit/chemistry/Probe.groovy",
                    "recipe_map": "MIXER",
                    "item_inputs": [],
                    "fluid_inputs": [{"name": "steam", "amount": 500}],
                    "item_outputs": [],
                    "output_amount": 250,
                    "duration": 320,
                    "voltage_tier": "MV",
                },
                material_census={
                    "census_id": "atlas-material-census:sha256:" + "1" * 64,
                    "uncertainties": [],
                    "collisions": [],
                    "observations": [
                        {
                            "material_id": 20000,
                            "registry_name": "existing_fluid",
                        }
                    ],
                    "occupied_values": [20000],
                },
            )

            self.assertEqual(
                [row["path"] for row in rendered["operations"]],
                [
                    "groovy/material/PetrochemistryMaterials.groovy",
                    "groovy/material/SuSyMaterials.groovy",
                    "groovy/postInit/chemistry/Probe.groovy",
                    "resources/langfiles/lang/en_us.lang",
                ],
            )
            updated_recipe = rendered["operations"][2]["content"].decode("utf-8")
            self.assertIn(
                ".fluidOutputs(fluid('pilot_coolant') * 250)",
                updated_recipe,
            )
            self.assertEqual(
                rendered["evidence"]["unification"],
                "verified-material-fluid-identity-no-extra-ore-entry",
            )
            self.assertEqual(
                (root / "groovy/prePostInit/oreDict.groovy").read_bytes(),
                before_ore_dictionary,
            )

    def test_ore_dictionary_appends_to_the_central_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _target(root)
            rendered = registration_render.render_registration(
                self.catalog,
                "ore-dictionary-entry",
                PROFILE_ROOT,
                root,
                {
                    "ore_name": "dustWorkbenchProbe",
                    "ingredient": {
                        "kind": "metaitem",
                        "name": "dustSodiumHydroxide",
                    },
                },
            )

            self.assertEqual(
                rendered["operations"][0]["path"],
                "groovy/prePostInit/oreDict.groovy",
            )
            self.assertTrue(
                rendered["operations"][0]["content"].decode("utf-8").endswith(
                    "ore('dustWorkbenchProbe').add("
                    "metaitem('dustSodiumHydroxide'))\n"
                )
            )


if __name__ == "__main__":
    unittest.main()
