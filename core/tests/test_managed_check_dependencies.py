"""Managed graph references retain exact registered check evidence."""

from contextlib import contextmanager
import os
import unittest
from unittest.mock import patch

from workbench_api.managed_trees import ManagedTreeError
from workbench_core import check_lifecycle as life
from workbench_core import check_retention
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.storage import manager
from workbench_core.storage.tree_catalog import TreeCatalog
from workbench_core.storage.registered import ResourceCatalog
import test_check_lifecycle as fixtures


class ManagedCheckDependenciesTests(unittest.TestCase):
    @contextmanager
    def _attested_catalog_for_reference_test(self):
        """Exercise dependency rules independently of W0's unproven-history gate."""

        original = ResourceCatalog.inventory

        def attested(catalog, *, workspace=None):
            result = original(catalog, workspace=workspace)
            result["root_state"] = "ready-proven"
            return result

        with patch.object(ResourceCatalog, "inventory", attested):
            yield

    def setUp(self):
        source = fixtures.CheckLifecycleTests()
        source.setUp()
        self.addCleanup(source.doCleanups)
        self.source = source
        self.workspace = source.root
        self.reference_id = life.reference_id(source.record)
        self.config = source.base / "config"
        self.evidence = source.base / "selected-evidence"
        self.enterContext(patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}))
        self.host = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        )

    def publish(self):
        with self.host.stage("evidence", "graph") as stage:
            stage.path.mkdir()
            (stage.path / "manifest.json").write_text('{"source":"registered"}')
            return stage.publish(validate=lambda path: self.assertTrue((path / "manifest.json").is_file()),
                                 references=(self.reference_id,))

    def test_committed_tree_protects_unpinned_source_and_stale_cleanup(self):
        with self._attested_catalog_for_reference_test():
            before = manager.plan_cleanup(self.workspace, selector=self.source.item()["item_id"])
            self.assertEqual("ready", before["status"])
            tree = self.publish()
            self.assertEqual((self.reference_id,), tree.references)
            self.assertEqual((self.reference_id,), self.host.describe(tree.tree_id).references)
            row = self.source.item()
            self.assertEqual("protected", row["deletion"]["state"])
            self.assertIn("referenced-managed-tree", row["deletion"]["reason_codes"])
            self.assertEqual([], life.pins(self.workspace, self.source.attempt.name))
            with self.assertRaisesRegex(manager.RuntimeManagerError, "no longer eligible"):
                manager.execute_cleanup(self.workspace, before)
        self.assertTrue(self.source.attempt.exists())

    def test_interrupted_commit_keeps_source_protected(self):
        original = TreeCatalog._write

        def interrupt(catalog, name, *arguments):
            if name == "commits":
                raise OSError("simulated exit after graph rename")
            return original(catalog, name, *arguments)

        with patch.object(TreeCatalog, "_write", interrupt):
            with self.assertRaisesRegex(OSError, "simulated exit"):
                self.publish()
        self.assertEqual("published-uncommitted", self.host.catalog.inventory()["trees"][0]["status"])
        self.assertIn("referenced-managed-tree", self.source.item()["deletion"]["reason_codes"])
        self.assertEqual((self.reference_id,), self.host.reconcile(
            self.host.catalog.inventory()["trees"][0]["tree_id"]).references)

    def test_failure_before_tree_rename_conservatively_keeps_source(self):
        with patch("workbench_core.managed_trees._rename_no_replace", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.publish()
        self.assertEqual("failed", self.host.catalog.inventory()["trees"][0]["status"])
        self.assertIn("referenced-managed-tree", self.source.item()["deletion"]["reason_codes"])

    def test_failure_between_source_anchor_and_tree_intent_keeps_source(self):
        original = TreeCatalog._write

        def interrupt(catalog, name, *arguments):
            if name == "intents":
                raise OSError("simulated exit before tree intent")
            return original(catalog, name, *arguments)

        with patch.object(TreeCatalog, "_write", interrupt):
            with self.assertRaisesRegex(OSError, "before tree intent"):
                self.publish()
        self.assertEqual("failed", self.host.catalog.inventory()["trees"][0]["status"])
        self.assertIn("referenced-managed-tree", self.source.item()["deletion"]["reason_codes"])

    def test_retired_source_and_other_workspace_are_refused(self):
        with self._attested_catalog_for_reference_test():
            alternate = self.source.base / "alternate-workspace"
            alternate.mkdir()
            wrong = CoreManagedTrees(workspace=alternate, configuration_home=self.config,
                                     locations={"evidence": self.evidence}, owner_id="atlas")
            with wrong.stage("evidence", "wrong") as stage:
                stage.path.mkdir()
                (stage.path / "one").write_bytes(b"one")
                with self.assertRaisesRegex(ManagedTreeError, "not registered"):
                    stage.publish(validate=lambda _: None, references=(self.reference_id,))
            retired = self.source.retire()
            self.assertEqual("trash", retired["kind"])
            with self.assertRaisesRegex(ManagedTreeError, "not live retained"):
                self.publish()
            plan = manager.plan_restore_trash(self.workspace, selector=retired["item_id"])
            manager.execute_restore_trash(self.workspace, plan)
            self.assertEqual((self.reference_id,), self.publish().references)

    def test_pin_removal_does_not_clear_graph_dependency(self):
        life.pin(self.workspace, self.source.attempt.name, "review")
        self.publish()
        life.pin(self.workspace, self.source.attempt.name, "review", remove=True)
        self.assertEqual([], life.pins(self.workspace, self.source.attempt.name))
        self.assertIn("referenced-managed-tree", self.source.item()["deletion"]["reason_codes"])

    def test_changed_saved_evidence_and_incomplete_registry_are_refused(self):
        saved = self.source.attempt / "input.txt"
        saved.write_bytes(b"changed saved source")
        with self.assertRaisesRegex(ManagedTreeError, "missing or changed"):
            self.publish()
        saved.write_bytes(b"complete saved source")
        result = self.source.attempt / "snapshot/result.json.gz"
        original = result.read_bytes()
        result.write_bytes(b"corrupt original report")
        with self.assertRaisesRegex(ManagedTreeError, "missing or changed"):
            self.publish()
        result.write_bytes(original)
        ledger = life._ledger(self.workspace) / (self.source.attempt.name + ".json")
        ledger.unlink()
        with self.assertRaisesRegex(ManagedTreeError, "accounting is incomplete"):
            self.publish()

    def test_changed_source_side_anchor_protects_all_registered_checks(self):
        tree = self.publish()
        marker = life._consumer_directory(self.workspace) / self.reference_id.split(":", 1)[1]
        (marker / (tree.tree_id.split(":", 1)[1] + ".json")).write_text("changed")
        row = self.source.item()
        self.assertEqual("protected", row["deletion"]["state"])
        self.assertIn("check-reference-accounting-incomplete", row["deletion"]["reason_codes"])
        self.assertTrue(check_retention.status(self.workspace)["coverage_gaps"])

    def test_catalog_loss_does_not_remove_check_side_protection(self):
        self.publish()
        self.host.catalog.root.rename(self.host.catalog.root.with_name("resources-lost"))
        self.assertIn("referenced-managed-tree", self.source.item()["deletion"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
