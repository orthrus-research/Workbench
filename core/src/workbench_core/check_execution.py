"""Bounded check execution through Core's existing process and storage owners."""

from dataclasses import asdict
from hashlib import sha256
import os
import platform
from pathlib import Path
import signal
import sys
import threading
import time
import re

from workbench_api.checks import observation, validate_observation
from .check_storage import (
    CheckStorageError,
    image_executable,
    ordinary,
    read_json,
    seal,
    write_json,
)
from .render import Renderer
from .runner import supervise_process
from .sessions import (
    RetainedSession,
    resolve_session,
    verify_live_console_manifest_chain,
)


MAX_LOG_BYTES = 16 * 1024**2
INHERITED_ENVIRONMENT = frozenset({
    "PATH", "LANG", "LC_ALL", "DISPLAY", "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
})


def host_observation(environment=None):
    """Bind the local host and admitted environment without retaining addresses."""
    values = dict(os.environ) if environment is None else environment
    admitted = {key: values[key] for key in sorted(INHERITED_ENVIRONMENT) if key in values}
    import json
    return {
        "system": platform.system(), "release": platform.release(),
        "machine": platform.machine(),
        "host_id": sha256(platform.node().encode()).hexdigest(),
        "environment_sha256": sha256(json.dumps(admitted, sort_keys=True).encode()).hexdigest(),
        "scope": "host and admitted environment only; graphics observations are compared separately",
    }


def capture_logs(runtime, names, *, optional_globs=()):
    result = {}
    selected = list(names)
    for pattern in optional_globs:
        from .check_storage import safe_path

        parent, separator, leaf = pattern.rpartition("/")
        if not separator or not re.fullmatch(r"(?:\*|[a-zA-Z0-9._-]+)\.[a-zA-Z0-9]+", leaf):
            raise CheckStorageError("optional logs require a bounded single-directory file pattern")
        directory = runtime / safe_path(parent)
        if not directory.exists() and not directory.is_symlink():
            continue
        ordinary(directory, directory=True)
        paths = []
        for path in directory.glob(leaf):
            paths.append(path)
            if len(paths) > 32:
                break
        if len(paths) > 32:
            result[pattern] = {"state": "over-bound", "text": None}
            continue
        selected.extend(path.relative_to(runtime).as_posix() for path in sorted(paths))
    if len(selected) > 64:
        raise CheckStorageError("too many declared check evidence files")
    for name in dict.fromkeys(selected):
        from .check_storage import safe_path

        path = runtime / safe_path(name)
        if not path.exists() and not path.is_symlink():
            result[name] = {"state": "missing", "text": None}
            continue
        path = ordinary(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES:
            result[name] = {"state": "over-bound", "text": None}
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                result[name] = {"state": "invalid-encoding", "text": None}
                continue
            result[name] = {
                "state": "captured",
                "text": text,
                "size": len(raw),
                "sha256": sha256(raw).hexdigest(),
            }
    return result


class CheckProgress(Renderer):
    def __init__(self, *, attempt, request_id, runtime, logs, optional_logs, observe, timeout, cancelled):
        self.attempt, self.runtime, self.logs = attempt, runtime, logs
        self.request_id = request_id
        self.optional_logs = optional_logs
        self.observe, self.timeout, self.cancelled = observe, timeout, cancelled
        self.started = time.monotonic()
        self.last_sample = 0
        self.stop_reason = None
        self.signalled = False
        self.console_directory = None
        self.observations = []
        (attempt / "observations").mkdir(mode=0o700)
        self.retain(observation("running", "starting", "Starting the captured candidate"))

    def retain(self, value):
        validate_observation(value)
        if self.observations and self.observations[-1]["observation"] == value:
            return
        if len(self.observations) >= 256:
            raise CheckStorageError("check observation history exceeds its bound")
        record = seal("check-progress", {
            "format": "workbench-check-progress-v1",
            "attempt_id": self.attempt.name,
            "request_id": self.request_id,
            "sequence": len(self.observations) + 1,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "observation": value,
        })
        write_json(self.attempt / "observations" / f"{record['sequence']:04d}.json", record)
        self.observations.append(record)

    def sample(self):
        logs = capture_logs(self.runtime, self.logs, optional_globs=self.optional_logs)
        if self.console_directory is not None:
            logs.update({
                "logs/process-" + key: value
                for key, value in capture_logs(
                    self.console_directory, ("stdout.raw", "stderr.raw")
                ).items()
            })
        value = validate_observation(self.observe(logs))
        for reference in value["evidence"]:
            record = logs.get(reference["log"], {})
            if record.get("state") != "captured" or reference["line"] > len(record["text"].splitlines()):
                raise CheckStorageError("check observation references unavailable evidence")
        self.retain(value)
        return value

    def consume(self, event):
        pass  # RetainedSession already stores every raw byte and normalized event.

    def pulse(self):
        now = time.monotonic()
        if self.stop_reason is not None:
            return
        if (
            self.signalled
            or self.cancelled()
            or (self.attempt / "cancel.json").exists()
        ):
            self.stop_reason = "cancelled"
        elif now - self.started >= self.timeout:
            self.stop_reason = "timed-out"
        elif now - self.last_sample >= 0.5:
            self.last_sample = now
            value = self.sample()
            if value["state"] == "checkpoint":
                self.stop_reason = "checkpoint-reached"
            elif value["state"] == "failure":
                self.stop_reason = "failure-observed"
        if self.stop_reason is not None:
            self.cancellation_requested = True


def execute(
    root,
    attempt,
    runtime,
    image,
    *,
    request_id,
    log_paths,
    optional_log_globs,
    observe,
    timeout,
    cancelled=lambda: False,
    expected_host=None,
    attachment=None,
):
    if sys.platform != "linux":
        raise CheckStorageError("saved checks require a native Linux host")
    ordinary(runtime, directory=True)
    executable = image_executable(root, image)
    if sha256(executable.read_bytes()).hexdigest() != image["executable"]["sha256"]:
        raise CheckStorageError("runtime executable changed before launch")
    if not 1 <= timeout <= 3600:
        raise CheckStorageError("check timeout must be between 1 and 3600 seconds")
    renderer = CheckProgress(
        attempt=attempt,
        request_id=request_id,
        runtime=runtime,
        logs=log_paths,
        optional_logs=optional_log_globs,
        observe=observe,
        timeout=timeout,
        cancelled=cancelled,
    )
    from .check_attachments import arguments as attachment_arguments
    command = [str(executable), *attachment_arguments(runtime, attachment), *image["arguments"]]
    # The image is a trusted local executable environment, not a sandbox. Do
    # not implicitly forward credentials or JVM injection environment options.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in INHERITED_ENVIRONMENT
    }
    if expected_host is not None and host_observation(environment) != expected_host:
        raise CheckStorageError("check host or admitted environment changed; prepare again")
    environment.update({"HOME": str(runtime), "TMPDIR": str(runtime / ".check-tmp")})
    (runtime / ".check-tmp").mkdir(mode=0o700)
    session = RetainedSession(
        root=root,
        command_id="workbench.saved-check",
        argv=command,
        cwd=runtime,
        intent="execute",
    )
    renderer.console_directory = session.directory
    write_json(
        attempt / "console.json",
        {"session_id": session.session_id, "directory": str(session.directory)},
    )
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(
                sig, lambda *_: setattr(renderer, "signalled", True)
            )
    try:
        result = supervise_process(
            command,
            cwd=runtime,
            root=root,
            session=session,
            renderer=renderer,
            source="saved-check",
            environment=environment,
            input_file=(
                runtime / image["launch_input"] if image.get("launch_input") else None
            ),
            require_group_closure=True,
        )
        value = {
            "state": "closed",
            "stop_reason": renderer.stop_reason or "process-exited",
            "console": asdict(result),
            "observations": renderer.observations,
        }
        write_json(attempt / "execution.json", value)
        return value
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def console_logs(root, session_id):
    directory, _ = resolve_session(root, session_id)
    verify_live_console_manifest_chain(root, session_id)
    result = capture_logs(directory, ("stdout.raw", "stderr.raw"))
    return {"logs/process-" + key: value for key, value in result.items()}


def read_progress(attempt, request_id):
    """Read only retained progress: no profile import or source recapture."""
    directory = attempt / "observations"
    records = sorted(ordinary(directory, directory=True).glob("*.json")) if directory.exists() else []
    if len(records) > 256 or any(path.name != f"{number:04d}.json" for number, path in enumerate(records, 1)):
        raise CheckStorageError("invalid check observation history")
    latest = None
    if records:
        latest = read_json(records[-1])
        body = {key: value for key, value in latest.items() if key != "id"}
        if (
            seal("check-progress", body) != latest
            or latest.get("format") != "workbench-check-progress-v1"
            or latest.get("attempt_id") != attempt.name
            or latest.get("request_id") != request_id
            or latest.get("sequence") != len(records)
        ):
            raise CheckStorageError("check progress identity changed")
        validate_observation(latest["observation"])
    if (attempt / "result.json").exists():
        state = "finished"
    elif (attempt / "recovery.json").exists():
        state = "recovered-incomplete"
    elif (attempt / "started.json").exists():
        from .check_storage import execution_lock

        try:
            with execution_lock(attempt):
                state = "needs-attention"
        except CheckStorageError:
            state = "running"
    else:
        state = "prepared-not-run"
    return {
        "format": "workbench-check-live-status-v1",
        "attempt_id": attempt.name, "request_id": request_id,
        "state": state, "progress": latest,
        "meaning": "Progress only; reopen the sealed result for the check outcome",
    }


def recover_execution(root, attempt, projection):
    """Close an interrupted exact console process, never a name-matched process."""
    from .sessions import _read_exact_manifest, _manifest_process_alive

    path = attempt / "console.json"
    if not path.exists():
        return {"state": "closed", "reason": "no console process was admitted"}
    console = read_json(path)
    directory, _ = resolve_session(root, console["session_id"])
    manifest, _, proof = _read_exact_manifest(directory)
    if manifest["command"]["cwd"] != str(projection):
        raise CheckStorageError("console process belongs to another projection")
    custody = proof.get("process_custody")
    if custody is None:
        return {"state": "closed", "reason": "launch gate was never released"}
    group = custody["process_group_id"]

    def group_exists():
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False

    for selected_signal in (signal.SIGTERM, signal.SIGKILL):
        if not group_exists():
            break
        if not _manifest_process_alive(manifest, custody):
            raise CheckStorageError(
                "group remains without its exact recorded leader; automatic recovery refuses uncertain process ownership"
            )
        os.killpg(group, selected_signal)
        deadline = time.monotonic() + 3
        while group_exists() and time.monotonic() < deadline:
            time.sleep(0.05)
    if group_exists():
        raise CheckStorageError(
            "owned process closure is unresolved; retain the projection"
        )
    return {
        "state": "closed",
        "session_id": console["session_id"],
        "reason": "exact recorded process group is absent",
    }
