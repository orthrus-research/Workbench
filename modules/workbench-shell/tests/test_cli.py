"""Focused tests for the useful Workbench inspection command."""

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
from unittest.mock import ANY, patch
from urllib.parse import unquote, urlparse


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_SOURCE = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from supersymmetry_project_fixture import (  # noqa: E402
    create_supersymmetry_project,
)
from workbench_shell.cli import main as cli_main  # noqa: E402
from workbench_core.configuration import (  # noqa: E402
    load_workbench_configuration,
)

class WorkbenchCliTest(unittest.TestCase):
    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        source_roots = [
            str(MODULE_ROOT / "src"),
            str(PROJECT_INTELLIGENCE_SOURCE),
        ]
        if environment.get("PYTHONPATH"):
            source_roots.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(source_roots)
        return environment

    def test_json_inspection_runs_from_the_project_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_supersymmetry_project(Path(temporary))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workbench_shell",
                    "inspect",
                    str(project),
                    "--json",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project,
                env=self._environment(),
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(
                result["workspace_context"]["project"]["name"],
                "Supersymmetry",
            )
            self.assertEqual(
                result["workspace_context"]["platform"]["profile_id"],
                "workbench-platform:cleanroom:provisional",
            )
            self.assertFalse((project / "profiles").exists())

    def test_human_inspection_surfaces_source_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_supersymmetry_project(Path(temporary))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workbench_shell",
                    "inspect",
                    str(project),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project,
                env=self._environment(),
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Project: Supersymmetry test", completed.stdout)
            self.assertIn(
                "Source: Minecraft 1.12.2; forge 14.23.5.2860",
                completed.stdout,
            )
            self.assertIn(
                "Target: workbench-platform:cleanroom:provisional",
                completed.stdout,
            )

    def test_feature_studio_plan_routes_through_one_owner_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "source"
            workspace.mkdir()
            result = {
                "format": "workbench-feature-studio-result-v2",
                "schema_version": 2,
                "result_id": "sha256:" + "1" * 64,
                "operation": "plan",
                "state": "ready",
            }
            output = io.StringIO()
            with (
                patch("workbench_shell.cli.plan_feature", return_value=result) as plan,
                patch(
                    "workbench_shell.cli.validate_feature_result",
                    side_effect=lambda value, **_kwargs: value,
                ),
            ):
                with redirect_stdout(output):
                    status = cli_main([
                        "feature",
                        "plan",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--name",
                        "Pilot Coolant",
                        "--color",
                        "0x425d73",
                        "--json",
                    ])
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            plan.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                workspace,
                name="Pilot Coolant",
                color="0x425d73",
                translation=None,
                symbol=None,
                launcher="prism",
            )

    def test_feature_json_preview_never_invokes_export_or_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            reviewed = "sha256:" + "a" * 64
            result = {
                "format": "workbench-feature-studio-result-v2",
                "schema_version": 2,
                "result_id": "feature-studio-result:sha256:" + "1" * 64,
                "operation": "export",
                "state": "ready",
                "plan": {"flow_plan_id": reviewed},
            }
            for operation in ("export", "verify"):
                with self.subTest(operation=operation):
                    selected = dict(result, operation=operation)
                    argv = [
                        "feature",
                        operation,
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--name",
                        "Pilot Coolant",
                        "--color",
                        "0x425d73",
                        "--plan-id",
                        reviewed,
                        "--show",
                        "--json",
                    ]
                    if operation == "export":
                        argv.extend(["--output", str(root / "export")])
                    else:
                        argv.extend(
                            [
                                "--launcher-executable",
                                str(root / "launcher"),
                                "--launcher-root",
                                str(root / "launcher-root"),
                            ]
                        )
                    output = io.StringIO()
                    with (
                        patch(
                            "workbench_shell.cli.preview_feature",
                            return_value=selected,
                        ) as preview,
                        patch("workbench_shell.cli.export_feature") as export,
                        patch("workbench_shell.cli.verify_feature") as verify,
                        patch(
                            "workbench_shell.cli.validate_feature_result",
                            side_effect=lambda value, **_kwargs: value,
                        ),
                        redirect_stdout(output),
                    ):
                        self.assertEqual(cli_main(argv), 0)
                    self.assertEqual(json.loads(output.getvalue()), selected)
                    preview.assert_called_once()
                    export.assert_not_called()
                    verify.assert_not_called()
                    self.assertFalse((root / "export").exists())

    def test_runtime_plan_uses_resolved_bootstrap_and_java_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_supersymmetry_project(Path(temporary))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workbench_shell",
                    "runtime-plan",
                    str(project),
                    "--side",
                    "client",
                    "--launcher",
                    "multimc",
                    "--json",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project,
                env=self._environment(),
                timeout=10,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            plan = json.loads(completed.stdout)
            self.assertEqual(plan["format"], "workbench-runtime-plan-v1")
            self.assertEqual(plan["state"], "ready")
            self.assertEqual(plan["request"], {
                "side": "client",
                "launcher": "multimc",
            })
            self.assertEqual(plan["blockers"], [])
            self.assertEqual(
                plan["target"]["cleanroom_version"],
                "0.6.12-alpha",
            )
            self.assertEqual(
                next(
                    artifact
                    for artifact in plan["artifacts"]
                    if artifact["id"] == "cleanroom_client"
                )["state"],
                "resolved",
            )
            self.assertEqual(
                plan["target"]["runtime_java"],
                "eclipse-temurin-25.0.4+7",
            )
            fixture_path = Path(
                unquote(urlparse(plan["target"]["fixture_root_uri"]).path)
            )
            self.assertFalse(fixture_path.exists())

    def test_runtime_materialize_routes_seed_roots_through_the_core(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "pack"
            seed = root / "existing-minecraft"
            packwiz = root / "packwiz"
            for path in (workspace, seed):
                path.mkdir()
            packwiz.write_text("test\n", encoding="utf-8")
            result = {
                "format": "workbench-packwiz-materialization-result-v2",
                "schema_version": 2,
                "outcome": "installed",
                "receipt": {
                    "materialization_id": "sha256:" + ("1" * 64),
                    "payload": {
                        "file_count": 2,
                        "total_bytes": 3,
                    },
                    "project": {
                        "name": "Supersymmetry",
                        "version": "test",
                    },
                    "target": {
                        "instance_root_uri": (
                            root / "instance"
                        ).as_uri(),
                        "receipt_uri": (
                            root / "receipts/packwiz-materialization-v2.json"
                        ).as_uri(),
                    },
                },
            }
            configuration = load_workbench_configuration(REPOSITORY_ROOT)
            output = io.StringIO()
            with (
                patch(
                    "workbench_shell.cli.load_workbench_configuration",
                    return_value=configuration,
                ) as load_configuration,
                patch(
                    "workbench_shell.cli.materialize_project_runtime",
                    return_value=result,
                ) as materialize,
                patch(
                    "workbench_shell.cli._require_manual_artifact_preflight"
                ) as preflight,
            ):
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-materialize",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--launcher",
                        "multimc",
                        "--packwiz",
                        str(packwiz),
                        "--seed",
                        str(seed),
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            load_configuration.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                Path("workbench.toml"),
            )
            preflight.assert_called_once_with(configuration, workspace, [seed])
            materialize.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                workspace,
                launcher="multimc",
                packwiz_executable=packwiz,
                seed_roots=[seed],
                configuration=configuration,
            )

    def test_runtime_materialize_human_output_surfaces_packwiz_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "pack"
            workspace.mkdir()
            result = {
                "format": "workbench-packwiz-materialization-result-v2",
                "schema_version": 2,
                "outcome": "installed",
                "receipt": {
                    "materialization_id": "sha256:" + ("1" * 64),
                    "project": {
                        "name": "Supersymmetry",
                        "version": "test",
                    },
                    "packwiz_options": {
                        "policy": "pack-declared-defaults",
                        "optional_count": 2,
                        "enabled_count": 1,
                        "disabled_count": 1,
                    },
                    "payload": {
                        "file_count": 2,
                        "total_bytes": 3,
                    },
                    "target": {
                        "instance_root_uri": (root / "instance").as_uri(),
                        "receipt_uri": (
                            root / "receipts/packwiz-materialization-v2.json"
                        ).as_uri(),
                    },
                },
            }
            output = io.StringIO()
            with (
                patch(
                    "workbench_shell.cli.materialize_project_runtime",
                    return_value=result,
                ),
                patch(
                    "workbench_shell.cli._require_manual_artifact_preflight"
                ) as preflight,
            ):
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-materialize",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                    ])

            self.assertEqual(status, 0)
            preflight.assert_called_once_with(ANY, workspace, [])
            rendered = output.getvalue()
            self.assertIn(
                "Packwiz declared defaults: 1 enabled, 1 disabled",
                rendered,
            )
            self.assertIn("packwiz-materialization-v2.json", rendered)

    def test_runtime_launch_emits_failed_receipt_and_nonzero_status(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "pack"
            launcher = root / "prismlauncher"
            launcher_root = root / "launcher-data"
            seed = root / "seed"
            config = root / "launch.toml"
            for path in (workspace, seed):
                path.mkdir()
            launcher.write_text("test\n", encoding="utf-8")
            config.write_text(
                (REPOSITORY_ROOT / "workbench.toml").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            result = {
                "format": "workbench-runtime-launch-result-v1",
                "schema_version": 1,
                "outcome": "failed",
                "receipt": {
                    "launch_id": "sha256:" + ("1" * 64),
                    "project": {
                        "name": "Supersymmetry",
                        "version": "test",
                    },
                    "launcher": {
                        "family": "prism",
                        "version_output": "PrismLauncher 11.0.2",
                        "projection_uri": (root / "instance").as_uri(),
                        "process_state": "exited",
                    },
                    "observation": {
                        "failure_kind": "minecraft-crash-report",
                    },
                    "target": {
                        "receipt_uri": (root / "receipt.json").as_uri(),
                    },
                },
            }
            configuration = load_workbench_configuration(
                REPOSITORY_ROOT,
                config,
            )
            output = io.StringIO()
            with (
                patch(
                    "workbench_shell.cli.load_workbench_configuration",
                    return_value=configuration,
                ) as load_configuration,
                patch(
                    "workbench_shell.cli.launch_project_runtime",
                    return_value=result,
                ) as launch,
                patch(
                    "workbench_shell.cli._require_manual_artifact_preflight"
                ) as preflight,
            ):
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-launch",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--config",
                        str(config),
                        "--launcher-executable",
                        str(launcher),
                        "--launcher-root",
                        str(launcher_root),
                        "--seed",
                        str(seed),
                        "--memory-mib",
                        "4096",
                        "--timeout",
                        "12",
                        "--json",
                    ])

            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.getvalue()), result)
            load_configuration.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                config,
            )
            preflight.assert_called_once_with(configuration, workspace, [seed])
            launch.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                workspace,
                launcher="prism",
                launcher_executable=launcher,
                launcher_root=launcher_root,
                launcher_profile=None,
                launcher_java=None,
                launcher_java_state=None,
                packwiz_executable=None,
                seed_roots=[seed],
                memory_mib=4096,
                offline_name="Workbench",
                compatibility_patches=[],
                timeout_seconds=12.0,
                configuration=configuration,
            )
            self.assertIs(
                launch.call_args.kwargs["configuration"],
                configuration,
            )

    def test_runtime_observe_routes_full_session_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "pack"
            launcher = root / "prismlauncher"
            launcher_root = root / "launcher-data"
            seed = root / "seed"
            patch_file = root / "compatibility.json"
            for path in (workspace, seed):
                path.mkdir()
            launcher.write_text("test\n", encoding="utf-8")
            patch_file.write_text("{}\n", encoding="utf-8")
            result = {
                "format": "workbench-runtime-observation-result-v1",
                "schema_version": 1,
                "outcome": "completed",
            }
            output = io.StringIO()
            with (
                patch(
                    "workbench_shell.cli.observe_project_runtime",
                    return_value=result,
                ) as observe,
                patch(
                    "workbench_shell.cli._require_manual_artifact_preflight"
                ) as preflight,
            ):
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-observe",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--launcher-executable",
                        str(launcher),
                        "--launcher-root",
                        str(launcher_root),
                        "--seed",
                        str(seed),
                        "--memory-mib",
                        "12288",
                        "--compatibility-patch",
                        str(patch_file),
                        "--launch-timeout",
                        "600",
                        "--attach-timeout",
                        "45",
                        "--session-timeout",
                        "7200",
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            preflight.assert_called_once_with(ANY, workspace, [seed])
            observe.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                workspace,
                launcher="prism",
                launcher_executable=launcher,
                launcher_root=launcher_root,
                launcher_profile=None,
                launcher_java=None,
                launcher_java_state=None,
                packwiz_executable=None,
                seed_roots=[seed],
                memory_mib=12288,
                offline_name="Workbench",
                compatibility_patches=[patch_file],
                timeout_seconds=600.0,
                attach_timeout=45.0,
                session_timeout=7200.0,
                configuration=ANY,
            )

    def test_runtime_diagnose_routes_roots_and_blocked_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "pack"
            receipt = root / "runtime-launch-v1.json"
            artifacts = root / "instance"
            workspace.mkdir()
            artifacts.mkdir()
            receipt.write_text("{}\n", encoding="utf-8")
            result = {
                "format": "workbench-runtime-diagnosis-v2",
                "schema_version": 2,
                "diagnosis_id": "sha256:" + ("2" * 64),
                "operation_class": "read-only",
                "authority": {
                    "classification": "integration-observation",
                    "normative": False,
                    "atlas_publication": False,
                    "sentinel_policy_finding": False,
                },
                "state": "blocked",
                "findings": [],
            }
            output = io.StringIO()
            with patch(
                "workbench_shell.cli.diagnose_project_runtime",
                return_value=result,
            ) as diagnose:
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-diagnose",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--receipt",
                        str(receipt),
                        "--artifact-root",
                        str(artifacts),
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            diagnose.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                workspace,
                launch_receipt=receipt,
                artifact_roots=[artifacts],
                configuration=ANY,
            )

    def test_runtime_worldgen_audit_routes_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "pack"
            runtime = root / "runtime"
            workspace.mkdir()
            runtime.mkdir()
            result = {
                "format": "workbench-runtime-worldgen-audit-v1",
                "schema_version": 1,
                "audit_id": "sha256:" + ("3" * 64),
                "operation_class": "read-only",
                "state": "findings-observed",
                "findings": [],
            }
            output = io.StringIO()
            with patch(
                "workbench_shell.cli.audit_project_worldgen",
                return_value=result,
            ) as audit:
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-worldgen-audit",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--runtime-root",
                        str(runtime),
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            audit.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                workspace,
                runtime_root=runtime,
                configuration=ANY,
            )

    def test_runtime_worldgen_fingerprint_routes_world_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            world = root / "world"
            output_path = root / "fingerprint.json"
            world.mkdir()
            result = {
                "format": "atlas-experimental-anvil-worldgen-fingerprint-v1",
                "schema_version": 1,
                "fingerprint_id": "sha256:" + ("4" * 64),
                "state": "fingerprinted",
            }
            output = io.StringIO()
            with patch(
                "workbench_shell.cli.fingerprint_runtime_worldgen",
                return_value=result,
            ) as fingerprint:
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-worldgen-fingerprint",
                        str(world),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--output",
                        str(output_path),
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            fingerprint.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                world,
                output=output_path,
            )

    def test_runtime_worldgen_compare_routes_both_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left.json"
            right = root / "right.json"
            output_path = root / "comparison.json"
            result = {
                "format": "atlas-experimental-anvil-worldgen-comparison-v1",
                "schema_version": 1,
                "comparison_id": "sha256:" + ("5" * 64),
                "state": "compared",
            }
            output = io.StringIO()
            with patch(
                "workbench_shell.cli.compare_runtime_worldgen",
                return_value=result,
            ) as compare:
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-worldgen-compare",
                        str(left),
                        str(right),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--output",
                        str(output_path),
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            compare.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                left,
                right,
                output=output_path,
            )

    def test_runtime_worldgen_block_delta_routes_both_worlds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            output_path = root / "block-delta.json"
            result = {
                "format": "atlas-experimental-anvil-block-delta-v2",
                "schema_version": 2,
                "observation_id": "sha256:" + ("6" * 64),
                "state": "attributed",
            }
            output = io.StringIO()
            with patch(
                "workbench_shell.cli.attribute_runtime_worldgen_blocks",
                return_value=result,
            ) as block_delta:
                with redirect_stdout(output):
                    status = cli_main([
                        "runtime-worldgen-block-delta",
                        str(left),
                        str(right),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                        "--output",
                        str(output_path),
                        "--json",
                    ])

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            block_delta.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                left,
                right,
                output=output_path,
            )


if __name__ == "__main__":
    unittest.main()
