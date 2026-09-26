"""Read-only, exact archive-to-tree verification for legacy IDE toolchains.

The historical extraction paths are retained. This reader pins directories and
ordinary files while it compares their contents, modes, and relative links with
the exact locked archive. Its result is an observation at read time, not a
durable tree admission or a grant to remove or repair the destination.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import posixpath
import re
import stat
import tarfile
import zipfile

from .durable_files import _directory


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
_MARKER = ".workbench-provisioned-sha256"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MEMBERS = 20000
_MAX_FILE = 1024 * 1024 * 1024
_MAX_TOTAL = 2 * 1024 * 1024 * 1024


class IdeToolchainReadError(ValueError):
    pass


@dataclass(frozen=True)
class _Member:
    kind: str
    mode: int | None
    size: int = 0
    digest: str = ""
    link: str = ""


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _unchanged(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        _identity(before) == _identity(after)
        and before.st_mode == after.st_mode
        and before.st_size == after.st_size
        and before.st_nlink == after.st_nlink
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )


def _parts(name: str, expected_root: str) -> tuple[str, ...]:
    if (
        not name or "\0" in name or "\\" in name or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
    ):
        raise IdeToolchainReadError("archive has an unsafe member name")
    parts = tuple(name.rstrip("/").split("/"))
    if parts[0] != expected_root:
        raise IdeToolchainReadError("archive member escapes its locked extraction root")
    if len(parts) > 1 and parts[1] == _MARKER:
        raise IdeToolchainReadError("archive member occupies Core's historical marker name")
    return parts[1:]


def _link_target(parts: tuple[str, ...], target: str) -> str:
    if not target or "\0" in target or "\\" in target or target.startswith("/"):
        raise IdeToolchainReadError("archive has an unsafe link target")
    resolved = posixpath.normpath(posixpath.join(*parts[:-1], target)) if len(parts) > 1 else posixpath.normpath(target)
    if resolved in {"", ".", ".."} or resolved.startswith("../"):
        raise IdeToolchainReadError("archive link escapes its extraction root")
    return target


def _add(expected: dict[tuple[str, ...], _Member], parts: tuple[str, ...], member: _Member) -> None:
    for depth in range(1, len(parts)):
        parent = parts[:depth]
        prior = expected.get(parent)
        if prior is None:
            expected[parent] = _Member("directory", None)
        elif prior.kind != "directory":
            raise IdeToolchainReadError("archive member traverses a non-directory")
    prior = expected.get(parts)
    if prior is not None and (prior.kind != "directory" or member.kind != "directory" or prior.mode is not None):
        raise IdeToolchainReadError("archive contains duplicate or conflicting members")
    expected[parts] = member


def _hash_stream(stream, size: int) -> str:
    if size < 0 or size > _MAX_FILE:
        raise IdeToolchainReadError("archive member exceeds the read bound")
    digest = sha256()
    remaining = size
    while remaining:
        block = stream.read(min(1024 * 1024, remaining))
        if not block:
            raise IdeToolchainReadError("archive member ended early")
        digest.update(block)
        remaining -= len(block)
    if stream.read(1):
        raise IdeToolchainReadError("archive member exceeds its declared size")
    return digest.hexdigest()


def _tar_data_file_mode(mode: int) -> int:
    """Match the regular-file mode emitted by extractall(filter='data')."""

    selected = mode & 0o755
    if not selected & 0o100:
        selected &= ~0o111
    return selected | 0o600


def _archive_inventory(stream, *, expected_root: str, archive_format: str) -> dict[tuple[str, ...], _Member]:
    expected: dict[tuple[str, ...], _Member] = {}
    total = 0
    if archive_format == "zip":
        with zipfile.ZipFile(stream) as bundle:
            infos = bundle.infolist()
            if len(infos) > _MAX_MEMBERS:
                raise IdeToolchainReadError("archive has too many members")
            for info in infos:
                parts = _parts(info.filename, expected_root)
                mode = (info.external_attr >> 16) & 0o777
                if info.is_dir():
                    member = _Member("directory", mode or None)
                elif stat.S_ISLNK(info.external_attr >> 16):
                    raise IdeToolchainReadError("ZIP links are not supported by the historical extractor")
                else:
                    total += info.file_size
                    if total > _MAX_TOTAL:
                        raise IdeToolchainReadError("archive content exceeds the read bound")
                    with bundle.open(info) as content:
                        digest = _hash_stream(content, info.file_size)
                    member = _Member("file", mode or None, info.file_size, digest)
                _add(expected, parts, member)
    elif archive_format == "tar":
        with tarfile.open(fileobj=stream, mode="r:*") as bundle:
            infos = bundle.getmembers()
            if len(infos) > _MAX_MEMBERS:
                raise IdeToolchainReadError("archive has too many members")
            for info in infos:
                parts = _parts(info.name, expected_root)
                if info.isdir():
                    # Python's data filter ignores TAR directory modes.
                    member = _Member("directory", None)
                elif info.issym():
                    member = _Member("link", None, link=_link_target(parts, info.linkname))
                elif info.isfile():
                    total += info.size
                    if total > _MAX_TOTAL:
                        raise IdeToolchainReadError("archive content exceeds the read bound")
                    content = bundle.extractfile(info)
                    if content is None:
                        raise IdeToolchainReadError("archive member cannot be read")
                    with content:
                        digest = _hash_stream(content, info.size)
                    member = _Member("file", _tar_data_file_mode(info.mode), info.size, digest)
                else:
                    raise IdeToolchainReadError("archive contains an unsupported member type")
                _add(expected, parts, member)
    else:
        raise IdeToolchainReadError("unknown locked archive format")
    if not expected:
        raise IdeToolchainReadError("archive has no locked extraction members")
    if () in expected and expected[()].kind != "directory":
        raise IdeToolchainReadError("archive extraction root is not a directory")
    if () not in expected:
        # npm's archive has an implicit package/ root. All other archives
        # carry an explicit root. The destination itself supplies this inode.
        expected[()] = _Member("directory", None)
    for parts, member in expected.items():
        if member.kind == "link":
            resolved = posixpath.normpath(posixpath.join(*parts[:-1], member.link)) if len(parts) > 1 else posixpath.normpath(member.link)
            if tuple(resolved.split("/")) not in expected:
                raise IdeToolchainReadError("archive link target is absent")
    return expected


def _verify_tree(directory_fd: int, parts: tuple[str, ...], expected: dict[tuple[str, ...], _Member], *, archive_sha256: str) -> int:
    before = os.fstat(directory_fd)
    if not stat.S_ISDIR(before.st_mode) or before.st_mode & 0o022 or before.st_uid != os.geteuid():
        raise IdeToolchainReadError("toolchain directory is not owner controlled")
    member = expected[parts]
    if member.mode is not None and stat.S_IMODE(before.st_mode) != member.mode:
        raise IdeToolchainReadError("toolchain directory mode differs from archive")
    children = {path[len(parts)] for path in expected if len(path) == len(parts) + 1 and path[:len(parts)] == parts}
    if not parts:
        children.add(_MARKER)
    actual = set(os.listdir(directory_fd))
    if actual != children:
        raise IdeToolchainReadError(f"toolchain tree has missing or extra members at {'/'.join(parts) or '.'}")
    files = 0
    for name in sorted(children):
        visible = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if name == _MARKER and not parts:
            if not stat.S_ISREG(visible.st_mode) or visible.st_mode & 0o022 or visible.st_uid != os.geteuid() or visible.st_size != 65:
                raise IdeToolchainReadError("historical lock marker is not an ordinary exact file")
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
            try:
                if _identity(os.fstat(descriptor)) != _identity(visible) or os.read(descriptor, 66) != (archive_sha256 + "\n").encode("ascii"):
                    raise IdeToolchainReadError("historical lock marker differs from archive")
                if not _unchanged(visible, os.stat(name, dir_fd=directory_fd, follow_symlinks=False)):
                    raise IdeToolchainReadError("historical lock marker changed during read")
            finally:
                os.close(descriptor)
            continue
        child = parts + (name,)
        item = expected[child]
        if visible.st_uid != os.geteuid() or (item.kind != "link" and visible.st_mode & 0o022):
            raise IdeToolchainReadError("toolchain member is not owner controlled")
        if item.kind == "directory":
            if not stat.S_ISDIR(visible.st_mode):
                raise IdeToolchainReadError("toolchain directory type differs from archive")
            child_fd = os.open(name, _DIR_FLAGS, dir_fd=directory_fd)
            try:
                if _identity(os.fstat(child_fd)) != _identity(visible):
                    raise IdeToolchainReadError("toolchain directory changed before read")
                files += _verify_tree(child_fd, child, expected, archive_sha256=archive_sha256)
            finally:
                os.close(child_fd)
        elif item.kind == "link":
            if not stat.S_ISLNK(visible.st_mode) or os.readlink(name, dir_fd=directory_fd) != item.link:
                raise IdeToolchainReadError("toolchain link differs from archive")
        elif item.kind == "file":
            if not stat.S_ISREG(visible.st_mode) or visible.st_size != item.size:
                raise IdeToolchainReadError("toolchain file differs from archive")
            if item.mode is not None and stat.S_IMODE(visible.st_mode) != item.mode:
                raise IdeToolchainReadError(f"toolchain file mode differs from archive at {'/'.join(child)}")
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
            try:
                opened = os.fstat(descriptor)
                if _identity(opened) != _identity(visible) or opened.st_size != item.size:
                    raise IdeToolchainReadError("toolchain file changed before read")
                with os.fdopen(os.dup(descriptor), "rb") as content:
                    if _hash_stream(content, item.size) != item.digest:
                        raise IdeToolchainReadError("toolchain file bytes differ from archive")
                after = os.fstat(descriptor)
                if not _unchanged(opened, after):
                    raise IdeToolchainReadError("toolchain file changed during read")
            finally:
                os.close(descriptor)
            files += 1
        else:
            raise IdeToolchainReadError("unexpected archive member")
        final = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _unchanged(visible, final):
            raise IdeToolchainReadError("toolchain member changed during read")
    after = os.fstat(directory_fd)
    if not _unchanged(before, after):
        raise IdeToolchainReadError("toolchain directory changed during read")
    return files


def verify_ide_toolchain_tree(
    archive: Path, destination: Path, *, archive_sha256: str,
    archive_size: int, expected_root: str, archive_format: str,
) -> int:
    """Return compared regular-file count, or refuse without changing either path."""

    if (
        os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
        or not isinstance(archive, Path) or not isinstance(destination, Path)
        or not archive.is_absolute() or not destination.is_absolute()
        or not isinstance(expected_root, str) or not expected_root
        or "/" in expected_root or "\\" in expected_root or expected_root in {".", ".."}
        or _DIGEST.fullmatch(archive_sha256) is None
        or not isinstance(archive_size, int) or archive_size <= 0
        or archive_format not in {"tar", "zip"}
    ):
        raise IdeToolchainReadError("locked toolchain read policy is invalid or unavailable")
    archive_parent = destination_parent = -1
    try:
        archive_parent = _directory(archive.parent, create=False)
        destination_parent = _directory(destination.parent, create=False)
        for parent in (archive_parent, destination_parent):
            info = os.fstat(parent)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise IdeToolchainReadError("locked toolchain parent is not owner controlled")
        archive_info = os.stat(archive.name, dir_fd=archive_parent, follow_symlinks=False)
        destination_info = os.stat(destination.name, dir_fd=destination_parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(archive_info.st_mode)
            or archive_info.st_uid != os.geteuid()
            or archive_info.st_mode & 0o022
            or archive_info.st_size != archive_size
            or not stat.S_ISDIR(destination_info.st_mode)
        ):
            raise IdeToolchainReadError("locked archive or extracted tree differs from its selected type or size")
        archive_fd = os.open(archive.name, _FILE_FLAGS, dir_fd=archive_parent)
        try:
            if not _unchanged(archive_info, os.fstat(archive_fd)):
                raise IdeToolchainReadError("locked archive changed before read")
            with os.fdopen(os.dup(archive_fd), "rb") as source:
                if _hash_stream(source, archive_size) != archive_sha256:
                    raise IdeToolchainReadError("locked archive bytes differ from the lock")
            os.lseek(archive_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(archive_fd), "rb") as source:
                expected = _archive_inventory(source, expected_root=expected_root, archive_format=archive_format)
            if not _unchanged(archive_info, os.fstat(archive_fd)):
                raise IdeToolchainReadError("locked archive changed during read")
        finally:
            os.close(archive_fd)
        destination_fd = os.open(destination.name, _DIR_FLAGS, dir_fd=destination_parent)
        try:
            if not _unchanged(destination_info, os.fstat(destination_fd)):
                raise IdeToolchainReadError("toolchain destination changed before read")
            compared = _verify_tree(destination_fd, (), expected, archive_sha256=archive_sha256)
            if not _unchanged(destination_info, os.stat(destination.name, dir_fd=destination_parent, follow_symlinks=False)):
                raise IdeToolchainReadError("toolchain destination changed during read")
        finally:
            os.close(destination_fd)
        return compared
    except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
        raise IdeToolchainReadError(f"locked toolchain read needs review: {exc}") from exc
    finally:
        if destination_parent >= 0:
            os.close(destination_parent)
        if archive_parent >= 0:
            os.close(archive_parent)


__all__ = ["IdeToolchainReadError", "verify_ide_toolchain_tree"]
