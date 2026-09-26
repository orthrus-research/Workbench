"""Pin and verify one Core-retained wheel throughout ZIP inspection."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import stat
from typing import Any, Iterator, Mapping
from zipfile import ZipFile

from workbench_api.durable_resources import DurableResourceError

from .durable_files import _directory as pinned_directory
from .environment_reconstruction import ReconstructionError
from .storage.exact_tree_inventory import MAX_FILE_BYTES


_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")


def _digest(descriptor: int, size: int) -> str:
    result = sha256()
    position = 0
    while position < size:
        block = os.pread(descriptor, min(1024 * 1024, size - position), position)
        if not block:
            raise ReconstructionError("retained wheel changed during source verification")
        result.update(block)
        position += len(block)
    return result.hexdigest()


@contextmanager
def opened_retained_wheel(path: Path, row: Mapping[str, Any]) -> Iterator[ZipFile]:
    """Hold the reviewed wheel inode and parent through ZIP use and readback."""

    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "pread") or path.name != row.get("filename")
            or type(row.get("size")) is not int
            or not 0 < row["size"] <= MAX_FILE_BYTES
            or type(row.get("sha256")) is not str
            or len(row["sha256"]) != 64):
        raise ReconstructionError("retained wheel has no supported exact source identity")
    parent = descriptor = -1
    try:
        parent = pinned_directory(path.parent, create=False)
        parent_before = os.fstat(parent)
        visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                or visible.st_size != row["size"]):
            raise ReconstructionError("retained wheel is not the reviewed ordinary file")
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent,
        )
        before = os.fstat(descriptor)
        if any(getattr(before, field) != getattr(visible, field) for field in _FIELDS):
            raise ReconstructionError("retained wheel changed before source verification")
        if _digest(descriptor, before.st_size) != row["sha256"]:
            raise ReconstructionError("retained wheel bytes differ from the reviewed closure")
        with os.fdopen(os.dup(descriptor), "rb") as source, ZipFile(source) as archive:
            yield archive
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        reopened_parent = pinned_directory(path.parent, create=False)
        try:
            if ((os.fstat(reopened_parent).st_dev, os.fstat(reopened_parent).st_ino)
                    != (parent_before.st_dev, parent_before.st_ino)
                    or any(getattr(before, field) != getattr(after, field)
                           or getattr(after, field) != getattr(final, field)
                           for field in _FIELDS)
                    or _digest(descriptor, before.st_size) != row["sha256"]):
                raise ReconstructionError("retained wheel or source path changed during ZIP inspection")
        finally:
            os.close(reopened_parent)
    except (OSError, DurableResourceError) as exc:
        raise ReconstructionError(f"retained wheel cannot be pinned for ZIP inspection: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


__all__ = ["opened_retained_wheel"]
