"""Core physical source edits preserve exact owner preconditions and later edits."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

from workbench_api.source_transactions import (
    SourceImage, SourceTransactionError, open_source_transaction,
    source_transactions_scope,
)
from workbench_core.source_transactions import CoreSourceTransactions


class SourceTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "source"
        self.root.mkdir()
        self.provider = CoreSourceTransactions(owner_id="blueprints")

    def test_core_binding_required_and_created_source_rolls_back(self) -> None:
        with source_transactions_scope(None), self.assertRaises(SourceTransactionError) as unavailable:
            open_source_transaction(self.root, binding="release-1")
        self.assertEqual("unavailable", unavailable.exception.code)
        with source_transactions_scope(self.provider):
            transaction = open_source_transaction(self.root, binding="release-1")
            stage = transaction.prepare(
                "new/file.txt", before=None,
                after=SourceImage("file", b"created\n", executable=True),
                create_parents=True,
            )
            target = self.root / "new/file.txt"
            self.assertFalse(target.exists())
            transaction.commit(stage)
            self.assertEqual(b"created\n", target.read_bytes())
            self.assertTrue(target.stat().st_mode & stat.S_IXUSR)
            transaction.rollback_all()
            transaction.cleanup(remove_created_directories=True)
            self.assertFalse(target.exists())
            self.assertFalse(target.parent.exists())

    def test_changed_baseline_between_prepare_and_commit_is_preserved(self) -> None:
        target = self.root / "existing.txt"
        target.write_bytes(b"before\n")
        transaction = self.provider.open(self.root, binding="release-2")
        stage = transaction.prepare(
            "existing.txt", before=SourceImage("file", b"before\n"),
            after=SourceImage("file", b"after\n"),
        )
        target.write_bytes(b"someone else\n")
        with self.assertRaises(SourceTransactionError) as stale:
            transaction.commit(stage)
        self.assertEqual("stale", stale.exception.code)
        transaction.rollback_all()
        transaction.cleanup()
        self.assertEqual(b"someone else\n", target.read_bytes())
        self.assertEqual([target], list(self.root.iterdir()))

    def test_later_edit_blocks_rollback_without_overwriting_it(self) -> None:
        target = self.root / "existing.txt"
        target.write_bytes(b"before\n")
        transaction = self.provider.open(self.root, binding="release-3")
        stage = transaction.prepare(
            "existing.txt", before=SourceImage("file", b"before\n"),
            after=SourceImage("file", b"after\n"),
        )
        transaction.commit(stage)
        target.write_bytes(b"later edit\n")
        with self.assertRaises(SourceTransactionError) as stale:
            transaction.rollback_all()
        self.assertEqual("stale", stale.exception.code)
        self.assertEqual(b"later edit\n", target.read_bytes())

    def test_parent_replacement_with_matching_bytes_blocks_commit(self) -> None:
        parent = self.root / "nested"
        parent.mkdir()
        target = parent / "existing.txt"
        target.write_bytes(b"before\n")
        transaction = self.provider.open(self.root, binding="release-parent")
        stage = transaction.prepare(
            "nested/existing.txt", before=SourceImage("file", b"before\n"),
            after=SourceImage("file", b"after\n"),
        )
        parent.rename(self.root / "moved")
        parent.mkdir()
        target.write_bytes(b"before\n")
        with self.assertRaises(SourceTransactionError) as changed:
            transaction.commit(stage)
        self.assertEqual("path", changed.exception.code)
        self.assertEqual(b"before\n", target.read_bytes())
        self.assertEqual(b"before\n", (self.root / "moved/existing.txt").read_bytes())

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows developer mode")
    def test_symlink_source_and_redirecting_parent(self) -> None:
        transaction = self.provider.open(self.root, binding="release-4")
        stage = transaction.prepare(
            "link", before=None, after=SourceImage("symlink", b"target.txt"),
        )
        transaction.commit(stage)
        self.assertEqual("target.txt", os.readlink(self.root / "link"))
        transaction.rollback_all()
        transaction.cleanup()
        self.assertFalse((self.root / "link").exists())
        outside = self.root.parent / "outside"
        outside.mkdir()
        (self.root / "redirect").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(SourceTransactionError) as unsafe:
            transaction.prepare(
                "redirect/child", before=None,
                after=SourceImage("file", b"unsafe"), create_parents=True,
            )
        self.assertEqual("path", unsafe.exception.code)
        self.assertFalse((outside / "child").exists())

    def test_cancellation_before_commit_preserves_source(self) -> None:
        target = self.root / "existing.txt"
        target.write_bytes(b"before\n")
        cancelled = False

        def check_cancelled() -> None:
            if cancelled:
                raise RuntimeError("cancelled")

        transaction = CoreSourceTransactions(
            owner_id="blueprints", check_cancelled=check_cancelled,
        ).open(self.root, binding="release-5")
        stage = transaction.prepare(
            "existing.txt", before=SourceImage("file", b"before\n"),
            after=SourceImage("file", b"after\n"),
        )
        cancelled = True
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            transaction.commit(stage)
        transaction.cleanup()
        self.assertEqual(b"before\n", target.read_bytes())

    def test_persisted_stage_reopens_after_replace_with_exact_mode(self) -> None:
        target = self.root / "existing.txt"
        target.write_bytes(b"before\n")
        target.chmod(0o750)
        token = "a" * 32
        transaction = self.provider.open(
            self.root, binding="m2-plan", staging_token=token,
        )
        stage = transaction.prepare(
            "existing.txt", before=SourceImage("file", b"before\n", executable=None),
            after=SourceImage("file", b"after\n", executable=None, mode=0o750),
            preserve_target_mode=True,
        )
        self.assertTrue(stage.staged_relative.startswith(
            f".existing.txt.workbench-{token}-",
        ))
        transaction.commit(stage)
        self.assertEqual(0o750, stat.S_IMODE(target.stat().st_mode))

        reopened = self.provider.open(
            self.root, binding="m2-plan", staging_token=token,
        )
        attached = reopened.attach(
            "existing.txt", before=SourceImage("file", b"before\n", executable=None),
            after=SourceImage("file", b"after\n", executable=None),
            staged_relative=stage.staged_relative, attempted=True,
        )
        self.assertEqual("after", reopened.classify(attached))
        reopened.rollback(attached)
        reopened.cleanup()
        self.assertEqual(b"before\n", target.read_bytes())
        self.assertEqual(0o750, stat.S_IMODE(target.stat().st_mode))

    def test_attached_stage_preserves_later_edit_and_rejects_wrong_token(self) -> None:
        target = self.root / "existing.txt"
        target.write_bytes(b"before\n")
        token = "b" * 32
        transaction = self.provider.open(
            self.root, binding="m2-plan", staging_token=token,
        )
        before = SourceImage("file", b"before\n", executable=None)
        after = SourceImage("file", b"after\n", executable=None, mode=0o644)
        stage = transaction.prepare("existing.txt", before=before, after=after)
        with self.assertRaises(SourceTransactionError) as wrong:
            self.provider.open(
                self.root, binding="m2-plan", staging_token="c" * 32,
            ).attach(
                "existing.txt", before=before, after=after,
                staged_relative=stage.staged_relative, attempted=False,
            )
        self.assertEqual("stage", wrong.exception.code)
        transaction.commit(stage)
        target.write_bytes(b"later\n")
        reopened = self.provider.open(
            self.root, binding="m2-plan", staging_token=token,
        )
        attached = reopened.attach(
            "existing.txt", before=before,
            after=SourceImage("file", b"after\n", executable=None),
            staged_relative=stage.staged_relative, attempted=True,
        )
        self.assertEqual("other", reopened.classify(attached))
        with self.assertRaises(SourceTransactionError) as stale:
            reopened.rollback(attached)
        self.assertEqual("stage", stale.exception.code)
        self.assertEqual(b"later\n", target.read_bytes())

    def test_orphan_cleanup_skips_uncreated_parents_and_other_tokens(self) -> None:
        target = self.root / "existing.txt"
        target.write_bytes(b"before\n")
        owned = self.root / f".existing.txt.workbench-{'d' * 32}-old.tmp"
        other = self.root / f".existing.txt.workbench-{'e' * 32}-other.tmp"
        owned.write_bytes(b"stage\n")
        other.write_bytes(b"other\n")
        transaction = self.provider.open(
            self.root, binding="m2-plan", staging_token="d" * 32,
        )
        transaction.cleanup_orphaned_stages(["missing/child.txt", "existing.txt"])
        self.assertFalse(owned.exists())
        self.assertEqual(b"other\n", other.read_bytes())
        self.assertEqual(b"before\n", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
