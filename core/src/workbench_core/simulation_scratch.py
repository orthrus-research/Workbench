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
    SimulationGitCapture, SimulationScratchError, SimulationScratchReference,
)

from . import check_storage, tool_process
from .host_filesystem import fsync_directory, private_path
from .temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


_PLAN = re.compile(r"blueprints-plan:sha256:[0-9a-f]{64}\Z")
_ROLE = "blueprints-simulation-v2"
_GIT_TIMEOUT_SECONDS = 300
_GIT_OUTPUT_LIMIT = 4 * 1024 * 1024
_GIT_INPUT_LIMIT = 1024 * 1024


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
