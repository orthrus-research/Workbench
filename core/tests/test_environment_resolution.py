"""The resolved environment owns Core command paths and module context."""

from __future__ import annotations

from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from workbench_core import cli, setup_cli
from workbench_core.environment_resolution import resolve_environment
from workbench_core.user_preferences import choose_default_workspace, register_workspace


def _selection(home: Path, *, profile: Path | None = None) -> dict[str, str | None]:
    return {
        "workspace": str(home / "saved"),
        "state_root": str(home / "saved-state"),
        "profile_config": str(profile) if profile is not None else None,
        "profile_selection_digest": None,
        "java_home": None,
        "managed_java_home": None,
        "git_executable": None,
    }


class EnvironmentResolutionTests(unittest.TestCase):
    def test_source_and_installed_roots_share_external_defaults_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            project.mkdir()
            source = base / "source"
            installed = base / "installed"
            source.mkdir()
            installed.mkdir()
            environment = {
                "HOME": str(base / "home"),
                "XDG_STATE_HOME": str(base / "state"),
            }
            contexts = [
                resolve_environment(root, environment=environment, current_directory=project)
                for root in (source, installed)
            ]
            for context in contexts:
                self.assertEqual(base / "home/.workbench", context.configuration_home)
                self.assertEqual(project, context.workspace)
                self.assertEqual(base / "state/workbench/runtime", context.state_root)
                self.assertIsNone(context.record["profile_configuration_reference"])
            self.assertEqual(contexts[0].record, contexts[1].record)
            self.assertFalse((source / ".workbench").exists())
            self.assertFalse((base / "home").exists())
            self.assertFalse((base / "state").exists())

    def test_saved_workspace_and_explicit_state_do_not_activate_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite = base / "suite"
            suite.mkdir()
            profile = base / "missing-profile.toml"
            config = base / "configuration"
            setup_cli._write_setup_record(config / "setup-v1.json", _selection(base, profile=profile))
            environment = {
                "WORKBENCH_CONFIG_HOME": str(config),
                "WORKBENCH_WORKSPACE": str(base / "bootstrap"),
                "WORKBENCH_STATE_ROOT": str(base / "explicit-state"),
            }
            context = resolve_environment(suite, environment=environment)
            self.assertEqual(base / "saved", context.workspace)
            self.assertEqual("setup-v1", context.record["workspace"]["source"])
            self.assertEqual(base / "explicit-state", context.state_root)
            self.assertEqual("environment", context.record["state_root"]["source"])
            self.assertEqual(str(profile), context.record["profile_configuration_reference"])
            self.assertFalse(profile.exists())
            explicit = resolve_environment(suite, workspace=base / "explicit", environment=environment)
            self.assertEqual(base / "explicit", explicit.workspace)
            self.assertEqual("argument", explicit.record["workspace"]["source"])

    def test_user_configuration_roots_keep_saved_choices_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite = base / "suite"
            suite.mkdir()
            contexts = []
            for label in ("alice", "bob"):
                home = base / label
                profile = home / "profile.toml"
                setup_cli._write_setup_record(
                    home / ".workbench/setup-v1.json",
                    _selection(home, profile=profile),
                )
                contexts.append(resolve_environment(suite, environment={"HOME": str(home)}))
            self.assertNotEqual(contexts[0].workspace, contexts[1].workspace)
            self.assertNotEqual(contexts[0].state_root, contexts[1].state_root)
            self.assertNotEqual(
                contexts[0].record["profile_configuration_reference"],
                contexts[1].record["profile_configuration_reference"],
            )

    def test_core_routes_share_resolved_paths_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            suite = base / "suite"
            suite.mkdir()
            environment = {
                "WORKBENCH_CONFIG_HOME": str(base / "configuration"),
                "WORKBENCH_WORKSPACE": str(base / "project"),
                "WORKBENCH_STATE_ROOT": str(base / "state"),
            }
            with patch.dict(os.environ, environment, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._dispatch_available(["environment", "resolve", "--json"], suite, ()))
                record = json.loads(output.getvalue())
                schema_path = (
                    Path(__file__).resolve().parents[1]
                    / "src/workbench_core/schemas/workbench-environment-resolution-v1.schema.json"
                )
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(record)
                self.assertEqual(str(base / "project"), record["workspace"]["path"])
                self.assertEqual(str(base / "state"), record["state_root"]["path"])
                with patch("workbench_core.storage.cli.main", return_value=0) as storage:
                    self.assertEqual(0, cli._dispatch_available(["storage", "list"], suite, ()))
                self.assertEqual(base / "state", storage.call_args.kwargs["workspace_root"])
                with patch("workbench_core.cli.dispatch", return_value=0) as dispatch:
                    self.assertEqual(0, cli._dispatch_available(["sample"], suite, ()))
                context = dispatch.call_args.args[1]
                self.assertEqual(base / "project", context.workspace)
                self.assertEqual(base / "state", context.state_root)
                self.assertIn("logs", context.locations)
            self.assertFalse((suite / ".workbench").exists())

    def test_state_override_rejects_symlink_without_creating_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            destination = base / "destination"
            destination.mkdir()
            alias = base / "state-alias"
            alias.symlink_to(destination, target_is_directory=True)
            environment = {
                "WORKBENCH_CONFIG_HOME": str(base / "configuration"),
                "WORKBENCH_STATE_ROOT": str(alias),
            }
            with self.assertRaisesRegex(ValueError, "symlink"):
                resolve_environment(base, environment=environment)
            self.assertEqual([], list(destination.iterdir()))

    def test_module_help_remains_available_with_invalid_saved_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = base / "configuration"
            config.mkdir()
            (config / "setup-v1.json").write_text("invalid\n", encoding="utf-8")
            (config / "settings.json").write_text("invalid\n", encoding="utf-8")
            (config / "workspaces.json").write_text("invalid\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(config)}, clear=True),
                patch("workbench_core.cli.dispatch", return_value=0) as dispatch,
            ):
                self.assertEqual(0, cli._dispatch_available(["sample", "--help"], base, ()))
            self.assertEqual(Path.cwd().resolve(), dispatch.call_args.args[1].workspace)

    def test_invalid_workspace_registry_has_settings_specific_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / ".workbench"
            config.mkdir()
            (config / "workspaces.json").write_text("invalid\n", encoding="utf-8")
            errors = io.StringIO()
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True), redirect_stderr(errors):
                self.assertEqual(2, cli._main(["sample"]))
            self.assertIn("workspace settings are invalid", errors.getvalue())
            self.assertIn("workspaces.json", errors.getvalue())
            self.assertNotIn("setup --repair", errors.getvalue())

    def test_saved_workspace_environment_is_scoped_to_one_cli_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            suite = home / "suite"
            suite.mkdir()
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / ".workbench")}
            register_workspace("first", str(home / "first"), make_default=True, environment=environment)
            observed: list[tuple[str | None, str | None]] = []

            def dispatch(arguments, root, *, caller_environment):
                observed.append((
                    os.environ.get("WORKBENCH_WORKSPACE"),
                    caller_environment.get("WORKBENCH_WORKSPACE"),
                ))
                return 0

            with (
                patch.dict(os.environ, environment, clear=True),
                patch("workbench_core.cli.source_root", return_value=suite),
                patch("workbench_core.host_services.install_local_host_services"),
                patch("workbench_core.module_cli.disabled_profiles", return_value=()),
                patch("workbench_api.profiles.profile_scope", return_value=nullcontext()),
                patch("workbench_core.package_guard.PackageActivity", return_value=nullcontext()),
                patch("workbench_core.cli._dispatch", side_effect=dispatch),
            ):
                self.assertEqual(0, cli._main(["sample"]))
                self.assertNotIn("WORKBENCH_WORKSPACE", os.environ)
                choose_default_workspace(None)
                self.assertEqual(0, cli._main(["sample"]))
                self.assertNotIn("WORKBENCH_WORKSPACE", os.environ)
                os.environ["WORKBENCH_WORKSPACE"] = str(home / "caller")
                register_workspace("second", str(home / "second"), make_default=True)
                self.assertEqual(0, cli._main(["sample"]))
                self.assertEqual(str(home / "caller"), os.environ["WORKBENCH_WORKSPACE"])
            self.assertEqual(
                [
                    (str(home / "first"), None),
                    (None, None),
                    (str(home / "second"), str(home / "caller")),
                ],
                observed,
            )


if __name__ == "__main__":
    unittest.main()
