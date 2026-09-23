from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/atlas/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_atlas_categorical_graph import (  # noqa: E402
    AtlasCategoricalGraphError,
    CategoricalGraphBundleBuilder,
    edge_record,
    node_record,
)
from workbench_atlas_recipe_health import (  # noqa: E402
    discover_recipe_health_operational_context,
    rebuild_recipe_health_index,
)


class RecipeIndexOperationV1Tests(unittest.TestCase):
    def build_missing_index_graph(self, root: Path) -> tuple[dict, dict[str, bytes]]:
        recipe = node_record(
            "gt-recipe",
            "mixer|fixture|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "a" * 64,
                "lookup_active": True,
            },
        )
        output = node_record("forge-fluid", "fixture_output", {})
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={"profile": "recipe-index-fixture"},
            evidence_binding={"capture_id": "recipe-index-fixture"},
        )
        builder.add_partition(
            "recipe-index",
            classification="recipe index fixture",
            dependencies=(),
            nodes=(recipe, output),
            edges=(
                edge_record(
                    "produces-gt-fluid", recipe["id"], output["id"], {}
                ),
            ),
            evidence_categories=("fixture",),
        )
        manifest = builder.close()
        (root / "query-index.sqlite3").unlink()
        manifest["query_index"] = None
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        authority = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*.jsonl")
        }
        return manifest, authority

    def test_missing_index_context_denies_search_with_exact_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, _ = self.build_missing_index_graph(root)

            context = discover_recipe_health_operational_context(root)

        self.assertEqual(
            "workbench-atlas-recipe-health-operational-context-v1",
            context["format"],
        )
        self.assertEqual(manifest["graph_set_id"], context["graph_set_id"])
        self.assertTrue(context["evidence_capabilities"]["recipe_search"])
        self.assertFalse(context["capabilities"]["recipe_search"])
        self.assertFalse(context["search"]["executable"])
        self.assertEqual("missing", context["query_index"]["state"])
        self.assertEqual(
            "query-index-descriptor-absent",
            context["query_index"]["reason_code"],
        )
        self.assertTrue(context["query_index"]["repair_supported"])
        self.assertEqual("index", context["repair"]["action"])
        self.assertFalse(context["repair"]["authoritative_graph_evidence_mutated"])

    def test_bounded_rebuild_preserves_authority_and_makes_search_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, authority_before = self.build_missing_index_graph(root)
            progress: list[dict] = []

            operation = rebuild_recipe_health_index(
                root,
                max_source_bytes=1024 * 1024,
                max_index_bytes=8 * 1024 * 1024,
                progress=lambda row: progress.append(dict(row)),
            )
            context = discover_recipe_health_operational_context(root)
            authority_after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.jsonl")
            }

        self.assertEqual(authority_before, authority_after)
        self.assertEqual(manifest["graph_set_id"], operation["graph_set_id"])
        self.assertTrue(operation["custody"]["graph_identity_preserved"])
        self.assertFalse(
            operation["custody"]["authoritative_graph_evidence_mutated"]
        )
        self.assertEqual(
            "query_index descriptor only",
            operation["custody"]["manifest_change_scope"],
        )
        self.assertLessEqual(
            operation["custody"]["published_query_index"]["size"],
            operation["bounds"]["max_index_bytes"],
        )
        self.assertGreaterEqual(
            operation["bounds"]["free_bytes_before"],
            operation["bounds"]["required_free_bytes"],
        )
        self.assertEqual("published", operation["progress"]["terminal_phase"])
        self.assertEqual("published", progress[-1]["phase"])
        identity_payload = dict(operation)
        operation_id = identity_payload.pop("operation_id")
        expected_digest = hashlib.sha256(
            json.dumps(
                identity_payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            "workbench-atlas-recipe-index-operation:sha256:" + expected_digest,
            operation_id,
        )
        self.assertTrue(context["capabilities"]["recipe_search"])
        self.assertTrue(context["search"]["executable"])
        self.assertEqual("verified", context["query_index"]["state"])
        self.assertIsNone(context["repair"])

    def test_corrupt_derived_bytes_disable_search_but_remain_rebuildable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            recipe = node_record(
                "gt-recipe",
                "mixer|corrupt|0",
                {"recipe_map": "mixer", "semantic_sha256": "b" * 64},
            )
            builder = CategoricalGraphBundleBuilder(
                root,
                scope={"profile": "corrupt-index-fixture"},
                evidence_binding={"capture_id": "corrupt-index-fixture"},
            )
            builder.add_partition(
                "corrupt-index",
                classification="corrupt index fixture",
                dependencies=(),
                nodes=(recipe,),
                edges=(),
                evidence_categories=("fixture",),
            )
            builder.close()
            with (root / "query-index.sqlite3").open("ab") as stream:
                stream.write(b"not-admitted-derived-bytes")

            context = discover_recipe_health_operational_context(root)

        self.assertFalse(context["capabilities"]["recipe_search"])
        self.assertEqual("unusable", context["query_index"]["state"])
        self.assertEqual(
            "query-index-verification-failed",
            context["query_index"]["reason_code"],
        )
        self.assertIn("bytes differ", context["query_index"]["reason"])
        self.assertTrue(context["query_index"]["repair_supported"])
        self.assertEqual("index", context["repair"]["action"])

    def test_source_bound_failure_precedes_derived_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            manifest, authority_before = self.build_missing_index_graph(root)

            with self.assertRaisesRegex(
                AtlasCategoricalGraphError, "authoritative stream bytes exceed"
            ):
                rebuild_recipe_health_index(
                    root,
                    max_source_bytes=1,
                    max_index_bytes=8 * 1024 * 1024,
                )
            after = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            authority_after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*.jsonl")
            }

        self.assertEqual(manifest, after)
        self.assertEqual(authority_before, authority_after)
        self.assertFalse((root / "query-index.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
