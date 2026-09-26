"""Resource catalog root identity and conservative V1 migration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import DurableResourceError
from workbench_core.storage.registered import CoreDurableResources, ResourceCatalog
from workbench_core.temporary_leases import CoreTemporaryLeases


class CatalogRootTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.evidence = self.home / "selected-evidence"
        self.config = self.home / "config"
        self.resources = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="sample",
            policy_id="fixture-policy", location_sources={"evidence": "user-settings"},
        )

    def test_catalog_root_manifest_marks_first_install_and_survives_restart(self) -> None:
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.verify_root(), "unproven-empty-home")
        reference = self.resources.publish_bytes("evidence", "first.json", b"first\n")
        outer = self.config / "resource-catalog-root-v1.json"
        inner = self.config / "resources-v1/.resource-catalog-root-v1.json"
        original = outer.read_bytes()
        self.assertEqual(original, inner.read_bytes())
        marker = json.loads(original)
        self.assertEqual(marker["format"], "workbench-resource-catalog-root-v1")
        self.assertEqual(marker["generation"], 1)
        self.assertEqual(marker["migration_origin"], "empty-home-first-use")
        self.assertEqual(marker["catalog_format"], "workbench-resource-catalog-v1")
        self.assertIn("working-allocations", marker["known_namespaces"])
        self.assertEqual(ResourceCatalog(self.config).verify_root(), "ready-unproven")
        self.assertEqual(ResourceCatalog(self.config).read_bytes(reference.resource_id), b"first\n")
        self.resources.publish_bytes("evidence", "second.json", b"second\n")
        self.assertEqual(outer.read_bytes(), original)

    def test_historical_v1_catalog_gains_root_manifest_without_rewriting_records(self) -> None:
        catalog = ResourceCatalog(self.config)
        historical_dirs = ("reservations", "intents", "commits", "aborts", "leases", "stores")

        def old_ensure(selected: ResourceCatalog) -> None:
            for directory in (selected.root, *(selected.root / name for name in historical_dirs)):
                directory.mkdir(parents=True, mode=0o700, exist_ok=True)

        store = self.home / "historical-store"
        store.mkdir(mode=0o700)
        with patch.object(ResourceCatalog, "_ensure", old_ensure):
            store_id = catalog.register_record_store(
                family="historical", owner_id="sample",
                workspace=self.workspace, root=store,
            )
        self.assertEqual(catalog.verify_root(), "legacy")
        historical = next((catalog.root / "stores").glob("*.json"))
        original = historical.read_bytes()
        catalog._ensure()
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertEqual(historical.read_bytes(), original)
        self.assertEqual(
            json.loads((self.config / "resource-catalog-root-v1.json").read_bytes())["migration_origin"],
            "legacy-v1",
        )
        self.assertEqual(
            [row["store_id"] for row in catalog.inventory(workspace=self.workspace)["record_stores"]],
            [store_id],
        )

    def test_missing_or_split_root_binding_never_initializes_empty_catalog(self) -> None:
        reference = self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        root = catalog.root
        lost = self.config / "resources-removed"
        root.rename(lost)
        with self.assertRaises(DurableResourceError) as missing:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual(missing.exception.code, "resource.unavailable")
        with self.assertRaises(DurableResourceError):
            catalog._ensure()
        self.assertFalse(root.exists())
        self.assertEqual(reference.path.read_bytes(), b"retained\n")
        lost.rename(root)
        (self.config / "resource-catalog-root-v1.json").unlink()
        with self.assertRaises(DurableResourceError) as split:
            catalog.verify_root()
        self.assertEqual(split.exception.code, "resource.unavailable")
        with self.assertRaises(DurableResourceError):
            catalog._ensure()

    def test_total_root_loss_remains_unproven_if_first_use_must_continue(self) -> None:
        retained = self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        catalog.root.rename(self.config / "resources-lost")
        (self.config / "resource-catalog-root-v1.json").rename(
            self.config / "resource-catalog-root-lost.json"
        )
        self.assertEqual(catalog.verify_root(), "unproven")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "unproven")
        fresh = self.resources.publish_bytes("evidence", "new.json", b"new\n")
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertEqual(catalog.read_bytes(fresh.resource_id), b"new\n")
        self.assertEqual(retained.path.read_bytes(), b"retained\n")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "ready-unproven")

    def test_corrupt_root_manifest_is_not_migrated_or_replaced(self) -> None:
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        catalog = ResourceCatalog(self.config)
        marker = self.config / "resource-catalog-root-v1.json"
        marker.write_bytes(b"{}\n")
        with self.assertRaises(DurableResourceError) as corrupt:
            catalog.verify_root()
        self.assertEqual(corrupt.exception.code, "resource.changed")
        with self.assertRaises(DurableResourceError):
            catalog._ensure()
        self.assertEqual(marker.read_bytes(), b"{}\n")

    def test_preexisting_preferences_allow_first_resource_but_keep_unproven_coverage(self) -> None:
        self.config.mkdir(mode=0o700)
        (self.config / "setup-v1.json").write_bytes(b"{}\n")
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.verify_root(), "unproven")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "unproven")
        reference = self.resources.publish_bytes("evidence", "first.json", b"first\n")
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertEqual(catalog.read_bytes(reference.resource_id), b"first\n")
        self.assertEqual(catalog.inventory(workspace=self.workspace)["root_state"], "ready-unproven")
        marker = json.loads((self.config / "resource-catalog-root-v1.json").read_bytes())
        self.assertEqual(marker["migration_origin"], "unproven-first-use")

    def test_partial_legacy_root_is_not_auto_completed(self) -> None:
        catalog = ResourceCatalog(self.config)
        catalog.root.mkdir(parents=True, mode=0o700)
        (catalog.root / "reservations").mkdir(mode=0o700)
        with self.assertRaises(DurableResourceError) as partial:
            catalog._ensure()
        self.assertEqual(partial.exception.code, "resource.changed")
        self.assertFalse((self.config / "resource-catalog-root-v1.json").exists())

    def test_private_mode_refusal_does_not_publish_root_manifest(self) -> None:
        catalog = ResourceCatalog(self.config)
        with patch("workbench_core.storage.registered.private_path", return_value=False):
            with self.assertRaises(DurableResourceError):
                self.resources.publish_bytes("evidence", "denied.json", b"denied\n")
        self.assertFalse((self.config / "resource-catalog-root-v1.json").exists())
        self.assertFalse(self.evidence.exists())

    def test_replaced_root_identity_is_rejected_even_with_copied_anchor(self) -> None:
        catalog = ResourceCatalog(self.config)
        self.resources.publish_bytes("evidence", "retained.json", b"retained\n")
        original = catalog.root
        renamed = self.config / "resources-renamed"
        original.rename(renamed)
        original.mkdir(mode=0o700)
        for name in ("reservations", "intents", "commits", "aborts", "leases", "stores"):
            (original / name).mkdir(mode=0o700)
        (original / ".resource-catalog-root-v1.json").write_bytes(
            (renamed / ".resource-catalog-root-v1.json").read_bytes()
        )
        with self.assertRaises(DurableResourceError) as changed:
            catalog.verify_root()
        self.assertEqual(changed.exception.code, "resource.changed")

    def test_child_catalogs_initialize_and_respect_parent_root_binding(self) -> None:
        temporary = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.config,
            locations={"tmp": self.home / "scratch"}, owner_id="validation",
        )
        temporary._ensure_catalog()
        catalog = ResourceCatalog(self.config)
        self.assertEqual(catalog.verify_root(), "ready-unproven")
        self.assertTrue((catalog.root / "temporary-leases").is_dir())
        catalog.root.rename(self.config / "resources-lost")
        with self.assertRaises(DurableResourceError):
            temporary._ensure_catalog()
        self.assertFalse(catalog.root.exists())

        from workbench_core.transport_trees import CoreTransportTrees

        transport_home = self.home / "transport-config"
        transport = CoreTransportTrees(
            workspace=self.workspace, configuration_home=transport_home,
            owner_id="public-export",
        )
        transport._ensure()
        self.assertEqual(ResourceCatalog(transport_home).verify_root(), "ready-unproven")
        self.assertTrue((transport_home / "resources-v1/transport-trees").is_dir())

    def test_cleanup_blocks_unregistered_workspace_items_with_unproven_history(self) -> None:
        from workbench_core.storage.manager import inventory_storage

        cache = self.workspace / ".workbench/cache"
        cache.mkdir(parents=True)
        unknown = cache / "unregistered.txt"
        unknown.write_bytes(b"unknown\n")
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            before = inventory_storage(self.workspace)
        selected = [row for row in before["items"] if Path(row["path"]) == unknown]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["deletion"]["state"], "protected")
        self.assertIn("registered-catalog-unproven", selected[0]["deletion"]["reason_codes"])
        self.resources.publish_bytes("evidence", "new.json", b"new\n")
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            after = inventory_storage(self.workspace)
        selected = [row for row in after["items"] if Path(row["path"]) == unknown]
        self.assertEqual(selected[0]["deletion"]["state"], "protected")
        self.assertIn("registered-catalog-unproven", selected[0]["deletion"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
