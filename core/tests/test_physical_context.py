"""Source and installed Core share one read-only physical context."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from workbench_core import cli, setup_cli
from workbench_core.physical_context import resolve_physical_context


class PhysicalContextTests(unittest.TestCase):
    def test_transport_schema_accepts_native_absolute_paths_only(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "src/workbench_core/schemas/workbench-physical-context-v1.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for absolute in ("/home/user/project", "C:\\Users\\User\\Project", "\\\\server\\share\\Project"):
            record = {
                "format": "workbench-physical-context-v1",
                "schema_version": 1,
                "configuration_home": absolute,
                "workspace": absolute,
                "state_root": absolute,
                "profile_configuration_reference": absolute,
            }
            validator.validate(record)
        record["workspace"] = "relative/project"
        self.assertFalse(validator.is_valid(record))

    def test_source_and_installed_roots_share_external_default_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            source = base / "source"
            installed = base / "installed"
            source.mkdir()
            installed.mkdir()
            environment = {
                "XDG_CONFIG_HOME": str(base / "configuration"),
                "XDG_STATE_HOME": str(base / "state"),
            }
            contexts = [
                resolve_physical_context(root, environment=environment, current_directory=project)
                for root in (source, installed)
            ]
            for context in contexts:
                self.assertEqual(base / "configuration/workbench", context.configuration_home)
                self.assertEqual(project, context.workspace)
                self.assertEqual(base / "state/workbench/runtime", context.state_root)
                self.assertIsNone(context.profile_configuration_reference)
                self.assertEqual(project, context.execution_context().workspace)
                self.assertEqual(context.state_root, context.execution_context().state_root)
            self.assertEqual(contexts[0], contexts[1])
            self.assertFalse((source / ".workbench").exists())
            self.assertFalse((base / "configuration").exists())
            self.assertFalse((base / "state").exists())

    def test_saved_workspace_and_explicit_state_precedence_do_not_activate_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite, saved_workspace, bootstrap, explicit_workspace = (
                base / name for name in ("suite", "saved", "bootstrap", "explicit")
            )
            for path in (suite, saved_workspace, bootstrap, explicit_workspace):
                path.mkdir()
            record_path = base / "configuration/setup-v1.json"
            profile_reference = base / "missing-profile.toml"
            setup_cli._write_setup_record(
                record_path,
                {
                    "workspace": str(saved_workspace),
                    "state_root": str(base / "saved-state"),
                    "profile_config": str(profile_reference),
                    "profile_selection_digest": None,
                    "java_home": None,
                    "managed_java_home": None,
                    "git_executable": None,
                },
            )
            environment = {
                "WORKBENCH_CONFIG_HOME": str(record_path.parent),
                "WORKBENCH_WORKSPACE": str(bootstrap),
                "WORKBENCH_STATE_ROOT": str(base / "explicit-state"),
            }
            context = resolve_physical_context(suite, environment=environment)
            self.assertEqual(saved_workspace, context.workspace)
            self.assertEqual(base / "explicit-state", context.state_root)
            self.assertEqual(profile_reference, context.profile_configuration_reference)
            self.assertFalse(profile_reference.exists())
            self.assertEqual(
                explicit_workspace,
                resolve_physical_context(
                    suite, workspace=explicit_workspace, environment=environment
                ).workspace,
            )

    def test_separate_user_roots_keep_workspace_choices_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite = base / "suite"
            suite.mkdir()
            contexts = []
            for label in ("alice", "bob"):
                home = base / label
                workspace = home / "project"
                workspace.mkdir(parents=True)
                configuration = home / "configuration/workbench"
                setup_cli._write_setup_record(
                    configuration / "setup-v1.json",
                    {
                        "workspace": str(workspace),
                        "state_root": str(home / "state"),
                        "profile_config": str(home / "profile.toml"),
                        "profile_selection_digest": None,
                        "java_home": None,
                        "managed_java_home": None,
                        "git_executable": None,
                    },
                )
                contexts.append(
                    resolve_physical_context(
                        suite,
                        environment={"XDG_CONFIG_HOME": str(home / "configuration")},
                    )
                )
            self.assertNotEqual(contexts[0].workspace, contexts[1].workspace)
            self.assertNotEqual(contexts[0].state_root, contexts[1].state_root)
            self.assertNotEqual(
                contexts[0].profile_configuration_reference,
                contexts[1].profile_configuration_reference,
            )

    def test_core_routes_use_context_and_expose_exact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite, project = base / "suite", base / "project"
            suite.mkdir()
            project.mkdir()
            environment = {
                "WORKBENCH_CONFIG_HOME": str(base / "configuration"),
                "WORKBENCH_WORKSPACE": str(project),
                "WORKBENCH_STATE_ROOT": str(base / "state"),
            }
            with patch.dict(os.environ, environment, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._dispatch_available(["environment", "paths", "--json"], suite, ()))
                record = json.loads(output.getvalue())
                schema_path = (
                    Path(__file__).resolve().parents[1]
                    / "src/workbench_core/schemas/workbench-physical-context-v1.schema.json"
                )
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(record)
                self.assertEqual("workbench-physical-context-v1", record["format"])
                self.assertEqual(str(project), record["workspace"])
                self.assertEqual(str(base / "state"), record["state_root"])
                with patch("workbench_core.storage.cli.main", return_value=0) as storage:
                    self.assertEqual(0, cli._dispatch_available(["storage", "list"], suite, ()))
                self.assertEqual(base / "state", storage.call_args.kwargs["workspace_root"])
                with patch("workbench_core.cli.dispatch", return_value=0) as dispatch:
                    self.assertEqual(0, cli._dispatch_available(["sample"], suite, ()))
                context = dispatch.call_args.args[1]
                self.assertEqual(project, context.workspace)
                self.assertEqual(base / "state", context.state_root)
            self.assertFalse((suite / ".workbench").exists())

    def test_source_core_storage_uses_external_default_without_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite = base / "suite"
            suite.mkdir()
            environment = {
                "WORKBENCH_CONFIG_HOME": str(base / "configuration"),
                "XDG_STATE_HOME": str(base / "state"),
            }
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("workbench_core.storage.cli.main", return_value=0) as storage,
            ):
                self.assertEqual(0, cli._dispatch_available(["storage", "list"], suite, ()))
            self.assertEqual(
                base / "state/workbench/runtime", storage.call_args.kwargs["workspace_root"]
            )
            self.assertFalse((suite / ".workbench").exists())

    def test_state_override_rejects_symlink_without_creating_a_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite, destination = base / "suite", base / "destination"
            suite.mkdir()
            destination.mkdir()
            alias = base / "state-alias"
            alias.symlink_to(destination, target_is_directory=True)
            environment = {
                "WORKBENCH_CONFIG_HOME": str(base / "configuration"),
                "WORKBENCH_STATE_ROOT": str(alias),
            }
            with self.assertRaisesRegex(ValueError, "symlink"):
                resolve_physical_context(suite, environment=environment, current_directory=suite)
            self.assertEqual([], list(destination.iterdir()))

    def test_module_help_remains_available_with_invalid_saved_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite = base / "suite"
            suite.mkdir()
            configuration = base / "configuration"
            configuration.mkdir()
            (configuration / "setup-v1.json").write_text("invalid\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(configuration)}, clear=True),
                patch("workbench_core.cli.dispatch", return_value=0) as dispatch,
            ):
                self.assertEqual(0, cli._dispatch_available(["sample", "--help"], suite, ()))
            self.assertEqual(Path.cwd().resolve(), dispatch.call_args.args[1].workspace)


if __name__ == "__main__":
    unittest.main()
