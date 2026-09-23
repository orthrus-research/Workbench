"""Regressions for intentionally removed product command surfaces."""

from __future__ import annotations

import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]
WORKBENCH = ROOT / "tools/workbench.py"
from workbench_shell import development_commands as ROUTER
from workbench_core import cli as CORE_ROUTER


class ProductCommandSurfaceTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(WORKBENCH), *arguments],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )

    def test_new_cleanroom_mod_preview_routes_to_the_exact_profile_owner(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            target = Path(temporary) / "new-project"
            result = self._run(
                "new",
                "cleanroom-mod",
                "preview",
                str(target),
                "--output-mode",
                "instructions",
                "--json",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(
                "workbench-cleanroom-mod-construction-result-v2",
                value["format"],
            )
            self.assertTrue(value["plan_id"].startswith(
                "workbench-cleanroom-mod-construction-plan:sha256:"
            ))
            self.assertFalse(target.exists())

    def test_change_runtime_config_routes_to_supersymmetry_owner(self) -> None:
        change_id = "workbench-feature-change:sha256:" + "1" * 64
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            config = base / "runtime.json"
            config.write_text("{}\n", encoding="utf-8")
            result = self._run(
                "change",
                "test",
                "material-fluid-recipe",
                change_id,
                "--state-root",
                str(base / "state"),
                "--runtime-config",
                str(config),
                "--json",
            )
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "installed runtime configuration shape or identity changed",
            result.stderr,
        )

    def test_release_feature_application_is_not_a_product_command(self) -> None:
        for arguments in (
            ("release-feature-application", "status", "--json"),
            ("legacy", "release-feature-application", "status", "--json"),
        ):
            with self.subTest(arguments=arguments):
                result = self._run(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn("no installed module provides this command", result.stderr)

    def test_shell_package_import_does_not_eagerly_load_product_modules(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(
            ROOT / "modules" / "workbench-shell" / "src"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, sys, workbench_shell; "
                    "print(json.dumps(sorted(name for name in sys.modules "
                    "if name.startswith('workbench_shell.'))))"
                ),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], json.loads(result.stdout))

    def test_shell_package_exports_the_susy_mod_dev_contract(self) -> None:
        import workbench_shell

        for name in (
            "SusyModCheckError",
            "SusyModDevError",
            "SusyModLaunchError",
            "SusyModRunError",
            "SusyModServerError",
            "SusyServerMaterializationError",
            "MATERIALIZATION_RECEIPT_FORMAT_V2",
            "MATERIALIZATION_RESULT_FORMAT_V2",
            "DEV_RUN_RECEIPT_FORMAT",
            "DEV_RUN_RESULT_FORMAT",
            "DEV_RUN_SIDES",
            "execute_susy_mod_build",
            "check_susy_mod_server",
            "launch_susy_mod_client",
            "launch_susy_mod_server",
            "run_susy_mod",
            "reopen_susy_mod_run",
            "materialize_susy_server",
            "susy_server_materialization_version",
            "verify_susy_server_materialization_receipt_identity",
            "plan_susy_mod_dev",
            "render_susy_mod_plan",
            "render_susy_mod_check",
            "render_susy_mod_result",
            "render_susy_mod_launch",
            "render_susy_mod_server",
            "render_susy_mod_run",
            "render_susy_server_materialization",
            "validate_susy_mod_plan",
        ):
            with self.subTest(name=name):
                self.assertIn(name, workbench_shell.__all__)
                self.assertTrue(hasattr(workbench_shell, name))

    def test_shell_package_exports_only_the_current_host_adapter(self) -> None:
        import workbench_shell
        from workbench_core import host_adapter

        current = (
            "HostAdapterV3Error",
            "compute_host_adapter_v3_receipt_id",
            "inspect_local_host_adapter_v3",
            "validate_host_adapter_v3_receipt",
        )
        retired = (
            "HostAdapterV1Error",
            "HostAdapterV2Error",
            "compute_host_adapter_v1_receipt_id",
            "compute_host_adapter_v2_receipt_id",
            "inspect_local_host_adapter_v1",
            "inspect_local_host_adapter_v2",
            "validate_host_adapter_v1_receipt",
            "validate_host_adapter_v2_receipt",
        )
        for name in current:
            with self.subTest(name=name):
                self.assertNotIn(name, workbench_shell.__all__)
                self.assertFalse(hasattr(workbench_shell, name))
                self.assertTrue(hasattr(host_adapter, name))
        for name in retired:
            with self.subTest(name=name):
                self.assertNotIn(name, workbench_shell.__all__)
                self.assertFalse(hasattr(workbench_shell, name))

    def test_numbered_milestone_flow_is_not_a_product_command(self) -> None:
        result = self._run("stage3-material-fluid")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no installed module provides this command", result.stderr)
        self.assertNotIn("Stage-3", result.stdout + result.stderr)

    def test_feature_namespace_remains_the_developer_flow(self) -> None:
        result = self._run("feature", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("examples", result.stdout)
        self.assertIn("run", result.stdout)
        self.assertIn("recover", result.stdout)
        self.assertNotIn("release-feature-application", result.stdout)

        plan_help = self._run("feature", "plan", "--help")
        self.assertEqual(plan_help.returncode, 0, plan_help.stderr)
        self.assertIn("plan --example KEY WORKSPACE", plan_help.stdout)

        examples = self._run("feature", "examples", "material-fluid-recipe", "--json")
        self.assertEqual(examples.returncode, 0, examples.stderr)
        record = json.loads(examples.stdout)
        self.assertEqual(1, record["count"])
        self.assertEqual(
            "supersymmetry-material-fluid-recipe-radon",
            record["examples"][0]["example_key"],
        )
        self.assertEqual("complete", record["examples"][0]["runtime_evidence_state"])
        self.assertFalse(record["authority_boundary"]["release_qualified"])

    def test_dev_show_routes_the_read_only_plan_without_building(self) -> None:
        help_result = self._run("dev", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn(
            "{run,show,build,stage,launch,launch-server,check}", help_result.stdout
        )
        self.assertIn("--project", help_result.stdout)
        self.assertIn("--pack", help_result.stdout)
        self.assertIn("--pack-mod", help_result.stdout)
        self.assertIn("--run", help_result.stdout)
        self.assertIn("--launch-timeout", help_result.stdout)
        self.assertIn("--server-template", help_result.stdout)
        self.assertIn("--accept-minecraft-eula", help_result.stdout)
        self.assertIn("--side {auto,client,server,both}", help_result.stdout)
        self.assertIn("--server-java", help_result.stdout)
        self.assertIn("--shutdown-timeout", help_result.stdout)
        self.assertIn("--runtime-experiment", help_result.stdout)
        self.assertIn("susy-reccomplex-susycore-0112-flag", help_result.stdout)
        self.assertIn("susy-server-shutdown-bridge", help_result.stdout)

        plan = {
            "format": "workbench-susy-mod-dev-plan-v1",
            "state": "ready",
            "plan_id": "workbench-susy-mod-dev-plan:fixture",
        }
        output = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
                return_value=plan,
            ) as planner,
            patch(
                "workbench_shell.susy_mod_dev.execute_susy_mod_build"
            ) as builder,
            patch("sys.stdout", output),
        ):
            status = ROUTER._dev_main(
                [
                    "show",
                    "--project",
                    "/tmp/source-mod",
                    "--pack",
                    "/tmp/supersymmetry",
                    "--pack-mod",
                    "sample.pw.toml",
                    "--java-home",
                    "/tmp/jdk17",
                    "--json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), plan)
        planner.assert_called_once_with(
            ROOT,
            Path("/tmp/source-mod"),
            Path("/tmp/supersymmetry"),
            pack_mod="sample.pw.toml",
            java_home=Path("/tmp/jdk17"),
            stage_client=False,
        )
        builder.assert_not_called()

    def test_dev_run_reopens_one_candidate_and_routes_both_sides(self) -> None:
        result = {
            "format": "workbench-susy-mod-dev-run-result-v1",
            "outcome": "passed",
            "receipt": {"receipt_id": "workbench-susy-mod-dev-run:fixture"},
        }
        rendered = json.dumps(result, sort_keys=True) + "\n"
        output = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_run.run_susy_mod",
                return_value=result,
            ) as runner,
            patch(
                "workbench_shell.susy_mod_run.render_susy_mod_run",
                return_value=rendered,
            ) as renderer,
            patch("workbench_shell.susy_mod_dev.plan_susy_mod_dev") as planner,
            patch("workbench_shell.susy_mod_dev.execute_susy_mod_build") as builder,
            patch("sys.stdout", output),
        ):
            status = ROUTER._dev_main(
                [
                    "run",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--side",
                    "both",
                    "--launcher",
                    "multimc",
                    "--launcher-root",
                    "/tmp/multimc-root",
                    "--offline-name",
                    "Developer",
                    "--server-template",
                    "/tmp/susy-server",
                    "--server-java",
                    "/tmp/jdk/bin/java",
                    "--runtime-experiment",
                    "susy-reccomplex-susycore-0112-flag",
                    "--runtime-experiment",
                    "susy-server-shutdown-bridge",
                    "--memory",
                    "6144",
                    "--launch-timeout",
                    "90.5",
                    "--shutdown-timeout",
                    "45.25",
                    "--json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), rendered)
        runner.assert_called_once_with(
            ROOT,
            "susy-mod-20260820T120000000000Z-abcdef123456",
            side="both",
            launcher="multimc",
            launcher_executable=None,
            launcher_root=Path("/tmp/multimc-root"),
            launcher_profile=None,
            launcher_java=None,
            launcher_java_state=None,
            client_compatibility_experiments=(
                "susy-reccomplex-susycore-0112-flag",
            ),
            server_template=Path("/tmp/susy-server"),
            server_java=Path("/tmp/jdk/bin/java"),
            accept_minecraft_eula=False,
            server_compatibility_experiments=(
                "susy-reccomplex-susycore-0112-flag",
                "susy-server-shutdown-bridge",
            ),
            memory_mib=6144,
            offline_name="Developer",
            client_timeout_seconds=90.5,
            server_timeout_seconds=90.5,
            shutdown_timeout_seconds=45.25,
        )
        renderer.assert_called_once_with(result, json_output=True)
        planner.assert_not_called()
        builder.assert_not_called()

    def test_dev_run_builds_once_and_stages_only_for_selected_client(self) -> None:
        preview_plan = {
            "format": "workbench-susy-mod-dev-plan-v1",
            "state": "ready",
            "plan_id": "workbench-susy-mod-dev-plan:preview",
            "replacement": {"applicable_sides": ["client", "server"]},
        }
        staged_plan = {
            **preview_plan,
            "plan_id": "workbench-susy-mod-dev-plan:staged",
        }
        build = {
            "format": "workbench-susy-mod-dev-result-v1",
            "outcome": "passed",
            "run_id": "susy-mod-20260821T120000000000Z-abcdef123456",
        }
        run_result = {
            "format": "workbench-susy-mod-dev-run-result-v1",
            "outcome": "passed",
            "receipt": {},
        }
        with (
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
                side_effect=(preview_plan, staged_plan),
            ) as planner,
            patch(
                "workbench_shell.susy_mod_dev.execute_susy_mod_build",
                return_value=build,
            ) as builder,
            patch(
                "workbench_shell.susy_mod_run.run_susy_mod",
                return_value=run_result,
            ) as runner,
            patch(
                "workbench_shell.susy_mod_run.render_susy_mod_run",
                return_value="both passed\n",
            ),
            patch("sys.stdout", io.StringIO()),
        ):
            status = ROUTER._dev_main(
                [
                    "run",
                    "--project",
                    "/tmp/source-mod",
                    "--pack",
                    "/tmp/supersymmetry",
                    "--side",
                    "both",
                    "--accept-minecraft-eula",
                    "--timeout",
                    "90",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(planner.call_count, 2)
        self.assertFalse(planner.call_args_list[0].kwargs["stage_client"])
        self.assertTrue(planner.call_args_list[1].kwargs["stage_client"])
        builder.assert_called_once_with(
            ROOT,
            staged_plan,
            timeout_seconds=90,
            stage_client=True,
        )
        self.assertEqual(
            runner.call_args.args,
            (ROOT, "susy-mod-20260821T120000000000Z-abcdef123456"),
        )
        self.assertEqual(runner.call_args.kwargs["side"], "both")
        self.assertTrue(runner.call_args.kwargs["accept_minecraft_eula"])

    def test_dev_run_server_only_build_does_not_stage_a_client(self) -> None:
        plan = {
            "format": "workbench-susy-mod-dev-plan-v1",
            "state": "ready",
            "plan_id": "workbench-susy-mod-dev-plan:server",
            "replacement": {"applicable_sides": ["server"]},
        }
        build = {
            "format": "workbench-susy-mod-dev-result-v1",
            "outcome": "passed",
            "run_id": "susy-mod-20260821T120000000000Z-server123456",
        }
        run_result = {
            "format": "workbench-susy-mod-dev-run-result-v1",
            "outcome": "passed",
            "receipt": {},
        }
        with (
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
                return_value=plan,
            ) as planner,
            patch(
                "workbench_shell.susy_mod_dev.execute_susy_mod_build",
                return_value=build,
            ) as builder,
            patch(
                "workbench_shell.susy_mod_run.run_susy_mod",
                return_value=run_result,
            ),
            patch(
                "workbench_shell.susy_mod_run.render_susy_mod_run",
                return_value="server passed\n",
            ),
            patch("sys.stdout", io.StringIO()),
        ):
            status = ROUTER._dev_main(
                [
                    "run",
                    "--project",
                    "/tmp/source-mod",
                    "--pack",
                    "/tmp/supersymmetry",
                    "--side",
                    "server",
                    "--server-template",
                    "/tmp/susy-server",
                ]
            )

        self.assertEqual(status, 0)
        planner.assert_called_once()
        self.assertFalse(planner.call_args.kwargs["stage_client"])
        builder.assert_called_once_with(
            ROOT,
            plan,
            timeout_seconds=1200,
            stage_client=False,
        )

    def test_fresh_server_only_managed_materialization_fails_before_build(self) -> None:
        plan = {
            "format": "workbench-susy-mod-dev-plan-v1",
            "state": "ready",
            "plan_id": "workbench-susy-mod-dev-plan:server",
            "replacement": {"applicable_sides": ["server"]},
        }
        with (
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
                return_value=plan,
            ) as planner,
            patch("workbench_shell.susy_mod_dev.execute_susy_mod_build") as builder,
            patch("sys.stderr", io.StringIO()) as error,
            self.assertRaises(SystemExit) as raised,
        ):
            ROUTER._dev_main(
                [
                    "run",
                    "--project",
                    "/tmp/source-mod",
                    "--pack",
                    "/tmp/supersymmetry",
                    "--side",
                    "server",
                    "--accept-minecraft-eula",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "a fresh server-only run requires --server-template",
            error.getvalue(),
        )
        planner.assert_called_once()
        builder.assert_not_called()

    def test_fresh_auto_rejects_inapplicable_scoped_flags_before_build(self) -> None:
        plan = {
            "format": "workbench-susy-mod-dev-plan-v1",
            "state": "ready",
            "plan_id": "workbench-susy-mod-dev-plan:client-only",
            "replacement": {"applicable_sides": ["client"]},
        }
        with (
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
                return_value=plan,
            ) as planner,
            patch("workbench_shell.susy_mod_dev.execute_susy_mod_build") as builder,
            patch("workbench_shell.susy_mod_run.run_susy_mod") as runner,
            patch("sys.stderr", io.StringIO()) as error,
            self.assertRaises(SystemExit) as raised,
        ):
            ROUTER._dev_main(
                [
                    "run",
                    "--project",
                    "/tmp/source-mod",
                    "--pack",
                    "/tmp/supersymmetry",
                    "--side",
                    "auto",
                    "--server-java",
                    "/tmp/jdk/bin/java",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--server-java", error.getvalue())
        planner.assert_called_once()
        builder.assert_not_called()
        runner.assert_not_called()

    def test_dev_run_rejects_side_scoped_flags_before_execution(self) -> None:
        client_with_server_flag = self._run(
            "dev",
            "run",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--side",
            "client",
            "--server-template",
            "/tmp/susy-server",
        )
        self.assertEqual(client_with_server_flag.returncode, 2)
        self.assertIn(
            "--server-template is not valid with --side client",
            client_with_server_flag.stderr,
        )

        server_with_client_flag = self._run(
            "dev",
            "run",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--side",
            "server",
            "--server-template",
            "/tmp/susy-server",
            "--launcher-root",
            "/tmp/prism-root",
        )
        self.assertEqual(server_with_client_flag.returncode, 2)
        self.assertIn(
            "--launcher-root is not valid with --side server",
            server_with_client_flag.stderr,
        )

        missing_server_source = self._run(
            "dev",
            "run",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--side",
            "both",
        )
        self.assertEqual(missing_server_source.returncode, 2)
        self.assertIn(
            "requires exactly one of --server-template or "
            "--accept-minecraft-eula",
            missing_server_source.stderr,
        )

    def test_dev_launch_routes_one_retained_stage_without_replanning(self) -> None:
        result = {
            "format": "workbench-susy-mod-launch-result-v1",
            "outcome": "passed",
            "receipt": {"launch_id": "workbench-susy-mod-launch:fixture"},
        }
        rendered = json.dumps(result, sort_keys=True) + "\n"
        output = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_launch.launch_susy_mod_client",
                return_value=result,
            ) as launcher,
            patch(
                "workbench_shell.susy_mod_launch.render_susy_mod_launch",
                return_value=rendered,
            ) as renderer,
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
            ) as planner,
            patch(
                "workbench_shell.susy_mod_dev.execute_susy_mod_build",
            ) as builder,
            patch("sys.stdout", output),
        ):
            status = ROUTER._dev_main(
                [
                    "launch",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--launcher",
                    "multimc",
                    "--launcher-executable",
                    "/tmp/multimc",
                    "--launcher-root",
                    "/tmp/multimc-root",
                    "--launcher-profile",
                    "Developer",
                    "--launcher-java",
                    "/tmp/jdk/bin/java",
                    "--launcher-java-state",
                    "/tmp/jdk-state",
                    "--runtime-experiment",
                    "susy-reccomplex-susycore-0112-flag",
                    "--memory",
                    "6144",
                    "--launch-timeout",
                    "90.5",
                    "--json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), rendered)
        launcher.assert_called_once_with(
            ROOT,
            "susy-mod-20260820T120000000000Z-abcdef123456",
            launcher="multimc",
            launcher_executable=Path("/tmp/multimc"),
            launcher_root=Path("/tmp/multimc-root"),
            launcher_profile="Developer",
            launcher_java=Path("/tmp/jdk/bin/java"),
            launcher_java_state=Path("/tmp/jdk-state"),
            compatibility_experiments=(
                "susy-reccomplex-susycore-0112-flag",
            ),
            memory_mib=6144,
            offline_name="Workbench",
            timeout_seconds=90.5,
        )
        renderer.assert_called_once_with(result, json_output=True)
        planner.assert_not_called()
        builder.assert_not_called()

    def test_dev_launch_server_routes_exact_run_options_without_replanning(self) -> None:
        result = {
            "format": "workbench-susy-mod-server-launch-result-v2",
            "outcome": "passed",
            "receipt": {
                "receipt_id": "workbench-susy-mod-server-launch:fixture"
            },
        }
        rendered = json.dumps(result, sort_keys=True) + "\n"
        output = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_server.launch_susy_mod_server",
                return_value=result,
            ) as launcher,
            patch(
                "workbench_shell.susy_mod_server.render_susy_mod_server",
                return_value=rendered,
            ) as renderer,
            patch(
                "workbench_shell.susy_mod_dev.plan_susy_mod_dev",
            ) as planner,
            patch(
                "workbench_shell.susy_mod_dev.execute_susy_mod_build",
            ) as builder,
            patch(
                "workbench_shell.susy_mod_launch.launch_susy_mod_client",
            ) as client_launcher,
            patch("sys.stdout", output),
        ):
            status = ROUTER._dev_main(
                [
                    "launch-server",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--server-template",
                    "/tmp/susy-server",
                    "--server-java",
                    "/tmp/jdk/bin/java",
                    "--runtime-experiment",
                    "susy-reccomplex-arg3",
                    "--runtime-experiment",
                    "susy-server-shutdown-bridge",
                    "--memory",
                    "6144",
                    "--launch-timeout",
                    "90.5",
                    "--shutdown-timeout",
                    "45.25",
                    "--json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), rendered)
        launcher.assert_called_once_with(
            ROOT,
            "susy-mod-20260820T120000000000Z-abcdef123456",
            server_template=Path("/tmp/susy-server"),
            accept_minecraft_eula=False,
            server_java=Path("/tmp/jdk/bin/java"),
            compatibility_experiments=(
                "susy-reccomplex-arg3",
                "susy-server-shutdown-bridge",
            ),
            memory_mib=6144,
            timeout_seconds=90.5,
            shutdown_timeout_seconds=45.25,
        )
        renderer.assert_called_once_with(result, json_output=True)
        planner.assert_not_called()
        builder.assert_not_called()
        client_launcher.assert_not_called()

    def test_dev_launch_server_uses_defaults_and_maps_failed_result_to_one(self) -> None:
        result = {
            "format": "workbench-susy-mod-server-launch-result-v2",
            "outcome": "failed",
            "receipt": {"failure_kind": "server-ready-timeout"},
        }
        output = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_server.launch_susy_mod_server",
                return_value=result,
            ) as launcher,
            patch(
                "workbench_shell.susy_mod_server.render_susy_mod_server",
                return_value="server failed\n",
            ),
            patch("sys.stdout", output),
        ):
            status = ROUTER._dev_main(
                [
                    "launch-server",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--accept-minecraft-eula",
                ]
            )

        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "server failed\n")
        launcher.assert_called_once_with(
            ROOT,
            "susy-mod-20260820T120000000000Z-abcdef123456",
            server_template=None,
            accept_minecraft_eula=True,
            server_java=None,
            compatibility_experiments=(),
            memory_mib=8192,
            timeout_seconds=600.0,
            shutdown_timeout_seconds=180.0,
        )

    def test_dev_launch_server_maps_preflight_error_to_two(self) -> None:
        from workbench_shell.susy_server_materialize import (
            SusyServerMaterializationError,
        )

        error = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_server.launch_susy_mod_server",
                side_effect=SusyServerMaterializationError(
                    "automatic server materialization failed"
                ),
            ),
            patch("sys.stderr", error),
        ):
            status = ROUTER._dev_main(
                [
                    "launch-server",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--accept-minecraft-eula",
                ]
            )

        self.assertEqual(status, 2)
        self.assertIn(
            "Workbench dev failed: automatic server materialization failed",
            error.getvalue(),
        )

    def test_dev_check_routes_matched_server_comparison_without_replanning(self) -> None:
        result = {
            "format": "workbench-susy-mod-server-comparison-result-v1",
            "outcome": "passed",
            "verdict": "candidate-improvement",
            "receipt": {
                "comparison_id": "workbench-susy-mod-server-comparison:fixture"
            },
            "next_actions": [],
        }
        rendered = json.dumps(result, sort_keys=True) + "\n"
        output = io.StringIO()
        with (
            patch(
                "workbench_shell.susy_mod_check.check_susy_mod_server",
                return_value=result,
            ) as checker,
            patch(
                "workbench_shell.susy_mod_check.render_susy_mod_check",
                return_value=rendered,
            ) as renderer,
            patch("workbench_shell.susy_mod_dev.plan_susy_mod_dev") as planner,
            patch(
                "workbench_shell.susy_mod_launch.launch_susy_mod_client"
            ) as client_launcher,
            patch(
                "workbench_shell.susy_mod_server.launch_susy_mod_server"
            ) as server_launcher,
            patch("sys.stdout", output),
        ):
            status = ROUTER._dev_main(
                [
                    "check",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--side",
                    "server",
                    "--server-template",
                    "/tmp/susy-server",
                    "--server-java",
                    "/tmp/jdk/bin/java",
                    "--runtime-experiment",
                    "susy-reccomplex-arg3",
                    "--runtime-experiment",
                    "susy-server-shutdown-bridge",
                    "--memory",
                    "6144",
                    "--launch-timeout",
                    "90.5",
                    "--shutdown-timeout",
                    "45.25",
                    "--json",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), rendered)
        checker.assert_called_once_with(
            ROOT,
            "susy-mod-20260820T120000000000Z-abcdef123456",
            server_template=Path("/tmp/susy-server"),
            accept_minecraft_eula=False,
            server_java=Path("/tmp/jdk/bin/java"),
            compatibility_experiments=(
                "susy-reccomplex-arg3",
                "susy-server-shutdown-bridge",
            ),
            memory_mib=6144,
            timeout_seconds=90.5,
            shutdown_timeout_seconds=45.25,
        )
        renderer.assert_called_once_with(result, json_output=True)
        planner.assert_not_called()
        client_launcher.assert_not_called()
        server_launcher.assert_not_called()

    def test_dev_check_maps_all_verdicts_and_preflight_error(self) -> None:
        for verdict, expected_status in (
            ("no-observed-regression", 0),
            ("candidate-improvement", 0),
            ("candidate-regression", 1),
            ("incomparable", 1),
        ):
            result = {
                "format": "workbench-susy-mod-server-comparison-result-v1",
                "outcome": "passed" if expected_status == 0 else "failed",
                "verdict": verdict,
                "receipt": {},
                "next_actions": [],
            }
            with self.subTest(verdict=verdict), patch(
                "workbench_shell.susy_mod_check.check_susy_mod_server",
                return_value=result,
            ) as checker, patch(
                "workbench_shell.susy_mod_check.render_susy_mod_check",
                return_value=f"{verdict}\n",
            ), patch("sys.stdout", io.StringIO()):
                status = ROUTER._dev_main(
                    [
                        "check",
                        "--run",
                        "susy-mod-20260820T120000000000Z-abcdef123456",
                        "--side",
                        "server",
                        "--accept-minecraft-eula",
                    ]
                )
                self.assertEqual(status, expected_status)
                checker.assert_called_once_with(
                    ROOT,
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    server_template=None,
                    accept_minecraft_eula=True,
                    server_java=None,
                    compatibility_experiments=(),
                    memory_mib=8192,
                    timeout_seconds=600.0,
                    shutdown_timeout_seconds=180.0,
                )

        from workbench_shell.susy_mod_check import SusyModCheckError

        error = io.StringIO()
        with patch(
            "workbench_shell.susy_mod_check.check_susy_mod_server",
            side_effect=SusyModCheckError("baseline attempt is unsafe"),
        ), patch("sys.stderr", error):
            status = ROUTER._dev_main(
                [
                    "check",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--side",
                    "server",
                    "--accept-minecraft-eula",
                ]
            )
        self.assertEqual(status, 2)
        self.assertIn("Workbench dev failed: baseline attempt is unsafe", error.getvalue())

    def test_dev_requires_pack_or_retained_run_for_the_selected_action(self) -> None:
        missing_pack = self._run("dev", "show", "--json")
        self.assertEqual(missing_pack.returncode, 2)
        self.assertIn("show requires --pack", missing_pack.stderr)

        missing_run = self._run("dev", "launch", "--json")
        self.assertEqual(missing_run.returncode, 2)
        self.assertIn("launch requires --run", missing_run.stderr)

        missing_server_run = self._run(
            "dev", "launch-server", "--server-template", "/tmp/susy-server"
        )
        self.assertEqual(missing_server_run.returncode, 2)
        self.assertIn("launch-server requires --run", missing_server_run.stderr)

        missing_server_eula = self._run(
            "dev",
            "launch-server",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
        )
        self.assertEqual(missing_server_eula.returncode, 2)
        self.assertIn(
            "launch-server automatic server materialization requires "
            "--accept-minecraft-eula",
            missing_server_eula.stderr,
        )

        missing_check_run = self._run(
            "dev",
            "check",
            "--side",
            "server",
            "--server-template",
            "/tmp/susy-server",
        )
        self.assertEqual(missing_check_run.returncode, 2)
        self.assertIn("check requires --run", missing_check_run.stderr)

        missing_check_eula = self._run(
            "dev",
            "check",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--side",
            "server",
        )
        self.assertEqual(missing_check_eula.returncode, 2)
        self.assertIn(
            "check automatic server materialization requires "
            "--accept-minecraft-eula",
            missing_check_eula.stderr,
        )

        missing_check_side = self._run(
            "dev",
            "check",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--server-template",
            "/tmp/susy-server",
        )
        self.assertEqual(missing_check_side.returncode, 2)
        self.assertIn("check requires --side server", missing_check_side.stderr)

        unsupported_check_side = self._run(
            "dev",
            "check",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--side",
            "client",
            "--server-template",
            "/tmp/susy-server",
        )
        self.assertEqual(unsupported_check_side.returncode, 2)
        self.assertIn("check requires --side server", unsupported_check_side.stderr)

        for action, extra in (
            ("launch-server", []),
            ("check", ["--side", "server"]),
        ):
            with self.subTest(eula_conflicts_with_override=action):
                conflict = self._run(
                    "dev",
                    action,
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    *extra,
                    "--server-template",
                    "/tmp/susy-server",
                    "--accept-minecraft-eula",
                )
                self.assertEqual(conflict.returncode, 2)
                self.assertIn(
                    "--accept-minecraft-eula cannot be used with "
                    "--server-template",
                    conflict.stderr,
                )

        launch_with_pack = self._run(
            "dev",
            "launch",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--pack",
            "/tmp/supersymmetry",
        )
        self.assertEqual(launch_with_pack.returncode, 2)
        self.assertIn("does not accept --pack", launch_with_pack.stderr)

        server_with_pack = self._run(
            "dev",
            "launch-server",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--server-template",
            "/tmp/susy-server",
            "--pack",
            "/tmp/supersymmetry",
        )
        self.assertEqual(server_with_pack.returncode, 2)
        self.assertIn("does not accept --pack", server_with_pack.stderr)

        check_with_pack = self._run(
            "dev",
            "check",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--side",
            "server",
            "--server-template",
            "/tmp/susy-server",
            "--pack",
            "/tmp/supersymmetry",
        )
        self.assertEqual(check_with_pack.returncode, 2)
        self.assertIn("does not accept --pack", check_with_pack.stderr)

        show_with_run = self._run(
            "dev",
            "show",
            "--pack",
            "/tmp/supersymmetry",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
        )
        self.assertEqual(show_with_run.returncode, 2)
        self.assertIn(
            "--run is only valid with run, launch, launch-server, or check",
            show_with_run.stderr,
        )

        show_with_experiment = self._run(
            "dev",
            "show",
            "--pack",
            "/tmp/supersymmetry",
            "--runtime-experiment",
            "susy-reccomplex-arg3",
        )
        self.assertEqual(show_with_experiment.returncode, 2)
        self.assertIn(
            "--runtime-experiment is only valid with launch",
            show_with_experiment.stderr,
        )

        launch_with_build_timeout = self._run(
            "dev",
            "launch",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--timeout",
            "10",
        )
        self.assertEqual(launch_with_build_timeout.returncode, 2)
        self.assertIn(
            "--timeout is only valid with build or stage",
            launch_with_build_timeout.stderr,
        )

        build_with_launch_timeout = self._run(
            "dev",
            "build",
            "--pack",
            "/tmp/supersymmetry",
            "--launch-timeout",
            "10",
        )
        self.assertEqual(build_with_launch_timeout.returncode, 2)
        self.assertIn(
            "--launch-timeout is only valid with launch",
            build_with_launch_timeout.stderr,
        )

        for option, value in (
            ("--launcher", "prism"),
            ("--launcher-executable", "/tmp/prism"),
            ("--launcher-root", "/tmp/prism-root"),
            ("--launcher-profile", "Developer"),
            ("--offline-name", "Workbench"),
            ("--launcher-java", "/tmp/jdk/bin/java"),
            ("--launcher-java-state", "/tmp/jdk-state"),
        ):
            with self.subTest(server_rejects=option):
                server_with_client_flag = self._run(
                    "dev",
                    "launch-server",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--server-template",
                    "/tmp/susy-server",
                    option,
                    value,
                )
                self.assertEqual(server_with_client_flag.returncode, 2)
                self.assertIn(f"{option} is only valid with launch", server_with_client_flag.stderr)

                check_with_client_flag = self._run(
                    "dev",
                    "check",
                    "--run",
                    "susy-mod-20260820T120000000000Z-abcdef123456",
                    "--side",
                    "server",
                    "--server-template",
                    "/tmp/susy-server",
                    option,
                    value,
                )
                self.assertEqual(check_with_client_flag.returncode, 2)
                self.assertIn(
                    f"{option} is only valid with launch",
                    check_with_client_flag.stderr,
                )

        launch_server_with_side = self._run(
            "dev",
            "launch-server",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--server-template",
            "/tmp/susy-server",
            "--side",
            "server",
        )
        self.assertEqual(launch_server_with_side.returncode, 2)
        self.assertIn("--side is only valid with check", launch_server_with_side.stderr)

        client_with_server_flag = self._run(
            "dev",
            "launch",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--server-template",
            "/tmp/susy-server",
        )
        self.assertEqual(client_with_server_flag.returncode, 2)
        self.assertIn(
            "--server-template is only valid with launch-server",
            client_with_server_flag.stderr,
        )

        client_with_eula = self._run(
            "dev",
            "launch",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--accept-minecraft-eula",
        )
        self.assertEqual(client_with_eula.returncode, 2)
        self.assertIn(
            "--accept-minecraft-eula is only valid with automatic "
            "launch-server or check materialization",
            client_with_eula.stderr,
        )

        client_with_server_experiment = self._run(
            "dev",
            "launch",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--runtime-experiment",
            "susy-server-shutdown-bridge",
        )
        self.assertEqual(client_with_server_experiment.returncode, 2)
        self.assertIn(
            "susy-server-shutdown-bridge is only valid with launch-server",
            client_with_server_experiment.stderr,
        )

        unknown_experiment = self._run(
            "dev",
            "launch",
            "--run",
            "susy-mod-20260820T120000000000Z-abcdef123456",
            "--runtime-experiment",
            "unknown",
        )
        self.assertEqual(unknown_experiment.returncode, 2)
        self.assertIn("invalid choice", unknown_experiment.stderr)

    def test_studio_help_uses_its_public_namespace(self) -> None:
        root_help = self._run("--help")
        self.assertEqual(root_help.returncode, 0, root_help.stderr)
        self.assertIn("studio", root_help.stdout)
        self.assertNotIn("Labs inventory", root_help.stdout)
        self.assertIn(
            "Workbench Core:",
            root_help.stdout,
        )
        for arguments, expected in (
            (("studio", "--help"), "usage: workbench studio"),
            (("studio", "inspect", "--help"), "usage: workbench studio inspect"),
        ):
            with self.subTest(arguments=arguments):
                result = self._run(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertNotIn("usage: workbench feature", result.stdout)

    def test_process_effects_route_delegates_arguments_and_status_verbatim(self) -> None:
        package = ModuleType("workbench_process_studio")
        package.__path__ = []
        cli = ModuleType("workbench_process_studio.cli")
        delegated = Mock(return_value=23)
        cli.main = delegated
        package.cli = cli
        forwarded = [
            "--baseline",
            "/tmp/baseline.json",
            "--candidate",
            "/tmp/candidate.json",
            "--envelope",
            "/tmp/envelope.json",
            "--fixture-adapters",
            "--output",
            "/tmp/comparison.json",
            "--json",
        ]
        with patch.dict(
            sys.modules,
            {
                "workbench_process_studio": package,
                "workbench_process_studio.cli": cli,
            },
        ), patch(
            "workbench_core.dispatch_setup._activate_user_setup",
            return_value=True,
        ) as activate_setup:
            status = CORE_ROUTER._main(
                ["process", "effects", "compare", *forwarded]
            )

        self.assertEqual(status, 23)
        activate_setup.assert_called_once_with(
            ["process", "effects", "compare", *forwarded]
        )
        delegated.assert_called_once_with(forwarded, root=ROOT)

    def test_process_effects_compare_help_uses_the_real_owner_parser(self) -> None:
        result = self._run("process", "effects", "compare", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: workbench process effects compare", result.stdout)
        self.assertIn("--envelope", result.stdout)
        self.assertIn("--fixture-adapters", result.stdout)

    def test_top_level_router_treats_epipe_as_clean_consumer_exit(self) -> None:
        for error in (
            BrokenPipeError(errno.EPIPE, "consumer closed"),
            OSError(errno.EPIPE, "consumer closed"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(CORE_ROUTER, "_main", side_effect=error),
                patch("sys.stdout", io.StringIO()),
            ):
                self.assertEqual(0, CORE_ROUTER.main(["--version"]))

        with (
            patch.object(
                CORE_ROUTER,
                "_main",
                side_effect=OSError(errno.EACCES, "not a pipe"),
            ),
            self.assertRaises(PermissionError),
        ):
            CORE_ROUTER.main(["--version"])

    def test_top_level_router_treats_keyboard_interrupt_as_user_cancellation(self) -> None:
        error = io.StringIO()
        with (
            patch.object(CORE_ROUTER, "_main", side_effect=KeyboardInterrupt),
            patch("sys.stderr", error),
        ):
            self.assertEqual(130, CORE_ROUTER.main(["setup"]))

        self.assertEqual("\nWorkbench cancelled.\n", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    def test_human_version_is_compact_and_has_non_color_status_text(self) -> None:
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.assertEqual(0, CORE_ROUTER.main(["--version"]))

        rendered = output.getvalue()
        self.assertTrue(rendered.startswith("Workbench "))
        self.assertIn("Workbench Core", rendered)
        self.assertNotIn("\x1b[", rendered)
        self.assertLessEqual(len(rendered.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
