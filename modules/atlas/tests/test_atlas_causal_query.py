#!/usr/bin/env python3

"""Focused traversal and mutation tests for ATLAS-M4-P03."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import workbench_atlas.atlas_causal_provenance_contract as provenance
import workbench_atlas.atlas_causal_query as causal_query
import workbench_atlas.atlas_provenance_normalizer as normalizer
import test_atlas_provenance_normalizer as fixtures  # noqa: E402
from workbench_atlas.knowledge_catalog import read_json  # noqa: E402


class AtlasCausalQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = provenance.load_policy()

    @staticmethod
    def exact_inputs() -> tuple[dict, dict]:
        source_index, extraction = fixtures.static_inputs(operations=[])
        call = next(
            item
            for item in source_index["records"]
            if item["span_kind"] == "call-site"
        )
        extraction["operations"] = [
            fixtures.operation(
                call["source_span_id"],
                operation_index=0,
                operation_kind="addition",
                target_state="exact",
            )
        ]
        return source_index, extraction

    @classmethod
    def normalize(
        cls,
        *,
        runtime_shape: str = "exact",
        authority_unavailable: bool = False,
    ) -> dict:
        source_index, extraction = cls.exact_inputs()
        bundle = fixtures.exact_runtime_bundle(source_index, extraction)
        if runtime_shape in {"partial", "final-only"}:
            retained_classes = (
                {"runtime-state", "final-runtime-record"}
                if runtime_shape == "partial"
                else {"final-runtime-record"}
            )
            retained_nodes = [
                item
                for item in bundle["nodes"]
                if item["node_class"] in retained_classes
            ]
            retained_node_ids = {item["node_id"] for item in retained_nodes}
            bundle["nodes"] = retained_nodes
            bundle["relations"] = [
                item
                for item in bundle["relations"]
                if item["subject_node_id"] in retained_node_ids
                and item["object_node_id"] in retained_node_ids
            ]
            used_evidence_ids = {
                evidence_id
                for item in [*bundle["nodes"], *bundle["relations"]]
                for evidence_id in item["evidence_ids"]
            }
            bundle["evidence"] = [
                item
                for item in bundle["evidence"]
                if item["evidence_id"] in used_evidence_ids
            ]
            bundle["operation_bindings"] = []
            bundle["configuration_bindings"] = []
        if authority_unavailable:
            unavailable_evidence = normalizer._evidence(
                "runtime-mechanics",
                "authority-unavailable",
                {
                    "snapshot_id": fixtures.SNAPSHOT,
                    "profile": fixtures.SCOPE["profile"],
                    "physical_side": fixtures.SCOPE["physical_side"],
                    "required_for": "causal-provenance-query",
                    "reason": "required-authority-not-retained",
                },
            )
            final = next(
                item
                for item in bundle["nodes"]
                if item["node_class"] == "final-runtime-record"
            )
            final["evidence_ids"] = sorted(
                [*final["evidence_ids"], unavailable_evidence["evidence_id"]]
            )
            final["node_id"] = provenance.content_id(
                provenance.NODE_PREFIX,
                final,
                "node_id",
            )
            bundle["evidence"].append(unavailable_evidence)
            bundle["evidence"].sort(key=lambda item: item["evidence_id"])
        bundle["nodes"].sort(key=lambda item: item["node_id"])
        bundle["relations"].sort(key=lambda item: item["relation_id"])
        bundle["bundle_id"] = provenance.content_id(
            normalizer.RUNTIME_BUNDLE_PREFIX,
            bundle,
            "bundle_id",
        )
        return normalizer._normalize_validated(
            source_index,
            extraction,
            bundle,
        )

    @classmethod
    def request(
        cls,
        document: dict,
        *,
        bounds: dict[str, int] | None = None,
    ) -> dict:
        final = next(
            item
            for item in document["nodes"]
            if item["node_class"] == "final-runtime-record"
        )
        row = {
            "schema_version": 1,
            "format": "susy-atlas-causal-provenance-request-v1",
            "request_id": "",
            "query_instance_id": "atlas-query:sha256:" + "a" * 64,
            "snapshot_id": fixtures.SNAPSHOT,
            "scope": {
                "profile": fixtures.SCOPE["profile"],
                "physical_side": fixtures.SCOPE["physical_side"],
            },
            "supporting_scopes": [],
            "final_runtime_record": copy.deepcopy(final["identity"]),
            "source_lock_id": fixtures.SOURCE_LOCK,
            "policy_id": cls.policy["policy_id"],
            "policy_sha256": provenance.canonical_sha256(cls.policy),
            "bounds": bounds
            or {
                "max_paths": 8,
                "max_nodes": 32,
                "max_relations": 32,
                "max_evidence_records": 16,
            },
        }
        row["request_id"] = provenance.content_id(
            provenance.REQUEST_PREFIX,
            row,
            "request_id",
        )
        return row

    @classmethod
    def add_second_closed_path(cls, document: dict) -> None:
        lifecycle = next(
            item
            for item in document["nodes"]
            if item["node_class"] == "lifecycle-event"
        )
        runtime_state = next(
            item
            for item in document["nodes"]
            if item["node_class"] == "runtime-state"
        )
        stage_evidence = normalizer._evidence(
            "runtime-mechanics",
            "stage-execution",
            {
                "snapshot_id": fixtures.SNAPSHOT,
                "profile": fixtures.SCOPE["profile"],
                "physical_side": fixtures.SCOPE["physical_side"],
                "stage_id": "groovy-post-init",
                "stage_execution_id": "STAGE-EXEC-PILOT-0001",
                "occurrence_id": lifecycle["identity"]["occurrence_id"],
                "executed_node_class": "lifecycle-event",
                "executed_identity_sha256": provenance.canonical_sha256(
                    lifecycle["identity"]
                ),
                "related_identity_sha256s": sorted(
                    [
                        provenance.canonical_sha256(lifecycle["identity"]),
                        provenance.canonical_sha256(runtime_state["identity"]),
                    ]
                ),
            },
        )
        transition_evidence = normalizer._evidence(
            "runtime-mechanics",
            "state-transition",
            {
                "snapshot_id": fixtures.SNAPSHOT,
                "profile": fixtures.SCOPE["profile"],
                "physical_side": fixtures.SCOPE["physical_side"],
                "stage_id": "groovy-post-init",
                "stage_execution_id": "STAGE-EXEC-PILOT-0001",
                "predicate": "registers",
                "subject_node_id": lifecycle["node_id"],
                "object_node_id": runtime_state["node_id"],
            },
        )
        relation = normalizer._relation(
            "registers",
            lifecycle["node_id"],
            runtime_state["node_id"],
            "causation",
            "groovy-post-init",
            [
                stage_evidence["evidence_id"],
                transition_evidence["evidence_id"],
            ],
        )
        document["evidence"].extend([stage_evidence, transition_evidence])
        document["evidence"].sort(key=lambda item: item["evidence_id"])
        document["relations"].append(relation)
        document["relations"].sort(key=lambda item: item["relation_id"])
        forward: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = {}
        for item in document["relations"]:
            forward.setdefault(item["subject_node_id"], []).append(
                item["relation_id"]
            )
            reverse.setdefault(item["object_node_id"], []).append(
                item["relation_id"]
            )
        document["indexes"] = {
            "forward": [
                {"node_id": node_id, "relation_ids": sorted(relation_ids)}
                for node_id, relation_ids in sorted(forward.items())
            ],
            "reverse": [
                {"node_id": node_id, "relation_ids": sorted(relation_ids)}
                for node_id, relation_ids in sorted(reverse.items())
            ],
        }
        document["summary"]["relation_count"] = len(document["relations"])
        document["summary"]["evidence_count"] = len(document["evidence"])
        document["normalization_id"] = provenance.content_id(
            normalizer.NORMALIZATION_PREFIX,
            document,
            "normalization_id",
        )
        normalizer.validate_normalization(document, policy=cls.policy)

    @staticmethod
    def node_classes(result: dict, path: dict) -> list[str]:
        nodes = {item["node_id"]: item for item in result["nodes"]}
        return [nodes[item]["node_class"] for item in path["node_ids"]]

    def test_exact_query_closes_source_to_final_path(self) -> None:
        document = self.normalize()
        result = causal_query.query(
            document,
            self.request(document),
            self.policy,
        )
        self.assertIs(result, provenance.validate_result(result, self.policy))
        self.assertEqual("closed", result["status"])
        self.assertEqual(1, len(result["paths"]))
        self.assertEqual(
            [
                "source-span",
                "script-operation",
                "runtime-state",
                "final-runtime-record",
            ],
            self.node_classes(result, result["paths"][0]),
        )
        self.assertEqual(
            ["invokes", "registers", "observed_as_final"],
            [
                next(
                    item["predicate"]
                    for item in result["relations"]
                    if item["relation_id"] == relation_id
                )
                for relation_id in result["paths"][0]["relation_ids"]
            ],
        )
        self.assertFalse(result["negative_statements"])

    def test_disconnected_static_candidate_does_not_become_a_causal_edge(
        self,
    ) -> None:
        document = self.normalize(runtime_shape="final-only")
        self.assertTrue(document["operation_candidates"])
        self.assertTrue(document["frontiers"])
        result = causal_query.query(
            document,
            self.request(document),
            self.policy,
        )
        self.assertEqual("unresolved", result["status"])
        self.assertFalse(result["paths"])
        self.assertFalse(result["relations"])
        self.assertEqual(
            {"final-runtime-record"},
            {item["node_class"] for item in result["nodes"]},
        )
        self.assertEqual(
            ["no-closed-causal-path"],
            [item["kind"] for item in result["negative_statements"]],
        )

    def test_runtime_only_chain_is_partial_not_closed(self) -> None:
        document = self.normalize(runtime_shape="partial")
        result = causal_query.query(
            document,
            self.request(document),
            self.policy,
        )
        self.assertEqual("partial", result["status"])
        self.assertEqual(
            [
                "runtime-transition-unobserved",
                "source-span-unavailable",
            ],
            result["closure"]["reason_codes"],
        )
        self.assertEqual(1, len(result["paths"]))
        self.assertEqual(
            ["runtime-state", "final-runtime-record"],
            self.node_classes(result, result["paths"][0]),
        )
        self.assertEqual(
            [result["paths"][0]["node_ids"][0]],
            result["closure"]["open_frontier_node_ids"],
        )

    def test_request_node_bound_produces_explicit_bounded_result(self) -> None:
        document = self.normalize()
        request = self.request(
            document,
            bounds={
                "max_paths": 8,
                "max_nodes": 3,
                "max_relations": 32,
                "max_evidence_records": 16,
            },
        )
        result = causal_query.query(document, request, self.policy)
        self.assertEqual("bounded", result["status"])
        self.assertEqual("node-bound-reached", result["truncation"]["reason"])
        self.assertGreaterEqual(result["truncation"]["omitted_at_least"], 1)
        self.assertEqual(
            [result["nodes"][0]["node_id"]],
            result["closure"]["open_frontier_node_ids"],
        )

    def test_projection_evidence_bound_is_explicit(self) -> None:
        document = self.normalize()
        request = self.request(
            document,
            bounds={
                "max_paths": 8,
                "max_nodes": 32,
                "max_relations": 32,
                "max_evidence_records": 3,
            },
        )
        result = causal_query.query(document, request, self.policy)
        self.assertEqual("bounded", result["status"])
        self.assertEqual(
            "evidence-bound-reached",
            result["truncation"]["reason"],
        )
        self.assertFalse(result["paths"])
        self.assertLessEqual(len(result["evidence"]), 3)

    def test_path_bound_retains_one_closed_branch_without_false_negative(
        self,
    ) -> None:
        document = self.normalize()
        self.add_second_closed_path(document)
        request = self.request(
            document,
            bounds={
                "max_paths": 1,
                "max_nodes": 32,
                "max_relations": 32,
                "max_evidence_records": 16,
            },
        )
        result = causal_query.query(document, request, self.policy)
        self.assertEqual("bounded", result["status"])
        self.assertEqual("path-bound-reached", result["truncation"]["reason"])
        self.assertEqual(1, len(result["paths"]))
        self.assertEqual("closed", result["paths"][0]["closure_state"])
        self.assertFalse(result["negative_statements"])

    def test_explicit_authority_loss_produces_unavailable_result(self) -> None:
        document = self.normalize(
            runtime_shape="final-only",
            authority_unavailable=True,
        )
        result = causal_query.query(
            document,
            self.request(document),
            self.policy,
        )
        self.assertEqual("unavailable", result["status"])
        self.assertFalse(result["paths"])
        self.assertEqual(
            ["required-evidence-unavailable"],
            result["closure"]["reason_codes"],
        )
        self.assertTrue(
            any(
                item["record_kind"] == "authority-unavailable"
                for item in result["evidence"]
            )
        )

    def test_query_is_byte_deterministic(self) -> None:
        document = self.normalize()
        request = self.request(document)
        first = causal_query.query(document, request, self.policy)
        second = causal_query.query(
            copy.deepcopy(document),
            copy.deepcopy(request),
            copy.deepcopy(self.policy),
        )
        self.assertEqual(
            provenance.canonical_json(first),
            provenance.canonical_json(second),
        )

    def test_request_must_bind_exact_scope_and_final_identity(self) -> None:
        document = self.normalize()
        request = self.request(document)
        request["scope"]["physical_side"] = "CLIENT"
        request["request_id"] = provenance.content_id(
            provenance.REQUEST_PREFIX,
            request,
            "request_id",
        )
        with self.assertRaisesRegex(
            causal_query.AtlasCausalQueryError,
            "primary scope is absent",
        ):
            causal_query.query(document, request, self.policy)

        request = self.request(document)
        request["final_runtime_record"]["runtime_record_sha256"] = "0" * 64
        request["request_id"] = provenance.content_id(
            provenance.REQUEST_PREFIX,
            request,
            "request_id",
        )
        with self.assertRaisesRegex(
            causal_query.AtlasCausalQueryError,
            "exact request final runtime record",
        ):
            causal_query.query(document, request, self.policy)

    def test_mutated_bidirectional_index_is_rejected(self) -> None:
        document = self.normalize()
        document["indexes"]["forward"][0]["relation_ids"] = []
        document["normalization_id"] = provenance.content_id(
            normalizer.NORMALIZATION_PREFIX,
            document,
            "normalization_id",
        )
        with self.assertRaisesRegex(
            causal_query.AtlasCausalQueryError,
            "indexes differ",
        ):
            causal_query.query(document, self.request(document), self.policy)

    def test_mutated_result_path_is_rejected_by_c01(self) -> None:
        document = self.normalize()
        result = causal_query.query(
            document,
            self.request(document),
            self.policy,
        )
        result["paths"][0]["node_ids"][0], result["paths"][0]["node_ids"][1] = (
            result["paths"][0]["node_ids"][1],
            result["paths"][0]["node_ids"][0],
        )
        result["paths"][0]["path_id"] = provenance.content_id(
            provenance.PATH_PREFIX,
            result["paths"][0],
            "path_id",
        )
        result["result_id"] = provenance.content_id(
            provenance.RESULT_PREFIX,
            result,
            "result_id",
        )
        with self.assertRaisesRegex(
            provenance.ProvenanceContractError,
            "edge-connected",
        ):
            provenance.validate_result(result, self.policy)

    def test_reviewed_example_is_valid_and_reproducible(self) -> None:
        if not causal_query.EXAMPLE_PATH.exists():
            self.skipTest("reviewed P03 example has not been published yet")
        example = read_json(causal_query.EXAMPLE_PATH)
        self.assertIs(example, provenance.validate_result(example, self.policy))
        document = self.normalize()
        regenerated = causal_query.query(
            document,
            example["request"],
            self.policy,
        )
        self.assertEqual(
            provenance.canonical_json(example),
            provenance.canonical_json(regenerated),
        )


if __name__ == "__main__":
    unittest.main()
