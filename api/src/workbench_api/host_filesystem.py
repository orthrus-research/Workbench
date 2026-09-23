"""Filesystem security port. The host explicitly supplies its implementation."""

from pathlib import Path
from typing import ContextManager, Protocol


class HostFilesystemError(OSError):
    """The host filesystem operation is unavailable or unsafe."""


class HostFilesystem(Protocol):
    def private_path(self, path: Path, *, directory: bool) -> bool: ...
    def secure_private_path(self, path: Path, *, directory: bool) -> Path: ...
    def secure_private_endpoint(self, path: Path) -> None: ...
    def fsync_directory(self, path: Path) -> None: ...


_host: HostFilesystem | None = None


def bind_host_filesystem(host: HostFilesystem) -> None:
    """Bind one process-wide host before starting worker threads.

    Modules consume this port but cannot implicitly import or discover Core.
    Binding is idempotent for the same host; replacing a live host is rejected.
    """
    global _host
    if not all(callable(getattr(host, name, None)) for name in (
        "private_path", "secure_private_path", "secure_private_endpoint", "fsync_directory",
    )):
        raise HostFilesystemError("host filesystem does not implement the required port")
    if _host is not None and _host is not host:
        raise HostFilesystemError("a different filesystem host is already bound")
    _host = host


def _filesystem() -> HostFilesystem:
    if _host is None:
        raise HostFilesystemError("no filesystem host is bound; start through Workbench Core")
    return _host


def private_path(path: Path, *, directory: bool) -> bool:
    return _filesystem().private_path(path, directory=directory)


def secure_private_path(path: Path, *, directory: bool) -> Path:
    return _filesystem().secure_private_path(path, directory=directory)


def secure_private_endpoint(path: Path) -> None:
    _filesystem().secure_private_endpoint(path)


def fsync_directory(path: Path) -> None:
    _filesystem().fsync_directory(path)


def file_lease(descriptor: int, *, exclusive: bool) -> ContextManager[None]:
    """Request the host's optional nonblocking shared/exclusive file lease."""
    operation = getattr(_filesystem(), "file_lease", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide file leases; update the host")
    return operation(descriptor, exclusive=exclusive)
