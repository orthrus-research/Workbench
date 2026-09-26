"""Core custody for one mutable Feature Change Work Session owner directory.

The registered record store selects the historical parent. Core alone creates
the fixed session child, retains its allocation identity and holds its lease.
There is deliberately no release or orphan cleanup transition here.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import stat
from typing import Iterator

from workbench_api.record_stores import (
    RecordStoreReference, SessionOwnerAllocationError, SessionOwnerReference,
)

from .host_filesystem import (
    fsync_directory, private_path, private_record_lock,
    publish_immutable_bytes, read_private_single_link_bytes,
    secure_private_path,
)
from .output_routing import _private_directory


_SESSION = re.compile(r"work-session-v2-[0-9a-f]{32}\Z")
_FORMAT = "workbench-session-owner-allocation-v1"
_PREFIX = _FORMAT + ":sha256:"
_MAX_RECORD = 8192
_MAX_START_RESULT = 1024 * 1024


def _fail(code: str, message: str) -> None:
    raise SessionOwnerAllocationError("owner." + code, message)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SessionOwnerAllocationError("owner.unavailable", f"session owner path is unavailable: {path}") from exc
    if (not stat.S_ISDIR(info.st_mode) or path.is_symlink()
            or getattr(path, "is_junction", lambda: False)()
            or not private_path(path, directory=True)):
        _fail("unsafe", "session owner path is redirected or not private")
    return info.st_dev, info.st_ino


class _HeldSessionOwner:
    def __init__(self, host: CoreSessionOwnerAllocations, reference: SessionOwnerReference, raw: bytes):
        self.host = host
        self.reference = reference
        self.raw = raw
        info = host.record_path.lstat()
        self.record_identity = info.st_dev, info.st_ino
        self.started_bound = host.started_path.exists() or host.started_path.is_symlink()

    def verify(self) -> SessionOwnerReference:
        reference, raw = self.host._read()
        info = self.host.record_path.lstat()
        if (raw != self.raw or reference != self.reference
                or (info.st_dev, info.st_ino) != self.record_identity):
            _fail("changed", "session owner allocation changed while its lease was held")
        return reference

    def record_started(self, expected_start_result: bytes) -> SessionOwnerReference:
        self.verify()
        self.host._record_started(self.reference, expected_start_result)
        self.started_bound = True
        return self.verify_started()

    def verify_started(self) -> SessionOwnerReference:
        reference = self.verify()
        self.host._read_started(reference)
        return reference


class CoreSessionOwnerAllocations:
    def __init__(self, store: RecordStoreReference, session_id: str):
        if (store.family != "feature-change-session-context-v1"
                or store.owner_id != "workbench-shell"
                or not store.root.is_absolute()):
            _fail("policy", "session owner requires the Feature Change Core store")
        if type(session_id) is not str or _SESSION.fullmatch(session_id) is None:
            _fail("policy", "session owner selector is invalid")
        self.store = store
        self.session_id = session_id
        self.owners = store.root / "session-owners"
        self.records = store.root / "owner-allocations"
        self.leases = store.root / "owner-leases"
        self.path = self.owners / session_id
        self.marker_path = self.path / "core-owner-allocation-v1.json"
        self.record_path = self.records / (session_id + ".json")
        self.started_path = self.records / (session_id + ".started.json")

    def _body(
        self, owner: tuple[int, int], collection: tuple[int, int],
        store_root: tuple[int, int],
    ) -> dict:
        return {
            "format": _FORMAT,
            "store_id": self.store.store_id,
            "owner_id": self.store.owner_id,
            "workspace": str(self.store.workspace),
            "store_root": str(self.store.root),
            "store_device": store_root[0], "store_inode": store_root[1],
            "session_id": self.session_id,
            "path": str(self.path),
            "state_root": str(self.path / "owner-state"),
            "collection_device": collection[0],
            "collection_inode": collection[1],
            "owner_device": owner[0],
            "owner_inode": owner[1],
        }

    def _read(self) -> tuple[SessionOwnerReference, bytes]:
        store_root = _directory_identity(self.store.root)
        collection = _directory_identity(self.owners)
        owner = _directory_identity(self.path)
        body = self._body(owner, collection, store_root)
        allocation_id = _PREFIX + sha256(_canonical(body)).hexdigest()
        marker = read_private_single_link_bytes(self.marker_path, byte_limit=_MAX_RECORD)
        if marker != _canonical({
            "format": _FORMAT + "-marker",
            "allocation_id": allocation_id,
            "session_id": self.session_id,
        }):
            _fail("changed", "session owner Core marker differs from its allocation")
        marker_info = self.marker_path.lstat()
        raw = read_private_single_link_bytes(self.record_path, byte_limit=_MAX_RECORD)
        try:
            record = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise SessionOwnerAllocationError("owner.record", "session owner allocation is not JSON") from exc
        if record != {
            **body,
            "allocation_id": allocation_id,
            "marker_device": marker_info.st_dev,
            "marker_inode": marker_info.st_ino,
        } or raw != _canonical(record):
            _fail("changed", "session owner allocation differs from its fixed Core identity")
        state_root = self.path / "owner-state"
        if state_root.exists() or state_root.is_symlink():
            _directory_identity(state_root)
        start = self.path / "start-result-v1.json"
        if start.exists() or start.is_symlink():
            info = start.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or start.is_symlink():
                _fail("unsafe", "session owner start result is redirected or linked")
        reference = SessionOwnerReference(
            allocation_id=allocation_id, store_id=self.store.store_id,
            workspace=self.store.workspace, owner_id=self.store.owner_id,
            session_id=self.session_id, path=self.path, state_root=state_root,
        )
        if self.started_path.exists() or self.started_path.is_symlink():
            self._read_started(reference)
        return reference, raw

    def _started_body(self, reference: SessionOwnerReference) -> dict:
        state = _directory_identity(reference.state_root)
        start = reference.path / "start-result-v1.json"
        raw = read_private_single_link_bytes(start, byte_limit=_MAX_START_RESULT)
        info = start.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("unsafe", "session owner start result is not independent")
        return {
            "format": _FORMAT + "-started",
            "allocation_id": reference.allocation_id,
            "state_device": state[0], "state_inode": state[1],
            "start_device": info.st_dev, "start_inode": info.st_ino,
            "start_size": len(raw), "start_sha256": sha256(raw).hexdigest(),
        }

    def _read_started(self, reference: SessionOwnerReference) -> None:
        raw = read_private_single_link_bytes(self.started_path, byte_limit=_MAX_RECORD)
        try:
            record = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise SessionOwnerAllocationError("owner.record", "session owner start binding is not JSON") from exc
        if record != self._started_body(reference) or raw != _canonical(record):
            _fail("changed", "session owner state or start result changed after Core binding")

    def _record_started(self, reference: SessionOwnerReference, expected_start_result: bytes) -> None:
        if type(expected_start_result) is not bytes or len(expected_start_result) > _MAX_START_RESULT:
            _fail("bounds", "session owner start result exceeds its byte bound")
        if self.started_path.exists() or self.started_path.is_symlink():
            _fail("unclaimed", "session owner start binding already exists")
        start = reference.path / "start-result-v1.json"
        if read_private_single_link_bytes(start, byte_limit=_MAX_START_RESULT) != expected_start_result:
            _fail("changed", "session owner start result differs from the admitted bytes")
        publish_immutable_bytes(
            self.started_path, _canonical(self._started_body(reference)),
            byte_limit=_MAX_RECORD,
        )
        self._read_started(reference)

    @contextmanager
    def open(self, *, create: bool) -> Iterator[_HeldSessionOwner]:
        if type(create) is not bool:
            _fail("policy", "session owner allocation mode is invalid")
        if not create and not (self.record_path.exists() or self.record_path.is_symlink()):
            _fail("unregistered", "session owner has no Core allocation record")
        _private_directory(self.leases)
        secure_private_path(self.leases, directory=True)
        with private_record_lock(self.leases / (self.session_id + ".lock")):
            if create:
                if (self.path.exists() or self.path.is_symlink()
                        or self.record_path.exists() or self.record_path.is_symlink()
                        or self.started_path.exists() or self.started_path.is_symlink()):
                    _fail("unclaimed", "session owner path or allocation already exists")
                _private_directory(self.owners)
                secure_private_path(self.owners, directory=True)
                _private_directory(self.records)
                secure_private_path(self.records, directory=True)
                self.path.mkdir(mode=0o700)
                secure_private_path(self.path, directory=True)
                owner = _directory_identity(self.path)
                collection = _directory_identity(self.owners)
                store_root = _directory_identity(self.store.root)
                fsync_directory(self.owners)
                body = self._body(owner, collection, store_root)
                allocation_id = _PREFIX + sha256(_canonical(body)).hexdigest()
                publish_immutable_bytes(
                    self.marker_path,
                    _canonical({
                        "format": _FORMAT + "-marker",
                        "allocation_id": allocation_id,
                        "session_id": self.session_id,
                    }),
                    byte_limit=_MAX_RECORD,
                )
                marker = self.marker_path.lstat()
                publish_immutable_bytes(
                    self.record_path,
                    _canonical({
                        **body,
                        "allocation_id": allocation_id,
                        "marker_device": marker.st_dev,
                        "marker_inode": marker.st_ino,
                    }),
                    byte_limit=_MAX_RECORD,
                )
            reference, raw = self._read()
            allocation = _HeldSessionOwner(self, reference, raw)
            yield allocation
            if allocation.started_bound:
                allocation.verify_started()
            else:
                allocation.verify()


__all__ = ["CoreSessionOwnerAllocations"]
