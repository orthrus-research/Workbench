"""Core directory publication, exact inventory, references and recovery."""

import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import workbench_core.managed_trees as tree_module

from workbench_api import Capability, ExecutionContext, Module
from workbench_api.durable_resources import DurableResourceError
from workbench_api.managed_trees import ManagedTreeError, managed_trees
from workbench_core import check_storage
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.storage.registered import CoreDurableResources, ResourceCatalog
from workbench_core.storage.tree_catalog import (
    ABORT_KIND, COMMIT_KIND, DERIVED_INTENT_KIND, INTENT_KIND, TreeCatalog,
    inventory_members,
)
from workbench_core.storage import manager
from workbench_core.temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


class ManagedTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.config = self.home / "config"
        self.evidence = self.home / "selected-evidence"
        self.host = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        )

    def _publish(self, *, output: Path | None = None, references=(), derived=()):
        name = "graph" if output is None else output.name
        with self.host.stage("evidence", name, requested_path=output) as stage:
            self.assertFalse(stage.path.exists())
            stage.path.mkdir()
            (stage.path / "source.jsonl").write_bytes(b"source\n")
            (stage.path / "index.db").write_bytes(b"index\n")
            result = stage.publish(
                validate=lambda path: self.assertEqual(b"source\n", (path / "source.jsonl").read_bytes()),
                domain_id="graph-set:one", references=references,
                derived_members=derived,
            )
        return result

    def test_portable_tree_has_a_bounded_file_limit_above_runtime_images(self) -> None:
        import workbench_core.storage.tree_catalog as catalog_module

        self.assertGreater(catalog_module._MAX_TREE_FILE_BYTES, 2 * 1024**3)
        self.assertLessEqual(catalog_module._MAX_TREE_FILE_BYTES, 32 * 1024**3)
        source = self.home / "small-tree"
        source.mkdir()
        (source / "member").write_bytes(b"three")
        with self.assertRaisesRegex(check_storage.CheckStorageError, "exceeds its bound"):
            check_storage.tree_manifest(source, max_file_bytes=4)
        with patch.object(catalog_module, "_MAX_TREE_FILE_BYTES", 4):
            with self.assertRaisesRegex(check_storage.CheckStorageError, "exceeds its bound"):
                inventory_members(source)
        with patch.object(catalog_module, "_MAX_TREE_FILE_BYTES", 5):
            self.assertEqual(5, inventory_members(source)[0]["size"])

    def test_typed_temporary_lease_reference_blocks_disposal_and_reopens(self) -> None:
        leases = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.config,
            locations={"source": self.home / "scratch"}, owner_id="atlas",
        )
        source = leases.allocate("source", "copied-source")
        (source.path / "input.txt").write_bytes(b"input")
        with leases.execution(source):
            tree = self._publish(references=(source.lease_id,))
            leases.retain(source, outcome="completed")
        self.assertEqual((source.lease_id,), self.host.reconcile(tree.tree_id).references)
        catalog = ResourceCatalog(self.config).inventory(workspace=self.workspace)
        self.assertEqual((source.lease_id,), tuple(catalog["trees"][0]["references"]))
        with self.assertRaises(TemporaryLeaseError) as caught:
            leases.reconcile(source.lease_id, drained=lambda: True)
        self.assertEqual("temporary.referenced", caught.exception.code)
        self.assertTrue(source.path.is_dir())

    def test_typed_temporary_lease_reference_rejects_other_owner(self) -> None:
        leases = CoreTemporaryLeases(
            workspace=self.workspace, configuration_home=self.config,
            locations={"source": self.home / "scratch"}, owner_id="validation",
        )
        source = leases.allocate("source", "foreign-source")
        with leases.execution(source):
            with self.assertRaisesRegex(ManagedTreeError, "referenced temporary lease"):
                self._publish(references=(source.lease_id,))

    @staticmethod
    def _atlas_manifest(graph_set_id: str, *, index: str, scope: str = "fixture") -> bytes:
        return (json.dumps({
            "graph_set_id": graph_set_id,
            "query_index": {"sha256": index},
            "scope": scope,
        }, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")

    def _publish_atlas_manifest(self, *, rule: bool):
        graph_set_id = "workbench-atlas-graph-set-v3:sha256:" + "a" * 64
        with self.host.stage("evidence", "graph") as stage:
            stage.path.mkdir()
            (stage.path / "source.jsonl").write_bytes(b"source\n")
            (stage.path / "query-index.sqlite3").write_bytes(b"index\n")
            (stage.path / "manifest.json").write_bytes(
                self._atlas_manifest(graph_set_id, index="first")
            )
            reference = stage.publish(
                validate=lambda path: self.assertEqual(b"source\n", (path / "source.jsonl").read_bytes()),
                domain_id=graph_set_id,
                derived_members=("query-index.sqlite3",),
                derived_manifest_rule="atlas-categorical-query-index-v1" if rule else None,
            )
        return reference, graph_set_id

    def test_versioned_derived_manifest_rule_keeps_authoritative_bytes_exact(self) -> None:
        reference, graph_set_id = self._publish_atlas_manifest(rule=True)
        intent = TreeCatalog(self.config / "resources-v1").intent(reference.tree_id)
        self.assertEqual(DERIVED_INTENT_KIND, intent["format"])
        (reference.path / "query-index.sqlite3").write_bytes(b"rebuilt\n")
        (reference.path / "manifest.json").write_bytes(
            self._atlas_manifest(graph_set_id, index="rebuilt")
        )
        rebuilt = self.host.describe(reference.tree_id)
        self.assertEqual(reference.content_sha256, rebuilt.content_sha256)
        self.assertEqual("changed", rebuilt.derived_status)
        (reference.path / "manifest.json").write_bytes(json.dumps({
            "graph_set_id": graph_set_id,
            "query_index": {"sha256": "rebuilt"},
            "scope": "fixture",
        }, sort_keys=True).encode("utf-8") + b"\n")
        with self.assertRaisesRegex(ManagedTreeError, "serialization changed"):
            self.host.describe(reference.tree_id)
        (reference.path / "manifest.json").write_bytes(
            self._atlas_manifest(graph_set_id, index="rebuilt", scope="altered")
        )
        with self.assertRaisesRegex(ManagedTreeError, "authoritative manifest changed"):
            self.host.describe(reference.tree_id)

    def test_old_intent_still_rejects_manifest_byte_changes(self) -> None:
        reference, graph_set_id = self._publish_atlas_manifest(rule=False)
        intent = TreeCatalog(self.config / "resources-v1").intent(reference.tree_id)
        self.assertEqual(INTENT_KIND, intent["format"])
        (reference.path / "manifest.json").write_bytes(
            self._atlas_manifest(graph_set_id, index="rebuilt")
        )
        with self.assertRaisesRegex(ManagedTreeError, "authoritative members changed"):
            self.host.describe(reference.tree_id)

    def test_derived_manifest_rule_requires_atlas_owner_and_derived_index(self) -> None:
        graph_set_id = "workbench-atlas-graph-set-v3:sha256:" + "a" * 64
        other_host = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="other",
        )
        for host, derived in ((other_host, ("query-index.sqlite3",)), (self.host, ())):
            with self.subTest(owner=host.owner_id, derived=derived):
                with self.assertRaisesRegex(ManagedTreeError, "derived manifest rule is unsupported"):
                    with host.stage("evidence", "graph") as stage:
                        stage.path.mkdir()
                        stage.publish(
                            validate=lambda _: None,
                            domain_id=graph_set_id,
                            derived_members=derived,
                            derived_manifest_rule="atlas-categorical-query-index-v1",
                        )

    def test_stage_has_absent_payload_and_publishes_exact_cataloged_tree(self) -> None:
        reference = self._publish()
        self.assertTrue(reference.path.is_dir())
        self.assertEqual({"index.db", "source.jsonl"}, {row["path"] for row in reference.members})
        self.assertEqual(reference, self.host.describe(reference.tree_id))
        inventory = ResourceCatalog(self.config).inventory(workspace=self.workspace)["trees"]
        self.assertEqual([reference.tree_id], [row["tree_id"] for row in inventory])
        self.assertEqual("committed", inventory[0]["status"])
        self.assertEqual("graph-set:one", reference.domain_id)

    def test_inventory_refuses_unknown_or_unsafe_tree_catalog_entries_across_workspaces(self) -> None:
        reference = self._publish()
        catalog = ResourceCatalog(self.config)
        root = catalog.root / "trees"
        foreign_workspace = self.home / "another-workspace"
        foreign_workspace.mkdir()
        reservation = root / "reservations" / (reference.tree_id.split(":", 1)[1] + ".json")
        cases = (
            (root / "future", lambda path: path.mkdir(), lambda path: path.rmdir()),
            (root / "reservations" / "pending.tmp",
             lambda path: path.write_bytes(b"pending"), lambda path: path.unlink()),
            (root / "intents" / ("a" * 32 + ".json"),
             lambda path: path.symlink_to(reservation), lambda path: path.unlink()),
            (root / "aborts" / ("b" * 32 + ".json"),
             lambda path: os.link(reservation, path), lambda path: path.unlink()),
            (root / "leases" / "unknown.lock",
             lambda path: path.write_bytes(b""), lambda path: path.unlink()),
        )
        for path, create, remove in cases:
            with self.subTest(path=path):
                create(path)
                try:
                    with self.assertRaises(ManagedTreeError) as changed:
                        catalog.inventory(workspace=foreign_workspace)
                    self.assertEqual(changed.exception.code, "tree.changed")
                finally:
                    remove(path)
        self.assertEqual([reference.tree_id], [
            row["tree_id"] for row in catalog.inventory(workspace=self.workspace)["trees"]
        ])

    def test_inventory_refuses_missing_child_or_orphan_commit_but_retains_pre_reservation_lease(self) -> None:
        reference = self._publish()
        catalog = ResourceCatalog(self.config)
        root = catalog.root / "trees"
        leases = root / "leases"
        displaced = root / "leases-lost"
        leases.rename(displaced)
        try:
            with self.assertRaises(ManagedTreeError) as missing:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual(missing.exception.code, "tree.changed")
        finally:
            displaced.rename(leases)

        commit = root / "commits" / ("c" * 32 + ".json")
        existing = root / "commits" / (reference.tree_id.split(":", 1)[1] + ".json")
        commit.write_bytes(existing.read_bytes())
        commit.chmod(0o600)
        try:
            with self.assertRaises(ManagedTreeError) as orphan:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual(orphan.exception.code, "tree.changed")
        finally:
            commit.unlink()

        # A hard exit after opening the lease but before reserve leaves this
        # exact private file; it must not turn into an invented reservation.
        orphan_lease = leases / ("d" * 32 + ".lock")
        orphan_lease.write_bytes(b"")
        orphan_lease.chmod(0o600)
        try:
            rows = catalog.inventory(workspace=self.workspace)["trees"]
            self.assertEqual([reference.tree_id], [row["tree_id"] for row in rows])
        finally:
            orphan_lease.unlink()

    def test_foreign_tree_child_content_is_checked_before_workspace_filter(self) -> None:
        own = self._publish()
        foreign_workspace = self.home / "foreign-workspace"
        foreign_workspace.mkdir()
        foreign_host = CoreManagedTrees(
            workspace=foreign_workspace, configuration_home=self.config,
            locations={"evidence": self.home / "foreign-evidence"}, owner_id="atlas",
        )
        with foreign_host.stage("evidence", "published") as stage:
            stage.path.mkdir()
            (stage.path / "payload.txt").write_bytes(b"foreign\n")
            published = stage.publish(validate=lambda _: None)
        with foreign_host.stage("evidence", "aborted") as stage:
            aborted_id = stage.tree_id
        catalog = ResourceCatalog(self.config)
        self.assertEqual([own.tree_id], [row["tree_id"] for row in
                         catalog.trees.inventory(workspace=self.workspace)])
        self.assertEqual([own.tree_id], [row["tree_id"] for row in
                         catalog.inventory(workspace=self.workspace)["trees"]])

        published_nonce = published.tree_id.rsplit(":", 1)[1]
        aborted_nonce = aborted_id.rsplit(":", 1)[1]
        root = catalog.trees.root
        cases = (
            (root / "intents" / f"{published_nonce}.json", "reservation_id", "different", INTENT_KIND),
            (root / "commits" / f"{published_nonce}.json", "intent_id", "different", COMMIT_KIND),
            (root / "aborts" / f"{aborted_nonce}.json", "tree_id", own.tree_id, ABORT_KIND),
        )
        for path, field, value, kind in cases:
            with self.subTest(path=path):
                original = path.read_bytes()
                forged = json.loads(original)
                forged[field] = value
                forged = check_storage.seal(kind, {key: item for key, item in forged.items() if key != "id"})
                path.write_bytes(check_storage.canonical(forged) + b"\n")
                try:
                    with self.assertRaises(ManagedTreeError) as direct:
                        catalog.trees.inventory(workspace=self.workspace)
                    self.assertEqual("tree.changed", direct.exception.code)
                    with self.assertRaises(ManagedTreeError) as integrated:
                        catalog.inventory(workspace=self.workspace)
                    self.assertEqual("tree.changed", integrated.exception.code)
                finally:
                    path.write_bytes(original)

    def test_exact_target_lookup_scopes_committed_and_allocated_trees(self) -> None:
        output = self.workspace / "graphs" / "selected"
        reference = self._publish(output=output)
        selected = self.host.lookup_target(
            "evidence", output, domain_id="graph-set:one",
        )
        self.assertEqual((reference.tree_id, "committed", output, "graph-set:one"),
                         (selected.tree_id, selected.status, selected.path, selected.domain_id))
        with self.assertRaisesRegex(ManagedTreeError, "another domain identity"):
            self.host.lookup_target("evidence", output, domain_id="other")
        with self.assertRaisesRegex(ManagedTreeError, "no catalog record"):
            self.host.lookup_target("evidence", self.workspace / "graphs" / "missing")

        pending = self.workspace / "graphs" / "pending"
        with self.host.stage("evidence", pending.name, requested_path=pending) as stage:
            stage.path.mkdir()
            row = self.host.lookup_target("evidence", pending)
            self.assertEqual((stage.tree_id, "incomplete", None),
                             (row.tree_id, row.status, row.domain_id))
            with self.assertRaisesRegex(ManagedTreeError, "no bound domain identity"):
                self.host.lookup_target("evidence", pending, domain_id="graph-set:pending")

    def test_exact_target_lookup_refuses_foreign_duplicate_and_redirected_records(self) -> None:
        output = self.workspace / "graphs" / "selected"
        self._publish(output=output)
        other_owner = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="another-owner",
        )
        with self.assertRaisesRegex(ManagedTreeError, "another binding"):
            other_owner.lookup_target("evidence", output)
        other_workspace = self.home / "other-workspace"
        other_workspace.mkdir()
        foreign = CoreManagedTrees(
            workspace=other_workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        )
        with self.assertRaisesRegex(ManagedTreeError, "another binding"):
            foreign.lookup_target("evidence", output)
        with self.assertRaisesRegex(ManagedTreeError, "exact absolute path"):
            self.host.lookup_target("evidence", Path("relative"))

        alias = self.workspace / "redirect"
        alias.symlink_to(output.parent, target_is_directory=True)
        with self.assertRaisesRegex(ManagedTreeError, "redirect"):
            self.host.lookup_target("evidence", alias / output.name)

        abandoned = self.workspace / "graphs" / "reused"
        with self.assertRaisesRegex(ValueError, "owner refused"):
            with self.host.stage("evidence", abandoned.name, requested_path=abandoned) as stage:
                stage.path.mkdir()
                stage.publish(validate=lambda _: (_ for _ in ()).throw(ValueError("owner refused")))
        self._publish(output=abandoned)
        with self.assertRaisesRegex(ManagedTreeError, "multiple catalog records"):
            self.host.lookup_target("evidence", abandoned)

    def test_owner_failure_retains_partial_stage_and_does_not_publish(self) -> None:
        output = self.workspace / "graphs" / "failed"
        with self.assertRaisesRegex(ValueError, "owner refused"):
            with self.host.stage("evidence", output.name, requested_path=output) as stage:
                stage.path.mkdir()
                (stage.path / "partial.txt").write_bytes(b"evidence")
                stage.publish(validate=lambda _: (_ for _ in ()).throw(ValueError("owner refused")))
        self.assertFalse(output.exists())
        row = ResourceCatalog(self.config).inventory()["trees"][0]
        self.assertEqual("failed", row["status"])
        self.assertEqual(b"evidence", (Path(row["staging"]) / "partial.txt").read_bytes())

    def test_filesystem_without_atomic_no_replace_fails_closed(self) -> None:
        output = self.workspace / "graphs" / "unsupported"
        with patch("workbench_core.managed_trees._rename_no_replace",
                   side_effect=ManagedTreeError("output.filesystem", "unsupported filesystem")):
            with self.assertRaisesRegex(ManagedTreeError, "unsupported filesystem"):
                with self.host.stage("evidence", output.name, requested_path=output) as stage:
                    stage.path.mkdir()
                    (stage.path / "source.jsonl").write_bytes(b"source")
                    stage.publish(validate=lambda _: None)
        self.assertFalse(output.exists())
        row = ResourceCatalog(self.config).inventory()["trees"][0]
        self.assertEqual("failed", row["status"])
        self.assertEqual(b"source", (Path(row["staging"]) / "source.jsonl").read_bytes())

    def test_owner_validator_must_not_change_inventory(self) -> None:
        with self.assertRaisesRegex(ManagedTreeError, "changed during owner validation"):
            with self.host.stage("evidence", "graph") as stage:
                stage.path.mkdir()
                (stage.path / "source.jsonl").write_bytes(b"first")
                stage.publish(validate=lambda path: (path / "source.jsonl").write_bytes(b"second"))
        row = ResourceCatalog(self.config).inventory()["trees"][0]
        self.assertEqual("failed", row["status"])
        self.assertEqual(b"second", (Path(row["staging"]) / "source.jsonl").read_bytes())

    def test_member_flush_failure_preserves_stage_without_publication(self) -> None:
        output = self.workspace / "graphs" / "unsynced"
        original = tree_module.fsync_directory

        def fail_payload_flush(path):
            if path.name == "payload":
                raise OSError("selected filesystem cannot flush payload")
            return original(path)

        with patch("workbench_core.managed_trees.fsync_directory", side_effect=fail_payload_flush):
            with self.assertRaisesRegex(OSError, "cannot flush payload"):
                self._publish(output=output)
        self.assertFalse(output.exists())
        row = ResourceCatalog(self.config).inventory()["trees"][0]
        self.assertEqual("failed", row["status"])
        self.assertEqual(b"source\n", (Path(row["staging"]) / "source.jsonl").read_bytes())

    def test_existing_destination_is_never_replaced(self) -> None:
        output = self.workspace / "graphs" / "selected"
        with self.assertRaisesRegex(ManagedTreeError, "already exists") as caught:
            with self.host.stage("evidence", output.name, requested_path=output) as stage:
                stage.path.mkdir()
                (stage.path / "source.jsonl").write_bytes(b"loser")
                output.mkdir()
                (output / "winner.txt").write_bytes(b"winner")
                stage.publish(validate=lambda _: None)
        self.assertEqual("output.exists", caught.exception.code)
        self.assertEqual(b"winner", (output / "winner.txt").read_bytes())
        self.assertEqual(b"loser", (Path(ResourceCatalog(self.config).inventory()["trees"][0]["staging"]) / "source.jsonl").read_bytes())

    def test_replaced_parent_cannot_redirect_tree_publication(self) -> None:
        output = self.workspace / "graphs" / "graph"
        displaced = self.workspace / "graphs-displaced"
        original = tree_module._rename_no_replace

        def replace_parent(source, target, **kwargs):
            output.parent.rename(displaced)
            output.parent.mkdir()
            (displaced / source.parent.name).rename(output.parent / source.parent.name)
            return original(source, target, **kwargs)

        with patch("workbench_core.managed_trees._rename_no_replace", side_effect=replace_parent):
            with self.assertRaisesRegex(ManagedTreeError, "destination parent changed"):
                self._publish(output=output)
        self.assertFalse(output.exists())
        self.assertFalse((displaced / output.name).exists())
        self.assertEqual("failed", ResourceCatalog(self.config).inventory()["trees"][0]["status"])

    def test_commit_interruption_recovers_only_original_published_inode(self) -> None:
        original = TreeCatalog._write

        def interrupted(catalog, name, *arguments):
            if name == "commits":
                raise OSError("simulated interruption after directory rename")
            return original(catalog, name, *arguments)

        with patch.object(TreeCatalog, "_write", interrupted):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                self._publish()
        catalog = ResourceCatalog(self.config)
        row = catalog.inventory()["trees"][0]
        self.assertEqual("published-uncommitted", row["status"])
        selected = self.host.lookup_target("evidence", Path(row["path"]), domain_id="graph-set:one")
        self.assertEqual((row["tree_id"], "published-uncommitted"),
                         (selected.tree_id, selected.status))
        reference = catalog.trees.reconcile(row["tree_id"], workspace=self.workspace)
        self.assertEqual("committed", catalog.inventory()["trees"][0]["status"])
        self.assertEqual(reference, self.host.describe(reference.tree_id))

    def test_abrupt_exit_after_intent_recovers_unchanged_stage(self) -> None:
        output = self.workspace / "graphs" / "recovered"
        script = """
import os
from pathlib import Path
import workbench_core.managed_trees as trees

trees._rename_no_replace = lambda source, target, **kwargs: os._exit(73)
host = trees.CoreManagedTrees(
    workspace=Path(os.environ["W3_WORKSPACE"]),
    configuration_home=Path(os.environ["W3_CONFIG"]),
    locations={"evidence": Path(os.environ["W3_EVIDENCE"])},
    owner_id="atlas",
)
with host.stage("evidence", "recovered", requested_path=Path(os.environ["W3_OUTPUT"])) as stage:
    stage.path.mkdir()
    (stage.path / "source.jsonl").write_bytes(b"source\\n")
    stage.publish(validate=lambda path: (path / "source.jsonl").read_bytes())
"""
        project = Path(__file__).resolve().parents[2]
        paths = [str(project / "api" / "src"), str(project / "core" / "src")]
        environment = dict(os.environ)
        environment.update({
            "PYTHONPATH": os.pathsep.join([*paths, environment.get("PYTHONPATH", "")]),
            "W3_WORKSPACE": str(self.workspace), "W3_CONFIG": str(self.config),
            "W3_EVIDENCE": str(self.evidence), "W3_OUTPUT": str(output),
        })
        result = subprocess.run([sys.executable, "-c", script], env=environment,
                                capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(73, result.returncode, result.stderr)
        row = ResourceCatalog(self.config).inventory()["trees"][0]
        self.assertEqual("incomplete", row["status"])
        selected = self.host.lookup_target("evidence", output)
        self.assertEqual((row["tree_id"], "incomplete"), (selected.tree_id, selected.status))
        self.assertIsNone(selected.domain_id)
        self.assertFalse(output.exists())
        self.assertEqual(b"source\n", (Path(row["staging"]) / "source.jsonl").read_bytes())
        reference = self.host.reconcile(row["tree_id"])
        self.assertEqual(output, reference.path)
        self.assertEqual("committed", ResourceCatalog(self.config).inventory()["trees"][0]["status"])
        self.assertEqual(reference, self.host.describe(reference.tree_id))

    def test_resource_dependencies_and_derived_member_rebuild(self) -> None:
        input_resource = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        ).publish_bytes("evidence", "source.json", b"source\n")
        reference = self._publish(references=(input_resource.resource_id,), derived=("index.db",))
        self.assertEqual((input_resource.resource_id,), reference.references)
        (reference.path / "index.db").write_bytes(b"rebuilt index\n")
        rebuilt = self.host.describe(reference.tree_id)
        self.assertEqual(reference.content_sha256, rebuilt.content_sha256)
        self.assertEqual("changed", rebuilt.derived_status)
        (reference.path / "index.db").unlink()
        self.assertEqual("missing", self.host.describe(reference.tree_id).derived_status)
        (reference.path / "source.jsonl").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ManagedTreeError, "authoritative members changed"):
            self.host.describe(reference.tree_id)

    def test_inventory_requires_committed_tree_resource_reference(self) -> None:
        input_resource = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        ).publish_bytes("evidence", "source.json", b"source\n")
        tree = self._publish(references=(input_resource.resource_id,))
        catalog = ResourceCatalog(self.config)
        inventory = catalog.inventory(workspace=self.workspace)
        self.assertEqual("ready-unproven", inventory["root_state"])
        self.assertEqual([tree.tree_id], [row["tree_id"] for row in inventory["trees"]])

        nonce = input_resource.resource_id.rsplit(":", 1)[1]
        commit = self.config / "resources-v1" / "commits" / f"{nonce}.json"
        held = self.home / "held-resource-commit.json"
        commit.rename(held)
        try:
            with self.assertRaises(DurableResourceError) as failure:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual("resource.changed", failure.exception.code)
            self.assertFalse(commit.exists())
        finally:
            held.rename(commit)
        self.assertEqual("committed", catalog.inventory(workspace=self.workspace)["trees"][0]["status"])

    def test_inventory_rejects_changed_tree_resource_payload(self) -> None:
        input_resource = CoreDurableResources(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        ).publish_bytes("evidence", "source.json", b"source\n")
        self._publish(references=(input_resource.resource_id,))
        input_resource.path.write_bytes(b"broken\n")
        with self.assertRaises(DurableResourceError) as failure:
            ResourceCatalog(self.config).inventory(workspace=self.workspace)
        self.assertEqual("resource.changed", failure.exception.code)

    def test_foreign_tree_resource_reference_is_checked_before_workspace_filter(self) -> None:
        own_tree = self._publish()
        foreign_workspace = self.home / "foreign-workspace"
        foreign_workspace.mkdir()
        foreign_evidence = self.home / "foreign-evidence"
        foreign_resource = CoreDurableResources(
            workspace=foreign_workspace, configuration_home=self.config,
            locations={"evidence": foreign_evidence}, owner_id="atlas",
        ).publish_bytes("evidence", "source.json", b"source\n")
        foreign_host = CoreManagedTrees(
            workspace=foreign_workspace, configuration_home=self.config,
            locations={"evidence": foreign_evidence}, owner_id="atlas",
        )
        with foreign_host.stage("evidence", "foreign") as stage:
            stage.path.mkdir()
            (stage.path / "payload.txt").write_bytes(b"foreign\n")
            stage.publish(validate=lambda _: None, references=(foreign_resource.resource_id,))

        catalog = ResourceCatalog(self.config)
        self.assertEqual([own_tree.tree_id], [row["tree_id"] for row in
                                              catalog.inventory(workspace=self.workspace)["trees"]])
        foreign_resource.path.write_bytes(b"broken\n")
        with self.assertRaises(DurableResourceError) as failure:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual("resource.changed", failure.exception.code)

    def test_tree_dependency_is_recorded_and_cross_workspace_reference_is_refused(self) -> None:
        source = self._publish()
        dependent = self._publish(references=(source.tree_id,))
        self.assertEqual((source.tree_id,), dependent.references)
        other_workspace = self.home / "other-workspace"
        other_workspace.mkdir()
        other_host = CoreManagedTrees(
            workspace=other_workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="atlas",
        )
        with self.assertRaisesRegex(ManagedTreeError, "another workspace"):
            with other_host.stage("evidence", "other") as stage:
                stage.path.mkdir()
                (stage.path / "source.txt").write_bytes(b"not admitted")
                stage.publish(validate=lambda _: None, references=(source.tree_id,))

    def test_inventory_requires_committed_tree_dependency(self) -> None:
        source = self._publish()
        dependent = self._publish(references=(source.tree_id,))
        catalog = ResourceCatalog(self.config)
        inventory = catalog.inventory(workspace=self.workspace)
        self.assertEqual("ready-unproven", inventory["root_state"])
        self.assertEqual({source.tree_id, dependent.tree_id},
                         {row["tree_id"] for row in inventory["trees"]})

        nonce = source.tree_id.rsplit(":", 1)[1]
        commit = self.config / "resources-v1/trees/commits" / f"{nonce}.json"
        held = self.home / "held-tree-commit.json"
        commit.rename(held)
        try:
            with self.assertRaises(DurableResourceError) as failure:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual("resource.changed", failure.exception.code)
            self.assertFalse(commit.exists())
        finally:
            held.rename(commit)
        self.assertEqual(2, len(catalog.inventory(workspace=self.workspace)["trees"]))
        (source.path / "source.jsonl").write_bytes(b"broken\n")
        with self.assertRaises(DurableResourceError) as failure:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual("resource.changed", failure.exception.code)

    def test_inventory_refuses_busy_tree_dependency(self) -> None:
        source = self._publish()
        self._publish(references=(source.tree_id,))
        catalog = ResourceCatalog(self.config)
        with catalog.trees.lease(source.tree_id, exclusive=True):
            with self.assertRaises(DurableResourceError) as failure:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual("resource.changed", failure.exception.code)

    def test_foreign_tree_dependency_is_checked_before_workspace_filter(self) -> None:
        own_tree = self._publish()
        foreign_workspace = self.home / "foreign-workspace"
        foreign_workspace.mkdir()
        foreign_host = CoreManagedTrees(
            workspace=foreign_workspace, configuration_home=self.config,
            locations={"evidence": self.home / "foreign-evidence"}, owner_id="atlas",
        )

        def publish_foreign(name: str, references: tuple[str, ...] = ()):
            with foreign_host.stage("evidence", name) as stage:
                stage.path.mkdir()
                (stage.path / "payload.txt").write_bytes(name.encode() + b"\n")
                return stage.publish(validate=lambda _: None, references=references)

        source = publish_foreign("source")
        publish_foreign("dependent", references=(source.tree_id,))
        catalog = ResourceCatalog(self.config)
        self.assertEqual([own_tree.tree_id], [row["tree_id"] for row in
                                              catalog.inventory(workspace=self.workspace)["trees"]])
        (source.path / "payload.txt").write_bytes(b"broken\n")
        with self.assertRaises(DurableResourceError) as failure:
            catalog.inventory(workspace=self.workspace)
        self.assertEqual("resource.changed", failure.exception.code)

    def test_unproven_catalog_protects_unrelated_sibling_pending_review(self) -> None:
        output = self.workspace / "graphs" / "graph"
        reference = self._publish(output=output)
        sibling = output.parent / "notes.txt"
        sibling.write_bytes(b"unrelated")
        def row(path):
            return {"path": str(path), "deletion": {
                "state": "eligible", "recoverability": "trash", "reason_codes": [],
            }}
        items = [row(output), row(output.parent), row(sibling)]
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            manager._protect_registered_resources(self.workspace, items)
        self.assertEqual(["protected", "protected", "protected"],
                         [item["deletion"]["state"] for item in items])
        self.assertIn("registered-catalog-unproven", items[-1]["deletion"]["reason_codes"])
        self.assertEqual(reference.path, output)

    def test_dispatch_binds_tree_port_to_admitted_owner(self) -> None:
        published = []

        def run(argv, *, context):
            self.assertEqual(["now"], argv)
            with managed_trees().stage("evidence", "tree") as stage:
                stage.path.mkdir()
                (stage.path / "record.txt").write_bytes(b"admitted")
                published.append(stage.publish(validate=lambda path: self.assertTrue(path.is_dir())))
            return 0

        context = ExecutionContext(
            self.workspace, self.home / "state",
            locations={"evidence": self.evidence}, configuration_home=self.config,
        )
        module = InstalledModule(
            "sample", "workbench-sample", "0.1.0", "available",
            module=Module("sample", "0.1.0", (
                Capability("sample.tree", ("sample",), "sample_plugin:run", "run"),
            )),
        )
        with patch.dict(sys.modules, {"sample_plugin": SimpleNamespace(run=run)}):
            self.assertEqual(0, dispatch(["sample", "now"], context, (module,)))
        self.assertEqual("sample", published[0].owner_id)
        self.assertEqual("committed", ResourceCatalog(self.config).inventory()["trees"][0]["status"])


if __name__ == "__main__":
    unittest.main()
