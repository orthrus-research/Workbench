"""Environment-scoped receipts for Core package mutations.

The package lease root is fixed to the account and Python environment. These
records remain readable when a package installation or optional module is
broken; they do not depend on workspace output or configuration locations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from workbench_api import ModuleError

from .durable_records import read_private_bytes, replace_private_bytes
from .host_filesystem import fsync_directory, private_path, secure_private_path


FORMAT = "workbench-package-operation-v1"
MAX_RECORD_BYTES = 64 * 1024
_ENVIRONMENT_ID = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset({
    "format", "schema_version", "record_id", "operation_id", "environment_id",
    "kind", "action", "target", "distribution", "wheel_sha256", "state",
    "external_started", "started_at", "finished_at", "result_code", "error",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sealed(body: dict) -> dict:
    record_id = "workbench-package-operation:sha256:" + sha256(_raw(body)).hexdigest()
    return {**body, "record_id": record_id}


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ModuleError("package operation repeats a record field")
        value[key] = item
    return value


def _valid(value: object, *, operation_id: str, environment_id: str) -> dict:
    if type(value) is not dict or set(value) != _FIELDS:
        raise ModuleError("package operation has unsupported fields")
    row = value
    if (
        row["format"] != FORMAT or type(row["schema_version"]) is not int
        or row["schema_version"] != 1 or row["operation_id"] != operation_id
        or row["environment_id"] != environment_id
        or type(row["kind"]) is not str or row["kind"] not in {"modules", "profiles"}
        or type(row["action"]) is not str or row["action"] not in {"install", "update", "remove"}
        or type(row["target"]) is not str or not 0 < len(row["target"]) <= 4096
        or (row["distribution"] is not None
            and (type(row["distribution"]) is not str or not row["distribution"]))
        or (row["wheel_sha256"] is not None
            and (type(row["wheel_sha256"]) is not str
                 or _DIGEST.fullmatch(row["wheel_sha256"]) is None))
        or type(row["state"]) is not str
        or row["state"] not in {"running", "external-running", "completed", "rejected", "incomplete"}
        or type(row["external_started"]) is not bool
        or type(row["started_at"]) is not str or not row["started_at"]
        or (row["finished_at"] is not None and (type(row["finished_at"]) is not str or not row["finished_at"]))
        or (row["result_code"] is not None and type(row["result_code"]) is not int)
        or (row["error"] is not None and (type(row["error"]) is not str or len(row["error"]) > 1024))
        or (row["state"] in {"running", "external-running"}
            and (row["state"] == "external-running") != row["external_started"])
        or (row["state"] in {"running", "external-running"}) != (row["finished_at"] is None)
        or (row["state"] in {"completed", "incomplete"} and not row["external_started"])
        or (row["state"] == "rejected" and row["external_started"])
        or (row["state"] == "completed" and row["result_code"] != 0)
        or row["record_id"] != _sealed({key: item for key, item in row.items() if key != "record_id"})["record_id"]
    ):
        raise ModuleError("package operation identity or state is invalid")
    return row


class PackageOperationStore:
    """A Core-owned journal beneath the fixed package exclusion domain."""

    def __init__(self, root: Path, environment_id: str):
        if not root.is_absolute() or _ENVIRONMENT_ID.fullmatch(environment_id) is None:
            raise ModuleError("package operation environment is invalid")
        self.root = root
        self.environment_id = environment_id

    @classmethod
    def current(cls) -> PackageOperationStore:
        from .package_guard import environment_id, guard_root
        return cls(guard_root(), environment_id())

    def _directory(self, *, create: bool) -> Path | None:
        for ancestor in (self.root, *self.root.parents):
            if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
                raise ModuleError("package operation path traverses a redirecting directory")
        if not self.root.is_dir():
            if not create and not self.root.exists():
                return None
            raise ModuleError("package lease root is unavailable")
        directory = self.root / "operations"
        if create:
            existed = directory.exists() or directory.is_symlink()
            directory.mkdir(mode=0o700, exist_ok=True)
        elif not directory.exists() and not directory.is_symlink():
            return None
        if directory.is_symlink() or not directory.is_dir():
            raise ModuleError("package operation directory is unsafe")
        if create:
            secure_private_path(directory, directory=True)
        if not private_path(directory, directory=True):
            raise ModuleError("package operation directory must be owner-private")
        if create and not existed:
            fsync_directory(self.root)
        return directory

    def _path(self, operation_id: str, *, create: bool) -> Path:
        if type(operation_id) is not str or _OPERATION_ID.fullmatch(operation_id) is None:
            raise ModuleError("package operation ID is invalid")
        directory = self._directory(create=create)
        if directory is None:
            raise ModuleError("package operation is unavailable")
        return directory / f"{operation_id}.json"

    def inspect(self, operation_id: str) -> dict:
        path = self._path(operation_id, create=False)
        try:
            value = json.loads(read_private_bytes(path, byte_limit=MAX_RECORD_BYTES), object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ModuleError("package operation is not valid UTF-8 JSON") from exc
        return _valid(value, operation_id=operation_id, environment_id=self.environment_id)

    def list(self, *, kind: str) -> list[dict]:
        if kind not in {"modules", "profiles"}:
            raise ModuleError("unknown package kind")
        directory = self._directory(create=False)
        if directory is None:
            return []
        rows = []
        for path in directory.glob("*.json"):
            if _OPERATION_ID.fullmatch(path.stem) is None:
                raise ModuleError("package operation directory has an unexpected record")
            row = self.inspect(path.stem)
            if row["kind"] == kind:
                rows.append(row)
        return sorted(rows, key=lambda row: (row["started_at"], row["operation_id"]), reverse=True)

    def begin(self, *, kind: str, action: str, target: str) -> PackageOperation:
        if kind not in {"modules", "profiles"} or action not in {"install", "update", "remove"}:
            raise ModuleError("unknown package operation")
        if type(target) is not str or not 0 < len(target) <= 4096:
            raise ModuleError("package operation target is invalid")
        operation_id = uuid4().hex
        body = {
            "format": FORMAT, "schema_version": 1, "operation_id": operation_id,
            "environment_id": self.environment_id, "kind": kind, "action": action,
            "target": target, "distribution": None, "wheel_sha256": None,
            "state": "running", "external_started": False,
            "started_at": _now(), "finished_at": None, "result_code": None,
            "error": None,
        }
        record = _sealed(body)
        replace_private_bytes(self._path(operation_id, create=True), _raw(record),
                              byte_limit=MAX_RECORD_BYTES, require_absent=True)
        return PackageOperation(self, record)

    def _replace(self, previous: dict, changes: dict) -> dict:
        record = _sealed({**{key: item for key, item in previous.items() if key != "record_id"}, **changes})
        _valid(record, operation_id=previous["operation_id"], environment_id=self.environment_id)
        replace_private_bytes(
            self._path(previous["operation_id"], create=False), _raw(record),
            byte_limit=MAX_RECORD_BYTES,
            expected_sha256="sha256:" + sha256(_raw(previous)).hexdigest(),
        )
        return record


class PackageOperation:
    def __init__(self, store: PackageOperationStore, record: dict):
        self.store = store
        self.record = record

    @property
    def external_started(self) -> bool:
        return self.record["external_started"]

    def bind_distribution(self, distribution: str, *, wheel: Path | None = None) -> None:
        if self.record["state"] != "running" or type(distribution) is not str or not distribution:
            raise ModuleError("package operation cannot bind this distribution")
        digest = None
        if wheel is not None:
            with wheel.open("rb") as stream:
                digest = file_digest(stream, "sha256").hexdigest()
        self.record = self.store._replace(self.record, {
            "distribution": distribution, "wheel_sha256": digest,
        })

    def before_external(self) -> None:
        if self.record["state"] != "running" or self.record["distribution"] is None:
            raise ModuleError("package operation has no admitted distribution")
        if self.record["action"] in {"install", "update"} and self.record["wheel_sha256"] is None:
            raise ModuleError("package operation has no verified wheel bytes")
        self.record = self.store._replace(self.record, {
            "state": "external-running", "external_started": True,
        })

    def finish(self, state: str, *, result_code: int | None = None, error: str | None = None) -> None:
        if state not in {"completed", "rejected", "incomplete"} or self.record["finished_at"] is not None:
            raise ModuleError("package operation is already terminal")
        if state == "completed" and (not self.external_started or result_code != 0):
            raise ModuleError("package operation lacks completed external admission")
        if state == "rejected" and self.external_started:
            raise ModuleError("started package mutation cannot be rejected as preflight")
        self.record = self.store._replace(self.record, {
            "state": state, "finished_at": _now(), "result_code": result_code,
            "error": error[:1024] if error is not None else None,
        })


__all__ = ["PackageOperationStore", "PackageOperation"]
