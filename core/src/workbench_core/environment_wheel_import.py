"""Reviewed custody of optional wheel bytes for a V3 environment share.

This operation retains exact, local wheel files under Core's managed-tree
catalog. It does not install them or close the share's package dependency gap.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import ctypes
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from workbench_api import ModuleError
from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .durable_records import private_record_lock
from .environment_input_candidates import (
    _wheel_candidate, validate_input_candidate,
)
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _host_variant,
    _resource_host, _seal, validate_share,
)
from .environment_resolution import resolve_environment
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .module_cli import _snapshot_wheel, _wheel
from .output_routing import _private_directory
from .runtime_java import JavaRuntimeError, host_platform
from .storage.registered import DurableResourceError
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY


PLAN_FORMAT = "workbench-environment-wheel-import-plan-v1"
RESULT_FORMAT = "workbench-environment-wheel-import-result-v1"
_WHEEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,199}\.whl\Z")
_MAX_WHEEL_BYTES = 256 * 1024 * 1024


def _store(local: Any) -> tuple[CoreManagedTrees, Path, Path]:
    root = local.state_root / "environment-inputs"
    target = root / "wheels"  # Candidate-specific directory is added by the caller.
    host = CoreManagedTrees(
        workspace=local.workspace, configuration_home=local.configuration_home,
        locations={"artifacts": root}, owner_id="workbench-core",
        policy_id=local.record["resolution_id"],
        location_sources={"artifacts": "core-managed-input"},
    )
    return host, root, target


def _private_store(root: Path, target: Path | None = None) -> None:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("managed wheel input traverses a redirect")
    state_root = root.parent
    for path in (state_root, root, root / "wheels", *((target.parent,) if target else ())):
        if (path.exists() or path.is_symlink()) and not private_path(path, directory=True):
            raise ReconstructionError("managed wheel input store is not owner-private")


def _tree_host_supported() -> bool:
    """Fail in review when Core cannot publish an exact Linux tree at all."""

    if (os.name != "posix" or not sys.platform.startswith("linux")
            or not hasattr(os, "O_NOFOLLOW")
            or os.scandir not in os.supports_fd
            or not {os.open, os.stat, os.link, os.mkdir, os.unlink}.issubset(
                os.supports_dir_fd,
            )):
        return False
    try:
        return getattr(ctypes.CDLL(None), "renameat2", None) is not None
    except OSError:
        return False


def _source_rows(wheels: Sequence[Path] | None, expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if wheels is None:
        return []
    if (not isinstance(wheels, (tuple, list)) or len(wheels) != len(expected)
            or any(not isinstance(path, Path) for path in wheels)):
        raise ReconstructionError("select one explicit local wheel per candidate package")
    rows: list[dict[str, Any]] = []
    for path in wheels:
        if not path.is_absolute() or _WHEEL_NAME.fullmatch(path.name) is None:
            raise ReconstructionError("selected wheel path or filename is not portable")
        identity = _wheel_candidate(path)
        rows.append({**identity, "path": str(path), "filename": path.name})
    rows.sort(key=lambda row: row["distribution"])
    if ([{key: value for key, value in row.items() if key not in {"path", "filename"}}
         for row in rows] != expected
            or len({row["filename"] for row in rows}) != len(rows)):
        raise ReconstructionError("selected wheel bytes differ from the reviewed candidate")
    return rows


def _tree_files(reference: ManagedTreeReference, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected = candidate["packages"]
    if (reference.owner_id != "workbench-core" or reference.role != "artifacts"
            or reference.domain_id != candidate["candidate_id"]
            or reference.derived_status != "current"
            or len(reference.members) != len(expected)):
        raise ReconstructionError("managed wheel tree has another identity or changed members")
    rows: list[dict[str, Any]] = []
    for member in reference.members:
        name = member.get("path")
        if (member.get("kind") != "file" or type(name) is not str
                or _WHEEL_NAME.fullmatch(name) is None
                or type(member.get("sha256")) is not str
                or type(member.get("size")) is not int):
            raise ReconstructionError("managed wheel tree contains an unsupported member")
        path = reference.path / name
        try:
            wheel = _wheel(path)
        except (ModuleError, OSError, ValueError) as exc:
            raise ReconstructionError(f"managed wheel cannot be reopened: {exc}") from exc
        rows.append({
            "distribution": wheel.distribution, "version": wheel.version,
            "sha256": "sha256:" + member["sha256"], "size": member["size"],
            "module_ids": sorted(wheel.modules), "profile_ids": sorted(wheel.profiles),
            "filename": name,
        })
    rows.sort(key=lambda row: row["distribution"])
    if ([{key: value for key, value in row.items() if key != "filename"}
         for row in rows] != expected
            or len({row["filename"] for row in rows}) != len(rows)):
        raise ReconstructionError("managed wheel tree differs from the reviewed candidate")
    return rows


def _validate_stage(root: Path, sources: list[dict[str, Any]], candidate: Mapping[str, Any]) -> None:
    expected_names = {row["filename"] for row in sources}
    if {path.name for path in root.iterdir()} != expected_names:
        raise ReconstructionError("managed wheel stage has unexpected members")
    rows = []
    for source_row in sources:
        path = root / source_row["filename"]
        rows.append(_wheel_candidate(path))
    rows.sort(key=lambda row: row["distribution"])
    if rows != candidate["packages"]:
        raise ReconstructionError("managed wheel stage differs from the reviewed candidate")


def _tree_state(
    host: CoreManagedTrees, target: Path, candidate: Mapping[str, Any],
) -> tuple[str, str | None, list[dict[str, str]]]:
    rows = [row for row in host.catalog.trees.inventory(workspace=host.workspace)
            if row["path"] == str(target)]
    prior: list[dict[str, str]] = []
    active: list[dict[str, Any]] = []
    for row in rows:
        if (row["owner_id"] != "workbench-core" or row["role"] != "artifacts"):
            raise ReconstructionError("managed wheel target has a foreign Core reservation")
        if row["status"] == "failed" and not target.exists() and not target.is_symlink():
            prior.append({"tree_id": str(row["tree_id"]), "status": "failed"})
            continue
        active.append(row)
    if len(active) > 1:
        raise ReconstructionError("managed wheel target has ambiguous Core reservations")
    if not active:
        if target.exists() or target.is_symlink():
            raise ReconstructionError("managed wheel target exists outside completed Core custody")
        return "acquire", None, prior
    row = active[0]
    status = str(row["status"])
    tree_id = str(row["tree_id"])
    if status not in {"committed", "published-uncommitted", "incomplete"}:
        raise ReconstructionError(f"managed wheel tree requires reviewed recovery: {status}")
    try:
        intent = host.catalog.trees.intent(tree_id)
    except ManagedTreeError as exc:
        raise ReconstructionError("managed wheel stage has no complete publication intent") from exc
    if (intent["domain_id"] != candidate["candidate_id"]
            or intent["policy_id"] != host.policy_id):
        raise ReconstructionError("managed wheel tree belongs to another reviewed input")
    if status == "committed":
        reference = host.describe(tree_id)
        if reference.path != target or reference.policy_id != host.policy_id:
            raise ReconstructionError("managed wheel tree path or policy changed")
        _tree_files(reference, candidate)
        return "reuse", tree_id, prior
    return "reconcile", tree_id, prior


def plan_wheel_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, wheels: Sequence[Path] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review exact local wheel bytes and classify any earlier publication."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("wheel import requires a V3 environment share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root, wheel_root = _store(local)
    target = wheel_root / reviewed["candidate_id"].rsplit(":", 1)[-1] / "snapshot"
    blockers: list[str] = []
    if not local.workspace.is_dir():
        blockers.append("selected workspace directory is missing")
    supported = _tree_host_supported()
    if not supported:
        blockers.append("exact Linux managed-tree publication is unavailable on this host")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        executing_host = None
        blockers.append(f"wheel import has no supported executing host: {exc}")
    if executing_host is not None and executing_host != reviewed["host_variant"]:
        blockers.append("wheel candidate targets another executing host")
    sources = _source_rows(wheels, reviewed["packages"])
    if supported:
        _private_store(root, target)
        try:
            action, tree_id, prior = _tree_state(host, target, reviewed)
        except (ManagedTreeError, DurableResourceError, OSError, ValueError) as exc:
            if isinstance(exc, ReconstructionError):
                raise
            raise ReconstructionError(f"managed wheel tree cannot be inventoried: {exc}") from exc
    else:
        action, tree_id, prior = "unsupported", None, []
    if action == "acquire" and not sources:
        blockers.append("local wheel sources are required for acquisition")
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "workspace": str(local.workspace), "environment_resolution_id": local.record["resolution_id"],
        "state_root": str(local.state_root), "target": str(target),
        "host_variant": reviewed["host_variant"], "packages": reviewed["packages"],
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "sources": sources, "action": action, "tree_id": tree_id,
        "prior_failed_trees": prior, "blockers": blockers,
        "state": "blocked" if blockers else "ready",
    }, "workbench-environment-wheel-import-plan", "plan_id")


def _copy_sources(stage: Path, sources: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    stage.mkdir(mode=0o700)
    for row, package in zip(sources, expected):
        with _snapshot_wheel(Path(row["path"])) as wheel:
            if (wheel.distribution != package["distribution"]
                    or wheel.version != package["version"]
                    or sorted(wheel.modules) != package["module_ids"]
                    or sorted(wheel.profiles) != package["profile_ids"]
                    or wheel.path.name != row["filename"]):
                raise ReconstructionError("selected wheel changed after review")
            target = stage / row["filename"]
            digest = sha256()
            size = 0
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
            with wheel.path.open("rb") as source:
                descriptor = os.open(target, flags, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    while block := source.read(1024 * 1024):
                        size += len(block)
                        if size > _MAX_WHEEL_BYTES or size > package["size"]:
                            raise ReconstructionError("selected wheel grew after review")
                        digest.update(block)
                        output.write(block)
            if (size != package["size"]
                    or "sha256:" + digest.hexdigest() != package["sha256"]):
                raise ReconstructionError("selected wheel bytes changed after review")


def apply_wheel_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    expected_plan_id: str, workspace: Path | str,
    wheels: Sequence[Path] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain wheel bytes and a linked result without invoking package install."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_wheel_import(
        suite_root, share, candidate, workspace=workspace, wheels=wheels,
        environment=values,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("wheel import changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("wheel import is blocked: " + "; ".join(plan["blockers"]))
    local = resolve_environment(suite_root, workspace=Path(plan["workspace"]), environment=values)
    host, root, _ = _store(local)
    _private_directory(root)
    _private_directory(Path(plan["target"]).parent)
    _private_store(root, Path(plan["target"]))
    lock = root / f".wheel-import-{plan['candidate_id'].rsplit(':', 1)[-1]}.lock"
    with private_record_lock(lock, wait=True):
        current = plan_wheel_import(
            suite_root, share, candidate, workspace=workspace, wheels=wheels,
            environment=values,
        )
        if current != plan:
            raise ReconstructionError("wheel import inputs changed before acquisition")
        service = _resource_host(Path(suite_root), local.workspace, values)
        if service.policy_id != plan["environment_resolution_id"]:
            raise ReconstructionError("wheel import resolution changed after review")
        prepared = {
            "format": "workbench-environment-wheel-import-attempt-v1",
            "schema_version": 1, "state": "prepared",
            "plan_id": plan["plan_id"], "share_id": plan["share_id"],
            "candidate_id": plan["candidate_id"], "action": plan["action"],
            "target": plan["target"], "tree_id": plan["tree_id"],
        }
        prepared_ref = service.publish_bytes(
            "evidence", "environment-wheel-import-attempt.json",
            _canonical(prepared) + b"\n", domain_id=plan["share_id"],
        )
        target = Path(plan["target"])
        if plan["action"] == "acquire":
            try:
                with host.stage("artifacts", target.name, requested_path=target) as stage:
                    _private_store(root, target)
                    _copy_sources(stage.path, plan["sources"], plan["packages"])
                    reference = stage.publish(
                        validate=lambda path: _validate_stage(path, plan["sources"], {
                            "packages": plan["packages"],
                        }),
                        domain_id=plan["candidate_id"],
                        references=(prepared_ref.resource_id,),
                        inventory_policy=EXACT_INVENTORY_POLICY,
                    )
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed wheel tree cannot be published: {exc}") from exc
        else:
            try:
                reference = (host.reconcile(plan["tree_id"])
                             if plan["action"] == "reconcile"
                             else host.describe(plan["tree_id"]))
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed wheel tree cannot be reopened: {exc}") from exc
        if (reference.path != target or reference.policy_id != host.policy_id
                or reference.workspace != local.workspace):
            raise ReconstructionError("managed wheel publication has another local binding")
        package_files = _tree_files(reference, {
            "candidate_id": plan["candidate_id"], "packages": plan["packages"],
        })
        result = {
            "format": RESULT_FORMAT, "schema_version": 1,
            "outcome": ("acquired" if plan["action"] == "acquire" else
                        "reconciled" if plan["action"] == "reconcile" else "reused"),
            "plan_id": plan["plan_id"], "share_id": plan["share_id"],
            "candidate_id": plan["candidate_id"],
            "attempt_resource_id": prepared_ref.resource_id,
            "workspace": plan["workspace"], "environment_resolution_id": plan["environment_resolution_id"],
            "tree_id": reference.tree_id, "tree_path": str(reference.path),
            "tree_content_sha256": reference.content_sha256,
            "packages": package_files,
            "unresolved_inputs": plan["unresolved_inputs"],
            "scope": "Exact optional wheel bytes only; installation and dependency closure remain unresolved.",
        }
        payload = _canonical(result) + b"\n"
        completed = service.publish_bytes(
            "evidence", "environment-wheel-import.json", payload,
            domain_id=plan["share_id"], references=(prepared_ref.resource_id,),
        )
        if service.read_bytes(completed.resource_id) != payload:
            raise ReconstructionError("wheel import result did not reopen exactly")
        return {**result, "resource": {
            "resource_id": completed.resource_id, "store_id": completed.store_id,
            "path": str(completed.path), "sha256": completed.sha256,
        }}


def reopen_wheel_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Prove a surviving result and managed wheel tree without source wheels."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    if not _tree_host_supported():
        raise ReconstructionError("exact Linux managed-tree reopening is unavailable on this host")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        raise ReconstructionError(f"wheel import has no supported executing host: {exc}") from exc
    if executing_host != reviewed["host_variant"]:
        raise ReconstructionError("wheel candidate targets another executing host")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root, _ = _store(local)
    _private_store(root)
    service = _resource_host(Path(suite_root), local.workspace, values)
    try:
        receipt_ref = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        receipt = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError) as exc:
        raise ReconstructionError(f"wheel import result cannot be reopened: {exc}") from exc
    if (receipt_ref.owner_id != "workbench-core" or receipt_ref.role != "evidence"
            or receipt_ref.domain_id != portable["share_id"]
            or type(receipt) is not dict or payload != _canonical(receipt) + b"\n"
            or receipt.get("format") != RESULT_FORMAT or receipt.get("schema_version") != 1
            or receipt.get("share_id") != portable["share_id"]
            or receipt.get("candidate_id") != reviewed["candidate_id"]
            or receipt.get("workspace") != str(local.workspace)
            or receipt.get("environment_resolution_id") != local.record["resolution_id"]
            or receipt.get("unresolved_inputs") != portable["lock"]["unresolved_inputs"]):
        raise ReconstructionError("wheel import result has another identity or scope")
    target = root / "wheels" / reviewed["candidate_id"].rsplit(":", 1)[-1] / "snapshot"
    try:
        reference = host.describe(receipt["tree_id"])
        files = _tree_files(reference, reviewed)
    except (ManagedTreeError, OSError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"managed wheel result tree cannot be reopened: {exc}") from exc
    if (reference.path != target or reference.policy_id != host.policy_id
            or receipt.get("tree_path") != str(target)
            or receipt.get("tree_content_sha256") != reference.content_sha256
            or receipt.get("packages") != files):
        raise ReconstructionError("wheel import result differs from its managed tree")
    return receipt


__all__ = ["plan_wheel_import", "apply_wheel_import", "reopen_wheel_import"]
