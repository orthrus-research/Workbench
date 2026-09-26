"""Owner-neutral contracts for Core-managed durable resources.

The host binds this port to one admitted capability and resolved environment.
Modules supply bytes and domain meaning; Core chooses custody and publishes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class DurableResourceError(ValueError):
    """A resource cannot be published or reopened under its bound policy."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResourceReference:
    resource_id: str
    store_id: str
    owner_id: str
    role: str
    path: Path
    bytes: int
    sha256: str
    policy_id: str | None
    domain_id: str | None = None


class DurableResources(Protocol):
    """One capability's Core-bound immutable resource service."""

    def publish_bytes(
        self,
        role: str,
        name: str,
        data: bytes,
        *,
        requested_path: Path | None = None,
        domain_id: str | None = None,
        references: tuple[str, ...] = (),
    ) -> ResourceReference: ...

    def describe(self, resource_id: str) -> ResourceReference: ...

    def read_bytes(self, resource_id: str) -> bytes: ...


__all__ = ["DurableResourceError", "DurableResources", "ResourceReference"]
