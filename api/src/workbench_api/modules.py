"""Contracts shared by the host and explicitly installed capability providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from threading import Event
from typing import Callable, Mapping, Sequence

API_VERSION = 1
IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")


class ModuleError(ValueError):
    """A module cannot be admitted or its operation cannot be executed."""


@dataclass(frozen=True)
class ExecutionContext:
    workspace: Path
    state_root: Path
    cancelled: Event = field(default_factory=Event)
    emit: Callable[[Mapping[str, object]], None] = field(default=lambda event: None)

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise ModuleError("operation cancelled")


@dataclass(frozen=True)
class Capability:
    id: str
    command: tuple[str, ...]
    handler: str
    description: str
    requires_profiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not IDENTIFIER.fullmatch(self.id):
            raise ModuleError(f"invalid capability id: {self.id!r}")
        if not self.command or any(not IDENTIFIER.fullmatch(p) for p in self.command):
            raise ModuleError(f"invalid command: {self.command!r}")
        package, separator, function = self.handler.partition(":")
        if not separator or not all(p.isidentifier() for p in package.split(".")) or not function.isidentifier():
            raise ModuleError(f"invalid capability handler: {self.handler!r}")
        if len(set(self.requires_profiles)) != len(self.requires_profiles) or any(not IDENTIFIER.fullmatch(value) for value in self.requires_profiles):
            raise ModuleError(f"invalid required profiles for capability {self.id!r}")


@dataclass(frozen=True)
class Module:
    id: str
    version: str
    capabilities: tuple[Capability, ...] = ()
    api_version: int = API_VERSION
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not IDENTIFIER.fullmatch(self.id):
            raise ModuleError(f"invalid module id: {self.id!r}")
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)[1-9][0-9]*)?", self.version):
            raise ModuleError(f"invalid module version: {self.version!r}")
        if type(self.api_version) is not int or self.api_version != API_VERSION:
            raise ModuleError(f"module {self.id} requires unsupported API {self.api_version}")
        for values in (tuple(c.id for c in self.capabilities), tuple(c.command for c in self.capabilities), self.requires):
            if len(values) != len(set(values)):
                raise ModuleError(f"module {self.id} repeats a declaration")
        if self.id in self.requires or any(not IDENTIFIER.fullmatch(p) for p in self.requires):
            raise ModuleError(f"invalid dependencies for module {self.id}")
