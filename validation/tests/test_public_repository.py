"""Regression tests for public-repository and independent-release planning."""

from __future__ import annotations

from copy import deepcopy
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SHELL_SOURCE = ROOT / "modules/workbench-shell/src"
if str(SHELL_SOURCE) not in sys.path:
    sys.path.insert(0, str(SHELL_SOURCE))

sys.path.insert(0, str(ROOT / "tools"))

from repository_policy import (  # noqa: E402
    PublicRepositoryError,
    load_public_repository,
    load_release_units,
    public_repository_summary,
    validate_public_repository,
    validate_release_units,
)

EXPORT_SPEC = importlib.util.spec_from_file_location(
    "workbench_prepare_public_export",
    ROOT / "tools/prepare_public_export.py",
)
if EXPORT_SPEC is None or EXPORT_SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load public export implementation")
PUBLIC_EXPORT = importlib.util.module_from_spec(EXPORT_SPEC)
EXPORT_SPEC.loader.exec_module(PUBLIC_EXPORT)


def _public_markdown_paths(root: Path):
    generated = {".git", ".workbench", ".pixi", ".vscode-test", "build", "node_modules"}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in generated)
        # Include matching directory names too, as Path.rglob("*.md") does;
        # the caller must still fail if a selected path cannot be read as text.
        for name in sorted([*names, *files]):
            if fnmatch.fnmatch(name, "*.md"):
                yield Path(directory) / name


class PublicRepositoryPlanTests(unittest.TestCase):
    def test_public_markdown_has_no_predecessor_executable_paths(self) -> None:
        forbidden = (
            "tools/deconstruction/",
            "tools/blueprints/",
            "modules/ide-surfaces/",
        )
        failures: list[str] = []
        for path in _public_markdown_paths(ROOT):
            text = path.read_text(encoding="utf-8")
            for prefix in forbidden:
                if prefix in text:
                    failures.append(f"{path.relative_to(ROOT)}: {prefix}")
        self.assertEqual([], failures)

    def test_markdown_selection_keeps_untracked_paths_and_exact_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            included = {"README.md", "untracked/notes.md", "build-notes/guide.md", ".md"}
            excluded = {
                f"nested/{name}/private.md"
                for name in (".git", ".workbench", ".pixi", ".vscode-test", "build", "node_modules")
            } | {"UPPER.MD", "plain.txt"}
            if os.name == "nt":
                included.add("UPPER.MD")
                excluded.remove("UPPER.MD")
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
            (root / "directory.md").mkdir()
            self.assertEqual(
                included | {"directory.md"},
                {path.relative_to(root).as_posix() for path in _public_markdown_paths(root)},
            )

    def test_checked_plan_distinguishes_public_visibility_from_migration(self) -> None:
        repository = load_public_repository(ROOT)
        self.assertEqual(
            "orthrus-research/workbench",
            repository["destination"]["full_name"],
        )
        self.assertEqual("public", repository["hosting_state"])
        self.assertEqual(
            "clean-root-export",
            repository["history_policy"]["kind"],
        )
        self.assertFalse(
            repository["history_policy"]["existing_branches_published"]
        )
        self.assertFalse(repository["history_policy"]["existing_tags_published"])
        self.assertNotIn("legacy_repositories", repository)
        self.assertEqual(
            {
                "repository_created": True,
                "history_pushed": True,
                "repository_settings_applied": False,
                "public_visibility_verified": True,
            },
            repository["claims"],
        )

    def test_component_authority_owns_distinct_artifacts_and_tags(self) -> None:
        registry = load_release_units(ROOT)
        units = registry["components"]
        self.assertEqual(22, len(units))
        self.assertIn("workbench-axiom-engine", [row["id"] for row in units])
        self.assertIn("workbench-api", [row["id"] for row in units])
        self.assertIn("workbench-profile-supersymmetry", [row["id"] for row in units])
        self.assertTrue(all(row["tags"]["final"] == row["id"] + "/v{version}" for row in units))
        artifact_ids = [
            artifact["id"]
            for row in units
            for artifact in row["artifacts"]
        ]
        self.assertEqual(len(artifact_ids), len(set(artifact_ids)))
        self.assertEqual("public-v1", registry["package"]["release_track"])

    def test_positive_hosting_claim_fails_while_destination_is_uncreated(self) -> None:
        changed = deepcopy(load_public_repository(ROOT))
        changed["hosting_state"] = "not-created"
        changed["claims"] = {key: False for key in changed["claims"]}
        changed["claims"]["repository_created"] = True
        with self.assertRaises(PublicRepositoryError):
            validate_public_repository(changed, ROOT)

    def test_hosting_state_claim_matrix_is_closed(self) -> None:
        private = deepcopy(load_public_repository(ROOT))
        private["hosting_state"] = "private-staging"
        private["claims"]["repository_created"] = True
        private["claims"]["public_visibility_verified"] = False
        self.assertEqual(
            "private-staging",
            validate_public_repository(private, ROOT)["hosting_state"],
        )

        contradictory_private = deepcopy(private)
        contradictory_private["claims"]["public_visibility_verified"] = True
        with self.assertRaises(PublicRepositoryError):
            validate_public_repository(contradictory_private, ROOT)

        incomplete_public = deepcopy(private)
        incomplete_public["hosting_state"] = "public"
        with self.assertRaises(PublicRepositoryError):
            validate_public_repository(incomplete_public, ROOT)

        complete_public = deepcopy(private)
        complete_public["hosting_state"] = "public"
        complete_public["claims"] = {
            key: True for key in complete_public["claims"]
        }
        self.assertEqual(
            "public",
            validate_public_repository(complete_public, ROOT)["hosting_state"],
        )

        public_without_migration = deepcopy(private)
        public_without_migration["hosting_state"] = "public"
        public_without_migration["claims"]["public_visibility_verified"] = True
        public_without_migration["claims"]["history_pushed"] = False
        observed = validate_public_repository(public_without_migration, ROOT)
        self.assertFalse(observed["claims"]["history_pushed"])
        self.assertFalse(observed["claims"]["repository_settings_applied"])

    def test_release_unit_cannot_borrow_another_tag_namespace(self) -> None:
        changed = deepcopy(load_release_units(ROOT))
        changed["components"][0]["tags"]["final"] = (
            "workbench-vscode/v{version}"
        )
        with self.assertRaises(PublicRepositoryError):
            validate_release_units(changed, ROOT)

    def test_cli_exposes_machine_readable_plan(self) -> None:
        completed = subprocess.run(
            [sys.executable, "tools/public_repository.py", "validate", "--json"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual("orthrus-research/workbench", summary["destination"])
        self.assertEqual(public_repository_summary(ROOT), summary)



class PublicExportHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="workbench-public-export-test-",
            dir="/tmp",
        )
        self.base = Path(self.temporary.name)
        self.repository = self.base / "repository"
        self.repository.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Workbench Test")
        self.git("config", "user.email", "workbench@example.invalid")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            check=False,
            env={
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith("GIT_")
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed.stdout.strip()

    def commit(self, message: str = "fixture") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def add_public_authority(self) -> None:
        destination = self.repository / "packaging/release"
        (destination / "schemas").mkdir(parents=True)
        shutil.copyfile(
            ROOT / "packaging/release/public-repository-v1.json",
            destination / "public-repository-v1.json",
        )
        shutil.copyfile(
            ROOT
            / "packaging/release/schemas/workbench-public-repository-v1.schema.json",
            destination / "schemas/workbench-public-repository-v1.schema.json",
        )
        tool = self.repository / "tools/prepare_public_export.py"
        tool.parent.mkdir()
        tool.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    def test_commit_inventory_ignores_hidden_worktree_bytes(self) -> None:
        source = self.repository / "source.txt"
        source.write_text("reviewed bytes\n", encoding="utf-8")
        commit = self.commit()
        self.git("update-index", "--assume-unchanged", "source.txt")
        source.write_text("unreviewed secret bytes\n", encoding="utf-8")

        files, _, _ = PUBLIC_EXPORT._commit_inventory(self.repository, commit)
        row = next(row for row in files if row["path"] == "source.txt")
        self.assertEqual(
            hashlib.sha256(b"reviewed bytes\n").hexdigest(),
            row["sha256"],
        )
        self.assertFalse(PUBLIC_EXPORT._index_state_is_plain(self.repository))

    def test_export_rejects_tracked_agent_files_without_relying_on_ignore_rules(self) -> None:
        (self.repository / "README.md").write_text("public usage\n", encoding="utf-8")
        private = self.repository / "nested/agents.local.md"
        private.parent.mkdir()
        private.write_text("private instructions\n", encoding="utf-8")
        commit = self.commit()
        with self.assertRaisesRegex(PUBLIC_EXPORT.PublicExportError, "private harness"):
            PUBLIC_EXPORT._commit_inventory(self.repository, commit)
        self.git("rm", "nested/agents.local.md")
        (self.repository / "notes.md").write_text("ATLAS-M8-G01\n", encoding="utf-8")
        commit = self.commit()
        with self.assertRaisesRegex(PUBLIC_EXPORT.PublicExportError, "private coordination"):
            PUBLIC_EXPORT._commit_inventory(self.repository, commit)

    def test_git_environment_cannot_redirect_repository_inspection(self) -> None:
        (self.repository / "source.txt").write_text("reviewed\n", encoding="utf-8")
        self.commit()
        with mock.patch.dict(
            os.environ,
            {"GIT_DIR": str(self.base / "invented"), "GIT_WORK_TREE": "/"},
        ):
            observed, object_format = PUBLIC_EXPORT._verified_git_root(
                self.repository
            )
        self.assertEqual(self.repository.resolve(), observed)
        self.assertEqual("sha1", object_format)

    def test_symlink_git_tree_entry_is_rejected(self) -> None:
        (self.repository / "source.txt").write_text("reviewed\n", encoding="utf-8")
        (self.repository / "indirect").symlink_to("source.txt")
        commit = self.commit()
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "symlink, gitlink, or non-blob",
        ):
            PUBLIC_EXPORT._commit_inventory(self.repository, commit)

    def test_case_colliding_paths_are_rejected(self) -> None:
        (self.repository / "Source.txt").write_text("one\n", encoding="utf-8")
        (self.repository / "source.txt").write_text("two\n", encoding="utf-8")
        commit = self.commit()
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "collide on case-insensitive filesystems",
        ):
            PUBLIC_EXPORT._commit_inventory(self.repository, commit)

    def test_sensitive_path_and_nonallowlisted_binary_are_rejected(self) -> None:
        (self.repository / ".env").write_text("TOKEN=value\n", encoding="utf-8")
        sensitive_commit = self.commit("sensitive")
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "sensitive path",
        ):
            PUBLIC_EXPORT._commit_inventory(self.repository, sensitive_commit)

        self.git("rm", "-q", ".env")
        (self.repository / "payload.dat").write_bytes(b"\x00\xffpayload")
        binary_commit = self.commit("binary")
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "non-allowlisted binary|binary control",
        ):
            PUBLIC_EXPORT._commit_inventory(self.repository, binary_commit)

    def test_aggregate_size_bound_is_enforced(self) -> None:
        (self.repository / "one.txt").write_text("one\n", encoding="utf-8")
        (self.repository / "two.txt").write_text("two\n", encoding="utf-8")
        commit = self.commit()
        with (
            mock.patch.object(PUBLIC_EXPORT, "MAX_TOTAL_SOURCE_BYTES", 7),
            self.assertRaisesRegex(
                PUBLIC_EXPORT.PublicExportError,
                "aggregate size bound",
            ),
        ):
            PUBLIC_EXPORT._commit_inventory(self.repository, commit)

    def test_dirty_plan_uses_commit_bytes_and_reports_blockers(self) -> None:
        self.add_public_authority()
        source = self.repository / "README.md"
        source.write_text("reviewed\n", encoding="utf-8")
        commit = self.commit()
        source.write_text("dirty secret\n", encoding="utf-8")

        plan = PUBLIC_EXPORT.public_export_plan(commit, root=self.repository)
        row = next(row for row in plan["files"] if row["path"] == "README.md")
        self.assertEqual(hashlib.sha256(b"reviewed\n").hexdigest(), row["sha256"])
        self.assertFalse(plan["eligible"])
        self.assertIn("working-tree-not-clean", plan["blockers"])
        self.assertIn("secret-scan-receipt-required", plan["blockers"])

    def test_scan_input_is_exact_and_cannot_be_mistaken_for_an_export(self) -> None:
        self.add_public_authority()
        (self.repository / "README.md").write_text("reviewed\n", encoding="utf-8")
        commit = self.commit()
        output = self.base / "scan-input"

        result = PUBLIC_EXPORT.stage_secret_scan_input(
            output,
            commit,
            root=self.repository,
        )

        self.assertFalse(result["eligible_for_public_import"])
        self.assertEqual("local-secret-scan-input-only", result["purpose"])
        self.assertEqual(
            "reviewed\n",
            (output / "tree/README.md").read_text(encoding="utf-8"),
        )
        self.assertTrue((output / "scan-input-manifest.json").is_file())
        self.assertFalse((output / "export-manifest.json").exists())
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "root inventory is not exact",
        ):
            PUBLIC_EXPORT.verify_export(output)

    def test_receipt_bound_build_is_reproducible_and_verifiable(self) -> None:
        self.add_public_authority()
        (self.repository / "README.md").write_text("reviewed\n", encoding="utf-8")
        executable = self.repository / "tools/reviewed-tool"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        commit = self.commit()
        plan = PUBLIC_EXPORT.public_export_plan(commit, root=self.repository)
        receipt = self.base / "secret-scan-receipt.json"
        receipt.write_bytes(
            PUBLIC_EXPORT._canonical_pretty_json(plan["secret_scan"]["template"])
        )

        first = self.base / "export-one"
        second = self.base / "export-two"
        result = PUBLIC_EXPORT.build_export(
            first,
            commit,
            receipt,
            root=self.repository,
        )
        PUBLIC_EXPORT.build_export(
            second,
            commit,
            receipt,
            root=self.repository,
        )
        self.assertEqual(commit, result["source_revision"])
        self.assertEqual(
            (first / "export-manifest.json").read_bytes(),
            (second / "export-manifest.json").read_bytes(),
        )
        self.assertEqual(
            "reviewed\n",
            (first / "tree/README.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            0o755,
            (first / "tree/tools/reviewed-tool").stat().st_mode & 0o777,
        )

        (first / "tree/injected.txt").write_text("stale\n", encoding="utf-8")
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "inventory does not match",
        ):
            PUBLIC_EXPORT.verify_export(first)

    def test_receipt_for_another_tree_is_rejected(self) -> None:
        self.add_public_authority()
        (self.repository / "README.md").write_text("reviewed\n", encoding="utf-8")
        commit = self.commit()
        plan = PUBLIC_EXPORT.public_export_plan(commit, root=self.repository)
        receipt_value = plan["secret_scan"]["template"]
        receipt_value["tree_sha256"] = "0" * 64
        receipt = self.base / "wrong-receipt.json"
        receipt.write_bytes(PUBLIC_EXPORT._canonical_pretty_json(receipt_value))
        with self.assertRaisesRegex(
            PUBLIC_EXPORT.PublicExportError,
            "exact-tree Gitleaks attestation",
        ):
            PUBLIC_EXPORT.public_export_plan(
                commit,
                receipt,
                root=self.repository,
            )


if __name__ == "__main__":
    unittest.main()
