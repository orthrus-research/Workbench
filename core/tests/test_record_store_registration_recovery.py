"""Conservative recovery of an interrupted Core record-store registration."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workbench_api import DurableResourceError
from workbench_core.storage.registered import ResourceCatalog


class RecordStoreRegistrationRecoveryTests(unittest.TestCase):
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

    def _target(self) -> Path:
        digest, _, _, _ = self.catalog._record_store_registration(**self.identity)
        return self.catalog.root / "stores" / f"{digest}.json"

    def _stage(self, suffix: str = "abcdefgh") -> Path:
        target = self._target()
        return target.with_name(f".{target.name}.{suffix}")

    def _assert_refused_without_removal(self, stage: Path, *, code: str = "resource.changed") -> None:
        target = self._target()
        before_stage = stage.lstat()
        before_target = target.lstat() if target.exists() else None
        with self.assertRaises(DurableResourceError) as refused:
            self.catalog.reconcile_interrupted_record_store_registration(**self.identity)
        self.assertEqual(refused.exception.code, code)
        self.assertEqual((stage.lstat().st_dev, stage.lstat().st_ino), (before_stage.st_dev, before_stage.st_ino))
        if before_target is None:
            self.assertFalse(target.exists())
        else:
            self.assertEqual(
                (target.lstat().st_dev, target.lstat().st_ino),
                (before_target.st_dev, before_target.st_ino),
            )
        self.assertEqual(self.catalog.verify_root(), "ready-unproven")

    def test_hard_exit_after_link_recovers_exact_registration_and_retains_root_uncertainty(self) -> None:
        self.catalog._ensure()
        root_manifest = self.catalog._root_manifest().read_bytes()
        repository = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(repository / "api/src"), str(repository / "core/src"),
            environment.get("PYTHONPATH", ""),
        ))
        code = """
import os
from pathlib import Path
from workbench_core import durable_records
from workbench_core.storage.registered import ResourceCatalog

real_link = os.link
def exit_after_link(source, target, *args, **kwargs):
    real_link(source, target, *args, **kwargs)
    os._exit(43)
durable_records.os.link = exit_after_link
ResourceCatalog(Path(__import__('sys').argv[1])).register_record_store(
    family='sample-records', owner_id='sample',
    workspace=Path(__import__('sys').argv[2]), root=Path(__import__('sys').argv[3]),
)
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.catalog.configuration_home),
             str(self.workspace), str(self.root)],
            env=environment, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 43, result.stderr)
        target = self._target()
        stages = list(target.parent.glob(f".{target.name}.*"))
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertEqual((target.stat().st_dev, target.stat().st_ino), (stage.stat().st_dev, stage.stat().st_ino))
        self.assertEqual(target.stat().st_nlink, 2)
        original = target.read_bytes()
        with self.assertRaises(DurableResourceError) as inventory:
            self.catalog.inventory(workspace=self.workspace)
        self.assertEqual(inventory.exception.code, "resource.changed")

        _, store_id, _, _ = self.catalog._record_store_registration(**self.identity)
        self.assertEqual(
            self.catalog.reconcile_interrupted_record_store_registration(**self.identity), store_id,
        )
        self.assertFalse(stage.exists())
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertEqual(self.catalog._root_manifest().read_bytes(), root_manifest)
        inventory = self.catalog.inventory(workspace=self.workspace)
        self.assertEqual(inventory["root_state"], "ready-unproven")
        self.assertEqual([row["store_id"] for row in inventory["record_stores"]], [store_id])
        self.assertEqual(self.catalog.reconcile_interrupted_record_store_registration(**self.identity), store_id)

    def test_reconcile_refuses_unpublished_stage(self) -> None:
        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage()
        os.link(target, stage)
        target.unlink()
        self._assert_refused_without_removal(stage, code="resource.changed")

    def test_reconcile_refuses_later_replacement_even_with_identical_bytes(self) -> None:
        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage()
        os.link(target, stage)
        original = target.read_bytes()
        target.unlink()
        target.write_bytes(original)
        target.chmod(0o600)
        self._assert_refused_without_removal(stage)
        self.assertEqual(target.read_bytes(), original)

    def test_reconcile_refuses_extra_link_or_stage(self) -> None:
        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage()
        os.link(target, stage)
        extra = self._stage("ijklmnop")
        os.link(target, extra)
        self._assert_refused_without_removal(stage)
        self.assertTrue(extra.exists())

        extra.unlink()
        third_link = self.home / "third-link"
        os.link(target, third_link)
        self._assert_refused_without_removal(stage)
        self.assertTrue(third_link.exists())

    def test_reconcile_refuses_unrecognized_stage_name(self) -> None:
        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage("unrecognized")
        os.link(target, stage)
        self._assert_refused_without_removal(stage)

    def test_reconcile_refuses_changed_payload_and_symlink_stage(self) -> None:
        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage()
        os.link(target, stage)
        stage.write_bytes(b"changed\n")
        self._assert_refused_without_removal(stage)

        stage.unlink()
        stage.symlink_to(target.name)
        self._assert_refused_without_removal(stage)

    def test_reconcile_requires_exact_identity_and_bound_catalog(self) -> None:
        with self.assertRaises(DurableResourceError) as empty:
            self.catalog.reconcile_interrupted_record_store_registration(**self.identity)
        self.assertEqual(empty.exception.code, "resource.unavailable")
        self.assertFalse(self.catalog.root.exists())

        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage()
        os.link(target, stage)
        other = self.home / "other-workspace"
        other.mkdir()
        with self.assertRaises(DurableResourceError):
            self.catalog.reconcile_interrupted_record_store_registration(
                **{**self.identity, "workspace": other},
            )
        self.assertTrue(stage.exists())
        self.assertTrue(target.exists())

    def test_reconcile_refuses_redirected_store_root(self) -> None:
        self.catalog.register_record_store(**self.identity)
        target, stage = self._target(), self._stage()
        os.link(target, stage)
        moved = self.home / "moved-records"
        self.root.rename(moved)
        self.root.symlink_to(moved, target_is_directory=True)
        self._assert_refused_without_removal(stage, code="resource.unsafe")

    def test_no_stage_idempotence_requires_exact_original_bytes(self) -> None:
        store_id = self.catalog.register_record_store(**self.identity)
        self.assertEqual(self.catalog.reconcile_interrupted_record_store_registration(**self.identity), store_id)
        target = self._target()
        target.write_bytes(target.read_bytes() + b"\n")
        with self.assertRaises(DurableResourceError) as changed:
            self.catalog.reconcile_interrupted_record_store_registration(**self.identity)
        self.assertEqual(changed.exception.code, "resource.changed")


if __name__ == "__main__":
    unittest.main()
