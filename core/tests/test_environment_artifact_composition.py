"""The package and fixture artifact evidence join stays reviewed and read-only."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch

from workbench_core.environment_artifact_composition import plan_environment_artifact_composition
from workbench_core.environment_package_composition import plan_environment_package_composition
from workbench_core.environment_reconstruction import ReconstructionError, _canonical, _resource_host

import test_environment_composition as composition_fixture
import test_environment_package_composition as package_fixture


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL exact input composition")
class EnvironmentArtifactCompositionTests(TestCase):
    setUp = composition_fixture.EnvironmentCompositionTests.setUp
    _git = composition_fixture.EnvironmentCompositionTests._git
    _attach_source_lock = composition_fixture.EnvironmentCompositionTests._attach_source_lock
    _plan = composition_fixture.EnvironmentCompositionTests._plan
    _apply = composition_fixture.EnvironmentCompositionTests._apply
    _acquired = composition_fixture.EnvironmentCompositionTests._acquired
    _arguments = composition_fixture.EnvironmentCompositionTests._arguments
    _five_inputs = composition_fixture.EnvironmentCompositionTests._five_inputs
    _ready_tools = composition_fixture.EnvironmentCompositionTests._ready_tools
    _joined = package_fixture.EnvironmentPackageCompositionTests._joined

    def _artifact(self):
        candidate, closure, package_call, _ = self._joined()
        package_plan = plan_environment_package_composition(
            self.target_suite, self.share, candidate, closure, **package_call,
        )
        service = _resource_host(self.target_suite, self.target_workspace, self.target_environment)
        fixture_id = package_plan["input_resources"]["profile_fixture"]["resource_id"]
        execution_ref = service.publish_bytes(
            "evidence", "synthetic-fixture-execution.json", b"{}\n",
            domain_id=self.share["share_id"],
        )
        jar_ref = service.publish_bytes(
            "artifacts", "synthetic-fixture.jar", b"synthetic-only",
            domain_id=self.share["share_id"], references=(execution_ref.resource_id,),
        )
        captured = {
            "format": "workbench-environment-fixture-artifact-admission-v1",
            "schema_version": 1, "state": "owner-artifact-snapshot-admitted",
            "share_id": self.share["share_id"], "candidate_id": candidate["candidate_id"],
            "workspace": str(self.target_workspace),
            "execution_result_resource_id": execution_ref.resource_id,
            "artifact_resource_id": jar_ref.resource_id,
            "artifact": {"sha256": "sha256:" + "a" * 64, "size": len(b"synthetic-only")},
            "unresolved_inputs": list(self.share["lock"]["unresolved_inputs"]),
        }
        admission_ref = service.publish_bytes(
            "evidence", "synthetic-fixture-artifact-admission.json",
            _canonical(captured) + b"\n", domain_id=self.share["share_id"],
            references=(fixture_id, execution_ref.resource_id, jar_ref.resource_id),
        )
        admitted = {**captured, "resource": {"resource_id": admission_ref.resource_id}}
        started = patch(
            "workbench_core.environment_artifact_composition.reopen_fixture_artifact_admission",
            return_value=admitted,
        )
        mocked = started.start()
        self.addCleanup(started.stop)
        call = {
            "workspace_name": "shared", "workspace": self.target_workspace,
            "expected_review_id": "reviewed-policy-in-test",
            "projection_result_resource_id": package_plan["input_resources"]["project"]["resource_id"],
            "binding_result_resource_id": package_plan["input_resources"]["selection"]["resource_id"],
            "preflight_result_resource_id": package_plan["input_resources"]["managed_tools"]["resource_id"],
            "execution_result_resource_id": execution_ref.resource_id,
            "artifact_admission_resource_id": admission_ref.resource_id,
            "environment": self.target_environment,
        }
        return candidate, closure, package_plan, call, captured, mocked

    def test_join_reopens_exact_artifact_and_preserves_every_remaining_marker(self) -> None:
        candidate, closure, package_plan, call, captured, mocked = self._artifact()
        plan = plan_environment_artifact_composition(
            self.target_suite, self.share, candidate, closure, package_plan, **call,
        )
        self.assertEqual("reviewed", plan["state"])
        self.assertEqual(package_plan["remaining_unresolved_inputs"], plan["remaining_unresolved_inputs"])
        self.assertIn("profile-fixture-and-tool-bytes", plan["remaining_unresolved_inputs"])
        self.assertEqual(package_plan["plan_id"], plan["package_composition_plan_id"])
        self.assertEqual(captured["artifact_resource_id"], plan["artifact_resource_id"])
        self.assertEqual(
            package_plan["input_resources"]["profile_fixture"]["resource_id"],
            mocked.call_args.kwargs["fixture_result_resource_id"],
        )
        self.assertEqual(plan["plan_id"], plan_environment_artifact_composition(
            self.target_suite, self.share, candidate, closure, package_plan, **call,
        )["plan_id"])

    def test_stale_package_plan_and_wrong_fixture_binding_refuse(self) -> None:
        candidate, closure, package_plan, call, _captured, _mocked = self._artifact()
        stale = {**package_plan, "plan_id": "another-review"}
        with self.assertRaisesRegex(ReconstructionError, "changed before fixture"):
            plan_environment_artifact_composition(
                self.target_suite, self.share, candidate, closure, stale, **call,
            )
        wrong = deepcopy(package_plan)
        wrong["input_resources"]["profile_fixture"]["resource_id"] = call[
            "artifact_admission_resource_id"
        ]
        with self.assertRaises(ReconstructionError):
            plan_environment_artifact_composition(
                self.target_suite, self.share, candidate, closure, wrong, **call,
            )

    def test_changed_artifact_readback_and_other_candidate_refuse(self) -> None:
        candidate, closure, package_plan, call, captured, mocked = self._artifact()
        mocked.return_value = {
            **captured, "artifact": {"sha256": "sha256:" + "b" * 64},
            "resource": {"resource_id": call["artifact_admission_resource_id"]},
        }
        with self.assertRaisesRegex(ReconstructionError, "changed during combined review"):
            plan_environment_artifact_composition(
                self.target_suite, self.share, candidate, closure, package_plan, **call,
            )
        wrong = {**captured, "candidate_id": "another-candidate"}
        service = _resource_host(self.target_suite, self.target_workspace, self.target_environment)
        wrong_ref = service.publish_bytes(
            "evidence", "synthetic-other-candidate-artifact.json",
            _canonical(wrong) + b"\n", domain_id=self.share["share_id"],
        )
        mocked.return_value = {
            **wrong, "resource": {"resource_id": wrong_ref.resource_id},
        }
        with self.assertRaisesRegex(ReconstructionError, "exact retained inputs"):
            plan_environment_artifact_composition(
                self.target_suite, self.share, candidate, closure, package_plan,
                **{**call, "artifact_admission_resource_id": wrong_ref.resource_id},
            )

    def test_duplicate_core_resource_and_changed_fixture_refuse(self) -> None:
        candidate, closure, package_plan, call, _captured, _mocked = self._artifact()
        duplicate = {**call, "artifact_admission_resource_id": package_plan["resources"]["package_admission"]["resource_id"]}
        with self.assertRaisesRegex(ReconstructionError, "must be distinct"):
            plan_environment_artifact_composition(
                self.target_suite, self.share, candidate, closure, package_plan, **duplicate,
            )
        fixture = self.five_fixture_result
        source = next(row for row in fixture["files"] if row["kind"] == "fixture-source")
        path = Path(fixture["tree_path"]) / source["relative_path"]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(ReconstructionError):
            plan_environment_artifact_composition(
                self.target_suite, self.share, candidate, closure, package_plan, **call,
            )


if __name__ == "__main__":
    import unittest
    unittest.main()
