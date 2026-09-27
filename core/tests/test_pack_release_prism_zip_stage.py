"""Read-only review and durable Linux staging of a synthetic WSL Prism ZIP."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import pack_instance_cli, pack_release_prism_zip_stage as stage
from workbench_core.pack_release_prism_zip_composition import plan_prism_zip_composition


ROOT = Path(__file__).resolve().parents[2]


class PrismZipStageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=ROOT / ".workbench")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.state.mkdir(mode=0o700)
        self.config.mkdir(mode=0o700)
        self.archive = self.root / "Prism-export.zip"
        with ZipFile(self.archive, "w") as source:
            source.writestr("instance.cfg", "[General]\nname=User branch\n")
            source.writestr("mmc-pack.json", json.dumps({"formatVersion": 1, "components": [
                {"uid": "net.minecraft", "version": "1.12.2"},
                {"uid": "net.minecraftforge", "version": "0.6.8-alpha"},
            ]}))
            source.writestr("patches/net.minecraftforge.json", "{}")
            source.writestr("minecraft/mods/Susy-Core.jar", b"jar-test-bytes")

    def _writable_9p(self):
        return patch.multiple(
            stage,
            _mount_type=lambda path: "9p" if path == self.archive.parent else "ext4",
        )

    def _plan_copy(self) -> dict:
        with self._writable_9p(), patch.object(
                stage.os, "statvfs", return_value=SimpleNamespace(f_flag=0)):
            return stage.plan_prism_zip_stage(self.archive, state_root=self.state)

    def _apply_copy(self, plan_id: str, **options) -> dict:
        with self._writable_9p(), patch.object(
                stage.os, "statvfs", return_value=SimpleNamespace(f_flag=0)):
            return stage.apply_prism_zip_stage(
                self.archive, state_root=self.state,
                expected_plan_id=plan_id, **options,
            )

    def test_linux_source_is_direct_and_does_not_stage(self) -> None:
        plan = stage.plan_prism_zip_stage(self.archive, state_root=self.state)
        self.assertEqual(plan["action"], "direct")
        self.assertEqual(plan["archive_path"], str(self.archive))
        result = stage.apply_prism_zip_stage(
            self.archive, state_root=self.state, expected_plan_id=plan["plan_id"],
        )
        self.assertEqual(result["outcome"], "direct")
        self.assertFalse((self.state / "pack-release-zip-staging").exists())

    def test_read_only_9p_source_is_direct(self) -> None:
        with self._writable_9p(), patch.object(
                stage.os, "statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)):
            plan = stage.plan_prism_zip_stage(self.archive, state_root=self.state)
        self.assertEqual(plan["action"], "direct")
        self.assertEqual(plan["source_filesystem"], "9p")
        self.assertEqual(plan["archive_path"], str(self.archive))

    def test_writable_9p_copy_reopens_without_source_and_feeds_importer(self) -> None:
        plan = self._plan_copy()
        self.assertEqual(plan["action"], "copy")
        self.assertEqual(plan["source_filesystem"], "9p")
        result = self._apply_copy(plan["plan_id"])
        self.assertEqual(result["outcome"], "copied")
        target = Path(result["archive_path"])
        self.assertEqual(target.read_bytes(), self.archive.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(plan_prism_zip_composition(
            target, state_root=self.state, config_home=self.config,
        )["file_count"], 1)
        self.assertEqual(self._apply_copy(plan["plan_id"])["outcome"], "reused")
        self.archive.unlink()
        reopened = stage.reopen_prism_zip_stage(
            state_root=self.state, expected_plan_id=plan["plan_id"],
        )
        self.assertEqual(reopened["outcome"], "reopened")
        self.assertEqual(reopened["archive_path"], str(target))

    def test_changed_source_and_symlink_fail_closed(self) -> None:
        plan = self._plan_copy()
        self.archive.write_bytes(self.archive.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self._apply_copy(plan["plan_id"])
        self.archive.unlink()
        self.archive.symlink_to(self.root / "other.zip")
        with self.assertRaises(ValueError):
            self._plan_copy()

    def test_reopen_rejects_changed_retained_archive(self) -> None:
        plan = self._plan_copy()
        copied = self._apply_copy(plan["plan_id"])
        Path(copied["archive_path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            stage.reopen_prism_zip_stage(
                state_root=self.state, expected_plan_id=plan["plan_id"],
            )

    def test_cancelled_copy_retains_private_stage_and_retry_succeeds(self) -> None:
        plan = self._plan_copy()
        pulses = 0

        def stop_during_copy() -> None:
            nonlocal pulses
            pulses += 1
            if pulses >= 4:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self._apply_copy(plan["plan_id"], check_cancelled=stop_during_copy)
        root = self.state / "pack-release-zip-staging"
        interrupted = list(root.glob(".*.zip-stage-*"))
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].stat().st_mode & 0o777, 0o700)
        self.assertFalse(Path(plan["archive_path"]).exists())
        self.assertEqual(self._apply_copy(plan["plan_id"])["outcome"], "copied")
        self.assertTrue(interrupted[0].exists())

    def test_cli_review_apply_and_reopen(self) -> None:
        def command(*arguments: str) -> dict:
            output = StringIO()
            with redirect_stdout(output), patch.object(
                    pack_instance_cli, "default_runtime_state_root", return_value=self.state
            ), patch.object(pack_instance_cli, "default_user_config_home", return_value=self.config):
                self.assertEqual(pack_instance_cli.main(arguments), 0)
            return json.loads(output.getvalue())

        with self._writable_9p(), patch.object(
                stage.os, "statvfs", return_value=SimpleNamespace(f_flag=0)):
            plan = command("zip-stage-plan", "--profile", "supersymmetry",
                           "--archive", str(self.archive), "--json")["stage"]
            result = command("zip-stage-apply", "--profile", "supersymmetry",
                             "--archive", str(self.archive),
                             "--expected-plan-id", plan["plan_id"], "--json")["stage"]
        self.assertEqual(result["outcome"], "copied")
        self.assertTrue(Path(result["archive_path"]).is_file())
        reopened = command("zip-stage-reopen", "--profile", "supersymmetry",
                           "--expected-plan-id", plan["plan_id"], "--json")["stage"]
        self.assertEqual(reopened["archive_path"], result["archive_path"])


if __name__ == "__main__":
    unittest.main()
