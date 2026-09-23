from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_crucible.runtime_pair import FeatureRuntimePairPorts
from workbench_shell.golden_journey_cli import (
    _installed_supersymmetry_runtime_ports,
    change_main,
    dev_fixture_main,
)


ROOT = Path(__file__).resolve().parents[3]


class GoldenJourneyCliV2Tests(unittest.TestCase):
    def test_exact_profile_runtime_config_constructs_owner_port(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary).resolve()
            uri = lambda name: (base / name).as_uri()
            config = base / "runtime-config.json"
            config.write_text(
                json.dumps(
                    {
                        "format": (
                            "workbench-supersymmetry-installed-material-fluid-"
                            "runtime-config-v1"
                        ),
                        "schema_version": 1,
                        "memory_mib": 8192,
                        "client": {
                            "attach_timeout_seconds": 120,
                            "launcher": "prism",
                            "launcher_executable_uri": uri("PrismLauncher"),
                            "launcher_java_state_uri": None,
                            "launcher_java_uri": None,
                            "launcher_profile": None,
                            "launcher_root_uri": uri("PrismRoot"),
                            "seed_root_uris": [uri("seed")],
                            "session_timeout_seconds": 900,
                            "timeout_seconds": 600,
                        },
                        "server": {
                            "java_uri": uri("java"),
                            "packwiz_installer_jar_uri": uri("packwiz.jar"),
                            "seed_receipt_uri": uri("server-receipt.json"),
                            "shutdown_timeout_seconds": 180,
                            "timeout_seconds": 900,
                        },
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            ports = _installed_supersymmetry_runtime_ports(ROOT, config)
        self.assertIsInstance(ports, FeatureRuntimePairPorts)

    def test_fixture_plan_run_and_recover_reuse_exact_records(self) -> None:
        plan = {"format": "workbench-cleanroom-dev-loop-plan-v1", "plan_id": "plan:test"}
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            plan_path = base / "plan.json"
            receipt_path = base / "receipt.json"
            output, error = StringIO(), StringIO()
            with patch(
                "workbench_shell.golden_journey_cli.plan_cleanroom_dev_loop",
                return_value=plan,
            ) as create:
                self.assertEqual(
                    dev_fixture_main(
                        [
                            "plan",
                            "--gradle-cmd",
                            "/tools/gradle",
                            "--java-home",
                            "/tools/java",
                            "--state-root",
                            str(base / "state"),
                            "--output",
                            str(plan_path),
                            "--json",
                        ],
                        root=ROOT,
                        output=output,
                        error=error,
                    ),
                    0,
                    error.getvalue(),
                )
            self.assertEqual(json.loads(plan_path.read_text()), plan)
            self.assertEqual(create.call_args.kwargs["sides"], ("client", "server"))

            output, error = StringIO(), StringIO()
            result = {"outcome": "passed", "receipt_path": str(receipt_path)}
            with (
                patch(
                    "workbench_shell.golden_journey_cli.validate_cleanroom_dev_loop_plan",
                    return_value=plan,
                ),
                patch(
                    "workbench_shell.golden_journey_cli.execute_cleanroom_dev_loop",
                    return_value=result,
                ) as execute,
            ):
                self.assertEqual(
                    dev_fixture_main(
                        ["run", "--plan", str(plan_path), "--json"],
                        root=ROOT,
                        output=output,
                        error=error,
                    ),
                    0,
                    error.getvalue(),
                )
            execute.assert_called_once_with(ROOT, plan)

            output, error = StringIO(), StringIO()
            with (
                patch(
                    "workbench_shell.golden_journey_cli.plan_cleanroom_dev_loop",
                    return_value=plan,
                ) as direct_plan,
                patch(
                    "workbench_shell.golden_journey_cli.execute_cleanroom_dev_loop",
                    return_value=result,
                ),
            ):
                self.assertEqual(
                    dev_fixture_main(
                        [
                            "run",
                            "--gradle-cmd",
                            "/tools/gradle",
                            "--java-home",
                            "/tools/java",
                            "--state-root",
                            str(base / "state"),
                            "--json",
                        ],
                        root=ROOT,
                        output=output,
                        error=error,
                    ),
                    0,
                    error.getvalue(),
                )
            self.assertEqual(direct_plan.call_args.kwargs["sides"], ("client", "server"))

            receipt_path.write_text("{}\n", encoding="utf-8")
            with patch(
                "workbench_shell.golden_journey_cli.recover_cleanroom_dev_loop",
                return_value={"format": "workbench-cleanroom-dev-loop-recovery-v1", "state": "clean"},
            ):
                self.assertEqual(
                    dev_fixture_main(
                        ["recover", str(receipt_path), "--json"],
                        root=ROOT,
                        output=StringIO(),
                        error=StringIO(),
                    ),
                    0,
                )

    def test_change_start_is_typed_and_runtime_test_requires_owner_port(self) -> None:
        started = {
            "format": "workbench-feature-change-workspace-v1",
            "change_id": "workbench-feature-change:sha256:" + "1" * 64,
            "plan_id": "plan:test",
            "lifecycle": "planned",
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            output, error = StringIO(), StringIO()
            with patch(
                "workbench_shell.golden_journey_cli.start_material_fluid_recipe_change",
                return_value=started,
            ) as start:
                code = change_main(
                    [
                        "start",
                        "material-fluid-recipe",
                        str(base / "workspace"),
                        "--state-root",
                        str(base / "state"),
                        "--name",
                        "Thermal Solvent",
                        "--color",
                        "#Aa44Cc",
                        "--translation",
                        "Thermal Solvent Localized",
                        "--symbol",
                        "ThermalSolventX",
                        "--recipe-script",
                        "groovy/postInit/chemistry/Probe.groovy",
                        "--recipe-map",
                        "batch_reactor",
                        "--input-fluid",
                        "steam",
                        "--input-amount",
                        "750",
                        "--output-amount",
                        "500",
                        "--duration",
                        "320",
                        "--voltage-tier",
                        "MV",
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                )
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(json.loads(output.getvalue())["change_id"], started["change_id"])
            self.assertEqual(start.call_args.kwargs["input_amount"], 750)

            output, error = StringIO(), StringIO()
            self.assertEqual(
                change_main(
                    [
                        "material-fluid-recipe",
                        "test",
                        started["change_id"],
                        "--state-root",
                        str(base / "state"),
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                ),
                2,
            )
            self.assertIn("--runtime-config", error.getvalue())

    def test_change_runtime_config_selects_exact_profile_owner_port(self) -> None:
        change_id = "workbench-feature-change:sha256:" + "1" * 64
        owner_ports = object()
        matrix = {
            "format": "workbench-feature-change-runtime-matrix-v1",
            "matrix_id": "workbench-feature-change-runtime-matrix:sha256:" + "2" * 64,
            "outcome": "passed",
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            config = base / "installed-runtime.json"
            config.write_text("{}\n", encoding="utf-8")
            output, error = StringIO(), StringIO()
            with (
                patch(
                    "workbench_shell.golden_journey_cli."
                    "_installed_supersymmetry_runtime_ports",
                    return_value=owner_ports,
                ) as load_owner,
                patch(
                    "workbench_shell.golden_journey_cli."
                    "run_feature_change_matrix",
                    return_value=matrix,
                ) as run_matrix,
            ):
                code = change_main(
                    [
                        "test",
                        "material-fluid-recipe",
                        change_id,
                        "--state-root",
                        str(base / "state"),
                        "--runtime-config",
                        str(config),
                        "--json",
                    ],
                    root=ROOT,
                    output=output,
                    error=error,
                )
            self.assertEqual(0, code, error.getvalue())
            load_owner.assert_called_once_with(ROOT, config)
            self.assertIs(run_matrix.call_args.kwargs["ports"], owner_ports)
            self.assertEqual(matrix, json.loads(output.getvalue()))

    def test_runtime_config_cannot_replace_an_injected_owner(self) -> None:
        change_id = "workbench-feature-change:sha256:" + "1" * 64
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            error = StringIO()
            code = change_main(
                [
                    "material-fluid-recipe",
                    "rollback",
                    change_id,
                    "--state-root",
                    str(base / "state"),
                    "--runtime-config",
                    str(base / "runtime.json"),
                ],
                root=ROOT,
                output=StringIO(),
                error=error,
                runtime_ports=object(),
            )
        self.assertEqual(2, code)
        self.assertIn("cannot replace an injected runtime owner", error.getvalue())

    def test_current_context_actions_derive_owner_paths_and_ids_once(self) -> None:
        change_id = "workbench-feature-change:sha256:" + "1" * 64
        plan_id = (
            "workbench-developer-material-fluid-recipe-plan:sha256:" + "2" * 64
        )
        context = {"change_id": change_id, "plan_id": plan_id}
        action_results = {
            "open": {"lifecycle": "planned"},
            "test": {"outcome": "passed"},
            "apply": {"state": "applied"},
            "verify": {"state": "verified"},
            "rollback": {"state": "restored"},
            "recover": {"state": "not-needed"},
        }
        owners = {
            "open": "open_feature_change",
            "test": "run_feature_change_matrix",
            "apply": "apply_feature_change",
            "verify": "verify_feature_change",
            "rollback": "rollback_feature_change",
            "recover": "recover_feature_change",
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            base = Path(temporary)
            state = base / "owner-state"
            runtime = base / "runtime-config.json"
            ports = object()
            for action, owner in owners.items():
                with self.subTest(action=action):
                    output, error = StringIO(), StringIO()
                    with (
                        patch(
                            "workbench_shell.golden_journey_cli."
                            "resolve_material_fluid_recipe_session_context",
                            return_value=(context, state, runtime),
                        ) as resolve,
                        patch(
                            "workbench_shell.golden_journey_cli."
                            "_installed_supersymmetry_runtime_ports",
                            return_value=ports,
                        ),
                        patch(
                            f"workbench_shell.golden_journey_cli.{owner}",
                            return_value=action_results[action],
                        ) as invoke,
                    ):
                        code = change_main(
                            [action, "material-fluid-recipe", "--json"],
                            root=ROOT,
                            output=output,
                            error=error,
                        )
                    self.assertEqual(0, code, error.getvalue())
                    resolve.assert_called_once_with(ROOT)
                    self.assertEqual(action_results[action], json.loads(output.getvalue()))
                    arguments = invoke.call_args.args
                    self.assertIn(state, arguments)
                    self.assertIn(change_id, arguments)
                    if action == "apply":
                        self.assertEqual(plan_id, invoke.call_args.kwargs["consent_plan_id"])
                    if action in {"test", "verify", "rollback"}:
                        self.assertIs(ports, invoke.call_args.kwargs["ports"])

            rejected = (
                ["open", "material-fluid-recipe", "--state-root", str(base)],
                ["test", "material-fluid-recipe", "--runtime-config", str(runtime)],
                ["apply", "material-fluid-recipe", "--consent-plan-id", plan_id],
            )
            for argv in rejected:
                with self.subTest(rejected=argv):
                    error = StringIO()
                    with patch(
                        "workbench_shell.golden_journey_cli."
                        "resolve_material_fluid_recipe_session_context",
                        return_value=(context, state, runtime),
                    ):
                        code = change_main(
                            argv,
                            root=ROOT,
                            output=StringIO(),
                            error=error,
                        )
                    self.assertEqual(2, code)
                    self.assertIn("current Work Session", error.getvalue())


if __name__ == "__main__":
    unittest.main()
