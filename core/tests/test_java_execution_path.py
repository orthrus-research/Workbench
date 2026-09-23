"""Read-only lexical Java paths keep their selected file identity."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_core import runtime_java


class JavaExecutionPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workbench-java-path-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).absolute()

    def windows(self):
        # Replacing this module's os reference avoids changing pathlib's host
        # path class while exercising the Windows decision on other hosts.
        return patch.object(runtime_java, "os", SimpleNamespace(name="nt", path=os.path))

    def test_ascii_existing_file_and_directory_stay_absolute_and_unchanged(self):
        executable = self.root / "java.exe"
        executable.write_bytes(b"selected executable")
        with patch.object(runtime_java, "_windows_short_path") as alias:
            self.assertEqual(runtime_java.java_execution_path(executable), executable)
            self.assertEqual(runtime_java.java_execution_path(self.root), self.root)
        alias.assert_not_called()
        self.assertEqual(executable.read_bytes(), b"selected executable")
        self.assertEqual(list(self.root.iterdir()), [executable])

    def test_linux_unicode_path_preserves_lexical_absolute_path(self):
        selected = self.root / "JDK é 資料"
        selected.mkdir()
        with patch.object(runtime_java, "os", SimpleNamespace(name="posix", path=os.path)), patch.object(
            runtime_java, "_windows_short_path"
        ) as alias:
            self.assertEqual(runtime_java.java_execution_path(selected), selected)
        alias.assert_not_called()

    def test_missing_path_is_refused_without_creation(self):
        selected = self.root / "does not exist"
        with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "existing ordinary"):
            runtime_java.java_execution_path(selected)
        self.assertFalse(selected.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unicode_windows_missing_alias_gives_actionable_read_only_error(self):
        selected = self.root / "JDK é 資料"
        selected.mkdir()
        sentinel = selected / "sentinel"
        sentinel.write_bytes(b"unchanged")
        with self.windows(), patch.object(runtime_java, "_windows_short_path", return_value=None):
            with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "shorter ASCII JDK or --state-root"):
                runtime_java.java_execution_path(selected)
        self.assertEqual(list(selected.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"unchanged")

    def test_windows_alias_must_identify_the_selected_file(self):
        selected = self.root / "java é.exe"
        unrelated = self.root / "different.exe"
        selected.write_bytes(b"same bytes do not mean same file")
        unrelated.write_bytes(selected.read_bytes())
        with self.windows(), patch.object(runtime_java, "_windows_short_path", return_value=unrelated):
            with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "same unchanged selected input"):
                runtime_java.java_execution_path(selected)
        self.assertEqual(selected.read_bytes(), unrelated.read_bytes())

    def test_non_ascii_alias_does_not_bypass_windows_requirement(self):
        selected = self.root / "JDK é"
        selected.mkdir()
        with self.windows(), patch.object(runtime_java, "_windows_short_path", return_value=selected):
            with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "ASCII 8.3 alias is unavailable"):
                runtime_java.java_execution_path(selected)

    def test_replacement_during_identity_check_is_refused(self):
        selected = self.root / "java.exe"
        selected.write_bytes(b"original executable")

        def replace_before_samefile(first, second):
            selected.unlink()
            selected.write_bytes(b"different executable")
            return True

        with patch.object(runtime_java.os.path, "samefile", side_effect=replace_before_samefile):
            with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "same unchanged selected input"):
                runtime_java.java_execution_path(selected)
        self.assertEqual(selected.read_bytes(), b"different executable")

    def test_symbolic_input_is_not_resolved_into_admissible_custody(self):
        target = self.root / "actual.exe"
        target.write_bytes(b"selected")
        link = self.root / "linked.exe"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("file symlink creation unavailable")
        with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "existing ordinary"):
            runtime_java.java_execution_path(link)

    def test_long_ascii_windows_path_requests_a_short_alias(self):
        # Keep each component portable; only the complete lexical path is long.
        selected = self.root / ("a" * 80) / ("b" * 80) / ("c" * 80)
        try:
            selected.mkdir(parents=True)
        except OSError:
            self.skipTest("host cannot create the long fixture directory")
        self.assertGreaterEqual(len(str(selected)), 240)
        with self.windows(), patch.object(runtime_java, "_windows_short_path", return_value=None) as alias:
            with self.assertRaisesRegex(runtime_java.JavaRuntimeError, "shorter ASCII"):
                runtime_java.java_execution_path(selected)
        alias.assert_called_once_with(selected)

    @unittest.skipUnless(os.name == "nt", "native DOS aliases require Windows")
    def test_native_unicode_file_and_directory_aliases_keep_exact_identity(self):
        directory = self.root / "selected JDK é 資料"
        directory.mkdir()
        executable = directory / "java.exe"
        executable.write_bytes(b"fixture selected executable")
        if runtime_java._windows_short_path(directory) is None:
            self.skipTest("selected volume does not provide ASCII 8.3 aliases")
        for selected in (directory, executable):
            with self.subTest(selected=selected.name):
                alias = runtime_java.java_execution_path(selected)
                self.assertTrue(str(alias).isascii())
                self.assertTrue(alias.samefile(selected))
                self.assertNotEqual(alias, selected)
        self.assertEqual(list(directory.iterdir()), [executable])
        self.assertEqual(executable.read_bytes(), b"fixture selected executable")
