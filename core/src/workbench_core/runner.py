"""Lossless process collection and truthful live-console lifecycle handling."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

from workbench_api.events import EventNormalizer, IncrementalEventDecoder
from workbench_api.profile_extensions import event_classifiers
from workbench_core.render import Renderer
from workbench_core.sessions import EphemeralSession, RawLocator, RetainedSession
from workbench_core.source import SourceIndex


_PROCESS_GATE = Path(__file__).with_name("process_gate.py")
_PROCESS_GATE_RELEASE = b"WORKBENCH-GO\n"
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


class RunnerError(RuntimeError):
    """A live-console collection or process-supervision operation failed."""


@dataclass(frozen=True)
class RunResult:
    process_exit_code: int | None
    effective_exit_code: int
    state: str
    outcome: str
    cancellation: str | None
    event_count: int
    outcome_failure_events: int
    session: Mapping[str, Any]


class _EventSink:
    def __init__(
        self,
        *,
        root: Path,
        source: str,
        session: RetainedSession | EphemeralSession,
        renderer: Renderer,
    ) -> None:
        self.root = root
        self.source = source
        self.session = session
        self.renderer = renderer
        self.normalizer = EventNormalizer(root=root, classifiers=event_classifiers())
        self.source_index = SourceIndex(root)
        self.sequence = 0
        self.started_ns = time.monotonic_ns()
        self.event_count = 0
        self.outcome_failures = 0
        self._system_line = 1

    def accept(self, event: Any) -> dict[str, Any]:
        value = event.as_dict() if hasattr(event, "as_dict") else dict(event)
        sequence = int(value.get("sequence", 0))
        if sequence <= self.sequence:
            raise RunnerError("normalizer event sequence did not preserve append order")
        self.sequence = sequence
        retained = self.session.record_event(value)
        self.renderer.consume(self.source_index.presentation_event(retained))
        self.event_count += 1
        self.outcome_failures += int(bool(value.get("outcome_failure")))
        return value

    def supervisor_stage(self, stage: str, state: str) -> dict[str, Any]:
        """Retain supervisor lifecycle text, then normalize it like every row."""

        message = f"[workbench] stage {stage} {state}"
        payload = (message + "\n").encode("utf-8")
        raw = self.session.write_raw("system", payload)
        event = self.normalizer.normalize_stage(
            stage,
            state,
            source="workbench",
            stream="system",
            raw_locator={
                "artifact": raw.path if self.session.retained else None,
                "byte_start": raw.byte_start,
                "byte_end": raw.byte_end,
                "line": self._system_line,
                "chunk": 1,
                "boundary": "lf",
            },
        )
        self._system_line += 1
        return self.accept(event)


def supervise_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    root: Path,
    session: RetainedSession | EphemeralSession,
    renderer: Renderer,
    source: str,
    environment: Mapping[str, str] | None = None,
    input_file: Path | None = None,
    input_limit: int | None = 1024 * 1024,
    capture_output: bool = True,
    interrupt_grace_seconds: float = 5.0,
    terminate_grace_seconds: float = 3.0,
    max_record_bytes: int = 256 * 1024,
    require_group_closure: bool = False,
    output_mode: str = "console",
) -> RunResult:
    """Supervise exact argv; raw mode captures bytes without console events.

    Retained console sessions always interpret output. Native-tool adapters use
    ephemeral sessions to own their protocol bytes and native result meaning.
    Both modes preserve supervisor lifecycle events and process custody.
    """

    try:
        if output_mode not in ("console", "raw"):
            raise ValueError("unknown process output mode")
        if output_mode == "raw" and session.retained:
            raise ValueError("raw process output requires an ephemeral session")
        command = _validate_process_inputs(argv, cwd)
        gate_input = []
        if input_file is not None:
            from .check_storage import ordinary

            selected_input = ordinary(input_file)
            if input_limit is not None and selected_input.stat().st_size > input_limit:
                raise RunnerError("process input exceeds its byte bound")
            from hashlib import sha256

            gate_input = [
                "--input-file",
                str(selected_input),
                sha256(selected_input.read_bytes()).hexdigest(),
            ]
    except Exception as exc:
        _finish_failed_session(session, message=str(exc))
        try:
            renderer.close()
        except Exception:
            pass
        raise RunnerError(f"live-console process validation failed: {exc}") from exc
    root = root.expanduser().resolve()
    sink = _EventSink(
        root=root,
        source=source,
        session=session,
        renderer=renderer,
    )
    context = {
        "command": shlex.join(_display_argv(command, root)),
        "retained": str(session.directory) if session.retained else None,
    }
    process: subprocess.Popen[bytes] | None = None
    cancellation: str | None = None
    process_exit: int | None = None
    if os.name == "nt":
        from .windows_process import PipeSelector, ProcessJob

        selector = PipeSelector()
    else:
        selector = selectors.DefaultSelector()
    process_job = None
    decoders: dict[str, IncrementalEventDecoder] | None = (
        {} if output_mode == "console" else None
    )
    try:
        renderer.start(context)
        if not capture_output and session.retained:
            session.value["limitations"].append(
                "Child output intentionally discarded at the launcher account boundary; this session records process lifecycle only."
            )
        child_environment = dict(os.environ if environment is None else environment)
        child_environment.setdefault("PYTHONUNBUFFERED", "1")
        child_environment.setdefault("NO_COLOR", "1")
        process = subprocess.Popen(
            [sys.executable, str(_PROCESS_GATE), *gate_input, "--", *command],
            cwd=cwd,
            env=child_environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            bufsize=0,
            shell=False,
            start_new_session=os.name == "posix",
        )
        if os.name == "nt":
            # The gate cannot execute the target until it belongs to Core's
            # kill-on-close job. Assignment failure leaves that gate unreleased.
            process_job = ProcessJob(process)
            process._workbench_job = process_job
        process_group = process.pid if os.name == "posix" else None
        session.bind_process(process.pid, process_group)
        if process.poll() is not None:
            raise RunnerError("live-console launch gate exited before custody release")
        assert process.stdin is not None
        try:
            process.stdin.write(_PROCESS_GATE_RELEASE)
            process.stdin.flush()
        finally:
            process.stdin.close()
        session.open_raw("stdout")
        session.open_raw("stderr")
        sink.supervisor_stage("child-process", "started")
        for stream, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            if pipe is None:
                continue
            if os.name != "nt":
                os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, data=stream)
            if decoders is not None:
                decoders[stream] = IncrementalEventDecoder(
                    sink.normalizer,
                    source=source,
                    stream=stream,
                    max_record_bytes=max_record_bytes,
                )

        cancel_deadline: float | None = None
        escalation = 0
        while _supervision_pending(selector, process, cancellation):
            if (require_group_closure and process.poll() is not None
                    and _exact_process_alive(process) and cancellation is None):
                # A descendant can retain stdout after its leader exits. Close
                # it before waiting for EOF, otherwise closure is unreachable.
                _terminate_exact_process(process, terminate_grace_seconds)
                cancellation = "descendants-terminated"
                cancel_deadline = time.monotonic() + terminate_grace_seconds
            if renderer.cancellation_requested and cancellation is None:
                cancellation = "interrupt-requested"
                _signal_exact_process(process, signal.SIGINT)
                cancel_deadline = time.monotonic() + interrupt_grace_seconds
                sink.supervisor_stage("cancellation", "running")
            if (
                cancellation is not None
                and _exact_process_alive(process)
                and cancel_deadline is not None
            ):
                if renderer.force_requested and escalation < 2:
                    escalation = 2
                    _signal_exact_process(process, _KILL_SIGNAL)
                    cancellation = "forced"
                    cancel_deadline = time.monotonic() + terminate_grace_seconds
                    sink.supervisor_stage("forced-termination", "failed")
                elif time.monotonic() >= cancel_deadline:
                    if escalation == 0:
                        escalation = 1
                        _signal_exact_process(process, signal.SIGTERM)
                        cancellation = "terminated"
                        cancel_deadline = time.monotonic() + terminate_grace_seconds
                        sink.supervisor_stage("termination-escalation", "running")
                    elif escalation == 1:
                        escalation = 2
                        _signal_exact_process(process, _KILL_SIGNAL)
                        cancellation = "forced"
                        cancel_deadline = time.monotonic() + terminate_grace_seconds
                        sink.supervisor_stage("forced-termination", "failed")
                    else:
                        raise RunnerError(
                            "cancelled process group remained alive after SIGKILL"
                        )

            _collect_ready(
                selector,
                decoders=decoders,
                sink=sink,
                session=session,
                timeout=0.1,
            )
            renderer.pulse()
        process_exit = process.wait()
        if require_group_closure and _exact_process_alive(process):
            _terminate_exact_process(process, terminate_grace_seconds)
            if _exact_process_alive(process):
                raise RunnerError("owned descendants remain after process closure")
            cancellation = cancellation or "descendants-terminated"
        if cancellation:
            sink.supervisor_stage("cancellation", "completed")
        elif process_exit == 0:
            sink.supervisor_stage("child-process", "completed")
        else:
            sink.supervisor_stage("child-process", "failed")
    except KeyboardInterrupt:
        if process is None:
            _finish_failed_session(session, message="interrupted before child launch")
            renderer.close()
            raise RunnerError(
                "live-console launch was interrupted before child startup"
            ) from None
        cancellation = "interrupt-requested"
        _signal_exact_process(process, signal.SIGINT)
        sink.supervisor_stage("cancellation", "running")
        deadline = time.monotonic() + interrupt_grace_seconds
        escalation = 0
        while _supervision_pending(selector, process, cancellation):
            if _exact_process_alive(process) and time.monotonic() >= deadline:
                if escalation == 0:
                    escalation = 1
                    cancellation = "terminated"
                    _signal_exact_process(process, signal.SIGTERM)
                    sink.supervisor_stage("termination-escalation", "running")
                    deadline = time.monotonic() + terminate_grace_seconds
                elif escalation == 1:
                    escalation = 2
                    cancellation = "forced"
                    _signal_exact_process(process, _KILL_SIGNAL)
                    sink.supervisor_stage("forced-termination", "failed")
                    deadline = time.monotonic() + terminate_grace_seconds
                else:
                    raise RunnerError(
                        "interrupted process group remained alive after SIGKILL"
                    )
            try:
                _collect_ready(
                    selector,
                    decoders=decoders,
                    sink=sink,
                    session=session,
                    timeout=0.1,
                )
            except KeyboardInterrupt:
                escalation = 2
                cancellation = "forced"
                _signal_exact_process(process, _KILL_SIGNAL)
                deadline = time.monotonic() + terminate_grace_seconds
        process_exit = process.wait()
        sink.supervisor_stage("cancellation", "completed")
    except Exception as exc:
        if process is not None:
            _terminate_exact_process(process, terminate_grace_seconds)
        _finish_failed_session(session, message=str(exc))
        renderer.close()
        raise RunnerError(f"live-console supervisor failed: {exc}") from exc
    finally:
        try:
            if process_job is not None:
                process_job.close()
        finally:
            if os.name != "nt":
                for key in list(selector.get_map().values()):
                    try:
                        key.fileobj.close()
                    except OSError:
                        pass
            try:
                selector.close()
            finally:
                if process is not None:
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()

    if cancellation:
        effective = 130
        state = "cancelled"
        outcome = "cancelled"
    elif process_exit not in (0, None):
        effective = _portable_exit(process_exit)
        state = "failed"
        outcome = "process-failed"
    elif sink.outcome_failures:
        effective = 1
        state = "failed"
        outcome = "observed-required-failure"
    else:
        effective = 0
        state = "complete"
        outcome = "complete"
    summary = {
        "outcome": outcome,
        "effective_exit_code": effective,
        "retained": str(session.directory) if session.retained else None,
        "event_count": sink.event_count,
        "outcome_failure_events": sink.outcome_failures,
    }
    manifest = _finish_session(
        session,
        renderer,
        state=state,
        process_exit_code=process_exit,
        effective_exit_code=effective,
        outcome=outcome,
        cancellation=cancellation,
    )
    _complete_renderer(renderer, summary)
    return RunResult(
        process_exit,
        effective,
        state,
        outcome,
        cancellation,
        sink.event_count,
        sink.outcome_failures,
        manifest,
    )


def ingest_files(
    paths: Iterable[Path],
    *,
    root: Path,
    session: RetainedSession | EphemeralSession,
    renderer: Renderer,
    max_record_bytes: int = 256 * 1024,
) -> RunResult:
    """Retain and normalize existing launch streams without executing a process."""

    supplied = list(paths)
    sink = _EventSink(
        root=root.expanduser().resolve(),
        source="file-ingest",
        session=session,
        renderer=renderer,
    )
    try:
        if not supplied:
            raise RunnerError("ingest requires at least one exact file")
        if len(supplied) > 15:
            raise RunnerError(
                "ingest accepts at most 15 files so the retained system stream "
                "and every input fit the V1 16-stream bound"
            )
        resolved = [path.expanduser().resolve(strict=True) for path in supplied]
        renderer.start(
            {
                "command": "ingest " + ", ".join(str(path) for path in resolved),
                "retained": str(session.directory) if session.retained else None,
            }
        )
        sink.supervisor_stage("file-ingest", "started")
        for index, path in enumerate(resolved, 1):
            stream = f"ingest-{index:03d}"
            session.open_raw(stream)
            decoder = IncrementalEventDecoder(
                sink.normalizer,
                source="file-ingest",
                stream=stream,
                max_record_bytes=max_record_bytes,
            )
            with path.open("rb", buffering=0) as source_file:
                while True:
                    chunk = source_file.read(64 * 1024)
                    if not chunk:
                        break
                    locator = session.write_raw(stream, chunk)
                    for event in _decoder_feed(decoder, chunk, locator):
                        sink.accept(event)
            for event in decoder.finish():
                sink.accept(event)
        sink.supervisor_stage("file-ingest", "completed")
    except Exception as exc:
        _finish_failed_session(session, message=str(exc))
        renderer.close()
        raise RunnerError(f"console ingestion failed: {exc}") from exc

    effective = 1 if sink.outcome_failures else 0
    state = "failed" if effective else "complete"
    outcome = "observed-required-failure" if effective else "complete"
    summary = {
        "outcome": outcome,
        "effective_exit_code": effective,
        "retained": str(session.directory) if session.retained else None,
        "event_count": sink.event_count,
        "outcome_failure_events": sink.outcome_failures,
    }
    manifest = _finish_session(
        session,
        renderer,
        state=state,
        process_exit_code=None,
        effective_exit_code=effective,
        outcome=outcome,
    )
    _complete_renderer(renderer, summary)
    return RunResult(
        None,
        effective,
        state,
        outcome,
        None,
        sink.event_count,
        sink.outcome_failures,
        manifest,
    )


def _decoder_feed(
    decoder: IncrementalEventDecoder, data: bytes, locator: RawLocator
) -> list[Any]:
    try:
        return list(
            decoder.feed(
                data,
                raw_path=locator.path,
                offset=locator.byte_start,
            )
        )
    except TypeError:
        # Keeps the supervisor compatible with the strict positional API used by
        # early V1 fixtures while retaining the same exact offsets.
        return list(decoder.feed(data, locator.path, locator.byte_start))


def _collect_ready(
    selector: selectors.BaseSelector,
    *,
    decoders: Mapping[str, IncrementalEventDecoder] | None,
    sink: _EventSink,
    session: RetainedSession | EphemeralSession,
    timeout: float,
) -> None:
    """Drain ready descriptors without ever coupling ingestion to viewport state."""

    for key, _ in selector.select(timeout=timeout):
        pipe = key.fileobj
        stream = str(key.data)
        try:
            chunk = (selector.read(key) if os.name == "nt"
                     else os.read(pipe.fileno(), 64 * 1024))
        except BlockingIOError:
            continue
        if not chunk:
            selector.unregister(pipe)
            pipe.close()
            if decoders is not None:
                for event in decoders[stream].finish():
                    sink.accept(event)
            continue
        locator = session.write_raw(stream, chunk)
        if decoders is not None:
            for event in _decoder_feed(decoders[stream], chunk, locator):
                sink.accept(event)


def _finish_failed_session(
    session: RetainedSession | EphemeralSession, *, message: str
) -> None:
    try:
        if session.retained and hasattr(session, "value"):
            session.value["limitations"].append(
                "Console supervision failed: " + _single_line(str(message), 2048)
            )
        session.finish(
            state="failed",
            process_exit_code=None,
            effective_exit_code=2,
            outcome="console-error",
            cancellation=None,
        )
    except Exception:
        session.abort(str(message))


def _complete_renderer(renderer: Renderer, summary: Mapping[str, Any]) -> None:
    """Render a terminal recap and restore terminal state without masking errors."""

    failure: Exception | None = None
    try:
        renderer.finish(summary)
    except Exception as exc:
        failure = exc
    try:
        renderer.close()
    except Exception as exc:
        if failure is None:
            failure = exc
    if failure is not None:
        raise RunnerError(
            f"live-console renderer failed after retained completion: {failure}"
        ) from failure


def _finish_session(
    session: RetainedSession | EphemeralSession,
    renderer: Renderer,
    **values: Any,
) -> dict[str, Any]:
    try:
        return session.finish(**values)
    except Exception as exc:
        try:
            renderer.close()
        except Exception:
            pass
        raise RunnerError(f"live-console session finalization failed: {exc}") from exc


def _signal_exact_process(
    process: subprocess.Popen[bytes], selected_signal: int
) -> None:
    try:
        job = getattr(process, "_workbench_job", None)
        if job is not None:
            job.terminate()
        elif os.name == "posix":
            # The group can outlive its leader. Never gate killpg on poll().
            os.killpg(process.pid, selected_signal)
        elif process.poll() is None:  # The unreleased gate if job setup failed.
            process.terminate()
    except ProcessLookupError:
        pass


def _exact_process_alive(process: subprocess.Popen[bytes]) -> bool:
    job = getattr(process, "_workbench_job", None)
    if job is not None:
        return job.alive()
    if os.name != "posix":  # pragma: no cover - platform dependent
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _supervision_pending(
    selector: selectors.BaseSelector,
    process: subprocess.Popen[bytes],
    cancellation: str | None,
) -> bool:
    return bool(
        selector.get_map()
        or process.poll() is None
        or (cancellation is not None and _exact_process_alive(process))
    )


def _terminate_exact_process(
    process: subprocess.Popen[bytes], grace_seconds: float
) -> None:
    """Best-effort bounded cleanup for the exact group, even after leader exit."""

    if _exact_process_alive(process):
        _signal_exact_process(process, signal.SIGTERM)
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while _exact_process_alive(process) and time.monotonic() < deadline:
            time.sleep(0.02)
    if _exact_process_alive(process):
        _signal_exact_process(process, _KILL_SIGNAL)
        deadline = time.monotonic() + max(0.1, grace_seconds)
        while _exact_process_alive(process) and time.monotonic() < deadline:
            time.sleep(0.02)
    if process.poll() is None:
        try:
            process.wait(timeout=max(0.1, grace_seconds))
        except subprocess.TimeoutExpired:
            pass


def _validate_process_inputs(argv: Sequence[str], cwd: Path) -> list[str]:
    if not argv:
        raise RunnerError("process argv cannot be empty")
    result: list[str] = []
    for index, argument in enumerate(argv):
        if not isinstance(argument, str) or (index == 0 and not argument):
            raise RunnerError(
                f"process argv item {index} is not text or is an empty executable"
            )
        if "\x00" in argument:
            raise RunnerError(f"process argv item {index} contains NUL")
        result.append(argument)
    resolved_cwd = cwd.expanduser().resolve(strict=True)
    if not resolved_cwd.is_dir():
        raise RunnerError(f"process working directory is not a directory: {cwd}")
    executable = Path(result[0]).expanduser()
    if (
        executable.is_absolute()
        or os.sep in result[0]
        or (os.altsep is not None and os.altsep in result[0])
    ):
        candidate = (
            executable if executable.is_absolute() else resolved_cwd / executable
        )
        try:
            resolved_executable = candidate.resolve(strict=True)
        except OSError as exc:
            raise RunnerError(
                f"process executable is unavailable: {result[0]}"
            ) from exc
        if not resolved_executable.is_file() or not os.access(
            resolved_executable, os.X_OK
        ):
            raise RunnerError(f"process executable is not runnable: {result[0]}")
    return result


def _portable_exit(value: int) -> int:
    if value < 0:
        return min(255, 128 + abs(value))
    return min(255, value)


def _display_argv(argv: Sequence[str], root: Path) -> list[str]:
    prefix = str(root) + os.sep
    return [
        argument[len(prefix) :] if argument.startswith(prefix) else argument
        for argument in argv
    ]


def _single_line(value: str, maximum: int) -> str:
    return (
        value.replace("\x00", "\\x00")
        .replace("\r", "\\r")
        .replace("\n", "\\n")[:maximum]
    )


__all__ = [
    "RunResult",
    "RunnerError",
    "ingest_files",
    "supervise_process",
]
