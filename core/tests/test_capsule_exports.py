from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api.capsule_exports import CapsuleExportError
from workbench_core.capsule_exports import CoreCapsuleExports
from workbench_core.storage.registered import ResourceCatalog


CAPSULE_ID = "workbench-reproduction-capsule:sha256:" + "a" * 64


class CoreCapsuleExportsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.config = self.root / "config"
        self.output_parent = self.root / "shared capsules"
        self.output_parent.mkdir()
        self.host = CoreCapsuleExports(
            workspace=self.workspace, configuration_home=self.config,
        )

    def test_publishes_selected_spaced_name_at_exact_parent_with_core_readback(self) -> None:
        target = self.output_parent / "failed server.wb-repro"
        before = self.output_parent.stat()
        data = b"reviewed capsule bytes"
        self.host.publish(target=target, data=data, capsule_id=CAPSULE_ID)
        self.assertEqual(target.read_bytes(), data)
        after = self.output_parent.stat()
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertTrue(self.host.cataloged(target=target))
        self.host.verify(
            target=target, capsule_id=CAPSULE_ID,
            size=len(data), sha256=sha256(data).hexdigest(),
        )
        rows = self.host.catalog.inventory()["resources"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], str(target))
        self.assertEqual(rows[0]["store_root"], str(self.output_parent))
        self.assertEqual(rows[0]["role_source"], "explicit-capsule-export")
        self.assertEqual(rows[0]["status"], "committed")

    def test_existing_target_and_historical_stage_refuse_without_replacement(self) -> None:
        target = self.output_parent / "existing.wb-repro"
        target.write_bytes(b"earlier capsule")
        with self.assertRaisesRegex(CapsuleExportError, "already exists"):
            self.host.publish(target=target, data=b"new", capsule_id=CAPSULE_ID)
        self.assertEqual(target.read_bytes(), b"earlier capsule")

        staged_target = self.output_parent / "staged.wb-repro"
        stage = self.output_parent / ".staged.wb-repro.tmp"
        stage.write_bytes(b"interrupted")
        with self.assertRaisesRegex(CapsuleExportError, "temporary output requires review"):
            self.host.publish(target=staged_target, data=b"new", capsule_id=CAPSULE_ID)
        self.assertFalse(staged_target.exists())
        self.assertEqual(stage.read_bytes(), b"interrupted")

    def test_unsafe_explicit_names_refuse_without_a_resource(self) -> None:
        for name in (
            "trailing.", "trailing ", "bad:name", "bad\\name", "bad\x01name",
            "bad\x80name", "bad\u202ename", "CON.wb-repro", "CON .wb-repro",
            "a" * 256,
        ):
            with self.subTest(name=name):
                with self.assertRaises(CapsuleExportError):
                    self.host.publish(
                        target=self.output_parent / name,
                        data=b"reviewed", capsule_id=CAPSULE_ID,
                    )
        self.assertEqual(self.host.catalog.inventory()["resources"], [])

    def test_interrupted_core_attempt_is_ambiguous_and_cannot_be_reused(self) -> None:
        target = self.output_parent / "interrupted.wb-repro"
        original = ResourceCatalog._write

        def interrupt_commit(catalog, category, *args):
            if category == "commits":
                raise OSError("simulated commit interruption")
            return original(catalog, category, *args)

        with patch.object(ResourceCatalog, "_write", interrupt_commit):
            with self.assertRaises(CapsuleExportError):
                self.host.publish(target=target, data=b"reviewed", capsule_id=CAPSULE_ID)
        self.assertEqual(target.read_bytes(), b"reviewed")
        with self.assertRaisesRegex(CapsuleExportError, "earlier Core attempt"):
            self.host.publish(target=target, data=b"reviewed", capsule_id=CAPSULE_ID)
        with self.assertRaisesRegex(CapsuleExportError, "single committed Core resource"):
            self.host.verify(
                target=target, capsule_id=CAPSULE_ID,
                size=8, sha256=sha256(b"reviewed").hexdigest(),
            )
        self.assertTrue(any(
            path.name.startswith(".workbench-resource-") and path.name.endswith(".pending")
            for path in self.output_parent.iterdir()
        ))

    def test_changed_bytes_or_different_workspace_fail_readback(self) -> None:
        target = self.output_parent / "reviewed.wb-repro"
        self.host.publish(target=target, data=b"reviewed", capsule_id=CAPSULE_ID)
        other_workspace = self.root / "other-workspace"
        other_workspace.mkdir()
        other = CoreCapsuleExports(
            workspace=other_workspace, configuration_home=self.config,
        )
        with self.assertRaises(CapsuleExportError):
            other.verify(
                target=target, capsule_id=CAPSULE_ID,
                size=8, sha256=sha256(b"reviewed").hexdigest(),
            )
        target.write_bytes(b"tampered")
        with self.assertRaises(CapsuleExportError):
            self.host.verify(
                target=target, capsule_id=CAPSULE_ID,
                size=8, sha256=sha256(b"reviewed").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
