"""Durable user location choices, independent of suite and profile sources."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from uuid import uuid4

from .host_filesystem import fsync_directory
from .setup_cli import _state_root, setup_record_lock
from .user_config_home import default_user_config_home, user_home


SETTINGS_FORMAT = "workbench-user-settings-v1"
WORKSPACES_FORMAT = "workbench-user-workspaces-v1"
LOCATION_ROLES = frozenset({
    "workspace_parent",
    "artifacts",
    "logs",
    "blueprint_library",
    "blueprint_sessions",
    "fixture_library",
    "fixture_instances",
    "cache",
    "evidence",
})
MAX_RECORD_BYTES = 256 * 1024
_WORKSPACE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class UserPreferencesError(ValueError):
    """A user configuration record or selected location is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(prefix: str, body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "record_id": prefix + ":sha256:" + sha256(_canonical(body)).hexdigest()}


def _settings(locations: Mapping[str, str]) -> dict[str, Any]:
    return _seal("workbench-user-settings", {
        "format": SETTINGS_FORMAT,
        "schema_version": 1,
        "locations": dict(sorted(locations.items())),
    })


def _workspaces(entries: list[dict[str, str]], default: str | None) -> dict[str, Any]:
    return _seal("workbench-user-workspaces", {
        "format": WORKSPACES_FORMAT,
        "schema_version": 1,
        "default": default,
        "entries": sorted(entries, key=lambda row: row["name"]),
    })


def default_settings_path(*, environment: Mapping[str, str] | None = None) -> Path:
    return default_user_config_home(environment=environment) / "settings.json"


def default_workspaces_path(*, environment: Mapping[str, str] | None = None) -> Path:
    return default_user_config_home(environment=environment) / "workspaces.json"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise UserPreferencesError(f"duplicate user configuration key: {key}")
        value[key] = item
    return value


def _read(path: Path) -> Any | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UserPreferencesError(f"cannot inspect user configuration: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_RECORD_BYTES:
        raise UserPreferencesError(f"user configuration must be a bounded regular file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UserPreferencesError(f"user configuration is not valid UTF-8 JSON: {path}") from exc


def _expression(value: str) -> str:
    if type(value) is not str or not value:
        raise UserPreferencesError("a location must be a non-empty path")
    if "\0" in value:
        raise UserPreferencesError("a location cannot contain a NUL byte")
    if value == "~" or value.startswith("~/") or value.startswith("./"):
        if ".." in Path(value).parts:
            raise UserPreferencesError("relative location expressions cannot traverse upward")
        return value
    if Path(value).is_absolute():
        return value
    raise UserPreferencesError("use an absolute path, ~/path, or ./path within the configuration home")


def resolve_expression(
    expression: str,
    *,
    environment: Mapping[str, str] | None = None,
    config_home: Path | None = None,
) -> Path:
    """Resolve a stored preference without creating its destination."""

    selected = _expression(expression)
    home = user_home(environment=environment)
    config = default_user_config_home(environment=environment) if config_home is None else config_home
    if selected == "~":
        candidate = home
    elif selected.startswith("~/"):
        candidate = home / selected[2:]
    elif selected.startswith("./"):
        candidate = config / selected[2:]
    else:
        candidate = Path(selected)
    try:
        return _state_root(candidate)
    except ValueError as exc:
        raise UserPreferencesError(f"location is unsafe: {expression}: {exc}") from exc


def load_settings(
    path: Path | str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    selected = default_settings_path(environment=environment) if path is None else Path(path)
    value = _read(selected)
    if value is None:
        return _settings({})
    if type(value) is not dict or set(value) != {"format", "schema_version", "locations", "record_id"}:
        raise UserPreferencesError("unsupported user settings fields")
    if value["format"] != SETTINGS_FORMAT or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise UserPreferencesError("this Workbench cannot read the user settings schema")
    locations = value["locations"]
    if type(locations) is not dict or set(locations) - LOCATION_ROLES:
        raise UserPreferencesError("user settings contain unsupported location roles")
    for role, expression in locations.items():
        if role not in LOCATION_ROLES:
            raise UserPreferencesError(f"unsupported location role: {role}")
        _expression(expression)
    if value != _settings(locations):
        raise UserPreferencesError("user settings identity does not match the saved choices")
    return value


def load_workspaces(
    path: Path | str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    selected = default_workspaces_path(environment=environment) if path is None else Path(path)
    value = _read(selected)
    if value is None:
        return _workspaces([], None)
    if type(value) is not dict or set(value) != {
        "format", "schema_version", "default", "entries", "record_id"
    }:
        raise UserPreferencesError("unsupported user workspace fields")
    if value["format"] != WORKSPACES_FORMAT or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise UserPreferencesError("this Workbench cannot read the user workspaces schema")
    entries = value["entries"]
    if type(entries) is not list or len(entries) > 256:
        raise UserPreferencesError("user workspace registry has too many entries")
    names: set[str] = set()
    for row in entries:
        if type(row) is not dict or set(row) != {"name", "path"}:
            raise UserPreferencesError("user workspace entry has unsupported fields")
        if type(row["name"]) is not str or _WORKSPACE_NAME.fullmatch(row["name"]) is None:
            raise UserPreferencesError("user workspace name is invalid")
        _expression(row["path"])
        if row["name"] in names:
            raise UserPreferencesError("duplicate user workspace name")
        names.add(row["name"])
    if value["default"] is not None and (
        type(value["default"]) is not str or value["default"] not in names
    ):
        raise UserPreferencesError("default workspace is not registered")
    if value != _workspaces(entries, value["default"]):
        raise UserPreferencesError("user workspaces identity does not match the saved choices")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    _state_root(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise UserPreferencesError("user configuration destination is not a regular file")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _prepare_home(path: Path) -> None:
    _state_root(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def set_location(
    role: str,
    expression: str | None,
    *,
    environment: Mapping[str, str] | None = None,
    expected_record_id: str | None = None,
) -> dict[str, Any]:
    """Save one preference while retaining every other choice."""

    if role not in LOCATION_ROLES:
        raise UserPreferencesError(f"unsupported location role: {role}")
    if expression is not None:
        resolve_expression(expression, environment=environment)
    path = default_settings_path(environment=environment)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_settings(path)
        if expected_record_id is not None and current["record_id"] != expected_record_id:
            raise UserPreferencesError("user settings changed after review")
        locations = dict(current["locations"])
        if expression is None:
            locations.pop(role, None)
        else:
            locations[role] = expression
        result = _settings(locations)
        _write(path, result)
        return result


def register_workspace(
    name: str,
    expression: str,
    *,
    make_default: bool = False,
    environment: Mapping[str, str] | None = None,
    expected_record_id: str | None = None,
) -> dict[str, Any]:
    """Retain one named workspace without copying project or profile content."""

    if type(name) is not str or _WORKSPACE_NAME.fullmatch(name) is None:
        raise UserPreferencesError("workspace name must be a lowercase slug")
    resolve_expression(expression, environment=environment)
    path = default_workspaces_path(environment=environment)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_workspaces(path)
        if expected_record_id is not None and current["record_id"] != expected_record_id:
            raise UserPreferencesError("user workspaces changed after review")
        entries = [row for row in current["entries"] if row["name"] != name]
        entries.append({"name": name, "path": expression})
        default = name if make_default else current["default"]
        result = _workspaces(entries, default)
        _write(path, result)
        return result


def remove_workspace(name: str, *, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    if type(name) is not str or _WORKSPACE_NAME.fullmatch(name) is None:
        raise UserPreferencesError("workspace name must be a lowercase slug")
    path = default_workspaces_path(environment=environment)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_workspaces(path)
        entries = [row for row in current["entries"] if row["name"] != name]
        if len(entries) == len(current["entries"]):
            raise UserPreferencesError(f"workspace is not registered: {name}")
        default = None if current["default"] == name else current["default"]
        result = _workspaces(entries, default)
        _write(path, result)
        return result


def choose_default_workspace(
    name: str | None, *, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    path = default_workspaces_path(environment=environment)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_workspaces(path)
        if name is not None and name not in {row["name"] for row in current["entries"]}:
            raise UserPreferencesError(f"workspace is not registered: {name}")
        result = _workspaces(current["entries"], name)
        _write(path, result)
        return result


__all__ = [
    "LOCATION_ROLES",
    "UserPreferencesError",
    "default_settings_path",
    "default_workspaces_path",
    "load_settings",
    "load_workspaces",
    "choose_default_workspace",
    "register_workspace",
    "remove_workspace",
    "resolve_expression",
    "set_location",
]
