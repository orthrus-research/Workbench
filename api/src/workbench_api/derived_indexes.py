"""Core custody for rebuilding a disposable index beside an immutable graph.

The graph owner validates the source streams and staged SQL. Core holds the
physical lease, stages outside the graph, and publishes the index and its
manifest descriptor with a recoverable prepared record.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Protocol


class DerivedIndexError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DerivedIndexAttempt:
    attempt_id: str
    root: Path
    state: str  # allocated, prepared, index-replaced, manifest-replaced, complete, changed, superseded, conflict


class DerivedIndexStage(Protocol):
    path: Path  # Absent SQLite payload path, created by the graph owner.

    def publish(
        self, *, manifest_bytes: bytes, expected_size: int,
        expected_sha256: str, validate_source: Callable[[Path], object],
    ) -> DerivedIndexAttempt: ...


class DerivedIndexes(Protocol):
    def stage(
        self, root: Path, *, graph_set_id: str,
    ) -> ContextManager[DerivedIndexStage]: ...

    def inspect(self, root: Path) -> tuple[DerivedIndexAttempt, ...]: ...


_UNBOUND = object()
_bound: ContextVar[DerivedIndexes | None | object] = ContextVar(
    "workbench_derived_indexes", default=_UNBOUND,
)
_host_provider: DerivedIndexes | None = None


def bind_derived_indexes(provider: DerivedIndexes | None) -> None:
    global _host_provider
    _host_provider = provider


@contextmanager
def derived_indexes_scope(provider: DerivedIndexes | None) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def derived_indexes_bound() -> bool:
    selected = _bound.get()
    return (_host_provider if selected is _UNBOUND else selected) is not None


def derived_indexes_scope_active() -> bool:
    """Distinguish an explicit unavailable dispatch scope from no dispatch."""
    return _bound.get() is not _UNBOUND


def derived_indexes() -> DerivedIndexes:
    selected = _bound.get()
    provider = _host_provider if selected is _UNBOUND else selected
    if provider is None:
        raise DerivedIndexError("unavailable", "derived-index publication requires Workbench Core")
    return provider


__all__ = [
    "DerivedIndexAttempt", "DerivedIndexError", "DerivedIndexStage", "DerivedIndexes",
    "bind_derived_indexes", "derived_indexes", "derived_indexes_bound",
    "derived_indexes_scope", "derived_indexes_scope_active",
]
