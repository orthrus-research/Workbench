"""Environment-wide package mutation exclusion and installed-code drift checks.

These advisory leases coordinate Workbench processes, not external pip. Pip
drift is detected and requires stopping/restarting the affected process; there
is no claim that an uncooperative external installer can be made transactional.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from importlib import metadata
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

from workbench_api import ModuleError


def environment_id() -> str:
    return sha256(str(Path(sys.prefix).resolve()).encode()).hexdigest()


def guard_root() -> Path:
    # The environment is the exclusion domain, even for distinct workspaces.
    # Resolve the account's fixed home, not caller-selected state/cache or HOME
    # environment variables: IDE clients and terminals must share exclusion.
    environment = environment_id()
    if os.name == "nt":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buffer) != 0:
            raise ModuleError("cannot resolve the account package lease directory")
        home = Path(buffer.value)
    else:
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".workbench-package-leases" / environment


def _require_private(observation, *, directory: bool) -> None:
    kind_ok = stat.S_ISDIR(observation.st_mode) if directory else stat.S_ISREG(observation.st_mode)
    if not kind_ok:
        raise ModuleError("package lease has the wrong file kind")
    if os.name != "nt" and (observation.st_uid != os.getuid() or stat.S_IMODE(observation.st_mode) & 0o077):
        raise ModuleError("package lease must be private and owned by the current account")


def _open_lock(path: Path) -> int:
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ModuleError("package lease path must not traverse symlinks")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _require_private(path.parent.stat(), directory=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _require_private(os.fstat(fd), directory=False)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _lock(fd: int, *, blocking: bool = False) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return True
    except (BlockingIOError, PermissionError):
        return False
    except OSError as exc:
        if exc.errno in {11, 13, 35, 36}:
            return False
        raise


@contextmanager
def _gate(root: Path):
    descriptor = _open_lock(root / "gate.lock")
    try:
        deadline = time.monotonic() + 0.5
        while not _lock(descriptor):
            if time.monotonic() >= deadline:
                raise ModuleError("package environment is busy; retry after the current package operation finishes")
            time.sleep(0.005)
        yield
    finally:
        os.close(descriptor)


def environment_fingerprint() -> str:
    """Include metadata contents and install identity, including same-version pip reinstalls."""
    rows = []
    for distribution in metadata.distributions():
        try:
            row = [distribution.metadata["Name"], distribution.version]
            for name in ("METADATA", "RECORD", "entry_points.txt", "direct_url.json"):
                row.append(distribution.read_text(name) or "")
            # Native metadata is replaced even during an identical reinstall.
            location = getattr(distribution, "_path", None)
            if location is not None:
                observation = Path(location).stat()
                row.append(f"{location}:{observation.st_ino}:{observation.st_mtime_ns}")
            rows.append("\0".join(row))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ModuleError("installed package metadata changed or is unreadable; stop active work and repair the environment") from exc
    return sha256("\n".join(sorted(rows)).encode()).hexdigest()


class PackageActivity:
    """Lease held for an entire command or service lifetime, including jobs."""

    def __init__(self, *, root: Path | None = None):
        self.root = guard_root() if root is None else root
        self.descriptor = None
        self.path = None
        with _gate(self.root):
            fd, path = tempfile.mkstemp(prefix="active-", suffix=".lock", dir=self.root)
            self.path = Path(path)
            try:
                if not _lock(fd):
                    raise ModuleError("cannot acquire package activity lease")
                self.fingerprint = environment_fingerprint()
                self.descriptor = fd
            except BaseException:
                os.close(fd)
                self.path.unlink(missing_ok=True)
                raise

    def check(self) -> None:
        if self.descriptor is None:
            raise ModuleError("package activity is already closed")
        if environment_fingerprint() != self.fingerprint:
            raise ModuleError("installed packages changed outside Workbench during active work; stop and restart this process before further work (no rollback was attempted)")

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
            self.path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                self.check()
        finally:
            self.close()


@contextmanager
def package_change(*, root: Path | None = None):
    selected = guard_root() if root is None else root
    with _gate(selected):
        for path in selected.glob("active-*.lock"):
            descriptor = _open_lock(path)
            try:
                if not _lock(descriptor):
                    raise ModuleError("package changes are blocked by active Workbench commands or services; stop them first")
            finally:
                os.close(descriptor)
            # Stale leases are crash debris, not retained workspace resources.
            path.unlink(missing_ok=True)
        yield
