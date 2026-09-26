"""Core-bound retained check attempts and snapshot custody.

Owners select their check meaning and admit their saved result. Core owns the
attempt allocation, execution lease, snapshot publication and retained read.
The exact attempt path remains an interoperability handle for native workers.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, Mapping, Protocol

from .managed_attempts import ManagedAttemptReference
from .retained_snapshots import RetainedSnapshotAdmission


class CheckAttemptError(ValueError):
    """A retained check was not admitted under Core custody."""


class CheckSnapshotReader(Protocol):
    manifest: Mapping[str, Any]
    publication: Mapping[str, Any]
    scope_supported: bool
    unsupported_sections: set[str]

    def read_record(self, section: str, key: str) -> Any: ...
    def query(self, query: dict) -> dict: ...
    def export(self, destination: Path) -> dict: ...
    def export_record(self, section: str, key: str, digest: str, destination: Path) -> dict: ...


class CheckAttempts(Protocol):
    def allocate(self, family: str, prefix: str, *, requested_root: Path,
                 workspace: Path | None = None) -> ManagedAttemptReference: ...
    def open_attempt(self, family: str, prefix: str, attempt_id: str, *,
                     requested_root: Path) -> ManagedAttemptReference: ...
    def execution(self, attempt: ManagedAttemptReference) -> ContextManager[None]: ...
    def active(self, attempt: ManagedAttemptReference) -> bool: ...
    def cancellation_requested(self, attempt: ManagedAttemptReference, binding: str) -> bool: ...
    def request_cancel(self, attempt: ManagedAttemptReference, binding: str) -> None: ...
    def publish_snapshot(self, attempt: ManagedAttemptReference, source: str, *,
                         scope: Mapping[str, Any], describe: Callable, verify: Callable,
                         cancelled: Callable[[], bool] = lambda: False) -> dict: ...
    def register_snapshot(self, attempt: ManagedAttemptReference, *, inputs: Mapping[str, str],
                          source_directories: tuple[str, ...] = (), context: Mapping[str, Any] | None = None,
                          references: tuple[str, ...] = (), reproduction: tuple[dict, ...] = ()) -> dict: ...
    def read_snapshot(self, attempt: ManagedAttemptReference, *, admission: RetainedSnapshotAdmission,
                      owner_id: str | None = None, admit: Callable | None = None,
                      expected_context: Mapping[str, Any] | None = None,
                      cancelled: Callable[[], bool] = lambda: False) -> ContextManager[CheckSnapshotReader]: ...
    def rebuild_snapshot(self, attempt: ManagedAttemptReference, *, admission: RetainedSnapshotAdmission,
                         cancelled: Callable[[], bool] = lambda: False) -> dict: ...
    def reconcile_snapshot(self, attempt: ManagedAttemptReference, *,
                           admission: RetainedSnapshotAdmission) -> list[dict]: ...


_bound: ContextVar[CheckAttempts | None] = ContextVar("workbench_check_attempts", default=None)


@contextmanager
def check_attempts_scope(provider: CheckAttempts) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def check_attempts() -> CheckAttempts:
    provider = _bound.get()
    if provider is None:
        raise CheckAttemptError("no retained check host is bound; invoke through Workbench Core")
    return provider


__all__ = ["CheckAttemptError", "CheckSnapshotReader", "CheckAttempts", "check_attempts", "check_attempts_scope"]
