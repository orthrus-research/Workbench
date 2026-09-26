"""Core physical custody and owner-independent inventory for service records."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re
import stat
from typing import Iterator

from workbench_api.host_filesystem import DurableRecordError
from workbench_api.service import ServicePhysicalLeasePorts, ServiceV3Error

from ..durable_records import publish_immutable_bytes, read_private_bytes, replace_private_bytes
from ..host_filesystem import fsync_directory, private_path, secure_private_path


_NAMESPACE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_INVENTORY_LIMIT = 128 * 1024 * 1024


def _directory(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise DurableRecordError("path", "service record directory must be absolute")
    if any(item.is_symlink() or getattr(item, "is_junction", lambda: False)()
           for item in (path, *path.parents)):
        raise DurableRecordError("unsafe", "service record directory traverses a redirect")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink():
        raise DurableRecordError("unsafe", "service record directory is not ordinary")
    secure_private_path(path, directory=True)
    if not private_path(path, directory=True):
        raise DurableRecordError("unsafe", "service record directory is not owner-private")


class CoreServiceRecordBackend:
    """One physical backend; a service owner still controls its record schema."""

    def __init__(self, root: Path, *, physical_leases: ServicePhysicalLeasePorts):
        if type(physical_leases) is not ServicePhysicalLeasePorts:
            raise ServiceV3Error("service.invalid-physical-lease-provider", "service record backend requires Host Adapter leases")
        _directory(root)
        self.root = root
        self.physical_leases = physical_leases
        self.namespace("locks")

    def namespace(self, name: str) -> Path:
        if type(name) is not str or _NAMESPACE.fullmatch(name) is None:
            raise DurableRecordError("path", "service record namespace is invalid")
        path = self.root / name
        _directory(path)
        return path

    def _parent(self, path: Path) -> None:
        if (
            not isinstance(path, Path) or not path.is_absolute()
            or path == self.root or not path.is_relative_to(self.root)
            or ".." in path.parts
        ):
            raise ServiceV3Error("service.invalid-root", "service record is outside its store")
        current = self.root
        for part in path.parent.relative_to(self.root).parts:
            current /= part
            try:
                _directory(current)
            except DurableRecordError as exc:
                raise ServiceV3Error(
                    "service.invalid-root", "service record directory is not safely private"
                ) from exc

    def publish_immutable(self, path: Path, raw: bytes) -> None:
        self._parent(path)
        try:
            previous_size = path.lstat().st_size
        except FileNotFoundError:
            previous_size = 0
        publish_immutable_bytes(path, raw, byte_limit=max(len(raw), previous_size), idempotent=True)

    def allocate_directory(self, path: Path) -> None:
        self._parent(path)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise DurableRecordError("collision", "service record directory already exists") from exc
        _directory(path)
        fsync_directory(path.parent)

    def replace(self, path: Path, raw: bytes) -> None:
        self._parent(path)
        try:
            previous_size = path.lstat().st_size
        except FileNotFoundError:
            previous_size = 0
        replace_private_bytes(path, raw, byte_limit=max(len(raw), previous_size))

    def read(self, path: Path) -> bytes:
        if (not isinstance(path, Path) or not path.is_absolute()
                or path == self.root or not path.is_relative_to(self.root)
                or ".." in path.parts):
            raise ServiceV3Error("service.invalid-root", "service record is outside its store")
        return read_private_bytes(path, byte_limit=_INVENTORY_LIMIT)

    @contextmanager
    def exclusive(self, key: str) -> Iterator[None]:
        if type(key) is not str or not key:
            raise DurableRecordError("path", "service record lease key is invalid")
        path = self.root / "locks" / f"{sha256(key.encode()).hexdigest()}.lock"
        try:
            lease = self.physical_leases.exclusive(path)
            lease.__enter__()
        except ServiceV3Error:
            raise
        except Exception as exc:
            raise ServiceV3Error(
                "service.physical-lease-failed", "Host Adapter record lease failed closed"
            ) from exc
        try:
            yield
        finally:
            try:
                lease.__exit__(None, None, None)
            except Exception as exc:
                raise ServiceV3Error(
                    "service.physical-lease-failed", "Host Adapter record lease release failed closed"
                ) from exc


def inspect_service_record_root(root: Path, *, byte_limit: int = _INVENTORY_LIMIT) -> dict:
    """Inventory physical bytes without importing or interpreting an owner module."""

    if not isinstance(root, Path) or not root.is_absolute() or type(byte_limit) is not int or byte_limit < 0:
        raise DurableRecordError("path", "service record inventory parameters are invalid")
    if not root.exists() and not root.is_symlink():
        return {"format": "workbench-service-physical-inventory-v1", "root": str(root),
                "status": "unavailable", "records": []}
    if (any(item.is_symlink() or getattr(item, "is_junction", lambda: False)()
            for item in (root, *root.parents))
            or not root.is_dir() or not private_path(root, directory=True)):
        raise DurableRecordError("unsafe", "service record root is not owner-private")
    rows: list[dict] = []

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if path.name.startswith(".") and path.name.endswith(".record.lock"):
                continue
            observed = path.lstat()
            if stat.S_ISDIR(observed.st_mode) and not path.is_symlink():
                if not private_path(path, directory=True):
                    rows.append({"path": relative.as_posix(), "status": "unsafe-directory"})
                elif relative.parts[0] != "locks":
                    visit(path)
            elif relative.parts[0] != "locks":
                if not stat.S_ISREG(observed.st_mode) or path.is_symlink():
                    rows.append({"path": relative.as_posix(), "status": "unsafe-file"})
                    continue
                try:
                    raw = read_private_bytes(path, byte_limit=byte_limit)
                except DurableRecordError as exc:
                    rows.append({"path": relative.as_posix(), "status": exc.code})
                else:
                    rows.append({"path": relative.as_posix(), "status": "available",
                                 "bytes": len(raw), "sha256": sha256(raw).hexdigest()})

    visit(root)
    return {"format": "workbench-service-physical-inventory-v1", "root": str(root),
            "status": "available", "records": rows}


__all__ = ["CoreServiceRecordBackend", "inspect_service_record_root"]
