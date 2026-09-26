"""Bind the selected Java source to a retained Cleanroom fixture policy.

This is an evidence-only Core result. A user-supplied path stays lexical and
unchecked here. The Cleanroom owner must preflight Java 25 before execution.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .configuration import WorkbenchConfigurationError, load_workbench_configuration
from .environment_composition import _reopen, _selection
from .environment_fixture_execution_policy import reopen_fixture_execution_policy
from .environment_fixture_gradle_extract import reopen_fixture_gradle_extraction
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _resource_host, _seal,
    validate_share,
)
from .environment_resolution import resolve_environment
from .runtime_java import (
    JavaRuntimeError, _file_uri_path, host_platform,
    inspect_managed_java_runtime, load_java_runtime_policy,
    select_managed_java_policy,
)
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-fixture-java-binding-plan-v1"
RESULT_FORMAT = "workbench-environment-fixture-java-binding-result-v1"
_SCOPE = (
    "Selected Java source only; an explicit user path is unchecked and the "
    "Cleanroom owner Java 25 preflight, fixture execution, artifact admission "
    "and reconstruction remain separate."
)
_REMAINING = [
    "cleanroom-owner-java25-preflight", "profile-fixture-execution",
    "fixture-artifact-admission", "combined-environment-reconstruction",
]


def _java_source(
    suite_root: Path, share: Mapping[str, Any], selection: Mapping[str, Any],
    reviewed: Mapping[str, Any], *, state_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    mode = share["intent"]["java"]["mode"]
    acquired = selection.get("managed_java")
    if mode == "local-binding-required":
        home = reviewed["java_home"]
        if (acquired is not None or type(home) is not str
                or not Path(home).is_absolute()):
            raise ReconstructionError("local Java selection has another binding")
        # Do not stat, resolve, inventory or launch this user-supplied path.
        return ({
            "kind": "user-path", "state": "owner-java25-preflight-required",
            "java_home": home,
        }, [])
    if reviewed["java_home"] is not None:
        raise ReconstructionError("managed Java selection gained a user path")
    try:
        configuration = load_workbench_configuration(
            suite_root, Path(selection["configuration_manifest"]["path"]),
        )
        policy = select_managed_java_policy(
            load_java_runtime_policy(suite_root, configuration=configuration),
            share["intent"]["java"]["feature_version"],
        )
    except (WorkbenchConfigurationError, JavaRuntimeError, OSError, KeyError,
            TypeError) as exc:
        raise ReconstructionError(f"selected managed Java policy cannot be read: {exc}") from exc
    lock = share["lock"]["java_policy"]
    if (policy["policy_sha256"] != lock["selected_policy_sha256"]
            or policy["feature_version"] != lock["feature_version"]):
        raise ReconstructionError("selected managed Java policy differs from the V3 share")
    if policy["feature_version"] != 25:
        raise ReconstructionError("Cleanroom fixture requires Java 25; this selection is another Java version")
    if acquired is None:
        return ({
            "kind": "managed", "state": "managed-java25-unacquired",
            "policy_sha256": policy["policy_sha256"],
        }, ["selected managed Java 25 has no retained acquisition"])
    if type(acquired) is not dict:
        raise ReconstructionError("selected managed Java acquisition has another shape")
    try:
        current = inspect_managed_java_runtime(
            policy, host_platform(), state_root=state_root,
        )
    except (JavaRuntimeError, OSError, ValueError) as exc:
        raise ReconstructionError(f"selected managed Java 25 changed: {exc}") from exc
    receipt = current.get("receipt") if type(current) is dict else None
    target = receipt.get("target") if type(receipt) is dict else None
    if (type(receipt) is not dict or current.get("source") != "managed"
            or type(target) is not dict
            or receipt.get("runtime_id") != acquired.get("runtime_id")
            or receipt.get("policy") != policy
            or target.get("receipt_uri") != acquired.get("receipt_uri")
            or acquired.get("policy_sha256") != policy["policy_sha256"]):
        raise ReconstructionError("selected managed Java 25 differs from its retained acquisition")
    try:
        java_home = _file_uri_path(
            target["java_home_uri"], "selected managed Java execution home",
        )
    except (JavaRuntimeError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"selected managed Java 25 has no execution home: {exc}") from exc
    return ({
        "kind": "managed", "state": "verified-managed-java25",
        "java_home": str(java_home), "runtime_id": receipt["runtime_id"],
        "receipt_uri": target["receipt_uri"],
        "policy_sha256": policy["policy_sha256"],
    }, [])


def plan_fixture_java_binding(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace_name: str, workspace: Path | str,
    selection_result_resource_id: str, fixture_result_resource_id: str,
    expected_review_id: str, gradle_result_resource_id: str,
    extraction_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review exact selection and tool evidence without touching a user JDK."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("fixture Java binding requires a V3 environment share")
    reviewed_candidate = validate_input_candidate(portable, deepcopy(dict(candidate)))
    identifiers = (
        selection_result_resource_id, fixture_result_resource_id,
        gradle_result_resource_id, extraction_result_resource_id,
    )
    if any(type(value) is not str for value in identifiers) or len(set(identifiers)) != 4:
        raise ReconstructionError("fixture Java binding needs four distinct Core result resources")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    selection, selection_sha = _reopen(
        service, selection_result_resource_id, "selection", portable["share_id"],
    )
    selected = _selection(
        Path(suite_root), portable, selection,
        workspace_name=workspace_name, workspace=local.workspace, environment=values,
    )
    review = reopen_fixture_execution_policy(
        suite_root, portable, reviewed_candidate, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, environment=values,
    )
    extracted = reopen_fixture_gradle_extraction(
        suite_root, portable, reviewed_candidate, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        gradle_result_resource_id=gradle_result_resource_id,
        result_resource_id=extraction_result_resource_id, environment=values,
    )
    source, blockers = _java_source(
        Path(suite_root), portable, selection, selected, state_root=local.state_root,
    )
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed_candidate["candidate_id"],
        "workspace_name": workspace_name, "workspace": str(local.workspace),
        "workspace_id": selection["workspace_id"],
        "environment_resolution_id": local.record["resolution_id"],
        "selection_result_resource_id": selection_result_resource_id,
        "selection_result_sha256": selection_sha,
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_policy_review_id": review["review_id"],
        "gradle_result_resource_id": gradle_result_resource_id,
        "extraction_result_resource_id": extraction_result_resource_id,
        "extraction_result_sha256": "sha256:" + sha256(_canonical(extracted) + b"\n").hexdigest(),
        "gradle_tree_id": extracted["tree_id"],
        "gradle_tree_content_sha256": extracted["tree_content_sha256"],
        "gradle_launcher_path": extracted["launcher_path"],
        "java_required_major": review["policy"]["java"]["required_major"],
        "java": source,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "remaining_prerequisites": list(_REMAINING),
        "blockers": blockers, "state": "blocked" if blockers else "binding-only",
    }, "workbench-environment-fixture-java-binding-plan", "plan_id")


def _result(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT, "schema_version": 1,
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "candidate_id": plan["candidate_id"],
        "workspace_name": plan["workspace_name"], "workspace": plan["workspace"],
        "workspace_id": plan["workspace_id"],
        "environment_resolution_id": plan["environment_resolution_id"],
        "selection_result_resource_id": plan["selection_result_resource_id"],
        "selection_result_sha256": plan["selection_result_sha256"],
        "fixture_result_resource_id": plan["fixture_result_resource_id"],
        "fixture_policy_review_id": plan["fixture_policy_review_id"],
        "gradle_result_resource_id": plan["gradle_result_resource_id"],
        "extraction_result_resource_id": plan["extraction_result_resource_id"],
        "extraction_result_sha256": plan["extraction_result_sha256"],
        "gradle_tree_id": plan["gradle_tree_id"],
        "gradle_tree_content_sha256": plan["gradle_tree_content_sha256"],
        "gradle_launcher_path": plan["gradle_launcher_path"],
        "java_required_major": plan["java_required_major"], "java": plan["java"],
        "unresolved_inputs": plan["unresolved_inputs"],
        "remaining_prerequisites": plan["remaining_prerequisites"],
        "state": "binding-only-unqualified", "scope": _SCOPE,
    }


def apply_fixture_java_binding(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    expected_plan_id: str, workspace_name: str, workspace: Path | str,
    selection_result_resource_id: str, fixture_result_resource_id: str,
    expected_review_id: str, gradle_result_resource_id: str,
    extraction_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain one immutable Core link, with no Java or Gradle execution."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_java_binding(
        suite_root, share, candidate, workspace_name=workspace_name,
        workspace=workspace, selection_result_resource_id=selection_result_resource_id,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        gradle_result_resource_id=gradle_result_resource_id,
        extraction_result_resource_id=extraction_result_resource_id,
        environment=values,
    )
    if type(expected_plan_id) is not str or plan["plan_id"] != expected_plan_id:
        raise ReconstructionError("fixture Java binding changed after review")
    if plan["state"] != "binding-only":
        raise ReconstructionError("fixture Java binding is blocked: " + "; ".join(plan["blockers"]))
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("fixture Java binding resolution changed after review")
    result = _result(plan)
    payload = _canonical(result) + b"\n"
    reference = service.publish_bytes(
        "evidence", "environment-fixture-java-binding.json", payload,
        domain_id=plan["share_id"], references=(
            selection_result_resource_id, fixture_result_resource_id,
            gradle_result_resource_id, extraction_result_resource_id,
        ),
    )
    if service.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("fixture Java binding result did not reopen exactly")
    return {**result, "resource": {
        "resource_id": reference.resource_id, "store_id": reference.store_id,
        "path": str(reference.path), "sha256": reference.sha256,
    }}


def reopen_fixture_java_binding(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace_name: str, workspace: Path | str,
    selection_result_resource_id: str, fixture_result_resource_id: str,
    expected_review_id: str, gradle_result_resource_id: str,
    extraction_result_resource_id: str, result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recompute the binding from retained Core inputs after restart."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_java_binding(
        suite_root, share, candidate, workspace_name=workspace_name,
        workspace=workspace, selection_result_resource_id=selection_result_resource_id,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        gradle_result_resource_id=gradle_result_resource_id,
        extraction_result_resource_id=extraction_result_resource_id,
        environment=values,
    )
    if plan["state"] != "binding-only":
        raise ReconstructionError("fixture Java binding is no longer available")
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    try:
        reference = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        result = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError) as exc:
        raise ReconstructionError(f"fixture Java binding cannot be reopened: {exc}") from exc
    if (reference.owner_id != "workbench-core" or reference.role != "evidence"
            or reference.domain_id != plan["share_id"]
            or type(result) is not dict or payload != _canonical(result) + b"\n"
            or result != _result(plan)):
        raise ReconstructionError("fixture Java binding result has another identity or scope")
    return result


__all__ = [
    "plan_fixture_java_binding", "apply_fixture_java_binding",
    "reopen_fixture_java_binding",
]
