"""Core owns physical Fresh Project V2 Git metadata mutations."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest

from workbench_api.git_bootstrap import GitBootstrapError, git_bootstrap_host, git_bootstrap_scope
from workbench_core.git_bootstrap import HOST
from workbench_core.host_services import install_local_host_services


class GitBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name) / "fresh"
        (self.target / ".git/info").mkdir(parents=True)
        self.exclude = self.target / ".git/info/exclude"
        self.marker = self.target / ".git/workbench-fresh-project-v2.json"

    def test_requires_core_binding(self) -> None:
        with git_bootstrap_scope(None), self.assertRaises(GitBootstrapError) as refused:
            git_bootstrap_host()
        self.assertEqual("unavailable", refused.exception.code)

    def test_direct_core_host_binds_git_metadata_port(self) -> None:
        install_local_host_services()
        self.assertIs(HOST, git_bootstrap_host())

    def test_exact_existing_exclude_and_marker_round_trip(self) -> None:
        self.exclude.write_bytes(b"original\n")
        self.exclude.chmod(0o644)
        with git_bootstrap_scope(HOST):
            port = git_bootstrap_host()
            port.replace_exclude(
                self.target, before=b"original\n", after=b"original\nadded\n",
            )
            self.assertEqual(b"original\nadded\n", self.exclude.read_bytes())
            self.assertEqual(0o600, stat.S_IMODE(self.exclude.stat().st_mode))
            port.create_marker(self.target, b'{"plan":"one"}\n')
            with self.assertRaises(GitBootstrapError):
                port.create_marker(self.target, b'{"plan":"two"}\n')
            port.restore_exclude(
                self.target, before=b"original\n", after=b"original\nadded\n",
            )
            port.restore_exclude(
                self.target, before=b"original\n", after=b"original\nadded\n",
            )
            self.assertEqual(b"original\n", self.exclude.read_bytes())
            port.remove_marker(self.target, expected=b'{"plan":"one"}\n')
            self.assertFalse(self.marker.exists())

    def test_created_exclude_is_removed_only_when_exact(self) -> None:
        with git_bootstrap_scope(HOST):
            port = git_bootstrap_host()
            port.replace_exclude(self.target, before=None, after=b"new\n")
            self.assertEqual(b"new\n", self.exclude.read_bytes())
            self.exclude.write_bytes(b"later edit\n")
            with self.assertRaises(GitBootstrapError) as stale:
                port.restore_exclude(self.target, before=None, after=b"new\n")
            self.assertEqual("stale", stale.exception.code)
            self.assertEqual(b"later edit\n", self.exclude.read_bytes())
            self.exclude.write_bytes(b"new\n")
            port.restore_exclude(self.target, before=None, after=b"new\n")
            self.assertFalse(self.exclude.exists())

    def test_second_link_and_symlink_refused_without_mutation(self) -> None:
        self.exclude.write_bytes(b"before")
        alternative = self.target / "alternate"
        try:
            os.link(self.exclude, alternative)
        except OSError as exc:
            self.skipTest(f"host cannot create hardlinks: {exc}")
        with git_bootstrap_scope(HOST), self.assertRaises(GitBootstrapError) as unsafe:
            git_bootstrap_host().replace_exclude(
                self.target, before=b"before", after=b"after",
            )
        self.assertEqual("path", unsafe.exception.code)
        self.assertEqual(b"before", alternative.read_bytes())
        alternative.unlink()
        self.exclude.unlink()
        outside = self.target.parent / "outside"
        outside.mkdir()
        (self.target / ".git/info").rmdir()
        try:
            (self.target / ".git/info").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"host cannot create symlinks: {exc}")
        with git_bootstrap_scope(HOST), self.assertRaises(GitBootstrapError) as redirected:
            git_bootstrap_host().replace_exclude(
                self.target, before=None, after=b"new",
            )
        self.assertEqual("path", redirected.exception.code)
        self.assertEqual([], list(outside.iterdir()))

    def test_changed_marker_is_retained_for_recovery_review(self) -> None:
        with git_bootstrap_scope(HOST):
            port = git_bootstrap_host()
            port.create_marker(self.target, b"marker-one\n")
            self.marker.write_bytes(b"marker-two\n")
            with self.assertRaises(GitBootstrapError) as stale:
                port.remove_marker(self.target, expected=b"marker-one\n")
            self.assertEqual("stale", stale.exception.code)
            self.assertEqual(b"marker-two\n", self.marker.read_bytes())

    def test_interrupted_exclude_stage_blocks_new_mutation(self) -> None:
        self.exclude.write_bytes(b"before\n")
        stage = self.exclude.parent / ".exclude.workbench-git-interrupted.tmp"
        stage.write_bytes(b"after\n")
        with git_bootstrap_scope(HOST), self.assertRaises(GitBootstrapError) as blocked:
            git_bootstrap_host().replace_exclude(
                self.target, before=b"before\n", after=b"after\n",
            )
        self.assertEqual("stage", blocked.exception.code)
        self.assertEqual(b"before\n", self.exclude.read_bytes())
        self.assertEqual(b"after\n", stage.read_bytes())


if __name__ == "__main__":
    unittest.main()
