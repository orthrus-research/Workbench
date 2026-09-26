"""Core supervision and physical custody for an owner's historical tool log."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile

from workbench_api.processes import LoggedProcessResult, ProcessError

from . import check_storage
from .durable_records import (
    private_record_lock, publish_immutable_bytes, read_bounded_single_link_bytes,
)
from .host_filesystem import fsync_directory, secure_private_path
from .sessions import EphemeralSession


class _MergedLog(EphemeralSession):
    def __init__(self, output, header: bytes, limit: int):
        super().__init__()
        self.output = output
        self.limit = limit
        self.size = 0
        self.digest = sha256()
        self._write(header)

    def _write(self, data: bytes) -> None:
        if self.size + len(data) > self.limit:
            raise ProcessError("logged process output exceeded its byte bound")
        if self.output.write(data) != len(data):
            raise ProcessError("logged process output could not be retained")
        self.size += len(data)
        self.digest.update(data)

    def write_raw(self, stream, data):
        if stream in {"stdout", "stderr"}:
            self._write(data)
        return super().write_raw(stream, data)


def _existing(path: Path, limit: int) -> tuple[bytes, tuple[int, int]] | None:
    if not path.exists() and not path.is_symlink():
        return None
    selected = check_storage.ordinary(path)
    info = selected.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProcessError("historical process log is not an independent file")
    raw = read_bounded_single_link_bytes(selected, byte_limit=limit)
    if selected.stat().st_dev != info.st_dev or selected.stat().st_ino != info.st_ino:
        raise ProcessError("historical process log changed while reading")
    return raw, (info.st_dev, info.st_ino)


def _preserve_previous(path: Path, previous: bytes, limit: int) -> None:
    history = path.parent / "history"
    if history.exists() or history.is_symlink():
        check_storage.ordinary(history, directory=True)
    else:
        history.mkdir(mode=0o700)
    secure_private_path(history, directory=True)
    archived = history / f"{path.stem}-{sha256(previous).hexdigest()[:16]}{path.suffix}"
    if archived.exists() or archived.is_symlink():
        check_storage.ordinary(archived)
        secure_private_path(archived, directory=False)
    publish_immutable_bytes(archived, previous, byte_limit=limit, idempotent=True)


def execute_logged(
    argv, *, cwd, log_path, environment, cancelled, timeout_seconds, output_limit,
) -> LoggedProcessResult:
    """Retain a bounded merged log, including nonzero exits and failed runs.

    A crash leaves its private pending log visible and blocks a silent retry.
    Existing V1/V2 log names and history names remain valid readers.
    """

    if not argv or not Path(argv[0]).is_absolute():
        raise ProcessError("logged process executable must be an explicit absolute path")
    path = Path(log_path)
    if not path.is_absolute() or not path.name or path.name in {".", ".."}:
        raise ProcessError("logged process requires an absolute log path")
    parent = check_storage.ordinary(path.parent, directory=True)
    secure_private_path(parent, directory=True)
    with private_record_lock(parent / f".{path.name}.core.lock"):
        pending_prefix = f".{path.name}."
        if any(child.name.startswith(pending_prefix) and child.name.endswith(".pending")
               for child in parent.iterdir()):
            raise ProcessError("earlier logged process has an unpublished pending log")
        previous = _existing(path, output_limit)
        if previous is not None:
            _preserve_previous(path, previous[0], output_limit)
        header = (json.dumps(
            {"command": list(argv), "cwd": str(cwd)},
            ensure_ascii=False, sort_keys=True,
        ) + "\n").encode("utf-8")
        descriptor, name = tempfile.mkstemp(prefix=pending_prefix, suffix=".pending", dir=parent)
        pending = Path(name)
        code = None
        failure = None
        try:
            with os.fdopen(descriptor, "wb") as output:
                session = _MergedLog(output, header, output_limit)
                from .tool_process import _run
                try:
                    code = _run(
                        argv, cwd=cwd, stdin=b"", environment=environment,
                        cancelled=cancelled, timeout_seconds=timeout_seconds,
                        session=session,
                    )
                except BaseException as exc:
                    failure = exc
                output.flush()
                os.fsync(output.fileno())
            secure_private_path(pending, directory=False)
            observed = _existing(path, output_limit)
            if ((previous is None) != (observed is None)
                    or previous is not None and observed is not None and observed != previous):
                raise ProcessError("historical process log changed before publication")
            if previous is None:
                os.link(pending, path)
                pending.unlink()
            else:
                os.replace(pending, path)
            fsync_directory(parent)
            retained = _existing(path, output_limit)
            if retained is None or sha256(retained[0]).hexdigest() != session.digest.hexdigest():
                raise ProcessError("published process log changed during readback")
            if failure is not None:
                raise failure
            if code is None:
                raise ProcessError("logged process did not report an exit status")
            return LoggedProcessResult(code, path, session.digest.hexdigest(), session.size)
        except BaseException:
            # Never discard an unpublished log. It may contain the only crash
            # evidence; a later invocation must stop for explicit review.
            raise
