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
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence import project_acquisition  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "Git is required for acquisition tests")
class ProjectAcquisitionTests(unittest.TestCase):
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
            project_acquisition,
            "_write_json_atomic",
            side_effect=project_acquisition.ProjectAcquisitionError(
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
            project_acquisition,
            "_write_json_atomic",
            side_effect=project_acquisition.ProjectAcquisitionError(
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
        real_replace = os.replace

        def fail_checkout_publish(source: object, target: object) -> None:
            if Path(target) == destination:
                raise OSError("simulated checkout publication failure")
            real_replace(source, target)

        with mock.patch.object(
            project_acquisition.os,
            "replace",
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
