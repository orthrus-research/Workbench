"""The combined review remains read-only and preserves fixture work."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch

from workbench_core.environment_package_composition import plan_environment_package_composition
from workbench_core.environment_reconstruction import (
    ReconstructionError, _canonical, _resource_host,
    apply_environment_input_composition, plan_environment_input_composition,
)

import test_environment_composition as composition_fixture


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL exact input composition")
class EnvironmentPackageCompositionTests(TestCase):
    setUp = composition_fixture.EnvironmentCompositionTests.setUp
    _git = composition_fixture.EnvironmentCompositionTests._git
    _attach_source_lock = composition_fixture.EnvironmentCompositionTests._attach_source_lock
    _plan = composition_fixture.EnvironmentCompositionTests._plan
    _apply = composition_fixture.EnvironmentCompositionTests._apply
    _acquired = composition_fixture.EnvironmentCompositionTests._acquired
    _arguments = composition_fixture.EnvironmentCompositionTests._arguments
    _five_inputs = composition_fixture.EnvironmentCompositionTests._five_inputs
    _ready_tools = composition_fixture.EnvironmentCompositionTests._ready_tools

    def _joined(self) -> tuple[dict, dict, dict, dict]:
        candidate, arguments = self._five_inputs()
        input_plan = plan_environment_input_composition(
            self.target_suite, self.share, candidate, **arguments,
        )
        inputs = apply_environment_input_composition(
            self.target_suite, self.share, candidate,
            expected_plan_id=input_plan["plan_id"], **arguments,
        )
        closure = {
            "plan_id": "closure-reviewed-in-this-test",
            "wheel_resource_id": arguments["wheel_resource_id"],
            "unresolved_inputs": list(self.share["lock"]["unresolved_inputs"]),
        }
        captured = {
            "state": "installed-packages-admitted",
            "share_id": self.share["share_id"], "candidate_id": candidate["candidate_id"],
            "closure_plan_id": closure["plan_id"],
            "package_result_resource_id": arguments["project_resource_id"],
            "install_result_resource_id": arguments["tool_resource_id"],
            "resolved_inputs": ["optional-module-packages"],
            "remaining_unresolved_inputs": [
                item for item in closure["unresolved_inputs"]
                if item != "optional-module-packages"
            ],
        }
        service = _resource_host(self.target_suite, self.target_workspace, self.target_environment)
        ref = service.publish_bytes(
            "evidence", "environment-package-admission.json", _canonical(captured) + b"\n",
            domain_id=self.share["share_id"],
            references=(arguments["project_resource_id"], arguments["tool_resource_id"]),
        )
        admitted = {**captured, "resource": {"resource_id": ref.resource_id}}
        started = patch(
            "workbench_core.environment_package_composition.reopen_package_admission",
            return_value=admitted,
        )
        started.start()
        self.addCleanup(started.stop)
        call = {
            "workspace_name": "shared", "workspace": self.target_workspace,
            "input_composition_resource_id": inputs["resource"]["resource_id"],
            "package_admission_resource_id": ref.resource_id,
            "environment": self.target_environment,
        }
        return candidate, closure, call, admitted

    def test_join_reopens_both_results_and_keeps_fixture_unresolved(self) -> None:
        candidate, closure, call, admitted = self._joined()
        plan = plan_environment_package_composition(
            self.target_suite, self.share, candidate, closure, **call,
        )
        self.assertEqual("reviewed", plan["state"])
        self.assertEqual(["optional-module-packages"], plan["newly_resolved_inputs"])
        self.assertNotIn("optional-module-packages", plan["remaining_unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", plan["remaining_unresolved_inputs"])
        self.assertEqual(admitted["install_result_resource_id"], plan["install_result_resource_id"])
        self.assertEqual({"input_composition", "package_admission"}, set(plan["resources"]))
        self.assertEqual(plan["plan_id"], plan_environment_package_composition(
            self.target_suite, self.share, candidate, closure, **call,
        )["plan_id"])

    def test_wrong_wheel_binding_and_missing_package_marker_refuse(self) -> None:
        candidate, closure, call, _ = self._joined()
        wrong = {**closure, "wheel_resource_id": call["package_admission_resource_id"]}
        with self.assertRaisesRegex(ReconstructionError, "exact retained inputs"):
            plan_environment_package_composition(
                self.target_suite, self.share, candidate, wrong, **call,
            )
        wrong = {**closure, "unresolved_inputs": [
            item for item in closure["unresolved_inputs"] if item != "optional-module-packages"
        ]}
        with self.assertRaisesRegex(ReconstructionError, "exact retained inputs"):
            plan_environment_package_composition(
                self.target_suite, self.share, candidate, wrong, **call,
            )

    def test_changed_admission_readback_and_duplicate_resource_refuse(self) -> None:
        candidate, closure, call, admitted = self._joined()
        with patch(
            "workbench_core.environment_package_composition.reopen_package_admission",
            return_value={**admitted, "resolved_inputs": []},
        ):
            with self.assertRaisesRegex(ReconstructionError, "changed during package review"):
                plan_environment_package_composition(
                    self.target_suite, self.share, candidate, closure, **call,
                )
        wrong = deepcopy(call)
        wrong["package_admission_resource_id"] = wrong["input_composition_resource_id"]
        with self.assertRaisesRegex(ReconstructionError, "distinct Core result"):
            plan_environment_package_composition(
                self.target_suite, self.share, candidate, closure, **wrong,
            )

    def test_changed_retained_fixture_refuses_combined_review(self) -> None:
        candidate, closure, call, _ = self._joined()
        fixture = self.five_fixture_result
        source = next(row for row in fixture["files"] if row["kind"] == "fixture-source")
        path = Path(fixture["tree_path"]) / source["relative_path"]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(ReconstructionError):
            plan_environment_package_composition(
                self.target_suite, self.share, candidate, closure, **call,
            )


if __name__ == "__main__":
    import unittest
    unittest.main()
