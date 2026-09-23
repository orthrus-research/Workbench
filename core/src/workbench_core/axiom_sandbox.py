"""Core selection, image provisioning and cleanup for Axiom OCI workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
from threading import Event
import time
from uuid import UUID, uuid4

from workbench_api.sandboxes import WorkerSandboxSelection


# Linux amd64 manifest, not a mutable tag. Java itself remains profile-selected.
AXIOM_OCI_IMAGE = (
    "docker.io/library/ubuntu@sha256:"
    "496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4"
)
_sessions: dict[str, tuple[str, str, Path]] = {}
_LABEL = "org.orthrus.workbench.axiom.session"


def _owner_marker() -> tuple[str, int, str]:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    pid = os.getpid()
    started = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    return boot, pid, started


def _owner_alive(record: dict) -> bool:
    if record["boot"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        return False
    try:
        started = Path(f"/proc/{record['pid']}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except FileNotFoundError:
        return False
    return started == record["started"]


def _journal_root(state_root: Path) -> Path:
    root = state_root / "sandboxes" / "axiom"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _record_session(root: Path, session: str, executable: str, host: str) -> Path:
    boot, pid, started = _owner_marker()
    record = {"schema": 1, "session": session, "docker": executable, "host": host,
              "boot": boot, "pid": pid, "started": started}
    path = root / (session + ".json")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                     prefix=".pending-", delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(record, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return path


def _cleanup_session(executable: str, host: str, session: str) -> None:
    label = "label=" + _LABEL + "=" + session
    listing = _docker(executable, host, "container", "ls", "-aq", "--filter", label, timeout=20)
    if listing.returncode:
        raise ValueError("Docker worker cleanup could not list the session's containers")
    containers = listing.stdout.decode("ascii", errors="strict").split()
    if containers:
        removed = _docker(executable, host, "container", "rm", "-f", *containers, timeout=20)
        if removed.returncode:
            raise ValueError("Docker worker cleanup could not remove the session's containers")
    remaining = _docker(executable, host, "container", "ls", "-aq", "--filter", label, timeout=20)
    if remaining.returncode or remaining.stdout.strip():
        raise ValueError("Docker worker cleanup could not verify container removal")


def _recover_sessions(root: Path, executable: str, host: str) -> list[str]:
    recovered = []
    for path in root.glob("*.json"):
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("indirect session record")
            record = json.loads(path.read_text(encoding="utf-8"))
            session = record["session"]
            if (record["schema"] != 1 or str(UUID(session)) != session or path.stem != session
                    or not isinstance(record["pid"], int)
                    or not all(isinstance(record[key], str)
                               for key in ("docker", "host", "boot", "started"))):
                raise ValueError("invalid session record")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Axiom sandbox recovery record is invalid: " + str(path)) from exc
        if _owner_alive(record):
            continue
        if record["docker"] != executable or record["host"] != host:
            raise ValueError("Axiom sandbox session " + session
                             + " belongs to another local Docker endpoint; recover it on that endpoint")
        try:
            _cleanup_session(executable, host, session)
        except ValueError as exc:
            raise ValueError("Axiom sandbox session " + session
                             + " could not be recovered; start its local Docker daemon and retry") from exc
        path.unlink()
        recovered.append(session)
    return recovered


def recover_axiom(state_root: Path) -> list[str]:
    """Remove only daemon-owned containers recorded for dead Workbench processes."""
    root = _journal_root(state_root)
    if not any(root.glob("*.json")):
        return []
    executable, host = _control()
    return _recover_sessions(root, executable, host)


def _run(command: list[str], *, cancelled: Event | None = None, timeout: int = 120):
    if cancelled is not None and cancelled.is_set():
        raise ValueError("Axiom sandbox preparation was cancelled")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(command, env={}, stdout=stdout, stderr=stderr)
        except OSError as exc:
            raise ValueError("Docker sandbox control is unavailable: " + type(exc).__name__) from exc
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled is not None and cancelled.is_set():
                    raise ValueError("Axiom sandbox preparation was cancelled")
                if time.monotonic() >= deadline:
                    raise ValueError("Docker sandbox control timed out")
                time.sleep(0.1)
        except BaseException:
            process.kill()
            process.wait()
            raise
        if cancelled is not None and cancelled.is_set():
            raise ValueError("Axiom sandbox preparation was cancelled")
        if os.fstat(stdout.fileno()).st_size > 2 * 1024 * 1024 or os.fstat(stderr.fileno()).st_size > 2 * 1024 * 1024:
            raise ValueError("Docker sandbox control output exceeds its bound")
        stdout.seek(0); stderr.seek(0)
        return subprocess.CompletedProcess(command, process.returncode, stdout.read(), stderr.read())


def _control():
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise ValueError("Axiom OCI evaluation requires Linux x64")
    selected = shutil.which("docker")
    if selected is None:
        raise ValueError("Docker is unavailable; install Docker Engine or select another qualified sandbox")
    executable = Path(selected).resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("Docker client is not an executable ordinary file")
    host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if not host.startswith("unix:///"):
        raise ValueError("Axiom requires a local Unix Docker socket; remote daemons are not admitted")
    path = Path(host.removeprefix("unix://")).resolve(strict=True)
    if not stat.S_ISSOCK(path.stat().st_mode):
        raise ValueError("The selected Docker endpoint is not a local socket")
    return str(executable), "unix://" + str(path)


def _docker(executable: str, host: str, *arguments: str, cancelled: Event | None = None,
            timeout: int = 120):
    return _run([executable, "--host", host, *arguments], cancelled=cancelled, timeout=timeout)


def open_axiom(backend: str, *, state_root: Path, cancelled: Event) -> WorkerSandboxSelection:
    if backend not in {"docker", "gvisor"}:
        raise ValueError("Core does not recognize the requested Axiom OCI backend")
    executable, host = _control()
    journal = _journal_root(state_root)
    _recover_sessions(journal, executable, host)
    info = _docker(executable, host, "info", "--format", "{{json .Runtimes}}", cancelled=cancelled)
    if info.returncode:
        raise ValueError("Docker daemon is unavailable; start the local service and retry")
    try:
        runtimes = json.loads(info.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Docker did not report its container runtimes") from exc
    runtime = "runsc" if backend == "gvisor" else "runc"
    if runtime not in runtimes:
        raise ValueError("Docker runtime " + runtime + " is unavailable; install and register it before Axiom evaluation")
    image = _docker(executable, host, "image", "inspect", AXIOM_OCI_IMAGE,
                    "--format", "{{.Os}}/{{.Architecture}}", cancelled=cancelled)
    if image.returncode:
        pulled = _docker(executable, host, "pull", "--quiet", "--platform=linux/amd64", AXIOM_OCI_IMAGE,
                         cancelled=cancelled, timeout=300)
        if pulled.returncode:
            raise ValueError("The pinned Axiom OCI image is unavailable; Docker pull failed or the host is offline")
        image = _docker(executable, host, "image", "inspect", AXIOM_OCI_IMAGE,
                        "--format", "{{.Os}}/{{.Architecture}}", cancelled=cancelled)
    if image.returncode or image.stdout.strip() != b"linux/amd64":
        raise ValueError("The pinned Axiom OCI image is not a Linux amd64 image")
    session = str(uuid4())
    path = _record_session(journal, session, executable, host)
    arguments = (
        "-Daxiom.sandbox.backend=" + backend,
        "-Daxiom.sandbox.docker=" + executable,
        "-Daxiom.sandbox.dockerHost=" + host,
        "-Daxiom.sandbox.image=" + AXIOM_OCI_IMAGE,
        "-Daxiom.sandbox.session=" + session,
        "-Daxiom.sandbox.user=" + str(os.getuid()) + ":" + str(os.getgid()),
        "-Daxiom.sandbox.groups=" + ",".join(map(str, sorted(set(os.getgroups()) | {os.getgid()}))),
    )
    _sessions[session] = executable, host, path
    return WorkerSandboxSelection(backend, "axiom.oci-worker.v1:" + runtime + ":" + AXIOM_OCI_IMAGE,
                                  arguments, session)


def close_axiom(selection: WorkerSandboxSelection) -> None:
    session = selection.session_id
    if session is None or session not in _sessions:
        raise ValueError("Axiom sandbox cleanup session is missing")
    executable, host, path = _sessions[session]
    _cleanup_session(executable, host, session)
    path.unlink()
    _sessions.pop(session)
