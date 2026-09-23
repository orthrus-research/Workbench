"""Final check outcomes follow classified evidence, not a raw finding count."""

import unittest

from workbench_api.checks import observation
from workbench_crucible.developer_checks import outcome, validate_interpretation


class SavedCheckResultTests(unittest.TestCase):
    def interpretation(self, category):
        blocking = category == "compiler"
        unknown = category == "unclassified"
        reference = {"log": "logs/example.log", "line": 1}
        return {
            "outcome": "failed" if blocking else "inconclusive" if unknown else "completed",
            "observation": observation("failure" if blocking else "checkpoint", "owner-stage", "Observed", [reference]),
            "checks": [],
            "findings": [{"category": category, "severity": "error" if blocking or unknown else "information", "blocking": blocking, "evidence": [reference]}],
            "findings_count": 1,
            "blocking_findings_count": int(blocking),
            "unclassified_findings_count": int(unknown),
            "truncated": False,
            "complete_logs": True,
            "runtime_observations": {"graphics": [], "complete": True},
        }

    def execution(self, reason="checkpoint-reached"):
        return {"state": "closed", "stop_reason": reason, "console": {"effective_exit_code": 130, "process_exit_code": -2, "cancellation": "interrupt-requested"}}

    def test_informational_findings_are_not_automatically_failures(self):
        value = self.interpretation("informational")
        validate_interpretation(value)
        self.assertEqual(outcome(self.execution(), value, None), "completed")

    def test_unknown_diagnostics_cannot_be_promoted_to_success(self):
        value = self.interpretation("unclassified")
        validate_interpretation(value)
        self.assertEqual(outcome(self.execution(), value, None), "inconclusive")
        with self.assertRaisesRegex(ValueError, "disagrees"):
            validate_interpretation({**value, "outcome": "completed"})
        with self.assertRaisesRegex(ValueError, "counts disagree"):
            validate_interpretation({**value, "unclassified_findings_count": 0})

    def test_terminal_observation_stop_and_user_cancellation_are_distinct(self):
        value = self.interpretation("compiler")
        validate_interpretation(value)
        self.assertEqual(outcome(self.execution("failure-observed"), value, None), "failed")
        for reason in ("cancelled", "timed-out"):
            self.assertEqual(outcome(self.execution(reason), value, None), reason)

    def test_missing_final_failure_evidence_does_not_reinterpret_stop_as_pass(self):
        self.assertEqual(outcome(self.execution("failure-observed"), self.interpretation("informational"), None), "inconclusive")

    def test_nonzero_process_exit_and_unverified_closure_still_prevent_success(self):
        value = self.interpretation("informational")
        execution = self.execution()
        execution["console"]["process_exit_code"] = 1
        self.assertEqual(outcome(execution, value, None), "failed")
        self.assertEqual(outcome({"state": "closure-unverified"}, value, None), "inconclusive")
