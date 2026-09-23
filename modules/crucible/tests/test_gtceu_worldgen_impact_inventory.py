from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_gtceu_worldgen.inventory import (  # noqa: E402
    REQUIRED_API_CLASSES,
)
from workbench_crucible_gtceu_worldgen_impact import (  # noqa: E402
    GtceuWorldgenImpactValidationError,
    build_gtceu_worldgen_impact_inventory,
    parse_gtceu_worldgen_impact_inventory,
)
from workbench_crucible_gtceu_worldgen_impact.impact import (  # noqa: E402
    IMPACT_EXACT_CLASSES,
)


class GtceuWorldgenImpactInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.jar = self.runtime / "mods/gregtech.jar"
        self.jar.parent.mkdir(parents=True)
        with ZipFile(self.jar, "w") as archive:
            archive.writestr(
                "mcmod.info",
                json.dumps(
                    [
                        {
                            "modid": "gregtech",
                            "version": "2.8.10-beta",
                            "mcversion": "1.12.2",
                        }
                    ]
                ),
            )
            for name in sorted(set(REQUIRED_API_CLASSES) | set(IMPACT_EXACT_CLASSES)):
                archive.writestr(name, b"fixture-impact-class")
            archive.writestr(
                "gregtech/api/worldgen/populator/SurfaceRockPopulator.class",
                b"surface-rock",
            )

        with ZipFile(self.runtime / "mods/visualores.jar", "w") as archive:
            archive.writestr(
                "mixins.visualores.gregtech.json",
                json.dumps({"package": "fixture", "mixins": ["GridMixin"]}),
            )
            archive.writestr(
                "fixture/GridMixin.class",
                b"gregtech/api/worldgen/generator/CachedGridEntry",
            )
        with ZipFile(self.runtime / "mods/unrelated.jar", "w") as archive:
            archive.writestr("fixture/Unrelated.class", b"nothing relevant")

        self.config = self.runtime / "config/gregtech"
        self.write(
            self.config / "gregtech.cfg",
            """
"worldgen options" {
    B:addLoot=true
    I:additionalVeinsInSection=4
    B:allUniqueStoneTypes=false
    B:disableRubberTreeGeneration=false
    B:disableVanillaOres=true
    B:generateVeinsInCenterOfChunk=true
    B:increaseDungeonLoot=true
    I:minVeinsInSection=2
    D:rubberTreeRateIncrease=3.0
}
""".lstrip(),
        )
        self.write_json(
            self.config / "dimensions.json",
            {"dims": [{"dimID": 0, "dimName": "Overworld"}]},
        )
        self.write_json(
            self.config / "worldgen_extracted.json",
            {"fluidVersion": 2, "veinVersion": 1},
        )
        self.write_json(
            self.config / "worldgen/vein/overworld/kimberlite.json",
            {
                "weight": 80,
                "density": 0.75,
                "min_height": 0,
                "max_height": 80,
                "dimension_filter": ["dimension_id:0"],
                "biome_modifier": {"type": "biome_dictionary", "SWAMP": 2},
                "generator": {"type": "layered", "radius": [20, 20]},
                "filler": {
                    "type": "layered",
                    "values": [
                        {
                            "primary": {
                                "type": "weight_random",
                                "values": [
                                    {"weight": 1, "value": "ore:diamond"}
                                ],
                            }
                        },
                        {"secondary": "ore:graphite"},
                        {"between": "ore:coal"},
                        {"sporadic": "ore:emerald"},
                    ],
                },
                "vein_populator": {
                    "type": "surface_rock",
                    "material": "diamond",
                },
            },
        )
        self.write_json(
            self.config / "worldgen/fluid/overworld/oil.json",
            {
                "weight": 20,
                "name": "Oil",
                "yield": {"min": 100, "max": 200},
                "depletion": {
                    "amount": 1,
                    "chance": 105,
                    "depleted_yield": 10,
                },
                "fluid": "oil",
                "dimension_filter": ["name:overworld"],
            },
        )
        self.write(
            self.runtime / "config/noworldgen5you.cfg",
            "map_structures {\n B:disable_stronghold=true\n B:disable_village=true\n}\n",
        )
        self.write(
            self.runtime / "config/visualores.cfg",
            "general {\n B:doRetrogen=true\n B:cullEmptyChunks=true\n}\n",
        )
        self.write(
            self.runtime / "config/sussypatches.cfg",
            "tweaks {\n B:\"Use XoShiRo256++ Random\"=true\n"
            " B:\"Make surface populators populate the whole chunk\"=true\n}\n",
        )
        self.write(
            self.runtime / "groovy/postInit/recipes.groovy",
            "mods.gregtech.macerator.recipeBuilder()\n",
        )

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def build(self) -> dict:
        return build_gtceu_worldgen_impact_inventory(
            jar_path=self.jar,
            config_root=self.config,
            runtime_root=self.runtime,
        )

    def test_inventory_accounts_for_effects_controls_and_candidates(self) -> None:
        report = self.build()
        self.assertEqual(report["summary"]["ore_definition_count"], 1)
        self.assertEqual(report["summary"]["bedrock_fluid_definition_count"], 1)
        self.assertEqual(
            report["runtime_controls"]["derived"][
                "counted_ore_veins_per_3x3_grid"
            ]["maximum_inclusive"],
            6,
        )
        self.assertEqual(
            report["definition_controls"]["bedrock_fluid_definitions"][0][
                "depletion_chance_effective"
            ],
            100,
        )
        self.assertEqual(
            report["summary"]["active_weight_random_definition_count"], 1
        )
        factories = report["api_surface"]["registered_component_factories"]
        layered = next(
            row for row in factories["shape_generators"] if row["identifier"] == "layered"
        )
        self.assertEqual(layered["active_definition_count"], 1)
        self.assertEqual(
            report["api_surface"]["spatial_and_state_constants"][
                "bedrock_fluid_cell_chunks"
            ],
            [8, 8],
        )
        self.assertFalse(
            report["api_surface"]["groovyscript_boundary"][
                "dedicated_gtceu_worldgen_dsl_found_in_tagged_source"
            ]
        )
        effects = {row["effect_id"]: row for row in report["effect_catalog"]}
        self.assertEqual(effects["physical_ore_veins"]["status"], "active")
        self.assertFalse(effects["bedrock_fluid_cells"]["writes_blocks"])
        self.assertEqual(
            effects["fluid_spring_populator"]["status"],
            "available_not_selected",
        )
        candidates = report["runtime_integration_scan"]["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["artifact"]["relative_path"], "mods/visualores.jar"
        )
        self.assertFalse(
            report["boundaries"]["bytecode_token_match_proves_behavior"]
        )
        self.assertEqual(
            report["pack_context"]["groovyscript_direct_worldgen_api_scan"][
                "direct_worldgen_api_match_count"
            ],
            0,
        )
        self.assertTrue(
            report["pack_context"]["sussypatches"]["selected_values"][
                "Use XoShiRo256++ Random"
            ]
        )
        self.assertEqual(parse_gtceu_worldgen_impact_inventory(report), report)

    def test_reload_hazards_are_explicitly_unproven(self) -> None:
        report = self.build()
        command = report["reload_and_persistence"]["command"]
        self.assertFalse(command["calls_bedrock_fluid_recalculate_chances"])
        self.assertFalse(command["safe_as_general_hot_reload"])
        hazards = {
            row["hazard_id"]: row
            for row in report["reload_and_persistence"]["source_derived_hazards"]
        }
        self.assertEqual(
            hazards["fluid_reload_incomplete_invalidation"]["runtime_probe_status"],
            "not_run",
        )
        self.assertEqual(
            hazards["weighted_filler_shared_rng"]["active_definition_paths"],
            ["worldgen/vein/overworld/kimberlite.json"],
        )

    def test_identity_drift_and_missing_control_fail_closed(self) -> None:
        report = self.build()
        changed = deepcopy(report)
        changed["summary"]["ore_definition_count"] = 2
        with self.assertRaisesRegex(
            GtceuWorldgenImpactValidationError, "inventory ID drift"
        ):
            parse_gtceu_worldgen_impact_inventory(changed)

        config_text = (self.config / "gregtech.cfg").read_text(encoding="utf-8")
        (self.config / "gregtech.cfg").write_text(
            config_text.replace("    B:disableVanillaOres=true\n", ""),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            GtceuWorldgenImpactValidationError,
            "exactly one GTCEu control disableVanillaOres",
        ):
            self.build()


if __name__ == "__main__":
    unittest.main()
