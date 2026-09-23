"""Owner-bound translation tests for proposed recipe Atlas assessment."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_shell import atlas_recipe_plan_assessment as subject  # noqa: E402


PLAN_ID = "workbench-supersymmetry-recipe-change-plan:sha256:" + "1" * 64


def _plan() -> dict:
    return {
        "id": PLAN_ID,
        "format": "workbench-supersymmetry-recipe-change-plan-v1",
        "kind": "workbench-supersymmetry-recipe-change-plan",
        "request": {
            "mutation": "add",
            "recipe_map": "MIXER",
            "script": "groovy/postInit/Probe.groovy",
            "duration": 60,
            "voltage_tier": "LV",
            "item_inputs": [{"kind": "ore", "name": "dustCopper", "amount": 1}],
            "fluid_inputs": [{"name": "water", "amount": 1000}],
            "item_outputs": [],
            "fluid_outputs": [{"name": "copper_sulfate_solution", "amount": 1000}],
        },
        "authority_bindings": {
            "recipe_map": {"alias": "MIXER", "registry_name": "mixer"}
        },
        "profile_context": {
            "cleanroom_version": "0.6.8-alpha",
            "pack_profile_id": "workbench-pack:supersymmetry",
            "platform_profile_id": "workbench-platform:cleanroom:provisional",
        },
    }


class AtlasRecipePlanAssessmentTests(unittest.TestCase):
    def test_translation_uses_only_the_owner_verified_current_plan(self) -> None:
        raw = {"untrusted": True}
        authority = mock.Mock()
        authority.validate_recipe_change_plan.return_value = _plan()
        authority.verify_recipe_change_plan.return_value = {
            "plan_id": PLAN_ID, "state": "ready", "reason": None
        }
        with mock.patch.object(
            subject, "recipe_change_authority", return_value=authority
        ), mock.patch.object(
            subject, "resolve_feature_record", return_value=raw
        ) as resolved:
            proposal = subject.recipe_change_proposal(
                ROOT, "/state", PLAN_ID
            )

        resolved.assert_called_once_with("/state", "plans", PLAN_ID)
        authority.validate_recipe_change_plan.assert_called_once_with(raw)
        authority.verify_recipe_change_plan.assert_called_once_with(ROOT, _plan())
        self.assertEqual(PLAN_ID, proposal["proposal_id"])
        self.assertEqual("mixer", proposal["recipe_map"])
        self.assertNotIn("script", proposal)
        self.assertEqual(
            [{"name": "copper_sulfate_solution", "amount": 1000}],
            proposal["fluid_outputs"],
        )

    def test_assessment_opens_one_explicit_graph_once(self) -> None:
        view = mock.MagicMock()
        view.describe.return_value = {
            "scope": {
                "pack_profile_id": "workbench-pack:supersymmetry",
                "platform_profile_id": "workbench-platform:cleanroom:provisional",
                "platform_candidate": "cleanroom-0.6.8-alpha",
            }
        }
        opened = mock.MagicMock()
        opened.__enter__.return_value = view
        proposal = {"proposal_id": PLAN_ID}
        report = {"format": "workbench-atlas-proposed-recipe-assessment-v1"}
        authority = mock.Mock()
        authority.validate_recipe_change_plan.return_value = _plan()
        authority.verify_recipe_change_plan.return_value = {
            "plan_id": PLAN_ID, "state": "ready", "reason": None
        }
        with mock.patch.object(
            subject, "resolve_feature_record", return_value={"retained": True}
        ), mock.patch.object(
            subject, "recipe_change_authority", return_value=authority
        ), mock.patch.object(
            subject, "_proposal_from_plan", return_value=proposal
        ), mock.patch.object(
            subject, "open_recipe_health", return_value=opened
        ) as graph_open, mock.patch.object(
            subject, "assess_proposed_recipe", return_value=report
        ) as assessed:
            actual = subject.assess_recipe_change_plan(
                ROOT,
                "/graph",
                "/state",
                PLAN_ID,
                max_depth=3,
                max_nodes=100,
            )

        self.assertEqual(report, actual)
        authority.verify_recipe_change_plan.assert_called_once_with(ROOT, _plan())
        graph_open.assert_called_once_with(Path("/graph"))
        assessed.assert_called_once_with(
            view, proposal, max_depth=3, max_nodes=100
        )
        opened.__exit__.assert_called_once()

    def test_assessment_rejects_a_different_pack_or_platform_graph(self) -> None:
        view = mock.MagicMock()
        view.describe.return_value = {
            "scope": {
                "pack_profile_id": "workbench-pack:different-pack",
                "platform_profile_id": "workbench-platform:cleanroom:provisional",
                "platform_candidate": "cleanroom-0.6.8-alpha",
            }
        }
        opened = mock.MagicMock()
        opened.__enter__.return_value = view
        authority = mock.Mock()
        authority.validate_recipe_change_plan.return_value = _plan()
        authority.verify_recipe_change_plan.return_value = {
            "plan_id": PLAN_ID, "state": "ready", "reason": None
        }
        with mock.patch.object(
            subject, "resolve_feature_record", return_value={"retained": True}
        ), mock.patch.object(
            subject, "recipe_change_authority", return_value=authority
        ), mock.patch.object(subject, "open_recipe_health", return_value=opened):
            with self.assertRaisesRegex(
                subject.DeveloperFeatureError,
                "graph pack/platform scope does not match",
            ):
                subject.assess_recipe_change_plan(
                    ROOT,
                    "/graph",
                    "/state",
                    PLAN_ID,
                )

        opened.__exit__.assert_called_once()

    def test_stale_plan_is_refused_before_the_graph_is_opened(self) -> None:
        authority = mock.Mock()
        authority.validate_recipe_change_plan.return_value = _plan()
        authority.verify_recipe_change_plan.return_value = {
            "plan_id": PLAN_ID,
            "state": "stale",
            "reason": "plan regenerated differently from current bytes",
        }
        with mock.patch.object(
            subject, "resolve_feature_record", return_value={"retained": True}
        ), mock.patch.object(
            subject, "recipe_change_authority", return_value=authority
        ), mock.patch.object(subject, "open_recipe_health") as opened:
            with self.assertRaisesRegex(subject.DeveloperFeatureError, "stale"):
                subject.assess_recipe_change_plan(ROOT, "/graph", "/state", PLAN_ID)
        opened.assert_not_called()

    def test_verifier_cannot_substitute_another_plan_identity(self) -> None:
        authority = mock.Mock()
        authority.validate_recipe_change_plan.return_value = _plan()
        authority.verify_recipe_change_plan.return_value = {
            "plan_id": "another-plan", "state": "ready", "reason": None
        }
        with mock.patch.object(
            subject, "resolve_feature_record", return_value={"retained": True}
        ), mock.patch.object(
            subject, "recipe_change_authority", return_value=authority
        ), mock.patch.object(subject, "open_recipe_health") as opened:
            with self.assertRaisesRegex(subject.DeveloperFeatureError, "stale"):
                subject.assess_recipe_change_plan(ROOT, "/graph", "/state", PLAN_ID)
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
