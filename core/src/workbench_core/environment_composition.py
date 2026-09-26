"""Review and retain the already acquired parts of a V3 environment share.

This operation does not acquire bytes. It reopens three Core results, checks
their current local bindings, and links them in one durable result. Package and
profile fixture inputs remain unresolved until they have their own locks.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

from . import tooling_provision
from .durable_records import read_private_bytes
from .environment_project_import import (
    ACQUISITION_FORMAT,
    RESULT_FORMAT_V2 as PROJECT_RESULT_FORMAT,
    _acquisition_receipt, _git_binding, _git_environment, _managed_destination,
    _receipt_path, _required_paths, _verify_owned_checkout,
)
from .environment_reconstruction import (
    ReconstructionError, RESULT_FORMAT_V4, SHARE_FORMAT_V3, _canonical,
    _resource_host, _seal, plan_import, validate_share,
)
from .environment_resolution import resolve_environment
from .environment_tool_import import RESULT_FORMAT as TOOL_RESULT_FORMAT
from .portable_managed_tools import inspect_locked_managed_tools
from .storage.registered import DurableResourceError
from .user_preferences import load_workspaces, resolve_expression


PLAN_FORMAT = "workbench-environment-composition-plan-v1"
RESULT_FORMAT = "workbench-environment-composition-result-v1"


def _reopen(
    service: Any, resource_id: str, label: str, share_id: str,
) -> tuple[dict[str, Any], str]:
    if type(resource_id) is not str:
        raise ReconstructionError(f"{label} resource ID is invalid")
    try:
        reference = service.describe(resource_id)
        if (reference.owner_id != "workbench-core" or reference.role != "evidence"
                or reference.domain_id != share_id):
            raise ReconstructionError(f"{label} resource has another Core custody scope")
        payload = service.read_bytes(resource_id)
        value = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"{label} Core result cannot be reopened: {exc}") from exc
    if type(value) is not dict or payload != _canonical(value) + b"\n":
        raise ReconstructionError(f"{label} Core result has noncanonical bytes")
    return value, "sha256:" + sha256(payload).hexdigest()


def _selection(
    suite_root: Path, portable: dict[str, Any], receipt: dict[str, Any],
    *, workspace_name: str, workspace: Path, environment: Mapping[str, str],
) -> dict[str, Any]:
    if (receipt.get("format") != RESULT_FORMAT_V4 or receipt.get("share") != portable
            or receipt.get("project_source_lock") != portable["lock"]["project_source_lock"]
            or receipt.get("managed_tool_lock") != portable["lock"]["managed_tool_lock"]):
        raise ReconstructionError("selection result does not belong to this exact V3 share")
    manifest = receipt.get("configuration_manifest")
    if type(manifest) is not dict or type(manifest.get("path")) is not str:
        raise ReconstructionError("selection result has no local Configuration V1 path")
    registry = load_workspaces(environment=environment)
    row = next((item for item in registry["entries"] if item["name"] == workspace_name), None)
    if (row is None or row.get("workspace_id") != receipt.get("workspace_id")
            or resolve_expression(row["path"], environment=environment) != workspace
            or row.get("profile_config") != manifest["path"]):
        raise ReconstructionError("local workspace selection changed after import")
    java_home = row.get("java_home")
    acquire_java = "managed_java" in receipt
    reviewed = plan_import(
        suite_root, portable, workspace_name=workspace_name, workspace=workspace,
        config_path=Path(manifest["path"]), java_home=java_home,
        acquire_managed_java=acquire_java, environment=environment,
    )
    if (reviewed["state"] != "ready" or reviewed["action"] != "reuse"
            or reviewed["profile_selection_digest"] != receipt.get("profile_selection_digest")
            or reviewed["configuration_manifest_sha256"] != manifest.get("sha256")
            or reviewed["managed_java_feature"] != row.get("managed_java_feature")):
        raise ReconstructionError("local workspace selection or profile inputs changed")
    expected_unresolved = [
        item for item in portable["lock"]["unresolved_inputs"]
        if not acquire_java or item != "managed-java-archive"
    ]
    if receipt.get("unresolved_inputs") != expected_unresolved:
        raise ReconstructionError("selection result omits locked unresolved inputs")
    return reviewed


def _project(
    portable: dict[str, Any], result: dict[str, Any], *, state_root: Path,
    environment: Mapping[str, str],
) -> None:
    lock = portable["lock"]["project_source_lock"]
    destination = _managed_destination(state_root, lock["sha256"])
    if (result.get("format") != PROJECT_RESULT_FORMAT
            or result.get("share_id") != portable["share_id"]
            or result.get("project_source_lock") != lock
            or result.get("managed_tool_lock") != portable["lock"]["managed_tool_lock"]
            or result.get("managed_destination") != str(destination)):
        raise ReconstructionError("project result does not match the exact share and managed input")
    acquisition = result.get("acquisition_receipt")
    if (type(acquisition) is not dict or type(acquisition.get("receipt_id")) is not str
            or acquisition.get("path") != str(_receipt_path(state_root, acquisition["receipt_id"]))):
        raise ReconstructionError("project acquisition receipt identity is invalid")
    receipt_path = Path(acquisition["path"])
    try:
        payload = read_private_bytes(receipt_path, byte_limit=256 * 1024)
        receipt = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ReconstructionError("project acquisition receipt is unavailable") from exc
    if (type(receipt) is not dict or payload != _canonical(receipt) + b"\n"
            or receipt.get("format") != ACQUISITION_FORMAT
            or receipt.get("receipt_id") != acquisition["receipt_id"]
            or acquisition.get("sha256") != "sha256:" + sha256(payload).hexdigest()
            or receipt.get("share_id") != portable["share_id"]
            or receipt.get("lock_id") != portable["lock"]["lock_id"]
            or receipt.get("project_source_lock") != lock
            or receipt.get("host_variant") != portable["lock"]["host_variant"]
            or receipt.get("destination") != str(destination)):
        raise ReconstructionError("project acquisition receipt changed or has another identity")
    git = receipt.get("git")
    if (type(git) is not dict or type(git.get("path")) is not str
            or _git_binding(git["path"]) != git):
        raise ReconstructionError("selected Git executable changed after acquisition")
    required = _required_paths(receipt.get("required_paths"))
    transport = receipt.get("transport")
    branch = receipt.get("branch")
    if (type(transport) is not dict or type(branch) is not str
            or receipt != _acquisition_receipt(
                portable, destination=destination, git=git, transport=transport,
                branch=branch, required_paths=required,
                host=portable["lock"]["host_variant"],
            )):
        raise ReconstructionError("project acquisition receipt seal changed")
    _verify_owned_checkout(
        destination, receipt_path, payload, git=git, lock=lock,
        environment=_git_environment(environment), required=required,
    )


def _tools(portable: dict[str, Any], result: dict[str, Any], *, state_root: Path) -> None:
    lock = portable["lock"]["managed_tool_lock"]
    if (result.get("format") != TOOL_RESULT_FORMAT
            or result.get("share_id") != portable["share_id"]
            or result.get("managed_tool_lock") != lock
            or result.get("state_root") != str(state_root)):
        raise ReconstructionError("managed-tool result does not match the exact share and state root")
    check = inspect_locked_managed_tools(lock, state_root=state_root)
    key = f"{lock['host_variant']['os']}-{lock['host_variant']['architecture']}"
    current = tooling_provision.inspect_tools(state_root, key=key)
    if (check["state"] != "ready" or current["state"] != "initialized"
            or any(current["tools"][name]["state"] != "ready" for name in ("prism", "packwiz"))
            or result.get("tools") != current["tools"]):
        raise ReconstructionError("managed-tool bytes changed or are unavailable")


def plan_environment_composition(
    suite_root: Path, share: Mapping[str, Any], *, workspace_name: str,
    workspace: Path | str, selection_resource_id: str, project_resource_id: str,
    tool_resource_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review three retained results against their live local bindings."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("composition requires an exact V3 project and tool share")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    identifiers = (selection_resource_id, project_resource_id, tool_resource_id)
    if len(set(identifiers)) != 3:
        raise ReconstructionError("composition needs three distinct Core result resources")
    selection, selection_sha = _reopen(service, selection_resource_id, "selection", portable["share_id"])
    project, project_sha = _reopen(service, project_resource_id, "project", portable["share_id"])
    tools, tool_sha = _reopen(service, tool_resource_id, "managed-tool", portable["share_id"])
    reviewed = _selection(
        Path(suite_root), portable, selection, workspace_name=workspace_name,
        workspace=local.workspace, environment=values,
    )
    _project(portable, project, state_root=local.state_root, environment=values)
    _tools(portable, tools, state_root=local.state_root)
    remaining = [item for item in selection["unresolved_inputs"]
                 if item != "workspace-project-bytes"]
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "lock_id": portable["lock"]["lock_id"],
        "workspace_name": workspace_name, "workspace": str(local.workspace),
        "workspace_id": selection["workspace_id"],
        "environment_resolution_id": local.record["resolution_id"],
        "selection_plan_id": reviewed["plan_id"],
        "resources": {
            "selection": {"resource_id": selection_resource_id, "sha256": selection_sha},
            "project": {"resource_id": project_resource_id, "sha256": project_sha},
            "managed_tools": {"resource_id": tool_resource_id, "sha256": tool_sha},
        },
        "unresolved_inputs": remaining,
        "state": "ready",
    }, "workbench-environment-composition-plan", "plan_id")


def apply_environment_composition(
    suite_root: Path, share: Mapping[str, Any], *, expected_plan_id: str,
    workspace_name: str, workspace: Path | str, selection_resource_id: str,
    project_resource_id: str, tool_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish a Core-linked result after repeating the exact review."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_environment_composition(
        suite_root, share, workspace_name=workspace_name, workspace=workspace,
        selection_resource_id=selection_resource_id,
        project_resource_id=project_resource_id, tool_resource_id=tool_resource_id,
        environment=values,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("environment composition changed after review")
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("environment composition resolution changed after review")
    result = {
        "format": RESULT_FORMAT, "schema_version": 1,
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "lock_id": plan["lock_id"], "workspace_id": plan["workspace_id"],
        "resources": plan["resources"],
        "unresolved_inputs": plan["unresolved_inputs"],
        "scope": "Local selection, exact managed project input, and Prism/Packwiz; packages and profile fixtures remain separate.",
    }
    payload = _canonical(result) + b"\n"
    references = tuple(item["resource_id"] for item in plan["resources"].values())
    reference = service.publish_bytes(
        "evidence", "environment-composition.json", payload,
        domain_id=plan["share_id"], references=references,
    )
    if service.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("environment composition result did not reopen exactly")
    return {**result, "resource": {
        "resource_id": reference.resource_id, "store_id": reference.store_id,
        "path": str(reference.path), "sha256": reference.sha256,
    }}


__all__ = ["plan_environment_composition", "apply_environment_composition"]
