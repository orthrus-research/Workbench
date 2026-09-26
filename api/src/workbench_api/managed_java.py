"""Core-owned JDK acquisition port for module operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ManagedJava(Protocol):
    def ensure(self, suite_root: Path, *, config_path: Path) -> dict[str, Any]: ...

    def for_execution(self, suite_root: Path, *, config_path: Path) -> dict[str, Any]: ...


__all__ = ["ManagedJava"]
