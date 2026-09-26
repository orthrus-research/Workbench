"""Core custody of an admitted platform owner's exact fixture and tool bytes.

The profile validates its source and lock. Core copies only the lock's declared
files and the candidate's complete three- or six-witness set into one recoverable
managed tree. This is input custody, not execution or reconstruction.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping

from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference
from workbench_api.profile_extensions import ProfileExtensionError, require_profile_extension

from .durable_records import private_record_lock
from .durable_files import _directory as pinned_directory
from .environment_input_candidates import (
    _ordinary_source, _profile_fixture_candidate, validate_input_candidate,
)
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _host_variant,
    _resource_host, _seal, validate_share,
)
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .runtime_java import JavaRuntimeError, host_platform
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-fixture-import-plan-v1"
RESULT_FORMAT = "workbench-environment-fixture-import-result-v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_WITNESS_BYTES = 2 * 1024 * 1024
_MAX_FILES = 1000
_MAX_TOTAL_BYTES = 256 * 1024 * 1024


def _store(local: Any) -> tuple[CoreManagedTrees, Path, Path]:
    root = local.state_root / "environment-inputs"
    host = CoreManagedTrees(
        workspace=local.workspace, configuration_home=local.configuration_home,
        locations={"artifacts": root}, owner_id="workbench-core",
        policy_id=local.record["resolution_id"],
        location_sources={"artifacts": "core-managed-input"},
    )
    return host, root, root / "fixtures"


def _private_store(root: Path, target: Path | None = None) -> None:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("managed fixture input traverses a redirect")
    for path in (root.parent, root, root / "fixtures", *((target.parent,) if target else ())):
        if (path.exists() or path.is_symlink()) and not private_path(path, directory=True):
            raise ReconstructionError("managed fixture input store is not owner-private")


def _relative(value: object) -> PurePosixPath:
    if type(value) is not str or value in {"", "."} or "\\" in value or "\0" in value:
        raise ReconstructionError("fixture owner lock has an unsafe source path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts)):
        raise ReconstructionError("fixture owner lock has an unsafe source path")
    return path


def _read_source(path: Path, *, limit: int) -> bytes:
    """Read an owner file through a pinned, no-follow parent directory."""

    _ordinary_source(path)
    try:
        parent = pinned_directory(path.parent, create=False)
        try:
            parent_identity = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
            visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(visible.st_mode) or visible.st_size > limit:
                raise ReconstructionError("fixture owner source is not a bounded ordinary file")
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path.name, flags, dir_fd=parent)
            try:
                opened = os.fstat(descriptor)
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                if (not stat.S_ISREG(opened.st_mode)
                        or any(getattr(visible, field) != getattr(opened, field) for field in fields)):
                    raise ReconstructionError("fixture owner source changed before reading")
                chunks = []
                remaining = limit + 1
                while remaining:
                    block = os.read(descriptor, min(1024 * 1024, remaining))
                    if not block:
                        break
                    chunks.append(block)
                    remaining -= len(block)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            raw = b"".join(chunks)
            if (len(raw) != opened.st_size or len(raw) > limit
                    or any(getattr(opened, field) != getattr(after, field)
                           or getattr(opened, field) != getattr(final, field) for field in fields)):
                raise ReconstructionError("fixture owner source changed during reading")
        finally:
            os.close(parent)
        _ordinary_source(path)
        reopened = pinned_directory(path.parent, create=False)
        try:
            if (os.fstat(reopened).st_dev, os.fstat(reopened).st_ino) != parent_identity:
                raise ReconstructionError("fixture owner source parent changed during reading")
        finally:
            os.close(reopened)
        return raw
    except (OSError, DurableResourceError) as exc:
        raise ReconstructionError(f"fixture owner source cannot be read safely: {exc}") from exc


def _locked_files(lock: object, fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    if type(lock) is not dict or lock.get("declaration_id") != fixture["declaration_id"]:
        raise ReconstructionError("fixture owner lock declaration differs from the candidate")
    declared = lock.get("declared_values")
    if (type(declared) is not dict or declared.get("tree_digest") != fixture["tree_digest"]
            or type(declared.get("identity")) is not dict
            or declared["identity"].get("digest") != fixture["tree_digest"]
            or declared.get("tree_digest_algorithm") != "sha256-file-tree-v1"):
        raise ReconstructionError("fixture owner lock tree identity differs from the candidate")
    rows = declared.get("files")
    if type(rows) is not list or not 1 <= len(rows) <= _MAX_FILES:
        raise ReconstructionError("fixture owner lock has no bounded file manifest")
    total = 0
    for row in rows:
        if (type(row) is not dict or set(row) != {"path", "sha256", "size"}
                or type(row["sha256"]) is not str or _DIGEST.fullmatch(row["sha256"]) is None
                or type(row["size"]) is not int or not 0 <= row["size"] <= _MAX_FILE_BYTES):
            raise ReconstructionError("fixture owner lock has an invalid file row")
        _relative(row["path"])
        total += row["size"]
    if (total > _MAX_TOTAL_BYTES
            or rows != sorted(rows, key=lambda row: row["path"].encode("utf-8"))
            or len({row["path"] for row in rows}) != len(rows)):
        raise ReconstructionError("fixture owner lock file manifest is ambiguous or too large")
    digest = "sha256:" + sha256(_canonical({
        "algorithm": "sha256-file-tree-v1", "files": rows,
    })).hexdigest()
    if digest != fixture["tree_digest"]:
        raise ReconstructionError("fixture owner lock file manifest digest changed")
    return rows


def _expected_files(lock: object, fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    witnesses = fixture["sources"]
    lock_rows = [row for row in witnesses if row["kind"] == "fixture-owner-lock"]
    if len(lock_rows) != 1:
        raise ReconstructionError("fixture candidate has no owner lock witness")
    parent = PurePosixPath(lock_rows[0]["relative_path"]).parent
    expected = [
        {"relative_path": row["relative_path"], "sha256": row["sha256"],
         "size": row["size"], "kind": row["kind"]}
        for row in witnesses
    ]
    for row in _locked_files(lock, fixture):
        expected.append({
            "relative_path": (parent / _relative(row["path"])).as_posix(),
            "sha256": row["sha256"], "size": row["size"],
            "kind": "fixture-source",
        })
    if len({row["relative_path"] for row in expected}) != len(expected):
        raise ReconstructionError("fixture owner lock overlaps a witness path")
    return sorted(expected, key=lambda row: row["relative_path"].encode("utf-8"))


def _expected_directories(files: list[dict[str, Any]]) -> set[str]:
    directories: set[str] = set()
    for row in files:
        parent = PurePosixPath(row["relative_path"]).parent
        while str(parent) != ".":
            directories.add(str(parent))
            parent = parent.parent
    return directories


def _source_snapshot(share: Mapping[str, Any], fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    platform = share["lock"]["platform_profile"]
    current = _profile_fixture_candidate(
        fixture["owner_profile_id"], platform_document_id=platform["profile_id"],
        platform_document_sha256=platform["sha256"],
    )
    if current != fixture:
        raise ReconstructionError("admitted fixture owner differs from the reviewed candidate")
    try:
        owner = require_profile_extension("workbench.workspace_home_fixtures", fixture["owner_profile_id"])
        lock = owner.read_owner_lock()
        if owner.validate_owner_lock(lock) != lock:
            raise ReconstructionError("fixture owner refused its source tree")
        source_root = owner.fixture_root()
        _ordinary_source(source_root)
        if not source_root.is_dir():
            raise ReconstructionError("fixture owner source root is unavailable")
        inputs = owner.source_inputs()
        if type(inputs) is not tuple:
            raise ReconstructionError("fixture owner source witnesses are unavailable")
        by_kind = {row["kind"]: row for row in inputs}
        if len(by_kind) != len(inputs) or set(by_kind) != {row["kind"] for row in fixture["sources"]}:
            raise ReconstructionError("fixture owner witnesses changed")
        lock_input = by_kind["fixture-owner-lock"]
        if lock_input["path"].parent != source_root:
            raise ReconstructionError("fixture owner lock is outside its source root")
        expected = _expected_files(lock, fixture)
        rows: list[dict[str, Any]] = []
        for item in expected:
            if item["kind"] == "fixture-source":
                lock_parent = PurePosixPath(lock_input["display_path"]).parent
                relative = PurePosixPath(item["relative_path"]).relative_to(lock_parent)
                path = source_root.joinpath(*relative.parts)
            else:
                record = by_kind[item["kind"]]
                if record["display_path"] != item["relative_path"]:
                    raise ReconstructionError("fixture owner witness path changed")
                path = record["path"]
            raw = _read_source(
                path, limit=_MAX_FILE_BYTES if item["kind"] == "fixture-source"
                else _MAX_WITNESS_BYTES,
            )
            if len(raw) != item["size"] or "sha256:" + sha256(raw).hexdigest() != item["sha256"]:
                raise ReconstructionError("fixture owner source bytes differ from the reviewed lock")
            rows.append({**item, "path": str(path)})
        if owner.validate_owner_lock(owner.read_owner_lock()) != lock:
            raise ReconstructionError("fixture owner changed during source inspection")
        if _profile_fixture_candidate(
            fixture["owner_profile_id"], platform_document_id=platform["profile_id"],
            platform_document_sha256=platform["sha256"],
        ) != fixture:
            raise ReconstructionError("fixture owner identity changed during source inspection")
        return rows
    except (ProfileExtensionError, OSError, ValueError, AttributeError, KeyError, TypeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"fixture owner cannot provide exact source bytes: {exc}") from exc


def _tree_files(reference: ManagedTreeReference, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (reference.owner_id != "workbench-core" or reference.role != "artifacts"
            or reference.domain_id != candidate["candidate_id"]
            or reference.derived_status != "current"):
        raise ReconstructionError("managed fixture tree has another identity or changed members")
    fixture = candidate["profile_fixture"]
    lock_row = next(row for row in fixture["sources"] if row["kind"] == "fixture-owner-lock")
    try:
        lock_path = reference.path.joinpath(*PurePosixPath(lock_row["relative_path"]).parts)
        raw = _read_source(lock_path, limit=_MAX_WITNESS_BYTES)
        if len(raw) != lock_row["size"] or "sha256:" + sha256(raw).hexdigest() != lock_row["sha256"]:
            raise ReconstructionError("retained fixture owner lock bytes changed")
        lock = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"retained fixture owner lock cannot be read: {exc}") from exc
    expected = _expected_files(lock, fixture)
    members = {row["path"]: row for row in reference.members}
    directories = _expected_directories(expected)
    if set(members) != {row["relative_path"] for row in expected} | directories:
        raise ReconstructionError("managed fixture tree contains missing or extra members")
    for row in expected:
        member = members[row["relative_path"]]
        if (member.get("kind") != "file" or member.get("size") != row["size"]
                or "sha256:" + str(member.get("sha256")) != row["sha256"]):
            raise ReconstructionError("managed fixture source bytes differ from the owner lock")
    if any(members[path].get("kind") != "directory" for path in directories):
        raise ReconstructionError("managed fixture tree directory changed type")
    return expected


def _tree_state(host: CoreManagedTrees, target: Path, candidate: Mapping[str, Any]) -> tuple[str, str | None, list[dict[str, str]]]:
    rows = [row for row in host.catalog.trees.inventory(workspace=host.workspace)
            if row["path"] == str(target)]
    prior: list[dict[str, str]] = []
    active = []
    for row in rows:
        if row["owner_id"] != "workbench-core" or row["role"] != "artifacts":
            raise ReconstructionError("managed fixture target has a foreign Core reservation")
        if row["status"] == "failed" and not target.exists() and not target.is_symlink():
            prior.append({"tree_id": str(row["tree_id"]), "status": "failed"})
        else:
            active.append(row)
    if len(active) > 1:
        raise ReconstructionError("managed fixture target has ambiguous Core reservations")
    if not active:
        if target.exists() or target.is_symlink():
            raise ReconstructionError("managed fixture target exists outside completed Core custody")
        return "acquire", None, prior
    row = active[0]
    status, tree_id = str(row["status"]), str(row["tree_id"])
    if status not in {"committed", "published-uncommitted", "incomplete"}:
        raise ReconstructionError(f"managed fixture tree requires reviewed recovery: {status}")
    try:
        intent = host.catalog.trees.intent(tree_id)
    except ManagedTreeError as exc:
        raise ReconstructionError("managed fixture stage has no complete publication intent") from exc
    if intent["domain_id"] != candidate["candidate_id"] or intent["policy_id"] != host.policy_id:
        raise ReconstructionError("managed fixture tree belongs to another reviewed input")
    if status == "committed":
        reference = host.describe(tree_id)
        if reference.path != target or reference.policy_id != host.policy_id:
            raise ReconstructionError("managed fixture tree path or policy changed")
        _tree_files(reference, candidate)
        return "reuse", tree_id, prior
    return "reconcile", tree_id, prior


def plan_fixture_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review the admitted owner source or an existing Core fixture snapshot."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("fixture import requires a V3 environment share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root, fixture_root = _store(local)
    target = fixture_root / reviewed["candidate_id"].rsplit(":", 1)[-1] / "snapshot"
    blockers = []
    if not local.workspace.is_dir():
        blockers.append("selected workspace directory is missing")
    supported = _tree_host_supported()
    if not supported:
        blockers.append("exact Linux managed-tree publication is unavailable on this host")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        executing_host = None
        blockers.append(f"fixture import has no supported executing host: {exc}")
    if executing_host is not None and executing_host != reviewed["host_variant"]:
        blockers.append("fixture candidate targets another executing host")
    if supported:
        _private_store(root, target)
        try:
            action, tree_id, prior = _tree_state(host, target, reviewed)
        except (ManagedTreeError, DurableResourceError, OSError, ValueError) as exc:
            if isinstance(exc, ReconstructionError):
                raise
            raise ReconstructionError(f"managed fixture tree cannot be inventoried: {exc}") from exc
    else:
        action, tree_id, prior = "unsupported", None, []
    sources = _source_snapshot(portable, reviewed["profile_fixture"]) if action == "acquire" and not blockers else []
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "workspace": str(local.workspace), "environment_resolution_id": local.record["resolution_id"],
        "state_root": str(local.state_root), "target": str(target),
        "host_variant": reviewed["host_variant"], "profile_fixture": reviewed["profile_fixture"],
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "sources": sources, "action": action, "tree_id": tree_id,
        "prior_failed_trees": prior, "blockers": blockers,
        "state": "blocked" if blockers else "ready",
    }, "workbench-environment-fixture-import-plan", "plan_id")


def _copy_sources(stage: Path, sources: list[dict[str, Any]]) -> None:
    stage.mkdir(mode=0o700)
    for row in sources:
        source = Path(row["path"])
        destination = stage.joinpath(*PurePosixPath(row["relative_path"]).parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = _read_source(
            source, limit=_MAX_FILE_BYTES if row["kind"] == "fixture-source"
            else _MAX_WITNESS_BYTES,
        )
        if len(raw) != row["size"] or "sha256:" + sha256(raw).hexdigest() != row["sha256"]:
            raise ReconstructionError("fixture owner source bytes changed after review")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)


def apply_fixture_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    expected_plan_id: str, workspace: Path | str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain exact fixture inputs with prepared and completed Core evidence."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_import(suite_root, share, candidate, workspace=workspace, environment=values)
    if type(expected_plan_id) is not str or plan["plan_id"] != expected_plan_id:
        raise ReconstructionError("fixture import changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("fixture import is blocked: " + "; ".join(plan["blockers"]))
    local = resolve_environment(suite_root, workspace=Path(plan["workspace"]), environment=values)
    host, root, _ = _store(local)
    _private_directory(root)
    _private_directory(Path(plan["target"]).parent)
    _private_store(root, Path(plan["target"]))
    lock = root / f".fixture-import-{plan['candidate_id'].rsplit(':', 1)[-1]}.lock"
    with private_record_lock(lock, wait=True):
        current = plan_fixture_import(suite_root, share, candidate, workspace=workspace, environment=values)
        if current != plan:
            raise ReconstructionError("fixture import inputs changed before acquisition")
        service = _resource_host(Path(suite_root), local.workspace, values)
        if service.policy_id != plan["environment_resolution_id"]:
            raise ReconstructionError("fixture import resolution changed after review")
        prepared = {
            "format": "workbench-environment-fixture-import-attempt-v1",
            "schema_version": 1, "state": "prepared", "plan_id": plan["plan_id"],
            "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
            "action": plan["action"], "target": plan["target"], "tree_id": plan["tree_id"],
        }
        prepared_ref = service.publish_bytes(
            "evidence", "environment-fixture-import-attempt.json",
            _canonical(prepared) + b"\n", domain_id=plan["share_id"],
        )
        target = Path(plan["target"])
        if plan["action"] == "acquire":
            try:
                with host.stage("artifacts", target.name, requested_path=target) as stage:
                    _private_store(root, target)
                    _copy_sources(stage.path, plan["sources"])
                    reference = stage.publish(
                        validate=lambda path: _validate_stage(path, plan["sources"], candidate),
                        domain_id=plan["candidate_id"],
                        references=(prepared_ref.resource_id,),
                        inventory_policy=EXACT_INVENTORY_POLICY,
                    )
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed fixture tree cannot be published: {exc}") from exc
        else:
            try:
                reference = (host.reconcile(plan["tree_id"]) if plan["action"] == "reconcile"
                             else host.describe(plan["tree_id"]))
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed fixture tree cannot be reopened: {exc}") from exc
        if (reference.path != target or reference.policy_id != host.policy_id
                or reference.workspace != local.workspace):
            raise ReconstructionError("managed fixture publication has another local binding")
        files = _tree_files(reference, candidate)
        result = {
            "format": RESULT_FORMAT, "schema_version": 1,
            "outcome": ("acquired" if plan["action"] == "acquire" else
                        "reconciled" if plan["action"] == "reconcile" else "reused"),
            "plan_id": plan["plan_id"], "share_id": plan["share_id"],
            "candidate_id": plan["candidate_id"],
            "attempt_resource_id": prepared_ref.resource_id,
            "workspace": plan["workspace"],
            "environment_resolution_id": plan["environment_resolution_id"],
            "tree_id": reference.tree_id, "tree_path": str(reference.path),
            "tree_content_sha256": reference.content_sha256,
            "profile_fixture": plan["profile_fixture"], "files": files,
            "unresolved_inputs": plan["unresolved_inputs"],
            "scope": "Exact profile fixture and preflight tool bytes only; execution and reconstruction remain unresolved.",
        }
        payload = _canonical(result) + b"\n"
        completed = service.publish_bytes(
            "evidence", "environment-fixture-import.json", payload,
            domain_id=plan["share_id"], references=(prepared_ref.resource_id,),
        )
        if service.read_bytes(completed.resource_id) != payload:
            raise ReconstructionError("fixture import result did not reopen exactly")
        return {**result, "resource": {
            "resource_id": completed.resource_id, "store_id": completed.store_id,
            "path": str(completed.path), "sha256": completed.sha256,
        }}


def _validate_stage(stage: Path, sources: list[dict[str, Any]], candidate: Mapping[str, Any]) -> None:
    from .storage.exact_tree_inventory import inventory_exact_members

    members, _, _, _ = inventory_exact_members(stage)
    expected = {row["relative_path"]: row for row in sources}
    observed = {str(row["path"]): row for row in members if row["kind"] == "file"}
    observed_directories = {str(row["path"]) for row in members if row["kind"] == "directory"}
    if set(observed) != set(expected) or observed_directories != _expected_directories(sources):
        raise ReconstructionError("managed fixture stage has missing or extra members")
    for name, row in expected.items():
        member = observed[name]
        if (member["size"] != row["size"] or "sha256:" + str(member["sha256"]) != row["sha256"]):
            raise ReconstructionError("managed fixture stage differs from the owner lock")
    fixture = candidate["profile_fixture"]
    lock_row = next(row for row in sources if row["kind"] == "fixture-owner-lock")
    raw = _read_source(stage.joinpath(*PurePosixPath(lock_row["relative_path"]).parts),
                       limit=_MAX_WITNESS_BYTES)
    if _expected_files(json.loads(raw.decode("utf-8")), fixture) != [
        {key: item[key] for key in ("relative_path", "sha256", "size", "kind")}
        for item in sources
    ]:
        raise ReconstructionError("managed fixture stage lock differs from the reviewed source")


def reopen_fixture_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Reopen retained bytes and the result without original owner source files."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    if not _tree_host_supported():
        raise ReconstructionError("exact Linux managed-tree reopening is unavailable on this host")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        raise ReconstructionError(f"fixture import has no supported executing host: {exc}") from exc
    if executing_host != reviewed["host_variant"]:
        raise ReconstructionError("fixture candidate targets another executing host")
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
        raise ReconstructionError(f"fixture import result cannot be reopened: {exc}") from exc
    if (receipt_ref.owner_id != "workbench-core" or receipt_ref.role != "evidence"
            or receipt_ref.domain_id != portable["share_id"]
            or type(receipt) is not dict or payload != _canonical(receipt) + b"\n"
            or receipt.get("format") != RESULT_FORMAT or receipt.get("schema_version") != 1
            or receipt.get("share_id") != portable["share_id"]
            or receipt.get("candidate_id") != reviewed["candidate_id"]
            or receipt.get("workspace") != str(local.workspace)
            or receipt.get("environment_resolution_id") != local.record["resolution_id"]
            or receipt.get("profile_fixture") != reviewed["profile_fixture"]
            or receipt.get("unresolved_inputs") != portable["lock"]["unresolved_inputs"]):
        raise ReconstructionError("fixture import result has another identity or scope")
    target = root / "fixtures" / reviewed["candidate_id"].rsplit(":", 1)[-1] / "snapshot"
    try:
        reference = host.describe(receipt["tree_id"])
        files = _tree_files(reference, reviewed)
    except (ManagedTreeError, OSError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"managed fixture result tree cannot be reopened: {exc}") from exc
    if (reference.path != target or reference.policy_id != host.policy_id
            or receipt.get("tree_path") != str(target)
            or receipt.get("tree_content_sha256") != reference.content_sha256
            or receipt.get("files") != files):
        raise ReconstructionError("fixture import result differs from its managed tree")
    return receipt


__all__ = ["plan_fixture_import", "apply_fixture_import", "reopen_fixture_import"]
