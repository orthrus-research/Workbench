"""Physical record custody supplied by Core to durable service owners.

The owner declares record names and validates their contents. Core creates
private directories, publishes bytes, and coordinates physical leases.
"""

from __future__ import annotations

from pathlib import Path
from typing import ContextManager, Protocol


class ServiceRecordBackend(Protocol):
    root: Path

    def namespace(self, name: str) -> Path: ...

    def allocate_directory(self, path: Path) -> None: ...

    def read(self, path: Path) -> bytes: ...

    def publish_immutable(self, path: Path, raw: bytes) -> None: ...

    def replace(self, path: Path, raw: bytes) -> None: ...

    def exclusive(self, key: str) -> ContextManager[None]: ...


__all__ = ["ServiceRecordBackend"]
