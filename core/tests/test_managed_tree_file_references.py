"""Core-issued file source references for managed result trees."""

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from workbench_api.durable_resources import DurableResourceError
from workbench_api.managed_trees import ManagedTreeError
from workbench_core.managed_trees import CoreManagedTrees


class ManagedTreeFileReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.config = root / "config"
        self.evidence = root / "evidence"
        self.host = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="workbench-shell",
        )
        self.source = root / "source.zip"
        self.payload = b"exact source bytes"
        self.source.write_bytes(self.payload)

    def _retain(self):
        return self.host.retain_file_reference(
            "evidence", "source.zip", self.source,
            sha256=sha256(self.payload).hexdigest(), size=len(self.payload),
            domain_id="bootstrap-plan:source",
        )

    def test_issued_source_reopens_and_tree_references_its_exact_bytes(self) -> None:
        source = self._retain()
        reopened, data = self.host.read_file_reference(source.resource_id)
        self.assertEqual(reopened, source)
        self.assertEqual(data, self.payload)
        with self.host.stage("evidence", "fixture") as stage:
            stage.path.mkdir()
            (stage.path / "instance.cfg").write_bytes(b"launcher")
            tree = stage.publish(
                validate=lambda _: None,
                references=(source.resource_id,),
            )
        self.assertEqual(
            self.host.reconcile(tree.tree_id).references,
            (source.resource_id,),
        )
        source.path.write_bytes(b"changed source bytes")
        with self.assertRaises(ManagedTreeError):
            self.host.read_file_reference(source.resource_id)
        with self.assertRaises((ManagedTreeError, DurableResourceError)):
            self.host.reconcile(tree.tree_id)

    def test_source_reader_refuses_foreign_owner_and_workspace(self) -> None:
        source = self._retain()
        foreign_owner = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="other",
        )
        with self.assertRaisesRegex(ManagedTreeError, "another owner"):
            foreign_owner.read_file_reference(source.resource_id)
        foreign_workspace = CoreManagedTrees(
            workspace=self.workspace.parent / "other-workspace",
            configuration_home=self.config,
            locations={"evidence": self.evidence}, owner_id="workbench-shell",
        )
        with self.assertRaisesRegex(ManagedTreeError, "cannot be reopened"):
            foreign_workspace.read_file_reference(source.resource_id)

    def test_snapshot_refuses_changed_source_and_exposes_core_bound(self) -> None:
        self.assertEqual(self.host.max_file_reference_bytes, 32 * 1024 * 1024)
        with self.assertRaisesRegex(ManagedTreeError, "cannot be retained exactly"):
            self.host.retain_file_reference(
                "evidence", "source.zip", self.source,
                sha256="0" * 64, size=len(self.payload),
            )
        with self.assertRaisesRegex(ManagedTreeError, "identity is invalid"):
            self.host.retain_file_reference(
                "evidence", "source.zip", self.source,
                sha256=sha256(self.payload).hexdigest(),
                size=self.host.max_file_reference_bytes + 1,
            )


if __name__ == "__main__":
    unittest.main()
