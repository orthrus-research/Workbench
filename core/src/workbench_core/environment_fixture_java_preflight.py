"""Retain a Cleanroom-owned Java 25 preflight for one fixture binding.

The owner inspects only the elected path. Core verifies its installed code
identity and retains the answer; a later fixture execution must preflight
again under process custody. This operation never launches Java or Gradle.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from workbench_api.profile_extensions import (
    ProfileExtensionError, profile_extension_identity,
    require_profile_extension,
)

from .environment_fixture_java_binding import (
    RESULT_FORMAT as BINDING_FORMAT, reopen_fixture_java_binding,
)
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _resource_host, _seal,
    validate_share,
)
from .environment_resolution import resolve_environment
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-fixture-java-preflight-plan-v1"
RESULT_FORMAT = "workbench-environment-fixture-java-preflight-result-v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PREFLIGHT_FORMAT = "workbench-cleanroom-fixture-java25-preflight-v1"
_SCOPE = (
    "Cleanroom owner Java 25 path preflight only; retained source projection, "
    "Gradle execution, artifact admission and reconstruction remain separate."
)
_REMAINING = [
    "retained-fixture-source-projection", "profile-fixture-execution",
    "fixture-artifact-admission", "combined-environment-reconstruction",
]


def _binding(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, binding_result_resource_id: str,
    environment: Mapping[str, str],
) -> tuple[dict[str, Any], str]:
    local = resolve_environment(suite_root, workspace=workspace, environment=environment)
    service = _resource_host(Path(suite_root), local.workspace, environment)
    try:
        reference = service.describe(binding_result_resource_id)
        payload = service.read_bytes(binding_result_resource_id)
        selected = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"fixture Java binding cannot be read: {exc}") from exc
    if (reference.owner_id != "workbench-core" or reference.role != "evidence"
            or reference.domain_id != share["share_id"]
            or type(selected) is not dict or payload != _canonical(selected) + b"\n"
            or selected.get("format") != BINDING_FORMAT):
        raise ReconstructionError("fixture Java binding has another Core scope")
    try:
        result = reopen_fixture_java_binding(
            suite_root, share, candidate,
            workspace_name=selected["workspace_name"], workspace=local.workspace,
            selection_result_resource_id=selected["selection_result_resource_id"],
            fixture_result_resource_id=selected["fixture_result_resource_id"],
            expected_review_id=selected["fixture_policy_review_id"],
            gradle_result_resource_id=selected["gradle_result_resource_id"],
            extraction_result_resource_id=selected["extraction_result_resource_id"],
            result_resource_id=binding_result_resource_id, environment=environment,
        )
    except (KeyError, TypeError) as exc:
        raise ReconstructionError("fixture Java binding is incomplete") from exc
    return result, "sha256:" + sha256(payload).hexdigest()


def _owner_java25(
    candidate: Mapping[str, Any], binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture = candidate["profile_fixture"]
    if fixture["owner_profile_id"] != "cleanroom":
        raise ReconstructionError("portable Java 25 preflight requires the Cleanroom owner")
    try:
        identity = profile_extension_identity("workbench.workspace_home_fixtures", "cleanroom")
        if identity != fixture["owner_code"]:
            raise ReconstructionError("installed Cleanroom owner code differs from the candidate")
        owner = require_profile_extension("workbench.workspace_home_fixtures", "cleanroom")
        inspect = getattr(owner, "inspect_portable_java_home")
        if not callable(inspect):
            raise ReconstructionError("Cleanroom owner has no portable Java 25 preflight")
        observed = inspect(java_home=Path(binding["java"]["java_home"]))
    except (ProfileExtensionError, OSError, ValueError, RuntimeError,
            AttributeError, KeyError, TypeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"Cleanroom Java 25 owner preflight failed: {exc}") from exc
    home = binding["java"]["java_home"]
    if (type(observed) is not dict or set(observed) != {
            "format", "schema_version", "feature_version", "runtime_version",
            "requested_home", "resolved_home", "release_sha256", "release_size",
            "executable_sha256", "executable_size",
        }
            or observed["format"] != _PREFLIGHT_FORMAT
            or observed["schema_version"] != 1
            or observed["feature_version"] != binding["java_required_major"]
            or observed["feature_version"] != 25
            or type(observed["runtime_version"]) is not str
            or observed["runtime_version"].split(".", 1)[0] != "25"
            or observed["requested_home"] != home
            or type(observed["resolved_home"]) is not str
            or not Path(observed["resolved_home"]).is_absolute()
            or any(type(observed[key]) is not str or _DIGEST.fullmatch(observed[key]) is None
                   for key in ("release_sha256", "executable_sha256"))
            or any(type(observed[key]) is not int or not 0 < observed[key] <= 2 * 1024 * 1024
                   for key in ("release_size", "executable_size"))):
        raise ReconstructionError("Cleanroom owner returned another Java 25 preflight")
    return identity, observed


def plan_fixture_java_preflight(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, binding_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read-only owner review of the one Java path already bound by Core."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("fixture Java preflight requires a V3 environment share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    binding, binding_sha = _binding(
        suite_root, portable, reviewed, workspace=local.workspace,
        binding_result_resource_id=binding_result_resource_id, environment=values,
    )
    owner_code, observed = _owner_java25(reviewed, binding)
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "binding_result_resource_id": binding_result_resource_id,
        "binding_result_sha256": binding_sha,
        "fixture_policy_review_id": binding["fixture_policy_review_id"],
        "gradle_tree_id": binding["gradle_tree_id"],
        "gradle_tree_content_sha256": binding["gradle_tree_content_sha256"],
        "owner_code": owner_code, "java": binding["java"],
        "owner_java25": observed,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "remaining_prerequisites": list(_REMAINING),
        "state": "review-only-unqualified",
    }, "workbench-environment-fixture-java-preflight-plan", "plan_id")


def _result(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT, "schema_version": 1,
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "candidate_id": plan["candidate_id"], "workspace": plan["workspace"],
        "environment_resolution_id": plan["environment_resolution_id"],
        "binding_result_resource_id": plan["binding_result_resource_id"],
        "binding_result_sha256": plan["binding_result_sha256"],
        "fixture_policy_review_id": plan["fixture_policy_review_id"],
        "gradle_tree_id": plan["gradle_tree_id"],
        "gradle_tree_content_sha256": plan["gradle_tree_content_sha256"],
        "owner_code": plan["owner_code"], "java": plan["java"],
        "owner_java25": plan["owner_java25"],
        "unresolved_inputs": plan["unresolved_inputs"],
        "remaining_prerequisites": plan["remaining_prerequisites"],
        "state": plan["state"], "scope": _SCOPE,
    }


def apply_fixture_java_preflight(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, binding_result_resource_id: str, expected_plan_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain the profile owner's reviewed answer under Core evidence custody."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_java_preflight(
        suite_root, share, candidate, workspace=workspace,
        binding_result_resource_id=binding_result_resource_id, environment=values,
    )
    if type(expected_plan_id) is not str or plan["plan_id"] != expected_plan_id:
        raise ReconstructionError("fixture Java 25 preflight changed after review")
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("fixture Java 25 preflight resolution changed")
    result = _result(plan)
    payload = _canonical(result) + b"\n"
    reference = service.publish_bytes(
        "evidence", "environment-fixture-java25-preflight.json", payload,
        domain_id=plan["share_id"], references=(binding_result_resource_id,),
    )
    if service.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("fixture Java 25 preflight did not reopen exactly")
    return {**result, "resource": {
        "resource_id": reference.resource_id, "store_id": reference.store_id,
        "path": str(reference.path), "sha256": reference.sha256,
    }}


def reopen_fixture_java_preflight(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, binding_result_resource_id: str,
    result_resource_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Repeat installed-owner and selected-path review after restart."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_java_preflight(
        suite_root, share, candidate, workspace=workspace,
        binding_result_resource_id=binding_result_resource_id, environment=values,
    )
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    try:
        reference = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        result = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError) as exc:
        raise ReconstructionError(f"fixture Java 25 preflight cannot reopen: {exc}") from exc
    if (reference.owner_id != "workbench-core" or reference.role != "evidence"
            or reference.domain_id != plan["share_id"]
            or type(result) is not dict or payload != _canonical(result) + b"\n"
            or result != _result(plan)):
        raise ReconstructionError("fixture Java 25 preflight result has another identity or scope")
    return result


__all__ = [
    "plan_fixture_java_preflight", "apply_fixture_java_preflight",
    "reopen_fixture_java_preflight",
]
