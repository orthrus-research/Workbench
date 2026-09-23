"""Native versions are owned independently; release views cannot write them."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import component_versions as versions
import verify_component_artifacts as artifacts


class NativeVersionTests(unittest.TestCase):
    def test_native_inventory_and_projections(self):
        authority, components = versions.load_authority()
        self.assertEqual(22, len(components))
        self.assertEqual("jvm", components["workbench-axiom-engine"]["kind"])
        self.assertEqual("python", components["workbench-axiom"]["kind"])
        self.assertEqual([], versions.check_projections(components))
        self.assertEqual("core/pyproject.toml", components["workbench-core"]["manifest"])
        atlas_version = tomllib.loads((ROOT / "modules/atlas/pyproject.toml").read_text())["project"]["version"]
        self.assertEqual(atlas_version, components["workbench-atlas"]["version"])
        self.assertNotIn("compatibility", authority)
        self.assertFalse((ROOT / "packaging/release/workbench-release.json").exists())

    def test_component_bump_does_not_bump_others(self):
        original = versions.inventory(ROOT)
        changed = deepcopy(original)
        atlas = next(row for row in changed if row["distribution"] == "workbench-atlas")
        atlas["version"] = f"{int(atlas['version'].split('.')[0]) + 1}.0.0"
        before = versions.load_authority()[1]
        with patch.object(versions, "inventory", return_value=changed):
            after = versions.load_authority()[1]
        self.assertEqual(["workbench-atlas"], [name for name in before if before[name]["version"] != after[name]["version"]])

    def test_version_sync_was_removed(self):
        result = subprocess.run([sys.executable, "tools/component_versions.py", "sync"], cwd=ROOT, capture_output=True)
        self.assertNotEqual(0, result.returncode)

    def test_exact_candidate_artifact_family(self):
        expected = artifacts.expected_filenames("workbench-core")
        core_version = tomllib.loads((ROOT / "core/pyproject.toml").read_text())["project"]["version"]
        self.assertEqual((f"workbench_core-{core_version}-py3-none-any.whl",), expected)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = directory / expected[0]
            candidate.write_bytes(b"candidate")
            self.assertEqual((candidate,), artifacts.verify_component_directory("workbench-core", directory))
            extra = directory / "extra.txt"
            extra.write_text("extra")
            with self.assertRaisesRegex(artifacts.ComponentArtifactError, "extra"):
                artifacts.verify_component_directory("workbench-core", directory)
            extra.unlink()
            candidate.unlink()
            with self.assertRaisesRegex(artifacts.ComponentArtifactError, "missing"):
                artifacts.verify_component_directory("workbench-core", directory)
            candidate.symlink_to(ROOT / "LICENSE")
            with self.assertRaisesRegex(artifacts.ComponentArtifactError, "regular"):
                artifacts.verify_component_directory("workbench-core", directory)
