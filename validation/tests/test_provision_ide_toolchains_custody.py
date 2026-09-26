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


if __name__ == "__main__":
    unittest.main()
