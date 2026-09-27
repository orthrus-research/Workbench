"""Opt-in Core scratch custody for Blueprints simulation V2 observations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Protocol, Sequence


class SimulationScratchError(ValueError):
    """Core cannot issue or retain the requested simulation scratch lease."""


@dataclass(frozen=True, slots=True)
class SimulationScratchReference:
    lease_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class SimulationGitCapture:
    exit_code: int
    stdout: bytes
    stderr: bytes
    capture_id: str
    attempt_name: str


@dataclass(frozen=True, slots=True)
class SimulationSandboxCapture:
    exit_code: int | None
    timed_out: bool
    output_limited: bool
    stdout_sha256: str
    stderr_sha256: str
    capture_id: str
    attempt_name: str


class SimulationScratchHost(Protocol):
    def allocate(self, *, parent: Path, plan_id: str) -> ContextManager[SimulationScratchReference]: ...
    def capture_git(
        self, reference: SimulationScratchReference, argv: Sequence[str], *,
        cwd: Path, stdin: bytes,
    ) -> SimulationGitCapture: ...

    def capture_sandbox(
        self, reference: SimulationScratchReference, argv: Sequence[str], *,
        cwd: Path, timeout_seconds: int, max_output_bytes: int,
    ) -> SimulationSandboxCapture: ...


_bound: ContextVar[SimulationScratchHost | None] = ContextVar(
    "workbench_simulation_scratch", default=None,
)
_git_capture: ContextVar[tuple[SimulationScratchReference, list[dict[str, str]]] | None] = ContextVar(
    "workbench_simulation_git_capture", default=None,
)
_sandbox_capture: ContextVar[tuple[SimulationScratchReference, list[dict[str, str]]] | None] = ContextVar(
    "workbench_simulation_sandbox_capture", default=None,
)


@contextmanager
def simulation_scratch_scope(host: SimulationScratchHost) -> Iterator[None]:
    token = _bound.set(host)
    try:
        yield
    finally:
        _bound.reset(token)


def allocate_simulation_scratch(
    *, parent: Path, plan_id: str,
) -> ContextManager[SimulationScratchReference]:
    host = _bound.get()
    if host is None:
        raise SimulationScratchError(
            "Blueprints simulation V2 requires a bound Core scratch host"
        )
    return host.allocate(parent=parent, plan_id=plan_id)


@contextmanager
def simulation_git_capture_scope(
    reference: SimulationScratchReference, captures: list[dict[str, str]],
) -> Iterator[None]:
    """Route only an opted-in V2 simulation's Git children through Core."""

    token = _git_capture.set((reference, captures))
    try:
        yield
    finally:
        _git_capture.reset(token)


def simulation_git_capture_active() -> bool:
    return _git_capture.get() is not None


def capture_simulation_git(
    argv: Sequence[str], *, cwd: Path, stdin: bytes = b"",
) -> SimulationGitCapture:
    active = _git_capture.get()
    host = _bound.get()
    if active is None or host is None or not callable(getattr(host, "capture_git", None)):
        raise SimulationScratchError("Blueprints V2 Git capture requires its Core lease host")
    reference, captures = active
    captured = host.capture_git(reference, tuple(argv), cwd=cwd, stdin=stdin)
    if not isinstance(captured, SimulationGitCapture):
        raise SimulationScratchError("Core returned an invalid Git capture")
    captures.append({
        "attempt": captured.attempt_name,
        "capture_id": captured.capture_id,
    })
    return captured


@contextmanager
def simulation_sandbox_capture_scope(
    reference: SimulationScratchReference, captures: list[dict[str, str]],
) -> Iterator[None]:
    """Route only opted-in V2 Bubblewrap children through Core."""

    token = _sandbox_capture.set((reference, captures))
    try:
        yield
    finally:
        _sandbox_capture.reset(token)


def simulation_sandbox_capture_active() -> bool:
    return _sandbox_capture.get() is not None


def capture_simulation_sandbox(
    argv: Sequence[str], *, cwd: Path, timeout_seconds: int,
    max_output_bytes: int, stage_id: str, command_sha256: str,
) -> SimulationSandboxCapture:
    active = _sandbox_capture.get()
    host = _bound.get()
    if active is None or host is None or not callable(getattr(host, "capture_sandbox", None)):
        raise SimulationScratchError("Blueprints V2 Bubblewrap capture requires its Core lease host")
    reference, captures = active
    captured = host.capture_sandbox(
        reference, tuple(argv), cwd=cwd,
        timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes,
    )
    if not isinstance(captured, SimulationSandboxCapture):
        raise SimulationScratchError("Core returned an invalid Bubblewrap capture")
    captures.append({
        "stage_id": stage_id, "command_sha256": command_sha256,
        "attempt": captured.attempt_name, "capture_id": captured.capture_id,
        "outcome": (
            "timed-out" if captured.timed_out else
            "output-limited" if captured.output_limited else "exited"
        ),
    })
    return captured


__all__ = [
    "SimulationScratchError", "SimulationScratchHost", "SimulationScratchReference",
    "SimulationGitCapture", "allocate_simulation_scratch", "simulation_scratch_scope",
    "simulation_git_capture_scope", "simulation_git_capture_active", "capture_simulation_git",
    "SimulationSandboxCapture", "simulation_sandbox_capture_scope",
    "simulation_sandbox_capture_active", "capture_simulation_sandbox",
]
