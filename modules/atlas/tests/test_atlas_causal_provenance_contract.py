#!/usr/bin/env python3

"""Focused contract and mutation tests for ATLAS-M4-C01."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import workbench_atlas.atlas_causal_provenance_contract as provenance


class AtlasCausalProvenanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = provenance.load_policy()
        cls.examples = provenance.validate_examples()
        cls.results = {
            row["status"]: row for row in cls.examples["results"]
        }

    def reidentify_result(self, result: dict) -> None:
        result["result_id"] = provenance.content_id(
            provenance.RESULT_PREFIX, result, "result_id"
        )

    def mutate_relation(
        self,
        result: dict,
        relation_predicate: str,
        **changes: object,
    ) -> None:
        relation = next(
            row
            for row in result["relations"]
            if row["predicate"] == relation_predicate
        )
        old_id = relation["relation_id"]
        relation.update(changes)
        relation["relation_id"] = provenance.content_id(
            provenance.RELATION_PREFIX,
            relation,
            "relation_id",
        )
        new_id = relation["relation_id"]
        for path in result["paths"]:
            if old_id not in path["relation_ids"]:
                continue
            path["relation_ids"] = [
                new_id if item == old_id else item
                for item in path["relation_ids"]
            ]
            path["path_id"] = provenance.content_id(
                provenance.PATH_PREFIX, path, "path_id"
            )
        result["relations"].sort(key=lambda row: row["relation_id"])
        result["paths"].sort(key=lambda row: row["path_id"])
        self.reidentify_result(result)

    def test_policy_and_all_canonical_examples_validate(self) -> None:
        self.assertEqual(
            "ATLAS-CAUSAL-PROVENANCE-POLICY-V1",
            self.policy["policy_id"],
        )
        self.assertEqual(
            {
                "closed",
                "partial",
                "bounded",
                "unresolved",
                "unavailable",
            },
            set(self.results),
        )

    def test_policy_has_exact_relation_and_node_ontology(self) -> None:
        self.assertEqual(
            provenance.NODE_CLASSES,
            set(self.policy["node_classes"]),
        )
        relation_policies = {
            row["predicate"]: row
            for row in self.policy["relation_policies"]
        }
        self.assertEqual(provenance.PREDICATES, set(relation_policies))
        for predicate, row in relation_policies.items():
            with self.subTest(predicate=predicate):
                self.assertEqual(
                    set(row["permitted_strengths"]),
                    set(row["evidence_requirements"]),
                )
                self.assertTrue(
                    all(row["evidence_requirements"].values())
                )
        for predicate in ("mutates", "removes", "replaces"):
            self.assertEqual(
                ["runtime-state"],
                relation_policies[predicate]["subject_classes"],
            )
        for predicate in ("copies", "transforms"):
            self.assertNotIn(
                "script-operation",
                relation_policies[predicate]["subject_classes"],
            )

        mutated = copy.deepcopy(self.policy)
        mutated["relation_policies"][0]["meaning"] = "Broadened meaning."
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "policy differs from immutable v1",
        ):
            provenance.validate_policy(mutated)

    def test_request_binds_policy_and_m1_query_identity(self) -> None:
        request = copy.deepcopy(self.examples["requests"][0])
        request["query_instance_id"] = (
            "atlas-query:sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "request content identity differs",
        ):
            provenance.validate_request(request, self.policy)

        request = copy.deepcopy(self.examples["requests"][0])
        request["policy_sha256"] = "0" * 64
        request["request_id"] = provenance.content_id(
            provenance.REQUEST_PREFIX, request, "request_id"
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "request policy digest differs",
        ):
            provenance.validate_request(request, self.policy)

    def test_supporting_scope_is_explicit_and_cannot_repeat_primary(self) -> None:
        request = copy.deepcopy(self.examples["requests"][0])
        request["supporting_scopes"] = [
            {
                "profile": "CLIENT_JEI_FINAL_STATE",
                "physical_side": "CLIENT",
            }
        ]
        request["request_id"] = provenance.content_id(
            provenance.REQUEST_PREFIX, request, "request_id"
        )
        self.assertIs(request, provenance.validate_request(request, self.policy))

        request["supporting_scopes"] = [copy.deepcopy(request["scope"])]
        request["request_id"] = provenance.content_id(
            provenance.REQUEST_PREFIX, request, "request_id"
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "primary scope must not be repeated",
        ):
            provenance.validate_request(request, self.policy)

    def test_source_span_identity_is_revision_tree_byte_and_digest_bound(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        source = next(
            row for row in result["nodes"] if row["node_class"] == "source-span"
        )
        self.assertEqual("inclusive-zero-based", self.policy["identity_policy"][
            "source_span_byte_interval"
        ])
        self.assertLessEqual(
            source["identity"]["byte_start"],
            source["identity"]["byte_end"],
        )
        source["identity"]["tree"] = "0" * 40
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "source-span evidence does not bind its source identity",
        ):
            provenance.validate_result(result, self.policy)

    def test_source_span_is_cross_bound_to_request_lock_and_evidence(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        source = next(
            row for row in result["nodes"] if row["node_class"] == "source-span"
        )
        source["identity"]["source_lock_id"] = "SUSY-OTHER-SOURCE-LOCK-0001"
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "source-span source lock differs from request",
        ):
            provenance.validate_result(result, self.policy)

        result = copy.deepcopy(self.results["closed"])
        source = next(
            row for row in result["nodes"] if row["node_class"] == "source-span"
        )
        source["identity"]["source_id"] = "SRC-OTHER"
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "source-span evidence does not bind its source identity",
        ):
            provenance.validate_result(result, self.policy)

    def test_runtime_node_must_include_exact_capture_evidence(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        final_node = next(
            row
            for row in result["nodes"]
            if row["node_class"] == "final-runtime-record"
        )
        source_evidence = next(
            row
            for row in result["evidence"]
            if row["record_kind"] == "source-span"
        )
        final_node["evidence_ids"] = [source_evidence["evidence_id"]]
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "runtime node omits its capture evidence",
        ):
            provenance.validate_result(result, self.policy)

    def test_evidence_record_kind_cannot_change_authority(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        source_evidence = next(
            row
            for row in result["evidence"]
            if row["record_kind"] == "source-span"
        )
        source_evidence["authority"] = "runtime-mechanics"
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "authority does not match its record kind",
        ):
            provenance.validate_result(result, self.policy)

    def test_script_operation_requires_identity_bound_execution(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        operation = next(
            row
            for row in result["nodes"]
            if row["node_class"] == "script-operation"
        )
        source = next(
            row for row in result["nodes"] if row["node_class"] == "source-span"
        )
        runtime_state = next(
            row for row in result["nodes"] if row["node_class"] == "runtime-state"
        )
        stage_evidence = next(
            row
            for row in result["evidence"]
            if row["record_kind"] == "stage-execution"
        )
        self.assertTrue(
            {
                provenance.canonical_sha256(source["identity"]),
                provenance.canonical_sha256(operation["identity"]),
                provenance.canonical_sha256(runtime_state["identity"]),
            }.issubset(stage_evidence["record"]["related_identity_sha256s"])
        )
        operation["identity"]["operation_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "script-operation lacks exact execution evidence",
        ):
            provenance.validate_result(result, self.policy)

    def test_static_possibility_cannot_close_a_causal_path(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        self.mutate_relation(
            result,
            "invokes",
            causal_strength="possibility",
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "possibility-only evidence cannot close",
        ):
            provenance.validate_result(result, self.policy)

    def test_lifecycle_reversal_fails_after_recomputing_all_ids(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        self.mutate_relation(
            result,
            "observed_as_final",
            lifecycle_stage_id="groovy-pre-init",
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "path lifecycle order is invalid",
        ):
            provenance.validate_result(result, self.policy)

    def test_similarity_cannot_be_relabelled_as_copy_evidence(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        self.mutate_relation(result, "registers", predicate="copies")
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "relation subject class is not permitted",
        ):
            provenance.validate_result(result, self.policy)

    def test_transition_evidence_binds_exact_endpoints(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        transition = next(
            row
            for row in result["evidence"]
            if row["record_kind"] == "state-transition"
        )
        transition["record"]["object_node_id"] = (
            provenance.NODE_PREFIX + "0" * 64
        )
        transition["record_sha256"] = provenance.canonical_sha256(
            transition["record"]
        )
        transition["evidence_id"] = provenance.content_id(
            provenance.EVIDENCE_PREFIX,
            transition,
            "evidence_id",
        )
        result["evidence"].sort(key=lambda row: row["evidence_id"])
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "evidence is unresolved|evidence is not exact",
        ):
            provenance.validate_result(result, self.policy)

    def test_final_runtime_binding_cannot_drift_from_request(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        final_node = next(
            row
            for row in result["nodes"]
            if row["node_class"] == "final-runtime-record"
        )
        final_node["identity"]["runtime_record_sha256"] = "0" * 64
        final_node["node_id"] = provenance.content_id(
            provenance.NODE_PREFIX, final_node, "node_id"
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "exactly one request-bound final runtime record|relation endpoint",
        ):
            provenance.validate_result(result, self.policy)

    def test_every_evidence_record_is_resolved_and_used(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        unused = copy.deepcopy(result["evidence"][0])
        unused["record"] = {"unused": True}
        unused["record_sha256"] = provenance.canonical_sha256(unused["record"])
        unused["evidence_id"] = provenance.content_id(
            provenance.EVIDENCE_PREFIX, unused, "evidence_id"
        )
        result["evidence"].append(unused)
        result["evidence"].sort(key=lambda row: row["evidence_id"])
        self.reidentify_result(result)
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "evidence is not exact and fully used",
        ):
            provenance.validate_result(result, self.policy)

    def test_explanation_derivation_stays_outside_causal_paths(self) -> None:
        result = copy.deepcopy(self.results["closed"])
        record = {
            "validator_id": "ATLAS-CAUSAL-PROVENANCE-CONTRACT-V1",
            "result_id": result["result_id"],
            "audience": "developer",
        }
        evidence = {
            "evidence_id": "",
            "authority": "derived-validation",
            "record_kind": "causal-validation",
            "record": record,
            "record_sha256": provenance.canonical_sha256(record),
        }
        evidence["evidence_id"] = provenance.content_id(
            provenance.EVIDENCE_PREFIX, evidence, "evidence_id"
        )
        final_node = next(
            row
            for row in result["nodes"]
            if row["node_class"] == "final-runtime-record"
        )
        explanation = {
            "node_id": "",
            "node_class": "explanation",
            "scope": copy.deepcopy(final_node["scope"]),
            "identity": {
                "audience": "developer",
                "statement_sha256": provenance.canonical_sha256(
                    {"statement": "Example developer explanation."}
                ),
            },
            "evidence_ids": [evidence["evidence_id"]],
        }
        explanation["node_id"] = provenance.content_id(
            provenance.NODE_PREFIX, explanation, "node_id"
        )
        relation = {
            "relation_id": "",
            "predicate": "derives_explanation",
            "subject_node_id": final_node["node_id"],
            "object_node_id": explanation["node_id"],
            "causal_strength": "derivation",
            "lifecycle_stage_id": None,
            "evidence_ids": [evidence["evidence_id"]],
        }
        relation["relation_id"] = provenance.content_id(
            provenance.RELATION_PREFIX, relation, "relation_id"
        )
        result["evidence"].append(evidence)
        result["evidence"].sort(key=lambda row: row["evidence_id"])
        result["nodes"].append(explanation)
        result["nodes"].sort(key=lambda row: row["node_id"])
        result["relations"].append(relation)
        result["relations"].sort(key=lambda row: row["relation_id"])
        result["truncation"]["emitted_evidence_records"] += 1
        result["truncation"]["emitted_nodes"] += 1
        result["truncation"]["emitted_relations"] += 1
        self.reidentify_result(result)
        self.assertIs(
            result, provenance.validate_result(result, self.policy)
        )

    def test_bounded_result_never_claims_completion(self) -> None:
        bounded = self.results["bounded"]
        self.assertTrue(bounded["truncation"]["truncated"])
        self.assertGreaterEqual(
            bounded["truncation"]["omitted_at_least"], 1
        )
        self.assertEqual(
            {
                "mode": "none-v1-single-bounded-result",
                "continuation": None,
            },
            bounded["pagination"],
        )
        self.assertFalse(
            self.policy["pagination_policy"]["truncation_is_completion"]
        )

    def test_negative_wording_is_policy_bound(self) -> None:
        result = copy.deepcopy(self.results["unresolved"])
        result["negative_statements"][0][
            "bounded_wording"
        ] = "No causal path exists."
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "negative statement wording is not policy-bound",
        ):
            provenance.validate_result(result, self.policy)

    def test_examples_are_deterministic_canonical_values(self) -> None:
        original = json.loads(
            provenance.EXAMPLES_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(
            provenance.canonical_sha256(original),
            provenance.canonical_sha256(self.examples),
        )
        for result in self.examples["results"]:
            with self.subTest(status=result["status"]):
                self.assertEqual(
                    result["result_id"],
                    provenance.content_id(
                        provenance.RESULT_PREFIX, result, "result_id"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
