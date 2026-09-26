"""Exact readback for historical IDE toolchain trees."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest

from workbench_core.ide_toolchain_reader import IdeToolchainReadError, verify_ide_toolchain_tree


class IdeToolchainReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "locked.tar.gz"
        self.tree = self.root / "locked"

    def _archive(self, *, link: str = "tool") -> tuple[str, int]:
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name in ("locked", "locked/bin"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                member.mode = 0o755
                bundle.addfile(member)
            member = tarfile.TarInfo("locked/bin/tool")
            member.size = len(b"exact executable")
            member.mode = 0o555
            bundle.addfile(member, BytesIO(b"exact executable"))
            member = tarfile.TarInfo("locked/bin/current")
            member.type = tarfile.SYMTYPE
            member.linkname = link
            bundle.addfile(member)
        return sha256(self.archive.read_bytes()).hexdigest(), self.archive.stat().st_size

    def _tree(self, digest: str) -> None:
        (self.tree / "bin").mkdir(parents=True)
        (self.tree / "bin/tool").write_bytes(b"exact executable")
        (self.tree / "bin/tool").chmod(0o755)
        (self.tree / "bin/current").symlink_to("tool")
        (self.tree / ".workbench-provisioned-sha256").write_text(digest + "\n", encoding="ascii")

    def _verify(self, digest: str, size: int) -> int:
        return verify_ide_toolchain_tree(
            self.archive, self.tree, archive_sha256=digest,
            archive_size=size, expected_root="locked", archive_format="tar",
        )

    def test_relative_link_and_exact_bytes_pass_then_change_refuses(self) -> None:
        digest, size = self._archive()
        self._tree(digest)
        self.assertEqual(1, self._verify(digest, size))
        (self.tree / "bin/tool").write_bytes(b"other executable")
        with self.assertRaisesRegex(IdeToolchainReadError, "bytes differ"):
            self._verify(digest, size)
        self.assertEqual(b"other executable", (self.tree / "bin/tool").read_bytes())

    def test_changed_link_and_extra_member_refuse_without_cleanup(self) -> None:
        digest, size = self._archive()
        self._tree(digest)
        (self.tree / "bin/current").unlink()
        (self.tree / "bin/current").symlink_to("../tool")
        with self.assertRaisesRegex(IdeToolchainReadError, "link differs"):
            self._verify(digest, size)
        (self.tree / "bin/current").unlink()
        (self.tree / "bin/current").symlink_to("tool")
        (self.tree / "unclaimed").write_bytes(b"retain")
        with self.assertRaisesRegex(IdeToolchainReadError, "extra members"):
            self._verify(digest, size)
        self.assertEqual(b"retain", (self.tree / "unclaimed").read_bytes())

    def test_archive_link_escape_refuses_even_if_marker_matches(self) -> None:
        digest, size = self._archive(link="../../outside")
        self._tree(digest)
        with self.assertRaisesRegex(IdeToolchainReadError, "link escapes"):
            self._verify(digest, size)


if __name__ == "__main__":
    unittest.main()
