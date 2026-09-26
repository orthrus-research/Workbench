"""Core custody of a reviewed, complete offline package wheelhouse.

The retained tree is an exact input. No package is installed by this API.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from workbench_api.durable_resources import DurableResourceError as PinnedDirectoryError
from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .durable_files import _directory as pinned_directory
from .durable_records import private_record_lock
from .environment_input_candidates import validate_input_candidate
from .environment_package_closure import plan_package_closure
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _seal, _resource_host,
    validate_share,
)
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported, reopen_wheel_import
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY, MAX_TOTAL_BYTES
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-package-import-plan-v1"
RESULT_FORMAT = "workbench-environment-package-import-result-v1"
ATTEMPT_FORMAT = "workbench-environment-package-import-attempt-v1"
_SCOPE = "Exact offline dependency wheelhouse bytes only; package installation and installed admission remain unresolved."
_CLOSURE_KEYS = frozenset({
    "format", "schema_version", "share_id", "candidate_id", "wheel_resource_id",
    "wheel_tree_id", "wheelhouse", "manifest_sha256", "requirements_lock_sha256",
    "target", "host_variant", "fixture_owner", "wheels", "closure",
    "unresolved_inputs", "coverage", "state", "plan_id",
})


def _reviewed(share: Mapping[str, Any], candidate: Mapping[str, Any], value: object) -> dict[str, Any]:
    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("package import requires a V3 environment share")
    selected = validate_input_candidate(portable, dict(candidate))
    if type(value) is not dict or set(value) != _CLOSURE_KEYS:
        raise ReconstructionError("package import needs an exact reviewed closure plan")
    body = {key: entry for key, entry in value.items() if key != "plan_id"}
    if (value != _seal(body, "workbench-environment-package-closure-plan", "plan_id")
            or value["format"] != "workbench-environment-package-closure-plan-v1"
            or value["schema_version"] != 1
            or value["state"] != "reviewed"
            or value["coverage"] != "reviewed-offline-dependency-closure-only"
            or value["share_id"] != portable["share_id"]
            or value["candidate_id"] != selected["candidate_id"]
            or value["host_variant"] != selected["host_variant"]
            or value["unresolved_inputs"] != portable["lock"]["unresolved_inputs"]
            or type(value["wheel_resource_id"]) is not str
            or type(value["wheel_tree_id"]) is not str
            or type(value["wheelhouse"]) is not str
            or not Path(value["wheelhouse"]).is_absolute()
            or type(value["wheels"]) is not list
            or type(value["closure"]) is not list):
        raise ReconstructionError("package closure review has another identity or scope")
    return value


def _content(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: entry for key, entry in value.items()
            if key not in {"wheelhouse", "plan_id"}}


def _verify_content(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    *, workspace: Path | str, reviewed: Mapping[str, Any], wheelhouse: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    current = plan_package_closure(
        suite_root, share, candidate, workspace=workspace,
        wheel_resource_id=reviewed["wheel_resource_id"],
        wheelhouse=wheelhouse, environment=environment,
    )
    if _content(current) != _content(reviewed):
        raise ReconstructionError("package wheelhouse differs from the reviewed closure")
    return current


def _store(local: Any) -> tuple[CoreManagedTrees, Path, Path]:
    root = local.state_root / "environment-inputs"
    host = CoreManagedTrees(
        workspace=local.workspace, configuration_home=local.configuration_home,
        locations={"artifacts": root}, owner_id="workbench-core",
        policy_id=local.record["resolution_id"],
        location_sources={"artifacts": "core-managed-input"},
    )
    return host, root, root / "package-closures"


def _private_store(root: Path, target: Path | None = None) -> None:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("managed package input traverses a redirect")
    paths = (root.parent, root, root / "package-closures")
    if target is not None:
        paths += (target.parent,)
    for path in paths:
        if (path.exists() or path.is_symlink()) and not private_path(path, directory=True):
            raise ReconstructionError("managed package input store is not owner-private")


def _expected_members(reviewed: Mapping[str, Any]) -> dict[str, tuple[str, int | None]]:
    expected = {
        "wheelhouse.json": (reviewed["manifest_sha256"].removeprefix("sha256:"), None),
        "requirements.lock": (reviewed["requirements_lock_sha256"].removeprefix("sha256:"), None),
    }
    for row in reviewed["wheels"]:
        expected["wheels/" + row["filename"]] = (row["sha256"], row["size"])
    return expected


def _tree_files(reference: ManagedTreeReference, reviewed: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (reference.owner_id != "workbench-core" or reference.role != "artifacts"
            or reference.domain_id != reviewed["plan_id"]
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or reference.derived_status != "current"
            or not private_path(reference.path, directory=True)):
        raise ReconstructionError("managed package tree has another identity or changed members")
    expected = _expected_members(reviewed)
    members = {row["path"]: row for row in reference.members}
    if len(members) != len(reference.members) or set(members) != set(expected) | {"wheels"}:
        raise ReconstructionError("managed package tree contains missing or extra members")
    directory = members["wheels"]
    if directory.get("kind") != "directory" or int(directory.get("mode", 0)) & 0o077:
        raise ReconstructionError("managed package wheel directory is not private")
    rows = []
    for name, (digest, size) in expected.items():
        member = members[name]
        if (member.get("kind") != "file" or member.get("sha256") != digest
                or (size is not None and member.get("size") != size)
                or int(member.get("mode", 0)) & 0o077):
            raise ReconstructionError("managed package file differs from the reviewed closure")
        rows.append({"path": name, "sha256": "sha256:" + digest, "size": member["size"]})
    return sorted(rows, key=lambda row: row["path"].encode("utf-8"))


def _tree_state(
    host: CoreManagedTrees, target: Path, reviewed: Mapping[str, Any],
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    environment: Mapping[str, str],
) -> tuple[str, str | None, list[dict[str, str]]]:
    rows = [row for row in host.catalog.trees.inventory(workspace=host.workspace)
            if row["path"] == str(target)]
    prior: list[dict[str, str]] = []
    active: list[dict[str, Any]] = []
    for row in rows:
        if row["owner_id"] != "workbench-core" or row["role"] != "artifacts":
            raise ReconstructionError("managed package target has a foreign Core reservation")
        if row["status"] == "failed" and not target.exists() and not target.is_symlink():
            prior.append({"tree_id": str(row["tree_id"]), "status": "failed"})
        else:
            active.append(row)
    if len(active) > 1:
        raise ReconstructionError("managed package target has ambiguous Core reservations")
    if not active:
        if target.exists() or target.is_symlink():
            raise ReconstructionError("managed package target exists outside Core custody")
        return "acquire", None, prior
    row = active[0]
    status, tree_id = str(row["status"]), str(row["tree_id"])
    if status not in {"committed", "published-uncommitted", "incomplete"}:
        raise ReconstructionError(f"managed package tree requires reviewed recovery: {status}")
    try:
        intent = host.catalog.trees.intent(tree_id)
    except ManagedTreeError as exc:
        raise ReconstructionError("managed package stage has no complete publication intent") from exc
    if (intent["domain_id"] != reviewed["plan_id"]
            or intent["policy_id"] != host.policy_id
            or type(intent.get("references")) is not list
            or len(intent["references"]) != 2
            or reviewed["wheel_resource_id"] not in intent["references"]):
        raise ReconstructionError("managed package tree belongs to another review")
    if status == "committed":
        reference = host.describe(tree_id)
        if reference.path != target or reference.policy_id != host.policy_id:
            raise ReconstructionError("managed package tree path or policy changed")
        _tree_files(reference, reviewed)
        _verify_content(
            suite_root, share, candidate, workspace=host.workspace,
            reviewed=reviewed, wheelhouse=reference.path, environment=environment,
        )
        return "reuse", tree_id, prior
    return "reconcile", tree_id, prior


def plan_package_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review source acquisition or verified reuse of one exact closure."""

    reviewed = _reviewed(share, candidate, dict(closure_plan))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    if not local.workspace.is_dir():
        raise ReconstructionError("package import workspace is unavailable")
    if not _tree_host_supported():
        raise ReconstructionError("exact Linux managed-tree publication is unavailable on this host")
    retained = reopen_wheel_import(
        suite_root, share, candidate, workspace=local.workspace,
        result_resource_id=reviewed["wheel_resource_id"], environment=values,
    )
    if retained["tree_id"] != reviewed["wheel_tree_id"]:
        raise ReconstructionError("retained optional wheel tree differs from the closure review")
    host, root, closure_root = _store(local)
    target = closure_root / reviewed["plan_id"].rsplit(":", 1)[-1] / "snapshot"
    _private_store(root, target)
    try:
        action, tree_id, prior = _tree_state(
            host, target, reviewed, Path(suite_root), share, candidate, values,
        )
    except (ManagedTreeError, DurableResourceError, OSError, ValueError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"managed package tree cannot be inventoried: {exc}") from exc
    blockers: list[str] = []
    if sum(row["size"] for row in reviewed["wheels"]) + 2 * 1024 * 1024 > MAX_TOTAL_BYTES:
        blockers.append("complete wheelhouse exceeds Core's exact-tree byte bound")
    source = Path(reviewed["wheelhouse"])
    if action == "acquire":
        if not source.exists() and not source.is_symlink():
            blockers.append("reviewed local wheelhouse is unavailable for acquisition")
        elif not blockers:
            current = plan_package_closure(
                suite_root, share, candidate, workspace=local.workspace,
                wheel_resource_id=reviewed["wheel_resource_id"],
                wheelhouse=source, environment=values,
            )
            if current != reviewed:
                raise ReconstructionError("package closure source changed after review")
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": reviewed["share_id"], "candidate_id": reviewed["candidate_id"],
        "closure_plan_id": reviewed["plan_id"],
        "wheel_resource_id": reviewed["wheel_resource_id"],
        "workspace": str(local.workspace), "environment_resolution_id": local.record["resolution_id"],
        "state_root": str(local.state_root), "target": str(target),
        "source_wheelhouse": str(source) if action == "acquire" else None,
        "action": action, "tree_id": tree_id, "prior_failed_trees": prior,
        "unresolved_inputs": reviewed["unresolved_inputs"], "blockers": blockers,
        "state": "blocked" if blockers else "ready",
    }, "workbench-environment-package-import-plan", "plan_id")


def _copy_file(source: Path, destination: Path, *, digest: str, limit: int) -> None:
    """Copy through a pinned parent; reject a changed source or redirect."""

    try:
        parent = pinned_directory(source.parent, create=False)
        try:
            parent_info = os.fstat(parent)
            visible = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(visible.st_mode) or not 0 < visible.st_size <= limit:
                raise ReconstructionError("package source is not a bounded ordinary file")
            source_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
            try:
                opened = os.fstat(source_fd)
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                if any(getattr(visible, field) != getattr(opened, field) for field in fields):
                    raise ReconstructionError("package source changed before copy")
                output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                observed, size = sha256(), 0
                with os.fdopen(output_fd, "wb") as output:
                    while block := os.read(source_fd, 1024 * 1024):
                        size += len(block)
                        if size > opened.st_size or size > limit:
                            raise ReconstructionError("package source grew during copy")
                        observed.update(block)
                        output.write(block)
                after = os.fstat(source_fd)
                final = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
                if (size != opened.st_size or observed.hexdigest() != digest
                        or any(getattr(opened, field) != getattr(after, field)
                               or getattr(opened, field) != getattr(final, field) for field in fields)):
                    raise ReconstructionError("package source changed during copy")
            finally:
                os.close(source_fd)
        finally:
            os.close(parent)
        reopened = pinned_directory(source.parent, create=False)
        try:
            if (os.fstat(reopened).st_dev, os.fstat(reopened).st_ino) != (parent_info.st_dev, parent_info.st_ino):
                raise ReconstructionError("package source parent changed during copy")
        finally:
            os.close(reopened)
    except (OSError, PinnedDirectoryError) as exc:
        raise ReconstructionError(f"package source cannot be copied safely: {exc}") from exc


def _copy_assembly(stage: Path, source: Path, reviewed: Mapping[str, Any]) -> None:
    stage.mkdir(mode=0o700)
    wheels = stage / "wheels"
    wheels.mkdir(mode=0o700)
    for name, (digest, size) in _expected_members(reviewed).items():
        _copy_file(
            source / name, stage / name, digest=digest,
            limit=size if size is not None else 1024 * 1024,
        )


def _validate_stage(
    stage: Path, suite_root: Path, share: Mapping[str, Any],
    candidate: Mapping[str, Any], reviewed: Mapping[str, Any],
    *, workspace: Path, environment: Mapping[str, str],
) -> None:
    if ({path.name for path in stage.iterdir()}
            != {"wheelhouse.json", "requirements.lock", "wheels"}
            or {path.name for path in (stage / "wheels").iterdir()}
            != {row["filename"] for row in reviewed["wheels"]}):
        raise ReconstructionError("managed package stage contains extra or missing members")
    _verify_content(
        suite_root, share, candidate, workspace=workspace,
        reviewed=reviewed, wheelhouse=stage, environment=environment,
    )


def _result(
    plan: Mapping[str, Any], attempt_resource_id: str,
    reference: ManagedTreeReference, files: list[dict[str, Any]],
) -> dict[str, Any]:
    references = set(reference.references)
    if (len(reference.references) != 2 or len(references) != 2
            or plan["wheel_resource_id"] not in references):
        raise ReconstructionError("managed package tree lacks its input evidence links")
    tree_attempt_resource_id = (references - {plan["wheel_resource_id"]}).pop()
    return {
        "format": RESULT_FORMAT, "schema_version": 1,
        "outcome": ("acquired" if plan["action"] == "acquire" else
                    "reconciled" if plan["action"] == "reconcile" else "reused"),
        "plan_id": plan["plan_id"], "closure_plan_id": plan["closure_plan_id"],
        "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
        "wheel_resource_id": plan["wheel_resource_id"],
        "attempt_resource_id": attempt_resource_id,
        "tree_attempt_resource_id": tree_attempt_resource_id,
        "workspace": plan["workspace"],
        "environment_resolution_id": plan["environment_resolution_id"],
        "tree_id": reference.tree_id, "tree_path": str(reference.path),
        "tree_content_sha256": reference.content_sha256,
        "files": files, "unresolved_inputs": plan["unresolved_inputs"],
        "scope": _SCOPE,
    }


def apply_package_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, expected_plan_id: str,
    workspace: Path | str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain a reviewed closure under Core without invoking pip or venv."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_package_import(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        environment=values,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("package import changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("package import is blocked: " + "; ".join(plan["blockers"]))
    local = resolve_environment(suite_root, workspace=Path(plan["workspace"]), environment=values)
    host, root, _ = _store(local)
    _private_directory(root)
    _private_directory(Path(plan["target"]).parent)
    _private_store(root, Path(plan["target"]))
    lock = root / f".package-import-{plan['closure_plan_id'].rsplit(':', 1)[-1]}.lock"
    with private_record_lock(lock, wait=True):
        current = plan_package_import(
            suite_root, share, candidate, closure_plan, workspace=workspace,
            environment=values,
        )
        if current != plan:
            raise ReconstructionError("package import inputs changed before acquisition")
        service = _resource_host(Path(suite_root), local.workspace, values)
        if service.policy_id != plan["environment_resolution_id"]:
            raise ReconstructionError("package import resolution changed after review")
        prepared = {
            "format": ATTEMPT_FORMAT, "schema_version": 1, "state": "prepared",
            "plan_id": plan["plan_id"], "closure_plan_id": plan["closure_plan_id"],
            "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
            "wheel_resource_id": plan["wheel_resource_id"],
            "action": plan["action"], "target": plan["target"], "tree_id": plan["tree_id"],
            "reviewed_plan": plan,
        }
        attempt_ref = service.publish_bytes(
            "evidence", "environment-package-import-attempt.json",
            _canonical(prepared) + b"\n", domain_id=plan["share_id"],
            references=(plan["wheel_resource_id"],),
        )
        target = Path(plan["target"])
        reviewed = _reviewed(share, candidate, dict(closure_plan))
        if plan["action"] == "acquire":
            try:
                with host.stage("artifacts", target.name, requested_path=target) as stage:
                    _private_store(root, target)
                    _copy_assembly(stage.path, Path(plan["source_wheelhouse"]), reviewed)
                    reference = stage.publish(
                        validate=lambda path: _validate_stage(
                            path, Path(suite_root), share, candidate, reviewed,
                            workspace=local.workspace, environment=values,
                        ),
                        domain_id=reviewed["plan_id"],
                        references=(attempt_ref.resource_id, plan["wheel_resource_id"]),
                        inventory_policy=EXACT_INVENTORY_POLICY,
                    )
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed package tree cannot be published: {exc}") from exc
        else:
            try:
                reference = (host.reconcile(plan["tree_id"])
                             if plan["action"] == "reconcile"
                             else host.describe(plan["tree_id"]))
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed package tree cannot be reopened: {exc}") from exc
        if (reference.path != target or reference.policy_id != host.policy_id
                or reference.workspace != local.workspace
                or len(reference.references) != 2
                or plan["wheel_resource_id"] not in reference.references
                or (plan["action"] == "acquire"
                    and attempt_ref.resource_id not in reference.references)):
            raise ReconstructionError("managed package publication has another local binding")
        files = _tree_files(reference, reviewed)
        _verify_content(
            Path(suite_root), share, candidate, workspace=local.workspace,
            reviewed=reviewed, wheelhouse=reference.path, environment=values,
        )
        result = _result(plan, attempt_ref.resource_id, reference, files)
        payload = _canonical(result) + b"\n"
        completed = service.publish_bytes(
            "evidence", "environment-package-import.json", payload,
            domain_id=plan["share_id"],
            references=(attempt_ref.resource_id, plan["wheel_resource_id"]),
        )
        if service.read_bytes(completed.resource_id) != payload:
            raise ReconstructionError("package import result did not reopen exactly")
        return {**result, "resource": {
            "resource_id": completed.resource_id, "store_id": completed.store_id,
            "path": str(completed.path), "sha256": completed.sha256,
        }}


def reopen_package_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    result_resource_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Reopen the exact retained assembly without its original source path."""

    reviewed = _reviewed(share, candidate, dict(closure_plan))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    if not _tree_host_supported():
        raise ReconstructionError("exact Linux managed-tree reopening is unavailable on this host")
    host, root, closure_root = _store(local)
    _private_store(root)
    service = _resource_host(Path(suite_root), local.workspace, values)
    try:
        ref = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        receipt = json.loads(payload.decode("utf-8"))
        attempt_ref = service.describe(receipt["attempt_resource_id"])
        attempt_raw = service.read_bytes(receipt["attempt_resource_id"])
        attempt = json.loads(attempt_raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"package import result cannot be reopened: {exc}") from exc
    target = closure_root / reviewed["plan_id"].rsplit(":", 1)[-1] / "snapshot"
    if (ref.owner_id != "workbench-core" or ref.role != "evidence"
            or ref.domain_id != reviewed["share_id"]
            or type(receipt) is not dict or payload != _canonical(receipt) + b"\n"
            or receipt.get("format") != RESULT_FORMAT or receipt.get("schema_version") != 1
            or receipt.get("closure_plan_id") != reviewed["plan_id"]
            or receipt.get("share_id") != reviewed["share_id"]
            or receipt.get("candidate_id") != reviewed["candidate_id"]
            or receipt.get("wheel_resource_id") != reviewed["wheel_resource_id"]
            or receipt.get("workspace") != str(local.workspace)
            or receipt.get("environment_resolution_id") != local.record["resolution_id"]
            or receipt.get("tree_path") != str(target)
            or receipt.get("unresolved_inputs") != reviewed["unresolved_inputs"]
            or attempt_ref.owner_id != "workbench-core" or attempt_ref.role != "evidence"
            or attempt_ref.domain_id != reviewed["share_id"]
            or type(attempt) is not dict
            or attempt_raw != _canonical(attempt) + b"\n"
            or attempt.get("format") != ATTEMPT_FORMAT
            or attempt.get("schema_version") != 1
            or attempt.get("state") != "prepared"
            or attempt.get("plan_id") != receipt.get("plan_id")
            or attempt.get("closure_plan_id") != reviewed["plan_id"]
            or attempt.get("share_id") != reviewed["share_id"]
            or attempt.get("candidate_id") != reviewed["candidate_id"]
            or attempt.get("wheel_resource_id") != reviewed["wheel_resource_id"]
            or attempt.get("target") != str(target)):
        raise ReconstructionError("package import result has another identity or scope")
    plan = attempt.get("reviewed_plan")
    if (type(plan) is not dict or plan.get("plan_id") != receipt["plan_id"]
            or plan != _seal(
                {key: value for key, value in plan.items() if key != "plan_id"},
                "workbench-environment-package-import-plan", "plan_id",
            )
            or plan.get("format") != PLAN_FORMAT or plan.get("schema_version") != 1
            or plan.get("state") != "ready" or plan.get("blockers") != []
            or plan.get("share_id") != reviewed["share_id"]
            or plan.get("candidate_id") != reviewed["candidate_id"]
            or plan.get("closure_plan_id") != reviewed["plan_id"]
            or plan.get("wheel_resource_id") != reviewed["wheel_resource_id"]
            or plan.get("workspace") != str(local.workspace)
            or plan.get("environment_resolution_id") != local.record["resolution_id"]
            or plan.get("target") != str(target)
            or plan.get("unresolved_inputs") != reviewed["unresolved_inputs"]
            or plan.get("action") not in {"acquire", "reconcile", "reuse"}
            or (plan.get("action") == "acquire"
                and plan.get("source_wheelhouse") != reviewed["wheelhouse"])
            or (plan.get("action") != "acquire" and plan.get("source_wheelhouse") is not None)
            or attempt.get("action") != plan["action"]
            or attempt.get("tree_id") != plan["tree_id"]):
        raise ReconstructionError("package import prepared review differs from its result")
    retained = reopen_wheel_import(
        suite_root, share, candidate, workspace=local.workspace,
        result_resource_id=reviewed["wheel_resource_id"], environment=values,
    )
    if retained["tree_id"] != reviewed["wheel_tree_id"]:
        raise ReconstructionError("retained optional wheel tree differs from the closure review")
    try:
        reference = host.describe(receipt["tree_id"])
        files = _tree_files(reference, reviewed)
    except (ManagedTreeError, OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"managed package tree cannot be reopened: {exc}") from exc
    if (reference.path != target or reference.policy_id != host.policy_id
            or receipt != _result(plan, receipt["attempt_resource_id"], reference, files)):
        raise ReconstructionError("package import result differs from its managed tree")
    try:
        tree_attempt_ref = service.describe(receipt["tree_attempt_resource_id"])
        tree_attempt_raw = service.read_bytes(receipt["tree_attempt_resource_id"])
        tree_attempt = json.loads(tree_attempt_raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"package tree's prepared attempt cannot be reopened: {exc}") from exc
    if (tree_attempt_ref.owner_id != "workbench-core"
            or tree_attempt_ref.role != "evidence"
            or tree_attempt_ref.domain_id != reviewed["share_id"]
            or type(tree_attempt) is not dict
            or tree_attempt_raw != _canonical(tree_attempt) + b"\n"
            or tree_attempt.get("format") != ATTEMPT_FORMAT
            or tree_attempt.get("schema_version") != 1
            or tree_attempt.get("state") != "prepared"
            or tree_attempt.get("action") != "acquire"
            or tree_attempt.get("closure_plan_id") != reviewed["plan_id"]
            or tree_attempt.get("share_id") != reviewed["share_id"]
            or tree_attempt.get("candidate_id") != reviewed["candidate_id"]
            or tree_attempt.get("wheel_resource_id") != reviewed["wheel_resource_id"]
            or tree_attempt.get("target") != str(target)):
        raise ReconstructionError("package tree has another prepared acquisition")
    _verify_content(
        Path(suite_root), share, candidate, workspace=local.workspace,
        reviewed=reviewed, wheelhouse=reference.path, environment=values,
    )
    return receipt


__all__ = ["plan_package_import", "apply_package_import", "reopen_package_import"]
