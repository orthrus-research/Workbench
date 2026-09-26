"""One retained fixture attempt has supervised, restart-safe process evidence."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from workbench_api.processes import ProcessError
from workbench_core.environment_fixture_execution import (
    apply_fixture_execution, inspect_fixture_execution, plan_fixture_execution,
    reopen_fixture_execution,
)
import workbench_core.environment_fixture_execution as execution_module
from workbench_core.environment_fixture_gradle_import import _qualified_filesystem
from workbench_core.environment_reconstruction import ReconstructionError
from workbench_core.environment_resolution import resolve_environment

import test_environment_fixture_command as command_tests


@skipIf(not sys.platform.startswith("linux"), "fixture execution uses Linux/WSL custody")
class EnvironmentFixtureExecutionTests(TestCase):
    setUp = command_tests.EnvironmentFixtureCommandTests.setUp
    _refresh = command_tests.EnvironmentFixtureCommandTests._refresh
    _selection = command_tests.EnvironmentFixtureCommandTests._selection
    _retain_fixture_and_gradle = command_tests.EnvironmentFixtureCommandTests._retain_fixture_and_gradle
    _inputs = command_tests.EnvironmentFixtureCommandTests._inputs
    _arguments = command_tests.EnvironmentFixtureCommandTests._arguments

    def _ready(self) -> None:
        command_tests.EnvironmentFixtureCommandTests._ready(self)
        # Ordinary unit-test temp roots may be tmpfs. A selected Linux/WSL
        # filesystem can run the same tests without this synthetic host shim.
        if not _qualified_filesystem(self.root):
            filesystem = patch(
                "workbench_core.environment_fixture_execution._qualified_filesystem",
                return_value=True,
            )
            filesystem.start()
            self.addCleanup(filesystem.stop)

    def _kwargs(self) -> dict:
        return {
            "workspace": self.workspace,
            "fixture_result_resource_id": self.fixture_result_id,
            "expected_review_id": self.review_id,
            "projection_result_resource_id": self.projection_id,
            "binding_result_resource_id": self.binding_id,
            "preflight_result_resource_id": self.preflight_id,
            "environment": self.environment,
        }

    def _fake_gradle(self, script: bytes) -> None:
        with ZipFile(self.archive, "w") as bundle:
            launcher = ZipInfo("gradle-9.6.1/bin/gradle")
            launcher.create_system = 3
            launcher.external_attr = (stat.S_IFREG | 0o755) << 16
            bundle.writestr(launcher, script)
            bundle.writestr("gradle-9.6.1/lib/gradle-launcher-9.6.1.jar", b"launcher")
        policy = self.owner.read_execution_policy()
        policy["gradle"]["archive_sha256"] = "sha256:" + sha256(self.archive.read_bytes()).hexdigest()
        policy["gradle"]["archive_size"] = self.archive.stat().st_size
        self.owner.policy_path.write_text(json.dumps(policy), encoding="utf-8")

    def test_supervised_exit_retains_capture_and_unadmitted_generated_output(self) -> None:
        self._fake_gradle(
            b"#!/bin/sh\nmkdir -p build\nprintf result > build/result.txt\n"
            b"printf observed-out\nprintf observed-err >&2\nexit 0\n",
        )
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        self.assertEqual("ready", plan["state"], plan["blockers"])
        self.assertFalse(Path(plan["paths"]["attempt"]).exists())
        self.assertEqual("not-started", inspect_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["state"])
        result = apply_fixture_execution(
            self.suite, self.share, self.candidate, expected_plan_id=plan["plan_id"],
            **self._kwargs(),
        )
        self.assertEqual("observed-process-group-exit-unadmitted", result["state"])
        self.assertEqual(0, result["capture"]["exit_code"])
        self.assertEqual(self.share["lock"]["unresolved_inputs"], result["unresolved_inputs"])
        self.assertEqual(b"result", (self.project / "build/result.txt").read_bytes())
        capture = Path(result["capture"]["capture_directory"])
        self.assertEqual(b"observed-out", (capture / "stdout.raw").read_bytes())
        self.assertEqual(b"observed-err", (capture / "stderr.raw").read_bytes())
        self.archive.unlink()
        for row in self.owner.inputs:
            row["path"].unlink()
        self.assertEqual(result["resource"]["resource_id"], reopen_fixture_execution(
            self.suite, self.share, self.candidate,
            result_resource_id=result["resource"]["resource_id"], **self._kwargs(),
        )["resource"]["resource_id"])
        self.assertEqual("blocked", plan_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["state"])
        with self.assertRaisesRegex(ReconstructionError, "blocked"):
            apply_fixture_execution(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._kwargs(),
            )

    def test_nonzero_exit_is_observed_without_artifact_admission(self) -> None:
        self._fake_gradle(b"#!/bin/sh\nprintf failed >&2\nexit 7\n")
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        result = apply_fixture_execution(
            self.suite, self.share, self.candidate, expected_plan_id=plan["plan_id"],
            **self._kwargs(),
        )
        self.assertEqual(7, result["capture"]["exit_code"])
        self.assertEqual("observed-process-group-exit-unadmitted", result["state"])
        self.assertEqual(result["capture"], inspect_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["capture"])

    def test_interruption_and_allocation_only_crash_remain_unknown(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        with patch("workbench_core.environment_fixture_execution.tool_process.capture",
                   side_effect=ProcessError("interrupted after launch")):
            with self.assertRaisesRegex(ReconstructionError, "unknown outcome"):
                apply_fixture_execution(
                    self.suite, self.share, self.candidate,
                    expected_plan_id=plan["plan_id"], **self._kwargs(),
                )
        self.assertEqual("unknown-after-preparation", inspect_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["state"])
        with self.assertRaisesRegex(ReconstructionError, "blocked"):
            apply_fixture_execution(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._kwargs(),
            )

    def test_crash_after_core_allocation_blocks_retry_without_marker(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        with patch("workbench_core.environment_fixture_execution._write_record",
                   side_effect=OSError("interrupted marker write")):
            with self.assertRaisesRegex(OSError, "interrupted marker write"):
                apply_fixture_execution(
                    self.suite, self.share, self.candidate,
                    expected_plan_id=plan["plan_id"], **self._kwargs(),
                )
        observed = inspect_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )
        self.assertEqual("unknown-after-preparation", observed["state"])
        self.assertFalse((Path(plan["paths"]["attempt"]) / "capture").exists())
        self.assertEqual("blocked", plan_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["state"])

    def test_missing_allocated_path_still_blocks_new_attempt(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        local = resolve_environment(
            self.suite, workspace=self.workspace, environment=self.environment,
        )
        target = Path(plan["paths"]["attempt"])
        host = execution_module._allocation_host(local)
        allocation = host.allocate(
            "environment.fixture", target.name, requested_path=target,
        )
        (target / ".workbench-allocation.json").unlink()
        target.rmdir()
        observed = inspect_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )
        self.assertEqual("unknown-after-preparation", observed["state"])
        self.assertEqual([allocation.allocation_id], observed["allocation_ids"])
        self.assertEqual("blocked", plan_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["state"])

    def test_crash_after_capture_before_finish_marker_remains_unknown(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        original = execution_module._write_record
        def interrupted(path, body):
            if path.name == "workbench-fixture-execution-finished.json":
                raise OSError("interrupted completion marker")
            original(path, body)
        with patch("workbench_core.environment_fixture_execution._write_record", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted completion marker"):
                apply_fixture_execution(
                    self.suite, self.share, self.candidate,
                    expected_plan_id=plan["plan_id"], **self._kwargs(),
                )
        target = Path(plan["paths"]["attempt"])
        self.assertTrue((target / "capture/capture.json").exists())
        self.assertEqual("unknown-after-preparation", inspect_fixture_execution(
            self.suite, self.share, self.candidate, **self._kwargs(),
        )["state"])

    def test_changed_attempt_marker_and_path_refuse_reopen(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        apply_fixture_execution(
            self.suite, self.share, self.candidate,
            expected_plan_id=plan["plan_id"], **self._kwargs(),
        )
        target = Path(plan["paths"]["attempt"])
        marker = target / "workbench-fixture-execution-attempt.json"
        saved = marker.read_bytes()
        marker.unlink()
        marker.write_bytes(b"{}\n")
        with self.assertRaises(ReconstructionError):
            inspect_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        marker.unlink()
        marker.write_bytes(saved)
        marker.chmod(0o600)
        moved = target.with_name("retained-moved-attempt")
        target.rename(moved)
        target.symlink_to(moved)
        with self.assertRaises(ReconstructionError):
            inspect_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())

    def test_changed_capture_refuses_observed_result_reopen(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        result = apply_fixture_execution(
            self.suite, self.share, self.candidate,
            expected_plan_id=plan["plan_id"], **self._kwargs(),
        )
        stream = Path(result["capture"]["capture_directory"]) / "stdout.raw"
        stream.write_bytes(b"changed")
        with self.assertRaisesRegex(ReconstructionError, "capture cannot reopen"):
            reopen_fixture_execution(
                self.suite, self.share, self.candidate,
                result_resource_id=result["resource"]["resource_id"], **self._kwargs(),
            )

    def test_process_source_mutation_stays_unknown_after_launch(self) -> None:
        self._fake_gradle(b"#!/bin/sh\nprintf changed > src/main.txt\nexit 0\n")
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        with self.assertRaises(ReconstructionError):
            apply_fixture_execution(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._kwargs(),
            )
        # Once the source is altered, even the command cannot be rederived;
        # the attempt and capture stay retained for manual review.
        self.assertTrue(Path(plan["paths"]["attempt"]).exists())
        self.assertTrue((Path(plan["paths"]["attempt"]) / "capture/capture.json").exists())

    def test_changed_java_and_foreign_target_refuse_before_launch(self) -> None:
        self._ready()
        plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        java = self.root / "selected-java25/release"
        java.write_bytes(b'JAVA_VERSION="8"\n')
        with self.assertRaises(ReconstructionError):
            apply_fixture_execution(
                self.suite, self.share, self.candidate,
                expected_plan_id=plan["plan_id"], **self._kwargs(),
            )
        self.assertFalse(Path(plan["paths"]["attempt"]).exists())
        java.write_bytes(b'JAVA_VERSION="25.0.4"\n')
        target = Path(plan["paths"]["attempt"])
        target.parent.mkdir(parents=True)
        target.symlink_to(self.root)
        blocked = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
        self.assertEqual("blocked", blocked["state"])
        with self.assertRaises(ReconstructionError):
            inspect_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())

    def test_unqualified_filesystem_refuses_before_allocation(self) -> None:
        self._ready()
        with patch("workbench_core.environment_fixture_execution._qualified_filesystem", return_value=False):
            plan = plan_fixture_execution(self.suite, self.share, self.candidate, **self._kwargs())
            self.assertEqual("blocked", plan["state"])
            with self.assertRaisesRegex(ReconstructionError, "blocked"):
                apply_fixture_execution(
                    self.suite, self.share, self.candidate,
                    expected_plan_id=plan["plan_id"], **self._kwargs(),
                )
        self.assertFalse(Path(plan["paths"]["attempt"]).exists())


if __name__ == "__main__":
    import unittest
    unittest.main()
