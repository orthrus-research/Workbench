"""Core placement and custody for the first mutable record families."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from contextlib import contextmanager
from typing import Iterator

from workbench_api.record_stores import RecordStoreReference, SessionOwnerAllocation
from workbench_api.state_paths import default_product_spine_state_root
from workbench_api.durable_resources import DurableResourceError

from ..host_filesystem import private_path, secure_private_path
from ..output_routing import _private_directory
from .registered import ResourceCatalog


class CoreRecordStores:
    """Select a namespace; the owner still defines keys and record semantics."""

    def __init__(self, *, workspace: Path, configuration_home: Path, owner_id: str):
        self.workspace = workspace
        self.owner_id = owner_id
        self.catalog = ResourceCatalog(configuration_home)

    def open(self, family: str, base: Path) -> RecordStoreReference:
        if not isinstance(base, Path):
            raise DurableResourceError("resource.policy", "record store owner or base is unsupported")
        selected = Path(os.path.abspath(base.expanduser()))
        if self.owner_id == "blueprints" and family.startswith("blueprints-"):
            protected = self.workspace / ".workbench/blueprints"
            if selected == protected or not selected.is_relative_to(protected):
                raise DurableResourceError(
                    "resource.policy", "Blueprints store is outside the selected target workspace",
                )
        if self.owner_id == "workbench-shell" and family == "work-session-v2":
            root = selected / ".workbench/sessions/work-session-v2"
        elif self.owner_id == "workbench-shell" and family == "feature-change-session-context-v1":
            root = default_product_spine_state_root(selected) / family
        elif self.owner_id == "workbench-shell" and family == "active-instance-v1":
            # Retain the per-workspace selector's existing file URIs while
            # protecting its parent as a mutable Core record namespace.
            root = selected / "active-instances"
        elif self.owner_id == "blueprints" and family == "blueprints-sealed-v1" and selected.name == "sealed":
            # Blueprints admits the target and protected session before it
            # requests this historical CAS namespace. Keep its V1 locators.
            root = selected
        elif self.owner_id == "blueprints" and family == "blueprints-dependency-cache-v1":
            # The environment lock owns each digest key below the selected
            # historical cache root; Core registers custody of that root.
            root = selected
        elif self.owner_id == "blueprints" and family == "blueprints-simulation-evidence-v1":
            # Keep the V1 evidence locator's exact selected CAS root while
            # registering its physical custody with Core.
            root = selected
        elif self.owner_id == "blueprints" and family == "blueprints-artifact-v1":
            # Release, history and session callers select distinct historical
            # CAS roots. Keep their V1 object keys and locators unchanged.
            root = selected
        elif self.owner_id == "blueprints" and family == "blueprints-session-pointer-v1":
            # The session's current.json stays beside its historical CAS root.
            root = selected
        elif self.owner_id == "blueprints" and family == "blueprints-history-transaction-v1":
            # Keep V1's recovery markers beside the history CAS. Their exact
            # selected root remains registered even after a failed rollback.
            root = selected
        elif self.owner_id == "validation" and family == "validation-timings-v1" and selected == self.workspace:
            # Keep the scheduler's historical latest-report lookup while
            # registering its mutable diagnostic namespace with Core.
            root = selected / ".workbench/validation/test-timings"
        elif self.owner_id == "validation" and family == "validation-ci-plan-v1" and selected == self.workspace:
            # The workflow uploads this exact historical plan directory.
            root = selected / ".workbench/validation/ci"
        elif self.owner_id == "validation" and family == "validation-invocations-v1" and selected == self.workspace:
            # Keep validation's per-invocation V1 result URI inside the checkout.
            root = selected / ".workbench/validation/invocations"
        else:
            raise DurableResourceError("resource.policy", "record store family is unsupported")
        _private_directory(root)
        secure_private_path(root, directory=True)
        if not private_path(root, directory=True):
            raise DurableResourceError("resource.unsafe", "record store cannot enforce private custody")
        store_id = self.catalog.register_record_store(
            family=family, owner_id=self.owner_id,
            workspace=self.workspace, root=root,
        )
        return RecordStoreReference(
            store_id=store_id, family=family, owner_id=self.owner_id,
            workspace=self.workspace, root=root,
            retention="protected-until-reviewed-policy",
        )

    def open_target(
        self, family: str, state_root: Path, target_workspace: Path,
    ) -> RecordStoreReference:
        """Bind a Shell-owned external binding to its explicit target workspace."""

        if self.owner_id != "workbench-shell" or family not in {
            "project-qualification-v1", "workspace-home-adoption-v2",
        }:
            raise DurableResourceError("resource.policy", "target record-store family is unsupported")
        if any(
            not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts
            for value in (state_root, target_workspace)
        ):
            raise DurableResourceError("resource.policy", "target record-store paths must be absolute")
        state = Path(os.path.abspath(state_root))
        target = Path(os.path.abspath(target_workspace))
        identities: dict[Path, tuple[int, int]] = {}
        try:
            for value in (state, target):
                for component in (value, *value.parents):
                    info = component.lstat()
                    if not stat.S_ISDIR(info.st_mode) or getattr(component, "is_junction", lambda: False)():
                        raise ValueError("record-store path traverses a redirect")
                    identities[component] = (info.st_dev, info.st_ino)
        except (OSError, ValueError) as exc:
            raise DurableResourceError("resource.unsafe", "target or state root is unsafe") from exc
        if family == "project-qualification-v1":
            if state == target or state.is_relative_to(target):
                raise DurableResourceError("resource.policy", "qualification state overlaps its target")
            root = state / "project-qualification-v1/bindings"
        else:
            root = state / "workspace-home-v2/adoptions"
        _private_directory(root)
        secure_private_path(root, directory=True)
        if not private_path(root, directory=True):
            raise DurableResourceError("resource.unsafe", "target record store cannot enforce private custody")
        store_id = self.catalog.register_record_store(
            family=family, owner_id=self.owner_id, workspace=target, root=root,
        )
        try:
            if any(
                (info.st_dev, info.st_ino) != identities[component]
                for component in identities
                for info in (component.lstat(),)
            ):
                raise ValueError("target or state root changed")
        except (OSError, ValueError) as exc:
            raise DurableResourceError("resource.changed", "target or state root changed during registration") from exc
        return RecordStoreReference(
            store_id=store_id, family=family, owner_id=self.owner_id,
            workspace=target, root=root, retention="protected-until-reviewed-policy",
        )

    @contextmanager
    def session_owner(
        self, family: str, base: Path, session_id: str, *, create: bool,
    ) -> Iterator[SessionOwnerAllocation]:
        if (
            self.owner_id != "workbench-shell"
            or family != "feature-change-session-context-v1"
            or not isinstance(base, Path)
            or Path(os.path.abspath(base.expanduser())) != self.workspace
        ):
            raise DurableResourceError(
                "resource.policy", "session owner requires the suite-bound Core record store",
            )
        from ..session_owner_allocations import CoreSessionOwnerAllocations

        store = self.open(family, base)
        with CoreSessionOwnerAllocations(store, session_id).open(create=create) as allocation:
            yield allocation


__all__ = ["CoreRecordStores"]
