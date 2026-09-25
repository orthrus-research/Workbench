"""One-time, non-destructive import of earlier per-user configuration files."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping
from uuid import uuid4

from .host_filesystem import fsync_directory
from .setup_cli import _state_root, load_setup_record, setup_record_lock
from .user_config_home import default_user_config_home, legacy_user_config_home
from .user_preferences import UserPreferencesError


_FILES = ("setup-v1.json", "recipe-fixtures-v1.json", "launcher-v1.json")
_MAX_BYTES = 256 * 1024


def _source_bytes(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= _MAX_BYTES:
        raise UserPreferencesError(f"legacy configuration is not a bounded regular file: {path}")
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise UserPreferencesError(f"legacy configuration changed while read: {path}")
    if path.name == "setup-v1.json":
        load_setup_record(path)
    elif path.name == "recipe-fixtures-v1.json":
        from .fixture_selection import load_fixture_registry
        load_fixture_registry(path)
    else:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UserPreferencesError(f"legacy launcher configuration is invalid: {path}") from exc
        if type(value) is not dict or set(value) != {"format", "schema_version", "record_id", "selection"}:
            raise UserPreferencesError(f"legacy launcher configuration has unsupported fields: {path}")
        if value["format"] != "workbench-launcher-setup-record-v1" or value["schema_version"] != 1:
            raise UserPreferencesError(f"legacy launcher configuration has an unsupported format: {path}")
        body = {key: value[key] for key in ("format", "schema_version", "selection")}
        expected = "workbench-launcher-setup:sha256:" + sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if value["record_id"] != expected:
            raise UserPreferencesError(f"legacy launcher configuration identity changed: {path}")
    return raw


def inspect_legacy_config_migration(
    *, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Report exact file copies without reading or changing generated data."""

    values = os.environ if environment is None else environment
    destination = default_user_config_home(environment=values)
    source = legacy_user_config_home(environment=values)
    if values.get("WORKBENCH_CONFIG_HOME") or source == destination:
        return {"source": str(source), "destination": str(destination), "files": [], "state": "explicit-config-home"}
    files = []
    for name in _FILES:
        origin = source / name
        target = destination / name
        raw = _source_bytes(origin)
        if raw is None:
            continue
        digest = "sha256:" + sha256(raw).hexdigest()
        if target.exists() or target.is_symlink():
            current = _source_bytes(target)
            state = "already-present" if current == raw else "conflict"
        else:
            state = "copy"
        files.append({"name": name, "sha256": digest, "state": state})
    state = "conflict" if any(row["state"] == "conflict" for row in files) else (
        "ready" if any(row["state"] == "copy" for row in files) else "nothing-to-import"
    )
    return {"source": str(source), "destination": str(destination), "files": files, "state": state}


def migrate_legacy_config(*, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Copy reviewed legacy records once; never replace the source or a conflict."""

    plan = inspect_legacy_config_migration(environment=environment)
    if plan["state"] == "conflict":
        raise UserPreferencesError("the stable configuration home has conflicting legacy records")
    if plan["state"] != "ready":
        return plan
    destination = Path(plan["destination"])
    _state_root(destination)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with setup_record_lock(destination / "migration-v1.json"):
        current = inspect_legacy_config_migration(environment=environment)
        if current != plan:
            raise UserPreferencesError("legacy configuration changed before import; review it again")
        for row in current["files"]:
            if row["state"] != "copy":
                continue
            source = Path(current["source"]) / row["name"]
            raw = _source_bytes(source)
            if raw is None or "sha256:" + sha256(raw).hexdigest() != row["sha256"]:
                raise UserPreferencesError("legacy configuration changed during import")
            target = destination / row["name"]
            if target.exists() or target.is_symlink():
                raise UserPreferencesError(f"configuration destination appeared during import: {target}")
            temporary = destination / f".{row['name']}.{uuid4().hex}.tmp"
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)
                fsync_directory(destination)
            finally:
                temporary.unlink(missing_ok=True)
    observed = inspect_legacy_config_migration(environment=environment)
    copied = {row["name"] for row in plan["files"] if row["state"] == "copy"}
    return {
        **observed,
        "files": [
            {**row, "state": "copied" if row["name"] in copied else row["state"]}
            for row in observed["files"]
        ],
        "state": "imported",
    }


__all__ = ["inspect_legacy_config_migration", "migrate_legacy_config"]
