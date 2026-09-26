"""Core process custody for a profile-owned reusable fixture build.

The owner supplies exact source and toolchain verification. Core owns the
projection lease, cache directories, child process, and retained streams.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol, Sequence


class ReusableFixtureBuildError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReusableFixtureBuildResult:
    exit_code: int
    projection_id: str
    capture_id: str
    capture_directory: Path
    stdout: bytes
    stderr: bytes


class ReusableFixtureBuilds(Protocol):
    def prepare(self, *, state_root: Path) -> None: ...

    def run(
        self, *, state_root: Path, source_digest: str, source_root: Path,
        project: Path,
        source_files: tuple[dict[str, object], ...],
        generated_parts: tuple[str, ...], generated_suffixes: tuple[str, ...],
        generated_roots: tuple[Path, ...],
        argv: Sequence[str], environment: Mapping[str, str],
        input_digest: str, verify_inputs: Callable[[], str],
        verify_source: Callable[[Path], object],
    ) -> ReusableFixtureBuildResult: ...


_bound: ContextVar[ReusableFixtureBuilds | None] = ContextVar(
    "workbench_reusable_fixture_builds", default=None,
)


@contextmanager
def reusable_fixture_builds_scope(provider: ReusableFixtureBuilds) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def reusable_fixture_builds() -> ReusableFixtureBuilds:
    provider = _bound.get()
    if provider is None:
        raise ReusableFixtureBuildError(
            "fixture.host", "no Core fixture build supervisor is bound",
        )
    return provider


__all__ = [
    "ReusableFixtureBuildError", "ReusableFixtureBuildResult", "ReusableFixtureBuilds",
    "reusable_fixture_builds", "reusable_fixture_builds_scope",
]
