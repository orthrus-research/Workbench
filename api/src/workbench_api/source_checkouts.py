"""Core-bound custody for an acquired Git source checkout.

The owner admits a reviewed remote and immutable object, then validates the
staged project's meaning. Core owns the clone process, stage, receipt bytes,
checkout publication and failure cleanup. A commit-only caller must not treat
the observed tree as an approved portable source lock.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Mapping, Protocol


class SourceCheckoutError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SourceCheckout(Protocol):
    @property
    def staging_root(self) -> Path: ...
    @property
    def observed_commit(self) -> str: ...
    @property
    def observed_tree(self) -> str: ...
    def publish(
        self, *, state_root: Path, receipt_id: str, receipt_bytes: bytes,
        byte_limit: int,
    ) -> Path: ...
    def close(self) -> None: ...


class SourceCheckouts(Protocol):
    def open(
        self, destination: Path, *, git_executable: str, remote_url: str,
        checkout_branch: str, expected_commit: str,
        expected_tree: str | None, environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> SourceCheckout: ...


_UNBOUND = object()
_bound: ContextVar[SourceCheckouts | None | object] = ContextVar(
    "workbench_source_checkouts", default=_UNBOUND,
)
_host_provider: SourceCheckouts | None = None


def bind_source_checkouts(provider: SourceCheckouts | None) -> None:
    """Compose the Core checkout port at a direct or dispatched entry point."""

    global _host_provider
    _host_provider = provider


@contextmanager
def source_checkouts_scope(provider: SourceCheckouts | None) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


@contextmanager
def open_source_checkout(
    destination: Path, *, git_executable: str, remote_url: str,
    checkout_branch: str, expected_commit: str,
    expected_tree: str | None, environment: Mapping[str, str],
    timeout_seconds: float,
) -> Iterator[SourceCheckout]:
    selected = _bound.get()
    provider = _host_provider if selected is _UNBOUND else selected
    if provider is None:
        raise SourceCheckoutError(
            "unavailable", "source checkout acquisition requires Workbench Core",
        )
    checkout = provider.open(
        destination, git_executable=git_executable, remote_url=remote_url,
        checkout_branch=checkout_branch, expected_commit=expected_commit,
        expected_tree=expected_tree, environment=environment,
        timeout_seconds=timeout_seconds,
    )
    try:
        yield checkout
    finally:
        checkout.close()


__all__ = [
    "SourceCheckout", "SourceCheckoutError", "SourceCheckouts",
    "bind_source_checkouts", "open_source_checkout", "source_checkouts_scope",
]
