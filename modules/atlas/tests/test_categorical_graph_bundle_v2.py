from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/atlas/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_atlas_categorical_graph import (  # noqa: E402
    AtlasCategoricalGraphError,
    CategoricalGraphBundleBuilder,
    CategoricalGraphQuery,
    edge_record,
    node_record,
    rebuild_query_index,
    validate_bundle_directory,
    validate_bundle_manifest,
    verify_query_index,
)
from workbench_atlas_categorical_graph import bundle as bundle_module  # noqa: E402


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    (root / "manifest.json").write_bytes(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _reseal_manifest(manifest: dict[str, object]) -> None:
    partitions = manifest["partitions"]
    assert isinstance(partitions, list)
    for partition in partitions:
        assert isinstance(partition, dict)
        payload = dict(partition)
        payload.pop("partition_content_id", None)
        partition["partition_content_id"] = (
            "workbench-atlas-graph-partition-v2:sha256:"
            + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        )
    manifest["summary"] = {
        "partition_count": len(partitions),
        "node_count": sum(partition["nodes"]["count"] for partition in partitions),
        "edge_count": sum(partition["edges"]["count"] for partition in partitions),
    }
    payload = dict(manifest)
    payload.pop("graph_set_id", None)
    payload.pop("query_index", None)
    graph_set_id = (
        "workbench-atlas-graph-set-v2:sha256:"
        + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    )
    manifest["graph_set_id"] = graph_set_id
    query_index = manifest.get("query_index")
    if isinstance(query_index, dict):
        query_index["derived_from_graph_set_id"] = graph_set_id


def _refresh_stream_descriptor(
    root: Path, manifest: dict[str, object], partition_index: int, family: str
) -> None:
    partition = manifest["partitions"][partition_index]
    descriptor = partition[family]
    data = (root / descriptor["file"]).read_bytes()
    rows = [json.loads(line) for line in data.splitlines()]
    count_key = "kinds" if family == "nodes" else "relations"
    row_key = "kind" if family == "nodes" else "relation"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[row_key]] = counts.get(row[row_key], 0) + 1
    descriptor.update(
        {
            "count": len(rows),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            count_key: dict(sorted(counts.items())),
        }
    )
    manifest["query_index"] = None
    _reseal_manifest(manifest)
    _write_manifest(root, manifest)


def _refresh_index_descriptor(root: Path, manifest: dict[str, object]) -> None:
    path = root / "query-index.sqlite3"
    data = path.read_bytes()
    manifest["query_index"].update(
        {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    )
    _write_manifest(root, manifest)


class CategoricalGraphBundleV2Tests(unittest.TestCase):
    def test_sql_verification_preserves_cancellation_and_resets_progress_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            builder = CategoricalGraphBundleBuilder(root, scope={}, evidence_binding={})
            builder.add_partition("many", classification="fixture", dependencies=(),
                nodes=(node_record("fixture", str(index), {}) for index in range(2000)),
                edges=[], evidence_categories=("fixture",))
            manifest = builder.close()
            connection = bundle_module._open_immutable_index(root / "query-index.sqlite3")
            failure = RuntimeError("cancelled SQLite verification")
            calls = 0
            def cancel():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise failure
            try:
                with mock.patch.object(bundle_module, "_verify_index_semantics", wraps=bundle_module._verify_index_semantics) as semantics:
                    with self.assertRaises(RuntimeError) as raised:
                        bundle_module._verify_index_connection(connection, manifest, check_cancelled=cancel)
                    self.assertIs(failure, raised.exception)
                    # Cancellation happened inside SQL, before Python row verification.
                    semantics.assert_not_called()
                    self.assertEqual(2, calls)
                self.assertEqual(2000, connection.execute("SELECT sum(1) FROM nodes").fetchone()[0])
                self.assertEqual(2, calls)
                bundle_module._verify_index_connection(connection, manifest)
            finally:
                connection.close()

    def build_fixture(self, root: Path) -> tuple[dict[str, object], dict, dict]:
        material = node_record("material", "example:iron", {"solid": True})
        property_node = node_record("material-property", "dust", {})
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={"profile": "fixture"},
            evidence_binding={"capture_id": "capture-fixture"},
        )
        builder.add_partition(
            "material-core",
            classification="material identity",
            dependencies=(),
            nodes=[material],
            edges=[],
            evidence_categories=("material-core",),
        )
        builder.add_partition(
            "material-capabilities",
            classification="material properties",
            dependencies=("material-core",),
            nodes=[property_node],
            edges=[
                edge_record(
                    "has-property",
                    material["id"],
                    property_node["id"],
                    {"runtime_class": "fixture.Dust"},
                )
            ],
            evidence_categories=("material-core",),
        )
        return builder.close(), material, property_node

    def test_partition_dependencies_are_queryable_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, material, property_node = self.build_fixture(root)
            self.assertEqual(
                manifest["graph_set_id"],
                validate_bundle_directory(root)["graph_set_id"],
            )
            self.assertEqual(
                manifest["graph_set_id"], verify_query_index(root)["graph_set_id"]
            )
            self.assertEqual(2, manifest["summary"]["partition_count"])
            self.assertEqual(2, manifest["summary"]["node_count"])
            self.assertEqual(1, manifest["summary"]["edge_count"])
            with CategoricalGraphQuery(root) as query:
                found = query.node("material", "example:iron")
                self.assertEqual(material["id"], found["id"])
                outgoing = query.outgoing(material["id"], "has-property")
                self.assertEqual(property_node["id"], outgoing[0]["target"])

    def test_edge_outside_dependency_closure_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            material = node_record("material", "example:iron", {})
            missing = node_record("material-property", "dust", {})
            builder = CategoricalGraphBundleBuilder(
                root,
                scope={"profile": "fixture"},
                evidence_binding={"capture_id": "capture-fixture"},
            )
            with self.assertRaisesRegex(
                AtlasCategoricalGraphError, "outside dependency closure"
            ):
                builder.add_partition(
                    "material-core",
                    classification="material identity",
                    dependencies=(),
                    nodes=[material],
                    edges=[
                        edge_record(
                            "has-property", material["id"], missing["id"], {}
                        )
                    ],
                    evidence_categories=("material-core",),
                )

    def test_earlier_but_undeclared_partition_is_not_in_strict_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            first = node_record("kind", "first", {})
            unrelated = node_record("kind", "unrelated", {})
            local = node_record("kind", "local", {})
            builder = CategoricalGraphBundleBuilder(
                root, scope={}, evidence_binding={}
            )
            builder.add_partition(
                "first",
                classification="first",
                dependencies=(),
                nodes=[first],
                edges=(),
                evidence_categories=("fixture",),
            )
            builder.add_partition(
                "unrelated",
                classification="unrelated",
                dependencies=(),
                nodes=[unrelated],
                edges=(),
                evidence_categories=("fixture",),
            )
            with self.assertRaisesRegex(
                AtlasCategoricalGraphError, "outside dependency closure"
            ):
                builder.add_partition(
                    "third",
                    classification="third",
                    dependencies=("first",),
                    nodes=[local],
                    edges=[edge_record("bad", local["id"], unrelated["id"], {})],
                    evidence_categories=("fixture",),
                )

    def test_authoritative_validation_ignores_missing_disposable_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            index = root / "query-index.sqlite3"
            index.unlink()
            self.assertEqual(
                manifest["graph_set_id"],
                validate_bundle_directory(root)["graph_set_id"],
            )
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "missing"):
                verify_query_index(root)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "missing"):
                CategoricalGraphQuery(root)
            self.assertFalse(index.exists(), "read-only query must not create the index")

    def test_query_rejects_missing_authoritative_node_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            node_stream = root / manifest["partitions"][0]["nodes"]["file"]
            node_stream.unlink()
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "nodes is missing"):
                verify_query_index(root)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "nodes is missing"):
                CategoricalGraphQuery(root)

    def test_explicit_missing_index_rebuild_restores_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, material, _ = self.build_fixture(root)
            (root / "query-index.sqlite3").unlink()
            with CategoricalGraphQuery(root, rebuild_if_missing=True) as query:
                self.assertEqual(
                    material["id"], query.node("material", "example:iron")["id"]
                )
            self.assertEqual(
                manifest["graph_set_id"], verify_query_index(root)["graph_set_id"]
            )

    def test_rebuild_is_byte_deterministic_and_preserves_graph_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            first = rebuild_query_index(root)
            second = rebuild_query_index(root)
            self.assertEqual(manifest["graph_set_id"], first["graph_set_id"])
            self.assertEqual(first["graph_set_id"], second["graph_set_id"])
            self.assertEqual(
                first["query_index"]["sha256"], second["query_index"]["sha256"]
            )
            self.assertEqual(first["query_index"]["size"], second["query_index"]["size"])

    def test_tampered_index_with_same_graph_metadata_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            self.build_fixture(root)
            index = root / "query-index.sqlite3"
            connection = sqlite3.connect(index)
            connection.execute(
                "UPDATE nodes SET properties_json='{}' WHERE semantic_key='example:iron'"
            )
            connection.commit()
            connection.close()
            # The authoritative streams remain valid and the graph metadata/counts
            # inside SQLite were deliberately left unchanged.
            validate_bundle_directory(root)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "bytes differ"):
                verify_query_index(root)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "bytes differ"):
                CategoricalGraphQuery(root)

    def test_forged_index_with_updated_descriptor_cannot_answer_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            connection = sqlite3.connect(root / "query-index.sqlite3")
            connection.execute(
                "UPDATE nodes SET properties_json='{}' WHERE semantic_key='example:iron'"
            )
            connection.commit()
            connection.close()
            # The cache descriptor is disposable and is deliberately resealed to
            # the forged bytes. Only equivalence to authoritative JSONL can catch it.
            _refresh_index_descriptor(root, manifest)
            validate_bundle_directory(root)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "semantic.*differs"):
                verify_query_index(root)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "semantic.*differs"):
                CategoricalGraphQuery(root)

    def test_rebuild_remeasures_equal_count_stream_used_for_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            stream = root / manifest["partitions"][0]["nodes"]["file"]
            authoritative_stream = stream.read_bytes()
            original_index = (root / "query-index.sqlite3").read_bytes()
            original_manifest = (root / "manifest.json").read_bytes()
            substituted_row = json.loads(authoritative_stream)
            substituted_row["properties"]["solid"] = None
            substituted_stream = _canonical_bytes(substituted_row) + b"\n"
            self.assertEqual(len(authoritative_stream), len(substituted_stream))

            real_builder = bundle_module._build_index_from_streams

            def substitute_before_build(
                build_root: Path, build_manifest: dict[str, object], staged: Path
            ) -> None:
                stream.write_bytes(substituted_stream)
                real_builder(build_root, build_manifest, staged)

            with mock.patch.object(
                bundle_module,
                "_build_index_from_streams",
                side_effect=substitute_before_build,
            ):
                with self.assertRaisesRegex(
                    AtlasCategoricalGraphError, "nodes used for index rebuild stream differs"
                ):
                    rebuild_query_index(root)

            self.assertEqual(original_index, (root / "query-index.sqlite3").read_bytes())
            self.assertEqual(original_manifest, (root / "manifest.json").read_bytes())
            stream.write_bytes(authoritative_stream)
            verify_query_index(root)

    def test_index_with_matching_descriptor_but_extra_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            connection = sqlite3.connect(root / "query-index.sqlite3")
            connection.execute("CREATE TABLE rogue(value TEXT)")
            connection.commit()
            connection.close()
            _refresh_index_descriptor(root, manifest)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "schema objects"):
                verify_query_index(root)

    def test_safe_paths_and_symlinked_streams_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "safe relative"):
                CategoricalGraphBundleBuilder(
                    base / "graph", scope={}, evidence_binding={}
                ).add_partition(
                    "../escape",
                    classification="escape",
                    dependencies=(),
                    nodes=(),
                    edges=(),
                    evidence_categories=("fixture",),
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            manifest["partitions"][0]["nodes"]["file"] = "../escape.jsonl"
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "safe relative"):
                validate_bundle_manifest(manifest)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            self.build_fixture(root)
            nodes = root / "material-core/nodes.jsonl"
            outside = Path(temporary) / "outside.jsonl"
            nodes.replace(outside)
            os.symlink(outside, nodes)
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "symlink"):
                validate_bundle_directory(root)

    def test_exact_manifest_descriptor_and_partition_content_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            manifest["partitions"][0]["nodes"]["unexpected"] = True
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "descriptor fields"):
                validate_bundle_manifest(manifest)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            manifest["partitions"][0]["classification"] = "tampered"
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "content identity"):
                validate_bundle_manifest(manifest)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            manifest["query_index"]["unexpected"] = True
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "index fields"):
                validate_bundle_manifest(manifest)

    def test_stream_rows_require_exact_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            path = root / manifest["partitions"][0]["nodes"]["file"]
            row = json.loads(path.read_text().splitlines()[0])
            row["unexpected"] = True
            path.write_bytes(_canonical_bytes(row) + b"\n")
            _refresh_stream_descriptor(root, manifest, 0, "nodes")
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "node fields"):
                validate_bundle_directory(root)

    def test_duplicate_node_and_edge_identities_are_rejected(self) -> None:
        for family, partition_index, message in (
            ("nodes", 0, "node identity is duplicated"),
            ("edges", 1, "edge identity is duplicated"),
        ):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "graph"
                manifest, _, _ = self.build_fixture(root)
                path = root / manifest["partitions"][partition_index][family]["file"]
                row = path.read_bytes().splitlines(keepends=True)[0]
                with path.open("ab") as stream:
                    stream.write(row)
                _refresh_stream_descriptor(root, manifest, partition_index, family)
                with self.assertRaisesRegex(AtlasCategoricalGraphError, message):
                    validate_bundle_directory(root)

    def test_noncanonical_jsonl_is_rejected_even_when_descriptor_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            path = root / manifest["partitions"][0]["nodes"]["file"]
            row = json.loads(path.read_text().splitlines()[0])
            path.write_bytes(json.dumps(row, sort_keys=True).encode("utf-8") + b"\n")
            _refresh_stream_descriptor(root, manifest, 0, "nodes")
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "not canonical JSONL"):
                validate_bundle_directory(root)

    def test_directory_validation_enforces_strict_declared_endpoint_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            manifest["partitions"][1]["dependencies"] = []
            manifest["query_index"] = None
            _reseal_manifest(manifest)
            _write_manifest(root, manifest)
            validate_bundle_manifest(manifest)
            with self.assertRaisesRegex(
                AtlasCategoricalGraphError, "outside dependency closure"
            ):
                validate_bundle_directory(root)

    def test_legacy_manifest_identity_has_explicit_compatibility_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _, _ = self.build_fixture(root)
            manifest.pop("validation_profile")
            manifest["partitions"][1]["dependencies"] = []
            manifest["query_index"] = None
            _reseal_manifest(manifest)
            _write_manifest(root, manifest)
            # Pre-hardening V2 identities keep their historical earlier-partition
            # closure. New builder output always carries the strict profile.
            self.assertNotIn("validation_profile", validate_bundle_directory(root))


if __name__ == "__main__":
    unittest.main()
