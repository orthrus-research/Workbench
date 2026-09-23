"""Native tools use the existing Core process supervisor with ephemeral I/O."""

from pathlib import Path
import tempfile
import time

from workbench_api.processes import ProcessError, ProcessResult
from .render import Renderer
from .runner import RunnerError, supervise_process
from .sessions import EphemeralSession
from . import process_capture
from .process_capture import open_output
from .host_filesystem import secure_private_path


class _Capture(EphemeralSession):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit
        self.output = {"stdout": bytearray(), "stderr": bytearray()}

    def write_raw(self, stream, data):
        if stream in self.output:
            if self.limit is not None and len(self.output[stream]) + len(data) > self.limit:
                raise ProcessError("native-tool output exceeded its byte bound")
            self.output[stream].extend(data)
        return super().write_raw(stream, data)


class _Control(Renderer):
    def __init__(self, cancelled, timeout):
        self.cancelled = cancelled
        self.deadline = None if timeout is None else time.monotonic() + timeout
        self.reason = None

    def consume(self, event):
        pass  # Domain output is returned verbatim, not interpreted as console health.

    def pulse(self):
        if self.cancelled.is_set():
            self.reason = "cancelled"
        elif self.deadline is not None and time.monotonic() >= self.deadline:
            self.reason = "timed out"
        if self.reason:
            self.cancellation_requested = True
            self.force_requested = True


def execute(argv, *, cwd, stdin, environment, cancelled, timeout_seconds, output_limit):
    session = _Capture(output_limit)
    code = _run(argv, cwd=cwd, stdin=stdin, environment=environment, cancelled=cancelled,
                timeout_seconds=timeout_seconds, session=session)
    return ProcessResult(code, bytes(session.output["stdout"]), bytes(session.output["stderr"]))


def capture(argv, *, directory, binding, cwd, stdin, environment, cancelled, timeout_seconds, output_limit):
    _validate(argv, cancelled)
    session = process_capture.FileCapture(directory, binding, output_limit)
    try:
        code = _run(argv, cwd=cwd, stdin=stdin, environment=environment, cancelled=cancelled,
                    timeout_seconds=timeout_seconds, session=session)
    except BaseException as exc:
        try:
            session.commit(failure={"type": type(exc).__name__, "message": str(exc)})
        except (OSError, ValueError, ProcessError) as retention_error:
            exc.add_note("Partial native capture could not be committed: " + str(retention_error))
        raise
    record = session.commit(exit_code=code)
    return process_capture.result(directory, record)


def _validate(argv, cancelled):
    if cancelled.is_set():
        raise ProcessError("native-tool invocation was cancelled before launch")
    if not argv or not Path(argv[0]).is_absolute():
        raise ProcessError("native-tool executable must be an explicit absolute path")


def _run(argv, *, cwd, stdin, environment, cancelled, timeout_seconds, session):
    _validate(argv, cancelled)
    renderer = _Control(cancelled, timeout_seconds)
    # The random private directory holds only this request; Core removes it on
    # every exit, including cancellation. No source file or result is deleted.
    with tempfile.TemporaryDirectory(prefix="workbench-tool-") as temporary:
        secure_private_path(Path(temporary), directory=True)
        input_path = Path(temporary) / "stdin"
        with input_path.open("xb") as stream:
            stream.write(stdin)
        secure_private_path(input_path, directory=False)
        try:
            result = supervise_process(
                argv, cwd=cwd, root=cwd, session=session, renderer=renderer,
                source="native-tool", environment=environment, input_file=input_path,
                input_limit=None,  # The API port already applied the caller's selected input policy.
                interrupt_grace_seconds=0.1, terminate_grace_seconds=1,
                require_group_closure=True,
                output_mode="raw",
            )
        except RunnerError as exc:
            raise ProcessError(str(exc)) from exc
    if renderer.reason or result.cancellation:
        raise ProcessError("native-tool invocation " + (renderer.reason or "cancelled"))
    if result.process_exit_code is None:
        raise ProcessError("native-tool process did not report an exit status")
    return result.process_exit_code
