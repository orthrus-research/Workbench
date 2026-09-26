"""Stable mutable allocations retain selected evidence and failed runs."""

from hashlib import sha256
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module
from workbench_api.working_allocations import WorkingAllocationError, WorkingAllocationReference
from workbench_api.working_allocations import working_allocations
from workbench_core.working_allocations import CoreWorkingAllocations, WorkingAllocationCatalog
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.storage.registered import ResourceCatalog
from workbench_core.storage import manager


class WorkingAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.config = self.home / "config"
        self.evidence = self.home / "evidence"
        self.host = self._host()

    def _host(self, *, evidence: Path | None = None) -> CoreWorkingAllocations:
        return CoreWorkingAllocations(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence if evidence is None else evidence},
            owner_id="crucible", policy_id="selected-policy",
        )

    def test_reservation_precedes_visible_allocation_and_path_stays_fixed(self) -> None:
        from workbench_core import working_allocations as module

        original = module.secure_private_path

        def observe(path: Path, *, directory: bool) -> Path:
            if path.name == "trial":
                rows = WorkingAllocationCatalog(self.config).inventory_rows()
                self.assertEqual(1, len(rows))
                self.assertEqual(str(path), rows[0]["path"])
                self.assertEqual("incomplete", rows[0]["status"])
                self.assertEqual("protected-until-reviewed-policy", rows[0]["retention"])
            return original(path, directory=directory)

        with patch.object(module, "secure_private_path", side_effect=observe):
            allocation = self.host.allocate("worldgen-iteration", "trial")
        self.assertEqual("trial", allocation.path.name)
        self.assertTrue(allocation.path.is_dir())
        self.assertEqual(allocation, self.host.open(allocation.allocation_id))
        self.assertEqual("incomplete", self.host.describe(allocation.allocation_id).status)
        self.assertEqual(str(allocation.path), WorkingAllocationCatalog(self.config).inventory_rows()[0]["path"])

    def test_selected_evidence_and_absolute_references_survive_reopen(self) -> None:
        allocation = self.host.allocate("worldgen-iteration", "trial")
        report = allocation.path / "iteration-report-v1.json"
        report.write_bytes(b'{"status":"complete"}\n')
        runtime = allocation.path / "runtime"
        runtime.mkdir()
        (runtime / "disposable.bin").write_bytes(b"world state not hashed")
        log = allocation.path / "logs" / "native.log"
        log.parent.mkdir()
        log.write_bytes(b"native evidence\n")
        with self.host.execution(allocation):
            terminal = self.host.finish(
                allocation, outcome="complete", evidence=(report, log),
                absolute_references=(report, runtime, log),
                validate=lambda path: self.assertEqual(report, path / "iteration-report-v1.json"),
            )
        self.assertEqual("complete", terminal.status)
        self.assertEqual({"iteration-report-v1.json", "logs/native.log"},
                         {row["relative_path"] for row in terminal.evidence})
        self.assertEqual({str(report), str(runtime), str(log)},
                         {row["absolute_path"] for row in terminal.absolute_references})
        self.assertEqual("sha256:" + sha256(report.read_bytes()).hexdigest(),
                         next(row["sha256"] for row in terminal.evidence if row["relative_path"] == report.name))
        self.assertEqual(allocation, self._host(evidence=self.home / "new-evidence").open(allocation.allocation_id))
        self.assertEqual(terminal, self._host(evidence=self.home / "new-evidence").describe(allocation.allocation_id))
        self.assertEqual(terminal, self.host.inventory()[0])
        self.assertEqual(terminal, self.host.verify(allocation.allocation_id))
        self.assertEqual(b"world state not hashed", (runtime / "disposable.bin").read_bytes())
        (runtime / "disposable.bin").write_bytes(b"mutable world changed")
        self.assertEqual(terminal, self.host.verify(allocation.allocation_id))
        report.write_bytes(b'{"status":"changed"}\n')
        with self.assertRaisesRegex(WorkingAllocationError, "terminal evidence changed"):
            self.host.verify(allocation.allocation_id)

    def test_large_disposable_tree_needs_only_selected_evidence(self) -> None:
        allocation = self.host.allocate("worldgen-iteration", "large-world")
        runtime = allocation.path / "runtime"
        runtime.mkdir()
        for index in range(4100):
            (runtime / f"chunk-{index:04d}.bin").touch()
        report = allocation.path / "iteration-report-v1.json"
        report.write_bytes(b"complete")
        with self.host.execution(allocation):
            result = self.host.finish(
                allocation, outcome="complete", evidence=(report,),
                absolute_references=(runtime, report), validate=lambda _: None,
            )
        self.assertEqual("complete", result.status)
        self.assertEqual(1, len(result.evidence))
        self.assertEqual(2, len(result.absolute_references))

    def test_repeated_label_never_replaces_first_allocation(self) -> None:
        first = self.host.allocate("worldgen-iteration", "trial")
        with self.assertRaisesRegex(WorkingAllocationError, "already exists"):
            self.host.allocate("worldgen-iteration", "trial")
        self.assertEqual(first, self.host.open(first.allocation_id))
        self.assertEqual(1, len(self.host.inventory()))

    def test_failed_and_abandoned_allocations_remain_discoverable(self) -> None:
        failed = self.host.allocate("worldgen-iteration", "failed")
        partial = failed.path / "iteration-report-v1.json"
        partial.write_bytes(b'{"status":"failed"}\n')
        with self.host.execution(failed):
            result = self.host.finish(
                failed, outcome="failed", evidence=(partial,),
                absolute_references=(partial,), failure="native process exited 1",
            )
        interrupted = self.host.allocate("worldgen-iteration", "interrupted")
        (interrupted.path / "partial.log").write_bytes(b"retained")
        rows = WorkingAllocationCatalog(self.config).inventory_rows(workspace=self.workspace)
        self.assertEqual({"failed", "incomplete"}, {row["status"] for row in rows})
        self.assertEqual({"protected-until-reviewed-policy"}, {row["retention"] for row in rows})
        self.assertEqual("native process exited 1", result.failure)
        self.assertEqual(b"retained", (interrupted.path / "partial.log").read_bytes())

    def test_interruption_before_activation_retains_registered_path(self) -> None:
        from workbench_core import working_allocations as module

        original = module.WorkingAllocationCatalog._write

        def interrupt(catalog, name, nonce, kind, body):
            if name == "activations":
                raise OSError("simulated interruption")
            return original(catalog, name, nonce, kind, body)

        with patch.object(module.WorkingAllocationCatalog, "_write", interrupt):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                self.host.allocate("worldgen-iteration", "trial")
        rows = WorkingAllocationCatalog(self.config).inventory_rows()
        self.assertEqual(1, len(rows))
        self.assertEqual("incomplete", rows[0]["status"])
        self.assertEqual("protected-until-reviewed-policy", rows[0]["retention"])
        self.assertTrue(Path(rows[0]["path"]).is_dir())

    def test_lease_and_cancellation_are_bound_to_exact_allocation(self) -> None:
        allocation = self.host.allocate("worldgen-iteration", "trial")
        self.assertFalse(self.host.active(allocation))
        with self.host.execution(allocation):
            self.assertTrue(self.host.active(allocation))
            with self.assertRaisesRegex(WorkingAllocationError, "already executing"):
                with self.host.execution(allocation):
                    pass
            self.host.request_cancel(allocation, "reviewed-request")
            self.host.request_cancel(allocation, "reviewed-request")
            self.assertTrue(self.host.cancellation_requested(allocation))
            with self.assertRaisesRegex(WorkingAllocationError, "cancellation request"):
                self.host.check_cancelled(allocation)
            with self.assertRaises(WorkingAllocationError):
                self.host.request_cancel(allocation, "different-request")
            report = allocation.path / "report.json"
            report.write_bytes(b"failed")
            with self.assertRaisesRegex(WorkingAllocationError, "cancellation request"):
                self.host.finish(allocation, outcome="complete", evidence=(report,), validate=lambda _: None)
            self.assertEqual("failed", self.host.finish(
                allocation, outcome="failed", evidence=(report,), failure="cancelled",
            ).status)
        self.assertFalse(self.host.active(allocation))

    def test_owner_validator_mutation_cannot_certify_evidence(self) -> None:
        allocation = self.host.allocate("worldgen-iteration", "trial")
        report = allocation.path / "report.json"
        report.write_bytes(b"before")
        with self.host.execution(allocation):
            with self.assertRaisesRegex(WorkingAllocationError, "changed during owner validation"):
                self.host.finish(
                    allocation, outcome="complete", evidence=(report,),
                    validate=lambda _: report.write_bytes(b"after"),
                )
        self.assertEqual("incomplete", self.host.describe(allocation.allocation_id).status)
        self.assertEqual(b"after", report.read_bytes())

    def test_invalid_target_is_rejected_before_catalog_or_namespace_creation(self) -> None:
        invalid = self.home / "outside" / "trial"
        for path in (Path("../outside/trial"), invalid, self.workspace / "ad-hoc" / "trial"):
            with self.assertRaises(WorkingAllocationError):
                self.host.allocate("worldgen-iteration", "trial", requested_path=path)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.evidence.exists())
        self.assertFalse(invalid.parent.exists())

    def test_symlink_or_forged_reference_cannot_gain_custody(self) -> None:
        outside = self.home / "outside"
        outside.mkdir()
        operational = self.workspace / ".workbench"
        operational.mkdir()
        link = operational / "redirect"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(WorkingAllocationError, "redirect"):
            self.host.allocate("worldgen-iteration", "trial", requested_path=link / "trial")
        self.assertFalse(self.config.exists())
        allocation = self.host.allocate("worldgen-iteration", "trial")
        forged = WorkingAllocationReference(
            allocation.allocation_id, allocation.family, allocation.label,
            outside / "trial", allocation.workspace, allocation.owner_id,
        )
        with self.assertRaisesRegex(WorkingAllocationError, "outside this Core host"):
            with self.host.execution(forged):
                pass

    def test_selected_evidence_must_be_in_tree_and_ordinary(self) -> None:
        allocation = self.host.allocate("worldgen-iteration", "trial")
        outside = self.home / "outside.txt"
        outside.write_bytes(b"outside")
        alias = allocation.path / "alias.txt"
        alias.symlink_to(outside)
        with self.host.execution(allocation):
            for selected in (outside, alias, allocation.path / "missing.txt"):
                with self.assertRaises((WorkingAllocationError, ValueError, OSError)):
                    self.host.finish(
                        allocation, outcome="failed", evidence=(selected,), failure="partial",
                    )
        self.assertEqual("incomplete", self.host.describe(allocation.allocation_id).status)

    def test_unavailable_private_permissions_fail_closed_before_reservation(self) -> None:
        from workbench_core import working_allocations as module

        with patch.object(module, "private_path", return_value=False):
            with self.assertRaisesRegex(WorkingAllocationError, "on WSL use a Linux filesystem"):
                self.host.allocate("worldgen-iteration", "trial")
        self.assertEqual([], WorkingAllocationCatalog(self.config).inventory_rows())

    def test_resource_catalog_lists_exact_allocations_by_workspace(self) -> None:
        first = self.host.allocate("worldgen-iteration", "first")
        other = self.home / "other-workspace"
        other.mkdir()
        other_host = CoreWorkingAllocations(
            workspace=other, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="crucible",
        )
        second = other_host.allocate("worldgen-iteration", "second")
        catalog = ResourceCatalog(self.config)
        self.assertEqual({first.allocation_id, second.allocation_id},
                         {row["allocation_id"] for row in catalog.inventory()["working_allocations"]})
        self.assertEqual([first.allocation_id],
                         [row["allocation_id"] for row in catalog.inventory(workspace=self.workspace)["working_allocations"]])

    def test_cleanup_protects_whole_allocation_for_every_lifecycle_state(self) -> None:
        states = []
        for label in ("incomplete", "failed", "complete"):
            allocation = self.host.allocate(
                "worldgen-iteration", label,
                requested_path=Path(".workbench/tmp") / label,
            )
            runtime = allocation.path / "runtime"
            runtime.mkdir()
            (runtime / "world.dat").write_bytes(label.encode())
            if label != "incomplete":
                report = allocation.path / "report.json"
                report.write_bytes(label.encode())
                with self.host.execution(allocation):
                    self.host.finish(
                        allocation, outcome=label, evidence=(report,),
                        validate=(lambda _: None) if label == "complete" else None,
                        failure="native failure" if label == "failed" else None,
                    )
            states.append((allocation, runtime))

        def item(path: Path) -> dict:
            return {"path": str(path), "deletion": {
                "state": "eligible", "recoverability": "trash", "reason_codes": [],
            }}

        sibling = self.workspace / ".workbench/tmp/unrelated"
        sibling.mkdir()
        items = [item(path) for allocation, runtime in states
                 for path in (allocation.path, runtime, allocation.path.parent)]
        items.append(item(sibling))
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            manager._protect_registered_resources(self.workspace, items)
            report = manager.inventory_storage(self.workspace)
            first_candidate = next(
                row for row in report["items"] if row["path"] == str(states[0][0].path)
            )
            blocked = manager.plan_cleanup(self.workspace, selector=first_candidate["item_id"])
        self.assertTrue(all(row["deletion"]["state"] == "protected" for row in items[:-1]))
        self.assertTrue(all("registered-resource" in row["deletion"]["reason_codes"] for row in items[:-1]))
        self.assertEqual("eligible", items[-1]["deletion"]["state"])
        self.assertEqual("blocked", blocked["status"])
        for allocation, _ in states:
            candidate = next(row for row in report["items"] if row["path"] == str(allocation.path))
            self.assertEqual("protected", candidate["deletion"]["state"])

    def test_corrupt_working_catalog_fails_cleanup_closed(self) -> None:
        allocation = self.host.allocate(
            "worldgen-iteration", "trial",
            requested_path=Path(".workbench/tmp/trial"),
        )
        nonce = allocation.allocation_id.rsplit(":", 1)[1]
        (self.config / "resources-v1/working-allocations/reservations" / f"{nonce}.json").write_bytes(b"corrupt\n")
        item = {"path": str(allocation.path / "runtime"), "deletion": {
            "state": "eligible", "recoverability": "trash", "reason_codes": [],
        }}
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            limitation = manager._protect_registered_resources(self.workspace, [item])
        self.assertIsNotNone(limitation)
        self.assertEqual("protected", item["deletion"]["state"])
        self.assertIn("registered-catalog-unavailable", item["deletion"]["reason_codes"])

    def test_dispatch_binds_working_port_to_admitted_owner(self) -> None:
        observed = []

        def run(argv, *, context):
            self.assertEqual(["now"], argv)
            allocation = working_allocations().allocate("worldgen-iteration", "trial")
            observed.append(allocation)
            return 0

        context = ExecutionContext(
            self.workspace, self.home / "state",
            locations={"evidence": self.evidence}, configuration_home=self.config,
        )
        module = InstalledModule(
            "sample", "workbench-sample", "0.1.0", "available",
            module=Module("sample", "0.1.0", (
                Capability("sample.working", ("sample",), "sample_plugin:run", "run"),
            )),
        )
        with patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}):
            self.assertEqual(0, dispatch(["sample", "now"], context, (module,)))
        self.assertEqual("sample", observed[0].owner_id)
        self.assertEqual("incomplete", ResourceCatalog(self.config).inventory()["working_allocations"][0]["status"])


if __name__ == "__main__":
    unittest.main()
