"""Core publication contract for one retained validation invocation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ValidationInvocationRecord(Protocol):
    @property
    def path(self) -> Path: ...

    def write(self, payload: bytes) -> None:
        """Publish the next exact revision, refusing changed prior evidence."""
        ...


__all__ = ["ValidationInvocationRecord"]
