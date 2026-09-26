"""Compact Core custody for large immutable transport trees.

The owner supplies the exact domain validator. Core inventories, flushes and
publishes its verified directory without replacing an existing destination.
The V2 reference carries counts and a streaming digest, not a member list.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Protocol


class TransportTreeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TransportTreeReference:
    tree_id: str
    store_id: str
    workspace: Path
    owner_id: str
    path: Path
    domain_id: str
    inventory_sha256: str
    file_count: int
    directory_count: int
    total_bytes: int


class TransportTreeStage(Protocol):
    tree_id: str
    path: Path  # Absent until the owner creates the payload.
    target: Path

    def publish(self, *, validate: Callable[[Path], object],
                domain_id: str) -> TransportTreeReference: ...


class TransportTrees(Protocol):
    def stage(self, target: Path) -> ContextManager[TransportTreeStage]: ...
    def describe(self, tree_id: str) -> TransportTreeReference: ...
    def reconcile(self, tree_id: str) -> TransportTreeReference: ...


_bound: ContextVar[TransportTrees | None] = ContextVar("workbench_transport_trees", default=None)


@contextmanager
def transport_trees_scope(provider: TransportTrees) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def transport_trees() -> TransportTrees:
    provider = _bound.get()
    if provider is None:
        raise TransportTreeError("transport.host", "no Core transport tree host is bound")
    return provider


__all__ = [
    "TransportTreeError", "TransportTreeReference", "TransportTreeStage",
    "TransportTrees", "transport_trees", "transport_trees_scope",
]
