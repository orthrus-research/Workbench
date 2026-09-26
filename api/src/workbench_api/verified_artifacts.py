"""Core-hosted acquisition and verified reuse of exact input artifacts.

The owner supplies its trusted URL, digest and size. Core chooses the cache
layout, verifies retained bytes before reuse and returns the exact local path
for the consuming tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class VerifiedArtifactError(ValueError):
    """An exact input could not be acquired or verified."""


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    path: Path
    sha256: str
    size: int
    outcome: str  # downloaded or reused


class VerifiedArtifactHost(Protocol):
    def acquire(
        self, *, url: str, expected_sha256: str, expected_size: int,
        state_root: Path, label: str, timeout_seconds: float,
        user_agent: str,
    ) -> VerifiedArtifact: ...


_host: VerifiedArtifactHost | None = None


def bind_verified_artifact_host(host: VerifiedArtifactHost) -> None:
    global _host
    if not callable(getattr(host, "acquire", None)):
        raise VerifiedArtifactError("verified artifact host does not implement acquisition")
    if _host is not None and _host is not host:
        raise VerifiedArtifactError("a different verified artifact host is already bound")
    _host = host


def acquire_verified_artifact(
    *, url: str, expected_sha256: str, expected_size: int,
    state_root: Path, label: str, timeout_seconds: float = 30.0,
    user_agent: str = "Workbench-Artifact-Store/0.1",
) -> VerifiedArtifact:
    if _host is None:
        raise VerifiedArtifactError(
            "no verified artifact host is bound; invoke through Workbench Core"
        )
    return _host.acquire(
        url=url, expected_sha256=expected_sha256,
        expected_size=expected_size, state_root=state_root, label=label,
        timeout_seconds=timeout_seconds, user_agent=user_agent,
    )


__all__ = [
    "VerifiedArtifact", "VerifiedArtifactError", "VerifiedArtifactHost",
    "acquire_verified_artifact", "bind_verified_artifact_host",
]
