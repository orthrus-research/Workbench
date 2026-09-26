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

from ..host_filesystem import (
    count_interrupted_create_once_stages, private_path,
    publish_create_once_bytes, read_private_single_link_bytes,
    secure_private_path,
)
from ..output_routing import _private_directory
from ..setup_cli import _state_root
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
        elif self.owner_id == "workbench-shell" and family == "cleanroom-fresh-bootstrap-v2":
            # The profile selects one V2 state root for its reviewed plan.
            # Keep its journal and receipt paths intact, including recovery
            # of a root created by the historical writer with mode 0755.
            if not base.is_absolute() or ".." in base.parts:
                raise DurableResourceError("resource.policy", "fresh bootstrap state root must be absolute")
            configuration = self.catalog.configuration_home
            if (
                selected == self.workspace or self.workspace.is_relative_to(selected)
                or selected == configuration or selected.is_relative_to(configuration)
                or configuration.is_relative_to(selected)
            ):
                raise DurableResourceError("resource.policy", "fresh bootstrap state root overlaps a protected root")
            try:
                _state_root(selected)
                if selected.exists():
                    info = selected.lstat()
                    if (
                        not stat.S_ISDIR(info.st_mode)
                        or (os.name != "nt" and (
                            info.st_uid != os.geteuid() or info.st_mode & 0o022
                        ))
                    ):
                        raise DurableResourceError(
                            "resource.unsafe", "fresh bootstrap state root is not owner-controlled",
                        )
            except DurableResourceError:
                raise
            except (OSError, ValueError) as exc:
                raise DurableResourceError("resource.unsafe", "fresh bootstrap state root is redirected") from exc
            root = selected
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

    def open_validation_invocation_target(self, target: Path) -> RecordStoreReference:
        """Register one explicitly selected validation result parent.

        Only validation's own diagnostic parent may be created or secured in
        place. Other selected parents must already have private custody; Core
        never changes permissions on an arbitrary external directory.
        """

        if self.owner_id != "validation" or not isinstance(target, Path) or not target.is_absolute():
            raise DurableResourceError("resource.policy", "explicit invocation needs an absolute validation target")
        if target.name in {"", ".", ".."} or ".." in target.parts:
            raise DurableResourceError("resource.policy", "explicit invocation target is not an exact file path")
        target = Path(os.path.abspath(target))
        parent = target.parent
        diagnostic = self.workspace / ".workbench/validation"
        for reserved in (
            diagnostic / "runs", diagnostic / "invocations",
            diagnostic / "test-timings", diagnostic / "ci",
        ):
            if target == reserved or target.is_relative_to(reserved):
                raise DurableResourceError("resource.policy", "explicit invocation overlaps Core validation storage")
        if target.is_relative_to(self.catalog.configuration_home):
            raise DurableResourceError("resource.policy", "explicit invocation overlaps Core configuration storage")
        try:
            _state_root(parent)
            if parent == diagnostic or parent.is_relative_to(diagnostic):
                _private_directory(parent)
                secure_private_path(parent, directory=True)
            elif not private_path(parent, directory=True):
                raise DurableResourceError(
                    "resource.unsafe", "explicit invocation parent must already be owner-private",
                )
            _state_root(parent)
        except DurableResourceError:
            raise
        except (OSError, ValueError) as exc:
            raise DurableResourceError("resource.unsafe", "explicit invocation parent is unavailable or redirected") from exc
        if not private_path(parent, directory=True):
            raise DurableResourceError("resource.unsafe", "explicit invocation parent lost private custody")
        family = "validation-invocation-explicit-v1"
        store_id = self.catalog.register_record_store(
            family=family, owner_id=self.owner_id, workspace=self.workspace, root=parent,
        )
        return RecordStoreReference(
            store_id=store_id, family=family, owner_id=self.owner_id,
            workspace=self.workspace, root=parent,
            retention="protected-until-reviewed-policy",
        )

    def publish_review_artifact(
        self, family: str, target: Path, data: bytes, *, byte_limit: int,
    ) -> RecordStoreReference:
        """Publish one owner-encoded plan at an exact, protected selected path."""

        if self.owner_id != "workbench-shell" or family != "cleanroom-construction-plan-v2":
            raise DurableResourceError("resource.policy", "review artifact family is unsupported")
        if (
            not isinstance(target, Path) or not target.is_absolute()
            or target.name in {"", ".", ".."} or ".." in target.parts
            or type(data) is not bytes or type(byte_limit) is not int
            or not 0 < len(data) <= byte_limit <= 16 * 1024 * 1024
        ):
            raise DurableResourceError("resource.policy", "review artifact requires exact bounded bytes and path")
        target = Path(os.path.abspath(target))
        if target.is_relative_to(self.catalog.configuration_home):
            raise DurableResourceError("resource.policy", "review artifact overlaps Core configuration storage")
        parent = target.parent
        try:
            _state_root(parent)
            existing = parent
            while not existing.exists():
                existing = existing.parent
            if not private_path(existing, directory=True):
                raise DurableResourceError(
                    "resource.unsafe", "review artifact parent must be owner-private",
                )
            _private_directory(parent)
            if not private_path(parent, directory=True):
                raise DurableResourceError(
                    "resource.unsafe", "review artifact parent lost private custody",
                )
            before = parent.lstat()
            if count_interrupted_create_once_stages(target):
                raise DurableResourceError(
                    "resource.incomplete", "interrupted review artifact publication requires review",
                )
        except DurableResourceError:
            raise
        except (OSError, ValueError) as exc:
            raise DurableResourceError("resource.unsafe", "review artifact parent is unavailable or redirected") from exc
        store_id = self.catalog.register_record_store(
            family=family, owner_id=self.owner_id, workspace=self.workspace, root=parent,
        )
        _state_root(parent)
        after_registration = parent.lstat()
        if (before.st_dev, before.st_ino) != (after_registration.st_dev, after_registration.st_ino):
            raise DurableResourceError("resource.changed", "review artifact parent changed during registration")
        publish_create_once_bytes(target, data, byte_limit=byte_limit)
        if read_private_single_link_bytes(target, byte_limit=byte_limit) != data:
            raise DurableResourceError("resource.changed", "review artifact changed after publication")
        _state_root(parent)
        after_publication = parent.lstat()
        if (before.st_dev, before.st_ino) != (after_publication.st_dev, after_publication.st_ino):
            raise DurableResourceError("resource.changed", "review artifact parent changed during publication")
        return RecordStoreReference(
            store_id=store_id, family=family, owner_id=self.owner_id,
            workspace=self.workspace, root=parent,
            retention="protected-until-reviewed-policy",
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
