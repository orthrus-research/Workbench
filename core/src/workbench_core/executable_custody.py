"""Private executable staging for byte-bound Workbench tool invocations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterator


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ExecutableCustodyError(RuntimeError):
    """An executable could not be staged or changed while in custody."""


def _open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _opened_identity(path: Path, *, maximum: int) -> tuple[str, int]:
    try:
        descriptor = os.open(path, _open_flags())
    except OSError as error:
        raise ExecutableCustodyError(
            f"executable cannot be opened safely: {path}"
        ) from error
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or not 1 <= before.st_size <= maximum
            ):
                raise ExecutableCustodyError(
                    f"executable is not one bounded regular file: {path}"
                )
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > maximum:
                    raise ExecutableCustodyError(
                        f"executable exceeds its byte bound: {path}"
                    )
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise ExecutableCustodyError(
            f"executable cannot be read safely: {path}"
        ) from error
    if (
        total != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or after.st_ctime_ns != before.st_ctime_ns
    ):
        raise ExecutableCustodyError(f"executable changed while being read: {path}")
    return digest.hexdigest(), total


@dataclass(frozen=True)
class CustodiedExecutable:
    """One private staged executable whose initial bytes are content-addressed."""

    path: Path
    sha256: str
    size: int
    source_path: Path
    maximum: int

    def require_unchanged(self) -> None:
        """Fail if the staged path no longer names the initially copied bytes."""

        try:
            info = self.path.lstat()
        except OSError as error:
            raise ExecutableCustodyError(
                f"staged executable is unavailable: {self.path}"
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ExecutableCustodyError(
                f"staged executable is no longer a regular file: {self.path}"
            )
        digest, size = _opened_identity(self.path, maximum=self.maximum)
        if digest != self.sha256 or size != self.size:
            raise ExecutableCustodyError(
                f"staged executable bytes changed after verification: {self.path}"
            )


@contextmanager
def stage_executable(
    source: Path,
    *,
    logical_name: str,
    maximum: int,
    expected_sha256: str | None = None,
) -> Iterator[CustodiedExecutable]:
    """Copy one opened regular file into a fresh private directory and hash it."""

    if _LOGICAL_NAME.fullmatch(logical_name) is None:
        raise ExecutableCustodyError("executable logical name is invalid")
    if type(maximum) is not int or maximum < 1:
        raise ExecutableCustodyError("executable byte bound is invalid")
    if expected_sha256 is not None and (
        type(expected_sha256) is not str or _DIGEST.fullmatch(expected_sha256) is None
    ):
        raise ExecutableCustodyError("expected executable digest is invalid")

    source = Path(source).expanduser()
    try:
        source = source.resolve(strict=True)
        source_info = source.lstat()
    except OSError as error:
        raise ExecutableCustodyError(
            f"executable source is unavailable: {source}"
        ) from error
    if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISREG(source_info.st_mode):
        raise ExecutableCustodyError(
            f"executable source is not one regular file: {source}"
        )
    if not os.access(source, os.X_OK):
        raise ExecutableCustodyError(f"executable source is not runnable: {source}")

    suffix = ".exe" if source.name.casefold().endswith(".exe") else ""
    # Do not let POSIX TMPDIR/TEMP redirect custody onto a filesystem (notably
    # a WSL-mounted Windows volume) that cannot enforce owner-only modes.
    temporary_parent = "/tmp" if os.name == "posix" else None
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"workbench-{logical_name}-custody-",
            dir=temporary_parent,
        ) as temporary:
            custody_root = Path(temporary)
            custody_root.chmod(0o700)
            root_info = custody_root.lstat()
            if (
                stat.S_ISLNK(root_info.st_mode)
                or not stat.S_ISDIR(root_info.st_mode)
                or (
                    hasattr(os, "geteuid")
                    and root_info.st_uid != os.geteuid()
                )
                or (
                    os.name != "nt"
                    and stat.S_IMODE(root_info.st_mode) != 0o700
                )
            ):
                raise ExecutableCustodyError(
                    "executable custody root is not one private directory"
                )
            destination = custody_root / f"{logical_name}{suffix}"
            source_descriptor = os.open(source, _open_flags())
            destination_flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
            )
            try:
                destination_descriptor = os.open(
                    destination, destination_flags, 0o700
                )
            except Exception:
                os.close(source_descriptor)
                raise
            total = 0
            source_stream = os.fdopen(source_descriptor, "rb")
            try:
                destination_stream = os.fdopen(destination_descriptor, "wb")
            except Exception:
                source_stream.close()
                os.close(destination_descriptor)
                raise
            with source_stream, destination_stream:
                source_opened = os.fstat(source_stream.fileno())
                if (
                    not stat.S_ISREG(source_opened.st_mode)
                    or not 1 <= source_opened.st_size <= maximum
                ):
                    raise ExecutableCustodyError(
                        "executable source is not one bounded regular file: "
                        f"{source}"
                    )
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > maximum:
                        raise ExecutableCustodyError(
                            f"executable source exceeds its byte bound: {source}"
                        )
                    destination_stream.write(chunk)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
                source_after = os.fstat(source_stream.fileno())
            if (
                total != source_opened.st_size
                or source_after.st_size != source_opened.st_size
                or source_after.st_mtime_ns != source_opened.st_mtime_ns
                or source_after.st_ctime_ns != source_opened.st_ctime_ns
            ):
                raise ExecutableCustodyError(
                    f"executable source changed while being staged: {source}"
                )
            destination.chmod(0o500)
            digest, size = _opened_identity(destination, maximum=maximum)
            if expected_sha256 is not None and digest != expected_sha256:
                raise ExecutableCustodyError(
                    "staged executable digest differs from the required custody identity"
                )
            staged = CustodiedExecutable(
                path=destination,
                sha256=digest,
                size=size,
                source_path=source,
                maximum=maximum,
            )
            staged.require_unchanged()
            yield staged
    except ExecutableCustodyError:
        raise
    except OSError as error:
        raise ExecutableCustodyError(
            f"executable could not be placed in private custody: {source}"
        ) from error
