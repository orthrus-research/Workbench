"""Developer edits, not construction plans, drive the local source review."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workbench_project_intelligence.source_baseline import capture_baseline
from workbench_project_intelligence.working_tree import (
    capture_source_inputs,
    WorkingTreeError,
)
from workbench_shell.developer_context import DeveloperSelection, DeveloperContextError
from workbench_shell.developer_context_cli import run_selected_action
from test_developer_feature import ROOT, _checkout


class LocalSourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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
        self.script = self.pack / "groovy/postInit/chemistry/Probe.groovy"
        self.git("add", ".")
        self.git("commit", "-qm", "saved review baseline")
        self.baseline = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.pack), *args], text=True)

    def review(self, *args):
        return run_selected_action(
            self.selection,
            ["review", "local", "--baseline-ref", self.baseline, *args],
            suite_root=ROOT,
        )["result"]

    def test_recipe_edit_has_exact_diff_and_no_checkout_or_index_mutation(self):
        self.script.write_text(self.script.read_text().replace("1000", "1250"))
        before = capture_source_inputs(self.pack)
        index = (self.pack / ".git/index").read_bytes()
        review = self.review()
        self.assertEqual(review["checks"][0]["state"], "completed", review["checks"])
        self.assertEqual(review["baseline"]["revision"], self.baseline)
        self.assertTrue(review["changes"])
        file = next(
            row for row in review["files"] if row["path"].endswith("Probe.groovy")
        )
        self.assertIn("1250", file["after_text"])
        self.assertIn("1000", file["before_text"])
        self.assertEqual(capture_source_inputs(self.pack), before)
        self.assertEqual(index, (self.pack / ".git/index").read_bytes())
        self.assertFalse(review["authority"]["construction_authorized"])
        self.assertEqual(review["checks"][2]["state"], "not-run")

    def test_successive_edits_reject_old_review_but_not_selection(self):
        first = self.review()
        self.assertEqual(
            first["review_id"],
            self.review("--expect-review", first["review_id"])["review_id"],
        )
        self.script.write_text(self.script.read_text() + "\n// developer edit\n")
        with self.assertRaisesRegex(DeveloperContextError, "stale"):
            self.review("--expect-review", first["review_id"])
        self.assertNotEqual(first["review_id"], self.review()["review_id"])

    def test_added_removed_binary_and_unknown_files_remain_visible(self):
        self.script.unlink()
        (self.pack / "notes.txt").write_text("developer notes")
        (self.pack / "asset.bin").write_bytes(b"\0\xff")
        result = self.review()
        files = {row["path"]: row for row in result["files"]}
        self.assertEqual(files["notes.txt"]["state"], "added")
        self.assertEqual(files["asset.bin"]["after_text_state"], "over-bound-or-binary")
        self.assertEqual(
            files["groovy/postInit/chemistry/Probe.groovy"]["state"], "removed"
        )
        self.assertTrue(any(row["state"] == "removed" for row in result["changes"]))

    def test_interpreter_failure_preserves_file_review_without_clean_claim(self):
        self.script.write_text(self.script.read_text() + "\n// edit\n")
        with patch(
            "workbench_shell.developer_source_review.build_navigation_declarations",
            side_effect=ValueError("invalid syntax"),
        ):
            result = self.review()
        self.assertTrue(result["files"])
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["checks"][0]["state"], "unavailable")

    def test_material_and_quest_changes_are_analysis_not_authoring_families(self):
        material = self.pack / "groovy/material/Review.groovy"
        material.write_text(
            "Water = new Material.Builder(20001, SuSyUtility.susyId('water')).liquid().build()\n"
        )
        quest = self.pack / "config/betterquesting/DefaultQuests/Quests/review/1.json"
        quest.parent.mkdir(parents=True)
        quest.write_text(
            json.dumps(
                {
                    "questID:3": 1,
                    "preRequisites:11": [99],
                    "properties:10": {"betterquesting:10": {"name:8": "Water"}},
                }
            )
        )
        result = self.review()
        self.assertEqual(result["checks"][0]["state"], "completed", result["checks"])
        self.assertIn(
            "material",
            {row["after"]["kind"] for row in result["changes"] if row["after"]},
        )
        self.assertTrue(
            any(
                row["code"] == "quest-prerequisite-dangling"
                for row in result["findings"]
            )
        )

    def test_comment_only_edit_does_not_invent_recipe_changes(self):
        self.script.write_text("// comment\n" + self.script.read_text())
        result = self.review()
        self.assertTrue(result["files"])
        self.assertEqual(result["changes"], [])

    def test_duplicate_recipe_removal_does_not_guess_occurrence_identity(self):
        duplicate = self.script.with_name("Duplicate.groovy")
        duplicate.write_text(self.script.read_text())
        self.git("add", ".")
        self.git("commit", "-qm", "duplicate declarations")
        self.baseline = self.git("rev-parse", "HEAD").strip()
        duplicate.unlink()
        result = self.review()
        self.assertTrue(any(row["state"] == "ambiguous" for row in result["changes"]))
        ambiguous = next(
            row for row in result["changes"] if row["state"] == "ambiguous"
        )
        self.assertIsNone(ambiguous["before"])
        self.assertEqual(2, ambiguous["before_count"])
        self.assertEqual(1, ambiguous["after_count"])

    def test_material_comments_and_quest_formatting_are_not_semantic_changes(self):
        material = self.pack / "groovy/material/Review.groovy"
        material.write_text(
            "Water = new Material.Builder(20001, SuSyUtility.susyId('water')).liquid().build()\n"
        )
        quest = self.pack / "config/betterquesting/DefaultQuests/Quests/review/1.json"
        quest.parent.mkdir(parents=True)
        value = {
            "questID:3": 1,
            "properties:10": {"betterquesting:10": {"name:8": "Water"}},
        }
        quest.write_text(json.dumps(value))
        self.git("add", ".")
        self.git("commit", "-qm", "declared baseline")
        self.baseline = self.git("rev-parse", "HEAD").strip()
        material.write_text("// comment\n" + material.read_text())
        quest.write_text(json.dumps(value, indent=2))
        result = self.review()
        self.assertEqual(result["checks"][0]["state"], "completed")
        self.assertEqual(result["changes"], [])

    def test_ignored_generated_state_does_not_invalidate_review(self):
        (self.pack / ".gitignore").write_text("scratch/\n")
        first = self.review()
        (self.pack / "scratch").mkdir()
        (self.pack / "scratch/log").write_text("ignored output")
        self.assertEqual(first["review_id"], self.review()["review_id"])

    def test_executable_bit_only_change_is_visible(self):
        self.script.chmod(0o755)
        result = self.review()
        row = next(
            row for row in result["files"] if row["path"].endswith("Probe.groovy")
        )
        self.assertNotEqual(row["before_mode"], row["after_mode"])

    def test_baseline_is_local_and_rejects_options_and_links(self):
        with self.assertRaises(WorkingTreeError):
            capture_baseline(self.pack, "--help")
        (self.pack / "link").symlink_to("pack.toml")
        self.git("add", "link")
        self.git("commit", "-qm", "link fixture")
        with self.assertRaisesRegex(WorkingTreeError, "symlink"):
            capture_baseline(self.pack, "HEAD")

    def test_source_drift_during_analysis_rejects_result(self):
        from workbench_shell.developer_source_review import (
            build_navigation_declarations,
        )

        def drift(*args, **kwargs):
            result = build_navigation_declarations(*args, **kwargs)
            self.script.write_text(self.script.read_text() + "\n// concurrent edit")
            return result

        with patch(
            "workbench_shell.developer_source_review.build_navigation_declarations",
            side_effect=drift,
        ):
            with self.assertRaisesRegex(DeveloperContextError, "inputs changed"):
                self.review()
