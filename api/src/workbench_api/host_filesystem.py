"""Filesystem security port. The host explicitly supplies its implementation."""

from pathlib import Path
from typing import ContextManager, Protocol


class HostFilesystemError(OSError):
    """The host filesystem operation is unavailable or unsafe."""


class DurableRecordError(HostFilesystemError):
    """Core refused a private record operation under its custody policy."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class HostFilesystem(Protocol):
    def private_path(self, path: Path, *, directory: bool) -> bool: ...
    def secure_private_path(self, path: Path, *, directory: bool) -> Path: ...
    def secure_private_endpoint(self, path: Path) -> None: ...
    def fsync_directory(self, path: Path) -> None: ...
    def read_private_bytes(self, path: Path, *, byte_limit: int) -> bytes: ...
    def read_bounded_bytes(self, path: Path, *, byte_limit: int) -> bytes: ...
    def publish_immutable_bytes(self, path: Path, data: bytes, *, byte_limit: int, idempotent: bool = False) -> None: ...
    def replace_private_bytes(
        self, path: Path, data: bytes, *, byte_limit: int,
        expected_sha256: str | None = None, require_absent: bool = False,
    ) -> None: ...
    def remove_private_bytes(
        self, path: Path, *, expected_sha256: str, byte_limit: int,
    ) -> None: ...
    def private_record_lock(self, path: Path, *, wait: bool = False) -> ContextManager[None]: ...
    def append_private_line(
        self, path: Path, line: bytes, *, expected_size: int, byte_limit: int,
        journal_byte_limit: int | None = None,
    ) -> int: ...
    def inspect_private_journal(self, path: Path, *, byte_limit: int) -> dict: ...


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


def read_private_bytes(path: Path, *, byte_limit: int) -> bytes:
    operation = getattr(_filesystem(), "read_private_bytes", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide private record reads")
    return operation(path, byte_limit=byte_limit)


def read_bounded_bytes(path: Path, *, byte_limit: int) -> bytes:
    operation = getattr(_filesystem(), "read_bounded_bytes", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide bounded input reads")
    return operation(path, byte_limit=byte_limit)


def publish_immutable_bytes(
    path: Path, data: bytes, *, byte_limit: int, idempotent: bool = False,
) -> None:
    operation = getattr(_filesystem(), "publish_immutable_bytes", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide immutable record publication")
    operation(path, data, byte_limit=byte_limit, idempotent=idempotent)


def replace_private_bytes(
    path: Path, data: bytes, *, byte_limit: int,
    expected_sha256: str | None = None, require_absent: bool = False,
) -> None:
    operation = getattr(_filesystem(), "replace_private_bytes", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide revisioned record replacement")
    operation(path, data, byte_limit=byte_limit,
              expected_sha256=expected_sha256, require_absent=require_absent)


def remove_private_bytes(
    path: Path, *, expected_sha256: str, byte_limit: int,
) -> None:
    """Remove one owner-private record only while its reviewed bytes still match."""

    operation = getattr(_filesystem(), "remove_private_bytes", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide private record removal")
    operation(path, expected_sha256=expected_sha256, byte_limit=byte_limit)


def private_record_lock(path: Path, *, wait: bool = False) -> ContextManager[None]:
    operation = getattr(_filesystem(), "private_record_lock", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide private record locks")
    return operation(path, wait=wait)


def append_private_line(
    path: Path, line: bytes, *, expected_size: int, byte_limit: int,
    journal_byte_limit: int | None = None,
) -> int:
    """Append one fsynced line after Core verifies the expected journal length.

    `byte_limit` bounds this line. `journal_byte_limit`, when supplied, bounds
    the entire file after append. The owner selects limits from its contract.
    An expected size of zero creates a fresh journal. A partial final line is
    retained for recovery inspection and cannot be extended by this port.
    """

    operation = getattr(_filesystem(), "append_private_line", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide private journals")
    return operation(
        path, line, expected_size=expected_size, byte_limit=byte_limit,
        journal_byte_limit=journal_byte_limit,
    )


def inspect_private_journal(path: Path, *, byte_limit: int) -> dict:
    """Inspect the complete prefix and any incomplete tail without modifying it."""

    operation = getattr(_filesystem(), "inspect_private_journal", None)
    if not callable(operation):
        raise HostFilesystemError("selected filesystem host does not provide private journals")
    return operation(path, byte_limit=byte_limit)
