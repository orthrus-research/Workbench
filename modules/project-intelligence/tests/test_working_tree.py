"""Stable saved source bytes and deletion names share one observation."""

from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workbench_project_intelligence import working_tree
from workbench_project_intelligence.saved_candidate import candidate_manifest


@unittest.skipUnless(shutil.which("git"), "Git is required for source observation")
class WorkingTreeInputsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-source-inputs-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "source with spaces é"
        self.root.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Workbench Test")
        self.git("config", "user.email", "workbench@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.root / "present.txt").write_bytes(b"original\n")
        (self.root / "removed é.txt").write_bytes(b"removed\n")
        (self.root / ".gitignore").write_bytes(b"ignored.txt\n")
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "source fixture")

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *arguments], stderr=subprocess.PIPE
        )

    def test_saved_addition_change_and_deletion_are_one_immutable_capture(self):
        (self.root / "present.txt").write_bytes(b"saved\r\n")
        (self.root / "removed é.txt").unlink()
        (self.root / "new é.txt").write_bytes(b"new\0\xff")
        (self.root / "ignored.txt").write_bytes(b"ignored")
        before_index = (self.root / ".git/index").read_bytes()
        # Accessing the capture and making a candidate must not query Git again.
        with patch.object(
            working_tree, "_git_bytes", wraps=working_tree._git_bytes
        ) as commands:
            captured = working_tree.capture_source_inputs(self.root)
            calls = commands.call_count
            self.assertEqual(captured.deleted_paths, ("removed é.txt",))
            self.assertEqual(captured.sources["present.txt"], b"saved\r\n")
            self.assertEqual(captured.sources["new é.txt"], b"new\0\xff")
            manifest = candidate_manifest(captured)
            self.assertEqual(commands.call_count, calls)
            self.assertEqual(
                [call.args[1] for call in commands.call_args_list],
                [("rev-parse", "--show-toplevel")]
                + [
                    ("rev-parse", "HEAD"),
                    ("ls-files", "--stage", "-z"),
                    ("ls-files", "--others", "--exclude-standard", "-z"),
                    ("status", "--porcelain", "--untracked-files=normal"),
                ] * 2,
            )
        self.assertEqual(
            {row["path"] for row in manifest["files"]},
            {".gitignore", "present.txt", "new é.txt"},
        )
        self.assertEqual(captured.observation["file_count"], 4)
        self.assertEqual(captured.observation, working_tree.observe_source(self.root))
        with self.assertRaises(FrozenInstanceError):
            captured.deleted_paths = ()
        with self.assertRaises(TypeError):
            captured.deleted_paths[0] = "different"
        (self.root / "removed é.txt").write_bytes(b"restored later")
        (self.root / "new é.txt").unlink()
        self.assertEqual(captured.deleted_paths, ("removed é.txt",))
        self.assertEqual(captured.sources["new é.txt"], b"new\0\xff")
        self.assertEqual((self.root / ".git/index").read_bytes(), before_index)

    def test_observation_and_v1_candidate_identity_are_unchanged(self):
        (self.root / "removed é.txt").unlink()
        captured = working_tree.capture_source_inputs(self.root)
        entries = []
        for name in (".gitignore", "present.txt", "removed é.txt"):
            path = self.root / name
            if not path.exists():
                entries.append({"path": name, "index_mode": "100644", "state": "missing"})
            else:
                raw = path.read_bytes()
                entries.append({
                    "path": name, "index_mode": "100644", "state": "present",
                    "mode": stat.S_IMODE(path.stat().st_mode), "size": len(raw),
                    "sha256": sha256(raw).hexdigest(),
                })
        expected = {
            "root_uri": self.root.resolve().as_uri(),
            "revision": self.git("rev-parse", "HEAD").decode().strip(),
            "dirty": True,
            "index_sha256": sha256(self.git("ls-files", "--stage", "-z")).hexdigest(),
            "file_count": 3,
            "source_sha256": sha256(json.dumps(
                entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")).hexdigest(),
        }
        self.assertEqual(captured.observation, expected)
        legacy = working_tree.SourceInputs(
            captured.observation_json, captured.files, captured.modes
        )
        self.assertEqual(legacy.deleted_paths, ())
        self.assertEqual(candidate_manifest(captured), candidate_manifest(legacy))
        self.assertEqual(
            working_tree.SourceInputs(captured.observation_json, captured.files).deleted_paths,
            (),
        )

    def test_staged_deletion_is_already_absent_from_index_snapshot(self):
        self.git("rm", "--quiet", "removed é.txt")
        captured = working_tree.capture_source_inputs(self.root)
        self.assertEqual(captured.deleted_paths, ())
        self.assertNotIn("removed é.txt", captured.sources)
        self.assertEqual(captured.observation["file_count"], len(captured.files))

    def test_missing_names_are_sorted(self):
        (self.root / "present.txt").unlink()
        (self.root / "removed é.txt").unlink()
        captured = working_tree.capture_source_inputs(self.root)
        self.assertEqual(captured.deleted_paths, ("present.txt", "removed é.txt"))

    def test_same_byte_touch_does_not_refresh_git_index(self):
        before = (self.root / ".git/index").read_bytes()
        target = self.root / "present.txt"
        metadata = target.stat()
        os.utime(target, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 10_000_000_000))
        captured = working_tree.capture_source_inputs(self.root)
        self.assertFalse(captured.observation["dirty"])
        self.assertEqual((self.root / ".git/index").read_bytes(), before)
        self.assertEqual(working_tree.observe_source(self.root), captured.observation)
        self.assertEqual((self.root / ".git/index").read_bytes(), before)

    def test_observation_disables_configured_content_filters_and_fsmonitor(self):
        (self.root / ".gitattributes").write_text("*.txt filter=capture-probe\n")
        self.git("add", ".gitattributes")
        self.git("commit", "--quiet", "-m", "filter attribute fixture")
        self.git("config", "filter.capture-probe.clean", "echo invoked > filter-ran.txt; cat")
        self.git("config", "filter.capture-probe.required", "true")
        self.git("config", "core.fsmonitor", "echo invoked > monitor-ran.txt")
        (self.root / "present.txt").write_bytes(b"saved raw bytes\n")
        before = (self.root / ".git/index").read_bytes()
        captured = working_tree.capture_source_inputs(self.root)
        self.assertEqual(captured.sources["present.txt"], b"saved raw bytes\n")
        self.assertTrue(captured.observation["dirty"])
        self.assertFalse((self.root / "filter-ran.txt").exists())
        self.assertFalse((self.root / "monitor-ran.txt").exists())
        self.assertEqual((self.root / ".git/index").read_bytes(), before)

    def test_safe_git_configuration_failure_has_no_unsafe_fallback(self):
        with patch.object(
            working_tree, "safe_git_prefix",
            side_effect=working_tree.GitObservationError("configuration cannot be inventoried"),
        ), patch.object(working_tree.subprocess, "run") as command:
            with self.assertRaisesRegex(working_tree.WorkingTreeError, "configuration cannot be inventoried"):
                working_tree.capture_source_inputs(self.root)
        command.assert_not_called()

    def test_deletion_or_restoration_between_passes_is_rejected(self):
        for initially_missing in (False, True):
            with self.subTest(initially_missing=initially_missing):
                target = self.root / "removed é.txt"
                if initially_missing:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(b"removed\n")
                original = working_tree._git_bytes
                passes = 0

                def changing_git(root, arguments):
                    nonlocal passes
                    if arguments == ("rev-parse", "HEAD"):
                        passes += 1
                        if passes == 2:
                            if initially_missing:
                                target.write_bytes(b"removed\n")
                            else:
                                target.unlink()
                    return original(root, arguments)

                with patch.object(working_tree, "_git_bytes", side_effect=changing_git):
                    with self.assertRaisesRegex(working_tree.WorkingTreeError, "changed"):
                        working_tree.capture_source_inputs(self.root)

    def test_untracked_disappearance_after_inventory_is_rejected(self):
        target = self.root / "new.txt"
        target.write_bytes(b"new")
        original = working_tree._git_bytes

        def changing_git(root, arguments):
            result = original(root, arguments)
            if arguments == ("ls-files", "--others", "--exclude-standard", "-z"):
                target.unlink()
            return result

        with patch.object(working_tree, "_git_bytes", side_effect=changing_git):
            with self.assertRaisesRegex(working_tree.WorkingTreeError, "changed"):
                working_tree.capture_source_inputs(self.root)

    def test_deletion_during_open_is_a_source_observation_error(self):
        target = self.root / "present.txt"
        original = os.open

        def disappearing_open(path, *args, **kwargs):
            if Path(path) == target:
                target.unlink()
            return original(path, *args, **kwargs)

        with patch.object(working_tree.os, "open", side_effect=disappearing_open):
            with self.assertRaisesRegex(working_tree.WorkingTreeError, "changed"):
                working_tree.capture_source_inputs(self.root)

    def test_deletion_after_read_is_a_source_observation_error(self):
        target = self.root / "present.txt"
        original = Path.lstat
        observations = 0

        def disappearing_lstat(path, *args, **kwargs):
            nonlocal observations
            if path == target:
                observations += 1
                if observations == 2:
                    target.unlink()
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", disappearing_lstat):
            with self.assertRaisesRegex(working_tree.WorkingTreeError, "changed"):
                working_tree.capture_source_inputs(self.root)
