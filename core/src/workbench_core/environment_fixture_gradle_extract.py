"""Extract retained Gradle ZIP bytes into a Core-owned exact tool tree.

The archive result remains the input authority. This route admits one
extracted tree and launcher path; it does not run Gradle or bind Java.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterator, Mapping
from zipfile import BadZipFile, ZipFile

from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .durable_files import _directory as pinned_directory
from .durable_records import private_record_lock
from .environment_fixture_gradle_import import (
    _MAX_ARCHIVE_BYTES, _STAT_FIELDS, _qualified_filesystem, _store,
    _zip_index, reopen_fixture_gradle_import,
)
from .environment_fixture_import import _ordinary_source
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _resource_host,
    _seal, validate_share,
)
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported
from .host_filesystem import private_path
from .output_routing import _private_directory
from .storage.exact_tree_inventory import EXACT_INVENTORY_POLICY, inventory_exact_members
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-fixture-gradle-extraction-plan-v1"
RESULT_FORMAT = "workbench-environment-fixture-gradle-extraction-result-v1"
_MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


def _private_store(root: Path, target: Path | None = None) -> None:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise ReconstructionError("managed Gradle tool store traverses a redirect")
    for path in (root.parent, root, root / "gradle-tools", *((target.parent,) if target else ())):
        if (path.exists() or path.is_symlink()) and not private_path(path, directory=True):
            raise ReconstructionError("managed Gradle tool store is not owner-private")


def _verify_stream(source: Any, gradle: Mapping[str, Any], archive_index: Mapping[str, Any]) -> None:
    source.seek(0)
    digest = sha256()
    size = 0
    while block := source.read(1024 * 1024):
        size += len(block)
        if size > gradle["archive_size"] or size > _MAX_ARCHIVE_BYTES:
            raise ReconstructionError("retained Gradle ZIP grew during extraction review")
        digest.update(block)
    if (size != gradle["archive_size"]
            or "sha256:" + digest.hexdigest() != gradle["archive_sha256"]):
        raise ReconstructionError("retained Gradle ZIP differs from the profile policy")
    source.seek(0)
    expected_index = {
        "member_count": archive_index["member_count"],
        "expanded_bytes": archive_index["expanded_bytes"],
        "member_index_sha256": archive_index["member_index_sha256"],
    }
    if (set(archive_index) != {"sha256", "size", *expected_index}
            or archive_index["sha256"] != gradle["archive_sha256"]
            or archive_index["size"] != gradle["archive_size"]
            or _zip_index(source, gradle["archive_root"]) != expected_index):
        raise ReconstructionError("retained Gradle ZIP member index changed")
    source.seek(0)


@contextmanager
def _opened_archive(
    path: Path, gradle: Mapping[str, Any], archive_index: Mapping[str, Any],
) -> Iterator[Any]:
    """Keep the exact ZIP descriptor and parent pinned through extraction."""

    _ordinary_source(path)
    if path.name != f"gradle-{gradle['version']}-bin.zip":
        raise ReconstructionError("retained Gradle ZIP has another archive name")
    parent = None
    try:
        parent = pinned_directory(path.parent, create=False)
        parent_identity = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                or visible.st_size != gradle["archive_size"]):
            raise ReconstructionError("retained Gradle ZIP is not an independent exact file")
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or any(getattr(opened, field) != getattr(visible, field)
                           for field in _STAT_FIELDS)):
                raise ReconstructionError("retained Gradle ZIP changed before opening")
            _verify_stream(source, gradle, archive_index)
            yield source
            _verify_stream(source, gradle, archive_index)
            after = os.fstat(source.fileno())
            final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if any(getattr(opened, field) != getattr(after, field)
                   or getattr(opened, field) != getattr(final, field)
                   for field in _STAT_FIELDS):
                raise ReconstructionError("retained Gradle ZIP changed during extraction")
    except (OSError, DurableResourceError) as exc:
        raise ReconstructionError(f"retained Gradle ZIP cannot be pinned: {exc}") from exc
    finally:
        if parent is not None:
            os.close(parent)
    _ordinary_source(path)
    reopened = pinned_directory(path.parent, create=False)
    try:
        if (os.fstat(reopened).st_dev, os.fstat(reopened).st_ino) != parent_identity:
            raise ReconstructionError("retained Gradle ZIP parent changed during extraction")
    finally:
        os.close(reopened)


def _content_rows(source: Any, archive_root: str) -> list[dict[str, Any]]:
    """Derive every expected extracted byte and mode from the pinned ZIP."""

    source.seek(0)
    directories: set[str] = set()
    files: list[dict[str, Any]] = []
    total = 0
    try:
        with ZipFile(source) as archive:
            for info in archive.infolist():
                file_type = stat.S_IFMT(info.external_attr >> 16)
                if ((info.is_dir() and file_type == stat.S_IFREG)
                        or (not info.is_dir() and file_type == stat.S_IFDIR)):
                    raise ReconstructionError("Gradle ZIP member type disagrees with its path")
                name = info.filename.rstrip("/")
                path = PurePosixPath(name)
                parent = path if info.is_dir() else path.parent
                while str(parent) != ".":
                    directories.add(str(parent))
                    parent = parent.parent
                if info.is_dir():
                    continue
                mode = stat.S_IMODE(info.external_attr >> 16) or 0o600
                if mode & 0o7000:
                    raise ReconstructionError("Gradle ZIP requests a privileged file mode")
                digest = sha256()
                size = 0
                with archive.open(info) as member:
                    while block := member.read(1024 * 1024):
                        size += len(block)
                        total += len(block)
                        if size > info.file_size or total > _MAX_EXPANDED_BYTES:
                            raise ReconstructionError("Gradle ZIP expands beyond its review bound")
                        digest.update(block)
                if size != info.file_size:
                    raise ReconstructionError("Gradle ZIP file is incomplete")
                files.append({
                    "path": name, "kind": "file", "size": size,
                    "sha256": digest.hexdigest(), "mode": mode,
                })
    except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise ReconstructionError(f"Gradle ZIP cannot provide exact content rows: {exc}") from exc
    launcher = f"{archive_root}/bin/gradle"
    launcher_row = next((row for row in files if row["path"] == launcher), None)
    if launcher_row is None or not launcher_row["mode"] & 0o111:
        raise ReconstructionError("Gradle ZIP launcher is not executable")
    rows = files + [{"path": name, "kind": "directory", "mode": 0o700}
                    for name in directories]
    rows.sort(key=lambda row: row["path"].encode("utf-8"))
    if len({row["path"] for row in rows}) != len(rows):
        raise ReconstructionError("Gradle ZIP file and directory paths collide")
    return rows


def _summary(rows: list[dict[str, Any]], archive_root: str) -> dict[str, Any]:
    return {
        "member_count": len(rows),
        "file_count": sum(row["kind"] == "file" for row in rows),
        "directory_count": sum(row["kind"] == "directory" for row in rows),
        "expanded_bytes": sum(row["size"] for row in rows if row["kind"] == "file"),
        "content_rows_sha256": "sha256:" + sha256(_canonical(rows)).hexdigest(),
        "launcher_relative_path": f"{archive_root}/bin/gradle",
    }


def _manifest(archive_path: Path, gradle: Mapping[str, Any], archive_index: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with _opened_archive(archive_path, gradle, archive_index) as source:
        rows = _content_rows(source, gradle["archive_root"])
    return _summary(rows, gradle["archive_root"]), rows


def _copy_member(destination: Path, source: Any, expected: Mapping[str, Any]) -> None:
    parent = pinned_directory(destination.parent, create=True)
    try:
        descriptor = os.open(
            destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=parent,
        )
        try:
            digest = sha256()
            size = 0
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > expected["size"]:
                    raise ReconstructionError("Gradle ZIP member grew during extraction")
                digest.update(block)
                remaining = memoryview(block)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise ReconstructionError("Gradle extraction write did not advance")
                    remaining = remaining[written:]
            if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                raise ReconstructionError("Gradle ZIP member changed during extraction")
            os.fchmod(descriptor, expected["mode"])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _extract(stage: Path, archive_path: Path, gradle: Mapping[str, Any],
             archive_index: Mapping[str, Any], expected: list[dict[str, Any]]) -> None:
    stage.mkdir(mode=0o700)
    by_path = {row["path"]: row for row in expected}
    with _opened_archive(archive_path, gradle, archive_index) as source:
        source.seek(0)
        try:
            with ZipFile(source) as archive:
                for info in archive.infolist():
                    name = info.filename.rstrip("/")
                    target = stage.joinpath(*PurePosixPath(name).parts)
                    if info.is_dir():
                        descriptor = pinned_directory(target, create=True)
                        os.close(descriptor)
                    else:
                        with archive.open(info) as member:
                            _copy_member(target, member, by_path[name])
        except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
            raise ReconstructionError(f"Gradle ZIP extraction failed: {exc}") from exc


def _observed_rows(members: Any) -> list[dict[str, Any]]:
    rows = []
    for member in members:
        row = {"path": member["path"], "kind": member["kind"], "mode": member["mode"]}
        if member["kind"] == "file":
            row.update({"size": member["size"], "sha256": member["sha256"]})
        rows.append(row)
    return sorted(rows, key=lambda row: row["path"].encode("utf-8"))


def _validate_stage(path: Path, expected: list[dict[str, Any]]) -> None:
    members, mode, _, _ = inventory_exact_members(path)
    if mode != 0o700 or _observed_rows(members) != expected:
        raise ReconstructionError("managed Gradle tool stage differs from the retained ZIP")


def _verify_tree(reference: ManagedTreeReference, target: Path, domain_id: str,
                 expected: list[dict[str, Any]]) -> None:
    if (reference.owner_id != "workbench-core" or reference.role != "artifacts"
            or reference.domain_id != domain_id or reference.path != target
            or reference.derived_status != "current"
            or reference.inventory_policy != EXACT_INVENTORY_POLICY
            or _observed_rows(reference.members) != expected):
        raise ReconstructionError("managed Gradle tool tree differs from the retained ZIP")


def _tree_state(host: Any, target: Path, archive_tree_id: str,
                expected: list[dict[str, Any]]) -> tuple[str, str | None, list[dict[str, str]]]:
    rows = [row for row in host.catalog.trees.inventory(workspace=host.workspace)
            if row["path"] == str(target)]
    prior: list[dict[str, str]] = []
    active = []
    for row in rows:
        if row["owner_id"] != "workbench-core" or row["role"] != "artifacts":
            raise ReconstructionError("managed Gradle tool target has a foreign Core reservation")
        # A failed prepublication reservation may remain after a later attempt
        # successfully owns the same target. It never grants custody itself.
        if row["status"] == "failed":
            prior.append({"tree_id": str(row["tree_id"]), "status": "failed"})
        else:
            active.append(row)
    if len(active) > 1:
        raise ReconstructionError("managed Gradle tool target has ambiguous Core reservations")
    if not active:
        if target.exists() or target.is_symlink():
            raise ReconstructionError("managed Gradle tool target exists outside Core custody")
        return "extract", None, prior
    row = active[0]
    status, tree_id = str(row["status"]), str(row["tree_id"])
    if status not in {"committed", "published-uncommitted", "incomplete"}:
        raise ReconstructionError(f"managed Gradle tool tree requires reviewed recovery: {status}")
    try:
        intent = host.catalog.trees.intent(tree_id)
    except ManagedTreeError as exc:
        raise ReconstructionError("managed Gradle tool stage has no publication intent") from exc
    if intent["domain_id"] != archive_tree_id or intent["policy_id"] != host.policy_id:
        raise ReconstructionError("managed Gradle tool tree belongs to another retained archive")
    if status == "committed":
        reference = host.describe(tree_id)
        if reference.policy_id != host.policy_id:
            raise ReconstructionError("managed Gradle tool tree policy changed")
        _verify_tree(reference, target, archive_tree_id, expected)
        return "reuse", tree_id, prior
    return "reconcile", tree_id, prior


def plan_fixture_gradle_extraction(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, gradle_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review the exact ZIP-derived tool tree and any prior Core publication."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("Gradle extraction needs a V3 environment share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    retained = reopen_fixture_gradle_import(
        suite_root, portable, reviewed, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        result_resource_id=gradle_result_resource_id, environment=environment,
    )
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root = _store(local)
    archive_tree_id = retained["tree_id"]
    target = root / "gradle-tools" / archive_tree_id.rsplit(":", 1)[-1] / "snapshot"
    archive_path = Path(retained["tree_path"]) / f"gradle-{retained['gradle']['version']}-bin.zip"
    manifest, expected = _manifest(archive_path, retained["gradle"], retained["archive"])
    blockers: list[str] = []
    if not local.workspace.is_dir():
        blockers.append("selected workspace directory is missing")
    supported = _tree_host_supported()
    if not supported:
        blockers.append("exact Linux managed-tree publication is unavailable on this host")
    if not _qualified_filesystem(root):
        blockers.append("managed Gradle tool store has an unqualified Linux/WSL filesystem")
    if supported:
        _private_store(root, target)
        try:
            action, tree_id, prior = _tree_state(host, target, archive_tree_id, expected)
        except (ManagedTreeError, DurableResourceError, OSError, ValueError) as exc:
            if isinstance(exc, ReconstructionError):
                raise
            raise ReconstructionError(f"managed Gradle tool tree cannot be inventoried: {exc}") from exc
    else:
        action, tree_id, prior = "unsupported", None, []
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_policy_review_id": expected_review_id,
        "gradle_result_resource_id": gradle_result_resource_id,
        "archive_tree_id": archive_tree_id,
        "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "target": str(target), "gradle": retained["gradle"],
        "archive": retained["archive"], "archive_path": str(archive_path),
        "manifest": manifest,
        "action": action, "tree_id": tree_id, "prior_failed_trees": prior,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "blockers": blockers, "state": "blocked" if blockers else "ready",
    }, "workbench-environment-fixture-gradle-extraction-plan", "plan_id")


def apply_fixture_gradle_extraction(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, gradle_result_resource_id: str,
    expected_plan_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish one exact tree; retain interruption evidence and refuse drift."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_gradle_extraction(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        gradle_result_resource_id=gradle_result_resource_id,
        environment=values,
    )
    if type(expected_plan_id) is not str or plan["plan_id"] != expected_plan_id:
        raise ReconstructionError("Gradle extraction changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("Gradle extraction is blocked: " + "; ".join(plan["blockers"]))
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root = _store(local)
    target = Path(plan["target"])
    archive_path = Path(plan["archive_path"])
    _private_directory(root)
    _private_directory(target.parent)
    _private_store(root, target)
    lock = root / f".gradle-extraction-{plan['archive_tree_id'].rsplit(':', 1)[-1]}.lock"
    with private_record_lock(lock, wait=True):
        current = plan_fixture_gradle_extraction(
            suite_root, share, candidate, workspace=workspace,
            fixture_result_resource_id=fixture_result_resource_id,
            expected_review_id=expected_review_id,
            gradle_result_resource_id=gradle_result_resource_id,
            environment=values,
        )
        if current != plan:
            raise ReconstructionError("Gradle extraction inputs changed before publication")
        service = _resource_host(Path(suite_root), local.workspace, values)
        if service.policy_id != plan["environment_resolution_id"]:
            raise ReconstructionError("Gradle extraction resolution changed after review")
        prepared = {
            "format": "workbench-environment-fixture-gradle-extraction-attempt-v1",
            "schema_version": 1, "state": "prepared", "plan_id": plan["plan_id"],
            "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
            "gradle_result_resource_id": gradle_result_resource_id,
            "archive_tree_id": plan["archive_tree_id"],
            "action": plan["action"], "target": str(target), "tree_id": plan["tree_id"],
        }
        prepared_ref = service.publish_bytes(
            "evidence", "environment-fixture-gradle-extraction-attempt.json",
            _canonical(prepared) + b"\n", domain_id=plan["share_id"],
            references=(gradle_result_resource_id,),
        )
        _, expected = _manifest(archive_path, plan["gradle"], plan["archive"])
        if plan["action"] == "extract":
            try:
                with host.stage("artifacts", target.name, requested_path=target) as stage:
                    _private_store(root, target)
                    _extract(stage.path, archive_path, plan["gradle"], plan["archive"], expected)
                    reference = stage.publish(
                        validate=lambda path: _validate_stage(path, expected),
                        domain_id=plan["archive_tree_id"],
                        references=(prepared_ref.resource_id, plan["archive_tree_id"]),
                        inventory_policy=EXACT_INVENTORY_POLICY,
                    )
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed Gradle tool tree cannot be published: {exc}") from exc
        else:
            try:
                reference = (host.reconcile(plan["tree_id"]) if plan["action"] == "reconcile"
                             else host.describe(plan["tree_id"]))
            except ManagedTreeError as exc:
                raise ReconstructionError(f"managed Gradle tool tree cannot be reopened: {exc}") from exc
        if (reference.path != target or reference.policy_id != host.policy_id
                or reference.workspace != local.workspace):
            raise ReconstructionError("managed Gradle tool publication has another local binding")
        _verify_tree(reference, target, plan["archive_tree_id"], expected)
        result = {
            "format": RESULT_FORMAT, "schema_version": 1,
            "outcome": ("extracted" if plan["action"] == "extract" else
                        "reconciled" if plan["action"] == "reconcile" else "reused"),
            "plan_id": plan["plan_id"], "share_id": plan["share_id"],
            "candidate_id": plan["candidate_id"],
            "fixture_result_resource_id": fixture_result_resource_id,
            "fixture_policy_review_id": expected_review_id,
            "gradle_result_resource_id": gradle_result_resource_id,
            "attempt_resource_id": prepared_ref.resource_id,
            "workspace": plan["workspace"],
            "environment_resolution_id": plan["environment_resolution_id"],
            "archive_tree_id": plan["archive_tree_id"],
            "tree_id": reference.tree_id, "tree_path": str(reference.path),
            "tree_content_sha256": reference.content_sha256,
            "gradle": plan["gradle"], "archive": plan["archive"],
            "manifest": plan["manifest"],
            "launcher_path": str(target / plan["manifest"]["launcher_relative_path"]),
            "unresolved_inputs": plan["unresolved_inputs"],
            "scope": "Exact extracted Gradle bytes only; Java binding, execution and reconstruction remain unresolved.",
        }
        payload = _canonical(result) + b"\n"
        completed = service.publish_bytes(
            "evidence", "environment-fixture-gradle-extraction.json", payload,
            domain_id=plan["share_id"], references=(prepared_ref.resource_id,),
        )
        if service.read_bytes(completed.resource_id) != payload:
            raise ReconstructionError("Gradle extraction result did not reopen exactly")
        return {**result, "resource": {
            "resource_id": completed.resource_id, "store_id": completed.store_id,
            "path": str(completed.path), "sha256": completed.sha256,
        }}


def reopen_fixture_gradle_extraction(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, gradle_result_resource_id: str,
    result_resource_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recheck the extracted tree against the retained ZIP after restart."""

    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    retained = reopen_fixture_gradle_import(
        suite_root, portable, reviewed, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        result_resource_id=gradle_result_resource_id, environment=environment,
    )
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    host, root = _store(local)
    if not _tree_host_supported() or not _qualified_filesystem(root):
        raise ReconstructionError("exact Linux/WSL Gradle tool reopening is unavailable")
    _private_store(root)
    archive_path = Path(retained["tree_path"]) / f"gradle-{retained['gradle']['version']}-bin.zip"
    manifest, expected = _manifest(archive_path, retained["gradle"], retained["archive"])
    service = _resource_host(Path(suite_root), local.workspace, values)
    try:
        receipt_ref = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        receipt = json.loads(payload.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError) as exc:
        raise ReconstructionError(f"Gradle extraction result cannot be reopened: {exc}") from exc
    target = root / "gradle-tools" / retained["tree_id"].rsplit(":", 1)[-1] / "snapshot"
    if (receipt_ref.owner_id != "workbench-core" or receipt_ref.role != "evidence"
            or receipt_ref.domain_id != portable["share_id"]
            or type(receipt) is not dict or payload != _canonical(receipt) + b"\n"
            or set(receipt) != {
                "format", "schema_version", "outcome", "plan_id", "share_id",
                "candidate_id", "fixture_result_resource_id", "fixture_policy_review_id",
                "gradle_result_resource_id", "attempt_resource_id", "workspace",
                "environment_resolution_id", "archive_tree_id", "tree_id", "tree_path",
                "tree_content_sha256", "gradle", "archive", "manifest",
                "launcher_path", "unresolved_inputs", "scope",
            }
            or receipt.get("format") != RESULT_FORMAT or receipt.get("schema_version") != 1
            or receipt.get("share_id") != portable["share_id"]
            or receipt.get("candidate_id") != reviewed["candidate_id"]
            or receipt.get("fixture_result_resource_id") != fixture_result_resource_id
            or receipt.get("fixture_policy_review_id") != expected_review_id
            or receipt.get("gradle_result_resource_id") != gradle_result_resource_id
            or receipt.get("archive_tree_id") != retained["tree_id"]
            or receipt.get("workspace") != str(local.workspace)
            or receipt.get("environment_resolution_id") != local.record["resolution_id"]
            or receipt.get("tree_path") != str(target)
            or receipt.get("launcher_path") != str(target / manifest["launcher_relative_path"])
            or receipt.get("gradle") != retained["gradle"]
            or receipt.get("archive") != retained["archive"]
            or receipt.get("manifest") != manifest
            or receipt.get("unresolved_inputs") != portable["lock"]["unresolved_inputs"]
            or receipt.get("scope") !=
            "Exact extracted Gradle bytes only; Java binding, execution and reconstruction remain unresolved."):
        raise ReconstructionError("Gradle extraction result has another identity or scope")
    try:
        attempt_ref = service.describe(receipt["attempt_resource_id"])
        attempt_raw = service.read_bytes(receipt["attempt_resource_id"])
        attempt = json.loads(attempt_raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, KeyError,
            TypeError) as exc:
        raise ReconstructionError(f"Gradle extraction attempt cannot be reopened: {exc}") from exc
    action = {"extracted": "extract", "reconciled": "reconcile", "reused": "reuse"}.get(
        receipt.get("outcome"),
    )
    if (action is None or attempt_ref.owner_id != "workbench-core"
            or attempt_ref.role != "evidence" or attempt_ref.domain_id != portable["share_id"]
            or type(attempt) is not dict or attempt_raw != _canonical(attempt) + b"\n"
            or set(attempt) != {
                "format", "schema_version", "state", "plan_id", "share_id",
                "candidate_id", "gradle_result_resource_id", "archive_tree_id",
                "action", "target", "tree_id",
            }
            or attempt.get("format") != "workbench-environment-fixture-gradle-extraction-attempt-v1"
            or attempt.get("schema_version") != 1 or attempt.get("state") != "prepared"
            or attempt.get("plan_id") != receipt.get("plan_id")
            or attempt.get("share_id") != portable["share_id"]
            or attempt.get("candidate_id") != reviewed["candidate_id"]
            or attempt.get("gradle_result_resource_id") != gradle_result_resource_id
            or attempt.get("archive_tree_id") != retained["tree_id"]
            or attempt.get("action") != action or attempt.get("target") != str(target)
            or (action == "extract" and attempt.get("tree_id") is not None)
            or (action in {"reuse", "reconcile"}
                and attempt.get("tree_id") != receipt.get("tree_id"))):
        raise ReconstructionError("Gradle extraction attempt has another identity or action")
    try:
        reference = host.describe(receipt["tree_id"])
    except (ManagedTreeError, OSError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"managed Gradle tool tree cannot be reopened: {exc}") from exc
    if (reference.policy_id != host.policy_id
            or receipt.get("tree_content_sha256") != reference.content_sha256
            or len(reference.references) != 2
            or retained["tree_id"] not in reference.references
            or (action == "extract"
                and receipt["attempt_resource_id"] not in reference.references)):
        raise ReconstructionError("Gradle extraction tree lost its Core source references")
    _verify_tree(reference, target, retained["tree_id"], expected)
    return receipt


__all__ = [
    "plan_fixture_gradle_extraction", "apply_fixture_gradle_extraction",
    "reopen_fixture_gradle_extraction",
]
