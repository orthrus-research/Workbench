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

from workbench_crucible_gtceu_worldgen import (  # noqa: E402
    GtceuWorldgenValidationError,
    build_gtceu_worldgen_inventory,
    materialize_overlay,
    parse_overlay_materialization,
    parse_gtceu_worldgen_inventory,
)
from workbench_crucible_gtceu_worldgen.inventory import (  # noqa: E402
    REQUIRED_API_CLASSES,
)


class GtceuWorldgenInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jar = self.root / "gregtech.jar"
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
            for name in REQUIRED_API_CLASSES:
                archive.writestr(name, b"fixture-api")

        self.config = self.root / "config/gregtech"
        self.write_json(
            self.config / "dimensions.json",
            {"dims": [{"dimID": 0, "dimName": "Overworld"}]},
        )
        self.write_json(
            self.config / "worldgen_extracted.json",
            {"fluidVersion": 2, "veinVersion": 1},
        )
        self.ore_path = self.config / "worldgen/vein/overworld/fluorite.json"
        self.write_json(self.ore_path, self.ore_definition())
        self.write_json(
            self.config / "worldgen/fluid/overworld/oil.json",
            {
                "weight": 20,
                "name": "Oil",
                "yield": {"min": 100, "max": 200},
                "depletion": {"amount": 1, "chance": 5, "depleted_yield": 10},
                "fluid": "oil",
            },
        )
        self.package = self.root / "observed.strataview"
        self.write_json(
            self.package,
            {
                "schema": "strata.strataview.package.v1",
                "chunkWindow": {
                    "minChunkX": 0,
                    "minChunkZ": 0,
                    "chunkSizeX": 1,
                    "chunkSizeZ": 1,
                    "haloChunks": 1,
                },
                "resourceStats": {
                    "blockStateCounts": [
                        {
                            "blockState": "gregtech:ore_fluorite_0[stone_type=stone]",
                            "count": 42,
                        }
                    ]
                },
            },
        )

    @staticmethod
    def ore_definition() -> dict:
        return {
            "weight": 80,
            "density": 0.75,
            "min_height": 0,
            "max_height": 80,
            "dimension_filter": ["dimension_id:0"],
            "generator": {"type": "layered", "radius": [20, 20]},
            "filler": {
                "type": "layered",
                "values": [
                    {"primary": "ore:fluorite"},
                    {"secondary": "ore:fluorite"},
                    {"between": "ore:fluorite"},
                    {"sporadic": "ore:sphalerite"},
                ],
            },
            "vein_populator": {"type": "surface_rock", "material": "fluorite"},
        }

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    def build(self) -> dict:
        return build_gtceu_worldgen_inventory(
            jar_path=self.jar,
            config_root=self.config,
            strataview_path=self.package,
        )

    def test_exact_inventory_quantifies_and_correlates_without_causality(self) -> None:
        report = self.build()
        self.assertEqual(report["summary"]["definition_count"], 2)
        self.assertEqual(report["summary"]["ore_definition_count"], 1)
        self.assertEqual(report["summary"]["fluid_definition_count"], 1)
        self.assertIn("fluorite", report["summary"]["material_tokens"])
        self.assertEqual(report["observation"]["observed_gtceu_block_count"], 42)
        self.assertEqual(
            report["observation"]["states"][0]["candidate_definition_paths"],
            ["worldgen/vein/overworld/fluorite.json"],
        )
        self.assertFalse(report["boundaries"]["observed_state_proves_deposit_cause"])
        self.assertEqual(parse_gtceu_worldgen_inventory(report), report)

    def test_version_and_identity_drift_fail_closed(self) -> None:
        report = self.build()
        changed = deepcopy(report)
        changed["summary"]["definition_count"] = 3
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "ID drift"):
            parse_gtceu_worldgen_inventory(changed)

        with ZipFile(self.jar, "w") as archive:
            archive.writestr(
                "mcmod.info",
                json.dumps(
                    [{"modid": "gregtech", "version": "2.9.0", "mcversion": "1.12.2"}]
                ),
            )
            for name in REQUIRED_API_CLASSES:
                archive.writestr(name, b"fixture-api")
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "expected GTCEu"):
            self.build()

    def test_checked_overlay_changes_only_a_fresh_materialization(self) -> None:
        report = self.build()
        definition = self.ore_definition()
        definition["weight"] = 81
        ore_binding = next(
            row
            for row in report["configuration"]["files"]
            if row["relative_path"] == "worldgen/vein/overworld/fluorite.json"
        )
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": report["inventory_id"],
            "operations": [
                {
                    "op": "replace",
                    "kind": "ore",
                    "relative_path": "worldgen/vein/overworld/fluorite.json",
                    "expected_sha256": ore_binding["sha256"],
                    "definition": definition,
                }
            ],
        }
        output = self.root / "materialized/config/gregtech"
        materialization = materialize_overlay(
            jar_path=self.jar,
            config_root=self.config,
            inventory=report,
            plan=plan,
            output_config_root=output,
        )
        self.assertEqual(json.loads(self.ore_path.read_text())["weight"], 80)
        self.assertEqual(
            json.loads(
                (output / "worldgen/vein/overworld/fluorite.json").read_text()
            )["weight"],
            81,
        )
        self.assertNotEqual(
            materialization["source_inventory_id"],
            materialization["output_inventory_id"],
        )
        self.assertEqual(
            parse_overlay_materialization(materialization),
            materialization,
        )
        changed = deepcopy(materialization)
        changed["operation_count"] = 2
        with self.assertRaisesRegex(
            GtceuWorldgenValidationError,
            "operation count drift",
        ):
            parse_overlay_materialization(changed)

    def test_overlay_rejects_source_drift_before_creating_output(self) -> None:
        report = self.build()
        definition = self.ore_definition()
        definition["weight"] = 81
        binding = next(
            row
            for row in report["configuration"]["files"]
            if row["relative_path"] == "worldgen/vein/overworld/fluorite.json"
        )
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": report["inventory_id"],
            "operations": [
                {
                    "op": "replace",
                    "kind": "ore",
                    "relative_path": "worldgen/vein/overworld/fluorite.json",
                    "expected_sha256": binding["sha256"],
                    "definition": definition,
                }
            ],
        }
        changed_source = self.ore_definition()
        changed_source["weight"] = 79
        self.write_json(self.ore_path, changed_source)
        output = self.root / "materialized/config/gregtech"
        with self.assertRaisesRegex(
            GtceuWorldgenValidationError,
            "configuration drifted",
        ):
            materialize_overlay(
                jar_path=self.jar,
                config_root=self.config,
                inventory=report,
                plan=plan,
                output_config_root=output,
            )
        self.assertFalse(output.exists())

    def test_overlay_rejects_unknown_or_action_inappropriate_fields(self) -> None:
        report = self.build()
        plan = {
            "format": "workbench-crucible-gtceu-worldgen-overlay-v1",
            "schema_version": 1,
            "target_inventory_id": report["inventory_id"],
            "operations": [
                {
                    "op": "add",
                    "kind": "ore",
                    "relative_path": "worldgen/vein/overworld/new.json",
                    "expected_sha256": "0" * 64,
                    "definition": self.ore_definition(),
                }
            ],
        }
        with self.assertRaisesRegex(GtceuWorldgenValidationError, "fields drift"):
            materialize_overlay(
                jar_path=self.jar,
                config_root=self.config,
                inventory=report,
                plan=plan,
                output_config_root=self.root / "materialized/config/gregtech",
            )


if __name__ == "__main__":
    unittest.main()
