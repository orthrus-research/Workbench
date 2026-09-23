"""Bounded native-tool process port. Modules never import Core's supervisor."""

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import ContextManager, Mapping, Protocol, Sequence


class ProcessError(RuntimeError):
    """The host could not complete the requested bounded invocation."""


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class ProcessOutput:
    """A Core-captured file; open through the host to verify its exact bytes."""

    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class CapturedProcessResult:
    exit_code: int
    capture_id: str
    binding: str
    stdout: ProcessOutput
    stderr: ProcessOutput

    @property
    def reference(self):
        return {"format": "workbench-process-capture-v1", "id": self.capture_id,
                "binding": self.binding}


class ProcessOutputReader(Protocol):
    """Forward-only binary reads; Core verifies the remainder on successful close."""

    def read(self, size: int = -1) -> bytes: ...


class ProcessHost(Protocol):
    def execute(
        self, argv: Sequence[str], *, cwd: Path, stdin: bytes,
        environment: Mapping[str, str], cancelled: Event,
        timeout_seconds: float | None, output_limit: int | None,
    ) -> ProcessResult: ...

    def capture(self, argv: Sequence[str], *, directory: Path, binding: str,
                cwd: Path, stdin: bytes, environment: Mapping[str, str], cancelled: Event,
                timeout_seconds: float | None, output_limit: int | None) -> CapturedProcessResult: ...

    def open_output(self, output: ProcessOutput) -> ContextManager[ProcessOutputReader]: ...


_host: ProcessHost | None = None


def bind_process_host(host: ProcessHost) -> None:
    global _host
    if not callable(getattr(host, "execute", None)):
        raise ProcessError("process host does not implement execute")
    if _host is not None and _host is not host:
        raise ProcessError("a different process host is already bound")
    _host = host


def execute_process(
    argv: Sequence[str], *, cwd: Path, stdin: bytes,
    environment: Mapping[str, str], cancelled: Event,
    timeout_seconds: float | None = 40, output_limit: int | None = 1024 * 1024,
    input_limit: int | None = 1024 * 1024,
) -> ProcessResult:
    _validate(stdin, cancelled, timeout_seconds, output_limit, input_limit)
    return _host.execute(
        tuple(argv), cwd=cwd, stdin=stdin, environment=dict(environment),
        cancelled=cancelled, timeout_seconds=timeout_seconds, output_limit=output_limit,
    )


def capture_process(
    argv: Sequence[str], *, directory: Path, binding: str, cwd: Path, stdin: bytes,
    environment: Mapping[str, str], cancelled: Event,
    timeout_seconds: float | None = 40, output_limit: int | None = 1024 * 1024,
    input_limit: int | None = 1024 * 1024,
) -> CapturedProcessResult:
    """Capture into a new child of owner-managed private storage.

    Core seals complete streams before returning. Failure leaves explicit partial
    evidence in that same owned directory, never a successful output reference.
    The owner includes the capture in its existing retention/export lifecycle.
    """
    _validate(stdin, cancelled, timeout_seconds, output_limit, input_limit)
    if not callable(getattr(_host, "capture", None)):
        raise ProcessError("process host does not implement file capture")
    return _host.capture(tuple(argv), directory=directory, binding=binding, cwd=cwd,
                         stdin=stdin, environment=dict(environment), cancelled=cancelled,
                         timeout_seconds=timeout_seconds, output_limit=output_limit)


def open_process_output(output: ProcessOutput) -> ContextManager[ProcessOutputReader]:
    """Read an exact capture through Core; successful close verifies all bytes."""
    if _host is None or not callable(getattr(_host, "open_output", None)):
        raise ProcessError("process host does not implement captured output reads")
    return _host.open_output(output)


def _validate(stdin, cancelled, timeout_seconds, output_limit, input_limit):
    if _host is None:
        raise ProcessError("no process host is bound; invoke this module through Workbench Core")
    # None explicitly suspends a resource target; cancellation remains required.
    if (timeout_seconds is not None and not 0 < timeout_seconds <= 300
            or output_limit is not None and not 1 <= output_limit <= 4 * 1024 * 1024
            or input_limit is not None and not 1 <= input_limit <= 1024 * 1024):
        raise ProcessError("native-tool bounds exceed the API policy")
    if not isinstance(stdin, bytes) or input_limit is not None and len(stdin) > input_limit:
        raise ProcessError("native-tool input must be bytes within the selected limit")
    if cancelled.is_set():
        raise ProcessError("native-tool invocation was cancelled before launch")
