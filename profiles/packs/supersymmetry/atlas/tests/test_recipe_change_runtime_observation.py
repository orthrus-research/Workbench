from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
import re
import unittest

from workbench_profile_supersymmetry.recipe_change_runtime_observation import (
    MARKER_PREFIX,
    MAX_MACHINE_INPUT_SLOTS,
    PROBE_FAILURE_CODES,
    RecipeChangeRuntimeObservationError,
    build_recipe_change_probe,
    build_recipe_change_probe_overlay,
    compare_recipe_change_observations,
    derive_recipe_change_probe_spec,
    interpret_recipe_change_observation,
    validate_recipe_change_assessment,
    validate_recipe_change_comparison,
)


CONTRACT_KIND = "workbench-supersymmetry-recipe-observation-contract"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _seal_contract(body: dict[str, object]) -> dict[str, object]:
    return {
        **body,
        "id": f"{CONTRACT_KIND}:sha256:{sha256(_canonical(body)).hexdigest()}",
    }


def _contract(*, physical_side: str = "client") -> dict[str, object]:
    item_inputs = [{"amount": 2, "kind": "ore", "name": "dustIron"}]
    fluid_inputs = [{"amount": 100, "name": "steam"}]
    item_outputs = [
        {"amount": 1, "kind": "metaitem", "name": "dustSteel"}
    ]
    fluid_outputs = [{"amount": 100, "name": "water"}]
    body: dict[str, object] = {
        "authority": {
            "construction_authority": "none",
            "interpretation_owner": "Atlas",
            "source_profile": "workbench-pack:supersymmetry",
        },
        "collision_definition": {
            "competing_recipe": (
                "a different registered recipe in the selected map that accepts "
                "a concrete active-runtime expansion of the planned inputs"
            ),
            "expected_competing_input_match_count": 0,
            "expected_exact_recipe_match_count": 1,
            "unresolved_input_expansions_are_failure": True,
        },
        "expected_recipe": {
            "duration": 200,
            "eut_expression": "VA[MV]",
            "fluid_inputs": fluid_inputs,
            "fluid_outputs": fluid_outputs,
            "item_inputs": item_inputs,
            "item_outputs": item_outputs,
            "recipe_map_alias": "BR",
            "recipe_map_registry_name": "batch_reactor",
            "source_owner": "groovy/postInit/recipes/Chemistry.groovy",
            "voltage_tier": "MV",
        },
        "format": "workbench-supersymmetry-recipe-observation-contract-v1",
        "identity_dependencies": [
            {
                "direction": "input",
                "domain": "item",
                "ordinal": 0,
                "value": item_inputs[0],
            },
            {
                "direction": "input",
                "domain": "fluid",
                "ordinal": 0,
                "value": fluid_inputs[0],
            },
            {
                "direction": "output",
                "domain": "item",
                "ordinal": 0,
                "value": item_outputs[0],
            },
            {
                "direction": "output",
                "domain": "fluid",
                "ordinal": 0,
                "value": fluid_outputs[0],
            },
        ],
        "kind": CONTRACT_KIND,
        "limitations": [
            "The result applies only to the exact disposable projection and cold start.",
            "Ore-dictionary collision coverage is limited to expansions present in that active runtime; unresolved expansions fail the observation.",
            "Specialized recipe-builder properties and chanced outputs are outside the V1 construction pattern.",
            "This contract does not authorize source mutation or publication.",
        ],
        "physical_side": physical_side,
        "probe_phase": "postInit-after-pack-recipe-owners",
        "required_checks": {
            "competing_input_match_count": 0,
            "declared_identities_resolve": True,
            "exact_recipe_match_count": 1,
            "groovy_origin": True,
            "lookup_resolves_exact_recipe": True,
            "recipe_map_alias_binding": True,
            "recipe_signature": True,
            "source_owner_compiled": True,
            "unresolved_input_expansion_count": 0,
        },
        "schema_version": 1,
        "source_plan_id": (
            "workbench-supersymmetry-recipe-change-plan:sha256:" + "a" * 64
        ),
        "state": "observation-required",
    }
    return _seal_contract(body)


def _contract_with_inputs(
    *,
    item_inputs: list[dict[str, object]],
    fluid_inputs: list[dict[str, object]],
) -> dict[str, object]:
    body = deepcopy(_contract())
    body.pop("id")
    expected = deepcopy(body["expected_recipe"])
    expected["item_inputs"] = item_inputs
    expected["fluid_inputs"] = fluid_inputs
    body["expected_recipe"] = expected
    dependencies: list[dict[str, object]] = []
    for direction in ("input", "output"):
        for domain in ("item", "fluid"):
            rows = expected[f"{domain}_{direction}s"]
            for ordinal, row in enumerate(rows):
                dependencies.append(
                    {
                        "direction": direction,
                        "domain": domain,
                        "ordinal": ordinal,
                        "value": row,
                    }
                )
    body["identity_dependencies"] = dependencies
    return _seal_contract(body)


def _actual(role: str, *, competitor_count: int = 0) -> dict[str, object]:
    candidate = role == "candidate"
    return {
        "actual_physical_side": "client",
        "competing_input_match_count": competitor_count,
        "concrete_input_expansion_count": 2,
        "declared_identity_count": 4,
        "duration": 200 if candidate else None,
        "eut": 120 if candidate else None,
        "exact_recipe_match_count": 1 if candidate else 0,
        "fluid_input_count": 1 if candidate else None,
        "fluid_output_count": 1 if candidate else None,
        "groovy_origin": True if candidate else None,
        "item_input_count": 1 if candidate else None,
        "item_output_count": 1 if candidate else None,
        "lookup_exact_match_count": 2 if candidate else 0,
        "recipe_count": 101 if candidate else 100,
        "recipe_map_alias_binding": True,
        "recipe_map_registry_name": "batch_reactor",
        "resolved_identity_count": 4,
        # The marker cannot self-claim this.  Atlas derives it from the exact
        # Groovy engine line in the custody-bound log.
        "source_owner_compiled": None,
        "unresolved_input_expansion_count": 0,
    }


def _marker(
    spec: object,
    actual: dict[str, object],
    *,
    error_kind: str | None = None,
    owner_classes: tuple[str, ...] | None = None,
) -> bytes:
    value = {
        "actual": actual,
        "contract_id": spec.contract_id,
        "error_kind": error_kind,
        "expected_physical_side": spec.physical_side,
        "format": "workbench-recipe-change-runtime-marker-v1",
        "probe_id": spec.probe_id,
        "projection_role": spec.projection_role,
        "source_plan_id": spec.source_plan_id,
        "stage": "postInit",
    }
    token = base64.urlsafe_b64encode(_canonical(value)).rstrip(b"=")
    if owner_classes is None:
        owner_classes = (spec.source_owner_class,)
    side = "CLIENT" if spec.physical_side == "client" else "SERVER"
    owner_lines = b"".join(
        (
            f"[12:34:56] [{side}/INFO] [supersymmetry]:  - running script "
            f"{owner_class}\n"
        ).encode("utf-8")
        for owner_class in owner_classes
    )
    return (
        b"[INFO] unrelated\n"
        + owner_lines
        + b"[INFO] "
        + MARKER_PREFIX.encode()
        + token
        + b"\n"
    )


def _capture(spec: object, log: bytes) -> dict[str, object]:
    return {
        "groovy_log_sha256": sha256(log).hexdigest(),
        "groovy_log_uri": f"file:///evidence/{spec.projection_role}/groovy.log",
        "physical_side": spec.physical_side,
        "projection_role": spec.projection_role,
        "runtime_receipt_sha256": "b" * 64,
        "runtime_receipt_size": 1024,
        "runtime_receipt_uri": (
            f"file:///evidence/{spec.projection_role}/runtime-receipt.json"
        ),
    }


def _assessment(spec: object, actual: dict[str, object]) -> dict[str, object]:
    log = _marker(spec, actual)
    return interpret_recipe_change_observation(
        spec, groovy_log_bytes=log, capture=_capture(spec, log)
    )


class RecipeChangeRuntimeObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        contract = _contract()
        self.baseline = derive_recipe_change_probe_spec(
            contract, projection_role="baseline"
        )
        self.candidate = derive_recipe_change_probe_spec(
            contract, projection_role="candidate"
        )

    def test_contract_derives_distinct_bound_lane_specs(self) -> None:
        self.assertNotEqual(self.baseline.probe_id, self.candidate.probe_id)
        self.assertEqual(self.baseline.contract_id, self.candidate.contract_id)
        self.assertEqual(
            self.candidate.source_owner_class,
            "postInit.recipes.Chemistry",
        )

        altered = deepcopy(_contract())
        body = dict(altered)
        body.pop("id")
        body["identity_dependencies"] = []
        with self.assertRaises(RecipeChangeRuntimeObservationError):
            derive_recipe_change_probe_spec(
                _seal_contract(body), projection_role="candidate"
            )

    def test_probe_is_side_specific_observation_only_create_overlay(self) -> None:
        script = build_recipe_change_probe(self.candidate).decode("utf-8")
        for token in (
            "RecipeMap.getByName",
            "Recipemaps.BR",
            "recipe.matches(false, queryItems, queryFluids)",
            "findRecipe(expectedEut, queryItems, queryFluids, true)",
            "isGroovyRecipe()",
            MARKER_PREFIX,
        ):
            self.assertIn(token, script)
        expected_failure_codes = (
            "probe-phase:recipe-map-binding",
            "probe-phase:physical-side-binding",
            "probe-phase:recipe-map-alias-binding",
            "probe-phase:item-input-resolution",
            "probe-phase:fluid-input-resolution",
            "probe-phase:item-output-resolution",
            "probe-phase:fluid-output-resolution",
            "probe-phase:recipe-list-acquisition",
            "probe-phase:signature-metadata-scan",
            "probe-phase:signature-chance-scan",
            "probe-phase:signature-item-scan",
            "probe-phase:signature-fluid-scan",
            "probe-phase:input-expansion",
            "probe-phase:collision-match-scan",
            "probe-phase:collision-find-recipe",
        )
        self.assertEqual(PROBE_FAILURE_CODES, expected_failure_codes)
        self.assertEqual(len(PROBE_FAILURE_CODES), len(set(PROBE_FAILURE_CODES)))
        self.assertLessEqual(len(PROBE_FAILURE_CODES), 32)
        self.assertEqual(
            set(re.findall(r"probe-phase:[a-z-]+", script)),
            set(PROBE_FAILURE_CODES),
        )
        for forbidden in (
            "recipeBuilder(",
            "buildAndRegister()",
            ".remove(",
            "getClass()",
            ".class.name",
            "failure.class",
            "failure.metaClass",
            "getAllLoadedScriptClasses",
            "GroovyScript.getSandbox",
        ):
            self.assertNotIn(forbidden, script)
        self.assertIn("catch (Throwable ignored)", script)
        self.assertIn("errorKind = probeFailureCode", script)

        client_overlay = build_recipe_change_probe_overlay(self.candidate)
        self.assertTrue(client_overlay["target"]["must_be_absent"])
        self.assertEqual(
            client_overlay["target"]["path"],
            ".minecraft/groovy/postInit/utils/"
            "ZzzzWorkbenchRecipeChangeAssertion.groovy",
        )
        server = derive_recipe_change_probe_spec(
            _contract(physical_side="dedicated-server"),
            projection_role="candidate",
        )
        self.assertEqual(
            build_recipe_change_probe_overlay(server)["target"]["path"],
            "groovy/postInit/utils/ZzzzWorkbenchRecipeChangeAssertion.groovy",
        )

    def test_probe_uses_bounded_machine_slot_shape_for_every_input_domain(
        self,
    ) -> None:
        cases = (
            (
                "no-items",
                [],
                [{"amount": 100, "name": "steam"}],
                "def expectedItemInputs = []",
            ),
            (
                "no-fluids",
                [{"amount": 2, "kind": "ore", "name": "dustIron"}],
                [],
                "def expectedFluidInputs = []",
            ),
            (
                "mixed",
                [{"amount": 2, "kind": "ore", "name": "dustIron"}],
                [{"amount": 100, "name": "steam"}],
                "def expectedFluidInputs = [[\"amount\": 100, \"name\": \"steam\"]]",
            ),
        )
        for label, item_inputs, fluid_inputs, expected_literal in cases:
            with self.subTest(label=label):
                spec = derive_recipe_change_probe_spec(
                    _contract_with_inputs(
                        item_inputs=item_inputs,
                        fluid_inputs=fluid_inputs,
                    ),
                    projection_role="candidate",
                )
                script = build_recipe_change_probe(spec).decode("utf-8")
                self.assertIn(expected_literal, script)
                self.assertIn(
                    "def mapItemSlotCount = recipeMap == null ? 0 : recipeMap.getMaxInputs()",
                    script,
                )
                self.assertIn(
                    "def mapFluidSlotCount = recipeMap == null ? 0 : recipeMap.getMaxFluidInputs()",
                    script,
                )
                self.assertIn(
                    f"mapItemSlotCount > {MAX_MACHINE_INPUT_SLOTS}",
                    script,
                )
                self.assertIn(
                    f"mapFluidSlotCount > {MAX_MACHINE_INPUT_SLOTS}",
                    script,
                )
                self.assertIn(
                    "while (queryItems.size() < mapItemSlotCount) queryItems.add(ItemStack.EMPTY)",
                    script,
                )
                self.assertIn(
                    "while (queryFluids.size() < mapFluidSlotCount) queryFluids.add(null)",
                    script,
                )
                self.assertIn(
                    "recipe.matches(false, queryItems, queryFluids)",
                    script,
                )
                self.assertIn(
                    "findRecipe(expectedEut, queryItems, queryFluids, true)",
                    script,
                )
                self.assertNotIn(
                    "recipe.matches(false, items, resolvedFluids)",
                    script,
                )
                self.assertNotIn(
                    "findRecipe(expectedEut, items, resolvedFluids, true)",
                    script,
                )

    def test_interpret_validate_and_compare_rederive_exact_counts(self) -> None:
        baseline = _assessment(self.baseline, _actual("baseline"))
        candidate = _assessment(self.candidate, _actual("candidate"))
        self.assertEqual(baseline["state"], "observed")
        self.assertEqual(candidate["state"], "observed")
        self.assertEqual(
            validate_recipe_change_assessment(self.candidate, candidate),
            candidate,
        )

        comparison = compare_recipe_change_observations(
            self.baseline, baseline, self.candidate, candidate
        )
        self.assertEqual(comparison["state"], "observed-change")
        self.assertEqual(
            comparison["observed_counts"],
            {
                "baseline": {
                    "competitor_count": 0,
                    "exact_count": 0,
                    "map_count": 100,
                    "unresolved_count": 0,
                },
                "candidate": {
                    "competitor_count": 0,
                    "exact_count": 1,
                    "map_count": 101,
                    "unresolved_count": 0,
                },
            },
        )
        self.assertEqual(comparison["delta"]["recipe_count"], 1)
        self.assertEqual(
            validate_recipe_change_comparison(
                self.baseline,
                baseline,
                self.candidate,
                candidate,
                comparison,
            ),
            comparison,
        )

        tampered = deepcopy(comparison)
        tampered["observed_counts"]["candidate"]["exact_count"] = 2
        with self.assertRaises(RecipeChangeRuntimeObservationError):
            validate_recipe_change_comparison(
                self.baseline,
                baseline,
                self.candidate,
                candidate,
                tampered,
            )

    def test_collision_and_tampering_fail_closed(self) -> None:
        baseline = _assessment(self.baseline, _actual("baseline"))
        collision = _assessment(
            self.candidate, _actual("candidate", competitor_count=1)
        )
        self.assertEqual(collision["state"], "mismatch")
        self.assertIn(
            "competing_input_match_count", collision["failed_checks"]
        )
        comparison = compare_recipe_change_observations(
            self.baseline, baseline, self.candidate, collision
        )
        self.assertEqual(comparison["state"], "comparison-mismatch")

        log = _marker(self.candidate, _actual("candidate"))
        with self.assertRaises(RecipeChangeRuntimeObservationError):
            interpret_recipe_change_observation(
                self.candidate,
                groovy_log_bytes=log + log,
                capture=_capture(self.candidate, log + log),
            )

        good = _assessment(self.candidate, _actual("candidate"))
        tampered = deepcopy(good)
        tampered["observed"]["recipe_count"] = 102
        with self.assertRaises(RecipeChangeRuntimeObservationError):
            validate_recipe_change_assessment(self.candidate, tampered)

    def test_stable_nonzero_competitor_count_does_not_relax_v1(self) -> None:
        baseline_actual = _actual("baseline", competitor_count=165)
        baseline_actual["recipe_count"] = 693
        candidate_actual = _actual("candidate", competitor_count=165)
        candidate_actual["recipe_count"] = 694
        baseline = _assessment(
            self.baseline,
            baseline_actual,
        )
        candidate = _assessment(
            self.candidate,
            candidate_actual,
        )
        self.assertEqual(baseline["state"], "mismatch")
        self.assertEqual(candidate["state"], "mismatch")
        comparison = compare_recipe_change_observations(
            self.baseline,
            baseline,
            self.candidate,
            candidate,
        )
        self.assertEqual(comparison["delta"]["competing_input_match_count"], 0)
        self.assertEqual(comparison["state"], "comparison-mismatch")
        self.assertFalse(
            comparison["checks"]["no_competing_recipe_before_or_after"]
        )

    def test_probe_exception_is_retained_as_mismatch(self) -> None:
        actual = {key: None for key in _actual("candidate")}
        for error_kind in PROBE_FAILURE_CODES:
            log = _marker(
                self.candidate,
                actual,
                error_kind=error_kind,
            )
            assessment = interpret_recipe_change_observation(
                self.candidate,
                groovy_log_bytes=log,
                capture=_capture(self.candidate, log),
            )
            self.assertEqual(assessment["state"], "mismatch")
            self.assertEqual(assessment["error_kind"], error_kind)
            self.assertIn("probe_execution", assessment["failed_checks"])
            self.assertIsNone(
                assessment["observed"]["exact_recipe_match_count"]
            )
            self.assertEqual(
                validate_recipe_change_assessment(
                    self.candidate, assessment
                ),
                assessment,
            )

        unknown = _marker(
            self.candidate,
            actual,
            error_kind="probe-phase:unknown",
        )
        with self.assertRaises(RecipeChangeRuntimeObservationError):
            interpret_recipe_change_observation(
                self.candidate,
                groovy_log_bytes=unknown,
                capture=_capture(self.candidate, unknown),
            )

    def test_source_owner_is_derived_only_from_one_exact_engine_line(self) -> None:
        actual = _actual("candidate")

        exact = _marker(self.candidate, actual)
        exact_assessment = interpret_recipe_change_observation(
            self.candidate,
            groovy_log_bytes=exact,
            capture=_capture(self.candidate, exact),
        )
        self.assertTrue(exact_assessment["observed"]["source_owner_compiled"])
        self.assertTrue(exact_assessment["checks"]["source_owner_compiled"])

        forged_actual = deepcopy(actual)
        forged_actual["source_owner_compiled"] = True
        forged = _marker(self.candidate, forged_actual)
        with self.assertRaises(RecipeChangeRuntimeObservationError):
            interpret_recipe_change_observation(
                self.candidate,
                groovy_log_bytes=forged,
                capture=_capture(self.candidate, forged),
            )

        evidence_variants = (
            (),
            ("postInit.recipes.NotChemistry",),
            (
                self.candidate.source_owner_class,
                self.candidate.source_owner_class,
            ),
        )
        for owner_classes in evidence_variants:
            with self.subTest(owner_classes=owner_classes):
                log = _marker(
                    self.candidate,
                    actual,
                    owner_classes=owner_classes,
                )
                assessment = interpret_recipe_change_observation(
                    self.candidate,
                    groovy_log_bytes=log,
                    capture=_capture(self.candidate, log),
                )
                self.assertEqual(assessment["state"], "mismatch")
                self.assertFalse(
                    assessment["observed"]["source_owner_compiled"]
                )
                self.assertIn(
                    "source_owner_compiled", assessment["failed_checks"]
                )

        # Even an exact engine-shaped line cannot prove the contract's
        # postInit-after-owner ordering when it occurs after the probe marker.
        reordered = _marker(
            self.candidate,
            actual,
            owner_classes=(),
        )
        owner_line = (
            "[12:34:57] [CLIENT/INFO] [supersymmetry]:  - running script "
            f"{self.candidate.source_owner_class}\n"
        ).encode("utf-8")
        reordered += owner_line
        assessment = interpret_recipe_change_observation(
            self.candidate,
            groovy_log_bytes=reordered,
            capture=_capture(self.candidate, reordered),
        )
        self.assertEqual(assessment["state"], "mismatch")
        self.assertIn("source_owner_compiled", assessment["failed_checks"])


if __name__ == "__main__":
    unittest.main()
