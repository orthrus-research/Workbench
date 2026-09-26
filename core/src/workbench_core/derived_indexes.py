"""Core transaction for Atlas's disposable SQLite index and manifest descriptor.

An attempt is retained beside the graph, on the same filesystem and outside
its managed tree. The graph owner validates authoritative streams and SQL;
Core owns the lease, staging, exact byte comparisons, two replacements and
restart classification. A replacement is never inferred from graph ID alone.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Iterator, Mapping
from uuid import uuid4

from workbench_api.derived_indexes import DerivedIndexAttempt, DerivedIndexError
from workbench_api.managed_trees import ManagedTreeError

from . import check_storage
from .host_filesystem import file_lease, fsync_directory, private_path, secure_private_path
from .storage.registered import ResourceCatalog
from .storage.tree_catalog import DERIVED_INTENT_KIND


_GRAPH_ID = re.compile(r"workbench-atlas-graph-set-v[23]:sha256:[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST = 16 * 1024 * 1024
_INDEX = "query-index.sqlite3"
_MANIFEST = "manifest.json"
_RULE = "atlas-categorical-query-index-v1"
_JOURNAL = "workbench-atlas-derived-index-attempt-v1"
_ALLOCATION = "workbench-atlas-derived-index-allocation-v1"
_COMPLETE = "workbench-atlas-derived-index-complete-v1"
_SUPERSEDED = "workbench-atlas-derived-index-superseded-v1"


def _identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino]


def _file(path: Path, *, limit: int | None = None) -> tuple[dict[str, object], bytes | None]:
    """Measure one independent ordinary file without following redirects."""
    try:
        visible = path.lstat()
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1:
            raise DerivedIndexError("changed", f"derived-index file is not independent: {path}")
        if limit is not None and visible.st_size > limit:
            raise DerivedIndexError("bounds", f"derived-index file exceeds its bound: {path}")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            before = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (visible.st_dev, visible.st_ino, visible.st_size, visible.st_mtime_ns, visible.st_ctime_ns)
            ):
                raise DerivedIndexError("changed", f"derived-index file changed during open: {path}")
            digest = sha256()
            data = bytearray() if limit is not None else None
            size = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                size += len(chunk)
                if limit is not None and size > limit:
                    raise DerivedIndexError("bounds", f"derived-index file exceeds its bound: {path}")
                digest.update(chunk)
                if data is not None:
                    data.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        last = path.lstat()
        fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                               info.st_ctime_ns, info.st_mode, info.st_nlink)
        if fields(before) != fields(after) or fields(after) != fields(last) or size != after.st_size:
            raise DerivedIndexError("changed", f"derived-index file changed during read: {path}")
        return ({"device": after.st_dev, "inode": after.st_ino, "size": size,
                 "sha256": digest.hexdigest(), "mode": stat.S_IMODE(after.st_mode),
                 "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns},
                bytes(data) if data is not None else None)
    except DerivedIndexError:
        raise
    except OSError as exc:
        raise DerivedIndexError("unavailable", f"derived-index file is unavailable: {path}") from exc


def _optional_file(path: Path) -> dict[str, object] | None:
    if not path.exists() and not path.is_symlink():
        return None
    return _file(path)[0]


def _same(left: dict[str, object] | None, right: dict[str, object] | None) -> bool:
    return left == right


def _relocated(left: dict[str, object] | None, right: dict[str, object] | None) -> bool:
    """Rename changes ctime; the inode, content and mode must remain exact."""
    if left is None or right is None:
        return left is right
    return all(left[key] == right[key] for key in (
        "device", "inode", "size", "sha256", "mode", "mtime_ns",
    ))


def _canonical_manifest(raw: bytes, graph_set_id: str) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate Atlas manifest key")
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        if (type(value) is not dict or value.get("graph_set_id") != graph_set_id
                or "query_index" not in value
                or json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                              allow_nan=False).encode("utf-8") + b"\n" != raw):
            raise ValueError("Atlas manifest is not canonical or graph-bound")
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise DerivedIndexError("manifest", "Atlas manifest is not canonical or graph-bound") from exc


def _stage_root(root: Path) -> Path:
    return root.parent / f".{root.name}.derived-index-core"


def _sealed(kind: str, body: Mapping[str, object]) -> dict:
    return check_storage.seal(kind, dict(body))


def _read_record(path: Path, kind: str) -> dict:
    try:
        if not private_path(path, directory=False):
            raise ValueError("record is not private")
        value = check_storage.read_json(path, byte_limit=8192)
        if not isinstance(value, dict) or value != _sealed(kind, {k: v for k, v in value.items() if k != "id"}):
            raise ValueError("record seal changed")
        return value
    except (OSError, ValueError) as exc:
        raise DerivedIndexError("changed", "derived-index attempt record changed") from exc


def _write_record(path: Path, kind: str, body: Mapping[str, object]) -> dict:
    value = _sealed(kind, body)
    check_storage.write_json(path, value, byte_limit=8192)
    return value


class _Stage:
    def __init__(self, host: CoreDerivedIndexes, root: Path, directory: Path,
                 graph_set_id: str, parent_id: list[int], root_id: list[int],
                 prior_manifest: dict[str, object], prior_raw: bytes,
                 prior_index: dict[str, object] | None,
                 predecessors: tuple[DerivedIndexAttempt, ...]):
        self.host = host
        self.root = root
        self.directory = directory
        self.path = directory / _INDEX
        self.graph_set_id = graph_set_id
        self.parent_id = parent_id
        self.root_id = root_id
        self.prior_manifest = prior_manifest
        self.prior_raw = prior_raw
        self.prior_index = prior_index
        self.predecessors = predecessors
        self.directory_id = _identity(directory.stat())
        self.prepared = False

    def _recheck(self, *, index: dict[str, object] | None,
                 relocated_index: bool = False) -> None:
        try:
            if (_identity(check_storage.ordinary(self.root.parent, directory=True).stat()) != self.parent_id
                    or _identity(check_storage.ordinary(self.root, directory=True).stat()) != self.root_id):
                raise DerivedIndexError("changed", "Atlas graph or parent directory changed")
        except (OSError, ValueError) as exc:
            raise DerivedIndexError("changed", "Atlas graph or parent directory changed") from exc
        current, raw = _file(self.root / _MANIFEST, limit=_MAX_MANIFEST)
        if current != self.prior_manifest or raw != self.prior_raw:
            raise DerivedIndexError("changed", "Atlas manifest changed before descriptor publication")
        current_index = _optional_file(self.root / _INDEX)
        if not (_relocated(current_index, index) if relocated_index else _same(current_index, index)):
            raise DerivedIndexError("changed", "Atlas index changed before replacement")

    def publish(self, *, manifest_bytes: bytes, expected_size: int,
                expected_sha256: str, validate_source: Callable[[Path], object]) -> DerivedIndexAttempt:
        if self.prepared:
            raise DerivedIndexError("state", "derived-index publication was already attempted")
        if (not callable(validate_source) or type(manifest_bytes) is not bytes
                or len(manifest_bytes) > _MAX_MANIFEST
                or type(expected_size) is not int or expected_size < 0
                or not isinstance(expected_sha256, str) or _SHA.fullmatch(expected_sha256) is None):
            raise DerivedIndexError("policy", "derived-index publication inputs are invalid")
        before = _canonical_manifest(self.prior_raw, self.graph_set_id)
        after = _canonical_manifest(manifest_bytes, self.graph_set_id)
        old_descriptor = before.pop("query_index")
        descriptor = after.pop("query_index")
        if (before != after or type(descriptor) is not dict
                or descriptor != {"role": "derived-disposable-index", "file": _INDEX,
                                  "size": expected_size, "sha256": expected_sha256,
                                  "derived_from_graph_set_id": self.graph_set_id}):
            raise DerivedIndexError("manifest", "Atlas manifest changed beyond its derived descriptor")
        before["query_index"] = old_descriptor
        after["query_index"] = descriptor
        if not self.path.is_file() or self.path.is_symlink():
            raise DerivedIndexError("stage", "derived-index stage is missing or redirected")
        staged, _ = _file(self.path)
        if staged["size"] != expected_size or staged["sha256"] != expected_sha256:
            raise DerivedIndexError("stage", "derived-index stage differs from owner proof")
        # The owner must revalidate all authoritative streams after SQL build.
        validated = validate_source(self.root)
        if not isinstance(validated, dict) or validated.get("graph_set_id") != self.graph_set_id:
            raise DerivedIndexError("source", "Atlas source validation did not preserve graph identity")
        self._recheck(index=self.prior_index)
        staged_again, _ = _file(self.path)
        if staged_again != staged:
            raise DerivedIndexError("stage", "derived-index stage changed during source validation")
        descriptor_fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor_fd)
        finally:
            os.close(descriptor_fd)
        replacement = self.directory / "manifest.new.json"
        fd = os.open(replacement, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, int(self.prior_manifest["mode"]))
            else:  # pragma: no cover - Windows host qualification
                os.chmod(replacement, int(self.prior_manifest["mode"]))
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(manifest_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        new_manifest, _ = _file(replacement, limit=_MAX_MANIFEST)
        if new_manifest["mode"] != self.prior_manifest["mode"]:
            raise DerivedIndexError("stage", "Atlas manifest mode changed during staging")
        fsync_directory(self.directory)
        intent = _write_record(self.directory / "intent.json", _JOURNAL, {
            "format": _JOURNAL, "root": str(self.root),
            "parent_identity": self.parent_id, "root_identity": self.root_id,
            "graph_set_id": self.graph_set_id,
            "prior_manifest": self.prior_manifest, "new_manifest": new_manifest,
            "prior_index": self.prior_index, "new_index": staged,
        })
        self.prepared = True
        # The owner has explicitly revalidated the graph and prepared a new
        # repair. Earlier partial attempts can no longer be promoted by Core.
        for earlier in self.predecessors:
            if earlier.state in {"prepared", "index-replaced", "manifest-replaced", "complete", "changed"}:
                _write_record(self.directory.parent / earlier.attempt_id / "superseded.json", _SUPERSEDED, {
                    "format": _SUPERSEDED, "next_intent_id": intent["id"],
                })
        self._recheck(index=self.prior_index)
        if _file(self.path)[0] != staged or _file(replacement, limit=_MAX_MANIFEST)[0] != new_manifest:
            raise DerivedIndexError("stage", "derived-index stage changed before replacement")
        os.replace(self.path, self.root / _INDEX)
        fsync_directory(self.root)
        if not _relocated(_file(self.root / _INDEX)[0], staged):
            raise DerivedIndexError("changed", "published Atlas index changed")
        # A partial transaction intentionally leaves the old manifest and a
        # visible mismatch. Inspect classifies this state for explicit repair.
        self._recheck(index=staged, relocated_index=True)
        if _file(replacement, limit=_MAX_MANIFEST)[0] != new_manifest:
            raise DerivedIndexError("stage", "staged Atlas manifest changed before replacement")
        os.replace(replacement, self.root / _MANIFEST)
        fsync_directory(self.root)
        if (not _relocated(_file(self.root / _MANIFEST, limit=_MAX_MANIFEST)[0], new_manifest)
                or not _relocated(_file(self.root / _INDEX)[0], staged)):
            raise DerivedIndexError("changed", "published Atlas derived bytes changed")
        _write_record(self.directory / "complete.json", _COMPLETE, {
            "format": _COMPLETE, "intent_id": intent["id"],
        })
        return DerivedIndexAttempt(self.directory.name, self.root, "complete")


class CoreDerivedIndexes:
    def __init__(self, *, configuration_home: Path, workspace: Path | None = None,
                 owner_id: str = "atlas"):
        if (not configuration_home.is_absolute() or workspace is not None and not workspace.is_absolute()
                or owner_id != "atlas"):
            raise DerivedIndexError("policy", "Atlas derived-index host needs exact Core roots and owner")
        self.configuration_home = configuration_home
        self.workspace = workspace
        self.owner_id = owner_id

    def _root(self, root: Path) -> tuple[Path, list[int], list[int]]:
        if not isinstance(root, Path) or not root.is_absolute() or root.name in {"", ".", ".."}:
            raise DerivedIndexError("path", "select an absolute Atlas graph directory")
        try:
            parent = check_storage.ordinary(root.parent, directory=True)
            graph = check_storage.ordinary(root, directory=True)
            return graph, _identity(parent.stat()), _identity(graph.stat())
        except (OSError, ValueError) as exc:
            raise DerivedIndexError("path", "Atlas graph path is unavailable or redirecting") from exc

    @contextmanager
    def _lease(self, root: Path, parent_id: list[int], root_id: list[int]) -> Iterator[Path]:
        holder = _stage_root(root)
        try:
            holder.mkdir(mode=0o700)
            secure_private_path(holder, directory=True)
            _write_record(holder / "root.json", _ALLOCATION, {
                "format": _ALLOCATION, "root": str(root),
                "parent_identity": parent_id, "root_identity": root_id,
            })
            fsync_directory(holder.parent)
        except FileExistsError:
            if not private_path(holder, directory=True):
                raise DerivedIndexError("changed", "Atlas derived-index custody directory is not private")
        record = _read_record(holder / "root.json", _ALLOCATION)
        if (record.get("root") != str(root) or record.get("parent_identity") != parent_id
                or record.get("root_identity") != root_id):
            raise DerivedIndexError("changed", "Atlas derived-index root identity changed")
        if self.workspace is not None:
            # Dispatch supplies the selected workspace independently of the
            # graph path, which may live in an external evidence store. Bind
            # this attempt namespace before exposing a new SQLite stage.
            ResourceCatalog(self.configuration_home).register_record_store(
                family="atlas-derived-index-v1", owner_id=self.owner_id,
                workspace=self.workspace, root=holder,
            )
        lock = holder / "owner.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not private_path(lock, directory=False)):
                raise DerivedIndexError("changed", "Atlas derived-index lease changed")
            try:
                with file_lease(descriptor, exclusive=True):
                    yield holder
            except BlockingIOError as exc:
                raise DerivedIndexError("busy", "Atlas derived-index graph is in use") from exc
        finally:
            os.close(descriptor)

    @contextmanager
    def _managed(self, root: Path, graph_set_id: str) -> Iterator[None]:
        catalog = ResourceCatalog(self.configuration_home)
        trees = catalog.trees
        matching = [row for row in trees.inventory() if row["path"] == str(root)]
        if len(matching) > 1:
            raise DerivedIndexError("managed", "Atlas graph has ambiguous managed-tree custody")
        if not matching:
            yield
            return
        tree_id = str(matching[0]["tree_id"])
        try:
            with trees.lease(tree_id, exclusive=True):
                intent = trees.intent(tree_id)
                if (intent["owner_id"] != self.owner_id or intent["format"] != DERIVED_INTENT_KIND
                        or intent["derived_manifest_rule"] != _RULE
                        or intent["domain_id"] != graph_set_id
                        or self.workspace is not None and intent["workspace"] != str(self.workspace)):
                    raise DerivedIndexError("managed", "Atlas managed graph lacks the exact derived-index rule")
                trees.commit(tree_id, intent)
                trees._verify(intent)  # Authoritative bytes and the V2 baseline must still match.
                yield
                trees._verify(intent)
        except ManagedTreeError as exc:
            raise DerivedIndexError("managed", f"Atlas managed graph custody changed: {exc}") from exc

    @contextmanager
    def stage(self, root: Path, *, graph_set_id: str) -> Iterator[_Stage]:
        if not isinstance(graph_set_id, str) or _GRAPH_ID.fullmatch(graph_set_id) is None:
            raise DerivedIndexError("policy", "Atlas graph-set identity is invalid")
        graph, parent_id, root_id = self._root(root)
        with self._managed(graph, graph_set_id), self._lease(graph, parent_id, root_id) as holder:
            attempts = self._inspect_locked(graph, holder, parent_id, root_id)
            if any(item.state == "conflict" for item in attempts):
                raise DerivedIndexError("conflict", "an earlier Atlas derived-index attempt changed")
            prior_manifest, prior_raw = _file(graph / _MANIFEST, limit=_MAX_MANIFEST)
            assert prior_raw is not None
            _canonical_manifest(prior_raw, graph_set_id)
            prior_index = _optional_file(graph / _INDEX)
            directory = holder / f"attempt-{uuid4().hex}"
            directory.mkdir(mode=0o700)
            secure_private_path(directory, directory=True)
            fsync_directory(holder)
            stage = _Stage(self, graph, directory, graph_set_id, parent_id, root_id,
                           prior_manifest, prior_raw, prior_index, attempts)
            try:
                yield stage
            finally:
                if not stage.prepared:
                    try:
                        same_stage = _identity(check_storage.ordinary(directory, directory=True).stat()) == stage.directory_id
                    except (OSError, ValueError):
                        same_stage = False
                    if same_stage:
                        # Remove only an empty stage. SQLite or an unexpected
                        # child could have changed outside our lease; retain
                        # such a stage for explicit classification and review.
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
                        else:
                            fsync_directory(holder)

    def _inspect_locked(self, root: Path, holder: Path, parent_id: list[int],
                        root_id: list[int]) -> tuple[DerivedIndexAttempt, ...]:
        result = []
        for directory in sorted(holder.glob("attempt-*")):
            if not private_path(directory, directory=True):
                raise DerivedIndexError("changed", "Atlas derived-index attempt directory changed")
            attempt = DerivedIndexAttempt(directory.name, root, "allocated")
            intent_path = directory / "intent.json"
            if not intent_path.exists() and not intent_path.is_symlink():
                result.append(attempt)
                continue
            intent = _read_record(intent_path, _JOURNAL)
            if (intent.get("root") != str(root) or intent.get("parent_identity") != parent_id
                    or intent.get("root_identity") != root_id):
                result.append(DerivedIndexAttempt(directory.name, root, "conflict"))
                continue
            superseded = directory / "superseded.json"
            if superseded.exists() or superseded.is_symlink():
                record = _read_record(superseded, _SUPERSEDED)
                state = "superseded" if isinstance(record.get("next_intent_id"), str) else "conflict"
                result.append(DerivedIndexAttempt(directory.name, root, state))
                continue
            manifest = _optional_file(root / _MANIFEST)
            index = _optional_file(root / _INDEX)
            old_manifest, new_manifest = intent["prior_manifest"], intent["new_manifest"]
            old_index, new_index = intent["prior_index"], intent["new_index"]
            completed = directory / "complete.json"
            if completed.exists() or completed.is_symlink():
                receipt = _read_record(completed, _COMPLETE)
                state = (
                    "conflict" if receipt.get("intent_id") != intent["id"] else
                    "complete" if _relocated(manifest, new_manifest) and _relocated(index, new_index) else
                    "changed"
                )
                result.append(DerivedIndexAttempt(directory.name, root, state))
                continue
            if manifest == old_manifest and index == old_index:
                state = "prepared"
            elif manifest == old_manifest and _relocated(index, new_index):
                state = "index-replaced"
            elif _relocated(manifest, new_manifest) and _relocated(index, new_index):
                state = "manifest-replaced"
            else:
                state = "conflict"
            result.append(DerivedIndexAttempt(directory.name, root, state))
        return tuple(result)

    def inspect(self, root: Path) -> tuple[DerivedIndexAttempt, ...]:
        graph, parent_id, root_id = self._root(root)
        holder = _stage_root(graph)
        if not holder.exists() and not holder.is_symlink():
            return ()
        with self._lease(graph, parent_id, root_id) as selected:
            return self._inspect_locked(graph, selected, parent_id, root_id)


__all__ = ["CoreDerivedIndexes"]
