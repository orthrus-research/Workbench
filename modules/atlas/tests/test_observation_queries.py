"""Observation queries preserve arbitrary families and exact evidence boundaries."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for source in (ROOT / "api/src", ROOT / "core/src", ROOT / "modules/atlas/src"):
    sys.path.insert(0, str(source))

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_atlas_categorical_graph import bundle as graph_bundle
from workbench_atlas_observations import ObservationError, describe_observations, open_observations
from workbench_atlas_observations.cli import main


def graph(root: Path, *, marker="one", legacy=False):
    kinds = ("material", "registry-entry", "gt-recipe-observation", "crafting-observation", "furnace-observation")
    references = [{"snapshot_id": marker, "section": "report", "pointer": f"/records/{index}"} for index in range(3)]
    nodes = [node_record(kind, f"fixture:{index}", {"name": "Café Observation", "stored": {"tag": [2, 1],
                         "large": 9007199254740993}, "lookup": None}, references) for index, kind in enumerate(kinds)]
    edges = [edge_record("observed-with", nodes[0]["id"], row["id"], {"ordinal": index}, references)
             for index, row in enumerate(nodes[1:])]
    arguments = {} if legacy else {"evidence_authority": "retained-observations-v1"}
    builder = CategoricalGraphBundleBuilder(root, scope={"snapshot": marker, "lifecycle": "initialization"},
                                           evidence_binding={"coverage": {"capture": "complete", "native_outcome": "native-failed"}},
                                           **arguments)
    builder.add_partition("observations", classification="Original stored state", dependencies=(), nodes=nodes,
                          edges=edges, evidence_categories=("report",), limitations=("Matching was not observed.",))
    return builder.close(), nodes, edges


class ObservationQueryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest, self.nodes, self.edges = graph(self.root / "graph")

    def test_search_all_families_and_inspect_preserves_original_values(self):
        with open_observations(self.root / "graph") as view:
            response = view.search("CAFÉ", limit=50)
            self.assertEqual({row["kind"] for row in self.nodes}, {row["kind"] for row in response["results"]})
            self.assertEqual({"limit": 50, "offset": 0, "returned": 5, "truncated": False, "next_cursor": None}, response["page"])
            self.assertEqual("native-failed", response["context"]["coverage"]["declaration"]["native_outcome"])
            report = view.inspect(self.nodes[0]["id"])
            self.assertEqual(self.nodes[0], report["selection"])
            self.assertEqual({"outgoing": {"observed-with": 4}, "incoming": {}}, report["relationships"])
            self.assertIn("no inferred matching", report["context"]["claim_boundary"])
            only = view.search("Observation", kind="furnace-observation")
            self.assertEqual([self.nodes[-1]], only["results"])

    def test_search_pages_are_exact_and_bound_to_graph_query_kind_and_size(self):
        with open_observations(self.root / "graph") as view:
            first = view.search("Observation", limit=2)
            second = view.search("Observation", limit=2, cursor=first["page"]["next_cursor"])
            third = view.search("Observation", limit=2, cursor=second["page"]["next_cursor"])
            self.assertEqual(5, len({row["id"] for page in (first, second, third) for row in page["results"]}))
            self.assertFalse(third["page"]["truncated"])
            cursor = first["page"]["next_cursor"]
            for arguments in ({"text": "other", "limit": 2}, {"text": "Observation", "limit": 3},
                              {"text": "Observation", "limit": 2, "kind": "material"}):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(ObservationError, "cursor"):
                    view.search(cursor=cursor, **arguments)
            with self.assertRaisesRegex(ObservationError, "cursor"):
                view.search("Observation", limit=2, cursor=cursor[:-2] + "!?")
        graph(self.root / "other", marker="other")
        with open_observations(self.root / "other") as other, self.assertRaisesRegex(ObservationError, "cursor"):
            other.search("Observation", limit=2, cursor=cursor)

    def test_relationship_pages_preserve_edges_and_selectable_endpoints(self):
        with open_observations(self.root / "graph") as view:
            page = view.relationships(self.nodes[0]["id"], limit=2)
            next_page = view.relationships(self.nodes[0]["id"], limit=2, cursor=page["page"]["next_cursor"])
            actual = [row["edge"] for part in (page, next_page) for row in part["results"]]
            self.assertEqual(sorted(self.edges, key=lambda row: row["id"]), sorted(actual, key=lambda row: row["id"]))
            for row in page["results"]:
                self.assertEqual(row["node"], view.inspect(row["node"]["id"])["selection"])
            incoming = view.relationships(self.nodes[1]["id"], direction="incoming", relation="observed-with")
            self.assertEqual(self.nodes[0], incoming["results"][0]["node"])
            for arguments in ({"direction": "incoming"}, {"relation": "other"}):
                with self.assertRaisesRegex(ObservationError, "cursor"):
                    view.relationships(self.nodes[0]["id"], limit=2, cursor=page["page"]["next_cursor"], **arguments)
            with self.assertRaisesRegex(ObservationError, "does not exist"):
                view.relationships("absent")

    def test_evidence_is_exact_paginated_and_does_not_claim_resolution(self):
        with open_observations(self.root / "graph") as view:
            first = view.evidence(self.nodes[0]["id"], limit=2)
            last = view.evidence(self.nodes[0]["id"], limit=2, cursor=first["page"]["next_cursor"])
            self.assertEqual(self.nodes[0]["evidence"], first["references"] + last["references"])
            self.assertEqual("unavailable-reader-not-selected", first["original_record_resolution"])
            with self.assertRaisesRegex(ObservationError, "cursor"):
                view.evidence(self.nodes[1]["id"], limit=2, cursor=first["page"]["next_cursor"])

    def test_literal_wildcards_and_invalid_bounds(self):
        with open_observations(self.root / "graph") as view:
            self.assertEqual([], view.search("%_")["results"])
            for limit in (True, 0, 1001):
                with self.assertRaises(ObservationError):
                    view.search("Observation", limit=limit)
            with self.assertRaises(ObservationError):
                view.relationships(self.nodes[0]["id"], direction="both")

    def test_cancel_during_sql_and_after_selection_publishes_no_result(self):
        state = {"cancel": False}
        def cancel():
            if state["cancel"]:
                raise ModuleError("cancelled test observation query")
        with open_observations(self.root / "graph", check_cancelled=cancel) as view:
            def casefold(value):
                state["cancel"] = True
                return value.casefold()
            view.query.connection.create_function("atlas_casefold", 1, casefold)
            with self.assertRaisesRegex(ModuleError, "cancelled"):
                view.search("Observation")

    def test_open_and_context_cancel_inside_stream_hash_and_index_semantic_verification(self):
        root = self.root / "graph"
        before = {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in root.rglob("*") if path.is_file()}
        for operation in (open_observations, describe_observations):
            for phase, threshold in (("validate_bundle_directory", 3),
                                     ("_file_measurement", 2), ("_verify_index_semantics", 3)):
                with self.subTest(operation=operation.__name__, phase=phase):
                    state = {"active": False, "checks": 0}
                    failure = ModuleError("cancelled during " + phase)
                    connections = []
                    original_phase = getattr(graph_bundle, phase)
                    original_open = graph_bundle._open_immutable_index

                    def cancel():
                        if state["active"]:
                            state["checks"] += 1
                            if state["checks"] == threshold:
                                raise failure

                    def observe_phase(*args, **kwargs):
                        state["active"] = True
                        try:
                            return original_phase(*args, **kwargs)
                        finally:
                            state["active"] = False

                    def observe_connection(*args, **kwargs):
                        connection = original_open(*args, **kwargs)
                        connections.append(connection)
                        return connection

                    with patch.object(graph_bundle, phase, side_effect=observe_phase), \
                         patch.object(graph_bundle, "_open_immutable_index", side_effect=observe_connection):
                        with self.assertRaises(ModuleError) as raised:
                            operation(root, check_cancelled=cancel)
                    self.assertIs(failure, raised.exception)
                    self.assertEqual(threshold, state["checks"])
                    for connection in connections:
                        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                            connection.execute("SELECT 1")
        self.assertEqual(before, {path.relative_to(root): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in root.rglob("*") if path.is_file()})
        with open_observations(root) as view:
            self.assertEqual(self.nodes[0], view.inspect(self.nodes[0]["id"])["selection"])

    def test_missing_index_context_is_inspectable_and_query_refuses(self):
        (self.root / "graph/query-index.sqlite3").unlink()
        context = describe_observations(self.root / "graph")
        self.assertFalse(context["query_index"]["usable"])
        self.assertEqual(self.manifest["graph_set_id"], context["graph_set_id"])
        with self.assertRaisesRegex(ValueError, "index is missing"):
            open_observations(self.root / "graph")

    def test_legacy_v2_keeps_its_authority_and_graph_identity(self):
        manifest, nodes, _ = graph(self.root / "legacy", legacy=True)
        with open_observations(self.root / "legacy") as view:
            self.assertEqual(manifest["authority"], view.describe()["authority"])
            self.assertEqual(manifest["graph_set_id"], view.describe()["graph_set_id"])
            self.assertEqual(nodes[0], view.inspect(nodes[0]["id"])["selection"])


class ObservationCliTests(unittest.TestCase):
    def call(self, *arguments, **kwargs):
        output, error = StringIO(), StringIO()
        code = main(arguments, output=output, error=error, **kwargs)
        return code, output.getvalue(), error.getvalue()

    def test_complete_search_relationship_evidence_journey(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, nodes, _ = graph(root)
            for command in (("context", str(root)), ("search", str(root), "Observation"),
                            ("inspect", str(root), nodes[0]["id"]),
                            ("relationships", str(root), nodes[0]["id"]),
                            ("evidence", str(root), nodes[0]["id"])):
                with self.subTest(command=command):
                    status, output, error = self.call(*command, "--json")
                    self.assertEqual(0, status, error)
                    result = json.loads(output)
                    self.assertEqual(manifest["graph_set_id"], result.get("context", result)["graph_set_id"])
            status, output, error = self.call("inspect", str(root), nodes[0]["id"])
            self.assertEqual(0, status, error)
            self.assertIn("9007199254740993", output)
            self.assertIn("Matching was not observed", output)

    def test_import_uses_explicit_adapter_side_and_verifies_receipt(self):
        for false_receipt in (False, True):
            with self.subTest(false_receipt=false_receipt), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                def project(path, output, *, side, check_cancelled):
                    self.assertEqual(root / "snapshot", path)
                    self.assertEqual("candidate", side)
                    check_cancelled()
                    manifest, _, _ = graph(output)
                    return {"state": "complete", "root": str(output.resolve()),
                            "graph_set_id": "wrong" if false_receipt else manifest["graph_set_id"]}
                adapter = SimpleNamespace(OBSERVATION_GRAPH_API_VERSION=1, project_snapshot=project)
                with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter) as resolver:
                    status, output, error = self.call("import-snapshot", str(root / "snapshot"), "--output", str(root / "graph"),
                                                     "--pack-profile", "fixture", "--side", "candidate", "--json")
                resolver.assert_called_once_with("workbench.observation_graphs", "fixture")
                self.assertEqual(2 if false_receipt else 0, status, error)
                if false_receipt:
                    self.assertEqual("", output)

    def test_import_final_validation_can_cancel_without_rendering_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = {"verifying": False, "checks": 0}
            def cancel():
                if state["verifying"]:
                    state["checks"] += 1
                    if state["checks"] == 3:
                        raise ModuleError("cancelled final graph verification")
            def project(path, output, *, side, check_cancelled):
                manifest, _, _ = graph(output)
                state["verifying"] = True
                return {"state": "complete", "root": str(output.resolve()), "graph_set_id": manifest["graph_set_id"]}
            adapter = SimpleNamespace(OBSERVATION_GRAPH_API_VERSION=1, project_snapshot=project)
            with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter):
                status, output, error = self.call("import-snapshot", str(root / "snapshot"), "--output", str(root / "graph"),
                    "--pack-profile", "fixture", "--json", context=SimpleNamespace(check_cancelled=cancel))
            self.assertEqual(2, status)
            self.assertEqual("", output)
            self.assertIn("cancelled final graph verification", error)
            self.assertEqual(3, state["checks"])

    def test_human_workflow_prefers_recognizable_labels_and_keeps_exact_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            ore = node_record("initialization-entry", "opaque-hash", {"label": "Cobaltite ore"})
            dust = node_record("initialization-entry", "another-hash", {"label": "Cobaltite dust"})
            builder = CategoricalGraphBundleBuilder(root, scope={}, evidence_binding={})
            builder.add_partition("fixture", classification="fixture", dependencies=(), nodes=[ore, dust],
                edges=[edge_record("stores-output", ore["id"], dust["id"], {})], evidence_categories=("fixture",))
            builder.close()
            for command in (("search", str(root), "Cobaltite"), ("inspect", str(root), ore["id"]),
                            ("relationships", str(root), ore["id"])):
                status, output, error = self.call(*command)
                self.assertEqual(0, status, error)
                self.assertIn("initialization-entry: Cobaltite ore", output)
                self.assertIn(ore["id"], output)
                if command[0] != "inspect":
                    self.assertIn("initialization-entry: Cobaltite dust", output)
                    self.assertIn(dust["id"], output)

    def test_absent_adapter_and_cancelled_request_emit_no_success(self):
        from workbench_api.profile_extensions import ProfileExtensionError
        with patch("workbench_api.profile_extensions.require_profile_extension", side_effect=ProfileExtensionError("disabled")):
            status, output, error = self.call("import-snapshot", "/missing", "--output", "/missing-new", "--pack-profile", "fixture")
            self.assertEqual((2, ""), (status, output))
            self.assertIn("disabled", error)
        context = ExecutionContext(ROOT, ROOT / ".workbench")
        context.cancelled.set()
        status, output, error = self.call("context", "/missing", "--json", context=context)
        self.assertEqual((2, ""), (status, output))
        self.assertIn("cancelled", error)

    def test_original_evidence_resolution_is_explicit_and_reference_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            _, nodes, _ = graph(root)
            def resolve(path, references, *, check_cancelled):
                self.assertEqual(Path("/original"), path)
                check_cancelled()
                return {"state": "resolved", "snapshot_id": "one", "records": [
                    {"reference": row, "value": {"original": index}} for index, row in enumerate(references)]}
            adapter = SimpleNamespace(OBSERVATION_GRAPH_API_VERSION=1, resolve_evidence=resolve)
            arguments = ("evidence", str(root), nodes[0]["id"], "--snapshot", "/original", "--pack-profile", "fixture", "--limit", "2", "--json")
            with patch("workbench_api.profile_extensions.require_profile_extension", return_value=adapter):
                status, output, error = self.call(*arguments)
                self.assertEqual(0, status, error)
                result = json.loads(output)
                self.assertEqual("resolved", result["original_record_resolution"])
                self.assertEqual(2, len(result["resolution"]["records"]))
                self.assertTrue(result["page"]["truncated"])
            bad = SimpleNamespace(OBSERVATION_GRAPH_API_VERSION=1, resolve_evidence=lambda *a, **kw: {
                "state": "resolved", "snapshot_id": "different", "records": []})
            with patch("workbench_api.profile_extensions.require_profile_extension", return_value=bad):
                status, output, error = self.call(*arguments)
                self.assertEqual((2, ""), (status, output))
                self.assertIn("misbound", error)
            def mutate_references(path, references, **kwargs):
                references[0]["pointer"] = "/wrong"
                return {"state": "resolved", "snapshot_id": "one", "records": [
                    {"reference": row, "value": {}} for row in references]}
            bad.resolve_evidence = mutate_references
            with patch("workbench_api.profile_extensions.require_profile_extension", return_value=bad):
                status, output, error = self.call(*arguments)
                self.assertEqual((2, ""), (status, output))
                self.assertIn("misbound", error)
            status, output, error = self.call("evidence", str(root), nodes[0]["id"], "--snapshot", "/original")
            self.assertEqual((2, ""), (status, output))
            self.assertIn("both", error)

    def test_index_rebuild_preserves_original_streams(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = graph(root)
            original = (root / "observations/nodes.jsonl").read_bytes()
            (root / "query-index.sqlite3").unlink()
            status, output, error = self.call("index", str(root), "--max-source-bytes", "1000000",
                                             "--max-index-bytes", "1048576", "--json")
            self.assertEqual(0, status, error)
            self.assertEqual(manifest["graph_set_id"], json.loads(output)["graph_set_id"])
            self.assertEqual(original, (root / "observations/nodes.jsonl").read_bytes())

    def test_core_dispatch_supports_observations_without_shell(self):
        import workbench_registration_atlas
        from workbench_core.modules import InstalledModule, dispatch
        with patch.object(workbench_registration_atlas, "version", return_value="0.1.1"):
            module = workbench_registration_atlas.module()
        installed = InstalledModule("atlas", "workbench-atlas", "0.1.1", "available", module=module)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, dispatch(["atlas", "observations", "--help"], ExecutionContext(ROOT, ROOT / ".workbench"), [installed]))
        self.assertIn("import-snapshot", output.getvalue())
        self.assertIn("relationships", output.getvalue())

    def test_observation_route_coexists_with_legacy_and_disabled_shell(self):
        import workbench_registration_atlas
        from workbench_core.modules import discover, dispatch
        with patch.object(workbench_registration_atlas, "version", return_value="0.1.1"):
            atlas = workbench_registration_atlas.module()
        modules = [atlas, Module("crucible", "0.1.1"), Module("workbench-shell", "0.1.1", capabilities=(
            Capability("workbench-shell.atlas-recipes", ("atlas", "recipes"), "legacy_shell:recipes", "Legacy recipe composition"),))]
        entries = [SimpleNamespace(name=module.id, value="registration:module", load=lambda value=module: lambda: value,
                                  dist=SimpleNamespace(metadata={"Name": "workbench-" + module.id}, version="0.1.1", requires=()))
                   for module in modules]
        for disabled in ((), ("workbench-shell",)):
            with self.subTest(disabled=disabled):
                installed = discover(entries=entries, disabled=disabled)
                self.assertEqual("available", next(row for row in installed if row.id == "atlas").state)
                with patch("workbench_atlas_observations.cli.main", return_value=0) as observations:
                    self.assertEqual(0, dispatch(["atlas", "observations", "context", "/graph"], ExecutionContext(ROOT, ROOT / ".workbench"), installed))
                    self.assertEqual(["context", "/graph"], observations.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
