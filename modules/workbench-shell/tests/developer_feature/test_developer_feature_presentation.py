"""Tests for the common, owner-validated IDE feature projection."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
)
from workbench_shell.developer_feature_cli import main as feature_main  # noqa: E402
from workbench_shell.developer_feature_presentation import (  # noqa: E402
    FORMAT,
    FORMAT_V2,
    RECORD_CATALOG_FORMAT,
    discover_feature_records,
    present_feature_record,
    validate_feature_record_catalog,
    validate_feature_presentation,
)
from workbench_shell import developer_feature_presentation as presentation  # noqa: E402
from workbench_blueprints.profile_construction import recipe_change_authority
from workbench_shell.developer_recipe_runtime import (  # noqa: E402
    RecipeRuntimeComparisonError,
)
from workbench_shell.developer_source_feature import (  # noqa: E402
    build_quest_for_process_plan,
)
from workbench_shell.developer_feature_workflow_views import (  # noqa: E402
    COMPACT_PLAN_FORMAT,
    OPTIONS_FORMAT,
    validate_compact_plan_result,
    validate_recipe_options_projection,
)


class DeveloperFeaturePresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / ".workbench/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        material_parent = self.root / "material"
        material_parent.mkdir()
        source_parent = self.root / "source"
        source_parent.mkdir()
        self.material = material_checkout(material_parent)
        self.source = source_checkout(source_parent)
        self.state = self.root / "state"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _material_plan(self) -> dict:
        return build_material_fluid_recipe_plan(
            ROOT,
            self.material,
            name="IDE Solvent",
            color="#2266aa",
            translation="IDE Solvent",
            symbol="IdeSolvent",
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="MIXER",
            input_fluid="steam",
            input_amount=1000,
            output_amount=1000,
            duration=80,
            voltage_tier="LV",
        )

    def _recipe_plan(self) -> dict:
        authority = recipe_change_authority("supersymmetry")
        return authority.build_recipe_add_plan(
            ROOT,
            self.source,
            recipe_script="groovy/postInit/chemistry/Probe.groovy",
            recipe_map="BR",
            item_inputs=[{"kind": "ore", "name": "dustSulfur", "amount": 2}],
            fluid_inputs=[{"name": "water", "amount": 1000}],
            fluid_outputs=[{"name": "sulfuric_acid", "amount": 1000}],
            duration=100,
            voltage_tier="LV",
        )

    def _quest_plan(self) -> dict:
        return build_quest_for_process_plan(
            ROOT,
            self.source,
            quest_id=100,
            add_prerequisite_id=300,
            requirement_type="IMPLICIT",
            title="IDE Quest",
        )

    def test_all_three_owner_plans_project_through_one_exact_shape(self) -> None:
        cases = (
            ("material-fluid-recipe", self._material_plan()),
            ("recipe-change", self._recipe_plan()),
            ("quest-for-process", self._quest_plan()),
        )
        for family, plan in cases:
            with self.subTest(family=family):
                retained = retain_feature_record(self.state, "plans", plan)
                result = present_feature_record(
                    ROOT,
                    self.state,
                    family=family,
                    collection="plans",
                    reference=plan["id"],
                )
                self.assertEqual(FORMAT, result["format"])
                self.assertEqual(family, result["family"])
                self.assertEqual(plan["id"], result["plan_id"])
                self.assertEqual(retained.as_uri(), result["owner_record"]["uri"])
                self.assertEqual("ready", result["verification"]["state"])
                self.assertEqual(
                    [row["path"] for row in plan["operations"]],
                    [row["path"] for row in result["operations"]],
                )
                apply = next(
                    row for row in result["actions"] if row["action"] == "apply"
                )
                self.assertIs(apply["available"], True)
                self.assertEqual(plan["id"], apply["consent_id"])
                run = next(
                    row for row in result["actions"] if row["action"] == "run"
                )
                self.assertEqual(
                    family in {"material-fluid-recipe", "recipe-change"},
                    run["available"],
                )
                self.assertEqual(result, validate_feature_presentation(result))
                schema = json.loads(
                    (
                        ROOT
                        / "modules/workbench-shell/schemas/developer-feature-presentation-v1.schema.json"
                    ).read_text(encoding="utf-8")
                )
                validator = validator_for(schema)
                validator.check_schema(schema)
                validator(schema).validate(result)

    def test_all_three_direct_planners_offer_compact_machine_results(self) -> None:
        cases = (
            (
                "material-fluid-recipe",
                [
                    "plan",
                    "material-fluid-recipe",
                    str(self.material),
                    "--name",
                    "Compact Solvent",
                    "--color",
                    "2266aa",
                    "--recipe-script",
                    "groovy/postInit/chemistry/Probe.groovy",
                    "--recipe-map",
                    "MIXER",
                    "--input-fluid",
                    "steam",
                ],
            ),
            (
                "recipe-change",
                [
                    "plan",
                    "recipe-change",
                    str(self.source),
                    "--recipe-script",
                    "groovy/postInit/chemistry/Probe.groovy",
                    "--recipe-map",
                    "BR",
                    "--item-input",
                    '{"kind":"ore","name":"dustSulfur","amount":2}',
                    "--fluid-input",
                    '{"name":"water","amount":1000}',
                    "--fluid-output",
                    '{"name":"sulfuric_acid","amount":1000}',
                    "--duration",
                    "100",
                    "--voltage-tier",
                    "LV",
                ],
            ),
            (
                "quest-for-process",
                [
                    "plan",
                    "quest-for-process",
                    str(self.source),
                    "--quest-id",
                    "100",
                    "--add-prerequisite-id",
                    "300",
                    "--title",
                    "Compact Quest",
                ],
            ),
        )
        for family, arguments in cases:
            with self.subTest(family=family):
                output = io.StringIO()
                errors = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    status = feature_main(
                        [
                            *arguments,
                            "--state-root",
                            str(self.state),
                            "--compact-json",
                        ],
                        suite_root=ROOT,
                    )
                self.assertEqual(0, status, errors.getvalue())
                rendered = output.getvalue()
                self.assertNotIn("base64", rendered.casefold())
                result = json.loads(rendered)
                self.assertEqual(COMPACT_PLAN_FORMAT, result["format"])
                self.assertEqual(family, result["family"])
                self.assertEqual("ready", result["state"])
                self.assertEqual("planned", result["transaction"]["current_effective_state"])
                self.assertEqual(
                    len(result["review"]["changed_files"]),
                    result["review"]["operation_count"],
                )
                retained = Path(
                    result["retained_plan_uri"].removeprefix("file://")
                )
                self.assertTrue(retained.is_file())
                self.assertEqual(result, validate_compact_plan_result(result))
                schema = json.loads(
                    (
                        ROOT
                        / "modules/workbench-shell/schemas"
                        / "developer-feature-compact-plan-result-v1.schema.json"
                    ).read_text(encoding="utf-8")
                )
                validator = validator_for(schema)
                validator.check_schema(schema)
                validator(schema).validate(result)

    def test_recipe_option_filters_are_bounded_shell_projections(self) -> None:
        cases = (
            ("material-fluid-recipe", self.material, "batch", "recipe_maps"),
            ("recipe-change", self.source, "probe", "scripts"),
        )
        for family, workspace, query, populated in cases:
            with self.subTest(family=family):
                output = io.StringIO()
                errors = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    status = feature_main(
                        [
                            "options",
                            family,
                            str(workspace),
                            "--query",
                            query,
                            "--limit",
                            "1",
                            "--json",
                        ],
                        suite_root=ROOT,
                    )
                self.assertEqual(0, status, errors.getvalue())
                result = json.loads(output.getvalue())
                self.assertEqual(OPTIONS_FORMAT, result["format"])
                self.assertEqual(1, result["limit"])
                self.assertEqual(1, result["counts"][populated]["returned"])
                self.assertEqual(
                    result, validate_recipe_options_projection(result)
                )
                schema = json.loads(
                    (
                        ROOT
                        / "modules/workbench-shell/schemas"
                        / "developer-feature-options-projection-v1.schema.json"
                    ).read_text(encoding="utf-8")
                )
                validator = validator_for(schema)
                validator.check_schema(schema)
                validator(schema).validate(result)

        human = io.StringIO()
        with redirect_stdout(human), redirect_stderr(io.StringIO()):
            status = feature_main(
                [
                    "options",
                    "recipe-change",
                    str(self.source),
                    "--query",
                    "probe",
                    "--limit",
                    "1",
                ],
                suite_root=ROOT,
            )
        self.assertEqual(0, status)
        self.assertIn("1 of 1 matches", human.getvalue())
        self.assertIn("total", human.getvalue())

    def test_cli_emits_projection_and_tampering_breaks_identity(self) -> None:
        plan = self._material_plan()
        retain_feature_record(self.state, "plans", plan)
        output = io.StringIO()
        with redirect_stdout(output):
            status = feature_main(
                [
                    "present",
                    "material-fluid-recipe",
                    "plans",
                    plan["id"],
                    "--state-root",
                    str(self.state),
                    "--json",
                ],
                suite_root=ROOT,
            )
        self.assertEqual(0, status)
        result = json.loads(output.getvalue())
        self.assertEqual(plan["id"], result["plan_id"])

        tampered = deepcopy(result)
        tampered["runtime"]["action_available"] = False
        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "identity or shape changed",
        ):
            validate_feature_presentation(tampered)

    def test_v2_run_projection_exposes_common_per_side_diagnostics(self) -> None:
        plan = self._recipe_plan()
        retain_feature_record(self.state, "plans", plan)
        base = present_feature_record(
            ROOT,
            self.state,
            family="recipe-change",
            collection="plans",
            reference=plan["id"],
        )
        digit = "a" * 64
        record_id = (
            "workbench-developer-recipe-change-runtime-comparison:sha256:" + digit
        )
        evidence = {
            "final_launch_receipt": {
                "id": "sha256:" + digit,
                "sha256": digit,
                "size": 12,
                "uri": (self.root / "runtime-launch-v3.json").as_uri(),
            },
            "groovy_log": {
                "sha256": digit,
                "size": 42,
                "uri": (self.root / "minecraft-groovy.log").as_uri(),
            },
            "runtime_session_receipt": {
                "id": "sha256:" + digit,
                "sha256": digit,
                "size": 24,
                "uri": (self.root / "runtime-observation-v1.json").as_uri(),
            },
        }
        owner = {
            "id": record_id,
            "limitations": [
                "Runtime comparison is client-only.",
                "The observer decoder digest is required for reopening.",
            ],
            "outcome": "failed",
            "sides": {
                "baseline": {
                    "assessment": None,
                    "error": {
                        "kind": "MarkerError",
                        "message": "marker missing",
                        "phase": "assertion",
                    },
                    "outcome": "failed",
                    "probe": {
                        "overlay_id": "overlay-baseline",
                        "overlay_spec_uri": (self.root / "overlay-v1.json").as_uri(),
                        "probe_id": "probe-baseline",
                        "script_uri": (self.root / "Probe.groovy").as_uri(),
                    },
                    "retained_evidence": evidence,
                    "runtime": {"state": "observed"},
                    "state": "incomplete",
                },
                "candidate": {
                    "assessment": None,
                    "error": {
                        "kind": "PriorSideFailed",
                        "message": "not run",
                        "phase": "blocked",
                    },
                    "outcome": "not-run",
                    "probe": None,
                    "retained_evidence": None,
                    "runtime": {"state": "not-run"},
                    "state": "incomplete",
                },
            },
            "state": "incomplete",
        }
        body = deepcopy(base)
        body.pop("id")
        body.update(
            {
                "actions": [],
                "collection": "runs",
                "format": FORMAT_V2,
                "limitations": presentation._limitations_projection(
                    plan, owner, include_owner=True
                ),
                "owner_record": {
                    "diagnostic_code": None,
                    "id": record_id,
                    "kind": "workbench-developer-recipe-change-runtime-comparison",
                    "state": "incomplete",
                    "uri": (self.root / "receipt.json").as_uri(),
                },
                "runtime": presentation._runtime_projection(
                    "recipe-change", plan, "runs", owner
                ),
                "schema_version": 2,
            }
        )
        result = presentation._seal(body)

        self.assertEqual(result, validate_feature_presentation(result))
        baseline, candidate = result["runtime"]["sides"]
        self.assertEqual("baseline", baseline["role"])
        self.assertEqual("assertion", baseline["error"]["phase"])
        self.assertEqual(
            evidence["final_launch_receipt"]["uri"],
            baseline["receipt"]["final_launch"]["uri"],
        )
        self.assertEqual("probe-baseline", baseline["probe"]["id"])
        self.assertEqual("candidate", candidate["role"])
        self.assertIsNone(candidate["receipt"])
        self.assertIsNone(candidate["probe"])
        self.assertIn("Runtime comparison is client-only.", result["limitations"])
        self.assertIn(
            "The observer decoder digest is required for reopening.",
            result["limitations"],
        )
        schema = json.loads(
            (
                ROOT
                / "modules/workbench-shell/schemas/developer-feature-presentation-v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(result)

        legacy_owner = deepcopy(owner)
        for side in legacy_owner["sides"].values():
            side.pop("retained_evidence")
            side["error"].pop("phase")
        legacy_runtime = presentation._runtime_projection(
            "recipe-change", plan, "runs", legacy_owner
        )
        self.assertNotIn("sides", legacy_runtime)

    def test_discovery_lists_all_owner_records_and_filters_exactly(self) -> None:
        plans = (
            ("material-fluid-recipe", self._material_plan()),
            ("recipe-change", self._recipe_plan()),
            ("quest-for-process", self._quest_plan()),
        )
        for _family, plan in plans:
            retain_feature_record(self.state, "plans", plan)

        result = discover_feature_records(ROOT, self.state)
        self.assertEqual(RECORD_CATALOG_FORMAT, result["format"])
        self.assertEqual(3, len(result["records"]))
        self.assertEqual(
            ["material-fluid-recipe", "quest-for-process", "recipe-change"],
            [row["family"] for row in result["records"]],
        )
        self.assertTrue(all(row["collection"] == "plans" for row in result["records"]))
        self.assertTrue(all(row["reference"] == row["record_id"] for row in result["records"]))
        self.assertEqual(result, validate_feature_record_catalog(result))

        filtered = discover_feature_records(
            ROOT,
            self.state,
            family="recipe-change",
            collection="plans",
        )
        self.assertEqual(1, len(filtered["records"]))
        self.assertEqual("recipe-change", filtered["records"][0]["family"])
        self.assertEqual(
            {"collection": "plans", "family": "recipe-change"},
            filtered["filters"],
        )

        schema = json.loads(
            (
                ROOT
                / "modules/workbench-shell/schemas/developer-feature-record-catalog-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(result)

        tampered = deepcopy(result)
        tampered["records"][0]["operation_count"] = 999
        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "identity or shape changed",
        ):
            validate_feature_record_catalog(tampered)

    def test_records_cli_and_empty_state_are_bounded(self) -> None:
        empty = discover_feature_records(ROOT, self.state)
        self.assertEqual([], empty["records"])
        plan = self._material_plan()
        retain_feature_record(self.state, "plans", plan)
        output = io.StringIO()
        with redirect_stdout(output):
            status = feature_main(
                [
                    "records",
                    "material-fluid-recipe",
                    "plans",
                    "--state-root",
                    str(self.state),
                    "--json",
                ],
                suite_root=ROOT,
            )
        self.assertEqual(0, status)
        result = json.loads(output.getvalue())
        self.assertEqual([plan["id"]], [row["record_id"] for row in result["records"]])

    def test_discovery_omits_only_historical_runtime_protocols(self) -> None:
        plan = self._recipe_plan()
        retain_feature_record(self.state, "plans", plan)
        plan_presentation = present_feature_record(
            ROOT,
            self.state,
            family="recipe-change",
            collection="plans",
            reference=plan["id"],
        )

        def unavailable_runtime(*args, **kwargs):
            if kwargs.get("collection") == "runs":
                raise RecipeRuntimeComparisonError(
                    "recipe runtime observer protocol changed"
                )
            return plan_presentation

        runtime_id = (
            "workbench-developer-recipe-change-runtime-comparison:sha256:"
            + "a" * 64
        )
        runtime_path = self.state / "runs" / ("a" * 64) / "record.json"
        with patch.object(
            presentation,
            "_discover_record_paths",
            return_value=[
                ("plans", self.state / "plans" / plan["id"].rsplit(":", 1)[1] / "record.json"),
                ("runs", runtime_path),
            ],
        ), patch.object(
            presentation,
            "resolve_feature_record",
            side_effect=[
                plan,
                {
                    "id": runtime_id,
                    "kind": "workbench-developer-recipe-change-runtime-comparison",
                },
            ],
        ), patch.object(
            presentation,
            "present_feature_record",
            side_effect=unavailable_runtime,
        ):
            result = discover_feature_records(ROOT, self.state)

        self.assertEqual([plan["id"]], [row["record_id"] for row in result["records"]])
        self.assertIn(
            "1 retained recipe runtime record(s) were omitted",
            result["limitations"][-1],
        )

        with patch.object(
            presentation,
            "_discover_record_paths",
            return_value=[("runs", runtime_path)],
        ), patch.object(
            presentation,
            "resolve_feature_record",
            return_value={
                "id": runtime_id,
                "kind": "workbench-developer-recipe-change-runtime-comparison",
            },
        ), patch.object(
            presentation,
            "present_feature_record",
            side_effect=RecipeRuntimeComparisonError("retained bytes changed"),
        ):
            with self.assertRaisesRegex(
                RecipeRuntimeComparisonError,
                "retained bytes changed",
            ):
                discover_feature_records(ROOT, self.state)

    def test_discovery_preserves_stale_diff_and_rejects_symlinked_state(self) -> None:
        plan = self._material_plan()
        retain_feature_record(self.state, "plans", plan)
        self.material.rename(self.root / "material-moved")

        result = discover_feature_records(ROOT, self.state)
        self.assertEqual("stale", result["records"][0]["verification_state"])
        presentation = present_feature_record(
            ROOT,
            self.state,
            family="material-fluid-recipe",
            collection="plans",
            reference=plan["id"],
        )
        self.assertEqual("stale", presentation["verification"]["state"])
        self.assertEqual(4, len(presentation["operations"]))

        linked = self.root / "linked-state"
        linked.symlink_to(self.state, target_is_directory=True)
        with self.assertRaisesRegex(
            DeveloperFeatureError,
            "state root is not an ordinary directory",
        ):
            discover_feature_records(ROOT, linked)


if __name__ == "__main__":
    unittest.main()
