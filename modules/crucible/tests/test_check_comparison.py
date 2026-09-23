"""Directional comparisons preserve failed/unknown outcomes and incomplete coverage."""
from copy import deepcopy
from hashlib import sha256
import unittest

from workbench_api.checks import observation
from workbench_crucible.check_comparison import compare_results, group_findings, validate_comparison
from workbench_crucible.developer_checks import seal_result, outcome, validate_result


def finding(message="Existing problem", category="unclassified"):
    blocking = category in {"compiler", "runtime", "environment"}
    return {"code": "fixture", "category": category, "severity": "error", "blocking": blocking,
            "message": message, "reason": "Observed fixture event", "location": None,
            "reported_position": None, "location_kind": None,
            "evidence": [{"log": "logs/latest.log", "line": 1}],
            "diagnostic": {"key": "diagnostic:sha256:" + sha256(message.encode()).hexdigest(), "family": "fixture", "subject": message,
                           "origin": {"kind": "unknown", "name": "fixture", "basis": "No causal attribution"}, "guidance": "Inspect evidence"}}


def sample(index, findings=(), *, checkpoint=True, complete=True, stop=None, error=None):
    rows = list(findings)
    blocking = sum(row["blocking"] for row in rows)
    unknown = sum(row["category"] == "unclassified" for row in rows)
    interpretation = {"outcome": "failed" if blocking else "completed" if checkpoint and complete and not unknown else "inconclusive",
        "observation": observation("failure" if blocking else "checkpoint" if checkpoint else "running", "stage", "Observed", [{"log": "logs/latest.log", "line": 1}]),
        "checks": [], "findings": rows, "findings_count": len(rows), "blocking_findings_count": blocking, "unclassified_findings_count": unknown,
        "complete_logs": complete, "truncated": False, "runtime_observations": {"graphics": ["fixture-gpu"], "complete": True}}
    execution = {"state": "closed", "stop_reason": stop or ("checkpoint-reached" if checkpoint else "failure-observed"),
                 "console": {"effective_exit_code": 130, "process_exit_code": 130, "cancellation": "interrupt-requested"}}
    return seal_result({
        "format": "workbench-saved-check-result-v4", "state": outcome(execution, interpretation, error),
        "workspace_uri": "file:///pack", "selection_id": "selected", "attempt_id": "check-" + f"{index:032x}",
        "request_id": "request-" + str(index), "candidate_id": "candidate-" + str(index), "candidate": {"revision": "abc", "dirty": False},
        "image_id": "image-1", "provider": {"source": "policy-1"}, "execution": execution, "cleanup": {"state": "trashed"}, "error": error,
        "interpretation": interpretation, "diagnostics": group_findings(interpretation), "assertions": None, "evidence": [], "attempt_uri": "file:///retained", "limitations": [],
        "provenance": {"format": "workbench-check-provenance-v1", "source_labels": {"local_tags": [], "release_verified": False},
                       "runtime": {"pack": {"name": "fixture", "version": "1"}, "platform": {"id": "fixture", "version": "1"}, "dependencies": []},
                       "image": {"id": "image-1", "binding": {"dependencies": "same"}}, "host": {"environment_sha256": "same"},
                       "check": {"id": "fixture"}, "timeout_seconds": 600},
        "authority": {"source_mutated": False, "construction_authorized": False, "qualification_granted": False},
    })


def reseal(value):
    return seal_result({key: row for key, row in value.items() if key != "id"})


class CheckComparisonTests(unittest.TestCase):
    def test_repeated_events_group_without_losing_evidence_or_promoting_unknowns(self):
        first, second = finding(), finding()
        second["evidence"] = [{"log": "logs/latest.log", "line": 2}]
        baseline = sample(1, [first, second])
        candidate = sample(2, [first, second])
        self.assertEqual(len(baseline["diagnostics"]), 1)
        self.assertEqual(baseline["diagnostics"][0]["finding_indices"], [0, 1])
        self.assertEqual(len(baseline["diagnostics"][0]["evidence"]), 2)
        comparison = compare_results(baseline, candidate)
        self.assertEqual(validate_comparison(comparison), comparison)
        self.assertEqual(comparison["state"], "compared")
        self.assertEqual(comparison["counts"]["persistent"], 1)
        self.assertEqual(comparison["candidate"]["state"], "inconclusive")
        self.assertFalse(comparison["authority"]["outcomes_promoted"])

    def test_early_failure_exposes_new_error_without_claiming_later_errors_resolved(self):
        baseline = sample(1, [finding("Late baseline problem")])
        candidate = sample(2, [finding("Compiler failure", "compiler")], checkpoint=False)
        result = compare_results(baseline, candidate)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["counts"]["newly-observed"], 1)
        self.assertEqual(result["counts"]["not-comparable"], 1)
        self.assertEqual(result["counts"]["no-longer-observed"], 0)
        self.assertEqual(result["candidate"]["state"], "failed")

    def test_corrected_candidate_can_report_failure_no_longer_observed_without_causation(self):
        result = compare_results(sample(1, [finding("Script failure", "runtime")], checkpoint=False), sample(2))
        self.assertEqual(result["counts"]["no-longer-observed"], 1)
        self.assertEqual(result["state"], "partial")
        self.assertTrue(any("not proof" in text for text in result["limitations"]))

    def test_occurrence_deltas_require_equal_full_checkpoint_coverage(self):
        baseline = sample(1, [finding(), finding()])
        result = compare_results(baseline, sample(2, [finding()]))
        self.assertEqual(result["counts"]["occurrence-count-changed"], 1)
        result = compare_results(baseline, sample(3, [finding(), finding("stop", "runtime")], checkpoint=False))
        self.assertEqual(result["counts"]["occurrence-count-changed"], 0)

    def test_incompatible_images_policy_host_and_graphics_fail_comparability(self):
        baseline = sample(1, [finding()])
        mutations = [
            lambda x: x.update(provider={"source": "different"}),
            lambda x: x["provenance"]["host"].update(environment_sha256="different"),
            lambda x: x["provenance"].update(timeout_seconds=100),
            lambda x: x["interpretation"]["runtime_observations"].update(graphics=["other-gpu"]),
            lambda x: x["provenance"]["runtime"]["platform"].update(version="2"),
        ]
        for mutate in mutations:
            candidate = sample(2, [finding()]); mutate(candidate)
            result = compare_results(baseline, reseal(candidate))
            self.assertEqual(result["state"], "not-comparable")
            self.assertEqual(result["counts"]["persistent"], 0)
        candidate = sample(3); candidate["image_id"] = candidate["provenance"]["image"]["id"] = "image-2"
        self.assertEqual(compare_results(baseline, reseal(candidate))["state"], "not-comparable")

    def test_missing_evidence_cancel_timeout_and_closure_prevent_absence_claims(self):
        baseline = sample(1, [finding()])
        for candidate in (sample(2, complete=False), sample(3, stop="cancelled"), sample(4, stop="timed-out"), sample(5, error="failed collection")):
            result = compare_results(baseline, candidate)
            self.assertEqual(result["state"], "not-comparable")
            self.assertEqual(result["counts"]["no-longer-observed"], 0)

    def test_comparison_never_mutates_inputs_and_rejects_same_run_or_tampering(self):
        before, after = sample(1, [finding()]), sample(2, [finding()])
        original = deepcopy((before, after))
        compare_results(before, after)
        self.assertEqual((before, after), original)
        with self.assertRaisesRegex(ValueError, "distinct"):
            compare_results(before, before)
        after["diagnostics"][0]["occurrence_count"] = 0
        with self.assertRaises(ValueError):
            validate_result(after)

    def test_local_tags_and_saved_source_changes_do_not_claim_release_or_break_comparison(self):
        before, after = sample(1), sample(2)
        after["candidate"] = {"revision": "def", "dirty": True}
        after["provenance"]["source_labels"]["local_tags"] = ["latest"]
        self.assertEqual(compare_results(before, reseal(after))["state"], "compared")
        after["provenance"]["source_labels"]["release_verified"] = True
        with self.assertRaises(ValueError):
            reseal(after)
