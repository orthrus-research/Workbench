"""Contracts shared by the host and explicitly installed capability providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from threading import Event
from typing import Callable, Mapping, Sequence

from .durable_resources import DurableResources, ResourceReference
from .managed_java import ManagedJava

API_VERSION = 1
IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")


class ModuleError(ValueError):
    """A module cannot be admitted or its operation cannot be executed."""


@dataclass(frozen=True, slots=True)
class EnvironmentSelection:
    """Core's immutable selection snapshot for one module operation.

    Managed choices are acquired through Core. A user-supplied Java path is
    passed through at selection time; its consuming operation handles failure.
    """

    resolution_id: str
    workspace_id: str | None
    workspace_name: str | None
    workspace_source: str
    profile_configuration: Path | None
    profile_source: str
    java_home: Path | None
    java_source: str
    git_executable: str | None
    managed_java_feature: int | None = None


@dataclass(frozen=True)
class ExecutionContext:
    workspace: Path
    state_root: Path
    cancelled: Event = field(default_factory=Event)
    emit: Callable[[Mapping[str, object]], None] = field(default=lambda event: None)
    locations: Mapping[str, Path] = field(default_factory=dict)
    output_resolver: Callable[[str, str], Path] | None = None
    configuration_home: Path | None = None
    environment_resolution_id: str | None = None
    location_sources: Mapping[str, str] = field(default_factory=dict)
    durable_resources: DurableResources | None = None
    selection: EnvironmentSelection | None = None
    managed_java: ManagedJava | None = None

    def location(self, role: str) -> Path:
        """Return a Core-resolved role path for an opted-in module adapter."""

        if role not in self.locations:
            raise ModuleError(f"location role is not resolved: {role}")
        return self.locations[role]

    def output_path(self, role: str, name: str) -> Path:
        """Allocate one invocation-scoped output through the Core host."""

        if self.output_resolver is None:
            raise ModuleError("the Core output router is unavailable")
        return self.output_resolver(role, name)

    def publish_bytes(
        self,
        role: str,
        name: str,
        data: bytes,
        *,
        requested_path: Path | None = None,
        domain_id: str | None = None,
        references: tuple[str, ...] = (),
    ) -> ResourceReference:
        """Publish through the Core-bound resource authority."""

        if self.durable_resources is None:
            raise ModuleError("the Core durable resource service is unavailable")
        self.check_cancelled()
        return self.durable_resources.publish_bytes(
            role, name, data, requested_path=requested_path,
            domain_id=domain_id, references=references,
        )

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
