"""Reused native assemblies retain exact wheels and selected dependencies."""
import json
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import native_distribution as distribution
from verify_wheelhouse import WheelhouseError


class NativeReuseTests(unittest.TestCase):
    def assembly(self, directory, *, requirement="helper[feature]>=1"):
        (directory / "wheels").mkdir(parents=True)
        definitions = {
            "workbench-core": ("0.1.0", ["workbench-api>=0.1", requirement]),
            "workbench-api": ("0.1.0", []),
            "workbench-unrelated": ("0.1.0", []),
            "helper": ("1.0", ["extra-dependency>=1; extra == 'feature'", "absent>=1; python_version < '2'"]),
            "extra-dependency": ("1.0", []),
            "pip": ("26.1.2", []),
        }
        records = []
        for name, (version, dependencies) in definitions.items():
            prefix = name.replace("-", "_") + "-" + version
            wheel = directory / "wheels" / f"{prefix}-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(prefix + ".dist-info/METADATA", f"Name: {name}\nVersion: {version}\n" + "".join(f"Requires-Dist: {dependency}\n" for dependency in dependencies))
                archive.writestr("payload.txt", name)
            records.append(distribution.wheel_record(wheel))
        records.sort(key=lambda row: row["name"])
        manifest = {"format": distribution.FORMAT, "source_sha256": "a" * 64,
                    "selected_components": ["workbench-core", "workbench-api", "workbench-unrelated"],
                    "native_versions": {name: version for name, (version, _) in definitions.items() if name.startswith("workbench-")},
                    "target": {"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": sys.platform, "machine": platform.machine()},
                    "wheels": records, "qualified": False}
        distribution._write_assembly(directory, manifest, root=ROOT)
        return manifest

    def derive(self, source, output):
        selected = (["workbench-core"], [{"id": "workbench-core", "version": "0.1.0"}, {"id": "workbench-api", "version": "0.1.0"}])
        with patch.object(distribution, "source_identity", return_value="a" * 64), patch.object(distribution, "selected_components", return_value=selected), patch.object(distribution, "_run", side_effect=AssertionError("reuse must never build/download")):
            return distribution.derive(source, output)

    def test_reuse_selects_metadata_closure_and_preserves_every_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "core"
            original = self.assembly(source)
            result = self.derive(source, output)
            self.assertEqual({"workbench-core", "workbench-api"}, set(result["native_versions"]))
            self.assertEqual({"workbench-core", "workbench-api", "helper", "extra-dependency", "pip"}, {row["name"] for row in result["wheels"]})
            self.assertTrue(all(row in original["wheels"] for row in result["wheels"]))
            self.assertEqual(result, distribution.verify(output))
            for row in result["wheels"]:
                self.assertEqual((source / "wheels" / row["filename"]).read_bytes(), (output / "wheels" / row["filename"]).read_bytes())

    def test_corruption_missing_dependency_and_wrong_version_are_rejected(self):
        for requirement in ("missing>=1", "helper>=99", "helper @ https://example.invalid/helper.whl"):
            with self.subTest(requirement=requirement), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "source"
                self.assembly(source, requirement=requirement)
                with self.assertRaisesRegex(distribution.DistributionError, "cannot satisfy"):
                    self.derive(source, Path(temporary) / "out")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            manifest = self.assembly(source)
            (source / "wheels" / manifest["wheels"][0]["filename"]).write_bytes(b"corruption")
            with self.assertRaises(WheelhouseError):
                self.derive(source, Path(temporary) / "out")

    def test_stale_source_or_other_target_cannot_reuse_wheels(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            manifest = self.assembly(source)
            with patch.object(distribution, "source_identity", return_value="b" * 64):
                with self.assertRaisesRegex(distribution.DistributionError, "source differs"):
                    distribution.current_assembly(source)
            manifest["target"]["machine"] = "another-architecture"
            distribution._write_assembly(source, manifest, root=ROOT)
            with self.assertRaisesRegex(distribution.DistributionError, "target differs"):
                distribution.current_assembly(source)

    def test_unexpected_native_dependency_does_not_broaden_core_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            self.assembly(source, requirement="workbench-unrelated>=0.1")
            with self.assertRaisesRegex(distribution.DistributionError, "closure differs"):
                self.derive(source, Path(temporary) / "out")

    def test_copy_time_corruption_is_caught_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "out"
            self.assembly(source)
            copy = distribution.shutil.copyfile

            def corrupt(original, destination):
                result = copy(original, destination)
                Path(destination).write_bytes(b"wrong bytes")
                return result

            with patch.object(distribution.shutil, "copyfile", side_effect=corrupt):
                with self.assertRaises(WheelhouseError):
                    self.derive(source, output)
            retained = output / "retain.txt"
            retained.write_text("retain")
            with self.assertRaisesRegex(distribution.DistributionError, "new directory"):
                self.derive(source, output)
            self.assertEqual("retain", retained.read_text())
