"""Prepared Core allocation, bounded processes, and restart install evidence."""

from __future__ import annotations

from multiprocessing import get_context
import os
from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch
import venv

from workbench_core import environment_package_install as install
from workbench_core import environment_package_install_plan as preflight
from workbench_core.environment_reconstruction import ReconstructionError
from workbench_core.working_allocations import CoreWorkingAllocations, WorkingAllocationCatalog

import test_environment_package_import as package_fixture


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL isolated install slice")
class EnvironmentPackageInstallTests(TestCase):
    def setUp(self) -> None:
        package_fixture.EnvironmentPackageImportTests.setUp(self)
        import_plan = package_fixture.EnvironmentPackageImportTests._plan(self)
        self.package = package_fixture.EnvironmentPackageImportTests._apply(self, import_plan)
        mounted = patch.object(preflight, "_mount_type", return_value="ext4")
        mounted.start()
        self.addCleanup(mounted.stop)

    def _plan(self) -> dict:
        return preflight.plan_package_install_preflight(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            environment=self.environment,
        )

    def _apply(self, plan: dict) -> dict:
        return install.apply_package_install(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            expected_plan_id=plan["plan_id"], environment=self.environment,
        )

    def _reconcile(self) -> dict:
        return install.reconcile_package_install(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            environment=self.environment,
        )

    def _reopen(self, result: dict) -> dict:
        return install.reopen_package_install(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            result_resource_id=result["resource"]["resource_id"],
            environment=self.environment,
        )

    @staticmethod
    def _benign_processes(plan: dict, closure: dict, wheelhouse: Path,
                          attempt_resource_id: str) -> tuple[dict, dict]:
        target = Path(plan["destination"])
        venv.EnvBuilder(with_pip=False, clear=False, symlinks=False).create(target)
        python = install._verify_python(plan)
        rows = {}
        for stage in ("install", "check"):
            rows[stage] = install._capture(
                plan, attempt_resource_id, stage,
                [sys.executable, "-c", f"print('{stage} captured')"], timeout=10,
            )
        return python, rows

    def test_core_allocation_prepared_attempt_and_bounded_capture_reopen(self) -> None:
        plan = self._plan()
        self.wheelhouse.rename(self.root / "former-wheelhouse")
        with patch.object(install, "_install", self._benign_processes):
            result = self._apply(plan)
        self.assertEqual("pip-complete-unadmitted", result["state"])
        self.assertEqual("installed", result["outcome"])
        self.assertEqual(self.closure["unresolved_inputs"], result["unresolved_inputs"])
        self.assertIn("optional-module-packages", result["unresolved_inputs"])
        target = Path(result["destination"])
        self.assertTrue((target / ".workbench-allocation.json").is_file())
        self.assertTrue((target / "workbench-package-install-attempt.json").is_file())
        self.assertTrue((target / "workbench-package-install-finished.json").is_file())
        self.assertTrue((target / "workbench-package-install-capture/capture.json").is_file())
        self.assertEqual(result["allocation_id"], self._reopen(result)["allocation_id"])
        reused = self._reconcile()
        self.assertEqual(result["resource"]["resource_id"], reused["resource"]["resource_id"])

    def test_crash_after_core_placeholder_preserves_incomplete_destination(self) -> None:
        plan = self._plan()
        with patch.object(install, "_install", side_effect=RuntimeError("interrupted after allocation")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._apply(plan)
        target = Path(plan["destination"])
        self.assertTrue((target / ".workbench-allocation.json").is_file())
        self.assertTrue((target / "workbench-package-install-attempt.json").is_file())
        review = self._reconcile()
        self.assertEqual("incomplete-review-required", review["state"])
        self.assertEqual(str(target), review["destination"])
        self.assertTrue(target.exists())
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            self._apply(plan)

    def test_hard_exit_after_placeholder_reopens_as_incomplete(self) -> None:
        plan = self._plan()

        def interrupted() -> None:
            with patch.object(install, "_install", side_effect=lambda *args: os._exit(79)):
                self._apply(plan)

        process = get_context("fork").Process(target=interrupted)
        process.start()
        process.join(20)
        self.assertEqual(79, process.exitcode)
        review = self._reconcile()
        self.assertEqual("incomplete-review-required", review["state"])
        self.assertTrue((Path(plan["destination"]) / ".workbench-allocation.json").is_file())

    def test_failed_supervised_process_retains_bounded_failure_capture(self) -> None:
        plan = self._plan()

        def failed(plan, closure, wheelhouse, attempt_resource_id):
            venv.EnvBuilder(with_pip=False, clear=False, symlinks=False).create(Path(plan["destination"]))
            install._capture(
                plan, attempt_resource_id, "install",
                [sys.executable, "-c", "import sys; print('failed'); sys.exit(4)"],
                timeout=10,
            )

        with patch.object(install, "_install", failed):
            with self.assertRaisesRegex(ReconstructionError, "exited with code 4"):
                self._apply(plan)
        target = Path(plan["destination"])
        self.assertTrue((target / "workbench-package-install-capture/capture.json").is_file())
        self.assertEqual("incomplete-review-required", self._reconcile()["state"])

    def test_interruption_before_domain_marker_preserves_core_allocation(self) -> None:
        plan = self._plan()
        with patch.object(install, "_record", side_effect=RuntimeError("marker interrupted")):
            with self.assertRaisesRegex(RuntimeError, "marker interrupted"):
                self._apply(plan)
        target = Path(plan["destination"])
        self.assertTrue((target / ".workbench-allocation.json").is_file())
        review = self._reconcile()
        self.assertEqual("incomplete-review-required", review["state"])
        self.assertIn("allocation_id", review)

    def test_interruption_before_core_activation_preserves_placeholder(self) -> None:
        plan = self._plan()
        original = WorkingAllocationCatalog._write

        def interrupt(catalog, name, nonce, kind, body):
            if name == "activations":
                raise RuntimeError("activation interrupted")
            return original(catalog, name, nonce, kind, body)

        with patch.object(WorkingAllocationCatalog, "_write", interrupt):
            with self.assertRaisesRegex(RuntimeError, "activation interrupted"):
                self._apply(plan)
        target = Path(plan["destination"])
        self.assertTrue((target / ".workbench-allocation.json").is_file())
        self.assertFalse((target / "workbench-package-install-attempt.json").exists())
        review = self._reconcile()
        self.assertEqual("incomplete-review-required", review["state"])
        self.assertIn("allocation_id", review)
        self.assertTrue(target.exists())

    def test_restart_after_finished_marker_reconciles_terminal_and_result(self) -> None:
        plan = self._plan()
        original = CoreWorkingAllocations.finish
        failed = False

        def interrupt(host, *args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("interrupted before allocation terminal")
            return original(host, *args, **kwargs)

        with patch.object(install, "_install", self._benign_processes):
            with patch.object(CoreWorkingAllocations, "finish", interrupt):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    self._apply(plan)
        self.assertTrue((Path(plan["destination"]) / "workbench-package-install-finished.json").is_file())
        result = self._reconcile()
        self.assertEqual("reconciled", result["outcome"])
        self._reopen(result)

    def test_restart_after_result_publication_failure_reuses_completion(self) -> None:
        plan = self._plan()
        with patch.object(install, "_install", self._benign_processes):
            with patch.object(install, "_publish_result", side_effect=RuntimeError("result interrupted")):
                with self.assertRaisesRegex(RuntimeError, "result interrupted"):
                    self._apply(plan)
        result = self._reconcile()
        self.assertEqual("reconciled", result["outcome"])
        self.assertEqual(result["resource"]["resource_id"], self._reconcile()["resource"]["resource_id"])

    def test_changed_process_output_refuses_completed_readback(self) -> None:
        plan = self._plan()
        with patch.object(install, "_install", self._benign_processes):
            result = self._apply(plan)
        output = Path(plan["destination"]) / "workbench-package-install-capture/stdout.raw"
        with output.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(ReconstructionError):
            self._reopen(result)

    def test_changed_marker_or_allocation_path_refuses_recovery(self) -> None:
        plan = self._plan()
        with patch.object(install, "_install", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self._apply(plan)
        target = Path(plan["destination"])
        marker = target / "workbench-package-install-attempt.json"
        marker.write_text("{}", encoding="utf-8")
        with self.assertRaises(ReconstructionError):
            self._reconcile()
        marker.unlink()
        moved = target.with_name(target.name + "-moved")
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ReconstructionError):
            self._reconcile()
        self.assertTrue(moved.exists())

    def test_foreign_target_and_changed_retained_wheel_refuse_before_install(self) -> None:
        plan = self._plan()
        target = Path(plan["destination"])
        target.mkdir(mode=0o700)
        with self.assertRaisesRegex(ReconstructionError, "changed after review"):
            self._apply(plan)
        target.rmdir()
        wheel = Path(self.package["tree_path"]) / "wheels/helper-1.0-py3-none-any.whl"
        with wheel.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ReconstructionError):
            self._apply(plan)
        self.assertFalse(target.exists())

    def test_install_command_uses_only_retained_wheelhouse_and_hash_lock(self) -> None:
        plan = self._plan()
        target = Path(plan["destination"])
        target.mkdir(mode=0o700)
        captured: list[list[str]] = []

        def record(_plan, _attempt, stage, command, *, timeout):
            captured.append(command)
            return {"capture_id": stage, "binding": "test", "exit_code": 0}

        with patch.object(install, "_capture", record):
            install._install(plan, self.closure, Path(self.package["tree_path"]), "attempt")
        bootstrap, check = captured
        self.assertIn("--no-index", bootstrap)
        self.assertIn("--require-hashes", bootstrap)
        self.assertIn("--only-binary=:all:", bootstrap)
        self.assertIn("--no-compile", bootstrap)
        self.assertEqual([str(target / "bin/python"), "-I", "-B", "-c"], bootstrap[:4])
        self.assertEqual(str(Path(self.package["tree_path"]) / "requirements.lock"), bootstrap[-1])
        self.assertIn(str(Path(self.package["tree_path"]) / "wheels"), bootstrap)
        self.assertEqual([str(target / "bin/python"), "-I", "-B", "-m", "pip", "--isolated", "check"], check)
