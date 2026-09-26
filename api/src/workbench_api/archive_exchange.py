"""Core-bound archive transport for domain-owned portable bundles.

The domain selects and validates its exact members. Core verifies transport
bytes, stages imports, and publishes a new destination. A host scope is bound
by admitted dispatch; archive contents never select an implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol


class ArchiveExchangeUnavailable(ValueError):
    """The current operation has no Core archive transport binding."""


class ArchiveExchange(Protocol):
    def build_manifest(
        self, members: Sequence[Mapping[str, object]], *, metadata: dict
    ) -> dict: ...

    def verify_directory(
        self, directory: Path, *, cancelled: Callable[[], bool] = ...
    ) -> dict: ...

    def import_archive(
        self, archive: Path, destination: Path, *,
        validate: Callable[[Path, dict], dict] | None = None,
        cancelled: Callable[[], bool] = ...,
    ) -> dict: ...

    def export_archive(
        self, destination: Path, members: Mapping[str, Path], *,
        metadata: dict, expected_manifest: dict | None = None,
        cancelled: Callable[[], bool] = ...,
    ) -> dict: ...


_bound: ContextVar[ArchiveExchange | None] = ContextVar(
    "workbench_archive_exchange", default=None
)


@contextmanager
def archive_exchange_scope(provider: ArchiveExchange) -> Iterator[None]:
    """Bind one admitted host for the current operation and its domain calls."""

    if not all(callable(getattr(provider, name, None)) for name in (
        "build_manifest", "verify_directory", "import_archive", "export_archive",
    )):
        raise ArchiveExchangeUnavailable("archive transport host is incomplete")
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def current_archive_exchange() -> ArchiveExchange:
    provider = _bound.get()
    if provider is None:
        raise ArchiveExchangeUnavailable(
            "Core archive transport is unavailable; run through Workbench Core"
        )
    return provider


__all__ = [
    "ArchiveExchange", "ArchiveExchangeUnavailable", "archive_exchange_scope",
    "current_archive_exchange",
]
