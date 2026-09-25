"""Stable user configuration and read-only environment resolution."""

from __future__ import annotations

from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from workbench_core import cli, setup_cli
from workbench_core.fixture_selection import register_recipe_fixture
from workbench_core.environment_resolution import resolve_environment
from workbench_core.user_config_home import default_user_config_home, default_user_record_path
from workbench_core.user_config_migration import (
    inspect_legacy_config_migration,
    migrate_legacy_config,
)
from workbench_core.user_preferences import (
    UserPreferencesError,
    default_settings_path,
    load_settings,
    load_workspaces,
    register_workspace,
    remove_workspace,
    set_location,
)
from workbench_core.fixture_selection import default_fixture_registry_path


def _selection(home: Path) -> dict[str, str | None]:
    return {
        "workspace": str(home / "project"),
        "state_root": str(home / "state"),
        "profile_config": None,
        "profile_selection_digest": None,
        "java_home": None,
        "managed_java_home": None,
        "git_executable": None,
    }


class UserPreferencesTests(unittest.TestCase):
    def test_versioned_schemas_accept_resolved_and_saved_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            suite = home / "suite"
            suite.mkdir()
            environment = {"HOME": str(home)}
            settings = set_location("logs", "~/logs", environment=environment)
            workspaces = register_workspace("project", "~/project", make_default=True, environment=environment)
            records = {
                "workbench-user-settings-v1": settings,
                "workbench-user-workspaces-v1": workspaces,
                "workbench-environment-resolution-v1": resolve_environment(suite, environment=environment).record,
            }
            schema_home = Path(__file__).resolve().parents[1] / "src/workbench_core/schemas"
            for name, record in records.items():
                schema = json.loads((schema_home / f"{name}.schema.json").read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(record)

    def test_new_home_is_stable_and_legacy_setup_import_is_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "old-config")}
            source = home / "old-config/workbench/setup-v1.json"
            original = setup_cli._write_setup_record(source, _selection(home))
            target = home / ".workbench/setup-v1.json"
            self.assertEqual(source, setup_cli.default_setup_record_path(environment=environment))
            plan = inspect_legacy_config_migration(environment=environment)
            self.assertEqual("ready", plan["state"])
            self.assertFalse(target.exists())
            result = migrate_legacy_config(environment=environment)
            self.assertEqual("imported", result["state"])
            self.assertEqual("copied", result["files"][0]["state"])
            self.assertEqual(target, setup_cli.default_setup_record_path(environment=environment))
            self.assertEqual(original, setup_cli.load_setup_record(source))
            self.assertEqual(original, setup_cli.load_setup_record(target))
            self.assertEqual(home / ".workbench", default_user_config_home(environment=environment))

    def test_legacy_import_refuses_conflicting_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            source = home / ".config/workbench/setup-v1.json"
            destination = home / ".workbench/setup-v1.json"
            setup_cli._write_setup_record(source, _selection(home))
            alternate = _selection(home)
            alternate["workspace"] = str(home / "another-project")
            setup_cli._write_setup_record(destination, alternate)
            self.assertEqual("conflict", inspect_legacy_config_migration(environment=environment)["state"])
            before = destination.read_bytes()
            with self.assertRaisesRegex(UserPreferencesError, "conflicting"):
                migrate_legacy_config(environment=environment)
            self.assertEqual(before, destination.read_bytes())

    def test_legacy_import_carries_fixture_and_launcher_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            legacy = home / ".config/workbench"
            setup_cli._write_setup_record(legacy / "setup-v1.json", _selection(home))
            register_recipe_fixture(
                "supersymmetry",
                home / "project",
                home / "runtime",
                home / "java",
                path=legacy / "recipe-fixtures-v1.json",
            )
            launcher_body = {
                "format": "workbench-launcher-setup-record-v1",
                "schema_version": 1,
                "selection": {
                    "family": "prism",
                    "executable": str(home / "PrismLauncher"),
                    "root": str(home / "PrismRoot"),
                },
            }
            launcher = {
                **launcher_body,
                "record_id": "workbench-launcher-setup:sha256:" + sha256(
                    json.dumps(launcher_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            (legacy / "launcher-v1.json").write_text(json.dumps(launcher), encoding="utf-8")
            result = migrate_legacy_config(environment=environment)
            self.assertEqual("imported", result["state"])
            self.assertEqual(3, len(result["files"]))
            for name in ("setup-v1.json", "recipe-fixtures-v1.json", "launcher-v1.json"):
                self.assertEqual((legacy / name).read_bytes(), (home / ".workbench" / name).read_bytes())

    def test_each_legacy_record_resolves_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            legacy = home / ".config/workbench"
            stable = home / ".workbench"
            legacy.mkdir(parents=True)
            stable.mkdir()
            (legacy / "recipe-fixtures-v1.json").write_text("legacy", encoding="utf-8")
            (legacy / "launcher-v1.json").write_text("legacy", encoding="utf-8")
            (stable / "setup-v1.json").write_text("stable", encoding="utf-8")
            self.assertEqual(stable / "setup-v1.json", default_user_record_path("setup-v1.json", environment=environment))
            self.assertEqual(legacy / "recipe-fixtures-v1.json", default_user_record_path("recipe-fixtures-v1.json", environment=environment))
            self.assertEqual(legacy / "launcher-v1.json", default_user_record_path("launcher-v1.json", environment=environment))
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(legacy / "recipe-fixtures-v1.json", default_fixture_registry_path())

    def test_resolver_uses_saved_roles_and_named_workspace_without_writing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            suite = home / "suite"
            suite.mkdir()
            environment = {"HOME": str(home), "XDG_STATE_HOME": str(home / "os-state")}
            set_location("artifacts", "~/artifacts", environment=environment)
            set_location("blueprint_library", "./library/blueprints", environment=environment)
            register_workspace("modpack", "~/projects/modpack", make_default=True, environment=environment)
            resolved = resolve_environment(suite, environment=environment)
            self.assertEqual(home / "projects/modpack", resolved.workspace)
            self.assertEqual("user-workspaces", resolved.record["workspace"]["source"])
            self.assertEqual(home / "artifacts", resolved.location("artifacts"))
            self.assertEqual("user-settings", resolved.record["locations"]["artifacts"]["source"])
            self.assertEqual(home / ".workbench/library/blueprints", resolved.location("blueprint_library"))
            self.assertEqual(home / "os-state/workbench/logs", resolved.location("logs"))
            with_state_override = resolve_environment(
                suite,
                environment={**environment, "WORKBENCH_STATE_ROOT": str(home / "operation-state")},
            )
            self.assertEqual(resolved.location("logs"), with_state_override.location("logs"))
            self.assertFalse((home / "projects").exists())
            self.assertFalse((home / "artifacts").exists())
            self.assertFalse((home / ".workbench/library").exists())
            overridden = resolve_environment(
                suite,
                workspace=home / "explicit-project",
                location_overrides={"artifacts": str(home / "explicit-artifacts")},
                environment=environment,
            )
            self.assertEqual(home / "explicit-project", overridden.workspace)
            self.assertEqual(home / "explicit-artifacts", overridden.location("artifacts"))
            self.assertEqual("argument", overridden.record["locations"]["artifacts"]["source"])
            remove_workspace("modpack", environment=environment)
            self.assertIsNone(load_workspaces(environment=environment)["default"])

    def test_plain_resolve_command_and_module_context_use_same_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            suite = home / "suite"
            suite.mkdir()
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            set_location("logs", "~/custom-logs", environment=environment)
            with patch.dict(os.environ, environment, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._dispatch_available(["environment", "resolve"], suite, ()))
                self.assertIn("Logs: " + str(home / "custom-logs"), output.getvalue())
                self.assertFalse(output.getvalue().lstrip().startswith("{"))
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._dispatch_available(["environment", "resolve", "."], suite, ()))
                self.assertIn("Workspace: " + str(Path.cwd().resolve()) + " (argument)", output.getvalue())
                with patch("workbench_core.cli.dispatch", return_value=0) as dispatch:
                    self.assertEqual(0, cli._dispatch_available(["sample"], suite, ()))
                context = dispatch.call_args.args[1]
                self.assertEqual(home / "custom-logs", context.location("logs"))
                self.assertEqual(home / "custom-logs", resolve_environment(suite).location("logs"))

    def test_configuration_commands_work_before_module_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"HOME": temporary, "XDG_STATE_HOME": str(Path(temporary) / "state")}
            with (
                patch.dict(os.environ, environment, clear=True),
                patch("workbench_core.module_cli.disabled_profiles", side_effect=AssertionError("admission reached")),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(0, cli._main(["settings"]))
                self.assertEqual(0, cli._main(["environment", "resolve", "."]))
            self.assertFalse((Path(temporary) / ".workbench").exists())

    def test_resolution_uses_one_snapshot_of_saved_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            suite = home / "suite"
            suite.mkdir()
            environment = {"HOME": str(home)}
            with (
                patch("workbench_core.physical_context.load_setup_record", side_effect=AssertionError("setup reloaded")),
                patch("workbench_core.physical_context.load_workspaces", side_effect=AssertionError("workspaces reloaded")),
            ):
                self.assertEqual(
                    home / ".workbench",
                    resolve_environment(suite, environment=environment).configuration_home,
                )

    def test_newer_or_changed_settings_fail_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"HOME": temporary}
            saved = set_location("logs", "~/logs", environment=environment)
            path = default_settings_path(environment=environment)
            altered = dict(saved)
            altered["schema_version"] = 2
            path.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(UserPreferencesError, "cannot read"):
                load_settings(environment=environment)
            with self.assertRaisesRegex(UserPreferencesError, "cannot read"):
                set_location("artifacts", "~/artifacts", environment=environment)
            self.assertEqual(altered, json.loads(path.read_text(encoding="utf-8")))

    def test_symlinked_location_is_rejected_before_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            real = home / "real"
            real.mkdir()
            (home / "alias").symlink_to(real, target_is_directory=True)
            environment = {"HOME": str(home)}
            with self.assertRaisesRegex(UserPreferencesError, "symlink"):
                set_location("logs", "~/alias/logs", environment=environment)
            self.assertFalse(default_settings_path(environment=environment).exists())


if __name__ == "__main__":
    unittest.main()
