"""Physical Git metadata custody for Blueprints fresh-project bootstrap.

This port is deliberately bounded to the historical exclude and marker files.
Blueprints admits the target and owns the V2 recovery journal; Core checks the
old bytes again and performs every metadata create, replace, and removal.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile

from workbench_api.git_bootstrap import GitBootstrapError

from .host_filesystem import fsync_directory


_MAXIMUM_METADATA_BYTES = 1024 * 1024
_MARKER_NAME = "workbench-fresh-project-v2.json"


def _fail(code: str, message: str) -> None:
    raise GitBootstrapError(code, message)


def _check_data(data: bytes | None) -> None:
    if data is not None and (type(data) is not bytes or len(data) > _MAXIMUM_METADATA_BYTES):
        _fail("bytes", "fresh Git metadata must be bounded bytes")


def _identity(state: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        state.st_dev, state.st_ino, state.st_mode, state.st_size,
        state.st_mtime_ns, state.st_nlink,
    )


def _ordinary_directory(path: Path) -> None:
    try:
        state = path.lstat()
    except OSError as exc:
        raise GitBootstrapError("path", "cannot inspect fresh Git directory") from exc
    if not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
        _fail("path", "fresh Git directory is redirected or not ordinary")


def _paths(target: Path, *, exclude: bool) -> tuple[Path, Path]:
    if not isinstance(target, Path) or not target.is_absolute() or target == Path(target.anchor):
        _fail("path", "fresh Git target must be an absolute non-root path")
    for path in reversed((target, *target.parents)):
        if getattr(path, "is_junction", lambda: False)():
            _fail("path", "fresh Git target traverses a junction")
        _ordinary_directory(path)
    git = target / ".git"
    _ordinary_directory(git)
    parent = git / "info" if exclude else git
    _ordinary_directory(parent)
    return parent, parent / ("exclude" if exclude else _MARKER_NAME)


def _read(path: Path) -> tuple[bytes, os.stat_result] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GitBootstrapError("path", "cannot open fresh Git metadata") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAXIMUM_METADATA_BYTES
        ):
            _fail("path", "fresh Git metadata is not a bounded independent file")
        chunks: list[bytes] = []
        remaining = _MAXIMUM_METADATA_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        visible = path.lstat()
    except OSError as exc:
        raise GitBootstrapError("stale", "fresh Git metadata changed while reading") from exc
    if (
        len(data) != before.st_size
        or len(data) > _MAXIMUM_METADATA_BYTES
        or _identity(before) != _identity(after)
        or _identity(after) != _identity(visible)
    ):
        _fail("stale", "fresh Git metadata changed while reading")
    return data, visible


def _matches(path: Path, expected: bytes | None) -> os.stat_result | None:
    _check_data(expected)
    observed = _read(path)
    if observed is None:
        if expected is None:
            return None
        _fail("stale", "fresh Git metadata disappeared after review")
    if expected is None or observed[0] != expected:
        _fail("stale", "fresh Git metadata bytes changed after review")
    return observed[1]


def _refuse_interrupted_stages(path: Path) -> None:
    prefix = f".{path.name}.workbench-git-"
    for candidate in path.parent.iterdir():
        if candidate.name.startswith(prefix) and candidate.name.endswith(".tmp"):
            _fail("stage", "interrupted fresh Git metadata stage requires recovery review")


def _create(path: Path, data: bytes) -> None:
    _check_data(data)
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise GitBootstrapError("stale", "fresh Git metadata already exists or cannot be created") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
        _matches(path, data)
    except BaseException:
        # A visible partial file is retained for journaled recovery review.
        raise


def _replace(path: Path, *, before: bytes | None, after: bytes) -> None:
    _check_data(before)
    _check_data(after)
    observed = _matches(path, before)
    if before == after:
        return
    if before is None:
        _create(path, after)
        return
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.workbench-git-", suffix=".tmp", dir=path.parent,
    )
    staged = Path(name)
    stage_state = os.fstat(descriptor)
    stage_identity = stage_state.st_dev, stage_state.st_ino
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
        visible = _matches(path, before)
        if observed is None or visible is None or _identity(observed) != _identity(visible):
            _fail("stale", "fresh Git metadata identity changed before replacement")
        os.replace(staged, path)
        fsync_directory(path.parent)
        _matches(path, after)
    finally:
        # A stage from this live call is safe to remove by its exact path.
        try:
            visible_stage = staged.lstat()
        except FileNotFoundError:
            pass
        else:
            if (visible_stage.st_dev, visible_stage.st_ino) == stage_identity:
                staged.unlink()


def _remove(path: Path, *, expected: bytes) -> None:
    first = _matches(path, expected)
    second = _matches(path, expected)
    if first is None or second is None or _identity(first) != _identity(second):
        _fail("stale", "fresh Git metadata identity changed before removal")
    path.unlink()
    fsync_directory(path.parent)


class CoreGitBootstrap:
    """Core physical mutations for one owner-reviewed fresh Git target."""

    def replace_exclude(self, target: Path, *, before: bytes | None, after: bytes) -> None:
        _, path = _paths(target, exclude=True)
        _refuse_interrupted_stages(path)
        _replace(path, before=before, after=after)

    def restore_exclude(self, target: Path, *, before: bytes | None, after: bytes) -> None:
        _, path = _paths(target, exclude=True)
        _refuse_interrupted_stages(path)
        _check_data(before)
        _check_data(after)
        current = _read(path)
        if (None if current is None else current[0]) == before:
            return
        if current is None or current[0] != after:
            _fail("stale", "fresh Git exclude bytes changed before recovery")
        if before is None:
            _remove(path, expected=after)
        else:
            _replace(path, before=after, after=before)

    def create_marker(self, target: Path, data: bytes) -> None:
        _, path = _paths(target, exclude=False)
        _create(path, data)

    def remove_marker(self, target: Path, *, expected: bytes) -> None:
        _, path = _paths(target, exclude=False)
        _remove(path, expected=expected)


HOST = CoreGitBootstrap()


__all__ = ["CoreGitBootstrap", "HOST"]
