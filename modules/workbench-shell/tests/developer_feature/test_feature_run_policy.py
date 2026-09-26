"""Core policy selection at the Feature runtime owner's first allocation."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_developer_feature import ROOT, _bytes, _checkout
from test_developer_source_feature import _checkout as _recipe_checkout
from workbench_core import cli as core_cli
from workbench_core.state_root_selection import effective_state_root, select_state_root
from workbench_shell.developer_feature import (
    build_material_fluid_recipe_plan, retain_feature_record,
)
from workbench_shell import developer_feature_runtime as runtime
from workbench_shell import developer_feature_cli
from workbench_blueprints.profile_construction import recipe_change_authority


class FeatureRunPolicyTests(unittest.TestCase):
    def test_core_selected_actions_bind_workspace_and_direct_override(self) -> None:
        temporary_parent = ROOT / ".workbench/test-tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_parent) as temporary:
            home = Path(temporary)
            workspace = _checkout(home)
            other_parent = home / "other"
            other_parent.mkdir()
            other_workspace = _checkout(other_parent)
            first_root = home / "first-state"
            second_root = home / "second-state"
            plan = build_material_fluid_recipe_plan(
                ROOT, workspace, name="Policy Solvent", color="0x425d73",
                recipe_script="groovy/postInit/chemistry/Probe.groovy",
                recipe_map="MIXER", input_fluid="steam", input_amount=750,
                output_amount=250, duration=320, voltage_tier="MV",
            )
            other_plan = build_material_fluid_recipe_plan(
                ROOT, other_workspace, name="Other Solvent", color="0x425d73",
                recipe_script="groovy/postInit/chemistry/Probe.groovy",
                recipe_map="MIXER", input_fluid="steam", input_amount=750,
                output_amount=250, duration=320, voltage_tier="MV",
            )
            environment = dict(os.environ)
            environment.update({
                "HOME": str(home), "USERPROFILE": str(home),
                "WORKBENCH_CONFIG_HOME": str(home / "config"),
                "WORKBENCH_WORKSPACE": str(workspace),
            })
            environment.pop("WORKBENCH_STATE_ROOT", None)
            initial = effective_state_root(
                workspace, "feature", suite_root=ROOT, environment=environment,
            )
            first = select_state_root(
                workspace, "feature", str(first_root), suite_root=ROOT,
                expected_policy_id=initial["policy_id"], environment=environment,
            )
            retain_feature_record(first_root, "plans", plan)
            before = _bytes(workspace)
            other_before = _bytes(other_workspace)
            second = select_state_root(
                workspace, "feature", str(second_root), suite_root=ROOT,
                expected_policy_id=first["policy_id"], environment=environment,
            )

            def invoke(*arguments: str) -> tuple[int, str, str]:
                stdout = StringIO()
                stderr = StringIO()
                with patch.dict(os.environ, environment, clear=True), patch(
                    "workbench_core.package_guard.account_home", return_value=home,
                ), redirect_stdout(stdout), redirect_stderr(stderr):
                    status = core_cli._main(["feature", *arguments, "--json"])
                return status, stdout.getvalue(), stderr.getvalue()

            status, _stdout, stderr = invoke(
                "apply", "material-fluid-recipe", plan["id"],
                "--consent", plan["id"], "--state-root", str(first_root),
                "--expected-state-root-policy-id", first["policy_id"],
            )
            self.assertEqual(2, status, stderr)
            self.assertIn("state-root policy changed after review", stderr)
            self.assertEqual(before, _bytes(workspace))
            self.assertFalse((first_root / "receipts").exists())

            status, _stdout, stderr = invoke(
                "apply", "material-fluid-recipe", plan["id"],
                "--consent", plan["id"], "--state-root", str(first_root),
                "--expected-state-root-policy-id", second["policy_id"],
            )
            self.assertEqual(2, status, stderr)
            self.assertIn("owner state root differs from the reviewed Core policy", stderr)
            self.assertEqual(before, _bytes(workspace))

            retain_feature_record(second_root, "plans", other_plan)
            status, _stdout, stderr = invoke(
                "apply", "material-fluid-recipe", other_plan["id"],
                "--consent", other_plan["id"],
            )
            self.assertEqual(2, status, stderr)
            self.assertIn("targets another Core-selected workspace", stderr)
            self.assertEqual(other_before, _bytes(other_workspace))
            self.assertFalse((second_root / "receipts").exists())

            status, stdout, stderr = invoke(
                "apply", "material-fluid-recipe", plan["id"],
                "--consent", plan["id"], "--state-root", str(first_root),
            )
            self.assertEqual(0, status, stderr)
            applied = json.loads(stdout)
            self.assertEqual("applied", applied["state"])
            self.assertNotEqual(before, _bytes(workspace))
            status, stdout, stderr = invoke(
                "rollback", "material-fluid-recipe", plan["id"], applied["id"],
                "--state-root", str(first_root),
            )
            self.assertEqual(0, status, stderr)
            self.assertEqual("restored", json.loads(stdout)["state"])
            self.assertEqual(before, _bytes(workspace))

            retain_feature_record(second_root, "plans", plan)
            status, stdout, stderr = invoke(
                "apply", "material-fluid-recipe", plan["id"],
                "--consent", plan["id"],
            )
            self.assertEqual(0, status, stderr)
            selected_application = json.loads(stdout)
            self.assertEqual("applied", selected_application["state"])
            self.assertTrue((second_root / "receipts").is_dir())
            applied_bytes = _bytes(workspace)
            select_state_root(
                workspace, "feature", str(home / "third-state"), suite_root=ROOT,
                expected_policy_id=second["policy_id"], environment=environment,
            )
            for action, arguments, collection in (
                ("rollback", (selected_application["id"],), "rollbacks"),
                ("recover", (), "recoveries"),
            ):
                with self.subTest(action=action):
                    status, _stdout, stderr = invoke(
                        action, "material-fluid-recipe", plan["id"], *arguments,
                        "--state-root", str(second_root),
                        "--expected-state-root-policy-id", second["policy_id"],
                    )
                    self.assertEqual(2, status, stderr)
                    self.assertIn("state-root policy changed after review", stderr)
                    self.assertEqual(applied_bytes, _bytes(workspace))
                    self.assertFalse((second_root / collection).exists())

    def test_compare_runtime_rejects_changed_selection_before_retained_writes(self) -> None:
        temporary_parent = ROOT / ".workbench/test-tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_parent) as temporary:
            home = Path(temporary)
            workspace = _recipe_checkout(home)
            first_root = home / "first-state"
            second_root = home / "second-state"
            plan = recipe_change_authority("supersymmetry").build_recipe_change_plan(
                ROOT, workspace, mutation="add",
                recipe_script="groovy/postInit/chemistry/Probe.groovy",
                recipe_map="batch_reactor",
                fluid_inputs=[{"name": "steam", "amount": 1000}],
                fluid_outputs=[{"name": "water", "amount": 1000}],
                duration=100, voltage_tier="LV",
            )
            environment = dict(os.environ)
            environment.update({
                "HOME": str(home), "USERPROFILE": str(home),
                "WORKBENCH_CONFIG_HOME": str(home / "config"),
                "WORKBENCH_WORKSPACE": str(workspace),
            })
            environment.pop("WORKBENCH_STATE_ROOT", None)
            original = effective_state_root(
                workspace, "feature", suite_root=ROOT, environment=environment,
            )
            first = select_state_root(
                workspace, "feature", str(first_root), suite_root=ROOT,
                expected_policy_id=original["policy_id"], environment=environment,
            )
            retain_feature_record(first_root, "plans", plan)

            def change_before_allocation(*_args, **kwargs):
                select_state_root(
                    workspace, "feature", str(second_root), suite_root=ROOT,
                    expected_policy_id=first["policy_id"], environment=environment,
                )
                with kwargs["allocation_scope"]:
                    kwargs["before_allocation"]()
                self.fail("stale Core policy unexpectedly allowed comparison allocation")

            stdout = StringIO()
            stderr = StringIO()
            with patch.dict(os.environ, environment, clear=True), patch(
                "workbench_core.package_guard.account_home", return_value=home,
            ), patch.object(
                developer_feature_cli, "run_recipe_change_runtime_comparison",
                side_effect=change_before_allocation,
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                status = core_cli._main([
                    "feature", "compare-runtime", "recipe-change", plan["id"],
                    "--consent", plan["id"],
                    "--launcher-executable", str(home / "launcher.exe"),
                    "--launcher-root", str(home / "launcher"), "--json",
                ])
            self.assertEqual(2, status, stderr.getvalue())
            self.assertIn("state-root policy changed after review", stderr.getvalue())
            self.assertFalse((first_root / "runtime").exists())
            self.assertFalse((first_root / "runs").exists())
            self.assertFalse((second_root / "runtime").exists())

    def test_plan_uses_core_selected_root_and_guards_the_retained_write(self) -> None:
        temporary_parent = ROOT / ".workbench/test-tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_parent) as temporary:
            home = Path(temporary)
            workspace = _checkout(home)
            first_root = home / "first-state"
            second_root = home / "second-state"
            environment = dict(os.environ)
            environment.update({
                "HOME": str(home), "USERPROFILE": str(home),
                "WORKBENCH_CONFIG_HOME": str(home / "config"),
                "WORKBENCH_WORKSPACE": str(workspace),
            })
            environment.pop("WORKBENCH_STATE_ROOT", None)
            original = effective_state_root(
                workspace, "feature", suite_root=ROOT, environment=environment,
            )
            first = select_state_root(
                workspace, "feature", str(first_root), suite_root=ROOT,
                expected_policy_id=original["policy_id"], environment=environment,
            )

            def invoke_target(
                target: Path, name: str, *extra: str,
            ) -> tuple[int, str, str]:
                arguments = [
                    "feature", "plan", "material-fluid-recipe", str(target),
                    "--name", name, "--color", "0x425d73",
                    "--recipe-script", "groovy/postInit/chemistry/Probe.groovy",
                    "--recipe-map", "MIXER", "--input-fluid", "steam",
                    *extra, "--json",
                ]
                stdout = StringIO()
                stderr = StringIO()
                with patch.dict(os.environ, environment, clear=True), patch(
                    "workbench_core.package_guard.account_home", return_value=home,
                ), redirect_stdout(stdout), redirect_stderr(stderr):
                    status = core_cli._main(arguments)
                return status, stdout.getvalue(), stderr.getvalue()

            def invoke(name: str, *extra: str) -> tuple[int, str, str]:
                return invoke_target(workspace, name, *extra)

            other_parent = home / "other"
            other_parent.mkdir()
            other_workspace = _checkout(other_parent)
            with patch.object(
                developer_feature_cli, "build_material_fluid_recipe_plan",
            ) as build, patch.object(
                developer_feature_cli, "retain_feature_record",
            ) as retain:
                status, _stdout, stderr = invoke_target(
                    other_workspace, "Wrong Core Workspace",
                )
            self.assertEqual(2, status, stderr)
            self.assertIn("plan target differs from the Core-selected workspace", stderr)
            build.assert_not_called()
            retain.assert_not_called()
            self.assertFalse((first_root / "plans").exists())

            status, stdout, stderr = invoke("Core Selected Plan")
            self.assertEqual(0, status, stderr)
            plan_id = json.loads(stdout)["id"]
            plan_digest = plan_id.rsplit(":", 1)[1]
            self.assertTrue((first_root / "plans" / plan_digest / "record.json").is_file())
            self.assertFalse((Path(original["state_root"]) / "plans" / plan_digest).exists())

            second = select_state_root(
                workspace, "feature", str(second_root), suite_root=ROOT,
                expected_policy_id=first["policy_id"], environment=environment,
            )
            status, _stdout, stderr = invoke(
                "Stale Policy Plan", "--expected-state-root-policy-id", first["policy_id"],
            )
            self.assertEqual(2, status, stderr)
            self.assertIn("state-root policy changed after review", stderr)
            self.assertFalse((second_root / "plans").exists())

            status, _stdout, stderr = invoke(
                "Mismatched Root Plan", "--state-root", str(first_root),
                "--expected-state-root-policy-id", second["policy_id"],
            )
            self.assertEqual(2, status, stderr)
            self.assertIn("owner state root differs from the reviewed Core policy", stderr)
            self.assertEqual([plan_digest], [item.name for item in (first_root / "plans").iterdir()])

            status, stdout, stderr = invoke(
                "Direct Override Plan", "--state-root", str(first_root),
            )
            self.assertEqual(0, status, stderr)
            override_id = json.loads(stdout)["id"]
            self.assertTrue((first_root / "plans" / override_id.rsplit(":", 1)[1] / "record.json").is_file())
            self.assertFalse((second_root / "plans").exists())

            status, stdout, stderr = invoke_target(
                other_workspace, "Direct Other Workspace",
                "--state-root", str(first_root),
            )
            self.assertEqual(0, status, stderr)
            other_override_id = json.loads(stdout)["id"]
            self.assertTrue(
                (first_root / "plans" / other_override_id.rsplit(":", 1)[1] / "record.json").is_file()
            )

            builder = developer_feature_cli.build_material_fluid_recipe_plan

            def change_selection(*args, **kwargs):
                result = builder(*args, **kwargs)
                select_state_root(
                    workspace, "feature", str(home / "third-state"), suite_root=ROOT,
                    expected_policy_id=second["policy_id"], environment=environment,
                )
                return result

            with patch.object(
                developer_feature_cli, "build_material_fluid_recipe_plan",
                side_effect=change_selection,
            ):
                status, _stdout, stderr = invoke("Changed During Build Plan")
            self.assertEqual(2, status, stderr)
            self.assertIn("state-root policy changed after review", stderr)
            self.assertFalse((second_root / "plans").exists())

    def test_core_owner_refuses_stale_or_redirected_root_and_keeps_direct_override(self) -> None:
        temporary_parent = ROOT / ".workbench/test-tmp"
        temporary_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_parent) as temporary:
            home = Path(temporary)
            workspace = _checkout(home)
            first_root = home / "first-state"
            second_root = home / "second-state"
            plan = build_material_fluid_recipe_plan(
                ROOT, workspace, name="Policy Solvent", color="0x425d73",
                recipe_script="groovy/postInit/chemistry/Probe.groovy",
                recipe_map="MIXER", input_fluid="steam", input_amount=750,
                output_amount=250, duration=320, voltage_tier="MV",
            )
            environment = dict(os.environ)
            environment.update({
                "HOME": str(home), "USERPROFILE": str(home),
                "WORKBENCH_CONFIG_HOME": str(home / "config"),
                "WORKBENCH_WORKSPACE": str(workspace),
            })
            environment.pop("WORKBENCH_STATE_ROOT", None)
            initial = effective_state_root(
                workspace, "feature", suite_root=ROOT, environment=environment,
            )
            first = select_state_root(
                workspace, "feature", str(first_root), suite_root=ROOT,
                expected_policy_id=initial["policy_id"], environment=environment,
            )
            retain_feature_record(first_root, "plans", plan)
            second = select_state_root(
                workspace, "feature", str(second_root), suite_root=ROOT,
                expected_policy_id=first["policy_id"], environment=environment,
            )

            def invoke(state_root: Path, policy_id: str | None) -> tuple[int, str, str]:
                arguments = [
                    "feature", "run", "material-fluid-recipe", plan["id"],
                    "--consent", plan["id"],
                    "--launcher-executable", str(home / "launcher.exe"),
                    "--launcher-root", str(home / "launcher"),
                    "--state-root", str(state_root), "--json",
                ]
                if policy_id is not None:
                    arguments.extend(("--expected-state-root-policy-id", policy_id))
                stdout = StringIO()
                stderr = StringIO()
                with patch.dict(os.environ, environment, clear=True), patch(
                    "workbench_core.package_guard.account_home", return_value=home,
                ), redirect_stdout(stdout), redirect_stderr(stderr):
                    status = core_cli._main(arguments)
                return status, stdout.getvalue(), stderr.getvalue()

            status, _stdout, stderr = invoke(first_root, first["policy_id"])
            self.assertEqual(2, status, stderr)
            self.assertIn("state-root policy changed after review", stderr)
            self.assertFalse((first_root / "runtime").exists())

            status, _stdout, stderr = invoke(first_root, second["policy_id"])
            self.assertEqual(2, status, stderr)
            self.assertIn("owner state root differs from the reviewed Core policy", stderr)
            self.assertFalse((first_root / "runtime").exists())

            retain_feature_record(second_root, "plans", plan)
            stale_verification = {"state": "stale", "reason": "test stopped before native launch"}
            with patch.object(runtime, "verify_material_fluid_recipe_plan", return_value=stale_verification):
                status, stdout, stderr = invoke(second_root, second["policy_id"])
            self.assertEqual(1, status, stderr)
            self.assertEqual("incomplete", json.loads(stdout)["state"])
            self.assertTrue((second_root / "runtime/material-fluid-recipe/attempts").is_dir())

            with patch.object(runtime, "verify_material_fluid_recipe_plan", return_value=stale_verification):
                status, stdout, stderr = invoke(first_root, None)
            self.assertEqual(1, status, stderr)
            self.assertEqual("incomplete", json.loads(stdout)["state"])
            self.assertTrue((first_root / "runtime/material-fluid-recipe/attempts").is_dir())


if __name__ == "__main__":
    unittest.main()
