"""Read-only join of admitted packages and one owner-validated fixture artifact.

This reviews exact retained evidence without resolving the share's remaining
fixture/tool marker or asserting a completed clean-root reconstruction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .environment_composition import _reopen
from .environment_fixture_artifact_admission import reopen_fixture_artifact_admission
from .environment_input_candidates import validate_input_candidate
from .environment_package_composition import (
    FORMAT as PACKAGE_PLAN_FORMAT, plan_environment_package_composition,
)
from .environment_reconstruction import ReconstructionError, _resource_host, _seal, validate_share
from .environment_resolution import resolve_environment


FORMAT = "workbench-environment-artifact-composition-plan-v1"


def _result_id(value: object, label: str) -> str:
    if type(value) is not str or not value.startswith("workbench-resource-v1:"):
        raise ReconstructionError(f"artifact composition has no exact {label} resource")
    return value


def plan_environment_artifact_composition(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], package_composition_plan: Mapping[str, Any],
    *, workspace_name: str, workspace: Path | str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    execution_result_resource_id: str, artifact_admission_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Reopen both evidence chains and report only their exact shared identity."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, dict(candidate))
    if type(package_composition_plan) is not dict or package_composition_plan.get("format") != PACKAGE_PLAN_FORMAT:
        raise ReconstructionError("artifact composition needs a reviewed package plan")
    resources = package_composition_plan.get("resources")
    inputs = package_composition_plan.get("input_resources")
    if (type(resources) is not dict or set(resources) != {"input_composition", "package_admission"}
            or type(inputs) is not dict or type(inputs.get("profile_fixture")) is not dict):
        raise ReconstructionError("package plan has no exact retained fixture result")
    try:
        input_id = _result_id(resources["input_composition"]["resource_id"], "input composition")
        package_id = _result_id(resources["package_admission"]["resource_id"], "package admission")
        fixture_id = _result_id(inputs["profile_fixture"]["resource_id"], "profile fixture")
    except (KeyError, TypeError) as exc:
        raise ReconstructionError("package plan has incomplete Core resource identities") from exc
    artifact_id = _result_id(artifact_admission_resource_id, "fixture artifact admission")
    execution_id = _result_id(execution_result_resource_id, "fixture execution")
    if len({input_id, package_id, fixture_id, artifact_id, execution_id}) != 5:
        raise ReconstructionError("package and fixture evidence resources must be distinct")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    current_package = plan_environment_package_composition(
        suite_root, portable, reviewed, closure_plan,
        workspace_name=workspace_name, workspace=local.workspace,
        input_composition_resource_id=input_id,
        package_admission_resource_id=package_id, environment=values,
    )
    if current_package != package_composition_plan:
        raise ReconstructionError("package composition changed before fixture artifact review")
    service = _resource_host(Path(suite_root), local.workspace, values)
    captured, artifact_sha = _reopen(
        service, artifact_id, "fixture artifact admission", portable["share_id"],
    )
    admitted = reopen_fixture_artifact_admission(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=fixture_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        execution_result_resource_id=execution_id,
        admission_resource_id=artifact_id, environment=values,
    )
    if type(admitted) is not dict:
        raise ReconstructionError("fixture artifact admission did not reopen as a Core result")
    resource = admitted.get("resource")
    if ({key: value for key, value in admitted.items() if key != "resource"} != captured
            or type(resource) is not dict or resource.get("resource_id") != artifact_id):
        raise ReconstructionError("fixture artifact admission changed during combined review")
    if (captured.get("state") != "owner-artifact-snapshot-admitted"
            or captured.get("share_id") != portable["share_id"]
            or captured.get("candidate_id") != reviewed["candidate_id"]
            or captured.get("workspace") != str(local.workspace)
            or captured.get("execution_result_resource_id") != execution_id
            or captured.get("unresolved_inputs") != portable["lock"]["unresolved_inputs"]
            or type(captured.get("artifact")) is not dict
            or type(captured.get("artifact_resource_id")) is not str
            or current_package.get("environment_resolution_id") != local.record["resolution_id"]):
        raise ReconstructionError("fixture artifact does not belong to the exact retained inputs")
    remaining = current_package["remaining_unresolved_inputs"]
    if (type(remaining) is not list or "profile-fixture-and-tool-bytes" not in remaining):
        raise ReconstructionError("artifact composition lost the unresolved fixture/tool marker")
    return _seal({
        "format": FORMAT, "schema_version": 1,
        "state": "reviewed",
        "coverage": "read-only-linked-package-and-fixture-artifact-evidence",
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "workspace_name": workspace_name, "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "closure_plan_id": current_package["closure_plan_id"],
        "package_composition_plan_id": current_package["plan_id"],
        "resources": {
            **current_package["resources"],
            "fixture_artifact_admission": {"resource_id": artifact_id, "sha256": artifact_sha},
        },
        "fixture_result_resource_id": fixture_id,
        "execution_result_resource_id": execution_id,
        "artifact_resource_id": captured["artifact_resource_id"],
        "artifact": captured["artifact"],
        "newly_evidenced": ["owner-validated-fixture-artifact-snapshot"],
        "remaining_unresolved_inputs": remaining,
        "scope": "Exact read-only evidence join only; selected pack source-lock authority, tool runtime materialization, detached descendant absence and clean-root reconstruction are not established.",
    }, "workbench-environment-artifact-composition-plan", "plan_id")


__all__ = ["plan_environment_artifact_composition"]
