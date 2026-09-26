"""No-replace Core promotion for legacy IDE extraction paths."""

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import workbench_core.prepared_directory_promotion as prepared


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux no-replace rename")
class PreparedDirectoryPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "jdk-25.0.4+7"
        self.stage = self.root / "jdk-25.0.4+7.stage123"
        self.stage.mkdir(mode=0o700)
        self.payload = self.stage / "jdk-25.0.4+7"
        self.payload.mkdir()
        (self.payload / "bin").mkdir()
        (self.payload / "bin/java").write_bytes(b"fixture runtime")
        (self.payload / "legal").mkdir()
        (self.payload / "legal/license").symlink_to("../bin/java")
        self.marker = b"a" * 64 + b"\n"

    def _promote(self) -> Path:
        return prepared.promote_prepared_directory(
            self.payload, self.target,
            marker_name=".workbench-provisioned-sha256", marker_bytes=self.marker,
        )

    def test_plus_named_target_and_internal_relative_link_keep_exact_path(self) -> None:
        self.assertEqual(self.target, self._promote())
        self.assertEqual(self.marker, (self.target / ".workbench-provisioned-sha256").read_bytes())
        self.assertEqual("../bin/java", os.readlink(self.target / "legal/license"))
        self.assertEqual(b"fixture runtime", (self.target / "bin/java").read_bytes())
        self.assertTrue(self.stage.is_dir())
        self.assertEqual([], list(self.stage.iterdir()))

    def test_markerless_generic_sibling_stage_uses_same_no_replace_operation(self) -> None:
        renamed_stage = self.root / "other-prepared-stage"
        self.stage.rename(renamed_stage)
        payload = renamed_stage / self.payload.name
        self.assertEqual(self.target, prepared.promote_prepared_directory(payload, self.target))
        self.assertEqual(b"fixture runtime", (self.target / "bin/java").read_bytes())
        self.assertFalse((self.target / ".workbench-provisioned-sha256").exists())
        self.assertTrue(renamed_stage.is_dir())
        self.assertEqual([], list(renamed_stage.iterdir()))

    def test_exact_core_stage_marker_remains_after_member_promotion(self) -> None:
        lease_marker = self.stage / ".core-lease.json"
        lease_marker.write_bytes(b"exact sealed lease\n")
        lease_marker.chmod(0o600)
        info = self.stage.stat()
        self.assertEqual(self.target, prepared.promote_prepared_directory(
            self.payload, self.target,
            marker_name=".workbench-provisioned-sha256", marker_bytes=self.marker,
            stage_marker=((info.st_dev, info.st_ino), lease_marker.name, b"exact sealed lease\n"),
        ))
        self.assertEqual(b"exact sealed lease\n", lease_marker.read_bytes())
        self.assertEqual([lease_marker], list(self.stage.iterdir()))

    def test_changed_core_stage_marker_refuses_before_move(self) -> None:
        lease_marker = self.stage / ".core-lease.json"
        lease_marker.write_bytes(b"changed\n")
        lease_marker.chmod(0o600)
        info = self.stage.stat()
        with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
            prepared.promote_prepared_directory(
                self.payload, self.target,
                stage_marker=((info.st_dev, info.st_ino), lease_marker.name, b"expected\n"),
            )
        self.assertEqual("directory.changed", refusal.exception.code)
        self.assertFalse(self.target.exists())
        self.assertEqual(b"changed\n", lease_marker.read_bytes())

    def test_private_interrupted_stage_inventory_keeps_empty_and_unpublished_stages(self) -> None:
        first = self.root / ".pack.01"
        first.mkdir(mode=0o700)
        (first / "variant").mkdir()
        second = self.root / ".pack.02"
        second.mkdir(mode=0o700)
        self.assertEqual(2, prepared.count_prepared_directory_stages(
            self.target, stage_prefix=".pack.",
        ))
        self.assertEqual(0, prepared.count_prepared_directory_stages(
            self.target, stage_prefix=".other.",
        ))

    def test_prepared_stage_inventory_refuses_redirected_or_shared_stage(self) -> None:
        stage = self.root / ".pack.01"
        stage.symlink_to(self.stage, target_is_directory=True)
        with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
            prepared.count_prepared_directory_stages(self.target, stage_prefix=".pack.")
        self.assertEqual("directory.unsafe", refusal.exception.code)
        stage.unlink()
        stage.mkdir(mode=0o755)
        with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
            prepared.count_prepared_directory_stages(self.target, stage_prefix=".pack.")
        self.assertEqual("directory.unsafe", refusal.exception.code)

    def test_invalid_marker_name_refuses_before_promotion(self) -> None:
        with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
            prepared.promote_prepared_directory(
                self.payload, self.target, marker_name="../unsafe", marker_bytes=self.marker,
            )
        self.assertEqual("directory.policy", refusal.exception.code)
        self.assertFalse(self.target.exists())

    def test_competing_destination_is_never_replaced_and_stage_is_retained(self) -> None:
        original = prepared._rename_no_replace

        def competitor(*args: object, **kwargs: object) -> None:
            self.target.mkdir()
            (self.target / "keep.bin").write_bytes(b"other owner")
            original(*args, **kwargs)

        with patch.object(prepared, "_rename_no_replace", side_effect=competitor):
            with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
                self._promote()
        self.assertEqual("output.exists", refusal.exception.code)
        self.assertEqual(b"other owner", (self.target / "keep.bin").read_bytes())
        self.assertEqual(self.marker, (self.payload / ".workbench-provisioned-sha256").read_bytes())

    def test_interrupted_or_extra_stage_children_refuse_without_cleanup(self) -> None:
        (self.stage / "other").write_bytes(b"retain")
        with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
            self._promote()
        self.assertEqual("directory.incomplete", refusal.exception.code)
        self.assertFalse(self.target.exists())
        self.assertEqual(b"retain", (self.stage / "other").read_bytes())

    def test_archive_supplied_marker_refuses_without_replacing_it(self) -> None:
        (self.payload / ".workbench-provisioned-sha256").write_bytes(b"archive marker")
        with self.assertRaises(prepared.PreparedDirectoryError):
            self._promote()
        self.assertFalse(self.target.exists())
        self.assertEqual(b"archive marker", (self.payload / ".workbench-provisioned-sha256").read_bytes())

    def test_shared_stage_refuses_before_marker(self) -> None:
        self.stage.chmod(0o755)
        with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
            self._promote()
        self.assertEqual("directory.unsafe", refusal.exception.code)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.payload / ".workbench-provisioned-sha256").exists())

    def test_changed_empty_stage_is_not_removed_after_publication(self) -> None:
        original = prepared._rename_no_replace
        displaced = self.root / "displaced-stage"

        def replace_stage(*args: object, **kwargs: object) -> None:
            original(*args, **kwargs)
            self.stage.rename(displaced)
            self.stage.mkdir(mode=0o700)

        with patch.object(prepared, "_rename_no_replace", side_effect=replace_stage):
            with self.assertRaises(prepared.PreparedDirectoryError) as refusal:
                self._promote()
        self.assertEqual("directory.changed", refusal.exception.code)
        self.assertEqual(self.marker, (self.target / ".workbench-provisioned-sha256").read_bytes())
        self.assertTrue(self.stage.is_dir())
        self.assertTrue(displaced.is_dir())

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX hard-exit injection")
    def test_hard_exit_after_move_keeps_complete_marker_and_empty_stage(self) -> None:
        original = prepared._rename_no_replace

        def exit_after_move(*args: object, **kwargs: object) -> None:
            original(*args, **kwargs)
            os._exit(71)

        child = os.fork()
        if child == 0:
            with patch.object(prepared, "_rename_no_replace", side_effect=exit_after_move):
                self._promote()
            os._exit(72)
        _, status = os.waitpid(child, 0)
        self.assertEqual(71, os.waitstatus_to_exitcode(status))
        self.assertEqual(self.marker, (self.target / ".workbench-provisioned-sha256").read_bytes())
        self.assertEqual([], list(self.stage.iterdir()))
        self.assertEqual("../bin/java", os.readlink(self.target / "legal/license"))


if __name__ == "__main__":
    unittest.main()
