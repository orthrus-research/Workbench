"""Core custody port for reviewed, shareable reproduction capsules."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Protocol


class CapsuleExportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CapsuleExportHost(Protocol):
    def publish(self, *, target: Path, data: bytes, capsule_id: str) -> None: ...
    def cataloged(self, *, target: Path) -> bool: ...
    def verify(
        self, *, target: Path, capsule_id: str, size: int, sha256: str,
    ) -> None: ...


_host: ContextVar[CapsuleExportHost | None] = ContextVar(
    "workbench_capsule_export_host", default=None,
)


@contextmanager
def capsule_export_scope(host: CapsuleExportHost) -> Iterator[None]:
    if any(not callable(getattr(host, name, None)) for name in ("publish", "cataloged", "verify")):
        raise CapsuleExportError("capsule.host", "Core capsule export host is incomplete")
    token = _host.set(host)
    try:
        yield
    finally:
        _host.reset(token)


def capsule_exports() -> CapsuleExportHost:
    host = _host.get()
    if host is None:
        raise CapsuleExportError("capsule.host", "no Core capsule export host is bound")
    return host


def capsule_exports_bound() -> bool:
    return _host.get() is not None


__all__ = [
    "CapsuleExportError", "CapsuleExportHost", "capsule_export_scope",
    "capsule_exports", "capsule_exports_bound",
]
