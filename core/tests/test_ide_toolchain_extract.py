"""Locked IDE archives are extracted only inside an active Core stage."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import stat
import tarfile
from tempfile import TemporaryDirectory
import unittest
import zipfile

from workbench_core.ide_toolchain_extract import (
    IdeToolchainExtractionError, extract_locked_ide_archive,
)
from workbench_core.temporary_leases import CoreTemporaryLeases


class IdeToolchainExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.host = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.home / "config",
            locations={"ide-toolchain": self.home / "toolchains"},
            owner_id="validation",
        )
        self.stage = self.host.allocate("ide-toolchain", "locked-stage")
        self.archive = self.home / "locked.zip"
        with zipfile.ZipFile(self.archive, "w") as bundle:
            member = zipfile.ZipInfo("locked/bin/tool")
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(member, b"exact tool")
        self.digest = sha256(self.archive.read_bytes()).hexdigest()

    def extract(self, *, digest: str | None = None) -> Path:
        return extract_locked_ide_archive(
            self.host, self.stage, self.archive,
            archive_sha256=digest or self.digest,
            archive_size=self.archive.stat().st_size,
            expected_root="locked", archive_format="zip",
        )

    def test_core_extracts_exact_zip_under_active_stage_and_retains_marker(self) -> None:
        with self.host.execution(self.stage):
            target = self.extract()
            self.assertEqual(b"exact tool", (target / "bin/tool").read_bytes())
            self.assertEqual(
                {".workbench-temporary-lease.json", "locked"},
                {path.name for path in self.stage.path.iterdir()},
            )
            self.host.retain(self.stage, outcome="completed")
        self.assertTrue((self.stage.path / ".workbench-temporary-retained.json").is_file())

    def test_wrong_archive_digest_or_nonempty_stage_refuses_without_erasing(self) -> None:
        with self.host.execution(self.stage):
            with self.assertRaisesRegex(IdeToolchainExtractionError, "bytes differ"):
                self.extract(digest="0" * 64)
            self.assertEqual(
                [".workbench-temporary-lease.json"],
                sorted(path.name for path in self.stage.path.iterdir()),
            )
            (self.stage.path / "keep.bin").write_bytes(b"retain")
            with self.assertRaisesRegex(IdeToolchainExtractionError, "not empty"):
                self.extract()
            self.assertEqual(b"retain", (self.stage.path / "keep.bin").read_bytes())

    def test_archive_escape_is_refused_before_extraction(self) -> None:
        unsafe = self.home / "unsafe.tar.gz"
        with tarfile.open(unsafe, "w:gz") as bundle:
            member = tarfile.TarInfo("locked/outside")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../escape"
            bundle.addfile(member)
        with self.host.execution(self.stage):
            with self.assertRaisesRegex(IdeToolchainExtractionError, "link escapes"):
                extract_locked_ide_archive(
                    self.host, self.stage, unsafe,
                    archive_sha256=sha256(unsafe.read_bytes()).hexdigest(),
                    archive_size=unsafe.stat().st_size,
                    expected_root="locked", archive_format="tar",
                )
        self.assertFalse((self.stage.path / "locked").exists())
        self.assertFalse((self.home / "escape").exists())

    def test_relative_tar_link_is_preserved_under_core_stage(self) -> None:
        archive = self.home / "safe.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for name in ("jdk-25.0.4+7", "jdk-25.0.4+7/bin"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
                bundle.addfile(member)
            member = tarfile.TarInfo("jdk-25.0.4+7/bin/java")
            member.mode = 0o555
            member.size = len(b"exact java")
            bundle.addfile(member, BytesIO(b"exact java"))
            member = tarfile.TarInfo("jdk-25.0.4+7/bin/current")
            member.type = tarfile.SYMTYPE
            member.linkname = "java"
            bundle.addfile(member)
        with self.host.execution(self.stage):
            extracted = extract_locked_ide_archive(
                self.host, self.stage, archive,
                archive_sha256=sha256(archive.read_bytes()).hexdigest(),
                archive_size=archive.stat().st_size,
                expected_root="jdk-25.0.4+7", archive_format="tar",
            )
            self.assertEqual(b"exact java", (extracted / "bin/java").read_bytes())
            self.assertEqual("java", (extracted / "bin/current").readlink().as_posix())
            self.host.retain(self.stage, outcome="completed")

    def test_lease_must_be_active_before_member_creation(self) -> None:
        with self.assertRaisesRegex(IdeToolchainExtractionError, "active Core lease"):
            self.extract()
        self.assertEqual(
            [".workbench-temporary-lease.json"],
            sorted(path.name for path in self.stage.path.iterdir()),
        )


if __name__ == "__main__":
    unittest.main()
