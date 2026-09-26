"""Core directory publication, exact inventory, references and recovery."""

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
from workbench_api.managed_trees import ManagedTreeError, managed_trees
from workbench_core.managed_trees import CoreManagedTrees
from workbench_core.modules import InstalledModule, dispatch
from workbench_core.storage.registered import CoreDurableResources, ResourceCatalog
from workbench_core.storage.tree_catalog import TreeCatalog
from workbench_core.storage import manager


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

    def test_stage_has_absent_payload_and_publishes_exact_cataloged_tree(self) -> None:
        reference = self._publish()
        self.assertTrue(reference.path.is_dir())
        self.assertEqual({"index.db", "source.jsonl"}, {row["path"] for row in reference.members})
        self.assertEqual(reference, self.host.describe(reference.tree_id))
        inventory = ResourceCatalog(self.config).inventory(workspace=self.workspace)["trees"]
        self.assertEqual([reference.tree_id], [row["tree_id"] for row in inventory])
        self.assertEqual("committed", inventory[0]["status"])
        self.assertEqual("graph-set:one", reference.domain_id)

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

    def test_registered_target_and_stage_do_not_protect_unrelated_sibling(self) -> None:
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
        self.assertEqual(["protected", "protected", "eligible"],
                         [item["deletion"]["state"] for item in items])
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
