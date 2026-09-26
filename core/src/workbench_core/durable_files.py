"""Pinned create-new file publication for Core durable resources.

The parent descriptor remains open from staging through publication. No module
selects a temporary name, performs a rename, or removes the published file.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat

from workbench_api.durable_resources import DurableResourceError


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
_PINNED_PUBLICATION_AVAILABLE = (
    os.name == "posix"
    and hasattr(os, "O_NOFOLLOW")
    and {os.open, os.stat, os.link, os.mkdir, os.unlink}.issubset(os.supports_dir_fd)
)


def _require_posix() -> None:
    if not _PINNED_PUBLICATION_AVAILABLE:
        raise DurableResourceError("output.filesystem", "pinned create-new publication is unavailable on this host")


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _directory(path: Path, *, create: bool) -> int:
    _require_posix()
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise DurableResourceError("output.write", "managed directory must be an absolute ordinary path")
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                visible = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                visible = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(visible.st_mode):
                raise DurableResourceError("output.changed", "managed directory traverses a non-directory or link")
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            opened = os.fstat(child)
            if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != _identity(visible):
                os.close(child)
                raise DurableResourceError("output.changed", "managed directory changed while opening")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _visible_parent(path: Path, expected: tuple[int, int]) -> None:
    try:
        descriptor = _directory(path, create=False)
    except (OSError, DurableResourceError) as exc:
        raise DurableResourceError("output.changed", "managed output parent became unavailable") from exc
    try:
        if _identity(os.fstat(descriptor)) != expected:
            raise DurableResourceError("output.changed", "managed output parent changed identity")
    finally:
        os.close(descriptor)


class StagedFile:
    """A prepared file; the caller writes an intent before calling publish."""

    def __init__(self, target: Path, data: bytes, nonce: str):
        if type(data) is not bytes:
            raise DurableResourceError("output.write", "managed output must contain bytes")
        self.target = target
        self.temporary = f".workbench-resource-{nonce}.pending"
        self.parent = _directory(target.parent, create=True)
        self.parent_identity = _identity(os.fstat(self.parent))
        self.descriptor = -1
        self.published = False
        self.size = len(data)
        self.digest = sha256(data).hexdigest()
        try:
            if target.name in {"", ".", ".."}:
                raise DurableResourceError("output.write", "managed output has no filename")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            self.descriptor = os.open(self.temporary, flags, 0o600, dir_fd=self.parent)
            self.file_identity = _identity(os.fstat(self.descriptor))
            remaining = memoryview(data)
            while remaining:
                count = os.write(self.descriptor, remaining)
                if count <= 0:
                    raise DurableResourceError("output.write", "managed output write made no progress")
                remaining = remaining[count:]
            os.fsync(self.descriptor)
            self._verify_temporary()
            _visible_parent(target.parent, self.parent_identity)
        except BaseException:
            self.close(cleanup=True)
            raise

    def _verify_temporary(self) -> None:
        visible = os.stat(self.temporary, dir_fd=self.parent, follow_symlinks=False)
        if not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1 or _identity(visible) != self.file_identity:
            raise DurableResourceError("output.changed", "staged output changed identity")

    def publish(self) -> None:
        self._verify_temporary()
        _visible_parent(self.target.parent, self.parent_identity)
        try:
            os.link(
                self.temporary, self.target.name,
                src_dir_fd=self.parent, dst_dir_fd=self.parent,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise DurableResourceError("output.exists", "managed output path already exists") from exc
        self.published = True
        os.fsync(self.parent)
        _visible_parent(self.target.parent, self.parent_identity)
        retained = os.stat(self.target.name, dir_fd=self.parent, follow_symlinks=False)
        if not stat.S_ISREG(retained.st_mode) or retained.st_nlink != 2 or _identity(retained) != self.file_identity:
            raise DurableResourceError("output.changed", "published output changed identity")

    def close(self, *, cleanup: bool) -> None:
        if self.parent < 0:
            return
        try:
            if self.descriptor >= 0:
                os.close(self.descriptor)
                self.descriptor = -1
            if cleanup:
                try:
                    observed = os.stat(self.temporary, dir_fd=self.parent, follow_symlinks=False)
                    if hasattr(self, "file_identity") and _identity(observed) == self.file_identity:
                        os.unlink(self.temporary, dir_fd=self.parent)
                        os.fsync(self.parent)
                except FileNotFoundError:
                    pass
        finally:
            os.close(self.parent)
            self.parent = -1


def read_verified(path: Path, *, expected_size: int, expected_sha256: str, allow_linked: bool = False) -> bytes:
    """Read exact bytes through a pinned ordinary-file descriptor."""

    parent = _directory(path.parent, create=False)
    try:
        parent_identity = _identity(os.fstat(parent))
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != (2 if allow_linked else 1):
            raise DurableResourceError("resource.changed", "retained resource is not an independent file")
        descriptor = os.open(path.name, _FILE_FLAGS, dir_fd=parent)
        try:
            opened = os.fstat(descriptor)
            if _identity(opened) != _identity(before) or opened.st_size != expected_size:
                raise DurableResourceError("resource.changed", "retained resource changed before read")
            chunks = []
            remaining = expected_size + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _visible_parent(path.parent, parent_identity)
        if (
            len(data) != expected_size
            or sha256(data).hexdigest() != expected_sha256
            or _identity(before) != _identity(after)
            or _identity(after) != _identity(final)
            or after.st_size != expected_size
            or final.st_size != expected_size
            or final.st_nlink != (2 if allow_linked else 1)
        ):
            raise DurableResourceError("resource.changed", "retained resource changed during read")
        return data
    finally:
        os.close(parent)


def unlink_prepared(target: Path, temporary: str, identity: tuple[int, int]) -> None:
    """Remove only the prepared sibling that still names the published inode."""

    parent = _directory(target.parent, create=False)
    try:
        parent_identity = _identity(os.fstat(parent))
        prepared = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
        published = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(prepared.st_mode)
            or not stat.S_ISREG(published.st_mode)
            or _identity(prepared) != identity
            or _identity(published) != identity
        ):
            raise DurableResourceError("resource.changed", "prepared output no longer names the published file")
        _visible_parent(target.parent, parent_identity)
        os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


__all__ = ["StagedFile", "read_verified", "unlink_prepared"]
