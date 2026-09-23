from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from workbench_project_intelligence.git_tree import _windows_extended_path
from workbench_project_intelligence.saved_candidate import (
    candidate_manifest,
    stage_candidate,
)
from workbench_project_intelligence.working_tree import SourceInputs, WorkingTreeError


class SavedCandidateTests(unittest.TestCase):
    def test_exact_bytes_and_modes_without_git_or_construction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source with spaces"
            source.mkdir()
            inputs = SourceInputs(
                json.dumps({"root_uri": source.as_uri()}),
                (("added.txt", b"saved\r\n"),),
                (("added.txt", 0o100755),),
            )
            manifest = stage_candidate(inputs, root / "stage")
            self.assertEqual(manifest, candidate_manifest(inputs))
            self.assertEqual((root / "stage/added.txt").read_bytes(), b"saved\r\n")
            self.assertEqual(manifest["files"][0]["mode"], 0o100755)
            if os.name != "nt":
                self.assertEqual((root / "stage/added.txt").stat().st_mode & 0o777, 0o755)
            self.assertFalse((root / "stage/.git").exists())
            with self.assertRaisesRegex(WorkingTreeError, "overlaps"):
                stage_candidate(inputs, source / "stage")

    def test_rejects_case_collisions_and_git_paths(self):
        for files in ((("A", b"a"), ("a", b"b")), ((".git/config", b"bad"),)):
            with self.assertRaises(WorkingTreeError):
                candidate_manifest(
                    SourceInputs(
                        "{}", files, tuple((name, 0o100644) for name, _ in files)
                    )
                )

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source", root / "retained"
            source.mkdir()
            target.mkdir()
            (target / "recipe.groovy").write_bytes(b"previous attempt")
            inputs = SourceInputs(json.dumps({"root_uri": source.as_uri()}),
                                  (("recipe.groovy", b"new captured bytes"),),
                                  (("recipe.groovy", 0o100644),))
            with self.assertRaises(FileExistsError):
                stage_candidate(inputs, target)
            self.assertEqual((target / "recipe.groovy").read_bytes(), b"previous attempt")

    @unittest.skipUnless(os.name == "nt", "Native Windows long-path staging")
    def test_long_destination_and_source_row_preserve_manifest_and_captured_bytes(self):
        with tempfile.TemporaryDirectory(prefix="candidate-long-") as temporary:
            root = Path(temporary)
            source = root / "Source 資料"
            source.mkdir()
            parent = root.joinpath("retained", "a" * 90, "b" * 90, "c" * 90)
            target = parent / "candidate"
            relative = "/".join(("scripts", "captured_" * 10, "recipes_" * 10, "variants_" * 10, "recipe.groovy"))
            captured = b"exact captured recipe\r\n\x00\xff"
            original = _windows_extended_path(source / relative)
            try:
                _windows_extended_path(parent).mkdir(parents=True)
                original.parent.mkdir(parents=True)
                original.write_bytes(captured)
                inputs = SourceInputs(json.dumps({"root_uri": source.as_uri()}),
                                      ((relative, captured),), ((relative, 0o100644),))
                expected = candidate_manifest(inputs)
                original.write_bytes(b"later live edit")
                self.assertGreater(len(str(target)), 260)
                self.assertGreater(len(str(source / relative)), 260)
                actual = stage_candidate(inputs, target)
                self.assertEqual(actual, expected)
                self.assertEqual(actual["source"]["root_uri"], source.as_uri())
                self.assertEqual(actual["files"][0]["path"], relative)
                self.assertNotIn("\\\\?\\", json.dumps(actual))
                self.assertEqual(_windows_extended_path(target / relative).read_bytes(), captured)
                self.assertEqual(original.read_bytes(), b"later live edit")
                with self.assertRaisesRegex(WorkingTreeError, "overlaps"):
                    stage_candidate(inputs, source / "retained")
                self.assertFalse(_windows_extended_path(source / "retained").exists())
            finally:
                shutil.rmtree(_windows_extended_path(root))

    @unittest.skipUnless(os.name == "nt", "Native Windows junction refusal")
    def test_destination_junction_cannot_bypass_source_overlap_refusal(self):
        with tempfile.TemporaryDirectory(prefix="candidate-junction-") as temporary:
            root = Path(temporary)
            source, alias = root / "source", root / "alias"
            source.mkdir()
            inputs = SourceInputs(json.dumps({"root_uri": source.as_uri()}),
                                  (("recipe.groovy", b"captured"),),
                                  (("recipe.groovy", 0o100644),))
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(source)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            try:
                with self.assertRaisesRegex(WorkingTreeError, "traverses a link"):
                    stage_candidate(inputs, alias / "retained")
                self.assertFalse((source / "retained").exists())
            finally:
                alias.rmdir()
