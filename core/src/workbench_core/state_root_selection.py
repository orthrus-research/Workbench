"""Core-owned, workspace-bound choices for retained client state roots."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from workbench_api.state_paths import default_feature_state_root

from .environment_resolution import resolve_environment
from .setup_cli import _state_root, setup_record_lock
from .user_config_home import default_user_config_home
from .user_preferences import (
    UserPreferencesError,
    _expression,
    _prepare_home,
    _read,
    _write,
    resolve_expression,
)


FORMAT = "workbench-state-root-selections-v1"
POLICY_FORMAT = "workbench-state-root-policy-v1"
ROLES = frozenset({"product-spine", "feature"})
_WORKSPACE_ID = re.compile(r"workbench-workspace-v1:[0-9a-f]{32}\Z")


def _identity(prefix: str, body: Mapping[str, Any]) -> str:
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return prefix + ":sha256:" + sha256(payload).hexdigest()


def default_state_root_selections_path(*, environment: Mapping[str, str] | None = None) -> Path:
    return default_user_config_home(environment=environment) / "state-roots-v1.json"


def _record(entries: list[dict[str, Any]]) -> dict[str, Any]:
    body = {
        "format": FORMAT,
        "schema_version": 1,
        "entries": sorted(entries, key=lambda row: row["workspace"]),
    }
    return {**body, "record_id": _identity("workbench-state-root-selections", body)}


def load_state_root_selections(
    *, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    path = default_state_root_selections_path(environment=environment)
    _state_root(path.parent)
    value = _read(path)
    if value is None:
        return _record([])
    if type(value) is not dict or set(value) != {"format", "schema_version", "entries", "record_id"}:
        raise UserPreferencesError("state-root selections have unsupported fields")
    if value["format"] != FORMAT or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise UserPreferencesError("unsupported state-root selections schema")
    entries = value["entries"]
    if type(entries) is not list or len(entries) > 256:
        raise UserPreferencesError("state-root selections have too many workspaces")
    seen: set[str] = set()
    for row in entries:
        if type(row) is not dict or set(row) != {"workspace", "workspace_id", "roots"}:
            raise UserPreferencesError("state-root selection has unsupported fields")
        workspace = row["workspace"]
        if type(workspace) is not str or not Path(workspace).is_absolute() or workspace in seen:
            raise UserPreferencesError("state-root workspace identity is invalid")
        seen.add(workspace)
        workspace_id = row["workspace_id"]
        if workspace_id is not None and (
            type(workspace_id) is not str or _WORKSPACE_ID.fullmatch(workspace_id) is None
        ):
            raise UserPreferencesError("state-root workspace reference is invalid")
        roots = row["roots"]
        if type(roots) is not dict or not roots or set(roots) - ROLES:
            raise UserPreferencesError("state-root roles are invalid")
        for expression in roots.values():
            _expression(expression)
    if value != _record(entries):
        raise UserPreferencesError("state-root selections identity does not match saved choices")
    return value


def _default_root(role: str, suite_root: Path, values: Mapping[str, str]) -> tuple[Path, str]:
    explicit = values.get("WORKBENCH_STATE_ROOT")
    if role == "product-spine":
        base = Path(explicit).expanduser() if explicit else default_feature_state_root(
            suite_root, environment=values,
        ).parent
        return _state_root(base / "product-spine"), "environment" if explicit else "platform-default"
    return _state_root(default_feature_state_root(suite_root, environment=values)), (
        "environment" if explicit else "platform-default"
    )


def _effective(
    workspace: Path | str, role: str, *, suite_root: Path,
    environment: Mapping[str, str], record: Mapping[str, Any],
) -> dict[str, Any]:
    if role not in ROLES:
        raise UserPreferencesError(f"unsupported state-root role: {role}")
    resolved = resolve_environment(suite_root, workspace=workspace, environment=environment)
    selected = resolved.workspace
    workspace_id = resolved.record["workspace_selection"]["workspace_id"]
    row = next((item for item in record["entries"] if item["workspace"] == str(selected)), None)
    expression = row["roots"].get(role) if row is not None else None
    if expression is not None and row["workspace_id"] != workspace_id:
        raise UserPreferencesError("state-root selection belongs to a changed workspace identity")
    if environment.get("WORKBENCH_STATE_ROOT"):
        root, source = _default_root(role, suite_root, environment)
    elif expression is not None:
        root = resolve_expression(
            expression, environment=environment, config_home=resolved.configuration_home,
        )
        source = "user-selection"
    else:
        root, source = _default_root(role, suite_root, environment)
    body = {
        "format": POLICY_FORMAT,
        "schema_version": 1,
        "configuration_home": str(resolved.configuration_home),
        "workspace": str(selected),
        "workspace_id": workspace_id,
        "role": role,
        "state_root": str(root),
        "source": source,
        "selections_record_id": record["record_id"],
    }
    return {**body, "policy_id": _identity("workbench-state-root-policy", body)}


def effective_state_root(
    workspace: Path | str, role: str, *, suite_root: Path | str,
    environment: Mapping[str, str] | None = None,
    expected_policy_id: str | None = None,
) -> dict[str, Any]:
    """Read Core's effective root; an expected identity refuses a stale review."""

    values = dict(os.environ if environment is None else environment)
    result = _effective(
        workspace, role, suite_root=Path(suite_root), environment=values,
        record=load_state_root_selections(environment=values),
    )
    if expected_policy_id is not None and result["policy_id"] != expected_policy_id:
        raise UserPreferencesError("state-root policy changed after review")
    return result


def select_state_root(
    workspace: Path | str, role: str, expression: str | None, *,
    suite_root: Path | str, expected_policy_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Save or clear one reviewed choice without taking custody of the root."""

    values = dict(os.environ if environment is None else environment)
    if role not in ROLES:
        raise UserPreferencesError(f"unsupported state-root role: {role}")
    if type(expected_policy_id) is not str or not expected_policy_id:
        raise UserPreferencesError("a reviewed state-root policy identity is required")
    if values.get("WORKBENCH_STATE_ROOT"):
        raise UserPreferencesError("remove the invocation state-root override before saving a durable choice")
    selected_root = resolve_expression(expression, environment=values) if expression is not None else None
    path = default_state_root_selections_path(environment=values)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_state_root_selections(environment=values)
        before = _effective(
            workspace, role, suite_root=Path(suite_root), environment=values, record=current,
        )
        if before["policy_id"] != expected_policy_id:
            raise UserPreferencesError("state-root policy changed after review")
        if selected_root is not None:
            selected_workspace = Path(before["workspace"])
            if selected_root == selected_workspace or selected_root.is_relative_to(selected_workspace):
                raise UserPreferencesError("retained state root must be outside the workspace")
        entries = [dict(row) for row in current["entries"]]
        row = next((item for item in entries if item["workspace"] == before["workspace"]), None)
        if row is None:
            row = {
                "workspace": before["workspace"],
                "workspace_id": before["workspace_id"],
                "roots": {},
            }
            entries.append(row)
        row["roots"] = dict(row["roots"])
        if expression is None:
            row["roots"].pop(role, None)
        else:
            row["roots"][role] = expression
        entries = [item for item in entries if item["roots"]]
        updated = _record(entries)
        if updated != current:
            _write(path, updated)
        return _effective(
            workspace, role, suite_root=Path(suite_root), environment=values, record=updated,
        )


def clear_stale_state_root(
    workspace: Path | str, role: str, *, suite_root: Path | str,
    expected_record_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Discard one old identity's choice after reviewing the exact saved record."""

    values = dict(os.environ if environment is None else environment)
    if role not in ROLES:
        raise UserPreferencesError(f"unsupported state-root role: {role}")
    if type(expected_record_id) is not str or not expected_record_id:
        raise UserPreferencesError("a reviewed selections record identity is required")
    path = default_state_root_selections_path(environment=values)
    _prepare_home(path)
    with setup_record_lock(path):
        current = load_state_root_selections(environment=values)
        if current["record_id"] != expected_record_id:
            raise UserPreferencesError("state-root selections changed after review")
        resolved = resolve_environment(Path(suite_root), workspace=workspace, environment=values)
        selected = str(resolved.workspace)
        workspace_id = resolved.record["workspace_selection"]["workspace_id"]
        row = next((item for item in current["entries"] if item["workspace"] == selected), None)
        if row is None or role not in row["roots"] or row["workspace_id"] == workspace_id:
            raise UserPreferencesError("there is no stale state-root selection for this workspace and role")
        entries = []
        for old in current["entries"]:
            if old["workspace"] != selected:
                entries.append(old)
                continue
            roots = {key: value for key, value in old["roots"].items() if key != role}
            if roots:
                entries.append({**old, "roots": roots})
        updated = _record(entries)
        _write(path, updated)
        return _effective(
            workspace, role, suite_root=Path(suite_root), environment=values, record=updated,
        )


__all__ = [
    "POLICY_FORMAT", "ROLES", "clear_stale_state_root",
    "default_state_root_selections_path", "effective_state_root",
    "load_state_root_selections", "select_state_root",
]
