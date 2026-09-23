"""Selected and retained paths stay canonical while native IO supports depth."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import capture_workspace as capture
from workbench_core import check_storage as storage
from workbench_core.filesystem_paths import _windows_access_path, native_path, resolved_path
from workbench_core.host_filesystem import private_path


SCALA = ("libraries/org/scala-lang/plugins/scala-continuations-plugin_2.11.1/"
         "1.0.2_mc/scala-continuations-plugin_2.11.1-1.0.2_mc.jar")
GROOVY = ("groovy/postInit/chemistry/inorganic_chemistry/elements/f_block/"
          "lanthanides/separations/BastnasiteProcessing.groovy")
DEEP = "/".join(["retained-data", "a" * 70, "b" * 70, "c" * 70, "d" * 70, "payload.bin"])


class NativePathSpellingTests(unittest.TestCase):
    def test_windows_drive_and_unc_spelling_preserve_ordinary_identity(self):
        short = r"C:\Users\developer\state"
        self.assertEqual(_windows_access_path(short), short)
        drive = short + "\\" + "a" * 220
        self.assertEqual(_windows_access_path(drive), "\\\\?\\" + drive)
        unc = r"\\server\share\state" + "\\" + "b" * 230
        self.assertEqual(_windows_access_path(unc), "\\\\?\\UNC\\" + unc[2:])

    def test_windows_device_relative_and_parent_spellings_are_refused(self):
        for value in (r"\\?\C:\state", r"\\.\C:\state", r"C:state", r"state\child", r"C:\state\..\child"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _windows_access_path(value)

    @unittest.skipIf(os.name == "nt", "POSIX identity check")
    def test_posix_access_has_no_new_spelling(self):
        selected = Path("/ordinary/" + "deep/" * 80 + "payload")
        self.assertEqual(native_path(selected), selected)


class CaptureLongPathTests(unittest.TestCase):
    def setUp(self):
        # Windows uses the user's normal writable profile area, without changing
        # global long-path or ACL settings. Cleanup is scoped to this fresh root.
        parent = Path(os.environ["LOCALAPPDATA"]) / "Temp" if os.name == "nt" else None
        if parent is not None:
            parent.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="workbench-longpath-", dir=parent))
        cleanup_path = Path("\\\\?\\" + str(self.root)) if os.name == "nt" else self.root
        self.addCleanup(shutil.rmtree, cleanup_path)
        self.state = self.root / "Workbench/runtime/recipe-captures"
        self.runtime = self.root / "selected runtime é"
        self.java = self.root / "selected java é"
        for path in (self.runtime, self.java):
            native_path(path).mkdir()
        self.write(self.runtime, SCALA, b"selected library bytes")
        self.write(self.runtime, DEEP, b"selected deep runtime")
        self.write(self.runtime, "groovy/deleted.groovy", b"stale template")
        self.write(self.runtime, "config/deleted.cfg", b"stale configuration")
        self.write(self.java, "bin/java.exe", b"selected toolchain executable")
        self.write(self.java, DEEP, b"selected deep toolchain")
        self.files = {GROOVY: b"saved recipe source\r\n", "groovy/" + DEEP: b"saved deep source"}
        self.rows = [{"path": name, "size": len(raw), "sha256": sha256(raw).hexdigest(), "mode": 0o100755}
                     for name, raw in sorted(self.files.items())]
        self.runtime_rows = capture.inventory(self.runtime, exclude=("groovy", "config"))
        self.java_rows = capture.inventory(self.java, contained_file_links=True, contained_directory_links=True)
        self.attempt = storage.allocate_attempt(self.state, "recipe-capture")

    @staticmethod
    def write(root, relative, raw):
        path = root / relative
        native_path(path.parent).mkdir(parents=True, exist_ok=True)
        native_path(path).write_bytes(raw)
        return path

    def materialize(self, **overrides):
        arguments = dict(runtime_root=self.runtime, runtime_files=self.runtime_rows,
                         java_home=self.java, java_files=self.java_rows,
                         source_files=self.files, source_rows=self.rows,
                         source_roots=("groovy", "config"))
        return capture.materialize(self.attempt, **{**arguments, **overrides})

    def test_complete_long_path_lifecycle_keeps_canonical_records_and_private_evidence(self):
        originals = (capture.inventory(self.runtime), capture.inventory(self.java))
        result = self.materialize()
        runtime = Path(result["runtime"])
        self.assertEqual(runtime, self.attempt / "runtime")
        self.assertGreater(len(str(runtime / SCALA)), 260)
        self.assertGreater(len(str(runtime / GROOVY)), 260)
        self.assertGreater(len(str(runtime / DEEP)), 320)
        self.assertGreater(len(str(self.runtime / DEEP)), 320)
        self.assertEqual(storage.read_bytes(runtime / SCALA), b"selected library bytes")
        self.assertEqual(storage.read_bytes(runtime / GROOVY), self.files[GROOVY])
        self.assertFalse(native_path(runtime / "groovy/deleted.groovy").exists())
        self.assertFalse(native_path(runtime / "config").exists())
        execution = self.attempt / "execution"
        storage.copy_manifest(runtime, execution, result["runtime_files"])
        self.assertEqual(capture.inventory(execution), result["runtime_files"])
        source_path = execution / "groovy" / DEEP
        expected = sha256(self.files["groovy/" + DEEP]).hexdigest()
        override = capture.replace_file(execution, "groovy/" + DEEP, b"execution-only", expected_sha256=expected)
        self.assertEqual(storage.read_bytes(source_path), b"execution-only")
        self.assertEqual(override["sha256"], sha256(b"execution-only").hexdigest())
        with self.assertRaisesRegex(storage.CheckStorageError, "expected bytes"):
            capture.replace_file(execution, "groovy/" + DEEP, b"stale override", expected_sha256=expected)
        receipt = source_path.parent / "receipt.json"
        value = {"runtime": str(runtime), "execution": str(execution), "source": GROOVY}
        storage.write_json(receipt, value)
        self.assertEqual(storage.read_json(receipt), value)
        self.assertEqual(storage.ordinary(receipt), receipt)
        self.assertEqual(resolved_path(receipt, strict=True), receipt)
        self.assertTrue(private_path(receipt, directory=False))
        with self.assertRaisesRegex(storage.CheckStorageError, "already exists"):
            storage.write_json(receipt, {"overwritten": True})
        with self.assertRaisesRegex(storage.CheckStorageError, "exceeds its bound"):
            storage.read_bytes(receipt, byte_limit=1)
        self.assertEqual(storage.read_json(receipt), value)
        created = capture.replace_file(execution, "groovy/" + DEEP + ".new", b"new bytes")
        self.assertEqual(created["size"], 9)
        with self.assertRaisesRegex(storage.CheckStorageError, "absent target"):
            capture.replace_file(execution, "groovy/" + DEEP + ".new", b"second write")
        self.assertNotIn("\\\\?\\", json.dumps(result) + json.dumps(value))
        self.assertEqual((capture.inventory(self.runtime), capture.inventory(self.java)), originals)
        with storage.execution_lock(self.attempt):
            self.assertTrue(storage.execution_active(self.attempt))
        self.assertFalse(storage.execution_active(self.attempt))

    def test_long_selected_runtime_mutation_refused_and_partial_attempt_retained(self):
        copy = storage.copy_manifest
        def mutate_after_copy(source, destination, rows, **kwargs):
            copy(source, destination, rows, **kwargs)
            if source == self.runtime:
                native_path(source / DEEP).write_bytes(b"new selected runtime")
        with patch.object(storage, "copy_manifest", mutate_after_copy):
            with self.assertRaisesRegex(storage.CheckStorageError, "selected runtime changed"):
                self.materialize()
        self.assertEqual(storage.read_bytes(self.attempt / "runtime" / DEEP), b"selected deep runtime")
        self.assertEqual(storage.read_bytes(self.runtime / DEEP), b"new selected runtime")

    def test_long_copy_cancellation_retains_partial_attempt_and_original_inputs(self):
        copy, stop = storage.copy_manifest, False
        before = capture.inventory(self.runtime)
        def cancel_after_copy(source, destination, rows, **kwargs):
            nonlocal stop
            copy(source, destination, rows, **kwargs)
            stop = True
        with patch.object(storage, "copy_manifest", cancel_after_copy):
            with self.assertRaisesRegex(storage.CheckStorageError, "cancelled"):
                self.materialize(cancelled=lambda: stop)
        self.assertEqual(storage.read_bytes(self.attempt / "runtime" / DEEP), b"selected deep runtime")
        self.assertEqual(capture.inventory(self.runtime), before)

    def test_long_hardlink_is_not_admitted_as_independent_data(self):
        selected = self.runtime / DEEP
        alias = selected.with_name("alias.bin")
        os.link(native_path(selected), native_path(alias))
        with self.assertRaisesRegex(storage.CheckStorageError, "independent"):
            capture.inventory(self.runtime)
        with self.assertRaisesRegex(storage.CheckStorageError, "independent"):
            storage.read_bytes(selected)

    def test_long_symbolic_link_is_not_admitted(self):
        selected = self.runtime / DEEP
        alias = selected.with_name("alias.bin")
        try:
            native_path(alias).symlink_to(selected.name)
        except OSError as exc:
            self.skipTest(f"native symlink creation unavailable: {exc}")
        with self.assertRaisesRegex(storage.CheckStorageError, "symbolic link"):
            storage.read_bytes(alias)
        with self.assertRaisesRegex(storage.CheckStorageError, "symbolic link"):
            capture.inventory(self.runtime)

    @unittest.skipUnless(os.name == "nt", "native Windows junction custody")
    def test_long_paths_below_junction_remain_refused_without_writes(self):
        alias = self.root / "junction"
        completed = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(self.runtime)],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        try:
            self.assertTrue(alias.is_junction())
            with self.assertRaisesRegex(storage.CheckStorageError, "symbolic link"):
                storage.read_bytes(alias / DEEP)
            with self.assertRaisesRegex(storage.CheckStorageError, "symbolic link"):
                storage.write_json((alias / DEEP).parent / "should-not-exist.json", {})
            self.assertFalse(native_path((self.runtime / DEEP).parent / "should-not-exist.json").exists())
        finally:
            alias.rmdir()


if __name__ == "__main__":
    unittest.main()
