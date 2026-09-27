#!/usr/bin/env python3

"""I01 conformance tests for the resumable core and canonical CLI."""

from __future__ import annotations

import copy
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from _support import SCHEMA_ROOT, SOURCE_ROOT, WORKBENCH_ROOT
from workbench_api import Capability, ExecutionContext, Module
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.temporary_leases import CoreTemporaryLeases

REPO_ROOT = WORKBENCH_ROOT
BLUEPRINTS_TOOLS = SOURCE_ROOT

if str(BLUEPRINTS_TOOLS) not in sys.path:
    sys.path.insert(0, str(BLUEPRINTS_TOOLS))

from workbench_blueprints import cli  # noqa: E402
from workbench_blueprints import interface  # noqa: E402
from workbench_blueprints import lifecycle  # noqa: E402
from workbench_blueprints import planner  # noqa: E402
from workbench_blueprints import standards  # noqa: E402
import test_simulation as simulation_fixture  # noqa: E402


class InterfaceTest(unittest.TestCase):

    def setUp(self) -> None:
        self.fixture = simulation_fixture.SimulationTest()
        self.fixture.setUp()
        self.workspace = (
            self.fixture.repository
            / ".workbench/blueprints/interface-test"
        )
        self.adapters = interface.AdapterSet(
            formatter_runner=self.fixture._formatter,
            dependency_provider=self.fixture._provider,
            post_checks={
                "synthetic-registration-check": lambda root: (
                    (
                        root / "groovy/material/syntheticium.groovy"
                    ).is_file(),
                    {"registration": "present"},
                )
            },
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _intake(self, output_mode: str) -> dict[str, Any]:
        value = copy.deepcopy(self.fixture.intake)
        value["output_mode"] = output_mode
        value["consent"]["allow_direct_apply"] = (
            output_mode == "direct-apply"
        )
        return value

    def _core(
        self,
        output_mode: str = "direct-apply",
        *,
        adapters: interface.AdapterSet | None = None,
    ) -> interface.BlueprintsCore:
        core = interface.BlueprintsCore(
            self.workspace,
            adapters=self.adapters if adapters is None else adapters,
        )
        initialized = core.init(
            target_repository=self.fixture.repository,
            repository_id="pack",
            registry_root=self.fixture.registry,
            asset_root=REPO_ROOT,
            ledger_path=self.fixture.ledger,
            intake=self._intake(output_mode),
        )
        self.assertEqual(initialized["state"], "initialized")
        return core

    def _write_json(self, name: str, value: dict[str, Any]) -> Path:
        path = self.fixture.root / name
        path.write_text(
            standards.canonical_json(value), encoding="utf-8"
        )
        return path

    def _cli(
        self,
        arguments: list[str],
        *,
        adapters: interface.AdapterSet | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        stdout = StringIO()
        stderr = StringIO()
        code = cli.main(
            ["--workspace", str(self.workspace), *arguments],
            adapters=self.adapters if adapters is None else adapters,
            stdout=stdout,
            stderr=stderr,
        )
        payload = stdout.getvalue() or stderr.getvalue()
        self.assertTrue(payload)
        result = json.loads(payload)
        self.assertEqual(
            payload, standards.canonical_json(result) + "\n"
        )
        return code, result, stderr.getvalue()

    def test_new_schemas_are_closed_and_valid(self) -> None:
        for name in (
            "blueprints-session-v1.schema.json",
            "blueprints-interface-result-v1.schema.json",
            "blueprints-observation-result-v2.schema.json",
        ):
            schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self.assertFalse(schema["additionalProperties"])

    def test_direct_v2_observation_retains_scratch_without_advancing_v1(self) -> None:
        core = self._core("instructions")
        core.plan(self.fixture.planning_evidence)
        pointer = (self.workspace / "current.json").read_bytes()
        environment_lock = self._write_json("observation-lock.json", self.fixture.environment)
        environment = dict(os.environ)
        environment["WORKBENCH_CONFIG_HOME"] = str(self.fixture.configuration_home)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(WORKBENCH_ROOT / "api/src"),
            str(WORKBENCH_ROOT / "core/src"),
            str(SOURCE_ROOT.parent),
            str(WORKBENCH_ROOT / "modules/project-intelligence/src"),
            environment.get("PYTHONPATH", ""),
        )))
        completed = subprocess.run(
            [sys.executable, "-m", "workbench_blueprints.cli",
             "--workspace", str(self.workspace), "observe-simulation-v2",
             "--environment-lock", str(environment_lock)],
            cwd=self.fixture.root, env=environment,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(5, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("observed-unqualified", result["status"])
        self.assertEqual("planned", result["state"])
        self.assertEqual("observed-unqualified", result["data"]["observation"]["status"])
        self.assertNotIn("evidence_locator_v2", completed.stdout)
        self.assertEqual(pointer, (self.workspace / "current.json").read_bytes())
        self.assertIsNone(interface.SessionStore(self.workspace).load()["simulation_result"])
        with self.assertRaises(interface.InterfaceDiagnostic) as rejected:
            core.generate()
        self.assertEqual("BPI120_ILLEGAL_PREDECESSOR", rejected.exception.code)
        rows = CoreTemporaryLeases.inventory_catalog(
            self.fixture.configuration_home, workspace=self.fixture.repository,
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("retained-unproven", rows[0]["status"])
        self.assertEqual(result["data"]["observation"]["scratch_lease_id"], rows[0]["lease_id"])

    def test_installed_v2_observation_with_passing_gates_stays_planned(self) -> None:
        core = self._core("instructions")
        core.plan(self.fixture.planning_evidence)
        pointer = (self.workspace / "current.json").read_bytes()
        environment_lock = self._write_json("installed-observation-lock.json", self.fixture.environment)
        module = Module("blueprints", "0.1.0", (
            Capability(
                "blueprints.blueprints", ("blueprints",),
                "workbench_registration_blueprints:blueprints", "Blueprints",
            ),
        ))
        installed = (InstalledModule(
            "blueprints", "workbench-blueprints", "0.1.0", "available", module=module,
        ),)
        context = ExecutionContext(
            self.fixture.repository, self.fixture.root / "state",
            configuration_home=self.fixture.configuration_home,
        )
        out, err = StringIO(), StringIO()
        original_main = cli.main
        def invoke(arguments, **kwargs):
            return original_main(
                arguments, adapters=self.adapters, stdout=out, stderr=err,
                **kwargs,
            )
        command = {
            "exit_code": 0, "timed_out": False, "output_limited": False,
            "stdout_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "command_sha256": "a" * 64,
        }
        with patch("workbench_blueprints.cli.main", side_effect=invoke), patch(
            "workbench_blueprints.simulation._command_result", return_value=command,
        ):
            exit_code = dispatch(
                ["blueprints", "--workspace", str(self.workspace),
                 "observe-simulation-v2", "--environment-lock", str(environment_lock)],
                context, installed,
            )
        self.assertEqual(5, exit_code, err.getvalue())
        self.assertEqual("", err.getvalue())
        result = json.loads(out.getvalue())
        self.assertEqual("observed-unqualified", result["status"])
        self.assertEqual("passed", result["data"]["observation"]["gate_status"])
        self.assertEqual("planned", result["state"])
        self.assertEqual(pointer, (self.workspace / "current.json").read_bytes())
        self.assertIsNone(interface.SessionStore(self.workspace).load()["run"]["simulation"])
        with patch("workbench_blueprints.simulation._command_result", return_value=command):
            simulated = core.simulate(self.fixture.environment)
        self.assertEqual("simulated", simulated["state"])
        self.assertEqual("succeeded", simulated["status"])
        self.assertEqual("released", core.generate()["state"])

    def test_installed_v2_refuses_another_selected_workspace(self) -> None:
        core = self._core("instructions")
        core.plan(self.fixture.planning_evidence)
        pointer = (self.workspace / "current.json").read_bytes()
        environment_lock = self._write_json("mismatched-observation-lock.json", self.fixture.environment)
        out, err = StringIO(), StringIO()
        code = cli.main(
            ["--workspace", str(self.workspace), "observe-simulation-v2",
             "--environment-lock", str(environment_lock)],
            selected_workspace=self.fixture.root / "different-target",
            selected_configuration_home=self.fixture.configuration_home,
            stdout=out, stderr=err,
        )
        self.assertEqual(4, code)
        self.assertEqual("", out.getvalue())
        self.assertEqual("rejected", json.loads(err.getvalue())["status"])
        self.assertEqual("BPI159_CORE_CUSTODY", json.loads(err.getvalue())["diagnostics"][0]["code"])
        self.assertEqual(pointer, (self.workspace / "current.json").read_bytes())
        self.assertEqual([], CoreTemporaryLeases.inventory_catalog(
            self.fixture.configuration_home, workspace=self.fixture.repository,
        ))

    def test_installed_v2_refuses_changed_target_before_core_lease(self) -> None:
        core = self._core("instructions")
        core.plan(self.fixture.planning_evidence)
        pointer = (self.workspace / "current.json").read_bytes()
        environment_lock = self._write_json("changed-target-lock.json", self.fixture.environment)
        out, err = StringIO(), StringIO()
        with patch("workbench_blueprints.cli.record_store_host_bound", return_value=True), patch(
            "workbench_blueprints.cli._direct_target",
            return_value=self.fixture.root / "external-target",
        ):
            code = cli.main(
                ["--workspace", str(self.workspace), "observe-simulation-v2",
                 "--environment-lock", str(environment_lock)],
                selected_workspace=self.fixture.repository,
                selected_configuration_home=self.fixture.configuration_home,
                stdout=out, stderr=err,
            )
        self.assertEqual(4, code)
        self.assertEqual("", out.getvalue())
        self.assertEqual("BPI159_CORE_CUSTODY", json.loads(err.getvalue())["diagnostics"][0]["code"])
        self.assertEqual(pointer, (self.workspace / "current.json").read_bytes())
        self.assertEqual([], CoreTemporaryLeases.inventory_catalog(
            self.fixture.configuration_home, workspace=self.fixture.repository,
        ))

    def test_installed_v2_refuses_redirected_session_inside_selected_workspace(self) -> None:
        core = self._core("instructions")
        core.plan(self.fixture.planning_evidence)
        redirected = self.workspace.parent / "redirected-session"
        redirected.symlink_to(self.workspace, target_is_directory=True)
        environment_lock = self._write_json("redirected-observation-lock.json", self.fixture.environment)
        out, err = StringIO(), StringIO()
        code = cli.main(
            ["--workspace", str(redirected), "observe-simulation-v2",
             "--environment-lock", str(environment_lock)],
            selected_workspace=self.fixture.repository,
            selected_configuration_home=self.fixture.configuration_home,
            stdout=out, stderr=err,
        )
        self.assertEqual(4, code)
        self.assertEqual("", out.getvalue())
        self.assertEqual("BPI106_WORKSPACE", json.loads(err.getvalue())["diagnostics"][0]["code"])
        self.assertEqual([], CoreTemporaryLeases.inventory_catalog(
            self.fixture.configuration_home, workspace=self.fixture.repository,
        ))

    def test_v2_rejection_uses_distinct_result_without_session_event(self) -> None:
        self._core("instructions")
        environment_lock = self._write_json("rejected-observation-lock.json", self.fixture.environment)
        before = interface.SessionStore(self.workspace).load()["run"]
        code, result, stderr = self._cli([
            "observe-simulation-v2", "--environment-lock", str(environment_lock),
        ])
        self.assertEqual(3, code)
        self.assertTrue(stderr)
        self.assertEqual("susy-blueprints-observation-result-v2", result["format"])
        self.assertEqual("rejected", result["status"])
        self.assertEqual("BPI120_ILLEGAL_PREDECESSOR", result["diagnostics"][0]["code"])
        self.assertEqual(before, interface.SessionStore(self.workspace).load()["run"])

    def test_init_requires_explicit_profile_authority_paths(self) -> None:
        with self.assertRaises(interface.InterfaceDiagnostic) as context:
            cli._parser().parse_args(
                [
                    "--workspace",
                    str(self.workspace),
                    "init",
                    "--target-repository",
                    str(self.fixture.repository),
                    "--repository-id",
                    "pack",
                    "--feature-family",
                    "material-backed-fluid",
                ]
            )
        self.assertEqual("BPI100_USAGE", context.exception.code)
        self.assertIn("--registry-root", context.exception.message)
        self.assertIn("--ledger", context.exception.message)

    def test_direct_cli_binds_target_core_custody_and_resumes(self) -> None:
        intake = self._write_json("direct-intake.json", self._intake("instructions"))
        configuration_home = self.fixture.root / "direct-configuration"
        environment = dict(os.environ)
        environment["WORKBENCH_CONFIG_HOME"] = str(configuration_home)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
            str(WORKBENCH_ROOT / "api/src"),
            str(WORKBENCH_ROOT / "core/src"),
            str(SOURCE_ROOT.parent),
            str(WORKBENCH_ROOT / "modules/project-intelligence/src"),
            environment.get("PYTHONPATH", ""),
        )))

        def command(*arguments: str) -> dict[str, Any]:
            completed = subprocess.run(
                [sys.executable, "-m", "workbench_blueprints.cli",
                 "--workspace", str(self.workspace), *arguments],
                cwd=self.fixture.root, env=environment,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            return json.loads(completed.stdout)

        initialized = command(
            "init",
            "--target-repository", str(self.fixture.repository),
            "--repository-id", "pack",
            "--intake", str(intake),
            "--registry-root", str(self.fixture.registry),
            "--asset-root", str(REPO_ROOT),
            "--ledger", str(self.fixture.ledger),
        )
        self.assertEqual(initialized["state"], "initialized")
        history = command("history")
        self.assertEqual(history["state"], "initialized")
        stores = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (configuration_home / "resources-v1/stores").glob("*.json")
        ]
        self.assertTrue(stores)
        self.assertTrue(all(row["workspace"] == str(self.fixture.repository) for row in stores))
        self.assertIn(str(self.workspace), {row["root"] for row in stores})

    def test_direct_cli_rejects_workspace_outside_target_before_core_registration(self) -> None:
        intake = self._write_json("outside-intake.json", self._intake("instructions"))
        configuration_home = self.fixture.root / "outside-configuration"
        environment = dict(os.environ)
        environment["WORKBENCH_CONFIG_HOME"] = str(configuration_home)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(WORKBENCH_ROOT / "api/src"),
            str(WORKBENCH_ROOT / "core/src"),
            str(SOURCE_ROOT.parent),
            str(WORKBENCH_ROOT / "modules/project-intelligence/src"),
        ))
        completed = subprocess.run(
            [sys.executable, "-m", "workbench_blueprints.cli",
             "--workspace", str(self.fixture.root / "other/.workbench/blueprints/session"),
             "init", "--target-repository", str(self.fixture.repository),
             "--repository-id", "pack", "--intake", str(intake),
             "--registry-root", str(self.fixture.registry),
             "--asset-root", str(REPO_ROOT),
             "--ledger", str(self.fixture.ledger)],
            cwd=self.fixture.root, env=environment,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(json.loads(completed.stderr)["diagnostics"][0]["code"], "BPI106_WORKSPACE")
        self.assertFalse(configuration_home.exists())

    def test_session_lock_uses_core_exclusive_legacy_marker(self) -> None:
        store = interface.SessionStore(self.workspace)
        lock_path = self.workspace / "interface.lock"
        with store.lock(create=True):
            self.assertEqual(b"", lock_path.read_bytes())
            with self.assertRaises(interface.InterfaceDiagnostic) as context:
                with store.lock():
                    pass
            self.assertEqual("BPI107_SESSION_LOCK", context.exception.code)
        self.assertFalse(lock_path.exists())
        with store.lock():
            self.assertTrue(lock_path.is_file())
        self.assertFalse(lock_path.exists())
        with self.assertRaisesRegex(RuntimeError, "owner failed"):
            with store.lock():
                raise RuntimeError("owner failed")
        self.assertFalse(lock_path.exists())

    def test_session_lock_preserves_interrupted_legacy_marker(self) -> None:
        self.workspace.mkdir(mode=0o700, parents=True)
        lock_path = self.workspace / "interface.lock"
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)
        store = interface.SessionStore(self.workspace)
        with self.assertRaises(interface.InterfaceDiagnostic) as context:
            with store.lock():
                pass
        self.assertEqual("BPI107_SESSION_LOCK", context.exception.code)
        self.assertEqual(b"", lock_path.read_bytes())
        lock_path.unlink()
        with store.lock():
            self.assertEqual(b"", lock_path.read_bytes())
        self.assertFalse(lock_path.exists())

    def test_session_lock_refuses_redirected_legacy_marker(self) -> None:
        self.workspace.mkdir(mode=0o700, parents=True)
        outside = self.fixture.root / "outside-lock"
        outside.write_bytes(b"outside")
        lock_path = self.workspace / "interface.lock"
        lock_path.symlink_to(outside)
        with self.assertRaises(interface.InterfaceDiagnostic) as context:
            with interface.SessionStore(self.workspace).lock():
                pass
        self.assertEqual("BPI107_SESSION_LOCK", context.exception.code)
        self.assertEqual(b"outside", outside.read_bytes())
        self.assertTrue(lock_path.is_symlink())

    def test_cli_keeps_an_existing_core_store_scope(self) -> None:
        self._core("instructions")
        with patch(
            "workbench_core.host_services.direct_module_custody_scope",
            side_effect=AssertionError("dispatch custody was shadowed"),
        ):
            code, result, stderr = self._cli(["history"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(result["state"], "initialized")

    def test_core_completes_resumable_direct_lifecycle(self) -> None:
        core = self._core()
        planned = core.plan(self.fixture.planning_evidence)
        simulated = interface.BlueprintsCore(
            self.workspace, adapters=self.adapters
        ).simulate(self.fixture.environment)
        generated = interface.BlueprintsCore(
            self.workspace, adapters=self.adapters
        ).generate()
        applied = interface.BlueprintsCore(
            self.workspace, adapters=self.adapters
        ).apply()
        verified = interface.BlueprintsCore(
            self.workspace, adapters=self.adapters
        ).verify()
        history = interface.BlueprintsCore(
            self.workspace, adapters=self.adapters
        ).history()
        exported = interface.BlueprintsCore(
            self.workspace, adapters=self.adapters
        ).export_proof()

        self.assertEqual(planned["state"], "planned")
        self.assertEqual(simulated["state"], "simulated")
        self.assertEqual(generated["state"], "released")
        self.assertEqual(applied["state"], "applied")
        self.assertEqual(verified["state"], "verified")
        self.assertEqual(history["state"], "verified")
        self.assertEqual(exported["state"], "verified")
        session = interface.SessionStore(self.workspace).load()
        self.assertEqual(
            [row["phase"] for row in session["run"]["events"]],
            [
                "init",
                "plan",
                "simulate",
                "generate",
                "apply",
                "verify",
                "history",
                "export-proof",
            ],
        )
        self.assertEqual(
            session["run"]["run_id"], exported["run_id"]
        )
        self.assertTrue(session["last_history_locator"])
        self.assertEqual(
            history["data"]["history"]["run_id"], history["run_id"]
        )
        proof = exported["data"]["proof"]
        self.assertEqual(proof["closure"], "target-verified")
        private_ids = {
            row["artifact_id"]
            for row in proof["artifacts"]
            if row["privacy"] == "local-private"
        }
        payload_ids = {
            row["artifact_id"]
            for row in exported["data"]["export_bundle"]["artifacts"]
        }
        self.assertTrue(private_ids.isdisjoint(payload_ids))

    def test_non_mutating_release_exports_but_cannot_apply(self) -> None:
        core = self._core("instructions")
        core.plan(self.fixture.planning_evidence)
        core.simulate(self.fixture.environment)
        generated = core.generate()
        before = planner.capture_target_state(self.fixture.repository, "pack")
        with self.assertRaisesRegex(
            interface.InterfaceDiagnostic, "BPI122_OUTPUT_MODE"
        ):
            core.apply()
        self.assertEqual(
            before,
            planner.capture_target_state(self.fixture.repository, "pack"),
        )
        exported = core.export_proof()
        self.assertEqual(exported["data"]["proof"]["closure"], "release-validated")
        session = interface.SessionStore(self.workspace).load()
        self.assertEqual(session["run"]["state"], "released")
        self.assertEqual(
            [row["phase"] for row in session["run"]["events"]],
            ["init", "plan", "simulate", "generate", "export-proof"],
        )
        self.assertEqual(generated["data"]["delivery"]["output_mode"], "instructions")

    def test_blocked_plan_is_persisted_without_candidate_and_can_retry(self) -> None:
        core = self._core("instructions")
        unavailable_query = copy.deepcopy(
            self.fixture.planning_evidence["atlas"]["queries"][0]
        )
        unavailable_query.pop("evidence_sha256")
        unavailable_query["availability"] = "unavailable"
        unavailable_query["result"] = None
        allocation = copy.deepcopy(self.fixture.planning_evidence["allocation"])
        reconciliation = copy.deepcopy(
            self.fixture.planning_evidence["reconciliation"]
        )
        for row in [*allocation, *reconciliation]:
            row.pop("evidence_sha256")
        unavailable = planner.build_planning_evidence(
            self.fixture.target["target_state_id"],
            queries=[unavailable_query],
            allocation=allocation,
            reconciliation=reconciliation,
        )
        blocked = core.plan(unavailable)
        self.assertEqual(blocked["status"], "failed")
        self.assertEqual(blocked["exit_code"], 5)
        self.assertEqual(blocked["state"], "initialized")
        self.assertFalse((self.workspace / "sealed").exists())
        registrations = (
            self.fixture.configuration_home / "resources-v1/stores"
        )
        self.assertFalse(any(
            json.loads(path.read_text(encoding="utf-8"))["root"]
            == str(self.workspace / "sealed")
            for path in registrations.glob("*.json")
        ))
        self.assertIsNone(
            interface.SessionStore(self.workspace).load()["run"]["candidate"]
        )
        ready = core.plan(self.fixture.planning_evidence)
        self.assertEqual(ready["state"], "planned")
        self.assertEqual(
            [row["result"] for row in interface.SessionStore(
                self.workspace
            ).load()["run"]["events"]],
            ["succeeded", "failed", "succeeded"],
        )

    def test_cli_runs_same_full_lifecycle_with_local_dependency_adapter(
        self,
    ) -> None:
        intake = self._write_json("intake.json", self._intake("direct-apply"))
        evidence = self._write_json(
            "planning-evidence.json", self.fixture.planning_evidence
        )
        environment = self._write_json(
            "environment-lock.json", self.fixture.environment
        )
        dependency_root = self.fixture.root / "dependency-source"
        dependency_root.mkdir()
        for name, content in self.fixture.tools.items():
            (dependency_root / name).write_bytes(content)
        cli_adapters = interface.AdapterSet(
            formatter_runner=self.fixture._formatter,
            post_checks=self.adapters.post_checks,
        )

        commands = [
            [
                "init",
                "--target-repository",
                str(self.fixture.repository),
                "--repository-id",
                "pack",
                "--intake",
                str(intake),
                "--registry-root",
                str(self.fixture.registry),
                "--asset-root",
                str(REPO_ROOT),
                "--ledger",
                str(self.fixture.ledger),
            ],
            ["plan", "--planning-evidence", str(evidence)],
            [
                "simulate",
                "--environment-lock",
                str(environment),
                "--dependency-source",
                str(dependency_root),
            ],
            ["generate"],
            ["apply"],
            ["verify"],
            ["history"],
            ["export-proof"],
        ]
        states = []
        for arguments in commands:
            code, result, stderr = self._cli(
                arguments, adapters=cli_adapters
            )
            self.assertEqual(code, 0, result)
            self.assertFalse(stderr)
            self.assertEqual(result["command"], arguments[0])
            states.append(result["state"])
            self.assertNotIn(
                "evidence_locator",
                standards.canonical_json(result),
            )
        self.assertEqual(
            states,
            [
                "initialized",
                "planned",
                "simulated",
                "released",
                "applied",
                "verified",
                "verified",
                "verified",
            ],
        )

    def test_illegal_cli_predecessor_is_rejected_without_event(self) -> None:
        self._core("instructions")
        environment = self._write_json(
            "environment-lock.json", self.fixture.environment
        )
        before = interface.SessionStore(self.workspace).load()["run"]
        code, result, stderr = self._cli(
            ["simulate", "--environment-lock", str(environment)]
        )
        self.assertEqual(code, 3)
        self.assertTrue(stderr)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(
            result["diagnostics"][0]["code"], "BPI120_ILLEGAL_PREDECESSOR"
        )
        after = interface.SessionStore(self.workspace).load()["run"]
        self.assertEqual(before, after)

    def test_history_is_a_no_op_from_initialized_state(self) -> None:
        core = self._core("instructions")
        recorded = core.history()
        self.assertEqual(recorded["state"], "initialized")
        self.assertEqual(
            [row["phase"] for row in interface.SessionStore(
                self.workspace
            ).load()["run"]["events"]],
            ["init", "history"],
        )
        planned = core.plan(self.fixture.planning_evidence)
        self.assertEqual(planned["state"], "planned")

    def test_missing_dependency_adapter_fails_simulation_without_release(
        self,
    ) -> None:
        adapters = interface.AdapterSet(
            formatter_runner=self.fixture._formatter
        )
        core = self._core("instructions", adapters=adapters)
        core.plan(self.fixture.planning_evidence)
        failed = core.simulate(self.fixture.environment)
        self.assertEqual(failed["state"], "simulation-failed")
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn(
            "evidence_locator", standards.canonical_json(failed)
        )
        with self.assertRaisesRegex(
            interface.InterfaceDiagnostic, "BPI120_ILLEGAL_PREDECESSOR"
        ):
            core.generate()
        session = interface.SessionStore(self.workspace).load()
        self.assertIsNone(session["run"]["release"])

    def test_cli_usage_and_duplicate_json_have_stable_exit_classes(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        code = cli.main(
            ["--workspace", str(self.workspace), "simulate"],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 2)
        usage = json.loads(stderr.getvalue())
        self.assertEqual(usage["command"], "simulate")
        self.assertEqual(usage["diagnostics"][0]["code"], "BPI100_USAGE")

        duplicate = self.fixture.root / "duplicate-intake.json"
        duplicate.write_text('{"sequence":0,"sequence":1}', encoding="utf-8")
        code, result, rejected = self._cli(
            [
                "init",
                "--target-repository",
                str(self.fixture.repository),
                "--repository-id",
                "pack",
                "--intake",
                str(duplicate),
                "--registry-root",
                str(self.fixture.registry),
                "--asset-root",
                str(REPO_ROOT),
                "--ledger",
                str(self.fixture.ledger),
            ]
        )
        self.assertEqual(code, 4)
        self.assertTrue(rejected)
        self.assertEqual(
            result["diagnostics"][0]["code"], "BPI102_JSON_DUPLICATE"
        )
        self.assertFalse((self.workspace / "current.json").exists())

    def test_cli_flags_and_json_compile_to_identical_request(self) -> None:
        intake = self._write_json(
            "intake.json", self._intake("instructions")
        )
        common = [
            "--target-repository",
            str(self.fixture.repository),
            "--repository-id",
            "pack",
            "--registry-root",
            str(self.fixture.registry),
            "--asset-root",
            str(REPO_ROOT),
            "--ledger",
            str(self.fixture.ledger),
        ]
        code, from_json, stderr = self._cli(
            ["init", *common, "--intake", str(intake)]
        )
        self.assertEqual(code, 0)
        self.assertFalse(stderr)

        flags_workspace = (
            self.fixture.repository
            / ".workbench/blueprints/flags-test"
        )
        stdout = StringIO()
        stderr_stream = StringIO()
        flag_arguments = [
            "--workspace",
            str(flags_workspace),
            "init",
            *common,
            "--feature-family",
            "material-backed-fluid",
            "--operation",
            "create",
            "--target-key",
            "susy:syntheticium",
            "--desired-outcome",
            "Create a material-backed fluid.",
            "--parameter",
            'translation="Syntheticium"',
            "--parameter",
            'name="Syntheticium"',
            "--output-mode",
            "instructions",
        ]
        flag_code = cli.main(
            flag_arguments,
            adapters=self.adapters,
            stdout=stdout,
            stderr=stderr_stream,
        )
        self.assertEqual(flag_code, 0, stderr_stream.getvalue())
        from_flags = json.loads(stdout.getvalue())
        self.assertEqual(
            from_json["data"]["request"], from_flags["data"]["request"]
        )
        self.assertEqual(from_json["run_id"], from_flags["run_id"])

    def test_session_pointer_tamper_fails_closed(self) -> None:
        self._core("instructions")
        pointer_path = self.workspace / "current.json"
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["session_sha256"] = "0" * 64
        pointer_path.write_text(
            standards.canonical_json(pointer), encoding="utf-8"
        )
        code, result, stderr = self._cli(["history"])
        self.assertEqual(code, 4)
        self.assertTrue(stderr)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(
            result["diagnostics"][0]["code"], "BPA111_ARTIFACT_MISSING"
        )

    def test_session_pointer_uses_core_custody_and_historical_read(self) -> None:
        self._core("instructions")
        pointer_path = self.workspace / "current.json"
        before = pointer_path.read_bytes()
        self.assertEqual(standards.canonical_json(json.loads(before)).encode("utf-8"), before)
        catalog_root = self.fixture.configuration_home / "resources-v1/stores"
        stores = [json.loads(path.read_text(encoding="utf-8")) for path in catalog_root.glob("*.json")]
        self.assertIn(
            ("blueprints-session-pointer-v1", str(self.workspace)),
            {(row["family"], row["root"]) for row in stores},
        )
        with (
            patch("workbench_blueprints.interface.open_record_store", return_value=None),
            patch("workbench_blueprints.lifecycle.open_record_store", return_value=None),
        ):
            self.assertEqual("initialized", interface.SessionStore(self.workspace).load()["run"]["state"])
        self.assertEqual(before, pointer_path.read_bytes())

    def test_workspace_traversal_is_rejected_before_creation(self) -> None:
        escaped = (
            self.fixture.repository
            / ".workbench/blueprints/session/../../escaped"
        )
        core = interface.BlueprintsCore(escaped, adapters=self.adapters)
        with self.assertRaisesRegex(
            interface.InterfaceDiagnostic, "BPI106_WORKSPACE"
        ):
            core.init(
                target_repository=self.fixture.repository,
                repository_id="pack",
                registry_root=self.fixture.registry,
                asset_root=REPO_ROOT,
                ledger_path=self.fixture.ledger,
                intake=self._intake("instructions"),
            )
        self.assertFalse(
            (
                self.fixture.repository / ".workbench/escaped"
            ).exists()
        )

    def test_legacy_workspace_root_is_rejected_without_creation(self) -> None:
        workspace = (
            self.fixture.repository
            / ".deconstruction/blueprints/legacy-session"
        )
        core = interface.BlueprintsCore(workspace, adapters=self.adapters)
        with self.assertRaisesRegex(
            interface.InterfaceDiagnostic,
            "workspace must be under target/.workbench/blueprints",
        ):
            core.init(
                target_repository=self.fixture.repository,
                repository_id="pack",
                registry_root=self.fixture.registry,
                asset_root=REPO_ROOT,
                ledger_path=self.fixture.ledger,
                intake=self._intake("instructions"),
            )
        self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
