"""Core-bound custody for one managed attempt's execution workspace."""

from __future__ import annotations

from typing import Callable, Protocol

from .managed_attempts import ManagedAttemptReference


class CaptureWorkspaceHostError(OSError):
    """The selected Core host cannot provide attempt-bound workspace custody."""


class CaptureExecutionWorkspace(Protocol):
    def read_optional(self, relative: str) -> bytes | None: ...

    def replace_file(
        self, relative: str, raw: bytes, *, expected_sha256: str | None = None,
    ) -> dict: ...

    def inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]: ...


class CaptureWorkspaces(Protocol):
    def execution(self, attempt: ManagedAttemptReference) -> CaptureExecutionWorkspace: ...


_host: CaptureWorkspaces | None = None


def bind_capture_workspaces(host: CaptureWorkspaces) -> None:
    global _host
    if not callable(getattr(host, "execution", None)):
        raise CaptureWorkspaceHostError("Core capture workspace host is incomplete")
    if _host is not None and _host is not host:
        raise CaptureWorkspaceHostError("a different Core capture workspace host is already bound")
    _host = host


def capture_execution_workspace(attempt: ManagedAttemptReference) -> CaptureExecutionWorkspace:
    if _host is None:
        raise CaptureWorkspaceHostError("no Core capture workspace host is bound")
    return _host.execution(attempt)


__all__ = [
    "CaptureExecutionWorkspace", "CaptureWorkspaceHostError", "CaptureWorkspaces",
    "bind_capture_workspaces", "capture_execution_workspace",
]
