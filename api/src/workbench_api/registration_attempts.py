"""Core custody for a reviewed registration's retained source attempt.

The Shell owner supplies its plan, source images and V1 receipt semantics.
Core holds the private backup/attempt bytes, ordered commit markers and
no-replace publication at the historical registration URI.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Protocol


class RegistrationAttemptError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RegistrationImage:
    path: str
    before: bytes
    after: bytes
    mode: int


class RegistrationAttempt(Protocol):
    @property
    def staging_token(self) -> str: ...
    @property
    def path(self) -> Path: ...
    @property
    def transaction_path(self) -> Path: ...
    @property
    def promoted(self) -> bool: ...
    def prepare(self, images: tuple[RegistrationImage, ...], receipt: bytes) -> None: ...
    def record_stage(self, ordinal: int, staged_relative: str | None) -> None: ...
    def mark_attempted(self, ordinal: int) -> None: ...
    def publish_applied(self, receipt: bytes) -> None: ...
    def restore_prepared(self, receipt: bytes) -> None: ...
    def promote(self) -> None: ...
    def discard(self) -> None: ...
    def inspect(self) -> dict: ...
    def finalize_committed(self) -> dict: ...
    def resume_partial(self) -> dict: ...


class RegistrationAttempts(Protocol):
    def open(
        self, *, state_root: Path, workspace: Path, payload: Path,
        plan_id: str, selection_id: str,
    ) -> ContextManager[RegistrationAttempt]: ...


_UNBOUND = object()
_bound: ContextVar[RegistrationAttempts | None | object] = ContextVar(
    "workbench_registration_attempts", default=_UNBOUND,
)
_host_provider: RegistrationAttempts | None = None


def bind_registration_attempts(provider: RegistrationAttempts | None) -> None:
    global _host_provider
    _host_provider = provider


@contextmanager
def registration_attempts_scope(provider: RegistrationAttempts | None) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def registration_attempts() -> RegistrationAttempts:
    selected = _bound.get()
    provider = _host_provider if selected is _UNBOUND else selected
    if provider is None:
        raise RegistrationAttemptError(
            "unavailable", "registration attempt custody requires Workbench Core",
        )
    return provider


__all__ = [
    "RegistrationAttempt", "RegistrationAttemptError", "RegistrationAttempts",
    "RegistrationImage", "bind_registration_attempts", "registration_attempts",
    "registration_attempts_scope",
]
