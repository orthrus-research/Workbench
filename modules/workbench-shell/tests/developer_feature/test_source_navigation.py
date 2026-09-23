"""End-to-end source-only service contract over a real selected Git checkout."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from workbench_atlas.source_navigation import SourceNavigationError
from workbench_pack_program_studio.source_intelligence import (
    build_navigation_declarations,
)
from workbench_project_intelligence.working_tree import capture_source_inputs
from workbench_shell.developer_context import DeveloperSelection
from workbench_shell.developer_context_cli import run_selected_action
from workbench_api.profiles import profile_scope
from workbench_api.profile_extensions import ProfileExtensionError
from workbench_pack_program_studio.source_intelligence import source_interpreter
from workbench_profile_supersymmetry.source_intelligence import _ingredient
from test_developer_feature import ROOT, _checkout


class SourceNavigationTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.pack = _checkout(Path(self.temp.name))
        self.selection = DeveloperSelection(
            self.pack.as_uri(), "supersymmetry", "cleanroom", "cleanroom-provisional"
        )
        (self.pack / "groovy/runConfig.json").write_text(
            json.dumps(
                {
                    "packName": "Supersymmetry",
                    "packId": "supersymmetry",
                    "version": "fixture",
                    "debug": False,
                    "loaders": {
                        "preInit": ["classes/", "material/", "preInit/"],
                        "postInit": ["prePostInit/", "postInit/"],
                    },
                }
            )
        )
        material = self.pack / "groovy/material/Test.groovy"
        material.parent.mkdir(exist_ok=True)
        material.write_text(
            "Water = new Material.Builder(20001, SuSyUtility.susyId('water')).liquid().build()\n"
        )
        for qid, prerequisite in ((1, 2), (2, 1), (3, 99)):
            path = (
                self.pack
                / f"config/betterquesting/DefaultQuests/Quests/test/{qid}.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "questID:3": qid,
                        "preRequisites:11": [prerequisite],
                        "properties:10": {
                            "betterquesting:10": {"name:8": f"Water quest {qid}"}
                        },
                        "tasks:9": {
                            "0:10": {
                                "taskID:8": "bq_standard:retrieval",
                                "requiredFluids:9": {
                                    "0:10": {"FluidName:8": "water", "Amount:3": 1000}
                                },
                            }
                        },
                    }
                )
            )

    def action(self, *arguments):
        return run_selected_action(
            self.selection, ["source", *arguments], suite_root=ROOT
        )

    def test_real_native_source_chain_and_exact_locations(self):
        search = self.action("search", "water", "--kind", "material")
        self.assertEqual(self.selection.id, search["context"]["selection_id"])
        result = search["result"]
        self.assertEqual(1, len(result["results"]))
        selected = result["results"][0]["selection_id"]
        related = self.action("related", selected)["result"]
        self.assertIn("quest", {row["kind"] for row in related["nodes"]})
        self.assertIn("recipe", {row["kind"] for row in related["nodes"]})
        self.assertIn(
            "declares-fluid-form", {row["relation"] for row in related["relationships"]}
        )
        self.assertTrue(related["context"]["prerequisite_cycles"]["back_edges"])
        self.assertEqual(1, related["context"]["relationship_states"]["dangling"])
        self.assertEqual("none", related["context"]["authority"]["runtime_authority"])
        location = self.action("location", selected)["result"]["location"]
        self.assertEqual("groovy/material/Test.groovy", location["path"])
        self.assertEqual("one-based-utf16", location["coordinate_system"])
        self.assertFalse((self.pack / ".workbench").exists())

    def test_snapshot_selection_rejects_unrelated_same_status_edit(self):
        path = self.pack / "note.txt"
        path.write_text("one")
        selected = self.action("search", "", "--kind", "recipe")["result"]["results"][
            0
        ]["selection_id"]
        path.write_text("two")
        with self.assertRaisesRegex(SourceNavigationError, "stale"):
            self.action("location", selected)

    def test_captured_inputs_are_immutable_and_normalization_does_not_reread(self):
        inputs = capture_source_inputs(self.pack)
        before = build_navigation_declarations(
            inputs,
            pack_profile="supersymmetry",
            platform_profile="cleanroom",
            variant="cleanroom-provisional",
        )
        (self.pack / "groovy/material/Test.groovy").write_text("changed")
        after = build_navigation_declarations(
            inputs,
            pack_profile="supersymmetry",
            platform_profile="cleanroom",
            variant="cleanroom-provisional",
        )
        self.assertEqual(before, after)
        with self.assertRaises(TypeError):
            inputs.sources["new"] = b"bad"

    def test_cached_interpreter_cannot_bypass_profile_admission(self):
        owner = source_interpreter("supersymmetry")
        inputs = capture_source_inputs(self.pack)
        with profile_scope(disabled=("supersymmetry",)):
            with self.assertRaises(ProfileExtensionError):
                owner.source_declarations(
                    inputs,
                    platform_profile="cleanroom",
                    variant="cleanroom-provisional",
                )

    def test_selector_kinds_remain_distinct_and_dynamic_expressions_unknown(self):
        kinds = [
            _ingredient(expression)[0]["kind"]
            for expression in (
                "fluid('water') * 1000",
                "ore('dustWater')",
                "item('minecraft:stone', 2)",
                "metaitem('dust.water')",
            )
        ]
        self.assertEqual(["fluid", "ore-dictionary", "item", "metaitem"], kinds)
        for expression in (
            "fluid(name)",
            "fluid('water').withTag(data)",
            "choose(fluid('water'))",
        ):
            self.assertIsNone(_ingredient(expression)[0])
        self.assertEqual(
            "unresolved", _ingredient("fluid('water') * amount")[1]["quantity_state"]
        )

    def test_unconnected_calls_do_not_complete_a_recipe_builder(self):
        path = self.pack / "groovy/postInit/chemistry/Probe.groovy"
        path.write_text(
            "MIXER.recipeBuilder();\nother.fluidInputs(fluid('wrong')); other.buildAndRegister()\n"
        )
        selected = self.action("search", "", "--kind", "recipe")["result"]["results"][
            0
        ]["selection_id"]
        inspected = self.action("inspect", selected)["result"]
        self.assertIn("recipe-builder-incomplete", inspected["selection"]["issues"])
        self.assertFalse(
            any(row["relation"] == "consumes" for row in inspected["relationships"])
        )

    def test_profile_resource_changes_invalidate_old_source_selections(self):
        from workbench_pack_program_studio import source_intelligence as owner

        original = owner.source_profile_resources
        selected = self.action("search", "water", "--kind", "material")["result"][
            "results"
        ][0]["selection_id"]

        def changed(*args):
            rows = original(*args)
            rows[0]["sha256"] = "b" * 64
            return rows

        with patch.object(owner, "source_profile_resources", side_effect=changed):
            with self.assertRaises(SourceNavigationError):
                self.action("location", selected)


if __name__ == "__main__":
    unittest.main()
