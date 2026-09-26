"""A V2 environment source lock acquires exact managed bytes from local Git."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from workbench_core.environment_reconstruction import (
    ReconstructionError, build_share, plan_import,
    plan_project_import, apply_project_import,
)
from workbench_core.host_filesystem import HostFilesystemError
from workbench_core.user_preferences import register_workspace, set_workspace_selection
from workbench_core.source_checkouts import SourceCheckoutError

from test_environment_reconstruction import SOURCE_SUITE, _environment, _suite


class EnvironmentProjectImportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.git = Path(shutil.which("git") or "")
        if not self.git.is_absolute():
            self.skipTest("Git is unavailable")
        self.source_suite = _suite(self.root / "source-suite")
        self.target_suite = _suite(self.root / "target-suite")
        self.source_user = self.root / "source-user"
        self.target_user = self.root / "target-user"
        self.source_workspace = self.source_user / "workspace"
        self.target_workspace = self.target_user / "workspace"
        self.source_workspace.mkdir(parents=True)
        self.target_workspace.mkdir(parents=True)
        self.source_environment = _environment(self.source_user)
        self.target_environment = _environment(self.target_user)
        register_workspace("pack", str(self.source_workspace), environment=self.source_environment)
        set_workspace_selection(
            "pack", profile_config=str(self.source_suite / "workbench.toml"),
            environment=self.source_environment,
        )
        self.work = self.root / "work"
        self.remote = self.root / "remote.git"
        self._git("init", "--initial-branch=main", str(self.work))
        self._git("-C", str(self.work), "config", "user.name", "Workbench Test")
        self._git("-C", str(self.work), "config", "user.email", "test@example.invalid")
        (self.work / "pack.marker").write_bytes(b"exact project bytes\n")
        self._git("-C", str(self.work), "add", "pack.marker")
        self._git("-C", str(self.work), "commit", "-m", "fixture")
        self.commit = self._git("-C", str(self.work), "rev-parse", "HEAD").strip()
        self.tree = self._git("-C", str(self.work), "rev-parse", "HEAD^{tree}").strip()
        self._git("clone", "--bare", str(self.work), str(self.remote))
        self._attach_source_lock()
        self.share = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True,
        )

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            [str(self.git), *args], check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        return result.stdout

    def _attach_source_lock(self) -> None:
        for suite in (self.source_suite, self.target_suite):
            profile = suite / "profiles/packs/supersymmetry/profile.yaml"
            text = profile.read_text(encoding="utf-8")
            marker = "    platform_profile_id: workbench-platform:cleanroom:provisional\n    maturity: experimental\n"
            if marker in text:
                replaced = text.replace(
                    marker,
                    "    platform_profile_id: workbench-platform:cleanroom:provisional\n"
                    "    source_lock: source-locks/legacy-forge/source-lock.json\n"
                    "    maturity: experimental\n", 1,
                )
                self.assertNotEqual(text, replaced)
                profile.write_text(replaced, encoding="utf-8")
            source = SOURCE_SUITE / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
            value = json.loads(source.read_text(encoding="utf-8"))
            pack = value["pack"]
            pack.update({
                "repository": "https://example.invalid/project.git",
                "revision": self.commit, "tree": self.tree,
            })
            value["lock_id"] = "workbench-local-git-test:sha256:" + sha256(
                json.dumps(pack, sort_keys=True).encode("utf-8")
            ).hexdigest()
            destination = suite / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )

    def _plan(self):
        return plan_project_import(
            self.target_suite, self.share, workspace_name="shared",
            workspace=self.target_workspace, git_executable=self.git,
            transport=str(self.remote), branch="main", required_paths=("pack.marker",),
            environment=self.target_environment,
        )

    def _apply(self, plan):
        return apply_project_import(
            self.target_suite, self.share, expected_plan_id=plan["plan_id"],
            workspace_name="shared", workspace=self.target_workspace,
            git_executable=self.git, transport=str(self.remote), branch="main",
            required_paths=("pack.marker",), environment=self.target_environment,
        )

    def test_acquire_then_reuse_exact_tree_without_changing_v3_import(self) -> None:
        earlier = plan_import(
            self.target_suite, self.share, workspace_name="shared",
            workspace=self.target_workspace, environment=self.target_environment,
        )
        self.assertEqual("workbench-environment-import-plan-v3", earlier["format"])
        self.assertIn("workspace-project-bytes", earlier["unresolved_inputs"])
        plan = self._plan()
        self.assertEqual(("ready", "acquire"), (plan["state"], plan["action"]))
        self.assertEqual(self.share["lock"]["project_source_lock"], plan["project_source_lock"])
        result = self._apply(plan)
        self.assertEqual("workbench-environment-project-import-result-v1", result["format"])
        self.assertEqual("acquired", result["outcome"])
        self.assertNotIn("workspace-project-bytes", result["unresolved_inputs"])
        self.assertIn("optional-module-packages", result["unresolved_inputs"])
        checkout = Path(result["managed_destination"])
        self.assertEqual(b"exact project bytes\n", (checkout / "pack.marker").read_bytes())
        self.assertEqual(self.commit, self._git("-C", str(checkout), "rev-parse", "HEAD").strip())
        self.assertEqual(self.tree, self._git("-C", str(checkout), "rev-parse", "HEAD^{tree}").strip())
        self.assertTrue(Path(result["acquisition_receipt"]["path"]).is_file())
        self.assertTrue(Path(result["resource"]["path"]).is_file())
        repeat = self._plan()
        self.assertEqual(("ready", "reuse"), (repeat["state"], repeat["action"]))
        with patch("workbench_core.environment_project_import.CoreSourceCheckouts.open", side_effect=AssertionError("reclone")):
            reused = self._apply(repeat)
        self.assertEqual("reused", reused["outcome"])
        self.assertEqual(checkout, Path(reused["managed_destination"]))

    def test_v3_tool_locked_share_acquires_project_without_claiming_tool_bytes(self) -> None:
        self.share = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        plan = self._plan()
        self.assertEqual(("ready", "workbench-environment-project-import-plan-v2"),
                         (plan["state"], plan["format"]))
        self.assertEqual(self.share["lock"]["managed_tool_lock"], plan["managed_tool_lock"])
        result = self._apply(plan)
        self.assertEqual("workbench-environment-project-import-result-v2", result["format"])
        self.assertEqual(plan["managed_tool_lock"], result["managed_tool_lock"])
        self.assertNotIn("workspace-project-bytes", result["unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", result["unresolved_inputs"])
        self.assertEqual(self.commit, self._git(
            "-C", result["managed_destination"], "rev-parse", "HEAD",
        ).strip())
        repeat = self._plan()
        self.assertEqual(("ready", "reuse"), (repeat["state"], repeat["action"]))
        self.assertEqual("reused", self._apply(repeat)["outcome"])

    def test_moved_remote_ref_and_changed_checkout_do_not_pass_review(self) -> None:
        plan = self._plan()
        (self.work / "pack.marker").write_bytes(b"new channel bytes\n")
        self._git("-C", str(self.work), "add", "pack.marker")
        self._git("-C", str(self.work), "commit", "-m", "moved")
        self._git("-C", str(self.work), "push", str(self.remote), "main:main")
        with self.assertRaisesRegex(SourceCheckoutError, "channel moved"):
            self._apply(plan)
        self.assertFalse(Path(plan["managed_destination"]).exists())
        self.assertFalse((self.target_user / "state/evidence/project-acquisition").exists())

        # Restore the reviewed branch, then modify the published worktree.
        self._git("-C", str(self.work), "push", "--force", str(self.remote), f"{self.commit}:main")
        retry = self._plan()
        result = self._apply(retry)
        (Path(result["managed_destination"]) / "pack.marker").write_bytes(b"local tamper\n")
        blocked = self._plan()
        self.assertEqual("blocked", blocked["state"])
        self.assertIn("worktree differs", "; ".join(blocked["blockers"]))

    def test_missing_transport_and_other_host_are_blocked(self) -> None:
        with self.assertRaisesRegex(ReconstructionError, "mirror is unavailable"):
            plan_project_import(
                self.target_suite, self.share, workspace_name="shared",
                workspace=self.target_workspace, git_executable=self.git,
                transport=str(self.root / "missing.git"), branch="main",
                required_paths=("pack.marker",), environment=self.target_environment,
            )
        other = {"os": "windows", "architecture": "x64"}
        if self.share["lock"]["host_variant"] == other:
            other = {"os": "linux", "architecture": "x64"}
        blocked = plan_project_import(
            self.target_suite, self.share, workspace_name="shared",
            workspace=self.target_workspace, git_executable=self.git,
            transport=str(self.remote), branch="main", required_paths=("pack.marker",),
            host=other, environment=self.target_environment,
        )
        self.assertEqual("blocked", blocked["state"])

    def test_failed_checkout_promotion_rolls_back_exact_receipt(self) -> None:
        from workbench_core import source_checkouts

        plan = self._plan()
        original = source_checkouts._rename_noreplace
        failed = False

        def fail_promotion(source: Path, destination: Path) -> None:
            nonlocal failed
            if source.name.startswith(".workbench-acquire-") and not failed:
                failed = True
                raise OSError("injected promotion failure")
            original(source, destination)

        with patch.object(source_checkouts, "_rename_noreplace", side_effect=fail_promotion):
            with self.assertRaisesRegex(SourceCheckoutError, "cannot publish acquired checkout"):
                self._apply(plan)
        self.assertFalse(Path(plan["managed_destination"]).exists())
        self.assertEqual([], list((self.target_user / "state/evidence/project-acquisition").glob("*.json")))
        self.assertEqual("ready", self._plan()["state"])
        self.assertEqual("acquired", self._apply(self._plan())["outcome"])

    def test_redirecting_local_mirror_is_rejected(self) -> None:
        alias = self.root / "mirror-alias"
        alias.symlink_to(self.remote, target_is_directory=True)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            plan_project_import(
                self.target_suite, self.share, workspace_name="shared",
                workspace=self.target_workspace, git_executable=self.git,
                transport=str(alias), branch="main", required_paths=("pack.marker",),
                environment=self.target_environment,
            )

    def test_wrong_reviewed_tree_is_rejected_before_publication(self) -> None:
        self.tree = "0" * 40
        self._attach_source_lock()
        self.share = build_share(
            self.source_suite, "pack", environment=self.source_environment,
            bind_project_source_lock=True,
        )
        plan = self._plan()
        with self.assertRaisesRegex(SourceCheckoutError, "tree differs"):
            self._apply(plan)
        self.assertFalse(Path(plan["managed_destination"]).exists())
        self.assertFalse((self.target_user / "state/evidence/project-acquisition").exists())

    def test_hidden_or_ignored_worktree_change_blocks_reuse(self) -> None:
        result = self._apply(self._plan())
        checkout = Path(result["managed_destination"])
        self._git("-C", str(checkout), "update-index", "--skip-worktree", "pack.marker")
        (checkout / "pack.marker").write_bytes(b"hidden local bytes\n")
        self.assertEqual("blocked", self._plan()["state"])
        self._git("-C", str(checkout), "update-index", "--no-skip-worktree", "pack.marker")
        (checkout / "pack.marker").write_bytes(b"exact project bytes\n")
        (checkout / ".git/info/exclude").write_text("ignored-local\n", encoding="utf-8")
        (checkout / "ignored-local").write_bytes(b"unreviewed bytes\n")
        self.assertEqual("blocked", self._plan()["state"])

    def test_interrupted_receipt_and_unprivate_store_block_safely(self) -> None:
        plan = self._plan()
        with patch(
            "workbench_core.environment_project_import.secure_private_path",
            side_effect=HostFilesystemError("filesystem cannot retain private mode"),
        ):
            with self.assertRaisesRegex(ReconstructionError, "private custody"):
                self._apply(plan)
        self.assertFalse(Path(plan["managed_destination"]).exists())
        receipt = Path(plan["state_root"]) / "evidence/project-acquisition" / (
            plan["acquisition_receipt_id"].rsplit(":", 1)[-1] + ".json"
        )
        receipt.parent.mkdir(parents=True, mode=0o700)
        receipt.write_bytes(b"interrupted receipt\n")
        blocked = self._plan()
        self.assertEqual("blocked", blocked["state"])
        self.assertIn("recovery is required", "; ".join(blocked["blockers"]))
        self.assertEqual(b"interrupted receipt\n", receipt.read_bytes())

    def test_private_store_loss_blocks_reuse(self) -> None:
        result = self._apply(self._plan())
        checkout = Path(result["managed_destination"])
        checkout.parent.chmod(0o755)
        blocked = self._plan()
        self.assertEqual("blocked", blocked["state"])
        self.assertIn("no longer private", "; ".join(blocked["blockers"]))


if __name__ == "__main__":
    unittest.main()
