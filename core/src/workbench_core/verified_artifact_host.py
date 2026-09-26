"""Core implementation of exact input acquisition and cache reopening."""

from __future__ import annotations

from pathlib import Path

from workbench_api.verified_artifacts import VerifiedArtifact, VerifiedArtifactError

from .artifact_store import ArtifactStoreError, fetch_verified_artifact


class CoreVerifiedArtifactHost:
    def acquire(
        self, *, url: str, expected_sha256: str, expected_size: int,
        state_root: Path, label: str, timeout_seconds: float,
        user_agent: str,
    ) -> VerifiedArtifact:
        selected = Path(state_root).expanduser()
        if not selected.is_absolute():
            raise VerifiedArtifactError("artifact state root must be absolute")
        try:
            path, outcome = fetch_verified_artifact(
                url=url, expected_sha256=expected_sha256,
                expected_size=expected_size, state_root=selected,
                label=label, timeout_seconds=timeout_seconds,
                user_agent=user_agent,
            )
        except ArtifactStoreError as exc:
            raise VerifiedArtifactError(str(exc)) from exc
        return VerifiedArtifact(
            path=path, sha256=expected_sha256, size=expected_size,
            outcome=outcome,
        )


HOST = CoreVerifiedArtifactHost()


__all__ = ["CoreVerifiedArtifactHost", "HOST"]
