"""Reviewed V3-share acquisition of Core's fixed managed developer tools.

This operation acquires only Prism and Packwiz. The portable lock supplies
policy identity; Core's existing provisioner owns downloads, builds and the
stable managed-tool store. Earlier selection and project import receipts keep
their original, narrower meaning.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from . import tooling_provision
from .environment_resolution import resolve_environment
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _host_variant,
    _resource_host, _seal, validate_share,
)
from .host_filesystem import private_path
from .portable_managed_tools import (
    inspect_locked_managed_tools, matches_local_managed_tool_policy,
)
from .runtime_java import JavaRuntimeError, host_platform


PLAN_FORMAT = "workbench-environment-tool-import-plan-v1"
RESULT_FORMAT = "workbench-environment-tool-import-result-v1"


def _tool_state(
    state_root: Path, key: str, *, seed_dir: Path | None,
    go_executable: Path | None,
) -> dict[str, Any]:
    try:
        return tooling_provision._plan(state_root, key, seed_dir, go_executable)
    except (OSError, ValueError) as exc:
        raise ReconstructionError(f"managed-tool source cannot be reviewed: {exc}") from exc


def _root_blocker(state_root: Path) -> str | None:
    for ancestor in (state_root, *state_root.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            return "managed-tool state root traverses a redirect"
    if state_root.exists() and not private_path(state_root, directory=True):
        return "managed-tool state root is not owner-private"
    return None


def plan_tool_import(
    suite_root: Path, share: Mapping[str, Any], *, workspace: Path | str,
    seed_dir: Path | str | None = None,
    go_executable: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review fixed-policy tool acquisition without writing or downloading."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("managed-tool byte import requires a V3 share")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    lock = portable["lock"]["managed_tool_lock"]
    blockers: list[str] = []
    if not local.workspace.is_dir():
        blockers.append("selected workspace directory is missing")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        executing_host = None
        blockers.append(f"managed tools have no supported executing host: {exc}")
    if executing_host != lock["host_variant"]:
        blockers.append("managed-tool lock targets another executing host")
    if not matches_local_managed_tool_policy(lock):
        blockers.append("Core managed-tool policy differs from the exact portable lock")
    root_blocker = _root_blocker(local.state_root)
    if root_blocker is not None:
        blockers.append(root_blocker)
    key = f"{lock['host_variant']['os']}-{lock['host_variant']['architecture']}"
    selected_seed = None if seed_dir is None else Path(seed_dir).expanduser().absolute()
    selected_go = None if go_executable is None else Path(go_executable).expanduser().absolute()
    tool_plan = None
    if not blockers:
        tool_plan = _tool_state(
            local.state_root, key, seed_dir=selected_seed, go_executable=selected_go,
        )
        if any(tool_plan["check"]["tools"][name]["state"] == "invalid"
               for name in ("prism", "packwiz")):
            blockers.append("existing managed-tool bytes are invalid; repair them explicitly")
    body = {
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"],
        "lock_id": portable["lock"]["lock_id"],
        "managed_tool_lock": lock,
        "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "state_root": str(local.state_root),
        "host_variant": executing_host,
        "tooling_plan": tool_plan,
        "action": (
            "blocked" if blockers else "acquire" if tool_plan["actions"] else "reuse"
        ),
        "blockers": blockers,
        "state": "blocked" if blockers else "ready",
    }
    return _seal(body, "workbench-environment-tool-import-plan", "plan_id")


def apply_tool_import(
    suite_root: Path, share: Mapping[str, Any], *, expected_plan_id: str,
    workspace: Path | str, seed_dir: Path | str | None = None,
    go_executable: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Acquire or reuse the reviewed tools and retain a Core result."""

    values = dict(os.environ if environment is None else environment)
    portable = validate_share(dict(share))
    plan = plan_tool_import(
        suite_root, portable, workspace=workspace, seed_dir=seed_dir,
        go_executable=go_executable, environment=values,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("managed-tool import plan changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("managed-tool import is blocked: " + "; ".join(plan["blockers"]))
    service = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("managed-tool import resolution changed after review")
    prepared = {
        "format": "workbench-environment-tool-import-attempt-v1",
        "schema_version": 1, "state": "prepared",
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "managed_tool_lock": plan["managed_tool_lock"],
        "tooling_plan_id": plan["tooling_plan"]["plan_id"],
    }
    prepared_ref = service.publish_bytes(
        "evidence", "environment-tool-import-attempt.json",
        _canonical(prepared) + b"\n", domain_id=plan["share_id"],
    )
    admitted = plan_tool_import(
        suite_root, portable, workspace=workspace, seed_dir=seed_dir,
        go_executable=go_executable, environment=values,
    )
    if admitted["plan_id"] != plan["plan_id"] or admitted["state"] != "ready":
        raise ReconstructionError("managed-tool inputs changed after prepared attempt")
    state_root = Path(plan["state_root"])
    key = f"{plan['managed_tool_lock']['host_variant']['os']}-{plan['managed_tool_lock']['host_variant']['architecture']}"
    selected_sources = plan["tooling_plan"]["selection"]
    try:
        if plan["action"] == "acquire":
            tooling_provision.prepare_tools(
                state_root, key=key, seed_dir=selected_sources["seed_dir"],
                go_executable=selected_sources["go_executable"],
            )
        observed = inspect_locked_managed_tools(plan["managed_tool_lock"], state_root=state_root)
        exact = tooling_provision.inspect_tools(state_root, key=key)
    except (OSError, ValueError) as exc:
        raise ReconstructionError(f"managed-tool byte acquisition did not complete: {exc}") from exc
    if observed["state"] != "ready" or exact["state"] != "initialized":
        raise ReconstructionError("managed-tool acquisition did not reopen the locked tools")
    for name in ("prism", "packwiz"):
        if exact["tools"][name]["state"] != "ready":
            raise ReconstructionError(f"managed {name} did not reopen as ready")
    result = {
        "format": RESULT_FORMAT, "schema_version": 1,
        "outcome": "acquired" if plan["action"] == "acquire" else "reused",
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "attempt_resource_id": prepared_ref.resource_id,
        "managed_tool_lock": plan["managed_tool_lock"],
        "state_root": str(state_root),
        "tools": exact["tools"],
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "scope": "Core-managed Prism and Packwiz bytes only; other environment inputs remain separate.",
    }
    payload = _canonical(result) + b"\n"
    completed = _resource_host(Path(suite_root), Path(plan["workspace"]), values)
    reference = completed.publish_bytes(
        "evidence", "environment-tool-import.json", payload,
        domain_id=plan["share_id"], references=(prepared_ref.resource_id,),
    )
    if completed.read_bytes(reference.resource_id) != payload:
        raise ReconstructionError("managed-tool import result did not reopen exactly")
    return {
        **result,
        "resource": {
            "resource_id": reference.resource_id, "store_id": reference.store_id,
            "path": str(reference.path), "sha256": reference.sha256,
        },
    }


__all__ = ["plan_tool_import", "apply_tool_import"]
