"""Opt-in Blueprints simulation scratch is Core issued and retained."""

from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from workbench_api.simulation_scratch import (
    SimulationScratchError, allocate_simulation_scratch, simulation_scratch_scope,
)
from workbench_core.modules import InstalledModule, dispatch
from workbench_core import process_capture
from workbench_core.simulation_scratch import CoreSimulationScratch
from workbench_core.temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


_PLAN = "blueprints-plan:sha256:" + "a" * 64


class SimulationScratchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.configuration_home = root / "config"
        self.parent = self.workspace / ".workbench/blueprints/session/simulation-workspaces"
        self.host = CoreSimulationScratch(
            workspace=self.workspace, configuration_home=self.configuration_home,
        )

    def test_unbound_request_refuses_before_allocation(self) -> None:
        with self.assertRaisesRegex(SimulationScratchError, "Core scratch host"):
            allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN)
        self.assertFalse(self.parent.exists())

    def test_completed_and_failed_calls_remain_in_restart_inventory(self) -> None:
        with simulation_scratch_scope(self.host):
            with allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN) as completed:
                (completed.path / "candidate.txt").write_text("retained", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN) as failed:
                    (failed.path / "partial.txt").write_text("partial", encoding="utf-8")
                    raise RuntimeError("interrupted")
        rows = CoreTemporaryLeases.inventory_catalog(
            self.configuration_home, workspace=self.workspace,
        )
        self.assertEqual(2, len(rows))
        self.assertEqual({completed.lease_id, failed.lease_id}, {row["lease_id"] for row in rows})
        self.assertEqual({"retained-unproven"}, {row["status"] for row in rows})
        self.assertEqual({"blueprints-simulation-v2"}, {row["role"] for row in rows})
        self.assertEqual("retained", (completed.path / "candidate.txt").read_text(encoding="utf-8"))
        self.assertEqual("partial", (failed.path / "partial.txt").read_text(encoding="utf-8"))
        leases = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.configuration_home,
            locations={"blueprints-simulation-v2": self.parent}, owner_id="blueprints",
        )
        with self.assertRaises(TemporaryLeaseError) as disposal:
            leases.reconcile(completed.lease_id, drained=lambda: True)
        self.assertEqual("temporary.policy", disposal.exception.code)
        self.assertTrue(completed.path.is_dir())

    def test_changed_lease_refuses_retention_and_remains_for_review(self) -> None:
        with simulation_scratch_scope(self.host):
            with self.assertRaises(SimulationScratchError):
                with allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN) as reference:
                    (reference.path / ".workbench-temporary-lease.json").write_bytes(b"changed\n")
        self.assertTrue(reference.path.is_dir())

    def test_git_capture_retains_exact_output_and_refuses_oversize_input(self) -> None:
        with simulation_scratch_scope(self.host):
            with allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN) as reference:
                result = self.host.capture_git(
                    reference, [sys.executable, "-c", "import sys; sys.stdout.write('captured')"],
                    cwd=self.workspace, stdin=b"",
                )
                self.assertEqual(0, result.exit_code)
                self.assertEqual(b"captured", result.stdout)
                with self.assertRaisesRegex(SimulationScratchError, "input exceeds"):
                    self.host.capture_git(
                        reference, [sys.executable, "-c", "pass"],
                        cwd=self.workspace, stdin=b"x" * (1024 * 1024 + 1),
                    )
        attempts = list((reference.path / "git-captures").iterdir())
        self.assertEqual([result.attempt_name], [item.name for item in attempts])
        started = json.loads((attempts[0] / "started.json").read_text(encoding="utf-8"))
        retained = process_capture.retained_files(
            attempts[0], binding=started["binding"], expected_id=result.capture_id,
            verify=True,
        )
        self.assertEqual(4, len(retained))
        self.assertEqual("retained-unproven", CoreTemporaryLeases.inventory_catalog(
            self.configuration_home, workspace=self.workspace,
        )[0]["status"])

    def test_git_capture_output_limit_retains_incomplete_attempt(self) -> None:
        with simulation_scratch_scope(self.host):
            with allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN) as reference:
                with self.assertRaisesRegex(SimulationScratchError, "byte bound"):
                    self.host.capture_git(
                        reference,
                        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 4194305)"],
                        cwd=self.workspace, stdin=b"",
                    )
        attempts = list((reference.path / "git-captures").iterdir())
        self.assertEqual(1, len(attempts))
        record = json.loads((attempts[0] / "capture.json").read_text(encoding="utf-8"))
        self.assertEqual("incomplete", record["state"])
        self.assertEqual("retained-unproven", CoreTemporaryLeases.inventory_catalog(
            self.configuration_home, workspace=self.workspace,
        )[0]["status"])

    def test_parent_outside_selected_workspace_is_refused(self) -> None:
        outside = self.workspace.parent / "elsewhere"
        with simulation_scratch_scope(self.host), self.assertRaises(SimulationScratchError):
            with allocate_simulation_scratch(parent=outside, plan_id=_PLAN):
                self.fail("outside scratch should not be issued")
        self.assertFalse(outside.exists())

    def test_installed_blueprints_scope_uses_selected_workspace_and_home(self) -> None:
        module = Module("blueprints", "0.1.0", (
            Capability("blueprints.scratch", ("scratch",), "scratch_plugin:run", "scratch"),
        ))
        installed = (InstalledModule(
            "blueprints", "workbench-blueprints", "0.1.0", "available", module=module,
        ),)

        def run(_arguments, *, context):
            with allocate_simulation_scratch(parent=self.parent, plan_id=_PLAN):
                pass
            return 0

        context = ExecutionContext(
            self.workspace, self.workspace.parent / "state",
            configuration_home=self.configuration_home,
        )
        with patch("workbench_core.modules.import_module", return_value=SimpleNamespace(run=run)):
            self.assertEqual(0, dispatch(["scratch"], context, installed))
        rows = CoreTemporaryLeases.inventory_catalog(
            self.configuration_home, workspace=self.workspace,
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("retained-unproven", rows[0]["status"])

        other = self.workspace.parent / "other"
        other.mkdir()
        mismatched = ExecutionContext(
            other, self.workspace.parent / "state",
            configuration_home=self.configuration_home,
        )
        with patch("workbench_core.modules.import_module", return_value=SimpleNamespace(run=run)):
            with self.assertRaises(ModuleError):
                dispatch(["scratch"], mismatched, installed)
        self.assertEqual(1, len(CoreTemporaryLeases.inventory_catalog(
            self.configuration_home, workspace=self.workspace,
        )))


if __name__ == "__main__":
    unittest.main()
