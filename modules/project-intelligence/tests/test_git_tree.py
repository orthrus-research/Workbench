"""Focused custody tests for raw local Git subtree materialization."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence.git_tree import (  # noqa: E402
    GitTreeMaterializationError,
    _display_git_path,
    _dirty_file_paths,
    _tree_entries,
    _windows_extended_path,
    materialize_git_merge_base_subtree,
    materialize_git_subtree,
)


@unittest.skipUnless(shutil.which("git"), "Git is required for Git-tree tests")
class GitTreeMaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workbench-git-tree-test-")
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.repository = self.temporary / "repository"
        self.repository.mkdir()
        self.source = self.repository / "pack"
        self.source.mkdir()
        self.environment = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        environment_patch = patch.dict(os.environ, self.environment)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

        self._git("init", "--quiet")
        self._git("config", "user.name", "Workbench Test")
        self._git("config", "user.email", "workbench-test@example.invalid")

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=os.environ.copy(),
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed.stdout.strip()

    def _commit(self, message: str = "fixture") -> str:
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _materialize(self):
        return materialize_git_subtree(
            self.source,
            "HEAD",
            PurePosixPath("."),
            PurePosixPath("."),
            max_files=20,
            max_file_bytes=1024 * 1024,
            max_total_bytes=4 * 1024 * 1024,
        )

    def _candidate_snapshot(self) -> tuple[str, str, str]:
        digest = hashlib.sha256(
            b"".join(
                path.relative_to(self.source).as_posix().encode("utf-8")
                + b"\0"
                + path.read_bytes()
                for path in sorted(self.source.rglob("*"))
                if path.is_file() and not path.is_symlink()
            )
        ).hexdigest()
        status = self._git(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
        return digest, status, self._git("rev-parse", "HEAD")

    def _assert_tree_paths_rejected(
        self,
        paths: tuple[bytes, ...],
        pattern: str,
    ) -> None:
        output = b"".join(
            b"100644 blob " + b"0" * 40 + b" 1\t" + path + b"\0"
            for path in paths
        )
        with patch(
            "workbench_project_intelligence.git_tree._run",
            return_value=output,
        ):
            with self.assertRaisesRegex(GitTreeMaterializationError, pattern):
                _tree_entries(
                    ("git",),
                    self.repository,
                    "0" * 40,
                    object_format="sha1",
                    max_files=20,
                    max_file_bytes=1024,
                    max_total_bytes=4096,
                )

    def test_dirty_path_scope_handles_cross_root_rename_and_copy_records(self) -> None:
        cases = (
            (
                "rename out",
                b"R  outside/New.groovy\0pack/Old.groovy\0",
                ("outside/New.groovy", "pack/Old.groovy"),
                ("pack/Old.groovy",),
            ),
            (
                "rename in",
                b"R  pack/New.groovy\0outside/Old.groovy\0",
                ("pack/New.groovy", "outside/Old.groovy"),
                ("pack/New.groovy",),
            ),
            (
                "copy out",
                b"C  outside/New.groovy\0pack/Old.groovy\0",
                ("outside/New.groovy",),
                (),
            ),
            (
                "copy in",
                b"C  pack/New.groovy\0outside/Old.groovy\0",
                ("pack/New.groovy",),
                ("pack/New.groovy",),
            ),
        )
        for label, status, expected_repository, expected_selected in cases:
            with self.subTest(label=label):
                repository_paths, selected_paths = _dirty_file_paths(
                    status,
                    "pack",
                )

                self.assertEqual(expected_repository, repository_paths)
                self.assertEqual(expected_selected, selected_paths)

    def test_git_path_display_distinguishes_raw_bytes_and_literal_escapes(self) -> None:
        self.assertEqual(r"pack/raw-\xff", _display_git_path(b"pack/raw-\xff"))
        self.assertEqual(
            r"pack/literal\\xff",
            _display_git_path(b"pack/literal\\xff"),
        )
        self.assertEqual(
            r"pack/line\x0abreak.groovy",
            _display_git_path(b"pack/line\nbreak.groovy"),
        )
        self.assertEqual(
            r"pack/bidi-\u202ereversed.groovy",
            _display_git_path("pack/bidi-\u202ereversed.groovy".encode("utf-8")),
        )

        repository_paths, selected_paths = _dirty_file_paths(
            b"?? pack/raw-\xff\0"
            b"?? pack/literal\\xff\0"
            b"?? pack/line\nbreak.groovy\0",
            "pack",
        )
        self.assertEqual(repository_paths, selected_paths)
        self.assertEqual(
            (
                r"pack/raw-\xff",
                r"pack/literal\\xff",
                r"pack/line\x0abreak.groovy",
            ),
            selected_paths,
        )

    def test_materializes_verified_commit_blobs_without_touching_candidate(self) -> None:
        recipe = self.source / "Recipe.groovy"
        config = self.source / "runConfig.json"
        recipe.write_bytes(b"duration(20)\n")
        config.write_bytes(b'{"loaders": {}}\n')
        commit = self._commit()
        tree = self._git("rev-parse", "HEAD^{tree}")
        recipe.write_bytes(b"duration(40)\n")
        before = self._candidate_snapshot()

        with self._materialize() as materialized:
            temporary_root = materialized.root
            self.assertEqual(b"duration(20)\n", (materialized.root / "Recipe.groovy").read_bytes())
            self.assertEqual(commit, materialized.commit_oid)
            self.assertEqual(tree, materialized.tree_oid)
            self.assertEqual("pack", materialized.repository_relative_path)
            self.assertEqual("exact-ref", materialized.selection_kind)
            self.assertIsNone(materialized.target_tip_oid)
            self.assertIsNone(materialized.repository_changed_file_count)
            self.assertIsNone(materialized.selected_changed_file_count)
            self.assertIsNone(materialized.repository_changed_paths)
            self.assertIsNone(materialized.selected_changed_paths)
            self.assertEqual(("pack/Recipe.groovy",), materialized.repository_dirty_paths)
            self.assertEqual(("pack/Recipe.groovy",), materialized.selected_dirty_paths)
            self.assertEqual(2, materialized.file_count)
            self.assertTrue(materialized.candidate_git_binding["dirty"])
            self.assertEqual(commit, materialized.candidate_git_binding["revision"])
            self.assertTrue(temporary_root.is_dir())

        self.assertFalse(temporary_root.exists())
        self.assertEqual(before, self._candidate_snapshot())

    def test_setup_selected_git_precedes_path_discovery(self) -> None:
        recipe = self.source / "Recipe.groovy"
        recipe.write_bytes(b"duration(20)\n")
        self._commit()
        selected_git = shutil.which("git")
        self.assertIsNotNone(selected_git)

        with patch.dict(
            os.environ,
            {
                "PATH": "",
                "WORKBENCH_GIT_EXECUTABLE": str(selected_git),
            },
        ):
            with self._materialize() as materialized:
                self.assertEqual(
                    b"duration(20)\n",
                    (materialized.root / "Recipe.groovy").read_bytes(),
                )

    def test_materializes_repository_root_as_selected_groovy_root(self) -> None:
        self.source = self.repository
        (self.source / "Recipe.groovy").write_bytes(b"duration(20)\n")
        (self.source / "runConfig.json").write_bytes(b'{"loaders": {}}\n')
        commit = self._commit()
        tree = self._git("rev-parse", "HEAD^{tree}")

        with self._materialize() as materialized:
            self.assertEqual(".", materialized.repository_relative_path)
            self.assertEqual(commit, materialized.commit_oid)
            self.assertEqual(tree, materialized.selected_tree_oid)
            self.assertEqual(materialized.root, materialized.selected_path)
            self.assertEqual(
                b"duration(20)\n",
                (materialized.selected_path / "Recipe.groovy").read_bytes(),
            )

    @unittest.skipUnless(os.name == "nt", "Windows long-path behavior")
    def test_materializes_beyond_legacy_windows_max_path(self) -> None:
        seed = self.repository / "blob-source.txt"
        seed.write_bytes(b"duration(20)\n")
        object_id = self._git("hash-object", "-w", str(seed))
        seed.unlink()
        relative = "/".join(
            ("pack", "a" * 80, "b" * 80, "c" * 80, "Recipe.groovy")
        )
        self._git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{object_id},{relative}",
        )
        self._git("commit", "--quiet", "-m", "long path fixture")

        with self._materialize() as materialized:
            target = materialized.selected_path.joinpath(
                "a" * 80,
                "b" * 80,
                "c" * 80,
                "Recipe.groovy",
            )
            self.assertGreater(len(os.fspath(target)), 260)
            self.assertEqual(b"duration(20)\n", target.read_bytes())

    @unittest.skipUnless(os.name == "nt", "Windows long-path behavior")
    def test_rejects_long_candidate_source_with_git_specific_guidance(self) -> None:
        ordinary_long_root = self.temporary / ("a" * 75)
        ordinary_source = ordinary_long_root.joinpath(
            "b" * 75,
            "c" * 75,
            "candidate",
        )
        native_long_root = _windows_extended_path(ordinary_long_root)
        native_source = _windows_extended_path(ordinary_source)
        try:
            native_source.mkdir(parents=True)
            self.assertGreater(len(os.fspath(ordinary_source)), 260)
            with self.assertRaisesRegex(
                GitTreeMaterializationError,
                "supported Git for Windows path limit.*shorter path.*--baseline PATH",
            ):
                with materialize_git_subtree(
                    ordinary_source,
                    "HEAD",
                    PurePosixPath("."),
                    PurePosixPath("."),
                    max_files=20,
                    max_file_bytes=1024 * 1024,
                    max_total_bytes=4 * 1024 * 1024,
                ):
                    self.fail("long candidate source should fail before Git")
        finally:
            if native_long_root.exists():
                shutil.rmtree(native_long_root)

    def test_rejects_case_collision_in_an_intermediate_component(self) -> None:
        self._assert_tree_paths_rejected(
            (b"Dir/A.groovy", b"dir/B.groovy"),
            "components collide by case or normalization",
        )

    def test_rejects_normalization_variant_in_an_intermediate_component(self) -> None:
        self._assert_tree_paths_rejected(
            (
                "Recipes/Cafe\u0301/A.groovy".encode("utf-8"),
            ),
            "not Unicode NFC",
        )

    def test_rejects_file_directory_prefix_collision(self) -> None:
        for paths in (
            (b"Name", b"name/Child.groovy"),
            (b"name/Child.groovy", b"Name"),
        ):
            with self.subTest(paths=paths):
                self._assert_tree_paths_rejected(
                    paths,
                    "collides as both file and directory",
                )

    def test_rejects_candidate_worktree_change_during_materialization(self) -> None:
        (self.source / "Recipe.groovy").write_bytes(b"duration(20)\n")
        (self.source / "runConfig.json").write_bytes(b'{"loaders": {}}\n')
        self._commit()

        with self.assertRaisesRegex(
            GitTreeMaterializationError,
            "candidate working tree changed during recipe review",
        ):
            with self._materialize():
                (self.repository / "changed-during-review.txt").write_text(
                    "changed\n",
                    encoding="utf-8",
                )

    def test_materializes_merge_base_and_counts_committed_pr_scope(self) -> None:
        recipe = self.source / "Recipe.groovy"
        recipe.write_bytes(b"duration(20)\n")
        base = self._commit("shared base")
        candidate_branch = self._git("branch", "--show-current")

        self._git("checkout", "--quiet", "-b", "target")
        (self.repository / "target-only.txt").write_text("target\n", encoding="utf-8")
        target_tip = self._commit("target tip")

        self._git("checkout", "--quiet", candidate_branch)
        recipe.write_bytes(b"duration(40)\n")
        (self.repository / "candidate-only.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        candidate_tip = self._commit("candidate tip")

        with materialize_git_merge_base_subtree(
            self.source,
            "target",
            PurePosixPath("."),
            PurePosixPath("."),
            max_files=20,
            max_file_bytes=1024 * 1024,
            max_total_bytes=4 * 1024 * 1024,
        ) as materialized:
            self.assertEqual(b"duration(20)\n", (materialized.root / "Recipe.groovy").read_bytes())
            self.assertEqual("merge-base", materialized.selection_kind)
            self.assertEqual("target", materialized.requested_ref)
            self.assertEqual(target_tip, materialized.target_tip_oid)
            self.assertEqual(base, materialized.commit_oid)
            self.assertEqual(candidate_tip, materialized.candidate_git_binding["revision"])
            self.assertEqual(2, materialized.repository_changed_file_count)
            self.assertEqual(1, materialized.selected_changed_file_count)
            self.assertEqual(
                ("candidate-only.txt", "pack/Recipe.groovy"),
                materialized.repository_changed_paths,
            )
            self.assertEqual(
                ("pack/Recipe.groovy",),
                materialized.selected_changed_paths,
            )
            self.assertEqual((), materialized.repository_dirty_paths)
            self.assertEqual((), materialized.selected_dirty_paths)

    def test_rejects_multiple_criss_cross_merge_bases(self) -> None:
        (self.source / "Recipe.groovy").write_bytes(b"duration(20)\n")
        base = self._commit("criss-cross base")
        tree = self._git("rev-parse", f"{base}^{{tree}}")
        candidate_branch = self._git("branch", "--show-current")

        left = self._git("commit-tree", tree, "-p", base, "-m", "left")
        right = self._git("commit-tree", tree, "-p", base, "-m", "right")
        candidate_tip = self._git(
            "commit-tree", tree, "-p", left, "-p", right, "-m", "candidate"
        )
        target_tip = self._git(
            "commit-tree", tree, "-p", right, "-p", left, "-m", "target"
        )
        self._git("update-ref", f"refs/heads/{candidate_branch}", candidate_tip)
        self._git("update-ref", "refs/heads/target", target_tip)

        with self.assertRaisesRegex(
            GitTreeMaterializationError, "multiple merge bases"
        ):
            with materialize_git_merge_base_subtree(
                self.source,
                "target",
                PurePosixPath("."),
                PurePosixPath("."),
                max_files=20,
                max_file_bytes=1024 * 1024,
                max_total_bytes=4 * 1024 * 1024,
            ):
                self.fail("ambiguous merge-base materialization must fail")

    def test_rejects_lfs_pointer_without_hydration(self) -> None:
        (self.source / "Recipe.groovy").write_bytes(
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + b"0" * 64 + b"\nsize 123\n"
        )
        self._commit()

        with self.assertRaisesRegex(GitTreeMaterializationError, "LFS pointer"):
            with self._materialize():
                self.fail("LFS pointer materialization must fail")

    def test_rejects_selected_symlink(self) -> None:
        target = self.source / "Recipe.groovy"
        target.write_bytes(b"duration(20)\n")
        link = self.source / "Linked.groovy"
        try:
            link.symlink_to("Recipe.groovy")
        except OSError as exc:
            self.skipTest(f"symlinks are unavailable: {exc}")
        self._commit()

        with self.assertRaisesRegex(GitTreeMaterializationError, "symbolic link"):
            with self._materialize():
                self.fail("symlink materialization must fail")

    def test_rejects_selected_submodule_gitlink(self) -> None:
        (self.source / "Recipe.groovy").write_bytes(b"duration(20)\n")
        commit = self._commit()
        self._git(
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{commit},pack/nested-module",
        )
        self._git("commit", "--quiet", "-m", "gitlink fixture")

        with self.assertRaisesRegex(GitTreeMaterializationError, "submodule gitlink"):
            with self._materialize():
                self.fail("gitlink materialization must fail")


if __name__ == "__main__":
    unittest.main()
