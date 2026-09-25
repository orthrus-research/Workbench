"""Stable per-user configuration home and the former platform location."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping


class LegacyConfigMigrationRequired(ValueError):
    """An old user record must be imported before normal use."""


def user_home(*, environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    selected = values.get("USERPROFILE") if os.name == "nt" else values.get("HOME")
    return Path(selected).expanduser().absolute() if selected else Path.home().absolute()


def default_user_config_home(
    *, environment: Mapping[str, str] | None = None
) -> Path:
    """Keep user choices at one version-independent path on every host."""

    values = os.environ if environment is None else environment
    explicit = values.get("WORKBENCH_CONFIG_HOME")
    if explicit:
        selected = Path(explicit).expanduser()
        if not selected.is_absolute():
            raise ValueError("WORKBENCH_CONFIG_HOME must be an absolute directory")
        return selected.absolute()
    return user_home(environment=values) / ".workbench"


def legacy_user_config_home(
    *, environment: Mapping[str, str] | None = None
) -> Path:
    """Locate earlier Setup V1 records without creating or moving them."""

    values = os.environ if environment is None else environment
    home = user_home(environment=values)
    if os.name == "nt":
        configured = values.get("LOCALAPPDATA") or values.get("APPDATA")
        base = Path(configured).expanduser() / "Workbench" if configured else home / "AppData/Local/Workbench"
    elif sys.platform == "darwin":
        base = home / "Library/Application Support/Workbench"
    else:
        configured = values.get("XDG_CONFIG_HOME")
        base = Path(configured).expanduser() / "workbench" if configured else home / ".config/workbench"
    return base.absolute()


def default_user_logs_root(
    *, environment: Mapping[str, str] | None = None
) -> Path:
    """Keep shared logs outside a selected workspace or operation state root."""

    values = os.environ if environment is None else environment
    home = user_home(environment=values)
    if os.name == "nt":
        configured = values.get("LOCALAPPDATA")
        base = Path(configured).expanduser() / "Workbench" if configured else home / "AppData/Local/Workbench"
    elif sys.platform == "darwin":
        base = home / "Library/Application Support/Workbench"
    else:
        configured = values.get("XDG_STATE_HOME")
        base = Path(configured).expanduser() / "workbench" if configured else home / ".local/state/workbench"
    return (base / "logs").absolute()


def default_user_record_path(
    name: str, *, environment: Mapping[str, str] | None = None
) -> Path:
    """Use the stable home and require explicit import of an older record."""

    if name not in {"setup-v1.json", "recipe-fixtures-v1.json", "launcher-v1.json"}:
        raise ValueError(f"unsupported user configuration record: {name}")
    values = os.environ if environment is None else environment
    current = default_user_config_home(environment=values) / name
    if values.get("WORKBENCH_CONFIG_HOME"):
        return current
    former = legacy_user_config_home(environment=values) / name
    if not (current.exists() or current.is_symlink()) and (
        former.exists() or former.is_symlink()
    ):
        raise LegacyConfigMigrationRequired(
            f"legacy user configuration exists at {former}; run "
            "'workbench settings migrate --dry-run', then 'workbench settings migrate'"
        )
    return current


__all__ = [
    "default_user_config_home", "default_user_logs_root", "default_user_record_path",
    "legacy_user_config_home", "LegacyConfigMigrationRequired", "user_home"
]
