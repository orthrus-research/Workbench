"""Core custody for small revisioned preference files.

The caller owns the schema and policy. Core owns the private directory,
cross-process update lock, bounded read, compared replacement and flush.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Callable

from workbench_api.host_filesystem import DurableRecordError, HostFilesystemError

from .durable_records import read_bounded_bytes, replace_private_bytes
from .host_filesystem import fsync_directory, secure_private_path
from .setup_cli import _state_root, setup_record_lock


class PreferenceRecordError(ValueError):
    """A preference file cannot be read or updated safely."""


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
