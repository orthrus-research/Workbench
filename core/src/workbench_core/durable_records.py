"""Core physical custody for private immutable and revisioned records.

Domain owners retain their schemas and transitions. Core owns staging,
publication, verified reads, and the expected-byte compare for current records.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from time import monotonic, sleep
from typing import Iterator

from workbench_api.host_filesystem import DurableRecordError, HostFilesystemError

from .host_filesystem import file_lease, fsync_directory, private_path, secure_private_path


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _parent(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or path.name in {"", ".", ".."}:
        raise DurableRecordError("path", "private record needs an absolute file path")
    for ancestor in (path.parent, *path.parent.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise DurableRecordError("unsafe", "private record path traverses a redirecting directory")
    if not private_path(path.parent, directory=True):
        raise DurableRecordError(
            "unsafe",
            "private record directory cannot enforce owner-only access; "
            "on WSL use its Linux filesystem when a Windows mount lacks Unix metadata",
        )


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    # Windows ACL changes can alter ctime without changing file custody.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _ordinary(
    path: Path, *, byte_limit: int, private: bool = True,
    single_link: bool = False,
) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise DurableRecordError("unavailable", "private record is unavailable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise DurableRecordError("unsafe", "private record is a symbolic link")
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size > byte_limit
        or (private and not private_path(path, directory=False))
        or (single_link and info.st_nlink != 1)
        or (
            private and os.name != "nt" and (
                info.st_mode & 0o077
                or (hasattr(os, "geteuid") and info.st_uid != os.geteuid())
            )
        )
    ):
        raise DurableRecordError("unsafe", "private record is not a bounded owner-private file")
    return info


def _read_bytes(
    path: Path, *, byte_limit: int, private: bool,
    single_link: bool = False,
) -> bytes:
    if private:
        _parent(path)
    elif not isinstance(path, Path) or not path.is_absolute():
        raise DurableRecordError("path", "bounded record needs an absolute file path")
    if type(byte_limit) is not int or byte_limit < 0:
        raise DurableRecordError("bounds", "private record byte limit is invalid")
    visible = _ordinary(path, byte_limit=byte_limit, private=private, single_link=single_link)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(visible):
                raise DurableRecordError("changed", "private record changed before reading")
            chunks: list[bytes] = []
            remaining = byte_limit + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        final = _ordinary(path, byte_limit=byte_limit, private=private, single_link=single_link)
    except DurableRecordError:
        raise
    except OSError as exc:
        raise DurableRecordError("unavailable", f"cannot read private record: {exc}") from exc
    data = b"".join(chunks)
    if len(data) != opened.st_size or _identity(opened) != _identity(after) or _identity(after) != _identity(final):
        raise DurableRecordError("changed", "private record changed during reading")
    if single_link and (
        opened.st_nlink != 1
        or any(
            getattr(visible, field) != getattr(final, field)
            or getattr(opened, field) != getattr(after, field)
            for field in ("st_mode", "st_nlink", "st_ctime_ns")
        )
        or any(
            getattr(visible, field) != getattr(opened, field)
            or getattr(after, field) != getattr(final, field)
            for field in ("st_mode", "st_nlink")
        )
    ):
        raise DurableRecordError("changed", "private record custody changed during reading")
    return data


def read_private_bytes(path: Path, *, byte_limit: int) -> bytes:
    return _read_bytes(path, byte_limit=byte_limit, private=True)


def read_private_single_link_bytes(path: Path, *, byte_limit: int) -> bytes:
    """Read one owner-private binding with singular, stable file custody."""

    return _read_bytes(path, byte_limit=byte_limit, private=True, single_link=True)


def read_bounded_bytes(path: Path, *, byte_limit: int) -> bytes:
    """Read an external ordinary input without requiring Core-owned private custody."""

    return _read_bytes(path, byte_limit=byte_limit, private=False)


def read_bounded_single_link_bytes(path: Path, *, byte_limit: int) -> bytes:
    """Read one ordinary external file without accepting another hard link."""

    return _read_bytes(path, byte_limit=byte_limit, private=False, single_link=True)


@contextmanager
def _prepared(
    path: Path, data: bytes, *, create_once_stage: bool = False,
) -> Iterator[tuple[Path, os.stat_result]]:
    descriptor = -1
    temporary: Path | None = None
    opened = False
    created = False
    try:
        if create_once_stage:
            # The historical adoption recovery reader sees this exact shape.
            temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags | getattr(os, "O_BINARY", 0), 0o600)
        else:
            descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temporary = Path(name)
        opened = True
        created = True
        os.chmod(temporary, 0o600)
        stream = os.fdopen(descriptor, "wb")
        opened = False
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            created = os.fstat(stream.fileno())
        secure_private_path(temporary, directory=False)
        if not private_path(temporary, directory=False):
            raise DurableRecordError("unsafe", "staged private record is not owner-private")
        secured = temporary.lstat()
        if not stat.S_ISREG(secured.st_mode) or _identity(created) != _identity(secured):
            raise DurableRecordError("changed", "staged private record changed while secured")
        yield temporary, secured
    except DurableRecordError:
        raise
    except HostFilesystemError as exc:
        raise DurableRecordError("unsafe", f"cannot secure private record: {exc}") from exc
    except OSError as exc:
        raise DurableRecordError("write", f"cannot stage private record: {exc}") from exc
    finally:
        if opened:
            os.close(descriptor)
        if created and temporary is not None:
            temporary.unlink(missing_ok=True)


def _check_data(data: bytes, byte_limit: int) -> None:
    if type(data) is not bytes or type(byte_limit) is not int or byte_limit < 0 or len(data) > byte_limit:
        raise DurableRecordError("bounds", "private record exceeds its byte bound")


def publish_immutable_bytes(
    path: Path, data: bytes, *, byte_limit: int, idempotent: bool = False,
) -> None:
    _parent(path)
    _check_data(data, byte_limit)
    if path.exists() or path.is_symlink():
        if idempotent and read_private_bytes(path, byte_limit=byte_limit) == data:
            return
        raise DurableRecordError("collision", "immutable private record already exists")
    with _prepared(path, data) as (temporary, secured):
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            if not idempotent or read_private_bytes(path, byte_limit=byte_limit) != data:
                raise DurableRecordError("collision", "immutable private record raced") from exc
        except OSError as exc:
            raise DurableRecordError("write", f"cannot publish immutable record: {exc}") from exc
        else:
            published = _ordinary(path, byte_limit=byte_limit)
            if _identity(published) != _identity(secured) or read_private_bytes(path, byte_limit=byte_limit) != data:
                raise DurableRecordError("changed", "published immutable record changed")
    fsync_directory(path.parent)


def publish_create_once_bytes(path: Path, data: bytes, *, byte_limit: int) -> None:
    """Publish under the historical visible-stage create-once convention."""

    _parent(path)
    _check_data(data, byte_limit)
    if path.exists() or path.is_symlink():
        raise DurableRecordError("collision", "create-once private record already exists")
    with _prepared(path, data, create_once_stage=True) as (temporary, secured):
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise DurableRecordError("collision", "create-once private record raced") from exc
        except OSError as exc:
            raise DurableRecordError("write", f"cannot publish create-once record: {exc}") from exc
        published = _ordinary(path, byte_limit=byte_limit)
        if _identity(published) != _identity(secured) or read_private_bytes(path, byte_limit=byte_limit) != data:
            raise DurableRecordError("changed", "published create-once record changed")
        # Keep the visible stage until the destination directory is durable.
        # A process crash before that flush must remain observable on reopen.
        fsync_directory(path.parent)


def count_interrupted_create_once_stages(path: Path) -> int:
    """Inventory visible crash stages without opening, changing or removing them."""

    _parent(path)
    prefix = f".{path.name}."
    try:
        candidates = sorted(path.parent.iterdir(), key=lambda item: item.name)
        count = 0
        for candidate in candidates:
            if not candidate.name.startswith(prefix) or not candidate.name.endswith(".tmp"):
                continue
            if not stat.S_ISREG(candidate.lstat().st_mode):
                raise DurableRecordError("unsafe", "interrupted create-once stage is not a regular file")
            count += 1
        return count
    except DurableRecordError:
        raise
    except OSError as exc:
        raise DurableRecordError("unavailable", f"cannot inspect interrupted create-once stages: {exc}") from exc


@contextmanager
def private_record_lock(lock_path: Path, *, wait: bool = False) -> Iterator[None]:
    """Hold an owner-private advisory lease across a domain transition."""

    _parent(lock_path)
    descriptor = -1
    created = False
    lease = None
    try:
        if lock_path.is_symlink():
            raise DurableRecordError("unsafe", "private record lock is a symbolic link")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            descriptor = os.open(lock_path, flags)
        opened = os.fstat(descriptor)
        visible = lock_path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _identity(opened) != _identity(visible)
        ):
            raise DurableRecordError("unsafe", "private record lock changed identity")
        if created:
            secure_private_path(lock_path, directory=False)
        if not private_path(lock_path, directory=False):
            raise DurableRecordError("unsafe", "private record lock is not owner-private")
        if created:
            os.fsync(descriptor)
            fsync_directory(lock_path.parent)
        deadline = monotonic() + 30.0
        while True:
            lease = file_lease(descriptor, exclusive=True)
            try:
                lease.__enter__()
                break
            except BlockingIOError as exc:
                if not wait or monotonic() >= deadline:
                    raise DurableRecordError("busy", "private record is held by another writer") from exc
                sleep(0.01)
    except DurableRecordError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except HostFilesystemError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise DurableRecordError("unsafe", f"cannot secure private record lock: {exc}") from exc
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise DurableRecordError("write", f"cannot lock private record: {exc}") from exc
    try:
        yield
    finally:
        try:
            assert lease is not None
            lease.__exit__(None, None, None)
        finally:
            os.close(descriptor)


@contextmanager
def private_exclusive_marker(path: Path) -> Iterator[None]:
    """Hold a V1-compatible create-exclusive marker in a private store.

    Older writers use the marker's existence for exclusion. An existing file
    may be a live writer or an interrupted attempt, so Core never adopts it.
    The marker is removed only if its exact opened inode remains unchanged.
    """

    _parent(path)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except FileExistsError as exc:
            raise DurableRecordError("busy", "exclusive private marker already exists") from exc
        except OSError as exc:
            raise DurableRecordError("write", f"cannot create exclusive private marker: {exc}") from exc
        try:
            secure_private_path(path, directory=False)
            opened = os.fstat(descriptor)
            visible = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or _identity(opened) != _identity(visible)
                or not private_path(path, directory=False)
            ):
                raise DurableRecordError("changed", "exclusive private marker changed during creation")
            os.fsync(descriptor)
            fsync_directory(path.parent)
            created = _identity(os.fstat(descriptor))
        except DurableRecordError:
            raise
        except HostFilesystemError as exc:
            raise DurableRecordError("unsafe", f"cannot secure exclusive private marker: {exc}") from exc
        except OSError as exc:
            raise DurableRecordError("write", f"cannot seal exclusive private marker: {exc}") from exc
        try:
            yield
        finally:
            try:
                opened = os.fstat(descriptor)
                visible = path.lstat()
                if (
                    not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                    or visible.st_size != 0 or _identity(opened) != created
                    or _identity(visible) != created
                    or not private_path(path, directory=False)
                ):
                    raise DurableRecordError("changed", "exclusive private marker changed while held")
                # Windows does not permit unlinking the marker with this
                # writer descriptor open. Recheck the exact visible inode
                # after close before removing the historical marker path.
                os.close(descriptor)
                descriptor = -1
                visible = path.lstat()
                if (
                    not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                    or visible.st_size != 0 or _identity(visible) != created
                    or not private_path(path, directory=False)
                ):
                    raise DurableRecordError("changed", "exclusive private marker changed before release")
                path.unlink()
                fsync_directory(path.parent)
            except DurableRecordError:
                raise
            except HostFilesystemError as exc:
                raise DurableRecordError("unsafe", f"cannot release exclusive private marker: {exc}") from exc
            except OSError as exc:
                raise DurableRecordError("write", f"cannot release exclusive private marker: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _record_lock(path: Path) -> Iterator[None]:
    # Setup's historical .<name>.lock may already be held while Core saves
    # preferences. Keep this lease distinct until that outer transaction moves.
    with private_record_lock(path.parent / f".{path.name}.record.lock"):
        yield


def replace_private_bytes(
    path: Path, data: bytes, *, byte_limit: int,
    expected_sha256: str | None = None, require_absent: bool = False,
) -> None:
    _parent(path)
    _check_data(data, byte_limit)
    if expected_sha256 is not None and (type(expected_sha256) is not str or _DIGEST.fullmatch(expected_sha256) is None):
        raise DurableRecordError("bounds", "expected private record digest is invalid")
    if expected_sha256 is not None and require_absent:
        raise DurableRecordError("bounds", "choose an expected digest or an absent target")
    with _record_lock(path):
        if require_absent and (path.exists() or path.is_symlink()):
            raise DurableRecordError("stale", "private record appeared after review")
        if expected_sha256 is not None:
            observed = read_private_bytes(path, byte_limit=byte_limit)
            if "sha256:" + sha256(observed).hexdigest() != expected_sha256:
                raise DurableRecordError("stale", "private record changed after review")
        elif path.exists() or path.is_symlink():
            _ordinary(path, byte_limit=byte_limit)
        with _prepared(path, data) as (temporary, secured):
            try:
                os.replace(temporary, path)
            except OSError as exc:
                raise DurableRecordError("write", f"cannot replace private record: {exc}") from exc
            published = _ordinary(path, byte_limit=byte_limit)
            if _identity(published) != _identity(secured) or read_private_bytes(path, byte_limit=byte_limit) != data:
                raise DurableRecordError("changed", "replaced private record changed")
        fsync_directory(path.parent)


def remove_private_bytes(
    path: Path, *, expected_sha256: str, byte_limit: int,
) -> None:
    """Remove a private record after Core confirms its exact reviewed digest.

    The caller owns the record's meaning and deletion policy. Core owns the
    physical comparison, redirect checks, cooperating-writer lease and sync.
    """

    _parent(path)
    if (
        type(expected_sha256) is not str
        or _DIGEST.fullmatch(expected_sha256) is None
        or type(byte_limit) is not int
        or byte_limit < 0
    ):
        raise DurableRecordError("bounds", "private record removal precondition is invalid")
    with _record_lock(path):
        before = _ordinary(path, byte_limit=byte_limit)
        observed = read_private_bytes(path, byte_limit=byte_limit)
        if "sha256:" + sha256(observed).hexdigest() != expected_sha256:
            raise DurableRecordError("stale", "private record changed after review")
        if _identity(before) != _identity(_ordinary(path, byte_limit=byte_limit)):
            raise DurableRecordError("changed", "private record changed before removal")
        try:
            path.unlink()
            fsync_directory(path.parent)
        except OSError as exc:
            raise DurableRecordError("write", f"cannot remove private record: {exc}") from exc


def append_private_line(
    path: Path, line: bytes, *, expected_size: int, byte_limit: int,
    journal_byte_limit: int | None = None,
) -> int:
    """Create or append one owner-private journal line with an exact-size precondition.

    The append lock serializes cooperating writers. A torn final line is left
    intact for owner recovery and prevents a later append from hiding it.
    """

    _parent(path)
    _check_data(line, byte_limit)
    if not line or not line.endswith(b"\n") or b"\n" in line[:-1]:
        raise DurableRecordError("bounds", "private journal append needs one complete line")
    if type(expected_size) is not int or expected_size < 0:
        raise DurableRecordError("bounds", "private journal expected size is invalid")
    if journal_byte_limit is not None and (
        type(journal_byte_limit) is not int
        or journal_byte_limit < 0
        or expected_size + len(line) > journal_byte_limit
    ):
        raise DurableRecordError("bounds", "private journal exceeds its total byte bound")
    with private_record_lock(path.parent / f".{path.name}.append.lock"):
        if expected_size == 0:
            if path.is_symlink():
                raise DurableRecordError("unsafe", "private journal is a symbolic link")
            if path.exists():
                raise DurableRecordError("collision", "private journal already exists")
            flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL
        else:
            flags = os.O_RDWR | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise DurableRecordError("collision", "private journal already exists") from exc
        except FileNotFoundError as exc:
            raise DurableRecordError("unavailable", "private journal is unavailable") from exc
        except OSError as exc:
            raise DurableRecordError("write", f"cannot open private journal: {exc}") from exc
        try:
            if expected_size == 0:
                secure_private_path(path, directory=False)
            visible = _ordinary(path, byte_limit=max(expected_size, path.lstat().st_size))
            opened = os.fstat(descriptor)
            if opened.st_nlink != 1 or _identity(opened) != _identity(visible):
                raise DurableRecordError("changed", "private journal changed before append")
            if opened.st_size != expected_size:
                raise DurableRecordError("stale", "private journal length changed after review")
            if expected_size:
                os.lseek(descriptor, expected_size - 1, os.SEEK_SET)
                if os.read(descriptor, 1) != b"\n":
                    raise DurableRecordError("incomplete", "private journal has an incomplete final line")
            remaining = memoryview(line)
            while remaining:
                count = os.write(descriptor, remaining)
                if count <= 0:
                    raise OSError("private journal append made no progress")
                remaining = remaining[count:]
            os.fsync(descriptor)
            finished = os.fstat(descriptor)
            published = _ordinary(path, byte_limit=expected_size + len(line))
            if (
                finished.st_nlink != 1
                or finished.st_size != expected_size + len(line)
                or _identity(finished) != _identity(published)
            ):
                raise DurableRecordError("changed", "private journal changed during append")
            os.lseek(descriptor, expected_size, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining_bytes = len(line)
            while remaining_bytes:
                chunk = os.read(descriptor, min(remaining_bytes, 65536))
                if not chunk:
                    raise DurableRecordError("changed", "private journal append could not be read back")
                chunks.append(chunk)
                remaining_bytes -= len(chunk)
            if b"".join(chunks) != line:
                raise DurableRecordError("changed", "private journal append differs on readback")
            if _identity(os.fstat(descriptor)) != _identity(
                _ordinary(path, byte_limit=expected_size + len(line))
            ):
                raise DurableRecordError("changed", "private journal changed after readback")
            if expected_size == 0:
                fsync_directory(path.parent)
            return finished.st_size
        except DurableRecordError:
            raise
        except HostFilesystemError as exc:
            raise DurableRecordError("unsafe", f"cannot secure private journal: {exc}") from exc
        except OSError as exc:
            raise DurableRecordError("write", f"cannot append private journal: {exc}") from exc
        finally:
            os.close(descriptor)


def inspect_private_journal(path: Path, *, byte_limit: int) -> dict:
    """Report a complete prefix and retained torn tail without editing either."""

    raw = read_private_bytes(path, byte_limit=byte_limit)
    complete_size = raw.rfind(b"\n") + 1
    tail = raw[complete_size:]
    return {
        "format": "workbench-private-journal-inspection-v1",
        "size": len(raw),
        "sha256": sha256(raw).hexdigest(),
        "complete_size": complete_size,
        "complete_sha256": sha256(raw[:complete_size]).hexdigest(),
        "incomplete_size": len(tail),
        "incomplete_sha256": sha256(tail).hexdigest() if tail else None,
    }


__all__ = [
    "read_private_bytes", "read_bounded_bytes", "read_bounded_single_link_bytes", "publish_immutable_bytes",
    "replace_private_bytes", "remove_private_bytes", "private_record_lock", "append_private_line",
    "inspect_private_journal",
]
