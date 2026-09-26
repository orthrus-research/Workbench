"""Core custody for mutable, retained attempt namespaces."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.managed_attempts import ManagedAttemptError, ManagedAttemptReference
from workbench_core import check_storage
from workbench_core.managed_attempts import CoreManagedAttempts
from workbench_core.storage.registered import ResourceCatalog


FAMILY = "recipe-capture-v1"
PREFIX = "recipe-capture"


class CoreManagedAttemptTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.config = self.home / "config"
        self.state = self.home / "state"
        self.evidence = self.home / "external-evidence"
        self.provider = self._provider()

    def _provider(self, *, evidence: Path | None = None) -> CoreManagedAttempts:
        return CoreManagedAttempts(
            workspace=self.workspace, configuration_home=self.config,
            state_root=self.state,
            locations={"evidence": self.evidence if evidence is None else evidence},
            owner_id="workbench-shell",
        )

    def test_registers_selected_external_namespace_before_first_attempt(self) -> None:
        original = check_storage.allocate_attempt

        def allocated(root: Path, prefix: str) -> Path:
            rows = ResourceCatalog(self.config).inventory(workspace=self.workspace)["record_stores"]
            self.assertEqual(1, len(rows))
            self.assertEqual(str(root / ".workbench/check-attempts"), rows[0]["path"])
            self.assertEqual("available", rows[0]["status"])
            return original(root, prefix)

        with patch.object(check_storage, "allocate_attempt", side_effect=allocated):
            reference = self.provider.allocate(FAMILY, PREFIX)
        self.assertEqual(self.provider.default_root(FAMILY), reference.root)
        self.assertEqual("workspace-", reference.root.parts[-3][:10])
        self.assertTrue(reference.path.is_dir())
        self.assertEqual(reference.path.name, reference.attempt_id)
        self.assertEqual(reference, self.provider.open(FAMILY, PREFIX, reference.attempt_id))
        self.assertEqual(1, len(ResourceCatalog(self.config).inventory()["record_stores"]))

    def test_two_selected_workspaces_have_separate_stores_and_reopen_after_switch(self) -> None:
        another = self.home / "other-workspace"
        another.mkdir()
        first = self.provider.allocate(FAMILY, PREFIX, workspace=self.workspace)
        second = self.provider.allocate(FAMILY, PREFIX, workspace=another)
        self.assertNotEqual(first.root, second.root)
        rows = ResourceCatalog(self.config).inventory()["record_stores"]
        self.assertEqual({str(self.workspace), str(another)}, {row["workspace"] for row in rows})
        self.assertEqual({str(first.path.parent), str(second.path.parent)}, {row["path"] for row in rows})
        switched = CoreManagedAttempts(
            workspace=another, configuration_home=self.config,
            state_root=self.home / "changed-state",
            locations={"evidence": self.home / "changed-evidence"},
            owner_id="workbench-shell",
        )
        self.assertEqual(first, switched.open(FAMILY, PREFIX, first.attempt_id))
        self.assertEqual(second, switched.open(FAMILY, PREFIX, second.attempt_id))

    def test_explicit_and_legacy_roots_reopen_after_provider_restart(self) -> None:
        explicit = self.home / "selected-state"
        selected = self.provider.allocate(FAMILY, PREFIX, requested_root=explicit)
        legacy = self.state / "recipe-captures"
        historical = self.provider.allocate(FAMILY, PREFIX, requested_root=legacy)
        restarted = self._provider(evidence=self.home / "new-evidence")
        self.assertEqual(selected, restarted.open(FAMILY, PREFIX, selected.attempt_id, requested_root=explicit))
        self.assertEqual(historical, restarted.open(
            FAMILY, PREFIX, historical.attempt_id, legacy_basename="recipe-captures",
        ))
        self.assertEqual(selected, restarted.open(FAMILY, PREFIX, selected.attempt_id))
        self.assertEqual(2, len(ResourceCatalog(self.config).inventory()["record_stores"]))

    def test_reopen_refuses_ambiguous_and_invalid_attempt_id(self) -> None:
        selected = self.provider.allocate(FAMILY, PREFIX)
        legacy = self.state / "recipe-captures"
        check_storage.initialize(legacy)
        duplicate = legacy / ".workbench/check-attempts" / selected.attempt_id
        duplicate.mkdir()
        with self.assertRaisesRegex(ManagedAttemptError, "ambiguous"):
            self.provider.open(FAMILY, PREFIX, selected.attempt_id, legacy_basename="recipe-captures")
        self.assertEqual(selected, self.provider.open(FAMILY, PREFIX, selected.attempt_id))
        with self.assertRaisesRegex(ManagedAttemptError, "exact managed attempt ID"):
            self.provider.open(FAMILY, PREFIX, "../" + selected.attempt_id)

    def test_cancellation_is_idempotent_and_binding_checked(self) -> None:
        reference = self.provider.allocate(FAMILY, PREFIX)
        self.assertFalse(self.provider.cancellation_requested(reference))
        self.provider.request_cancel(reference, "reviewed-request")
        self.provider.request_cancel(reference, "reviewed-request")
        self.assertTrue(self.provider.cancellation_requested(reference))
        with self.assertRaisesRegex(ManagedAttemptError, "binding changed"):
            self.provider.request_cancel(reference, "different-request")
        self.assertEqual({"request_id": "reviewed-request"}, check_storage.read_json(reference.path / "cancel.json"))
        (reference.path / "cancel.json").write_text('{"request_id":"reviewed-request","extra":true}')
        with self.assertRaisesRegex(ManagedAttemptError, "marker changed"):
            self.provider.cancellation_requested(reference)

    def test_execution_lease_exposes_active_operation(self) -> None:
        reference = self.provider.allocate(FAMILY, PREFIX)
        self.assertFalse(self.provider.active(reference))
        with self.provider.execution(reference):
            self.assertTrue(self.provider.active(reference))
        self.assertFalse(self.provider.active(reference))

    def test_forged_reference_is_refused_without_creating_any_files(self) -> None:
        reference = self.provider.allocate(FAMILY, PREFIX)
        foreign = self.home / "foreign"
        foreign.mkdir()
        forged = ManagedAttemptReference(
            reference.family, reference.attempt_id, foreign,
            foreign / ".workbench/check-attempts" / reference.attempt_id,
            reference.store_id,
        )
        for action in (
            lambda: self.provider.active(forged),
            lambda: self.provider.execution(forged),
            lambda: self.provider.cancellation_requested(forged),
            lambda: self.provider.request_cancel(forged, "binding"),
        ):
            with self.assertRaisesRegex(ManagedAttemptError, "outside Core custody"):
                action()
        self.assertEqual([], list(foreign.iterdir()))
        self.assertFalse((reference.path / "execution.lock").exists())
        self.assertFalse((reference.path / "cancel.json").exists())


if __name__ == "__main__":
    unittest.main()
