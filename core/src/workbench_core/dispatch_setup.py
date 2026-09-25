"""Apply explicitly saved user setup before dispatch."""
from __future__ import annotations
import os
import sys

def _activate_user_setup(arguments: list[str]) -> bool:
    """Load physical user defaults while preserving explicit CLI authority."""

    if (
        arguments[:1] in (["setup"], ["settings"], ["repair"], ["tooling"], ["version"], ["--version"])
        or arguments[:2] == ["environment", "resolve"]
        or "--help" in arguments
        or "-h" in arguments
    ):
        return True
    from workbench_core.setup_cli import (
        SetupError,
        apply_setup_environment_defaults,
        default_setup_record_path,
        load_setup_record,
    )

    try:
        record = load_setup_record(default_setup_record_path())
        if record is not None:
            apply_setup_environment_defaults(record)
        from .user_preferences import load_workspaces, resolve_expression
        registry = load_workspaces()
        if registry["default"] is not None:
            entry = next(row for row in registry["entries"] if row["name"] == registry["default"])
            os.environ["WORKBENCH_WORKSPACE"] = str(resolve_expression(entry["path"]))
        return True
    except (OSError, SetupError, ValueError) as exc:
        print(
            "Workbench setup is invalid: "
            f"{exc}; run workbench setup --check or workbench setup --repair",
            file=sys.stderr,
        )
        return False
