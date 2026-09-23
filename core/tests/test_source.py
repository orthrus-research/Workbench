from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from workbench_core.source import SourceIndex


class SourceIndexTests(unittest.TestCase):
    def test_prunes_generated_and_private_storage_before_descent(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            hidden = root / ".workbench/deep/Hidden.java"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("class Hidden {}\n", encoding="utf-8")
            visible = root / "src/main/java/Visible.java"
            visible.parent.mkdir(parents=True)
            visible.write_text("class Visible {}\n", encoding="utf-8")
            index = SourceIndex(root)
            self.assertEqual(
                index.resolve({"candidate": "Visible.java"})["resolution"],
                "unique",
            )
            self.assertEqual(
                index.resolve({"candidate": "pkg/Hidden.java"})["resolution"],
                "unresolved",
            )

    def test_incomplete_index_never_claims_a_unique_match(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            for name in ("First.java", "Second.java", "Third.java"):
                (root / name).write_text(f"class {name[:-5]} {{}}\n", encoding="utf-8")
            index = SourceIndex(
                root,
                max_scanned_entries=1,
                max_source_files=10,
            )
            resolutions = {
                index.resolve({"candidate": f"nested/{name}"})["resolution"]
                for name in ("First.java", "Second.java", "Third.java")
            }
            self.assertNotIn("unique", resolutions)
            self.assertTrue(
                resolutions
                <= {"indeterminate-index-limited", "unresolved-index-limited"}
            )
            self.assertLessEqual(index._scanned_entries, 2)


if __name__ == "__main__":
    unittest.main()
