"""Core custody for the bounded Git metadata of a fresh project.

The construction owner selects and validates a fresh target, the exact exclude
bytes, and its V2 bootstrap marker. Core performs the physical mutations at
the historical Git paths. The surrounding Git repository and project source
transaction have separate ownership and recovery rules.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Protocol


class GitBootstrapError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class GitBootstrap(Protocol):
    def replace_exclude(self, target: Path, *, before: bytes | None, after: bytes) -> None: ...
    def restore_exclude(self, target: Path, *, before: bytes | None, after: bytes) -> None: ...
    def create_marker(self, target: Path, data: bytes) -> None: ...
    def remove_marker(self, target: Path, *, expected: bytes) -> None: ...


_UNBOUND = object()
_bound: ContextVar[GitBootstrap | None | object] = ContextVar(
    "workbench_git_bootstrap", default=_UNBOUND,
)
_host_provider: GitBootstrap | None = None


def bind_git_bootstrap(provider: GitBootstrap | None) -> None:
    """Compose the Core implementation at a direct host-service entry."""

    global _host_provider
    _host_provider = provider


@contextmanager
def git_bootstrap_scope(provider: GitBootstrap | None) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def git_bootstrap_host() -> GitBootstrap:
    selected = _bound.get()
    provider = _host_provider if selected is _UNBOUND else selected
    if provider is None:
        raise GitBootstrapError(
            "unavailable", "fresh Git metadata changes require Workbench Core",
        )
    return provider


__all__ = [
    "GitBootstrap", "GitBootstrapError", "bind_git_bootstrap",
    "git_bootstrap_host", "git_bootstrap_scope",
]
