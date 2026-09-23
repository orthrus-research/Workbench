"""Explicit constructor services supplied to downstream runtime composition."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ReviewedPlanPorts:
    validate: Callable[[Mapping[str, Any]], dict[str, Any]]
    verify: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
    workspace: Callable[[Mapping[str, Any]], Path]

    def __post_init__(self) -> None:
        if not all(
            callable(value) for value in (self.validate, self.verify, self.workspace)
        ):
            raise ValueError(
                "reviewed plan ports require validation, verification and workspace owners"
            )
