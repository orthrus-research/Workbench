"""A reused observation view never publishes data after its files change."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for source in (ROOT / "api/src", ROOT / "modules/atlas/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_atlas_categorical_graph import (
    AtlasCategoricalGraphError, CategoricalGraphBundleBuilder, CategoricalGraphQuery,
    edge_record, node_record,
)
from workbench_atlas_categorical_graph import bundle
from workbench_atlas_observations import ObservationError, open_observations
from workbench_atlas_observations import view as observation_view
from workbench_atlas_observations.view import ObservationChangedError


def build_graph(root):
    references = [{"snapshot_id": "snapshot:original", "section": "report",
                   "record_key": "value", "json_pointer": "/records/0"}]
    nodes = [node_record("stored-observation", str(index),
                         {"label": "Observed value", "count": 1}, references)
             for index in range(3)]
    edges = [edge_record("stored-reference", nodes[0]["id"], target["id"],
                         {"ordinal": index}, references)
             for index, target in enumerate(nodes[1:])]
    builder = CategoricalGraphBundleBuilder(root, scope={"snapshot": "original"},
        evidence_binding={"coverage": "complete", "native_outcome": "native-failed"},
        evidence_authority="retained-observations-v1")
    builder.add_partition("observations", classification="Stored evidence", dependencies=(),
        nodes=nodes, edges=edges, evidence_categories=("report",),
        limitations=("Stored values do not establish executed behavior.",))
    return builder.close(), nodes


def rewrite_preserving_mtime(path, data=None):
    before = path.stat()
    path.write_bytes(path.read_bytes() if data is None else data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    return before


class ObservationViewLifetimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.container = self.base / "container"
        self.root = self.container / "graph"
        self.manifest, self.nodes = build_graph(self.root)
        self.selection = self.nodes[0]["id"]

    def opened(self, root=None, **kwargs):
        view = open_observations(root or self.root, **kwargs)
        self.addCleanup(view.close)
        return view

    def assert_connection_closed(self, connection):
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
            connection.execute("SELECT 1")

    def assert_terminal(self, view):
        self.assert_connection_closed(view.query.connection)
        for operation in (view.describe, lambda: view.search("Observed"),
                          lambda: view.inspect(self.selection),
                          lambda: view.relationships(self.selection),
                          lambda: view.evidence(self.selection)):
            with self.assertRaises(ObservationChangedError):
                operation()

    def test_context_manifest_and_selected_values_are_isolated_from_callers(self):
        view = self.opened()
        expected_context = view.describe()
        expected_manifest = view.manifest
        response = view.search("Observed", limit=1)
        response["context"]["scope"]["snapshot"] = "invented"
        response["context"]["evidence_binding"]["native_outcome"] = "completed"
        response["context"]["summary"]["node_count"] = 0
        response["context"]["coverage"]["partitions"][0]["limitations"].clear()
        response["results"][0]["properties"]["count"] = 99
        manifest = view.manifest
        manifest["scope"]["snapshot"] = "changed directly"
        self.assertEqual(expected_context, view.describe())
        self.assertEqual(expected_manifest, view.manifest)
        self.assertEqual(self.nodes[0], view.inspect(self.selection)["selection"])
        self.assertEqual("native-failed", view.describe()["evidence_binding"]["native_outcome"])

    def test_close_is_idempotent_and_all_query_methods_are_terminal(self):
        view = self.opened()
        with patch.object(view.query, "close", wraps=view.query.close) as close:
            view.close()
            view.close()
            self.assertEqual(1, close.call_count)
        for operation in (view.describe, lambda: view.search("Observed"),
                          lambda: view.inspect(self.selection),
                          lambda: view.relationships(self.selection),
                          lambda: view.evidence(self.selection)):
            with self.assertRaisesRegex(ObservationError, "closed"):
                operation()
        self.assert_connection_closed(view.query.connection)

    def test_sibling_creation_and_directory_timestamp_changes_do_not_invalidate(self):
        view = self.opened()
        expected = view.inspect(self.selection)
        (self.root / "unrelated.txt").write_text("unrelated artifact")
        (self.container / "sibling").mkdir()
        (self.base / "unrelated-parent-file").write_text("sibling activity")
        for directory in (self.root, self.container, self.base):
            stamp = directory.stat()
            os.utime(directory, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
        self.assertEqual(expected, view.inspect(self.selection))

    def test_same_size_rewrite_with_restored_mtime_invalidates_every_bound_file(self):
        for family in ("manifest", "nodes", "edges", "index"):
            with self.subTest(family=family):
                root = self.base / ("rewrite-" + family)
                manifest, _ = build_graph(root)
                relative = {"manifest": "manifest.json", "index": "query-index.sqlite3",
                            "nodes": manifest["partitions"][0]["nodes"]["file"],
                            "edges": manifest["partitions"][0]["edges"]["file"]}[family]
                path = root / relative
                view = self.opened(root)
                before = rewrite_preserving_mtime(path)
                self.assertEqual(before.st_size, path.stat().st_size)
                self.assertEqual(before.st_mtime_ns, path.stat().st_mtime_ns)
                self.assertNotEqual(before.st_ctime_ns, path.stat().st_ctime_ns)
                with self.assertRaises(ObservationChangedError):
                    view.inspect(self.selection)
                self.assert_terminal(view)
                # Identical restored bytes remain admissible through a fresh open.
                with open_observations(root) as reopened:
                    self.assertEqual(self.nodes[0], reopened.inspect(self.selection)["selection"])

    def test_restoring_source_bytes_cannot_revive_an_invalidated_view(self):
        view = self.opened()
        path = self.root / self.manifest["partitions"][0]["nodes"]["file"]
        original = path.read_bytes()
        altered = original.replace(b'"count":1', b'"count":2', 1)
        self.assertNotEqual(original, altered)
        self.assertEqual(len(original), len(altered))
        before = rewrite_preserving_mtime(path, altered)
        with self.assertRaises(ObservationChangedError):
            view.search("Observed")
        path.write_bytes(original)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assert_terminal(view)

    def test_deletion_and_atomic_equal_byte_index_replacement_are_terminal(self):
        for change in ("delete-manifest", "delete-index", "replace-index"):
            with self.subTest(change=change):
                root = self.base / change
                build_graph(root)
                view = self.opened(root)
                index = root / "query-index.sqlite3"
                if change == "delete-manifest":
                    (root / "manifest.json").unlink()
                elif change == "delete-index":
                    index.unlink()
                else:
                    candidate = root / "replacement.sqlite3"
                    candidate.write_bytes(index.read_bytes())
                    os.replace(candidate, index)
                with self.assertRaises(ObservationChangedError):
                    view.describe()
                self.assert_terminal(view)

    def test_symlink_ancestor_is_refused_before_cold_verification(self):
        alias = self.base / "alias"
        alias.symlink_to(self.container, target_is_directory=True)
        with patch.object(observation_view, "CategoricalGraphQuery") as cold_open:
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "symlink"):
                open_observations(alias / "graph")
            cold_open.assert_not_called()

    def test_ancestor_replacement_with_symlink_to_same_files_invalidates(self):
        view = self.opened()
        relocated = self.base / "relocated"
        self.container.rename(relocated)
        self.container.symlink_to(relocated, target_is_directory=True)
        self.assertEqual((relocated / "graph/query-index.sqlite3").stat().st_ino,
                         (self.root / "query-index.sqlite3").stat().st_ino)
        with self.assertRaises(ObservationChangedError):
            view.evidence(self.selection)
        self.assert_terminal(view)

    def test_drift_around_index_verification_never_returns_a_view_and_closes_once(self):
        for timing in ("before-index-verification", "after-index-verification"):
            with self.subTest(timing=timing):
                root = self.base / timing
                manifest, _ = build_graph(root)
                stream = root / manifest["partitions"][0]["nodes"]["file"]
                real_verify, real_open = bundle._verified_index_connection, bundle._open_immutable_index
                real_close = CategoricalGraphQuery.close
                connections = []
                def observe_open(path):
                    connection = real_open(path)
                    connections.append(connection)
                    return connection
                def changed_verify(*args, **kwargs):
                    if timing == "before-index-verification":
                        rewrite_preserving_mtime(stream)
                    connection = real_verify(*args, **kwargs)
                    if timing == "after-index-verification":
                        rewrite_preserving_mtime(stream)
                    return connection
                with patch.object(bundle, "_open_immutable_index", side_effect=observe_open), \
                     patch.object(bundle, "_verified_index_connection", side_effect=changed_verify), \
                     patch.object(CategoricalGraphQuery, "close", autospec=True, side_effect=real_close) as close:
                    with self.assertRaises(ObservationChangedError):
                        open_observations(root)
                    self.assertEqual(1, close.call_count)
                self.assertEqual(1, len(connections))
                self.assert_connection_closed(connections[0])

    def test_index_stat_failure_after_sqlite_open_closes_the_connection(self):
        connections = []
        real_open = bundle._open_immutable_index
        def disappear_after_open(path):
            connection = real_open(path)
            connections.append(connection)
            path.unlink()
            return connection
        with patch.object(bundle, "_open_immutable_index", side_effect=disappear_after_open):
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "cannot be inspected"):
                bundle._verified_index_connection(self.root, self.manifest)
        self.assertEqual(1, len(connections))
        self.assert_connection_closed(connections[0])

    def test_index_rewrite_after_semantic_verification_is_detected_and_closed(self):
        connections = []
        real_verify = bundle._verify_index_connection
        def change_after_semantics(connection, manifest, **kwargs):
            connections.append(connection)
            real_verify(connection, manifest, **kwargs)
            rewrite_preserving_mtime(self.root / "query-index.sqlite3")
        with patch.object(bundle, "_verify_index_connection", side_effect=change_after_semantics):
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "changed during verification"):
                bundle._verified_index_connection(self.root, self.manifest)
        self.assertEqual(1, len(connections))
        self.assert_connection_closed(connections[0])

    def test_source_drift_during_sql_cannot_publish_rows(self):
        view = self.opened()
        stream = self.root / self.manifest["partitions"][0]["nodes"]["file"]
        changed = []
        def casefold(value):
            if not changed:
                rewrite_preserving_mtime(stream)
                changed.append(True)
            return value.casefold()
        view.query.connection.create_function("atlas_casefold", 1, casefold)
        with self.assertRaises(ObservationChangedError):
            view.search("Observed")
        self.assertEqual([True], changed)
        self.assert_terminal(view)

    def test_index_replacement_during_sql_cannot_publish_rows(self):
        view = self.opened()
        index = self.root / "query-index.sqlite3"
        replacement = self.root / "replacement.sqlite3"
        replacement.write_bytes(index.read_bytes())
        changed = []
        def casefold(value):
            if not changed:
                os.replace(replacement, index)
                changed.append(True)
            return value.casefold()
        view.query.connection.create_function("atlas_casefold", 1, casefold)
        with self.assertRaises(ObservationChangedError):
            view.search("Observed")
        self.assertEqual([True], changed)
        self.assert_terminal(view)

    def test_drift_during_context_copy_does_not_publish_describe_or_inspect(self):
        for operation in ("describe", "inspect"):
            with self.subTest(operation=operation):
                root = self.base / ("context-" + operation)
                build_graph(root)
                view = self.opened(root)
                real_context = observation_view._context
                changed = []
                def change_after_copy(*args):
                    result = real_context(*args)
                    rewrite_preserving_mtime(root / "manifest.json")
                    changed.append(True)
                    return result
                with patch.object(observation_view, "_context", side_effect=change_after_copy):
                    with self.assertRaises(ObservationChangedError):
                        view.describe() if operation == "describe" else view.inspect(self.selection)
                self.assertEqual([True], changed)
                self.assert_terminal(view)

    def test_cancelled_sql_preserves_exception_and_can_recover_without_reopening(self):
        failure = RuntimeError("cancelled lifetime query")
        state = {"cancelled": False}
        def cancel():
            if state["cancelled"]:
                raise failure
        view = self.opened(check_cancelled=cancel)
        def casefold(value):
            state["cancelled"] = True
            return value.casefold()
        view.query.connection.create_function("atlas_casefold", 1, casefold)
        with self.assertRaises(RuntimeError) as raised:
            view.search("Observed")
        self.assertIs(failure, raised.exception)
        state["cancelled"] = False
        view.query.connection.create_function("atlas_casefold", 1, str.casefold)
        self.assertEqual(3, len(view.search("Observed")["results"]))
        self.assertEqual((1,), view.query.connection.execute("SELECT 1").fetchone())
        self.assertEqual(self.nodes[0], view.inspect(self.selection)["selection"])


if __name__ == "__main__":
    unittest.main()
