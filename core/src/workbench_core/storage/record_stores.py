"""Core placement and custody for the first mutable record families."""

from __future__ import annotations

import os
from pathlib import Path

from workbench_api.record_stores import RecordStoreReference
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
        if self.owner_id == "workbench-shell" and family == "work-session-v2":
            root = selected / ".workbench/sessions/work-session-v2"
        elif self.owner_id == "workbench-shell" and family == "feature-change-session-context-v1":
            root = default_product_spine_state_root(selected) / family
        elif self.owner_id == "blueprints" and family == "blueprints-sealed-v1" and selected.name == "sealed":
            # Blueprints admits the target and protected session before it
            # requests this historical CAS namespace. Keep its V1 locators.
            root = selected
        elif self.owner_id == "blueprints" and family == "blueprints-dependency-cache-v1":
            # The environment lock owns each digest key below the selected
            # historical cache root; Core registers custody of that root.
            root = selected
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


__all__ = ["CoreRecordStores"]
