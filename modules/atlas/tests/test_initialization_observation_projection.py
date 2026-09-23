from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workbench_atlas_categorical_graph import CategoricalGraphQuery
from workbench_atlas_observations.projection import (
    ObservationProjectionError, project_retained_observations,
    resolve_json_pointer, resolve_retained_evidence,
)
from workbench_atlas_observations.cli import main
from workbench_atlas_observations import projection


def reader_fixture():
    report = {"id": "result:fixture", "state": "completed", "findings": [], "native": {
        "status": "native-failed", "result": {"initialization": {"status": "native-failed"},
        "execution": {"nativeInitialization": {
            "stage": "original-recipe-initialization-returned", "loaderState": "AVAILABLE",
            "nativeStoredFurnaceRecipes": {"schema": "axiom.native-stored-furnace-recipes.v1",
                "status": "observed", "storedValuesComplete": True,
                "callbackInvocationsByObserver": 0, "smelting": [
                    {"input": {"type": "item-stack", "item": "fixture:ore", "count": 1},
                     "value": {"type": "item-stack", "item": "fixture:ingot", "count": 1}}]}}}}}}
    manifest = {"id": "snapshot:fixture", "format": "workbench-check-snapshot-v1",
        "request_id": "request:fixture", "result_id": report["id"], "scope_id": "scope:fixture",
        "producer": {"id": "axiom", "build_sha256": "a" * 64}, "bindings": {"source": "b" * 64},
        "source_result": {"sha256": "c" * 64, "bytes": 99}, "retained_inputs": [],
        "native_outcome": "native-failed", "coverage": "incomplete",
        "sections": [{"id": "report", "schema": "axiom-retained-report-v1", "state": "observed"}]}
    reader = SimpleNamespace(manifest=manifest, request={"baseline": None, "inputs": {"context": {
        "id": "supersymmetry:fixture", "initializationStage": "recipes", "side": "server"}}},
        scope_supported=True, unsupported_sections=[], read_record=lambda section, key: report)
    return reader, report


class InitializationProjectionTests(unittest.TestCase):
    def test_cli_accepts_parent_segments_and_symlinked_parent_receipts(self):
        reader, _ = reader_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "parent").mkdir()
            (root / "alias").symlink_to(root / "parent", target_is_directory=True)
            adapter = SimpleNamespace(OBSERVATION_GRAPH_API_VERSION=1,
                project_snapshot=lambda path, output, **kwargs: project_retained_observations(
                    reader, output, profile_id="fixture", **kwargs))
            for destination in (root / "parent/../graph", root / "alias/other-graph"):
                with self.subTest(destination=destination):
                    output, error = StringIO(), StringIO()
                    with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter):
                        status = main(["import-snapshot", str(root / "snapshot"), "--output", str(destination),
                                       "--pack-profile", "fixture", "--json"], output=output, error=error)
                    self.assertEqual(0, status, error.getvalue())
                    self.assertEqual(str(destination.resolve()), json.loads(output.getvalue())["root"])
                    with CategoricalGraphQuery(destination) as view:
                        self.assertEqual(json.loads(output.getvalue())["graph_set_id"], view.manifest["graph_set_id"])

    def test_dangling_output_symlink_is_not_followed(self):
        reader, _ = reader_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "output"
            destination.symlink_to(root / "absent", target_is_directory=True)
            with self.assertRaisesRegex(ObservationProjectionError, "already exists"):
                project_retained_observations(reader, destination, profile_id="fixture")
            self.assertTrue(destination.is_symlink())
            self.assertFalse((root / "absent").exists())

    def test_cancel_during_final_stream_validation_removes_unpublished_staging(self):
        reader, _ = reader_fixture()
        state = {"verifying": False, "checks": 0}
        original = projection.validate_bundle_directory
        failure = RuntimeError("cancelled final projection verification")
        def validate(*args, **kwargs):
            state["verifying"] = True
            return original(*args, **kwargs)
        def cancel():
            if state["verifying"]:
                state["checks"] += 1
                if state["checks"] == 3:
                    raise failure
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(projection, "validate_bundle_directory", side_effect=validate):
                with self.assertRaises(RuntimeError) as raised:
                    project_retained_observations(reader, root / "graph", profile_id="fixture", check_cancelled=cancel)
            self.assertIs(failure, raised.exception)
            self.assertEqual(3, state["checks"])
            self.assertEqual([], list(root.iterdir()))

    def test_native_failure_and_incomplete_coverage_survive_successful_projection(self):
        reader, report = reader_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "graph"
            receipt = project_retained_observations(reader, output, profile_id="supersymmetry")
            self.assertEqual(receipt["state"], "complete")
            self.assertEqual(receipt["native_outcome"], "native-failed")
            self.assertEqual(receipt["capture_coverage"], "incomplete")
            self.assertTrue(any(row["family"] == "furnace" and row["node_count"] > 0
                                for row in receipt["family_coverage"]))
            with CategoricalGraphQuery(output) as view:
                self.assertEqual(view.manifest["scope"]["initialization_context"]["side"], "server")
                self.assertEqual(view.manifest["evidence_binding"]["snapshot_id"], "snapshot:fixture")
                self.assertEqual(view.connection.execute("SELECT count(*) FROM nodes WHERE kind='gt-recipe'").fetchone()[0], 0)
            self.assertFalse(any(p.name.startswith(".atlas-observations-") for p in Path(tmp).iterdir()))

    def test_cancellation_during_projection_never_exposes_complete_graph(self):
        reader, _ = reader_fixture()
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 12:
                raise RuntimeError("operation cancelled")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "graph"
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                project_retained_observations(reader, output, profile_id="supersymmetry", check_cancelled=cancel)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_unsupported_snapshot_and_wrong_side_refuse_before_publication(self):
        for mutation in (lambda r: setattr(r, "scope_supported", False),
                         lambda r: r.manifest["producer"].update(id="other"),
                         lambda r: r.manifest["sections"][0].update(schema="future")):
            reader, _ = reader_fixture()
            mutation(reader)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ObservationProjectionError):
                    project_retained_observations(reader, Path(tmp)/"graph", profile_id="supersymmetry")
                self.assertEqual(list(Path(tmp).iterdir()), [])
        reader, _ = reader_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ObservationProjectionError, "paired side"):
                project_retained_observations(reader, Path(tmp)/"graph", side="candidate", profile_id="supersymmetry")

    def test_original_evidence_is_exact_and_rejects_wrong_snapshot(self):
        reader, report = reader_fixture()
        ref = {"snapshot_id": "snapshot:fixture", "section": "report", "record_key": "value",
               "json_pointer": "/native/result/execution/nativeInitialization/nativeStoredFurnaceRecipes/smelting/0"}
        resolved = resolve_retained_evidence(reader, [ref])
        self.assertEqual(resolved["state"], "resolved")
        self.assertEqual(resolved["records"][0]["value"], resolve_json_pointer(report, ref["json_pointer"]))
        wrong = dict(ref, snapshot_id="snapshot:other")
        with self.assertRaisesRegex(ObservationProjectionError, "different snapshot"):
            resolve_retained_evidence(reader, [wrong])

    def test_pointer_escaping_and_invalid_array_positions(self):
        self.assertEqual(resolve_json_pointer({"a/b": {"~": [7]}}, "/a~1b/~0/0"), 7)
        for pointer in ("/01", "/-1", "/1", "/~2", "missing-slash"):
            with self.assertRaises(ObservationProjectionError):
                resolve_json_pointer([7], pointer)
