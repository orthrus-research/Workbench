"""Core command handoff for the Textual fresh release policy review."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from workbench_core.pack_instance_cli import main


class PackInstanceFreshPolicyCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = Mock()
        self.service.policy_status.return_value = {
            "schema": "workbench.pack-release.fresh-setup.v1",
            "action": "policy-status", "status": "review_required",
            "input_plan_id": "input-id", "required_external_files": [
                {"project_id": 10, "file_id": 100},
            ], "optional_external_files": [
                {"project_id": 20, "file_id": 200},
            ],
        }
        self.service.plan_policy_review.return_value = {
            "schema": "workbench.pack-release.fresh-setup.v1",
            "action": "plan-policy-review", "status": "planned",
            "policy_plan": {"plan_id": "reviewed-id"},
        }
        self.service.apply_policy_review.return_value = {
            "schema": "workbench.pack-release.fresh-setup.v1",
            "action": "apply-policy-review", "status": "ready",
            "policy_plan_id": "reviewed-id",
        }
        self.service.reopen_pending_policy.return_value = {
            "schema": "workbench.pack-release.fresh-setup.v1",
            "action": "reopen-pending-policy", "status": "ready",
            "policy_plan_id": "reviewed-id",
        }
        provider = patch(
            "workbench_core.pack_instance_cli._fresh_service",
            return_value=self.service,
        )
        provider.start()
        self.addCleanup(provider.stop)

    def _call(self, *arguments: str) -> dict:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main([
                *arguments, "--profile", "supersymmetry", "--json",
            ]))
        return json.loads(output.getvalue())

    def test_status_plan_apply_and_reopen_use_exact_reviewed_pairs(self) -> None:
        status = self._call("fresh-policy-status")
        self.assertEqual("review_required", status["policy"]["status"])
        self.assertEqual(10, status["policy"]["required_external_files"][0]["project_id"])
        plan = self._call(
            "fresh-policy-plan", "--resourcepack-pair", "10:100",
            "--resourcepack-pair", "11:101", "--optional-pair", "20:200",
        )
        self.assertEqual("reviewed-id", plan["policy"]["policy_plan"]["plan_id"])
        self.service.plan_policy_review.assert_called_once_with(
            resourcepack_pairs=((10, 100), (11, 101)),
            optional_selected=((20, 200),),
        )
        applied = self._call(
            "fresh-policy-apply", "--resourcepack-pair", "10:100",
            "--optional-pair", "20:200",
            "--expected-policy-plan-id", "reviewed-id",
        )
        self.assertEqual("ready", applied["policy"]["status"])
        self.service.apply_policy_review.assert_called_once_with(
            resourcepack_pairs=((10, 100),),
            optional_selected=((20, 200),),
            expected_plan_id="reviewed-id",
        )
        reopened = self._call("fresh-policy-reopen")
        self.assertEqual("reviewed-id", reopened["policy"]["policy_plan_id"])
        self.service.reopen_pending_policy.assert_called_once_with()

    def test_policy_options_reject_missing_or_wrong_action(self) -> None:
        invalid = (
            ("fresh-policy-plan",),
            ("fresh-policy-plan", "--resourcepack-pair", "0:1"),
            ("fresh-policy-plan", "--resourcepack-pair", "10:100",
             "--expected-policy-plan-id", "unexpected"),
            ("fresh-policy-apply", "--resourcepack-pair", "10:100"),
            ("fresh-policy-status", "--optional-pair", "20:200"),
            ("fresh-policy-reopen", "--resourcepack-pair", "10:100"),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
                    main([*arguments, "--profile", "supersymmetry", "--json"])
                self.assertEqual(2, raised.exception.code)
        self.service.plan_policy_review.assert_not_called()
        self.service.apply_policy_review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
