"""Regression tests for isolated Python-suite execution evidence."""

from __future__ import annotations

import argparse
from io import StringIO
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

VALIDATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VALIDATION_ROOT))

from run_python_suite import (  # noqa: E402
    _configure_suite,
    _discover_tests,
    _run_id_argument,
    _source_fingerprint_argument,
    _tests_in,
    _write_report,
    _write_inventory,
    TimingResult,
)
from orchestration import OrchestrationFailure, load_suite_report, load_test_inventory
from suite_measurement import PhaseClock, measure_tests
from suite_catalog import PythonTestSuite  # noqa: E402


class PythonSuiteIsolationTests(unittest.TestCase):
    def test_fixture_inventory_reports_exact_not_run_cases_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "native-not-run.json"
            command = [sys.executable, str(VALIDATION_ROOT / "run_python_suite.py"),
                       "validation-native-fixtures", "--collect-only", "--report", str(report)]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            body = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual("not-run", body["state"])
            self.assertEqual(18, len(body["test_ids"]))
            self.assertTrue(all(test.startswith("test_axiom_native_execution.") for test in body["test_ids"]))
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(0, second.returncode)
            self.assertIn("refusing to replace", second.stderr)

    def test_blueprints_sandbox_inventory_reports_not_run_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "blueprints-not-run.json"
            command = [sys.executable, str(VALIDATION_ROOT / "run_python_suite.py"),
                       "blueprints-native-fixtures", "--collect-only", "--report", str(report)]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual("not-run", body["state"])
            self.assertEqual(40, len(body["test_ids"]))
            self.assertEqual(
                {"test_conformance", "test_interface", "test_lifecycle", "test_simulation"},
                {test.split(".", 1)[0] for test in body["test_ids"]},
            )

    def _run_cases(self, cases):
        suite = unittest.TestSuite(cases)
        phases = PhaseClock()
        with measure_tests(suite, phases):
            result = unittest.TextTestRunner(stream=StringIO(), resultclass=TimingResult).run(suite)
        return result, phases

    def test_measurements_preserve_fixture_order_and_restore_hooks(self) -> None:
        events = []

        class Sample(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                events.append("class setup")

            def setUp(self):
                events.append("setup")
                self.addCleanup(events.append, "cleanup")

            def test_body(self):
                events.append("body")

            def tearDown(self):
                events.append("teardown")

            @classmethod
            def tearDownClass(cls):
                events.append("class teardown")

        case = Sample("test_body")
        before = dict(case.__dict__)
        result, phases = self._run_cases([case])
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(["class setup", "setup", "body", "teardown", "cleanup", "class teardown"], events)
        self.assertEqual(set(before), set(case.__dict__))
        self.assertTrue({"test_setup", "test_body", "test_teardown", "test_cleanup", "class_setup", "class_teardown_cleanup"} <= phases.seconds.keys())

    def test_nested_phase_clock_does_not_double_count(self) -> None:
        ticks = iter([0.0, 1.0, 4.0, 6.0])
        clock = PhaseClock(lambda: next(ticks))
        with clock.span("parent"):
            with clock.span("child"):
                pass
        self.assertEqual({"child": 3.0, "parent": 3.0}, clock.seconds)

    def test_subtest_failure_and_skip_reasons_survive_reporting(self) -> None:
        class Sample(unittest.TestCase):
            def test_failure(self):
                with self.subTest(value=1):
                    self.fail("boundary rejection")

            def test_skip(self):
                self.skipTest("requires another platform")

            @unittest.expectedFailure
            def test_expected(self):
                self.fail("known behavior")

        result, _ = self._run_cases(unittest.defaultTestLoader.loadTestsFromTestCase(Sample))
        rows = {row["test"].rsplit(".", 1)[-1]: row for row in result.timings}
        self.assertEqual("failed", rows["test_failure"]["outcome"])
        self.assertEqual("requires another platform", rows["test_skip"]["reason"])
        self.assertIn("known behavior", rows["test_expected"]["reason"])
        self.assertEqual("failed", result.subtests[0]["outcome"])
        self.assertIn("in test_failure", result.subtests[0]["reason"])

    def test_failed_class_fixture_leaves_explicit_unrun_tests(self) -> None:
        class Sample(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("fixture failed")

            def test_never(self):
                raise AssertionError("must never run")

        cases = [Sample("test_never")]
        ids = tuple(case.id() for case in cases)
        result, phases = self._run_cases(cases)
        with tempfile.TemporaryDirectory() as temporary:
            target = _write_report("validation", result, discovered=1, elapsed=0.0,
                                   report=Path(temporary) / "report.json", run_id="fixture-run",
                                   source_fingerprint="sha256:fixture", collected_ids=ids, phases=phases)
            report = load_suite_report(target, expected_suite="validation", expected_run_id="fixture-run",
                                       expected_source_fingerprint="sha256:fixture", expected_test_ids=ids,
                                       require_success=False)
            self.assertFalse(report.successful)
            self.assertEqual("not-run", report.tests[0].outcome)
            self.assertEqual(0, report.executed_tests)
            self.assertIn("fixture failed", result.fixture_events[0]["reason"])

    def test_skipped_subtest_keeps_a_valid_parent_without_hiding_later_failure(self) -> None:
        class Sample(unittest.TestCase):
            def test_skipped(self):
                with self.subTest(part="optional"):
                    self.skipTest("optional part")
                self.assertTrue(True)

            def test_failed(self):
                with self.subTest(part="broken"):
                    self.fail("broken")
                with self.subTest(part="optional"):
                    self.skipTest("optional part")

        result, _ = self._run_cases(unittest.defaultTestLoader.loadTestsFromTestCase(Sample))
        outcomes = {row["test"].rsplit(".", 1)[-1]: row["outcome"] for row in result.timings}
        self.assertEqual({"test_skipped": "passed", "test_failed": "failed"}, outcomes)
        self.assertEqual(2, sum(row["outcome"] == "skipped" for row in result.subtests))

    def test_same_count_wrong_ids_cannot_replace_admitted_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            inventory = _write_inventory("validation", ("sample.A", "sample.B"), target,
                                         run_id="inventory-run", source_fingerprint="sha256:fixture")
            with self.assertRaisesRegex(ValueError, "previously admitted"):
                _write_inventory("validation", ("sample.B", "sample.C"), target,
                                 run_id="inventory-run", source_fingerprint="sha256:fixture")
            expected = load_test_inventory(inventory, expected_suite="validation", expected_run_id="inventory-run",
                                           expected_source_fingerprint="sha256:fixture", expected_authority="Workbench validation")
            result = SimpleNamespace(testsRun=2, wasSuccessful=lambda: True, timings=[
                {"test": name, "outcome": "passed", "seconds": 0.0} for name in ("sample.B", "sample.C")
            ])
            _write_report("validation", result, discovered=2, elapsed=0.0, report=target,
                          run_id="inventory-run", source_fingerprint="sha256:fixture", collected_ids=("sample.B", "sample.C"))
            with self.assertRaisesRegex(OrchestrationFailure, "admitted inventory"):
                load_suite_report(target, expected_suite="validation", expected_run_id="inventory-run",
                                  expected_source_fingerprint="sha256:fixture", expected_test_ids=expected)

    def test_discovery_uses_only_catalog_assigned_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_membership_fast_sample.py").write_text(
                "import unittest\n"
                "class FastTests(unittest.TestCase):\n"
                "    def test_fast(self): pass\n",
                encoding="utf-8",
            )
            (tests / "test_membership_slow_sample.py").write_text(
                "raise RuntimeError('excluded test module imported')\n",
                encoding="utf-8",
            )
            suite = PythonTestSuite(
                "membership",
                "Membership authority",
                "tests",
                "quick",
                "Exercise exact test membership.",
                include_test_files=("test_membership_fast_sample.py",),
            )
            with patch("suite_catalog.ROOT", root):
                discovered = _discover_tests(suite)
            self.assertEqual(1, discovered.countTestCases())
            self.assertEqual(
                ["test_membership_fast_sample.FastTests.test_fast"],
                [test.id() for test in _tests_in(discovered)],
            )

    def test_explicit_temporary_root_applies_to_every_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "isolated"
            with patch.dict(os.environ, {}, clear=True):
                _configure_suite("validation", temporary_root=root)
                self.assertTrue(root.is_dir())
                self.assertEqual(str(root), os.environ["TMPDIR"])
                self.assertEqual(str(root), os.environ["TMP"])
                self.assertEqual(str(root), os.environ["TEMP"])

    def test_report_uses_current_v3_run_identity(self) -> None:
        result = SimpleNamespace(
            testsRun=2,
            timings=[
                {"test": "sample.Test.test_z", "outcome": "passed", "seconds": 0.2},
                {"test": "sample.Test.test_a", "outcome": "passed", "seconds": 0.1},
            ],
            wasSuccessful=lambda: True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "nested" / "timing.json"
            written = _write_report(
                "validation",
                result,
                discovered=2,
                elapsed=0.3,
                report=report,
                run_id="validation-20260825T120000Z",
                source_fingerprint="sha256:abc123",
            )

            self.assertEqual(report, written)
            document = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual("workbench-python-test-timing-v3", document["format"])
            self.assertEqual("validation-20260825T120000Z", document["run_id"])
            self.assertEqual("sha256:abc123", document["source_fingerprint"])
            self.assertEqual(
                ["sample.Test.test_a", "sample.Test.test_z"],
                [row["test"] for row in document["tests"]],
            )
            self.assertEqual([], list(report.parent.glob(".timing.json.*.tmp")))

    def test_report_requires_current_run_identity(self) -> None:
        result = SimpleNamespace(testsRun=0, timings=[], wasSuccessful=lambda: True)
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "timing.json"
            with self.assertRaisesRegex(TypeError, "run_id.*source_fingerprint"):
                _write_report(
                    "validation",
                    result,
                    discovered=0,
                    elapsed=0.0,
                    report=report,
                )
            self.assertFalse(report.exists())

    def test_both_run_identity_fields_are_required(self) -> None:
        result = SimpleNamespace(testsRun=0, timings=[], wasSuccessful=lambda: True)
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "timing.json"
            with self.assertRaisesRegex(TypeError, "source_fingerprint"):
                _write_report(
                    "validation",
                    result,
                    discovered=0,
                    elapsed=0.0,
                    report=report,
                    run_id="run-only",
                )

    def test_run_metadata_tokens_reject_paths_and_whitespace(self) -> None:
        self.assertEqual("validation-123", _run_id_argument("validation-123"))
        self.assertEqual(
            "sha256:abc-123",
            _source_fingerprint_argument("sha256:abc-123"),
        )
        for value in (
            "",
            ".",
            "..",
            " leading",
            "two words",
            "path/segment",
            "path\\segment",
            "colon:not-allowed",
        ):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    _run_id_argument(value)


if __name__ == "__main__":
    unittest.main()
