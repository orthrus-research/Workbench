"""Integration tests for the parallel suite scheduler glue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import signal
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


VALIDATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VALIDATION_ROOT))

import suite_execution as scheduler  # noqa: E402
from orchestration import ProcessOutcome, fingerprint_paths, create_run_paths, load_suite_report, OrchestrationFailure
from suite_measurement import inventory_digest  # noqa: E402
from suite_catalog import PythonTestSuite  # noqa: E402


class ParallelValidationSchedulerTests(unittest.TestCase):
    def test_parent_retains_admission_when_child_rewrites_its_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = self._suite("alpha")
            paths = create_run_paths(root / "runs", "admission-run")
            with patch.object(scheduler, "ROOT", root):
                request = scheduler._suite_requests((suite,), paths=paths, source_fingerprint="source:one")[0]
                self._write_success_report(request)
                script = '''
import json, pathlib, sys, time
report = pathlib.Path(sys.argv[1])
admission = report.with_suffix('.admitted.json')
deadline = time.monotonic() + 5
while not admission.exists():
    if time.monotonic() > deadline: raise RuntimeError('no admission')
    time.sleep(.01)
for path, field in ((report, 'collected_ids'), (report.with_suffix('.inventory.json'), 'test_ids')):
    document = json.loads(path.read_text())
    document[field] = ['alpha.Tests.replacement']
    import hashlib
    document['inventory_digest'] = 'sha256:' + hashlib.sha256(json.dumps(document[field], separators=(',', ':')).encode()).hexdigest()
    if 'tests' in document: document['tests'][0]['test'] = 'alpha.Tests.replacement'
    path.write_text(json.dumps(document))
'''
                command = (sys.executable, "-c", script, str(request.report_path), "--run-id", paths.run_id, "--source-fingerprint", "source:one")
                request = replace(request, command=command)
                result = scheduler._run_suite_process(request, process_registry={}, process_lock=threading.Lock(), interruption_event=threading.Event())
            self.assertEqual(0, result.outcome.returncode)
            self.assertEqual(("alpha.Tests.test_case",), result.admitted_test_ids)
            with self.assertRaisesRegex(OrchestrationFailure, "admitted inventory"):
                load_suite_report(request.report_path, expected_suite="alpha", expected_run_id=paths.run_id,
                                  expected_source_fingerprint="source:one", expected_test_ids=result.admitted_test_ids)

    def _suite(
        self,
        name: str,
        *,
        repository_temp: bool = False,
    ) -> PythonTestSuite:
        return PythonTestSuite(
            name=name,
            authority=f"{name} authority",
            start_dir=".",
            tier="quick",
            purpose=f"Exercise {name}.",
            repository_temp=repository_temp,
        )

    @staticmethod
    def _write_success_report(request: scheduler._SuiteRequest) -> None:
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        request.log_path.write_text("fake suite passed\n", encoding="utf-8")
        arguments = list(request.command)
        run_id = arguments[arguments.index("--run-id") + 1]
        source_fingerprint = arguments[
            arguments.index("--source-fingerprint") + 1
        ]
        request.report_path.with_suffix(".inventory.json").write_text(json.dumps({
            "format": "workbench-python-test-inventory-v1", "suite": request.suite.name,
            "authority": request.suite.authority, "run_id": run_id,
            "source_fingerprint": source_fingerprint,
            "test_ids": [f"{request.suite.name}.Tests.test_case"],
            "inventory_digest": inventory_digest([f"{request.suite.name}.Tests.test_case"]),
        }))
        request.report_path.write_text(
            json.dumps(
                {
                    "format": "workbench-python-test-timing-v3",
                    "state": "passed", "fixture_events": [], "phases": {},
                    "collected_ids": [f"{request.suite.name}.Tests.test_case"],
                    "inventory_digest": inventory_digest([f"{request.suite.name}.Tests.test_case"]),
                    "run_id": run_id,
                    "source_fingerprint": source_fingerprint,
                    "suite": request.suite.name,
                    "authority": request.suite.authority,
                    "discovered_tests": 1,
                    "executed_tests": 1,
                    "successful": True,
                    "seconds": 0.05,
                    "tests": [
                        {
                            "test": f"{request.suite.name}.Tests.test_case",
                            "outcome": "passed",
                            "seconds": 0.05,
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def test_two_workers_overlap_and_publish_complete_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            suites = (self._suite("alpha"), self._suite("beta"))
            counter_lock = threading.Lock()
            active = 0
            maximum_active = 0

            def fake_run(request, **_kwargs):
                nonlocal active, maximum_active
                with counter_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                try:
                    time.sleep(0.05)
                    self._write_success_report(request)
                finally:
                    with counter_lock:
                        active -= 1
                return scheduler._SuiteProcessResult(
                    request,
                    ProcessOutcome(request.suite.name, 0, 0.05, 900),
                    admitted_test_ids=(f"{request.suite.name}.Tests.test_case",),
                )

            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(
                    scheduler,
                    "_validation_temporary_storage",
                    return_value=root / "external-temp",
                ),
                patch.object(scheduler, "suites_for_tier", return_value=suites),
                patch.object(scheduler, "new_run_id", return_value="run-parallel"),
                patch.object(scheduler, "_run_suite_process", side_effect=fake_run),
            ):
                paths = scheduler.run_python_suites(
                    tier="quick",
                    selected=(),
                    jobs=2,
                    source_fingerprint=fingerprint,
                    repository_files=lambda: [source],
                )

            self.assertEqual(2, maximum_active)
            manifest = json.loads((paths.root / "run.json").read_text())
            self.assertEqual("passed", manifest["state"])
            self.assertEqual(["alpha", "beta"], manifest["selected_suites"])
            self.assertEqual(
                {"alpha", "beta"},
                {row["name"] for row in manifest["completed_suites"]},
            )
            for suite in suites:
                self.assertTrue(
                    (
                        root
                        / ".workbench/validation/test-timings"
                        / f"{suite.name}.json"
                    ).is_file()
                )

    def test_failure_stops_new_admission_but_drains_active_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            suites = (
                self._suite("alpha"),
                self._suite("beta", repository_temp=True),
                self._suite("gamma"),
            )
            launched: list[str] = []

            def fake_run(request, **_kwargs):
                launched.append(request.suite.name)
                request.temporary_root.mkdir(parents=True, exist_ok=True)
                (request.temporary_root / "scratch").write_text(
                    request.suite.name,
                    encoding="utf-8",
                )
                request.log_path.parent.mkdir(parents=True, exist_ok=True)
                if request.suite.name == "alpha":
                    request.log_path.write_text(
                        "intentional failure\n", encoding="utf-8"
                    )
                    return scheduler._SuiteProcessResult(
                        request,
                        ProcessOutcome(request.suite.name, 7, 0.01, 900),
                    )
                time.sleep(0.05)
                self._write_success_report(request)
                return scheduler._SuiteProcessResult(
                    request,
                    ProcessOutcome(request.suite.name, 0, 0.05, 900),
                    admitted_test_ids=(f"{request.suite.name}.Tests.test_case",),
                )

            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(
                    scheduler,
                    "_validation_temporary_storage",
                    return_value=root / "external-temp",
                ),
                patch.object(scheduler, "suites_for_tier", return_value=suites),
                patch.object(scheduler, "new_run_id", return_value="run-failure"),
                patch.object(scheduler, "_run_suite_process", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(
                    scheduler.SuiteExecutionFailure,
                    "alpha: suite alpha exited with status 7",
                ):
                    scheduler.run_python_suites(
                        tier="quick",
                        selected=(),
                        jobs=2,
                        source_fingerprint=fingerprint,
                        repository_files=lambda: [source],
                    )

            self.assertEqual({"alpha", "beta"}, set(launched))
            self.assertNotIn("gamma", launched)
            manifest = json.loads(
                (
                    root
                    / ".workbench/validation/runs/run-failure/run.json"
                ).read_text()
            )
            self.assertEqual("failed", manifest["state"])
            self.assertEqual(
                ["beta"],
                [row["name"] for row in manifest["completed_suites"]],
            )
            self.assertFalse(
                (root / "external-temp/run-failure").exists(),
                "failure must remove external run-owned suite scratch",
            )
            self.assertFalse(
                (
                    root
                    / ".workbench/validation/runs/run-failure/repository-tmp"
                ).exists(),
                "failure must remove repository-scoped suite scratch",
            )

    def test_keyboard_interrupt_terminates_owned_process_and_terminalizes_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            suite = self._suite("alpha")
            owned_process = Mock()
            registered = threading.Event()
            terminated = threading.Event()

            def fake_run(request, *, process_registry, process_lock, **_kwargs):
                request.temporary_root.mkdir(parents=True, exist_ok=True)
                (request.temporary_root / "scratch").write_text(
                    "interrupted",
                    encoding="utf-8",
                )
                with process_lock:
                    process_registry[request.suite.name] = owned_process
                registered.set()
                if not terminated.wait(timeout=2):
                    raise AssertionError("owned process was not terminated")
                with process_lock:
                    process_registry.pop(request.suite.name, None)
                return scheduler._SuiteProcessResult(
                    request,
                    ProcessOutcome(request.suite.name, -9, 0.05, 900),
                )

            def interrupt_wait(*_args, **_kwargs):
                if not registered.wait(timeout=2):
                    raise AssertionError("owned process was not registered")
                raise KeyboardInterrupt("operator cancelled")

            def terminate(process):
                self.assertIs(owned_process, process)
                terminated.set()

            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(
                    scheduler,
                    "_validation_temporary_storage",
                    return_value=root / "external-temp",
                ),
                patch.object(scheduler, "suites_for_tier", return_value=(suite,)),
                patch.object(scheduler, "new_run_id", return_value="run-interrupted"),
                patch.object(scheduler, "_run_suite_process", side_effect=fake_run),
                patch.object(scheduler, "wait", side_effect=interrupt_wait),
                patch.object(
                    scheduler,
                    "_terminate_process",
                    side_effect=terminate,
                ) as terminate_process,
            ):
                with self.assertRaisesRegex(KeyboardInterrupt, "operator cancelled"):
                    scheduler.run_python_suites(
                        tier="quick",
                        selected=(),
                        jobs=1,
                        source_fingerprint=fingerprint,
                        repository_files=lambda: [source],
                    )

            terminate_process.assert_called_once_with(owned_process)
            run_root = root / ".workbench/validation/runs/run-interrupted"
            manifest = json.loads((run_root / "run.json").read_text())
            self.assertEqual("failed", manifest["state"])
            self.assertEqual([], manifest["completed_suites"])
            self.assertIn(
                "validation interrupted by KeyboardInterrupt: operator cancelled",
                manifest["failures"],
            )
            self.assertFalse((root / "external-temp/run-interrupted").exists())

    def test_cleanup_failure_fails_closed_and_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            suite = self._suite("alpha")

            def fake_run(request, **_kwargs):
                request.temporary_root.mkdir(parents=True, exist_ok=True)
                self._write_success_report(request)
                return scheduler._SuiteProcessResult(
                    request,
                    ProcessOutcome(request.suite.name, 0, 0.05, 900),
                    admitted_test_ids=(f"{request.suite.name}.Tests.test_case",),
                )

            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(
                    scheduler,
                    "_validation_temporary_storage",
                    return_value=root / "external-temp",
                ),
                patch.object(scheduler, "suites_for_tier", return_value=(suite,)),
                patch.object(scheduler, "new_run_id", return_value="run-cleanup"),
                patch.object(scheduler, "_run_suite_process", side_effect=fake_run),
                patch.object(
                    scheduler.shutil,
                    "rmtree",
                    side_effect=OSError("busy"),
                ),
            ):
                with self.assertRaisesRegex(
                    scheduler.SuiteExecutionFailure,
                    "could not remove isolated suite temporary storage: busy",
                ):
                    scheduler.run_python_suites(
                        tier="quick",
                        selected=(),
                        jobs=1,
                        source_fingerprint=fingerprint,
                        repository_files=lambda: [source],
                    )

            manifest = json.loads(
                (
                    root
                    / ".workbench/validation/runs/run-cleanup/run.json"
                ).read_text()
            )
            self.assertEqual("failed", manifest["state"])
            self.assertIn(
                "could not remove isolated suite temporary storage: busy",
                manifest["failures"],
            )

    def test_concurrent_validator_invocations_keep_run_evidence_isolated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            suite = self._suite("alpha")
            overlap = threading.Barrier(2)
            run_ids = iter(("run-concurrent-a", "run-concurrent-b"))
            run_id_lock = threading.Lock()

            def next_run_id() -> str:
                with run_id_lock:
                    return next(run_ids)

            def fake_run(request, **_kwargs):
                request.temporary_root.mkdir(parents=True, exist_ok=True)
                overlap.wait(timeout=2)
                self._write_success_report(request)
                return scheduler._SuiteProcessResult(
                    request,
                    ProcessOutcome(request.suite.name, 0, 0.05, 900),
                    admitted_test_ids=(f"{request.suite.name}.Tests.test_case",),
                )

            def invoke():
                return scheduler.run_python_suites(
                    tier="quick",
                    selected=(),
                    jobs=1,
                    source_fingerprint=fingerprint,
                    repository_files=lambda: [source],
                )

            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(
                    scheduler,
                    "_validation_temporary_storage",
                    return_value=root / "external-temp",
                ),
                patch.object(scheduler, "suites_for_tier", return_value=(suite,)),
                patch.object(scheduler, "new_run_id", side_effect=next_run_id),
                patch.object(scheduler, "_run_suite_process", side_effect=fake_run),
                ThreadPoolExecutor(max_workers=2) as callers,
            ):
                futures = (callers.submit(invoke), callers.submit(invoke))
                paths = tuple(future.result(timeout=5) for future in futures)

            self.assertEqual(
                {"run-concurrent-a", "run-concurrent-b"},
                {path.run_id for path in paths},
            )
            for path in paths:
                manifest = json.loads((path.root / "run.json").read_text())
                self.assertEqual("passed", manifest["state"])
                self.assertEqual(path.run_id, manifest["run_id"])
                report = json.loads(path.report_for("alpha").read_text())
                self.assertEqual(path.run_id, report["run_id"])
                self.assertFalse(path.temporary.exists())

            latest = json.loads(
                (
                    root
                    / ".workbench/validation/test-timings/alpha.json"
                ).read_text()
            )
            self.assertIn(
                latest["run_id"],
                {"run-concurrent-a", "run-concurrent-b"},
            )


class ProcessTerminationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux process-group custody probe")
    def test_termination_reaches_ignoring_descendant_after_leader_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "child.pid"
            child = "import os,signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path(" + repr(str(ready)) + ").write_text(str(os.getpid())); time.sleep(60)"
            leader = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "])"
            process = subprocess.Popen([sys.executable, "-c", leader], start_new_session=True)
            try:
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                child_pid = int(ready.read_text())
                scheduler._terminate_process(process)
                state = Path(f"/proc/{child_pid}/stat")
                deadline = time.monotonic() + 3
                while state.exists() and state.read_text().split(") ", 1)[1][0] != "Z" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(not state.exists() or state.read_text().split(") ", 1)[1][0] == "Z")
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    def test_windows_termination_targets_the_complete_descendant_tree(self) -> None:
        process = Mock()
        process.pid = 4242
        process.poll.return_value = None
        process.wait.return_value = 1

        with (
            patch.object(scheduler.os, "name", "nt"),
            patch.object(scheduler.subprocess, "run") as run,
        ):
            scheduler._terminate_process(process)

        run.assert_called_once_with(
            ["taskkill.exe", "/PID", "4242", "/T", "/F"],
            check=False,
            stdout=scheduler.subprocess.DEVNULL,
            stderr=scheduler.subprocess.DEVNULL,
            timeout=5,
        )
        process.wait.assert_called_once_with(timeout=5)
        process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
