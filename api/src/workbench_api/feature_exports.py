"""Core custody for Feature Studio's reviewed two-file export bundle."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Protocol


class FeatureExportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FeatureExportHost(Protocol):
    def cataloged(self, *, workspace: Path, target: Path) -> bool: ...

    def publish(
        self, *, workspace: Path, target: Path, patch: bytes,
        receipt: bytes, receipt_id: str,
    ) -> str: ...

    def verify(
        self, *, workspace: Path, target: Path, tree_id: str,
        patch_sha256: str, patch_size: int,
        receipt_sha256: str, receipt_size: int, receipt_id: str,
    ) -> None: ...


_host: FeatureExportHost | None = None
_scoped: ContextVar[FeatureExportHost | None] = ContextVar(
    "workbench_feature_export_host", default=None,
)


def bind_feature_export_host(host: FeatureExportHost) -> None:
    global _host
    if any(not callable(getattr(host, name, None)) for name in ("cataloged", "publish", "verify")):
        raise FeatureExportError("feature-export.host", "Core feature export host is incomplete")
    if _host is not None and _host is not host:
        raise FeatureExportError("feature-export.host", "a different Core feature export host is already bound")
    _host = host


@contextmanager
def feature_export_scope(host: FeatureExportHost) -> Iterator[None]:
    if any(not callable(getattr(host, name, None)) for name in ("cataloged", "publish", "verify")):
        raise FeatureExportError("feature-export.host", "Core feature export host is incomplete")
    token = _scoped.set(host)
    try:
        yield
    finally:
        _scoped.reset(token)


def feature_exports() -> FeatureExportHost:
    host = _scoped.get() or _host
    if host is None:
        raise FeatureExportError("feature-export.host", "no Core feature export host is bound")
    return host


def feature_exports_bound() -> bool:
    return (_scoped.get() or _host) is not None


__all__ = [
    "FeatureExportError", "FeatureExportHost", "bind_feature_export_host",
    "feature_export_scope", "feature_exports", "feature_exports_bound",
]
