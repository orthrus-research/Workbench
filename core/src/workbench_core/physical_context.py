"""Resolve one read-only user context for Core and module dispatch."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from workbench_api import ExecutionContext
from workbench_api.state_paths import default_runtime_state_root

from .setup_cli import (
    _state_root,
    _workspace,
    default_setup_record_path,
    load_setup_record,
)


FORMAT = "workbench-physical-context-v1"


@dataclass(frozen=True, slots=True)
class PhysicalContext:
    """Physical locations, with no implicit pack or platform authority."""

    configuration_home: Path
    workspace: Path
    state_root: Path
    profile_configuration_reference: Path | None

    def execution_context(self) -> ExecutionContext:
        """Pass only the physical operation roots through the module API."""

        return ExecutionContext(self.workspace, self.state_root)

    def record(self) -> dict[str, object]:
        return {
            "format": FORMAT,
            "schema_version": 1,
            "configuration_home": str(self.configuration_home),
            "workspace": str(self.workspace),
            "state_root": str(self.state_root),
            "profile_configuration_reference": (
                str(self.profile_configuration_reference)
                if self.profile_configuration_reference is not None
                else None
            ),
        }


def resolve_physical_context(
    suite_root: Path | str,
    *,
    workspace: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
    current_directory: Path | str | None = None,
    include_saved_setup: bool = True,
) -> PhysicalContext:
    """Derive paths without creating state or activating a selected profile.

    Explicit workspace arguments win. The saved setup workspace wins over a
    bootstrap environment value; an explicit state environment value wins over
    saved setup. These are the same precedence rules used by the launcher.
    """

    values = os.environ if environment is None else environment
    record_path = default_setup_record_path(environment=values)
    saved = load_setup_record(record_path) if include_saved_setup else None
    selection = saved["selection"] if saved is not None else {}
    selected_workspace = (
        workspace
        if workspace is not None
        else selection.get("workspace")
        or values.get("WORKBENCH_WORKSPACE")
        or current_directory
        or Path.cwd()
    )
    selected_state = (
        values.get("WORKBENCH_STATE_ROOT")
        or selection.get("state_root")
        or default_runtime_state_root(suite_root, environment=values)
    )
    profile_reference = selection.get("profile_config")
    if profile_reference is not None:
        candidate = Path(profile_reference).expanduser()
        if not candidate.is_absolute():
            raise ValueError("saved profile configuration reference must be absolute")
        profile_reference = candidate.resolve()
    return PhysicalContext(
        configuration_home=record_path.parent,
        workspace=_workspace(selected_workspace),
        state_root=_state_root(selected_state),
        profile_configuration_reference=profile_reference,
    )


__all__ = ["FORMAT", "PhysicalContext", "resolve_physical_context"]
