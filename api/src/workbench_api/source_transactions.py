"""Core-bound physical transactions for explicitly admitted source edits.

The domain owner validates consent, plans, source identity and resulting
semantics. Core alone stages and mutates the protected source tree.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, Sequence


class SourceTransactionError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceImage:
    kind: str  # "file" or "symlink"
    data: bytes
    executable: bool | None = False
    mode: int | None = None


@dataclass(frozen=True, slots=True)
class SourceStage:
    token: str
    staged_relative: str | None = None


class SourceTransaction(Protocol):
    def prepare(
        self, relative: str, *, before: SourceImage | None,
        after: SourceImage | None, create_parents: bool = False,
        preserve_target_mode: bool = False,
    ) -> SourceStage: ...
    def attach(
        self, relative: str, *, before: SourceImage | None,
        after: SourceImage | None, staged_relative: str | None,
        attempted: bool,
    ) -> SourceStage: ...
    def classify(self, stage: SourceStage) -> str: ...
    def commit(self, stage: SourceStage) -> None: ...
    def rollback(self, stage: SourceStage) -> None: ...
    def rollback_all(self) -> None: ...
    def cleanup(self, *, remove_created_directories: bool = False) -> None: ...
    def cleanup_orphaned_stages(self, relatives: Sequence[str]) -> None: ...


class SourceTransactions(Protocol):
    def open(
        self, root: Path, *, binding: str, staging_token: str | None = None,
    ) -> SourceTransaction: ...


_UNBOUND = object()
_bound: ContextVar[SourceTransactions | None | object] = ContextVar(
    "workbench_source_transactions", default=_UNBOUND,
)
_host_provider: SourceTransactions | None = None


def bind_source_transactions(provider: SourceTransactions | None) -> None:
    """Compose the Core source port for direct host-service entry points."""

    global _host_provider
    _host_provider = provider


@contextmanager
def source_transactions_scope(provider: SourceTransactions | None) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def open_source_transaction(
    root: Path, *, binding: str, staging_token: str | None = None,
) -> SourceTransaction:
    selected = _bound.get()
    provider = _host_provider if selected is _UNBOUND else selected
    if provider is None:
        raise SourceTransactionError(
            "unavailable", "protected source edits require Workbench Core",
        )
    return provider.open(root, binding=binding, staging_token=staging_token)


__all__ = [
    "SourceImage", "SourceStage", "SourceTransaction", "SourceTransactionError",
    "SourceTransactions", "bind_source_transactions", "open_source_transaction",
    "source_transactions_scope",
]
