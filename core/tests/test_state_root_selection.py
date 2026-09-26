"""Workspace-bound Core state-root policy and public settings route."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import cli, settings_cli
from workbench_core.state_root_selection import (
    CoreStateRootPolicies, clear_stale_state_root, effective_state_root,
    load_state_root_selections, select_state_root,
)
from workbench_core.user_preferences import (
    UserPreferencesError, register_workspace, set_workspace_selection,
)


class StateRootSelectionTests(unittest.TestCase):
    def test_core_owner_port_resolves_the_saved_feature_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "project"
            workspace.mkdir()
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            original = effective_state_root(
                workspace, "feature", suite_root=home, environment=environment,
            )
            selected = select_state_root(
                workspace, "feature", str(home / "feature-state"), suite_root=home,
                expected_policy_id=original["policy_id"], environment=environment,
            )
            provider = CoreStateRootPolicies(
                suite_root=home, configuration_home=home / "config",
                environment=environment,
            )
            self.assertEqual(selected, provider.resolve(workspace, "feature"))

    def test_settings_route_runs_with_only_core_and_api_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "project"
            workspace.mkdir()
            repository = Path(__file__).resolve().parents[2]
            environment = {
                **os.environ,
                "HOME": str(home),
                "WORKBENCH_CONFIG_HOME": str(home / "config"),
                "PYTHONPATH": os.pathsep.join((
                    str(repository / "api/src"), str(repository / "core/src"),
                )),
            }
            environment.pop("WORKBENCH_STATE_ROOT", None)
            process = subprocess.run([
                sys.executable, "-c",
                "import json,sys; from workbench_core.settings_cli import main; "
                "raise SystemExit(main(sys.argv[1:]))",
                "state-root", "resolve", str(workspace), "product-spine", "--json",
            ], cwd=home, env=environment, capture_output=True, text=True, check=True)
            policy = json.loads(process.stdout)
            self.assertEqual(str(workspace), policy["workspace"])
            self.assertEqual("platform-default", policy["source"])
            self.assertEqual("", process.stderr)

    def test_two_core_clients_read_same_workspace_choice_and_stale_review_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "project"
            workspace.mkdir()
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            register_workspace("project", str(workspace), environment=environment)
            initial = effective_state_root(
                workspace, "product-spine", suite_root=home, environment=environment,
            )
            selected_path = home / "retained" / "sessions"
            selected = select_state_root(
                workspace, "product-spine", str(selected_path), suite_root=home,
                expected_policy_id=initial["policy_id"], environment=environment,
            )
            self.assertEqual("user-selection", selected["source"])
            self.assertEqual(str(selected_path), selected["state_root"])
            self.assertNotEqual(initial["policy_id"], selected["policy_id"])
            with self.assertRaisesRegex(UserPreferencesError, "outside the workspace"):
                select_state_root(
                    workspace, "product-spine", str(workspace / "state"),
                    suite_root=home, expected_policy_id=selected["policy_id"],
                    environment=environment,
                )

            with patch.dict(os.environ, environment, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, settings_cli.main([
                        "state-root", "resolve", str(workspace), "product-spine", "--json",
                    ], suite_root=home))
                self.assertEqual(selected, json.loads(output.getvalue()))
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, cli._main([
                        "settings", "state-root", "resolve", str(workspace),
                        "product-spine", "--json",
                    ]))
                self.assertEqual(selected["policy_id"], json.loads(output.getvalue())["policy_id"])

            with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
                effective_state_root(
                    workspace, "product-spine", suite_root=home, environment=environment,
                    expected_policy_id=initial["policy_id"],
                )
            with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
                select_state_root(
                    workspace, "product-spine", str(home / "other"), suite_root=home,
                    expected_policy_id=initial["policy_id"], environment=environment,
                )
            self.assertEqual(
                selected_path,
                Path(effective_state_root(
                    workspace, "product-spine", suite_root=home, environment=environment,
                )["state_root"]),
            )

    def test_roles_are_independent_and_environment_override_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "project"
            workspace.mkdir()
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            original = effective_state_root(workspace, "product-spine", suite_root=home, environment=environment)
            selected = select_state_root(
                workspace, "product-spine", str(home / "product"), suite_root=home,
                expected_policy_id=original["policy_id"], environment=environment,
            )
            feature = effective_state_root(workspace, "feature", suite_root=home, environment=environment)
            self.assertEqual("platform-default", feature["source"])
            override = effective_state_root(
                workspace, "product-spine", suite_root=home,
                environment={**environment, "WORKBENCH_STATE_ROOT": str(home / "override")},
            )
            self.assertEqual(str(home / "override" / "product-spine"), override["state_root"])
            self.assertEqual("environment", override["source"])
            self.assertNotEqual(selected["policy_id"], override["policy_id"])
            with self.assertRaisesRegex(UserPreferencesError, "invocation state-root override"):
                select_state_root(
                    workspace, "product-spine", str(home / "hidden"), suite_root=home,
                    expected_policy_id=override["policy_id"],
                    environment={**environment, "WORKBENCH_STATE_ROOT": str(home / "override")},
                )

    def test_redirected_root_and_changed_workspace_identity_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            workspace = home / "project"
            workspace.mkdir()
            environment = {"HOME": str(home), "WORKBENCH_CONFIG_HOME": str(home / "config")}
            before = effective_state_root(workspace, "feature", suite_root=home, environment=environment)
            selected_path = home / "selected"
            select_state_root(
                workspace, "feature", str(selected_path), suite_root=home,
                expected_policy_id=before["policy_id"], environment=environment,
            )
            (home / "actual").mkdir()
            selected_path.symlink_to(home / "actual", target_is_directory=True)
            with self.assertRaisesRegex(UserPreferencesError, "unsafe"):
                effective_state_root(workspace, "feature", suite_root=home, environment=environment)
            selected_path.unlink()
            register_workspace("project", str(workspace), environment=environment)
            set_workspace_selection("project", java_home=str(home / "jdk"), environment=environment)
            with self.assertRaisesRegex(UserPreferencesError, "changed workspace identity"):
                effective_state_root(workspace, "feature", suite_root=home, environment=environment)
            saved = load_state_root_selections(environment=environment)
            with self.assertRaisesRegex(UserPreferencesError, "changed after review"):
                clear_stale_state_root(
                    workspace, "feature", suite_root=home,
                    expected_record_id="old", environment=environment,
                )
            with patch.dict(os.environ, environment, clear=True):
                shown = io.StringIO()
                with redirect_stdout(shown):
                    self.assertEqual(0, settings_cli.main(["show", "--json"], suite_root=home))
                self.assertEqual(saved, json.loads(shown.getvalue())["state_roots"])
                cleared_output = io.StringIO()
                with redirect_stdout(cleared_output):
                    self.assertEqual(0, settings_cli.main([
                        "state-root", "clear-stale", str(workspace), "feature",
                        "--expected-record-id", saved["record_id"], "--json",
                    ], suite_root=home))
                cleared = json.loads(cleared_output.getvalue())
            self.assertEqual("platform-default", cleared["source"])
            self.assertEqual(cleared, effective_state_root(
                workspace, "feature", suite_root=home, environment=environment,
            ))


if __name__ == "__main__":
    unittest.main()
