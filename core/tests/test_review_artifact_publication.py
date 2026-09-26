"""Core custody for an owner-encoded fresh review artifact."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from workbench_api.durable_resources import DurableResourceError
from workbench_api.record_stores import publish_review_artifact, record_store_scope
from workbench_core.storage.record_stores import CoreRecordStores


class ReviewArtifactPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.private = self.base / "private"
        self.private.mkdir(mode=0o700)
        self.provider = CoreRecordStores(
            workspace=self.workspace,
            configuration_home=self.base / "config",
            owner_id="workbench-shell",
        )

    def _publish(self, target: Path, data: bytes = b'{"plan":1}\n'):
        with record_store_scope(self.provider):
            return publish_review_artifact(
                "cleanroom-construction-plan-v2", target, data,
                byte_limit=16 * 1024 * 1024,
            )

    def test_core_creates_private_child_and_retains_exact_plan_store(self) -> None:
        target = self.private / "nested" / "plan.json"
        reference = self._publish(target)

        self.assertEqual(target.read_bytes(), b'{"plan":1}\n')
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertEqual(target.stat().st_mode & 0o077, 0)
        self.assertEqual(reference.root, target.parent)
        self.assertEqual(reference.retention, "protected-until-reviewed-policy")
        rows = self.provider.catalog.inventory(workspace=self.workspace)["record_stores"]
        self.assertEqual([row["store_id"] for row in rows], [reference.store_id])

    def test_shared_parent_refuses_without_changing_permissions_or_creating_child(self) -> None:
        shared = self.base / "shared"
        shared.mkdir(mode=0o755)
        target = shared / "nested" / "plan.json"
        with self.assertRaises(DurableResourceError) as refused:
            self._publish(target)
        self.assertEqual(refused.exception.code, "resource.unsafe")
        self.assertEqual(shared.stat().st_mode & 0o777, 0o755)
        self.assertFalse(target.parent.exists())

    def test_existing_plan_and_interrupted_stage_are_never_adopted(self) -> None:
        target = self.private / "plan.json"
        target.write_bytes(b"old plan\n")
        target.chmod(0o600)
        with self.assertRaises(OSError):
            self._publish(target)
        self.assertEqual(target.read_bytes(), b"old plan\n")

        target.unlink()
        stage = self.private / f".{target.name}.deadbeef.tmp"
        stage.write_bytes(b"interrupted\n")
        stage.chmod(0o600)
        with self.assertRaises(DurableResourceError) as refused:
            self._publish(target)
        self.assertEqual(refused.exception.code, "resource.incomplete")
        self.assertFalse(target.exists())
        self.assertEqual(stage.read_bytes(), b"interrupted\n")

    def test_redirected_parent_refuses(self) -> None:
        redirect = self.base / "redirect"
        os.symlink(self.private, redirect)
        with self.assertRaises(DurableResourceError) as refused:
            self._publish(redirect / "plan.json")
        self.assertEqual(refused.exception.code, "resource.unsafe")
        self.assertFalse((self.private / "plan.json").exists())


if __name__ == "__main__":
    unittest.main()
