"""Contracts for isolated and concurrent Python-suite validation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from typing import Callable
from unittest.mock import patch


VALIDATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VALIDATION_ROOT))

from orchestration import (  # noqa: E402
    OrchestrationFailure,
    ProcessOutcome,
    SchedulingItem,
    create_run_paths,
    deterministic_batches,
    fingerprint_paths,
    load_suite_report,
    new_run_id,
    require_source_fingerprint,
    require_successful_process,
    select_runnable,
    validate_run_id,
)


class ValidationRunIdentityTests(unittest.TestCase):
    def test_run_id_is_sortable_and_cross_platform_safe(self) -> None:
        run_id = new_run_id(
            now=datetime(2026, 8, 25, 14, 15, 16, 123456, tzinfo=timezone.utc),
            entropy="0123abcd",
        )
        self.assertEqual("20260825T141516123456Z-0123abcd", run_id)
        self.assertEqual(run_id, validate_run_id(run_id))

    def test_run_id_rejects_path_syntax_and_windows_unsafe_colon(self) -> None:
        for run_id in ("../escape", ".", "run/id", "run\\id", "run:id", ""):
            with self.subTest(run_id=run_id):
                with self.assertRaises(OrchestrationFailure):
                    validate_run_id(run_id)

    def test_run_tree_is_new_and_suite_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary) / "runs"
            paths = create_run_paths(storage, "run-001")
            self.assertEqual(storage / "run-001", paths.root)
            self.assertEqual(
                paths.reports / "validation.json", paths.report_for("validation")
            )
            self.assertEqual(paths.logs / "atlas.log", paths.log_for("atlas"))
            self.assertEqual(paths.temporary / "atlas", paths.temporary_for("atlas"))
            for directory in (paths.reports, paths.logs, paths.temporary):
                self.assertTrue(directory.is_dir())
            with self.assertRaisesRegex(OrchestrationFailure, "already exists"):
                create_run_paths(storage, "run-001")

    def test_run_tree_can_place_temporary_storage_outside_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = create_run_paths(
                root / "artifacts",
                "run-002",
                temporary_storage_root=root / "suite-temp",
            )
            self.assertEqual(root / "suite-temp/run-002", paths.temporary)
            self.assertTrue(paths.temporary.is_dir())
            self.assertFalse(paths.temporary.is_relative_to(paths.root))


class SourceFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_order_independent_but_binds_paths_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("same\n", encoding="utf-8")
            (root / "b.py").write_text("same\n", encoding="utf-8")
            observed = fingerprint_paths(root, ("b.py", "a.py"))
            self.assertEqual(observed, fingerprint_paths(root, ("a.py", "b.py")))

            (root / "b.py").write_text("changed\n", encoding="utf-8")
            changed_bytes = fingerprint_paths(root, ("a.py", "b.py"))
            self.assertNotEqual(observed, changed_bytes)

            (root / "b.py").write_text("same\n", encoding="utf-8")
            self.assertNotEqual(observed, fingerprint_paths(root, ("a.py",)))

    def test_fingerprint_rejects_ambiguous_source_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            (root / "directory").mkdir()
            link = root / "link.py"
            link.symlink_to(source)
            cases = (
                ("duplicate", ("source.py", "source.py")),
                ("missing", ("missing.py",)),
                ("directory", ("directory",)),
                ("symlink", ("link.py",)),
                ("escape", ("../outside.py",)),
            )
            for label, selected in cases:
                with self.subTest(label=label):
                    with self.assertRaises(OrchestrationFailure):
                        fingerprint_paths(root, selected)

    def test_end_of_run_check_rejects_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("before\n", encoding="utf-8")
            expected = fingerprint_paths(root, ("source.py",))
            require_source_fingerprint(root, ("source.py",), expected)
            source.write_text("after\n", encoding="utf-8")
            with self.assertRaisesRegex(OrchestrationFailure, "drifted"):
                require_source_fingerprint(root, ("source.py",), expected)

    def test_fingerprint_rejects_a_file_that_changes_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("before\n", encoding="utf-8")
            original_read = Path.read_bytes

            def changing_read(path: Path) -> bytes:
                payload = original_read(path)
                if path == source:
                    path.write_text("after with another size\n", encoding="utf-8")
                return payload

            with patch.object(Path, "read_bytes", changing_read):
                with self.assertRaisesRegex(OrchestrationFailure, "changed while"):
                    fingerprint_paths(root, (source,))


class SuiteReportTests(unittest.TestCase):
    def _document(self) -> dict[str, object]:
        return {
            "format": "workbench-python-test-timing-v2",
            "suite": "validation",
            "authority": "Workbench validation",
            "run_id": "run-001",
            "source_fingerprint": "sha256:" + "a" * 64,
            "discovered_tests": 2,
            "executed_tests": 2,
            "successful": True,
            "seconds": 1.25,
            "tests": [
                {"test": "tests.Example.test_a", "outcome": "passed", "seconds": 0.5},
                {"test": "tests.Example.test_b", "outcome": "skipped", "seconds": 0.0},
            ],
        }

    def _write(self, root: Path, document: object) -> Path:
        target = root / "validation.json"
        target.write_text(json.dumps(document), encoding="utf-8")
        return target

    def _load(self, path: Path, **overrides: object):
        arguments: dict[str, object] = {
            "expected_suite": "validation",
            "expected_authority": "Workbench validation",
            "expected_run_id": "run-001",
            "expected_source_fingerprint": "sha256:" + "a" * 64,
        }
        arguments.update(overrides)
        return load_suite_report(path, **arguments)  # type: ignore[arg-type]

    def test_complete_run_bound_report_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._load(self._write(Path(temporary), self._document()))
            self.assertTrue(report.successful)
            self.assertEqual(2, report.executed_tests)
            self.assertEqual(
                ("tests.Example.test_a", "tests.Example.test_b"),
                tuple(timing.test for timing in report.tests),
            )

    def test_report_identity_must_match_invocation(self) -> None:
        mutations = {
            "format": "future-format",
            "suite": "atlas",
            "authority": "Other authority",
            "run_id": "run-000",
            "source_fingerprint": "sha256:" + "b" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                document = self._document()
                document[field] = value
                with self.assertRaisesRegex(OrchestrationFailure, "mismatch"):
                    self._load(self._write(Path(temporary), document))

    def test_report_requires_complete_unique_terminal_timings(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, object]], object]]] = [
            ("missing-field", lambda d: d.pop("run_id")),
            ("partial-execution", lambda d: d.update(executed_tests=1)),
            ("missing-timing", lambda d: d["tests"].pop()),
            (
                "duplicate-test",
                lambda d: d["tests"][1].update(test="tests.Example.test_a"),
            ),
            (
                "unknown-outcome",
                lambda d: d["tests"][1].update(outcome="unknown"),
            ),
            ("nonfinite-seconds", lambda d: d.update(seconds=float("nan"))),
        ]
        for label, mutate in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                document = self._document()
                mutate(document)
                with self.assertRaises(OrchestrationFailure):
                    self._load(self._write(Path(temporary), document))

    def test_report_failure_cannot_be_accepted_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = self._document()
            document["successful"] = False
            document["tests"][0]["outcome"] = "failed"
            path = self._write(Path(temporary), document)
            with self.assertRaisesRegex(OrchestrationFailure, "records failure"):
                self._load(path)
            parsed = self._load(path, require_success=False)
            self.assertFalse(parsed.successful)

    def test_report_flag_must_agree_with_test_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = self._document()
            document["tests"][0]["outcome"] = "error"
            with self.assertRaisesRegex(OrchestrationFailure, "disagrees"):
                self._load(self._write(Path(temporary), document))

    def test_report_must_be_fresh_for_this_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._write(Path(temporary), self._document())
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
            with self.assertRaisesRegex(OrchestrationFailure, "predates"):
                self._load(path, not_before_ns=1_000_000_001)


class DeterministicSchedulerTests(unittest.TestCase):
    def test_two_workers_select_catalog_order_and_bypass_busy_lock(self) -> None:
        first = SchedulingItem("first", 0, resource_locks=("repository-temp",))
        second = SchedulingItem("second", 1, resource_locks=("repository-temp",))
        independent = SchedulingItem("independent", 2)
        selected = select_runnable(
            (independent, second, first),
            (),
            jobs=2,
        )
        self.assertEqual(
            ("first", "independent"), tuple(item.name for item in selected)
        )

    def test_exclusive_suite_is_a_catalog_barrier(self) -> None:
        active = SchedulingItem("active", 0)
        exclusive = SchedulingItem("exclusive", 1, exclusive=True)
        later = SchedulingItem("later", 2)
        self.assertEqual(
            (), select_runnable((exclusive, later), (active,), jobs=2)
        )
        self.assertEqual(
            (exclusive,), select_runnable((later, exclusive), (), jobs=2)
        )
        self.assertEqual(
            (), select_runnable((later,), (exclusive,), jobs=2)
        )

    def test_static_batches_are_deterministic_and_lock_safe(self) -> None:
        items = (
            SchedulingItem("first", 0, resource_locks=("repository-temp",)),
            SchedulingItem("second", 1, resource_locks=("repository-temp",)),
            SchedulingItem("independent", 2),
            SchedulingItem("exclusive", 3, exclusive=True),
            SchedulingItem("after", 4),
        )
        observed = tuple(
            tuple(item.name for item in batch)
            for batch in deterministic_batches(tuple(reversed(items)), jobs=2)
        )
        self.assertEqual(
            (
                ("first", "independent"),
                ("second",),
                ("exclusive",),
                ("after",),
            ),
            observed,
        )


class ProcessOutcomeTests(unittest.TestCase):
    def test_timeout_is_failure_even_if_killed_process_reports_zero(self) -> None:
        outcome = ProcessOutcome("atlas", 0, 10.1, 10, timed_out=True)
        self.assertFalse(outcome.successful)
        with self.assertRaisesRegex(OrchestrationFailure, "timed out after 10s"):
            require_successful_process(outcome)

    def test_nonzero_and_missing_exit_status_fail_distinctly(self) -> None:
        with self.assertRaisesRegex(OrchestrationFailure, "status 7"):
            require_successful_process(ProcessOutcome("atlas", 7, 1.0, 10))
        with self.assertRaisesRegex(OrchestrationFailure, "without a process exit"):
            require_successful_process(ProcessOutcome("atlas", None, 1.0, 10))
        require_successful_process(ProcessOutcome("atlas", 0, 1.0, 10))


if __name__ == "__main__":
    unittest.main()
