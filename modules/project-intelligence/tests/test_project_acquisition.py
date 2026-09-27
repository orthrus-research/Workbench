"""Focused tests for exact profile-owned project acquisition."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
for dependency in (ROOT / "api/src", ROOT / "core/src"):
    if str(dependency) not in sys.path:
        sys.path.insert(0, str(dependency))
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_core.host_services import install_local_host_services  # noqa: E402
from workbench_core import source_checkouts as core_source_checkouts  # noqa: E402
from workbench_api.source_checkouts import (  # noqa: E402
    SourceCheckoutError, open_source_checkout, source_checkouts_scope,
)
from workbench_project_intelligence import project_acquisition  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "Git is required for acquisition tests")
class ProjectAcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_local_host_services()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="workbench-project-acquire-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.remote = self.root / "remote.git"
        self.source.mkdir()
        self.environment = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "PATH": "",
        }
        self.git = str(Path(shutil.which("git") or "git").resolve())
        self._run(self.source, "init", "--quiet")
        self._run(self.source, "config", "user.name", "Workbench Test")
        self._run(self.source, "config", "user.email", "workbench@example.invalid")
        for directory in ("config", "groovy", "mods"):
            selected = self.source / directory
            selected.mkdir()
            (selected / ".keep").write_text("fixture\n", encoding="utf-8")
        (self.source / "pack.toml").write_text("name = 'Fixture'\n", encoding="utf-8")
        (self.source / "index.toml").write_text("hash-format = 'sha256'\n", encoding="utf-8")
        self._run(self.source, "add", "--all")
        self._run(self.source, "commit", "--quiet", "-m", "fixture")
        self._run(self.source, "branch", "-M", "master-ceu")
        self.commit = self._run(self.source, "rev-parse", "HEAD").stdout.strip()
        completed = subprocess.run(
            [self.git, "clone", "--quiet", "--bare", str(self.source), str(self.remote)],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, **self.environment},
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.profile_path = self.root / "acquisition.json"
        self.profile = {
            "format": project_acquisition.PROFILE_FORMAT,
            "schema_version": 1,
            "profile_id": "workbench-pack:fixture:acquisition-v1",
            "project_id": "fixture",
            "display_name": "Fixture",
            "remote": {
                "kind": "git",
                "url": str(self.remote),
                "provider": "local-test",
                "pull_request_ref_template": None,
            },
            "default_channel": "current",
            "channels": [
                {
                    "id": "current",
                    "aliases": ["latest", "master-ceu"],
                    "remote_ref": "refs/heads/master-ceu",
                    "checkout_branch": "master-ceu",
                }
            ],
            "workspace": {
                "required_paths": [
                    {"path": "pack.toml", "kind": "file"},
                    {"path": "index.toml", "kind": "file"},
                    {"path": "config", "kind": "directory"},
                    {"path": "groovy", "kind": "directory"},
                    {"path": "mods", "kind": "directory"},
                ]
            },
        }
        self.profile_path.write_text(
            json.dumps(self.profile, indent=2) + "\n", encoding="utf-8"
        )

    def _run(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [self.git, "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, **self.environment},
            timeout=30,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return completed

    def _plan(self, destination: Path, channel: str = "latest") -> dict[str, object]:
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        return project_acquisition.build_acquisition_plan(
            profile,
            channel_name=channel,
            destination=destination,
            git_executable=self.git,
            environment=self.environment,
        )

    def _plan_v2(
        self, destination: Path, channel: str = "latest"
    ) -> dict[str, object]:
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        return project_acquisition.build_acquisition_plan_v2(
            profile,
            channel_name=channel,
            destination=destination,
            git_executable=self.git,
            environment=self.environment,
        )

    def test_supersymmetry_profile_and_schema_are_strict_json(self) -> None:
        profile = project_acquisition.load_acquisition_profile(
            ROOT / "profiles/packs/supersymmetry/acquisition-v1.json"
        )
        schema = json.loads(
            (
                ROOT
                / "profiles/packs/supersymmetry/schemas/"
                "workbench-supersymmetry-acquisition-profile-v1.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual("supersymmetry", profile["project_id"])
        self.assertEqual(
            "refs/heads/master-ceu", profile["channels"][0]["remote_ref"]
        )
        self.assertEqual(
            project_acquisition.PROFILE_FORMAT,
            schema["properties"]["format"]["const"],
        )

    def test_plan_resolves_latest_alias_to_one_exact_commit(self) -> None:
        destination = self.root / "checkout"
        plan = self._plan(destination)

        self.assertEqual(project_acquisition.PLAN_FORMAT, plan["format"])
        self.assertEqual("current", plan["channel_id"])
        self.assertEqual(self.commit, plan["resolved_commit"])
        self.assertEqual(str(destination), plan["destination"])
        self.assertEqual(self.git, plan["git_executable"])
        self.assertFalse(destination.exists())

    def test_apply_publishes_exact_checkout_and_external_receipt(self) -> None:
        destination = self.root / "checkout"
        state = self.root / "state"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan(destination)

        result = project_acquisition.apply_acquisition_plan(
            profile,
            plan,
            state_root=state,
            environment=self.environment,
            clone_timeout=60,
        )

        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(project_acquisition.RESULT_FORMAT, result["format"])
        self.assertEqual(self.commit, result["resolved_commit"])
        self.assertEqual(self.commit, self._run(destination, "rev-parse", "HEAD").stdout.strip())
        self.assertTrue((destination / "groovy/.keep").is_file())
        promisor = subprocess.run(
            [
                self.git,
                "-C",
                str(destination),
                "config",
                "--local",
                "--get",
                "remote.origin.promisor",
            ],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, **self.environment},
            timeout=30,
        )
        self.assertNotEqual(0, promisor.returncode)
        receipt_path = Path(str(result["receipt_path"]))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(project_acquisition.RECEIPT_FORMAT, receipt["format"])
        self.assertEqual(self.commit, receipt["resolved_commit"])
        self.assertEqual(
            project_acquisition._identity(
                "workbench-project-acquisition",
                {key: value for key, value in receipt.items()
                 if key not in {"format", "schema_version", "receipt_id", "acquired_at"}},
            ),
            receipt["receipt_id"],
        )
        self.assertEqual(
            state / "evidence/project-acquisition"
            / f"{receipt['receipt_id'].rsplit(':', 1)[-1]}.json",
            receipt_path,
        )
        self.assertEqual(0, receipt_path.stat().st_mode & 0o077)
        self.assertTrue(receipt_path.is_relative_to(state))
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_v2_plan_is_read_only_and_apply_creates_missing_parents(self) -> None:
        destination = self.root / "new" / "developer" / "checkout"
        state = self.root / "state"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)

        plan = self._plan_v2(destination)

        self.assertEqual(project_acquisition.PLAN_FORMAT_V2, plan["format"])
        self.assertEqual(2, plan["schema_version"])
        self.assertEqual("create", plan["destination_parent_action"])
        self.assertFalse(destination.parent.exists())

        result = project_acquisition.apply_acquisition_plan(
            profile,
            plan,
            state_root=state,
            environment=self.environment,
            clone_timeout=60,
        )

        self.assertEqual("acquired", result["outcome"])
        self.assertEqual(project_acquisition.RESULT_FORMAT_V2, result["format"])
        self.assertEqual("create", result["destination_parent_action"])
        self.assertEqual(str(destination.parent), result["destination_parent"])
        self.assertEqual(self.commit, self._run(destination, "rev-parse", "HEAD").stdout.strip())
        self.assertTrue(destination.parent.is_dir())

    def test_v2_failed_apply_removes_only_empty_parents_it_created(self) -> None:
        destination = self.root / "new" / "developer" / "checkout"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan_v2(destination)

        with mock.patch.object(
            core_source_checkouts,
            "publish_immutable_bytes",
            side_effect=OSError(
                "simulated receipt failure"
            ),
        ):
            with self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError,
                "simulated receipt failure",
            ):
                project_acquisition.apply_acquisition_plan(
                    profile,
                    plan,
                    state_root=self.root / "state",
                    environment=self.environment,
                )

        self.assertFalse((self.root / "new").exists())

    def test_receipt_failure_does_not_publish_checkout(self) -> None:
        destination = self.root / "checkout"
        state = self.root / "state"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan(destination)

        with mock.patch.object(
            core_source_checkouts,
            "publish_immutable_bytes",
            side_effect=OSError(
                "simulated receipt failure"
            ),
        ):
            with self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError,
                "simulated receipt failure",
            ):
                project_acquisition.apply_acquisition_plan(
                    profile,
                    plan,
                    state_root=state,
                    environment=self.environment,
                    clone_timeout=60,
                )

        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_checkout_publish_failure_removes_new_receipt(self) -> None:
        destination = self.root / "checkout"
        state = self.root / "state"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan(destination)
        real_rename = core_source_checkouts._rename_noreplace

        def fail_checkout_publish(source: Path, target: Path) -> None:
            if target == destination:
                raise OSError("simulated checkout publication failure")
            real_rename(source, target)

        with mock.patch(
            "workbench_core.source_checkouts._rename_noreplace",
            side_effect=fail_checkout_publish,
        ):
            with self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError,
                "cannot publish acquired checkout",
            ):
                project_acquisition.apply_acquisition_plan(
                    profile,
                    plan,
                    state_root=state,
                    environment=self.environment,
                    clone_timeout=60,
                )

        self.assertFalse(destination.exists())
        self.assertFalse(any(state.rglob("*.json")))
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_checkout_publication_does_not_replace_a_later_destination(self) -> None:
        destination = self.root / "checkout"
        state = self.root / "state"
        plan = self._plan(destination)
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        real_rename = core_source_checkouts._rename_noreplace

        def create_competing_destination(source: Path, target: Path) -> None:
            if target == destination:
                destination.mkdir()
            real_rename(source, target)

        with mock.patch.object(
            core_source_checkouts, "_rename_noreplace",
            side_effect=create_competing_destination,
        ):
            with self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError,
                "cannot publish acquired checkout",
            ):
                project_acquisition.apply_acquisition_plan(
                    profile, plan, state_root=state, environment=self.environment,
                )
        self.assertTrue(destination.is_dir())
        self.assertEqual([], list(destination.iterdir()))
        self.assertFalse(any(state.rglob("*.json")))
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_receipt_replacement_is_preserved_when_checkout_promotion_fails(self) -> None:
        destination = self.root / "checkout"
        state = self.root / "state"
        plan = self._plan(destination)
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        real_rename = core_source_checkouts._rename_noreplace

        def replace_receipt_then_fail(source: Path, target: Path) -> None:
            if target == destination:
                receipt = next((state / "evidence/project-acquisition").glob("*.json"))
                replacement = receipt.with_suffix(".replacement")
                replacement.write_bytes(b"later writer\n")
                replacement.chmod(0o600)
                os.replace(replacement, receipt)
                raise OSError("simulated checkout publication failure")
            real_rename(source, target)

        with mock.patch.object(
            core_source_checkouts, "_rename_noreplace",
            side_effect=replace_receipt_then_fail,
        ):
            with self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError,
                "recovery is required",
            ):
                project_acquisition.apply_acquisition_plan(
                    profile, plan, state_root=state, environment=self.environment,
                )
        receipt = next((state / "evidence/project-acquisition").glob("*.json"))
        self.assertEqual(b"later writer\n", receipt.read_bytes())
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_core_tree_precondition_is_distinct_from_commit_only_v1_plan(self) -> None:
        destination = self.root / "checkout"
        plan = self._plan(destination)
        self.assertNotIn("resolved_tree", plan)
        exact_tree = self._run(self.source, "rev-parse", "HEAD^{tree}").stdout.strip()
        with open_source_checkout(
            destination, git_executable=self.git,
            remote_url=str(self.remote), checkout_branch="master-ceu",
            expected_commit=self.commit, expected_tree=exact_tree,
            environment=self.environment, timeout_seconds=60,
        ) as checkout:
            self.assertEqual(self.commit, checkout.observed_commit)
            self.assertEqual(exact_tree, checkout.observed_tree)
            self.assertTrue((checkout.staging_root / "pack.toml").is_file())
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))
        with self.assertRaises(SourceCheckoutError) as mismatch:
            with open_source_checkout(
                destination, git_executable=self.git,
                remote_url=str(self.remote), checkout_branch="master-ceu",
                expected_commit=self.commit, expected_tree="0" * len(self.commit),
                environment=self.environment, timeout_seconds=60,
            ):
                self.fail("mismatched tree must not be exposed")
        self.assertEqual("tree", mismatch.exception.code)
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_core_binding_and_redirected_parent_fail_before_clone(self) -> None:
        destination = self.root / "checkout"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan(destination)
        with source_checkouts_scope(None):
            with self.assertRaisesRegex(project_acquisition.ProjectAcquisitionError, "Core"):
                project_acquisition.apply_acquisition_plan(
                    profile, plan, state_root=self.root / "state",
                    environment=self.environment,
                )
        real = self.root / "real"
        (real / "developer").mkdir(parents=True)
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        redirected = link / "developer" / "checkout"
        redirected_plan = self._plan(redirected)
        with self.assertRaisesRegex(project_acquisition.ProjectAcquisitionError, "redirect"):
            project_acquisition.apply_acquisition_plan(
                profile, redirected_plan, state_root=self.root / "state",
                environment=self.environment,
            )
        self.assertFalse((real / "developer" / "checkout").exists())
        self.assertFalse(any((real / "developer").glob(".workbench-acquire-*")))

    def test_private_store_unavailable_fails_without_exposing_checkout(self) -> None:
        destination = self.root / "checkout"
        state = self.root / "state"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan(destination)
        with mock.patch.object(
            core_source_checkouts, "secure_private_path",
            side_effect=OSError("simulated WSL mount without private metadata"),
        ):
            with self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError,
                "cannot secure acquisition receipt store",
            ):
                project_acquisition.apply_acquisition_plan(
                    profile, plan, state_root=state, environment=self.environment,
                )
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))
        self.assertFalse(any(state.rglob("*.json")))

    def test_required_path_cannot_traverse_cloned_symlink(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / ".keep").write_text("not acquired\n", encoding="utf-8")
        shutil.rmtree(self.source / "config")
        (self.source / "config").symlink_to(outside, target_is_directory=True)
        self._run(self.source, "add", "--all")
        self._run(self.source, "commit", "--quiet", "-m", "symlink")
        self._run(self.source, "push", "--quiet", str(self.remote), "master-ceu")
        self.profile["workspace"]["required_paths"][2] = {
            "path": "config/.keep", "kind": "file",
        }
        self.profile_path.write_text(json.dumps(self.profile), encoding="utf-8")
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        destination = self.root / "checkout"
        plan = self._plan(destination)
        with self.assertRaisesRegex(project_acquisition.ProjectAcquisitionError, "redirect"):
            project_acquisition.apply_acquisition_plan(
                profile, plan, state_root=self.root / "state",
                environment=self.environment,
            )
        self.assertFalse(destination.exists())
        self.assertFalse(any(self.root.glob(".workbench-acquire-*")))

    def test_remote_movement_invalidates_reviewed_plan_before_clone(self) -> None:
        destination = self.root / "checkout"
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        plan = self._plan(destination)
        (self.source / "pack.toml").write_text("name = 'Moved'\n", encoding="utf-8")
        self._run(self.source, "add", "pack.toml")
        self._run(self.source, "commit", "--quiet", "-m", "move")
        self._run(self.source, "push", "--quiet", str(self.remote), "master-ceu")

        with self.assertRaisesRegex(
            project_acquisition.ProjectAcquisitionError,
            "plan changed",
        ):
            project_acquisition.apply_acquisition_plan(
                profile,
                plan,
                state_root=self.root / "state",
                environment=self.environment,
            )

        self.assertFalse(destination.exists())

    def test_non_tty_cli_renders_plan_without_writing(self) -> None:
        destination = self.root / "checkout"
        output = io.StringIO()
        error = io.StringIO()

        status = project_acquisition.main(
            [
                "fixture",
                "--channel",
                "latest",
                "--destination",
                str(destination),
                "--git-executable",
                self.git,
                "--json",
            ],
            profiles={"fixture": self.profile_path},
            default_state_root=self.root / "state",
            input_stream=io.StringIO(),
            output=output,
            error=error,
            environment=self.environment,
        )

        self.assertEqual(0, status, error.getvalue())
        self.assertEqual(
            project_acquisition.PLAN_FORMAT_V2,
            json.loads(output.getvalue())["format"],
        )
        self.assertFalse(destination.exists())

    def test_non_tty_cli_plans_nested_missing_destination_without_writing(self) -> None:
        destination = self.root / "new" / "developer" / "checkout"
        output = io.StringIO()
        error = io.StringIO()

        status = project_acquisition.main(
            [
                "fixture",
                "--channel",
                "latest",
                "--destination",
                str(destination),
                "--git-executable",
                self.git,
                "--json",
            ],
            profiles={"fixture": self.profile_path},
            default_state_root=self.root / "state",
            input_stream=io.StringIO(),
            output=output,
            error=error,
            environment=self.environment,
        )

        self.assertEqual(0, status, error.getvalue())
        plan = json.loads(output.getvalue())
        self.assertEqual(project_acquisition.PLAN_FORMAT_V2, plan["format"])
        self.assertEqual("create", plan["destination_parent_action"])
        self.assertFalse(destination.parent.exists())

    def test_branch_list_and_acquisition_accept_git_valid_feature_name(self) -> None:
        branch = "feature/next+µ"
        self._run(self.source, "branch", branch)
        self._run(self.source, "push", str(self.remote), branch)
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        listing = project_acquisition.list_remote_branches(
            profile, git_executable=self.git, environment=self.environment,
        )
        self.assertEqual(project_acquisition.BRANCH_LIST_FORMAT, listing["format"])
        self.assertIn(
            {"name": branch, "commit": self.commit}, listing["branches"],
        )
        target = self.root / "new" / "feature-checkout"
        plan = project_acquisition.build_branch_acquisition_plan(
            profile, branch_name=branch, destination=target,
            git_executable=self.git, environment=self.environment,
        )
        self.assertEqual(project_acquisition.PLAN_FORMAT_V3, plan["format"])
        self.assertEqual("refs/heads/" + branch, plan["remote_ref"])
        self.assertEqual(self.commit, plan["resolved_commit"])
        result = project_acquisition.apply_acquisition_plan(
            profile, plan, state_root=self.root / "state",
            environment=self.environment, clone_timeout=60,
        )
        self.assertEqual(project_acquisition.RESULT_FORMAT_V3, result["format"])
        self.assertTrue(result["profile_compatible"])
        self.assertFalse(result["source_only"])
        self.assertEqual(self.commit, self._run(target, "rev-parse", "HEAD").stdout.strip())
        receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
        self.assertEqual(project_acquisition.RECEIPT_FORMAT_V2, receipt["format"])
        self.assertEqual(branch, receipt["branch_name"])

    def test_source_only_branch_retains_checkout_without_pack_shape(self) -> None:
        branch = "feature/source-only"
        self._run(self.source, "checkout", "-b", branch)
        self._run(self.source, "rm", "pack.toml")
        self._run(self.source, "commit", "--quiet", "-m", "remove pack file")
        self._run(self.source, "push", str(self.remote), branch)
        profile = project_acquisition.load_acquisition_profile(self.profile_path)
        target = self.root / "source-only-checkout"
        plan = project_acquisition.build_branch_acquisition_plan(
            profile, branch_name=branch, destination=target, source_only=True,
            git_executable=self.git, environment=self.environment,
        )
        result = project_acquisition.apply_acquisition_plan(
            profile, plan, state_root=self.root / "state",
            environment=self.environment, clone_timeout=60,
        )
        self.assertTrue(target.is_dir())
        self.assertFalse(result["profile_compatible"])
        self.assertIn("pack.toml", result["profile_diagnostic"])
        self.assertEqual([], json.loads(Path(result["receipt_path"]).read_text())["required_paths"])
        strict_target = self.root / "strict-checkout"
        strict = project_acquisition.build_branch_acquisition_plan(
            profile, branch_name=branch, destination=strict_target,
            git_executable=self.git, environment=self.environment,
        )
        with self.assertRaisesRegex(project_acquisition.ProjectAcquisitionError, "pack.toml"):
            project_acquisition.apply_acquisition_plan(
                profile, strict, state_root=self.root / "state",
                environment=self.environment, clone_timeout=60,
            )
        self.assertFalse(strict_target.exists())

    def test_explicit_fork_url_is_https_github_and_bound_to_plan(self) -> None:
        profile = {**self.profile, "remote": {
            **self.profile["remote"], "provider": "github",
            "url": "https://github.com/SymmetricDevs/Supersymmetry.git",
        }}
        with mock.patch.object(project_acquisition, "resolve_remote_commit", return_value=self.commit):
            plan = project_acquisition.build_branch_acquisition_plan(
                profile, branch_name="feature/next+1", destination=self.root / "fork",
                repository_url="https://github.com/Developer/Supersymmetry/",
                git_executable=self.git, environment=self.environment,
            )
        self.assertEqual(
            "https://github.com/Developer/Supersymmetry.git", plan["remote_url"]
        )
        for unsafe in (
            "http://github.com/Developer/Supersymmetry",
            "https://github.com.evil.example/Developer/Supersymmetry",
            "https://user@github.com/Developer/Supersymmetry",
            "https://github.com/Developer/Supersymmetry?x=1",
            "https://github.com:bad/Developer/Supersymmetry",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(
                project_acquisition.ProjectAcquisitionError, "repository"
            ):
                project_acquisition.build_branch_acquisition_plan(
                    profile, branch_name="feature/next+1", destination=self.root / "fork",
                    repository_url=unsafe, git_executable=self.git,
                    environment=self.environment,
                )

    def test_branch_cli_list_plan_and_apply_rejects_moved_head(self) -> None:
        branch = "feature/moving"
        self._run(self.source, "branch", branch)
        self._run(self.source, "push", str(self.remote), branch)
        common = ["fixture", "--git-executable", self.git, "--json"]
        output, error = io.StringIO(), io.StringIO()
        status = project_acquisition.main(
            [*common, "--list-branches"],
            profiles={"fixture": self.profile_path},
            default_state_root=self.root / "state", output=output, error=error,
            environment=self.environment,
        )
        self.assertEqual(0, status, error.getvalue())
        self.assertIn(branch, {row["name"] for row in json.loads(output.getvalue())["branches"]})
        target = self.root / "moving-checkout"
        output, error = io.StringIO(), io.StringIO()
        selection = [*common, "--branch", branch, "--destination", str(target)]
        status = project_acquisition.main(
            [*selection, "--plan"],
            profiles={"fixture": self.profile_path},
            default_state_root=self.root / "state", output=output, error=error,
            environment=self.environment,
        )
        self.assertEqual(0, status, error.getvalue())
        plan = json.loads(output.getvalue())
        self.assertEqual(project_acquisition.PLAN_FORMAT_V3, plan["format"])
        self._run(self.source, "checkout", branch)
        (self.source / "pack.toml").write_text("name = 'Moved'\n", encoding="utf-8")
        self._run(self.source, "add", "pack.toml")
        self._run(self.source, "commit", "--quiet", "-m", "move branch")
        self._run(self.source, "push", str(self.remote), branch)
        output, error = io.StringIO(), io.StringIO()
        status = project_acquisition.main(
            [*selection, "--apply", plan["plan_id"]],
            profiles={"fixture": self.profile_path},
            default_state_root=self.root / "state", output=output, error=error,
            environment=self.environment,
        )
        self.assertEqual(2, status)
        self.assertIn("review it again", error.getvalue())
        self.assertFalse(target.exists())

    def test_existing_destination_and_ambiguous_alias_fail_closed(self) -> None:
        destination = self.root / "checkout"
        destination.mkdir()
        with self.assertRaisesRegex(
            project_acquisition.ProjectAcquisitionError,
            "must not already exist",
        ):
            self._plan(destination)

        invalid = json.loads(self.profile_path.read_text(encoding="utf-8"))
        invalid["channels"].append(
            {
                "id": "other",
                "aliases": ["latest"],
                "remote_ref": "refs/heads/other",
                "checkout_branch": "other",
            }
        )
        self.profile_path.write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaisesRegex(
            project_acquisition.ProjectAcquisitionError,
            "alias is repeated",
        ):
            project_acquisition.load_acquisition_profile(self.profile_path)


if __name__ == "__main__":
    unittest.main()
