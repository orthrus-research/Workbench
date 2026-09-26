"""Read-only join of V3 retained inputs and admitted installed packages."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .environment_composition import _reopen, reopen_environment_input_composition
from .environment_input_candidates import validate_input_candidate
from .environment_package_admission import reopen_package_admission
from .environment_reconstruction import ReconstructionError, _resource_host, _seal, validate_share
from .environment_resolution import resolve_environment


FORMAT = "workbench-environment-package-composition-plan-v1"
_PACKAGE_MARKER = "optional-module-packages"


def plan_environment_package_composition(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace_name: str,
    workspace: Path | str, input_composition_resource_id: str,
    package_admission_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Reopen both exact results and review the remaining reconstruction work."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, dict(candidate))
    if (type(input_composition_resource_id) is not str
            or type(package_admission_resource_id) is not str
            or input_composition_resource_id == package_admission_resource_id):
        raise ReconstructionError("package composition needs distinct Core result resources")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    inputs = reopen_environment_input_composition(
        suite_root, portable, reviewed, workspace_name=workspace_name,
        workspace=local.workspace, result_resource_id=input_composition_resource_id,
        environment=values,
    )
    input_payload, input_sha = _reopen(
        service, input_composition_resource_id, "input composition", portable["share_id"],
    )
    if input_payload != inputs:
        raise ReconstructionError("input composition changed during package review")
    captured, admission_sha = _reopen(
        service, package_admission_resource_id, "package admission", portable["share_id"],
    )
    package_result_id = captured.get("package_result_resource_id")
    install_result_id = captured.get("install_result_resource_id")
    if (type(package_result_id) is not str or type(install_result_id) is not str
            or len({input_composition_resource_id, package_admission_resource_id,
                    package_result_id, install_result_id}) != 4):
        raise ReconstructionError("package admission has ambiguous Core resource identities")
    admitted = reopen_package_admission(
        suite_root, portable, reviewed, closure_plan, workspace=local.workspace,
        package_result_resource_id=package_result_id,
        install_result_resource_id=install_result_id,
        admission_resource_id=package_admission_resource_id, environment=values,
    )
    resource = admitted.get("resource")
    if ({key: value for key, value in admitted.items() if key != "resource"} != captured
            or type(resource) is not dict
            or resource.get("resource_id") != package_admission_resource_id):
        raise ReconstructionError("package admission changed during package review")
    unresolved = inputs.get("unresolved_inputs")
    closure_unresolved = closure_plan.get("unresolved_inputs")
    if (inputs.get("share_id") != portable["share_id"]
            or inputs.get("candidate_id") != reviewed["candidate_id"]
            or inputs.get("workspace") != str(local.workspace)
            or inputs.get("environment_resolution_id") != local.record["resolution_id"]
            or admitted.get("state") != "installed-packages-admitted"
            or admitted.get("share_id") != portable["share_id"]
            or admitted.get("candidate_id") != reviewed["candidate_id"]
            or admitted.get("closure_plan_id") != closure_plan.get("plan_id")
            or admitted.get("package_result_resource_id") != package_result_id
            or type(unresolved) is not list or _PACKAGE_MARKER not in unresolved
            or type(closure_unresolved) is not list or _PACKAGE_MARKER not in closure_unresolved
            or admitted.get("resolved_inputs") != [_PACKAGE_MARKER]
            or admitted.get("remaining_unresolved_inputs") != [
                item for item in closure_unresolved if item != _PACKAGE_MARKER
            ]
            or closure_plan.get("wheel_resource_id") != inputs.get("resources", {}).get(
                "optional_wheels", {},
            ).get("resource_id")):
        raise ReconstructionError("admitted packages do not belong to the exact retained inputs")
    remaining = [item for item in unresolved if item != _PACKAGE_MARKER]
    if not remaining or "profile-fixture-and-tool-bytes" not in remaining:
        raise ReconstructionError("package composition lost an unresolved profile fixture input")
    return _seal({
        "format": FORMAT, "schema_version": 1,
        "state": "reviewed", "coverage": "read-only-linked-input-and-package-evidence",
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "closure_plan_id": closure_plan["plan_id"],
        "workspace_name": workspace_name, "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "java": inputs["java"],
        "resources": {
            "input_composition": {"resource_id": input_composition_resource_id, "sha256": input_sha},
            "package_admission": {"resource_id": package_admission_resource_id, "sha256": admission_sha},
        },
        "input_resources": inputs["resources"],
        "package_result_resource_id": package_result_id,
        "install_result_resource_id": install_result_id,
        "newly_resolved_inputs": [_PACKAGE_MARKER],
        "remaining_unresolved_inputs": remaining,
        "scope": "Read-only link of five acquired inputs and installed package admission; profile fixture execution, tool runtime materialization and clean-root rebuild remain unresolved.",
    }, "workbench-environment-package-composition-plan", "plan_id")


__all__ = ["plan_environment_package_composition"]
