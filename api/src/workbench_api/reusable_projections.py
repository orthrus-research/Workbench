"""Core custody for existing reusable source projections with mutable build state.

The owner admits exact source files. Core binds the fixed historical directory,
checks generated member safety, and keeps an active read lease while it is used.
Publication of a new projection and build execution are separate capabilities.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Protocol


class ReusableProjectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReusableProjectionReference:
    projection_id: str
    path: Path
    project: Path
    source_digest: str
    owner_id: str
    workspace: Path


class ReusableProjections(Protocol):
    def adopt(
        self, family: str, path: Path, *, source_digest: str,
        project_relative: Path, source_files: tuple[dict[str, object], ...],
        generated_parts: tuple[str, ...], generated_suffixes: tuple[str, ...],
        validate: Callable[[Path], object],
    ) -> ReusableProjectionReference: ...

    def open(
        self, projection_id: str, *, validate: Callable[[Path], object],
    ) -> ContextManager[ReusableProjectionReference]: ...


_bound: ContextVar[ReusableProjections | None] = ContextVar(
    "workbench_reusable_projections", default=None,
)


@contextmanager
def reusable_projections_scope(provider: ReusableProjections) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def reusable_projections() -> ReusableProjections:
    provider = _bound.get()
    if provider is None:
        raise ReusableProjectionError(
            "projection.host", "no reusable projection host is bound; invoke through Workbench Core",
        )
    return provider


__all__ = [
    "ReusableProjectionError", "ReusableProjectionReference", "ReusableProjections",
    "reusable_projections", "reusable_projections_scope",
]
