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

from test_developer_feature import ROOT, _checkout
from workbench_core import cli as core_cli
from workbench_core.state_root_selection import effective_state_root, select_state_root
from workbench_shell.developer_feature import (
    build_material_fluid_recipe_plan, retain_feature_record,
)
from workbench_shell import developer_feature_runtime as runtime
from workbench_shell import developer_feature_cli


class FeatureRunPolicyTests(unittest.TestCase):
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
            })
            environment.pop("WORKBENCH_STATE_ROOT", None)
            original = effective_state_root(
                workspace, "feature", suite_root=ROOT, environment=environment,
            )
            first = select_state_root(
                workspace, "feature", str(first_root), suite_root=ROOT,
                expected_policy_id=original["policy_id"], environment=environment,
            )

            def invoke(name: str, *extra: str) -> tuple[int, str, str]:
                arguments = [
                    "feature", "plan", "material-fluid-recipe", str(workspace),
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
