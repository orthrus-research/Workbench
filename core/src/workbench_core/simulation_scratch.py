"""Core retained scratch for opt-in Blueprints simulation V2 observations."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterator
from uuid import uuid4

from workbench_api.simulation_scratch import (
    SimulationScratchError, SimulationScratchReference,
)

from .temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


_PLAN = re.compile(r"blueprints-plan:sha256:[0-9a-f]{64}\Z")
_ROLE = "blueprints-simulation-v2"


class CoreSimulationScratch:
    """Allocate an exact private lease and retain it until absence is proven."""

    def __init__(self, *, workspace: Path, configuration_home: Path):
        if (not isinstance(workspace, Path) or not workspace.is_absolute()
                or not isinstance(configuration_home, Path)
                or not configuration_home.is_absolute()):
            raise SimulationScratchError("simulation scratch host identity is invalid")
        self.workspace = workspace
        self.configuration_home = configuration_home

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
