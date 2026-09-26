"""Durable user location choices, independent of suite and profile sources."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import stat
from typing import Any, Mapping
from uuid import uuid4

from workbench_api.host_filesystem import DurableRecordError

from .durable_records import replace_private_bytes
from .setup_cli import _state_root, setup_record_lock
from .user_config_home import default_user_config_home, user_home


SETTINGS_FORMAT = "workbench-user-settings-v1"
WORKSPACES_FORMAT = "workbench-user-workspaces-v1"
WORKSPACES_FORMAT_V2 = "workbench-user-workspaces-v2"
WORKSPACES_FORMAT_V3 = "workbench-user-workspaces-v3"
MANAGED_JAVA_FEATURES = frozenset({8})
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
_WORKSPACE_ID = re.compile(r"workbench-workspace-v1:[0-9a-f]{32}\Z")


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


def _workspaces_v2(entries: list[dict[str, Any]], default: str | None) -> dict[str, Any]:
    return _seal("workbench-user-workspaces", {
        "format": WORKSPACES_FORMAT_V2,
        "schema_version": 2,
        "default": default,
        "entries": sorted(entries, key=lambda row: row["name"]),
    })


def _workspaces_v3(entries: list[dict[str, Any]], default: str | None) -> dict[str, Any]:
    return _seal("workbench-user-workspaces", {
        "format": WORKSPACES_FORMAT_V3,
        "schema_version": 3,
        "default": default,
        "entries": sorted(entries, key=lambda row: row["name"]),
    })


def _workspace_record(entries: list[dict[str, Any]], default: str | None, *, version: int) -> dict[str, Any]:
    if version == 3:
        return _workspaces_v3(entries, default)
    return _workspaces_v2(entries, default) if version == 2 else _workspaces(entries, default)


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


def resolve_selection_path(
    expression: str, *, environment: Mapping[str, str] | None = None,
    config_home: Path | None = None,
) -> Path:
    """Resolve a saved file or JDK candidate without requiring it to exist."""

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
        _state_root(candidate.parent)
    except ValueError as exc:
        raise UserPreferencesError(f"workspace selection is unsafe: {expression}: {exc}") from exc
    if candidate.is_symlink():
        raise UserPreferencesError("workspace selection cannot be a symbolic link")
    return candidate.absolute()


def resolve_java_path(
    expression: str, *, environment: Mapping[str, str] | None = None,
    config_home: Path | None = None,
) -> Path:
    """Keep a user-supplied Java path lexical, including symlink aliases."""

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
    return candidate.absolute()


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
    if (
        type(value["schema_version"]) is not int
        or (value["format"], value["schema_version"]) not in {
            (WORKSPACES_FORMAT, 1), (WORKSPACES_FORMAT_V2, 2),
            (WORKSPACES_FORMAT_V3, 3),
        }
    ):
        raise UserPreferencesError("this Workbench cannot read the user workspaces schema")
    version = value["schema_version"]
    entries = value["entries"]
    if type(entries) is not list or len(entries) > 256:
        raise UserPreferencesError("user workspace registry has too many entries")
    names: set[str] = set()
    for row in entries:
        expected_fields = {"name", "path"} if version == 1 else {
            "name", "path", "workspace_id", "profile_config", "java_home"
        }
        if version == 3:
            expected_fields.add("managed_java_feature")
        if type(row) is not dict or set(row) != expected_fields:
            raise UserPreferencesError("user workspace entry has unsupported fields")
        if type(row["name"]) is not str or _WORKSPACE_NAME.fullmatch(row["name"]) is None:
            raise UserPreferencesError("user workspace name is invalid")
        _expression(row["path"])
        if version == 2:
            if type(row["workspace_id"]) is not str or _WORKSPACE_ID.fullmatch(row["workspace_id"]) is None:
                raise UserPreferencesError("user workspace reference is invalid")
            for field in ("profile_config", "java_home"):
                if row[field] is not None:
                    _expression(row[field])
        if version == 3:
            if type(row["workspace_id"]) is not str or _WORKSPACE_ID.fullmatch(row["workspace_id"]) is None:
                raise UserPreferencesError("user workspace reference is invalid")
            for field in ("profile_config", "java_home"):
                if row[field] is not None:
                    _expression(row[field])
            feature = row["managed_java_feature"]
            if feature is not None and (type(feature) is not int or feature not in MANAGED_JAVA_FEATURES):
                raise UserPreferencesError("unsupported managed Java feature")
            if feature is not None and row["java_home"] is not None:
                raise UserPreferencesError("choose a managed Java feature or a Java path")
        if row["name"] in names:
            raise UserPreferencesError("duplicate user workspace name")
        names.add(row["name"])
    if value["default"] is not None and (
        type(value["default"]) is not str or value["default"] not in names
    ):
        raise UserPreferencesError("default workspace is not registered")
    if version in {2, 3} and len({row["workspace_id"] for row in entries}) != len(entries):
        raise UserPreferencesError("duplicate user workspace reference")
    if value != _workspace_record(entries, value["default"], version=version):
        raise UserPreferencesError("user workspaces identity does not match the saved choices")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    _state_root(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    try:
        replace_private_bytes(path, payload, byte_limit=MAX_RECORD_BYTES)
    except DurableRecordError as exc:
        raise UserPreferencesError(f"cannot save user configuration: {exc}") from exc


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
    selected_root = resolve_expression(expression, environment=environment)
    path = default_workspaces_path(environment=environment)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_workspaces(path)
        if expected_record_id is not None and current["record_id"] != expected_record_id:
            raise UserPreferencesError("user workspaces changed after review")
        for existing in current["entries"]:
            if existing["name"] == name:
                continue
            other = resolve_expression(existing["path"], environment=environment)
            if selected_root == other or (
                selected_root.exists() and other.exists() and selected_root.samefile(other)
            ):
                raise UserPreferencesError("another named workspace uses this directory")
        entries = [row for row in current["entries"] if row["name"] != name]
        previous = next((row for row in current["entries"] if row["name"] == name), None)
        if current["schema_version"] in {2, 3}:
            entry = (
                dict(previous) if previous is not None and previous["path"] == expression
                else {"workspace_id": "workbench-workspace-v1:" + uuid4().hex,
                      "profile_config": None, "java_home": None,
                      **({"managed_java_feature": None} if current["schema_version"] == 3 else {})}
            )
            entry.update(name=name, path=expression)
        else:
            entry = {"name": name, "path": expression}
        entries.append(entry)
        default = name if make_default else current["default"]
        result = _workspace_record(entries, default, version=current["schema_version"])
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
        result = _workspace_record(entries, default, version=current["schema_version"])
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
        result = _workspace_record(current["entries"], name, version=current["schema_version"])
        _write(path, result)
        return result


def set_workspace_selection(
    name: str, *, profile_config: str | None | object = Ellipsis,
    java_home: str | None | object = Ellipsis,
    managed_java_feature: int | None | object = Ellipsis,
    environment: Mapping[str, str] | None = None,
    expected_record_id: str | None = None,
) -> dict[str, Any]:
    """Save a revisioned workspace choice, migrating only when needed."""

    if type(name) is not str or _WORKSPACE_NAME.fullmatch(name) is None:
        raise UserPreferencesError("workspace name must be a lowercase slug")
    if profile_config is Ellipsis and java_home is Ellipsis and managed_java_feature is Ellipsis:
        raise UserPreferencesError("select a profile or Java choice to change")
    if profile_config is not Ellipsis and profile_config is not None:
        if type(profile_config) is not str:
            raise UserPreferencesError("workspace selection must be a path expression")
        resolve_selection_path(profile_config, environment=environment)
    if java_home is not Ellipsis and java_home is not None:
        if type(java_home) is not str:
            raise UserPreferencesError("workspace selection must be a path expression")
        resolve_java_path(java_home, environment=environment)
    if managed_java_feature is not Ellipsis and managed_java_feature is not None:
        if type(managed_java_feature) is not int or managed_java_feature not in MANAGED_JAVA_FEATURES:
            raise UserPreferencesError("unsupported managed Java feature")
    if java_home is not Ellipsis and java_home is not None and managed_java_feature is not Ellipsis and managed_java_feature is not None:
        raise UserPreferencesError("choose a managed Java feature or a Java path")
    path = default_workspaces_path(environment=environment)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_workspaces(path)
        if expected_record_id is not None and current["record_id"] != expected_record_id:
            raise UserPreferencesError("user workspaces changed after review")
        version = 3 if current["schema_version"] == 3 or managed_java_feature is not Ellipsis else 2
        entries = []
        found = False
        for old in current["entries"]:
            row = dict(old)
            if current["schema_version"] == 1:
                row.update(
                    workspace_id="workbench-workspace-v1:" + uuid4().hex,
                    profile_config=None, java_home=None,
                )
            if version == 3 and current["schema_version"] != 3:
                row["managed_java_feature"] = None
            if row["name"] == name:
                found = True
                if profile_config is not Ellipsis:
                    row["profile_config"] = profile_config
                if java_home is not Ellipsis:
                    row["java_home"] = java_home
                    if java_home is not None and version == 3:
                        row["managed_java_feature"] = None
                if managed_java_feature is not Ellipsis:
                    row["managed_java_feature"] = managed_java_feature
                    if managed_java_feature is not None:
                        row["java_home"] = None
            entries.append(row)
        if not found:
            raise UserPreferencesError(f"workspace is not registered: {name}")
        result = _workspace_record(entries, current["default"], version=version)
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
    "resolve_java_path",
    "resolve_selection_path",
    "set_location",
    "set_workspace_selection",
]
