"""Core-bound user selections for profile-specific runtime fixtures.

Selections name locations only. The selected profile must inspect the bytes
before a native operation can use them.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Protocol


class FixtureSelectionHostError(ValueError):
    """A command was invoked without Core's fixture-selection authority."""


class FixtureSelections(Protocol):
    def register(
        self, profile: str, workspace: Path | str, runtime: Path | str,
        java_home: Path | str,
    ) -> dict[str, Any]: ...

    def resolve(
        self, profile: str, workspace: Path | str | None = None, *,
        runtime: Path | str | None = None,
        java_home: Path | str | None = None,
    ) -> dict[str, Any]: ...


_bound: ContextVar[FixtureSelections | None] = ContextVar(
    "workbench_fixture_selections", default=None,
)


@contextmanager
def fixture_selections_scope(provider: FixtureSelections) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def fixture_selections() -> FixtureSelections:
    provider = _bound.get()
    if provider is None:
        raise FixtureSelectionHostError(
            "no fixture-selection host is bound; invoke through Workbench Core"
        )
    return provider


__all__ = [
    "FixtureSelectionHostError", "FixtureSelections", "fixture_selections",
    "fixture_selections_scope",
]
