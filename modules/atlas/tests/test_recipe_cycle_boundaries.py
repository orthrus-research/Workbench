"""Independent small graphs for removal, circular supply and lookup boundaries."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from workbench_atlas_categorical_graph import (
    CategoricalGraphBundleBuilder,
    edge_record,
    node_record,
)
from workbench_atlas_recipe_health import assess_proposed_recipe, compare_runtime_recipe_graphs, open_recipe_health


class RecipeCycleBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-cycle-boundary-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def graph(self, name, recipes, *, protocol="a" * 64, incomplete=(),
              observations_only=False, limitations=(), include_recipe_map=False):
        """Each row is (name, lookup state, required resource names, outputs).

        Separate input names mean AND selectors. No production traversal helper
        supplies expected results: each case specifies its tiny graph directly.
        """
        resources = {
            name: node_record("forge-fluid", name, {"name": name})
            for _, _, inputs, outputs in recipes
            for name in (*inputs, *outputs)
        }
        nodes, edges, recipe_nodes = list(resources.values()), [], {}
        recipe_map = node_record("gt-recipe-map", "mixer", {"name": "mixer"})
        if include_recipe_map:
            nodes.append(recipe_map)
        for label, active, inputs, outputs in recipes:
            signature = sha256(label.encode()).hexdigest()
            recipe = node_record(
                "gt-recipe", f"mixer|{signature}|0",
                {"recipe_map": "mixer", "semantic_sha256": signature,
                 "duplicate_ordinal": 0, "lookup_active": active,
                 **({"duration": 40, "eut": 30} if include_recipe_map else {})},
            )
            nodes.append(recipe)
            recipe_nodes[label] = recipe
            if include_recipe_map:
                edges.append(edge_record("contained-in-recipe-map", recipe["id"], recipe_map["id"], {}))
            for ordinal, resource in enumerate(inputs):
                selector = node_record(
                    "gt-recipe-input-selector", f"{label}|fluid|{ordinal}",
                    {"ordinal": ordinal, "amount": 1000, "non_consumable": False,
                     **({"acceptance_complete": False} if label in incomplete else {})},
                )
                nodes.append(selector)
                edges.extend((
                    edge_record("has-fluid-input-selector", recipe["id"], selector["id"], {"ordinal": ordinal}),
                    edge_record("observes-gt-fluid-input-representative" if observations_only and label in incomplete else "accepts-gt-fluid-input",
                                selector["id"], resources[resource]["id"], {"amount": 1000}),
                ))
            for ordinal, resource in enumerate(outputs):
                edges.append(edge_record(
                    "produces-gt-fluid", recipe["id"], resources[resource]["id"],
                    {"ordinal": ordinal, "amount": 1000},
                ))
        path = self.root / name
        builder = CategoricalGraphBundleBuilder(
            path,
            scope={"pack_profile_id": "fixture-pack", "platform_profile_id": "fixture-platform",
                   "platform_candidate": "fixture", "physical_side": "dedicated_server",
                   "projection_profile": "cycle-boundary-v1"},
            evidence_binding={
                "capture_id": name, "adapter_profile_sha256": protocol,
                "input_manifest_sha256": "b" * 64,
                "category_results": {"gt-recipes": {
                    "category_id": "transformation-recipe", "checkpoint_id": "post-start-end-tick",
                    "record_count": len(recipes), "records_sha256": "c" * 64,
                    "result_sha256": "d" * 64,
                }},
            },
        )
        builder.add_partition(
            "cycle-boundary", classification="independent structural cycle fixture",
            dependencies=(), nodes=nodes, edges=edges,
            evidence_categories=("transformation-recipe",),
            limitations=limitations,
        )
        builder.close()
        return path, recipe_nodes, resources

    def compare(self, before, after, *, max_depth=4):
        with open_recipe_health(before) as left, open_recipe_health(after) as right:
            return compare_runtime_recipe_graphs(left, right, max_nodes=200, max_depth=max_depth)

    def test_unchanged_self_recycler_is_not_a_clean_alternative_after_source_removal(self):
        external = ("external", True, ("feed",), ("product",))
        recycler = ("recycler", True, ("product",), ("product",))
        before, recipes, resources = self.graph("before", (external, recycler))
        after, _, _ = self.graph("after", (recycler,))
        report = self.compare(before, after)
        self.assertEqual("compatible", report["compatibility"]["state"])
        signals = report["cycle_signals"].get("remaining_producer_dependency_cycle_signals", [])
        self.assertEqual(1, len(signals))
        self.assertEqual(recipes["recycler"]["id"], signals[0]["recipe_id"])
        self.assertEqual([resources["product"]["id"]], signals[0]["affected_resource_ids"])
        self.assertEqual("unknown", signals[0]["viability_effect"])
        self.assertEqual(1, report["summary"]["remaining_producer_dependency_cycle_signal_count"])
        self.assertEqual([], report["cycle_signals"]["introduced_with_added_recipes"])
        self.assertEqual([], report["resource_flow_deltas"]["newly_without_observed_finite_producers"])
        self.assertEqual([], report["propagation"]["at_risk_recipes"])

    def test_independent_remaining_producer_does_not_report_a_cycle(self):
        external = ("external", True, ("feed",), ("product",))
        alternative = ("alternative", True, ("other-feed",), ("product",))
        before, _, _ = self.graph("before", (external, alternative))
        after, _, _ = self.graph("after", (alternative,))
        report = self.compare(before, after)
        self.assertEqual([], report["cycle_signals"]["remaining_producer_dependency_cycle_signals"])
        self.assertEqual([], report["frontiers"])

    def test_remaining_multi_recipe_cycle_retains_independent_producer_and_no_viability_claim(self):
        external = ("external", True, ("feed",), ("product",))
        remaining = (
            ("recycler", True, ("intermediate",), ("product",)),
            ("return", True, ("product",), ("intermediate",)),
            ("independent", True, ("other-feed",), ("product",)),
        )
        before, recipes, resources = self.graph("before", (external, *remaining))
        after, _, _ = self.graph("after", remaining)
        report = self.compare(before, after)
        signals = report["cycle_signals"]["remaining_producer_dependency_cycle_signals"]
        self.assertEqual(1, len(signals))
        self.assertEqual([
            recipes["recycler"]["id"], resources["product"]["id"],
            recipes["return"]["id"], resources["intermediate"]["id"],
            recipes["recycler"]["id"],
        ], signals[0]["path"])
        self.assertEqual("unknown", signals[0]["viability_effect"])
        self.assertEqual([], report["propagation"]["at_risk_recipes"])

    def test_unknown_remaining_producer_activity_withholds_cycle_classification(self):
        external = ("external", True, ("feed",), ("product",))
        unknown = ("unknown", None, ("product",), ("product",))
        before, _, _ = self.graph("before", (external, unknown))
        after, _, _ = self.graph("after", (unknown,))
        report = self.compare(before, after)
        self.assertEqual([], report["cycle_signals"]["remaining_producer_dependency_cycle_signals"])
        self.assertEqual([], report["resource_flow_deltas"]["newly_without_observed_finite_producers"])
        self.assertIn("recipe-lookup-activity-unavailable", {r["code"] for r in report["unknowns"]})

    def test_terminal_resource_at_depth_bound_does_not_claim_omitted_cycle_work(self):
        terminal = ("terminal", True, ("product",), ("leaf",))
        added = ("added", True, ("feed",), ("product",))
        before, _, _ = self.graph("before", (terminal,))
        after, _, _ = self.graph("after", (terminal, added))
        report = self.compare(before, after, max_depth=1)
        self.assertEqual([], report["cycle_signals"]["introduced_with_added_recipes"])
        self.assertFalse(any(row.get("phase") == "introduced-cycle-scan" for row in report["frontiers"]))
        self.assertEqual("complete-within-bounds", report["cycle_signals"]["status"])

    def test_real_continuation_at_depth_bound_retains_cycle_frontier(self):
        existing = (
            ("middle", True, ("product",), ("intermediate",)),
            ("return", True, ("intermediate",), ("feed",)),
        )
        added = ("added", True, ("feed",), ("product",))
        before, _, _ = self.graph("before", existing)
        after, _, _ = self.graph("after", (*existing, added))
        report = self.compare(before, after, max_depth=1)
        self.assertEqual([], report["cycle_signals"]["introduced_with_added_recipes"])
        self.assertTrue(any(row.get("phase") == "introduced-cycle-scan" and row["kind"] == "depth-bound" for row in report["frontiers"]))
        self.assertEqual("truncated", report["cycle_signals"]["status"])
        complete = self.compare(before, after, max_depth=2)
        self.assertEqual(1, len(complete["cycle_signals"]["introduced_with_added_recipes"]))

    def test_impact_does_not_expose_an_already_lookup_inactive_consumer(self):
        path, recipes, _ = self.graph("graph", (
            ("selected", True, ("feed",), ("product",)),
            ("inactive", False, ("product",), ("tail",)),
        ))
        with open_recipe_health(path) as view:
            report = view.impact(recipes["selected"]["id"], max_nodes=200)
        self.assertEqual([], report["propagation"]["at_risk_recipes"])
        self.assertEqual([], report["direct"]["outputs"][0]["downstream_consumers"])

    def test_impact_unknown_consumer_activity_is_explicit_and_not_exposed(self):
        path, recipes, _ = self.graph("graph", (
            ("selected", True, ("feed",), ("product",)),
            ("unknown", None, ("product",), ("tail",)),
        ))
        with open_recipe_health(path) as view:
            report = view.impact(recipes["selected"]["id"], max_nodes=200)
        self.assertEqual([], report["propagation"]["at_risk_recipes"])
        self.assertIn("consumer-lookup-state-unavailable", {row["code"] for row in report["unknowns"]})

    def test_selected_removal_propagates_through_an_alternative_using_its_other_output(self):
        path, recipes, resources = self.graph("graph", (
            ("selected", True, ("feed",), ("product", "intermediate")),
            ("alternative", True, ("intermediate",), ("product",)),
        ))
        with open_recipe_health(path) as view:
            report = view.impact(recipes["selected"]["id"], max_nodes=200)
        self.assertEqual([recipes["alternative"]["id"]], [r["recipe"]["selection_id"] for r in report["propagation"]["at_risk_recipes"]])
        self.assertEqual({resources["product"]["id"], resources["intermediate"]["id"]}, {r["resource"]["selection_id"] for r in report["propagation"]["at_risk_resources"]})

    def test_incomparable_pair_retains_empty_cycle_section_without_claiming_absence(self):
        rows = (("recipe", True, ("feed",), ("product",)),)
        before, _, _ = self.graph("before", rows)
        after, _, _ = self.graph("after", rows, protocol="f" * 64)
        report = self.compare(before, after)
        self.assertEqual("incomparable", report["compatibility"]["state"])
        self.assertEqual([], report["cycle_signals"]["remaining_producer_dependency_cycle_signals"])
        self.assertEqual("not-compared", report["cycle_signals"]["status"])
        self.assertIsNone(report["summary"]["remaining_producer_dependency_cycle_signal_count"])

    def test_incomplete_selected_input_reports_uncertainty_without_exact_acceptance(self):
        path, recipes, _ = self.graph("graph", (
            ("selected", True, ("feed",), ("product",)),
        ), incomplete=("selected",), observations_only=True,
            limitations=("Custom input matching was not executed.",))
        with open_recipe_health(path) as view:
            report = view.impact(recipes["selected"]["id"], max_nodes=200)
        self.assertEqual([], report["direct"]["inputs"])
        self.assertIn("selector-acceptance-incomplete", {row["code"] for row in report["unknowns"]})
        self.assertTrue(report["summary"]["truncated"])
        self.assertEqual({"code": "graph-projection-limitations",
                          "message": "Custom input matching was not executed."}, report["evidence_gaps"][-1])
        self.assertEqual(8, len(report["evidence_gaps"]))

    def test_incomplete_partial_selector_is_not_an_exposed_consumer_in_either_analysis(self):
        external = ("external", True, ("feed",), ("product",))
        consumer = ("consumer", True, ("product",), ("tail",))
        before, recipes, _ = self.graph("before", (external, consumer), incomplete=("consumer",))
        after, _, _ = self.graph("after", (consumer,), incomplete=("consumer",))
        with open_recipe_health(before) as view:
            impact = view.impact(recipes["external"]["id"], max_nodes=200)
        self.assertEqual([], impact["propagation"]["at_risk_recipes"])
        self.assertTrue(impact["summary"]["truncated"])
        comparison = self.compare(before, after)
        self.assertEqual([], comparison["propagation"]["at_risk_recipes"])
        self.assertTrue(comparison["summary"]["truncated"])
        self.assertTrue(any(row.get("relation") == "selector-acceptance-incomplete" for row in comparison["frontiers"]))

    def test_comparison_retains_projection_limits_for_each_side_including_incomparable(self):
        rows = (("recipe", True, ("feed",), ("product",)),)
        before, _, _ = self.graph("before", rows, limitations=("Before graph limit.",))
        for protocol in ("a"*64, "f"*64):
            after, _, _ = self.graph("after-" + protocol[0], rows, protocol=protocol,
                                      limitations=("After graph limit.",))
            report = self.compare(before, after)
            gaps = [row for row in report["evidence_gaps"] if row["code"] == "graph-projection-limitations"]
            self.assertEqual([{"code": "graph-projection-limitations", "side": "before", "message": "Before graph limit."},
                              {"code": "graph-projection-limitations", "side": "after", "message": "After graph limit."}], gaps)

    def test_incomplete_selector_cannot_establish_proposed_structural_collision(self):
        path, _, _ = self.graph("graph", (("existing", True, ("feed",), ("product",)),),
                                incomplete=("existing",), include_recipe_map=True,
                                limitations=("Custom matching is not modeled.",))
        proposal = {
            "proposal_id": "workbench-plan:sha256:" + "1"*64,
            "source_format": "workbench-plan-v1", "source_kind": "workbench-plan",
            "mutation": "add", "recipe_map": "mixer", "duration": 40, "voltage_tier": "LV",
            "item_inputs": [], "item_outputs": [],
            "fluid_inputs": [{"name": "feed", "amount": 1000}],
            "fluid_outputs": [{"name": "product", "amount": 1000}],
        }
        with open_recipe_health(path) as view:
            report = assess_proposed_recipe(view, proposal, max_nodes=200)
        self.assertEqual(0, report["collision_assessment"]["resolved_request_structure"]["candidate_count"])
        self.assertIn("selector-acceptance-incomplete", {row["code"] for row in report["unknowns"]})
        self.assertEqual("graph-projection-limitations", report["evidence_gaps"][-1]["code"])


if __name__ == "__main__":
    unittest.main()
