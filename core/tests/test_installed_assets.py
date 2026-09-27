"""An installed client can find its retained, byte-verified Axiom engine."""

from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_core.cli import _dispatch
from workbench_core.installed_assets import resolve_axiom_engine_source


class InstalledAxiomEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workbench-installed-engine-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / "installs" / "test-v1"
        self.install.mkdir(parents=True)
        self.bundle = self.root / "bundles" / "test-v1"
        self.archive = self.bundle / "axiom" / "workbench-axiom-engine-0.1.0.zip"
        self.archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.archive, "w") as output:
            output.writestr("engine.txt", "fixture")
        self.digest = sha256(self.archive.read_bytes()).hexdigest()
        wheelhouse = self.bundle / "wheelhouse" / "wheelhouse.json"
        wheelhouse.parent.mkdir()
        wheelhouse_record = {"format": "workbench-native-wheelhouse-v1", "wheels": []}
        wheelhouse.write_text(json.dumps(wheelhouse_record, indent=2))
        self.wheelhouse_digest = sha256(wheelhouse.read_bytes()).hexdigest()
        self.installed_digest = sha256(json.dumps(
            wheelhouse_record, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        self.assertNotEqual(self.wheelhouse_digest, self.installed_digest)
        (self.install / "workbench-hook.json").write_text(json.dumps({
            "format": "workbench-hook-install-v1", "state": "installed",
            "release_tag": "test-v1", "archive_sha256": "b" * 64,
            "python_runtime_id": "test-runtime",
        }))
        (self.install / "workbench-install.json").write_text(json.dumps({
            "format": "workbench-native-install-v1", "state": "installed",
            "wheelhouse_manifest_sha256": self.installed_digest,
        }))
        self.manifest = {
            "format": "workbench-install-bundle-v2", "edition": "supersymmetry-client",
            "state": "assembled", "release_tag": "test-v1",
            "wheelhouse_manifest_sha256": self.wheelhouse_digest,
            "engine_archive": "axiom/" + self.archive.name,
            "files": [
                {"path": "wheelhouse/wheelhouse.json", "size": wheelhouse.stat().st_size,
                 "sha256": self.wheelhouse_digest},
                {"path": "axiom/" + self.archive.name,
                 "size": self.archive.stat().st_size, "sha256": self.digest},
            ],
        }
        self._write_manifest()

    def _write_manifest(self) -> None:
        (self.bundle / "BUNDLE-MANIFEST.json").write_text(json.dumps(self.manifest))

    def test_verifies_exact_installed_engine_and_cli_json(self) -> None:
        result = resolve_axiom_engine_source(installation=self.install)
        self.assertEqual("verified", result["state"])
        self.assertEqual(str(self.archive), result["archive_path"])
        self.assertEqual(self.digest, result["sha256"])
        with patch("sys.prefix", str(self.install)), redirect_stdout(StringIO()) as output:
            code = _dispatch(["installed", "axiom-engine", "--json"], self.root)
        self.assertEqual(0, code)
        self.assertEqual(result, json.loads(output.getvalue()))

    def test_tampered_or_missing_engine_is_never_offered(self) -> None:
        self.archive.write_bytes(self.archive.read_bytes() + b"changed")
        result = resolve_axiom_engine_source(installation=self.install)
        self.assertEqual("unavailable", result["state"])
        self.assertIsNone(result["archive_path"])

    def test_bundle_and_install_receipts_must_match(self) -> None:
        self.manifest["wheelhouse_manifest_sha256"] = "c" * 64
        self._write_manifest()
        self.assertEqual("unavailable", resolve_axiom_engine_source(installation=self.install)["state"])
        self.manifest["wheelhouse_manifest_sha256"] = self.wheelhouse_digest
        self.manifest["engine_archive"] = "axiom/other.zip"
        self._write_manifest()
        self.assertEqual("unavailable", resolve_axiom_engine_source(installation=self.install)["state"])

    def test_wheelhouse_bytes_and_installed_identity_must_match(self) -> None:
        wheelhouse = self.bundle / "wheelhouse" / "wheelhouse.json"
        original = wheelhouse.read_bytes()
        wheelhouse.write_bytes(original + b" ")
        self.assertEqual("unavailable", resolve_axiom_engine_source(installation=self.install)["state"])
        wheelhouse.write_bytes(original)
        receipt = self.install / "workbench-install.json"
        record = json.loads(receipt.read_text())
        record["wheelhouse_manifest_sha256"] = self.wheelhouse_digest
        receipt.write_text(json.dumps(record))
        self.assertEqual("unavailable", resolve_axiom_engine_source(installation=self.install)["state"])

    def test_link_and_unsafe_manifest_path_are_rejected(self) -> None:
        other = self.root / "other.zip"
        self.archive.rename(other)
        self.archive.symlink_to(other)
        self.assertEqual("unavailable", resolve_axiom_engine_source(installation=self.install)["state"])
        self.archive.unlink()
        other.rename(self.archive)
        self.manifest["files"][1]["path"] = "axiom/../outside.zip"
        self._write_manifest()
        self.assertEqual("unavailable", resolve_axiom_engine_source(installation=self.install)["state"])

    def test_wheelhouse_only_install_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workbench-wheel-only-") as temporary:
            result = resolve_axiom_engine_source(installation=Path(temporary))
        self.assertEqual("unavailable", result["state"])
        self.assertIsNone(result["archive_path"])


if __name__ == "__main__":
    unittest.main()
