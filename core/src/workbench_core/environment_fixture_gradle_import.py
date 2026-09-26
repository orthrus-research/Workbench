"""Core custody of one exact local Gradle archive selected by a fixture policy.

This retains and reopens archive bytes. It does not download, extract, run,
or qualify a Gradle distribution or reconstruct the environment.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Mapping
from zipfile import BadZipFile, LargeZipFile, ZipFile

from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .durable_files import _directory as pinned_directory
from .durable_records import private_record_lock
from .environment_fixture_execution_policy import review_fixture_execution_policy
from .environment_fixture_import import _ordinary_source
from .environment_input_candidates import validate_input_candidate
from .environment_package_install_plan import _SUPPORTED_FILESYSTEMS, _mount_type
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _host_variant,
    _resource_host, _seal, validate_share,
)
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported
from .host_filesystem import private_path
from .managed_trees import CoreManagedTrees
from .output_routing import _private_directory
from .runtime_java import JavaRuntimeError, _zip_member_path, host_platform
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-fixture-gradle-import-plan-v1"
RESULT_FORMAT = "workbench-environment-fixture-gradle-import-result-v1"
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_MEMBERS = 100_000
_MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")


def _qualified_filesystem(path: Path) -> bool:
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    return existing.is_dir() and _mount_type(existing) in _SUPPORTED_FILESYSTEMS


def _private_store(root: Path, target: Path | None = None) -> None:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("managed Gradle archive store traverses a redirect")
    for path in (root.parent, root, root / "gradle", *((target.parent,) if target else ())):
        if (path.exists() or path.is_symlink()) and not private_path(path, directory=True):
            raise ReconstructionError("managed Gradle archive store is not owner-private")


def _store(local: Any) -> tuple[CoreManagedTrees, Path]:
    root = local.state_root / "environment-inputs"
    host = CoreManagedTrees(
        workspace=local.workspace, configuration_home=local.configuration_home,
        locations={"artifacts": root}, owner_id="workbench-core",
        policy_id=local.record["resolution_id"],
        location_sources={"artifacts": "core-managed-input"},
    )
    return host, root


def _zip_index(source: Any, archive_root: str) -> dict[str, Any]:
    try:
        with ZipFile(source) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_MEMBERS:
                raise ReconstructionError("Gradle ZIP has an invalid member count")
            if sum(info.file_size for info in infos) > _MAX_EXPANDED_BYTES:
                raise ReconstructionError("Gradle ZIP exceeds the expansion bound")
            names: set[str] = set()
            files: set[str] = set()
            rows: list[dict[str, Any]] = []
            expanded = 0
            for info in infos:
                member = _zip_member_path(info)
                name = member.as_posix()
                if (name != info.filename.rstrip("/") or member.parts[0] != archive_root
                        or any(part in {"", ".", ".."} for part in info.filename.split("/")[:-1])):
                    raise ReconstructionError("Gradle ZIP has an unsafe or foreign root member")
                key = name.casefold()
                if key in names:
                    raise ReconstructionError("Gradle ZIP repeats a case-folded member")
                names.add(key)
                kind = "directory" if info.is_dir() else "file"
                if kind == "file":
                    files.add(name)
                    count = 0
                    with archive.open(info) as content:
                        while block := content.read(1024 * 1024):
                            count += len(block)
                            if count > info.file_size:
                                raise ReconstructionError("Gradle ZIP member exceeds its declared size")
                    if count != info.file_size:
                        raise ReconstructionError("Gradle ZIP member has an incomplete payload")
                    expanded += count
                elif info.file_size != 0 or info.CRC != 0:
                    raise ReconstructionError("Gradle ZIP directory has a payload")
                rows.append({
                    "path": name, "kind": kind, "size": info.file_size,
                    "crc32": f"{info.CRC:08x}", "compression": info.compress_type,
                    "mode": stat.S_IMODE(info.external_attr >> 16),
                })
            for name in files:
                parent = PurePosixPath(name).parent
                while str(parent) != ".":
                    if str(parent) in files:
                        raise ReconstructionError("Gradle ZIP has a file ancestor")
                    parent = parent.parent
            if f"{archive_root}/bin/gradle" not in files:
                raise ReconstructionError("Gradle ZIP lacks its launcher")
            return {
                "member_count": len(rows), "expanded_bytes": expanded,
                "member_index_sha256": "sha256:" + sha256(_canonical(sorted(
                    rows, key=lambda row: row["path"].encode("utf-8"),
                ))).hexdigest(),
            }
    except (BadZipFile, LargeZipFile, JavaRuntimeError, OSError, RuntimeError,
            NotImplementedError) as exc:
        raise ReconstructionError(f"Gradle archive is not a safe complete ZIP: {exc}") from exc


def _snapshot_archive(
    path: Path, gradle: Mapping[str, Any], *, destination: Path | None = None,
) -> dict[str, Any]:
    """Stream exact bytes through a pinned parent and check every ZIP CRC."""

    _ordinary_source(path)
    if not path.is_absolute() or path.name != f"gradle-{gradle['version']}-bin.zip":
        raise ReconstructionError("selected Gradle ZIP path has another archive name")
    parent = None
    try:
        parent = pinned_directory(path.parent, create=False)
        parent_identity = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(visible.st_mode)
                or visible.st_size != gradle["archive_size"]
                or not 0 < visible.st_size <= _MAX_ARCHIVE_BYTES):
            raise ReconstructionError("selected Gradle ZIP is not an exact bounded file")
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode)
                    or any(getattr(visible, field) != getattr(opened, field)
                           for field in _STAT_FIELDS)):
                raise ReconstructionError("selected Gradle ZIP changed before reading")
            output = None
            if destination is not None:
                output = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                )
            try:
                digest = sha256()
                size = 0
                while block := os.read(descriptor, 1024 * 1024):
                    size += len(block)
                    if size > gradle["archive_size"]:
                        raise ReconstructionError("selected Gradle ZIP grew after review")
                    digest.update(block)
                    if output is not None:
                        remaining = memoryview(block)
                        while remaining:
                            written = os.write(output, remaining)
                            if written <= 0:
                                raise ReconstructionError("managed Gradle ZIP copy did not advance")
                            remaining = remaining[written:]
                if (size != gradle["archive_size"]
                        or "sha256:" + digest.hexdigest() != gradle["archive_sha256"]):
                    raise ReconstructionError("selected Gradle ZIP bytes differ from the profile policy")
                os.lseek(descriptor, 0, os.SEEK_SET)
                with os.fdopen(os.dup(descriptor), "rb") as source:
                    index = _zip_index(source, gradle["archive_root"])
                if output is not None:
                    os.fsync(output)
                after = os.fstat(descriptor)
            finally:
                if output is not None:
                    os.close(output)
            final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if any(getattr(opened, field) != getattr(after, field)
                   or getattr(opened, field) != getattr(final, field)
                   for field in _STAT_FIELDS):
                raise ReconstructionError("selected Gradle ZIP changed during inspection")
        finally:
            os.close(descriptor)
    except (OSError, DurableResourceError) as exc:
        raise ReconstructionError(f"selected Gradle ZIP cannot be read safely: {exc}") from exc
    finally:
        if parent is not None:
            os.close(parent)
    _ordinary_source(path)
    reopened = pinned_directory(path.parent, create=False)
    try:
        if (os.fstat(reopened).st_dev, os.fstat(reopened).st_ino) != parent_identity:
            raise ReconstructionError("selected Gradle ZIP parent changed during inspection")
    finally:
        os.close(reopened)
    return {"sha256": gradle["archive_sha256"], "size": size, **index}


def _tree_archive(
    reference: ManagedTreeReference, target: Path, review_id: str,
    gradle: Mapping[str, Any],
) -> dict[str, Any]:
    filename = f"gradle-{gradle['version']}-bin.zip"
    if (reference.owner_id != "workbench-core" or reference.role != "artifacts"
            or reference.domain_id != review_id or reference.path != target
            or reference.derived_status != "current" or len(reference.members) != 1):
        raise ReconstructionError("managed Gradle archive tree has another identity or members")
    member = reference.members[0]
    if (member.get("path") != filename or member.get("kind") != "file"
            or member.get("size") != gradle["archive_size"]
            or "sha256:" + str(member.get("sha256")) != gradle["archive_sha256"]):
        raise ReconstructionError("managed Gradle archive differs from the profile policy")
    return _snapshot_archive(target / filename, gradle)


def _tree_state(
    host: CoreManagedTrees, target: Path, review_id: str,
    gradle: Mapping[str, Any],
) -> tuple[str, str | None, list[dict[str, str]]]:
    rows = [row for row in host.catalog.trees.inventory(workspace=host.workspace)
            if row["path"] == str(target)]
    prior: list[dict[str, str]] = []
    active = []
    for row in rows:
        if row["owner_id"] != "workbench-core" or row["role"] != "artifacts":
            raise ReconstructionError("managed Gradle target has a foreign Core reservation")
        if row["status"] == "failed" and not target.exists() and not target.is_symlink():
            prior.append({"tree_id": str(row["tree_id"]), "status": "failed"})
        else:
            active.append(row)
    if len(active) > 1:
        raise ReconstructionError("managed Gradle target has ambiguous Core reservations")
    if not active:
        if target.exists() or target.is_symlink():
            raise ReconstructionError("managed Gradle target exists outside Core custody")
        return "acquire", None, prior
    row = active[0]
    status, tree_id = str(row["status"]), str(row["tree_id"])
    if status not in {"committed", "published-uncommitted", "incomplete"}:
        raise ReconstructionError(f"managed Gradle tree requires reviewed recovery: {status}")
    try:
        intent = host.catalog.trees.intent(tree_id)
    except ManagedTreeError as exc:
        raise ReconstructionError("managed Gradle stage has no complete publication intent") from exc
    if intent["domain_id"] != review_id or intent["policy_id"] != host.policy_id:
        raise ReconstructionError("managed Gradle tree belongs to another reviewed policy")
    if status == "committed":
        reference = host.describe(tree_id)
        if reference.path != target or reference.policy_id != host.policy_id:
            raise ReconstructionError("managed Gradle tree path or policy changed")
        _tree_archive(reference, target, review_id, gradle)
        return "reuse", tree_id, prior
    return "reconcile", tree_id, prior


def plan_fixture_gradle_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str, expected_review_id: str,
    archive: Path | None = None, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review one supplied ZIP and the exact retained fixture-policy binding."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("Gradle import requires a V3 environment share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    review = review_fixture_execution_policy(
        suite_root, portable, reviewed, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        environment=environment,
    )
    if type(expected_review_id) is not str or review["review_id"] != expected_review_id:
        raise ReconstructionError("fixture execution policy review changed before Gradle import")
    gradle = review["policy"]["gradle"]
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root = _store(local)
    target = root / "gradle" / review["review_id"].rsplit(":", 1)[-1] / "snapshot"
    blockers = []
    if not local.workspace.is_dir():
        blockers.append("selected workspace directory is missing")
    supported = _tree_host_supported()
    if not supported:
        blockers.append("exact Linux managed-tree publication is unavailable on this host")
    if not _qualified_filesystem(root):
        blockers.append("managed Gradle store has an unqualified Linux/WSL filesystem")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        executing_host = None
        blockers.append(f"Gradle import has no supported executing host: {exc}")
    if executing_host is not None and executing_host != reviewed["host_variant"]:
        blockers.append("fixture policy targets another executing host")
    if supported:
        _private_store(root, target)
        try:
            action, tree_id, prior = _tree_state(host, target, review["review_id"], gradle)
        except (ManagedTreeError, DurableResourceError, OSError, ValueError) as exc:
            if isinstance(exc, ReconstructionError):
                raise
            raise ReconstructionError(f"managed Gradle tree cannot be inventoried: {exc}") from exc
    else:
        action, tree_id, prior = "unsupported", None, []
    source = None
    if archive is not None:
        if not isinstance(archive, Path) or not archive.is_absolute():
            raise ReconstructionError("Gradle import needs an absolute local ZIP path")
        if not _qualified_filesystem(archive.parent):
            blockers.append("selected Gradle ZIP has an unqualified Linux/WSL filesystem")
        else:
            source = {"path": str(archive), **_snapshot_archive(archive, gradle)}
    if action == "acquire" and source is None:
        blockers.append("exact local Gradle ZIP is required for acquisition")
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_policy_review_id": review["review_id"],
        "workspace": str(local.workspace), "environment_resolution_id": local.record["resolution_id"],
        "target": str(target), "host_variant": reviewed["host_variant"],
        "gradle": gradle, "source": source, "action": action, "tree_id": tree_id,
        "prior_failed_trees": prior, "blockers": blockers,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "state": "blocked" if blockers else "ready",
    }, "workbench-environment-fixture-gradle-import-plan", "plan_id")


def apply_fixture_gradle_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str, expected_review_id: str,
    expected_plan_id: str, archive: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish the verified archive in a private exact tree with prepared evidence."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_gradle_import(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, archive=archive,
        environment=values,
    )
    if type(expected_plan_id) is not str or plan["plan_id"] != expected_plan_id:
        raise ReconstructionError("Gradle import changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("Gradle import is blocked: " + "; ".join(plan["blockers"]))
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root = _store(local)
    target = Path(plan["target"])
    _private_directory(root)
    _private_directory(target.parent)
    _private_store(root, target)
    lock = root / f".gradle-import-{plan['fixture_policy_review_id'].rsplit(':', 1)[-1]}.lock"
    with private_record_lock(lock, wait=True):
        current = plan_fixture_gradle_import(
            suite_root, share, candidate, workspace=workspace,
            fixture_result_resource_id=fixture_result_resource_id,
            expected_review_id=expected_review_id, archive=archive,
            environment=values,
        )
        if current != plan:
            raise ReconstructionError("Gradle import inputs changed before acquisition")
        service = _resource_host(Path(suite_root), local.workspace, values)
        if service.policy_id != plan["environment_resolution_id"]:
            raise ReconstructionError("Gradle import resolution changed after review")
        prepared = {
            "format": "workbench-environment-fixture-gradle-import-attempt-v1",
            "schema_version": 1, "state": "prepared", "plan_id": plan["plan_id"],
            "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
            "fixture_policy_review_id": expected_review_id,
            "action": plan["action"], "target": str(target), "tree_id": plan["tree_id"],
        }
        prepared_ref = service.publish_bytes(
            "evidence", "environment-fixture-gradle-import-attempt.json",
            _canonical(prepared) + b"\n", domain_id=plan["share_id"],
            references=(fixture_result_resource_id,),
        )
        if plan["action"] == "acquire":
            try:
                with host.stage("artifacts", target.name, requested_path=target) as stage:
                    _private_store(root, target)
                    stage.path.mkdir(mode=0o700)
                    filename = f"gradle-{plan['gradle']['version']}-bin.zip"
                    copied = _snapshot_archive(
                        Path(plan["source"]["path"]), plan["gradle"],
                        destination=stage.path / filename,
                    )
                    if copied != {key: value for key, value in plan["source"].items() if key != "path"}:
                        raise ReconstructionError("Gradle ZIP changed after review")
                    reference = stage.publish(
                        validate=lambda path: _validate_stage(path, plan),
                        domain_id=expected_review_id,
                        references=(prepared_ref.resource_id,),
                        inventory_policy=EXACT_INVENTORY_POLICY,
                    )
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed Gradle tree cannot be published: {exc}") from exc
        else:
            try:
                reference = (host.reconcile(plan["tree_id"]) if plan["action"] == "reconcile"
                             else host.describe(plan["tree_id"]))
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed Gradle tree cannot be reopened: {exc}") from exc
        if (reference.path != target or reference.policy_id != host.policy_id
                or reference.workspace != local.workspace):
            raise ReconstructionError("managed Gradle publication has another local binding")
        archive_index = _tree_archive(reference, target, expected_review_id, plan["gradle"])
        result = {
            "format": RESULT_FORMAT, "schema_version": 1,
            "outcome": ("acquired" if plan["action"] == "acquire" else
                        "reconciled" if plan["action"] == "reconcile" else "reused"),
            "plan_id": plan["plan_id"], "share_id": plan["share_id"],
            "candidate_id": plan["candidate_id"],
            "fixture_result_resource_id": fixture_result_resource_id,
            "fixture_policy_review_id": expected_review_id,
            "attempt_resource_id": prepared_ref.resource_id,
            "workspace": plan["workspace"],
            "environment_resolution_id": plan["environment_resolution_id"],
            "tree_id": reference.tree_id, "tree_path": str(reference.path),
            "tree_content_sha256": reference.content_sha256,
            "gradle": plan["gradle"], "archive": archive_index,
            "unresolved_inputs": plan["unresolved_inputs"],
            "scope": "Exact Gradle ZIP bytes only; extraction, execution and reconstruction remain unresolved.",
        }
        payload = _canonical(result) + b"\n"
        completed = service.publish_bytes(
            "evidence", "environment-fixture-gradle-import.json", payload,
            domain_id=plan["share_id"], references=(prepared_ref.resource_id,),
        )
        if service.read_bytes(completed.resource_id) != payload:
            raise ReconstructionError("Gradle import result did not reopen exactly")
        return {**result, "resource": {
            "resource_id": completed.resource_id, "store_id": completed.store_id,
            "path": str(completed.path), "sha256": completed.sha256,
        }}


def _validate_stage(path: Path, plan: Mapping[str, Any]) -> None:
    from .storage.exact_tree_inventory import inventory_exact_members

    members, _, _, _ = inventory_exact_members(path)
    filename = f"gradle-{plan['gradle']['version']}-bin.zip"
    if len(members) != 1 or members[0]["path"] != filename or members[0]["kind"] != "file":
        raise ReconstructionError("managed Gradle stage has missing or extra members")
    observed = _snapshot_archive(path / filename, plan["gradle"])
    if observed != {key: value for key, value in plan["source"].items() if key != "path"}:
        raise ReconstructionError("managed Gradle stage differs from the reviewed archive")


def reopen_fixture_gradle_import(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Verify retained Gradle bytes and result without the original archive."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    review = review_fixture_execution_policy(
        suite_root, portable, reviewed, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        environment=environment,
    )
    if type(expected_review_id) is not str or review["review_id"] != expected_review_id:
        raise ReconstructionError("fixture execution policy changed before Gradle reopening")
    if not _tree_host_supported():
        raise ReconstructionError("exact Linux managed-tree reopening is unavailable on this host")
    try:
        executing_host = _host_variant(host_platform())
    except (JavaRuntimeError, ReconstructionError) as exc:
        raise ReconstructionError(f"Gradle import has no supported executing host: {exc}") from exc
    if executing_host != reviewed["host_variant"]:
        raise ReconstructionError("fixture policy targets another executing host")
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root = _store(local)
    if not _qualified_filesystem(root):
        raise ReconstructionError("managed Gradle store has an unqualified Linux/WSL filesystem")
    _private_store(root)
    service = _resource_host(Path(suite_root), local.workspace, values)
    try:
        receipt_ref = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        receipt = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError) as exc:
        raise ReconstructionError(f"Gradle import result cannot be reopened: {exc}") from exc
    if (receipt_ref.owner_id != "workbench-core" or receipt_ref.role != "evidence"
            or receipt_ref.domain_id != portable["share_id"]
            or type(receipt) is not dict or payload != _canonical(receipt) + b"\n"
            or set(receipt) != {
                "format", "schema_version", "outcome", "plan_id", "share_id",
                "candidate_id", "fixture_result_resource_id", "fixture_policy_review_id",
                "attempt_resource_id", "workspace", "environment_resolution_id",
                "tree_id", "tree_path", "tree_content_sha256", "gradle", "archive",
                "unresolved_inputs", "scope",
            }
            or receipt.get("format") != RESULT_FORMAT or receipt.get("schema_version") != 1
            or receipt.get("share_id") != portable["share_id"]
            or receipt.get("candidate_id") != reviewed["candidate_id"]
            or receipt.get("fixture_result_resource_id") != fixture_result_resource_id
            or receipt.get("fixture_policy_review_id") != expected_review_id
            or receipt.get("workspace") != str(local.workspace)
            or receipt.get("environment_resolution_id") != local.record["resolution_id"]
            or receipt.get("gradle") != review["policy"]["gradle"]
            or receipt.get("scope") !=
            "Exact Gradle ZIP bytes only; extraction, execution and reconstruction remain unresolved."
            or receipt.get("unresolved_inputs") != portable["lock"]["unresolved_inputs"]):
        raise ReconstructionError("Gradle import result has another identity or scope")
    target = root / "gradle" / expected_review_id.rsplit(":", 1)[-1] / "snapshot"
    try:
        attempt_id = receipt["attempt_resource_id"]
        attempt_ref = service.describe(attempt_id)
        attempt_raw = service.read_bytes(attempt_id)
        attempt = json.loads(attempt_raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, KeyError,
            TypeError) as exc:
        raise ReconstructionError(f"Gradle prepared attempt cannot be reopened: {exc}") from exc
    action = {"acquired": "acquire", "reconciled": "reconcile", "reused": "reuse"}.get(
        receipt.get("outcome"),
    )
    if (action is None or type(attempt) is not dict or set(attempt) != {
                "format", "schema_version", "state", "plan_id", "share_id",
                "candidate_id", "fixture_policy_review_id", "action", "target", "tree_id",
            }
            or attempt_ref.owner_id != "workbench-core" or attempt_ref.role != "evidence"
            or attempt_ref.domain_id != portable["share_id"]
            or attempt_raw != _canonical(attempt) + b"\n"
            or attempt.get("format") != "workbench-environment-fixture-gradle-import-attempt-v1"
            or attempt.get("schema_version") != 1 or attempt.get("state") != "prepared"
            or attempt.get("plan_id") != receipt.get("plan_id")
            or attempt.get("share_id") != portable["share_id"]
            or attempt.get("candidate_id") != reviewed["candidate_id"]
            or attempt.get("fixture_policy_review_id") != expected_review_id
            or attempt.get("target") != str(target) or attempt.get("action") != action
            or (action == "acquire" and attempt.get("tree_id") is not None)
            or (action in {"reuse", "reconcile"}
                and attempt.get("tree_id") != receipt.get("tree_id"))):
        raise ReconstructionError("Gradle prepared attempt has another identity or action")
    try:
        reference = host.describe(receipt["tree_id"])
        archive_index = _tree_archive(
            reference, target, expected_review_id, review["policy"]["gradle"],
        )
    except (ManagedTreeError, OSError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"managed Gradle tree cannot be reopened: {exc}") from exc
    if (reference.policy_id != host.policy_id
            or receipt.get("tree_path") != str(target)
            or receipt.get("tree_content_sha256") != reference.content_sha256
            or receipt.get("archive") != archive_index):
        raise ReconstructionError("Gradle import result differs from its managed tree")
    return receipt


__all__ = ["plan_fixture_gradle_import", "apply_fixture_gradle_import", "reopen_fixture_gradle_import"]
