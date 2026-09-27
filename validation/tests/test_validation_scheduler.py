"""Integration tests for the parallel suite scheduler glue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
    def setUp(self) -> None:
        configuration = tempfile.TemporaryDirectory()
        self.addCleanup(configuration.cleanup)
        self.configuration_home = Path(configuration.name) / "config"
        environment = patch.dict(
            os.environ, {"WORKBENCH_CONFIG_HOME": str(self.configuration_home)},
        )
        environment.start()
        self.addCleanup(environment.stop)

    def _registered_run(self, root: Path, run_id: str):
        from workbench_core.working_allocations import WorkingAllocationCatalog

        rows = WorkingAllocationCatalog(self.configuration_home).inventory_rows(workspace=root)
        return next(row for row in rows if row["label"] == run_id)

    def test_suite_child_keeps_isolated_config_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = create_run_paths(root / "runs", "isolated-child")
            suite = self._suite("alpha")
            with patch.object(scheduler, "ROOT", root):
                request = scheduler._suite_requests(
                    (suite,), paths=paths, source_fingerprint="source:one",
                )[0]
                request = replace(request, command=(
                    sys.executable, "-c",
                    "import os; print(os.environ['WORKBENCH_CONFIG_HOME'])",
                ))
                result = scheduler._run_suite_process(
                    request, process_registry={}, process_lock=threading.Lock(),
                    interruption_event=threading.Event(),
                )
            self.assertEqual(0, result.outcome.returncode)
            self.assertEqual(
                str(request.temporary_root / "environment/config"),
                request.log_path.read_text(encoding="utf-8").strip(),
            )
            self.assertNotEqual(self.configuration_home, request.temporary_root / "environment/config")

    def test_registered_suite_log_streams_at_historical_path(self) -> None:
        from core_run_custody import allocate_validation_run

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host, allocation = allocate_validation_run(root, "live-run")
            paths = create_run_paths(
                root / ".workbench/validation/runs", "live-run",
                allocated_root=allocation.path,
            )
            release = root / "release"
            script = (
                "import pathlib, sys, time\n"
                "print('first', flush=True)\n"
                "release = pathlib.Path(sys.argv[1])\n"
                "deadline = time.monotonic() + 5\n"
                "while not release.exists() and time.monotonic() < deadline: time.sleep(.01)\n"
                "print('second', flush=True)\n"
            )
            with patch.object(scheduler, "ROOT", root):
                request = scheduler._suite_requests(
                    (self._suite("alpha"),), paths=paths,
                    source_fingerprint="source:one",
                    core_allocation_id=allocation.allocation_id,
                    core_configuration_home=self.configuration_home,
                )[0]
                request = replace(request, command=(
                    sys.executable, "-c", script, str(release),
                ))
                with host.execution(allocation), ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        scheduler._run_suite_process, request,
                        process_registry={}, process_lock=threading.Lock(),
                        interruption_event=threading.Event(),
                    )
                    try:
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            if request.log_path.exists() and b"first\n" in request.log_path.read_bytes():
                                break
                            time.sleep(.01)
                        self.assertEqual(b"first\n", request.log_path.read_bytes())
                        self.assertFalse(future.done())
                    finally:
                        release.write_bytes(b"go")
                    result = future.result(timeout=5)
            self.assertEqual(0, result.outcome.returncode)
            self.assertEqual(b"first\nsecond\n", request.log_path.read_bytes())
            self.assertEqual(0, request.log_path.stat().st_mode & 0o077)

    def test_registered_suite_log_refuses_foreign_run_and_existing_output(self) -> None:
        from core_run_custody import allocate_validation_run, open_validation_run_log
        from workbench_api.working_allocations import WorkingAllocationError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, allocation = allocate_validation_run(root, "log-run")
            paths = create_run_paths(
                root / ".workbench/validation/runs", "log-run",
                allocated_root=allocation.path,
            )
            log = paths.log_for("alpha")
            options = dict(
                selected_path=log, configuration_home=self.configuration_home,
            )
            with self.assertRaisesRegex(ValueError, "selected Core validation run"):
                with open_validation_run_log(
                    root, "other-run", allocation.allocation_id, "alpha", **options,
                ):
                    pass
            with self.assertRaises(WorkingAllocationError):
                with open_validation_run_log(
                    root, "log-run", allocation.allocation_id, "alpha",
                    selected_path=log, configuration_home=root / "foreign-home",
                ):
                    pass
            with self.assertRaisesRegex(ValueError, "path differs"):
                with open_validation_run_log(
                    root, "log-run", allocation.allocation_id, "alpha",
                    selected_path=root / "elsewhere.log",
                    configuration_home=self.configuration_home,
                ):
                    pass
            self.assertFalse(log.exists())
            with open_validation_run_log(
                root, "log-run", allocation.allocation_id, "alpha", **options,
            ) as stream:
                stream.write(b"partial log\n")
                self.assertEqual(b"partial log\n", log.read_bytes())
            with self.assertRaises(FileExistsError):
                with open_validation_run_log(
                    root, "log-run", allocation.allocation_id, "alpha", **options,
                ):
                    pass
            self.assertEqual(b"partial log\n", log.read_bytes())

    def test_core_run_manifest_revisions_preserve_changed_and_interrupted_evidence(self) -> None:
        from core_run_custody import allocate_validation_run, publish_validation_run_manifest
        from workbench_api.host_filesystem import DurableRecordError
        from workbench_api.working_allocations import WorkingAllocationError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, allocation = allocate_validation_run(root, "manifest-run")
            paths = create_run_paths(
                root / ".workbench/validation/runs", "manifest-run",
                allocated_root=allocation.path,
            )
            target = paths.root / "run.json"
            writer = scheduler._CoreRunManifestWriter(
                root, paths, allocation.allocation_id, self.configuration_home,
            )
            options = dict(
                tier="quick", jobs=1, source_fingerprint="source:one",
                selected_suites=(), reports={}, failures=[],
                started_at="2026-09-27T00:00:00Z", writer=writer,
            )
            stage = paths.root / ".run.json.interrupted.tmp"
            stage.write_bytes(b"incomplete")
            with self.assertRaisesRegex(OSError, "interrupted Core run manifest stage"):
                scheduler._write_run_manifest(paths, state="running", **options)
            self.assertFalse(target.exists())
            self.assertIsNone(writer.previous_sha256)
            stage.unlink()

            with self.assertRaises(WorkingAllocationError):
                publish_validation_run_manifest(
                    root, paths.run_id, allocation.allocation_id, b"foreign\n",
                    selected_path=target, configuration_home=root / "foreign-config",
                )
            with self.assertRaisesRegex(ValueError, "path differs"):
                publish_validation_run_manifest(
                    root, paths.run_id, allocation.allocation_id, b"foreign\n",
                    selected_path=paths.root / "foreign.json",
                    configuration_home=self.configuration_home,
                )
            self.assertFalse(target.exists())

            scheduler._write_run_manifest(paths, state="running", **options)
            first = target.read_bytes()
            self.assertEqual("running", json.loads(first)["state"])
            self.assertEqual(0, target.stat().st_mode & 0o077)
            stage.write_bytes(b"interrupted after link")
            with self.assertRaisesRegex(OSError, "interrupted Core run manifest stage"):
                scheduler._write_run_manifest(paths, state="failed", **options)
            self.assertEqual(first, target.read_bytes())
            stage.unlink()
            scheduler._write_run_manifest(paths, state="failed", **options)
            self.assertEqual("failed", json.loads(target.read_bytes())["state"])
            self.assertNotEqual(first, target.read_bytes())

            target.write_bytes(b'{"state":"competing"}\n')
            with self.assertRaisesRegex(DurableRecordError, "changed after review"):
                scheduler._write_run_manifest(paths, state="passed", **options)
            self.assertEqual(b'{"state":"competing"}\n', target.read_bytes())
            interruption = KeyboardInterrupt("interrupted")
            with patch.object(scheduler, "_cleanup_run_temporary", return_value=[]):
                scheduler._terminalize_abnormal_run(
                    paths, error=interruption, tier="quick", jobs=1,
                    source_fingerprint="source:one", selected_suites=(), reports={},
                    failures=[], started_at="2026-09-27T00:00:00Z",
                    scratch=(None, ()), drained=lambda: True, writer=writer,
                )
            self.assertEqual(b'{"state":"competing"}\n', target.read_bytes())
            self.assertIn("could not write its failed terminal manifest", " ".join(interruption.__notes__))

    def test_fresh_core_run_preserves_historical_tree_and_secures_parent(self) -> None:
        from core_run_custody import allocate_validation_run

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            historic = root / ".workbench/validation/runs/old-run/run.json"
            historic.parent.mkdir(parents=True)
            historic.write_bytes(b'{"legacy":true}\n')
            historic_log = historic.parent / "logs/alpha.log"
            historic_log.parent.mkdir()
            historic_log.write_bytes(b"historical suite output\n")
            historic.parent.parent.chmod(0o755)
            host, current = allocate_validation_run(root, "new-run")
            from workbench_api.working_allocations import WorkingAllocationError
            self.assertEqual(root / ".workbench/validation/runs/new-run", current.path)
            self.assertEqual(b'{"legacy":true}\n', historic.read_bytes())
            self.assertEqual(b"historical suite output\n", historic_log.read_bytes())
            self.assertEqual("incomplete", host.describe(current.allocation_id).status)
            self.assertEqual("new-run", self._registered_run(root, "new-run")["label"])
            self.assertEqual(1, len(host.inventory()), "legacy run must not be silently adopted")
            with self.assertRaisesRegex(WorkingAllocationError, "already exists"):
                allocate_validation_run(root, "old-run")
            if os.name == "posix":
                self.assertEqual(0o700, historic.parent.parent.stat().st_mode & 0o777)

    def test_timing_report_uses_core_store_and_upgrades_historical_file(self) -> None:
        from workbench_core.storage.registered import ResourceCatalog

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / ".workbench/validation/test-timings/alpha.json"
            old.parent.mkdir(parents=True)
            old.write_bytes(b'{"old":true}\n')
            old.chmod(0o644)
            with patch.object(scheduler, "ROOT", root):
                scheduler._publish_timing_report({"run_id": "new-run"}, "alpha")
            self.assertEqual({"run_id": "new-run"}, json.loads(old.read_bytes()))
            self.assertEqual(0, old.stat().st_mode & 0o077)
            rows = ResourceCatalog(self.configuration_home).inventory(
                workspace=root,
            )["record_stores"]
            self.assertEqual(1, len(rows))
            self.assertEqual("validation-timings-v1", rows[0]["family"])
            self.assertEqual(str(old.parent), rows[0]["path"])

    def test_concurrent_latest_timing_publishers_wait_and_keep_last_finisher(self) -> None:
        from workbench_core import durable_records
        from workbench_core.storage.registered import ResourceCatalog

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / ".workbench/validation/test-timings/alpha.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(b'{"run_id":"legacy"}\n')
            target.chmod(0o644)
            first = {"run_id": "first", "suite": "alpha", "more": "x" * 128}
            second = {"run_id": "second", "suite": "alpha"}
            first_inside = threading.Event()
            second_entered = threading.Event()
            release_first = threading.Event()
            original_prepared = durable_records._prepared
            original_replace = durable_records.replace_private_bytes

            @contextmanager
            def pause_first(path, data, **kwargs):
                with original_prepared(path, data, **kwargs) as staged:
                    if path == target and b'"first"' in data:
                        first_inside.set()
                        if not release_first.wait(5):
                            raise AssertionError("first publisher was not released")
                    yield staged

            def observe_second(path, data, **kwargs):
                if path == target and b'"second"' in data:
                    second_entered.set()
                return original_replace(path, data, **kwargs)

            with patch.object(scheduler, "ROOT", root), \
                    patch.object(durable_records, "_prepared", side_effect=pause_first), \
                    patch.object(durable_records, "replace_private_bytes", side_effect=observe_second), \
                    ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(scheduler._publish_timing_report, first, "alpha")
                try:
                    self.assertTrue(first_inside.wait(5))
                    second_future = pool.submit(scheduler._publish_timing_report, second, "alpha")
                    self.assertTrue(second_entered.wait(5))
                    self.assertFalse(second_future.done())
                finally:
                    release_first.set()
                first_future.result(timeout=5)
                second_future.result(timeout=5)
            self.assertEqual(second, json.loads(target.read_bytes()))
            self.assertEqual(0o600, target.stat().st_mode & 0o777)
            rows = ResourceCatalog(self.configuration_home).inventory(workspace=root)["record_stores"]
            self.assertEqual(
                [("validation-timings-v1", str(target.parent))],
                [(row["family"], row["path"]) for row in rows],
            )

    def test_timing_report_refuses_historical_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            other = root / "other.txt"
            other.write_bytes(b"unrelated\n")
            store = root / ".workbench/validation/test-timings"
            store.mkdir(parents=True)
            os.link(other, store / "alpha.json")
            with patch.object(scheduler, "ROOT", root):
                with self.assertRaisesRegex(OSError, "not an ordinary file"):
                    scheduler._publish_timing_report({"run_id": "new-run"}, "alpha")
            self.assertEqual(b"unrelated\n", other.read_bytes())

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
        from core_run_custody import publish_validation_run_record

        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        request.log_path.parent.mkdir(parents=True, exist_ok=True)
        request.log_path.write_text("fake suite passed\n", encoding="utf-8")
        arguments = list(request.command)
        run_id = arguments[arguments.index("--run-id") + 1]
        source_fingerprint = arguments[
            arguments.index("--source-fingerprint") + 1
        ]
        inventory = request.report_path.with_suffix(".inventory.json")
        inventory_bytes = (json.dumps({
            "format": "workbench-python-test-inventory-v1", "suite": request.suite.name,
            "authority": request.suite.authority, "run_id": run_id,
            "source_fingerprint": source_fingerprint,
            "test_ids": [f"{request.suite.name}.Tests.test_case"],
            "inventory_digest": inventory_digest([f"{request.suite.name}.Tests.test_case"]),
        }, indent=2, sort_keys=True) + "\n").encode("utf-8")
        report_bytes = (
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
                }, indent=2, sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if request.core_allocation_id is None:
            inventory.write_bytes(inventory_bytes)
            request.report_path.write_bytes(report_bytes)
        else:
            for kind, target, payload in (
                ("inventory", inventory, inventory_bytes),
                ("report", request.report_path, report_bytes),
            ):
                publish_validation_run_record(
                    scheduler.ROOT, run_id, request.core_allocation_id,
                    request.suite.name, kind, payload, selected_path=target,
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
            retained = self._registered_run(root, paths.run_id)
            self.assertEqual("complete", retained["status"])
            self.assertEqual("protected-until-reviewed-policy", retained["retention"])
            self.assertEqual(["run.json"], [row["relative_path"] for row in retained["evidence"]])
            from workbench_api.working_allocations import WorkingAllocationError
            from workbench_core.working_allocations import CoreWorkingAllocations

            reopened = CoreWorkingAllocations(
                workspace=root, configuration_home=self.configuration_home,
                locations={"evidence": root / ".workbench/validation/runs"},
                owner_id="validation",
            )
            self.assertEqual("complete", reopened.verify(retained["allocation_id"]).status)
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
            (paths.root / "run.json").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkingAllocationError, "terminal evidence changed"):
                reopened.verify(retained["allocation_id"])

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
            retained = self._registered_run(root, "run-failure")
            self.assertEqual("failed", retained["status"])
            self.assertEqual(["run.json"], [row["relative_path"] for row in retained["evidence"]])
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
            self.assertEqual("failed", self._registered_run(root, "run-interrupted")["status"])
            self.assertEqual("failed", manifest["state"])
            self.assertEqual([], manifest["completed_suites"])
            self.assertIn(
                "validation interrupted by KeyboardInterrupt: operator cancelled",
                manifest["failures"],
            )
            self.assertFalse((root / "external-temp/run-interrupted").exists())

    def test_cleanup_failure_fails_closed_and_is_recorded(self) -> None:
        from workbench_core.temporary_leases import CoreTemporaryLeases

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
                    CoreTemporaryLeases,
                    "_remove_owned_tree",
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
            self.assertEqual("failed", self._registered_run(root, "run-cleanup")["status"])
            self.assertIn(
                "could not remove isolated suite temporary storage: busy",
                manifest["failures"],
            )

    def test_setup_failure_before_launch_disposes_both_core_scratch_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(
                    scheduler, "_validation_temporary_storage",
                    return_value=root / "external-temp",
                ),
                patch.object(scheduler, "new_run_id", return_value="run-setup-error"),
                patch.object(scheduler, "_suite_requests", side_effect=RuntimeError("planning failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "planning failed"):
                    scheduler.run_python_suites(
                        tier="quick", selected=("core-api",), jobs=1,
                        source_fingerprint=fingerprint,
                        repository_files=lambda: [source],
                    )
            run_root = root / ".workbench/validation/runs/run-setup-error"
            self.assertFalse((root / "external-temp/run-setup-error").exists())
            self.assertFalse((run_root / "repository-tmp").exists())
            self.assertEqual("failed", self._registered_run(root, "run-setup-error")["status"])

    def test_surviving_posix_suite_group_retains_scratch(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX process-group drain policy")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.py"
            source.write_text("pass\n", encoding="utf-8")
            fingerprint = fingerprint_paths(root, (source,))
            suite = self._suite("alpha")

            def fake_run(request, *, launched_groups, **_kwargs):
                launched_groups.add(12345)
                self._write_success_report(request)
                return scheduler._SuiteProcessResult(
                    request,
                    ProcessOutcome(request.suite.name, 0, 0.05, 900),
                    admitted_test_ids=(f"{request.suite.name}.Tests.test_case",),
                )

            with (
                patch.object(scheduler, "ROOT", root),
                patch.object(scheduler, "_validation_temporary_storage", return_value=root / "external-temp"),
                patch.object(scheduler, "suites_for_tier", return_value=(suite,)),
                patch.object(scheduler, "new_run_id", return_value="run-descendant"),
                patch.object(scheduler, "_run_suite_process", side_effect=fake_run),
                patch.object(scheduler, "_process_group_active", return_value=True),
            ):
                with self.assertRaisesRegex(
                    scheduler.SuiteExecutionFailure, "process tree is not confirmed drained",
                ):
                    scheduler.run_python_suites(
                        tier="quick", selected=(), jobs=1,
                        source_fingerprint=fingerprint,
                        repository_files=lambda: [source],
                    )
            self.assertTrue((root / "external-temp/run-descendant").is_dir())
            self.assertTrue((root / ".workbench/validation/runs/run-descendant/repository-tmp").is_dir())
            self.assertEqual("failed", self._registered_run(root, "run-descendant")["status"])

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
