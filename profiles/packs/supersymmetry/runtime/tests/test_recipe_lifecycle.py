"""Lifecycle coverage/source evidence never upgrades a final-state assertion."""
from copy import deepcopy
from hashlib import sha256
import unittest

from workbench_crucible.check_explanations import validate_explanation
from workbench_crucible.check_lifecycle import validate_lifecycle
from workbench_profile_supersymmetry import recipe_lifecycle as lifecycle
from workbench_profile_supersymmetry import recipe_explanations, recipe_checks
from test_recipe_checks import inputs, expectation, capture, logs, PATH


def trace(plan):
    source = {"path": PATH, "sha256": sha256(inputs().sources[PATH]).hexdigest(), "line": 2,
              "class_name": "postInit.Check", "compiled_sha256": "c" * 64}
    def event(index, operation, parent, finished, recipe_id=None, returned=None, validation=None):
        return dict(id=f"e{index}", operation=operation, parent=None if parent is None else f"e{parent}",
                    thread=12, source=source, outcome="returned", recipe_id=recipe_id, returned=returned,
                    validation=validation, finished=finished, properties=None)
    return {"format": "workbench-recipe-lifecycle-v1", "nonce": "nonce", "candidate_id": plan["candidate_id"],
            "state": "complete", "problems": [], "scope": "fixture", "source_bindings": 1, "total_events": 6,
            "hooks": {name: {"input_sha256": "a"*64, "definition_sha256": digest, "output_sha256": "b"*64,
                             "methods": sorted(lifecycle.HOOKS[name])} for name, digest in lifecycle.DEFINITIONS.items()},
            "events": [event(0,"registration",None,6), event(1,"build",0,2,"o0",validation="VALID"),
                       event(2,"validation",1,1,returned="VALID"), event(3,"add",0,5,"o0",True,"VALID"),
                       event(4,"post-validation",3,3,"o0",validation="VALID"), event(5,"insertion",3,4,"o0",True)],
            "links": {"r0":"o0"}}


class RecipeLifecycleTests(unittest.TestCase):
    def explain(self, life):
        plan = expectation(); value = capture(plan); value["lifecycle"] = life
        observed = recipe_checks.interpret(logs(value), "nonce", plan)
        report = recipe_explanations.explain(inputs(), plan, observed)
        validate_explanation(report, plan, observed["evidence"])
        validate_lifecycle(observed["details"]["lifecycle"], life)
        return observed, report

    def test_verified_line_and_recipe_identity_link_are_not_static_source_matching(self):
        observed, report = self.explain(trace(expectation()))
        self.assertEqual(observed["state"], "complete")
        self.assertEqual(observed["details"]["lifecycle"]["state"], "complete")
        self.assertIn("Recipe occurrence: o0; final snapshot references: r0", report["text"])
        sources = [source for row in report["sections"] for source in row["sources"] if source["basis"] == "executed-source-line"]
        self.assertEqual(len(sources), 6)
        self.assertEqual(sources[0]["location"]["start"]["line"], 2)
        self.assertIn("not necessarily a unique declaration", report["text"])

    def test_missing_failed_and_malformed_trace_do_not_change_snapshot_facts(self):
        for state in (None, {"state":"incomplete", "problems":["hook missing"]}, "invalid", []):
            with self.subTest(state=state):
                observed, report = self.explain(state)
                self.assertEqual(observed["facts"], expectation()["expected"])
                self.assertNotEqual(observed["details"]["lifecycle"]["state"], "complete")

    def test_changed_nonce_hook_source_or_parent_rejects_only_causal_links(self):
        for kind in ("nonce", "hook", "source", "line", "parent", "thread", "recipe", "unfinished", "overflow", "unknown-final", "negative-finish", "properties"):
            value = trace(expectation())
            if kind == "nonce": value["nonce"] = "other"
            if kind == "hook": value["hooks"].pop(next(iter(value["hooks"])))
            if kind == "source": value["events"][0]["source"]["sha256"] = "f"*64
            if kind == "line": value["events"][0]["source"]["line"] = 10000
            if kind == "parent": value["events"][1]["parent"] = "e1"
            if kind == "thread": value["events"][1]["thread"] = 15
            if kind == "recipe": value["links"]["r0"] = "o100"
            if kind == "unfinished": value["events"][0]["outcome"] = "open"
            if kind == "overflow": value["state"] = "incomplete"; value["problems"] = ["event-bound"]
            if kind == "unknown-final": value["links"]["r999"] = "o0"
            if kind == "negative-finish": value["events"][0]["finished"] = -1
            if kind == "properties": value["events"][0]["properties"] = []
            observed, report = self.explain(value)
            self.assertEqual(observed["details"]["lifecycle"]["state"], "incomplete", kind)
            self.assertEqual(observed["facts"], expectation()["expected"], kind)
            self.assertFalse(any(row["sources"] for row in report["sections"] if row["id"].startswith("lifecycle-")), kind)

    def test_rejected_insertion_is_not_given_an_invented_collision_reason(self):
        value = trace(expectation()); value["events"][-1]["returned"] = False
        observed, report = self.explain(value)
        self.assertIn("requires separate decision evidence", report["text"])

    def test_empty_final_links_are_valid_but_logger_placeholder_damage_is_not(self):
        value = trace(expectation()); value["links"] = {}
        observed, report = self.explain(value)
        self.assertEqual(observed["details"]["lifecycle"]["state"], "complete")
        value["links"] = 0
        observed, report = self.explain(value)
        self.assertEqual(observed["details"]["lifecycle"]["state"], "incomplete")
        self.assertEqual(observed["facts"], expectation()["expected"])
        probe = recipe_checks.probe(expectation(), "nonce", trace=True).decode()
        self.assertIn("log.infoMC('{}', ['[WORKBENCH-RECIPE-CAPTURE]' + encoded] as Object[])", probe)

    def test_profile_free_integrity_rejects_resealed_derived_event_changes(self):
        value = trace(expectation()); observed, _ = self.explain(value)
        derived = deepcopy(observed["details"]["lifecycle"])
        derived["events"][-1]["returned"] = False
        with self.assertRaisesRegex(ValueError, "differs from captured"):
            validate_lifecycle(derived, value)

    def test_attachment_configuration_keys_are_unpadded_and_source_bound(self):
        spec = lifecycle.attachment(inputs(), "nonce", expectation=expectation())
        for row in spec["configuration"].decode().splitlines():
            if row.startswith("source."):
                key, digest = row.split("=",1)
                self.assertEqual(len(digest),64)
                self.assertNotIn("=",key)
        self.assertEqual(set(spec["sources"]), {"RecipeAgent.java", "RecipeTrace.java", "RecipeDecisionHooks.java", "RecipeDecisions.java"})
        self.assertEqual(spec["policy"]["external_dependencies"], [])
