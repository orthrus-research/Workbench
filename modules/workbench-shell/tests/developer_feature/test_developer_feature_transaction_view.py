"""Tests for the compact current-state developer-feature transaction view."""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema.validators import validator_for


ROOT = Path(__file__).resolve().parents[4]
for source in sorted((ROOT / "modules").glob("*/src")):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from test_developer_feature import _checkout as material_checkout  # noqa: E402
from test_developer_source_feature import _checkout as source_checkout  # noqa: E402
from workbench_shell.developer_feature import (  # noqa: E402
    DeveloperFeatureError,
    build_material_fluid_recipe_plan,
    retain_feature_record,
    transaction_state_root,
)
from workbench_shell.developer_feature_cli import main as feature_main  # noqa: E402
from workbench_shell.developer_feature_presentation import (  # noqa: E402
    discover_feature_records,
)
from workbench_blueprints.profile_construction import recipe_change_authority
from workbench_shell.developer_source_feature import (  # noqa: E402
    build_quest_for_process_plan,
)
from workbench_shell.developer_feature_transaction_view import (  # noqa: E402
    FORMAT,
    _current_state,
    build_feature_transaction_view,
    validate_feature_transaction_view,
)


class DeveloperFeatureTransactionViewTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        checkout_parent = self.root / "material"
        checkout_parent.mkdir()
        self.workspace = material_checkout(checkout_parent)
        self.state = self.root / "state"
        self.plan = build_material_fluid_recipe_plan(
            ROOT,
            self.workspace,
            name="Transaction View Solvent",
            color="#2266aa",
            translation="Transaction View Solvent",
            symbol="TransactionViewSolvent",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="MIXER",
            input_fluid="steam",
            input_amount=1000,
            output_amount=1000,
            duration=80,
            voltage_tier="LV",
        )
        retain_feature_record(self.state, "plans", self.plan)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _command(self, *argv: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = feature_main([*argv, "--json"], suite_root=ROOT)
        return status, json.loads(output.getvalue())

    @staticmethod
    def _reseal(value: dict) -> dict:
        body = dict(value)
        body.pop("id", None)
        return {
            **body,
            "id": (
                "workbench-developer-feature-transaction-view:sha256:"
                + sha256(
                    json.dumps(
                        body,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
            ),
        }

    def test_plan_apply_and_rollback_have_distinct_effective_states(self) -> None:
        planned = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual(FORMAT, planned["format"])
        self.assertEqual("planned", planned["current_effective_state"])
        self.assertEqual("matches-before", planned["workspace_match"]["state"])
        self.assertEqual(["plans"], [row["collection"] for row in planned["records"]])
        self.assertTrue(planned["actions"][1]["available"])
        self.assertFalse(planned["actions"][2]["available"])
        self.assertNotIn("before_base64", planned["operations"][0])
        self.assertLess(len(json.dumps(planned)), 100_000)
        schema = json.loads(
            (
                ROOT
                / "modules/workbench-shell/schemas/developer-feature-transaction-view-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(planned)
        catalog = discover_feature_records(
            ROOT,
            self.state,
            family="material-fluid-recipe",
        )
        self.assertTrue(
            set(catalog["limitations"]).issubset(set(planned["limitations"]))
        )
        status, projected = self._command(
            "transaction",
            "material-fluid-recipe",
            self.plan["id"],
            "--state-root",
            str(self.state),
        )
        self.assertEqual(0, status)
        self.assertEqual(planned, projected)
        human = io.StringIO()
        with redirect_stdout(human):
            status = feature_main(
                [
                    "transaction",
                    "material-fluid-recipe",
                    self.plan["id"],
                    "--state-root",
                    str(self.state),
                ],
                suite_root=ROOT,
            )
        self.assertEqual(0, status)
        self.assertIn("Developer-feature transaction: PLANNED", human.getvalue())
        self.assertIn("Reviewed operations:", human.getvalue())
        self.assertIn("Eligible next actions", human.getvalue())

        status, receipt = self._command(
            "apply",
            "material-fluid-recipe",
            self.plan["id"],
            "--consent",
            self.plan["id"],
            "--state-root",
            str(self.state),
        )
        self.assertEqual(0, status)
        applied = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual("applied", applied["current_effective_state"])
        self.assertEqual("matches-after", applied["workspace_match"]["state"])
        self.assertEqual("stale", applied["plan_freshness"]["state"])
        self.assertTrue(applied["actions"][2]["available"])
        self.assertEqual(receipt["id"], applied["actions"][2]["record_id"])

        status, rollback = self._command(
            "rollback",
            "material-fluid-recipe",
            self.plan["id"],
            receipt["id"],
            "--state-root",
            str(self.state),
        )
        self.assertEqual(0, status)
        restored = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual("restored", restored["current_effective_state"])
        self.assertEqual("matches-before", restored["workspace_match"]["state"])
        self.assertEqual("ready", restored["plan_freshness"]["state"])
        self.assertEqual(
            ["plans", "receipts", "rollbacks"],
            [row["collection"] for row in restored["records"]],
        )
        self.assertIn(
            receipt["id"],
            [row["record_id"] for row in restored["records"]],
        )
        self.assertIn(
            rollback["id"],
            [row["record_id"] for row in restored["records"]],
        )
        self.assertTrue(restored["actions"][1]["available"])
        self.assertFalse(restored["actions"][2]["available"])

    def test_mixed_and_drifted_bytes_are_not_actionable(self) -> None:
        status, _receipt = self._command(
            "apply",
            "material-fluid-recipe",
            self.plan["id"],
            "--consent",
            self.plan["id"],
            "--state-root",
            str(self.state),
        )
        self.assertEqual(0, status)
        first = self.plan["operations"][0]
        target = self.workspace / first["path"]
        target.write_bytes(
            __import__("base64").b64decode(first["before_base64"], validate=True)
        )
        mixed = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual("mixed", mixed["current_effective_state"])
        self.assertFalse(mixed["actions"][1]["available"])
        self.assertFalse(mixed["actions"][2]["available"])

        target.write_bytes(b"not either reviewed state\n")
        drifted = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual("drifted", drifted["current_effective_state"])

    def test_validator_rejects_resealed_effective_state_lie(self) -> None:
        value = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        changed = deepcopy(value)
        changed["current_effective_state"] = "applied"
        changed = self._reseal(changed)
        # Shape validation alone cannot establish live bytes, but impossible
        # effective/match combinations must still be rejected.
        with self.assertRaises(DeveloperFeatureError):
            validate_feature_transaction_view(changed)

    def test_validator_rejects_cross_family_retained_kind(self) -> None:
        value = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        changed = deepcopy(value)
        row = changed["records"][0]
        row["record_kind"] = "workbench-developer-source-feature-plan"
        row["record_id"] = (
            "workbench-developer-source-feature-plan:sha256:" + "0" * 64
        )
        row["reference"] = row["record_id"]
        changed = self._reseal(changed)
        with self.assertRaises(DeveloperFeatureError):
            validate_feature_transaction_view(changed)

    def test_interrupted_transaction_suppresses_other_mutating_actions(self) -> None:
        transaction = transaction_state_root(self.state, self.plan["id"])
        active = transaction / "active-transaction.json"
        active.write_text("{}\n", encoding="utf-8")
        planned = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual("interrupted", planned["current_effective_state"])
        self.assertFalse(planned["actions"][1]["available"])
        self.assertTrue(planned["actions"][3]["available"])
        active.unlink()

        status, _receipt = self._command(
            "apply",
            "material-fluid-recipe",
            self.plan["id"],
            "--consent",
            self.plan["id"],
            "--state-root",
            str(self.state),
        )
        self.assertEqual(0, status)
        active.write_text("{}\n", encoding="utf-8")
        applied = build_feature_transaction_view(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            plan_reference=self.plan["id"],
        )
        self.assertEqual("interrupted", applied["current_effective_state"])
        self.assertFalse(applied["actions"][2]["available"])
        self.assertTrue(applied["actions"][3]["available"])

    def test_automatic_partial_failure_rollback_is_currently_restored(self) -> None:
        state = _current_state(
            {"state": "matches-before"},
            [
                {
                    "collection": "receipts",
                    "diagnostic_code": "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
                    "record_state": "rejected",
                }
            ],
        )
        self.assertEqual("restored", state)

    def test_external_plan_path_requires_retention_in_selected_state(self) -> None:
        digest = self.plan["id"].rsplit(":", 1)[1]
        plan_path = self.state / "plans" / digest / "record.json"
        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "plan is not retained in the selected feature state root",
        ):
            build_feature_transaction_view(
                ROOT,
                self.root / "other-state",
                family="material-fluid-recipe",
                plan_reference=plan_path,
            )

    def test_all_three_owner_families_build_compact_schema_valid_views(self) -> None:
        recipe = recipe_change_authority("supersymmetry").build_recipe_change_plan(
            ROOT,
            self.workspace,
            mutation="add",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="MIXER",
            item_inputs=[],
            fluid_inputs=[{"name": "steam", "amount": 1000}],
            item_outputs=[],
            fluid_outputs=[{"name": "distilled_water", "amount": 1000}],
            duration=40,
            voltage_tier="LV",
        )
        retain_feature_record(self.state, "plans", recipe)

        source_parent = self.root / "source"
        source_parent.mkdir()
        source_workspace = source_checkout(source_parent)
        quest = build_quest_for_process_plan(
            ROOT,
            source_workspace,
            quest_id=100,
            add_prerequisite_id=300,
            requirement_type="IMPLICIT",
        )
        retain_feature_record(self.state, "plans", quest)

        schema = json.loads(
            (
                ROOT
                / "modules/workbench-shell/schemas/developer-feature-transaction-view-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = validator_for(schema)(schema)
        for family, plan in (
            ("material-fluid-recipe", self.plan),
            ("recipe-change", recipe),
            ("quest-for-process", quest),
        ):
            with self.subTest(family=family):
                value = build_feature_transaction_view(
                    ROOT,
                    self.state,
                    family=family,
                    plan_reference=plan["id"],
                )
                validator.validate(value)
                self.assertEqual("planned", value["current_effective_state"])
                self.assertLess(len(json.dumps(value)), 100_000)


if __name__ == "__main__":
    unittest.main()
