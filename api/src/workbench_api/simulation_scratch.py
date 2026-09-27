"""Opt-in Core scratch custody for Blueprints simulation V2 observations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Protocol


class SimulationScratchError(ValueError):
    """Core cannot issue or retain the requested simulation scratch lease."""


@dataclass(frozen=True, slots=True)
class SimulationScratchReference:
    lease_id: str
    path: Path


class SimulationScratchHost(Protocol):
    def allocate(self, *, parent: Path, plan_id: str) -> ContextManager[SimulationScratchReference]: ...


_bound: ContextVar[SimulationScratchHost | None] = ContextVar(
    "workbench_simulation_scratch", default=None,
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


__all__ = [
    "SimulationScratchError", "SimulationScratchHost", "SimulationScratchReference",
    "allocate_simulation_scratch", "simulation_scratch_scope",
]
