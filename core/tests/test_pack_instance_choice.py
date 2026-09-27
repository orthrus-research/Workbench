"""A pack instance source choice is private, revisioned, and path-free."""

from pathlib import Path
import json
import tempfile
import unittest

from workbench_core.pack_instance_choice import (
    load_pack_instance_choice, save_pack_instance_choice,
)


PLAN = "workbench-pack-release-client-composition-plan:sha256:" + "a" * 64


class PackInstanceChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / ".workbench"

    def test_recovery_keeps_core_source_identity_and_launcher_choice(self) -> None:
        baseline = load_pack_instance_choice(self.config)
        self.assertIsNone(baseline["source_plan_id"])
        saved = save_pack_instance_choice(
            self.config, source_kind="user-prism-zip", source_plan_id=PLAN,
            launcher_root=str(self.root / "PrismLauncher"), workspace_name="dev",
            expected_record_id=baseline["record_id"],
        )
        self.assertEqual(saved, load_pack_instance_choice(self.config))
        stored = self.config / "pack-instance-supersymmetry.json"
        self.assertEqual(0o600, stored.stat().st_mode & 0o777)
        self.assertNotIn("source_archive_path", stored.read_text(encoding="utf-8"))
        self.assertEqual(PLAN, json.loads(stored.read_text(encoding="utf-8"))["source_plan_id"])

    def test_stale_revision_cannot_replace_a_saved_choice(self) -> None:
        baseline = load_pack_instance_choice(self.config)
        saved = save_pack_instance_choice(
            self.config, source_kind="user-prism-zip", source_plan_id=PLAN,
            launcher_root=None, workspace_name=None,
            expected_record_id=baseline["record_id"],
        )
        with self.assertRaisesRegex(ValueError, "changed after review"):
            save_pack_instance_choice(
                self.config, source_kind="user-prism-zip", source_plan_id=PLAN,
                launcher_root=str(self.root / "another"), workspace_name=None,
                expected_record_id=baseline["record_id"],
            )
        self.assertEqual(saved, load_pack_instance_choice(self.config))

    def test_invalid_source_or_modified_record_is_refused(self) -> None:
        baseline = load_pack_instance_choice(self.config)
        with self.assertRaisesRegex(ValueError, "source kind and plan ID"):
            save_pack_instance_choice(
                self.config, source_kind="user-prism-zip", source_plan_id=None,
                launcher_root=None, workspace_name=None,
                expected_record_id=baseline["record_id"],
            )
        saved = save_pack_instance_choice(
            self.config, source_kind="user-prism-zip", source_plan_id=PLAN,
            launcher_root=None, workspace_name=None,
            expected_record_id=baseline["record_id"],
        )
        path = self.config / "pack-instance-supersymmetry.json"
        path.write_text(path.read_text(encoding="utf-8").replace(PLAN, PLAN[:-1] + "b"),
                        encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity differs"):
            load_pack_instance_choice(self.config)
        self.assertNotEqual(saved["source_plan_id"], json.loads(path.read_text())["source_plan_id"])


if __name__ == "__main__":
    unittest.main()
