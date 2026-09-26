"""Core-bound custody for retained attempts and their execution leases.

The attempt path is an interoperability handle for owners and native tools.
Core selects and registers its store, owns the lease and cancellation marker,
and keeps historical roots available for explicit reopening.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Protocol


class ManagedAttemptError(ValueError):
    """An attempt cannot be allocated or reopened under Core custody."""


@dataclass(frozen=True, slots=True)
class ManagedAttemptReference:
    family: str
    attempt_id: str
    root: Path
    path: Path
    store_id: str


class ManagedAttempts(Protocol):
    def default_root(self, family: str, *, workspace: Path | None = None) -> Path: ...

    def allocate(
        self, family: str, prefix: str, *, requested_root: Path | None = None,
        workspace: Path | None = None,
    ) -> ManagedAttemptReference: ...

    def open(
        self, family: str, prefix: str, attempt_id: str, *,
        requested_root: Path | None = None, legacy_basename: str | None = None,
    ) -> ManagedAttemptReference: ...

    def execution(self, attempt: ManagedAttemptReference) -> ContextManager[None]: ...

    def active(self, attempt: ManagedAttemptReference) -> bool: ...

    def cancellation_requested(self, attempt: ManagedAttemptReference) -> bool: ...

    def request_cancel(self, attempt: ManagedAttemptReference, binding: str) -> None: ...


_bound: ContextVar[ManagedAttempts | None] = ContextVar("workbench_managed_attempts", default=None)


@contextmanager
def managed_attempts_scope(provider: ManagedAttempts) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def managed_attempts() -> ManagedAttempts:
    provider = _bound.get()
    if provider is None:
        raise ManagedAttemptError("no managed attempt host is bound; invoke through Workbench Core")
    return provider


__all__ = [
    "ManagedAttemptError", "ManagedAttemptReference", "ManagedAttempts",
    "managed_attempts", "managed_attempts_scope",
]
