"""CI uploads follow source-Core custody and the native build manifest."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / "validation", ROOT / "tools"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from build_tree_custody import publish_build_tree  # noqa: E402
from ci_artifacts import CiArtifactError, native_assembly_upload  # noqa: E402
from validation_diagnostics import DiagnosticRun  # noqa: E402
from verify_wheelhouse import verify  # noqa: E402


class CiArtifactTests(unittest.TestCase):
    def _fixture(self, base: Path, *, owner_id: str = "native-build") -> tuple[Path, Path, Path, dict]:
        workspace = base / "workspace"
        workspace.mkdir()
        configuration_home = base / "config"
        artifact = workspace / ".workbench/native-suite"

        def produce(path: Path) -> dict:
            wheels = path / "wheels"
            wheels.mkdir(parents=True)
            rows = []
            for name, version in (("pip", "26.1.2"), ("workbench-core", "0.1.0")):
                filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
                payload = (name + "\n").encode("ascii")
                (wheels / filename).write_bytes(payload)
                rows.append({"filename": filename, "name": name, "version": version,
                             "size": len(payload), "sha256": sha256(payload).hexdigest()})
            manifest = {
                "format": "workbench-native-wheelhouse-v1",
                "source_sha256": "a" * 64,
                "selected_components": ["workbench-core"],
                "native_versions": {"workbench-core": "0.1.0"},
                "target": {"python": "3.13", "platform": "linux", "machine": "x86_64"},
                "wheels": rows,
                "qualified": False,
            }
            (path / "requirements.lock").write_text("".join(
                f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
                for row in rows
            ), encoding="utf-8")
            (path / "wheelhouse.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            return manifest

        diagnostics_path = workspace / ".workbench/validation/ci/native-build"
        with DiagnosticRun(diagnostics_path, "native-build", ("assembly",)) as diagnostics:
            with diagnostics.phase("assembly"):
                manifest, reference = publish_build_tree(
                    artifact, produce,
                    lambda path, expected: self.assertEqual(expected, verify(path)),
                    lambda path, _result: "workbench-native-wheelhouse-v1:sha256:"
                    + sha256((path / "wheelhouse.json").read_bytes()).hexdigest(),
                    owner_id=owner_id, workspace=workspace,
                    configuration_home=configuration_home,
                )
            diagnostics.document["metadata"].update(
                artifact_path=str(reference.path), artifact_tree_id=reference.tree_id,
                source_sha256=manifest["source_sha256"], target=manifest["target"],
                wheels=manifest["wheels"],
            )
        return workspace, configuration_home, diagnostics_path, manifest

    def test_source_core_tree_and_manifest_select_exact_successful_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, home, diagnostics, manifest = self._fixture(Path(temporary))
            selection = native_assembly_upload(
                diagnostics, workspace=workspace, configuration_home=home,
            )
            artifact = workspace / ".workbench/native-suite"
            self.assertEqual(str(artifact), selection["path"])
            self.assertTrue(selection["tree_id"].startswith("workbench-tree-v1:"))
            self.assertEqual(
                sha256((artifact / "wheelhouse.json").read_bytes()).hexdigest(),
                selection["manifest_sha256"],
            )
            self.assertFalse(manifest["qualified"])

    def test_foreign_workspace_owner_and_changed_wheel_cannot_be_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace, home, diagnostics, _manifest = self._fixture(base)
            other = base / "other-workspace"
            other.mkdir()
            with self.assertRaises(CiArtifactError):
                native_assembly_upload(diagnostics, workspace=other, configuration_home=home)
            wheel = next((workspace / ".workbench/native-suite/wheels").iterdir())
            wheel.write_bytes(b"changed\n")
            with self.assertRaises(ValueError):
                native_assembly_upload(diagnostics, workspace=workspace, configuration_home=home)

        with tempfile.TemporaryDirectory() as temporary:
            workspace, home, diagnostics, _manifest = self._fixture(
                Path(temporary), owner_id="other-builder",
            )
            with self.assertRaises(CiArtifactError):
                native_assembly_upload(diagnostics, workspace=workspace, configuration_home=home)

    def test_failed_producer_report_cannot_select_success_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, home, diagnostics, _manifest = self._fixture(Path(temporary))
            report_path = diagnostics / "report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["state"] = "failed"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(CiArtifactError):
                native_assembly_upload(diagnostics, workspace=workspace, configuration_home=home)

    def test_workflow_uses_discovered_success_path_and_keeps_failure_diagnostics(self) -> None:
        import yaml

        workflow = yaml.load((ROOT / ".github/workflows/validate.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual({"workflow_dispatch"}, set(workflow["on"]))
        steps = workflow["jobs"]["native-packages"]["steps"]
        discovery = next(step for step in steps if step.get("id") == "native_upload")
        self.assertIn("validation/ci_artifacts.py native-assembly", discovery["run"])
        assembly = next(step for step in steps if step.get("with", {}).get("name") == "native-linux-assembly")
        self.assertEqual("success()", assembly["if"])
        self.assertEqual("${{ steps.native_upload.outputs.path }}", assembly["with"]["path"])
        diagnostics = next(step for step in steps if step.get("with", {}).get("name") == "native-diagnostics-linux-x64")
        self.assertEqual("always()", diagnostics["if"])
        self.assertEqual(".workbench/validation/", diagnostics["with"]["path"])


if __name__ == "__main__":
    unittest.main()
