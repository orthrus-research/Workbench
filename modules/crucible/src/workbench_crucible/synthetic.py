"""Deterministic, fully linked C01 synthetic graph publication.

This module is a retained proving caller, not a product profile or an authority
fixture.  It constructs exact objects and every core record kind through the
public C01 sealing API, then hands the complete pinned view to the production
publication validator.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any

from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id
from .records import (
    AdapterValidatedReference,
    RelationResolvers,
    TrustedAuthorityAdapter,
    ValidatedExternalReference,
    ValidatedRecord,
    seal_record,
    semantic_root_v2,
    validate_publication,
)


_FORMAT_STEM = {
    "object-descriptor": "object-descriptor",
    "evidence-record": "evidence-record",
    "admission-record": "admission-record",
    "ledger-entry": "ledger-entry",
    "evidence-set-revision": "evidence-set-revision",
    "graph-record": "graph-record",
    "graph-revision": "graph-revision",
    "graph-set-revision": "graph-set-revision",
    "materialization-recipe": "materialization-recipe",
    "dependency-manifest": "dependency-manifest",
    "reference-event": "reference-event",
}
_GRAPH_KIND_ORDER = (
    "node",
    "edge",
    "property",
    "evidence-link",
    "refinement-mapping",
    "frontier",
    "conflict",
)
_MAIN_GRAPH_KINDS = tuple(
    item for item in _GRAPH_KIND_ORDER if item != "refinement-mapping"
)
_COUNT_KEY = {
    "node": "nodes",
    "edge": "edges",
    "property": "properties",
    "evidence-link": "evidence_links",
    "refinement-mapping": "refinement_mappings",
    "frontier": "frontiers",
    "conflict": "conflicts",
}
_SUBJECT_SCHEMA_ID = (
    "workbench://schemas/crucible-fixtures/"
    "synthetic-subject-identity-v1.schema.json"
)
_VALUE_SCHEMA_ID = (
    "workbench://schemas/crucible-fixtures/"
    "synthetic-closed-values-v1.schema.json"
)


def _module_root() -> Path:
    return _module_resource_root(__file__, 'crucible')


def _header(kind: str) -> dict[str, Any]:
    stem = _FORMAT_STEM[kind]
    filename = f"crucible-{stem}-v2.schema.json"
    return {
        "kind": kind,
        "format": f"workbench-crucible-{stem}-v2",
        "schema_version": 2,
        "schema_id": f"workbench://schemas/crucible/{filename}",
        "canonicalizer": CANONICALIZER_ID,
    }


def _canonical_set(values: list[Any]) -> list[Any]:
    return sorted(values, key=canonical_json_bytes)


def _manifest_root(
    candidate: Mapping[str, Any], field: str, domain: str
) -> str:
    projection = dict(candidate)
    projection.pop("id", None)
    projection.pop(field, None)
    return semantic_root_v2(domain, projection)


@dataclass(frozen=True, slots=True)
class SyntheticPublication:
    """One immutable C01 publication plus its exact pinned object view."""

    records: tuple[ValidatedRecord, ...]
    records_by_id: Mapping[str, ValidatedRecord]
    blobs_by_id: Mapping[str, bytes]
    external_records_by_id: Mapping[str, bytes]
    external_scopes_by_id: Mapping[str, tuple[str, str, str] | None]
    trusted_adapters: Mapping[str, TrustedAuthorityAdapter]
    policy_expectations: frozenset[tuple[str, str, str, str, str]]
    aliases: Mapping[str, str]
    graph_records_by_body_kind: Mapping[str, ValidatedRecord]

    def record_resolver(self, record_id: str):
        return self.records_by_id.get(record_id)

    def blob_resolver(self, object_id: str):
        return self.blobs_by_id.get(object_id)

    def external_reference_resolver(self, expectation):
        raw = self.external_records_by_id.get(expectation.record_id)
        if raw is None:
            return None
        declared_scope = self.external_scopes_by_id.get(expectation.record_id)
        expected_scope = (
            expectation.expected_authority_id,
            expectation.expected_owner_revision_id,
            expectation.expected_adapter_id,
        )
        kind = expectation.record_id.split(":", 1)[0]
        if declared_scope is None and expected_scope == (None, None, None):
            return ValidatedExternalReference(
                record_id=expectation.record_id,
                kind=kind,
                canonical_bytes=raw,
            )
        if declared_scope != expected_scope:
            return None
        return AdapterValidatedReference(
            record_id=expectation.record_id,
            kind=kind,
            canonical_bytes=raw,
            adapter_id=declared_scope[2],
            owner_authority_id=declared_scope[0],
            owner_revision_id=declared_scope[1],
        )

    def policy_validator(self, request):
        scope = request.authority_scope
        if scope is None:
            return False
        key = (
            request.rule,
            request.record.id,
            scope.owner_authority_id,
            scope.owner_revision_id,
            scope.authority_adapter_id,
        )
        retained = self.records_by_id.get(request.record.id)
        expected_adapter = self.trusted_adapters.get(scope.authority_adapter_id)
        return (
            key in self.policy_expectations
            and retained is not None
            and request.record.canonical_bytes == retained.canonical_bytes
            and request.authority_adapter == expected_adapter
        )

    @property
    def relations(self) -> RelationResolvers:
        return RelationResolvers(
            record_resolver=self.record_resolver,
            blob_resolver=self.blob_resolver,
            external_reference_resolver=self.external_reference_resolver,
            trusted_adapters=self.trusted_adapters,
            policy_validator=self.policy_validator,
            require_complete=True,
        )

    def record(self, alias: str) -> dict[str, Any]:
        return self.records_by_id[self.aliases[alias]].to_dict()

    def validate(self):
        """Run the production cross-record publication validator."""

        return validate_publication(self.records, relations=self.relations)

    def summary(self) -> dict[str, Any]:
        counts = Counter(record.kind for record in self.records)
        evidence_set = self.record("evidence-set-revision")
        dependencies = [
            item.to_dict()
            for item in self.records
            if item.kind == "dependency-manifest"
        ]
        graphs = [
            item.to_dict() for item in self.records if item.kind == "graph-revision"
        ]
        graph_set = self.record("graph-set-revision")
        reference = self.record("reference-event")
        graph_record_ids: dict[str, list[str]] = {
            record_kind: [] for record_kind in _GRAPH_KIND_ORDER
        }
        for item in self.records:
            if item.kind != "graph-record":
                continue
            value = item.to_dict()
            graph_record_ids[value["body"]["record_kind"]].append(item.id)
        return {
            "format": "workbench-crucible-v2-synthetic-publication-summary-v1",
            "aliases": dict(sorted(self.aliases.items())),
            "record_ids": [record.id for record in self.records],
            "blob_object_ids": sorted(
                self.blobs_by_id, key=lambda item: item.encode("utf-8")
            ),
            "external_record_ids": sorted(
                self.external_records_by_id, key=lambda item: item.encode("utf-8")
            ),
            "trusted_adapter_ids": sorted(
                self.trusted_adapters, key=lambda item: item.encode("utf-8")
            ),
            "external_scope_oracle": {
                "count": len(self.external_scopes_by_id),
                "semantic_root": semantic_root_v2(
                    "synthetic-publication/external-scope-oracle",
                    [
                        {
                            "record_id": record_id,
                            "scope": (
                                None
                                if scope is None
                                else {
                                    "owner_authority_id": scope[0],
                                    "owner_revision_id": scope[1],
                                    "authority_adapter_id": scope[2],
                                }
                            ),
                        }
                        for record_id, scope in sorted(
                            self.external_scopes_by_id.items()
                        )
                    ],
                ),
            },
            "policy_expectation_oracle": {
                "count": len(self.policy_expectations),
                "semantic_root": semantic_root_v2(
                    "synthetic-publication/policy-expectation-oracle",
                    [list(item) for item in sorted(self.policy_expectations)],
                ),
            },
            "graph_record_ids_by_body_kind": graph_record_ids,
            "record_kind_counts": dict(sorted(counts.items())),
            "roots": {
                "aggregate": {
                    "dependency_manifests": {
                        item["id"]: item["semantic_root"] for item in dependencies
                    },
                    "evidence_set_revision": evidence_set["semantic_root"],
                    "graph_revisions": {
                        item["id"]: item["aggregate_semantic_root"]
                        for item in graphs
                    },
                    "graph_set_revision": graph_set["aggregate_semantic_root"],
                },
                "dependency_footprints": [
                    {
                        "dependency_manifest_id": dependency["id"],
                        "footprint_roots": [
                            item["footprint_root"]
                            for item in dependency["footprints"]
                        ],
                    }
                    for dependency in dependencies
                ],
                "admission_partitions": [
                    {
                        "partition_key": item["partition_key"],
                        "shard_ordinal": item["shard_ordinal"],
                        "semantic_root": item["semantic_root"],
                    }
                    for item in evidence_set["admission_partitions"]
                ],
                "evidence_partitions": [
                    {
                        "partition_key": item["partition_key"],
                        "shard_ordinal": item["shard_ordinal"],
                        "semantic_root": item["semantic_root"],
                    }
                    for item in evidence_set["evidence_partitions"]
                ],
                "effective_admission": evidence_set[
                    "effective_admission_root"
                ],
                "effective_evidence": evidence_set["effective_evidence_root"],
                "graph_partitions": [
                    {
                        "graph_revision_id": graph["id"],
                        "partition_key": item["partition_key"],
                        "record_kind": item["record_kind"],
                        "shard_ordinal": item["shard_ordinal"],
                        "semantic_root": item["semantic_root"],
                    }
                    for graph in graphs
                    for item in graph["partitions"]
                ],
                "graph_records": {
                    graph["id"]: graph["record_roots"] for graph in graphs
                },
            },
            "reference_target": reference["new_target_id"],
        }


class _Builder:
    def __init__(self, module_root: Path):
        self.module_root = module_root
        self.records: list[ValidatedRecord] = []
        self.records_by_id: dict[str, ValidatedRecord] = {}
        self.blobs_by_id: dict[str, bytes] = {}
        self.external_records_by_id: dict[str, bytes] = {}
        self.external_scopes_by_id: dict[str, tuple[str, str, str] | None] = {}
        self.trusted_adapters: dict[str, TrustedAuthorityAdapter] = {}
        self.aliases: dict[str, str] = {}
        self.graph_records: dict[str, dict[str, Any]] = {}

    def external_id(
        self,
        kind: str,
        label: str,
        authority_binding: Mapping[str, str] | None = None,
    ) -> str:
        body = {
            "kind": kind,
            "format": "workbench-crucible-synthetic-external-reference-v1",
            "canonicalizer": CANONICALIZER_ID,
            "fixture": label,
        }
        record_id = content_id(kind, body)
        raw = canonical_json_bytes({**body, "id": record_id})
        previous = self.external_records_by_id.setdefault(record_id, raw)
        if previous != raw:
            raise AssertionError(f"synthetic external ID collision: {record_id}")
        declared_scope = (
            None
            if authority_binding is None
            else (
                authority_binding["owner_authority_id"],
                authority_binding["owner_revision_id"],
                authority_binding["authority_adapter_id"],
            )
        )
        previous_scope = self.external_scopes_by_id.setdefault(
            record_id, declared_scope
        )
        if previous_scope != declared_scope:
            raise AssertionError(
                f"synthetic external scope collision: {record_id}"
            )
        return record_id

    def bind_external_scope(
        self, record_id: str, authority_binding: Mapping[str, str]
    ) -> None:
        declared_scope = (
            authority_binding["owner_authority_id"],
            authority_binding["owner_revision_id"],
            authority_binding["authority_adapter_id"],
        )
        previous = self.external_scopes_by_id.get(record_id)
        if previous not in (None, declared_scope):
            raise AssertionError(
                f"synthetic external scope collision: {record_id}"
            )
        self.external_scopes_by_id[record_id] = declared_scope

    def authority_binding(self, label: str) -> dict[str, str]:
        owner_authority_id = self.external_id("authority", label)
        owner_revision_id = self.external_id("owner-revision", f"{label}-v2-fixture")
        adapter_body = {
            "kind": "authority-adapter",
            "format": "workbench-crucible-synthetic-authority-adapter-v1",
            "canonicalizer": CANONICALIZER_ID,
            "fixture": f"{label}-v2-fixture",
            "owner_authority_id": owner_authority_id,
        }
        adapter_id = content_id("authority-adapter", adapter_body)
        adapter_raw = canonical_json_bytes({**adapter_body, "id": adapter_id})
        self.external_records_by_id[adapter_id] = adapter_raw
        self.trusted_adapters[adapter_id] = TrustedAuthorityAdapter(
            adapter_id=adapter_id,
            owner_authority_id=owner_authority_id,
            canonical_bytes=adapter_raw,
        )
        binding = {
            "owner_authority_id": owner_authority_id,
            "owner_revision_id": owner_revision_id,
            "authority_adapter_id": adapter_id,
        }
        self.bind_external_scope(owner_authority_id, binding)
        self.bind_external_scope(owner_revision_id, binding)
        self.bind_external_scope(adapter_id, binding)
        return binding

    def seal(self, candidate: Mapping[str, Any], alias: str | None = None) -> dict[str, Any]:
        validated = seal_record(candidate)
        if validated.id in self.records_by_id:
            raise AssertionError(f"duplicate synthetic record ID: {validated.id}")
        self.records.append(validated)
        self.records_by_id[validated.id] = validated
        if alias is not None:
            self.aliases[alias] = validated.id
        return validated.to_dict()

    def object_descriptor(
        self,
        *,
        alias: str,
        raw: bytes,
        media_type: str,
        semantic_role: str,
        representation: str = "exact-bytes",
        described_schema_id: str | None = None,
        described_schema_object_descriptor_id: str | None = None,
        described_record_kind: str | None = None,
        canonical_item_count: int | None = None,
        total_order_policy_id: str | None = None,
        total_order_authority: Mapping[str, str] | None = None,
        minimum_record_key: str | None = None,
        maximum_record_key: str | None = None,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest()
        object_id = f"workbench-blob-v2:sha256:{digest}"
        self.blobs_by_id[object_id] = raw
        return self.seal(
            {
                **_header("object-descriptor"),
                "object_id": object_id,
                "sha256": digest,
                "byte_length": len(raw),
                "media_type": media_type,
                "representation": representation,
                "semantic_role": semantic_role,
                "described_schema_id": described_schema_id,
                "described_schema_object_descriptor_id": described_schema_object_descriptor_id,
                "described_record_kind": described_record_kind,
                "canonical_item_count": canonical_item_count,
                "total_order_policy_id": total_order_policy_id,
                "total_order_authority": total_order_authority,
                "minimum_record_key": minimum_record_key,
                "maximum_record_key": maximum_record_key,
            },
            alias=alias,
        )

    def exact_object(
        self,
        alias: str,
        raw: bytes,
        role: str,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        return self.object_descriptor(
            alias=alias,
            raw=raw,
            media_type=media_type,
            semantic_role=role,
        )

    def canonical_value(
        self,
        *,
        alias: str,
        value: Any,
        semantic_role: str,
        schema_descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.object_descriptor(
            alias=alias,
            raw=canonical_json_bytes(value),
            media_type="application/json",
            semantic_role=semantic_role,
            representation="canonical-json-value",
            described_schema_id=_VALUE_SCHEMA_ID,
            described_schema_object_descriptor_id=str(schema_descriptor["id"]),
            canonical_item_count=1,
        )

    def schema_object(self, stem: str) -> dict[str, Any]:
        path = self.module_root / "schemas" / f"crucible-{stem}-v2.schema.json"
        return self.exact_object(
            f"schema:{stem}", path.read_bytes(), "record-schema", "application/schema+json"
        )

    def subject_identity(
        self,
        name: str,
        subject_schema: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        identity = {
            "namespace": "workbench.synthetic",
            "name": name,
        }
        subject_id = content_id("synthetic-subject", identity)
        descriptor = self.object_descriptor(
            alias=f"subject-identity:{name}",
            raw=canonical_json_bytes(identity),
            media_type="application/json",
            semantic_role="subject-identity",
            representation="canonical-json-value",
            described_schema_id=_SUBJECT_SCHEMA_ID,
            described_schema_object_descriptor_id=subject_schema["id"],
            canonical_item_count=1,
        )
        return subject_id, descriptor

    def graph_record(
        self,
        *,
        body_kind: str,
        logical_key: str,
        body: dict[str, Any],
        authority_owner: dict[str, str],
        context_ref_id: str,
        recipe_id: str,
        evidence_record_id: str,
        derivation_step_id: str,
        source_graph_record_ids: list[str] | None = None,
        source_graph_inputs: list[dict[str, Any]] | None = None,
        graph_family_id: str = "synthetic.graph",
        category_id: str = "synthetic.mechanics",
        resolution_id: str = "synthetic.exact",
    ) -> dict[str, Any]:
        record = self.seal(
            {
                **_header("graph-record"),
                "authority_owner": authority_owner,
                "graph_family_id": graph_family_id,
                "category_id": category_id,
                "resolution_id": resolution_id,
                "context_ref_id": context_ref_id,
                "logical_key": logical_key,
                "body": body,
                "recipe_id": recipe_id,
                "derivation_step_id": derivation_step_id,
                "evidence_inputs": [
                    {
                        "input_contract_key": "synthetic.evidence",
                        "evidence_record_id": evidence_record_id,
                    }
                ],
                "source_graph_inputs": _canonical_set(
                    source_graph_inputs
                    or [
                        {
                            "input_contract_key": "synthetic.local-main-records",
                            "source_kind": "local-output",
                            "contract_key": "synthetic.main",
                            "predecessor_step_id": None,
                            "graph_record_id": record_id,
                        }
                        for record_id in (source_graph_record_ids or [])
                    ]
                ),
                "qualifiers_object_descriptor_id": None,
                "evidence_state": "known",
                "limitations": [],
            }
        )
        self.graph_records[body_kind] = record
        self.aliases[f"graph-record:{body_kind}"] = record["id"]
        return record


def build_synthetic_publication(
    module_root: Path | None = None,
) -> SyntheticPublication:
    """Build the same complete C01 publication for the same checked-in inputs."""

    root = _module_root() if module_root is None else module_root.resolve()
    builder = _Builder(root)

    admission_schema = builder.schema_object("admission-record")
    evidence_schema = builder.schema_object("evidence-record")
    graph_schema = builder.schema_object("graph-record")
    subject_schema_path = (
        root / "tests/fixtures/crucible-v2/synthetic-subject-identity-v1.schema.json"
    )
    subject_schema = builder.exact_object(
        "schema:synthetic-subject",
        subject_schema_path.read_bytes(),
        "subject-identity-schema",
        "application/schema+json",
    )
    value_schema_path = (
        root / "tests/fixtures/crucible-v2/synthetic-closed-values-v1.schema.json"
    )
    value_schema = builder.exact_object(
        "schema:synthetic-values",
        value_schema_path.read_bytes(),
        "closed-value-schema",
        "application/schema+json",
    )
    builder.aliases["parameter-schema"] = value_schema["id"]
    builder.aliases["property-schema"] = value_schema["id"]

    source_a = builder.exact_object("source:a", b"synthetic source A\n", "source")
    source_b = builder.exact_object(
        "source:b",
        canonical_json_bytes({"a": {"b/c": "synthetic source B"}}),
        "source",
        "application/json",
    )
    payload = builder.canonical_value(
        alias="evidence-payload",
        value={
            "value_kind": "evidence-payload",
            "observation": "alpha-before-beta",
            "ordinal": 0,
        },
        semantic_role="evidence-payload",
        schema_descriptor=value_schema,
    )
    validation_result = builder.canonical_value(
        alias="validation-result",
        value={"value_kind": "validation-result", "outcome": "passed"},
        semantic_role="validation-result",
        schema_descriptor=value_schema,
    )
    selector = builder.exact_object("selector", b"synthetic selector v1\n", "selector")
    parameter_schema = value_schema
    parameter_values = builder.canonical_value(
        alias="parameter-values",
        value={"value_kind": "parameter-values"},
        semantic_role="parameter-values",
        schema_descriptor=value_schema,
    )
    source_tree = builder.exact_object("source-tree", b"synthetic source tree v1\n", "source-tree")
    dependency_lock = builder.exact_object(
        "dependency-lock", b"synthetic dependency lock v1\n", "dependency-lock"
    )
    runtime_environment = builder.exact_object(
        "runtime-environment", b"synthetic runtime environment v1\n", "runtime-environment"
    )
    resource_limits = builder.canonical_value(
        alias="resource-limits",
        value={"value_kind": "resource-limits", "records": 64},
        semantic_role="resource-limits",
        schema_descriptor=value_schema,
    )
    compatibility = builder.canonical_value(
        alias="compatibility-constraints",
        value={"value_kind": "compatibility-constraints", "context": "exact"},
        semantic_role="compatibility-constraints",
        schema_descriptor=value_schema,
    )
    property_schema = value_schema
    property_value = builder.canonical_value(
        alias="property-value",
        value={"value_kind": "property-value", "value": "present"},
        semantic_role="property-value",
        schema_descriptor=value_schema,
    )
    refinement_summary = builder.canonical_value(
        alias="refinement-summary",
        value={"value_kind": "refinement-summary", "fine_subject_count": 1},
        semantic_role="refinement-summary",
        schema_descriptor=value_schema,
    )

    context_ref_id = builder.external_id("context-ref", "synthetic-context")
    atlas_owner = builder.authority_binding("atlas")
    blueprints_owner = builder.authority_binding("blueprints")
    crucible_owner = builder.authority_binding("crucible")
    profile_owner = builder.authority_binding("synthetic-profile")
    profile_adapter_id = builder.external_id(
        "profile-adapter", "synthetic-profile-adapter"
    )
    builder.bind_external_scope(profile_adapter_id, profile_owner)
    profile_adapter_binding = {
        "profile_adapter_id": profile_adapter_id,
        "profile_authority": profile_owner,
    }
    source_bindings = _canonical_set(
        [
            {
                "role": "primary",
                "object_descriptor_id": source_a["id"],
                "locator": {"kind": "byte-range", "start": 0, "length": len(b"synthetic source A\n")},
            },
            {
                "role": "secondary",
                "object_descriptor_id": source_b["id"],
                "locator": {"kind": "json-pointer", "pointer": "/a/b~1c"},
            },
        ]
    )
    evidence = builder.seal(
        {
            **_header("evidence-record"),
            "authority_owner": crucible_owner,
            "evidence_kind": "synthetic.observation",
            "context_ref_id": context_ref_id,
            "producer_id": builder.external_id(
                "producer", "synthetic-producer", crucible_owner
            ),
            "transport_normalizer_id": builder.external_id(
                "transport-normalizer", "synthetic-normalizer", crucible_owner
            ),
            "source_bindings": source_bindings,
            "payload_object_descriptor_id": payload["id"],
            "evidence_state": "known",
            "capture_condition": {
                "completion": "complete",
                "loss": "none",
                "truncated": False,
                "coverage": {
                    "state": "complete",
                    "coverage_object_descriptor_id": None,
                    "omitted_at_least": None,
                },
            },
            "provenance_record_ids": [],
            "corrects_record_ids": [],
            "supersedes_record_ids": [],
            "legacy_bindings": [],
            "migration_manifest_object_descriptor_id": None,
            "limitations": [
                {
                    "code": "synthetic-reference-shaped-text",
                    "detail": (
                        "policy:sha256:"
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    ),
                }
            ],
        },
        alias="evidence-record",
    )
    ledger_evidence = builder.seal(
        {
            **_header("ledger-entry"),
            "context_ref_id": context_ref_id,
            "ledger_namespace": "synthetic.evidence",
            "ordinal": 0,
            "previous_entry_id": None,
            "entry_record_kind": "evidence-record",
            "entry_record_id": evidence["id"],
        },
        alias="ledger-entry:evidence",
    )
    admission = builder.seal(
        {
            **_header("admission-record"),
            "decision_owner": atlas_owner,
            "candidate_record_id": evidence["id"],
            "candidate_kind": "evidence-record",
            "context_ref_id": context_ref_id,
            "validator_ids": [
                builder.external_id("validator", "synthetic-admission", atlas_owner)
            ],
            "evaluated_schema_object_descriptor_ids": [evidence_schema["id"]],
            "policy_ids": [
                builder.external_id("policy", "synthetic-admission", atlas_owner)
            ],
            "profile_adapter_bindings": [profile_adapter_binding],
            "outcome": "admitted",
            "eligible_for_materialization": True,
            "diagnostics": [],
            "limitations": [],
            "prior_admission_record_id": None,
            "validation_result_object_descriptor_id": validation_result["id"],
            "supporting_receipt_ids": [],
        },
        alias="admission-record",
    )
    ledger_admission = builder.seal(
        {
            **_header("ledger-entry"),
            "context_ref_id": context_ref_id,
            "ledger_namespace": "synthetic.evidence",
            "ordinal": 1,
            "previous_entry_id": ledger_evidence["id"],
            "entry_record_kind": "admission-record",
            "entry_record_id": admission["id"],
        },
        alias="ledger-head",
    )

    admission_shard_raw = canonical_json_bytes(admission) + b"\n"
    admission_shard = builder.object_descriptor(
        alias="admission-shard",
        raw=admission_shard_raw,
        media_type="application/x-ndjson",
        semantic_role="effective-admission-shard",
        representation="canonical-ndjson-shard",
        described_schema_id=admission["schema_id"],
        described_schema_object_descriptor_id=admission_schema["id"],
        described_record_kind="admission-record",
        canonical_item_count=1,
        total_order_policy_id=builder.external_id(
            "policy", "admission-candidate-id-order", atlas_owner
        ),
        total_order_authority=atlas_owner,
        minimum_record_key=evidence["id"],
        maximum_record_key=evidence["id"],
    )
    admission_partition = {
        "partition_key": "synthetic",
        "shard_ordinal": 0,
        "object_descriptor_id": admission_shard["id"],
        "record_count": 1,
        "minimum_record_key": evidence["id"],
        "maximum_record_key": evidence["id"],
        "semantic_root": semantic_root_v2(
            "evidence-set-revision/effective-admissions/partition",
            {
                "partition_key": "synthetic",
                "record_ids": [admission["id"]],
                "shard_ordinal": 0,
            },
        ),
    }
    admission_partition_summary = {
        key: admission_partition[key]
        for key in (
            "partition_key",
            "record_count",
            "semantic_root",
            "shard_ordinal",
        )
    }

    evidence_shard_raw = canonical_json_bytes(evidence) + b"\n"
    evidence_shard = builder.object_descriptor(
        alias="evidence-shard",
        raw=evidence_shard_raw,
        media_type="application/x-ndjson",
        semantic_role="evidence-shard",
        representation="canonical-ndjson-shard",
        described_schema_id=evidence["schema_id"],
        described_schema_object_descriptor_id=evidence_schema["id"],
        described_record_kind="evidence-record",
        canonical_item_count=1,
        total_order_policy_id=builder.external_id(
            "policy", "evidence-record-id-order", atlas_owner
        ),
        total_order_authority=atlas_owner,
        minimum_record_key=evidence["id"],
        maximum_record_key=evidence["id"],
    )
    evidence_partition = {
        "partition_key": "synthetic",
        "shard_ordinal": 0,
        "object_descriptor_id": evidence_shard["id"],
        "record_count": 1,
        "minimum_record_key": evidence["id"],
        "maximum_record_key": evidence["id"],
        "semantic_root": semantic_root_v2(
            "evidence-set-revision/effective-evidence/partition",
            {
                "partition_key": "synthetic",
                "record_ids": [evidence["id"]],
                "shard_ordinal": 0,
            },
        ),
    }
    evidence_partition_summary = {
        key: evidence_partition[key]
        for key in (
            "partition_key",
            "record_count",
            "semantic_root",
            "shard_ordinal",
        )
    }
    evidence_set_candidate = {
        **_header("evidence-set-revision"),
        "authority_owner": atlas_owner,
        "context_ref_id": context_ref_id,
        "parent_evidence_set_revision_ids": [],
        "merge_recipe_id": None,
        "ledger_heads": [
            {
                "ledger_namespace": "synthetic.evidence",
                "head_record_id": ledger_admission["id"],
                "ordinal": 1,
            }
        ],
        "admission_selection_policy_id": builder.external_id(
            "policy", "synthetic-selection", atlas_owner
        ),
        "effective_admission_count": 1,
        "effective_admission_root": semantic_root_v2(
            "evidence-set-revision/effective-admissions",
            [admission_partition_summary],
        ),
        "admission_partitions": [admission_partition],
        "effective_evidence_count": 1,
        "effective_evidence_root": semantic_root_v2(
            "evidence-set-revision/effective-evidence",
            [evidence_partition_summary],
        ),
        "evidence_partitions": [evidence_partition],
        "scope_selector_id": builder.external_id(
            "selector", "synthetic-scope", atlas_owner
        ),
        "history_roots": {
            "rejected": None,
            "quarantined": None,
            "superseded": None,
            "conflicted": None,
        },
        "coverage": {
            "state": "complete",
            "coverage_object_descriptor_id": None,
            "omitted_at_least": None,
        },
        "limitations": [],
        "evidence_frontier_object_descriptor_id": None,
        "semantic_root": "",
    }
    evidence_set_candidate["semantic_root"] = _manifest_root(
        evidence_set_candidate, "semantic_root", "evidence-set-revision/aggregate"
    )
    evidence_set = builder.seal(evidence_set_candidate, alias="evidence-set-revision")

    subject_alpha_id, subject_alpha = builder.subject_identity("alpha", subject_schema)
    subject_beta_id, subject_beta = builder.subject_identity("beta", subject_schema)
    implementation_id = builder.external_id(
        "implementation", "synthetic-recipe-v1", blueprints_owner
    )
    recipe_policy_names = {
        "total_order",
        "partition",
        "deduplication",
        "dependency_footprint",
        "invalidation",
        "deterministic_failure",
    }
    policies = {
        name: builder.external_id(
            "policy",
            f"synthetic-{name}",
            blueprints_owner if name in recipe_policy_names else atlas_owner,
        )
        for name in (
            "identity",
            "total_order",
            "partition",
            "deduplication",
            "category_membership",
            "category_overlap",
            "cross_category_edges",
            "resolution_aggregation",
            "refinement_mapping",
            "evidence_propagation",
            "conflict_propagation",
            "frontier_propagation",
            "dependency_footprint",
            "invalidation",
            "deterministic_failure",
        )
    }
    direct_step_body = {
        "step_key": "synthetic.direct",
        "implementation_id": implementation_id,
        "input_contracts": _canonical_set(
            [
                {
                    "input_contract_key": "synthetic.evidence",
                    "input_mode": "evidence",
                    "evidence_kind_ids": ["synthetic.observation"],
                    "source_contracts": [],
                },
                {
                    "input_contract_key": "synthetic.local-main-records",
                    "input_mode": "source-graph-record",
                    "evidence_kind_ids": [],
                    "source_contracts": [
                        {
                            "source_kind": "local-output",
                            "contract_key": "synthetic.main",
                            "predecessor_step_id": None,
                            "record_kinds": _canonical_set(
                                ["node", "property", "conflict"]
                            ),
                        }
                    ],
                },
            ]
        ),
        "output_contracts": _canonical_set(
            [
                {
                    "output_contract_key": "synthetic.main",
                    "output_record_kinds": _canonical_set(
                        list(_MAIN_GRAPH_KINDS)
                    ),
                },
                {
                    "output_contract_key": "synthetic.coarse",
                    "output_record_kinds": ["node"],
                },
            ]
        ),
    }
    direct_step_id = content_id("derivation-step", direct_step_body)
    direct_step = {
        "derivation_step_id": direct_step_id,
        **direct_step_body,
    }
    refinement_step_body = {
        "step_key": "synthetic.refinement",
        "implementation_id": implementation_id,
        "input_contracts": _canonical_set(
            [
                {
                    "input_contract_key": "synthetic.evidence",
                    "input_mode": "evidence",
                    "evidence_kind_ids": ["synthetic.observation"],
                    "source_contracts": [],
                },
                {
                    "input_contract_key": "synthetic.refinement-coarse",
                    "input_mode": "source-graph-record",
                    "evidence_kind_ids": [],
                    "source_contracts": [
                        {
                            "source_kind": "accepted-graph-input",
                            "contract_key": "synthetic.input-coarse",
                            "predecessor_step_id": None,
                            "record_kinds": ["node"],
                        }
                    ],
                },
                {
                    "input_contract_key": "synthetic.refinement-baseline",
                    "input_mode": "source-graph-record",
                    "evidence_kind_ids": [],
                    "source_contracts": [
                        {
                            "source_kind": "accepted-graph-input",
                            "contract_key": "synthetic.input-baseline",
                            "predecessor_step_id": None,
                            "record_kinds": ["node"],
                        }
                    ],
                },
                {
                    "input_contract_key": "synthetic.refinement-candidate",
                    "input_mode": "source-graph-record",
                    "evidence_kind_ids": [],
                    "source_contracts": [
                        {
                            "source_kind": "accepted-graph-input",
                            "contract_key": "synthetic.input-main",
                            "predecessor_step_id": None,
                            "record_kinds": ["node"],
                        }
                    ],
                },
            ]
        ),
        "output_contracts": [
            {
                "output_contract_key": "synthetic.refinement",
                "output_record_kinds": ["refinement-mapping"],
            }
        ],
    }
    refinement_step_id = content_id("derivation-step", refinement_step_body)
    refinement_step = {
        "derivation_step_id": refinement_step_id,
        **refinement_step_body,
    }
    subject_contracts = [
        {
            "node_kind": "synthetic.subject",
            "subject_kind": "synthetic-subject",
            "subject_identity_schema_object_descriptor_id": subject_schema["id"],
        }
    ]
    recipe = builder.seal(
        {
            **_header("materialization-recipe"),
            "recipe_name": "synthetic.recipe",
            "semantic_version": "1.0.0",
            "recipe_owner": blueprints_owner,
            "authority_owner": atlas_owner,
            "implementation": {
                "implementation_id": implementation_id,
                "executable_object_descriptor_id": None,
                "source_tree_object_descriptor_id": source_tree["id"],
                "dependency_lock_object_descriptor_id": dependency_lock["id"],
                "runtime_environment_object_descriptor_id": runtime_environment["id"],
            },
            "derivation_steps": [direct_step, refinement_step],
            "accepted_inputs": {
                "evidence_kind_ids": ["synthetic.observation"],
                "graph_input_contracts": _canonical_set(
                    [
                        {
                            "graph_input_contract_key": "synthetic.input-coarse",
                            "graph_family_id": "synthetic.graph",
                            "category_id": "synthetic.mechanics",
                            "resolution_id": "synthetic.coarse",
                            "record_kinds": ["node"],
                        },
                        {
                            "graph_input_contract_key": "synthetic.input-baseline",
                            "graph_family_id": "synthetic.graph",
                            "category_id": "synthetic.mechanics",
                            "resolution_id": "synthetic.exact",
                            "record_kinds": _canonical_set(
                                list(_MAIN_GRAPH_KINDS)
                            ),
                        },
                        {
                            "graph_input_contract_key": "synthetic.input-main",
                            "graph_family_id": "synthetic.graph",
                            "category_id": "synthetic.mechanics",
                            "resolution_id": "synthetic.exact",
                            "record_kinds": _canonical_set(
                                list(_MAIN_GRAPH_KINDS)
                            ),
                        },
                    ]
                ),
                "scope_compatibility_rule_id": builder.external_id(
                    "policy", "synthetic-scope-compatibility", blueprints_owner
                ),
            },
            "input_selector_object_descriptor_id": selector["id"],
            "parameter_schema_object_descriptor_id": parameter_schema["id"],
            "canonical_parameter_values_object_descriptor_id": parameter_values["id"],
            "schema_object_descriptor_ids": [graph_schema["id"]],
            "ontology_ids": [],
            "profile_adapter_bindings": [profile_adapter_binding],
            "tool_ids": [builder.external_id("tool", "synthetic-materializer")],
            "output_contracts": [
                {
                    "output_contract_key": "synthetic.main",
                    "graph_family_id": "synthetic.graph",
                    "category_id": "synthetic.mechanics",
                    "resolution_id": "synthetic.exact",
                    "graph_record_schema_object_descriptor_id": graph_schema["id"],
                    "subject_contracts": subject_contracts,
                },
                {
                    "output_contract_key": "synthetic.coarse",
                    "graph_family_id": "synthetic.graph",
                    "category_id": "synthetic.mechanics",
                    "resolution_id": "synthetic.coarse",
                    "graph_record_schema_object_descriptor_id": graph_schema["id"],
                    "subject_contracts": subject_contracts,
                },
                {
                    "output_contract_key": "synthetic.refinement",
                    "graph_family_id": "synthetic.graph",
                    "category_id": "synthetic.refinement",
                    "resolution_id": "synthetic.coarse-to-exact",
                    "graph_record_schema_object_descriptor_id": graph_schema["id"],
                    "subject_contracts": subject_contracts,
                },
            ],
            "policies": policies,
            "resource_limits_object_descriptor_id": resource_limits["id"],
            "incremental_publication_enabled": False,
            "full_build_conformance_case_ids": [
                builder.external_id(
                    "conformance-case", "synthetic-full", blueprints_owner
                )
            ],
            "incremental_equivalence_case_ids": [],
        },
        alias="materialization-recipe",
    )

    derivation_step_id = direct_step_id
    node_alpha = builder.graph_record(
        body_kind="node",
        logical_key="node:alpha",
        body={
            "record_kind": "node",
            "subject_id": subject_alpha_id,
            "subject_identity_object_descriptor_id": subject_alpha["id"],
            "node_kind": "synthetic.subject",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
    )
    node_beta = builder.graph_record(
        body_kind="node-beta",
        logical_key="node:beta",
        body={
            "record_kind": "node",
            "subject_id": subject_beta_id,
            "subject_identity_object_descriptor_id": subject_beta["id"],
            "node_kind": "synthetic.subject",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
    )
    coarse_node_alpha = builder.graph_record(
        body_kind="node-coarse",
        logical_key="node:alpha",
        body={
            "record_kind": "node",
            "subject_id": subject_alpha_id,
            "subject_identity_object_descriptor_id": subject_alpha["id"],
            "node_kind": "synthetic.subject",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
        resolution_id="synthetic.coarse",
    )
    edge = builder.graph_record(
        body_kind="edge",
        logical_key="edge:alpha-before-beta",
        body={
            "record_kind": "edge",
            "source_subject_id": subject_alpha_id,
            "target_subject_id": subject_beta_id,
            "source_node_record_id": node_alpha["id"],
            "target_node_record_id": node_beta["id"],
            "predicate_id": "synthetic.before",
            "direction": "directed",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
        source_graph_record_ids=[node_alpha["id"], node_beta["id"]],
    )
    property_record = builder.graph_record(
        body_kind="property",
        logical_key="property:alpha-present",
        body={
            "record_kind": "property",
            "subject_id": subject_alpha_id,
            "subject_node_record_id": node_alpha["id"],
            "property_id": "synthetic.state",
            "value_schema_object_descriptor_id": property_schema["id"],
            "value_object_descriptor_id": property_value["id"],
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
        source_graph_record_ids=[node_alpha["id"]],
    )
    builder.graph_record(
        body_kind="evidence-link",
        logical_key="evidence-link:alpha",
        body={
            "record_kind": "evidence-link",
            "supported_graph_record_id": node_alpha["id"],
            "supporting_evidence_record_id": evidence["id"],
            "support_role": "synthetic.primary",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
        source_graph_record_ids=[node_alpha["id"]],
    )
    builder.graph_record(
        body_kind="frontier",
        logical_key="frontier:beta",
        body={
            "record_kind": "frontier",
            "subject_ids": [subject_beta_id],
            "state": "bounded",
            "reason_code": "synthetic-bound",
            "continuation_object_descriptor_id": None,
            "omitted_at_least": 1,
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
    )
    conflict_target = builder.graph_record(
        body_kind="conflict-target",
        logical_key="conflict:z-target",
        body={
            "record_kind": "conflict",
            "subject_ids": _canonical_set([subject_alpha_id, subject_beta_id]),
            "conflicting_graph_record_ids": _canonical_set([node_alpha["id"], property_record["id"]]),
            "reason_code": "synthetic-conflict",
            "resolution_state": "retained",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
        source_graph_record_ids=[node_alpha["id"], property_record["id"]],
    )
    conflict_forward = builder.graph_record(
        body_kind="conflict",
        logical_key="conflict:a-forward",
        body={
            "record_kind": "conflict",
            "subject_ids": _canonical_set([subject_alpha_id, subject_beta_id]),
            "conflicting_graph_record_ids": _canonical_set(
                [node_alpha["id"], conflict_target["id"]]
            ),
            "reason_code": "synthetic-forward-reference",
            "resolution_state": "retained",
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=derivation_step_id,
        source_graph_record_ids=[node_alpha["id"], conflict_target["id"]],
    )

    partition_records = {
        graph_kind: [builder.graph_records[graph_kind]]
        for graph_kind in _MAIN_GRAPH_KINDS
    }
    partition_records["node"] = sorted(
        [node_alpha, node_beta],
        key=lambda item: item["logical_key"].encode("utf-8"),
    )
    partition_records["conflict"] = sorted(
        [conflict_forward, conflict_target],
        key=lambda item: item["logical_key"].encode("utf-8"),
    )
    partitions: list[dict[str, Any]] = []
    record_counts = {field: 0 for field in _COUNT_KEY.values()}
    record_roots = {field: None for field in _COUNT_KEY.values()}
    total_order_policy_id = policies["total_order"]
    for graph_kind in _MAIN_GRAPH_KINDS:
        rows = partition_records[graph_kind]
        raw = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
        shard = builder.object_descriptor(
            alias=f"graph-shard:{graph_kind}",
            raw=raw,
            media_type="application/x-ndjson",
            semantic_role="graph-record-shard",
            representation="canonical-ndjson-shard",
            described_schema_id=rows[0]["schema_id"],
            described_schema_object_descriptor_id=graph_schema["id"],
            described_record_kind="graph-record",
            canonical_item_count=len(rows),
            total_order_policy_id=total_order_policy_id,
            total_order_authority=blueprints_owner,
            minimum_record_key=rows[0]["logical_key"],
            maximum_record_key=rows[-1]["logical_key"],
        )
        record_ids = [row["id"] for row in rows]
        partitions.append(
            {
                "partition_key": "synthetic.main",
                "shard_ordinal": 0,
                "record_kind": graph_kind,
                "object_descriptor_id": shard["id"],
                "record_count": len(rows),
                "minimum_record_key": rows[0]["logical_key"],
                "maximum_record_key": rows[-1]["logical_key"],
                "semantic_root": semantic_root_v2(
                    f"graph-revision/partition/{graph_kind}",
                    {
                        "partition_key": "synthetic.main",
                        "record_ids": record_ids,
                        "shard_ordinal": 0,
                    },
                ),
            }
        )
        field = _COUNT_KEY[graph_kind]
        record_counts[field] = len(rows)
        record_roots[field] = semantic_root_v2(
            f"graph-revision/records/{graph_kind}", record_ids
        )

    input_components = _canonical_set(
        [
            {
                "component_key": "synthetic.recipe-implementation",
                "component_role": "recipe-implementation",
                "component_kind": "implementation",
                "component_id": implementation_id,
                "authority_binding": blueprints_owner,
            },
            {
                "component_key": "synthetic.graph-record-schema",
                "component_role": "record-schema",
                "component_kind": "object-descriptor",
                "component_id": graph_schema["id"],
                "authority_binding": None,
            },
            {
                "component_key": "synthetic.dependency-footprint-policy",
                "component_role": "dependency-footprint-policy",
                "component_kind": "policy",
                "component_id": policies["dependency_footprint"],
                "authority_binding": blueprints_owner,
            },
            {
                "component_key": "synthetic.invalidation-policy",
                "component_role": "invalidation-policy",
                "component_kind": "policy",
                "component_id": policies["invalidation"],
                "authority_binding": blueprints_owner,
            },
        ]
    )
    component_keys = _canonical_set(
        [item["component_key"] for item in input_components]
    )
    footprint = {
        "output_partitions": _canonical_set(
            [
                {
                    "partition_key": "synthetic.main",
                    "shard_ordinal": 0,
                    "record_kind": graph_kind,
                }
                for graph_kind in _MAIN_GRAPH_KINDS
            ]
        ),
        "evidence_record_ids": [evidence["id"]],
        "admission_record_ids": [admission["id"]],
        "admission_partitions": [
            {
                "evidence_set_revision_id": evidence_set["id"],
                "partition_key": "synthetic",
                "shard_ordinal": 0,
            }
        ],
        "evidence_partitions": [
            {
                "evidence_set_revision_id": evidence_set["id"],
                "partition_key": "synthetic",
                "shard_ordinal": 0,
            }
        ],
        "source_graph_records": [],
        "source_graph_partitions": [],
        "component_keys": component_keys,
        "footprint_root": "",
    }
    footprint["footprint_root"] = semantic_root_v2(
        "dependency-manifest/footprint",
        {key: value for key, value in footprint.items() if key != "footprint_root"},
    )
    dependency_candidate = {
        **_header("dependency-manifest"),
        "authority_owner": atlas_owner,
        "recipe_owner": blueprints_owner,
        "recipe_id": recipe["id"],
        "recipe_implementation_id": implementation_id,
        "context_ref_id": context_ref_id,
        "evidence_set_revision_ids": [evidence_set["id"]],
        "input_graph_bindings": [],
        "parameter_values_object_descriptor_id": parameter_values["id"],
        "input_components": input_components,
        "parent_dependency_manifest_id": None,
        "dependency_state": "exact",
        "invalidation_policy_id": policies["invalidation"],
        "footprints": [footprint],
        "semantic_root": "",
    }
    dependency_candidate["semantic_root"] = _manifest_root(
        dependency_candidate, "semantic_root", "dependency-manifest/aggregate"
    )
    dependency = builder.seal(dependency_candidate, alias="dependency-manifest")

    profile_revision_id = builder.external_id(
        "profile-revision", "synthetic-profile", profile_owner
    )
    support_decision_id = builder.external_id(
        "support-decision", "synthetic-experimental", profile_owner
    )
    profile_support_binding = {
        "profile_revision_id": profile_revision_id,
        "profile_adapter_id": profile_adapter_id,
        "profile_authority": profile_owner,
        "support_decision_ids": [support_decision_id],
    }
    tool_id = builder.external_id("tool", "synthetic-materializer")
    graph_candidate = {
        **_header("graph-revision"),
        "graph_family_id": "synthetic.graph",
        "category_id": "synthetic.mechanics",
        "resolution_id": "synthetic.exact",
        "purpose_id": "synthetic.conformance",
        "authority_owner": atlas_owner,
        "recipe_owner": blueprints_owner,
        "materializer": {
            "component_id": builder.external_id("component", "crucible-kernel"),
            "implementation_id": builder.external_id("implementation", "synthetic-kernel"),
        },
        "custodian": {
            "component_id": builder.external_id("component", "crucible-store"),
            "implementation_id": builder.external_id("implementation", "synthetic-store"),
        },
        "recipe_id": recipe["id"],
        "recipe_implementation_id": implementation_id,
        "parameter_values_object_descriptor_id": parameter_values["id"],
        "ontology_ids": [],
        "record_schema_object_descriptor_ids": [graph_schema["id"]],
        "profile_support_bindings": [profile_support_binding],
        "tool_ids": [tool_id],
        "evidence_set_revision_ids": [evidence_set["id"]],
        "input_graph_bindings": [],
        "context_ref_id": context_ref_id,
        "parent_graph_revision_ids": [],
        "partition_policy_id": policies["partition"],
        "total_order_policy_id": policies["total_order"],
        "dependency_manifest_id": dependency["id"],
        "partitions": partitions,
        "record_counts": record_counts,
        "record_roots": record_roots,
        "aggregate_semantic_root": "",
        "evidence_state": "known",
        "coverage": {
            "state": "bounded",
            "coverage_object_descriptor_id": None,
            "omitted_at_least": 1,
        },
        "completeness": "bounded",
        "support_state": "experimental",
        "limitations": [],
        "frontier_record_ids": [builder.graph_records["frontier"]["id"]],
        "conflict_record_ids": _canonical_set(
            [conflict_forward["id"], conflict_target["id"]]
        ),
        "semantic_validation_requirement_ids": [
            builder.external_id(
                "validation-requirement", "synthetic-closed", atlas_owner
            )
        ],
    }
    graph_candidate["aggregate_semantic_root"] = _manifest_root(
        graph_candidate, "aggregate_semantic_root", "graph-revision/aggregate"
    )
    graph = builder.seal(graph_candidate, alias="graph-revision")

    def publish_single_record_graph(
        *,
        record: dict[str, Any],
        record_kind: str,
        partition_key: str,
        category_id: str,
        resolution_id: str,
        input_graph_bindings: list[dict[str, Any]],
        source_graph_records: list[dict[str, Any]],
        source_graph_partitions: list[dict[str, Any]],
        alias_suffix: str,
    ) -> dict[str, Any]:
        shard_raw = canonical_json_bytes(record) + b"\n"
        shard = builder.object_descriptor(
            alias=f"graph-shard:{alias_suffix}",
            raw=shard_raw,
            media_type="application/x-ndjson",
            semantic_role="graph-record-shard",
            representation="canonical-ndjson-shard",
            described_schema_id=record["schema_id"],
            described_schema_object_descriptor_id=graph_schema["id"],
            described_record_kind="graph-record",
            canonical_item_count=1,
            total_order_policy_id=policies["total_order"],
            total_order_authority=blueprints_owner,
            minimum_record_key=record["logical_key"],
            maximum_record_key=record["logical_key"],
        )
        partition = {
            "partition_key": partition_key,
            "shard_ordinal": 0,
            "record_kind": record_kind,
            "object_descriptor_id": shard["id"],
            "record_count": 1,
            "minimum_record_key": record["logical_key"],
            "maximum_record_key": record["logical_key"],
            "semantic_root": semantic_root_v2(
                f"graph-revision/partition/{record_kind}",
                {
                    "partition_key": partition_key,
                    "record_ids": [record["id"]],
                    "shard_ordinal": 0,
                },
            ),
        }
        single_counts = {field: 0 for field in _COUNT_KEY.values()}
        single_roots = {field: None for field in _COUNT_KEY.values()}
        single_counts[_COUNT_KEY[record_kind]] = 1
        single_roots[_COUNT_KEY[record_kind]] = semantic_root_v2(
            f"graph-revision/records/{record_kind}", [record["id"]]
        )
        single_footprint = {
            "output_partitions": [
                {
                    "partition_key": partition_key,
                    "shard_ordinal": 0,
                    "record_kind": record_kind,
                }
            ],
            "evidence_record_ids": [evidence["id"]],
            "admission_record_ids": [admission["id"]],
            "admission_partitions": [
                {
                    "evidence_set_revision_id": evidence_set["id"],
                    "partition_key": "synthetic",
                    "shard_ordinal": 0,
                }
            ],
            "evidence_partitions": [
                {
                    "evidence_set_revision_id": evidence_set["id"],
                    "partition_key": "synthetic",
                    "shard_ordinal": 0,
                }
            ],
            "source_graph_records": _canonical_set(source_graph_records),
            "source_graph_partitions": _canonical_set(
                source_graph_partitions
            ),
            "component_keys": component_keys,
            "footprint_root": "",
        }
        single_footprint["footprint_root"] = semantic_root_v2(
            "dependency-manifest/footprint",
            {
                key: value
                for key, value in single_footprint.items()
                if key != "footprint_root"
            },
        )
        canonical_input_graph_bindings = _canonical_set(input_graph_bindings)
        dependency_value = {
            **_header("dependency-manifest"),
            "authority_owner": atlas_owner,
            "recipe_owner": blueprints_owner,
            "recipe_id": recipe["id"],
            "recipe_implementation_id": implementation_id,
            "context_ref_id": context_ref_id,
            "evidence_set_revision_ids": [evidence_set["id"]],
            "input_graph_bindings": canonical_input_graph_bindings,
            "parameter_values_object_descriptor_id": parameter_values["id"],
            "input_components": input_components,
            "parent_dependency_manifest_id": None,
            "dependency_state": "exact",
            "invalidation_policy_id": policies["invalidation"],
            "footprints": [single_footprint],
            "semantic_root": "",
        }
        dependency_value["semantic_root"] = _manifest_root(
            dependency_value,
            "semantic_root",
            "dependency-manifest/aggregate",
        )
        single_dependency = builder.seal(
            dependency_value,
            alias=f"dependency-manifest:{alias_suffix}",
        )
        graph_value = {
            **_header("graph-revision"),
            "graph_family_id": "synthetic.graph",
            "category_id": category_id,
            "resolution_id": resolution_id,
            "purpose_id": "synthetic.conformance",
            "authority_owner": atlas_owner,
            "recipe_owner": blueprints_owner,
            "materializer": {
                "component_id": builder.external_id(
                    "component", "crucible-kernel"
                ),
                "implementation_id": builder.external_id(
                    "implementation", "synthetic-kernel"
                ),
            },
            "custodian": {
                "component_id": builder.external_id(
                    "component", "crucible-store"
                ),
                "implementation_id": builder.external_id(
                    "implementation", "synthetic-store"
                ),
            },
            "recipe_id": recipe["id"],
            "recipe_implementation_id": implementation_id,
            "parameter_values_object_descriptor_id": parameter_values["id"],
            "ontology_ids": [],
            "record_schema_object_descriptor_ids": [graph_schema["id"]],
            "profile_support_bindings": [profile_support_binding],
            "tool_ids": [tool_id],
            "evidence_set_revision_ids": [evidence_set["id"]],
            "input_graph_bindings": canonical_input_graph_bindings,
            "context_ref_id": context_ref_id,
            "parent_graph_revision_ids": [],
            "partition_policy_id": policies["partition"],
            "total_order_policy_id": policies["total_order"],
            "dependency_manifest_id": single_dependency["id"],
            "partitions": [partition],
            "record_counts": single_counts,
            "record_roots": single_roots,
            "aggregate_semantic_root": "",
            "evidence_state": "known",
            "coverage": {
                "state": "bounded",
                "coverage_object_descriptor_id": None,
                "omitted_at_least": 1,
            },
            "completeness": "bounded",
            "support_state": "experimental",
            "limitations": [],
            "frontier_record_ids": [],
            "conflict_record_ids": [],
            "semantic_validation_requirement_ids": [
                builder.external_id(
                    "validation-requirement", "synthetic-closed", atlas_owner
                )
            ],
        }
        graph_value["aggregate_semantic_root"] = _manifest_root(
            graph_value,
            "aggregate_semantic_root",
            "graph-revision/aggregate",
        )
        return builder.seal(
            graph_value, alias=f"graph-revision:{alias_suffix}"
        )

    coarse_graph = publish_single_record_graph(
        record=coarse_node_alpha,
        record_kind="node",
        partition_key="synthetic.coarse",
        category_id="synthetic.mechanics",
        resolution_id="synthetic.coarse",
        input_graph_bindings=[],
        source_graph_records=[],
        source_graph_partitions=[],
        alias_suffix="coarse",
    )
    baseline_graph = publish_single_record_graph(
        record=node_alpha,
        record_kind="node",
        partition_key="synthetic.baseline",
        category_id="synthetic.mechanics",
        resolution_id="synthetic.exact",
        input_graph_bindings=[],
        source_graph_records=[],
        source_graph_partitions=[],
        alias_suffix="baseline",
    )
    empty_dependency = builder.records_by_id[
        baseline_graph["dependency_manifest_id"]
    ].to_dict()
    empty_dependency.pop("id")
    empty_dependency["footprints"] = []
    empty_dependency["semantic_root"] = _manifest_root(
        empty_dependency,
        "semantic_root",
        "dependency-manifest/aggregate",
    )
    empty_dependency = builder.seal(
        empty_dependency, alias="dependency-manifest:empty"
    )
    empty_graph = dict(baseline_graph)
    empty_graph.pop("id")
    empty_graph["dependency_manifest_id"] = empty_dependency["id"]
    empty_graph["partitions"] = []
    empty_graph["record_counts"] = {
        field: 0 for field in _COUNT_KEY.values()
    }
    empty_graph["record_roots"] = {
        field: None for field in _COUNT_KEY.values()
    }
    empty_graph["evidence_state"] = "known-absent"
    empty_graph["coverage"] = {
        "state": "complete",
        "coverage_object_descriptor_id": None,
        "omitted_at_least": None,
    }
    empty_graph["completeness"] = "complete"
    empty_graph["aggregate_semantic_root"] = _manifest_root(
        empty_graph,
        "aggregate_semantic_root",
        "graph-revision/aggregate",
    )
    empty_graph = builder.seal(empty_graph, alias="graph-revision:empty")
    refinement_record = builder.graph_record(
        body_kind="refinement-mapping",
        logical_key="refinement:alpha-beta",
        body={
            "record_kind": "refinement-mapping",
            "coarse_endpoint": {
                "subject_id": subject_alpha_id,
                "source_graph_revision_id": coarse_graph["id"],
                "source_node_record_id": coarse_node_alpha["id"],
                "graph_family_id": "synthetic.graph",
                "category_id": "synthetic.mechanics",
                "resolution_id": "synthetic.coarse",
            },
            "fine_endpoints": [
                {
                    "subject_id": subject_beta_id,
                    "source_graph_revision_id": graph["id"],
                    "source_node_record_id": node_beta["id"],
                    "graph_family_id": "synthetic.graph",
                    "category_id": "synthetic.mechanics",
                    "resolution_id": "synthetic.exact",
                }
            ],
            "mapping_policy_id": policies["refinement_mapping"],
            "omitted_conflict_record_ids": [],
            "summary_object_descriptor_id": refinement_summary["id"],
        },
        authority_owner=atlas_owner,
        context_ref_id=context_ref_id,
        recipe_id=recipe["id"],
        evidence_record_id=evidence["id"],
        derivation_step_id=refinement_step_id,
        source_graph_inputs=[
            {
                "input_contract_key": "synthetic.refinement-coarse",
                "source_kind": "accepted-graph-input",
                "contract_key": "synthetic.input-coarse",
                "predecessor_step_id": None,
                "graph_record_id": coarse_node_alpha["id"],
            },
            {
                "input_contract_key": "synthetic.refinement-baseline",
                "source_kind": "accepted-graph-input",
                "contract_key": "synthetic.input-baseline",
                "predecessor_step_id": None,
                "graph_record_id": node_alpha["id"],
            },
            {
                "input_contract_key": "synthetic.refinement-candidate",
                "source_kind": "accepted-graph-input",
                "contract_key": "synthetic.input-main",
                "predecessor_step_id": None,
                "graph_record_id": node_beta["id"],
            },
        ],
        category_id="synthetic.refinement",
        resolution_id="synthetic.coarse-to-exact",
    )
    refinement_graph = publish_single_record_graph(
        record=refinement_record,
        record_kind="refinement-mapping",
        partition_key="synthetic.refinement",
        category_id="synthetic.refinement",
        resolution_id="synthetic.coarse-to-exact",
        input_graph_bindings=[
            {
                "graph_input_contract_key": "synthetic.input-coarse",
                "graph_revision_id": coarse_graph["id"],
            },
            {
                "graph_input_contract_key": "synthetic.input-baseline",
                "graph_revision_id": baseline_graph["id"],
            },
            {
                "graph_input_contract_key": "synthetic.input-main",
                "graph_revision_id": graph["id"],
            },
        ],
        source_graph_records=[
            {
                "graph_input_contract_key": "synthetic.input-coarse",
                "graph_record_id": coarse_node_alpha["id"],
            },
            {
                "graph_input_contract_key": "synthetic.input-baseline",
                "graph_record_id": node_alpha["id"],
            },
            {
                "graph_input_contract_key": "synthetic.input-main",
                "graph_record_id": node_beta["id"],
            },
        ],
        source_graph_partitions=[
            {
                "graph_input_contract_key": "synthetic.input-coarse",
                "graph_revision_id": coarse_graph["id"],
                "partition_key": "synthetic.coarse",
                "shard_ordinal": 0,
                "record_kind": "node",
            },
            {
                "graph_input_contract_key": "synthetic.input-baseline",
                "graph_revision_id": baseline_graph["id"],
                "partition_key": "synthetic.baseline",
                "shard_ordinal": 0,
                "record_kind": "node",
            },
            {
                "graph_input_contract_key": "synthetic.input-main",
                "graph_revision_id": graph["id"],
                "partition_key": "synthetic.main",
                "shard_ordinal": 0,
                "record_kind": "node",
            },
        ],
        alias_suffix="refinement",
    )

    workbench_owner = builder.authority_binding("workbench")
    compatibility_rule_id = builder.external_id(
        "policy", "synthetic-compatibility", atlas_owner
    )
    graph_set_candidate = {
        **_header("graph-set-revision"),
        "graph_set_family_id": "synthetic.graph-set",
        "purpose_id": "synthetic.conformance",
        "composition_owner": workbench_owner,
        "context_ref_id": context_ref_id,
        "evidence_set_revision_ids": [evidence_set["id"]],
        "profile_support_bindings": [profile_support_binding],
        "compatibility_rule_id": compatibility_rule_id,
        "compatibility_rule_authority": atlas_owner,
        "compatibility_constraints_object_descriptor_id": compatibility["id"],
        "members": [
            {
                "category_id": graph["category_id"],
                "resolution_id": graph["resolution_id"],
                "graph_revision_id": graph["id"],
                "alignment": {
                    "compatibility_decision_id": builder.external_id(
                        "compatibility-decision", "synthetic-main-graph", atlas_owner
                    ),
                    "decision_authority": atlas_owner,
                    "compatibility_rule_id": compatibility_rule_id,
                    "compatibility_constraints_object_descriptor_id": compatibility["id"],
                    "member_context_ref_id": graph["context_ref_id"],
                    "target_context_ref_id": context_ref_id,
                    "member_evidence_set_revision_ids": graph[
                        "evidence_set_revision_ids"
                    ],
                    "target_evidence_set_revision_ids": [evidence_set["id"]],
                    "context_result": "exact",
                    "evidence_result": "exact",
                    "outcome": "compatible",
                },
            },
            {
                "category_id": coarse_graph["category_id"],
                "resolution_id": coarse_graph["resolution_id"],
                "graph_revision_id": coarse_graph["id"],
                "alignment": {
                    "compatibility_decision_id": builder.external_id(
                        "compatibility-decision",
                        "synthetic-coarse-graph",
                        atlas_owner,
                    ),
                    "decision_authority": atlas_owner,
                    "compatibility_rule_id": compatibility_rule_id,
                    "compatibility_constraints_object_descriptor_id": compatibility["id"],
                    "member_context_ref_id": coarse_graph["context_ref_id"],
                    "target_context_ref_id": context_ref_id,
                    "member_evidence_set_revision_ids": coarse_graph[
                        "evidence_set_revision_ids"
                    ],
                    "target_evidence_set_revision_ids": [evidence_set["id"]],
                    "context_result": "exact",
                    "evidence_result": "exact",
                    "outcome": "compatible",
                },
            },
        ],
        "join_graphs": [],
        "refinement_graphs": [
            {
                "graph_revision_id": refinement_graph["id"],
                "alignment": {
                    "compatibility_decision_id": builder.external_id(
                        "compatibility-decision",
                        "synthetic-refinement-graph",
                        atlas_owner,
                    ),
                    "decision_authority": atlas_owner,
                    "compatibility_rule_id": compatibility_rule_id,
                    "compatibility_constraints_object_descriptor_id": compatibility["id"],
                    "member_context_ref_id": refinement_graph["context_ref_id"],
                    "target_context_ref_id": context_ref_id,
                    "member_evidence_set_revision_ids": refinement_graph[
                        "evidence_set_revision_ids"
                    ],
                    "target_evidence_set_revision_ids": [evidence_set["id"]],
                    "context_result": "exact",
                    "evidence_result": "exact",
                    "outcome": "compatible",
                },
            }
        ],
        "coverage": graph["coverage"],
        "completeness": graph["completeness"],
        "support_state": graph["support_state"],
        "limitations": [],
        "conflict_record_ids": graph["conflict_record_ids"],
        "aggregate_semantic_root": "",
    }
    graph_set_candidate["aggregate_semantic_root"] = _manifest_root(
        graph_set_candidate, "aggregate_semantic_root", "graph-set-revision/aggregate"
    )
    graph_set = builder.seal(graph_set_candidate, alias="graph-set-revision")
    builder.seal(
        {
            **_header("reference-event"),
            "context_ref_id": context_ref_id,
            "reference_namespace": "synthetic",
            "reference_name": "current",
            "transition_sequence": 0,
            "prior_event_id": None,
            "expected_old_target_id": None,
            "new_target_kind": "graph-set-revision",
            "new_target_id": graph_set["id"],
            "authorization_authority": workbench_owner,
            "actor_id": builder.external_id(
                "actor", "synthetic-publisher", workbench_owner
            ),
            "tool_id": builder.external_id(
                "tool", "synthetic-publisher", workbench_owner
            ),
            "authorization_decision_id": builder.external_id(
                "authorization-decision",
                "synthetic-publication",
                workbench_owner,
            ),
            "reason_code": "initial-publication",
        },
        alias="reference-event",
    )

    builder.aliases["graph-record:node"] = node_alpha["id"]
    # The second node is published but is not the canonical malicious-fixture
    # target for the node body.
    builder.aliases["graph-record:node-beta"] = node_beta["id"]
    policy_expectations: set[tuple[str, str, str, str, str]] = set()

    def retain_policy_expectation(
        rule: str, record_id: str, binding: Mapping[str, str]
    ) -> None:
        policy_expectations.add(
            (
                rule,
                record_id,
                binding["owner_authority_id"],
                binding["owner_revision_id"],
                binding["authority_adapter_id"],
            )
        )

    for validated in builder.records:
        value = validated.to_dict()
        kind = validated.kind
        if (
            kind == "object-descriptor"
            and value["representation"] == "canonical-ndjson-shard"
        ):
            retain_policy_expectation(
                "canonical-total-order",
                validated.id,
                value["total_order_authority"],
            )
        elif kind == "admission-record":
            retain_policy_expectation(
                "admission-validation", validated.id, value["decision_owner"]
            )
        elif kind == "reference-event":
            retain_policy_expectation(
                "reference-authorization",
                validated.id,
                value["authorization_authority"],
            )
        if kind == "evidence-record":
            retain_policy_expectation(
                "evidence-assertion", validated.id, value["authority_owner"]
            )
        elif kind == "graph-record":
            retain_policy_expectation(
                "graph-record-semantics",
                validated.id,
                value["authority_owner"],
            )
        if kind == "graph-revision":
            retain_policy_expectation(
                "graph-materialization", validated.id, value["recipe_owner"]
            )
            retain_policy_expectation(
                "graph-category-semantics",
                validated.id,
                value["authority_owner"],
            )
        elif kind == "materialization-recipe":
            retain_policy_expectation(
                "recipe-definition", validated.id, value["recipe_owner"]
            )
            retain_policy_expectation(
                "recipe-definition", validated.id, value["authority_owner"]
            )
        elif kind == "graph-set-revision":
            retain_policy_expectation(
                "graph-set-composition",
                validated.id,
                value["composition_owner"],
            )
            retain_policy_expectation(
                "compatibility-rule-application",
                validated.id,
                value["compatibility_rule_authority"],
            )
        if kind in {
            "evidence-record",
            "evidence-set-revision",
            "graph-record",
            "graph-revision",
        }:
            retain_policy_expectation(
                "state-reduction", validated.id, value["authority_owner"]
            )
        elif kind == "graph-set-revision":
            retain_policy_expectation(
                "state-reduction", validated.id, value["composition_owner"]
            )
        if kind in {"graph-revision", "graph-set-revision"}:
            for profile_binding in value["profile_support_bindings"]:
                retain_policy_expectation(
                    "state-reduction",
                    validated.id,
                    profile_binding["profile_authority"],
                )
        if kind == "evidence-set-revision":
            retain_policy_expectation(
                "evidence-selection-history",
                validated.id,
                value["authority_owner"],
            )
        elif kind == "graph-record":
            recipe_owner = builder.records_by_id[value["recipe_id"]].to_dict()[
                "recipe_owner"
            ]
            retain_policy_expectation(
                "derivation", validated.id, recipe_owner
            )
        elif kind == "dependency-manifest":
            retain_policy_expectation(
                "dependency-closure", validated.id, value["recipe_owner"]
            )
            retain_policy_expectation(
                "dependency-semantics", validated.id, value["authority_owner"]
            )
        elif kind == "graph-set-revision":
            for member in [
                *value["members"],
                *value["join_graphs"],
                *value["refinement_graphs"],
            ]:
                retain_policy_expectation(
                    "compatibility-alignment",
                    validated.id,
                    member["alignment"]["decision_authority"],
                )
    return SyntheticPublication(
        records=tuple(builder.records),
        records_by_id=MappingProxyType(dict(builder.records_by_id)),
        blobs_by_id=MappingProxyType(dict(builder.blobs_by_id)),
        external_records_by_id=MappingProxyType(dict(builder.external_records_by_id)),
        external_scopes_by_id=MappingProxyType(dict(builder.external_scopes_by_id)),
        trusted_adapters=MappingProxyType(dict(builder.trusted_adapters)),
        policy_expectations=frozenset(policy_expectations),
        aliases=MappingProxyType(dict(builder.aliases)),
        graph_records_by_body_kind=MappingProxyType(
            {
                key: builder.records_by_id[value["id"]]
                for key, value in builder.graph_records.items()
                if key in _GRAPH_KIND_ORDER
            }
        ),
    )


__all__ = [
    "SyntheticPublication",
    "build_synthetic_publication",
]
