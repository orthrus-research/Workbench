"""Large immutable transport custody and crash-recovery boundaries."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from workbench_api.durable_resources import DurableResourceError
from workbench_api.managed_trees import ManagedTreeError
from workbench_api.transport_trees import TransportTreeError
from workbench_core import check_storage
from workbench_core import transport_trees as module
from workbench_core.storage import manager
from workbench_core.storage.registered import ResourceCatalog
from workbench_core.transport_trees import CoreTransportTrees, inventory_tree, summarize_rows


@unittest.skipUnless(sys.platform == "linux", "V2 transport publication is Linux/WSL only")
class TransportTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.config = self.base / "config"
        self.outputs = self.base / "outputs"
        self.host = self._host()

    def _host(self) -> CoreTransportTrees:
        return CoreTransportTrees(
            workspace=self.workspace, configuration_home=self.config,
            owner_id="public-export",
        )

    @staticmethod
    def _fill(path: Path) -> None:
        path.mkdir(mode=0o700)
        tree = path / "tree"
        tree.mkdir(mode=0o755)
        (tree / "source.txt").write_bytes(b"reviewed\n")
        (path / "export-manifest.json").write_bytes(b"{}\n")
        os.chmod(tree, 0o755)
        os.chmod(tree / "source.txt", 0o644)
        os.chmod(path / "export-manifest.json", 0o644)

    def _publish(self, name: str = "selected"):
        target = self.outputs / name
        with self.host.stage(target) as stage:
            self.assertFalse(stage.path.exists())
            self._fill(stage.path)
            reference = stage.publish(
                validate=lambda path: self.assertEqual(b"reviewed\n", (path / "tree/source.txt").read_bytes()),
                domain_id="public-export:reviewed-head:passing-receipt",
            )
        return reference

    def test_publish_has_compact_reference_and_exact_output(self) -> None:
        reference = self._publish("public output")
        self.assertEqual(2, reference.file_count)
        self.assertEqual(1, reference.directory_count)
        self.assertEqual(reference, self._host().describe(reference.tree_id))
        self.assertEqual("committed", self._host().inventory()[0]["status"])
        self.assertEqual({"tree", "export-manifest.json"}, {entry.name for entry in reference.path.iterdir()})
        self.assertFalse((reference.path / "transport-intent.json").exists())
        self.assertFalse(hasattr(reference, "members"))

    def test_cleanup_reference_projection_includes_transport_target_and_stage(self) -> None:
        reference = self._publish()
        row = ResourceCatalog(self.config).inventory(workspace=self.workspace)["transport_trees"][0]
        sibling = reference.path.parent / "unrelated"

        def item(path: Path) -> dict:
            return {"path": str(path), "deletion": {
                "state": "eligible", "recoverability": "trash", "reason_codes": [],
            }}

        items = [item(path) for path in (
            reference.path, reference.path / "tree", reference.path.parent,
            Path(row["staging"]), sibling,
        )]
        with patch.dict(os.environ, {"WORKBENCH_CONFIG_HOME": str(self.config)}):
            manager._protect_registered_resources(self.workspace, items)
        for selected in items[:-1]:
            self.assertIn("registered-resource", selected["deletion"]["reason_codes"])
        self.assertNotIn("registered-resource", items[-1]["deletion"]["reason_codes"])
        self.assertIn("registered-catalog-unproven", items[-1]["deletion"]["reason_codes"])

    def test_resource_catalog_validates_foreign_transport_records_without_payload_scan(self) -> None:
        selected = self._publish("selected")
        foreign = self.base / "foreign-workspace"
        foreign.mkdir()
        other = CoreTransportTrees(
            workspace=foreign, configuration_home=self.config, owner_id="public-export",
        )
        with other.stage(self.base / "foreign-outputs" / "other") as stage:
            self._fill(stage.path)
            foreign_tree = stage.publish(validate=lambda _: None, domain_id="reviewed")
        catalog = ResourceCatalog(self.config)
        with patch.object(module, "inventory_tree", side_effect=AssertionError("payload scan")):
            inventory = catalog.inventory(workspace=self.workspace)
        self.assertEqual("ready-unproven", inventory["root_state"])
        self.assertEqual([selected.tree_id], [row["tree_id"] for row in inventory["transport_trees"]])
        self.assertEqual("committed-record", inventory["transport_trees"][0]["status"])
        self.assertEqual("catalog-only", inventory["transport_trees"][0]["verification"])

        nonce = foreign_tree.tree_id.rsplit(":", 1)[1]
        commit = catalog.root / "transport-trees" / "commits" / f"{nonce}.json"
        original = commit.read_bytes()
        changed = check_storage.read_json(commit)
        changed["intent_id"] = "wrong"
        changed.pop("id")
        commit.write_bytes(check_storage.canonical(check_storage.seal(
            "workbench-transport-commit-v2", changed,
        )))
        try:
            with self.assertRaises(DurableResourceError) as caught:
                catalog.inventory(workspace=self.workspace)
            self.assertEqual("resource.changed", caught.exception.code)
        finally:
            commit.write_bytes(original)
        self.assertEqual([selected.tree_id], [
            row["tree_id"] for row in catalog.inventory(workspace=self.workspace)["transport_trees"]
        ])

    def test_resource_catalog_refuses_orphan_record_and_preserves_pre_record_lock(self) -> None:
        selected = self._publish("selected")
        catalog = ResourceCatalog(self.config)
        namespace = catalog.root / "transport-trees"
        foreign = self.base / "foreign-workspace"
        foreign.mkdir()
        orphan = namespace / "intents" / ("a" * 32 + ".json")
        orphan.write_bytes(b"{}")
        orphan.chmod(0o600)
        try:
            with self.assertRaises(DurableResourceError) as caught:
                catalog.inventory(workspace=foreign)
            self.assertEqual("resource.changed", caught.exception.code)
        finally:
            orphan.unlink()

        pre_record_lock = namespace / "leases" / ("b" * 32 + ".lock")
        pre_record_lock.touch(mode=0o600)
        try:
            self.assertEqual([], catalog.inventory(workspace=foreign)["transport_trees"])
        finally:
            pre_record_lock.unlink()
        self.assertEqual([selected.tree_id], [
            row["tree_id"] for row in catalog.inventory(workspace=self.workspace)["transport_trees"]
        ])

    def test_accepted_310000_parent_directory_model_streams_with_small_memory(self) -> None:
        def rows(branches: int):
            yield {"path": "tree", "kind": "directory", "mode": 0o755}
            for number in range(branches):
                prefix = f"tree/{number:04d}"
                for depth in range(31):
                    relative = prefix + "/d" * depth
                    module._path(relative)
                    yield {"path": relative, "kind": "directory", "mode": 0o755}
                relative = prefix + "/d" * 30 + "/file"
                module._path(relative)
                yield {"path": relative, "kind": "file", "mode": 0o644,
                       "size": 0, "sha256": "0" * 64}
            yield {"path": "export-manifest.json", "kind": "file", "mode": 0o644,
                   "size": 0, "sha256": "0" * 64}

        summary = summarize_rows(rows(10_000))
        tracemalloc.start()
        try:
            summarize_rows(rows(1_000))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual((10_001, 310_001), (summary.file_count, summary.directory_count))
        self.assertLess(peak, 16 * 1024 * 1024)
        source_name = "/".join(["a" * 15] * 31 + ["b" * 16])
        self.assertEqual(512, len(source_name.encode("utf-8")))
        module._path("tree/" + source_name)

    def test_inventory_does_not_use_v1_directory_limited_walker(self) -> None:
        root = self.base / "tree-root"
        self._fill(root)
        with patch("workbench_core.transport_trees.check_storage.manifest_paths",
                   side_effect=AssertionError("V1 walker called")):
            self.assertEqual(2, inventory_tree(root).file_count)

    @unittest.skipUnless(os.environ.get("WORKBENCH_TRANSPORT_STRESS") == "1",
                         "large filesystem proof runs only when selected")
    def test_filesystem_walk_exceeds_old_100000_directory_ceiling(self) -> None:
        root = self.base / "large-tree"
        root.mkdir(mode=0o700)
        tree = root / "tree"
        tree.mkdir(mode=0o755)
        for number in range(10_000):
            branch = tree / f"{number:04d}"
            branch.mkdir(mode=0o755)
            for _ in range(9):
                branch /= "d"
                branch.mkdir(mode=0o755)
            (branch / "file").write_bytes(b"x")
        (root / "export-manifest.json").write_bytes(b"{}\n")
        summary = inventory_tree(root)
        self.assertEqual((10_001, 100_001), (summary.file_count, summary.directory_count))

    def test_owner_refusal_and_changed_member_keep_stage(self) -> None:
        target = self.outputs / "refused"
        with self.assertRaisesRegex(ValueError, "owner refused"):
            with self.host.stage(target) as stage:
                self._fill(stage.path)
                stage.publish(validate=lambda _: (_ for _ in ()).throw(ValueError("owner refused")),
                              domain_id="reviewed")
        row = self.host.inventory()[0]
        self.assertEqual("failed", row["status"])
        self.assertEqual(b"reviewed\n", (row["staging"] / "tree/source.txt").read_bytes())
        self.assertFalse(target.exists())

        target = self.outputs / "changed"
        with self.assertRaisesRegex(TransportTreeError, "changed during owner validation"):
            with self.host.stage(target) as stage:
                self._fill(stage.path)
                stage.publish(
                    validate=lambda path: (path / "tree/source.txt").write_bytes(b"changed\n"),
                    domain_id="reviewed",
                )
        self.assertFalse(target.exists())

    def test_symlink_special_file_and_mode_are_refused(self) -> None:
        for name, mutate in (
            ("link", lambda path: (path / "tree/source.txt").symlink_to(self.base / "elsewhere")),
            ("fifo", lambda path: os.mkfifo(path / "tree/pipe")),
            ("mode", lambda path: os.chmod(path / "tree/source.txt", 0o600)),
        ):
            with self.subTest(name=name):
                target = self.outputs / name
                with self.assertRaises(TransportTreeError):
                    with self.host.stage(target) as stage:
                        self._fill(stage.path)
                        if name == "link":
                            (stage.path / "tree/source.txt").unlink()
                        mutate(stage.path)
                        stage.publish(validate=lambda _: None, domain_id="reviewed")
                self.assertFalse(target.exists())

    def test_destination_collision_is_no_replace_and_stage_remains(self) -> None:
        target = self.outputs / "raced"

        def collide(_path: Path) -> None:
            target.mkdir()
            (target / "winner").write_bytes(b"winner")

        with self.assertRaises(TransportTreeError) as caught:
            with self.host.stage(target) as stage:
                self._fill(stage.path)
                stage.publish(validate=collide, domain_id="reviewed")
        self.assertEqual("output.exists", caught.exception.code)
        self.assertEqual(b"winner", (target / "winner").read_bytes())
        self.assertEqual(b"reviewed\n", (self.host.inventory()[0]["staging"] / "tree/source.txt").read_bytes())

    def test_changed_parent_and_unsupported_filesystem_refuse_publication(self) -> None:
        target = self.outputs / "parent"
        displaced = self.base / "old-outputs"
        original = module._rename_no_replace

        def swap(source, destination, **kwargs):
            self.outputs.rename(displaced)
            self.outputs.mkdir()
            return original(source, destination, **kwargs)

        with patch.object(module, "_rename_no_replace", side_effect=swap):
            with self.assertRaises(TransportTreeError):
                with self.host.stage(target) as stage:
                    self._fill(stage.path)
                    stage.publish(validate=lambda _: None, domain_id="reviewed")
        self.assertFalse(target.exists())
        self.assertTrue((displaced / self.host.inventory()[0]["staging"].parent.name / "payload").exists())

        target = self.outputs / "unsupported"
        with patch.object(module, "_rename_no_replace",
                          side_effect=ManagedTreeError("output.filesystem", "unsupported filesystem")):
            with self.assertRaisesRegex(TransportTreeError, "unsupported filesystem"):
                with self.host.stage(target) as stage:
                    self._fill(stage.path)
                    stage.publish(validate=lambda _: None, domain_id="reviewed")
        self.assertFalse(target.exists())

    def test_intent_before_rename_restart_replays_original_inode(self) -> None:
        target = self.outputs / "resume"
        original = module._rename_no_replace
        with patch.object(module, "_rename_no_replace", side_effect=OSError("interrupted")):
            with self.assertRaises(TransportTreeError):
                with self.host.stage(target) as stage:
                    self._fill(stage.path)
                    stage.publish(validate=lambda _: None, domain_id="reviewed")
        row = self.host.inventory()[0]
        self.assertEqual("prepared-incomplete", row["status"])
        self.assertFalse(target.exists())
        self.assertTrue(row["staging"].exists())
        self.assertEqual(target, self._host().reconcile(row["tree_id"]).path)
        self.assertEqual("committed", self._host().inventory()[0]["status"])
        self.assertIs(module._rename_no_replace, original)

    def test_abrupt_exit_before_and_after_intent_preserves_recovery_boundary(self) -> None:
        source_root = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(source_root / "api/src"), str(source_root / "core/src"),
        ))
        environment.update({
            "W8_WORKSPACE": str(self.workspace), "W8_CONFIG": str(self.config),
            "W8_OUTPUTS": str(self.outputs),
        })
        script = """
import os
from pathlib import Path
from workbench_core import transport_trees as module

host = module.CoreTransportTrees(
    workspace=Path(os.environ['W8_WORKSPACE']),
    configuration_home=Path(os.environ['W8_CONFIG']),
    owner_id='public-export',
)
name = os.environ['W8_NAME']
with host.stage(Path(os.environ['W8_OUTPUTS']) / name) as stage:
    stage.path.mkdir(mode=0o700)
    (stage.path / 'tree').mkdir(mode=0o755)
    (stage.path / 'tree/source.txt').write_bytes(b'reviewed\\n')
    (stage.path / 'export-manifest.json').write_bytes(b'{}\\n')
    if name == 'before-intent':
        os._exit(72)
    if name == 'after-intent':
        module._rename_no_replace = lambda *args, **kwargs: os._exit(73)
    if name == 'after-rename':
        original_write = host._write
        def crash_before_commit(record_name, *args, **kwargs):
            if record_name == 'commits':
                os._exit(74)
            return original_write(record_name, *args, **kwargs)
        host._write = crash_before_commit
    stage.publish(validate=lambda _: None, domain_id='reviewed')
"""
        for name, expected_exit in (("before-intent", 72), ("after-intent", 73),
                                    ("after-rename", 74)):
            with self.subTest(name=name):
                environment["W8_NAME"] = name
                result = subprocess.run(
                    [sys.executable, "-c", script], env=environment,
                    capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(expected_exit, result.returncode, result.stderr)
        rows = {row["path"].name: row for row in self._host().inventory()}
        self.assertEqual("reserved-incomplete", rows["before-intent"]["status"])
        self.assertEqual("prepared-incomplete", rows["after-intent"]["status"])
        self.assertEqual("prepared-incomplete", rows["after-rename"]["status"])
        self.assertTrue(rows["before-intent"]["staging"].is_dir())
        self.assertTrue(rows["after-rename"]["path"].is_dir())
        self.assertFalse(rows["after-rename"]["staging"].exists())
        catalog_rows = {
            Path(row["path"]).name: row
            for row in ResourceCatalog(self.config).inventory(workspace=self.workspace)["transport_trees"]
        }
        self.assertEqual("reserved-incomplete", catalog_rows["before-intent"]["status"])
        self.assertEqual("prepared-incomplete", catalog_rows["after-intent"]["status"])
        self.assertEqual("prepared-incomplete", catalog_rows["after-rename"]["status"])
        with self.assertRaises(TransportTreeError):
            self._host().reconcile(rows["before-intent"]["tree_id"])
        reference = self._host().reconcile(rows["after-intent"]["tree_id"])
        self.assertEqual(b"reviewed\n", (reference.path / "tree/source.txt").read_bytes())
        published = self._host().reconcile(rows["after-rename"]["tree_id"])
        self.assertEqual(b"reviewed\n", (published.path / "tree/source.txt").read_bytes())

    def test_rename_before_commit_restart_replays_published_inode(self) -> None:
        target = self.outputs / "published-uncommitted"
        original = self.host._write

        def fail_commit(name, *args, **kwargs):
            if name == "commits":
                raise OSError("simulated crash after rename")
            return original(name, *args, **kwargs)

        with patch.object(self.host, "_write", side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                with self.host.stage(target) as stage:
                    self._fill(stage.path)
                    stage.publish(validate=lambda _: None, domain_id="reviewed")
        self.assertTrue(target.is_dir())
        row = self.host.inventory()[0]
        self.assertEqual("prepared-incomplete", row["status"])
        self.assertEqual(target, self._host().reconcile(row["tree_id"]).path)

    def test_changed_inode_on_recovery_is_refused(self) -> None:
        target = self.outputs / "changed-inode"
        with patch.object(module, "_rename_no_replace", side_effect=OSError("interrupted")):
            with self.assertRaises(TransportTreeError):
                with self.host.stage(target) as stage:
                    self._fill(stage.path)
                    stage.publish(validate=lambda _: None, domain_id="reviewed")
        row = self.host.inventory()[0]
        original = self.outputs / "original"
        row["staging"].rename(original)
        self._fill(row["staging"])
        with self.assertRaises(TransportTreeError):
            self._host().reconcile(row["tree_id"])
        self.assertFalse(target.exists())
        self.assertEqual(b"reviewed\n", (original / "tree/source.txt").read_bytes())

    def test_recovery_and_readback_refuse_content_or_mode_drift(self) -> None:
        target = self.outputs / "drift-before-rename"
        with patch.object(module, "_rename_no_replace", side_effect=OSError("interrupted")):
            with self.assertRaises(TransportTreeError):
                with self.host.stage(target) as stage:
                    self._fill(stage.path)
                    stage.publish(validate=lambda _: None, domain_id="reviewed")
        row = self.host.inventory()[0]
        (row["staging"] / "tree/source.txt").write_bytes(b"other\n")
        with self.assertRaisesRegex(TransportTreeError, "inventory changed"):
            self._host().reconcile(row["tree_id"])
        self.assertFalse(target.exists())

        published = self._publish("drift-after-commit")
        os.chmod(published.path / "tree/source.txt", 0o755)
        with self.assertRaisesRegex(TransportTreeError, "inventory changed"):
            self._host().describe(published.tree_id)

    def test_reserved_stage_without_intent_never_reconciles_as_published(self) -> None:
        target = self.outputs / "before-intent"
        with self.host.stage(target) as stage:
            self._fill(stage.path)
        row = self.host.inventory()[0]
        self.assertEqual("failed", row["status"])
        with self.assertRaises(TransportTreeError):
            self._host().reconcile(row["tree_id"])
        self.assertTrue(row["staging"].is_dir())

    @unittest.skipUnless(sys.platform == "linux", "Linux mount IDs are required")
    def test_same_device_other_mount_is_refused(self) -> None:
        root = self.base / "mounted"
        self._fill(root)
        with patch.object(module, "_mount_id", side_effect=[7, 7, 7, 8]):
            with self.assertRaisesRegex(TransportTreeError, "another mount"):
                inventory_tree(root)


if __name__ == "__main__":
    unittest.main()
