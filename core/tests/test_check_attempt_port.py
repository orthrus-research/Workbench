"""Core retained-check custody across writer, reader, recovery and restart."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_api import Capability, ExecutionContext, Module
from workbench_api.check_attempts import check_attempts
from workbench_api.managed_attempts import ManagedAttemptError, ManagedAttemptReference
from workbench_api.retained_snapshots import RetainedSnapshotAdmission
from workbench_core import check_lifecycle, check_snapshots, check_storage
from workbench_core.check_attempts import CoreCheckAttempts
from workbench_core.managed_attempts import CoreManagedAttempts
from workbench_core.modules import InstalledModule, dispatch
import test_check_snapshots as fixtures


FAMILY = "material-checks"
PREFIX = "material-check"


class CoreCheckAttemptPortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.config = self.home / "config"
        self.root = self.home / "checks"
        self.host = self._host()
        self.reference = self.host.allocate(FAMILY, PREFIX, requested_root=self.root,
                                            workspace=self.workspace)
        self.fixture = fixtures.CheckSnapshotsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.attempt = self.reference.path
        self.fixture.source = self.reference.path / "result.json"
        self.fixture.expected = {
            "attempt_id": self.reference.attempt_id, "request_id": "fixture-request",
        }
        check_storage.write_json(self.fixture.source, self.fixture.value, byte_limit=None)
        check_storage.write_json(self.reference.path / "request.json", {"request": "fixture"})
        self.admission = RetainedSnapshotAdmission(
            scope=lambda manifest: self.fixture.scope,
            expected=self.fixture.expected,
            supported_schemas=frozenset({"fixture-recipes-v1", "fixture-report-v1", "fixture-values-v1"}),
        )

    def _host(self, *, state: Path | None = None):
        attempts = CoreManagedAttempts(
            workspace=self.workspace, configuration_home=self.config,
            state_root=self.home / "state" if state is None else state,
            locations={"evidence": self.home / "evidence"}, owner_id="workbench-shell",
        )
        return CoreCheckAttempts(attempts)

    def _publish(self):
        original = self.fixture.describe

        def describe(value):
            fields, views, summary = original(value)
            fields["retained_inputs"] = [{"role": "request", "content": {
                **check_snapshots.file_content(self.reference.path / "request.json"),
                "media_type": "application/json",
            }}]
            return fields, views, summary

        return self.host.publish_snapshot(
            self.reference, "result.json", scope=self.fixture.scope,
            describe=describe, verify=self.fixture.verify,
        )

    def test_publish_register_restart_read_and_export_preserve_original(self):
        original = self.fixture.source.read_bytes()
        manifest = self._publish()
        custody = self.host.register_snapshot(
            self.reference, inputs={"request": "request.json"},
            context={"owner": "fixture"},
        )
        restarted = self._host(state=self.home / "changed-state")
        reopened = restarted.open_attempt(FAMILY, PREFIX, self.reference.attempt_id,
                                          requested_root=self.root)
        self.assertEqual(self.reference, reopened)
        def admit(request, inputs):
            self.assertEqual({"request": "fixture"}, request)
            self.assertEqual(b'{"request":"fixture"}\n', inputs.read_input("request"))
            return self.admission

        with restarted.read_snapshot(reopened, admission=self.admission,
                                     owner_id="fixture", admit=admit) as opened:
            self.assertEqual(manifest, opened.manifest)
            self.assertEqual(self.fixture.value, opened.read_record("report", "value"))
            destination = self.home / "exported-result.json"
            opened.export(destination)
        self.assertEqual(original, destination.read_bytes())
        self.assertEqual(original, self.fixture.source.read_bytes())
        self.assertEqual(manifest["id"], custody["snapshot_id"])
        (self.reference.path / "request.json").write_bytes(b'{"request":"changed"}\n')
        with self.assertRaisesRegex(ValueError, "retained evidence changed"):
            with restarted.read_snapshot(reopened, admission=self.admission,
                                         owner_id="fixture", admit=admit):
                self.fail("changed registered input was admitted")

    def test_forged_reference_is_rejected_before_read_lock_or_publication(self):
        foreign = self.home / "foreign"
        foreign.mkdir()
        forged = ManagedAttemptReference(
            self.reference.family, self.reference.attempt_id, foreign,
            foreign / ".workbench/check-attempts" / self.reference.attempt_id,
            self.reference.store_id,
        )
        for action in (
            lambda: self.host.publish_snapshot(
                forged, "result.json", scope=self.fixture.scope,
                describe=lambda _: self.fail("owner callback ran"),
                verify=lambda _: self.fail("owner callback ran"),
            ),
            lambda: self.host.read_snapshot(forged, admission=self.admission).__enter__(),
            lambda: self.host.reconcile_snapshot(forged, admission=self.admission),
        ):
            with self.assertRaisesRegex(ManagedAttemptError, "outside Core custody"):
                action()
        self.assertEqual([], list(foreign.iterdir()))
        self.assertFalse((self.reference.path / "snapshot").exists())
        self.assertFalse((self.reference.path / "snapshot.lock").exists())

    def test_execution_holds_attempt_and_check_store_leases(self):
        self.assertFalse(self.host.active(self.reference))
        with self.host.execution(self.reference):
            self.assertTrue(self.host.active(self.reference))
            self.assertTrue(check_lifecycle.busy(self.root))
        self.assertFalse(self.host.active(self.reference))
        self.assertFalse(check_lifecycle.busy(self.root))

    def test_interrupted_commit_reconciles_without_erasing_failure_journal(self):
        original = check_snapshots._completed
        with patch.object(check_snapshots, "_completed", side_effect=OSError("commit interrupted")):
            with self.assertRaisesRegex(OSError, "commit interrupted"):
                self._publish()
        journal = next((self.reference.path / "snapshot-operations").iterdir())
        self.assertTrue((journal / "failed.json").is_file())
        self.assertFalse((journal / "completed.json").exists())
        self.assertTrue((self.reference.path / "snapshot/publication.json").is_file())
        recovered = self.host.reconcile_snapshot(self.reference, admission=self.admission)
        self.assertEqual("reconciled-published-snapshot", recovered[0]["state"])
        self.assertTrue((journal / "failed.json").is_file())
        self.assertTrue((journal / "completed.json").is_file())
        with self.host.read_snapshot(self.reference, admission=self.admission) as opened:
            self.assertEqual(self.fixture.value, opened.read_record("report", "value"))

    def test_failed_owner_admission_keeps_result_and_failure_receipt(self):
        original = self.fixture.source.read_bytes()

        def refuse(value):
            raise ValueError("owner refused this result")

        with self.assertRaisesRegex(ValueError, "owner refused"):
            self.host.publish_snapshot(
                self.reference, "result.json", scope=self.fixture.scope,
                describe=self.fixture.describe, verify=refuse,
            )
        journal = next((self.reference.path / "snapshot-operations").iterdir())
        self.assertTrue((journal / "failed.json").is_file())
        self.assertFalse((self.reference.path / "snapshot").exists())
        self.assertEqual(original, self.fixture.source.read_bytes())
        recovered = self.host.reconcile_snapshot(self.reference, admission=self.admission)
        self.assertEqual("discarded-unpublished-staging", recovered[0]["state"])
        self.assertTrue((journal / "failed.json").is_file())
        self.assertTrue((journal / "recovered.json").is_file())
        self.assertEqual(original, self.fixture.source.read_bytes())

    def test_dispatch_binds_check_attempt_port_for_admitted_module(self):
        allocated = []

        def run(argv, *, context):
            self.assertEqual(["now"], argv)
            allocated.append(check_attempts().allocate(
                "fixture-checks", "fixture-check", requested_root=self.home / "dispatch-checks",
                workspace=context.workspace,
            ))
            return 0

        context = ExecutionContext(
            self.workspace, self.home / "state",
            locations={"evidence": self.home / "evidence"}, configuration_home=self.config,
        )
        module = InstalledModule(
            "sample", "workbench-sample", "0.1.0", "available",
            module=Module("sample", "0.1.0", (
                Capability("sample.check", ("sample",), "sample_plugin:run", "run"),
            )),
        )
        with patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}):
            self.assertEqual(0, dispatch(["sample", "now"], context, (module,)))
        self.assertTrue(allocated[0].path.is_dir())
        self.assertEqual("sample", next(row for row in self.host.attempts.catalog.inventory()["record_stores"]
                                        if row["family"] == "fixture-checks")["owner_id"])


if __name__ == "__main__":
    unittest.main()
