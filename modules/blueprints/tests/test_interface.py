#!/usr/bin/env python3

"""I01 conformance tests for the resumable core and canonical CLI."""

from __future__ import annotations

import copy
from io import StringIO
import json
from pathlib import Path
import sys
from typing import Any
import unittest

from jsonschema import Draft202012Validator

from _support import SCHEMA_ROOT, SOURCE_ROOT, WORKBENCH_ROOT

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
        ):
            schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self.assertFalse(schema["additionalProperties"])

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
