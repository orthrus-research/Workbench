"""Read-only translation from user choices to operation location parameters."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .physical_context import resolve_physical_context
from .setup_cli import _state_root, _workspace, default_setup_record_path, load_setup_record
from .user_config_home import default_user_config_home, default_user_logs_root, user_home
from .user_preferences import (
    LOCATION_ROLES,
    default_settings_path,
    default_workspaces_path,
    load_settings,
    load_workspaces,
    resolve_expression,
)


FORMAT = "workbench-environment-resolution-v1"


@dataclass(frozen=True, slots=True)
class ResolvedEnvironment:
    configuration_home: Path
    workspace: Path
    state_root: Path
    locations: Mapping[str, Path]
    record: Mapping[str, Any]

    def location(self, role: str) -> Path:
        if role not in LOCATION_ROLES:
            raise ValueError(f"unsupported Workbench location role: {role}")
        return self.locations[role]


def _entry(path: Path, source: str) -> dict[str, str]:
    return {"path": str(path), "source": source}


def resolve_environment(
    suite_root: Path | str,
    *,
    workspace: Path | str | None = None,
    location_overrides: Mapping[str, Path | str] | None = None,
    environment: Mapping[str, str] | None = None,
    current_directory: Path | str | None = None,
) -> ResolvedEnvironment:
    """Resolve paths and their provenance without creating local state.

    Explicit targets win, then the named user default, then Setup V1. Profile
    references and saved Java/Git paths remain candidates for owner validation.
    """

    values = os.environ if environment is None else environment
    config_home = default_user_config_home(environment=values)
    settings_path = default_settings_path(environment=values)
    workspaces_path = default_workspaces_path(environment=values)
    settings = load_settings(settings_path)
    workspaces = load_workspaces(workspaces_path)
    setup_path = default_setup_record_path(environment=values)
    setup = load_setup_record(setup_path)
    selection = setup["selection"] if setup is not None else {}
    selected_workspace: Path | str | None = workspace
    if selected_workspace is not None:
        workspace_source = "argument"
    elif workspaces["default"] is not None:
        row = next(row for row in workspaces["entries"] if row["name"] == workspaces["default"])
        selected_workspace = resolve_expression(row["path"], environment=values, config_home=config_home)
        workspace_source = "user-workspaces"
    elif selection.get("workspace"):
        workspace_source = "setup-v1"
    elif values.get("WORKBENCH_WORKSPACE"):
        workspace_source = "environment"
    else:
        workspace_source = "current-directory"
    physical = resolve_physical_context(
        suite_root,
        workspace=selected_workspace,
        environment=values,
        current_directory=current_directory,
        _setup_record_path=setup_path,
        _saved_setup=setup,
        _workspace_registry=workspaces,
    )
    selected = _workspace(physical.workspace)
    if values.get("WORKBENCH_STATE_ROOT"):
        state_source = "environment"
    elif selection.get("state_root"):
        state_source = "setup-v1"
    else:
        state_source = "platform-default"
    home = user_home(environment=values)
    defaults = {
        "workspace_parent": home / "Workspaces",
        "artifacts": physical.state_root / "artifacts",
        "logs": default_user_logs_root(environment=values),
        "blueprint_library": config_home / "library/blueprints",
        "blueprint_sessions": physical.state_root / "blueprints",
        "fixture_library": config_home / "library/fixtures",
        "fixture_instances": physical.state_root / "fixture-instances",
        "cache": physical.state_root / "cache",
        "evidence": physical.state_root / "evidence",
    }
    overrides = {} if location_overrides is None else dict(location_overrides)
    if set(overrides) - LOCATION_ROLES:
        raise ValueError("unsupported Workbench location override")
    locations: dict[str, Path] = {}
    location_records: dict[str, dict[str, str]] = {}
    for role in sorted(LOCATION_ROLES):
        if role in overrides:
            path = resolve_expression(
                str(overrides[role]), environment=values, config_home=config_home
            )
            source = "argument"
        elif role in settings["locations"]:
            path = resolve_expression(
                settings["locations"][role],
                environment=values,
                config_home=config_home,
            )
            source = "user-settings"
        else:
            path = _state_root(defaults[role])
            source = "default"
        locations[role] = path
        location_records[role] = _entry(path, source)
    body: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": 1,
        "configuration_home": str(config_home),
        "settings": {
            "path": str(settings_path),
            "record_id": settings["record_id"],
            "present": settings_path.is_file(),
        },
        "workspaces": {
            "path": str(workspaces_path),
            "record_id": workspaces["record_id"],
            "present": workspaces_path.is_file(),
        },
        "setup": {"path": str(setup_path), "record_id": setup["record_id"] if setup else None},
        "workspace": _entry(selected, workspace_source),
        "state_root": _entry(physical.state_root, state_source),
        "locations": location_records,
        "profile_configuration_reference": (
            str(physical.profile_configuration_reference)
            if physical.profile_configuration_reference is not None
            else None
        ),
        "tool_candidates": {
            "java_home": (
                values.get("WORKBENCH_JAVA_HOME")
                or selection.get("java_home")
                or selection.get("managed_java_home")
            ),
            "git_executable": (
                values.get("WORKBENCH_GIT_EXECUTABLE")
                or selection.get("git_executable")
            ),
        },
    }
    identity = "workbench-environment-resolution:sha256:" + sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ResolvedEnvironment(
        configuration_home=config_home,
        workspace=selected,
        state_root=physical.state_root,
        locations=MappingProxyType(dict(locations)),
        record={**body, "resolution_id": identity},
    )


__all__ = ["FORMAT", "ResolvedEnvironment", "resolve_environment"]
