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
        self._attempt_path()

    def _attempt_path(self) -> Path:
        return self._attempts._verified_path(self._reference)

    def _root(self) -> Path:
        # Reopen the exact cataloged attempt on every operation. The caller
        # cannot retarget this capability to another directory or attempt.
        return check_storage.ordinary(self._attempt_path() / "execution", directory=True)

    def copy_runtime(
        self, rows: list[dict], *, cancelled: Callable[[], bool] = lambda: False,
    ) -> Path:
        attempt = self._attempt_path()
        target = attempt / "execution"
        # Preserve V1 member modes, cancellation and partial-copy behavior by
        # delegating to the same Core copier at the fixed attempt-relative URI.
        check_storage.copy_manifest(attempt / "runtime", target, rows, cancelled=cancelled)
        return target

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
