from __future__ import annotations

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
    PROPOSED_ASSESSMENT_FORMAT,
    RecipeHealthError,
    assess_proposed_recipe,
    open_recipe_health,
)


SOURCE_KIND = "workbench-supersymmetry-recipe-change-plan"


def _proposal(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "proposal_id": SOURCE_KIND + ":sha256:" + "1" * 64,
        "source_format": "workbench-supersymmetry-recipe-change-plan-v1",
        "source_kind": SOURCE_KIND,
        "mutation": "add",
        "recipe_map": "mixer",
        "duration": 40,
        "voltage_tier": "LV",
        "item_inputs": [],
        "fluid_inputs": [{"name": "feed", "amount": 1000}],
        "item_outputs": [],
        "fluid_outputs": [{"name": "product", "amount": 1000}],
    }
    value.update(changes)
    return value


class ProposedRecipeAssessmentV1Tests(unittest.TestCase):
    def build_graph(self, root: Path) -> dict[str, dict]:
        recipe_map = node_record("gt-recipe-map", "mixer", {"name": "mixer"})
        machine = node_record(
            "gt-machine", "fixture:mixer.lv", {"registry_name": "fixture:mixer.lv", "tier": 1}
        )
        feed = node_record("forge-fluid", "feed", {"name": "feed"})
        product = node_record("forge-fluid", "product", {"name": "product"})
        existing = node_record(
            "gt-recipe",
            "mixer|existing|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "2" * 64,
                "duplicate_ordinal": 0,
                "duration": 40,
                "eut": 30,
            },
        )
        recycle = node_record(
            "gt-recipe",
            "mixer|recycle|0",
            {
                "recipe_map": "mixer",
                "semantic_sha256": "3" * 64,
                "duplicate_ordinal": 0,
                "duration": 20,
                "eut": 30,
            },
        )
        existing_selector = node_record(
            "gt-recipe-input-selector", "mixer|existing|fluid|0", {"ordinal": 0}
        )
        recycle_selector = node_record(
            "gt-recipe-input-selector", "mixer|recycle|fluid|0", {"ordinal": 0}
        )
        quest = node_record("betterquesting-quest", "100", {"quest_id": 100})
        task = node_record("betterquesting-task-occurrence", "100|0", {"task_id": 0})
        requirement = node_record(
            "betterquesting-fluid-requirement-occurrence",
            "100|0|0",
            {"ordinal": 0, "amount": 1000},
        )
        nodes = [
            recipe_map,
            machine,
            feed,
            product,
            existing,
            recycle,
            existing_selector,
            recycle_selector,
            quest,
            task,
            requirement,
        ]
        edges = [
            edge_record("uses-recipe-map", machine["id"], recipe_map["id"], {}),
            edge_record("contained-in-recipe-map", existing["id"], recipe_map["id"], {}),
            edge_record("contained-in-recipe-map", recycle["id"], recipe_map["id"], {}),
            edge_record(
                "has-fluid-input-selector", existing["id"], existing_selector["id"], {"ordinal": 0}
            ),
            edge_record(
                "accepts-gt-fluid-input", existing_selector["id"], feed["id"], {"amount": 1000}
            ),
            edge_record(
                "produces-gt-fluid", existing["id"], product["id"], {"amount": 1000, "ordinal": 0}
            ),
            edge_record(
                "has-fluid-input-selector", recycle["id"], recycle_selector["id"], {"ordinal": 0}
            ),
            edge_record(
                "accepts-gt-fluid-input", recycle_selector["id"], product["id"], {"amount": 1000}
            ),
            edge_record(
                "produces-gt-fluid", recycle["id"], feed["id"], {"amount": 1000, "ordinal": 0}
            ),
            edge_record("owns-progression-task", quest["id"], task["id"], {}),
            edge_record(
                "has-progression-fluid-requirement", task["id"], requirement["id"], {"ordinal": 0}
            ),
            edge_record("requires-progression-fluid", requirement["id"], product["id"], {}),
        ]
        builder = CategoricalGraphBundleBuilder(
            root,
            scope={"profile": "proposed-recipe-fixture"},
            evidence_binding={"capture_id": "proposed-recipe-fixture"},
        )
        builder.add_partition(
            "proposed-recipe",
            classification="proposed recipe assessment fixture",
            dependencies=(),
            nodes=nodes,
            edges=edges,
            evidence_categories=("fixture",),
        )
        builder.close()
        return {
            "existing": existing,
            "product": product,
            "feed": feed,
            "quest": quest,
        }

    def test_assessment_exposes_collision_flow_cycle_quest_and_numeric_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            fixture = self.build_graph(graph)
            with open_recipe_health(graph) as view:
                report = assess_proposed_recipe(
                    view, _proposal(), max_depth=3, max_nodes=100
                )
                repeated = assess_proposed_recipe(
                    view, _proposal(), max_depth=3, max_nodes=100
                )

        self.assertEqual(report, repeated)
        self.assertEqual(PROPOSED_ASSESSMENT_FORMAT, report["format"])
        self.assertFalse(report["scenario"]["proposed_source_bytes_observed_runtime"])
        self.assertEqual(2, report["summary"]["exact_observed_resource_key_count"])
        collision = report["collision_assessment"]
        self.assertEqual(
            "unavailable-before-runtime-observation",
            collision["exact_runtime_signature"]["status"],
        )
        self.assertEqual(
            [fixture["existing"]["id"]],
            [
                row["recipe"]["selection_id"]
                for row in collision["resolved_request_structure"]["candidates"]
            ],
        )
        flow = report["output_flow"][0]
        self.assertEqual(
            [fixture["existing"]["id"]],
            [row["recipe"]["selection_id"] for row in flow["existing_producers"]],
        )
        self.assertEqual(1, report["summary"]["output_input_cycle_candidate_count"])
        cycle = report["dependency_cycle_candidates"][0]
        self.assertEqual(fixture["product"]["id"], cycle["observed_output_to_input_path"][0])
        self.assertEqual(fixture["feed"]["id"], cycle["observed_output_to_input_path"][-1])
        quests = report["progression_signals"]["quest_signals"]
        self.assertEqual(
            fixture["quest"]["id"],
            quests["direct_output_requirements"][0]["quest"]["selection_id"],
        )
        numeric = report["progression_signals"]["energy_and_machine_signals"]
        self.assertEqual(40, numeric["proposal"]["duration"])
        self.assertIsNone(numeric["proposal"]["numeric_eut"])
        self.assertEqual([20, 40], numeric["observed_durations"])
        self.assertEqual([30], numeric["observed_euts"])
        self.assertEqual(0, report["summary"]["dead_path_candidate_count"])
        gap_codes = {row["code"] for row in report["evidence_gaps"]}
        self.assertIn("progression-reachability-not-proven", gap_codes)
        self.assertIn("proposed-runtime-not-observed", gap_codes)
        self.assertIn("graph-capture-workspace-revision-unbound", gap_codes)

    def test_symbolic_metaitem_remains_unresolved_and_blocks_collision_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            self.build_graph(graph)
            proposal = _proposal(
                fluid_inputs=[],
                item_inputs=[{"kind": "metaitem", "name": "dustCopper", "amount": 1}],
            )
            with open_recipe_health(graph) as view:
                report = assess_proposed_recipe(view, proposal, max_nodes=100)

        resolved = report["resolution"]["item_inputs"][0]
        self.assertEqual("unsupported-symbolic-binding", resolved["status"])
        self.assertEqual(
            "resource-resolution-incomplete",
            report["collision_assessment"]["resolved_request_structure"]["status"],
        )
        self.assertEqual(1, report["summary"]["unresolved_or_ambiguous_resource_count"])
        self.assertIn(
            "symbolic-metaitem-runtime-binding-unavailable",
            {row["code"] for row in report["unknowns"]},
        )

    def test_translation_and_bounds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            self.build_graph(graph)
            with open_recipe_health(graph) as view:
                forged = _proposal(mutation="remove")
                with self.assertRaisesRegex(RecipeHealthError, "content-bound ADD"):
                    assess_proposed_recipe(view, forged)
                with self.assertRaisesRegex(RecipeHealthError, "outside 10..2000"):
                    assess_proposed_recipe(view, _proposal(), max_nodes=9)

    def test_exact_direct_item_count_resolves_and_matches_recipe_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            recipe_map = node_record("gt-recipe-map", "mixer", {"name": "mixer"})
            wrong_count = node_record(
                "item-variant",
                "fixture:feed|count-1",
                {
                    "registry_name": "fixture:feed",
                    "metadata": 0,
                    "count_in_observation": 1,
                },
            )
            feed = node_record(
                "item-variant",
                "fixture:feed|count-2",
                {
                    "registry_name": "fixture:feed",
                    "metadata": 0,
                    "count_in_observation": 2,
                },
            )
            product = node_record(
                "item-variant",
                "fixture:product|count-3",
                {
                    "registry_name": "fixture:product",
                    "metadata": 0,
                    "count_in_observation": 3,
                },
            )
            recipe = node_record(
                "gt-recipe",
                "mixer|direct-item|0",
                {
                    "recipe_map": "mixer",
                    "semantic_sha256": "4" * 64,
                    "duplicate_ordinal": 0,
                    "duration": 40,
                    "eut": 30,
                },
            )
            selector = node_record(
                "gt-recipe-input-selector",
                "mixer|direct-item|item|0",
                {"ordinal": 0},
            )
            builder = CategoricalGraphBundleBuilder(
                graph,
                scope={"profile": "direct-item-proposal"},
                evidence_binding={"capture_id": "direct-item-proposal"},
            )
            builder.add_partition(
                "direct-item-proposal",
                classification="direct item proposal fixture",
                dependencies=(),
                nodes=[
                    recipe_map,
                    wrong_count,
                    feed,
                    product,
                    recipe,
                    selector,
                ],
                edges=[
                    edge_record(
                        "contained-in-recipe-map",
                        recipe["id"],
                        recipe_map["id"],
                        {},
                    ),
                    edge_record(
                        "has-item-input-selector",
                        recipe["id"],
                        selector["id"],
                        {"ordinal": 0},
                    ),
                    edge_record(
                        "accepts-gt-item-alternative",
                        selector["id"],
                        feed["id"],
                        {"alternative_ordinal": 0},
                    ),
                    edge_record(
                        "produces-gt-item",
                        recipe["id"],
                        product["id"],
                        {"ordinal": 0, "chanced": False},
                    ),
                ],
                evidence_categories=("fixture",),
            )
            builder.close()
            proposal = _proposal(
                fluid_inputs=[],
                fluid_outputs=[],
                item_inputs=[
                    {
                        "kind": "item",
                        "name": "fixture:feed",
                        "metadata": 0,
                        "amount": 2,
                    }
                ],
                item_outputs=[
                    {
                        "kind": "item",
                        "name": "fixture:product",
                        "metadata": 0,
                        "amount": 3,
                    }
                ],
            )

            with open_recipe_health(graph) as view:
                report = assess_proposed_recipe(view, proposal, max_nodes=100)

        resolved = report["resolution"]["item_inputs"][0]
        self.assertEqual("exact-observed-key", resolved["status"])
        self.assertEqual(
            [feed["id"]],
            [row["selection_id"] for row in resolved["candidates"]],
        )
        self.assertEqual(1, report["summary"]["structural_collision_candidate_count"])
        self.assertEqual(
            recipe["id"],
            report["collision_assessment"]["resolved_request_structure"][
                "candidates"
            ][0]["recipe"]["selection_id"],
        )

    def test_missing_recipe_map_is_evidence_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            builder = CategoricalGraphBundleBuilder(
                graph,
                scope={"profile": "missing-recipe-map"},
                evidence_binding={"capture_id": "missing-recipe-map"},
            )
            builder.add_partition(
                "missing-recipe-map",
                classification="missing recipe map fixture",
                dependencies=(),
                nodes=[
                    node_record("forge-fluid", "feed", {"name": "feed"}),
                    node_record("forge-fluid", "product", {"name": "product"}),
                ],
                edges=[],
                evidence_categories=("fixture",),
            )
            builder.close()

            with open_recipe_health(graph) as view:
                report = assess_proposed_recipe(view, _proposal(), max_nodes=100)

        self.assertEqual(
            "evidence-unavailable",
            report["collision_assessment"]["resolved_request_structure"]["status"],
        )
        self.assertIn(
            "proposed-recipe-map-evidence-unavailable",
            {row["code"] for row in report["unknowns"]},
        )


if __name__ == "__main__":
    unittest.main()
