"""Extract one locked IDE archive inside an active Core temporary lease.

The archive is checked against its lock and its members are screened before
extraction. The caller retains the Core stage on failure, then separately
promotes and admits a completed tree. This Linux bootstrap path does not
repair an existing target or dispose an interrupted stage.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import zipfile

from .durable_files import _directory
from .ide_toolchain_reader import (
    _DIR_FLAGS, _FILE_FLAGS, _archive_inventory,
    _hash_stream, _identity, _unchanged,
)
from .temporary_leases import CoreTemporaryLeases


class IdeToolchainExtractionError(ValueError):
    pass


def _descend(root_fd: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_fd)
    try:
        for name in parts:
            child = os.open(name, _DIR_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _sync_extracted(root_fd: int, expected: dict) -> None:
    """Persist regular members and directories using no-follow parent handles."""

    for parts, member in sorted(expected.items()):
        if not parts:
            continue
        parent_fd = _descend(root_fd, parts[:-1])
        try:
            visible = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            if visible.st_uid != os.geteuid() or (
                member.kind != "link" and visible.st_mode & 0o022
            ):
                raise IdeToolchainExtractionError("IDE extracted member lost owner custody")
            if member.kind == "file":
                if not stat.S_ISREG(visible.st_mode) or visible.st_size != member.size:
                    raise IdeToolchainExtractionError("IDE extracted file differs from archive")
                descriptor = os.open(parts[-1], _FILE_FLAGS, dir_fd=parent_fd)
                try:
                    if _identity(os.fstat(descriptor)) != _identity(visible):
                        raise IdeToolchainExtractionError("IDE extracted file changed before sync")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            elif member.kind == "link":
                if not stat.S_ISLNK(visible.st_mode) or os.readlink(
                    parts[-1], dir_fd=parent_fd,
                ) != member.link:
                    raise IdeToolchainExtractionError("IDE extracted link differs from archive")
            elif member.kind != "directory" or not stat.S_ISDIR(visible.st_mode):
                raise IdeToolchainExtractionError("IDE extracted member type differs from archive")
        finally:
            os.close(parent_fd)
    for parts, member in sorted(expected.items(), key=lambda row: (-len(row[0]), row[0])):
        if member.kind != "directory":
            continue
        directory_fd = _descend(root_fd, parts)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def extract_locked_ide_archive(
    host: CoreTemporaryLeases, reference, archive: Path, *,
    archive_sha256: str, archive_size: int, expected_root: str,
    archive_format: str,
) -> Path:
    """Populate an empty active lease from an exact TAR or ZIP archive."""

    if (
        sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW")
        or not isinstance(host, CoreTemporaryLeases)
        or not isinstance(archive, Path) or not archive.is_absolute()
        or ".." in archive.parts
        or reference is None or not isinstance(getattr(reference, "path", None), Path)
        or archive_format not in {"tar", "zip"}
        or not isinstance(expected_root, str) or expected_root in {"", ".", ".."}
        or "/" in expected_root or "\\" in expected_root
        or type(archive_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", archive_sha256) is None
        or type(archive_size) is not int or archive_size <= 0
    ):
        raise IdeToolchainExtractionError("IDE extraction policy is invalid or unavailable")

    archive_parent = archive_fd = stage_fd = stage_parent = extracted_fd = -1
    try:
        stage_identity, marker_name, _marker_bytes = host.prepared_stage_marker(reference)
        stage_fd = _directory(reference.path, create=False)
        stage_parent = _directory(reference.path.parent, create=False)
        stage_before = os.fstat(stage_fd)
        pinned_stage = Path(f"/proc/self/fd/{stage_fd}")
        if _identity(stage_before) != stage_identity or set(os.listdir(stage_fd)) != {marker_name}:
            raise IdeToolchainExtractionError("IDE extraction stage is not empty or changed")
        archive_parent = _directory(archive.parent, create=False)
        parent_info = os.fstat(archive_parent)
        archive_before = os.stat(archive.name, dir_fd=archive_parent, follow_symlinks=False)
        if (
            parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022
            or not stat.S_ISREG(archive_before.st_mode)
            or archive_before.st_uid != os.geteuid()
            or archive_before.st_mode & 0o022
            or archive_before.st_size != archive_size
        ):
            raise IdeToolchainExtractionError("locked IDE archive lacks selected custody")
        archive_fd = os.open(archive.name, _FILE_FLAGS, dir_fd=archive_parent)
        if not _unchanged(archive_before, os.fstat(archive_fd)):
            raise IdeToolchainExtractionError("locked IDE archive changed before extraction")
        with os.fdopen(os.dup(archive_fd), "rb") as source:
            if _hash_stream(source, archive_size) != archive_sha256:
                raise IdeToolchainExtractionError("locked IDE archive bytes differ from the lock")
        os.lseek(archive_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(archive_fd), "rb") as source:
            expected = _archive_inventory(
                source, expected_root=expected_root, archive_format=archive_format,
            )
        if not _unchanged(archive_before, os.fstat(archive_fd)):
            raise IdeToolchainExtractionError("locked IDE archive changed during preflight")
        if _identity(stage_before) != _identity(os.stat(
            reference.path.name, dir_fd=stage_parent, follow_symlinks=False,
        )):
            raise IdeToolchainExtractionError("IDE extraction stage changed before publication")
        os.lseek(archive_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(archive_fd), "rb") as source:
            if archive_format == "tar":
                with tarfile.open(fileobj=source, mode="r:*") as bundle:
                    bundle.extractall(pinned_stage, filter="data")
            else:
                with zipfile.ZipFile(source) as bundle:
                    infos = bundle.infolist()
                    bundle.extractall(pinned_stage)
                    for member in infos:
                        mode = (member.external_attr >> 16) & 0o777
                        if mode:
                            (pinned_stage / member.filename).chmod(mode)
        if not _unchanged(archive_before, os.fstat(archive_fd)):
            raise IdeToolchainExtractionError("locked IDE archive changed during extraction")
        stage_after = os.fstat(stage_fd)
        visible_stage = os.stat(
            reference.path.name, dir_fd=stage_parent, follow_symlinks=False,
        )
        if (
            _identity(stage_before) != _identity(stage_after)
            or _identity(stage_before) != _identity(visible_stage)
            or stage_after.st_uid != stage_before.st_uid
            or stage_after.st_mode != stage_before.st_mode
        ):
            raise IdeToolchainExtractionError("IDE extraction stage changed during extraction")
        if set(os.listdir(stage_fd)) != {marker_name, expected_root}:
            raise IdeToolchainExtractionError("IDE extraction produced unexpected stage members")
        extracted_fd = os.open(expected_root, _DIR_FLAGS, dir_fd=stage_fd)
        _sync_extracted(extracted_fd, expected)
        os.fsync(stage_fd)
        host.open(reference.lease_id)
        return reference.path / expected_root
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        if isinstance(exc, IdeToolchainExtractionError):
            raise
        raise IdeToolchainExtractionError(f"Core IDE extraction needs review: {exc}") from exc
    finally:
        for descriptor in (archive_fd, archive_parent, stage_fd, stage_parent, extracted_fd):
            if descriptor >= 0:
                os.close(descriptor)


__all__ = ["IdeToolchainExtractionError", "extract_locked_ide_archive"]
