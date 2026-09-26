"""Core-bound source scratch for a Packwiz V2 materialization.

The owner supplies its workspace and selected state root. Core allocates the
scratch directory and decides whether the exact lease may be disposed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import ContextManager, Iterator, Protocol


class TemporaryScratchError(ValueError):
    """The selected Core scratch host cannot provide a safe lease."""


class TemporaryScratchHost(Protocol):
    def packwiz_source(
        self, *, workspace: Path, state_root: Path, plan_digest: str,
    ) -> ContextManager[Path]: ...


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


__all__ = [
    "TemporaryScratchError", "TemporaryScratchHost", "packwiz_source_scratch",
    "temporary_scratch_bound", "temporary_scratch_scope",
]
