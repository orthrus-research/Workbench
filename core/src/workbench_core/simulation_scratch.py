"""Core retained scratch for opt-in Blueprints simulation V2 observations."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import Event
from typing import Sequence
from typing import Iterator
from uuid import uuid4

from workbench_api.simulation_scratch import (
    SimulationGitCapture, SimulationSandboxCapture, SimulationScratchError,
    SimulationScratchReference,
)
from workbench_api.processes import ProcessError, ProcessOutput

from . import check_storage, process_capture, tool_process
from .host_filesystem import fsync_directory, private_path
from .runner import RunnerError, supervise_process
from .temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


_PLAN = re.compile(r"blueprints-plan:sha256:[0-9a-f]{64}\Z")
_ROLE = "blueprints-simulation-v2"
_GIT_TIMEOUT_SECONDS = 300
_GIT_OUTPUT_LIMIT = 4 * 1024 * 1024
_GIT_INPUT_LIMIT = 1024 * 1024
_SANDBOX_TIMEOUT_MAX = 3600
_SANDBOX_OUTPUT_MAX = 16 * 1024 * 1024


class _SandboxOutputLimit(Exception):
    pass


class _SandboxCapture(process_capture.FileCapture):
    """Retain the first approved bytes plus one exact overflow witness."""

    def write_raw(self, stream, data):
        if stream in self.streams:
            remaining = self.limit - self.counts[stream]
            if len(data) > remaining:
                if remaining:
                    super().write_raw(stream, data[:remaining])
                raise _SandboxOutputLimit("Bubblewrap output exceeded its approved byte bound")
        return super().write_raw(stream, data)


def _stream_prefix_digest(directory: Path, record: dict, name: str, limit: int) -> str:
    row = record["streams"][name]
    output = ProcessOutput(directory / row["path"], row["bytes"], row["sha256"])
    digest = sha256()
    remaining = limit
    with process_capture.open_output(output) as stream:
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


class CoreSimulationScratch:
    """Allocate an exact private lease and retain it until absence is proven."""

    def __init__(
        self, *, workspace: Path, configuration_home: Path,
        cancelled: Event | None = None,
    ):
        if (not isinstance(workspace, Path) or not workspace.is_absolute()
                or not isinstance(configuration_home, Path)
                or not configuration_home.is_absolute()):
            raise SimulationScratchError("simulation scratch host identity is invalid")
        self.workspace = workspace
        self.configuration_home = configuration_home
        self.cancelled = Event() if cancelled is None else cancelled

    def capture_git(
        self, reference: SimulationScratchReference, argv: Sequence[str], *,
        cwd: Path, stdin: bytes,
    ) -> SimulationGitCapture:
        """Retain a bounded, exact Git attempt inside this Core lease."""

        if (not isinstance(reference, SimulationScratchReference)
                or not isinstance(cwd, Path) or not cwd.is_absolute()
                or not (cwd == self.workspace or cwd.is_relative_to(reference.path))
                or type(stdin) is not bytes or len(stdin) > _GIT_INPUT_LIMIT
                or not argv or any(type(part) is not str or not part for part in argv)):
            raise SimulationScratchError("V2 Git capture path or input exceeds Core policy")
        binding_body = {
            "argv": list(argv), "cwd": str(cwd),
            "stdin_sha256": sha256(stdin).hexdigest(),
        }
        binding = (
            f"blueprints-git-v2:{reference.lease_id}:"
            + sha256(json.dumps(binding_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        try:
            with CoreTemporaryLeases.reference_lease(
                self.configuration_home, reference.lease_id,
                workspace=self.workspace, owner_id="blueprints",
                role=_ROLE, path=reference.path,
            ) as lease:
                captured_root = lease.path / "git-captures"
                try:
                    captured_root.mkdir(mode=0o700)
                    fsync_directory(lease.path)
                except FileExistsError:
                    pass
                check_storage.ordinary(captured_root, directory=True)
                if not private_path(captured_root, directory=True):
                    raise SimulationScratchError("V2 Git capture root is not owner-private")
                check_storage.ordinary(cwd, directory=True)
                attempt_name = uuid4().hex
                captured = tool_process.capture(
                    argv, directory=captured_root / attempt_name,
                    binding=binding, cwd=cwd, stdin=stdin,
                    environment=os.environ, cancelled=self.cancelled,
                    timeout_seconds=_GIT_TIMEOUT_SECONDS,
                    output_limit=_GIT_OUTPUT_LIMIT,
                )
                with tool_process.open_output(captured.stdout) as stream:
                    stdout = stream.read()
                with tool_process.open_output(captured.stderr) as stream:
                    stderr = stream.read()
                return SimulationGitCapture(
                    captured.exit_code, stdout, stderr, captured.capture_id,
                    attempt_name,
                )
        except (OSError, ValueError, tool_process.ProcessError, TemporaryLeaseError) as exc:
            if isinstance(exc, SimulationScratchError):
                raise
            raise SimulationScratchError(str(exc)) from exc

    def capture_sandbox(
        self, reference: SimulationScratchReference, argv: Sequence[str], *,
        cwd: Path, timeout_seconds: int, max_output_bytes: int,
    ) -> SimulationSandboxCapture:
        """Capture one V2 Bubblewrap command under its full approved lock bounds."""

        if (not isinstance(reference, SimulationScratchReference)
                or not isinstance(cwd, Path) or not cwd.is_absolute()
                or ".." in cwd.parts
                or not cwd.is_relative_to(reference.path)
                or not argv or any(type(part) is not str or not part for part in argv)
                or not Path(argv[0]).is_absolute()
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= _SANDBOX_TIMEOUT_MAX
                or type(max_output_bytes) is not int
                or not 0 <= max_output_bytes <= _SANDBOX_OUTPUT_MAX):
            raise SimulationScratchError("V2 Bubblewrap capture path or lock limits are invalid")
        binding_body = {
            "lease_id": reference.lease_id, "argv": list(argv), "cwd": str(cwd),
            "timeout_seconds": timeout_seconds, "max_output_bytes": max_output_bytes,
        }
        binding = "blueprints-sandbox-v2:" + sha256(check_storage.canonical(binding_body)).hexdigest()
        try:
            with CoreTemporaryLeases.reference_lease(
                self.configuration_home, reference.lease_id,
                workspace=self.workspace, owner_id="blueprints",
                role=_ROLE, path=reference.path,
            ) as lease:
                capture_root = lease.path / "sandbox-captures"
                try:
                    capture_root.mkdir(mode=0o700)
                    fsync_directory(lease.path)
                except FileExistsError:
                    pass
                check_storage.ordinary(capture_root, directory=True)
                if not private_path(capture_root, directory=True):
                    raise SimulationScratchError("V2 Bubblewrap capture root is not owner-private")
                cursor = cwd
                while cursor != lease.path:
                    check_storage.ordinary(cursor, directory=True)
                    cursor = cursor.parent
                check_storage.ordinary(lease.path, directory=True)
                attempt_name = uuid4().hex
                directory = capture_root / attempt_name
                session = _SandboxCapture(directory, binding, max_output_bytes + 1)
                control = tool_process._Control(self.cancelled, timeout_seconds)

                def observed(record: dict, *, exit_code: int | None,
                             timed_out: bool, output_limited: bool) -> SimulationSandboxCapture:
                    reopened = process_capture.load(directory, binding=binding)
                    if reopened != record:
                        raise SimulationScratchError("V2 Bubblewrap capture changed during readback")
                    return SimulationSandboxCapture(
                        exit_code, timed_out, output_limited,
                        _stream_prefix_digest(directory, record, "stdout", max_output_bytes),
                        _stream_prefix_digest(directory, record, "stderr", max_output_bytes),
                        record["id"], attempt_name,
                    )

                try:
                    result = supervise_process(
                        argv, cwd=cwd, root=cwd, session=session, renderer=control,
                        source="blueprints-sandbox", environment=os.environ,
                        input_file=None, input_limit=0,
                        interrupt_grace_seconds=0.1, terminate_grace_seconds=1,
                        require_group_closure=True, output_mode="raw",
                    )
                except BaseException as exc:
                    try:
                        record = session.commit(failure={
                            "type": type(exc).__name__, "message": str(exc),
                        })
                    except (OSError, ValueError) as retention_error:
                        exc.add_note("Partial Bubblewrap capture could not be committed: " + str(retention_error))
                        raise
                    if isinstance(exc, RunnerError) and isinstance(exc.__cause__, _SandboxOutputLimit):
                        return observed(record, exit_code=None, timed_out=False, output_limited=True)
                    raise
                if control.reason == "timed out":
                    record = session.commit(failure={"type": "timeout", "message": "approved command timed out"})
                    return observed(
                        record, exit_code=result.process_exit_code,
                        timed_out=True, output_limited=False,
                    )
                if control.reason or result.cancellation or result.process_exit_code is None:
                    session.commit(failure={
                        "type": "cancelled-or-unclosed", "message": str(result.cancellation or control.reason),
                    })
                    raise SimulationScratchError("V2 Bubblewrap process closure is unproven")
                record = session.commit(exit_code=result.process_exit_code)
                return observed(
                    record, exit_code=result.process_exit_code,
                    timed_out=False, output_limited=any(
                        row["bytes"] > max_output_bytes for row in record["streams"].values()
                    ),
                )
        except (OSError, ValueError, ProcessError, RunnerError, TemporaryLeaseError) as exc:
            if isinstance(exc, SimulationScratchError):
                raise
            raise SimulationScratchError(str(exc)) from exc

    @contextmanager
    def allocate(
        self, *, parent: Path, plan_id: str,
    ) -> Iterator[SimulationScratchReference]:
        protected = self.workspace / ".workbench" / "blueprints"
        if (not isinstance(parent, Path) or not parent.is_absolute()
                or ".." in parent.parts
                or parent == protected or not parent.is_relative_to(protected)
                or type(plan_id) is not str or _PLAN.fullmatch(plan_id) is None):
            raise SimulationScratchError("simulation scratch parent or plan is invalid")
        leases = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.configuration_home,
            locations={_ROLE: parent}, owner_id="blueprints",
        )
        try:
            reference = leases.allocate(
                _ROLE, f"simulation-{sha256(plan_id.encode('utf-8')).hexdigest()[:16]}-{uuid4().hex}",
            )
            with leases.execution(reference):
                try:
                    yield SimulationScratchReference(reference.lease_id, reference.path)
                except BaseException as exc:
                    try:
                        leases.retain(reference, outcome="failed")
                    except (OSError, TemporaryLeaseError) as retention_error:
                        exc.add_note("Core simulation scratch retention failed: " + str(retention_error))
                    raise
                else:
                    leases.retain(reference, outcome="completed")
        except TemporaryLeaseError as exc:
            raise SimulationScratchError(str(exc)) from exc


__all__ = ["CoreSimulationScratch"]
