"""Core custody for small revisioned preference files.

The caller owns the schema and policy. Core owns the private directory,
cross-process update lock, bounded read, compared replacement and flush.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import Callable

from workbench_api.host_filesystem import DurableRecordError, HostFilesystemError

from .durable_records import read_bounded_bytes, replace_private_bytes
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .setup_cli import _state_root, setup_record_lock


class PreferenceRecordError(ValueError):
    """A preference file cannot be read or updated safely."""


def _privatize_legacy_record(path: Path, previous: bytes) -> None:
    """Upgrade a single-link historical preference before private CAS.

    Older owner writers could create an ordinary 0644 record. The Core writer
    keeps its bytes and locator, but must make the file private before asking
    the private compare-and-replace port to update it.
    """

    if private_path(path, directory=False):
        return
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        visible = path.lstat()
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1:
            raise PreferenceRecordError("legacy preference is not a single regular file")
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                visible.st_dev, visible.st_ino, visible.st_size, visible.st_mtime_ns
            ):
                raise PreferenceRecordError("legacy preference changed before privacy upgrade")
            if os.name == "nt":  # Native Windows uses an owner-private DACL.
                secure_private_path(path, directory=False)
            else:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            after = path.lstat()
            if (
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                or not private_path(path, directory=False)
                or read_bounded_bytes(path, byte_limit=len(previous)) != previous
            ):
                raise PreferenceRecordError("legacy preference changed during privacy upgrade")
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise PreferenceRecordError(f"cannot secure legacy preference: {exc}") from exc


def update_preference_bytes(
    path: Path,
    transform: Callable[[bytes | None], bytes],
    *,
    byte_limit: int,
) -> bytes:
    """Compare and replace one private preference under its existing lock.

    A caller may interpret a missing file as its domain default. Legacy files
    remain readable, while subsequent writes require Core's private custody.
    """

    if not isinstance(path, Path) or not path.is_absolute():
        raise PreferenceRecordError("preference path must be absolute")
    if type(byte_limit) is not int or byte_limit <= 0:
        raise PreferenceRecordError("preference byte limit is invalid")
    try:
        _state_root(path.parent)
        missing: list[Path] = []
        cursor = path.parent
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        _state_root(path.parent)
        secure_private_path(path.parent, directory=True)
        for directory in reversed(missing):
            fsync_directory(directory.parent)
        with setup_record_lock(path):
            try:
                previous = read_bounded_bytes(path, byte_limit=byte_limit)
            except DurableRecordError as exc:
                if exc.code != "unavailable" or path.exists() or path.is_symlink():
                    raise
                previous = None
            proposed = transform(previous)
            if type(proposed) is not bytes or not 0 < len(proposed) <= byte_limit:
                raise PreferenceRecordError("preference payload is outside its byte limit")
            if previous is not None:
                _privatize_legacy_record(path, previous)
            replace_private_bytes(
                path, proposed, byte_limit=byte_limit,
                require_absent=previous is None,
                expected_sha256=(
                    "sha256:" + sha256(previous).hexdigest()
                    if previous is not None else None
                ),
            )
            return proposed
    except (DurableRecordError, HostFilesystemError) as exc:
        raise PreferenceRecordError(f"cannot update preference: {exc}") from exc


__all__ = ["PreferenceRecordError", "update_preference_bytes"]
