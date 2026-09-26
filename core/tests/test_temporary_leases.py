"""Disposable Core scratch custody and conservative restart recovery."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.durable_resources import DurableResourceError
from workbench_core import check_storage
from workbench_core.storage.registered import ResourceCatalog
from workbench_core.temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


class TemporaryLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.config = self.base / "config"
        self.scratch = self.base / "scratch"
        self.host = self._host()

    def _host(self) -> CoreTemporaryLeases:
        return CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.config,
            locations={"system": self.scratch}, owner_id="validation",
        )

    def test_resource_inventory_validates_all_leases_before_workspace_filter(self) -> None:
        current = self.host.allocate("system", "current")
        foreign = self.base / "foreign-workspace"
        foreign.mkdir()
        other = CoreTemporaryLeases(
            workspace=foreign, configuration_home=self.config,
            locations={"system": self.base / "foreign-scratch"}, owner_id="validation",
        ).allocate("system", "foreign")
        catalog = ResourceCatalog(self.config)
        rows = catalog.inventory(workspace=self.workspace)["temporary_leases"]
        self.assertEqual([(current.lease_id, "active-or-abandoned")], [
            (row["lease_id"], row["status"]) for row in rows
        ])
        self.assertEqual([other.lease_id], [
            row["lease_id"] for row in catalog.inventory(workspace=foreign)["temporary_leases"]
        ])

        namespace = catalog.root / "temporary-leases"
        nonce = other.lease_id.rsplit(":", 1)[1]
        activation = namespace / "activations" / f"{nonce}.json"
        original = activation.read_bytes()
        changed = check_storage.read_json(activation)
        changed["reservation_id"] = "wrong"
        changed.pop("id")
        activation.write_bytes(check_storage.canonical(check_storage.seal(
            "workbench-temporary-activation-v1", changed,
        )))
        try:
            with self.assertRaises(DurableResourceError) as caught:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual("resource.changed", caught.exception.code)
        finally:
            activation.write_bytes(original)
        self.assertEqual([current.lease_id], [
            row["lease_id"] for row in catalog.inventory(workspace=self.workspace)["temporary_leases"]
        ])

    def test_catalog_refuses_orphan_and_unsafe_children_globally(self) -> None:
        current = self.host.allocate("system", "known")
        catalog = ResourceCatalog(self.config)
        namespace = catalog.root / "temporary-leases"
        foreign = self.base / "foreign-workspace"
        foreign.mkdir()
        candidates = (
            namespace / "activations" / ("a" * 32 + ".json"),
            namespace / "leases" / ("b" * 32 + ".lock"),
            namespace / "failures" / "unexpected.pending",
        )
        for path in candidates:
            with self.subTest(path=path):
                path.write_bytes(b"{}")
                path.chmod(0o600)
                try:
                    with self.assertRaises(DurableResourceError) as caught:
                        catalog.inventory(workspace=foreign)
                    self.assertEqual("resource.changed", caught.exception.code)
                finally:
                    path.unlink()
        self.assertEqual([], catalog.inventory(workspace=foreign)["temporary_leases"])
        self.assertEqual(current.lease_id, catalog.inventory(workspace=self.workspace)["temporary_leases"][0]["lease_id"])

        reservation = namespace / "reservations" / (current.lease_id.rsplit(":", 1)[1] + ".json")
        linked = namespace / "reservations" / ("c" * 32 + ".json")
        linked.hardlink_to(reservation)
        try:
            with self.assertRaises(DurableResourceError) as caught:
                catalog.inventory(workspace=foreign)
            self.assertEqual("resource.changed", caught.exception.code)
        finally:
            linked.unlink()
        self.assertEqual([], catalog.inventory(workspace=foreign)["temporary_leases"])

    def test_foreign_failure_must_bind_its_exact_disposal_intent(self) -> None:
        current = self.host.allocate("system", "current")
        foreign = self.base / "foreign-workspace"
        foreign.mkdir()
        other_host = CoreTemporaryLeases(
            workspace=foreign, configuration_home=self.config,
            locations={"system": self.base / "foreign-scratch"}, owner_id="validation",
        )
        other = other_host.allocate("system", "failed")
        with other_host.execution(other):
            with patch.object(other_host, "_remove_owned_tree", side_effect=OSError("fixture busy")):
                with self.assertRaisesRegex(TemporaryLeaseError, "fixture busy"):
                    other_host.dispose(other, drained=lambda: True)
        catalog = ResourceCatalog(self.config)
        self.assertEqual([current.lease_id], [
            row["lease_id"] for row in catalog.inventory(workspace=self.workspace)["temporary_leases"]
        ])
        failure = next((catalog.root / "temporary-leases" / "failures").iterdir())
        original = failure.read_bytes()
        changed = check_storage.read_json(failure)
        changed["intent_id"] = "wrong"
        changed.pop("id")
        failure.write_bytes(check_storage.canonical(check_storage.seal(
            "workbench-temporary-disposal-failure-v1", changed,
        )))
        try:
            with self.assertRaises(DurableResourceError) as caught:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual("resource.changed", caught.exception.code)
        finally:
            failure.write_bytes(original)
        self.assertEqual([current.lease_id], [
            row["lease_id"] for row in catalog.inventory(workspace=self.workspace)["temporary_leases"]
        ])

    def test_catalog_preserves_pre_activation_crash_state(self) -> None:
        original_write = self.host._write

        def interrupt_activation(name, *args, **kwargs):
            if name == "activations":
                raise OSError("interrupted activation")
            return original_write(name, *args, **kwargs)

        with patch.object(self.host, "_write", side_effect=interrupt_activation):
            with self.assertRaisesRegex(OSError, "interrupted activation"):
                self.host.allocate("system", "pre-activation")
        rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["temporary_leases"]
        self.assertEqual(["reserved-incomplete"], [row["status"] for row in rows])
        self.assertTrue((self.scratch / "pre-activation").is_dir())

    def test_active_lease_requires_a_confirmed_drain_before_disposal(self) -> None:
        reference = self.host.allocate("system", "run-one")
        (reference.path / "scratch.txt").write_bytes(b"temporary")
        with self.host.execution(reference):
            with self.assertRaisesRegex(TemporaryLeaseError, "not confirmed drained"):
                self.host.dispose(reference, drained=lambda: False)
            self.assertEqual(b"temporary", (reference.path / "scratch.txt").read_bytes())
            with self.assertRaisesRegex(TemporaryLeaseError, "active"):
                self._host().reconcile(reference.lease_id, drained=lambda: True)
            self.host.dispose(reference, drained=lambda: True)
        self.assertFalse(reference.path.exists())
        self._host().reconcile(reference.lease_id, drained=lambda: True)
        self.assertEqual("disposed", self._host().inventory()[0]["state"])

    def test_changed_root_is_never_deleted(self) -> None:
        reference = self.host.allocate("system", "run-two")
        displaced = self.scratch / "displaced"
        reference.path.rename(displaced)
        reference.path.mkdir()
        (reference.path / "unrelated.txt").write_bytes(b"other")
        with self.assertRaisesRegex(TemporaryLeaseError, "changed"):
            with self.host.execution(reference):
                self.fail("changed root must not enter an execution lease")
        self.assertEqual(b"other", (reference.path / "unrelated.txt").read_bytes())
        self.assertTrue((displaced / ".workbench-temporary-lease.json").is_file())

    def test_swap_before_isolating_disposal_root_keeps_new_tree(self) -> None:
        from workbench_core import temporary_leases

        reference = self.host.allocate("system", "run-swap")
        original_rename = temporary_leases._rename_no_replace
        displaced = self.scratch / "run-swap-displaced"

        def swap(source, destination, **kwargs):
            source.rename(displaced)
            source.mkdir()
            (source / "unrelated.txt").write_bytes(b"other")
            return original_rename(source, destination, **kwargs)

        with self.host.execution(reference):
            with patch("workbench_core.temporary_leases._rename_no_replace", side_effect=swap):
                with self.assertRaisesRegex(TemporaryLeaseError, "could not isolate"):
                    self.host.dispose(reference, drained=lambda: True)
        self.assertEqual(b"other", (reference.path / "unrelated.txt").read_bytes())
        self.assertTrue(displaced.is_dir())

    def test_failed_cleanup_retains_exact_root_for_restart_recovery(self) -> None:
        reference = self.host.allocate("system", "run-three")
        (reference.path / "scratch.txt").write_bytes(b"temporary")
        def partial_delete(path: Path, **_kwargs) -> None:
            (path / ".workbench-temporary-lease.json").unlink()
            raise OSError("busy")

        with self.host.execution(reference):
            with patch.object(self.host, "_remove_owned_tree", side_effect=partial_delete):
                with self.assertRaisesRegex(TemporaryLeaseError, "busy"):
                    self.host.dispose(reference, drained=lambda: True)
        tombstone = next(self.scratch.glob(".workbench-temporary-*.disposing"))
        self.assertFalse(reference.path.exists())
        self.assertFalse((tombstone / ".workbench-temporary-lease.json").exists())
        self.assertEqual(b"temporary", (tombstone / "scratch.txt").read_bytes())
        failure_records = list((self.config / "resources-v1/temporary-leases/failures").glob("*.json"))
        self.assertEqual(1, len(failure_records))
        self.assertIn("busy", failure_records[0].read_text(encoding="utf-8"))
        restarted = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.config,
            locations={"system": self.base / "new-scratch-root"},
            owner_id="validation",
        )
        self.assertEqual(reference, restarted.inventory()[0]["reference"])
        self.assertEqual("disposal-incomplete", ResourceCatalog(self.config).inventory(
            workspace=self.workspace,
        )["temporary_leases"][0]["status"])
        with self.assertRaisesRegex(TemporaryLeaseError, "not confirmed drained"):
            restarted.reconcile(reference.lease_id, drained=lambda: False)
        restarted.reconcile(reference.lease_id, drained=lambda: True)
        self.assertFalse(tombstone.exists())
        self.assertEqual("disposed", ResourceCatalog(self.config).inventory(
            workspace=self.workspace,
        )["temporary_leases"][0]["status"])
        restarted.reconcile(reference.lease_id, drained=lambda: True)

    def test_missing_root_after_intent_remains_unknown(self) -> None:
        reference = self.host.allocate("system", "run-missing")
        (reference.path / "scratch.txt").write_bytes(b"temporary")
        moved = self.scratch / "moved-after-intent"

        def move_then_fail(path: Path, **_kwargs) -> None:
            path.rename(moved)
            raise OSError("root moved")

        with self.host.execution(reference):
            with patch.object(self.host, "_remove_owned_tree", side_effect=move_then_fail):
                with self.assertRaises(TemporaryLeaseError):
                    self.host.dispose(reference, drained=lambda: True)
        restarted = self._host()
        self.assertEqual("disposal-unknown", restarted.inventory()[0]["state"])
        with self.assertRaises(TemporaryLeaseError) as caught:
            restarted.reconcile(reference.lease_id, drained=lambda: True)
        self.assertEqual("temporary.unknown", caught.exception.code)
        self.assertEqual(b"temporary", (moved / "scratch.txt").read_bytes())
        nonce = reference.lease_id.rsplit(":", 1)[1]
        catalog = self.config / "resources-v1/temporary-leases"
        self.assertTrue((catalog / "disposal-intents" / f"{nonce}.json").is_file())
        self.assertFalse((catalog / "disposals" / f"{nonce}.json").exists())
        self.assertEqual("disposal-unknown", restarted.inventory()[0]["state"])

    @unittest.skipUnless(sys.platform == "linux", "Linux mount IDs are required")
    def test_same_device_other_mount_is_refused_before_deletion(self) -> None:
        reference = self.host.allocate("system", "run-mount")
        nested = reference.path / "bound"
        nested.mkdir()
        (nested / "keep.txt").write_bytes(b"unrelated")
        with self.host.execution(reference):
            with patch("workbench_core.temporary_leases._mount_id", side_effect=[11, 12]):
                with self.assertRaises(TemporaryLeaseError) as caught:
                    self.host.dispose(reference, drained=lambda: True)
        self.assertEqual("temporary.changed", caught.exception.code)
        tombstone = next(self.scratch.glob(".workbench-temporary-*.disposing"))
        self.assertEqual(b"unrelated", (tombstone / "bound" / "keep.txt").read_bytes())
        self._host().reconcile(reference.lease_id, drained=lambda: True)

    def test_abandoned_allocation_needs_new_drain_proof(self) -> None:
        reference = self.host.allocate("system", "run-four")
        (reference.path / "scratch.txt").write_bytes(b"temporary")
        restarted = self._host()
        with self.assertRaisesRegex(TemporaryLeaseError, "not confirmed drained"):
            restarted.reconcile(reference.lease_id, drained=lambda: False)
        self.assertTrue(reference.path.exists())
        restarted.reconcile(reference.lease_id, drained=lambda: True)
        self.assertFalse(reference.path.exists())

    def test_private_mode_refusal_covers_wsl_like_mounts(self) -> None:
        with patch("workbench_core.temporary_leases.private_path", return_value=False):
            with self.assertRaisesRegex(TemporaryLeaseError, "on WSL use a Linux filesystem"):
                self.host.allocate("system", "run-five")
        self.assertFalse((self.scratch / "run-five").exists())


if __name__ == "__main__":
    unittest.main()
