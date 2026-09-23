"""Actual lookup decisions are distinct from snapshot matches and machine validity."""
from copy import deepcopy
import unittest

from workbench_crucible.check_decisions import validate_decisions
from workbench_profile_supersymmetry import recipe_decisions as decisions, recipe_checks, recipe_explanations
from test_recipe_checks import inputs, expectation, capture, logs
from test_recipe_lifecycle import trace


def decision_capture(plan):
    value = capture(plan)
    value["lifecycle"] = trace(plan)
    predicate = next(name for name in decisions.SITES if name.startswith("lambda$recurseIngredientTreeFindRecipe$5")) + ":7"
    step = dict(id="d0", site=predicate, outcome="candidate-predicate-passed", operation=None, query="q0", thread=12,
                recipe="o0", competing=None, key=None, depth=-1, branch=None)
    query = dict(id="q0", calls=1, thread=12, arguments=dict(map="mixer", items=value["queries"][0]["items"], fluids=[],
                voltage_limit=2147483647, exact_voltage=False, item_slots=4, fluid_slots=4),
                 outcome="returned", selected="o0", exception=None, step_start=0, step_end=1)
    value["decisions"] = dict(format="workbench-recipe-decisions-v1", nonce="nonce", candidate_id=plan["candidate_id"],
                              state="complete", problems=[], methods=sorted(decisions.METHODS), steps=[step], queries=[query],
                              links={"r0": "o0"}, total_steps=1)
    return value


class RecipeDecisionTests(unittest.TestCase):
    def explain(self, value):
        plan = expectation()
        observed = recipe_checks.interpret(logs(value), "nonce", plan)
        report = recipe_explanations.explain(inputs(), plan, observed)
        derived = observed["details"]["decisions"]
        validate_decisions(derived, value["decisions"])
        return observed, report, derived

    def test_original_invocation_arguments_selection_and_source_are_bound(self):
        observed, report, derived = self.explain(decision_capture(expectation()))
        self.assertEqual(derived["state"], "complete")
        self.assertEqual(observed["facts"], expectation()["expected"])
        self.assertIn("Original findRecipe invocations: 1", report["text"])
        self.assertIn("Selected recipe: o0", report["text"])
        self.assertIn("not full recipe validity", report["text"])
        query = next(row for row in report["sections"] if row["id"] == "decision-q0")
        self.assertTrue(query["sources"])

    def test_tampered_or_incomplete_decisions_preserve_independent_snapshot(self):
        for change in ("nonce", "candidate", "methods", "twice", "arguments", "voltage", "exact", "selection", "missing", "thread", "site", "outcome", "gap", "links", "bound", "source", "overlong-id"):
            value = decision_capture(expectation()); raw = value["decisions"]
            if change == "nonce": raw["nonce"] = "different"
            if change == "candidate": raw["candidate_id"] = "different"
            if change == "methods": raw["methods"].pop()
            if change == "twice": raw["queries"][0]["calls"] = 2
            if change == "arguments": raw["queries"][0]["arguments"]["items"] = []
            if change == "voltage": raw["queries"][0]["arguments"]["voltage_limit"] = 30
            if change == "exact": raw["queries"][0]["arguments"]["exact_voltage"] = True
            if change == "selection": raw["queries"][0]["selected"] = "o1"
            if change == "missing": raw["steps"] = []
            if change == "thread": raw["steps"][0]["thread"] = 13
            if change == "site": raw["steps"][0]["site"] = "invented"
            if change == "outcome": raw["steps"][0]["outcome"] = "invented"
            if change == "gap": raw["steps"][0]["id"] = "d1"; raw["total_steps"] = 2
            if change == "links": raw["links"] = {"r0": "o1"}
            if change == "bound": raw["state"] = "incomplete"; raw["problems"] = ["decision-step-bound"]
            if change == "source": value["lifecycle"]["events"][0]["source"]["sha256"] = "a"*64
            if change == "overlong-id": raw["steps"][0]["id"] = "d" + "1"*5000
            observed, report, derived = self.explain(value)
            self.assertEqual(derived["state"], "incomplete", change)
            self.assertEqual(observed["facts"], expectation()["expected"], change)
            self.assertFalse(any(row["sources"] for row in report["sections"] if row["id"].startswith("decision-")), change)

    def test_accepting_but_unvisited_is_not_called_rejected(self):
        value = decision_capture(expectation())
        value["records"].append(deepcopy(value["records"][0]) | {"id": "r1"})
        value["queries"][0]["accepting"].append("r1")
        value["decisions"]["links"]["r1"] = "o1"
        _, report, derived = self.explain(value)
        self.assertEqual(derived["state"], "complete")
        self.assertIn("Accepting recipes not evaluated by this lookup: o1", report["text"])

    def test_false_insertion_requires_observed_conflict_identity(self):
        value = decision_capture(expectation()); raw = value["decisions"]
        value["lifecycle"]["events"][-1]["returned"] = False
        method = next(name for name in decisions.SITES if name.startswith("recurseIngredientTreeAdd"))
        conflict = dict(raw["steps"][0], id="d0", site=method+":142", outcome="different-recipe-blocks-insertion",
                        operation="e5", query=None, recipe="o0", competing="o1")
        raw["steps"][0]["id"] = "d1"; raw["steps"].insert(0, conflict)
        raw["total_steps"] = 2; raw["queries"][0].update(step_start=1, step_end=2)
        _, report, derived = self.explain(value)
        self.assertEqual(derived["state"], "complete")
        self.assertIn("First observed blocking decision: different-recipe-blocks-insertion", report["text"])
        raw["steps"][0]["competing"] = "o0"
        self.assertEqual(self.explain(value)[2]["state"], "incomplete")

    def test_missing_or_damaged_decisions_do_not_become_no_recipe(self):
        for raw in (None, [], {}, {"state": "incomplete", "problems": ["decision-byte-bound"]}):
            value = decision_capture(expectation()); value["decisions"] = raw
            observed, _, derived = self.explain(value)
            self.assertNotEqual(derived["state"], "complete")
            self.assertEqual(observed["facts"], expectation()["expected"])

    def test_failed_predicate_and_no_selection_are_observations_not_nonexecution(self):
        value = decision_capture(expectation()); raw = value["decisions"]
        value["records"] = []; value["queries"][0].update(accepting=[], winner=None, winner_id=None)
        value["lifecycle"]["links"] = {}; raw["links"] = {}
        raw["steps"][0]["outcome"] = "candidate-predicate-failed"; raw["queries"][0]["selected"] = None
        observed, report, derived = self.explain(value)
        self.assertEqual(derived["state"], "complete")
        self.assertFalse(observed["facts"]["lookup_satisfies_expectation"])
        self.assertIn("candidate-predicate-failed", report["text"])
        self.assertIn("Selected recipe: None", report["text"])
        raw["steps"][0]["recipe"] = None
        self.assertEqual(self.explain(value)[2]["state"], "incomplete")

    def test_subtree_conflict_does_not_invent_a_unique_competing_recipe(self):
        value = decision_capture(expectation()); raw = value["decisions"]
        value["lifecycle"]["events"][-1]["returned"] = False
        method = next(name for name in decisions.SITES if name.startswith("lambda$recurseIngredientTreeAdd"))
        step = dict(raw["steps"][0], site=method+":25", outcome="terminal-occupied-subtree", operation="e5", query=None)
        raw["steps"][0]["id"] = "d1"; raw["steps"].insert(0, step)
        raw["total_steps"] = 2; raw["queries"][0].update(step_start=1, step_end=2)
        _, report, derived = self.explain(value)
        self.assertEqual(derived["state"], "complete")
        self.assertIn("no unique conflicting recipe is asserted", report["text"])
        self.assertNotIn("First observed blocking decision: terminal-occupied-subtree", report["text"])
        value["lifecycle"]["events"][-1]["returned"] = True
        _, report, derived = self.explain(value)
        self.assertEqual(derived["state"], "complete")
        self.assertIn("insertion outcome: returned; returned: True", report["text"])
        self.assertNotIn("Registration not inserted", report["text"])

    def test_profile_free_integrity_rejects_resealed_derived_selection(self):
        value = decision_capture(expectation()); derived = deepcopy(self.explain(value)[2])
        derived["queries"][0]["selected"] = "o2"
        with self.assertRaisesRegex(ValueError, "differs from captured"):
            validate_decisions(derived, value["decisions"])

    def test_probe_invokes_original_lookup_once_and_always_closes_query_scope(self):
        source = recipe_checks.probe(expectation(), "nonce", trace=True).decode()
        self.assertEqual(source.count("map.findRecipe("), 1)
        self.assertLess(source.index("recipe.matches(false"), source.index("workbenchTraceQuery(queryId"))
        self.assertIn("finally { try { RecipeMap.workbenchTraceQuery(null)", source)
        self.assertNotIn("__TRACE_", source)
        lifecycle_failure = source.index("problems: ['trace-snapshot-failed']")
        decision_capture = source.index("RecipeMap.workbenchTraceDecisions(")
        decision_failure = source.index("problems: ['decision-snapshot-failed']")
        self.assertLess(lifecycle_failure, decision_capture)
        self.assertLess(decision_capture, decision_failure)
