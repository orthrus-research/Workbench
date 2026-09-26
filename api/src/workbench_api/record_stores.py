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
from typing import ContextManager, Iterator, Protocol


@dataclass(frozen=True, slots=True)
class RecordStoreReference:
    store_id: str
    family: str
    owner_id: str
    workspace: Path
    root: Path
    retention: str


class SessionOwnerAllocationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SessionOwnerReference:
    allocation_id: str
    store_id: str
    workspace: Path
    owner_id: str
    session_id: str
    path: Path
    state_root: Path


class SessionOwnerAllocation(Protocol):
    @property
    def reference(self) -> SessionOwnerReference: ...

    def verify(self) -> SessionOwnerReference: ...

    def record_started(self, expected_start_result: bytes) -> SessionOwnerReference: ...

    def verify_started(self) -> SessionOwnerReference: ...


class RecordStores(Protocol):
    def open(self, family: str, base: Path) -> RecordStoreReference: ...

    def open_target(
        self, family: str, state_root: Path, target_workspace: Path,
    ) -> RecordStoreReference: ...

    def session_owner(
        self, family: str, base: Path, session_id: str, *, create: bool,
    ) -> ContextManager[SessionOwnerAllocation]: ...

    def publish_review_artifact(
        self, family: str, target: Path, data: bytes, *, byte_limit: int,
    ) -> RecordStoreReference: ...


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


def open_target_record_store(
    family: str, state_root: Path, target_workspace: Path,
) -> RecordStoreReference | None:
    """Register an owner-admitted target even when dispatch selected another workspace.

    The domain owner must validate the target and binding identity first. Core
    validates the physical target/state relationship and owns the namespace.
    """

    provider = _bound.get()
    return None if provider is None else provider.open_target(
        family, state_root, target_workspace,
    )


def record_store_host_bound() -> bool:
    """Tell a direct entry point whether dispatch already selected custody."""

    return _bound.get() is not None


def session_owner_scope(
    family: str, base: Path, session_id: str, *, create: bool,
) -> ContextManager[SessionOwnerAllocation]:
    """Hold Core's fixed session-owner child while the domain performs work."""

    provider = _bound.get()
    if provider is None:
        raise SessionOwnerAllocationError(
            "owner.host", "session owner allocation requires Workbench Core",
        )
    return provider.session_owner(family, base, session_id, create=create)


def publish_review_artifact(
    family: str, target: Path, data: bytes, *, byte_limit: int,
) -> RecordStoreReference:
    """Ask Core to publish one fresh owner-encoded review artifact.

    The owner chooses the exact bytes and requested file. Core admits its
    physical parent, registers the protected store, and performs publication.
    """

    from .durable_resources import DurableResourceError

    provider = _bound.get()
    operation = getattr(provider, "publish_review_artifact", None)
    if not callable(operation):
        raise DurableResourceError(
            "resource.host", "review artifact publication requires Workbench Core",
        )
    return operation(family, target, data, byte_limit=byte_limit)


__all__ = [
    "RecordStoreReference", "RecordStores", "record_store_scope",
    "open_record_store", "open_target_record_store", "record_store_host_bound",
    "SessionOwnerAllocation", "SessionOwnerAllocationError", "SessionOwnerReference",
    "session_owner_scope",
    "publish_review_artifact",
]
