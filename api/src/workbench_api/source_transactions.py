"""Core-bound physical transactions for explicitly admitted source edits.

The domain owner validates consent, plans, source identity and resulting
semantics. Core alone stages and mutates the protected source tree.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol


class SourceTransactionError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceImage:
    kind: str  # "file" or "symlink"
    data: bytes
    executable: bool = False


@dataclass(frozen=True, slots=True)
class SourceStage:
    token: str


class SourceTransaction(Protocol):
    def prepare(
        self, relative: str, *, before: SourceImage | None,
        after: SourceImage | None, create_parents: bool = False,
    ) -> SourceStage: ...
    def commit(self, stage: SourceStage) -> None: ...
    def rollback(self, stage: SourceStage) -> None: ...
    def rollback_all(self) -> None: ...
    def cleanup(self, *, remove_created_directories: bool = False) -> None: ...


class SourceTransactions(Protocol):
    def open(self, root: Path, *, binding: str) -> SourceTransaction: ...


_bound: ContextVar[SourceTransactions | None] = ContextVar(
    "workbench_source_transactions", default=None,
)


@contextmanager
def source_transactions_scope(provider: SourceTransactions) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def open_source_transaction(root: Path, *, binding: str) -> SourceTransaction:
    provider = _bound.get()
    if provider is None:
        raise SourceTransactionError(
            "unavailable", "protected source edits require Workbench Core",
        )
    return provider.open(root, binding=binding)


__all__ = [
    "SourceImage", "SourceStage", "SourceTransaction", "SourceTransactionError",
    "SourceTransactions", "open_source_transaction", "source_transactions_scope",
]
