"""Native packaging closure, identity, archive and content-boundary checks."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import native_distribution as distribution


class NativePackageTests(unittest.TestCase):
    def test_default_is_only_core_and_api(self):
        requested, selected = distribution.selected_components()
        self.assertEqual(["workbench-core"], requested)
        self.assertEqual({"workbench-core", "workbench-api"}, {row["id"] for row in selected})

    def test_suite_is_selection_not_second_distribution(self):
        requested, selected = distribution.selected_components(suite=True)
        self.assertEqual(19, len(selected))
        self.assertEqual(19, len(requested))
        self.assertIn("workbench-axiom", requested)
        self.assertEqual(1, sum(row["id"] == "workbench-core" for row in selected))
        self.assertNotIn("workbench-tui", requested)

    def test_textual_client_is_explicit_and_does_not_change_core_closure(self):
        requested, selected = distribution.selected_components(["workbench-tui"])
        self.assertEqual(["workbench-tui"], requested)
        self.assertEqual({"workbench-tui"}, {row["id"] for row in selected})
        requested, selected = distribution.selected_components(["workbench-core", "workbench-tui"])
        self.assertEqual(["workbench-core", "workbench-tui"], requested)
        self.assertEqual({"workbench-api", "workbench-core", "workbench-tui"}, {row["id"] for row in selected})

    def test_selected_module_and_profile_close_dependencies(self):
        _, selected = distribution.selected_components(["workbench-profile-supersymmetry"])
        ids = {row["id"] for row in selected}
        self.assertIn("workbench-profile-cleanroom", ids)
        self.assertIn("workbench-crucible", ids)
        with self.assertRaises(distribution.DistributionError):
            distribution.selected_components(["unknown"])

    def test_incompatible_native_dependency_is_rejected(self):
        authority, components = distribution.load_authority()
        components["workbench-core"]["dependencies"] = ["workbench-api>=99"]
        with patch.object(distribution, "load_authority", return_value=(authority, components)):
            with self.assertRaisesRegex(distribution.DistributionError, "incompatible"):
                distribution.selected_components()

    def test_existing_build_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            keep = destination / "retained.txt"
            keep.write_text("keep")
            with self.assertRaisesRegex(distribution.DistributionError, "new directory"):
                distribution.build(destination)
            self.assertEqual("keep", keep.read_text())

    def test_unsafe_or_mismatched_wheel_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "example-1.0.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("../escape", "bad")
            with self.assertRaisesRegex(distribution.DistributionError, "unsafe"):
                distribution.wheel_record(wheel)
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("example-1.0.0.dist-info/METADATA", "Name: other\nVersion: 1.0.0\n")
            with self.assertRaisesRegex(distribution.DistributionError, "identity"):
                distribution.wheel_record(wheel)

    def test_live_documentation_references_exist(self):
        for module in ROOT.glob("modules/*/pyproject.toml"):
            self.assertTrue((module.parent / "LICENSE").is_file())
            self.assertEqual((ROOT / "NOTICE.md").read_bytes(), (module.parent / "NOTICE.md").read_bytes())
