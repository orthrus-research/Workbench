"""Core-bound custody for one managed attempt's execution workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from .managed_attempts import ManagedAttemptReference


class CaptureWorkspaceHostError(OSError):
    """The selected Core host cannot provide attempt-bound workspace custody."""


class CaptureExecutionWorkspace(Protocol):
    def copy_runtime(
        self, rows: list[dict], *, cancelled: Callable[[], bool] = lambda: False,
    ) -> Path: ...

    def read_optional(self, relative: str) -> bytes | None: ...

    def replace_file(
        self, relative: str, raw: bytes, *, expected_sha256: str | None = None,
    ) -> dict: ...

    def inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]: ...


class CapturePreparedWorkspace(Protocol):
    def materialize_inputs(
        self, *, runtime_root: Path, runtime_files: list[dict],
        java_home: Path, java_files: list[dict],
        source_files: dict[str, bytes], source_rows: list[dict],
        source_roots: list[str], runtime_exclude: list[str] | tuple[str, ...] = (),
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict: ...

    def create_from_build(self, relative: str, artifact: dict) -> dict: ...

    def inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]: ...

    def java_inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]: ...


class CaptureWorkspaces(Protocol):
    def execution(self, attempt: ManagedAttemptReference) -> CaptureExecutionWorkspace: ...

    def prepared(self, attempt: ManagedAttemptReference) -> CapturePreparedWorkspace: ...


_host: CaptureWorkspaces | None = None


def bind_capture_workspaces(host: CaptureWorkspaces) -> None:
    global _host
    if (not callable(getattr(host, "execution", None))
            or not callable(getattr(host, "prepared", None))):
        raise CaptureWorkspaceHostError("Core capture workspace host is incomplete")
    if _host is not None and _host is not host:
        raise CaptureWorkspaceHostError("a different Core capture workspace host is already bound")
    _host = host


def capture_execution_workspace(attempt: ManagedAttemptReference) -> CaptureExecutionWorkspace:
    if _host is None:
        raise CaptureWorkspaceHostError("no Core capture workspace host is bound")
    return _host.execution(attempt)


def capture_prepared_workspace(attempt: ManagedAttemptReference) -> CapturePreparedWorkspace:
    if _host is None:
        raise CaptureWorkspaceHostError("no Core capture workspace host is bound")
    return _host.prepared(attempt)


__all__ = [
    "CaptureExecutionWorkspace", "CapturePreparedWorkspace", "CaptureWorkspaceHostError",
    "CaptureWorkspaces", "bind_capture_workspaces", "capture_execution_workspace",
    "capture_prepared_workspace",
]
