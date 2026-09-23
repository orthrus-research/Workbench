"""Independent finite-capture cases for the pack-wide dead-end audit."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from workbench_atlas_recipe_health import RecipeHealthError, open_recipe_health
from workbench_atlas_recipe_health import dead_ends
from workbench_atlas_recipe_health.dead_ends import DEAD_END_AUDIT_FORMAT, audit_recipe_dead_ends
from test_captured_recipe_routes import route_graph


class RecipeDeadEndsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-dead-ends-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def report(self, rows, **kwargs):
        self.graph = route_graph(self.root / "graph", rows, **kwargs)
        with open_recipe_health(self.graph["path"]) as view:
            self.result = audit_recipe_dead_ends(view)
        self.recipes = {row["semantic_key"]: row for row in self.result["recipes"]}
        self.resources = {row["semantic_key"]: row for row in self.result["resources"]}
        return self.result

    def test_missing_producer_consumer_and_both_are_local_candidates(self):
        report = self.report([
            {"name": "both", "inputs": [{"members": ["missing"], "complete": True}], "outputs": ["unused"]},
            {"name": "upstream", "inputs": [{"members": ["missing"], "complete": True}], "outputs": ["used"]},
            {"name": "downstream", "inputs": [{"members": ["used"], "complete": True}], "outputs": ["terminal"]},
        ])
        self.assertEqual(DEAD_END_AUDIT_FORMAT, report["format"])
        self.assertIn("both-sides-candidate", self.recipes["both"]["findings"])
        self.assertEqual(["missing-producer-candidate"], self.recipes["upstream"]["findings"])
        self.assertEqual(["no-output-use-candidate", "stranded-output-candidate"], self.recipes["downstream"]["findings"])
        self.assertEqual("unassessed", report["coverage"]["transitive_supply"])
        self.assertEqual("unsupported", report["coverage"]["external_acquisition"])
        self.assertEqual("unsupported", report["coverage"]["terminal_use"])
        self.assertEqual(2, report["summary"]["missing_producer_candidate_count"])
        self.assertEqual(2, report["summary"]["no_output_use_candidate_count"])
        self.assertEqual(1, report["summary"]["both_sides_candidate_count"])

    def test_one_valid_alternative_supplies_slot_without_promoting_other_alternatives(self):
        self.report([
            {"name": "target", "inputs": [{"members": ["missing", "inactive", "unknown", "supplied"], "complete": True}], "outputs": ["target"]},
            {"name": "source", "outputs": ["supplied"]},
            {"name": "inactive", "active": False, "outputs": ["inactive"]},
            {"name": "unknown", "active": None, "outputs": ["unknown"]},
        ])
        target = self.recipes["target"]
        self.assertEqual("observed-local-links", target["upstream"]["status"])
        self.assertEqual("observed-active-producer", target["inputs"][0]["supply_status"])
        self.assertEqual(4, len(target["inputs"][0]["alternatives"]))
        self.assertEqual([], self.resources["missing"]["producers"])
        self.assertEqual({"inactive": 1}, self.resources["inactive"]["producer_counts"])
        self.assertEqual({"unknown": 1}, self.resources["unknown"]["producer_counts"])

    def test_inactive_and_unknown_recipes_are_unassessed_and_never_active_links(self):
        report = self.report([
            {"name": "target", "inputs": [{"members": ["inactive"], "complete": True},
                                             {"members": ["unknown"], "complete": True}], "outputs": ["target"]},
            {"name": "inactive", "active": False, "inputs": [{"members": ["target"], "complete": True}], "outputs": ["inactive"]},
            {"name": "unknown", "active": None, "inputs": [{"members": ["target"], "complete": True}], "outputs": ["unknown"]},
        ])
        target = self.recipes["target"]
        self.assertEqual("missing-producer-candidate", target["upstream"]["status"])
        self.assertEqual(1, len(target["upstream"]["missing_selector_ids"]))
        self.assertEqual(1, len(target["upstream"]["unknown_selector_ids"]))
        self.assertEqual("unknown", target["downstream"]["status"])
        for name in ("inactive", "unknown"):
            self.assertEqual("unassessed", self.recipes[name]["upstream"]["status"])
            self.assertEqual("unassessed", self.recipes[name]["downstream"]["status"])
            self.assertEqual([], self.recipes[name]["findings"])
        self.assertEqual(1, report["summary"]["active_recipe_count"])
        self.assertEqual(1, report["summary"]["inactive_recipe_count"])
        self.assertEqual(1, report["summary"]["unknown_activity_recipe_count"])
        self.assertEqual([], report["cycles"])

    def test_incomplete_matching_and_observation_only_representatives_remain_unknown(self):
        self.report([
            {"name": "target", "inputs": [{"observed": ["available"], "complete": False},
                                             {"members": [], "complete": False}], "outputs": ["target"]},
            {"name": "source", "outputs": ["available"]},
        ])
        target = self.recipes["target"]
        self.assertEqual("unknown", target["upstream"]["status"])
        self.assertTrue(all(not slot["alternatives"] for slot in target["inputs"]))
        self.assertEqual(1, sum(len(slot["observed_representatives"]) for slot in target["inputs"]))
        self.assertEqual([], self.resources["available"]["uses"])
        self.assertEqual("unknown", self.recipes["source"]["downstream"]["status"])
        self.assertEqual(1, len(self.resources["available"]["observation_only_uses"]))
        self.assertEqual(2, self.result["coverage"]["incomplete_selector_count"])

    def test_complete_empty_match_is_a_candidate_and_runtime_limits_remain_unknown(self):
        self.report([
            {"name": "empty", "inputs": [{"members": [], "complete": True}], "outputs": ["a"]},
            {"name": "limited", "inputs": [{"members": ["missing"], "complete": True,
                                              "properties": {"matching_limits": ["capability-not-captured"]}}], "outputs": ["b"]},
        ])
        self.assertIn("missing-producer-candidate", self.recipes["empty"]["findings"])
        self.assertIn("no-accepted-resource-in-captured-domain", self.recipes["empty"]["inputs"][0]["issues"])
        self.assertEqual("unknown", self.recipes["limited"]["upstream"]["status"])
        self.assertEqual(["capability-not-captured"], self.recipes["limited"]["inputs"][0]["matching_limits"])

    def test_reusable_and_unknown_consumption_are_uses_without_claiming_consumption(self):
        self.report([
            {"name": "source", "outputs": ["tool", "unknown", "fluid:feed"]},
            {"name": "consumer", "inputs": [{"members": ["tool"], "complete": True, "reusable": True},
                                              {"members": ["unknown"], "complete": True, "reusable": None},
                                              {"members": ["fluid:feed"], "fluid": True, "amount": 144, "complete": True}],
             "outputs": ["target"]},
        ])
        self.assertEqual("observed-local-links", self.recipes["source"]["downstream"]["status"])
        self.assertEqual("reusable", self.resources["tool"]["uses"][0]["consumption"])
        self.assertEqual("unknown", self.resources["unknown"]["uses"][0]["consumption"])
        self.assertEqual("consumed", self.resources["fluid:feed"]["uses"][0]["consumption"])
        fluid = next(slot for slot in self.recipes["consumer"]["inputs"] if slot["family"] == "fluid")
        self.assertEqual(144, fluid["amount"])

    def test_stranded_byproduct_is_not_whole_recipe_without_use(self):
        self.report([
            {"name": "source", "outputs": ["used", "stranded"]},
            {"name": "consumer", "inputs": [{"members": ["used"], "complete": True}], "outputs": ["target"]},
        ])
        source = self.recipes["source"]
        self.assertEqual("partial-output-use-gap", source["downstream"]["status"])
        self.assertEqual(["stranded-output-candidate"], source["findings"])
        self.assertEqual(1, len(source["downstream"]["stranded_output_edge_ids"]))

    def test_partial_output_gap_retains_unknown_use_in_unresolved_count(self):
        report = self.report([
            {"name": "source", "outputs": ["stranded", "uncertain"]},
            {"name": "unknown", "active": None, "inputs": [{"members": ["uncertain"], "complete": True}], "outputs": ["target"]},
        ])
        self.assertEqual("partial-output-use-gap", self.recipes["source"]["downstream"]["status"])
        self.assertEqual(1, len(self.recipes["source"]["downstream"]["unknown_output_edge_ids"]))
        self.assertEqual(2, report["summary"]["unresolved_recipe_count"])

    def test_chance_outputs_are_distinct_and_keep_exact_values_even_at_full_chance(self):
        huge = 9007199254740993
        self.report([
            {"name": "source", "outputs": [{"resource": "target", "amount": huge},
                                             {"resource": "target", "amount": 4, "chanced": True,
                                              "output_family": "chanced_item_outputs", "chance": 10000,
                                              "chance_boost": 0, "logic_class": "XOR"}]},
            {"name": "consumer", "inputs": [{"members": ["target"], "complete": True}], "outputs": ["end"]},
        ])
        outputs = self.recipes["source"]["outputs"]
        self.assertEqual(2, len(outputs))
        self.assertEqual({"chanced", "guaranteed"}, {output["output_kind"] for output in outputs})
        self.assertEqual({huge, 4}, {output["values"]["amount"] for output in outputs})
        chance = next(output for output in outputs if output["output_kind"] == "chanced")
        self.assertEqual(10000, chance["values"]["chance"])
        self.assertEqual("XOR", chance["values"]["logic_class"])
        self.assertEqual("unassessed", self.result["coverage"]["chance_feasibility"])

    def test_missing_input_inventory_is_not_inputless(self):
        self.report([
            {"name": "missing", "input_inventory": False, "outputs": ["unknown"]},
            {"name": "zero", "outputs": ["observed"]},
            {"name": "no-output", "outputs": []},
        ])
        self.assertEqual("unknown", self.recipes["missing"]["upstream"]["status"])
        self.assertEqual("observed-inputless", self.recipes["zero"]["upstream"]["status"])
        self.assertEqual("unknown", self.recipes["no-output"]["downstream"]["status"])
        self.assertEqual([], self.recipes["no-output"]["findings"])

    def test_invalid_output_quantity_is_not_valid_supply_or_stranded_output(self):
        self.report([
            {"name": "invalid", "outputs": [{"resource": "a", "amount": 0},
                                             {"resource": "b", "amount": -1},
                                             {"resource": "c", "amount": None}]},
            {"name": "consumer", "inputs": [{"members": [name], "complete": True} for name in ("a", "b", "c")],
             "outputs": ["target"]},
        ])
        self.assertEqual("unknown", self.recipes["consumer"]["upstream"]["status"])
        self.assertTrue(all(slot["supply_status"] == "unknown" for slot in self.recipes["consumer"]["inputs"]))
        self.assertEqual("unknown", self.recipes["invalid"]["downstream"]["status"])
        self.assertEqual([], self.recipes["invalid"]["findings"])
        self.assertEqual(3, len(self.recipes["invalid"]["issues"]))

    def test_invalid_input_quantity_does_not_supply_or_consume_even_with_positive_output(self):
        report = self.report([
            {"name": "source", "outputs": ["a"]},
            {"name": "consumer", "inputs": [{"members": ["a"], "complete": True, "amount": 0}], "outputs": ["target"]},
            {"name": "false-cycle", "inputs": [{"members": ["cycle"], "complete": True, "amount": -1}], "outputs": ["cycle"]},
        ])
        self.assertEqual("unknown", self.recipes["consumer"]["upstream"]["status"])
        self.assertEqual("unknown", self.recipes["source"]["downstream"]["status"])
        self.assertEqual([], report["cycles"])

    def test_empty_inventory_reports_no_supported_recipes(self):
        report = self.report([], extra_resources=["unexplored"])
        self.assertEqual("no-supported-recipes", report["coverage"]["status"])
        self.assertEqual(0, report["summary"]["recipe_count"])
        self.assertEqual([], report["recipes"])
        self.assertEqual(1, report["coverage"]["scanned_node_kind_counts"]["item-variant"])

    def test_inventory_count_mismatch_and_invalid_counts_are_refused(self):
        for number, counts in enumerate(({"item": 1, "fluid": 0}, {"item": False, "fluid": 0},
                                         {"item": -1, "fluid": 0}, {"item": 0})):
            with self.subTest(counts=counts):
                graph = route_graph(self.root / f"graph-{number}", [{"name": "r", "input_counts": counts, "outputs": ["target"]}])
                with open_recipe_health(graph["path"]) as view, self.assertRaises(RecipeHealthError):
                    audit_recipe_dead_ends(view)

    def test_observation_only_complete_selector_is_refused(self):
        graph = route_graph(self.root / "graph", [{"name": "r", "inputs": [{"observed": ["a"], "complete": True}], "outputs": ["b"]}])
        with open_recipe_health(graph["path"]) as view, self.assertRaisesRegex(RecipeHealthError, "observation-only"):
            audit_recipe_dead_ends(view)

    def test_isolated_cycle_has_exact_witness_without_claiming_viability(self):
        report = self.report([
            {"name": "a", "inputs": [{"members": ["b"], "complete": True}], "outputs": ["a"]},
            {"name": "b", "inputs": [{"members": ["a"], "complete": True}], "outputs": ["b"]},
        ])
        self.assertEqual(1, report["summary"]["cycle_count"])
        component = report["cycles"][0]
        self.assertEqual([], component["incoming_dependency_edges"])
        self.assertEqual([], component["outgoing_dependency_edges"])
        self.assertEqual("unknown", component["seed_supply"])
        self.assertEqual("unknown", component["viability_effect"])
        witness = component["witness"]
        self.assertEqual(witness["node_ids"][0], witness["node_ids"][-1])
        for index, arc in enumerate(witness["arcs"]):
            edge = self.graph["edges"][arc["edge_id"]]
            endpoints = [edge["source"], edge["target"]]
            if arc["direction"] == "reverse":
                endpoints.reverse()
            self.assertEqual(endpoints, witness["node_ids"][index:index + 2])
        self.assertTrue(all(row["findings"] == ["structural-cycle"] for row in report["recipes"]))

    def test_external_alternative_and_downstream_use_remain_visible_at_cycle_boundary(self):
        report = self.report([
            {"name": "loop", "inputs": [{"members": ["loop", "outside"], "complete": True}], "outputs": ["loop"]},
            {"name": "source", "outputs": ["outside"]},
            {"name": "consumer", "inputs": [{"members": ["loop"], "complete": True}], "outputs": ["end"]},
        ])
        cycle = report["cycles"][0]
        self.assertEqual(1, len(cycle["outgoing_dependency_edges"]))
        self.assertEqual(1, len(cycle["incoming_dependency_edges"]))
        self.assertEqual("unknown", cycle["viability_effect"])
        self.assertIn("structural-cycle", self.recipes["loop"]["findings"])
        self.assertNotIn("structural-cycle", self.recipes["consumer"]["findings"])

    def test_deterministic_complete_inventory_and_summary_reconcile(self):
        graph = route_graph(self.root / "graph", [
            {"name": f"r-{index}", "active": (True, False, None)[index % 3],
             "inputs": [{"members": ["shared"], "complete": True}], "outputs": [f"target-{index}"]}
            for index in range(103)])
        with open_recipe_health(graph["path"]) as view:
            sql = []
            view.query.connection.set_trace_callback(sql.append)
            first = audit_recipe_dead_ends(view)
            view.query.connection.set_trace_callback(None)
            second = audit_recipe_dead_ends(view)
        self.assertEqual(first, second)
        self.assertEqual("workbench-atlas-captured-active-links-v1", first["policy"]["id"])
        self.assertEqual(sha256(Path(dead_ends.__file__).read_bytes()).hexdigest(), first["policy"]["implementation_sha256"])
        self.assertEqual(2, len([statement for statement in sql if statement.startswith("SELECT")]))
        self.assertEqual(103, first["summary"]["recipe_count"])
        self.assertEqual(set(row["id"] for row in graph["recipes"].values()), {row["selection_id"] for row in first["recipes"]})
        self.assertFalse(first["summary"]["truncated"])
        self.assertEqual("complete-finite", first["coverage"]["scan"])
        self.assertEqual(Counter(row["upstream"]["status"] for row in first["recipes"]), first["summary"]["upstream_status_counts"])
        self.assertEqual(Counter(row["downstream"]["status"] for row in first["recipes"]), first["summary"]["downstream_status_counts"])
        self.assertEqual(Counter(code for row in first["recipes"] for code in row["findings"]), first["summary"]["finding_counts"])

    def test_cancellation_propagates_during_scan_and_cycle_analysis(self):
        graph = route_graph(self.root / "graph", [{"name": "loop", "inputs": [{"members": ["loop"], "complete": True}], "outputs": ["loop"]}])
        for limit in (1, 10, 40):
            with self.subTest(limit=limit), open_recipe_health(graph["path"]) as view:
                calls = 0
                def cancel():
                    nonlocal calls
                    calls += 1
                    if calls >= limit:
                        raise InterruptedError("cancelled")
                with self.assertRaisesRegex(InterruptedError, "cancelled"):
                    audit_recipe_dead_ends(view, check_cancelled=cancel)

    def test_source_and_initialization_views_are_refused(self):
        with self.assertRaisesRegex(RecipeHealthError, "observed categorical"):
            audit_recipe_dead_ends(object())
        graph = route_graph(self.root / "graph", [{"name": "r", "outputs": ["a"]}])
        with open_recipe_health(graph["path"]) as view:
            view.manifest = {**view.manifest, "format": "workbench-atlas-categorical-graph-bundle-v3"}
            with self.assertRaisesRegex(RecipeHealthError, "initialization observations"):
                audit_recipe_dead_ends(view)


if __name__ == "__main__":
    unittest.main()
