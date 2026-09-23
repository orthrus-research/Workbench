from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
FIXTURES = ROOT / "modules/crucible/tests/fixtures/crucible-v2"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible import RecordValidationError, RelationResolvers, assert_schema_registry_ready, load_canonical_record, registered_contracts, semantic_root_v2, seal_record, validate_publication, validate_record, ValidatedRecord, AdapterValidatedReference, ValidatedExternalReference
from workbench_api.canonical import canonical_json_bytes, record_content_id
from workbench_crucible.synthetic import (  # noqa: E402
    SyntheticPublication,
    build_synthetic_publication,
)


GRAPH_BODY_KINDS = {
    "node",
    "edge",
    "property",
    "evidence-link",
    "refinement-mapping",
    "frontier",
    "conflict",
}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise AssertionError(f"fixture pointer is not absolute: {pointer}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _at(value, pointer: str):
    current = value
    for part in _parts(pointer):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def _replace(value, pointer: str, replacement) -> None:
    parts = _parts(pointer)
    current = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    tail = parts[-1]
    if isinstance(current, list):
        current[int(tail)] = replacement
    else:
        current[tail] = replacement


def _reidentify(record: dict) -> dict:
    record.pop("id", None)
    record["id"] = record_content_id(record)
    return record


def _recompute_aggregate(record: dict) -> None:
    field_domain = {
        "evidence-set-revision": (
            "semantic_root",
            "evidence-set-revision/aggregate",
        ),
        "dependency-manifest": (
            "semantic_root",
            "dependency-manifest/aggregate",
        ),
        "graph-revision": (
            "aggregate_semantic_root",
            "graph-revision/aggregate",
        ),
        "graph-set-revision": (
            "aggregate_semantic_root",
            "graph-set-revision/aggregate",
        ),
    }
    field, domain = field_domain[record["kind"]]
    projection = dict(record)
    projection.pop("id", None)
    projection.pop(field, None)
    record[field] = semantic_root_v2(domain, projection)


class CrucibleV2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.publication = build_synthetic_publication()
        cls.malicious = _read_json(FIXTURES / "malicious-cases.json")["cases"]

    def assertDiagnostic(self, error, phase: str, code: str) -> None:
        actual = {(item.phase.value, item.code) for item in error.diagnostics}
        self.assertIn((phase, code), actual, actual)

    def relations(self, **overrides) -> RelationResolvers:
        values = {
            "record_resolver": self.publication.record_resolver,
            "blob_resolver": self.publication.blob_resolver,
            "external_reference_resolver": self.publication.external_reference_resolver,
            "trusted_adapters": self.publication.trusted_adapters,
            "policy_validator": self.publication.policy_validator,
            "require_complete": True,
        }
        values.update(overrides)
        return RelationResolvers(**values)

    def test_schema_registry_is_closed_and_complete(self) -> None:
        resources = assert_schema_registry_ready()
        self.assertEqual(12, len(resources))
        self.assertEqual(
            {
                "object-descriptor",
                "evidence-record",
                "admission-record",
                "ledger-entry",
                "evidence-set-revision",
                "graph-record",
                "graph-revision",
                "graph-set-revision",
                "materialization-recipe",
                "dependency-manifest",
                "reference-event",
            },
            {contract.kind for contract in registered_contracts()},
        )

    def test_unknown_field_is_rejected_before_identity_is_calculated(self) -> None:
        candidate = self.publication.record("evidence-record")
        candidate.pop("id")
        candidate["unexpected"] = True
        with mock.patch(
            "workbench_crucible.records.record_content_id",
            side_effect=AssertionError("identity must not run"),
        ) as identity:
            with self.assertRaises(RecordValidationError) as caught:
                seal_record(candidate)
        identity.assert_not_called()
        self.assertDiagnostic(
            caught.exception, "schema", "schema.additionalProperties"
        )

    def test_synthetic_publication_matches_retained_identity_oracle(self) -> None:
        expected = _read_json(FIXTURES / "expected-publication.json")
        self.assertEqual(expected, self.publication.summary())
        validated = self.publication.validate()
        self.assertEqual(
            [record.id for record in self.publication.records],
            [record.id for record in validated],
        )
        reference = self.publication.record("reference-event")
        self.assertEqual(
            self.publication.aliases["graph-set-revision"],
            reference["new_target_id"],
        )

    def test_clean_rebuild_is_byte_identical(self) -> None:
        rebuilt = build_synthetic_publication()
        self.assertEqual(self.publication.summary(), rebuilt.summary())
        first = [record.canonical_bytes for record in self.publication.records]
        second = [record.canonical_bytes for record in rebuilt.records]
        self.assertEqual(first, second)

    def test_every_record_round_trips_from_exact_canonical_bytes(self) -> None:
        for record in self.publication.records:
            with self.subTest(kind=record.kind, record_id=record.id):
                encoded = record.canonical_bytes
                loaded = load_canonical_record(encoded)
                self.assertEqual(record.id, loaded.id)
                self.assertEqual(encoded, loaded.canonical_bytes)

    def test_publication_snapshots_are_deeply_immutable_to_callers(self) -> None:
        before = self.publication.summary()
        evidence = self.publication.record("evidence-record")
        evidence["limitations"][0]["detail"] = "caller mutation"
        evidence["source_bindings"].clear()
        self.assertNotEqual(evidence, self.publication.record("evidence-record"))
        self.assertEqual(before, self.publication.summary())
        with self.assertRaises(TypeError):
            self.publication.records_by_id["forged"] = self.publication.records[0]
        with self.assertRaises(TypeError):
            self.publication.blobs_by_id["forged"] = b"forged"

    def test_forward_reference_inside_graph_shard_is_valid(self) -> None:
        graph = self.publication.record("graph-revision")
        partition = next(
            item for item in graph["partitions"] if item["record_kind"] == "conflict"
        )
        raw = self.publication.blobs_by_id[
            self.publication.records_by_id[partition["object_descriptor_id"]]
            .to_dict()["object_id"]
        ]
        rows = [load_canonical_record(line).to_dict() for line in raw.splitlines()]
        self.assertEqual("conflict:a-forward", rows[0]["logical_key"])
        self.assertEqual("conflict:z-target", rows[1]["logical_key"])
        self.assertIn(rows[1]["id"], rows[0]["body"]["conflicting_graph_record_ids"])
        self.publication.validate()

    def test_reference_shaped_semantic_text_is_never_resolved(self) -> None:
        shaped = "policy:sha256:" + "a" * 64
        seen: list[str] = []

        def external(expectation):
            seen.append(expectation.record_id)
            return self.publication.external_reference_resolver(expectation)

        relations = RelationResolvers(
            record_resolver=self.publication.record_resolver,
            blob_resolver=self.publication.blob_resolver,
            external_reference_resolver=external,
            trusted_adapters=self.publication.trusted_adapters,
            policy_validator=self.publication.policy_validator,
            require_complete=True,
        )
        validate_publication(self.publication.records, relations=relations)
        self.assertNotIn(shaped, seen)

    def test_external_resolver_cannot_mutate_its_reference_expectation(self) -> None:
        original = self.publication.record("graph-revision")["tool_ids"][0]
        replacement = next(
            record_id
            for record_id in self.publication.external_records_by_id
            if record_id.startswith("tool:sha256:") and record_id != original
        )

        def mutating_resolver(expectation):
            if expectation.record_id != original:
                return self.publication.external_reference_resolver(expectation)
            object.__setattr__(expectation, "record_id", replacement)
            return ValidatedExternalReference(
                record_id=replacement,
                kind="tool",
                canonical_bytes=self.publication.external_records_by_id[replacement],
            )

        with self.assertRaises(RecordValidationError) as caught:
            validate_publication(
                self.publication.records,
                relations=self.relations(
                    external_reference_resolver=mutating_resolver
                ),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.external-resolver-mutation",
        )

    def test_external_reference_cannot_be_reassigned_to_another_owner(self) -> None:
        evidence = self.publication.record("evidence-record")
        original_producer = evidence["producer_id"]
        evidence["authority_owner"] = self.publication.record(
            "admission-record"
        )["decision_owner"]
        _reidentify(evidence)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(evidence, relations=self.relations())
        self.assertDiagnostic(
            caught.exception, "relation", "relation.external-unavailable"
        )
        self.assertIsNone(
            self.publication.external_reference_resolver(
                type(
                    "Expectation",
                    (),
                    {
                        "record_id": original_producer,
                        "expected_authority_id": evidence["authority_owner"][
                            "owner_authority_id"
                        ],
                        "expected_owner_revision_id": evidence["authority_owner"][
                            "owner_revision_id"
                        ],
                        "expected_adapter_id": evidence["authority_owner"][
                            "authority_adapter_id"
                        ],
                    },
                )()
            )
        )

    def test_forged_validated_record_snapshot_from_resolver_is_rejected(self) -> None:
        evidence = self.publication.record("evidence-record")
        target = self.publication.records_by_id[
            evidence["payload_object_descriptor_id"]
        ]
        other = self.publication.records_by_id[
            self.publication.aliases["source:a"]
        ]
        forged = ValidatedRecord(
            id=target.id,
            kind=target.kind,
            format=target.format,
            schema_id=target.schema_id,
            canonical_bytes=other.canonical_bytes,
            references=target.references,
        )

        def resolver(record_id: str):
            if record_id == target.id:
                return forged
            return self.publication.record_resolver(record_id)

        with self.assertRaises(RecordValidationError) as caught:
            validate_record(evidence, relations=self.relations(record_resolver=resolver))
        self.assertDiagnostic(
            caught.exception, "relation", "relation.invalid-target"
        )

    def test_external_result_cannot_nominate_mismatched_adapter(self) -> None:
        evidence = self.publication.record("evidence-record")
        target = evidence["producer_id"]

        def external(expectation):
            result = self.publication.external_reference_resolver(expectation)
            if expectation.record_id != target or result is None:
                return result
            self.assertIsInstance(result, AdapterValidatedReference)
            return AdapterValidatedReference(
                record_id=result.record_id,
                kind=result.kind,
                canonical_bytes=result.canonical_bytes,
                adapter_id="authority-adapter:sha256:" + "f" * 64,
                owner_authority_id=result.owner_authority_id,
                owner_revision_id=result.owner_revision_id,
            )

        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                evidence,
                relations=self.relations(external_reference_resolver=external),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.external-authority-binding",
        )

    def test_publication_requires_owner_policy_hook(self) -> None:
        with self.assertRaisesRegex(ValueError, "owner-policy validation port"):
            validate_publication(
                self.publication.records,
                relations=self.relations(policy_validator=None),
            )

    def test_false_owner_policy_result_rejects_publication(self) -> None:
        with self.assertRaises(RecordValidationError) as caught:
            validate_publication(
                self.publication.records,
                relations=self.relations(policy_validator=lambda request: False),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.policy-rejected"
        )

    def test_owner_policy_cannot_mutate_its_isolated_record_view(self) -> None:
        def mutating_policy(request):
            object.__setattr__(
                request.record,
                "id",
                self.publication.aliases["graph-revision"],
            )
            return True

        with self.assertRaises(RecordValidationError) as caught:
            validate_publication(
                self.publication.records,
                relations=self.relations(policy_validator=mutating_policy),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.policy-validator-mutation",
        )

        # The callback received only reconstructed transport values; its
        # forced mutation cannot corrupt the publication's pinned records.
        validate_publication(self.publication.records, relations=self.relations())

    def test_derivation_policy_runs_under_exact_recipe_owner(self) -> None:
        graph_record = self.publication.record("graph-record:edge")
        recipe_owner = self.publication.record("materialization-recipe")[
            "recipe_owner"
        ]
        semantic_owner = graph_record["authority_owner"]
        self.assertNotEqual(recipe_owner, semantic_owner)
        observed = []

        def policy(request):
            if request.rule == "derivation":
                observed.append(request.authority_scope)
            return True

        validate_record(
            graph_record,
            relations=self.relations(policy_validator=policy),
        )
        self.assertTrue(observed)
        expected_scope = (
            recipe_owner["owner_authority_id"],
            recipe_owner["owner_revision_id"],
            recipe_owner["authority_adapter_id"],
        )
        self.assertEqual(
            {expected_scope},
            {
                (
                    scope.owner_authority_id,
                    scope.owner_revision_id,
                    scope.authority_adapter_id,
                )
                for scope in observed
            },
        )

    def test_admission_decision_cannot_be_replayed_for_another_outcome(self) -> None:
        admission = self.publication.record("admission-record")
        admission["outcome"] = "rejected"
        admission["eligible_for_materialization"] = False
        _reidentify(admission)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(admission, relations=self.relations())
        self.assertDiagnostic(
            caught.exception, "relation", "relation.policy-rejected"
        )

    def test_reference_authorization_cannot_be_replayed_for_another_target(self) -> None:
        event = self.publication.record("reference-event")
        event["new_target_kind"] = "graph-revision"
        event["new_target_id"] = self.publication.aliases["graph-revision"]
        _reidentify(event)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(event, relations=self.relations())
        self.assertDiagnostic(
            caught.exception, "relation", "relation.policy-rejected"
        )

    def test_fabricated_recipe_requires_both_exact_owner_attestations(self) -> None:
        recipe = self.publication.record("materialization-recipe")
        recipe["semantic_version"] = "1.0.1"
        _reidentify(recipe)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(recipe, relations=self.relations())
        self.assertDiagnostic(
            caught.exception, "relation", "relation.policy-rejected"
        )

    def test_graph_owner_role_hooks_fail_closed_independently(self) -> None:
        for rule in ("graph-materialization", "graph-category-semantics"):
            with self.subTest(rule=rule):
                def policy(request, rejected_rule=rule):
                    if request.rule == rejected_rule:
                        return False
                    return self.publication.policy_validator(request)

                with self.assertRaises(RecordValidationError) as caught:
                    validate_publication(
                        self.publication.records,
                        relations=self.relations(policy_validator=policy),
                    )
                self.assertDiagnostic(
                    caught.exception, "relation", "relation.policy-rejected"
                )

    def test_same_coordinate_input_revisions_retain_distinct_recipe_roles(self) -> None:
        refinement = self.publication.record("graph-revision:refinement")
        bindings = {
            item["graph_input_contract_key"]: item["graph_revision_id"]
            for item in refinement["input_graph_bindings"]
        }
        baseline_id = bindings["synthetic.input-baseline"]
        candidate_id = bindings["synthetic.input-main"]
        self.assertNotEqual(baseline_id, candidate_id)
        baseline = self.publication.records_by_id[baseline_id].to_dict()
        candidate = self.publication.records_by_id[candidate_id].to_dict()
        self.assertEqual(
            (
                baseline["graph_family_id"],
                baseline["category_id"],
                baseline["resolution_id"],
            ),
            (
                candidate["graph_family_id"],
                candidate["category_id"],
                candidate["resolution_id"],
            ),
        )

    def test_empty_known_absent_graph_has_empty_dependency_footprints(self) -> None:
        graph = self.publication.record("graph-revision:empty")
        dependency = self.publication.records_by_id[
            graph["dependency_manifest_id"]
        ].to_dict()
        self.assertEqual([], graph["partitions"])
        self.assertEqual([], dependency["footprints"])
        self.assertEqual("known-absent", graph["evidence_state"])
        self.publication.validate()

    def test_nonempty_graph_rejects_empty_dependency_footprints(self) -> None:
        graph = self.publication.record("graph-revision")
        graph["dependency_manifest_id"] = self.publication.aliases[
            "dependency-manifest:empty"
        ]
        _recompute_aggregate(graph)
        _reidentify(graph)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                graph,
                relations=self.relations(policy_validator=lambda request: True),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.dependency-partitions",
        )

    def test_false_compatibility_decision_binding_is_owner_rejected(self) -> None:
        graph_set = self.publication.record("graph-set-revision")
        graph_set["members"][0]["alignment"]["compatibility_decision_id"] = (
            graph_set["members"][1]["alignment"]["compatibility_decision_id"]
        )
        _recompute_aggregate(graph_set)
        _reidentify(graph_set)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(graph_set, relations=self.relations())
        self.assertDiagnostic(
            caught.exception, "relation", "relation.policy-rejected"
        )

    def test_exact_dependency_cannot_claim_an_empty_footprint(self) -> None:
        dependency = self.publication.record("dependency-manifest")
        footprint = dependency["footprints"][0]
        for field in (
            "evidence_record_ids",
            "admission_record_ids",
            "admission_partitions",
            "evidence_partitions",
            "source_graph_records",
            "source_graph_partitions",
        ):
            footprint[field] = []
        footprint["footprint_root"] = semantic_root_v2(
            "dependency-manifest/footprint",
            {key: value for key, value in footprint.items() if key != "footprint_root"},
        )
        _recompute_aggregate(dependency)
        _reidentify(dependency)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(dependency, relations=self.relations())
        self.assertDiagnostic(
            caught.exception, "relation", "relation.policy-rejected"
        )

    def test_cyclic_derivation_attempt_is_rejected_before_identity(self) -> None:
        recipe = self.publication.record("materialization-recipe")
        direct, refinement = recipe["derivation_steps"]
        source = next(
            contract
            for input_contract in direct["input_contracts"]
            for contract in input_contract["source_contracts"]
        )
        source["source_kind"] = "predecessor-output"
        source["predecessor_step_id"] = refinement["derivation_step_id"]
        _reidentify(recipe)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(recipe)
        self.assertDiagnostic(
            caught.exception, "semantic", "semantic.derivation-step-id"
        )

    def test_all_seven_graph_bodies_are_valid_and_relation_closed(self) -> None:
        self.assertEqual(
            GRAPH_BODY_KINDS,
            set(self.publication.graph_records_by_body_kind),
        )
        for body_kind, record in self.publication.graph_records_by_body_kind.items():
            with self.subTest(body_kind=body_kind):
                validated = validate_record(
                    record.to_dict(), relations=self.publication.relations
                )
                self.assertEqual(body_kind, validated.to_dict()["body"]["record_kind"])

    def test_graph_body_references_must_be_in_keyed_provenance_bindings(self) -> None:
        cases = (
            (
                "edge",
                "source_graph_inputs",
                "source_node_record_id",
                "semantic.endpoint-source-closure",
            ),
            (
                "property",
                "source_graph_inputs",
                "subject_node_record_id",
                "semantic.property-source-closure",
            ),
            (
                "evidence-link",
                "source_graph_inputs",
                "supported_graph_record_id",
                "semantic.evidence-link-source-closure",
            ),
            (
                "evidence-link",
                "evidence_inputs",
                "supporting_evidence_record_id",
                "semantic.evidence-link-closure",
            ),
            (
                "conflict",
                "source_graph_inputs",
                "conflicting_graph_record_ids",
                "semantic.conflict-source-closure",
            ),
        )
        for body_kind, binding_field, body_field, expected_code in cases:
            with self.subTest(body_kind=body_kind, body_field=body_field):
                record = self.publication.graph_records_by_body_kind[
                    body_kind
                ].to_dict()
                referenced = record["body"][body_field]
                referenced_id = referenced[0] if isinstance(referenced, list) else referenced
                id_field = (
                    "evidence_record_id"
                    if binding_field == "evidence_inputs"
                    else "graph_record_id"
                )
                record[binding_field] = [
                    item
                    for item in record[binding_field]
                    if item[id_field] != referenced_id
                ]
                _reidentify(record)
                with self.assertRaises(RecordValidationError) as caught:
                    validate_record(record)
                self.assertDiagnostic(caught.exception, "semantic", expected_code)

        refinement = self.publication.graph_records_by_body_kind[
            "refinement-mapping"
        ].to_dict()
        coarse_node_id = refinement["body"]["coarse_endpoint"][
            "source_node_record_id"
        ]
        refinement["source_graph_inputs"] = [
            item
            for item in refinement["source_graph_inputs"]
            if item["graph_record_id"] != coarse_node_id
        ]
        _reidentify(refinement)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(refinement)
        self.assertDiagnostic(
            caught.exception, "semantic", "semantic.refinement-source-closure"
        )

    def test_publication_requires_both_pinned_resolvers(self) -> None:
        with self.assertRaisesRegex(ValueError, "pinned record_resolver and blob_resolver"):
            validate_publication(
                self.publication.records,
                relations=RelationResolvers(
                    record_resolver=self.publication.record_resolver,
                    blob_resolver=None,
                ),
            )

    def test_blob_digest_and_length_are_checked_against_exact_bytes(self) -> None:
        descriptor = self.publication.record("source:a")

        def wrong_blob(object_id: str):
            if object_id == descriptor["object_id"]:
                return b"wrong bytes"
            return self.publication.blob_resolver(object_id)

        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                descriptor,
                relations=RelationResolvers(
                    record_resolver=self.publication.record_resolver,
                    blob_resolver=wrong_blob,
                ),
            )
        self.assertTrue(
            {"relation.blob-length", "relation.blob-digest"}
            & {item.code for item in caught.exception.diagnostics}
        )

    def test_bound_child_schema_closedness_bypasses_fail_closed(self) -> None:
        cases = _read_json(FIXTURES / "bound-schema-malicious.json")["cases"]
        for case in cases:
            with self.subTest(case_id=case["case_id"]):
                schema_raw = canonical_json_bytes(case["schema"])
                digest = hashlib.sha256(schema_raw).hexdigest()
                schema_descriptor = self.publication.record("property-schema")
                schema_descriptor["object_id"] = (
                    f"workbench-blob-v2:sha256:{digest}"
                )
                schema_descriptor["sha256"] = digest
                schema_descriptor["byte_length"] = len(schema_raw)
                _reidentify(schema_descriptor)

                value_descriptor = self.publication.record("property-value")
                value_descriptor["described_schema_id"] = case["schema"]["$id"]
                value_descriptor["described_schema_object_descriptor_id"] = (
                    schema_descriptor["id"]
                )
                _reidentify(value_descriptor)

                def resolver(record_id: str):
                    if record_id == schema_descriptor["id"]:
                        return schema_descriptor
                    return self.publication.record_resolver(record_id)

                def blob_resolver(object_id: str):
                    if object_id == schema_descriptor["object_id"]:
                        return schema_raw
                    return self.publication.blob_resolver(object_id)

                with self.assertRaises(RecordValidationError) as caught:
                    validate_record(
                        value_descriptor,
                        relations=self.relations(
                            record_resolver=resolver,
                            blob_resolver=blob_resolver,
                            policy_validator=lambda request: True,
                        ),
                    )
                self.assertDiagnostic(
                    caught.exception,
                    case["expected_phase"],
                    case["expected_code"],
                )

    def test_malicious_fixture_coverage_is_exhaustive(self) -> None:
        registered = {contract.kind for contract in registered_contracts()}
        top_level_unknowns = {
            case["target"]
            for case in self.malicious
            if case["case_id"].endswith("-unknown-field")
            and not case["target"].startswith("graph-record:")
        }
        nested = {
            case["target"].split(":", 1)[1]
            for case in self.malicious
            if case["target"].startswith("graph-record:")
            and case["case_id"].endswith("-unknown-field")
        }
        self.assertEqual(registered, top_level_unknowns)
        self.assertEqual(GRAPH_BODY_KINDS, nested)
        case_ids = {case["case_id"] for case in self.malicious}
        for required in (
            "evidence-source-binding-order",
            "graph-partition-order",
            "wrong-kind-reference",
            "missing-reference",
            "rejected-effective-admission",
            "object-digest-mismatch",
            "evidence-set-root-mismatch",
            "dependency-footprint-root-mismatch",
            "dependency-aggregate-root-mismatch",
            "graph-partition-root-mismatch",
            "graph-record-root-mismatch",
            "graph-aggregate-root-mismatch",
            "graph-set-aggregate-root-mismatch",
            "cross-context-graph-revision",
        ):
            self.assertIn(required, case_ids)

    def _target(self, target: str) -> dict:
        aliases = {
            "object-descriptor": "source:a",
            "evidence-record": "evidence-record",
            "admission-record": "admission-record",
            "ledger-entry": "ledger-head",
            "evidence-set-revision": "evidence-set-revision",
            "graph-record": "graph-record:node",
            "graph-revision": "graph-revision",
            "graph-set-revision": "graph-set-revision",
            "materialization-recipe": "materialization-recipe",
            "dependency-manifest": "dependency-manifest",
            "reference-event": "reference-event",
        }
        if target.startswith("graph-record:"):
            return self.publication.graph_records_by_body_kind[
                target.split(":", 1)[1]
            ].to_dict()
        return deepcopy(self.publication.record(aliases[target]))

    def _membership_variant(
        self,
        admission: dict,
        evidence: dict,
        *,
        ledger_head_id: str,
        ledger_head_ordinal: int,
        extra_records: list[dict] | None = None,
    ) -> tuple[dict, dict[str, dict], dict[str, bytes]]:
        rows = {
            "admission": (admission, "admission-shard", admission["candidate_record_id"]),
            "evidence": (evidence, "evidence-shard", evidence["id"]),
        }
        overrides: dict[str, dict] = {
            item["id"]: item
            for item in [admission, evidence, *(extra_records or [])]
        }
        blobs: dict[str, bytes] = {}
        partitions: dict[str, dict] = {}
        for membership_kind, (row, alias, record_key) in rows.items():
            raw = canonical_json_bytes(row) + b"\n"
            digest = hashlib.sha256(raw).hexdigest()
            descriptor = self.publication.record(alias)
            descriptor["object_id"] = f"workbench-blob-v2:sha256:{digest}"
            descriptor["sha256"] = digest
            descriptor["byte_length"] = len(raw)
            descriptor["canonical_item_count"] = 1
            descriptor["minimum_record_key"] = record_key
            descriptor["maximum_record_key"] = record_key
            _reidentify(descriptor)
            overrides[descriptor["id"]] = descriptor
            blobs[descriptor["object_id"]] = raw
            domain = (
                "evidence-set-revision/effective-admissions/partition"
                if membership_kind == "admission"
                else "evidence-set-revision/effective-evidence/partition"
            )
            partitions[membership_kind] = {
                "partition_key": "synthetic",
                "shard_ordinal": 0,
                "object_descriptor_id": descriptor["id"],
                "record_count": 1,
                "minimum_record_key": record_key,
                "maximum_record_key": record_key,
                "semantic_root": semantic_root_v2(
                    domain,
                    {
                        "partition_key": "synthetic",
                        "record_ids": [row["id"]],
                        "shard_ordinal": 0,
                    },
                ),
            }

        evidence_set = self.publication.record("evidence-set-revision")
        evidence_set["ledger_heads"] = [
            {
                "ledger_namespace": "synthetic.evidence",
                "head_record_id": ledger_head_id,
                "ordinal": ledger_head_ordinal,
            }
        ]
        evidence_set["admission_partitions"] = [partitions["admission"]]
        evidence_set["evidence_partitions"] = [partitions["evidence"]]
        evidence_set["effective_admission_root"] = semantic_root_v2(
            "evidence-set-revision/effective-admissions",
            [
                {
                    "partition_key": "synthetic",
                    "record_count": 1,
                    "semantic_root": partitions["admission"]["semantic_root"],
                    "shard_ordinal": 0,
                }
            ],
        )
        evidence_set["effective_evidence_root"] = semantic_root_v2(
            "evidence-set-revision/effective-evidence",
            [
                {
                    "partition_key": "synthetic",
                    "record_count": 1,
                    "semantic_root": partitions["evidence"]["semantic_root"],
                    "shard_ordinal": 0,
                }
            ],
        )
        _recompute_aggregate(evidence_set)
        _reidentify(evidence_set)
        overrides[evidence_set["id"]] = evidence_set
        return evidence_set, overrides, blobs

    def _variant_relations(
        self, overrides: dict[str, dict], blobs: dict[str, bytes]
    ) -> RelationResolvers:
        def resolver(record_id: str):
            return overrides.get(record_id) or self.publication.record_resolver(record_id)

        def blob_resolver(object_id: str):
            return blobs.get(object_id) or self.publication.blob_resolver(object_id)

        return self.relations(
            record_resolver=resolver,
            blob_resolver=blob_resolver,
            policy_validator=lambda request: True,
        )

    def test_evidence_parent_owner_and_context_scope_cannot_change(self) -> None:
        variants = {
            "authority": (
                "authority_owner",
                self.publication.record("evidence-record")["authority_owner"],
            ),
            "context": ("context_ref_id", "context-ref:sha256:" + "f" * 64),
        }
        for name, (field, replacement) in variants.items():
            with self.subTest(scope=name):
                parent = self.publication.record("evidence-set-revision")
                parent[field] = replacement
                _recompute_aggregate(parent)
                _reidentify(parent)
                child = self.publication.record("evidence-set-revision")
                child["parent_evidence_set_revision_ids"] = [parent["id"]]
                _recompute_aggregate(child)
                _reidentify(child)
                with self.assertRaises(RecordValidationError) as caught:
                    validate_record(
                        child,
                        relations=self._variant_relations(
                            {parent["id"]: parent, child["id"]: child}, {}
                        ),
                    )
                self.assertDiagnostic(
                    caught.exception,
                    "relation",
                    "relation.evidence-parent-scope",
                )

    def test_child_evidence_revision_cannot_drop_parent_ledger_namespace(self) -> None:
        evidence_entry = self.publication.record("ledger-entry:evidence")
        evidence_entry["ledger_namespace"] = "synthetic.extra"
        _reidentify(evidence_entry)
        admission_entry = self.publication.record("ledger-head")
        admission_entry["ledger_namespace"] = "synthetic.extra"
        admission_entry["previous_entry_id"] = evidence_entry["id"]
        _reidentify(admission_entry)

        parent = self.publication.record("evidence-set-revision")
        parent["ledger_heads"].append(
            {
                "ledger_namespace": "synthetic.extra",
                "head_record_id": admission_entry["id"],
                "ordinal": 1,
            }
        )
        _recompute_aggregate(parent)
        _reidentify(parent)
        child = self.publication.record("evidence-set-revision")
        child["parent_evidence_set_revision_ids"] = [parent["id"]]
        _recompute_aggregate(child)
        _reidentify(child)
        overrides = {
            evidence_entry["id"]: evidence_entry,
            admission_entry["id"]: admission_entry,
            parent["id"]: parent,
            child["id"]: child,
        }
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                child,
                relations=self._variant_relations(overrides, {}),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.evidence-parent-ledger"
        )

    def test_child_evidence_head_must_descend_from_parent_head(self) -> None:
        parent = self.publication.record("evidence-set-revision")
        child = self.publication.record("evidence-set-revision")
        child["parent_evidence_set_revision_ids"] = [parent["id"]]
        child["ledger_heads"] = [
            {
                "ledger_namespace": "synthetic.evidence",
                "head_record_id": self.publication.aliases["ledger-entry:evidence"],
                "ordinal": 0,
            }
        ]
        _recompute_aggregate(child)
        _reidentify(child)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                child,
                relations=self._variant_relations(
                    {parent["id"]: parent, child["id"]: child}, {}
                ),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.evidence-parent-ledger"
        )

    def test_forged_evidence_history_roots_are_recomputed(self) -> None:
        for history_class in ("rejected", "quarantined", "superseded"):
            with self.subTest(history_class=history_class):
                evidence_set = self.publication.record("evidence-set-revision")
                evidence_set["history_roots"][history_class] = "a" * 64
                _recompute_aggregate(evidence_set)
                _reidentify(evidence_set)
                with self.assertRaises(RecordValidationError) as caught:
                    validate_record(
                        evidence_set,
                        relations=self._variant_relations(
                            {evidence_set["id"]: evidence_set}, {}
                        ),
                    )
                self.assertDiagnostic(
                    caught.exception,
                    "relation",
                    "relation.evidence-history-root",
                )

    def _graph_with_extra_node_shard(
        self, row: dict
    ) -> tuple[dict, dict[str, dict], dict[str, bytes]]:
        graph = self.publication.record("graph-revision")
        original_partition = next(
            item for item in graph["partitions"] if item["record_kind"] == "node"
        )
        original_descriptor = self.publication.records_by_id[
            original_partition["object_descriptor_id"]
        ].to_dict()
        raw = canonical_json_bytes(row) + b"\n"
        digest = hashlib.sha256(raw).hexdigest()
        descriptor = dict(original_descriptor)
        descriptor["object_id"] = f"workbench-blob-v2:sha256:{digest}"
        descriptor["sha256"] = digest
        descriptor["byte_length"] = len(raw)
        descriptor["canonical_item_count"] = 1
        descriptor["minimum_record_key"] = row["logical_key"]
        descriptor["maximum_record_key"] = row["logical_key"]
        _reidentify(descriptor)
        extra_partition = dict(original_partition)
        extra_partition["shard_ordinal"] = 1
        extra_partition["object_descriptor_id"] = descriptor["id"]
        extra_partition["record_count"] = 1
        extra_partition["minimum_record_key"] = row["logical_key"]
        extra_partition["maximum_record_key"] = row["logical_key"]
        extra_partition["semantic_root"] = semantic_root_v2(
            "graph-revision/partition/node",
            {
                "partition_key": original_partition["partition_key"],
                "record_ids": [row["id"]],
                "shard_ordinal": 1,
            },
        )
        insert_at = graph["partitions"].index(original_partition) + 1
        graph["partitions"].insert(insert_at, extra_partition)
        original_raw = self.publication.blobs_by_id[original_descriptor["object_id"]]
        original_ids = [
            load_canonical_record(line).id for line in original_raw.splitlines()
        ]
        graph["record_counts"]["nodes"] += 1
        graph["record_roots"]["nodes"] = semantic_root_v2(
            "graph-revision/records/node", [*original_ids, row["id"]]
        )
        _recompute_aggregate(graph)
        _reidentify(graph)
        overrides = {
            descriptor["id"]: descriptor,
            row["id"]: row,
            graph["id"]: graph,
        }
        return graph, overrides, {descriptor["object_id"]: raw}

    def test_graph_record_id_cannot_appear_in_multiple_shards(self) -> None:
        row = self.publication.record("graph-record:node")
        graph, overrides, blobs = self._graph_with_extra_node_shard(row)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                graph,
                relations=self._variant_relations(overrides, blobs),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.graph-record-duplicate"
        )

    def test_distinct_graph_records_cannot_reuse_a_logical_key(self) -> None:
        row = self.publication.record("graph-record:node")
        row["limitations"] = [
            {"code": "synthetic-distinct-record", "detail": "same logical key"}
        ]
        _reidentify(row)
        graph, overrides, blobs = self._graph_with_extra_node_shard(row)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                graph,
                relations=self._variant_relations(overrides, blobs),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.graph-logical-key-duplicate",
        )

    def test_graph_shard_key_ranges_must_be_monotonic_and_nonoverlapping(self) -> None:
        row = self.publication.record("graph-record:node")
        row["logical_key"] = "node:000-before-first-shard"
        _reidentify(row)
        graph, overrides, blobs = self._graph_with_extra_node_shard(row)
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                graph,
                relations=self._variant_relations(overrides, blobs),
            )
        self.assertDiagnostic(
            caught.exception, "relation", "relation.graph-shard-range"
        )

    def _rejected_effective_admission(
        self, expected_phase: str, expected_code: str
    ) -> None:
        admission = self.publication.record("admission-record")
        admission["outcome"] = "rejected"
        admission["eligible_for_materialization"] = False
        _reidentify(admission)
        ledger = self.publication.record("ledger-head")
        ledger["ordinal"] = 2
        ledger["previous_entry_id"] = self.publication.aliases["ledger-head"]
        ledger["entry_record_id"] = admission["id"]
        _reidentify(ledger)
        evidence = self.publication.record("evidence-record")
        evidence_set, overrides, blobs = self._membership_variant(
            admission,
            evidence,
            ledger_head_id=ledger["id"],
            ledger_head_ordinal=2,
            extra_records=[ledger],
        )
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                evidence_set,
                relations=self._variant_relations(overrides, blobs),
            )
        self.assertDiagnostic(
            caught.exception,
            expected_phase,
            expected_code,
        )

    def test_post_head_evidence_and_admission_cannot_leak_into_membership(self) -> None:
        evidence = self.publication.record("evidence-record")
        evidence["evidence_kind"] = "synthetic.leaked-observation"
        _reidentify(evidence)
        admission = self.publication.record("admission-record")
        admission["candidate_record_id"] = evidence["id"]
        _reidentify(admission)
        evidence_set, overrides, blobs = self._membership_variant(
            admission,
            evidence,
            ledger_head_id=self.publication.aliases["ledger-head"],
            ledger_head_ordinal=1,
        )
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(
                evidence_set,
                relations=self._variant_relations(overrides, blobs),
            )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.effective-admission-ledger",
        )
        self.assertDiagnostic(
            caught.exception,
            "relation",
            "relation.effective-evidence-ledger",
        )

    def _exercise_malicious_case(self, case: dict) -> None:
        if case["operation"] == "reject-admission-and-reseal-descendants":
            self._rejected_effective_admission(
                case["expected_phase"], case["expected_code"]
            )
            return

        record = self._target(case["target"])
        operation = case["operation"]
        if operation == "add":
            _replace(record, case["pointer"], case["value"])
        elif operation in ("replace", "replace-and-reseal"):
            _replace(record, case["pointer"], case["value"])
        elif operation == "replace-from-and-reseal":
            _replace(record, case["pointer"], self.publication.aliases[case["value_from"]])
        elif operation in ("reverse-array", "reverse-array-and-reseal"):
            _replace(record, case["pointer"], list(reversed(_at(record, case["pointer"]))))
        else:
            self.fail(f"unknown malicious fixture operation: {operation}")

        if operation.endswith("and-reseal") or operation == "reverse-array":
            if case["case_id"] in {
                "graph-partition-order",
                "graph-partition-root-mismatch",
                "graph-record-root-mismatch",
                "cross-context-graph-revision",
            }:
                _recompute_aggregate(record)
            _reidentify(record)

        relations = None if case["expected_stage"] == "record" else self.relations(
            policy_validator=lambda request: True
        )
        with self.assertRaises(RecordValidationError) as caught:
            validate_record(record, relations=relations)
        self.assertDiagnostic(
            caught.exception, case["expected_phase"], case["expected_code"]
        )

    def test_every_retained_malicious_variant_fails_closed(self) -> None:
        for case in self.malicious:
            with self.subTest(case_id=case["case_id"]):
                self._exercise_malicious_case(case)


if __name__ == "__main__":
    unittest.main()
