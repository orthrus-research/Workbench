"""Exact capture workspaces preserve selected inputs and replace source roots."""

from hashlib import sha256
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import capture_workspace as capture
from workbench_core import check_storage


class CaptureWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-capture-workspace-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / "selected runtime é"
        self.java = self.root / "selected java é"
        self.attempt = self.root / "owned attempt"
        for path in (self.runtime, self.java, self.attempt):
            path.mkdir()
        for relative, raw in {
            "mods/runtime.jar": b"runtime", "config/removed.txt": b"old config",
            "scripts/removed.txt": b"old script", "resources/removed.txt": b"old asset",
            "logs/old.log": b"ephemeral", "server.properties": b"old setting",
        }.items():
            self.write(self.runtime, relative, raw)
        self.write(self.java, "bin/java", b"selected executable")
        self.write(self.java, "lib/tool", b"tool bytes")
        (self.java / "bin/java").chmod(0o755)
        self.source_roots = ("config", "scripts", "resources")
        self.source_files = {
            "config/new é.txt": b"saved\r\n", "scripts/new.txt": b"new script",
            "pack.toml": b"outside the runtime roots",
        }
        self.source_rows = [
            {"path": name, "size": len(raw), "sha256": sha256(raw).hexdigest(), "mode": 0o100644}
            for name, raw in sorted(self.source_files.items())
        ]
        self.runtime_rows = capture.inventory(self.runtime, exclude=(*self.source_roots, "logs"))
        self.java_rows = capture.inventory(self.java, contained_file_links=True)

    @staticmethod
    def write(root, relative, raw):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def materialize(self, **overrides):
        arguments = {
            "runtime_root": self.runtime, "runtime_files": self.runtime_rows,
            "java_home": self.java, "java_files": self.java_rows,
            "source_files": self.source_files, "source_rows": self.source_rows,
            "source_roots": self.source_roots, "runtime_exclude": ("logs",),
        }
        return capture.materialize(self.attempt, **{**arguments, **overrides})

    def test_complete_source_root_replacement_removes_all_template_stale_data(self):
        before_runtime = capture.inventory(self.runtime)
        before_java = capture.inventory(self.java)
        result = self.materialize()
        runtime = Path(result["runtime"])
        self.assertTrue(runtime.is_absolute())
        self.assertEqual((runtime / "config/new é.txt").read_bytes(), b"saved\r\n")
        self.assertEqual((runtime / "scripts/new.txt").read_bytes(), b"new script")
        self.assertEqual((runtime / "mods/runtime.jar").read_bytes(), b"runtime")
        self.assertFalse((runtime / "config/removed.txt").exists())
        self.assertFalse((runtime / "scripts/removed.txt").exists())
        self.assertFalse((runtime / "resources").exists())
        self.assertFalse((runtime / "logs").exists())
        self.assertFalse((runtime / "pack.toml").exists())
        self.assertEqual(capture.inventory(runtime), result["runtime_files"])
        self.assertEqual(capture.inventory(Path(result["java_home"])), result["java_files"])
        self.assertEqual(capture.inventory(self.runtime), before_runtime)
        self.assertEqual(capture.inventory(self.java), before_java)

    def test_source_empty_inventory_removes_entire_selected_roots(self):
        result = self.materialize(source_files={}, source_rows=[])
        runtime = Path(result["runtime"])
        for relative in self.source_roots:
            self.assertFalse((runtime / relative).exists())

    def test_materialized_mode_separates_git_intent_from_windows_suffix_modes(self):
        for name in ("run.sh", "Recipe.groovy", "run", "run.py"):
            with self.subTest(name=name):
                self.assertEqual(capture.materialized_mode(name, 0o100755, windows=True), 0o644)
                self.assertEqual(capture.materialized_mode(name, 0o100755, windows=False), 0o755)
        for name in ("run.exe", "run.COM", "run.cmd", "run.bat"):
            with self.subTest(name=name):
                self.assertEqual(capture.materialized_mode(name, 0o100644, windows=True), 0o755)
                self.assertEqual(capture.materialized_mode(name, 0o100644, windows=False), 0o644)

    def test_executable_saved_shell_and_groovy_rows_keep_declared_identity(self):
        source_files = {"scripts/run.sh": b"echo saved", "scripts/Recipe.groovy": b"saved recipe"}
        source_rows = [
            {"path": name, "size": len(raw), "sha256": sha256(raw).hexdigest(), "mode": 0o100755}
            for name, raw in sorted(source_files.items())
        ]
        before = deepcopy(source_rows)
        result = self.materialize(source_files=source_files, source_rows=source_rows)
        self.assertEqual(source_rows, before)
        expected = 0o644 if os.name == "nt" else 0o755
        for row in result["runtime_files"]:
            if row["path"] in source_files:
                self.assertEqual(row["mode"], expected)
        self.assertEqual(capture.inventory(Path(result["runtime"])), result["runtime_files"])

    def test_runtime_drift_before_copy_rejected_without_creating_projection(self):
        self.write(self.runtime, "mods/added.jar", b"new")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "runtime changed"):
            self.materialize()
        self.assertFalse((self.attempt / "runtime").exists())

    def test_original_drift_after_copy_rejected_and_partial_attempt_retained(self):
        original = check_storage.copy_manifest

        def changing_copy(source, destination, rows, **kwargs):
            original(source, destination, rows, **kwargs)
            if source == self.runtime:
                self.write(self.runtime, "mods/added.jar", b"new")

        with patch.object(check_storage, "copy_manifest", side_effect=changing_copy):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "runtime changed"):
                self.materialize()
        self.assertTrue((self.attempt / "runtime/mods/runtime.jar").exists())
        self.assertTrue((self.runtime / "mods/added.jar").exists())

    def test_retained_projection_drift_rejected(self):
        original = check_storage.copy_manifest

        def changing_copy(source, destination, rows, **kwargs):
            original(source, destination, rows, **kwargs)
            if source == self.runtime:
                self.write(destination, "unexpected.txt", b"unexpected")

        with patch.object(check_storage, "copy_manifest", side_effect=changing_copy):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "retained runtime"):
                self.materialize()
        self.assertTrue((self.attempt / "runtime/unexpected.txt").exists())

    def test_toolchain_drift_rejected(self):
        self.write(self.java, "lib/tool", b"changed")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "toolchain changed"):
            self.materialize()

    def test_source_bytes_must_match_every_selected_row(self):
        bad = dict(self.source_files)
        bad["scripts/new.txt"] = b"other bytes"
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "source bytes differ"):
            self.materialize(source_files=bad)
        bad = dict(self.source_files)
        bad["scripts/undeclared.txt"] = b"undeclared"
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "declared rows differ"):
            self.materialize(source_files=bad)
        self.assertFalse((self.attempt / "runtime").exists())

    def test_source_roots_must_be_excluded_from_reviewed_runtime(self):
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "excluded source"):
            self.materialize(runtime_files=capture.inventory(self.runtime))

    def test_attempt_may_not_overlap_selected_runtime(self):
        attempt = self.runtime / "attempt"
        attempt.mkdir()
        self.attempt = attempt
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "overlaps"):
            self.materialize()

    def test_cancellation_keeps_partial_copy_and_original_inputs(self):
        state = {"cancelled": False}
        original = check_storage.copy_manifest

        def cancelled_copy(source, destination, rows, **kwargs):
            original(source, destination, rows, **kwargs)
            state["cancelled"] = True

        with patch.object(check_storage, "copy_manifest", side_effect=cancelled_copy):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "cancelled"):
                self.materialize(cancelled=lambda: state["cancelled"])
        self.assertTrue((self.attempt / "runtime/mods/runtime.jar").exists())
        self.assertEqual((self.runtime / "mods/runtime.jar").read_bytes(), b"runtime")

    def test_cancelled_inventory_refuses_before_read(self):
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "cancelled"):
            capture.inventory(self.runtime, cancelled=lambda: True)

    def test_storage_initialization_rejects_junction_ancestors_and_children_before_writes(self):
        ancestor = self.root / "junction ancestor"
        ancestor.mkdir()
        state = ancestor / "new-state"
        with patch.object(Path, "is_junction", lambda path: path == ancestor, create=True):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "traverses a link"):
                check_storage.initialize(state)
        self.assertFalse(state.exists())
        self.assertEqual(list(ancestor.iterdir()), [])

        state = self.root / "existing private state"
        state.mkdir()
        check_storage.secure_private_path(state, directory=True)
        child = state / ".workbench"
        child.mkdir()
        with patch.object(Path, "is_junction", lambda path: path == child, create=True):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "traverses a link"):
                check_storage.initialize(state)
        self.assertEqual(list(child.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "real junctions require Windows")
    def test_native_junction_state_refusal_leaves_private_target_untouched(self):
        for kind in ("ancestor", "managed-child"):
            with self.subTest(kind=kind):
                target = self.root / (kind + " private target")
                target.mkdir()
                check_storage.secure_private_path(target, directory=True)
                (target / "sentinel.txt").write_bytes(b"untouched target")
                before = capture.inventory(target)
                if kind == "ancestor":
                    alias = self.root / "native junction ancestor"
                    state = alias / "new-state"
                else:
                    state = self.root / "native private state"
                    state.mkdir()
                    check_storage.secure_private_path(state, directory=True)
                    alias = state / ".workbench"
                subprocess.run(
                    [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", str(alias), str(target)],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                try:
                    self.assertTrue(alias.is_junction())
                    self.assertFalse(alias.is_symlink())
                    with self.assertRaisesRegex(capture.CaptureWorkspaceError, "traverses a link"):
                        check_storage.initialize(state)
                    self.assertEqual(capture.inventory(target), before)
                    self.assertEqual([path.name for path in target.iterdir()], ["sentinel.txt"])
                    self.assertTrue(check_storage.private_path(target, directory=True))
                finally:
                    alias.rmdir()

    def test_contained_toolchain_file_links_materialize_as_ordinary_bytes(self):
        link = self.java / "lib/tool-alias"
        try:
            link.symlink_to("tool")
        except OSError:
            self.skipTest("file symlink creation unavailable")
        result = self.materialize(java_files=capture.inventory(self.java, contained_file_links=True))
        retained = Path(result["java_home"]) / "lib/tool-alias"
        self.assertFalse(retained.is_symlink())
        self.assertEqual(retained.read_bytes(), b"tool bytes")

    def directory_link(self, path, target):
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation unavailable")

    def test_contained_directory_aliases_require_explicit_policy_and_materialize_all_paths(self):
        self.write(self.java, "man/ja_JP.UTF-8/man1/java.1", b"selected JDK manual")
        alias = self.java / "man/ja"
        self.directory_link(alias, "ja_JP.UTF-8")
        for arguments in ({}, {"contained_file_links": True}):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(capture.CaptureWorkspaceError, "symbolic link"):
                    capture.inventory(self.java, **arguments)
        rows = capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)
        names = {row["path"] for row in rows}
        self.assertIn("man/ja/man1/java.1", names)
        self.assertIn("man/ja_JP.UTF-8/man1/java.1", names)
        result = self.materialize(java_files=rows)
        retained = Path(result["java_home"])
        self.assertFalse((retained / "man/ja").is_symlink())
        self.assertFalse((retained / "man/ja").is_junction())
        self.assertEqual((retained / "man/ja/man1/java.1").read_bytes(), b"selected JDK manual")
        self.assertEqual(capture.inventory(retained), rows)
        self.assertTrue(alias.is_symlink())

    def test_file_alias_inside_directory_alias_is_materialized(self):
        self.write(self.java, "lib/shared/payload", b"selected payload")
        try:
            (self.java / "lib/shared/payload-alias").symlink_to("payload")
        except OSError:
            self.skipTest("file symlink creation unavailable")
        self.directory_link(self.java / "lib/directory-alias", "shared")
        rows = capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)
        result = self.materialize(java_files=rows)
        retained = Path(result["java_home"]) / "lib/directory-alias/payload-alias"
        self.assertFalse(retained.is_symlink())
        self.assertEqual(retained.read_bytes(), b"selected payload")

    def test_toolchain_directory_alias_escape_is_refused(self):
        outside = self.root / "outside directory"
        outside.mkdir()
        (outside / "secret").write_bytes(b"not a selected JDK input")
        self.directory_link(self.java / "escaped-directory", outside)
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "directory link escapes"):
            capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)

    def test_toolchain_directory_alias_ancestor_cycle_is_refused(self):
        self.directory_link(self.java / "lib/ancestor", self.java)
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "ancestor cycle"):
            capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)

    def test_toolchain_directory_alias_sibling_cycle_is_refused(self):
        (self.java / "first").mkdir()
        (self.java / "second").mkdir()
        self.directory_link(self.java / "first/to-second", self.java / "second")
        self.directory_link(self.java / "second/to-first", self.java / "first")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "ancestor cycle"):
            capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)

    def test_toolchain_broken_alias_is_not_silently_omitted(self):
        try:
            (self.java / "lib/missing.debuginfo").symlink_to("not-in-this-selected-jdk")
        except OSError:
            self.skipTest("file symlink creation unavailable")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "missing, cyclic or unavailable"):
            capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)

    def test_toolchain_alias_retarget_after_copy_is_rejected(self):
        self.write(self.java, "lib/first/payload", b"first selected bytes")
        self.write(self.java, "lib/second/payload", b"second selected bytes")
        alias = self.java / "lib/alias"
        self.directory_link(alias, "first")
        rows = capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)
        original = check_storage.copy_manifest

        def retargeting_copy(source, destination, selected_rows, **kwargs):
            original(source, destination, selected_rows, **kwargs)
            if source == self.java:
                alias.unlink()
                alias.symlink_to("second", target_is_directory=True)

        with patch.object(check_storage, "copy_manifest", side_effect=retargeting_copy):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "toolchain changed"):
                self.materialize(java_files=rows)
        self.assertEqual((self.attempt / "java/lib/alias/payload").read_bytes(), b"first selected bytes")

    def test_escaping_toolchain_link_is_refused(self):
        target = self.root / "outside"
        target.write_bytes(b"outside")
        try:
            (self.java / "lib/escape").symlink_to(target)
        except OSError:
            self.skipTest("file symlink creation unavailable")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "escapes"):
            capture.inventory(self.java, contained_file_links=True)

    def test_windows_reserved_and_nonportable_filenames_are_refused(self):
        # Validate declared rows so this test also runs on Windows hosts that
        # cannot create these filenames in the first place.
        for name in ("CON.txt", "nested/COM1", "nested/LPT².txt", "bad?.txt", "name.", "name ", "a/../b"):
            with self.subTest(name=name):
                row = {"path": name, "mode": 0o644, "size": 0, "sha256": sha256(b"").hexdigest()}
                with self.assertRaises(capture.CaptureWorkspaceError):
                    self.materialize(runtime_files=[row])

    def test_component_case_normalization_and_file_directory_collisions_refused(self):
        for names in (
            ("Dir/a", "dir/b"), ("é/a", "e\u0301/b"),
            ("path", "path/child"), ("path/child", "path"),
        ):
            with self.subTest(names=names):
                rows = [
                    {"path": name, "mode": 0o644, "size": 0, "sha256": sha256(b"").hexdigest()}
                    for name in names
                ]
                with self.assertRaisesRegex(capture.CaptureWorkspaceError, "collide"):
                    self.materialize(runtime_files=rows)

    def test_inventory_checks_empty_directory_portability(self):
        # The manifest itself has no empty directory rows; directory paths
        # must still be admitted before they disappear from that projection.
        if os.name == "nt":
            self.skipTest("Windows cannot create the intentionally invalid path")
        (self.runtime / "NUL").mkdir()
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "portable"):
            capture.inventory(self.runtime)

    def test_source_root_case_alias_cannot_be_silently_omitted(self):
        raw = b"case alias"
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "root components collide"):
            self.materialize(
                source_files={"CONFIG/new.txt": raw},
                source_rows=[{"path": "CONFIG/new.txt", "mode": 0o100644, "size": len(raw), "sha256": sha256(raw).hexdigest()}],
            )

    def test_runtime_source_root_alias_cannot_retain_stale_template_data(self):
        other = self.root / "case alias runtime"
        other.mkdir()
        self.write(other, "CONFIG/stale.txt", b"stale")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "root components collide"):
            capture.inventory(other, exclude=("config",))

    def test_custom_source_root_prefix_alias_is_refused(self):
        other = self.root / "custom root alias runtime"
        other.mkdir()
        self.write(other, "Custom/other.txt", b"stale")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "root components collide"):
            capture.inventory(other, exclude=("custom/property-scripts",))

    def test_git_directory_and_worktree_pointer_are_only_explicit_exclusions(self):
        for is_directory in (False, True):
            with self.subTest(is_directory=is_directory):
                other = self.root / ("git-directory" if is_directory else "git-file")
                other.mkdir()
                if is_directory:
                    self.write(other, ".git/config", b"private git settings")
                else:
                    self.write(other, ".git", b"gitdir: /private/path")
                self.write(other, "runtime.txt", b"runtime")
                with self.assertRaises(capture.CaptureWorkspaceError):
                    capture.inventory(other)
                self.assertEqual(
                    [row["path"] for row in capture.inventory(other, exclude=(".git",))],
                    ["runtime.txt"],
                )

    def test_exact_override_preserves_mode_and_requires_fresh_digest(self):
        target = self.runtime / "server.properties"
        before = sha256(target.read_bytes()).hexdigest()
        mode = capture.inventory(self.runtime)
        expected_mode = next(row["mode"] for row in mode if row["path"] == "server.properties")
        row = capture.replace_file(self.runtime, "server.properties", b"new setting", expected_sha256=before)
        self.assertEqual(row, {"path": "server.properties", "size": 11, "sha256": sha256(b"new setting").hexdigest(), "mode": expected_mode})
        self.assertEqual(target.read_bytes(), b"new setting")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "expected bytes"):
            capture.replace_file(self.runtime, "server.properties", b"overwrite", expected_sha256=before)
        self.assertEqual(target.read_bytes(), b"new setting")

    def test_new_override_requires_absence_and_safe_relative_path(self):
        capture.replace_file(self.runtime, "new.txt", b"new")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "absent"):
            capture.replace_file(self.runtime, "new.txt", b"overwrite")
        for name in ("../escape.txt", "CON", "bad/name."):
            with self.subTest(name=name):
                with self.assertRaises(capture.CaptureWorkspaceError):
                    capture.replace_file(self.runtime, name, b"bad")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "unavailable"):
            capture.replace_file(self.runtime, "missing.txt", b"new", expected_sha256=sha256(b"old").hexdigest())

    def test_new_override_returns_host_materialized_mode(self):
        row = capture.replace_file(self.runtime, "script.cmd", b"echo saved")
        actual = next(item for item in capture.inventory(self.runtime) if item["path"] == "script.cmd")
        self.assertEqual(row, actual)

    def test_override_rejects_case_alias_and_link(self):
        self.write(self.runtime, "Camel.txt", b"existing")
        with self.assertRaisesRegex(capture.CaptureWorkspaceError, "collides"):
            capture.replace_file(self.runtime, "camel.txt", b"alias")
        try:
            (self.runtime / "linked.txt").symlink_to("Camel.txt")
        except OSError:
            return
        with self.assertRaises(capture.CaptureWorkspaceError):
            capture.replace_file(self.runtime, "linked.txt", b"bad", expected_sha256=sha256(b"existing").hexdigest())

    def test_failed_override_keeps_original_and_temporary_file(self):
        target = self.runtime / "server.properties"
        expected = sha256(target.read_bytes()).hexdigest()
        with patch.object(capture.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "temporary files retained"):
                capture.replace_file(self.runtime, "server.properties", b"new", expected_sha256=expected)
        self.assertEqual(target.read_bytes(), b"old setting")
        self.assertEqual(len(list(self.runtime.glob(".capture-write-*"))), 1)

    def test_override_rechecks_expected_bytes_before_atomic_publication(self):
        target = self.runtime / "server.properties"
        expected = sha256(target.read_bytes()).hexdigest()
        original = os.fsync

        def changing_fsync(descriptor):
            original(descriptor)
            target.write_bytes(b"changed by another writer")

        with patch.object(capture.os, "fsync", side_effect=changing_fsync):
            with self.assertRaisesRegex(capture.CaptureWorkspaceError, "expected bytes"):
                capture.replace_file(self.runtime, "server.properties", b"new", expected_sha256=expected)
        self.assertEqual(target.read_bytes(), b"changed by another writer")
        self.assertEqual(len(list(self.runtime.glob(".capture-write-*"))), 1)
