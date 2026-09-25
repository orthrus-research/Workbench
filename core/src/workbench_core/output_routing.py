"""Small, module-grouped operational logs and Core-selected output paths."""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import RLock
from typing import Any, Mapping, TextIO
from uuid import uuid4

from workbench_api import ModuleError

from .setup_cli import _state_root, setup_record_lock


FORMAT = "workbench-module-log-event-v1"
OUTPUT_ROLES = frozenset({
    "logs", "artifacts", "evidence", "cache", "blueprint_sessions", "fixture_instances"
})
MAX_STREAM_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *[f"COM{i}" for i in range(1, 10)],
    *[f"LPT{i}" for i in range(1, 10)],
})
_CURRENT_RUN: ContextVar[OutputInvocation | None] = ContextVar("workbench_module_log_run", default=None)
_STREAM_LOCK = RLock()
_active_streams = 0
_stream_proxies: tuple[_StreamProxy, _StreamProxy] | None = None
_base_streams: tuple[TextIO, TextIO] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _private_directory(path: Path) -> None:
    """Create only non-redirecting paths; keep new output directories private."""

    if not path.is_absolute():
        raise ModuleError("output root must be absolute")
    _state_root(path)
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        if not cursor.exists() and not cursor.is_symlink():
            try:
                cursor.mkdir(mode=0o700)
            except FileExistsError:
                pass  # Another writer may have created it; validate below.
        _state_root(cursor)
    if not path.is_dir():
        raise ModuleError(f"output directory is unavailable: {path}")


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _file_observation(path: Path) -> dict[str, Any]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ModuleError(f"output file changed type: {path}")
    digest = sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or _file_identity(opened) != _file_identity(before):
            raise ModuleError(f"output file changed before observation: {path}")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
        if _file_identity(os.fstat(stream.fileno())) != _file_identity(opened) or _file_identity(path.lstat()) != _file_identity(before):
            raise ModuleError(f"output file changed during observation: {path}")
    return {"path": str(path), "bytes": size, "sha256": "sha256:" + digest.hexdigest()}


def _append_event(path: Path, event: Mapping[str, Any], *, durable: bool = False) -> None:
    """Append one JSON line while cooperating Workbench processes hold a file lock."""

    _private_directory(path.parent)
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    with setup_record_lock(path):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ModuleError(f"module log is not an ordinary file: {path}")
            if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)
            before = info.st_size
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("module log append made no progress")
                    remaining = remaining[written:]
                if durable:
                    os.fsync(descriptor)
            except BaseException:
                os.ftruncate(descriptor, before)
                raise
        finally:
            os.close(descriptor)


class _StreamProxy:
    """Send current-context Python text to its run and preserve terminal output."""

    def __init__(self, terminal: TextIO, stream: str):
        self.terminal = terminal
        self.stream = stream

    def write(self, value: str) -> int:
        result = self.terminal.write(value)
        run = _CURRENT_RUN.get()
        if run is not None:
            run._capture(self.stream, value)
        return result

    def flush(self) -> None:
        self.terminal.flush()
        run = _CURRENT_RUN.get()
        if run is not None:
            run._flush_text(self.stream)

    def __getattr__(self, name: str):
        return getattr(self.terminal, name)


def _install_streams() -> None:
    global _active_streams, _stream_proxies, _base_streams
    with _STREAM_LOCK:
        if _active_streams == 0:
            _base_streams = (sys.stdout, sys.stderr)
            _stream_proxies = (_StreamProxy(sys.stdout, "stdout"), _StreamProxy(sys.stderr, "stderr"))
            sys.stdout, sys.stderr = _stream_proxies
        _active_streams += 1


def _release_streams(token: Token) -> None:
    global _active_streams, _stream_proxies, _base_streams
    _CURRENT_RUN.reset(token)
    with _STREAM_LOCK:
        _active_streams -= 1
        if _active_streams == 0:
            assert _stream_proxies is not None and _base_streams is not None
            if sys.stdout is _stream_proxies[0]:
                sys.stdout = _base_streams[0]
            if sys.stderr is _stream_proxies[1]:
                sys.stderr = _base_streams[1]
            _stream_proxies = None
            _base_streams = None


class OutputInvocation:
    """One command's events, all appended to its module's daily log."""

    def __init__(
        self, locations: Mapping[str, Path], module_id: str, capability_id: str,
        *, workspace: Path | None = None,
    ):
        if not _IDENTIFIER.fullmatch(module_id) or not _IDENTIFIER.fullmatch(capability_id):
            raise ModuleError("module and capability identifiers must be portable")
        self.locations = locations
        self.module_id = module_id
        self.capability_id = capability_id
        self.workspace = workspace
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ-") + uuid4().hex[:12]
        self.exit_code: int | None = None
        self.allocations: dict[tuple[str, str], Path] = {}
        self.saved = {"stdout": 0, "stderr": 0}
        self.omitted = {"stdout": 0, "stderr": 0}
        self._pending = {"stdout": "", "stderr": ""}
        self._capture_lock = RLock()
        self._active = False
        self._token: Token | None = None

    def _event(self, kind: str, *, durable: bool = False, **fields: Any) -> None:
        timestamp = _now()
        path = Path(self.locations["logs"]) / "modules" / self.module_id / f"{timestamp[:10]}.jsonl"
        _append_event(path, {
            "format": FORMAT,
            "schema_version": 1,
            "timestamp": timestamp,
            "module_id": self.module_id,
            "capability_id": self.capability_id,
            "run_id": self.run_id,
            "kind": kind,
            **fields,
        }, durable=durable)

    def __enter__(self) -> "OutputInvocation":
        self._event("started", workspace=str(self.workspace) if self.workspace is not None else None, durable=True)
        _install_streams()
        self._token = _CURRENT_RUN.set(self)
        self._active = True
        return self

    def _capture(self, stream: str, value: str) -> None:
        with self._capture_lock:
            if not self._active:
                return
            raw = value.encode("utf-8", errors="replace")
            remaining = max(0, MAX_STREAM_BYTES - self.saved[stream])
            retained = raw[:remaining].decode("utf-8", errors="ignore")
            retained_bytes = len(retained.encode("utf-8"))
            self.saved[stream] += retained_bytes
            self.omitted[stream] += len(raw) - retained_bytes
            if retained_bytes < len(raw):
                self.saved[stream] = MAX_STREAM_BYTES
            self._pending[stream] += retained
            while self._pending[stream]:
                newline = self._pending[stream].find("\n")
                if newline < 0 and len(self._pending[stream]) < 65536:
                    break
                end = newline + 1 if 0 <= newline < 65536 else 65536
                self._event("python_text", stream=stream, text=self._pending[stream][:end])
                self._pending[stream] = self._pending[stream][end:]

    def _flush_text(self, stream: str) -> None:
        with self._capture_lock:
            if self._active and self._pending[stream]:
                self._event("python_text", stream=stream, text=self._pending[stream])
                self._pending[stream] = ""

    def output_path(self, role: str, name: str) -> Path:
        if role not in OUTPUT_ROLES or role not in self.locations:
            raise ModuleError(f"unsupported output role: {role}")
        if (
            type(name) is not str
            or _NAME.fullmatch(name) is None
            or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise ModuleError("output name must be one portable filename")
        if (role, name) in self.allocations:
            raise ModuleError(f"output destination was already requested: {role}/{name}")
        directory = Path(self.locations[role]) / "outputs" / role / self.module_id
        _private_directory(directory)
        target = directory / f"{self.run_id}-{name}"
        if target.exists() or target.is_symlink():
            raise ModuleError(f"output destination already exists: {target}")
        self.allocations[(role, name)] = target
        return target

    def __exit__(self, error_type, error, traceback) -> bool:
        assert self._token is not None
        output_error: Exception | None = None
        outputs = []
        try:
            with self._capture_lock:
                try:
                    self._flush_text("stdout")
                    self._flush_text("stderr")
                finally:
                    # A child context can outlive this invocation while another
                    # invocation still has the process streams installed.
                    self._active = False
            for (role, name), path in sorted(self.allocations.items()):
                try:
                    observation = _file_observation(path)
                except FileNotFoundError:
                    observation = None
                    output_error = output_error or ModuleError(f"requested output was not written: {path}")
                except Exception as exc:
                    observation = None
                    output_error = output_error or exc
                outputs.append({"role": role, "name": name, "path": str(path), "file": observation})
            failure = error if error_type is not None else output_error
            outcome = (
                "interrupted" if error_type is KeyboardInterrupt else
                "failed" if failure is not None or self.exit_code != 0 else "completed"
            )
            effective_exit = (
                130 if outcome == "interrupted" else
                2 if failure is not None else self.exit_code
            )
            self._event(
                "finished", durable=True, outcome=outcome, exit_code=effective_exit,
                error_type=type(failure).__name__ if failure is not None else None,
                error_message=str(failure)[:1000] if failure is not None else None,
                omitted_bytes=dict(self.omitted), outputs=outputs,
            )
        finally:
            _release_streams(self._token)
            self._token = None
        if error_type is None and output_error is not None:
            raise output_error
        return False


__all__ = ["FORMAT", "OUTPUT_ROLES", "OutputInvocation"]
