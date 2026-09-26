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
        stage = "workbench-temporary-lease-v1:" + "a" * 32
        first = self.admit(stage_lease_id=stage)
        self.assertEqual(stage, first["stage_lease_id"])
        self.assertEqual(first, self.admit())
        with self.assertRaisesRegex(IdeToolchainAdmissionError, "differs"):
            self.admit(stage_lease_id="workbench-temporary-lease-v1:" + "b" * 32)

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
