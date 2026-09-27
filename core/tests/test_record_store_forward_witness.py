"""Candidate-scoped pre-registration witnesses under a fresh V2 Core root."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import DurableResourceError
from workbench_core.durable_records import publish_immutable_bytes
from workbench_core.storage.registered import ResourceCatalog


class RecordStoreForwardWitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.root = self.home / "records"
        self.root.mkdir(mode=0o700)
        self.catalog = ResourceCatalog(self.home / "config")
        self.identity = {
            "family": "sample-records", "owner_id": "sample",
            "workspace": self.workspace, "root": self.root,
        }

    def _paths(self) -> tuple[Path, Path]:
        digest, _, _, _ = self.catalog._record_store_registration(**self.identity)
        return (
            self.workspace / ".workbench/record-store-forward-v1" / f"{digest}.json",
            self.catalog.root / "stores" / f"{digest}.json",
        )

    def test_v2_witness_is_verified_before_registration_and_reopens_without_cleanup(self) -> None:
        witness, registration = self._paths()
        observed: list[bool] = []

        def publish(path: Path, data: bytes, **kwargs: object) -> None:
            if path == registration:
                observed.append(witness.is_file())
                self.assertFalse(registration.exists())
            publish_immutable_bytes(path, data, **kwargs)

        with patch("workbench_core.storage.registered.publish_immutable_bytes", side_effect=publish):
            store_id, resource_id = self.catalog.register_record_store_forward(**self.identity)
        self.assertEqual(observed, [True])
        self.assertEqual(self.catalog.verify_root(), "ready-unproven")
        self.assertEqual(
            self.catalog.post_birth_coverage(
                resource_id, workspace=self.workspace,
                owner_id="workbench-core", target=witness,
            ),
            "post-birth-covered",
        )
        self.assertEqual(
            self.catalog.inspect_forward_record_store_registration(
                **self.identity, witness_resource_id=resource_id,
            ),
            {
                "format": "workbench-record-store-forward-inspection-v1",
                "workspace": str(self.workspace), "store_id": store_id,
                "witness_resource_id": resource_id,
                "status": "current-registration",
                "historical_completeness": "unproven",
                "cleanup_authority": "none", "root_state": "ready-unproven",
            },
        )

    def test_legacy_root_cannot_gain_retroactive_witness(self) -> None:
        store_id = self.catalog.register_record_store(**self.identity)
        witness, registration = self._paths()
        original = registration.read_bytes()
        with self.assertRaises(DurableResourceError) as refused:
            self.catalog.register_record_store_forward(**self.identity)
        self.assertEqual(refused.exception.code, "resource.unsupported")
        self.assertFalse(witness.exists())
        self.assertEqual(registration.read_bytes(), original)
        self.assertEqual(self.catalog.verify_root(), "ready-unproven")
        self.assertEqual(
            [row["store_id"] for row in self.catalog.inventory(workspace=self.workspace)["record_stores"]],
            [store_id],
        )

    def test_completed_v2_registration_cannot_be_witnessed_afterward(self) -> None:
        self.catalog._ensure(fresh_epoch=True)
        self.catalog.register_record_store(**self.identity)
        witness, registration = self._paths()
        original = registration.read_bytes()
        with self.assertRaises(DurableResourceError) as refused:
            self.catalog.register_record_store_forward(**self.identity)
        self.assertEqual(refused.exception.code, "resource.changed")
        self.assertFalse(witness.exists())
        self.assertEqual(registration.read_bytes(), original)

    def test_interrupted_registration_keeps_witness_without_claiming_completion(self) -> None:
        witness, registration = self._paths()

        def interrupt(path: Path, data: bytes, **kwargs: object) -> None:
            if path == registration:
                raise RuntimeError("stop before registration")
            publish_immutable_bytes(path, data, **kwargs)

        with patch("workbench_core.storage.registered.publish_immutable_bytes", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "stop before registration"):
                self.catalog.register_record_store_forward(**self.identity)
        self.assertTrue(witness.is_file())
        self.assertFalse(registration.exists())
        resources = self.catalog.inventory(workspace=self.workspace)["resources"]
        self.assertEqual(len(resources), 1)
        resource_id = resources[0]["resource_id"]
        reopened = ResourceCatalog(self.catalog.configuration_home)
        self.assertEqual(
            reopened.inspect_forward_record_store_registration(
                **self.identity, witness_resource_id=resource_id,
            )["status"],
            "witnessed-registration-unavailable",
        )
        with self.assertRaises(DurableResourceError):
            self.catalog.register_record_store_forward(**self.identity)
        self.assertFalse(registration.exists())

    def test_lost_workspace_issue_refuses_positive_diagnostic(self) -> None:
        _, resource_id = self.catalog.register_record_store_forward(**self.identity)
        epoch = self.catalog.fresh_root_epoch()["root_epoch"]
        issue = self.workspace / ".workbench/resource-issuance-v2" / epoch / "0000000000000001.json"
        issue.unlink()
        with self.assertRaises(DurableResourceError):
            self.catalog.inspect_forward_record_store_registration(
                **self.identity, witness_resource_id=resource_id,
            )
        self.assertEqual(self.catalog.verify_root(), "ready-unproven")

    def test_replaced_store_root_refuses_original_witness(self) -> None:
        _, resource_id = self.catalog.register_record_store_forward(**self.identity)
        self.root.rename(self.home / "old-records")
        self.root.mkdir(mode=0o700)
        with self.assertRaises(DurableResourceError) as refused:
            self.catalog.inspect_forward_record_store_registration(
                **self.identity, witness_resource_id=resource_id,
            )
        self.assertEqual(refused.exception.code, "resource.changed")

    def test_foreign_workspace_and_store_cannot_reuse_issued_witness(self) -> None:
        _, resource_id = self.catalog.register_record_store_forward(**self.identity)
        foreign_workspace = self.home / "other-workspace"
        foreign_workspace.mkdir()
        foreign_root = self.home / "other-records"
        foreign_root.mkdir(mode=0o700)
        for changed in (
            {**self.identity, "workspace": foreign_workspace},
            {**self.identity, "root": foreign_root},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(DurableResourceError) as refused:
                    self.catalog.inspect_forward_record_store_registration(
                        **changed, witness_resource_id=resource_id,
                    )
                self.assertEqual(refused.exception.code, "resource.changed")
        self.assertEqual(
            self.catalog.inspect_forward_record_store_registration(
                **self.identity, witness_resource_id=resource_id,
            )["cleanup_authority"],
            "none",
        )

    @unittest.skipIf(os.name == "nt", "POSIX mode custody is tested on Linux/WSL")
    def test_shared_writable_workspace_refuses_before_catalog_creation(self) -> None:
        self.workspace.chmod(0o777)
        with self.assertRaises(DurableResourceError):
            self.catalog.register_record_store_forward(**self.identity)
        self.assertFalse(self.catalog.configuration_home.exists())


if __name__ == "__main__":
    unittest.main()
