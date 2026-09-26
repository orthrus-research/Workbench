"""An IDE tree admission is exact, immutable and dependent on readback."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from workbench_core.ide_toolchain_admissions import (
    CoreIdeToolchainAdmissions, IdeToolchainAdmissionError,
)
from workbench_core.storage.registered import ResourceCatalog
from workbench_core.temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


class IdeToolchainAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.root = self.home / "toolchains/ide-validation-v1"
        self.root.mkdir(parents=True)
        self.archive = self.home / "locked.zip"
        with zipfile.ZipFile(self.archive, "w") as bundle:
            member = zipfile.ZipInfo("locked-tool/bin")
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(member, b"exact bytes")
        self.digest = sha256(self.archive.read_bytes()).hexdigest()
        self.target = self.root / "locked-tool"
        self.target.mkdir()
        (self.target / "bin").write_bytes(b"exact bytes")
        (self.target / ".workbench-provisioned-sha256").write_text(
            self.digest + "\n", encoding="ascii",
        )
        self.host = CoreIdeToolchainAdmissions(self.root)

    def admit(self, *, stage_lease_id: str | None = None) -> dict:
        return self.host.admit(
            self.archive, self.target, archive_sha256=self.digest,
            archive_size=self.archive.stat().st_size,
            expected_root="locked-tool", archive_format="zip",
            stage_lease_id=stage_lease_id,
        )

    def record(self) -> Path:
        records = list((self.home / "toolchains/.ide-toolchain-core/admissions-v1").glob("*.json"))
        self.assertEqual(1, len(records))
        return records[0]

    def test_historical_tree_admission_is_registered_and_stable_on_reopen(self) -> None:
        first = self.admit()
        path = self.record()
        raw = path.read_bytes()
        reopened = CoreIdeToolchainAdmissions(self.root).admit(
            self.archive, self.target, archive_sha256=self.digest,
            archive_size=self.archive.stat().st_size,
            expected_root="locked-tool", archive_format="zip",
        )
        self.assertEqual(first, reopened)
        self.assertEqual(raw, path.read_bytes())
        self.assertEqual(self.target.stat().st_ino, first["target_inode"])
        self.assertEqual(1, first["regular_files"])
        self.assertRegex(first["members_sha256"], r"^[0-9a-f]{64}$")
        inventory = ResourceCatalog(self.host.configuration_home).inventory(
            workspace=self.host.workspace,
        )
        self.assertEqual("ready-unproven", inventory["root_state"])
        self.assertIn("validation-ide-toolchain-admissions-v1", {
            row["family"] for row in inventory["record_stores"]
        })
        self.assertEqual(1, len(inventory["ide_toolchain_admissions"]))
        self.assertEqual("catalog-only", inventory["ide_toolchain_admissions"][0]["status"])
        self.assertEqual("present", inventory["ide_toolchain_admissions"][0]["parent_store_registration"])

    def test_original_stage_provenance_survives_later_reuse(self) -> None:
        stages = CoreTemporaryLeases(
            workspace=self.host.workspace,
            configuration_home=self.host.configuration_home,
            locations={"ide-toolchain": self.root}, owner_id="validation",
        )
        stage = stages.allocate("ide-toolchain", f"ide-{self.digest}-source")
        with stages.execution(stage):
            first = self.admit(stage_lease_id=stage.lease_id)
            stages.retain(stage, outcome="completed")
            with self.assertRaisesRegex(TemporaryLeaseError, "IDE toolchain source stage remains retained"):
                stages.dispose(stage, drained=lambda: True)
        self.assertEqual(stage.lease_id, first["stage_lease_id"])
        self.assertEqual(first, self.admit())
        with self.assertRaisesRegex(IdeToolchainAdmissionError, "differs"):
            self.admit(stage_lease_id="workbench-temporary-lease-v1:" + "b" * 32)

    def test_lost_admission_with_retained_stage_cannot_be_reborn_as_historical(self) -> None:
        stages = CoreTemporaryLeases(
            workspace=self.host.workspace,
            configuration_home=self.host.configuration_home,
            locations={"ide-toolchain": self.root}, owner_id="validation",
        )
        stage = stages.allocate("ide-toolchain", f"ide-{self.digest}-source")
        with stages.execution(stage):
            self.admit(stage_lease_id=stage.lease_id)
            stages.retain(stage, outcome="completed")
        record = self.record()
        held = self.home / "held-admission.json"
        record.rename(held)
        try:
            with self.assertRaisesRegex(IdeToolchainAdmissionError, "missing IDE admission"):
                self.admit()
            self.assertFalse(record.exists())
            self.assertTrue(self.target.is_dir())
        finally:
            held.rename(record)

    def test_retained_source_marker_change_refuses_exact_reuse(self) -> None:
        stages = CoreTemporaryLeases(
            workspace=self.host.workspace,
            configuration_home=self.host.configuration_home,
            locations={"ide-toolchain": self.root}, owner_id="validation",
        )
        stage = stages.allocate("ide-toolchain", f"ide-{self.digest}-source")
        with stages.execution(stage):
            self.admit(stage_lease_id=stage.lease_id)
            stages.retain(stage, outcome="completed")
        marker = stage.path / ".workbench-temporary-lease.json"
        retained = marker.read_bytes()
        marker.write_bytes(b"changed\n")
        try:
            with self.assertRaisesRegex(IdeToolchainAdmissionError, "source stage changed"):
                self.admit()
            self.assertTrue(self.target.is_dir())
        finally:
            marker.write_bytes(retained)

    def test_changed_bytes_and_replaced_root_refuse_without_rewriting_admission(self) -> None:
        self.admit()
        path = self.record()
        raw = path.read_bytes()
        (self.target / "bin").write_bytes(b"changed")
        with self.assertRaises(IdeToolchainAdmissionError):
            self.admit()
        self.assertEqual(raw, path.read_bytes())
        (self.target / "bin").write_bytes(b"exact bytes")
        displaced = self.root / "displaced"
        self.target.rename(displaced)
        self.target.mkdir()
        (self.target / "bin").write_bytes(b"exact bytes")
        (self.target / ".workbench-provisioned-sha256").write_text(
            self.digest + "\n", encoding="ascii",
        )
        with self.assertRaisesRegex(IdeToolchainAdmissionError, "differs"):
            self.admit()
        self.assertEqual(raw, path.read_bytes())
        self.assertTrue(displaced.is_dir())

    def test_held_admission_fences_another_client_and_rechecks_after_use(self) -> None:
        self.admit()
        arguments = dict(
            archive_sha256=self.digest, archive_size=self.archive.stat().st_size,
            expected_root="locked-tool", archive_format="zip",
        )
        with self.host.hold(self.archive, self.target, **arguments) as selected:
            self.assertEqual(self.target, selected)
            with self.assertRaisesRegex(IdeToolchainAdmissionError, "held by another writer"):
                with CoreIdeToolchainAdmissions(self.root).hold(
                    self.archive, self.target, **arguments,
                ):
                    self.fail("a second hold crossed the Core admission fence")
        with self.assertRaisesRegex(IdeToolchainAdmissionError, "differs from archive"):
            with self.host.hold(self.archive, self.target, **arguments):
                (self.target / "bin").write_bytes(b"changed")
        self.assertTrue(self.record().exists())

    def test_held_admission_refuses_a_missing_record_without_recreating_it(self) -> None:
        self.admit()
        record = self.record()
        held = self.home / "held-admission.json"
        record.rename(held)
        try:
            with self.assertRaises(IdeToolchainAdmissionError):
                with self.host.hold(
                    self.archive, self.target, archive_sha256=self.digest,
                    archive_size=self.archive.stat().st_size,
                    expected_root="locked-tool", archive_format="zip",
                ):
                    self.fail("a missing admission was reused")
            self.assertFalse(record.exists())
        finally:
            held.rename(record)

    def test_interrupted_record_publication_retains_tree_for_exact_retry(self) -> None:
        with patch(
            "workbench_core.ide_toolchain_admissions.publish_immutable_bytes",
            side_effect=RuntimeError("after readback"),
        ):
            with self.assertRaisesRegex(RuntimeError, "after readback"):
                self.admit()
        self.assertEqual(b"exact bytes", (self.target / "bin").read_bytes())
        self.assertEqual([], list((self.home / "toolchains/.ide-toolchain-core/admissions-v1").glob("*.json")))
        self.assertEqual(1, self.admit()["regular_files"])

    def test_changed_record_and_invalid_stage_are_refused(self) -> None:
        with self.assertRaisesRegex(IdeToolchainAdmissionError, "policy"):
            self.admit(stage_lease_id="unrecognized-stage")
        self.admit()
        path = self.record()
        path.write_bytes(b"{}\n")
        with self.assertRaisesRegex(IdeToolchainAdmissionError, "changed"):
            self.admit()
        self.assertTrue(self.target.is_dir())

    def test_catalog_shows_interrupted_record_stage_without_adopting_it(self) -> None:
        self.admit()
        record = self.record()
        stage = record.parent / f".{record.name}.abcdefgh"
        stage.write_bytes(b"interrupted")
        stage.chmod(0o600)
        rows = ResourceCatalog(self.host.configuration_home).inventory(
            workspace=self.host.workspace,
        )["ide_toolchain_admissions"]
        self.assertEqual(["unbound-record-stage", "catalog-only"], [row["status"] for row in rows])
        self.assertTrue(stage.exists())

    def test_catalog_refuses_admission_with_missing_parent_registration(self) -> None:
        self.admit()
        catalog = ResourceCatalog(self.host.configuration_home)
        registration, = (catalog.root / "stores").glob("*.json")
        displaced = self.home / "held-registration.json"
        registration.rename(displaced)
        try:
            with self.assertRaisesRegex(Exception, "IDE admission catalog is unavailable"):
                catalog.inventory(workspace=self.host.workspace)
            self.assertTrue(self.record().exists())
        finally:
            displaced.rename(registration)


if __name__ == "__main__":
    unittest.main()
