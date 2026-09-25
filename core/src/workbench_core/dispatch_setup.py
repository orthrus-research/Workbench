"""Apply explicitly saved user setup before dispatch."""
from __future__ import annotations
from contextlib import contextmanager
import os
import sys
from threading import RLock
from typing import Iterator


_SETUP_ENVIRONMENT_KEYS = (
    "WORKBENCH_WORKSPACE",
    "WORKBENCH_STATE_ROOT",
    "WORKBENCH_GIT_EXECUTABLE",
    "WORKBENCH_JAVA_HOME",
    "PATH",
)
_ACTIVATION_LOCK = RLock()


@contextmanager
def user_setup_environment(arguments: list[str]) -> Iterator[bool]:
    """Confine legacy environment defaults to one in-process Core dispatch."""

    with _ACTIVATION_LOCK:
        previous = {key: os.environ.get(key) for key in _SETUP_ENVIRONMENT_KEYS}
        try:
            yield _activate_user_setup(arguments)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


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
    from .user_config_home import LegacyConfigMigrationRequired

    try:
        record = load_setup_record(default_setup_record_path())
        if record is not None:
            apply_setup_environment_defaults(record)
    except LegacyConfigMigrationRequired as exc:
        print(f"Workbench setup requires migration: {exc}", file=sys.stderr)
        return False
    except (OSError, SetupError, ValueError) as exc:
        print(
            "Workbench setup is invalid: "
            f"{exc}; run workbench setup --check or workbench setup --repair",
            file=sys.stderr,
        )
        return False
    from .user_preferences import load_workspaces, resolve_expression
    try:
        registry = load_workspaces()
        if registry["default"] is not None:
            entry = next(row for row in registry["entries"] if row["name"] == registry["default"])
            os.environ["WORKBENCH_WORKSPACE"] = str(resolve_expression(entry["path"]))
        return True
    except (OSError, ValueError) as exc:
        print(
            f"Workbench workspace settings are invalid: {exc}; "
            "inspect workspaces.json in the user configuration home",
            file=sys.stderr,
        )
        return False
