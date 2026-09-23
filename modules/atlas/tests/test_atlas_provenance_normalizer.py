#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import workbench_atlas.atlas_provenance_normalizer as normalizer
import workbench_atlas.atlas_causal_provenance_contract as provenance


SNAPSHOT = "SNAPSHOT-SUSY-0-1-16-11-9D3AA7AE0"
SOURCE_LOCK = "supersymmetry-legacy-forge"
SCOPE = {
    "snapshot_id": SNAPSHOT,
    "profile": "COMMON_FINAL_STATE",
    "physical_side": "DEDICATED_SERVER",
}


def reidentify(
    row: dict,
    *,
    field: str,
    prefix: str,
) -> dict:
    result = copy.deepcopy(row)
    result[field] = provenance.content_id(prefix, result, field)
    return result


def source_record(
    *,
    symbol: str,
    span_kind: str,
    byte_start: int,
    byte_end: int,
    span_sha256: str,
    policy: str,
) -> dict:
    identity = {
        "source_lock_id": SOURCE_LOCK,
        "source_id": "SRC-PACK",
        "repository": "https://github.com/SymmetricDevs/Supersymmetry",
        "revision": "9d3aa7ae0294bf27f0b8acbb893d61da23a06972",
        "tree": "3f6101aac500bca8ba892a5a7d060a3b490ee11c",
        "path": (
            "groovy/runConfig.json"
            if span_kind == "configuration-entry"
            else "groovy/postInit/pilot.groovy"
        ),
        "symbol": symbol,
        "line_start": 1,
        "line_end": 1,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "file_sha256": "1" * 64,
        "span_sha256": span_sha256,
        "extraction_policy_id": policy,
    }
    return {
        "source_span_id": (
            normalizer.SOURCE_SPAN_PREFIX + provenance.canonical_sha256(identity)
        ),
        "span_kind": span_kind,
        "language": "json" if span_kind == "configuration-entry" else "groovy",
        "identity": identity,
        "extraction": {
            "state": "syntax-exact",
            "syntax_kind": span_kind,
        },
    }


def operation(
    source_span_id: str,
    *,
    operation_index: int,
    operation_kind: str,
    target_state: str,
) -> dict:
    value_state = {
        "exact": "exact-literal",
        "symbolic": "qualified-expression",
        "unresolved": "complex-expression",
    }[target_state]
    payload = {
        "source_span_id": source_span_id,
        "stage_id": "groovy-post-init",
        "lifecycle_stage_id": "groovy-post-init",
        "root": "postInit/",
        "root_ordinal": 1,
        "script_ordinal": 7,
        "path": "groovy/postInit/pilot.groovy",
        "byte_start": 20 + operation_index,
        "operation_kind": operation_kind,
        "callee": {
            "addition": "buildAndRegister",
            "removal": "remove",
            "replacement": "replace",
        }.get(operation_kind, "setProperty"),
        "rule_id": "PACK-OP-PILOT-V1",
        "target": {
            "state": target_state,
            "value_state": value_state,
            "normalized_value": "MIXER" if target_state != "unresolved" else "",
            "expression_sha256": provenance.canonical_sha256(
                {"target": target_state, "index": operation_index}
            ),
            "expression_preview": "MIXER",
            "reason": {
                "exact": "literal-target",
                "symbolic": "recipe-map-symbol-requires-runtime-resolution",
                "unresolved": "dynamic-target-expression",
            }[target_state],
        },
        "execution_state": "not-observed-static-candidate",
        "operation_index": operation_index,
    }
    payload["operation_sha256"] = provenance.canonical_sha256(payload)
    return reidentify(
        {**payload, "operation_id": ""},
        field="operation_id",
        prefix=normalizer.OPERATION_PREFIX,
    )


def static_inputs(
    *,
    operations: list[dict] | None = None,
) -> tuple[dict, dict]:
    call = source_record(
        symbol="pilot#call:buildAndRegister@1:1",
        span_kind="call-site",
        byte_start=20,
        byte_end=37,
        span_sha256="2" * 64,
        policy="ATLAS-GROOVY-BALANCED-CALL-V1",
    )
    config = source_record(
        symbol="json-pointer:/loaders/postInit/0",
        span_kind="configuration-entry",
        byte_start=4,
        byte_end=14,
        span_sha256="3" * 64,
        policy="ATLAS-JSON-VALUE-SPAN-V1",
    )
    selection = reidentify(
        {
            "source_span_id": config["source_span_id"],
            "key_path": "/loaders/postInit/0",
            "selected_value": "postInit/",
            "selected_value_sha256": provenance.canonical_sha256("postInit/"),
            "selection_state": "locked-pack-value",
            "configuration_selection_id": "",
        },
        field="configuration_selection_id",
        prefix=normalizer.CONFIG_PREFIX,
    )
    if operations is None:
        operations = [
            operation(
                call["source_span_id"],
                operation_index=0,
                operation_kind="addition",
                target_state="exact",
            ),
            operation(
                call["source_span_id"],
                operation_index=1,
                operation_kind="addition",
                target_state="symbolic",
            ),
            operation(
                call["source_span_id"],
                operation_index=2,
                operation_kind="removal",
                target_state="unresolved",
            ),
        ]
    unresolved = []
    for item in operations:
        if item["target"]["state"] != "unresolved":
            continue
        unresolved.append(
            reidentify(
                {
                    "operation_id": item["operation_id"],
                    "source_span_id": item["source_span_id"],
                    "reason": item["target"]["reason"],
                    "boundary_state": "unresolved",
                    "boundary_id": "",
                },
                field="boundary_id",
                prefix=normalizer.BOUNDARY_PREFIX,
            )
        )
    source_index = {
        "snapshot_id": SNAPSHOT,
        "source_lock_id": SOURCE_LOCK,
        "index_id": "atlas-source-span-index:sha256:" + "4" * 64,
        "records": [config, call],
    }
    extraction = {
        "extraction_id": "atlas-pack-mutation-extraction:sha256:" + "5" * 64,
        "configuration_selections": [selection],
        "operations": operations,
        "unresolved_boundaries": unresolved,
    }
    return source_index, extraction


def exact_runtime_bundle(
    source_index: dict,
    extraction: dict,
) -> dict:
    operation_row = extraction["operations"][0]
    source_row = next(
        item
        for item in source_index["records"]
        if item["source_span_id"] == operation_row["source_span_id"]
    )
    source_evidence = normalizer._source_evidence(
        source_index["index_id"], source_row
    )
    source_node = normalizer._node(
        "source-span",
        SCOPE,
        source_row["identity"],
        [source_evidence["evidence_id"]],
    )
    operation_identity = {
        "source_span_id": source_node["node_id"],
        "stage_execution_id": "STAGE-EXEC-PILOT-0001",
        "operation_index": operation_row["operation_index"],
        "operation_sha256": operation_row["operation_sha256"],
    }
    runtime_node_id = (
        "rg:common_final_state_dedicated_server:recipe:pilot_exact"
    )
    state_sha256 = "6" * 64
    record_sha256 = "7" * 64
    capture = normalizer._evidence(
        "runtime-mechanics",
        "final-runtime-record",
        {
            "snapshot_id": SNAPSHOT,
            "profile": SCOPE["profile"],
            "physical_side": SCOPE["physical_side"],
            "runtime_node_id": runtime_node_id,
            "runtime_kind": "recipe",
            "state_sha256": state_sha256,
            "runtime_record_sha256": record_sha256,
        },
    )
    runtime_state = normalizer._node(
        "runtime-state",
        SCOPE,
        {
            "runtime_node_id": runtime_node_id,
            "state_sha256": state_sha256,
            "capture_evidence_id": capture["evidence_id"],
        },
        [capture["evidence_id"]],
    )
    final_node = normalizer._node(
        "final-runtime-record",
        SCOPE,
        {
            "runtime_node_id": runtime_node_id,
            "runtime_kind": "recipe",
            "runtime_record_sha256": record_sha256,
            "capture_evidence_id": capture["evidence_id"],
        },
        [capture["evidence_id"]],
    )
    related = sorted(
        {
            provenance.canonical_sha256(source_node["identity"]),
            provenance.canonical_sha256(operation_identity),
            provenance.canonical_sha256(runtime_state["identity"]),
        }
    )
    stage_evidence = normalizer._evidence(
        "runtime-mechanics",
        "stage-execution",
        {
            "snapshot_id": SNAPSHOT,
            "profile": SCOPE["profile"],
            "physical_side": SCOPE["physical_side"],
            "stage_id": "groovy-post-init",
            "stage_execution_id": operation_identity["stage_execution_id"],
            "occurrence_id": "OCC-GROOVY-POST-INIT-0001",
            "executed_node_class": "script-operation",
            "executed_identity_sha256": provenance.canonical_sha256(
                operation_identity
            ),
            "operation_id": operation_row["operation_id"],
            "related_identity_sha256s": related,
        },
    )
    operation_node = normalizer._node(
        "script-operation",
        SCOPE,
        operation_identity,
        [stage_evidence["evidence_id"]],
    )
    lifecycle_identity = {
        "lifecycle_model_id": "FORGE-CLEANROOM-SUSY-LIFECYCLE-V1",
        "stage_id": "groovy-post-init",
        "occurrence_id": "OCC-GROOVY-POST-INIT-0001",
        "ordinal": 0,
    }
    lifecycle_evidence = normalizer._evidence(
        "runtime-mechanics",
        "stage-execution",
        {
            "snapshot_id": SNAPSHOT,
            "profile": SCOPE["profile"],
            "physical_side": SCOPE["physical_side"],
            "stage_id": "groovy-post-init",
            "stage_execution_id": operation_identity["stage_execution_id"],
            "occurrence_id": lifecycle_identity["occurrence_id"],
            "executed_node_class": "lifecycle-event",
            "executed_identity_sha256": provenance.canonical_sha256(
                lifecycle_identity
            ),
            "related_identity_sha256s": [
                provenance.canonical_sha256(lifecycle_identity)
            ],
        },
    )
    lifecycle_node = normalizer._node(
        "lifecycle-event",
        SCOPE,
        lifecycle_identity,
        [lifecycle_evidence["evidence_id"]],
    )
    transition = normalizer._evidence(
        "runtime-mechanics",
        "state-transition",
        {
            "snapshot_id": SNAPSHOT,
            "profile": SCOPE["profile"],
            "physical_side": SCOPE["physical_side"],
            "stage_id": "groovy-post-init",
            "stage_execution_id": operation_identity["stage_execution_id"],
            "operation_id": operation_row["operation_id"],
            "operation_node_id": operation_node["node_id"],
            "predicate": "registers",
            "subject_node_id": operation_node["node_id"],
            "object_node_id": runtime_state["node_id"],
        },
    )
    register = normalizer._relation(
        "registers",
        operation_node["node_id"],
        runtime_state["node_id"],
        "causation",
        "groovy-post-init",
        [stage_evidence["evidence_id"], transition["evidence_id"]],
    )
    observed = normalizer._relation(
        "observed_as_final",
        runtime_state["node_id"],
        final_node["node_id"],
        "observation",
        "final-observation",
        [capture["evidence_id"]],
    )
    selection = extraction["configuration_selections"][0]
    config_source = next(
        item
        for item in source_index["records"]
        if item["source_span_id"] == selection["source_span_id"]
    )
    config_source_evidence = normalizer._source_evidence(
        source_index["index_id"], config_source
    )
    config_source_node = normalizer._node(
        "source-span",
        SCOPE,
        config_source["identity"],
        [config_source_evidence["evidence_id"]],
    )
    config_node = normalizer._node(
        "configuration-entry",
        SCOPE,
        {
            "source_span_id": config_source_node["node_id"],
            "key_path": selection["key_path"],
            "selected_value_sha256": selection["selected_value_sha256"],
        },
        [config_source_evidence["evidence_id"]],
    )
    config_evidence = normalizer._evidence(
        "runtime-mechanics",
        "configuration-selection",
        {
            "snapshot_id": SNAPSHOT,
            "profile": SCOPE["profile"],
            "physical_side": SCOPE["physical_side"],
            "stage_id": "groovy-post-init",
            "stage_execution_id": operation_identity["stage_execution_id"],
            "configuration_selection_id": selection[
                "configuration_selection_id"
            ],
            "configuration_node_id": config_node["node_id"],
            "selected_value_sha256": selection["selected_value_sha256"],
        },
    )
    loads = normalizer._relation(
        "loads_configuration",
        config_node["node_id"],
        lifecycle_node["node_id"],
        "participation",
        "groovy-post-init",
        [config_evidence["evidence_id"]],
    )
    rows = {
        "schema_version": 1,
        "format": normalizer.RUNTIME_BUNDLE_FORMAT,
        "bundle_id": "",
        "snapshot_id": SNAPSHOT,
        "source_lock_id": SOURCE_LOCK,
        "scopes": [copy.deepcopy(SCOPE)],
        "nodes": sorted(
            [operation_node, lifecycle_node, runtime_state, final_node],
            key=lambda item: item["node_id"],
        ),
        "relations": sorted(
            [register, loads, observed], key=lambda item: item["relation_id"]
        ),
        "evidence": sorted(
            [
                capture,
                stage_evidence,
                lifecycle_evidence,
                transition,
                config_evidence,
            ],
            key=lambda item: item["evidence_id"],
        ),
        "operation_bindings": [
            {
                "operation_id": operation_row["operation_id"],
                "node_id": operation_node["node_id"],
            }
        ],
        "configuration_bindings": [
            {
                "configuration_selection_id": selection[
                    "configuration_selection_id"
                ],
                "relation_id": loads["relation_id"],
            }
        ],
    }
    rows["bundle_id"] = provenance.content_id(
        normalizer.RUNTIME_BUNDLE_PREFIX, rows, "bundle_id"
    )
    return rows


class AtlasProvenanceNormalizerTests(unittest.TestCase):
    def test_cli_path_arguments_and_contract_annotations_resolve(self) -> None:
        from typing import get_type_hints

        arguments = normalizer.parser().parse_args(["check", "normalized.json"])
        self.assertEqual(arguments.artifact, Path("normalized.json"))
        self.assertIs(get_type_hints(provenance.load_policy)["path"], Path)

    def reidentify_artifact(self, document: dict) -> None:
        document["normalization_id"] = provenance.content_id(
            normalizer.NORMALIZATION_PREFIX,
            document,
            "normalization_id",
        )

    @staticmethod
    def reindex_artifact(document: dict) -> None:
        forward: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = {}
        for relation in document["relations"]:
            forward.setdefault(relation["subject_node_id"], []).append(
                relation["relation_id"]
            )
            reverse.setdefault(relation["object_node_id"], []).append(
                relation["relation_id"]
            )
        document["indexes"] = {
            "forward": [
                {"node_id": node_id, "relation_ids": sorted(ids)}
                for node_id, ids in sorted(forward.items())
            ],
            "reverse": [
                {"node_id": node_id, "relation_ids": sorted(ids)}
                for node_id, ids in sorted(reverse.items())
            ],
        }

    def test_static_candidates_map_scoped_nodes_and_remain_open(self) -> None:
        source_index, extraction = static_inputs()
        bundle = normalizer.empty_runtime_bundle(
            snapshot_id=SNAPSHOT,
            source_lock_id=SOURCE_LOCK,
            scopes=[SCOPE],
        )
        document = normalizer._normalize_validated(
            source_index, extraction, bundle
        )
        self.assertIs(normalizer.validate_normalization(document), document)
        self.assertEqual(3, len(document["operation_candidates"]))
        self.assertTrue(
            all(
                item["execution_state"] == "static-only"
                and item["promoted_node_id"] is None
                for item in document["operation_candidates"]
            )
        )
        self.assertEqual(
            {
                "runtime-transition-unobserved",
                "identity-reconciliation-unresolved",
                "dynamic-script-unresolved",
            },
            {item["reason_code"] for item in document["frontiers"]},
        )
        self.assertFalse(document["relations"])
        self.assertEqual({"forward": [], "reverse": []}, document["indexes"])
        for mapping in document["source_mappings"]:
            self.assertNotEqual(mapping["source_span_id"], mapping["node_id"])

    def test_exact_execution_and_endpoints_close_only_the_normalized_frontier(self) -> None:
        source_index, extraction = static_inputs(
            operations=[
                operation(
                    source_record(
                        symbol="pilot#call:buildAndRegister@1:1",
                        span_kind="call-site",
                        byte_start=20,
                        byte_end=37,
                        span_sha256="2" * 64,
                        policy="ATLAS-GROOVY-BALANCED-CALL-V1",
                    )["source_span_id"],
                    operation_index=0,
                    operation_kind="addition",
                    target_state="exact",
                )
            ]
        )
        # static_inputs creates the same deterministic call-span identity.
        bundle = exact_runtime_bundle(source_index, extraction)
        document = normalizer._normalize_validated(
            source_index, extraction, bundle
        )
        self.assertIs(normalizer.validate_normalization(document), document)
        self.assertFalse(document["frontiers"])
        self.assertEqual(1, document["summary"]["executed_operation_count"])
        self.assertEqual(
            {"invokes", "loads_configuration", "registers", "observed_as_final"},
            {item["predicate"] for item in document["relations"]},
        )
        self.assertTrue(document["indexes"]["forward"])
        self.assertTrue(document["indexes"]["reverse"])

    def test_missing_transition_keeps_executed_operation_partial(self) -> None:
        source_index, extraction = static_inputs(
            operations=[
                operation(
                    source_record(
                        symbol="pilot#call:buildAndRegister@1:1",
                        span_kind="call-site",
                        byte_start=20,
                        byte_end=37,
                        span_sha256="2" * 64,
                        policy="ATLAS-GROOVY-BALANCED-CALL-V1",
                    )["source_span_id"],
                    operation_index=0,
                    operation_kind="addition",
                    target_state="exact",
                )
            ]
        )
        bundle = exact_runtime_bundle(source_index, extraction)
        register = next(
            item for item in bundle["relations"] if item["predicate"] == "registers"
        )
        used = set(register["evidence_ids"])
        bundle["relations"].remove(register)
        bundle["evidence"] = [
            item for item in bundle["evidence"] if item["evidence_id"] not in used
        ]
        # Keep stage execution because the operation node still references it.
        operation_node = next(
            item
            for item in bundle["nodes"]
            if item["node_class"] == "script-operation"
        )
        stage_id = operation_node["evidence_ids"][0]
        stage_row = next(
            item
            for item in exact_runtime_bundle(source_index, extraction)["evidence"]
            if item["evidence_id"] == stage_id
        )
        if not any(item["evidence_id"] == stage_id for item in bundle["evidence"]):
            bundle["evidence"].append(stage_row)
            bundle["evidence"].sort(key=lambda item: item["evidence_id"])
        bundle["bundle_id"] = provenance.content_id(
            normalizer.RUNTIME_BUNDLE_PREFIX, bundle, "bundle_id"
        )
        document = normalizer._normalize_validated(
            source_index, extraction, bundle
        )
        self.assertEqual(
            ["runtime-transition-unobserved"],
            [
                item["reason_code"]
                for item in document["frontiers"]
                if item["primitive_kind"] == "operation"
            ],
        )

    def test_wrong_profile_and_namespace_similarity_fail_closed(self) -> None:
        source_index, extraction = static_inputs(
            operations=[
                operation(
                    source_record(
                        symbol="pilot#call:buildAndRegister@1:1",
                        span_kind="call-site",
                        byte_start=20,
                        byte_end=37,
                        span_sha256="2" * 64,
                        policy="ATLAS-GROOVY-BALANCED-CALL-V1",
                    )["source_span_id"],
                    operation_index=0,
                    operation_kind="addition",
                    target_state="exact",
                )
            ]
        )
        bundle = exact_runtime_bundle(source_index, extraction)
        mutated = copy.deepcopy(bundle)
        operation_node = next(
            item
            for item in mutated["nodes"]
            if item["node_class"] == "script-operation"
        )
        operation_node["scope"]["physical_side"] = "CLIENT"
        operation_node["node_id"] = provenance.content_id(
            provenance.NODE_PREFIX, operation_node, "node_id"
        )
        mutated["bundle_id"] = provenance.content_id(
            normalizer.RUNTIME_BUNDLE_PREFIX, mutated, "bundle_id"
        )
        with self.assertRaisesRegex(
            normalizer.AtlasProvenanceNormalizationError,
            "execution evidence|operation binding does not resolve",
        ):
            normalizer._normalize_validated(
                source_index, extraction, mutated
            )

        static_bundle = normalizer.empty_runtime_bundle(
            snapshot_id=SNAPSHOT,
            source_lock_id=SOURCE_LOCK,
            scopes=[SCOPE],
        )
        document = normalizer._normalize_validated(
            source_index, extraction, static_bundle
        )
        self.assertTrue(document["frontiers"])
        self.assertFalse(
            any(
                "MIXER" in item.get("predicate", "")
                for item in document["relations"]
            )
        )

    def test_mutations_of_indexes_evidence_and_identity_are_rejected(self) -> None:
        source_index, extraction = static_inputs()
        document = normalizer._normalize_validated(
            source_index,
            extraction,
            normalizer.empty_runtime_bundle(
                snapshot_id=SNAPSHOT,
                source_lock_id=SOURCE_LOCK,
                scopes=[SCOPE],
            ),
        )
        mutated = copy.deepcopy(document)
        mutated["source_mappings"][0]["node_id"] = mutated["source_mappings"][0][
            "source_span_id"
        ].replace("atlas-source-span:", "atlas-provenance-node:")
        self.reidentify_artifact(mutated)
        with self.assertRaisesRegex(
            normalizer.AtlasProvenanceNormalizationError,
            "source primitive mapping",
        ):
            normalizer.validate_normalization(mutated)

        exact_source, exact_extraction = static_inputs(
            operations=[
                operation(
                    source_record(
                        symbol="pilot#call:buildAndRegister@1:1",
                        span_kind="call-site",
                        byte_start=20,
                        byte_end=37,
                        span_sha256="2" * 64,
                        policy="ATLAS-GROOVY-BALANCED-CALL-V1",
                    )["source_span_id"],
                    operation_index=0,
                    operation_kind="addition",
                    target_state="exact",
                )
            ]
        )
        closed = normalizer._normalize_validated(
            exact_source,
            exact_extraction,
            exact_runtime_bundle(exact_source, exact_extraction),
        )
        mutated = copy.deepcopy(closed)
        mutated["indexes"]["forward"] = []
        self.reidentify_artifact(mutated)
        with self.assertRaisesRegex(
            normalizer.AtlasProvenanceNormalizationError,
            "forward/reverse indexes",
        ):
            normalizer.validate_normalization(mutated)

        mutated = copy.deepcopy(closed)
        mutated["evidence"][0]["record"]["forged"] = True
        self.reidentify_artifact(mutated)
        with self.assertRaisesRegex(
            normalizer.AtlasProvenanceNormalizationError,
            "evidence record digest differs",
        ):
            normalizer.validate_normalization(mutated)

    def test_normalization_is_byte_repeatable(self) -> None:
        source_index, extraction = static_inputs()
        bundle = normalizer.empty_runtime_bundle(
            snapshot_id=SNAPSHOT,
            source_lock_id=SOURCE_LOCK,
            scopes=[SCOPE],
        )
        first = normalizer._normalize_validated(
            source_index, extraction, bundle
        )
        second = normalizer._normalize_validated(
            source_index, extraction, bundle
        )
        self.assertEqual(
            provenance.canonical_json(first),
            provenance.canonical_json(second),
        )

    def test_scopes_are_normalized_independently(self) -> None:
        source_index, extraction = static_inputs()
        client_scope = {
            "snapshot_id": SNAPSHOT,
            "profile": "COMMON_FINAL_STATE",
            "physical_side": "CLIENT",
        }
        document = normalizer._normalize_validated(
            source_index,
            extraction,
            normalizer.empty_runtime_bundle(
                snapshot_id=SNAPSHOT,
                source_lock_id=SOURCE_LOCK,
                scopes=[client_scope, SCOPE],
            ),
        )
        self.assertEqual(2, document["summary"]["scope_count"])
        self.assertEqual(6, document["summary"]["operation_candidate_count"])
        self.assertEqual(8, document["summary"]["frontier_count"])
        self.assertEqual(
            {"CLIENT", "DEDICATED_SERVER"},
            {
                item["scope"]["physical_side"]
                for item in document["operation_candidates"]
            },
        )

    def test_reversed_final_lifecycle_and_wrong_transition_operation_fail(self) -> None:
        source_index, extraction = static_inputs(
            operations=[
                operation(
                    source_record(
                        symbol="pilot#call:buildAndRegister@1:1",
                        span_kind="call-site",
                        byte_start=20,
                        byte_end=37,
                        span_sha256="2" * 64,
                        policy="ATLAS-GROOVY-BALANCED-CALL-V1",
                    )["source_span_id"],
                    operation_index=0,
                    operation_kind="addition",
                    target_state="exact",
                )
            ]
        )
        document = normalizer._normalize_validated(
            source_index,
            extraction,
            exact_runtime_bundle(source_index, extraction),
        )
        mutated = copy.deepcopy(document)
        observed = next(
            item
            for item in mutated["relations"]
            if item["predicate"] == "observed_as_final"
        )
        observed["lifecycle_stage_id"] = "groovy-post-init"
        observed["relation_id"] = provenance.content_id(
            provenance.RELATION_PREFIX, observed, "relation_id"
        )
        mutated["relations"].sort(key=lambda item: item["relation_id"])
        self.reindex_artifact(mutated)
        self.reidentify_artifact(mutated)
        with self.assertRaisesRegex(
            normalizer.AtlasProvenanceNormalizationError,
            "wrong lifecycle stage",
        ):
            normalizer.validate_normalization(mutated)

        bundle = exact_runtime_bundle(source_index, extraction)
        transition = next(
            item
            for item in bundle["evidence"]
            if item["record_kind"] == "state-transition"
        )
        transition["record"]["operation_id"] = (
            "atlas-pack-operation:sha256:" + "9" * 64
        )
        transition["record_sha256"] = provenance.canonical_sha256(
            transition["record"]
        )
        old_evidence_id = transition["evidence_id"]
        transition["evidence_id"] = provenance.content_id(
            provenance.EVIDENCE_PREFIX, transition, "evidence_id"
        )
        register = next(
            item
            for item in bundle["relations"]
            if item["predicate"] == "registers"
        )
        register["evidence_ids"] = [
            transition["evidence_id"] if item == old_evidence_id else item
            for item in register["evidence_ids"]
        ]
        register["evidence_ids"].sort()
        register["relation_id"] = provenance.content_id(
            provenance.RELATION_PREFIX, register, "relation_id"
        )
        bundle["relations"].sort(key=lambda item: item["relation_id"])
        bundle["evidence"].sort(key=lambda item: item["evidence_id"])
        bundle["bundle_id"] = provenance.content_id(
            normalizer.RUNTIME_BUNDLE_PREFIX, bundle, "bundle_id"
        )
        with self.assertRaisesRegex(
            normalizer.AtlasProvenanceNormalizationError,
            "operation transition does not bind",
        ):
            normalizer._normalize_validated(
                source_index, extraction, bundle
            )

    def test_reviewed_example_validates(self) -> None:
        example = json.loads(normalizer.EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertIs(normalizer.validate_normalization(example), example)


if __name__ == "__main__":
    unittest.main()
