"""Bounded, run-scoped diagnostics for native and IDE validation subprocesses."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import threading
import time
import uuid

OUTPUT_LIMIT = 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat()


def terminate_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


class DiagnosticRun:
    """A new directory per run; no old report can silently satisfy this run."""

    def __init__(self, directory: Path, lane: str, phases=(), *, metadata=None):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.document = {
            "format": "workbench-stage-diagnostics-v1", "run_id": uuid.uuid4().hex,
            "lane": lane, "state": "running", "started_at": _now(),
            "python": sys.version, "platform": platform.platform(),
            "metadata": metadata or {}, "phases": [
                {"name": name, "state": "pending", "required": True} for name in phases],
        }
        self._local = threading.local()
        self._lock = threading.RLock()
        self._processes = {}
        self._cancelled = False
        self._next_command = 0
        self._started = time.perf_counter()
        self._write()

    def _write(self):
        with self._lock:
            temporary = self.directory / "report.json.tmp"
            temporary.write_text(json.dumps(self.document, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.directory / "report.json")

    def __enter__(self):
        self._termination_handler = None
        if threading.current_thread() is threading.main_thread():
            self._termination_handler = signal.getsignal(signal.SIGTERM)
            def interrupted(_signal, _frame):
                raise KeyboardInterrupt("validation received a termination signal")
            signal.signal(signal.SIGTERM, interrupted)
        return self

    def __exit__(self, kind, error, traceback):
        if self._termination_handler is not None:
            signal.signal(signal.SIGTERM, self._termination_handler)
        for row in self.document["phases"]:
            if row["state"] == "pending":
                row.update(state="unrun", reason="earlier failure" if error else "required phase did not run")
        incomplete = any(row["state"] != "passed" for row in self.document["phases"])
        self.document.update(state="cancelled" if isinstance(error, KeyboardInterrupt) else "failed" if error or incomplete else "passed",
                             finished_at=_now(), wall_seconds=time.perf_counter() - self._started)
        if error:
            self.document["error"] = f"{type(error).__name__}: {str(error)[:4096]}"
        self._write()
        if incomplete and error is None:
            raise RuntimeError("validation ended without every required phase")
        return False

    def cancel(self):
        with self._lock:
            self._cancelled = True
            processes = tuple(self._processes.values())
        for process in processes:
            terminate_tree(process)

    @contextmanager
    def phase(self, name):
        parents = getattr(self._local, "parents", None)
        if parents is None:
            parents = self._local.parents = []
        with self._lock:
            row = next((row for row in self.document["phases"] if row["name"] == name and row["state"] == "pending"), None)
            if row is None:
                row = {"name": name, "required": True}
                if parents:
                    row["parent"] = parents[-1]
                self.document["phases"].append(row)
            row.update(state="running", started_at=_now())
            started = time.perf_counter()
            parents.append(name)
            self._write()
        try:
            yield row
        except BaseException as error:
            with self._lock:
                row.update(state="timed-out" if isinstance(error, subprocess.TimeoutExpired) else "cancelled" if isinstance(error, KeyboardInterrupt) else "failed",
                           error=f"{type(error).__name__}: {str(error)[:4096]}")
            raise
        else:
            with self._lock:
                row["state"] = "passed"
        finally:
            with self._lock:
                row.update(finished_at=_now(), wall_seconds=time.perf_counter() - started)
                parents.pop()
                self._write()

    def command(self, arguments, *, cwd, env=None, timeout=600, expected=0):
        arguments = [str(value) for value in arguments]
        with self._lock:
            self._next_command += 1
            name = f"command-{self._next_command:03d}"
        # Redirected Windows output can use a legacy code page. Escaping the
        # progress preview keeps logging from blocking a valid Unicode command;
        # the report retains the original argument values.
        print("+", json.dumps(arguments[:4], ensure_ascii=True), flush=True)
        with self.phase(name) as row:
            with self._lock:
                row.update(command=arguments, cwd=str(cwd), timeout_seconds=timeout, expected_exit=expected)
                if self._cancelled:
                    raise KeyboardInterrupt("validation commands were cancelled")
                process = subprocess.Popen(arguments, cwd=cwd, env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           start_new_session=os.name != "nt")
                self._processes[process.pid] = process
            buffers = {"stdout": bytearray(), "stderr": bytearray()}
            truncated = {"stdout": False, "stderr": False}

            def drain(label, stream):
                with stream:
                    for block in iter(lambda: stream.read(65536), b""):
                        buffers[label].extend(block)
                        if len(buffers[label]) > OUTPUT_LIMIT:
                            del buffers[label][:-OUTPUT_LIMIT]
                            truncated[label] = True

            readers = [threading.Thread(target=drain, args=(label, stream), daemon=True)
                       for label, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
            for reader in readers:
                reader.start()
            try:
                process.wait(timeout=timeout)
            except BaseException:
                terminate_tree(process)
                raise
            finally:
                for reader in readers:
                    reader.join(timeout=10)
                if any(reader.is_alive() for reader in readers):
                    terminate_tree(process)
                    for reader in readers:
                        reader.join(timeout=10)
                row.update(exit_code=process.returncode, output_truncated=truncated)
                for label, data in buffers.items():
                    filename = f"{name}.{label}.log"
                    (self.directory / filename).write_bytes(bytes(data))
                    row[label] = filename
                with self._lock:
                    self._processes.pop(process.pid, None)
                if any(reader.is_alive() for reader in readers):
                    raise RuntimeError("command descendants retained output streams after termination")
            stdout = buffers["stdout"].decode("utf-8", errors="replace")
            stderr = buffers["stderr"].decode("utf-8", errors="replace")
            if self._cancelled:
                raise KeyboardInterrupt("validation commands were cancelled")
            if process.returncode != expected:
                raise subprocess.CalledProcessError(process.returncode, arguments, output=stdout, stderr=stderr)
            return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def default_directory(root: Path, lane: str) -> Path:
    return root / ".workbench/validation/stages" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{lane}-{uuid.uuid4().hex[:8]}"
