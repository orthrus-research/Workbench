"""Product-generic mutable state locations without workflow imports."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping


PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE = "WORKBENCH_PACKAGED_SUITE_ROOT"


def default_feature_state_root(
    _suite_root: Path | str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return mutable state outside source and installed package trees."""

    values = os.environ if environment is None else environment
    explicit = values.get("WORKBENCH_STATE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        local = values.get("LOCALAPPDATA")
        base = (
            Path(local).expanduser()
            if local
            else Path.home() / "AppData" / "Local"
        )
        return base / "Workbench" / "developer-features"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Workbench"
            / "developer-features"
        )
    xdg_state = values.get("XDG_STATE_HOME")
    base = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local/state"
    return base / "workbench" / "developer-features"


def default_runtime_state_root(
    suite_root: Path | str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return the setup/runtime root, preserving an explicit override exactly."""

    values = os.environ if environment is None else environment
    explicit = values.get("WORKBENCH_STATE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return default_feature_state_root(
        suite_root,
        environment=values,
    ).parent / "runtime"


def default_suite_state_root(suite_root: Path | str) -> Path:
    """Return mutable suite state without writing into an installed package.

    Source checkouts retain their historical ignored ``.workbench`` location.
    The verified portable launcher binds its exact embedded suite through an
    internal environment marker, selecting the platform user-state directory
    for that installed, immutable copy.
    """

    suite = Path(suite_root).expanduser().resolve()
    explicit = os.environ.get("WORKBENCH_STATE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if suite.name == "workbench_resources":
        return default_runtime_state_root()
    packaged_value = os.environ.get(PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE)
    if packaged_value:
        try:
            packaged_suite = Path(packaged_value).expanduser().resolve(strict=True)
        except OSError:
            packaged_suite = None
        if packaged_suite == suite:
            return default_feature_state_root().parent / "runtime"
    return suite / ".workbench"


def default_product_spine_state_root(
    suite_root: Path | str | None = None,
) -> Path:
    """Return the shared Work Session/Home state root used by public adapters."""

    explicit = os.environ.get("WORKBENCH_STATE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve() / "product-spine"
    return default_feature_state_root(suite_root).parent / "product-spine"


__all__ = [
    "PACKAGED_SUITE_ROOT_ENVIRONMENT_VARIABLE",
    "default_feature_state_root",
    "default_product_spine_state_root",
    "default_runtime_state_root",
    "default_suite_state_root",
]
