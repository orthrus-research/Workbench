"""Focused tests for the user-facing Workbench setup boundary."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parent
PROJECT_INTELLIGENCE_SOURCE = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_core import setup_cli  # noqa: E402


def _environment_status() -> dict[str, object]:
    return {
        "state": "ready",
        "execution": {
            "mode": "packaged-pixi",
            "source_checkout": False,
        },
        "pixi": {
            "state": "unavailable",
            "version": None,
            "required_constraint": None,
            "matches_required_constraint": None,
            "executable": None,
        },
    }


def _git_ready() -> dict[str, object]:
    return setup_cli._dependency(
        "git",
        "Git",
        "ready",
        "git version 2.fixture",
        source="/tools/git",
    )


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class _InterruptOnEOF(_TTY):
    def readline(self, *args: object, **kwargs: object) -> str:
        value = super().readline(*args, **kwargs)
        if value == "":
            raise KeyboardInterrupt
        return value


class SetupCliTests(unittest.TestCase):
    def test_dispatch_uses_installed_profile_resources_and_preserves_source_root(self) -> None:
        from workbench_core.cli import _dispatch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, resources, source = root / "workbench_core", root / "workbench_resources", root / "source"
            for path in (package, resources, source / "core"):
                path.mkdir(parents=True)
            (source / "core/pyproject.toml").write_text("# source fixture")
            for given, expected in ((package, resources), (source, source)):
                with self.subTest(given=given), patch("workbench_api.resources.repository_root", return_value=resources), \
                        patch.object(setup_cli, "main", return_value=0) as setup:
                    self.assertEqual(0, _dispatch(["setup", "--plan"], given))
                setup.assert_called_once_with(["--plan"], root=expected)

    @staticmethod
    def _make_supersymmetry_workspace(path: Path) -> None:
        path.mkdir()
        (path / "pack.toml").write_text("name = 'Supersymmetry'\n", encoding="utf-8")
        (path / "index.toml").write_text("hash-format = 'sha256'\n", encoding="utf-8")
        for directory in ("config", "groovy", "mods"):
            (path / directory).mkdir()
        profile = json.loads(
            (
                REPOSITORY_ROOT
                / "profiles/packs/supersymmetry/runtime/manual-artifacts-v1.json"
            ).read_text(encoding="utf-8")
        )
        for row in profile["artifacts"]:
            metadata = path.joinpath(*Path(row["metadata_path"]).parts)
            metadata.write_text(
                """name = "{name}"
filename = "{filename}"
side = "both"

[download]
hash-format = "{hash_format}"
hash = "{hash_value}"
mode = "metadata:curseforge"

[update.curseforge]
project-id = {project_id}
file-id = {file_id}
""".format(
                    name=row["display_name"],
                    filename=row["filename"],
                    hash_format=row["hash_format"],
                    hash_value=row["hash"],
                    project_id=row["project_id"],
                    file_id=row["file_id"],
                ),
                encoding="utf-8",
            )

    def _main(
        self,
        arguments: list[str],
        *,
        record: Path,
        input_text: str = "",
        tty: bool = False,
        environment: dict[str, str] | None = None,
        input_stream: io.StringIO | None = None,
    ) -> tuple[int, str, str]:
        selected_input: io.StringIO = (
            input_stream
            if input_stream is not None
            else _TTY(input_text)
            if tty
            else io.StringIO(input_text)
        )
        output: io.StringIO = _TTY() if tty else io.StringIO()
        error = io.StringIO()
        with (
            patch.object(setup_cli, "inspect_environment_status", return_value=_environment_status()),
            patch.object(setup_cli, "_probe_git", return_value=_git_ready()),
        ):
            status = setup_cli.main(
                arguments,
                root=REPOSITORY_ROOT,
                input_stream=selected_input,
                output=output,
                error=error,
                environment=(
                    {"PATH": os.environ.get("PATH", "")}
                    if environment is None
                    else environment
                ),
                record_path=record,
            )
        return status, output.getvalue(), error.getvalue()

    def test_non_tty_plain_setup_is_deterministic_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "config/setup-v1.json"
            status, output, error = self._main([], record=record)

            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("interactive setup requires a TTY", error)
            self.assertFalse(record.exists())
            self.assertFalse(record.parent.exists())

    def test_default_full_setup_requires_an_explicit_project_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "config/setup-v1.json"
            status, output, error = self._main(
                [],
                record=record,
                input_text="\n\n\n",
                tty=True,
            )

            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertIn("Workbench setup · System", output)
            self.assertIn("Git:", output)
            self.assertIn("Project setup file (workbench.toml) [required]", output)
            self.assertIn("full developer setup requires a project setup file", output)
            self.assertIn("nothing was changed", output)
            self.assertFalse(record.exists())
            self.assertFalse(record.parent.exists())

    def test_review_only_is_explicit_and_y_applies_without_a_typed_plan_token(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "config/setup-v1.json"
            status, output, error = self._main(
                [],
                record=record,
                input_text="2\ny\n",
                tty=True,
            )

            self.assertEqual(0, status, error)
            self.assertIn("Apply this setup? [Y/n]", output)
            self.assertNotIn("Type apply", output)
            self.assertNotIn("workbench-setup-plan:sha256:", output)
            self.assertNotIn("workbench-user-setup:sha256:", output)
            self.assertIn("REVIEW-ONLY", output)
            self.assertIn("REQUIRED FOR DEVELOPMENT", output)
            self.assertIn(
                "Project setup: ! \x1b[33m[REQUIRED FOR DEVELOPMENT]",
                output,
            )
            self.assertIn("Not used for this setup:", output)
            self.assertNotIn("Setup record:", output)
            self.assertTrue(record.is_file())

    def test_n_customizes_and_later_workspace_remains_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            bootstrap_workspace = parent / "bootstrap"
            selected_workspace = parent / "selected project"
            bootstrap_workspace.mkdir()
            selected_workspace.mkdir()
            record = parent / "config/setup-v1.json"
            answers = "\n".join(
                (
                    "2",
                    "n",
                    str(selected_workspace),
                    "review-only",
                    "",
                    "",
                    "y",
                )
            ) + "\n"

            status, output, error = self._main(
                [],
                record=record,
                input_text=answers,
                tty=True,
                environment={
                    "PATH": os.environ.get("PATH", ""),
                    "WORKBENCH_WORKSPACE": str(bootstrap_workspace),
                },
            )

            saved = setup_cli.load_setup_record(record)
            revised = output.split("Revised setup", 1)[1]
            self.assertEqual(0, status, error)
            self.assertEqual(2, output.count("Apply this setup? [Y/n]"))
            self.assertIn(f"Workspace: {selected_workspace}", revised)
            expected_next = (
                subprocess.list2cmdline(
                    ["workbench", "open", str(selected_workspace)]
                )
                if os.name == "nt"
                else shlex.join(["workbench", "open", str(selected_workspace)])
            )
            self.assertIn(f"Next: {expected_next}", revised)
            self.assertEqual(str(selected_workspace), saved["selection"]["workspace"])

            process_environment = {
                "PATH": os.environ.get("PATH", ""),
                "WORKBENCH_WORKSPACE": str(bootstrap_workspace),
            }
            setup_cli.apply_setup_environment_defaults(
                saved,
                environment=process_environment,
            )
            self.assertEqual(
                str(selected_workspace),
                process_environment["WORKBENCH_WORKSPACE"],
            )

    def test_tty_color_and_no_color_keep_the_same_status_words(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            color_status, color_output, color_error = self._main(
                [],
                record=parent / "color/setup-v1.json",
                input_text="2\n",
                tty=True,
            )
            plain_status, plain_output, plain_error = self._main(
                [],
                record=parent / "plain/setup-v1.json",
                input_text="2\n",
                tty=True,
                environment={
                    "PATH": os.environ.get("PATH", ""),
                    "NO_COLOR": "",
                },
            )

            self.assertEqual(0, color_status, color_error)
            self.assertEqual(0, plain_status, plain_error)
            self.assertIn("\x1b[32m", color_output)
            self.assertIn("✓ \x1b[32m[READY]", color_output)
            self.assertIn("[READY]", plain_output)
            self.assertIn("[REQUIRED FOR DEVELOPMENT]", plain_output)
            self.assertNotIn("\x1b[", plain_output)

    def test_ctrl_c_at_consent_returns_130_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "config/setup-v1.json"
            status, output, error = self._main(
                [],
                record=record,
                tty=True,
                input_stream=_InterruptOnEOF("2\n"),
            )

            self.assertEqual(130, status)
            self.assertEqual("", error)
            self.assertIn("Apply this setup? [Y/n]", output)
            self.assertIn("Setup cancelled", output)
            self.assertFalse(record.exists())
            self.assertFalse(record.parent.exists())

    def test_missing_git_system_summary_has_one_status_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "config/setup-v1.json"
            host_check = {
                "state": "attention",
                "host": {
                    "family": "other",
                    "system": "FixtureOS",
                    "machine": "fixture",
                },
                "requirements": {
                    "git": {
                        "state": "missing",
                        "executable": None,
                        "repair": {
                            "state": "manual",
                            "detail": "Install Git for FixtureOS.",
                        },
                    }
                },
            }

            with patch.object(
                setup_cli,
                "inspect_host_requirements",
                return_value=host_check,
            ):
                status, output, error = self._main(
                    [],
                    record=record,
                    input_text="2\n",
                    tty=True,
                )

            system_section = output.split("Workbench setup · Choose", 1)[0]
            self.assertEqual(1, status)
            self.assertEqual("", error)
            self.assertEqual(1, system_section.count("Git:"))
            self.assertIn("Install Git for FixtureOS.", system_section)
            self.assertFalse(record.exists())

    def test_quick_plan_never_selects_supersymmetry_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "generic-project"
            workspace.mkdir()
            record = parent / "config/setup-v1.json"

            status, output, error = self._main(
                ["--plan", "--json", "--workspace", str(workspace)],
                record=record,
            )
            plan = json.loads(output)

            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual(setup_cli.PLAN_FORMAT_V3, plan["format"])
            self.assertEqual(3, plan["schema_version"])
            self.assertIsNone(plan["expected_record_id"])
            self.assertIsNone(plan["selection"]["profile_config"])
            self.assertIsNone(plan["selection"]["profile_selection_digest"])
            profile = next(row for row in plan["dependencies"] if row["id"] == "runtime-profile")
            java = next(row for row in plan["dependencies"] if row["id"] == "java")
            self.assertEqual("not-needed", profile["state"])
            self.assertEqual("not-needed", java["state"])
            self.assertFalse(java["required"])
            self.assertNotIn("create-workspace", [row["id"] for row in plan["actions"]])
            self.assertFalse(record.exists())

    def test_missing_git_is_optional_for_directory_review_and_required_on_request(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            with (
                patch.object(
                    setup_cli,
                    "inspect_environment_status",
                    return_value=_environment_status(),
                ),
                patch.object(
                    setup_cli,
                    "inspect_host_requirements",
                    return_value={
                        "requirements": {
                            "git": {"state": "missing", "executable": None},
                        }
                    },
                ),
            ):
                directory_check = setup_cli.inspect_setup(
                    REPOSITORY_ROOT,
                    selection,
                    record_path=parent / "config/setup-v1.json",
                    record_present=False,
                    environment={"PATH": ""},
                )
                git_check = setup_cli.inspect_setup(
                    REPOSITORY_ROOT,
                    selection,
                    record_path=parent / "config/setup-v1.json",
                    record_present=False,
                    environment={"PATH": ""},
                    require_git=True,
                )

            optional = next(
                row for row in directory_check["dependencies"] if row["id"] == "git"
            )
            required = next(
                row for row in git_check["dependencies"] if row["id"] == "git"
            )
            self.assertFalse(optional["required"])
            self.assertEqual("ready", directory_check["state"])
            self.assertTrue(required["required"])
            self.assertIn("git", git_check["blockers"])
            self.assertEqual("attention", git_check["state"])
            self.assertIn("Exact-directory review remains available", required["detail"])

    def test_existing_setup_repair_keeps_git_flow_conditional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            record = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            setup_cli._write_setup_record(record, selection)
            host_check = {
                "state": "attention",
                "host": {
                    "family": "other",
                    "system": "OtherOS",
                    "machine": "fixture",
                },
                "requirements": {
                    "git": {
                        "state": "missing",
                        "executable": None,
                        "repair": {"state": "manual", "detail": "Install Git manually."},
                    }
                },
            }
            output = io.StringIO()
            error = io.StringIO()

            def missing_git(
                _selected: str | None,
                _environment: dict[str, str],
                *,
                required: bool = False,
            ) -> dict[str, object]:
                return setup_cli._dependency(
                    "git",
                    "Git",
                    "missing-manual",
                    "Git is missing.",
                    required=required,
                    repair="Run workbench repair.",
                )

            with (
                patch.object(
                    setup_cli,
                    "inspect_environment_status",
                    return_value=_environment_status(),
                ),
                patch.object(
                    setup_cli,
                    "inspect_host_requirements",
                    return_value=host_check,
                ),
                patch.object(setup_cli, "_probe_git", side_effect=missing_git),
            ):
                status = setup_cli.main(
                    ["--repair", "--plan", "--json"],
                    root=REPOSITORY_ROOT,
                    output=output,
                    error=error,
                    environment={"PATH": ""},
                    record_path=record,
                )

            plan = json.loads(output.getvalue())
            git = next(row for row in plan["dependencies"] if row["id"] == "git")
            self.assertEqual(0, status)
            self.assertEqual("", error.getvalue())
            self.assertFalse(git["required"])
            self.assertNotIn("git", plan["blockers"])

    def test_reviewed_plan_can_be_applied_and_checked_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            state = parent / "state"
            record = parent / "config/setup-v1.json"
            common = [
                "--workspace",
                str(workspace),
                "--state-root",
                str(state),
            ]

            planned, output, error = self._main(
                ["--plan", "--json", *common],
                record=record,
            )
            plan = json.loads(output)
            self.assertEqual(0, planned)
            self.assertEqual("", error)
            self.assertFalse(record.exists())

            applied, output, error = self._main(
                ["--apply", plan["plan_id"], "--json", *common],
                record=record,
            )
            result = json.loads(output)
            self.assertEqual(0, applied)
            self.assertEqual("", error)
            self.assertEqual("configured", result["outcome"])
            self.assertEqual("ready", result["verification"]["state"])
            self.assertTrue(record.is_file())
            loaded = setup_cli.load_setup_record(record)
            self.assertEqual(result["record"]["record_id"], loaded["record_id"])

            checked, output, error = self._main(
                ["--check", "--json"],
                record=record,
            )
            check = json.loads(output)
            self.assertEqual(0, checked)
            self.assertEqual("", error)
            self.assertTrue(check["configured"])
            self.assertEqual("ready", check["state"])

    def test_nonexistent_workspace_is_v2_read_only_until_exact_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace_parent = parent / "new-parent"
            workspace = workspace_parent / "project"
            state = parent / "state"
            record = parent / "config/setup-v1.json"
            common = [
                "--workspace",
                str(workspace),
                "--state-root",
                str(state),
            ]

            planned, output, error = self._main(
                ["--plan", "--json", *common],
                record=record,
            )
            plan = json.loads(output)

            self.assertEqual(0, planned, error)
            self.assertEqual(setup_cli.PLAN_FORMAT_V3, plan["format"])
            self.assertEqual(3, plan["schema_version"])
            self.assertIsNone(plan["expected_record_id"])
            self.assertEqual(
                ["create-workspace", "save-user-setup"],
                [row["id"] for row in plan["actions"]],
            )
            workspace_row = next(
                row for row in plan["dependencies"] if row["id"] == "workspace"
            )
            self.assertEqual("missing-installable", workspace_row["state"])
            self.assertFalse(workspace_parent.exists())
            self.assertFalse(record.exists())

            applied, output, error = self._main(
                ["--apply", plan["plan_id"], "--json", *common],
                record=record,
            )
            result = json.loads(output)

            self.assertEqual(0, applied, error)
            self.assertEqual(setup_cli.RESULT_FORMAT_V2, result["format"])
            self.assertEqual(
                [{"component": "workspace", "path": str(workspace)}],
                result["created"],
            )
            self.assertTrue(workspace.is_dir())
            self.assertEqual(
                str(workspace),
                setup_cli.load_setup_record(record)["selection"]["workspace"],
            )

    def test_apply_rejects_a_setup_record_created_after_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "reviewed-project"
            workspace.mkdir()
            concurrent_workspace = parent / "concurrent-project"
            concurrent_workspace.mkdir()
            state = parent / "state"
            record = parent / "config/setup-v1.json"
            common = [
                "--workspace",
                str(workspace),
                "--state-root",
                str(state),
            ]

            planned, output, error = self._main(
                ["--plan", "--json", *common],
                record=record,
            )
            plan = json.loads(output)
            concurrent = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=concurrent_workspace,
                state_root=parent / "concurrent-state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            concurrent_record = setup_cli._write_setup_record(record, concurrent)
            retained = record.read_bytes()

            applied, output, apply_error = self._main(
                ["--apply", plan["plan_id"], "--json", *common],
                record=record,
            )

            self.assertEqual(0, planned, error)
            self.assertEqual(2, applied)
            self.assertEqual("", output)
            self.assertIn("does not match the current plan", apply_error)
            self.assertEqual(retained, record.read_bytes())
            self.assertEqual(
                concurrent_record["record_id"],
                setup_cli.load_setup_record(record)["record_id"],
            )

    def test_apply_compare_and_swap_preserves_a_concurrent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            original_workspace = parent / "original-project"
            reviewed_workspace = parent / "reviewed-project"
            concurrent_workspace = parent / "concurrent-project"
            for workspace in (
                original_workspace,
                reviewed_workspace,
                concurrent_workspace,
            ):
                workspace.mkdir()
            record = parent / "config/setup-v1.json"
            original = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=original_workspace,
                state_root=parent / "original-state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            original_record = setup_cli._write_setup_record(record, original)
            planned, output, error = self._main(
                ["--plan", "--json", "--workspace", str(reviewed_workspace)],
                record=record,
            )
            plan = json.loads(output)
            concurrent = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=concurrent_workspace,
                state_root=parent / "concurrent-state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            concurrent_record: dict[str, object] | None = None

            def replace_during_apply(*_args: object, **_kwargs: object) -> dict[str, object]:
                nonlocal concurrent_record
                concurrent_record = setup_cli._write_setup_record(record, concurrent)
                return {
                    "state": "ready",
                    "blockers": [],
                    "managed_installs_available": [],
                    "dependencies": [],
                }

            self.assertEqual(0, planned, error)
            self.assertEqual(
                original_record["record_id"],
                plan["expected_record_id"],
            )
            with (
                patch.object(setup_cli, "inspect_setup", side_effect=replace_during_apply),
                self.assertRaisesRegex(
                    setup_cli.SetupError,
                    "changed after the setup plan was reviewed",
                ),
            ):
                setup_cli._apply_plan(
                    REPOSITORY_ROOT,
                    plan,
                    record_path=record,
                    environment={"PATH": os.environ.get("PATH", "")},
                )

            self.assertIsNotNone(concurrent_record)
            retained = record.read_bytes()
            self.assertEqual(
                concurrent_record["record_id"],
                setup_cli.load_setup_record(record)["record_id"],
            )
            self.assertEqual(retained, record.read_bytes())

    def test_failed_apply_removes_only_empty_workspace_directories_it_created(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace_parent = parent / "created-parent"
            workspace = workspace_parent / "project"
            record = parent / "config/setup-v1.json"
            status, output, error = self._main(
                [
                    "--plan",
                    "--json",
                    "--workspace",
                    str(workspace),
                    "--state-root",
                    str(parent / "state"),
                ],
                record=record,
            )
            plan = json.loads(output)
            failed_verification = {
                "state": "attention",
                "blockers": ["forced-verification-failure"],
                "managed_installs_available": [],
                "dependencies": [],
            }

            self.assertEqual(0, status, error)
            with (
                patch.object(
                    setup_cli,
                    "inspect_setup",
                    return_value=failed_verification,
                ),
                self.assertRaisesRegex(
                    setup_cli.SetupError,
                    "not ready after apply",
                ),
            ):
                setup_cli._apply_plan(
                    REPOSITORY_ROOT,
                    plan,
                    record_path=record,
                    environment={"PATH": os.environ.get("PATH", "")},
                )

            self.assertFalse(workspace.exists())
            self.assertFalse(workspace_parent.exists())
            self.assertFalse(record.exists())

    def test_failed_apply_preserves_created_workspace_once_it_has_user_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace_parent = parent / "created-parent"
            workspace = workspace_parent / "project"
            record = parent / "config/setup-v1.json"
            status, output, error = self._main(
                ["--plan", "--json", "--workspace", str(workspace)],
                record=record,
            )
            plan = json.loads(output)

            def fail_after_user_data(
                *_args: object,
                **_kwargs: object,
            ) -> dict[str, object]:
                (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
                return {
                    "state": "attention",
                    "blockers": ["forced-verification-failure"],
                    "managed_installs_available": [],
                    "dependencies": [],
                }

            self.assertEqual(0, status, error)
            with (
                patch.object(
                    setup_cli,
                    "inspect_setup",
                    side_effect=fail_after_user_data,
                ),
                self.assertRaises(setup_cli.SetupError),
            ):
                setup_cli._apply_plan(
                    REPOSITORY_ROOT,
                    plan,
                    record_path=record,
                )

            self.assertEqual(
                "keep\n",
                (workspace / "keep.txt").read_text(encoding="utf-8"),
            )
            self.assertTrue(workspace_parent.is_dir())
            self.assertFalse(record.exists())

    def test_workspace_creation_fails_closed_on_a_concurrent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace_parent = parent / "raced-parent"
            workspace = workspace_parent / "project"
            record = parent / "config/setup-v1.json"
            status, output, error = self._main(
                ["--plan", "--json", "--workspace", str(workspace)],
                record=record,
            )
            plan = json.loads(output)
            real_mkdir = Path.mkdir
            raced = False

            def race_parent(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> None:
                nonlocal raced
                if path == workspace_parent and not raced:
                    raced = True
                    real_mkdir(path, *args, **kwargs)
                    raise FileExistsError(str(path))
                real_mkdir(path, *args, **kwargs)

            self.assertEqual(0, status, error)
            with (
                patch.object(Path, "mkdir", new=race_parent),
                self.assertRaisesRegex(setup_cli.SetupError, "changed after review"),
            ):
                setup_cli._apply_plan(
                    REPOSITORY_ROOT,
                    plan,
                    record_path=record,
                )

            self.assertTrue(raced)
            self.assertTrue(workspace_parent.is_dir())
            self.assertFalse(workspace.exists())
            self.assertFalse(record.exists())

    def test_wrong_plan_id_does_not_create_record_or_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            state = parent / "state"
            record = parent / "config/setup-v1.json"

            status, output, error = self._main(
                [
                    "--apply",
                    "workbench-setup-plan:sha256:" + "0" * 64,
                    "--workspace",
                    str(workspace),
                    "--state-root",
                    str(state),
                ],
                record=record,
            )

            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("does not match the current plan", error)
            self.assertFalse(record.exists())
            self.assertFalse(record.parent.exists())
            self.assertFalse(state.exists())

    def test_setup_record_cannot_be_placed_inside_selected_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "project"
            workspace.mkdir()
            record = workspace / ".workbench/setup-v1.json"

            status, output, error = self._main(
                ["--plan", "--workspace", str(workspace)],
                record=record,
            )

            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("outside the selected workspace", error)
            self.assertFalse(record.parent.exists())

    def test_workspace_symlinks_are_rejected_before_plan_or_creation(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation needs additional Windows privileges")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            target = parent / "target"
            target.mkdir()
            direct_link = parent / "workspace-link"
            direct_link.symlink_to(target, target_is_directory=True)
            parent_link = parent / "parent-link"
            parent_link.symlink_to(target, target_is_directory=True)

            for workspace in (direct_link, parent_link / "not-created"):
                with self.subTest(workspace=workspace):
                    record = parent / (workspace.name + "-setup.json")
                    status, output, error = self._main(
                        ["--plan", "--workspace", str(workspace)],
                        record=record,
                    )

                    self.assertEqual(2, status)
                    self.assertEqual("", output)
                    self.assertIn("symlink", error)
                    self.assertFalse(record.exists())
            self.assertFalse((target / "not-created").exists())

    def test_state_root_symlink_ancestor_is_rejected_before_plan_or_creation(
        self,
    ) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation needs additional Windows privileges")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            outside = parent / "outside"
            outside.mkdir()
            state_alias = parent / "state-alias"
            state_alias.symlink_to(outside, target_is_directory=True)
            record = parent / "config/setup-v1.json"

            status, output, error = self._main(
                [
                    "--plan",
                    "--workspace",
                    str(workspace),
                    "--state-root",
                    str(state_alias / "not-created"),
                ],
                record=record,
            )

            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("symlink", error)
            self.assertFalse((outside / "not-created").exists())
            self.assertFalse(record.exists())

    def test_state_root_windows_junction_shape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            state_parent = parent / "junction-shaped-parent"
            state_parent.mkdir()
            record = parent / "config/setup-v1.json"
            real_redirect_check = setup_cli._is_redirecting_path_component

            def junction_shape(path: Path, info: os.stat_result) -> bool:
                return path == state_parent or real_redirect_check(path, info)

            with patch.object(
                setup_cli,
                "_is_redirecting_path_component",
                side_effect=junction_shape,
            ):
                status, output, error = self._main(
                    [
                        "--plan",
                        "--workspace",
                        str(workspace),
                        "--state-root",
                        str(state_parent / "state"),
                    ],
                    record=record,
                )

            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("junction", error)
            self.assertFalse((state_parent / "state").exists())
            self.assertFalse(record.exists())

    def test_custom_runtime_profile_offers_java_only_after_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            state = parent / "state"
            record = parent / "config/setup-v1.json"

            status, output, error = self._main(
                [
                    "--plan",
                    "--json",
                    "--workspace",
                    str(workspace),
                    "--state-root",
                    str(state),
                    "--profile-config",
                    "workbench.toml",
                ],
                record=record,
            )
            plan = json.loads(output)

            self.assertEqual(0, status)
            self.assertEqual("", error)
            profile = next(row for row in plan["dependencies"] if row["id"] == "runtime-profile")
            java = next(row for row in plan["dependencies"] if row["id"] == "java")
            self.assertEqual("ready", profile["state"])
            self.assertIn("supersymmetry", profile["detail"])
            self.assertEqual("missing-installable", java["state"])
            self.assertTrue(java["required"])
            self.assertEqual(["install-managed-java", "save-user-setup"], [row["id"] for row in plan["actions"]])
            self.assertFalse(record.exists())
            self.assertFalse(state.exists())

    def test_no_windows_short_name_blocks_setup_before_java_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            state = parent / "state-验证"
            state.mkdir()
            marker = state / "keep.txt"
            marker.write_text("unchanged\n", encoding="utf-8")
            record = parent / "config/setup-v1.json"
            failure = setup_cli.JavaRuntimeError(
                "Windows 8.3 ASCII DOS short-name alias is unavailable; "
                "pass an ASCII --state-root"
            )

            with patch.object(
                setup_cli,
                "plan_managed_java_execution",
                side_effect=failure,
            ):
                status, output, error = self._main(
                    [
                        "--plan",
                        "--json",
                        "--workspace",
                        str(workspace),
                        "--state-root",
                        str(state),
                        "--profile-config",
                        "workbench.toml",
                    ],
                    record=record,
                )
            plan = json.loads(output)

            self.assertEqual(0, status, error)
            self.assertEqual("blocked", plan["state"])
            self.assertIn("java", plan["blockers"])
            java = next(row for row in plan["dependencies"] if row["id"] == "java")
            self.assertEqual("incompatible", java["state"])
            self.assertIn("ASCII --state-root", java["detail"])
            self.assertNotIn(
                "install-managed-java",
                [row["id"] for row in plan["actions"]],
            )
            self.assertEqual("unchanged\n", marker.read_text(encoding="utf-8"))
            self.assertFalse(record.exists())

    def test_unicode_windows_java_plan_discloses_and_binds_cds_transform(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            record = parent / "config/setup-v1.json"
            execution = {
                "kind": "windows-short-path",
                "portability_transform_id": (
                    setup_cli.WINDOWS_UNICODE_CDS_TRANSFORM_ID
                ),
            }

            with patch.object(
                setup_cli,
                "plan_managed_java_execution",
                return_value=execution,
            ):
                status, output, error = self._main(
                    [
                        "--plan",
                        "--json",
                        "--workspace",
                        str(workspace),
                        "--state-root",
                        str(parent / "state-验证"),
                        "--profile-config",
                        "workbench.toml",
                    ],
                    record=record,
                )
            plan = json.loads(output)
            action = next(
                row for row in plan["actions"] if row["id"] == "install-managed-java"
            )

            self.assertEqual(0, status, error)
            self.assertEqual(execution, action["execution"])
            self.assertIn("optional startup-cache archives", action["effect"])
            self.assertIn("record every removal", action["effect"])
            self.assertFalse(record.exists())

    def test_setup_apply_rejects_java_portability_transform_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            java_home = root / "jdk"
            java = java_home / "bin/java.exe"
            java.parent.mkdir(parents=True)
            java.write_bytes(b"java")
            plan = {
                "plan_id": "workbench-setup-plan:sha256:" + "1" * 64,
                "actions": [
                    {
                        "id": "install-managed-java",
                        "execution": {
                            "kind": "windows-short-path",
                            "portability_transform_id": (
                                setup_cli.WINDOWS_UNICODE_CDS_TRANSFORM_ID
                            ),
                        },
                    }
                ],
                "dependencies": [],
            }
            selection = {
                "profile_config": str(REPOSITORY_ROOT / "workbench.toml"),
                "state_root": str(root / "state"),
            }
            managed = {
                "source": "managed",
                "outcome": "provisioned",
                "receipt": {
                    "runtime_id": "sha256:" + "2" * 64,
                    "execution": {
                        "kind": "windows-short-path",
                        "java_home_uri": java_home.as_uri(),
                        "java_uri": java.as_uri(),
                    },
                    "portability": {
                        "cds": {
                            "state": "vendor-default",
                            "transform_id": None,
                            "removed_archives": [],
                        }
                    },
                    "target": {"java_home_uri": java_home.as_uri()},
                },
            }

            with patch.object(
                setup_cli,
                "ensure_java_runtime",
                return_value=managed,
            ):
                with self.assertRaisesRegex(
                    setup_cli.SetupError,
                    "differs from the reviewed setup plan",
                ):
                    setup_cli._finish_setup_apply(
                        REPOSITORY_ROOT,
                        plan,
                        selection,
                        record_path=root / "setup.json",
                    )

    def test_setup_apply_revalidates_state_custody_before_java_mutation(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation needs additional Windows privileges")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewed_parent = root / "reviewed-parent"
            reviewed_parent.mkdir()
            outside = root / "outside"
            outside.mkdir()
            reviewed_state = setup_cli._state_root(reviewed_parent / "state")
            plan = {
                "plan_id": "workbench-setup-plan:sha256:" + "1" * 64,
                "actions": [{"id": "install-managed-java", "execution": {}}],
                "dependencies": [],
            }
            selection = {
                "profile_config": str(REPOSITORY_ROOT / "workbench.toml"),
                "state_root": str(reviewed_state),
            }
            reviewed_parent.rmdir()
            reviewed_parent.symlink_to(outside, target_is_directory=True)

            with (
                patch.object(setup_cli, "ensure_java_runtime") as ensure,
                self.assertRaisesRegex(
                    setup_cli.SetupError,
                    "symlinks, junctions, or other reparse points",
                ),
            ):
                setup_cli._finish_setup_apply(
                    REPOSITORY_ROOT,
                    plan,
                    selection,
                    record_path=root / "setup.json",
                )

            ensure.assert_not_called()
            self.assertFalse((outside / "state").exists())

    def test_fresh_unicode_windows_state_is_blocked_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            state = parent / "state-验证"
            record = parent / "setup-record/setup-v1.json"
            windows = {
                "os": "windows",
                "architecture": "x64",
                "system": "Windows",
                "machine": "AMD64",
            }

            with patch.object(
                setup_cli,
                "host_platform",
                return_value=windows,
            ):
                status, output, error = self._main(
                    [
                        "--plan",
                        "--json",
                        "--workspace",
                        str(workspace),
                        "--state-root",
                        str(state),
                        "--profile-config",
                        "workbench.toml",
                    ],
                    record=record,
                )
            plan = json.loads(output)

            self.assertEqual(0, status, error)
            self.assertEqual("blocked", plan["state"])
            self.assertIn("java", plan["blockers"])
            java = next(
                row for row in plan["dependencies"] if row["id"] == "java"
            )
            self.assertEqual("incompatible", java["state"])
            self.assertIn("nonexistent Unicode", java["detail"])
            self.assertIn("ASCII --state-root", java["detail"])
            self.assertNotIn(
                "install-managed-java",
                [row["id"] for row in plan["actions"]],
            )
            self.assertFalse(state.exists())
            self.assertFalse(record.exists())
            self.assertFalse(record.parent.exists())

    def test_runtime_profile_rejects_an_empty_workspace_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "empty"
            workspace.mkdir()
            record = parent / "config/setup-v1.json"

            status, output, error = self._main(
                [
                    "--plan",
                    "--json",
                    "--workspace",
                    str(workspace),
                    "--profile-config",
                    "workbench.toml",
                ],
                record=record,
            )
            plan = json.loads(output)

            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual("blocked", plan["state"])
            self.assertIn("profile-workspace", plan["blockers"])
            row = next(
                dependency
                for dependency in plan["dependencies"]
                if dependency["id"] == "profile-workspace"
            )
            self.assertEqual("incompatible", row["state"])
            self.assertIn("workbench project acquire supersymmetry", row["repair"])
            self.assertFalse(record.exists())

    def test_matching_saved_setup_plan_is_read_only_and_apply_reuses_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            record = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            setup_cli._write_setup_record(record, selection)
            before = record.read_bytes()

            status, output, error = self._main(
                ["--plan", "--json"],
                record=record,
            )
            plan = json.loads(output)

            self.assertEqual(0, status, error)
            self.assertEqual(["verify-user-setup"], [row["id"] for row in plan["actions"]])
            applied, result_output, apply_error = self._main(
                ["--apply", plan["plan_id"], "--json"],
                record=record,
            )
            result = json.loads(result_output)
            self.assertEqual(0, applied, apply_error)
            self.assertEqual("reused", result["outcome"])
            self.assertEqual(before, record.read_bytes())

    def test_existing_managed_java_is_planned_as_reuse_not_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            java_home = parent / "managed-java"
            java_home.mkdir()
            record = parent / "config/setup-v1.json"
            managed = {
                "receipt": {
                    "target": {
                        "java_home_uri": java_home.as_uri(),
                    }
                }
            }

            with patch.object(
                setup_cli,
                "inspect_managed_java_runtime",
                return_value=managed,
            ):
                status, output, error = self._main(
                    [
                        "--plan",
                        "--json",
                        "--workspace",
                        str(workspace),
                        "--state-root",
                        str(parent / "state"),
                        "--profile-config",
                        "workbench.toml",
                    ],
                    record=record,
                )
            plan = json.loads(output)

            self.assertEqual(0, status, error)
            self.assertNotIn(
                "install-managed-java",
                [row["id"] for row in plan["actions"]],
            )
            self.assertEqual(
                str(java_home),
                plan["selection"]["managed_java_home"],
            )
            java = next(row for row in plan["dependencies"] if row["id"] == "java")
            self.assertEqual("ready", java["state"])
            self.assertIn("reused", java["detail"])

    def test_state_root_replacement_drops_stale_managed_java_and_uses_v3_cas(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            old_state = parent / "old-state"
            new_state = parent / "new-state"
            record_path = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=old_state,
                profile_config=REPOSITORY_ROOT / "workbench.toml",
                java_home=None,
                git_executable=None,
            )
            selection["managed_java_home"] = str(old_state / "jdks/stale-home")
            prior = setup_cli._write_setup_record(record_path, selection)
            retained = record_path.read_bytes()

            status, output, error = self._main(
                ["--plan", "--json", "--state-root", str(new_state)],
                record=record_path,
            )
            plan = json.loads(output)

            self.assertEqual(0, status, error)
            self.assertEqual(setup_cli.PLAN_FORMAT_V3, plan["format"])
            self.assertEqual(prior["record_id"], plan["expected_record_id"])
            self.assertEqual(str(new_state), plan["selection"]["state_root"])
            self.assertIsNone(plan["selection"]["managed_java_home"])
            self.assertEqual(
                ["install-managed-java", "save-user-setup"],
                [row["id"] for row in plan["actions"]],
            )
            self.assertEqual(retained, record_path.read_bytes())
            self.assertFalse(new_state.exists())

    def test_state_root_replacement_rebinds_compatible_managed_java_in_new_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            old_state = parent / "old-state"
            new_state = parent / "new-state"
            new_home = new_state / "jdks/verified-home"
            new_home.mkdir(parents=True)
            record_path = parent / "config/setup-v1.json"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=old_state,
                profile_config=REPOSITORY_ROOT / "workbench.toml",
                java_home=None,
                git_executable=None,
            )
            selection["managed_java_home"] = str(old_state / "jdks/stale-home")
            prior = setup_cli._write_setup_record(record_path, selection)
            retained = record_path.read_bytes()
            managed = {
                "receipt": {
                    "target": {
                        "java_home_uri": new_home.as_uri(),
                    }
                }
            }

            with patch.object(
                setup_cli,
                "inspect_managed_java_runtime",
                return_value=managed,
            ) as inspect_managed:
                status, output, error = self._main(
                    ["--plan", "--json", "--state-root", str(new_state)],
                    record=record_path,
                )
            plan = json.loads(output)

            self.assertEqual(0, status, error)
            self.assertEqual(setup_cli.PLAN_FORMAT_V3, plan["format"])
            self.assertEqual(prior["record_id"], plan["expected_record_id"])
            self.assertEqual(str(new_state), plan["selection"]["state_root"])
            self.assertEqual(str(new_home), plan["selection"]["managed_java_home"])
            self.assertEqual(
                ["save-user-setup"],
                [row["id"] for row in plan["actions"]],
            )
            java = next(
                row for row in plan["dependencies"] if row["id"] == "java"
            )
            self.assertEqual("ready", java["state"])
            self.assertIn("reused", java["detail"])
            inspect_managed.assert_called_once()
            self.assertEqual(
                str(new_state),
                inspect_managed.call_args.kwargs["state_root"],
            )
            self.assertEqual(retained, record_path.read_bytes())

    def test_declining_explicit_java_plan_never_starts_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            self._make_supersymmetry_workspace(workspace)
            state = parent / "state"
            record = parent / "config/setup-v1.json"
            answers = "\n".join(
                (
                    "1",
                    str(workspace),
                    "workbench.toml",
                    str(state),
                    "",
                    "",
                )
            ) + "\n"

            with patch.object(setup_cli, "ensure_java_runtime") as ensure:
                status, output, error = self._main(
                    [],
                    record=record,
                    input_text=answers,
                    tty=True,
                )

            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertIn("nothing was changed", output)
            ensure.assert_not_called()
            self.assertFalse(record.exists())
            self.assertFalse(record.parent.exists())
            self.assertFalse(state.exists())

    def test_setup_record_symlink_is_rejected_without_touching_target(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation needs additional Windows privileges")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            target = parent / "target.json"
            target.write_text("unchanged\n", encoding="utf-8")
            record = parent / "setup-v1.json"
            record.symlink_to(target)

            status, output, error = self._main(["--check"], record=record)

            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("non-symlink", error)
            self.assertEqual("unchanged\n", target.read_text(encoding="utf-8"))

    def test_top_level_router_exposes_setup_specific_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(REPOSITORY_ROOT / "tools/workbench.py"), "setup", "--help"],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        normalized_help = " ".join(completed.stdout.split())
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("usage: workbench setup", completed.stdout)
        self.assertIn("Full developer", normalized_help)
        self.assertIn("Review-only", normalized_help)
        self.assertIn("Repair", normalized_help)

    def test_explicit_state_root_environment_is_used_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            selected_state = parent / "custom-state"
            record = parent / "config/setup-v1.json"

            status, output, error = self._main(
                ["--plan", "--json", "--workspace", str(workspace)],
                record=record,
                environment={
                    "PATH": os.environ.get("PATH", ""),
                    "WORKBENCH_STATE_ROOT": str(selected_state),
                },
            )
            plan = json.loads(output)

            self.assertEqual(0, status)
            self.assertEqual("", error)
            self.assertEqual(str(selected_state), plan["selection"]["state_root"])
            self.assertNotEqual(
                str(selected_state.parent / "runtime"),
                plan["selection"]["state_root"],
            )

    def test_saved_physical_defaults_preserve_caller_overrides_and_profile_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            workspace = parent / "project"
            workspace.mkdir()
            selected_git = parent / "tools/git"
            selected_java = parent / "java"
            selected_git.parent.mkdir()
            selected_git.touch()
            selected_java.mkdir()
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=workspace,
                state_root=parent / "saved-state",
                profile_config=REPOSITORY_ROOT / "workbench.toml",
                java_home=selected_java,
                git_executable=selected_git,
            )
            record = setup_cli._write_setup_record(
                parent / "config/setup-v1.json",
                selection,
            )
            caller_state = str(parent / "caller-state")
            environment = {
                "PATH": str(parent / "ambient-tools"),
                "WORKBENCH_STATE_ROOT": caller_state,
            }

            applied = setup_cli.apply_setup_environment_defaults(
                record,
                environment=environment,
            )

            self.assertEqual(str(workspace), environment["WORKBENCH_WORKSPACE"])
            self.assertEqual(caller_state, environment["WORKBENCH_STATE_ROOT"])
            self.assertEqual(str(selected_git), environment["WORKBENCH_GIT_EXECUTABLE"])
            self.assertEqual(str(selected_java), environment["WORKBENCH_JAVA_HOME"])
            self.assertEqual(
                [str(selected_git.parent), str(parent / "ambient-tools")],
                environment["PATH"].split(os.pathsep),
            )
            self.assertEqual(caller_state, applied["WORKBENCH_STATE_ROOT"])
            self.assertNotIn("WORKBENCH_PROFILE_CONFIG", environment)
            self.assertNotIn("WORKBENCH_PACK_PROFILE", environment)

    def test_router_uses_saved_workspace_only_when_target_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            saved_workspace = parent / "saved-workspace"
            explicit_workspace = parent / "explicit-workspace"
            for workspace in (saved_workspace, explicit_workspace):
                workspace.mkdir()
                (workspace / "settings.gradle").touch()
            config_home = parent / "config"
            selection = setup_cli._new_selection(
                REPOSITORY_ROOT,
                workspace=saved_workspace,
                state_root=parent / "state",
                profile_config=None,
                java_home=None,
                git_executable=None,
            )
            setup_cli._write_setup_record(config_home / "setup-v1.json", selection)
            environment = dict(os.environ)
            environment["WORKBENCH_CONFIG_HOME"] = str(config_home)
            for key in (
                "WORKBENCH_WORKSPACE",
                "WORKBENCH_STATE_ROOT",
                "WORKBENCH_GIT_EXECUTABLE",
                "WORKBENCH_JAVA_HOME",
            ):
                environment.pop(key, None)
            environment["WORKBENCH_WORKSPACE"] = str(parent / "bootstrap-workspace")

            defaulted = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "tools/workbench.py"),
                    "open",
                    "--json",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            explicit = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "tools/workbench.py"),
                    "open",
                    str(explicit_workspace),
                    "--json",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )

            self.assertEqual(0, defaulted.returncode, defaulted.stderr)
            self.assertEqual(0, explicit.returncode, explicit.stderr)
            self.assertEqual(
                str(saved_workspace),
                json.loads(defaulted.stdout)["workspace"]["root"],
            )
            self.assertEqual(
                str(explicit_workspace),
                json.loads(explicit.stdout)["workspace"]["root"],
            )


if __name__ == "__main__":
    unittest.main()
