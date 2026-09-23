"""Focused lifecycle and failure tests for Workbench protocol V2."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import ANY, patch

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from supersymmetry_project_fixture import (  # noqa: E402
    create_supersymmetry_project,
)
from workbench_shell import ProtocolSession  # noqa: E402


def _initialize_request(
    *,
    major: int = 2,
    minor: int = 0,
    required_methods: list[str] | None = None,
    workspace: Path = REPOSITORY_ROOT,
) -> dict[str, object]:
    params: dict[str, object] = {
        "protocol_version": {"major": major, "minor": minor},
        "client": {
            "id": "workbench-client:test",
            "kind": "test",
            "version": "0.0.0",
        },
        "workspace_roots": [workspace.as_uri()],
        "execution_host": {"kind": "test"},
    }
    if required_methods is not None:
        params["required_methods"] = required_methods
    return {
        "jsonrpc": "2.0",
        "id": "initialize",
        "method": "initialize",
        "params": params,
    }


class ProtocolSessionTest(unittest.TestCase):
    def test_configuration_is_loaded_once_and_reused_for_session(self) -> None:
        snapshot = object()
        inspected = {"workspace_context": {"project": {"name": "probe"}}}
        planned = {"format": "workbench-runtime-plan-v1"}
        with (
            patch(
                "workbench_shell.protocol.load_workbench_configuration",
                return_value=snapshot,
            ) as load_configuration,
            patch(
                "workbench_shell.protocol.inspect_project",
                return_value=inspected,
            ) as inspect,
            patch(
                "workbench_shell.protocol.plan_project_runtime",
                return_value=planned,
            ) as plan,
        ):
            session = ProtocolSession(suite_root=REPOSITORY_ROOT)
            initialized = session.handle_message(_initialize_request())
            inspection = session.handle_message({
                "jsonrpc": "2.0",
                "id": "inspect",
                "method": "workspace/inspect",
                "params": {},
            })
            runtime_plan = session.handle_message({
                "jsonrpc": "2.0",
                "id": "runtime-plan",
                "method": "runtime/plan",
                "params": {
                    "side": "client",
                    "launcher": "prism",
                },
            })

        self.assertIn("result", initialized or {})
        self.assertEqual(inspection, {
            "jsonrpc": "2.0",
            "id": "inspect",
            "result": inspected,
        })
        self.assertEqual(runtime_plan, {
            "jsonrpc": "2.0",
            "id": "runtime-plan",
            "result": planned,
        })
        load_configuration.assert_called_once_with(
            REPOSITORY_ROOT.resolve(),
            Path("workbench.toml"),
        )
        self.assertIs(inspect.call_args.kwargs["configuration"], snapshot)
        self.assertIs(plan.call_args.kwargs["configuration"], snapshot)

    def test_initialize_inspect_and_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = create_supersymmetry_project(Path(temporary))
            logs: list[str] = []
            session = ProtocolSession(
                suite_root=REPOSITORY_ROOT,
                logger=logs.append,
            )
            initialize = session.handle_message(
                _initialize_request(
                    minor=7,
                    required_methods=[
                        "workspace/inspect",
                        "runtime/plan",
                        "runtime/diagnose",
                        "shutdown",
                    ],
                    workspace=project,
                )
            )
            self.assertIsNotNone(initialize)
            assert initialize is not None
            self.assertEqual(initialize["result"]["protocol_version"], {
                "major": 2,
                "minor": 3,
            })
            self.assertRegex(
                initialize["result"]["server"]["distribution"]["identity"],
                r"^sha256:[0-9a-f]{64}$",
            )
            unavailable = initialize["result"]["capabilities"][
                "unavailable_methods"
            ]
            self.assertEqual(unavailable, [])
            self.assertIn(
                "runtime/plan",
                {
                    method["method"]
                    for method in initialize["result"]["capabilities"][
                        "supported_methods"
                    ]
                },
            )
            self.assertIn(
                "registration/apply",
                {
                    method["method"]
                    for method in initialize["result"]["capabilities"][
                        "supported_methods"
                    ]
                },
            )

            inspect = session.handle_message({
                "jsonrpc": "2.0",
                "id": "inspect",
                "method": "workspace/inspect",
                "params": {},
            })
            self.assertIsNotNone(inspect)
            assert inspect is not None
            self.assertEqual(
                inspect["result"]["workspace_context"]["project"]["name"],
                "Supersymmetry",
            )
            self.assertEqual(
                inspect["result"]["workspace_context"]["platform"][
                    "profile_id"
                ],
                "workbench-platform:cleanroom:provisional",
            )

            runtime_plan = session.handle_message({
                "jsonrpc": "2.0",
                "id": "runtime-plan",
                "method": "runtime/plan",
                "params": {
                    "side": "client",
                    "launcher": "prism",
                },
            })
            self.assertIsNotNone(runtime_plan)
            assert runtime_plan is not None
            self.assertEqual(runtime_plan["result"]["state"], "ready")
            self.assertEqual(
                runtime_plan["result"]["request"]["launcher"],
                "prism",
            )

            receipt_path = Path(temporary) / "runtime-launch-v1.json"
            receipt_path.write_text("{}\n", encoding="utf-8")
            diagnosis_result = {
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
            with patch(
                "workbench_shell.protocol.diagnose_project_runtime",
                return_value=diagnosis_result,
            ) as diagnose:
                diagnosis = session.handle_message({
                    "jsonrpc": "2.0",
                    "id": "runtime-diagnose",
                    "method": "runtime/diagnose",
                    "params": {
                        "receipt_uri": receipt_path.as_uri(),
                        "artifact_root_uris": [],
                    },
                })
            self.assertEqual(diagnosis, {
                "jsonrpc": "2.0",
                "id": "runtime-diagnose",
                "result": diagnosis_result,
            })
            diagnose.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                project.resolve(),
                launch_receipt=receipt_path,
                artifact_roots=[],
                configuration=ANY,
            )

            shutdown = session.handle_message({
                "jsonrpc": "2.0",
                "id": "shutdown",
                "method": "shutdown",
                "params": {},
            })
            self.assertEqual(shutdown, {
                "jsonrpc": "2.0",
                "id": "shutdown",
                "result": None,
            })
            self.assertTrue(session.shutdown_requested)
            self.assertTrue(any("initialized" in entry for entry in logs))

            schema = json.loads(
                (
                    MODULE_ROOT / "schemas/client-protocol-v2.schema.json"
                ).read_text(encoding="utf-8")
            )
            validator = Draft202012Validator(schema)
            validator.validate(initialize)
            validator.validate(inspect)
            validator.validate(runtime_plan)
            validator.validate(diagnosis)
            validator.validate(shutdown)
            runtime_plan_schema = json.loads(
                (
                    MODULE_ROOT / "schemas/runtime-plan-v1.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(runtime_plan_schema).validate(
                runtime_plan["result"]
            )

    def test_inspection_before_initialize_is_rejected(self) -> None:
        response = ProtocolSession(suite_root=REPOSITORY_ROOT).handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "workspace/inspect",
            "params": {},
        })
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["error"]["code"], -32002)

    def test_incompatible_major_and_required_method_fail(self) -> None:
        mismatch = ProtocolSession(suite_root=REPOSITORY_ROOT).handle_message(
            _initialize_request(major=3)
        )
        self.assertIsNotNone(mismatch)
        assert mismatch is not None
        self.assertEqual(mismatch["error"]["code"], -32001)

        available = ProtocolSession(
            suite_root=REPOSITORY_ROOT
        ).handle_message(
            _initialize_request(required_methods=["runtime/plan"])
        )
        self.assertIsNotNone(available)
        assert available is not None
        self.assertIn("result", available)

        unavailable = ProtocolSession(
            suite_root=REPOSITORY_ROOT
        ).handle_message(
            _initialize_request(required_methods=["runtime/provision"])
        )
        self.assertIsNotNone(unavailable)
        assert unavailable is not None
        self.assertEqual(unavailable["error"]["code"], -32004)
        self.assertEqual(
            unavailable["error"]["data"]["methods"],
            ["runtime/provision"],
        )

    def test_duplicate_initialize_and_unavailable_method_are_explicit(self) -> None:
        session = ProtocolSession(suite_root=REPOSITORY_ROOT)
        first = session.handle_message(_initialize_request())
        self.assertIn("result", first or {})

        duplicate = session.handle_message(_initialize_request())
        self.assertIsNotNone(duplicate)
        assert duplicate is not None
        self.assertEqual(duplicate["error"]["code"], -32003)

        runtime = session.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "runtime/provision",
            "params": {},
        })
        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertEqual(runtime["error"]["code"], -32601)

    def test_notifications_do_not_change_session_state(self) -> None:
        session = ProtocolSession(suite_root=REPOSITORY_ROOT)
        response = session.handle_message({
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": _initialize_request()["params"],
        })
        self.assertIsNone(response)
        self.assertEqual(session.state, "new")

    def test_required_empty_params_are_not_implicit(self) -> None:
        session = ProtocolSession(suite_root=REPOSITORY_ROOT)
        session.handle_message(_initialize_request())
        response = session.handle_message({
            "jsonrpc": "2.0",
            "id": "inspect",
            "method": "workspace/inspect",
        })
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response["error"]["code"], -32602)

    def test_active_instance_and_registration_methods_share_services(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = create_supersymmetry_project(root)
            instance = root / "instance"
            instance.mkdir()
            state = root / "state"
            session = ProtocolSession(
                suite_root=REPOSITORY_ROOT,
                state_root=state,
            )
            session.handle_message(_initialize_request(workspace=project))

            selected_result = {
                "format": "workbench-active-instance-result-v1",
                "schema_version": 1,
                "outcome": "selected",
                "selection": {"selection_id": "sha256:" + "1" * 64},
                "selection_uri": (state / "selection.json").as_uri(),
            }
            with patch(
                "workbench_shell.protocol.initialize_active_instance",
                return_value=selected_result,
            ) as select:
                selected = session.handle_message({
                    "jsonrpc": "2.0",
                    "id": "select",
                    "method": "instance/select",
                    "params": {"instance_uri": instance.as_uri()},
                })
            self.assertEqual(selected, {
                "jsonrpc": "2.0",
                "id": "select",
                "result": selected_result,
            })
            select.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                project.resolve(),
                instance,
                state_root=state.resolve(),
                configuration=ANY,
            )

            current_result = {
                "format": "workbench-active-instance-v1",
                "schema_version": 1,
                "selection_id": "sha256:" + "1" * 64,
                "state": "active",
                "selection_uri": (state / "selection.json").as_uri(),
                "instance_path": instance,
                "payload_path": instance / ".minecraft",
            }
            with patch(
                "workbench_shell.protocol.load_active_instance",
                return_value=current_result,
            ):
                current = session.handle_message({
                    "jsonrpc": "2.0",
                    "id": "current",
                    "method": "instance/current",
                    "params": {},
                })
            self.assertNotIn("instance_path", current["result"])
            self.assertNotIn("payload_path", current["result"])

            capabilities_result = {
                "format": "workbench-registration-capabilities-v1",
                "schema_version": 1,
                "catalog_id": "sha256:" + "2" * 64,
                "families": [],
                "patterns": [],
            }
            with patch(
                "workbench_shell.protocol.registration_capabilities",
                return_value=capabilities_result,
            ) as capabilities:
                listed = session.handle_message({
                    "jsonrpc": "2.0",
                    "id": "capabilities",
                    "method": "registration/capabilities",
                    "params": {"pattern": "ore-dictionary-entry"},
                })
            self.assertEqual(listed["result"], capabilities_result)
            capabilities.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                project.resolve(),
                pattern_key="ore-dictionary-entry",
                state_root=state.resolve(),
                configuration=ANY,
            )

            answers = {
                "ore_name": "dustProtocolProbe",
                "ingredient": {
                    "kind": "metaitem",
                    "name": "dustSodiumHydroxide",
                },
            }
            plan_result = {
                "format": "workbench-registration-plan-v1",
                "schema_version": 1,
                "plan_id": "sha256:" + "3" * 64,
                "state": "ready",
                "operations": [],
            }
            with patch(
                "workbench_shell.protocol.plan_active_registration",
                return_value=plan_result,
            ) as plan:
                planned = session.handle_message({
                    "jsonrpc": "2.0",
                    "id": "plan",
                    "method": "registration/plan",
                    "params": {
                        "pattern": "ore-dictionary-entry",
                        "answers": answers,
                    },
                })
            self.assertEqual(planned["result"], plan_result)
            plan.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                project.resolve(),
                pattern_key="ore-dictionary-entry",
                answers=answers,
                state_root=state.resolve(),
                configuration=ANY,
            )

            applied_result = {
                "format": "workbench-registration-result-v1",
                "schema_version": 1,
                "outcome": "applied",
                "plan": plan_result,
                "receipt": {},
            }
            with patch(
                "workbench_shell.protocol.apply_active_registration",
                return_value=applied_result,
            ) as apply:
                applied = session.handle_message({
                    "jsonrpc": "2.0",
                    "id": "apply",
                    "method": "registration/apply",
                    "params": {
                        "pattern": "ore-dictionary-entry",
                        "answers": answers,
                        "consent": {
                            "approved": True,
                            "plan_id": plan_result["plan_id"],
                            "operation_class": "local-mutation",
                        },
                    },
                })
            self.assertEqual(applied["result"], applied_result)
            apply.assert_called_once_with(
                REPOSITORY_ROOT.resolve(),
                project.resolve(),
                pattern_key="ore-dictionary-entry",
                answers=answers,
                expected_plan_id=plan_result["plan_id"],
                state_root=state.resolve(),
                configuration=ANY,
            )

            refused = session.handle_message({
                "jsonrpc": "2.0",
                "id": "refused",
                "method": "registration/apply",
                "params": {
                    "pattern": "ore-dictionary-entry",
                    "answers": answers,
                    "consent": {
                        "approved": False,
                        "plan_id": plan_result["plan_id"],
                        "operation_class": "local-mutation",
                    },
                },
            })
            self.assertEqual(refused["error"]["code"], -32602)
            self.assertEqual(
                refused["error"]["data"]["kind"],
                "consent_required",
            )


if __name__ == "__main__":
    unittest.main()
