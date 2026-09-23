"""Source-package capture is immutable and uses verified Git object bytes."""

from hashlib import sha1
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_axiom_target as targets
import axiom_sources


class AxiomTargetBuildTests(unittest.TestCase):
    def test_path_and_selection_boundaries(self):
        policy = {"prefixes": ["src/main/"], "paths": ["LICENSE"]}
        self.assertTrue(targets.selected("src/main/java/Example.java", policy))
        self.assertFalse(targets.selected("src/main-else/Example.java", policy))
        self.assertTrue(targets.selected("LICENSE", policy))
        for name in ("/etc/passwd", "a/../b", "a//b", "a\\b", "C:/a", "./a"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                targets.ordinary_path(name)

    def test_objects_validate_git_headers_and_bytes(self):
        raw = b"selected source"
        identifier = sha1(b"blob 15\0" + raw).hexdigest()
        output = f"{identifier} blob 15\n".encode() + raw + b"\n"
        with patch.object(targets, "git", return_value=output):
            self.assertEqual(("blob", raw), targets.objects(Path("unused"), [identifier])[identifier])
        with patch.object(targets, "git", return_value=output.replace(raw, b"tampered source")):
            with self.assertRaises(ValueError):
                targets.objects(Path("unused"), [identifier])

    def test_candidates_are_reproducible_and_never_clobber(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = {"manifest.json": b"{}", "blobs/" + "0" * 64: b"fixture"}
            first, second = root / "one.zip", root / "two.zip"
            targets.write_candidate(first, archive)
            targets.write_candidate(second, dict(reversed(list(archive.items()))))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with self.assertRaises(ValueError):
                targets.write_candidate(first, archive)
            with zipfile.ZipFile(first) as captured:
                self.assertEqual(set(archive), set(captured.namelist()))

    def test_candidate_symlinks_are_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keep = root / "keep"
            keep.write_bytes(b"keep")
            link = root / "link.zip"
            link.symlink_to(keep)
            with self.assertRaises(ValueError):
                targets.write_candidate(link, {"manifest.json": b"{}"})
            self.assertEqual(b"keep", keep.read_bytes())

    def test_source_provisioning_never_reuses_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "must be new"):
                axiom_sources.provision(Path(temporary))

    def test_source_provisioning_rejects_non_public_urls_before_creating_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new-sources"
            lock = {"repositories": [{"id": "repo", "commit": "a" * 40, "url": "ssh://private-host/repo"}]}
            with patch.object(axiom_sources.json, "loads", return_value=lock):
                with self.assertRaisesRegex(ValueError, "HTTPS"):
                    axiom_sources.provision(root)
            self.assertFalse(root.exists())
