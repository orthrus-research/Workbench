"""Core-bound custody for mutable domain record namespaces.

Domain owners choose records and transitions. Core chooses the physical
namespace, gives it a stable identity, and registers its retention boundary.
An absent binding keeps historical direct adapters readable outside dispatch.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol


@dataclass(frozen=True, slots=True)
class RecordStoreReference:
    store_id: str
    family: str
    owner_id: str
    workspace: Path
    root: Path
    retention: str


class RecordStores(Protocol):
    def open(self, family: str, base: Path) -> RecordStoreReference: ...


_bound: ContextVar[RecordStores | None] = ContextVar("workbench_record_stores", default=None)


@contextmanager
def record_store_scope(provider: RecordStores) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def open_record_store(family: str, base: Path) -> RecordStoreReference | None:
    """Open a Core-managed namespace during dispatch, or a legacy adapter."""

    provider = _bound.get()
    return None if provider is None else provider.open(family, base)


__all__ = ["RecordStoreReference", "RecordStores", "record_store_scope", "open_record_store"]
