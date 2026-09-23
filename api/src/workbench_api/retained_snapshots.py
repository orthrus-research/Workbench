"""Owner-neutral ports for reading an explicitly selected retained check.

Core verifies custody and supplies bytes. The producer admits its historical
request and observation contracts. Consumers do not initialize the producer.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Protocol

from packaging.utils import canonicalize_name

from .modules import ModuleError
from .profiles import require_optional_distribution


@dataclass(frozen=True)
class RetainedSnapshotAdmission:
    scope: Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
    expected: Mapping[str, Any]
    supported_schemas: frozenset[str]


class RetainedInputs(Protocol):
    attempt_id: str
    context: Mapping[str, Any]

    def read_input(self, role: str) -> bytes: ...
    def source_files(self, directory: str, files: list[dict]) -> dict[str, bytes]: ...
    def seal(self, kind: str, value: dict) -> dict: ...
    def scope_identity(self, scope: Mapping[str, Any]) -> str: ...


class RetainedSnapshotReader(Protocol):
    request: Mapping[str, Any]
    manifest: Mapping[str, Any]
    custody: Mapping[str, Any]
    scope_supported: bool
    unsupported_sections: set[str]

    def read_record(self, section: str, key: str) -> Any: ...
    def query(self, query: dict) -> dict: ...


@dataclass(frozen=True)
class RetainedSnapshotProvider:
    open_snapshot: Callable[..., AbstractContextManager[RetainedSnapshotReader]]
    api_version: int = 1


def retained_snapshot_provider(name: str, requirement: str) -> RetainedSnapshotProvider:
    """Load one caller-selected provider without importing its implementation.

    The composition caller supplies both the provider and versioned distribution;
    retained evidence must not select executable code. Host admission is checked
    on every request, including after a provider has already been imported.
    """
    admitted = require_optional_distribution(requirement)
    entries = tuple(metadata.entry_points(group="workbench.retained_snapshot_providers", name=name))
    if len(entries) != 1:
        raise ModuleError("retained snapshot reading requires exactly one selected provider")
    entry = entries[0]
    if (entry.dist is None
            or canonicalize_name(entry.dist.metadata.get("Name", "")) != admitted["distribution"]
            or entry.dist.version != admitted["version"]):
        raise ModuleError("retained snapshot provider differs from the admitted distribution")
    try:
        provider = entry.load()()
    except (Exception, SystemExit) as exc:
        raise ModuleError("retained snapshot provider could not be loaded") from exc
    if (not isinstance(provider, RetainedSnapshotProvider)
            or type(provider.api_version) is not int or provider.api_version != 1
            or not callable(provider.open_snapshot)):
        raise ModuleError("retained snapshot provider has an incompatible API")
    return provider
