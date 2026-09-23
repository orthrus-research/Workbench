from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/atlas/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_atlas_categorical_graph import (  # noqa: E402
    CategoricalGraphBundleBuilder,
    edge_record,
    node_record,
)
from workbench_atlas_recipe_health import (  # noqa: E402
    RecipeHealthError,
    discover_recipe_health_context,
    open_recipe_health,
)


class RecipeHealthViewV1Tests(unittest.TestCase):
    def build_graph(self, root: Path) -> dict[str, dict]:
        recipe_map = node_record("gt-recipe-map", "batch_reactor", {})
        origin = node_record("gt-recipe-origin-class", "groovy", {})
        radon = node_record("forge-fluid", "radon", {"name": "radon"})
        waste = node_record(
            "forge-fluid", "radon_waste", {"name": "radon_waste"}
        )
        source = node_record(
            "groovy-source-resource",
            "groovy/postInit/Chemistry.groovy",
            {"source_path": "groovy/postInit/Chemistry.groovy"},
        )
        callsite = node_record(
            "groovy-source-callsite",
            "groovy/postInit/Chemistry.groovy|line:17",
            {"source_path": "groovy/postInit/Chemistry.groovy", "line": 17},
        )
        event = node_record(
            "groovy-mutation-event",
            "capture|mutation:1",
            {
                "operation": "gt-recipe-add",
                "execution_source_path": "groovy/postInit/Chemistry.groovy",
            },
            [{"adapter_id": "mutations", "record_ordinal": 1}],
        )
        subject = node_record(
            "groovy-mutation-subject",
            "capture|mutation:1|subject:0",
            {"subject_kind": "gt-recipe", "ordinal": 0},
        )
        selector_a = node_record(
            "gt-recipe-input-selector",
            "batch_reactor|same-signature|0|fluid|0",
            {"amount": 1000, "ordinal": 0, "non_consumable": False},
        )
        selector_b = node_record(
            "gt-recipe-input-selector",
            "batch_reactor|same-signature|1|fluid|0",
            {"amount": 1000, "ordinal": 0, "non_consumable": False},
        )
        recipe_a = node_record(
            "gt-recipe",
            "batch_reactor|same-signature|0",
            {
                "recipe_map": "batch_reactor",
                "semantic_sha256": "same-signature",
                "duplicate_ordinal": 0,
                "duration": 40,
                "eut": 30,
            },
        )
        recipe_b = node_record(
            "gt-recipe",
            "batch_reactor|same-signature|1",
            {
                "recipe_map": "batch_reactor",
                "semantic_sha256": "same-signature",
                "duplicate_ordinal": 1,
                "duration": 40,
                "eut": 30,
            },
        )
        other_map_recipe = node_record(
            "gt-recipe",
            "centrifuge|same-signature|0",
            {
                "recipe_map": "centrifuge",
                "semantic_sha256": "same-signature",
                "duplicate_ordinal": 0,
            },
        )
        nodes = [
            recipe_map,
            origin,
            radon,
            waste,
            source,
            callsite,
            event,
            subject,
            selector_a,
            selector_b,
            recipe_a,
            recipe_b,
            other_map_recipe,
        ]
        edges = [
            edge_record("contained-in-recipe-map", recipe_a["id"], recipe_map["id"], {}),
            edge_record("contained-in-recipe-map", recipe_b["id"], recipe_map["id"], {}),
            edge_record("has-recipe-origin-class", recipe_a["id"], origin["id"], {}),
            edge_record("has-recipe-origin-class", recipe_b["id"], origin["id"], {}),
            edge_record(
                "has-fluid-input-selector", recipe_a["id"], selector_a["id"], {"ordinal": 0}
            ),
            edge_record(
                "has-fluid-input-selector", recipe_b["id"], selector_b["id"], {"ordinal": 0}
            ),
            edge_record(
                "accepts-gt-fluid-input", selector_a["id"], radon["id"], {"amount": 1000}
            ),
            edge_record(
                "accepts-gt-fluid-input", selector_b["id"], radon["id"], {"amount": 1000}
            ),
            edge_record(
                "produces-gt-fluid", recipe_a["id"], waste["id"], {"amount": 1000}
            ),
            edge_record("has-mutation-subject", event["id"], subject["id"], {}),
            edge_record(
                "identifies-surviving-recipe-by-object-identity",
                subject["id"],
                recipe_a["id"],
                {},
            ),
            edge_record(
                "causally-attributed-to-groovy-source", event["id"], source["id"], {}
            ),
            edge_record("invoked-through-source-callsite", event["id"], callsite["id"], {}),
            edge_record("callsite-defined-in-source", callsite["id"], source["id"], {}),
        ]
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={"profile": "recipe-health-fixture"},
            evidence_binding={"capture_id": "capture"},
        )
        builder.add_partition(
            "recipe-health",
            classification="recipe health fixture",
            dependencies=(),
            nodes=nodes,
            edges=edges,
            evidence_categories=("fixture",),
        )
        builder.close()
        return {
            "recipe_a": recipe_a,
            "recipe_b": recipe_b,
            "other_map_recipe": other_map_recipe,
            "radon": radon,
            "waste": waste,
        }

    def test_graph_search_selects_target_without_known_semantic_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            fixture = self.build_graph(graph)
            discovered = discover_recipe_health_context(graph / "manifest.json")
            self.assertEqual("categorical-graph-v2", discovered["context_type"])
            self.assertEqual(3, discovered["recipe_count"])
            self.assertFalse(discovered["capabilities"]["reachability"])

            with open_recipe_health(graph) as view:
                search = view.search("radon")
                ids = {row["selection_id"] for row in search["results"]}
                self.assertIn(fixture["radon"]["id"], ids)
                self.assertIn(fixture["waste"]["id"], ids)

                report = view.inspect(fixture["radon"]["id"])
                self.assertEqual("target", report["role"])
                self.assertEqual([], report["flow"]["producers"])
                self.assertEqual(2, len(report["flow"]["consumers"]))
                self.assertEqual(
                    {fixture["recipe_a"]["id"], fixture["recipe_b"]["id"]},
                    {
                        row["recipe"]["selection_id"]
                        for row in report["flow"]["consumers"]
                    },
                )
                gap_codes = {row["code"] for row in report["evidence_gaps"]}
                self.assertIn("stoichiometry-not-assessed", gap_codes)
                self.assertIn("reachability-not-assessed", gap_codes)

    def test_recipe_report_has_exact_collision_flow_and_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            fixture = self.build_graph(graph)
            with open_recipe_health(graph) as view:
                report = view.inspect(fixture["recipe_a"]["id"])
                duplicate = report["duplicate_signature"]
                self.assertEqual("collision", duplicate["status"])
                self.assertEqual(2, duplicate["exact_match_count"])
                self.assertNotIn(
                    fixture["other_map_recipe"]["id"],
                    {row["selection_id"] for row in duplicate["recipes"]},
                )
                self.assertEqual(1, len(report["inputs"]))
                self.assertEqual(fixture["radon"]["id"], report["inputs"][0]["node"]["selection_id"])
                self.assertEqual(1, len(report["outputs"]))
                self.assertEqual(fixture["waste"]["id"], report["outputs"][0]["node"]["selection_id"])
                self.assertEqual("observed-source-mutation", report["ownership"]["status"])
                self.assertEqual(
                    ["groovy/postInit/Chemistry.groovy"],
                    report["ownership"]["source_paths"],
                )
                self.assertEqual(1, len(report["ownership"]["mutations"]))

                origin_only = view.inspect(fixture["recipe_b"]["id"])
                self.assertEqual("runtime-origin-class-only", origin_only["ownership"]["status"])
                self.assertIn(
                    "source-mutation-evidence-unavailable",
                    {row["code"] for row in origin_only["evidence_gaps"]},
                )

    def test_source_only_search_is_explicit_about_runtime_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "checkout"
            source = checkout / "groovy/postInit/Chemistry.groovy"
            source.parent.mkdir(parents=True)
            source.write_text(
                "mods.gregtech.batch_reactor.recipeBuilder()\n"
                "    .fluidInputs(fluid('radon') * 1000)\n",
                encoding="utf-8",
            )
            discovered = discover_recipe_health_context(checkout)
            self.assertEqual("source-only-checkout", discovered["context_type"])
            self.assertFalse(discovered["capabilities"]["duplicate_signatures"])

            with open_recipe_health(checkout) as view:
                search = view.search("radon")
                self.assertEqual(1, len(search["results"]))
                report = view.inspect(search["results"][0]["selection_id"])
                self.assertEqual("source-occurrence", report["role"])
                self.assertEqual(
                    "groovy/postInit/Chemistry.groovy",
                    report["selection"]["source_path"],
                )
                self.assertEqual("source-location-only", report["ownership"]["status"])
                stale = search["results"][0]["selection_id"]
                source.write_text(source.read_text().replace("radon", "argon"), encoding="utf-8")
                with self.assertRaisesRegex(RecipeHealthError, "stale"):
                    view.inspect(stale)
                with self.assertRaisesRegex(RecipeHealthError, "format"):
                    view.inspect("source-occurrence:groovy%2FpostInit%2FChemistry.groovy:2:25")
                gap_codes = {row["code"] for row in report["evidence_gaps"]}
                self.assertIn("runtime-recipes-unavailable", gap_codes)
                self.assertIn("duplicate-signatures-unavailable", gap_codes)
                self.assertIn("producer-consumer-evidence-unavailable", gap_codes)

    def test_bad_context_and_nonexistent_selection_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty"
            empty.mkdir()
            with self.assertRaisesRegex(RecipeHealthError, "no Groovy"):
                open_recipe_health(empty)

            graph = Path(temporary) / "graph"
            self.build_graph(graph)
            with open_recipe_health(graph) as view:
                with self.assertRaisesRegex(RecipeHealthError, "does not exist"):
                    view.inspect("workbench-atlas-node-v2:gt-recipe:missing")

    def test_source_search_does_not_follow_profile_symlink_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            safe = checkout / "scripts/Safe.zs"
            safe.parent.mkdir(parents=True)
            safe.write_text("safe_recipe_marker\n", encoding="utf-8")
            external = root / "external"
            external.mkdir()
            (external / "Private.groovy").write_text(
                "external_recipe_marker\n", encoding="utf-8"
            )
            try:
                os.symlink(external, checkout / "groovy")
            except OSError as exc:  # pragma: no cover - platform capability
                self.skipTest(f"symbolic links unavailable: {exc}")

            with open_recipe_health(checkout) as view:
                self.assertEqual(1, view.describe()["source_file_count"])
                self.assertEqual([], view.search("external_recipe_marker")["results"])
                self.assertEqual(1, len(view.search("safe_recipe_marker")["results"]))


if __name__ == "__main__":
    unittest.main()
