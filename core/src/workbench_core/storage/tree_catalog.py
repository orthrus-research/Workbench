"""Append-only Core catalog for exact directory publication and recovery.

Tree records live under the existing resource-catalog root. The first version
keeps both authoritative and explicitly rebuildable derived member inventories;
the latter may change without changing the authoritative tree identity.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Iterator, Mapping

from workbench_api.managed_trees import ManagedTreeError, ManagedTreeReference

from .. import check_storage
from ..durable_records import publish_immutable_bytes
from ..host_filesystem import file_lease, private_path, secure_private_path
from ..output_routing import _private_directory
from .exact_tree_inventory import (
    EXACT_INVENTORY_POLICY, MAX_DIRECTORIES, MAX_FILES,
    exact_content_sha256, inventory_exact_members,
)


TREE_KIND = "workbench-managed-tree-v1"
RESERVATION_KIND = "workbench-tree-reservation-v1"
INTENT_KIND = "workbench-tree-intent-v1"
DERIVED_INTENT_KIND = "workbench-tree-intent-v2"
EXACT_INTENT_KIND = "workbench-tree-intent-v3"
COMMIT_KIND = "workbench-tree-commit-v1"
ABORT_KIND = "workbench-tree-abort-v1"
_TREE_ID = re.compile(r"workbench-tree-v1:([0-9a-f]{32})\Z")
_REFERENCE = re.compile(r"(?:workbench-(?:resource|tree)-v1:[0-9a-f]{32}|workbench-check-v1:[0-9a-f]{64})\Z")
_OWNER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ATLAS_GRAPH_ID = re.compile(r"workbench-atlas-graph-set-v[23]:sha256:[0-9a-f]{64}\Z")
_MAX_RECORD_BYTES = 4 * 1024 * 1024
_MAX_MEMBERS = 4096
_MAX_ATLAS_MANIFEST_BYTES = 16 * 1024 * 1024


def _nonce(tree_id: str) -> str:
    match = _TREE_ID.fullmatch(tree_id) if isinstance(tree_id, str) else None
    if match is None:
        raise ManagedTreeError("tree.id", "select an exact managed tree ID")
    return match.group(1)


def _sealed(kind: str, body: Mapping[str, object]) -> dict:
    return check_storage.seal(kind, dict(body))


def _store_id(root: Path) -> str:
    return "workbench-resource-store:sha256:" + sha256(os.fsencode(root)).hexdigest()


def inventory_members(
    root: Path, *, derived_members: tuple[str, ...] = (),
    cancelled=lambda: False, require_derived: bool = True,
) -> list[dict[str, object]]:
    """Inventory ordinary files and empty directories with portable names."""
    if (not isinstance(derived_members, tuple)
            or any(not isinstance(name, str) for name in derived_members)
            or len(set(derived_members)) != len(derived_members)):
        raise ManagedTreeError("tree.members", "derived member names must be unique paths")
    for name in derived_members:
        check_storage.safe_path(name)
    directories = []
    for relative, is_directory in check_storage.manifest_paths(root, cancelled=cancelled):
        if is_directory:
            directories.append({"path": relative, "kind": "directory", "classification": "authoritative"})
    files = []
    for row in check_storage.tree_manifest(root, cancelled=cancelled):
        files.append({"path": row["path"], "kind": "file", "size": row["size"],
                      "sha256": row["sha256"], "mode": row["mode"],
                      "classification": "derived" if row["path"] in derived_members else "authoritative"})
    paths = {row["path"] for row in files}
    if require_derived and not set(derived_members) <= paths:
        raise ManagedTreeError("tree.members", "a declared derived member is missing")
    result = sorted([*directories, *files], key=lambda row: row["path"])
    if len(result) > _MAX_MEMBERS:
        raise ManagedTreeError("tree.bounds", "managed tree has too many members")
    return result


def _content_sha256(members: list[dict[str, object]]) -> str:
    authoritative = [row for row in members if row["classification"] == "authoritative"]
    return sha256(check_storage.canonical(authoritative)).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManagedTreeError("tree.changed", "Atlas manifest has duplicate fields")
        result[key] = value
    return result


def atlas_manifest_baseline(
    root: Path, *, members: list[dict[str, object]], domain_id: str,
) -> str:
    """Bind every Atlas manifest byte except the declared derived descriptor."""

    if not isinstance(domain_id, str) or _ATLAS_GRAPH_ID.fullmatch(domain_id) is None:
        raise ManagedTreeError("tree.policy", "Atlas graph identity is invalid")
    row = next((item for item in members if item["path"] == "manifest.json"), None)
    if row is None or row["kind"] != "file" or row["classification"] != "authoritative":
        raise ManagedTreeError("tree.policy", "Atlas manifest is not an authoritative file")
    path = root / "manifest.json"
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _MAX_ATLAS_MANIFEST_BYTES:
            raise ManagedTreeError("tree.changed", "Atlas manifest is not a bounded independent file")
        raw = bytearray()
        while len(raw) <= _MAX_ATLAS_MANIFEST_BYTES:
            part = os.read(descriptor, min(1024 * 1024, _MAX_ATLAS_MANIFEST_BYTES + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(descriptor)
        visible = path.lstat()
        if (
            len(raw) > _MAX_ATLAS_MANIFEST_BYTES
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink, before.st_mode)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink, after.st_mode)
            or stat.S_IMODE(after.st_mode) != row["mode"]
            or (after.st_dev, after.st_ino) != (visible.st_dev, visible.st_ino)
            or len(raw) != row["size"]
            or sha256(raw).hexdigest() != row["sha256"]
        ):
            raise ManagedTreeError("tree.changed", "Atlas manifest changed during verification")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if type(value) is not dict or value.get("graph_set_id") != domain_id or "query_index" not in value:
            raise ManagedTreeError("tree.changed", "Atlas manifest graph binding changed")
        canonical = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        if canonical != raw:
            raise ManagedTreeError("tree.changed", "Atlas manifest serialization changed")
        value["query_index"] = None
        baseline = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        return sha256(baseline).hexdigest()
    except ManagedTreeError:
        raise
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ManagedTreeError("tree.changed", "Atlas manifest cannot be verified") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_members(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > _MAX_MEMBERS:
        raise ManagedTreeError("tree.changed", "managed tree member inventory is invalid")
    seen = set()
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ManagedTreeError("tree.changed", "managed tree member entry is invalid")
        try:
            check_storage.safe_path(row["path"])
        except ValueError as exc:
            raise ManagedTreeError("tree.changed", "managed tree member path is invalid") from exc
        if row["path"] in seen:
            raise ManagedTreeError("tree.changed", "managed tree member path is duplicated")
        seen.add(row["path"])
        if row.get("kind") == "directory":
            if set(row) != {"path", "kind", "classification"} or row["classification"] != "authoritative":
                raise ManagedTreeError("tree.changed", "managed tree directory entry is invalid")
        elif row.get("kind") == "file":
            if (set(row) != {"path", "kind", "size", "sha256", "mode", "classification"}
                    or type(row["size"]) is not int or row["size"] < 0
                    or not isinstance(row["sha256"], str) or _SHA.fullmatch(row["sha256"]) is None
                    or type(row["mode"]) is not int or row["mode"] not in {0o644, 0o755}
                    or row["classification"] not in {"authoritative", "derived"}):
                raise ManagedTreeError("tree.changed", "managed tree file entry is invalid")
        else:
            raise ManagedTreeError("tree.changed", "managed tree member kind is invalid")
    if [row["path"] for row in value] != sorted(seen):
        raise ManagedTreeError("tree.changed", "managed tree member order changed")
    return value


class TreeCatalog:
    def __init__(self, resource_catalog_root: Path):
        self.root = resource_catalog_root / "trees"

    def _directory(self, name: str) -> Path:
        return self.root / name

    def _path(self, name: str, nonce: str) -> Path:
        return self._directory(name) / f"{nonce}.json"

    def ensure(self) -> None:
        for path in (self.root, *(self._directory(name) for name in (
            "reservations", "intents", "commits", "aborts", "leases",
        ))):
            _private_directory(path)
            secure_private_path(path, directory=True)

    def _write(self, name: str, nonce: str, kind: str, body: Mapping[str, object]) -> dict:
        record = _sealed(kind, body)
        check_storage.write_json(self._path(name, nonce), record, byte_limit=_MAX_RECORD_BYTES)
        return record

    def _read(self, name: str, nonce: str, kind: str) -> dict:
        try:
            record = check_storage.read_json(self._path(name, nonce), byte_limit=_MAX_RECORD_BYTES)
        except (OSError, ValueError) as exc:
            raise ManagedTreeError("tree.unavailable", f"managed tree {name} is unavailable") from exc
        if (not isinstance(record, dict)
                or record != _sealed(kind, {key: value for key, value in record.items() if key != "id"})):
            raise ManagedTreeError("tree.changed", f"managed tree {name} changed")
        return record

    @contextmanager
    def lease(self, tree_id: str, *, exclusive: bool = False, create: bool = False) -> Iterator[None]:
        nonce = _nonce(tree_id)
        path = self._directory("leases") / f"{nonce}.lock"
        if not path.parent.is_dir():
            raise ManagedTreeError("tree.unavailable", "managed tree lease directory is unavailable")
        flags = os.O_RDWR | (os.O_CREAT if create else 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileNotFoundError as exc:
            raise ManagedTreeError("tree.unavailable", "managed tree lease is unavailable") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ManagedTreeError("tree.changed", "managed tree lease is not an independent file")
            secure_private_path(path, directory=False)
            try:
                with file_lease(descriptor, exclusive=exclusive):
                    yield
            except BlockingIOError as exc:
                raise ManagedTreeError("tree.busy", "managed tree is in use") from exc
        finally:
            os.close(descriptor)

    def _target(self, record: Mapping[str, object]) -> Path:
        root, relative = record.get("store_root"), record.get("relative_path")
        if not isinstance(root, str) or not isinstance(relative, str):
            raise ManagedTreeError("tree.changed", "managed tree target is invalid")
        base = Path(root)
        if not base.is_absolute() or _store_id(base) != record.get("store_id"):
            raise ManagedTreeError("tree.changed", "managed tree store identity changed")
        try:
            name = check_storage.safe_path(relative)
        except ValueError as exc:
            raise ManagedTreeError("tree.changed", "managed tree relative target changed") from exc
        return base / name

    def reserve(self, body: Mapping[str, object]) -> dict:
        tree_id = body.get("tree_id")
        nonce = _nonce(tree_id)
        if (body.get("format") != RESERVATION_KIND
                or body.get("staging") != f".workbench-tree-{nonce}.pending"
                or not isinstance(body.get("workspace"), str)
                or not Path(body["workspace"]).is_absolute()
                or type(body.get("parent_device")) is not int
                or type(body.get("parent_inode")) is not int
                or body.get("role") not in {"evidence", "artifacts"}):
            raise ManagedTreeError("tree.policy", "managed tree reservation is invalid")
        self._target(body)
        return self._write("reservations", nonce, RESERVATION_KIND, body)

    def reservation(self, tree_id: str) -> dict:
        nonce = _nonce(tree_id)
        value = self._read("reservations", nonce, RESERVATION_KIND)
        if (set(value) != {"id", "format", "tree_id", "store_id", "store_root", "relative_path",
                           "workspace", "owner_id", "role", "role_source", "policy_id",
                           "staging", "allocated_at", "parent_device", "parent_inode"}
                or value.get("tree_id") != tree_id
                or value.get("format") != RESERVATION_KIND
                or value.get("staging") != f".workbench-tree-{nonce}.pending"
                or not isinstance(value["workspace"], str) or not Path(value["workspace"]).is_absolute()
                or type(value.get("parent_device")) is not int
                or type(value.get("parent_inode")) is not int
                or not isinstance(value["owner_id"], str) or _OWNER.fullmatch(value["owner_id"]) is None
                or value["role"] not in {"evidence", "artifacts"}
                or not isinstance(value["role_source"], str)
                or value["policy_id"] is not None and not isinstance(value["policy_id"], str)):
            raise ManagedTreeError("tree.changed", "managed tree reservation identity changed")
        self._target(value)
        return value

    def intent(self, tree_id: str) -> dict:
        nonce = _nonce(tree_id)
        try:
            value = check_storage.read_json(self._path("intents", nonce), byte_limit=_MAX_RECORD_BYTES)
        except (OSError, ValueError) as exc:
            raise ManagedTreeError("tree.unavailable", "managed tree intent is unavailable") from exc
        kind = value.get("format") if isinstance(value, dict) else None
        if (not isinstance(kind, str)
                or kind not in {INTENT_KIND, DERIVED_INTENT_KIND, EXACT_INTENT_KIND}
                or value != _sealed(kind, {key: item for key, item in value.items() if key != "id"})):
            raise ManagedTreeError("tree.changed", "managed tree intent changed")
        reservation = self.reservation(tree_id)
        fields = {"id", "format", "tree_id", "reservation_id", "store_id",
                  "store_root", "relative_path", "workspace", "owner_id", "role",
                  "role_source", "policy_id", "staging", "parent_device", "parent_inode",
                  "device", "inode", "members", "content_sha256", "domain_id",
                  "references", "prepared_at"}
        if kind == DERIVED_INTENT_KIND:
            fields |= {"derived_manifest_rule", "derived_manifest_base_sha256"}
        elif kind == EXACT_INTENT_KIND:
            fields = (fields - {"members"}) | {
                "inventory_policy", "member_count", "file_count", "directory_count", "root_mode",
            }
        if (set(value) != fields
                or value.get("tree_id") != tree_id or value.get("format") != kind
                or value.get("reservation_id") != reservation["id"]
                or any(value.get(key) != reservation.get(key) for key in (
                    "store_id", "store_root", "relative_path", "workspace", "owner_id",
                    "role", "role_source", "policy_id", "staging", "parent_device", "parent_inode",
                ))
                or type(value.get("device")) is not int or type(value.get("inode")) is not int
                or not isinstance(value.get("references"), list)
                or len(value["references"]) > 128
                or any(not isinstance(item, str) or _REFERENCE.fullmatch(item) is None
                       for item in value["references"])
                or len(set(value["references"])) != len(value["references"])
                or value["domain_id"] is not None and (
                    not isinstance(value["domain_id"], str) or not 0 < len(value["domain_id"]) <= 512)
                or not isinstance(value.get("content_sha256"), str)
                or not _SHA.fullmatch(value["content_sha256"])):
            raise ManagedTreeError("tree.changed", "managed tree intent changed")
        if kind == EXACT_INTENT_KIND:
            if (value["inventory_policy"] != EXACT_INVENTORY_POLICY
                    or type(value["member_count"]) is not int
                    or type(value["file_count"]) is not int
                    or type(value["directory_count"]) is not int
                    or not 0 <= value["file_count"] <= MAX_FILES
                    or not 0 <= value["directory_count"] <= MAX_DIRECTORIES
                    or value["member_count"] != value["file_count"] + value["directory_count"]
                    or type(value["root_mode"]) is not int
                    or not 0 <= value["root_mode"] <= 0o7777):
                raise ManagedTreeError("tree.changed", "managed tree exact inventory metadata changed")
        else:
            members = _validate_members(value.get("members"))
            if value["content_sha256"] != _content_sha256(members):
                raise ManagedTreeError("tree.changed", "managed tree authoritative digest changed")
        if kind == DERIVED_INTENT_KIND:
            paths = {row["path"]: row for row in members}
            if (
                value["owner_id"] != "atlas"
                or value["derived_manifest_rule"] != "atlas-categorical-query-index-v1"
                or not isinstance(value["derived_manifest_base_sha256"], str)
                or _SHA.fullmatch(value["derived_manifest_base_sha256"]) is None
                or paths.get("manifest.json", {}).get("classification") != "authoritative"
                or paths.get("query-index.sqlite3", {}).get("classification") != "derived"
                or not isinstance(value["domain_id"], str)
                or _ATLAS_GRAPH_ID.fullmatch(value["domain_id"]) is None
            ):
                raise ManagedTreeError("tree.changed", "managed tree derived manifest rule changed")
        self._target(value)
        return value

    def commit(self, tree_id: str, intent: Mapping[str, object]) -> dict:
        nonce = _nonce(tree_id)
        value = self._read("commits", nonce, COMMIT_KIND)
        if (set(value) != {"id", "format", "tree_id", "intent_id", "committed_at"}
                or value["format"] != COMMIT_KIND or value["tree_id"] != tree_id
                or value["intent_id"] != intent["id"]):
            raise ManagedTreeError("tree.changed", "managed tree commit changed")
        return value

    def abort(self, tree_id: str, reason: str) -> dict:
        nonce = _nonce(tree_id)
        from datetime import datetime, timezone
        return self._write("aborts", nonce, ABORT_KIND, {
            "format": ABORT_KIND, "tree_id": tree_id, "reason": reason,
            "aborted_at": datetime.now(timezone.utc).isoformat(),
        })

    def _verify(self, intent: Mapping[str, object]) -> str:
        target = self._target(intent)
        try:
            selected = check_storage.ordinary(target, directory=True)
            info = selected.stat()
        except (OSError, ValueError) as exc:
            raise ManagedTreeError("tree.unavailable", "managed tree target is unavailable") from exc
        if (info.st_dev, info.st_ino) != (intent["device"], intent["inode"]):
            raise ManagedTreeError("tree.changed", "managed tree directory identity changed")
        if intent["format"] == EXACT_INTENT_KIND:
            self._verify_exact(intent, target)
            return "current"
        expected = intent["members"]
        derived = tuple(row["path"] for row in expected if row["classification"] == "derived")
        try:
            observed = inventory_members(target, derived_members=derived, require_derived=False)
        except (OSError, ValueError) as exc:
            raise ManagedTreeError("tree.changed", "managed tree members cannot be verified") from exc
        before = {row["path"]: row for row in expected}
        after = {row["path"]: row for row in observed}
        if any(path not in before for path in after):
            raise ManagedTreeError("tree.changed", "managed tree gained undeclared members")
        derived_manifest = intent["format"] == DERIVED_INTENT_KIND
        if any(after.get(path) != row for path, row in before.items()
               if row["classification"] == "authoritative"
               and not (derived_manifest and path == "manifest.json")):
            raise ManagedTreeError("tree.changed", "managed tree authoritative members changed")
        if derived_manifest:
            original = before["manifest.json"]
            current = after.get("manifest.json")
            if (current is None or current["kind"] != "file"
                    or current["mode"] != original["mode"]
                    or current["classification"] != "authoritative"
                    or atlas_manifest_baseline(
                        target, members=observed, domain_id=str(intent["domain_id"]),
                    ) != intent["derived_manifest_base_sha256"]):
                raise ManagedTreeError("tree.changed", "managed tree authoritative manifest changed")
        if any(path not in after for path, row in before.items()
               if row["classification"] == "derived"):
            return "missing"
        return "current" if observed == expected else "changed"

    def _verify_exact(
        self, intent: Mapping[str, object], target: Path,
    ) -> list[dict[str, object]]:
        try:
            observed, root_mode, files, directories = inventory_exact_members(target)
        except ManagedTreeError as exc:
            raise ManagedTreeError("tree.changed", "managed tree exact members cannot be verified") from exc
        if (root_mode != intent["root_mode"]
                or files != intent["file_count"]
                or directories != intent["directory_count"]
                or len(observed) != intent["member_count"]
                or exact_content_sha256(observed) != intent["content_sha256"]):
            raise ManagedTreeError("tree.changed", "managed tree exact members changed")
        return observed

    def _reference(
        self, intent: Mapping[str, object], *, derived_status: str,
        members: list[dict[str, object]] | None = None,
    ) -> ManagedTreeReference:
        if intent["format"] == EXACT_INTENT_KIND:
            # The compact intent contains only a digest. A reference still
            # exposes full verified rows to existing API consumers.
            member_rows = self._verify_exact(intent, self._target(intent)) if members is None else members
        else:
            member_rows = intent["members"]
        return ManagedTreeReference(
            tree_id=str(intent["tree_id"]), store_id=str(intent["store_id"]),
            owner_id=str(intent["owner_id"]), workspace=Path(intent["workspace"]),
            role=str(intent["role"]), path=self._target(intent),
            content_sha256="sha256:" + str(intent["content_sha256"]),
            members=tuple(member_rows), derived_status=derived_status,
            references=tuple(intent["references"]),
            policy_id=intent["policy_id"], domain_id=intent["domain_id"],
            inventory_policy=(EXACT_INVENTORY_POLICY if intent["format"] == EXACT_INTENT_KIND
                              else "portable-v1"),
        )

    def describe(self, tree_id: str, *, workspace: Path | None = None) -> ManagedTreeReference:
        with self.lease(tree_id):
            intent = self.intent(tree_id)
            if workspace is not None and intent["workspace"] != str(workspace):
                raise ManagedTreeError("tree.scope", "managed tree belongs to another workspace")
            self.commit(tree_id, intent)
            derived_status = self._verify(intent)
            return self._reference(intent, derived_status=derived_status)

    def reconcile(
        self, tree_id: str, *, workspace: Path | None = None,
        publish_prepared: Callable[[Path, Path, Mapping[str, object]], None] | None = None,
    ) -> ManagedTreeReference:
        with self.lease(tree_id, exclusive=True):
            intent = self.intent(tree_id)
            if workspace is not None and intent["workspace"] != str(workspace):
                raise ManagedTreeError("tree.scope", "managed tree belongs to another workspace")
            try:
                derived_status = self._verify(intent)
            except ManagedTreeError as exc:
                if exc.code != "tree.unavailable" or publish_prepared is None:
                    raise
                # An intent exists only after owner validation and an exact
                # before/after inventory. The unchanged private payload may be
                # published under the same tree lease after an abrupt exit.
                nonce = _nonce(tree_id)
                if self._path("aborts", nonce).is_file():
                    raise ManagedTreeError("tree.failed", "aborted tree publication requires a new operation") from exc
                target = self._target(intent)
                staged = target.parent / intent["staging"] / "payload"
                selected = check_storage.ordinary(staged, directory=True)
                info = selected.stat()
                if (info.st_dev, info.st_ino) != (intent["device"], intent["inode"]):
                    raise ManagedTreeError("tree.changed", "managed tree staging identity changed")
                if intent["format"] == EXACT_INTENT_KIND:
                    self._verify_exact(intent, staged)
                else:
                    derived = tuple(row["path"] for row in intent["members"] if row["classification"] == "derived")
                    if inventory_members(staged, derived_members=derived) != intent["members"]:
                        raise ManagedTreeError("tree.changed", "managed tree staging members changed")
                publish_prepared(staged, target, intent)
                derived_status = self._verify(intent)
            try:
                self.commit(tree_id, intent)
            except ManagedTreeError as exc:
                if exc.code != "tree.unavailable":
                    raise
                from datetime import datetime, timezone
                self._write("commits", _nonce(tree_id), COMMIT_KIND, {
                    "format": COMMIT_KIND, "tree_id": tree_id,
                    "intent_id": intent["id"],
                    "committed_at": datetime.now(timezone.utc).isoformat(),
                })
            return self._reference(intent, derived_status=derived_status)

    def inventory(
        self, *, workspace: Path | None = None,
        validate_references: Callable[[str, str, tuple[str, ...]], None] | None = None,
    ) -> list[dict[str, object]]:
        if not self.root.exists() and not self.root.is_symlink():
            return []
        names = ("reservations", "intents", "commits", "aborts", "leases")
        try:
            check_storage.ordinary(self.root, directory=True)
            if not private_path(self.root, directory=True):
                raise ValueError("managed tree catalog lost private custody")
            if {path.name for path in self.root.iterdir()} != set(names):
                raise ValueError("managed tree catalog has an unknown or missing namespace")
            for name in names:
                directory = self._directory(name)
                check_storage.ordinary(directory, directory=True)
                if not private_path(directory, directory=True):
                    raise ValueError("managed tree namespace lost private custody")
                suffix = ".lock" if name == "leases" else ".json"
                for path in directory.iterdir():
                    info = path.lstat()
                    if (re.fullmatch(r"[0-9a-f]{32}" + re.escape(suffix), path.name) is None
                            or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or not private_path(path, directory=False)):
                        raise ValueError("managed tree catalog has an unknown or unsafe entry")
                    if (name in {"intents", "commits", "aborts"}
                            and not self._path("reservations", path.stem).is_file()):
                        raise ValueError("managed tree catalog has an orphan record")
                    if name == "commits" and not self._path("intents", path.stem).is_file():
                        raise ValueError("managed tree catalog has an orphan commit")
            # A lease may precede its reservation if staging was interrupted.
        except (OSError, ValueError) as exc:
            raise ManagedTreeError("tree.changed", "managed tree catalog inventory is unavailable or changed") from exc
        result = []
        for path in sorted(self._directory("reservations").glob("*.json")):
            tree_id = f"workbench-tree-v1:{path.stem}"
            reservation = self.reservation(tree_id)
            selected = workspace is None or reservation["workspace"] == str(workspace)
            intent_path = self._path("intents", path.stem)
            intent = (
                self.intent(tree_id)
                if intent_path.is_file() and (selected or validate_references is not None)
                else None
            )
            if intent is not None and validate_references is not None:
                validate_references(tree_id, str(reservation["workspace"]), tuple(intent["references"]))
            if not selected:
                continue
            target = self._target(reservation)
            stage = target.parent / reservation["staging"] / "payload"
            if intent is not None:
                try:
                    derived_status = self._verify(intent)
                    try:
                        self.commit(tree_id, intent)
                    except ManagedTreeError as exc:
                        status = "published-uncommitted" if exc.code == "tree.unavailable" else "changed"
                    else:
                        status = "committed"
                except ManagedTreeError as exc:
                    derived_status = None
                    aborted = self._path("aborts", path.stem).is_file()
                    status = (
                        "failed" if aborted and exc.code == "tree.unavailable"
                        else "conflict" if aborted else
                        "incomplete" if exc.code == "tree.unavailable" and (stage.exists() or stage.is_symlink()) else
                        "unavailable" if exc.code == "tree.unavailable" else "changed"
                    )
                content_sha256 = "sha256:" + intent["content_sha256"]
                member_count = (intent["member_count"] if intent["format"] == EXACT_INTENT_KIND
                                else len(intent["members"]))
                references = intent["references"]
            else:
                status = ("failed" if self._path("aborts", path.stem).is_file()
                          else "incomplete" if stage.exists() or stage.is_symlink()
                          else "conflict" if target.exists() or target.is_symlink()
                          else "allocated")
                content_sha256 = None
                member_count = 0
                references = []
                derived_status = None
            result.append({
                "tree_id": tree_id, "owner_id": reservation["owner_id"],
                "workspace": reservation["workspace"], "role": reservation["role"],
                "store_id": reservation["store_id"], "path": str(target),
                "staging": str(stage), "status": status,
                "content_sha256": content_sha256, "member_count": member_count,
                "references": references, "derived_status": derived_status,
                "retention": "protected-until-reviewed-policy",
            })
        return result


__all__ = ["TreeCatalog", "inventory_members", "atlas_manifest_baseline",
           "INTENT_KIND", "DERIVED_INTENT_KIND", "EXACT_INTENT_KIND", "COMMIT_KIND", "RESERVATION_KIND",
           "_content_sha256", "_store_id"]
