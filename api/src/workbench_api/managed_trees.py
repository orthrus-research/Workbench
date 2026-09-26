"""Core-bound staging and publication of retained directory resources.

An owner receives an absent payload path inside a Core-owned private staging
allocation. Core inventories its members, invokes the owner's validator, and
publishes the directory without replacing an existing target. Publication
uses the bounded portable-v1 inventory by default. The explicit
posix-exact-v1 inventory supports large POSIX payloads and attests exact file
and directory modes; it does not support derived members.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Protocol

from .durable_resources import ResourceReference


class ManagedTreeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ManagedTreeReference:
    tree_id: str
    store_id: str
    owner_id: str
    workspace: Path
    role: str
    path: Path
    content_sha256: str
    members: tuple[dict[str, object], ...]
    derived_status: str  # current, changed, or missing against the prepared inventory.
    references: tuple[str, ...]
    policy_id: str | None
    domain_id: str | None
    inventory_policy: str


@dataclass(frozen=True, slots=True)
class ManagedTreeTarget:
    """One exact cataloged target, including an incomplete publication."""

    tree_id: str
    status: str
    path: Path
    domain_id: str | None


class ManagedTreeStage(Protocol):
    tree_id: str
    path: Path  # Absent until the owner creates the payload.
    target: Path

    def publish(
        self, *, validate: Callable[[Path], object],
        domain_id: str | None = None,
        references: tuple[str, ...] = (),
        derived_members: tuple[str, ...] = (),
        derived_manifest_rule: str | None = None,
        inventory_policy: str = "portable-v1",
    ) -> ManagedTreeReference: ...


class ManagedTrees(Protocol):
    workspace: Path  # Exact workspace bound by the host.
    owner_id: str  # Module owner selected by the host.

    def retain_file_reference(
        self, role: str, name: str, source: Path, *,
        sha256: str, size: int, domain_id: str | None = None,
    ) -> ResourceReference: ...

    def retain_bytes_reference(
        self, role: str, name: str, data: bytes, *,
        references: tuple[str, ...] = (), domain_id: str | None = None,
    ) -> ResourceReference: ...

    def stage(
        self, role: str, name: str, *, requested_path: Path | None = None,
    ) -> ContextManager[ManagedTreeStage]: ...

    def describe(self, tree_id: str) -> ManagedTreeReference: ...

    def lookup_target(
        self, role: str, path: Path, *, domain_id: str | None = None,
    ) -> ManagedTreeTarget: ...

    def reconcile(self, tree_id: str) -> ManagedTreeReference: ...


_bound: ContextVar[ManagedTrees | None] = ContextVar("workbench_managed_trees", default=None)


@contextmanager
def managed_trees_scope(provider: ManagedTrees) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def managed_trees() -> ManagedTrees:
    provider = _bound.get()
    if provider is None:
        raise ManagedTreeError("tree.host", "no managed tree host is bound; invoke through Workbench Core")
    return provider


def managed_trees_bound() -> bool:
    return _bound.get() is not None


__all__ = [
    "ManagedTreeError", "ManagedTreeReference", "ManagedTreeStage", "ManagedTreeTarget", "ManagedTrees",
    "managed_trees", "managed_trees_bound", "managed_trees_scope",
]
