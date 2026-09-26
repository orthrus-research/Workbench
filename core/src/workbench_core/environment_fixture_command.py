"""Read-only owner command plan over retained Cleanroom fixture inputs.

Core reopens the private projection, exact Gradle tree and elected Java 25
preflight, then asks the installed profile owner to compose the command. The
plan is not a process attempt and grants no execution or artifact admission.
"""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any, Mapping

from workbench_api.profile_extensions import (
    ProfileExtensionError, profile_extension_identity, require_profile_extension,
)

from .environment_fixture_execution_policy import reopen_fixture_execution_policy
from .environment_fixture_import import reopen_fixture_import
from .environment_fixture_java_preflight import _binding, reopen_fixture_java_preflight
from .environment_fixture_projection import reopen_fixture_projection
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _seal, validate_share,
)
from .environment_resolution import resolve_environment


FORMAT = "workbench-environment-fixture-command-plan-v1"


def plan_fixture_command(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review exact owner argv and environment without starting a process."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("fixture command requires a V3 share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    review = reopen_fixture_execution_policy(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, environment=values,
    )
    imported = reopen_fixture_import(
        suite_root, portable, reviewed, workspace=local.workspace,
        result_resource_id=fixture_result_resource_id, environment=values,
    )
    projection = reopen_fixture_projection(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        result_resource_id=projection_result_resource_id, environment=values,
    )
    binding, binding_sha = _binding(
        suite_root, portable, reviewed, workspace=local.workspace,
        binding_result_resource_id=binding_result_resource_id, environment=values,
    )
    preflight = reopen_fixture_java_preflight(
        suite_root, portable, reviewed, workspace=local.workspace,
        binding_result_resource_id=binding_result_resource_id,
        result_resource_id=preflight_result_resource_id, environment=values,
    )
    if (binding["fixture_result_resource_id"] != fixture_result_resource_id
            or binding["fixture_policy_review_id"] != expected_review_id
            or preflight["binding_result_sha256"] != binding_sha
            or projection["fixture_tree_id"] != imported["tree_id"]):
        raise ReconstructionError("fixture command inputs have another retained binding")
    policy = review["policy"]
    cleanup = Path(imported["tree_path"]) / policy["cleanup_init"]["relative_path"]
    try:
        identity = profile_extension_identity("workbench.workspace_home_fixtures", "cleanroom")
        if identity != reviewed["profile_fixture"]["owner_code"]:
            raise ReconstructionError("installed Cleanroom owner code differs from the candidate")
        owner = require_profile_extension("workbench.workspace_home_fixtures", "cleanroom")
        command = owner.build_portable_command(
            policy=policy, project=Path(projection["project"]),
            gradle_cmd=Path(binding["gradle_launcher_path"]),
            java_home=Path(binding["java"]["java_home"]),
            cleanup_init=cleanup, state_root=local.state_root,
        )
    except (ProfileExtensionError, OSError, ValueError, RuntimeError,
            AttributeError, KeyError, TypeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"Cleanroom owner cannot compose retained command: {exc}") from exc
    paths = policy["paths"]
    fields = {
        "gradle_bin": binding["gradle_launcher_path"],
        "project": projection["project"],
        "project_cache": str(local.state_root / paths["project_cache_relative_to_state"]),
        "cleanup_init": str(cleanup),
        "java_home": preflight["owner_java25"]["resolved_home"],
        "gradle_home": str(local.state_root / paths["gradle_home_relative_to_state"]),
    }
    expected = {
        "argv": [value.format_map(fields) for value in policy["argv_template"]],
        "cwd": projection["project"],
        "environment_overrides": {
            name: value.format_map(fields)
            for name, value in policy["environment"].items()
        },
        "capture": policy["capture"], "restart": policy["restart"],
    }
    if command != expected:
        raise ReconstructionError("Cleanroom owner command differs from retained policy")
    if reopen_fixture_java_preflight(
        suite_root, portable, reviewed, workspace=local.workspace,
        binding_result_resource_id=binding_result_resource_id,
        result_resource_id=preflight_result_resource_id, environment=values,
    ) != preflight:
        raise ReconstructionError("Java 25 changed during command review")
    if reopen_fixture_projection(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        result_resource_id=projection_result_resource_id, environment=values,
    ) != projection:
        raise ReconstructionError("fixture source changed during command review")
    return _seal({
        "format": FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_policy_review_id": expected_review_id,
        "projection_result_resource_id": projection_result_resource_id,
        "projection_id": projection["projection_id"],
        "binding_result_resource_id": binding_result_resource_id,
        "preflight_result_resource_id": preflight_result_resource_id,
        "gradle_tree_id": binding["gradle_tree_id"],
        "owner_code": identity, "command": command,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "remaining_prerequisites": [
            "prepared-supervised-gradle-execution", "fixture-artifact-admission",
            "combined-environment-reconstruction",
        ],
        "state": "review-only-unqualified",
    }, "workbench-environment-fixture-command-plan", "plan_id")


__all__ = ["plan_fixture_command"]
