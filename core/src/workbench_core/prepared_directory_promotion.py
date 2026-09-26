"""Core's bounded no-replace move for a prepared, unadmitted directory.

This bootstrap port deliberately does not inventory members. The caller owns
the prepared content and any marker meaning; Core owns an optional exact
marker write and physical promotion. Existing destinations and interrupted
stages are retained.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat

from workbench_api.managed_trees import ManagedTreeError

from .durable_files import _directory as pinned_directory
from .host_filesystem import private_path
from .managed_trees import _rename_no_replace


class PreparedDirectoryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def promote_prepared_directory(
    payload: Path, target: Path, *, marker_name: str | None = None,
    marker_bytes: bytes | None = None,
) -> Path:
    """Optionally write an exact marker, then atomically move one adjacent stage.

    The stage must be a private direct child of a sibling directory. This
    protects existing outputs but does not attest member bytes or make a
    matching marker sufficient for admission.
    """

    if (
        not isinstance(payload, Path) or not isinstance(target, Path)
        or not payload.is_absolute() or not target.is_absolute()
        or ".." in payload.parts or ".." in target.parts
        or payload.parent.parent != target.parent
        or payload.name in {"", ".", ".."} or target.name in {"", ".", ".."}
        or (
            (marker_name is None and marker_bytes is not None)
            or (marker_name is not None and (
                not isinstance(marker_name, str)
                or marker_name in {"", ".", ".."}
                or len(marker_name) > 128 or "/" in marker_name
                or "\\" in marker_name or ":" in marker_name or "\0" in marker_name
                or type(marker_bytes) is not bytes
                or not 0 < len(marker_bytes) <= 128
            ))
        )
    ):
        raise PreparedDirectoryError("directory.policy", "prepared directory paths or marker are invalid")
    if os.name != "posix" or os.scandir not in os.supports_fd:
        raise PreparedDirectoryError("directory.filesystem", "pinned prepared-directory promotion requires POSIX")

    parent_fd = stage_fd = payload_fd = -1
    try:
        parent_fd = pinned_directory(target.parent, create=False)
        stage_fd = pinned_directory(payload.parent, create=False)
        payload_fd = pinned_directory(payload, create=False)
        parent_info = os.fstat(parent_fd)
        stage_info = os.fstat(stage_fd)
        payload_info = os.fstat(payload_fd)
        if (
            not private_path(payload.parent, directory=True)
            or _identity(stage_info) != _identity(
                os.stat(payload.parent.name, dir_fd=parent_fd, follow_symlinks=False)
            )
            or _identity(payload_info) != _identity(
                os.stat(payload.name, dir_fd=stage_fd, follow_symlinks=False)
            )
            or parent_info.st_mode & 0o022
            or (hasattr(os, "geteuid") and parent_info.st_uid != os.geteuid())
        ):
            raise PreparedDirectoryError("directory.unsafe", "prepared directory lost its selected custody")
        with os.scandir(stage_fd) as scanned:
            if {entry.name for entry in scanned} != {payload.name}:
                raise PreparedDirectoryError("directory.incomplete", "prepared directory has unexpected stage children")
        if marker_name is not None:
            assert marker_bytes is not None
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            marker_fd = os.open(marker_name, flags, 0o600, dir_fd=payload_fd)
            try:
                remaining = memoryview(marker_bytes)
                while remaining:
                    written = os.write(marker_fd, remaining)
                    if written <= 0:
                        raise PreparedDirectoryError("directory.write", "prepared marker write made no progress")
                    remaining = remaining[written:]
                os.fsync(marker_fd)
            finally:
                os.close(marker_fd)
        os.fsync(payload_fd)
        try:
            _rename_no_replace(
                payload, target, parent_identity=_identity(parent_info),
                payload_identity=_identity(payload_info),
            )
        except ManagedTreeError as exc:
            raise PreparedDirectoryError(exc.code, str(exc)) from exc
        os.fsync(parent_fd)
        visible = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(visible.st_mode) or _identity(visible) != _identity(payload_info):
            raise PreparedDirectoryError("directory.changed", "promoted directory changed identity")
        reopened = pinned_directory(target.parent, create=False)
        try:
            if _identity(os.fstat(reopened)) != _identity(parent_info):
                raise PreparedDirectoryError("directory.changed", "promoted directory parent changed identity")
        finally:
            os.close(reopened)
        # The stage root stays as an empty publication witness. A path-based
        # rmdir cannot atomically bind deletion to its pinned inode, so cleanup
        # awaits a separately reviewed Core disposition.
        try:
            stage_visible = os.stat(payload.parent.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(stage_visible.st_mode) or _identity(stage_visible) != _identity(stage_info):
                raise PreparedDirectoryError("directory.changed", "published directory stage changed identity")
            os.fsync(stage_fd)
        except PreparedDirectoryError:
            raise
        except OSError as exc:
            raise PreparedDirectoryError("directory.incomplete", "published directory has stage residue") from exc
        return target
    except PreparedDirectoryError:
        raise
    except OSError as exc:
        raise PreparedDirectoryError("directory.unavailable", "prepared directory promotion is unavailable") from exc
    finally:
        for descriptor in (payload_fd, stage_fd, parent_fd):
            if descriptor >= 0:
                os.close(descriptor)


__all__ = ["PreparedDirectoryError", "promote_prepared_directory"]
