"""Explicit runtime-provider contract; Core does not select a default profile."""

from __future__ import annotations

from importlib import metadata
from typing import Any
from packaging.utils import canonicalize_name

from .profiles import profile_status


class RuntimeProviderError(RuntimeError):
    """The selected runtime provider is missing, incompatible, or rejected input."""


def runtime_provider(name: str) -> Any:
    admitted = [row for row in profile_status() if row.id == name and row.state == "available"]
    if len(admitted) != 1:
        raise RuntimeProviderError(f"profile {name!r} is not enabled and admitted")
    entries = tuple(metadata.entry_points(group="workbench.runtime_providers", name=name))
    if len(entries) != 1:
        raise RuntimeProviderError(f"profile {name!r} requires exactly one installed runtime provider")
    try:
        if entries[0].dist is None or canonicalize_name(entries[0].dist.metadata["Name"]) != canonicalize_name(admitted[0].distribution):
            raise RuntimeProviderError(f"profile {name!r} does not own its runtime provider")
        provider = entries[0].load()()
        methods = ("resolve_profile_path", "load_profile", "discover_runtime_template", "audit_runtime_template", "provision_runtime", "configure_runtime")
        if getattr(provider, "api_version", None) != 1 or not all(callable(getattr(provider, m, None)) for m in methods):
            raise RuntimeProviderError(f"profile {name!r} has an incompatible runtime provider")
        return provider
    except RuntimeProviderError:
        raise
    except (Exception, SystemExit) as exc:
        raise RuntimeProviderError(f"profile {name!r} failed to load: {type(exc).__name__}") from exc
