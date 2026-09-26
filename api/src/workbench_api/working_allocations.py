"""Core-bound custody for long-running, mutable working allocations.

The path is stable while native tools write and while reports refer to it.
Core retains the whole allocation; owners select the small evidence set whose
bytes need an exact terminal inventory and validate domain completion.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Protocol


class WorkingAllocationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WorkingAllocationReference:
    allocation_id: str
    family: str
    label: str
    path: Path
    workspace: Path
    owner_id: str


@dataclass(frozen=True, slots=True)
class WorkingAllocationDescription:
    reference: WorkingAllocationReference
    status: str  # reserved, incomplete, complete, failed, missing, or changed
    retention: str
    evidence: tuple[dict[str, object], ...]
    absolute_references: tuple[dict[str, object], ...]
    failure: str | None


class WorkingAllocations(Protocol):
    def allocate(
        self, family: str, label: str, *, requested_path: Path | None = None,
    ) -> WorkingAllocationReference: ...

    def open(self, allocation_id: str) -> WorkingAllocationReference: ...

    def describe(self, allocation_id: str) -> WorkingAllocationDescription: ...

    def verify(self, allocation_id: str) -> WorkingAllocationDescription: ...

    def inventory(self) -> tuple[WorkingAllocationDescription, ...]: ...

    def execution(self, allocation: WorkingAllocationReference) -> ContextManager[None]: ...

    def active(self, allocation: WorkingAllocationReference) -> bool: ...

    def request_cancel(self, allocation: WorkingAllocationReference, binding: str) -> None: ...

    def cancellation_requested(self, allocation: WorkingAllocationReference) -> bool: ...

    def check_cancelled(self, allocation: WorkingAllocationReference) -> None: ...

    def finish(
        self, allocation: WorkingAllocationReference, *, outcome: str,
        evidence: tuple[Path, ...] = (), absolute_references: tuple[Path, ...] = (),
        validate: Callable[[Path], object] | None = None,
        failure: str | None = None,
    ) -> WorkingAllocationDescription: ...


_bound: ContextVar[WorkingAllocations | None] = ContextVar(
    "workbench_working_allocations", default=None,
)


@contextmanager
def working_allocations_scope(provider: WorkingAllocations) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def working_allocations() -> WorkingAllocations:
    provider = _bound.get()
    if provider is None:
        raise WorkingAllocationError(
            "working.host", "no managed working-allocation host is bound; invoke through Workbench Core",
        )
    return provider


__all__ = [
    "WorkingAllocationDescription", "WorkingAllocationError", "WorkingAllocationReference",
    "WorkingAllocations", "working_allocations", "working_allocations_scope",
]
