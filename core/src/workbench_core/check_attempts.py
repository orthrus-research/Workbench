"""Core adapter for retained check attempts and historical snapshot storage."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from workbench_api.check_attempts import CheckAttemptError, CheckSnapshotReader
from workbench_api.managed_attempts import ManagedAttemptReference
from workbench_api.retained_snapshots import RetainedSnapshotAdmission

from . import check_lifecycle, check_snapshots, check_storage
from .managed_attempts import CoreManagedAttempts
from .retained_snapshots import open_retained_snapshot


class CoreCheckAttempts:
    def __init__(self, attempts: CoreManagedAttempts, *, check_cancelled=lambda: None):
        self.attempts = attempts
        self.check_cancelled = check_cancelled

    def _cancelled(self, cancelled: Callable[[], bool]) -> bool:
        try:
            self.check_cancelled()
        except Exception:
            return True
        return cancelled()

    def _path(self, attempt: ManagedAttemptReference) -> Path:
        return self.attempts._verified_path(attempt)

    @staticmethod
    def _admission(admission: RetainedSnapshotAdmission) -> RetainedSnapshotAdmission:
        if (not isinstance(admission, RetainedSnapshotAdmission)
                or not callable(admission.scope)
                or not isinstance(admission.expected, Mapping)
                or not isinstance(admission.supported_schemas, frozenset)):
            raise CheckAttemptError("retained snapshot needs explicit owner admission")
        return admission

    def allocate(self, family: str, prefix: str, *, requested_root: Path,
                 workspace: Path | None = None) -> ManagedAttemptReference:
        self.check_cancelled()
        return self.attempts.allocate(family, prefix, requested_root=requested_root, workspace=workspace)

    def open_attempt(self, family: str, prefix: str, attempt_id: str, *,
                     requested_root: Path) -> ManagedAttemptReference:
        return self.attempts.open(family, prefix, attempt_id, requested_root=requested_root)

    @contextmanager
    def execution(self, attempt: ManagedAttemptReference) -> Iterator[None]:
        self._path(attempt)
        with check_lifecycle.lease(attempt.root), self.attempts.execution(attempt):
            yield

    def active(self, attempt: ManagedAttemptReference) -> bool:
        return self.attempts.active(attempt)

    def cancellation_requested(self, attempt: ManagedAttemptReference, binding: str) -> bool:
        path = self._path(attempt) / "cancel.json"
        if not path.exists() and not path.is_symlink():
            return False
        if check_storage.read_json(path) != {"request_id": binding}:
            raise CheckAttemptError("retained check cancellation belongs to another request")
        return True

    def request_cancel(self, attempt: ManagedAttemptReference, binding: str) -> None:
        self.attempts.request_cancel(attempt, binding)

    def publish_snapshot(self, attempt: ManagedAttemptReference, source: str, *,
                         scope: Mapping[str, Any], describe: Callable, verify: Callable,
                         cancelled: Callable[[], bool] = lambda: False) -> dict:
        path = self._path(attempt)
        if source != "result.json" or not callable(describe) or not callable(verify):
            raise CheckAttemptError("retained snapshot needs an admitted result and owner callbacks")
        self.check_cancelled()
        with check_lifecycle.lease(attempt.root):
            return check_snapshots.publish(
                path, path / source, scope=scope, describe=describe, verify=verify,
                cancelled=lambda: self._cancelled(cancelled),
            )

    def register_snapshot(self, attempt: ManagedAttemptReference, *, inputs: Mapping[str, str],
                          source_directories: tuple[str, ...] = (), context: Mapping[str, Any] | None = None,
                          references: tuple[str, ...] = (), reproduction: tuple[dict, ...] = ()) -> dict:
        path = self._path(attempt)
        return check_lifecycle.register(
            attempt.root, path, inputs=dict(inputs), source_directories=source_directories,
            context=dict(context or {}), references=references, reproduction=reproduction,
        )

    @contextmanager
    def read_snapshot(self, attempt: ManagedAttemptReference, *, admission: RetainedSnapshotAdmission,
                      owner_id: str | None = None, admit: Callable | None = None,
                      expected_context: Mapping[str, Any] | None = None,
                      cancelled: Callable[[], bool] = lambda: False) -> Iterator[CheckSnapshotReader]:
        selected = self._admission(admission)
        path = self._path(attempt)
        custody = path / check_lifecycle.MANIFEST
        ledger = attempt.root / ".workbench/runtime-manager/checks" / (attempt.attempt_id + ".json")
        with check_lifecycle.lease(attempt.root):
            if any(candidate.exists() or candidate.is_symlink() for candidate in (custody, ledger)):
                if not isinstance(owner_id, str) or not owner_id or not callable(admit):
                    raise CheckAttemptError("registered snapshot needs an installed owner admission callback")
                with open_retained_snapshot(
                    path, owner_id=owner_id, admit=admit,
                    expected_context=expected_context,
                    cancelled=lambda: self._cancelled(cancelled),
                ) as opened:
                    if any(opened.manifest.get(key) != value for key, value in selected.expected.items()):
                        raise CheckAttemptError("owner admission differs from selected snapshot")
                    yield opened
                return
            # Snapshots predating check-custody registration remain readable
            # under the owner's historical scope and exact request binding.
            with check_snapshots.Snapshot(
                path, scope=selected.scope, expected=dict(selected.expected),
                supported_schemas=selected.supported_schemas,
                cancelled=lambda: self._cancelled(cancelled),
            ) as opened:
                yield opened

    def rebuild_snapshot(self, attempt: ManagedAttemptReference, *, admission: RetainedSnapshotAdmission,
                         cancelled: Callable[[], bool] = lambda: False) -> dict:
        selected = self._admission(admission)
        path = self._path(attempt)
        with check_lifecycle.lease(attempt.root):
            return check_snapshots.rebuild(
                path, scope=selected.scope, expected=dict(selected.expected),
                cancelled=lambda: self._cancelled(cancelled),
            )

    def reconcile_snapshot(self, attempt: ManagedAttemptReference, *,
                           admission: RetainedSnapshotAdmission) -> list[dict]:
        selected = self._admission(admission)
        path = self._path(attempt)
        self.check_cancelled()
        with check_lifecycle.lease(attempt.root):
            return check_snapshots.recover(
                path, scope=selected.scope, expected=dict(selected.expected),
            )


__all__ = ["CoreCheckAttempts"]
