"""Release upload discovery reopens descriptor-selected bytes under source Core."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import publish_component_candidate as candidate
from verify_component_artifacts import expected_filenames


class ComponentCandidateCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.home = self.root / "core-home"
        self.outputs = self.workspace / ".workbench/candidates"
        self.source = self.root / "unselected-source"

    def _build_source(
        self, component: str, payload: bytes, *, extra: bool = False,
        owner_override: str | None = None,
    ) -> str:
        from workbench_core.managed_trees import CoreManagedTrees

        name, owner, relative = candidate._component_artifact(component)
        root = self.workspace / ".workbench" / ("source-" + component)
        self.source = root / "wheels" if owner == "native-build" else root
        host = CoreManagedTrees(
            workspace=self.workspace, configuration_home=self.home,
            locations={"artifacts": root.parent}, owner_id=owner_override or owner,
        )
        with host.stage("artifacts", root.name, requested_path=root) as stage:
            stage.path.mkdir()
            selected = stage.path / relative
            selected.parent.mkdir(exist_ok=True)
            selected.write_bytes(payload)
            if extra:
                (selected.parent / "unselected-wheel.whl").write_bytes(b"unselected")
            reference = stage.publish(validate=lambda _path: None)
        self.assertEqual(name, selected.name)
        return reference.tree_id

    def _publish(self, component: str) -> dict[str, object]:
        return candidate.publish_candidate(
            component, self.source, workspace=self.workspace,
            configuration_home=self.home, output_root=self.outputs,
        )

    def _discover(self, component: str, tree_id: str) -> dict[str, object]:
        return candidate.discover_candidate(
            component, tree_id, workspace=self.workspace,
            configuration_home=self.home, output_root=self.outputs,
        )

    def test_python_and_client_candidate_reopen_exact_file_without_raw_extras(self) -> None:
        for component in ("workbench-core", "workbench-vscode"):
            with self.subTest(component=component):
                name = expected_filenames(component)[0]
                payload = (component + " candidate bytes").encode()
                source_tree_id = self._build_source(component, payload, extra=True)
                record = self._publish(component)
                self.assertEqual(candidate.FORMAT, record["format"])
                self.assertEqual(source_tree_id, record["source_tree_id"])
                self.assertEqual(name, record["artifact_name"])
                self.assertEqual(payload, (Path(record["path"]) / name).read_bytes())
                self.assertEqual([name], [path.name for path in Path(record["path"]).iterdir()])
                self.assertEqual(record, self._discover(component, str(record["tree_id"])))
                self.assertEqual(0o644, (Path(record["path"]) / name).stat().st_mode & 0o777)
                from workbench_core.storage.registered import ResourceCatalog
                retained = ResourceCatalog(self.home).trees.describe(str(record["tree_id"]), workspace=self.workspace)
                self.assertEqual((source_tree_id,), retained.references)

    def test_drifted_candidate_and_foreign_descriptor_refuse_discovery(self) -> None:
        name = expected_filenames("workbench-core")[0]
        self._build_source("workbench-core", b"exact candidate")
        record = self._publish("workbench-core")
        with self.assertRaises(ValueError):
            self._discover("workbench-vscode", str(record["tree_id"]))
        (Path(record["path"]) / name).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self._discover("workbench-core", str(record["tree_id"]))

    def test_referenced_source_drift_refuses_candidate_rediscovery(self) -> None:
        name = expected_filenames("workbench-core")[0]
        self._build_source("workbench-core", b"exact candidate")
        record = self._publish("workbench-core")
        (self.source / name).write_bytes(b"changed source")
        with self.assertRaises(ValueError):
            self._discover("workbench-core", str(record["tree_id"]))

    def test_wrong_source_build_owner_refuses_candidate_publication(self) -> None:
        self._build_source("workbench-core", b"bytes", owner_override="another-build")
        with self.assertRaisesRegex(candidate.CandidatePublicationError, "Core source build is unavailable"):
            self._publish("workbench-core")
        self.assertFalse(self.outputs.exists())

    def test_source_symlink_or_unsupported_axiom_does_not_publish(self) -> None:
        name = expected_filenames("workbench-core")[0]
        (self.root / "foreign").write_bytes(b"foreign")
        self._build_source("workbench-core", b"exact candidate")
        (self.source / name).unlink()
        (self.source / name).symlink_to(self.root / "foreign")
        with self.assertRaisesRegex(candidate.CandidatePublicationError, "source build"):
            self._publish("workbench-core")
        with self.assertRaisesRegex(candidate.CandidatePublicationError, "Python or client"):
            self._publish("workbench-axiom-engine")
        parent = self.outputs / "outputs" / candidate.OWNER
        self.assertFalse(parent.exists())

    def test_failed_core_stage_remains_inspectable_without_candidate_output(self) -> None:
        name = expected_filenames("workbench-core")[0]
        self._build_source("workbench-core", b"source")

        def fail(_source: Path, target: Path) -> str:
            target.write_bytes(b"partial")
            raise candidate.CandidatePublicationError("injected copy failure")

        with patch.object(candidate, "_copy_exact_source", side_effect=fail):
            with self.assertRaisesRegex(candidate.CandidatePublicationError, "injected"):
                self._publish("workbench-core")
        parent = self.outputs / "outputs" / candidate.OWNER
        self.assertEqual([], [path for path in parent.iterdir() if not path.name.startswith(".workbench-tree-")])
        stages = list(parent.glob(".workbench-tree-*.pending/payload/" + name))
        self.assertEqual(1, len(stages))
        self.assertEqual(b"partial", stages[0].read_bytes())

    def test_isolated_source_checkout_import_uses_source_core(self) -> None:
        name = expected_filenames("workbench-core")[0]
        self._build_source("workbench-core", b"source-only candidate")
        script = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "import publish_component_candidate as candidate; "
            "result = candidate.publish_candidate(sys.argv[2], Path(sys.argv[3]), "
            "workspace=Path(sys.argv[4]), configuration_home=Path(sys.argv[5]), "
            "output_root=Path(sys.argv[6])); "
            "import workbench_core; "
            "assert Path(workbench_core.__file__).is_relative_to(Path(sys.argv[7])); "
            "print(json.dumps(result))"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script, str(ROOT / "tools"), "workbench-core",
             str(self.source), str(self.workspace), str(self.home), str(self.outputs),
             str(ROOT / "core/src")],
            cwd=ROOT, capture_output=True, text=True, timeout=20, check=True,
        )
        record = json.loads(completed.stdout)
        self.assertEqual(b"source-only candidate", (Path(record["path"]) / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
