"""Core V2 custody for large immutable transport directories.

This catalog is deliberately separate from ManagedTrees V1. An owner validates
the staged payload before Core seals a compact streaming inventory, flushes it,
and publishes the exact original directory with atomic no-replace semantics.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable, Iterator, Mapping
import unicodedata
from uuid import uuid4

from workbench_api.managed_trees import ManagedTreeError
from workbench_api.transport_trees import TransportTreeError, TransportTreeReference

from . import check_storage
from .durable_files import _directory as pinned_directory
from .durable_records import DurableRecordError, private_record_lock, publish_immutable_bytes
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .managed_trees import _ensure_parent, _rename_no_replace
from .output_routing import _WINDOWS_RESERVED, _private_directory
from .storage.registered import ResourceCatalog
from .storage.tree_catalog import _store_id


_ID = re.compile(r"workbench-transport-tree-v2:([0-9a-f]{32})\Z")
_OWNER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RESERVATION = "workbench-transport-reservation-v2"
_INTENT = "workbench-transport-intent-v2"
_COMMIT = "workbench-transport-commit-v2"
_ABORT = "workbench-transport-abort-v2"
_RECORD_LIMIT = 1024 * 1024

# One V1 public-export manifest wraps the complete accepted 10,000-file tree.
# Each source path can contribute 31 unique parent directories, plus tree/.
MAX_FILES = 10_001
MAX_DIRECTORIES = 310_001
MAX_TOTAL_BYTES = 128 * 1024 * 1024 + 8 * 1024 * 1024
MAX_PATH_DEPTH = 33
MAX_PATH_BYTES = 517  # "tree/" plus the 512-byte public source path.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _nonce(tree_id: str) -> str:
    match = _ID.fullmatch(tree_id) if isinstance(tree_id, str) else None
    if match is None:
        raise TransportTreeError("transport.id", "select an exact V2 transport tree ID")
    return match.group(1)


def _sealed(kind: str, body: Mapping[str, object]) -> dict:
    return check_storage.seal(kind, dict(body))


def _path(relative: str) -> None:
    try:
        parts = check_storage.safe_path(relative).parts
        if (len(parts) > MAX_PATH_DEPTH or len(relative.encode("utf-8", "strict")) > MAX_PATH_BYTES
                or unicodedata.normalize("NFC", relative) != relative):
            raise ValueError("transport path is outside its bound")
        for part in parts:
            if (part.endswith((" ", ".")) or any(ord(char) < 32 or ord(char) == 127 for char in part)
                    or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED):
                raise ValueError("transport path is not portable")
    except (UnicodeError, ValueError) as exc:
        raise TransportTreeError("transport.members", f"transport member path is unsafe: {relative!r}") from exc


@dataclass(frozen=True, slots=True)
class TransportInventory:
    inventory_sha256: str
    file_count: int
    directory_count: int
    total_bytes: int


def summarize_rows(rows: Iterator[dict[str, object]]) -> TransportInventory:
    """Hash canonical ordered rows without retaining a member list."""

    digest = sha256(b"workbench-transport-inventory-v2\n")
    files = directories = total = 0
    for row in rows:
        if row["kind"] == "directory":
            directories += 1
        else:
            files += 1
            total += int(row["size"])
        if files > MAX_FILES or directories > MAX_DIRECTORIES or total > MAX_TOTAL_BYTES:
            raise TransportTreeError("transport.bounds", "transport tree exceeds public export capacity")
        digest.update(check_storage.canonical(row))
        digest.update(b"\n")
    if files == 0:
        raise TransportTreeError("transport.members", "transport tree has no files")
    return TransportInventory(digest.hexdigest(), files, directories, total)


def _mount_id(directory_fd: int) -> int:
    if sys.platform != "linux":
        raise TransportTreeError("transport.filesystem", "transport mount identity is unavailable")
    try:
        with open(f"/proc/self/fdinfo/{directory_fd}", encoding="ascii") as info:
            for line in info:
                key, separator, value = line.partition(":")
                if key == "mnt_id" and separator:
                    result = int(value.strip())
                    if result > 0:
                        return result
    except (OSError, ValueError) as exc:
        raise TransportTreeError("transport.filesystem", "transport mount identity is unavailable") from exc
    raise TransportTreeError("transport.filesystem", "transport mount identity is unavailable")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns)


def inventory_tree(root: Path, *, flush: bool = False) -> TransportInventory:
    """Replay a pinned, ordinary tree with bounded memory and exact modes."""

    if os.name != "posix":  # Windows requires a native pinned-directory walker.
        raise TransportTreeError("transport.filesystem", "V2 transport inventory needs POSIX directory handles")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = pinned_directory(root.parent, create=False)
        try:
            root_fd = os.open(root.name, flags, dir_fd=parent_fd)
            try:
                initial = os.fstat(root_fd)
                if not stat.S_ISDIR(initial.st_mode) or stat.S_IMODE(initial.st_mode) != 0o700:
                    raise TransportTreeError("transport.members", "transport root changed type or mode")
                mount_id = _mount_id(root_fd)
                if _mount_id(parent_fd) != mount_id:
                    raise TransportTreeError("transport.members", "transport root crosses another mount")

                def visit(directory_fd: int, prefix: str) -> Iterator[dict[str, object]]:
                    before = os.fstat(directory_fd)
                    if not stat.S_ISDIR(before.st_mode) or _mount_id(directory_fd) != mount_id:
                        raise TransportTreeError("transport.members", "transport tree crosses another mount")
                    with os.scandir(directory_fd) as scanned:
                        entries = sorted(scanned, key=lambda entry: os.fsencode(entry.name))
                    folded: set[str] = set()
                    for entry in entries:
                        name = entry.name
                        relative = f"{prefix}/{name}" if prefix else name
                        _path(relative)
                        key = unicodedata.normalize("NFC", name).casefold()
                        if key in folded:
                            raise TransportTreeError("transport.members", "transport member names collide by case")
                        folded.add(key)
                        visible = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        mode = stat.S_IMODE(visible.st_mode)
                        if stat.S_ISDIR(visible.st_mode):
                            if mode != 0o755:
                                raise TransportTreeError("transport.members", "transport directory mode changed")
                            yield {"path": relative, "kind": "directory", "mode": mode}
                            child_fd = os.open(name, flags, dir_fd=directory_fd)
                            try:
                                if _identity(os.fstat(child_fd)) != _identity(visible):
                                    raise TransportTreeError("transport.changed", "transport directory changed during inventory")
                                yield from visit(child_fd, relative)
                            finally:
                                os.close(child_fd)
                        elif stat.S_ISREG(visible.st_mode):
                            if mode not in {0o644, 0o755} or visible.st_nlink != 1:
                                raise TransportTreeError("transport.members", "transport file mode or links changed")
                            file_fd = os.open(
                                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=directory_fd,
                            )
                            try:
                                opened = os.fstat(file_fd)
                                if _identity(opened) != _identity(visible) or opened.st_size != visible.st_size:
                                    raise TransportTreeError("transport.changed", "transport file changed during inventory")
                                content = sha256()
                                size = 0
                                while chunk := os.read(file_fd, 1024 * 1024):
                                    content.update(chunk)
                                    size += len(chunk)
                                    if size > visible.st_size or size > MAX_TOTAL_BYTES:
                                        raise TransportTreeError("transport.changed", "transport file grew during inventory")
                                if flush:
                                    os.fsync(file_fd)
                                observed = os.fstat(file_fd)
                                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                                if (size != visible.st_size or _identity(observed) != _identity(visible)
                                        or _identity(current) != _identity(visible)):
                                    raise TransportTreeError("transport.changed", "transport file changed during inventory")
                                yield {"path": relative, "kind": "file", "mode": mode,
                                       "size": size, "sha256": content.hexdigest()}
                            finally:
                                os.close(file_fd)
                        else:
                            raise TransportTreeError("transport.members", "transport tree contains a symlink or special file")
                    if flush:
                        os.fsync(directory_fd)
                    if _identity(os.fstat(directory_fd)) != _identity(before):
                        raise TransportTreeError("transport.changed", "transport directory changed during inventory")

                with closing(visit(root_fd, "")) as rows:
                    result = summarize_rows(rows)
                visible = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
                if _identity(os.fstat(root_fd)) != _identity(initial) or _identity(visible) != _identity(initial):
                    raise TransportTreeError("transport.changed", "transport root changed during inventory")
                return result
            finally:
                os.close(root_fd)
        finally:
            os.close(parent_fd)
    except TransportTreeError:
        raise
    except (OSError, ValueError) as exc:
        raise TransportTreeError("transport.changed", f"transport tree is unavailable or changed: {exc}") from exc


class _Stage:
    def __init__(self, host: CoreTransportTrees, reservation: dict):
        self.host = host
        self.reservation = reservation
        self.tree_id = reservation["tree_id"]
        self.target = Path(reservation["path"])
        self.staging_root = Path(reservation["staging"])
        self.path = self.staging_root / "payload"
        self.renamed = False
        self.committed = False

    def publish(self, *, validate: Callable[[Path], object],
                domain_id: str) -> TransportTreeReference:
        if self.committed or self.renamed:
            raise TransportTreeError("transport.state", "transport publication was already attempted")
        if not callable(validate) or type(domain_id) is not str or not 0 < len(domain_id) <= 512:
            raise TransportTreeError("transport.owner", "transport publication needs a validator and domain identity")
        original = self.path.lstat()
        before = inventory_tree(self.path)
        validate(self.path)
        if inventory_tree(self.path) != before:
            raise TransportTreeError("transport.changed", "transport tree changed during owner validation")
        if inventory_tree(self.path, flush=True) != before or inventory_tree(self.path) != before:
            raise TransportTreeError("transport.changed", "transport tree changed while flushing")
        if not private_path(self.staging_root, directory=True):
            raise TransportTreeError("transport.changed", "transport staging root lost private custody")
        fsync_directory(self.staging_root)
        fsync_directory(self.target.parent)
        info = self.path.lstat()
        if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
            raise TransportTreeError("transport.changed", "transport root changed identity during preparation")
        nonce = _nonce(self.tree_id)
        intent = self.host._write("intents", nonce, _INTENT, {
            "format": _INTENT, "tree_id": self.tree_id,
            "reservation_id": self.reservation["id"],
            "device": info.st_dev, "inode": info.st_ino,
            "inventory_sha256": before.inventory_sha256,
            "file_count": before.file_count,
            "directory_count": before.directory_count,
            "total_bytes": before.total_bytes,
            "domain_id": domain_id, "prepared_at": _now(),
        })
        self.host._publish_intent(self.reservation, intent, self)
        return self.host._reference(self.reservation, intent)


class CoreTransportTrees:
    """Source-available Core publication and recovery for large export trees."""

    def __init__(self, *, workspace: Path, configuration_home: Path, owner_id: str):
        if (not isinstance(workspace, Path) or not workspace.is_absolute()
                or not isinstance(configuration_home, Path) or not configuration_home.is_absolute()
                or not isinstance(owner_id, str) or _OWNER.fullmatch(owner_id) is None):
            raise TransportTreeError("transport.policy", "transport host identity is invalid")
        self.workspace = workspace
        self.configuration_home = configuration_home
        self.owner_id = owner_id
        self.root = ResourceCatalog(configuration_home).root / "transport-trees"

    def _directory(self, name: str) -> Path:
        return self.root / name

    def _record(self, name: str, nonce: str) -> Path:
        return self._directory(name) / f"{nonce}.json"

    def _ensure(self) -> None:
        for path in (self.root, *(self._directory(name) for name in (
            "reservations", "intents", "commits", "aborts", "leases",
        ))):
            _private_directory(path)
            secure_private_path(path, directory=True)

    def _write(self, name: str, nonce: str, kind: str, body: Mapping[str, object],
               *, idempotent: bool = False) -> dict:
        record = _sealed(kind, body)
        publish_immutable_bytes(
            self._record(name, nonce), check_storage.canonical(record) + b"\n",
            byte_limit=_RECORD_LIMIT, idempotent=idempotent,
        )
        return record

    def _read(self, name: str, nonce: str, kind: str) -> dict:
        path = self._record(name, nonce)
        try:
            if not private_path(path, directory=False):
                raise TransportTreeError("transport.changed", "transport catalog lost private custody")
            record = check_storage.read_json(path, byte_limit=_RECORD_LIMIT)
        except TransportTreeError:
            raise
        except (OSError, ValueError) as exc:
            raise TransportTreeError("transport.unavailable", f"transport {name} record is unavailable") from exc
        if not isinstance(record, dict) or record != _sealed(
            kind, {key: value for key, value in record.items() if key != "id"}
        ):
            raise TransportTreeError("transport.changed", f"transport {name} record changed")
        return record

    def _reservation(self, tree_id: str) -> dict:
        nonce = _nonce(tree_id)
        row = self._read("reservations", nonce, _RESERVATION)
        target = Path(str(row.get("path", "")))
        stage = target.parent / f".workbench-transport-{nonce}.pending"
        if (set(row) != {"id", "format", "tree_id", "workspace", "owner_id", "path",
                         "store_root", "store_id", "parent_device", "parent_inode",
                         "staging", "allocated_at"}
                or row["format"] != _RESERVATION or row["tree_id"] != tree_id
                or row["workspace"] != str(self.workspace) or row["owner_id"] != self.owner_id
                or not target.is_absolute() or target.name in {"", ".", ".."}
                or row["store_root"] != str(target.parent)
                or row["store_id"] != _store_id(target.parent)
                or row["staging"] != str(stage)
                or type(row["parent_device"]) is not int or type(row["parent_inode"]) is not int):
            raise TransportTreeError("transport.changed", "transport reservation changed")
        return row

    def _intent(self, tree_id: str) -> dict:
        nonce = _nonce(tree_id)
        row = self._read("intents", nonce, _INTENT)
        reservation = self._reservation(tree_id)
        if (set(row) != {"id", "format", "tree_id", "reservation_id", "device", "inode",
                         "inventory_sha256", "file_count", "directory_count", "total_bytes",
                         "domain_id", "prepared_at"}
                or row["format"] != _INTENT or row["tree_id"] != tree_id
                or row["reservation_id"] != reservation["id"]
                or type(row["device"]) is not int or type(row["inode"]) is not int
                or type(row["inventory_sha256"]) is not str
                or _SHA.fullmatch(row["inventory_sha256"]) is None
                or type(row["file_count"]) is not int or not 1 <= row["file_count"] <= MAX_FILES
                or type(row["directory_count"]) is not int
                or not 0 <= row["directory_count"] <= MAX_DIRECTORIES
                or type(row["total_bytes"]) is not int
                or not 0 <= row["total_bytes"] <= MAX_TOTAL_BYTES
                or type(row["domain_id"]) is not str or not 0 < len(row["domain_id"]) <= 512):
            raise TransportTreeError("transport.changed", "transport intent changed")
        return row

    def _committed(self, tree_id: str, intent: dict) -> bool:
        nonce = _nonce(tree_id)
        if not self._record("commits", nonce).exists():
            return False
        row = self._read("commits", nonce, _COMMIT)
        if (set(row) != {"id", "format", "tree_id", "intent_id", "committed_at"}
                or row["format"] != _COMMIT or row["tree_id"] != tree_id
                or row["intent_id"] != intent["id"]):
            raise TransportTreeError("transport.changed", "transport commit changed")
        return True

    def _reference(self, reservation: dict, intent: dict) -> TransportTreeReference:
        return TransportTreeReference(
            tree_id=reservation["tree_id"], store_id=reservation["store_id"],
            workspace=self.workspace,
            owner_id=self.owner_id, path=Path(reservation["path"]),
            domain_id=intent["domain_id"],
            inventory_sha256=intent["inventory_sha256"],
            file_count=intent["file_count"],
            directory_count=intent["directory_count"],
            total_bytes=intent["total_bytes"],
        )

    def _verify(self, path: Path, reservation: dict, intent: dict) -> None:
        target = Path(reservation["path"])
        staging_root = Path(reservation["staging"])
        if path == target:
            parent = path.parent.stat()
        elif path == staging_root / "payload":
            stage_info = staging_root.lstat()
            if (not stat.S_ISDIR(stage_info.st_mode)
                    or not private_path(staging_root, directory=True)):
                raise TransportTreeError("transport.changed", "transport staging root changed custody")
            parent = staging_root.parent.stat()
        else:
            raise TransportTreeError("transport.path", "transport payload path is not registered")
        if ((parent.st_dev, parent.st_ino) !=
                (reservation["parent_device"], reservation["parent_inode"])):
            raise TransportTreeError("transport.changed", "transport destination parent changed")
        selected = path.lstat()
        if (not stat.S_ISDIR(selected.st_mode)
                or (selected.st_dev, selected.st_ino) != (intent["device"], intent["inode"])):
            raise TransportTreeError("transport.changed", "transport payload changed identity")
        actual = inventory_tree(path)
        parent_after = target.parent.stat()
        if ((parent_after.st_dev, parent_after.st_ino) !=
                (reservation["parent_device"], reservation["parent_inode"])):
            raise TransportTreeError("transport.changed", "transport destination parent changed")
        expected = TransportInventory(
            intent["inventory_sha256"], intent["file_count"],
            intent["directory_count"], intent["total_bytes"],
        )
        if actual != expected:
            raise TransportTreeError("transport.changed", "transport inventory changed")

    def _publish_intent(self, reservation: dict, intent: dict, stage: _Stage | None = None) -> None:
        target = Path(reservation["path"])
        payload = Path(reservation["staging"]) / "payload"
        self._verify(payload, reservation, intent)
        try:
            _rename_no_replace(
                payload, target,
                parent_identity=(reservation["parent_device"], reservation["parent_inode"]),
                payload_identity=(intent["device"], intent["inode"]),
            )
        except ManagedTreeError as exc:
            raise TransportTreeError(exc.code, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise TransportTreeError("transport.changed", "transport staging path or destination parent changed") from exc
        if stage is not None:
            stage.renamed = True
        fsync_directory(target.parent)
        self._verify(target, reservation, intent)
        self._write("commits", _nonce(reservation["tree_id"]), _COMMIT, {
            "format": _COMMIT, "tree_id": reservation["tree_id"],
            "intent_id": intent["id"], "committed_at": _now(),
        })
        if stage is not None:
            stage.committed = True
        try:
            Path(reservation["staging"]).rmdir()
        except OSError:
            pass

    @contextmanager
    def stage(self, target: Path) -> Iterator[_Stage]:
        if (not isinstance(target, Path) or not target.is_absolute()
                or any(part in {".", ".."} for part in target.parts)
                or target.name in {"", ".", ".."}
                or target == self.workspace or target == self.configuration_home
                or target == self.root or target in self.root.parents
                or self.root in target.parents):
            raise TransportTreeError("transport.path", "transport destination is invalid")
        _ensure_parent(target.parent)
        if target.exists() or target.is_symlink():
            raise TransportTreeError("output.exists", "transport destination already exists")
        parent = check_storage.ordinary(target.parent, directory=True).stat()
        self._ensure()
        nonce = uuid4().hex
        tree_id = f"workbench-transport-tree-v2:{nonce}"
        with private_record_lock(self._directory("leases") / f"{nonce}.lock"):
            staging = target.parent / f".workbench-transport-{nonce}.pending"
            reservation = self._write("reservations", nonce, _RESERVATION, {
                "format": _RESERVATION, "tree_id": tree_id,
                "workspace": str(self.workspace), "owner_id": self.owner_id,
                "path": str(target), "store_root": str(target.parent),
                "store_id": _store_id(target.parent),
                "parent_device": parent.st_dev, "parent_inode": parent.st_ino,
                "staging": str(staging), "allocated_at": _now(),
            })
            stage = _Stage(self, reservation)
            try:
                staging.mkdir(mode=0o700)
                secure_private_path(staging, directory=True)
                yield stage
            except BaseException as exc:
                if not stage.renamed:
                    self._write("aborts", nonce, _ABORT, {
                        "format": _ABORT, "tree_id": tree_id,
                        "reservation_id": reservation["id"],
                        "reason": type(exc).__name__, "aborted_at": _now(),
                    }, idempotent=True)
                raise
            else:
                if not stage.committed and not stage.renamed:
                    self._write("aborts", nonce, _ABORT, {
                        "format": _ABORT, "tree_id": tree_id,
                        "reservation_id": reservation["id"],
                        "reason": "unpublished", "aborted_at": _now(),
                    })

    def describe(self, tree_id: str) -> TransportTreeReference:
        reservation = self._reservation(tree_id)
        intent = self._intent(tree_id)
        if not self._committed(tree_id, intent):
            raise TransportTreeError("transport.incomplete", "transport tree is not committed")
        self._verify(Path(reservation["path"]), reservation, intent)
        return self._reference(reservation, intent)

    def reconcile(self, tree_id: str) -> TransportTreeReference:
        reservation = self._reservation(tree_id)
        nonce = _nonce(tree_id)
        try:
            with private_record_lock(self._directory("leases") / f"{nonce}.lock"):
                intent = self._intent(tree_id)
                if self._committed(tree_id, intent):
                    return self.describe(tree_id)
                target = Path(reservation["path"])
                payload = Path(reservation["staging"]) / "payload"
                if target.exists() or target.is_symlink():
                    if payload.exists() or payload.is_symlink():
                        raise TransportTreeError("output.exists", "transport destination collided with staged payload")
                    self._verify(target, reservation, intent)
                    fsync_directory(target.parent)
                    self._write("commits", nonce, _COMMIT, {
                        "format": _COMMIT, "tree_id": tree_id,
                        "intent_id": intent["id"], "committed_at": _now(),
                    })
                elif payload.exists() or payload.is_symlink():
                    self._publish_intent(reservation, intent)
                else:
                    raise TransportTreeError("transport.incomplete", "transport original payload is missing")
                return self.describe(tree_id)
        except DurableRecordError as exc:
            raise TransportTreeError("transport.lease", f"transport lease is unavailable: {exc}") from exc

    def inventory(self) -> tuple[dict[str, object], ...]:
        if not self._directory("reservations").exists():
            return ()
        rows = []
        for path in sorted(self._directory("reservations").glob("*.json")):
            raw = self._read("reservations", path.stem, _RESERVATION)
            if raw.get("workspace") != str(self.workspace) or raw.get("owner_id") != self.owner_id:
                continue
            tree_id = f"workbench-transport-tree-v2:{path.stem}"
            reservation = self._reservation(tree_id)
            if self._record("commits", path.stem).exists():
                intent = self._intent(tree_id)
                self._committed(tree_id, intent)
                status = "committed"
            elif self._record("intents", path.stem).exists():
                self._intent(tree_id)
                status = "prepared-incomplete"
            elif self._record("aborts", path.stem).exists():
                abort = self._read("aborts", path.stem, _ABORT)
                if (set(abort) != {"id", "format", "tree_id", "reservation_id",
                                   "reason", "aborted_at"}
                        or abort["format"] != _ABORT or abort["tree_id"] != tree_id
                        or abort["reservation_id"] != reservation["id"]):
                    raise TransportTreeError("transport.changed", "transport abort changed")
                status = "failed"
            else:
                status = "reserved-incomplete"
            rows.append({"tree_id": tree_id, "path": Path(reservation["path"]),
                         "staging": Path(reservation["staging"]) / "payload",
                         "status": status})
        return tuple(rows)


__all__ = ["CoreTransportTrees", "TransportInventory", "inventory_tree", "summarize_rows"]
