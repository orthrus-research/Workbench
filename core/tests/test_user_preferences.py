"""Stable user configuration and read-only environment resolution."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from workbench_core import cli, setup_cli
from workbench_core import settings_cli
from workbench_core.dispatch_setup import user_setup_environment
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
    default_workspaces_path,
    load_settings,
    load_workspaces,
    register_workspace,
    remove_workspace,
    set_location,
    set_workspace_selection,
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
    def test_settings_json_selection_round_trip_for_textual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            initial = register_workspace("alpha", str(home / "alpha"), environment=environment)
            with patch.dict(os.environ, environment, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._main(["settings", "workspace", "list", "--json"]))
                self.assertEqual(initial, json.loads(output.getvalue()))
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._main([
                        "settings", "workspace", "select", "alpha",
                        "--profile-config", str(home / "workbench.toml"),
                        "--java-home", str(home / "jdk-25"),
                        "--expected-record-id", initial["record_id"], "--json",
                    ]))
                selected = json.loads(output.getvalue())
                self.assertEqual("workbench-user-workspaces-v2", selected["format"])
                self.assertEqual(str(home / "jdk-25"), selected["entries"][0]["java_home"])
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._main([
                        "settings", "workspace", "select", "alpha", "--clear-java",
                        "--expected-record-id", selected["record_id"], "--json",
                    ]))
                cleared = json.loads(output.getvalue())
                self.assertIsNone(cleared["entries"][0]["java_home"])
                self.assertEqual(str(home / "workbench.toml"), cleared["entries"][0]["profile_config"])
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._main([
                        "settings", "workspace", "select", "alpha", "--java-feature", "8",
                        "--expected-record-id", cleared["record_id"], "--json",
                    ]))
                managed = json.loads(output.getvalue())
                self.assertEqual("workbench-user-workspaces-v3", managed["format"])
                self.assertEqual(8, managed["entries"][0]["managed_java_feature"])

    def test_named_workspaces_cannot_share_one_physical_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "source"
            workspace.mkdir()
            environment = {"HOME": str(home)}
            register_workspace("first", str(workspace), environment=environment)
            with self.assertRaisesRegex(UserPreferencesError, "another named workspace"):
                register_workspace("second", str(workspace), environment=environment)

    def test_two_workspaces_retain_independent_profile_and_java_choices_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            suite = home / "suite"
            suite.mkdir()
            config = home / "config"
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(config)}
            for name in ("alpha", "beta"):
                (home / name).mkdir()
                (home / f"{name}.toml").write_text("profile = 'fixture'\n", encoding="utf-8")
                (home / f"jdk-{name}").mkdir()
            initial = register_workspace("alpha", str(home / "alpha"), make_default=True, environment=environment)
            register_workspace("beta", str(home / "beta"), environment=environment)
            self.assertEqual(1, initial["schema_version"])
            selected = set_workspace_selection(
                "alpha", profile_config=str(home / "alpha.toml"), java_home=str(home / "jdk-alpha"),
                environment=environment,
            )
            self.assertEqual(2, selected["schema_version"])
            first_id = next(row["workspace_id"] for row in selected["entries"] if row["name"] == "alpha")
            with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
                set_workspace_selection("beta", java_home=str(home / "jdk-beta"),
                                        environment=environment, expected_record_id=initial["record_id"])
            selected = set_workspace_selection(
                "beta", profile_config=str(home / "beta.toml"), java_home=str(home / "jdk-beta"),
                environment=environment, expected_record_id=selected["record_id"],
            )
            self.assertEqual(first_id, next(row["workspace_id"] for row in selected["entries"] if row["name"] == "alpha"))
            schema = json.loads((Path(__file__).resolve().parents[1] / "src/workbench_core/schemas/workbench-user-workspaces-v2.schema.json").read_text())
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(selected)
            for name in ("alpha", "beta"):
                resolved = resolve_environment(suite, workspace=home / name, environment=environment)
                self.assertEqual(str(home / f"{name}.toml"), resolved.record["profile_configuration_reference"])
                self.assertEqual(str(home / f"jdk-{name}"), resolved.record["tool_candidates"]["java_home"])
                self.assertEqual("user-workspaces-v2", resolved.record["choice_sources"]["java_home"])
            process_environment = {**os.environ, **environment, "WORKBENCH_WORKSPACE": str(home / "beta")}
            process_environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "api/src") + os.pathsep + str(Path(__file__).resolve().parents[1] / "src")
            reopened = subprocess.run(
                [sys.executable, "-c", "import json; from pathlib import Path; from workbench_core.environment_resolution import resolve_environment; print(json.dumps(resolve_environment(Path.cwd()).record))"],
                cwd=suite, env=process_environment, capture_output=True, text=True, check=True,
            )
            self.assertEqual(str(home / "jdk-beta"), json.loads(reopened.stdout)["tool_candidates"]["java_home"])

    def test_setup_java_is_not_activated_for_another_named_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            config = home / "config"
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(config)}
            setup = _selection(home)
            setup["java_home"] = str(home / "jdk-legacy")
            setup_cli._write_setup_record(config / "setup-v1.json", setup)
            register_workspace("other", str(home / "other"), make_default=True, environment=environment)
            selected = set_workspace_selection("other", java_home=str(home / "jdk-selected"), environment=environment)
            with patch.dict(os.environ, environment, clear=True):
                with user_setup_environment(["sample"]) as activated:
                    self.assertTrue(activated)
                    self.assertEqual(str(home / "other"), os.environ["WORKBENCH_WORKSPACE"])
                    self.assertEqual(str(home / "jdk-selected"), os.environ["WORKBENCH_JAVA_HOME"])
                self.assertNotIn("WORKBENCH_JAVA_HOME", os.environ)
                with user_setup_environment(["sample"]) as activated:
                    self.assertTrue(activated)
                    self.assertEqual(str(home / "jdk-selected"), os.environ["WORKBENCH_JAVA_HOME"])
                set_workspace_selection("other", java_home=None, environment=environment,
                                        expected_record_id=selected["record_id"])
                with user_setup_environment(["sample"]) as activated:
                    self.assertTrue(activated)
                    self.assertNotIn("WORKBENCH_JAVA_HOME", os.environ)

    def test_managed_java_8_choice_round_trips_without_a_java_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            register_workspace("first", str(home / "first"), environment=environment)
            register_workspace("second", str(home / "second"), environment=environment)
            selected = set_workspace_selection("first", managed_java_feature=8, environment=environment)
            self.assertEqual("workbench-user-workspaces-v3", selected["format"])
            first = next(row for row in selected["entries"] if row["name"] == "first")
            self.assertIsNone(first["java_home"])
            self.assertEqual(8, first["managed_java_feature"])
            self.assertEqual(selected, load_workspaces(environment=environment))
            schema = json.loads((Path(__file__).resolve().parents[1] / "src/workbench_core/schemas/workbench-user-workspaces-v3.schema.json").read_text())
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(selected)
            resolved = resolve_environment(home, workspace=home / "first", environment=environment)
            self.assertEqual(8, resolved.operation_selection().managed_java_feature)
            self.assertIsNone(resolved.operation_selection().java_home)
            self.assertEqual("workbench-environment-resolution-v2", resolved.record["format"])
            resolution_schema = json.loads((Path(__file__).resolve().parents[1] / "src/workbench_core/schemas/workbench-environment-resolution-v2.schema.json").read_text())
            Draft202012Validator.check_schema(resolution_schema)
            Draft202012Validator(resolution_schema).validate(resolved.record)
            other = resolve_environment(home, workspace=home / "second", environment=environment)
            self.assertIsNone(other.operation_selection().managed_java_feature)
            with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
                set_workspace_selection("first", java_home=str(home / "jdk"), environment=environment,
                                        expected_record_id="old")
            replaced = set_workspace_selection("first", java_home=str(home / "jdk"), environment=environment,
                                               expected_record_id=selected["record_id"])
            first = next(row for row in replaced["entries"] if row["name"] == "first")
            self.assertEqual(str(home / "jdk"), first["java_home"])
            self.assertIsNone(first["managed_java_feature"])
            with self.assertRaisesRegex(UserPreferencesError, "choose a managed Java feature or a Java path"):
                set_workspace_selection("first", java_home=str(home / "jdk"), managed_java_feature=8,
                                        environment=environment)

    def test_supplied_java_symlink_is_retained_without_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            actual = home / "real-jdk"
            actual.mkdir()
            alias = home / "selected-jdk"
            alias.symlink_to(actual, target_is_directory=True)
            environment = {"HOME": str(home)}
            register_workspace("chosen", str(home / "project"), environment=environment)
            set_workspace_selection("chosen", java_home=str(alias), environment=environment)
            operation = resolve_environment(
                home, workspace=home / "project", environment=environment,
            ).operation_selection()
            self.assertEqual(alias, operation.java_home)

    def test_acquire_named_workspace_uses_core_managed_choice_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            register_workspace("chosen", str(home / "chosen"), environment=environment)
            saved = set_workspace_selection("chosen", managed_java_feature=8, environment=environment)
            result = {"format": "workbench-java-runtime-result-v2", "source": "managed",
                      "outcome": "provisioned", "receipt": {"policy": {"feature_version": 8}}}
            with patch.dict(os.environ, environment, clear=True), patch(
                "workbench_core.managed_java.CoreManagedJava"
            ) as core_java:
                core_java.return_value.ensure.return_value = result
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, settings_cli.main([
                        "workspace", "acquire", "chosen", "--expected-record-id",
                        saved["record_id"], "--json",
                    ], suite_root=home))
            self.assertEqual(result, json.loads(output.getvalue()))
            self.assertEqual(8, core_java.call_args.kwargs["selection"].managed_java_feature)
            self.assertIsNone(core_java.call_args.kwargs["selection"].java_home)
            self.assertEqual(home, core_java.return_value.ensure.call_args.args[0])
            with patch.dict(os.environ, environment, clear=True), patch(
                "workbench_core.managed_java.CoreManagedJava.ensure"
            ) as acquire:
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        settings_cli.main([
                            "workspace", "acquire", "chosen", "--expected-record-id", "stale",
                        ], suite_root=home)
            acquire.assert_not_called()
            set_workspace_selection("chosen", java_home=str(home / "uninspected-jdk"),
                                    environment=environment)
            with patch.dict(os.environ, environment, clear=True), patch(
                "workbench_core.managed_java.CoreManagedJava.ensure"
            ) as acquire:
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        settings_cli.main(["workspace", "acquire", "chosen"], suite_root=home)
            acquire.assert_not_called()

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
                "workbench-user-config-migration-v1": inspect_legacy_config_migration(environment=environment),
            }
            schema_home = Path(__file__).resolve().parents[1] / "src/workbench_core/schemas"
            for name, record in records.items():
                schema = json.loads((schema_home / f"{name}.schema.json").read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(record)

    def test_resolution_schema_requires_absolute_resolved_paths(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "src/workbench_core/schemas/workbench-environment-resolution-v1.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        path_validator = Draft202012Validator(schema["$defs"]["path"])
        for value in ("/home/user/project", r"C:\Users\User\Project", r"\\server\share\Project"):
            with self.subTest(absolute=value):
                self.assertTrue(path_validator.is_valid(value))
        for value in ("relative/project", r"C:relative", r"\server"):
            with self.subTest(relative=value):
                self.assertFalse(path_validator.is_valid(value))

        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            record = resolve_environment(home, environment={"HOME": str(home)}).record
        validator = Draft202012Validator(schema)
        for keys in (
            ("configuration_home",),
            ("settings", "path"),
            ("workspaces", "path"),
            ("setup", "path"),
            ("workspace", "path"),
            ("state_root", "path"),
            ("locations", "logs", "path"),
            ("profile_configuration_reference",),
        ):
            altered = json.loads(json.dumps(record))
            field = altered
            for key in keys[:-1]:
                field = field[key]
            field[keys[-1]] = "relative/project"
            with self.subTest(field=keys):
                self.assertFalse(validator.is_valid(altered))

    def test_new_home_is_stable_and_legacy_setup_import_is_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "old-config")}
            source = home / "old-config/workbench/setup-v1.json"
            original = setup_cli._write_setup_record(source, _selection(home))
            target = home / ".workbench/setup-v1.json"
            with self.assertRaisesRegex(ValueError, "workbench settings migrate --dry-run"):
                setup_cli.default_setup_record_path(environment=environment)
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

    def test_legacy_record_reports_migration_without_blocking_migrate_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            source = home / ".config/workbench/setup-v1.json"
            setup_cli._write_setup_record(source, _selection(home))
            errors = io.StringIO()
            output = io.StringIO()
            with patch.dict(os.environ, environment, clear=True), redirect_stderr(errors), redirect_stdout(output):
                self.assertEqual(2, cli._main(["environment", "resolve", "--json"]))
                self.assertEqual(0, cli._main(["settings", "migrate", "--dry-run"]))
            self.assertIn("workbench settings migrate --dry-run", errors.getvalue())
            self.assertIn("State: ready", output.getvalue())
            self.assertFalse((home / ".workbench/setup-v1.json").exists())

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

    def test_legacy_launcher_import_rejects_invalid_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            source = home / ".config/workbench/launcher-v1.json"
            source.parent.mkdir(parents=True)
            for selection in (
                {"family": "unknown", "executable": str(home / "Launcher"), "root": str(home / "Root")},
                {"family": "prism", "executable": "relative-launcher", "root": str(home / "Root")},
                {"family": [], "executable": str(home / "Launcher"), "root": str(home / "Root")},
            ):
                with self.subTest(selection=selection):
                    body = {
                        "format": "workbench-launcher-setup-record-v1",
                        "schema_version": 1,
                        "selection": selection,
                    }
                    record = {
                        **body,
                        "record_id": "workbench-launcher-setup:sha256:" + sha256(
                            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        ).hexdigest(),
                    }
                    source.write_text(json.dumps(record), encoding="utf-8")
                    with self.assertRaisesRegex(UserPreferencesError, "invalid selection"):
                        inspect_legacy_config_migration(environment=environment)
                    self.assertFalse((home / ".workbench/launcher-v1.json").exists())

    def test_selected_import_recovers_valid_setup_when_other_legacy_file_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            environment = {"HOME": str(home)}
            legacy = home / ".config/workbench"
            setup_cli._write_setup_record(legacy / "setup-v1.json", _selection(home))
            launcher = legacy / "launcher-v1.json"
            launcher.write_text("invalid\n", encoding="utf-8")
            with self.assertRaisesRegex(UserPreferencesError, "legacy launcher configuration"):
                inspect_legacy_config_migration(environment=environment)
            with patch.dict(os.environ, environment, clear=True), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, cli._main([
                    "settings", "migrate", "--dry-run", "--json", "--file", "setup-v1.json",
                ]))
                plan = json.loads(output.getvalue())
            self.assertEqual("workbench-user-config-migration-v1", plan["format"])
            self.assertEqual("ready", plan["state"])
            self.assertEqual(["setup-v1.json"], [row["name"] for row in plan["files"]])
            result = migrate_legacy_config(environment=environment, filenames=("setup-v1.json",))
            self.assertEqual("imported", result["state"])
            self.assertEqual((legacy / "setup-v1.json").read_bytes(), (home / ".workbench/setup-v1.json").read_bytes())
            self.assertEqual("invalid\n", launcher.read_text(encoding="utf-8"))

    def test_each_legacy_record_requires_explicit_import(self) -> None:
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
            for name in ("recipe-fixtures-v1.json", "launcher-v1.json"):
                with self.assertRaisesRegex(ValueError, "workbench settings migrate --dry-run"):
                    default_user_record_path(name, environment=environment)
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ValueError, "workbench settings migrate --dry-run"):
                    default_fixture_registry_path()

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
                patch("workbench_core.environment_resolution.load_setup_record", wraps=setup_cli.load_setup_record) as setup_reader,
                patch("workbench_core.environment_resolution.load_workspaces", wraps=load_workspaces) as workspace_reader,
            ):
                self.assertEqual(
                    home / ".workbench",
                    resolve_environment(suite, environment=environment).configuration_home,
                )
            self.assertEqual(1, setup_reader.call_count)
            self.assertEqual(1, workspace_reader.call_count)

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

    def test_boolean_schema_versions_are_rejected_for_both_user_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"HOME": temporary}
            for loader, path in (
                (load_settings, default_settings_path(environment=environment)),
                (load_workspaces, default_workspaces_path(environment=environment)),
            ):
                record = loader(environment=environment)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({**record, "schema_version": True}), encoding="utf-8")
                with self.assertRaisesRegex(UserPreferencesError, "schema"):
                    loader(environment=environment)

    def test_malformed_default_workspace_has_a_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"HOME": temporary}
            path = default_workspaces_path(environment=environment)
            path.parent.mkdir(parents=True)
            record = {**load_workspaces(environment=environment), "default": []}
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(UserPreferencesError, "default workspace"):
                load_workspaces(environment=environment)

    @unittest.skipIf(os.name == "nt", "POSIX file mode check")
    def test_user_record_is_private_before_atomic_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"HOME": temporary}
            replace = os.replace
            observed: list[int] = []

            def inspect_temporary(source: Path, destination: Path) -> None:
                observed.append(stat.S_IMODE(source.stat().st_mode))
                replace(source, destination)

            old_umask = os.umask(0)
            try:
                with patch("workbench_core.durable_records.os.replace", side_effect=inspect_temporary):
                    set_location("logs", "~/logs", environment=environment)
            finally:
                os.umask(old_umask)
            self.assertEqual([0o600], observed)

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
