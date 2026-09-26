"""Core placement, registration, leases and cancellation for retained attempts.

The resource catalog protects an attempt namespace before its first allocation.
Individual attempt closure and dependency registration are a later contract;
the namespace remains protected from automatic collection in the meantime.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from hashlib import sha256
from typing import Mapping

from workbench_api.managed_attempts import ManagedAttemptError, ManagedAttemptReference

from . import check_storage
from .setup_cli import _workspace
from .storage.registered import ResourceCatalog


_FAMILY = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_PREFIX = re.compile(r"[a-z][a-z-]{0,47}\Z")
_OWNER = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")


class CoreManagedAttempts:
    def __init__(
        self, *, workspace: Path, configuration_home: Path, state_root: Path,
        locations: Mapping[str, Path], owner_id: str,
    ):
        if not workspace.is_absolute() or not configuration_home.is_absolute() or not state_root.is_absolute():
            raise ManagedAttemptError("managed attempt context requires absolute local roots")
        if not isinstance(owner_id, str) or _OWNER.fullmatch(owner_id) is None:
            raise ManagedAttemptError("managed attempt owner is invalid")
        self.workspace = self._selected_workspace(workspace)
        self.state_root = state_root
        self.locations = dict(locations)
        self.owner_id = owner_id
        self.catalog = ResourceCatalog(configuration_home)

    @staticmethod
    def _names(family: str, prefix: str) -> None:
        if not _FAMILY.fullmatch(family) or not _PREFIX.fullmatch(prefix):
            raise ManagedAttemptError("managed attempt family or prefix is invalid")

    @staticmethod
    def _absolute(value: Path) -> Path:
        if not isinstance(value, Path):
            raise ManagedAttemptError("managed attempt root must be a path")
        return Path(os.path.abspath(value.expanduser()))

    @staticmethod
    def _selected_workspace(value: Path) -> Path:
        if not isinstance(value, Path):
            raise ManagedAttemptError("managed attempt workspace must be a path")
        try:
            return _workspace(value)
        except ValueError as exc:
            raise ManagedAttemptError(f"managed attempt workspace is unavailable: {exc}") from exc

    def _owner_workspace(self, value: Path | None) -> Path:
        return self.workspace if value is None else self._selected_workspace(value)

    def default_root(self, family: str, *, workspace: Path | None = None) -> Path:
        if not _FAMILY.fullmatch(family):
            raise ManagedAttemptError("managed attempt family is invalid")
        evidence = self.locations.get("evidence")
        if evidence is None or not evidence.is_absolute():
            raise ManagedAttemptError("Core did not resolve an evidence store for managed attempts")
        selected = self._owner_workspace(workspace)
        identity = sha256(os.fsencode(os.path.normcase(str(selected)))).hexdigest()
        return evidence / "attempts" / ("workspace-" + identity) / self.owner_id / family

    def _register(self, family: str, root: Path, workspace: Path) -> str:
        directory = root / ".workbench/check-attempts"
        check_storage.ordinary(directory, directory=True)
        return self.catalog.register_record_store(
            family=family, owner_id=self.owner_id,
            workspace=workspace, root=directory,
        )

    def allocate(
        self, family: str, prefix: str, *, requested_root: Path | None = None,
        workspace: Path | None = None,
    ) -> ManagedAttemptReference:
        self._names(family, prefix)
        selected = self._owner_workspace(workspace)
        root = self.default_root(family, workspace=selected) if requested_root is None else self._absolute(requested_root)
        check_storage.initialize(root)
        store_id = self._register(family, root, selected)
        path = check_storage.allocate_attempt(root, prefix)
        return ManagedAttemptReference(family, path.name, root, path, store_id)

    def open(
        self, family: str, prefix: str, attempt_id: str, *,
        requested_root: Path | None = None, legacy_basename: str | None = None,
    ) -> ManagedAttemptReference:
        self._names(family, prefix)
        if not isinstance(attempt_id, str) or re.fullmatch(re.escape(prefix) + r"-[0-9a-f]{32}", attempt_id) is None:
            raise ManagedAttemptError("select an exact managed attempt ID")
        if legacy_basename is not None and not _FAMILY.fullmatch(legacy_basename):
            raise ManagedAttemptError("legacy attempt location is invalid")
        selected_root = self._absolute(requested_root) if requested_root is not None else None
        cataloged = self.catalog.inventory()["record_stores"]
        candidates: list[tuple[Path, Path, str | None]] = []
        for row in cataloged:
            directory = Path(row["path"])
            if (row["family"] != family or row["owner_id"] != self.owner_id
                    or directory.name != "check-attempts" or directory.parent.name != ".workbench"):
                continue
            root = directory.parent.parent
            if selected_root is not None and root != selected_root:
                continue
            path = directory / attempt_id
            if path.exists() or path.is_symlink():
                candidates.append((root, path, row["store_id"]))
        # These locations predate catalog registration. A cataloged root is
        # already covered above; keep distinct registrations ambiguous.
        fallback_roots = ([selected_root] if selected_root is not None else [
            self.default_root(family),
            self.locations["evidence"] / "attempts" / self.owner_id / family,
            *([self.state_root / legacy_basename] if legacy_basename is not None else []),
        ])
        cataloged_roots = {root for root, _, _ in candidates}
        for root in fallback_roots:
            if root is None or root in cataloged_roots:
                continue
            path = root / ".workbench/check-attempts" / attempt_id
            if path.exists() or path.is_symlink():
                candidates.append((root, path, None))
        if len(candidates) != 1:
            raise ManagedAttemptError(
                "managed attempt is unavailable" if not candidates
                else "managed attempt identity is ambiguous across selected stores"
            )
        root, path, store_id = candidates[0]
        check_storage.ordinary(path, directory=True)
        if store_id is None:
            store_id = self._register(family, root, self.workspace)
        return ManagedAttemptReference(family, attempt_id, root, path, store_id)

    def _verified_path(self, attempt: ManagedAttemptReference) -> Path:
        if (not isinstance(attempt, ManagedAttemptReference)
                or not isinstance(attempt.family, str) or not _FAMILY.fullmatch(attempt.family)
                or not isinstance(attempt.attempt_id, str)
                or re.fullmatch(r"[a-z][a-z-]{0,47}-[0-9a-f]{32}", attempt.attempt_id) is None
                or not isinstance(attempt.root, Path) or not isinstance(attempt.path, Path)
                or not isinstance(attempt.store_id, str)):
            raise ManagedAttemptError("managed attempt reference is invalid")
        expected = attempt.root / ".workbench/check-attempts" / attempt.attempt_id
        if attempt.path != expected:
            raise ManagedAttemptError("managed attempt reference path changed")
        rows = [row for row in self.catalog.inventory()["record_stores"]
                if row["store_id"] == attempt.store_id]
        if (len(rows) != 1 or rows[0]["family"] != attempt.family
                or rows[0]["owner_id"] != self.owner_id
                or rows[0]["path"] != str(expected.parent)
                or rows[0]["status"] != "available"):
            raise ManagedAttemptError("managed attempt reference is outside Core custody")
        return check_storage.ordinary(expected, directory=True)

    def execution(self, attempt: ManagedAttemptReference):
        return check_storage.execution_lock(self._verified_path(attempt))

    def active(self, attempt: ManagedAttemptReference) -> bool:
        return check_storage.execution_active(self._verified_path(attempt))

    def cancellation_requested(self, attempt: ManagedAttemptReference) -> bool:
        path = self._verified_path(attempt) / "cancel.json"
        if not path.exists() and not path.is_symlink():
            return False
        value = check_storage.read_json(path)
        if (not isinstance(value, dict) or set(value) != {"request_id"}
                or not isinstance(value["request_id"], str) or not value["request_id"]):
            raise ManagedAttemptError("managed attempt cancellation marker changed")
        return True

    def request_cancel(self, attempt: ManagedAttemptReference, binding: str) -> None:
        if not isinstance(binding, str) or not binding:
            raise ManagedAttemptError("managed attempt cancellation requires an owner binding")
        path = self._verified_path(attempt) / "cancel.json"
        marker = {"request_id": binding}
        if path.exists() or path.is_symlink():
            if check_storage.read_json(path) != marker:
                raise ManagedAttemptError("managed attempt cancellation binding changed")
        else:
            check_storage.write_json(path, marker)


__all__ = ["CoreManagedAttempts"]
