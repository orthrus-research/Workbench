"""Core-bound source scratch for a Packwiz V2 materialization.

The owner supplies its workspace and selected state root. Core allocates the
scratch directory and decides whether the exact lease may be disposed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Protocol


class TemporaryScratchError(ValueError):
    """The selected Core scratch host cannot provide a safe lease."""


@dataclass(frozen=True, slots=True)
class TemporaryScratchReference:
    """The exact Core-issued scratch lease and its owner-visible directory."""

    lease_id: str
    path: Path


class TemporaryScratchHost(Protocol):
    def packwiz_source(
        self, *, workspace: Path, state_root: Path, plan_digest: str,
    ) -> ContextManager[Path]: ...

    def packwiz_source_reference(
        self, *, workspace: Path, state_root: Path, plan_digest: str,
    ) -> ContextManager[TemporaryScratchReference]: ...


_bound: ContextVar[TemporaryScratchHost | None] = ContextVar(
    "workbench_temporary_scratch", default=None,
)


@contextmanager
def temporary_scratch_scope(host: TemporaryScratchHost) -> Iterator[None]:
    token = _bound.set(host)
    try:
        yield
    finally:
        _bound.reset(token)


def temporary_scratch_bound() -> bool:
    return _bound.get() is not None


def packwiz_source_scratch(
    *, workspace: Path, state_root: Path, plan_digest: str,
) -> ContextManager[Path]:
    host = _bound.get()
    if host is None:
        raise TemporaryScratchError(
            "no Core temporary scratch host is bound; invoke through Workbench Core"
        )
    return host.packwiz_source(
        workspace=workspace, state_root=state_root, plan_digest=plan_digest,
    )


def packwiz_source_scratch_reference(
    *, workspace: Path, state_root: Path, plan_digest: str,
) -> ContextManager[TemporaryScratchReference]:
    host = _bound.get()
    if host is None:
        raise TemporaryScratchError(
            "no Core temporary scratch host is bound; invoke through Workbench Core"
        )
    return host.packwiz_source_reference(
        workspace=workspace, state_root=state_root, plan_digest=plan_digest,
    )


__all__ = [
    "TemporaryScratchError", "TemporaryScratchHost", "TemporaryScratchReference",
    "packwiz_source_scratch", "packwiz_source_scratch_reference",
    "temporary_scratch_bound", "temporary_scratch_scope",
]
