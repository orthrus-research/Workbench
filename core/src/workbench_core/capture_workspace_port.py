"""Exact managed-attempt binding for Core's existing capture workspace tools."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from workbench_api.capture_workspaces import CaptureWorkspaceHostError
from workbench_api.managed_attempts import ManagedAttemptReference, managed_attempts

from . import capture_workspace, check_storage
from .filesystem_paths import native_path
from .managed_attempts import CoreManagedAttempts


class _ExecutionWorkspace:
    def __init__(self, attempts: CoreManagedAttempts, reference: ManagedAttemptReference):
        self._attempts = attempts
        self._reference = reference
        self._root()

    def _root(self) -> Path:
        # Reopen the exact cataloged attempt on every operation. The caller
        # cannot retarget this capability to another directory or attempt.
        attempt = self._attempts._verified_path(self._reference)
        return check_storage.ordinary(attempt / "execution", directory=True)

    def read_optional(self, relative: str) -> bytes | None:
        root = self._root()
        target = root.joinpath(*capture_workspace._parts(relative))
        return check_storage.read_bytes(target) if native_path(target).exists() else None

    def replace_file(
        self, relative: str, raw: bytes, *, expected_sha256: str | None = None,
    ) -> dict:
        return capture_workspace.replace_file(
            self._root(), relative, raw, expected_sha256=expected_sha256,
        )

    def inventory(self, *, cancelled: Callable[[], bool] = lambda: False) -> list[dict]:
        return capture_workspace.inventory(self._root(), cancelled=cancelled)


class CoreCaptureWorkspaces:
    def execution(self, attempt: ManagedAttemptReference) -> _ExecutionWorkspace:
        selected = managed_attempts()
        if not isinstance(selected, CoreManagedAttempts):
            raise CaptureWorkspaceHostError("capture execution needs a Core managed-attempt host")
        return _ExecutionWorkspace(selected, attempt)


HOST = CoreCaptureWorkspaces()


__all__ = ["CoreCaptureWorkspaces", "HOST"]
