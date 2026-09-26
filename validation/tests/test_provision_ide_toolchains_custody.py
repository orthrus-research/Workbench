"""IDE archive acquisition uses Core without erasing historical inputs."""

from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation"))
import provision_ide_toolchains as provision


class IdeArchiveCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "downloads"
        self.source = self.root / "locked.tar.gz"
        self.source.write_bytes(b"exact locked archive bytes")
        self.entry = {
            "archive_root": "locked-tool",
            "archive_url": self.source.as_uri(),
            "archive_sha256": sha256(self.source.read_bytes()).hexdigest(),
            "archive_size": self.source.stat().st_size,
        }
        selected = patch.object(provision, "DOWNLOAD_ROOT", self.cache)
        selected.start()
        self.addCleanup(selected.stop)

    def test_fetches_exact_bytes_through_core_and_reuses_cache(self) -> None:
        acquired = provision.download(self.entry, ".tar.gz")
        self.assertEqual(self.source.read_bytes(), acquired.read_bytes())
        self.assertEqual(self.cache / "artifacts/sha256" / self.entry["archive_sha256"], acquired)
        self.source.unlink()
        self.assertEqual(acquired, provision.download(self.entry, ".tar.gz"))

    def test_adopts_only_exact_historical_archive_without_network(self) -> None:
        self.cache.mkdir()
        legacy = self.cache / "locked-tool.tar.gz"
        legacy.write_bytes(self.source.read_bytes())
        self.entry["archive_url"] = "https://invalid.example/archive.tar.gz"
        acquired = provision.download(self.entry, ".tar.gz")
        self.assertNotEqual(legacy, acquired)
        self.assertEqual(legacy.read_bytes(), acquired.read_bytes())
        self.assertTrue(legacy.exists())

    def test_changed_historical_archive_is_retained_for_review(self) -> None:
        self.cache.mkdir()
        legacy = self.cache / "locked-tool.tar.gz"
        legacy.write_bytes(b"changed")
        with self.assertRaisesRegex(provision.ProvisionFailure, "retain for review"):
            provision.download(self.entry, ".tar.gz")
        self.assertEqual(b"changed", legacy.read_bytes())
        self.assertFalse((self.cache / "artifacts").exists())

    def test_wrong_locked_size_refuses_before_cache_publication(self) -> None:
        self.entry["archive_size"] += 1
        with self.assertRaisesRegex(provision.ProvisionFailure, "size mismatch"):
            provision.download(self.entry, ".tar.gz")
        self.assertFalse((self.cache / "artifacts/sha256" / self.entry["archive_sha256"]).exists())

    def test_redirected_historical_cache_root_refuses(self) -> None:
        real = self.root / "real"
        real.mkdir()
        self.cache.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(provision.ProvisionFailure, "traverses a redirect"):
            provision.download(self.entry, ".tar.gz")
        self.assertEqual([], list(real.iterdir()))


class IdeExtractionPreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.toolchains = self.root / "toolchains"
        self.archive = self.root / "locked.zip"
        self.archive.write_bytes(b"locked archive")
        self.entry = {
            "archive_root": "locked-tool",
            "archive_sha256": sha256(self.archive.read_bytes()).hexdigest(),
        }
        selected = patch.object(provision, "TOOLCHAIN_ROOT", self.toolchains)
        selected.start()
        self.addCleanup(selected.stop)

    def _extract(self, _archive: Path, temporary: Path) -> None:
        extracted = temporary / self.entry["archive_root"]
        extracted.mkdir()
        (extracted / "bin").write_bytes(b"exact installed bytes")

    def test_fresh_target_publishes_then_matching_marker_reuses_same_path(self) -> None:
        with patch.object(provision, "download", return_value=self.archive) as download:
            published = provision.provision_entry(
                self.entry, suffix=".zip", extractor=self._extract,
            )
            self.assertEqual(self.toolchains / "locked-tool", published)
            self.assertEqual(b"exact installed bytes", (published / "bin").read_bytes())
            self.assertEqual(self.entry["archive_sha256"] + "\n", (
                published / ".workbench-provisioned-sha256"
            ).read_text(encoding="ascii"))
            again = provision.provision_entry(
                self.entry, suffix=".zip", extractor=self._extract,
            )
            interrupted_tail = self.toolchains / "locked-tool.postmove"
            interrupted_tail.mkdir()
            after_interruption = provision.provision_entry(
                self.entry, suffix=".zip", extractor=self._extract,
            )
        self.assertEqual(published, again)
        self.assertEqual(published, after_interruption)
        self.assertTrue(interrupted_tail.is_dir())
        download.assert_called_once()

    def test_invalid_lock_root_refuses_before_download_or_stage(self) -> None:
        for archive_root, extracted_root in (
            ("../escape", None), ("locked-tool", "../escape"),
            ("locked/tool", None),
        ):
            with self.subTest(archive_root=archive_root, extracted_root=extracted_root):
                entry = {**self.entry, "archive_root": archive_root}
                with patch.object(provision, "download") as download:
                    with self.assertRaisesRegex(provision.ProvisionFailure, "invalid extraction root"):
                        provision.provision_entry(
                            entry, suffix=".zip", extractor=self._extract,
                            extracted_root=extracted_root,
                        )
                    download.assert_not_called()
        self.assertFalse(self.toolchains.exists())

    def test_changed_or_missing_marker_preserves_existing_tree_on_retries(self) -> None:
        for marker_bytes in (b"different\n", b"\xff", None):
            with self.subTest(marker_bytes=marker_bytes):
                destination = self.toolchains / self.entry["archive_root"]
                destination.mkdir(parents=True, exist_ok=True)
                sentinel = destination / "keep.bin"
                sentinel.write_bytes(b"historical installation")
                marker = destination / ".workbench-provisioned-sha256"
                if marker_bytes is None:
                    marker.unlink(missing_ok=True)
                else:
                    marker.write_bytes(marker_bytes)
                with patch.object(provision, "download") as download:
                    for _ in range(2):
                        with self.assertRaisesRegex(provision.ProvisionFailure, "retain for review"):
                            provision.provision_entry(
                                self.entry, suffix=".zip", extractor=self._extract,
                            )
                    download.assert_not_called()
                self.assertEqual(b"historical installation", sentinel.read_bytes())
                self.assertEqual(marker_bytes, marker.read_bytes() if marker.exists() else None)

    def test_redirected_destination_or_marker_refuses_without_replacing_tree(self) -> None:
        destination = self.toolchains / self.entry["archive_root"]
        real = self.root / "real"
        real.mkdir()
        (real / ".workbench-provisioned-sha256").write_text(
            self.entry["archive_sha256"] + "\n", encoding="ascii",
        )
        self.toolchains.mkdir()
        destination.symlink_to(real, target_is_directory=True)
        with patch.object(provision, "download") as download:
            with self.assertRaisesRegex(provision.ProvisionFailure, "redirected"):
                provision.provision_entry(self.entry, suffix=".zip", extractor=self._extract)
            download.assert_not_called()
        destination.unlink()
        destination.mkdir()
        marker = destination / ".workbench-provisioned-sha256"
        marker.symlink_to(real / ".workbench-provisioned-sha256")
        with patch.object(provision, "download") as download:
            with self.assertRaisesRegex(provision.ProvisionFailure, "retain for review"):
                provision.provision_entry(self.entry, suffix=".zip", extractor=self._extract)
            download.assert_not_called()
        self.assertTrue(marker.is_symlink())

        marker.unlink()
        source_marker = self.root / "shared-marker"
        source_marker.write_text(self.entry["archive_sha256"] + "\n", encoding="ascii")
        marker.hardlink_to(source_marker)
        with patch.object(provision, "download") as download:
            self.assertEqual(
                destination,
                provision.provision_entry(self.entry, suffix=".zip", extractor=self._extract),
            )
            download.assert_not_called()
        self.assertEqual(2, marker.stat().st_nlink)

    def test_destination_created_during_extraction_is_retained(self) -> None:
        destination = self.toolchains / self.entry["archive_root"]

        def competing_extraction(archive: Path, temporary: Path) -> None:
            self._extract(archive, temporary)
            destination.mkdir()
            (destination / "keep.bin").write_bytes(b"other publisher")

        with patch.object(provision, "download", return_value=self.archive):
            with self.assertRaisesRegex(provision.ProvisionFailure, "already exists"):
                provision.provision_entry(
                    self.entry, suffix=".zip", extractor=competing_extraction,
                )
        self.assertEqual(b"other publisher", (destination / "keep.bin").read_bytes())
        stages = list(self.toolchains.glob("locked-tool.*"))
        self.assertEqual(1, len(stages))
        self.assertEqual(
            (self.entry["archive_sha256"] + "\n").encode(),
            (stages[0] / "locked-tool/.workbench-provisioned-sha256").read_bytes(),
        )
        (destination / "keep.bin").unlink()
        destination.rmdir()
        with patch.object(provision, "download") as download:
            with self.assertRaisesRegex(provision.ProvisionFailure, "interrupted IDE toolchain extraction"):
                provision.provision_entry(
                    self.entry, suffix=".zip", extractor=self._extract,
                )
            download.assert_not_called()

    def test_preexisting_interrupted_stage_refuses_before_download(self) -> None:
        stage = self.toolchains / "locked-tool.partial123"
        stage.mkdir(parents=True)
        (stage / "partial.bin").write_bytes(b"historical residue")
        with patch.object(provision, "download") as download:
            with self.assertRaisesRegex(provision.ProvisionFailure, "interrupted IDE toolchain extraction"):
                provision.provision_entry(
                    self.entry, suffix=".zip", extractor=self._extract,
                )
            download.assert_not_called()
        self.assertEqual(b"historical residue", (stage / "partial.bin").read_bytes())

    def test_failed_extraction_retains_partial_stage_and_refuses_retry(self) -> None:
        def broken_extraction(_archive: Path, temporary: Path) -> None:
            (temporary / "partial.bin").write_bytes(b"unfinished")
            raise OSError("archive ended early")

        with patch.object(provision, "download", return_value=self.archive) as download:
            with self.assertRaisesRegex(provision.ProvisionFailure, "retain stage for review"):
                provision.provision_entry(
                    self.entry, suffix=".zip", extractor=broken_extraction,
                )
            stage, = self.toolchains.glob("locked-tool.*")
            self.assertEqual(b"unfinished", (stage / "partial.bin").read_bytes())
            with self.assertRaisesRegex(provision.ProvisionFailure, "interrupted IDE toolchain extraction"):
                provision.provision_entry(
                    self.entry, suffix=".zip", extractor=self._extract,
                )
            download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
