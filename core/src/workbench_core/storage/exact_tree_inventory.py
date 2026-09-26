"""Bounded POSIX inventory for large, exact-mode managed trees.

This is an opt-in alternative to the portable V1 tree inventory. It keeps
member rows out of the catalog intent while binding every path, byte and mode
to a streaming digest. All traversal and file reads use no-follow descriptors.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable

from workbench_api.managed_trees import ManagedTreeError

from ..durable_files import _directory as pinned_directory


EXACT_INVENTORY_POLICY = "posix-exact-v1"
MAX_FILES = 100_000
MAX_DIRECTORIES = 100_000
MAX_FILE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 32 * 1024**3
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def _safe_member_path(value: str) -> None:
    """Admit literal POSIX member names without path escape or normalization."""
    path = PurePosixPath(value)
    if (
        not value or path.as_posix() != value or path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or "\0" in value
    ):
        raise ManagedTreeError("tree.members", "managed tree has an unsafe member path")


def _open_relative_directory(root_fd: int, relative: str) -> int:
    descriptor = os.dup(root_fd)
    try:
        if relative:
            for part in relative.split("/"):
                visible = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISDIR(visible.st_mode):
                    raise ManagedTreeError("tree.members", "managed tree traverses a redirect")
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                opened = os.fstat(child)
                if (not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)):
                    os.close(child)
                    raise ManagedTreeError("tree.changed", "managed tree directory changed during inventory")
                os.close(descriptor)
                descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _file_row(directory_fd: int, name: str, relative: str,
              cancelled: Callable[[], bool]) -> dict[str, object]:
    visible = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1:
        raise ManagedTreeError("tree.members", "managed tree requires independent ordinary files")
    if visible.st_size > MAX_FILE_BYTES:
        raise ManagedTreeError("tree.bounds", "managed tree file exceeds its byte bound")
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)):
            raise ManagedTreeError("tree.changed", "managed tree file changed during inventory")
        digest, size = sha256(), 0
        while True:
            if cancelled():
                raise ManagedTreeError("tree.cancelled", "managed tree inventory was cancelled")
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > before.st_size or size > MAX_FILE_BYTES:
                raise ManagedTreeError("tree.changed", "managed tree file grew during inventory")
            digest.update(chunk)
        after = os.fstat(descriptor)
        last_visible = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if (size != before.st_size
                or any(getattr(before, field) != getattr(after, field)
                       or getattr(before, field) != getattr(last_visible, field) for field in fields)):
            raise ManagedTreeError("tree.changed", "managed tree file changed during inventory")
        return {
            "path": relative, "kind": "file", "size": size,
            "sha256": digest.hexdigest(), "mode": stat.S_IMODE(before.st_mode),
            "classification": "authoritative",
        }
    finally:
        os.close(descriptor)


def inventory_exact_members(
    root: Path, *, cancelled: Callable[[], bool] = lambda: False,
) -> tuple[list[dict[str, object]], int, int, int]:
    """Return exact rows, root mode, file count and directory count.

    The input may contain 100,000 files and 100,000 directories. The total
    payload is bounded to 32 GiB, but catalog records stay below 4 MiB because
    this inventory is represented by its digest and counts in the intent.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.scandir not in os.supports_fd:
        raise ManagedTreeError("tree.policy", "exact POSIX tree inventory is unavailable on this host")
    try:
        root_fd = pinned_directory(root, create=False)
    except (OSError, ValueError) as exc:
        raise ManagedTreeError("tree.changed", "managed tree root cannot be pinned") from exc
    try:
        root_stat = os.fstat(root_fd)
        pending: list[tuple[str, int, int, int]] = [
            ("", root_stat.st_dev, root_stat.st_ino, root_stat.st_mode),
        ]
        rows: list[dict[str, object]] = []
        files = directories = total = 0
        while pending:
            if cancelled():
                raise ManagedTreeError("tree.cancelled", "managed tree inventory was cancelled")
            relative_directory, expected_dev, expected_ino, expected_mode = pending.pop()
            directory_fd = _open_relative_directory(root_fd, relative_directory)
            try:
                before = os.fstat(directory_fd)
                if (before.st_dev, before.st_ino, before.st_mode) != (
                    expected_dev, expected_ino, expected_mode,
                ):
                    raise ManagedTreeError("tree.changed", "managed tree directory changed during inventory")
                with os.scandir(directory_fd) as scanned:
                    entries = sorted(scanned, key=lambda entry: entry.name)
                children: list[tuple[str, int, int, int]] = []
                for entry in entries:
                    if cancelled():
                        raise ManagedTreeError("tree.cancelled", "managed tree inventory was cancelled")
                    relative = f"{relative_directory}/{entry.name}" if relative_directory else entry.name
                    _safe_member_path(relative)
                    visible = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(visible.st_mode):
                        directories += 1
                        if directories > MAX_DIRECTORIES:
                            raise ManagedTreeError("tree.bounds", "managed tree exceeds its directory bound")
                        rows.append({"path": relative, "kind": "directory",
                                     "mode": stat.S_IMODE(visible.st_mode),
                                     "classification": "authoritative"})
                        children.append((relative, visible.st_dev, visible.st_ino, visible.st_mode))
                    elif stat.S_ISREG(visible.st_mode):
                        files += 1
                        if files > MAX_FILES:
                            raise ManagedTreeError("tree.bounds", "managed tree exceeds its file bound")
                        row = _file_row(directory_fd, entry.name, relative, cancelled)
                        total += int(row["size"])
                        if total > MAX_TOTAL_BYTES:
                            raise ManagedTreeError("tree.bounds", "managed tree exceeds its byte bound")
                        rows.append(row)
                    else:
                        raise ManagedTreeError("tree.members", "managed tree contains a link or special file")
                after = os.fstat(directory_fd)
                if ((before.st_dev, before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns)):
                    raise ManagedTreeError("tree.changed", "managed tree directory changed during inventory")
                pending.extend(reversed(children))
            finally:
                os.close(directory_fd)
        root_after = os.fstat(root_fd)
        if ((root_stat.st_dev, root_stat.st_ino, root_stat.st_mode,
             root_stat.st_mtime_ns, root_stat.st_ctime_ns)
                != (root_after.st_dev, root_after.st_ino, root_after.st_mode,
                    root_after.st_mtime_ns, root_after.st_ctime_ns)):
            raise ManagedTreeError("tree.changed", "managed tree root changed during inventory")
        rows.sort(key=lambda row: str(row["path"]))
        return rows, stat.S_IMODE(root_stat.st_mode), files, directories
    except OSError as exc:
        raise ManagedTreeError("tree.changed", "managed tree members cannot be inventoried") from exc
    finally:
        os.close(root_fd)


def exact_content_sha256(members: list[dict[str, object]]) -> str:
    """Hash the canonical member array without building one large JSON value."""
    digest = sha256(b"[")
    for index, row in enumerate(members):
        if index:
            digest.update(b",")
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8"))
    digest.update(b"]")
    return digest.hexdigest()


__all__ = [
    "EXACT_INVENTORY_POLICY", "MAX_FILES", "MAX_DIRECTORIES",
    "inventory_exact_members", "exact_content_sha256",
]
