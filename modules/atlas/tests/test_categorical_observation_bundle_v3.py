"""Versioned source authority without changing retained V2 graph identities."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from workbench_atlas_categorical_graph import (
    AtlasCategoricalGraphError, CategoricalGraphBundleBuilder, CategoricalGraphQuery,
    node_record, edge_record, rebuild_query_index, validate_bundle_directory,
    validate_bundle_manifest,
)


class ObservationBundleTests(unittest.TestCase):
    def build(self, root, *, authority="retained-observations-v1"):
        material = node_record("initialization-material", "fixture:iron", {"fingerprint": "a"})
        registry = node_record("initialization-registry", "fixture:materials", {})
        builder = CategoricalGraphBundleBuilder(root, scope={"stage": "initialization"},
            evidence_binding={"snapshot_id": "fixture"}, evidence_authority=authority)
        builder.add_partition("observations", classification="retained storage observations",
            dependencies=(), nodes=[registry, material],
            edges=[edge_record("observed-member", registry["id"], material["id"], {})],
            evidence_categories=("report",))
        return builder.close(), registry

    def test_v3_uses_distinct_authority_and_identity_with_same_exact_query_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "graph"
            manifest, registry = self.build(root)
            self.assertEqual(manifest["schema_version"], 3)
            self.assertTrue(manifest["graph_set_id"].startswith("workbench-atlas-graph-set-v3:"))
            self.assertNotIn("Crucible", manifest["authority"]["claim"])
            self.assertEqual(validate_bundle_directory(root), manifest)
            with CategoricalGraphQuery(root) as query:
                self.assertEqual(query.outgoing(registry["id"])[0]["relation"], "observed-member")

    def test_v2_default_retains_original_authority_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _ = self.build(Path(tmp) / "old", authority="crucible-occurrence-v1")
            new, _ = self.build(Path(tmp) / "new")
            self.assertEqual(old["schema_version"], 2)
            self.assertIn("Crucible", old["authority"]["claim"])
            self.assertTrue(old["graph_set_id"].startswith("workbench-atlas-graph-set-v2:"))
            self.assertNotEqual(old["graph_set_id"], new["graph_set_id"])
            self.assertEqual(old["partitions"], new["partitions"])

    def test_authority_cannot_be_swapped_or_legacy_validation_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, _ = self.build(Path(tmp) / "old", authority="crucible-occurrence-v1")
            new, _ = self.build(Path(tmp) / "new")
            for original, authority in [(old, new["authority"]), (new, old["authority"])]:
                invalid = deepcopy(original)
                invalid["authority"] = authority
                with self.assertRaisesRegex(AtlasCategoricalGraphError, "authority"):
                    validate_bundle_manifest(invalid)
            invalid = deepcopy(new)
            del invalid["validation_profile"]
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "dependency closure"):
                validate_bundle_manifest(invalid)

    def test_explicit_rebuild_retains_v3_graph_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "graph"
            manifest, _ = self.build(root)
            (root / "query-index.sqlite3").unlink()
            rebuild_query_index(root, max_source_bytes=1_000_000, max_index_bytes=1_000_000)
            rebuilt = json.loads((root / "manifest.json").read_text())
            self.assertEqual(rebuilt["graph_set_id"], manifest["graph_set_id"])
            self.assertEqual(rebuilt["authority"], manifest["authority"])

    def test_unknown_authority_refused_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "graph"
            with self.assertRaisesRegex(AtlasCategoricalGraphError, "authority"):
                self.build(root, authority="made-up")
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
