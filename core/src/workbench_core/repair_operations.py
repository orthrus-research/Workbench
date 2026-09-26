"""Account-scoped Core receipts for reviewed host repair applies.

An attempt begins as incomplete before host or Setup mutation. Only the
repair owner's verified V1 result can close it as completed or partial.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterator, Mapping
from uuid import uuid4

from workbench_api import ModuleError

from .durable_records import private_record_lock, read_private_bytes, replace_private_bytes
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .package_guard import account_home


RESULT_FORMAT = "workbench-repair-result-v1"
FORMAT = "workbench-repair-operation-v1"
MAX_RECORD_BYTES = 128 * 1024
_OPERATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = frozenset({
    "format", "schema_version", "record_id", "operation_id", "plan_id",
    "state", "started_at", "finished_at", "result", "error",
})


def default_repair_operation_root() -> Path:
    return account_home() / ".workbench-core-operations" / "repair"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sealed(body: dict) -> dict:
    record_id = "workbench-repair-operation:sha256:" + sha256(_raw(body)).hexdigest()
    return {**body, "record_id": record_id}


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ModuleError("repair operation repeats a record field")
        value[key] = item
    return value


def _valid(value: object, *, operation_id: str) -> dict:
    if type(value) is not dict or set(value) != _FIELDS:
        raise ModuleError("repair operation has unsupported fields")
    row = value
    result = row["result"]
    if (
        row["format"] != FORMAT or type(row["schema_version"]) is not int
        or row["schema_version"] != 1 or row["operation_id"] != operation_id
        or type(row["plan_id"]) is not str or not 0 < len(row["plan_id"]) <= 256
        or type(row["state"]) is not str
        or row["state"] not in {"incomplete", "partial", "completed"}
        or type(row["started_at"]) is not str or not row["started_at"]
        or (row["finished_at"] is not None
            and (type(row["finished_at"]) is not str or not row["finished_at"]))
        or (row["error"] is not None
            and (type(row["error"]) is not str or len(row["error"]) > 1024))
        or (row["state"] == "incomplete" and result is not None)
        or (row["state"] in {"partial", "completed"} and row["finished_at"] is None)
        or (row["state"] in {"partial", "completed"} and type(result) is not dict)
        or (type(result) is dict and (
            result.get("format") != RESULT_FORMAT
            or result.get("applied_plan_id") != row["plan_id"]
        ))
        or (row["state"] == "partial" and result.get("outcome") != "partial")
        or (row["state"] == "completed" and result.get("outcome") not in {"ready", "repaired"})
        or row["record_id"] != _sealed({key: item for key, item in row.items() if key != "record_id"})["record_id"]
    ):
        raise ModuleError("repair operation identity or state is invalid")
    return row


class RepairOperationStore:
    """Private operation history independent of Setup and optional modules."""

    def __init__(self, root: Path | None = None):
        selected = default_repair_operation_root() if root is None else root
        if not isinstance(selected, Path) or not selected.is_absolute():
            raise ModuleError("repair operation root must be absolute")
        self.root = selected

    def _directory(self, *, create: bool) -> Path | None:
        for ancestor in (self.root, *self.root.parents):
            if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
                raise ModuleError("repair operation path traverses a redirecting directory")
        for directory in (self.root.parent, self.root):
            if not directory.exists():
                if not create:
                    return None
                directory.mkdir(mode=0o700)
                secure_private_path(directory, directory=True)
                fsync_directory(directory.parent)
            if not directory.is_dir() or not private_path(directory, directory=True):
                raise ModuleError("repair operation directory must be owner-private")
        return self.root

    def _path(self, operation_id: str, *, create: bool) -> Path:
        if type(operation_id) is not str or _OPERATION_ID.fullmatch(operation_id) is None:
            raise ModuleError("repair operation ID is invalid")
        directory = self._directory(create=create)
        if directory is None:
            raise ModuleError("repair operation is unavailable")
        return directory / f"{operation_id}.json"

    @contextmanager
    def apply_scope(self) -> Iterator[None]:
        directory = self._directory(create=True)
        assert directory is not None
        with private_record_lock(directory / ".apply.lock"):
            yield

    def begin(self, plan_id: str) -> RepairOperation:
        if type(plan_id) is not str or not 0 < len(plan_id) <= 256:
            raise ModuleError("repair plan identity is invalid")
        operation_id = uuid4().hex
        record = _sealed({
            "format": FORMAT, "schema_version": 1,
            "operation_id": operation_id, "plan_id": plan_id,
            "state": "incomplete", "started_at": _now(),
            "finished_at": None, "result": None, "error": None,
        })
        replace_private_bytes(self._path(operation_id, create=True), _raw(record),
                              byte_limit=MAX_RECORD_BYTES, require_absent=True)
        return RepairOperation(self, record)

    def inspect(self, operation_id: str) -> dict:
        path = self._path(operation_id, create=False)
        try:
            value = json.loads(read_private_bytes(path, byte_limit=MAX_RECORD_BYTES),
                               object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ModuleError("repair operation is not valid UTF-8 JSON") from exc
        return _valid(value, operation_id=operation_id)

    def list(self) -> list[dict]:
        directory = self._directory(create=False)
        if directory is None:
            return []
        rows = []
        for path in directory.glob("*.json"):
            if _OPERATION_ID.fullmatch(path.stem) is None:
                raise ModuleError("repair operation directory has an unexpected record")
            rows.append(self.inspect(path.stem))
        return sorted(rows, key=lambda row: (row["started_at"], row["operation_id"]), reverse=True)

    def _replace(self, previous: dict, changes: dict) -> dict:
        record = _sealed({**{key: item for key, item in previous.items() if key != "record_id"}, **changes})
        _valid(record, operation_id=previous["operation_id"])
        replace_private_bytes(
            self._path(previous["operation_id"], create=False), _raw(record),
            byte_limit=MAX_RECORD_BYTES,
            expected_sha256="sha256:" + sha256(_raw(previous)).hexdigest(),
        )
        return record


class RepairOperation:
    def __init__(self, store: RepairOperationStore, record: dict):
        self.store = store
        self.record = record

    def finish(self, *, result: Mapping | None = None, error: str | None = None) -> None:
        if self.record["finished_at"] is not None:
            raise ModuleError("repair operation is already terminal")
        if result is None:
            if error is None:
                raise ModuleError("incomplete repair needs its observed failure")
            state = "incomplete"
        elif result.get("format") == RESULT_FORMAT and result.get("applied_plan_id") == self.record["plan_id"]:
            if result.get("outcome") == "partial":
                state = "partial"
            elif result.get("outcome") in {"ready", "repaired"}:
                state = "completed"
            else:
                raise ModuleError("repair result has an unsupported outcome")
        else:
            raise ModuleError("repair result does not match the reviewed plan")
        self.record = self.store._replace(self.record, {
            "state": state, "finished_at": _now(),
            "result": dict(result) if result is not None else None,
            "error": error[:1024] if error is not None else None,
        })


__all__ = ["RESULT_FORMAT", "RepairOperationStore", "default_repair_operation_root"]
