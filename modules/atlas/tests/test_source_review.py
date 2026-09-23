import json
import unittest
from unittest.mock import patch

from workbench_atlas.source_review import build_source_review
from workbench_project_intelligence.working_tree import SourceInputs


def inputs(files, root="file:///fixture"):
    return SourceInputs(
        json.dumps({"root_uri": root, "revision": "a" * 40}), tuple(files.items())
    )


class SourceReviewTests(unittest.TestCase):
    def test_unknown_edits_are_exact_files_not_semantic_approval(self):
        result = build_source_review(
            inputs({"old.txt": b"old"}), inputs({"new.txt": b"new"})
        )
        self.assertEqual(
            {"added", "removed"}, {row["state"] for row in result["files"]}
        )
        self.assertEqual("unavailable", result["checks"][0]["state"])
        self.assertFalse(result["authority"]["source_mutated"])

    def test_empty_binary_and_over_bound_text_are_distinguished(self):
        with patch("workbench_atlas.source_review.MAX_TEXT_BYTES", 2):
            result = build_source_review(
                inputs({}), inputs({"empty": b"", "binary": b"\0", "large": b"abc"})
            )
        files = {row["path"]: row for row in result["files"]}
        self.assertEqual("included", files["empty"]["after_text_state"])
        self.assertEqual("", files["empty"]["after_text"])
        self.assertEqual("over-bound-or-binary", files["binary"]["after_text_state"])
        self.assertIsNone(files["large"]["after_text"])

    def test_bounded_projection_reports_exact_count_and_truncation(self):
        with patch("workbench_atlas.source_review.MAX_FILES", 1):
            result = build_source_review(inputs({}), inputs({"a": b"a", "b": b"b"}))
        self.assertEqual(2, result["counts"]["files"])
        self.assertEqual(1, len(result["files"]))
        self.assertTrue(result["truncated"]["files"])

    def test_cannot_compare_another_workspace_as_local_changes(self):
        with self.assertRaisesRegex(ValueError, "different workspaces"):
            build_source_review(inputs({}), inputs({}, root="file:///elsewhere"))
