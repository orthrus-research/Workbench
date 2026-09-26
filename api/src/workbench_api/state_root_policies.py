"""A Core-bound guard for workspace durable-location policy during owner writes."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import ContextManager, Iterator, Protocol


class StateRootPolicyError(ValueError):
    """The selected Core state-root policy is unavailable or stale."""


class StateRootPolicies(Protocol):
    def hold(
        self, workspace: Path, role: str, state_root: Path, expected_policy_id: str,
    ) -> ContextManager[None]: ...


_bound: ContextVar[StateRootPolicies | None] = ContextVar(
    "workbench_state_root_policies", default=None,
)


@contextmanager
def state_root_policies_scope(provider: StateRootPolicies | None) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def state_root_policies() -> StateRootPolicies:
    provider = _bound.get()
    if provider is None:
        raise StateRootPolicyError("no Core state-root policy host is bound")
    return provider


__all__ = [
    "StateRootPolicies", "StateRootPolicyError", "state_root_policies",
    "state_root_policies_scope",
]
