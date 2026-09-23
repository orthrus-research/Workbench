#!/usr/bin/env python3

"""Focused tests for the Manuals implementation-guide contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = MODULE_ROOT / "contracts"
EXAMPLE_ROOT = MODULE_ROOT / "examples"
CONTRACT_PATH = CONTRACT_ROOT / "implementation-guide-v1.md"
POLICY_PATH = CONTRACT_ROOT / "requirement-policy-v1.json"
EXAMPLES_PATH = EXAMPLE_ROOT / "contract-examples-v1.json"

CONTRACT_ID = "MANUALS-IMPLEMENTATION-GUIDE-V1"
POLICY_ID = "MANUALS-REQUIREMENT-POLICY-V1"
REQUIREMENT_CLASSES = [
    "compile-required",
    "runtime-required",
    "identity-required",
    "presentation-required",
    "integration-required",
    "conditional",
    "conventional",
    "optional",
]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _without(value: dict[str, Any], key: str) -> dict[str, Any]:
    projected = copy.deepcopy(value)
    projected.pop(key, None)
    return projected


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise AssertionError(f"mutation path is not a JSON pointer: {pointer}")
    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    ]


def _apply_mutation(document: dict[str, Any], mutation: dict[str, Any]) -> None:
    parts = _pointer_parts(mutation["path"])
    parent: Any = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    leaf = parts[-1]
    operation = mutation["op"]
    if isinstance(parent, list):
        if operation == "add" and leaf == "-":
            parent.append(copy.deepcopy(mutation["value"]))
        elif operation == "remove":
            parent.pop(int(leaf))
        elif operation == "replace":
            parent[int(leaf)] = copy.deepcopy(mutation["value"])
        else:
            raise AssertionError(f"unsupported list mutation: {mutation}")
    elif operation == "remove":
        del parent[leaf]
    elif operation in {"add", "replace"}:
        parent[leaf] = copy.deepcopy(mutation["value"])
    else:
        raise AssertionError(f"unsupported object mutation: {mutation}")


def _semantic_errors(
    document: dict[str, Any],
    policy: dict[str, Any],
) -> set[str]:
    errors: set[str] = set()
    role_policy = {role["id"]: role for role in policy["authority_roles"]}
    class_policy = {
        requirement["id"]: requirement
        for requirement in policy["requirement_classes"]
    }

    if document.get("contract_id") != CONTRACT_ID:
        errors.add("contract-id")
    if document.get("policy_id") != POLICY_ID:
        errors.add("policy-id")

    request = document.get("request", {})
    expected_request_id = "manual-request:sha256:" + _sha256(
        _without(request, "request_id")
    )
    if request.get("request_id") != expected_request_id:
        errors.add("request-id")

    evidence_rows = document.get("evidence", [])
    evidence = {row.get("id"): row for row in evidence_rows}
    if len(evidence) != len(evidence_rows) or None in evidence:
        errors.add("evidence-id")
    for row in evidence_rows:
        role = role_policy.get(row.get("authority_role"))
        if role is None or row.get("basis") not in role["permitted_bases"]:
            errors.add("authority-role-basis-mismatch")
        if row.get("record_sha256") != _sha256(row.get("record")):
            errors.add("evidence-record-digest")

    referenced: set[str] = set()
    for row in evidence_rows:
        referenced.update(row.get("derived_from", []))

    steps = document.get("steps", [])
    if [step.get("ordinal") for step in steps] != list(range(len(steps))):
        errors.add("step-order")
    step_ids = {step.get("step_id") for step in steps}
    for step in steps:
        referenced.update(step.get("evidence_ids", []))
        referenced.update(step.get("omission", {}).get("evidence_ids", []))
        for requirement in step.get("requirements", []):
            classification = requirement.get("classification")
            rule = class_policy.get(classification)
            if rule is None:
                errors.add("requirement-class")
                continue
            if requirement.get("modal") != rule["modal"]:
                errors.add("requirement-modal")
            condition = requirement.get("condition")
            if rule["condition"] == "required":
                if not isinstance(condition, dict):
                    errors.add("conditional-predicate-required")
                else:
                    required_condition_fields = {
                        "predicate_id",
                        "statement",
                        "inputs",
                        "when_true_class",
                    }
                    if not required_condition_fields.issubset(condition):
                        errors.add("conditional-predicate-required")
                    if condition.get("when_true_class") not in rule.get(
                        "when_true_classes",
                        [],
                    ):
                        errors.add("conditional-true-class")
            elif condition is not None:
                errors.add("requirement-condition-forbidden")

            support_ids = requirement.get("support_evidence_ids", [])
            referenced.update(support_ids)
            support_roles = {
                evidence[evidence_id]["authority_role"]
                for evidence_id in support_ids
                if evidence_id in evidence
            }
            if not any(
                set(combination).issubset(support_roles)
                for combination in rule["support_any"]
            ):
                errors.add("requirement-support-threshold")

            omission = requirement.get("omission", {})
            omission_ids = omission.get("evidence_ids", [])
            referenced.update(omission_ids)
            omission_roles = {
                evidence[evidence_id]["authority_role"]
                for evidence_id in omission_ids
                if evidence_id in evidence
            }
            if omission.get("status") in {"observed", "proven"}:
                if not omission.get("closed_scope"):
                    errors.add("closed-scope-required")
                if "omission-proof" not in omission_roles:
                    errors.add("omission-proof-required")
            if omission.get("status") == "expected" and not {
                "api-contract",
                "source-enforcement",
            }.intersection(omission_roles):
                errors.add("expected-omission-authority")

            projected = {
                "requirement": _without(requirement, "requirement_id"),
                "step_ordinal": step["ordinal"],
            }
            expected_requirement_id = (
                "manual-requirement:sha256:" + _sha256(projected)
            )
            if requirement.get("requirement_id") != expected_requirement_id:
                errors.add("requirement-id")

        expected_step_id = "manual-step:sha256:" + _sha256(
            _without(step, "step_id")
        )
        if step.get("step_id") != expected_step_id:
            errors.add("step-id")

    for example in document.get("examples", []):
        referenced.update(example.get("evidence_ids", []))
        if example.get("normative") is not False:
            errors.add("normative-example")
        if not set(example.get("step_ids", [])).issubset(step_ids):
            errors.add("example-step-reference")
    for observation in document.get("expected_observations", []):
        referenced.update(observation.get("evidence_ids", []))
    for validation in document.get("validation", []):
        referenced.update(validation.get("evidence_ids", []))
    for unknown in document.get("known_unknowns", []):
        referenced.update(unknown.get("evidence_ids", []))
        if unknown.get("status") not in policy["absence_states"]:
            errors.add("unknown-status")

    evidence_ids = set(evidence)
    if not referenced.issubset(evidence_ids):
        errors.add("disconnected-evidence")
    if policy["claim_rules"]["unused_evidence_forbidden"]:
        if evidence_ids - referenced:
            errors.add("unused-evidence")

    expected_manual_id = "manual:sha256:" + _sha256(
        _without(document, "manual_id")
    )
    if document.get("manual_id") != expected_manual_id:
        errors.add("manual-id")
    return errors


class ManualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = CONTRACT_PATH.read_text(encoding="utf-8")
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        cls.examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))

    def test_contract_fixes_identity_authority_and_projection_boundaries(
        self,
    ) -> None:
        for required in (
            CONTRACT_ID,
            POLICY_ID,
            "## Canonical JSON and identity",
            "## Ordered implementation steps",
            "## Requirement statements",
            "## Omission and negative claims",
            "## Evidence bindings",
            "## Deterministic guide projection",
            "Contract examples are not JSON-Schema validation",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.contract)
        for target in re.findall(r"\[[^\]]+\]\(([^)#]+)", self.contract):
            with self.subTest(link=target):
                self.assertTrue((CONTRACT_ROOT / target).resolve().exists())

    def test_requirement_policy_is_complete_and_fail_closed(self) -> None:
        policy = self.policy
        self.assertEqual(1, policy["schema_version"])
        self.assertEqual(CONTRACT_ID, policy["contract_id"])
        self.assertEqual(POLICY_ID, policy["policy_id"])
        self.assertEqual(
            "manual-canonical-json-v1",
            policy["canonicalization_id"],
        )
        classes = policy["requirement_classes"]
        self.assertEqual(REQUIREMENT_CLASSES, [entry["id"] for entry in classes])
        self.assertEqual(len(classes), len({entry["id"] for entry in classes}))
        self.assertEqual(
            ["must"] * 5 + ["must-when", "should", "may"],
            [entry["modal"] for entry in classes],
        )
        self.assertEqual(
            ["forbidden"] * 5 + ["required", "forbidden", "forbidden"],
            [entry["condition"] for entry in classes],
        )
        roles = policy["authority_roles"]
        role_ids = {role["id"] for role in roles}
        self.assertEqual(len(roles), len(role_ids))
        self.assertNotIn("unresolved", role_ids)
        self.assertTrue(
            all(
                isinstance(role.get("admission_rule"), str)
                and role["admission_rule"].strip()
                for role in roles
            )
        )
        for entry in classes:
            self.assertGreater(len(entry["support_any"]), 0)
            for combination in entry["support_any"]:
                self.assertGreater(len(combination), 0)
                self.assertTrue(set(combination).issubset(role_ids))
        rules = policy["claim_rules"]
        self.assertFalse(rules["example_frequency_is_normative"])
        self.assertTrue(rules["example_promotion_forbidden"])
        self.assertFalse(rules["runtime_observation_proves_reproduction"])
        self.assertTrue(rules["unused_evidence_forbidden"])

    def test_valid_fixture_closes_identity_references_and_all_classes(
        self,
    ) -> None:
        cases = self.examples["valid_cases"]
        self.assertGreater(len(cases), 0)
        observed_classes: set[str] = set()
        for case in cases:
            with self.subTest(case=case["case_id"]):
                document = case["document"]
                self.assertTrue(document["fixture"])
                self.assertEqual(set(), _semantic_errors(document, self.policy))
                for step in document["steps"]:
                    observed_classes.update(
                        requirement["classification"]
                        for requirement in step["requirements"]
                    )
        self.assertEqual(set(REQUIREMENT_CLASSES), observed_classes)

    def test_invalid_mutations_cover_required_fail_closed_cases(self) -> None:
        valid_cases = {
            case["case_id"]: case["document"]
            for case in self.examples["valid_cases"]
        }
        expected_case_ids = {
            "unsupported-authority-promotion",
            "missing-conditional-predicate",
            "example-promoted-to-normative",
            "disconnected-evidence",
            "false-closed-world-negative",
        }
        invalid_cases = self.examples["invalid_cases"]
        self.assertEqual(expected_case_ids, {case["case_id"] for case in invalid_cases})
        for case in invalid_cases:
            document = copy.deepcopy(valid_cases[case["base_case_id"]])
            for mutation in case["mutations"]:
                _apply_mutation(document, mutation)
            errors = _semantic_errors(document, self.policy)
            with self.subTest(case=case["case_id"], errors=sorted(errors)):
                self.assertTrue(set(case["expected_error_codes"]).issubset(errors))


if __name__ == "__main__":
    unittest.main()
