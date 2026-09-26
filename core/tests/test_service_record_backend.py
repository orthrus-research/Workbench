"""Core can inspect and publish service bytes without the semantic owner."""

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest

from workbench_api.host_filesystem import DurableRecordError
from workbench_api.service import ServicePhysicalLeasePorts, ServiceV3Error
from workbench_core.service.record_backend import (
    CoreServiceRecordBackend, inspect_service_record_root,
)
from workbench_core.storage.registered import ResourceCatalog


class ServiceRecordBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "service"
        self.leased = []

        @contextmanager
        def exclusive(path):
            self.leased.append(path)
            yield

        self.leases = ServicePhysicalLeasePorts(
            provider_id="test-service-records",
            acquire_instance=lambda _path: lambda: None,
            exclusive=exclusive,
        )
        self.backend = CoreServiceRecordBackend(self.root, physical_leases=self.leases)

    def test_core_only_inventory_reopens_published_and_replaced_records(self) -> None:
        jobs = self.backend.namespace("jobs")
        self.backend.allocate_directory(jobs / "example")
        with self.assertRaisesRegex(DurableRecordError, "already exists"):
            self.backend.allocate_directory(jobs / "example")
        immutable = jobs / "example" / "submission.json"
        head = jobs / "example" / "head.json"
        self.backend.publish_immutable(immutable, b'{"kind":"submission"}\n')
        self.backend.publish_immutable(immutable, b'{"kind":"submission"}\n')
        with self.assertRaisesRegex(DurableRecordError, "already exists"):
            self.backend.publish_immutable(immutable, b"changed")
        with self.backend.exclusive("job-v2:example"):
            self.backend.replace(head, b'{"ordinal":1}\n')
            self.backend.replace(head, b'{"ordinal":2}\n')
        inventory = inspect_service_record_root(self.root)
        self.assertEqual("available", inventory["status"])
        self.assertEqual(
            ["jobs/example/head.json", "jobs/example/submission.json"],
            [row["path"] for row in inventory["records"]],
        )
        self.assertTrue(all(row["status"] == "available" for row in inventory["records"]))
        self.assertEqual(b'{"ordinal":2}\n', head.read_bytes())
        self.assertEqual(1, len(self.leased))
        self.assertTrue(self.leased[0].is_relative_to(self.root / "locks"))

    def test_outside_and_redirected_targets_are_rejected_before_publication(self) -> None:
        outside = self.base / "outside.json"
        with self.assertRaisesRegex(ServiceV3Error, "outside"):
            self.backend.publish_immutable(outside, b"unsafe")
        self.assertFalse(outside.exists())
        (self.root / "jobs").symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ServiceV3Error, "not safely private"):
            self.backend.publish_immutable(self.root / "jobs" / "unsafe.json", b"unsafe")
        self.assertEqual("unsafe-file", next(
            row["status"] for row in inspect_service_record_root(self.root)["records"]
            if row["path"] == "jobs"
        ))

    def test_core_catalog_protects_service_root_across_restart(self) -> None:
        configuration = self.base / "configuration"
        workspace = self.base / "workspace"
        first = CoreServiceRecordBackend(
            self.root, physical_leases=self.leases,
            configuration_home=configuration, workspace=workspace,
        )
        second = CoreServiceRecordBackend(
            self.root, physical_leases=self.leases,
            configuration_home=configuration, workspace=workspace,
        )
        self.assertEqual(first.store_id, second.store_id)
        inventory = ResourceCatalog(configuration).inventory(workspace=workspace)
        self.assertEqual([first.store_id], [row["store_id"] for row in inventory["record_stores"]])
        self.assertEqual(str(self.root), inventory["record_stores"][0]["path"])
        self.assertEqual("service-jobs", inventory["record_stores"][0]["family"])
        with self.assertRaisesRegex(ServiceV3Error, "both configuration and workspace"):
            CoreServiceRecordBackend(
                self.root, physical_leases=self.leases,
                configuration_home=configuration,
            )


if __name__ == "__main__":
    unittest.main()
