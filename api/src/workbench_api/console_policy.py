"""Immutable profile metadata consumed by presentation, not execution policy."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class ConsolePolicy:
    profile_id: str
    roles: tuple[str, ...]
    compatibility_experiments: Mapping[str, str]
    server_experiments: tuple[str, ...]
    examples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        from .modules import IDENTIFIER, ModuleError

        values = (self.profile_id, *self.roles, *self.compatibility_experiments,
                  *self.server_experiments, *self.examples)
        if any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in values):
            raise ModuleError("console policy contains invalid identifiers")
        if not set(self.compatibility_experiments).issubset(self.server_experiments):
            raise ModuleError("console policy server experiments omit compatibility inputs")
        if any(not isinstance(role, str) or not IDENTIFIER.fullmatch(role) for role in self.compatibility_experiments.values()):
            raise ModuleError("console policy contains invalid resource roles")
        object.__setattr__(self, "compatibility_experiments", MappingProxyType(dict(self.compatibility_experiments)))


def console_policies() -> tuple[ConsolePolicy, ...]:
    from .profile_extensions import profile_extensions
    from .profiles import profiles

    owners = {profile.id: profile for profile in profiles()}
    result = []
    for extension in profile_extensions("workbench.console_policies"):
        if extension.state != "available":
            continue
        try:
            policy = extension.value()
            if not isinstance(policy, ConsolePolicy) or policy.profile_id != extension.profile_id:
                raise ValueError("console policy does not belong to its owner")
            for role in policy.compatibility_experiments.values():
                owners[policy.profile_id].resource(role)
            result.append(policy)
        except (Exception, SystemExit):
            # A malformed optional policy has no presentation capabilities.
            continue
    return tuple(result)


def profile_choices(role: str) -> tuple[str, ...]:
    return tuple(policy.profile_id for policy in console_policies() if role in policy.roles)


def experiment_choices(*, server: bool) -> tuple[str, ...]:
    return tuple(sorted({value for policy in console_policies()
                         for value in (policy.server_experiments if server else policy.compatibility_experiments)}))


def example_choices() -> tuple[str, ...]:
    return tuple(sorted({value for policy in console_policies() for value in policy.examples}))
