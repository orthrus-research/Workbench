"""Self-tests for the canonical Python suite catalog."""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

VALIDATION_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = VALIDATION_ROOT.parent
sys.path.insert(0, str(VALIDATION_ROOT))

from suite_catalog import (  # noqa: E402
    PYTHON_TEST_SUITES,
    SUITES_BY_NAME,
    VALIDATION_AUTHORITY_TEST_FILES,
    VALIDATION_FAST_TEST_FILES,
    VALIDATION_NATIVE_FIXTURE_TEST_FILES,
    PythonTestSuite,
    suites_for_tier,
)

SKIP_DIRECTORIES = {
    ".git",
    ".gradle",
    ".intellijPlatform",
    ".pixi",
    ".workbench",
    ".venv",
    "__pycache__",
    "build",
    "node_modules",
    "out",
}


def _python_test_files() -> set[Path]:
    discovered: set[Path] = set()
    for relative_root in ("core", "api", "modules", "profiles", "tests", "validation"):
        root = REPOSITORY_ROOT / relative_root
        for directory, names, files in os.walk(root):
            names[:] = sorted(name for name in names if name not in SKIP_DIRECTORIES)
            base = Path(directory)
            if "tests" not in base.relative_to(REPOSITORY_ROOT).parts:
                continue
            for filename in files:
                if filename.startswith("test_") and filename.endswith(".py"):
                    discovered.add((base / filename).relative_to(REPOSITORY_ROOT))
    return discovered


class TestSuiteCatalogTests(unittest.TestCase):
    def test_every_python_test_file_belongs_to_exactly_one_suite(self) -> None:
        assignments: dict[Path, list[str]] = {}
        for suite in PYTHON_TEST_SUITES:
            for path in suite.test_files():
                relative = path.relative_to(REPOSITORY_ROOT)
                assignments.setdefault(relative, []).append(suite.name)
        discovered = _python_test_files()
        self.assertEqual(discovered, set(assignments))
        self.assertEqual(
            {},
            {path: names for path, names in assignments.items() if len(names) != 1},
        )

    def test_suite_identity_metadata_and_paths_are_valid(self) -> None:
        names = [suite.name for suite in PYTHON_TEST_SUITES]
        self.assertEqual(len(names), len(set(names)))
        for suite in PYTHON_TEST_SUITES:
            with self.subTest(suite=suite.name):
                self.assertRegex(suite.name, re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"))
                self.assertIn(suite.tier, {"quick", "intensive"})
                self.assertTrue(suite.authority.strip())
                self.assertTrue(suite.purpose.strip())
                self.assertTrue(suite.path.is_dir())
                self.assertTrue(any(suite.path.glob("test_*.py")))
                self.assertIs(type(suite.exclusive), bool)
                self.assertIs(type(suite.resource_locks), tuple)
                self.assertEqual(len(suite.resource_locks), len(set(suite.resource_locks)))
                self.assertIs(type(suite.timeout_seconds), int)
                self.assertGreater(suite.timeout_seconds, 0)
                self.assertTrue(suite.test_files())
                for resource_lock in suite.resource_locks:
                    self.assertRegex(
                        resource_lock,
                        re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
                    )
                for source in suite.python_paths:
                    self.assertTrue((REPOSITORY_ROOT / source).is_dir(), source)

    def test_resource_scheduling_defaults_and_intensive_overrides(self) -> None:
        quick_suites = tuple(
            suite for suite in PYTHON_TEST_SUITES if suite.tier == "quick"
        )
        intensive_suites = tuple(
            suite for suite in PYTHON_TEST_SUITES if suite.tier == "intensive"
        )

        self.assertTrue(quick_suites)
        self.assertTrue(intensive_suites)
        self.assertFalse(any(suite.timeout_seconds != 900 for suite in quick_suites))
        self.assertFalse(any(suite.exclusive for suite in quick_suites))
        self.assertEqual(
            {
                "cleanroom-profile": ("cleanroom-physical-fixture",),
                "workbench-shell": ("cleanroom-physical-fixture",),
            },
            {
                suite.name: suite.resource_locks
                for suite in PYTHON_TEST_SUITES
                if suite.resource_locks
            },
        )
        self.assertEqual(
            {
                "validation-authority": (False, 9000),
                "validation-native-fixtures": (False, 3600),
                "workbench-shell": (False, 3600),
                "blueprints": (False, 3600),
                "crucible": (True, 9000),
            },
            {
                suite.name: (suite.exclusive, suite.timeout_seconds)
                for suite in intensive_suites
            },
        )

    def test_quick_suites_precede_intensive_suites(self) -> None:
        tiers = [suite.tier for suite in PYTHON_TEST_SUITES]
        first_intensive = tiers.index("intensive")
        self.assertNotIn("quick", tiers[first_intensive:])
        self.assertEqual(
            ["validation-authority", "validation-native-fixtures", "workbench-shell", "blueprints", "crucible"],
            [suite.name for suite in PYTHON_TEST_SUITES if suite.tier == "intensive"],
        )

    def test_validation_authority_files_form_one_exact_partition(self) -> None:
        quick_files = {
            path.name for path in SUITES_BY_NAME["validation"].test_files()
        }
        authority_files = tuple(
            path.name
            for path in SUITES_BY_NAME["validation-authority"].test_files()
        )
        native_files = tuple(path.name for path in SUITES_BY_NAME["validation-native-fixtures"].test_files())
        self.assertEqual(set(VALIDATION_FAST_TEST_FILES), quick_files)
        self.assertEqual(VALIDATION_AUTHORITY_TEST_FILES, authority_files)
        self.assertEqual(VALIDATION_NATIVE_FIXTURE_TEST_FILES, native_files)
        self.assertTrue(set(authority_files).isdisjoint(quick_files))
        self.assertTrue(set(native_files).isdisjoint(quick_files | set(authority_files)))
        self.assertEqual(
            {path.name for path in (VALIDATION_ROOT / "tests").glob("test_*.py")},
            quick_files | set(authority_files) | set(native_files),
        )

    def test_public_source_tier_declares_exact_native_fixture_exclusion(self) -> None:
        canonical = {suite.name for suite in suites_for_tier("canonical")}
        source = {suite.name for suite in suites_for_tier("source-ci")}
        self.assertEqual({"validation-native-fixtures"}, canonical - source)
        self.assertEqual(set(), source - canonical)
        self.assertNotIn("validation-native-fixtures", {suite.name for suite in suites_for_tier("quick")})

    def test_explicit_test_file_membership_fails_closed(self) -> None:
        cases = (
            {"include_test_files": ("test_validate.py", "test_validate.py")},
            {"include_test_files": ("../test_validate.py",)},
            {"include_test_files": ("validate.py",)},
            {"include_test_files": ("test_[x].py",)},
            {"include_test_files": ("test_missing.py",)},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                suite = PythonTestSuite(
                    "invalid-membership",
                    "Test authority",
                    "validation/tests",
                    "quick",
                    "Reject invalid membership.",
                    **overrides,
                )
                with self.assertRaises(ValueError):
                    suite.test_files()


if __name__ == "__main__":
    unittest.main()
