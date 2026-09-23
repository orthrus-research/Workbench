"""Bounded graph-recipe execution from one admitted evidence snapshot.

The qualified fresh worker returns semantic values, never caller-sealed graph
records. This module seals the records itself and delegates physical shard
construction to K01. It owns no store, reference, context selection, or
authority discovery behavior, and gives the worker none of those ports.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Iterable

from workbench_crucible_kernel import (
    AuthorityBinding,
    OwnerPolicyBinding,
    PartitionAssembly,
    ShardKernelConfig,
    ShardArtifact,
    assemble_graph_partitions,
)
from workbench_api.canonical import CANONICALIZER_ID, CanonicalJsonError, canonical_json_bytes, content_id, parse_canonical_json
from workbench_crucible import RecordValidationError, ValidatedRecord, load_canonical_record, seal_record, semantic_root_v2


_MAX_INPUT_BYTES = 1024 * 1024
_MAX_PAYLOAD_BYTES = 64 * 1024
_MAX_REGISTRATIONS = 32
_MAX_RECORDS_PER_SHARD = 64
_MAX_WORKER_OUTPUT_BYTES = 64 * 1024
_MAX_WORKER_SECONDS = 30
_MAX_GRAPH_RECORDS = 32
_MAX_GRAPH_IDENTITY_BYTES = 4096
_MAX_SOURCE_ROW_IDS_PER_RECORD = 32
_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_SEMANTIC = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SEMANTIC_TEXT = re.compile(r"[^\r\n\x00]{1,512}\Z")
_OBJECT_DESCRIPTOR_ID = re.compile(r"object-descriptor:sha256:[0-9a-f]{64}\Z")
_SCHEMA_ID = re.compile(r"workbench://schemas/[a-zA-Z0-9._~!$&'()*+,;=:@%/-]+\Z")
WORKER_PROTOCOL = "workbench-crucible-recipe-worker-v1"
_WORKER_HANDLER_DOMAIN = b"workbench-crucible-builtin-recipe-handler-v1\x00"


def _installed_worker_handler_id() -> str:
    worker_bytes = Path(__file__).with_name("_fresh_worker.py").read_bytes()
    return "handler:sha256:" + hashlib.sha256(
        _WORKER_HANDLER_DOMAIN + worker_bytes
    ).hexdigest()


BUILTIN_SYNTHETIC_BEFORE_HANDLER_ID = _installed_worker_handler_id()
BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID = (
    "capability:sha256:"
    "c4f9e22ccf08d3aaac37805f422b396b79d8a9ee6c0734505a8ee9487240164d"
)
BUILTIN_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID = (
    "capability:sha256:"
    "36d09c8c668450b22dc229529d4c8bcdfd6017fe8795cdeaa53271d9f351f271"
)
BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID = (
    "capability:sha256:"
    "d7c8bdfed0753400b2cb728a1196eb9f7e3926b5d561a0cf88cbaf8adf07fd1d"
)
BUILTIN_WORLDGEN_STABILITY_CAPABILITY_ID = (
    "capability:sha256:"
    "0000d61f88b9e552ee8579a917f28d1acfa1dcef00c95eeb6bf842d99fc9a1e9"
)
BUILTIN_WORLDGEN_GENERATIVE_CAPABILITY_ID = (
    "capability:sha256:"
    "49a20265c2ed3cf4c75dc6d4ff3342fba3d841001bff1a0047b50314e337773c"
)
BUILTIN_WORLDGEN_REALIZED_CAPABILITY_ID = (
    "capability:sha256:"
    "f5ac746b7779eaa31a59c175b26d91cea49509789e1c087824cb6e6e9572d27e"
)
BUILTIN_GRAPH_HANDLER_ID = BUILTIN_SYNTHETIC_BEFORE_HANDLER_ID
WORLDGEN_CAPTURE_HEALTH_GRAPH_VALUE_KIND = (
    "workbench-worldgen-capture-health-graph-v1"
)
WORLDGEN_OCCURRENCE_GRAPH_VALUE_KIND = "workbench-worldgen-occurrence-graph-v1"
WORLDGEN_STABILITY_GRAPH_VALUE_KIND = "workbench-worldgen-stability-graph-v1"
WORLDGEN_GENERATIVE_GRAPH_VALUE_KIND = "workbench-worldgen-generative-graph-v1"
WORLDGEN_REALIZED_GRAPH_VALUE_KIND = "workbench-worldgen-realized-graph-v1"
WORLDGEN_GRAPH_BUNDLE_VALUE_KIND = "workbench-worldgen-graph-bundle-v1"
_EVIDENCE_SUPPORT_ROLE = "materialization.input-evidence"
_PROPERTY_VALUE_SCHEMA_PREFIX = (
    "workbench://schemas/crucible-materializer/"
    "declarative-property-value/"
)
_WORLDGEN_VALUE_KINDS = {
    BUILTIN_WORLDGEN_CAPTURE_HEALTH_CAPABILITY_ID: WORLDGEN_CAPTURE_HEALTH_GRAPH_VALUE_KIND,
    BUILTIN_WORLDGEN_OCCURRENCE_CAPABILITY_ID: WORLDGEN_OCCURRENCE_GRAPH_VALUE_KIND,
    BUILTIN_WORLDGEN_STABILITY_CAPABILITY_ID: WORLDGEN_STABILITY_GRAPH_VALUE_KIND,
    BUILTIN_WORLDGEN_GENERATIVE_CAPABILITY_ID: WORLDGEN_GENERATIVE_GRAPH_VALUE_KIND,
    BUILTIN_WORLDGEN_REALIZED_CAPABILITY_ID: WORLDGEN_REALIZED_GRAPH_VALUE_KIND,
}
_BUILTIN_CAPABILITY_IDS = {
    BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID,
    *_WORLDGEN_VALUE_KINDS,
}


class MaterializationError(ValueError):
    """Stable fail-closed recipe-execution diagnostic."""

    def __init__(self, code: str, path: str, message: str):
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code: str, path: str, message: str) -> None:
    raise MaterializationError(code, path, message)


@dataclass(frozen=True, slots=True)
class MaterializedBlob:
    object_id: str
    data: bytes


@dataclass(frozen=True, slots=True)
class RecipeExecutionInput:
    execution_binding_id: str
    recipe_id: str
    implementation_id: str
    derivation_step_id: str
    evidence_record_id: str
    evidence_kind: str
    context_ref_id: str
    payload_bytes: bytes


@dataclass(frozen=True, slots=True)
class RecipeSemanticOutput:
    """Validated semantic output for the retained synthetic relation trial."""

    subject_namespace: str
    source_subject_name: str
    target_subject_name: str
    predicate_id: str
    direction: str
    partition_key: str


@dataclass(frozen=True, slots=True)
class _GraphSubject:
    """One validated worldgen subject proposal returned by the closed worker."""

    subject_key: str
    logical_key: str
    identity: Any
    source_row_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GraphRelation:
    """One validated worldgen relation proposal over subject keys."""

    logical_key: str
    source_subject_key: str
    target_subject_key: str
    predicate_id: str
    direction: str
    source_row_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GraphProperty:
    """One validated worldgen property proposal over a subject key."""

    logical_key: str
    subject_key: str
    property_key: str
    property_state: str
    value: Any
    source_row_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GraphSemanticOutput:
    """Bounded node/edge proposal from a retained worldgen payload."""

    value_kind: str
    partition_key: str
    subjects: tuple[_GraphSubject, ...]
    relations: tuple[_GraphRelation, ...]
    properties: tuple[_GraphProperty, ...]
    emit_evidence_links: bool


@dataclass(frozen=True, slots=True)
class RecipeExecutionBounds:
    """Owner-bound limits enforced by both parent and fresh worker."""

    max_input_bytes: int
    max_payload_bytes: int
    max_output_bytes: int
    records_per_shard: int
    worker_timeout_seconds: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
            "max_payload_bytes": self.max_payload_bytes,
            "records_per_shard": self.records_per_shard,
            "worker_timeout_seconds": self.worker_timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class RecipeExecutionBinding:
    """Immutable, content-addressed owner qualification of one recipe handler."""

    execution_binding_id: str
    recipe_id: str
    implementation_id: str
    source_tree_object_descriptor_id: str | None
    executable_object_descriptor_id: str | None
    execution_artifact_object_descriptor_id: str
    dependency_lock_object_descriptor_id: str
    runtime_environment_object_descriptor_id: str
    resource_limits_object_descriptor_id: str
    handler_id: str
    capability_id: str
    owner_authority_id: str
    owner_revision_id: str
    authority_adapter_id: str
    derivation_step_id: str
    output_contract_key: str
    subject_identity_schema_id: str
    subject_identity_schema_object_descriptor_id: str
    graph_record_schema_object_descriptor_id: str
    materializer_component_id: str
    materializer_implementation_id: str
    custodian_component_id: str
    custodian_implementation_id: str
    worker_protocol: str
    bounds: RecipeExecutionBounds
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_owner": {
                "authority_adapter_id": self.authority_adapter_id,
                "owner_authority_id": self.owner_authority_id,
                "owner_revision_id": self.owner_revision_id,
            },
            "bounds": self.bounds.to_dict(),
            "capability_id": self.capability_id,
            "custodian": {
                "component_id": self.custodian_component_id,
                "implementation_id": self.custodian_implementation_id,
            },
            "dependency_lock_object_descriptor_id": self.dependency_lock_object_descriptor_id,
            "derivation_step_id": self.derivation_step_id,
            "executable_object_descriptor_id": self.executable_object_descriptor_id,
            "execution_artifact_object_descriptor_id": self.execution_artifact_object_descriptor_id,
            "execution_binding_id": self.execution_binding_id,
            "format": "workbench-crucible-recipe-execution-binding-v1",
            "graph_record_schema_object_descriptor_id": self.graph_record_schema_object_descriptor_id,
            "handler_id": self.handler_id,
            "implementation_id": self.implementation_id,
            "materializer": {
                "component_id": self.materializer_component_id,
                "implementation_id": self.materializer_implementation_id,
            },
            "output_contract_key": self.output_contract_key,
            "recipe_id": self.recipe_id,
            "resource_limits_object_descriptor_id": self.resource_limits_object_descriptor_id,
            "runtime_environment_object_descriptor_id": self.runtime_environment_object_descriptor_id,
            "schema_version": 1,
            "source_tree_object_descriptor_id": self.source_tree_object_descriptor_id,
            "subject_identity_schema_id": self.subject_identity_schema_id,
            "subject_identity_schema_object_descriptor_id": self.subject_identity_schema_object_descriptor_id,
            "worker_protocol": self.worker_protocol,
        }


@dataclass(frozen=True, slots=True)
class RecipeImplementationRegistration:
    """One binding registered for the built-in fresh-worker adapter."""

    execution_binding: RecipeExecutionBinding

    @property
    def recipe_id(self) -> str:
        return self.execution_binding.recipe_id

    @property
    def implementation_id(self) -> str:
        return self.execution_binding.implementation_id

    @property
    def derivation_step_id(self) -> str:
        return self.execution_binding.derivation_step_id

    @property
    def output_contract_key(self) -> str:
        return self.execution_binding.output_contract_key

    @property
    def subject_identity_schema_id(self) -> str:
        return self.execution_binding.subject_identity_schema_id

    @property
    def records_per_shard(self) -> int:
        return self.execution_binding.bounds.records_per_shard


@dataclass(frozen=True, slots=True)
class RecipeImplementationRegistrySnapshot(
    Mapping[str, RecipeImplementationRegistration]
):
    """Bounded immutable registry ordering and root."""

    registrations: tuple[RecipeImplementationRegistration, ...]
    registry_root: str

    def __getitem__(self, implementation_id: str) -> RecipeImplementationRegistration:
        result = self.get(implementation_id)
        if result is None:
            raise KeyError(implementation_id)
        return result

    def __iter__(self):
        return (item.implementation_id for item in self.registrations)

    def __len__(self) -> int:
        return len(self.registrations)

    def get(self, implementation_id: str) -> RecipeImplementationRegistration | None:
        return next(
            (
                item
                for item in self.registrations
                if item.implementation_id == implementation_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class RecipeExecutionQualification:
    execution_binding_id: str
    qualification_root: str
    semantic_output: RecipeSemanticOutput | _GraphSemanticOutput


@dataclass(frozen=True, slots=True)
class MaterializationInput:
    recipe: ValidatedRecord
    evidence_set_revision: ValidatedRecord
    evidence_partition_descriptor: ValidatedRecord
    evidence_partition_bytes: bytes
    admission_partition_descriptor: ValidatedRecord
    admission_partition_bytes: bytes
    evidence: ValidatedRecord
    admission: ValidatedRecord
    payload_descriptor: ValidatedRecord
    payload_bytes: bytes


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    recipe_id: str
    execution_binding_id: str
    execution_qualification_root: str
    implementation_registry_root: str
    implementation_id: str
    derivation_step_id: str
    output_contract_key: str
    context_ref_id: str
    evidence_set_revision_id: str
    evidence_record_id: str
    admission_record_id: str
    authority_owner: AuthorityBinding
    recipe_owner: AuthorityBinding
    graph_family_id: str
    category_id: str
    resolution_id: str
    parameter_values_object_descriptor_id: str
    record_schema_object_descriptor_id: str
    partition_policy_id: str
    total_order_policy_id: str
    dependency_footprint_policy_id: str
    invalidation_policy_id: str
    evidence_partition_key: str
    evidence_shard_ordinal: int
    admission_partition_key: str
    admission_shard_ordinal: int
    graph_records: tuple[ValidatedRecord, ...]
    object_descriptors: tuple[ValidatedRecord, ...]
    blobs: tuple[MaterializedBlob, ...]
    assembly: PartitionAssembly

    def publication_fields(self) -> dict[str, Any]:
        """Return fresh mechanical dependency and graph candidate fields."""

        owner = self.authority_owner.to_dict()
        recipe_owner = self.recipe_owner.to_dict()
        components = [
            {
                "component_key": "materializer.recipe-implementation",
                "component_role": "recipe-implementation",
                "component_kind": "implementation",
                "component_id": self.implementation_id,
                "authority_binding": recipe_owner,
            },
            {
                "component_key": "materializer.graph-record-schema",
                "component_role": "record-schema",
                "component_kind": "object-descriptor",
                "component_id": self.record_schema_object_descriptor_id,
                "authority_binding": None,
            },
            {
                "component_key": "materializer.dependency-footprint-policy",
                "component_role": "dependency-footprint-policy",
                "component_kind": "policy",
                "component_id": self.dependency_footprint_policy_id,
                "authority_binding": recipe_owner,
            },
            {
                "component_key": "materializer.invalidation-policy",
                "component_role": "invalidation-policy",
                "component_kind": "policy",
                "component_id": self.invalidation_policy_id,
                "authority_binding": recipe_owner,
            },
        ]
        components.sort(key=canonical_json_bytes)
        output_partitions = [
                {
                    "partition_key": shard.coordinate.partition_key,
                    "shard_ordinal": shard.coordinate.shard_ordinal,
                    "record_kind": shard.coordinate.record_kind,
                }
                for shard in self.assembly.shards
            ]
        output_partitions.sort(key=canonical_json_bytes)
        footprint = {
            "output_partitions": output_partitions,
            "evidence_record_ids": [self.evidence_record_id],
            "admission_record_ids": [self.admission_record_id],
            "admission_partitions": [{
                "evidence_set_revision_id": self.evidence_set_revision_id,
                "partition_key": self.admission_partition_key,
                "shard_ordinal": self.admission_shard_ordinal,
            }],
            "evidence_partitions": [{
                "evidence_set_revision_id": self.evidence_set_revision_id,
                "partition_key": self.evidence_partition_key,
                "shard_ordinal": self.evidence_shard_ordinal,
            }],
            "source_graph_records": [],
            "source_graph_partitions": [],
            "component_keys": sorted(
                (item["component_key"] for item in components),
                key=lambda value: value.encode("utf-8"),
            ),
            "footprint_root": "",
        }
        footprint["footprint_root"] = semantic_root_v2(
            "dependency-manifest/footprint",
            {key: value for key, value in footprint.items() if key != "footprint_root"},
        )
        graph = self.assembly.graph_revision_fields()
        graph.update({
            "authority_owner": owner,
            "recipe_id": self.recipe_id,
            "recipe_implementation_id": self.implementation_id,
            "parameter_values_object_descriptor_id": self.parameter_values_object_descriptor_id,
            "evidence_set_revision_ids": [self.evidence_set_revision_id],
            "input_graph_bindings": [],
            "parent_graph_revision_ids": [],
        })
        return {
            "dependency": {
                "authority_owner": owner,
                "recipe_owner": recipe_owner,
                "recipe_id": self.recipe_id,
                "recipe_implementation_id": self.implementation_id,
                "context_ref_id": self.context_ref_id,
                "evidence_set_revision_ids": [self.evidence_set_revision_id],
                "input_graph_bindings": [],
                "parameter_values_object_descriptor_id": self.parameter_values_object_descriptor_id,
                "input_components": components,
                "parent_dependency_manifest_id": None,
                "dependency_state": "exact",
                "invalidation_policy_id": self.invalidation_policy_id,
                "footprints": [footprint],
            },
            "graph": graph,
        }


def _record(snapshot: Any, kind: str, path: str) -> ValidatedRecord:
    if type(snapshot) is not ValidatedRecord or type(snapshot.canonical_bytes) is not bytes:
        _fail("materializer.invalid-record", path, "input must be an exact validated snapshot")
    try:
        record = load_canonical_record(snapshot.canonical_bytes)
    except (RecordValidationError, TypeError) as exc:
        raise MaterializationError("materializer.invalid-record", path, "snapshot is not canonical C01 data") from exc
    if record.kind != kind:
        _fail("materializer.wrong-record-kind", path, f"record must be {kind}")
    return record


def _authority(value: dict[str, str]) -> AuthorityBinding:
    return AuthorityBinding(
        value["owner_authority_id"], value["owner_revision_id"], value["authority_adapter_id"]
    )


def _verify_blob(descriptor: ValidatedRecord, raw: bytes, path: str) -> dict[str, Any]:
    value = descriptor.to_dict()
    digest = hashlib.sha256(raw).hexdigest()
    if (
        value["object_id"] != f"workbench-blob-v2:sha256:{digest}"
        or value["sha256"] != digest
        or value["byte_length"] != len(raw)
    ):
        _fail("materializer.object-mismatch", path, "descriptor does not bind the exact bytes")
    return value


def _single_partition(
    revision: dict[str, Any], descriptor: ValidatedRecord, raw: bytes,
    supplied: ValidatedRecord, *, kind: str, field: str, path: str,
) -> tuple[str, int]:
    partitions = revision[field]
    if len(partitions) != 1 or partitions[0]["record_count"] != 1:
        _fail("materializer.membership-bound", path, "trial requires one single-row effective partition")
    partition = partitions[0]
    if partition["object_descriptor_id"] != descriptor.id:
        _fail("materializer.partition-descriptor", path, "revision does not bind the supplied descriptor")
    value = _verify_blob(descriptor, raw, f"{path}/bytes")
    if value["canonical_item_count"] != 1 or value["described_record_kind"] != kind:
        _fail("materializer.partition-descriptor", path, "descriptor has the wrong row contract")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        _fail("materializer.partition-framing", path, "partition must contain one LF-terminated row")
    row = _record(load_canonical_record(raw[:-1]), kind, f"{path}/row")
    if row.canonical_bytes != supplied.canonical_bytes:
        _fail("materializer.partition-membership", path, "supplied snapshot is not the effective row")
    return partition["partition_key"], partition["shard_ordinal"]


def _validate_bounds(value: Any, path: str) -> RecipeExecutionBounds:
    if type(value) is not RecipeExecutionBounds:
        _fail("materializer.invalid-execution-binding", path, "bounds must be an exact immutable snapshot")
    fields = value.to_dict()
    if any(type(item) is not int for item in fields.values()):
        _fail("materializer.invalid-execution-binding", path, "bounds must be exact integers")
    if not (1 <= value.max_input_bytes <= _MAX_INPUT_BYTES):
        _fail("materializer.input-bound", f"{path}/max_input_bytes", "worker input budget is outside the materializer bound")
    if not (
        1 <= value.max_payload_bytes <= _MAX_PAYLOAD_BYTES
        and value.max_payload_bytes <= value.max_input_bytes
    ):
        _fail("materializer.payload-bound", f"{path}/max_payload_bytes", "worker payload budget is outside the materializer bound")
    if not (1 <= value.max_output_bytes <= _MAX_WORKER_OUTPUT_BYTES):
        _fail("materializer.worker-output-bound", f"{path}/max_output_bytes", "worker output budget is outside the materializer bound")
    if not (1 <= value.records_per_shard <= _MAX_RECORDS_PER_SHARD):
        _fail("materializer.shard-bound", f"{path}/records_per_shard", "records-per-shard is outside the materializer bound")
    if not (1 <= value.worker_timeout_seconds <= _MAX_WORKER_SECONDS):
        _fail("materializer.worker-time-bound", f"{path}/worker_timeout_seconds", "worker timeout is outside the materializer bound")
    return RecipeExecutionBounds(**fields)


def _binding_without_id(binding: RecipeExecutionBinding) -> dict[str, Any]:
    value = binding.to_dict()
    value.pop("execution_binding_id")
    return value


def _snapshot_binding(value: Any, path: str = "/execution_binding") -> RecipeExecutionBinding:
    if type(value) is not RecipeExecutionBinding:
        _fail("materializer.invalid-execution-binding", path, "binding must be an exact immutable snapshot")
    bounds = _validate_bounds(value.bounds, f"{path}/bounds")
    exact_strings = (
        value.execution_binding_id,
        value.recipe_id,
        value.implementation_id,
        value.execution_artifact_object_descriptor_id,
        value.dependency_lock_object_descriptor_id,
        value.runtime_environment_object_descriptor_id,
        value.resource_limits_object_descriptor_id,
        value.handler_id,
        value.capability_id,
        value.owner_authority_id,
        value.owner_revision_id,
        value.authority_adapter_id,
        value.derivation_step_id,
        value.output_contract_key,
        value.subject_identity_schema_id,
        value.subject_identity_schema_object_descriptor_id,
        value.graph_record_schema_object_descriptor_id,
        value.materializer_component_id,
        value.materializer_implementation_id,
        value.custodian_component_id,
        value.custodian_implementation_id,
        value.worker_protocol,
    )
    if any(type(item) is not str for item in exact_strings) or type(value.canonical_bytes) is not bytes:
        _fail("materializer.invalid-execution-binding", path, "binding fields require exact immutable scalar types")
    for nullable in (value.source_tree_object_descriptor_id, value.executable_object_descriptor_id):
        if nullable is not None and type(nullable) is not str:
            _fail("materializer.invalid-execution-binding", path, "artifact descriptors must be strings or null")
    descriptor_ids = (
        value.execution_artifact_object_descriptor_id,
        value.dependency_lock_object_descriptor_id,
        value.runtime_environment_object_descriptor_id,
        value.resource_limits_object_descriptor_id,
        value.subject_identity_schema_object_descriptor_id,
        value.graph_record_schema_object_descriptor_id,
    ) + tuple(
        item for item in (value.source_tree_object_descriptor_id, value.executable_object_descriptor_id) if item is not None
    )
    if any(_OBJECT_DESCRIPTOR_ID.fullmatch(item) is None for item in descriptor_ids):
        _fail("materializer.invalid-execution-binding", path, "binding contains an invalid object descriptor ID")
    active_artifact = value.executable_object_descriptor_id or value.source_tree_object_descriptor_id
    if active_artifact is None or value.execution_artifact_object_descriptor_id != active_artifact:
        _fail("materializer.execution-artifact-mismatch", f"{path}/execution_artifact_object_descriptor_id", "active artifact must be the executable, or source when no executable exists")
    patterns = (
        (value.recipe_id, r"materialization-recipe:sha256:[0-9a-f]{64}"),
        (value.implementation_id, r"implementation:sha256:[0-9a-f]{64}"),
        (value.handler_id, r"handler:sha256:[0-9a-f]{64}"),
        (value.capability_id, r"capability:sha256:[0-9a-f]{64}"),
        (value.owner_authority_id, r"authority:sha256:[0-9a-f]{64}"),
        (value.owner_revision_id, r"owner-revision:sha256:[0-9a-f]{64}"),
        (value.authority_adapter_id, r"authority-adapter:sha256:[0-9a-f]{64}"),
        (value.derivation_step_id, r"derivation-step:sha256:[0-9a-f]{64}"),
        (value.materializer_component_id, r"component:sha256:[0-9a-f]{64}"),
        (value.materializer_implementation_id, r"implementation:sha256:[0-9a-f]{64}"),
        (value.custodian_component_id, r"component:sha256:[0-9a-f]{64}"),
        (value.custodian_implementation_id, r"implementation:sha256:[0-9a-f]{64}"),
    )
    if any(re.fullmatch(pattern, item) is None for item, pattern in patterns):
        _fail("materializer.invalid-execution-binding", path, "binding contains an invalid typed identity")
    if _SEMANTIC.fullmatch(value.output_contract_key) is None or _SCHEMA_ID.fullmatch(value.subject_identity_schema_id) is None:
        _fail("materializer.invalid-execution-binding", path, "binding contains an invalid output or schema identity")
    try:
        installed_handler_id = _installed_worker_handler_id()
    except OSError as exc:
        raise MaterializationError(
            "materializer.worker-unavailable",
            path,
            "installed fresh-worker handler cannot be measured",
        ) from exc
    if (
        value.worker_protocol != WORKER_PROTOCOL
        or value.handler_id != installed_handler_id
        or value.capability_id not in _BUILTIN_CAPABILITY_IDS
    ):
        _fail("materializer.worker-unavailable", path, "binding names no installed fresh-worker handler")
    expected_id = content_id("recipe-execution-binding", _binding_without_id(value))
    if value.execution_binding_id != expected_id or value.canonical_bytes != canonical_json_bytes(value.to_dict()):
        _fail("materializer.execution-binding-identity", path, "binding ID or retained canonical bytes do not match its fields")
    return RecipeExecutionBinding(
        **{
            field: (bounds if field == "bounds" else getattr(value, field))
            for field in value.__dataclass_fields__
        }
    )


def seal_recipe_execution_binding(
    recipe: ValidatedRecord,
    *,
    derivation_step_id: str,
    output_contract_key: str,
    handler_id: str,
    capability_id: str,
    subject_identity_schema_id: str,
    materializer_component_id: str,
    materializer_implementation_id: str,
    custodian_component_id: str,
    custodian_implementation_id: str,
    bounds: RecipeExecutionBounds,
) -> RecipeExecutionBinding:
    """Seal the exact recipe, owner, artifacts, handler, schemas, and bounds."""

    recipe = _record(recipe, "materialization-recipe", "/recipe")
    value = recipe.to_dict()
    step = next((item for item in value["derivation_steps"] if item["derivation_step_id"] == derivation_step_id), None)
    output = next((item for item in value["output_contracts"] if item["output_contract_key"] == output_contract_key), None)
    step_output = None if step is None else next((item for item in step["output_contracts"] if item["output_contract_key"] == output_contract_key), None)
    if step is None or output is None or step_output is None or step["implementation_id"] != value["implementation"]["implementation_id"] or len(output["subject_contracts"]) != 1:
        _fail("materializer.execution-binding-recipe", "/recipe", "step and output are not one exact recipe implementation contract")
    implementation = value["implementation"]
    artifact = implementation["executable_object_descriptor_id"] or implementation["source_tree_object_descriptor_id"]
    if artifact is None:
        _fail("materializer.execution-binding-recipe", "/recipe/implementation", "recipe has no executable or source artifact")
    owner = value["recipe_owner"]
    checked_bounds = _validate_bounds(bounds, "/bounds")
    fields: dict[str, Any] = {
        "recipe_id": recipe.id,
        "implementation_id": implementation["implementation_id"],
        "source_tree_object_descriptor_id": implementation["source_tree_object_descriptor_id"],
        "executable_object_descriptor_id": implementation["executable_object_descriptor_id"],
        "execution_artifact_object_descriptor_id": artifact,
        "dependency_lock_object_descriptor_id": implementation["dependency_lock_object_descriptor_id"],
        "runtime_environment_object_descriptor_id": implementation["runtime_environment_object_descriptor_id"],
        "resource_limits_object_descriptor_id": value["resource_limits_object_descriptor_id"],
        "handler_id": handler_id,
        "capability_id": capability_id,
        "owner_authority_id": owner["owner_authority_id"],
        "owner_revision_id": owner["owner_revision_id"],
        "authority_adapter_id": owner["authority_adapter_id"],
        "derivation_step_id": derivation_step_id,
        "output_contract_key": output_contract_key,
        "subject_identity_schema_id": subject_identity_schema_id,
        "subject_identity_schema_object_descriptor_id": output["subject_contracts"][0]["subject_identity_schema_object_descriptor_id"],
        "graph_record_schema_object_descriptor_id": output["graph_record_schema_object_descriptor_id"],
        "materializer_component_id": materializer_component_id,
        "materializer_implementation_id": materializer_implementation_id,
        "custodian_component_id": custodian_component_id,
        "custodian_implementation_id": custodian_implementation_id,
        "worker_protocol": WORKER_PROTOCOL,
        "bounds": checked_bounds,
    }
    provisional = RecipeExecutionBinding("", **fields, canonical_bytes=b"")
    execution_binding_id = content_id("recipe-execution-binding", _binding_without_id(provisional))
    with_id = RecipeExecutionBinding(execution_binding_id, **fields, canonical_bytes=b"")
    sealed = RecipeExecutionBinding(
        execution_binding_id,
        **fields,
        canonical_bytes=canonical_json_bytes(with_id.to_dict()),
    )
    return _snapshot_binding(sealed)


def _validate_binding_recipe(binding: RecipeExecutionBinding, recipe: ValidatedRecord) -> None:
    value = recipe.to_dict()
    implementation = value["implementation"]
    owner = value["recipe_owner"]
    step = next((item for item in value["derivation_steps"] if item["derivation_step_id"] == binding.derivation_step_id), None)
    output = next((item for item in value["output_contracts"] if item["output_contract_key"] == binding.output_contract_key), None)
    step_output = None if step is None else next((item for item in step["output_contracts"] if item["output_contract_key"] == binding.output_contract_key), None)
    expected = (
        binding.recipe_id == recipe.id,
        binding.implementation_id == implementation["implementation_id"],
        binding.source_tree_object_descriptor_id == implementation["source_tree_object_descriptor_id"],
        binding.executable_object_descriptor_id == implementation["executable_object_descriptor_id"],
        binding.dependency_lock_object_descriptor_id == implementation["dependency_lock_object_descriptor_id"],
        binding.runtime_environment_object_descriptor_id == implementation["runtime_environment_object_descriptor_id"],
        binding.resource_limits_object_descriptor_id == value["resource_limits_object_descriptor_id"],
        binding.owner_authority_id == owner["owner_authority_id"],
        binding.owner_revision_id == owner["owner_revision_id"],
        binding.authority_adapter_id == owner["authority_adapter_id"],
        step is not None and step["implementation_id"] == binding.implementation_id,
        step_output is not None,
        output is not None and len(output["subject_contracts"]) == 1,
        output is not None and output["graph_record_schema_object_descriptor_id"] == binding.graph_record_schema_object_descriptor_id,
        output is not None and output["subject_contracts"][0]["subject_identity_schema_object_descriptor_id"] == binding.subject_identity_schema_object_descriptor_id,
    )
    if not all(expected):
        _fail("materializer.execution-binding-mismatch", "/registrations", "execution binding does not match every identity-bearing recipe field")


def snapshot_recipe_implementation_registry(
    registrations: Mapping[str, RecipeImplementationRegistration] | RecipeImplementationRegistrySnapshot,
) -> RecipeImplementationRegistrySnapshot:
    if type(registrations) is RecipeImplementationRegistrySnapshot:
        raw_items = {item.implementation_id: item for item in registrations.registrations}
        supplied_root = registrations.registry_root
    else:
        try:
            raw_items = dict(registrations)
        except Exception as exc:
            raise MaterializationError("materializer.invalid-registry", "/registrations", "registry cannot be snapshotted") from exc
        supplied_root = None
    if len(raw_items) > _MAX_REGISTRATIONS:
        _fail("materializer.registry-bound", "/registrations", "registry exceeds the bounded implementation count")
    copies: list[RecipeImplementationRegistration] = []
    for key, registration in raw_items.items():
        if type(key) is not str or type(registration) is not RecipeImplementationRegistration:
            _fail("materializer.invalid-registry", "/registrations", "registry requires exact keyed registrations")
        binding = _snapshot_binding(registration.execution_binding, f"/registrations/{key}")
        if key != binding.implementation_id:
            _fail("materializer.invalid-registry", "/registrations", "registry key does not match the bound implementation")
        copies.append(RecipeImplementationRegistration(binding))
    copies.sort(key=lambda item: item.execution_binding.execution_binding_id.encode("utf-8"))
    if len({item.implementation_id for item in copies}) != len(copies):
        _fail("materializer.invalid-registry", "/registrations", "registry contains duplicate implementations")
    root = semantic_root_v2(
        "recipe-implementation-registry",
        [item.execution_binding.execution_binding_id for item in copies],
    )
    if supplied_root is not None and supplied_root != root:
        _fail("materializer.invalid-registry", "/registrations/registry_root", "registry snapshot root drifted")
    return RecipeImplementationRegistrySnapshot(tuple(copies), root)


def _registration(
    recipe: ValidatedRecord,
    registrations: Mapping[str, RecipeImplementationRegistration] | RecipeImplementationRegistrySnapshot,
) -> RecipeImplementationRegistration:
    snapshot = snapshot_recipe_implementation_registry(registrations)
    implementation_id = recipe.to_dict()["implementation"]["implementation_id"]
    registration = snapshot.get(implementation_id)
    if registration is None:
        _fail("materializer.implementation-unavailable", "/recipe/implementation", "exact implementation is not registered")
    _validate_binding_recipe(registration.execution_binding, recipe)
    return registration


def _validate_semantics(output: Any) -> RecipeSemanticOutput:
    if type(output) is not RecipeSemanticOutput:
        _fail("materializer.invalid-implementation-output", "/implementation", "worker returned the wrong exact output type")
    result = (
        output.subject_namespace,
        output.source_subject_name,
        output.target_subject_name,
        output.predicate_id,
        output.direction,
        output.partition_key,
    )
    if any(type(item) is not str for item in result):
        _fail("materializer.invalid-implementation-output", "/implementation", "worker output fields must be exact strings")
    namespace, source, target, predicate, direction, partition = result
    if not _SEMANTIC.fullmatch(namespace) or not _NAME.fullmatch(source) or not _NAME.fullmatch(target) or source == target:
        _fail("materializer.invalid-implementation-output", "/implementation/subjects", "subject semantics are invalid")
    if not _SEMANTIC.fullmatch(predicate) or direction not in {"directed", "symmetric"} or not _SEMANTIC.fullmatch(partition):
        _fail("materializer.invalid-implementation-output", "/implementation", "edge or partition semantics are invalid")
    return RecipeSemanticOutput(namespace, source, target, predicate, direction, partition)


def _graph_row_ids(value: Any, path: str) -> tuple[str, ...]:
    if (
        type(value) is not list
        or not 1 <= len(value) <= _MAX_SOURCE_ROW_IDS_PER_RECORD
        or any(
            type(item) is not str or _SEMANTIC_TEXT.fullmatch(item) is None
            for item in value
        )
        or value != sorted(value, key=lambda item: item.encode("utf-8"))
        or len(value) != len(set(value))
    ):
        _fail(
            "materializer.invalid-implementation-output",
            path,
            "source row IDs must be a non-empty, bounded, byte-ordered unique string list",
        )
    return tuple(value)


def _validate_graph_semantics(
    output: Any, capability_id: str
) -> _GraphSemanticOutput:
    expected_kind = _WORLDGEN_VALUE_KINDS.get(capability_id)
    if (
        expected_kind is None
        or type(output) is not dict
        or set(output)
        != {
            "emit_evidence_links",
            "partition_key",
            "properties",
            "relations",
            "subjects",
            "value_kind",
        }
        or output.get("value_kind") != expected_kind
        or type(output.get("partition_key")) is not str
        or _SEMANTIC.fullmatch(output["partition_key"]) is None
        or type(output.get("subjects")) is not list
        or type(output.get("relations")) is not list
        or type(output.get("properties")) is not list
        or type(output.get("emit_evidence_links")) is not bool
        or not output["subjects"]
        or (
            len(output["subjects"])
            + len(output["relations"])
            + len(output["properties"])
        )
        * (2 if output["emit_evidence_links"] else 1)
        > _MAX_GRAPH_RECORDS
    ):
        _fail(
            "materializer.invalid-implementation-output",
            "/implementation/output",
            "declarative output has the wrong capability, fields, or record bound",
        )

    subjects: list[_GraphSubject] = []
    subject_keys: set[str] = set()
    logical_keys: set[str] = set()
    identity_bytes: set[bytes] = set()
    previous_subject_key: bytes | None = None
    for index, item in enumerate(output["subjects"]):
        path = f"/implementation/output/subjects/{index}"
        if type(item) is not dict or set(item) != {
            "identity",
            "logical_key",
            "source_row_ids",
            "subject_key",
        }:
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative subject fields are not closed",
            )
        subject_key = item["subject_key"]
        logical_key = item["logical_key"]
        if (
            type(subject_key) is not str
            or _SEMANTIC_TEXT.fullmatch(subject_key) is None
            or type(logical_key) is not str
            or _SEMANTIC_TEXT.fullmatch(logical_key) is None
        ):
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative subject keys are invalid",
            )
        encoded_key = subject_key.encode("utf-8")
        if previous_subject_key is not None and encoded_key <= previous_subject_key:
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative subjects are not strictly ordered by subject key",
            )
        previous_subject_key = encoded_key
        try:
            raw_identity = canonical_json_bytes(item["identity"])
        except CanonicalJsonError as exc:
            raise MaterializationError(
                "materializer.invalid-implementation-output",
                f"{path}/identity",
                "subject identity is outside canonical JSON V2",
            ) from exc
        if len(raw_identity) > _MAX_GRAPH_IDENTITY_BYTES:
            _fail(
                "materializer.invalid-implementation-output",
                f"{path}/identity",
                "subject identity exceeds its byte bound",
            )
        if (
            subject_key in subject_keys
            or logical_key in logical_keys
            or raw_identity in identity_bytes
        ):
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative subject keys, logical keys, and identities must be unique",
            )
        subject_keys.add(subject_key)
        logical_keys.add(logical_key)
        identity_bytes.add(raw_identity)
        subjects.append(
            _GraphSubject(
                subject_key,
                logical_key,
                item["identity"],
                _graph_row_ids(item["source_row_ids"], f"{path}/source_row_ids"),
            )
        )

    relations: list[_GraphRelation] = []
    previous_relation_key: bytes | None = None
    for index, item in enumerate(output["relations"]):
        path = f"/implementation/output/relations/{index}"
        if type(item) is not dict or set(item) != {
            "direction",
            "logical_key",
            "predicate_id",
            "source_row_ids",
            "source_subject_key",
            "target_subject_key",
        }:
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative relation fields are not closed",
            )
        logical_key = item["logical_key"]
        source_key = item["source_subject_key"]
        target_key = item["target_subject_key"]
        predicate = item["predicate_id"]
        direction = item["direction"]
        if (
            type(logical_key) is not str
            or _SEMANTIC_TEXT.fullmatch(logical_key) is None
            or logical_key in logical_keys
            or type(source_key) is not str
            or source_key not in subject_keys
            or type(target_key) is not str
            or target_key not in subject_keys
            or type(predicate) is not str
            or _SEMANTIC.fullmatch(predicate) is None
            or direction not in {"directed", "symmetric"}
        ):
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative relation keys, endpoints, predicate, or direction are invalid",
            )
        encoded_key = logical_key.encode("utf-8")
        if previous_relation_key is not None and encoded_key <= previous_relation_key:
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative relations are not strictly ordered by logical key",
            )
        previous_relation_key = encoded_key
        logical_keys.add(logical_key)
        relations.append(
            _GraphRelation(
                logical_key,
                source_key,
                target_key,
                predicate,
                direction,
                _graph_row_ids(item["source_row_ids"], f"{path}/source_row_ids"),
            )
        )

    properties: list[_GraphProperty] = []
    previous_property_key: bytes | None = None
    for index, item in enumerate(output["properties"]):
        path = f"/implementation/output/properties/{index}"
        if type(item) is not dict or set(item) != {
            "logical_key",
            "property_key",
            "property_state",
            "source_row_ids",
            "subject_key",
            "value",
        }:
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative property fields are not closed",
            )
        logical_key = item["logical_key"]
        subject_key = item["subject_key"]
        property_key = item["property_key"]
        property_state = item["property_state"]
        if (
            type(logical_key) is not str
            or _SEMANTIC_TEXT.fullmatch(logical_key) is None
            or logical_key in logical_keys
            or type(subject_key) is not str
            or subject_key not in subject_keys
            or type(property_key) is not str
            or _SEMANTIC.fullmatch(property_key) is None
            or type(property_state) is not str
            or _SEMANTIC.fullmatch(property_state) is None
        ):
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative property keys, subject, or state are invalid",
            )
        encoded_key = logical_key.encode("utf-8")
        if previous_property_key is not None and encoded_key <= previous_property_key:
            _fail(
                "materializer.invalid-implementation-output",
                path,
                "declarative properties are not strictly ordered by logical key",
            )
        previous_property_key = encoded_key
        try:
            raw_value = canonical_json_bytes(
                {
                    "property_state": property_state,
                    "value": item["value"],
                }
            )
        except CanonicalJsonError as exc:
            raise MaterializationError(
                "materializer.invalid-implementation-output",
                f"{path}/value",
                "property value is outside canonical JSON V2",
            ) from exc
        if len(raw_value) > _MAX_GRAPH_IDENTITY_BYTES:
            _fail(
                "materializer.invalid-implementation-output",
                f"{path}/value",
                "property value exceeds its byte bound",
            )
        logical_keys.add(logical_key)
        properties.append(
            _GraphProperty(
                logical_key,
                subject_key,
                property_key,
                property_state,
                item["value"],
                _graph_row_ids(item["source_row_ids"], f"{path}/source_row_ids"),
            )
        )
    return _GraphSemanticOutput(
        expected_kind,
        output["partition_key"],
        tuple(subjects),
        tuple(relations),
        tuple(properties),
        output["emit_evidence_links"],
    )


def _semantic_output_value(
    output: RecipeSemanticOutput | _GraphSemanticOutput,
) -> dict[str, Any]:
    if type(output) is RecipeSemanticOutput:
        return {
            field: getattr(output, field) for field in output.__dataclass_fields__
        }
    if type(output) is _GraphSemanticOutput:
        return {
            "emit_evidence_links": output.emit_evidence_links,
            "partition_key": output.partition_key,
            "properties": [
                {
                    "logical_key": item.logical_key,
                    "property_key": item.property_key,
                    "property_state": item.property_state,
                    "source_row_ids": list(item.source_row_ids),
                    "subject_key": item.subject_key,
                    "value": item.value,
                }
                for item in output.properties
            ],
            "relations": [
                {
                    "direction": item.direction,
                    "logical_key": item.logical_key,
                    "predicate_id": item.predicate_id,
                    "source_row_ids": list(item.source_row_ids),
                    "source_subject_key": item.source_subject_key,
                    "target_subject_key": item.target_subject_key,
                }
                for item in output.relations
            ],
            "subjects": [
                {
                    "identity": item.identity,
                    "logical_key": item.logical_key,
                    "source_row_ids": list(item.source_row_ids),
                    "subject_key": item.subject_key,
                }
                for item in output.subjects
            ],
            "value_kind": output.value_kind,
        }
    _fail(
        "materializer.invalid-implementation-output",
        "/implementation/output",
        "worker returned an unknown semantic output type",
    )


def _fresh_worker_call(
    binding: RecipeExecutionBinding,
    request: RecipeExecutionInput,
    phase: str,
) -> RecipeSemanticOutput | _GraphSemanticOutput:
    if type(request) is not RecipeExecutionInput:
        _fail("materializer.worker-request-mismatch", "/implementation", "worker request must be an exact immutable value")
    request_strings = (
        request.execution_binding_id,
        request.recipe_id,
        request.implementation_id,
        request.derivation_step_id,
        request.evidence_record_id,
        request.evidence_kind,
        request.context_ref_id,
    )
    if (
        any(type(item) is not str for item in request_strings)
        or request.execution_binding_id != binding.execution_binding_id
        or request.recipe_id != binding.recipe_id
        or request.implementation_id != binding.implementation_id
        or request.derivation_step_id != binding.derivation_step_id
        or re.fullmatch(r"evidence-record:sha256:[0-9a-f]{64}", request.evidence_record_id) is None
        or _SEMANTIC.fullmatch(request.evidence_kind) is None
        or re.fullmatch(r"context-ref:sha256:[0-9a-f]{64}", request.context_ref_id) is None
    ):
        _fail("materializer.worker-request-mismatch", "/implementation", "worker request does not match the execution binding")
    if type(request.payload_bytes) is not bytes or len(request.payload_bytes) > binding.bounds.max_payload_bytes:
        _fail("materializer.payload-bound", "/implementation/payload", "worker payload exceeds its owner-bound budget")
    nonce = hashlib.sha256(
        canonical_json_bytes({
            "capability_id": binding.capability_id,
            "derivation_step_id": binding.derivation_step_id,
            "evidence_kind": request.evidence_kind,
            "execution_binding_id": binding.execution_binding_id,
            "evidence_record_id": request.evidence_record_id,
            "implementation_id": binding.implementation_id,
            "output_contract_key": binding.output_contract_key,
            "payload_sha256": hashlib.sha256(request.payload_bytes).hexdigest(),
            "phase": phase,
            "recipe_id": binding.recipe_id,
        })
    ).hexdigest()
    wire = canonical_json_bytes({
        "capability_id": binding.capability_id,
        "derivation_step_id": binding.derivation_step_id,
        "evidence_kind": request.evidence_kind,
        "execution_binding_id": binding.execution_binding_id,
        "handler_id": binding.handler_id,
        "implementation_id": binding.implementation_id,
        "output_contract_key": binding.output_contract_key,
        "payload_base64": base64.b64encode(request.payload_bytes).decode("ascii"),
        "payload_sha256": hashlib.sha256(request.payload_bytes).hexdigest(),
        "protocol": binding.worker_protocol,
        "recipe_id": binding.recipe_id,
        "request_nonce": nonce,
    })
    if len(wire) > binding.bounds.max_input_bytes:
        _fail("materializer.input-bound", "/implementation", "canonical worker request exceeds its owner-bound budget")
    worker = Path(__file__).with_name("_fresh_worker.py")
    profiles = {
        "qualification-a": ("C", "UTC"),
        "qualification-b": ("C.UTF-8", "Etc/GMT+5"),
        "execution": ("C.UTF-8", "UTC"),
    }
    profile = profiles.get(phase)
    if profile is None:
        _fail("materializer.worker-request-mismatch", "/implementation", "worker phase is outside the closed protocol")
    environment = {
        "LANG": profile[0],
        "LC_ALL": profile[0],
        "TZ": profile[1],
        "WORKBENCH_RECIPE_WORKER": "isolated-v1",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="workbench-recipe-worker-") as directory:
            os.chmod(directory, 0o700)
            completed = subprocess.run(
                [sys.executable, "-I", "-S", str(worker)],
                cwd=directory,
                env=environment,
                input=wire,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                close_fds=True,
                start_new_session=True,
                timeout=binding.bounds.worker_timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        raise MaterializationError(
            "materializer.worker-timeout",
            "/implementation",
            "fresh worker exceeded its owner-bound timeout",
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise MaterializationError("materializer.worker-failed", "/implementation", "fresh worker did not complete") from exc
    if completed.returncode != 0:
        _fail("materializer.implementation-failed", "/implementation", "fresh worker rejected the bounded request")
    if len(completed.stdout) > binding.bounds.max_output_bytes:
        _fail("materializer.worker-output-bound", "/implementation", "fresh worker output exceeds its owner-bound budget")
    try:
        value = parse_canonical_json(completed.stdout)
    except CanonicalJsonError as exc:
        raise MaterializationError("materializer.invalid-implementation-output", "/implementation", "fresh worker output is not canonical JSON") from exc
    if (
        type(value) is not dict
        or set(value) != {
            "execution_binding_id",
            "output",
            "protocol",
            "request_nonce",
        }
        or value["execution_binding_id"] != binding.execution_binding_id
        or value["protocol"] != binding.worker_protocol
        or value["request_nonce"] != nonce
        or type(value["output"]) is not dict
    ):
        _fail("materializer.invalid-implementation-output", "/implementation", "fresh worker output does not match the exact protocol")
    if binding.capability_id == BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID:
        if set(value["output"]) != {
            "direction",
            "partition_key",
            "predicate_id",
            "source_subject_name",
            "subject_namespace",
            "target_subject_name",
        }:
            _fail(
                "materializer.invalid-implementation-output",
                "/implementation/output",
                "synthetic worker output does not match its exact protocol",
            )
        return _validate_semantics(RecipeSemanticOutput(**value["output"]))
    return _validate_graph_semantics(value["output"], binding.capability_id)


def qualify_recipe_execution(
    registration: RecipeImplementationRegistration,
    request: RecipeExecutionInput,
) -> RecipeExecutionQualification:
    """Qualify two fresh workers before one separately executed worker run."""

    if type(registration) is not RecipeImplementationRegistration:
        _fail("materializer.invalid-registry", "/registration", "registration must be an exact immutable snapshot")
    binding = _snapshot_binding(registration.execution_binding)
    first = _fresh_worker_call(binding, request, "qualification-a")
    second = _fresh_worker_call(binding, request, "qualification-b")
    if first != second:
        _fail("materializer.nondeterministic-output", "/implementation", "fresh qualification workers returned different semantics")
    root = semantic_root_v2(
        "recipe-execution-qualification",
        {
            "execution_binding_id": binding.execution_binding_id,
            "output": _semantic_output_value(first),
            "payload_sha256": hashlib.sha256(request.payload_bytes).hexdigest(),
        },
    )
    return RecipeExecutionQualification(binding.execution_binding_id, root, first)


def _exact_object(
    raw: bytes, *, media_type: str, semantic_role: str
) -> tuple[ValidatedRecord, MaterializedBlob]:
    digest = hashlib.sha256(raw).hexdigest()
    object_id = f"workbench-blob-v2:sha256:{digest}"
    descriptor = seal_record(
        {
            "kind": "object-descriptor",
            "format": "workbench-crucible-object-descriptor-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-object-descriptor-v2.schema.json",
            "canonicalizer": CANONICALIZER_ID,
            "object_id": object_id,
            "sha256": digest,
            "byte_length": len(raw),
            "media_type": media_type,
            "representation": "exact-bytes",
            "semantic_role": semantic_role,
            "described_schema_id": None,
            "described_schema_object_descriptor_id": None,
            "described_record_kind": None,
            "canonical_item_count": None,
            "total_order_policy_id": None,
            "total_order_authority": None,
            "minimum_record_key": None,
            "maximum_record_key": None,
        }
    )
    return descriptor, MaterializedBlob(object_id, raw)


def _canonical_value_object(
    value: Any,
    *,
    semantic_role: str,
    schema_id: str,
    schema_descriptor_id: str,
) -> tuple[ValidatedRecord, MaterializedBlob]:
    raw = canonical_json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    object_id = f"workbench-blob-v2:sha256:{digest}"
    descriptor = seal_record(
        {
            "kind": "object-descriptor",
            "format": "workbench-crucible-object-descriptor-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-object-descriptor-v2.schema.json",
            "canonicalizer": CANONICALIZER_ID,
            "object_id": object_id,
            "sha256": digest,
            "byte_length": len(raw),
            "media_type": "application/json",
            "representation": "canonical-json-value",
            "semantic_role": semantic_role,
            "described_schema_id": schema_id,
            "described_schema_object_descriptor_id": schema_descriptor_id,
            "described_record_kind": None,
            "canonical_item_count": 1,
            "total_order_policy_id": None,
            "total_order_authority": None,
            "minimum_record_key": None,
            "maximum_record_key": None,
        }
    )
    return descriptor, MaterializedBlob(object_id, raw)


def _subject(
    identity: Any,
    subject_kind: str,
    schema_id: str,
    schema_descriptor_id: str,
) -> tuple[str, ValidatedRecord, MaterializedBlob]:
    descriptor, blob = _canonical_value_object(
        identity,
        semantic_role="subject-identity",
        schema_id=schema_id,
        schema_descriptor_id=schema_descriptor_id,
    )
    return content_id(subject_kind, identity), descriptor, blob


def _source_row_provenance(
    source_row_ids: tuple[str, ...],
) -> tuple[ValidatedRecord, MaterializedBlob]:
    return _exact_object(
        canonical_json_bytes({"source_row_ids": list(source_row_ids)}),
        media_type="application/json",
        semantic_role="source-row-provenance",
    )


def _closed_exact_schema(value: Any) -> dict[str, Any]:
    if type(value) is dict:
        return {
            "additionalProperties": False,
            "properties": {
                key: _closed_exact_schema(item) for key, item in value.items()
            },
            "required": sorted(value, key=lambda key: key.encode("utf-8")),
            "type": "object",
        }
    return {"const": value}


def _property_value(
    property_state: str, value: Any
) -> tuple[
    ValidatedRecord,
    MaterializedBlob,
    ValidatedRecord,
    MaterializedBlob,
]:
    property_value = {"property_state": property_state, "value": value}
    property_value_digest = hashlib.sha256(
        canonical_json_bytes(property_value)
    ).hexdigest()
    property_schema_id = (
        f"{_PROPERTY_VALUE_SCHEMA_PREFIX}{property_value_digest}.schema.json"
    )
    property_schema = {
        "$id": property_schema_id,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_closed_exact_schema(property_value),
    }
    schema_descriptor, schema_blob = _exact_object(
        canonical_json_bytes(property_schema),
        media_type="application/schema+json",
        semantic_role="property-value-schema",
    )
    value_descriptor, value_blob = _canonical_value_object(
        property_value,
        semantic_role="property-value",
        schema_id=property_schema_id,
        schema_descriptor_id=schema_descriptor.id,
    )
    return schema_descriptor, schema_blob, value_descriptor, value_blob


def _local_source_contract(
    step: Mapping[str, Any], output_contract_key: str, record_kind: str
) -> tuple[str, Mapping[str, Any]]:
    matches = [
        (item["input_contract_key"], contract)
        for item in step["input_contracts"]
        if item["input_mode"] == "source-graph-record"
        for contract in item["source_contracts"]
        if contract["source_kind"] == "local-output"
        and contract["contract_key"] == output_contract_key
        and record_kind in contract["record_kinds"]
    ]
    if len(matches) != 1:
        _fail(
            "materializer.recipe-profile",
            "/recipe/derivation_steps",
            f"local {record_kind} provenance requires one exact source contract",
        )
    return matches[0]


def _local_source_input(
    input_contract_key: str,
    output_contract_key: str,
    record_id: str,
) -> dict[str, Any]:
    return {
        "input_contract_key": input_contract_key,
        "source_kind": "local-output",
        "contract_key": output_contract_key,
        "predecessor_step_id": None,
        "graph_record_id": record_id,
    }


def _seal_synthetic_semantics(
    semantics: RecipeSemanticOutput,
    *,
    header: Mapping[str, Any],
    common: Mapping[str, Any],
    step: Mapping[str, Any],
    output_contract_key: str,
    subject_contract: Mapping[str, Any],
    subject_identity_schema_id: str,
) -> tuple[
    tuple[ValidatedRecord, ...],
    tuple[ValidatedRecord, ...],
    tuple[MaterializedBlob, ...],
]:
    left_identity = {
        "namespace": semantics.subject_namespace,
        "name": semantics.source_subject_name,
    }
    right_identity = {
        "namespace": semantics.subject_namespace,
        "name": semantics.target_subject_name,
    }
    left_id, left_descriptor, left_blob = _subject(
        left_identity,
        subject_contract["subject_kind"],
        subject_identity_schema_id,
        subject_contract["subject_identity_schema_object_descriptor_id"],
    )
    right_id, right_descriptor, right_blob = _subject(
        right_identity,
        subject_contract["subject_kind"],
        subject_identity_schema_id,
        subject_contract["subject_identity_schema_object_descriptor_id"],
    )

    def node(name: str, subject_id: str, descriptor_id: str) -> ValidatedRecord:
        return seal_record(
            {
                **header,
                **common,
                "logical_key": f"node:{name}",
                "body": {
                    "record_kind": "node",
                    "subject_id": subject_id,
                    "subject_identity_object_descriptor_id": descriptor_id,
                    "node_kind": subject_contract["node_kind"],
                },
                "source_graph_inputs": [],
            }
        )

    left = node(semantics.source_subject_name, left_id, left_descriptor.id)
    right = node(semantics.target_subject_name, right_id, right_descriptor.id)
    node_input_key, _ = _local_source_contract(
        step, output_contract_key, "node"
    )
    source_graph_inputs = [
        _local_source_input(node_input_key, output_contract_key, item.id)
        for item in (left, right)
    ]
    source_graph_inputs.sort(key=canonical_json_bytes)
    edge = seal_record(
        {
            **header,
            **common,
            "logical_key": (
                f"edge:{semantics.source_subject_name}-"
                f"{semantics.predicate_id.rsplit('.', 1)[-1]}-"
                f"{semantics.target_subject_name}"
            ),
            "body": {
                "record_kind": "edge",
                "source_subject_id": left_id,
                "target_subject_id": right_id,
                "source_node_record_id": left.id,
                "target_node_record_id": right.id,
                "predicate_id": semantics.predicate_id,
                "direction": semantics.direction,
            },
            "source_graph_inputs": source_graph_inputs,
        }
    )
    return (
        (left, right, edge),
        (left_descriptor, right_descriptor),
        (left_blob, right_blob),
    )


def _seal_graph_semantics(
    semantics: _GraphSemanticOutput,
    *,
    header: Mapping[str, Any],
    common: Mapping[str, Any],
    step: Mapping[str, Any],
    step_output: Mapping[str, Any],
    output_contract_key: str,
    subject_contract: Mapping[str, Any],
    subject_identity_schema_id: str,
    evidence_record_id: str,
) -> tuple[
    tuple[ValidatedRecord, ...],
    tuple[ValidatedRecord, ...],
    tuple[MaterializedBlob, ...],
]:
    required_kinds = {"node"}
    if semantics.relations:
        required_kinds.add("edge")
    if semantics.properties:
        required_kinds.add("property")
    if semantics.emit_evidence_links:
        required_kinds.add("evidence-link")
    if not required_kinds.issubset(set(step_output["output_record_kinds"])):
        _fail(
            "materializer.recipe-profile",
            "/recipe/derivation_steps/output_contracts",
            "recipe step does not declare every emitted graph record kind",
        )

    descriptors: list[ValidatedRecord] = []
    blobs: list[MaterializedBlob] = []
    descriptor_ids: set[str] = set()
    object_ids: set[str] = set()

    def retain(descriptor: ValidatedRecord, blob: MaterializedBlob) -> None:
        if descriptor.id not in descriptor_ids:
            descriptor_ids.add(descriptor.id)
            descriptors.append(descriptor)
        if blob.object_id not in object_ids:
            object_ids.add(blob.object_id)
            blobs.append(blob)

    nodes: list[ValidatedRecord] = []
    node_by_key: dict[str, tuple[str, ValidatedRecord]] = {}
    for proposal in semantics.subjects:
        subject_id, identity_descriptor, identity_blob = _subject(
            proposal.identity,
            subject_contract["subject_kind"],
            subject_identity_schema_id,
            subject_contract["subject_identity_schema_object_descriptor_id"],
        )
        provenance_descriptor, provenance_blob = _source_row_provenance(
            proposal.source_row_ids
        )
        retain(identity_descriptor, identity_blob)
        retain(provenance_descriptor, provenance_blob)
        node = seal_record(
            {
                **header,
                **common,
                "logical_key": proposal.logical_key,
                "body": {
                    "record_kind": "node",
                    "subject_id": subject_id,
                    "subject_identity_object_descriptor_id": identity_descriptor.id,
                    "node_kind": subject_contract["node_kind"],
                },
                "source_graph_inputs": [],
                "qualifiers_object_descriptor_id": provenance_descriptor.id,
            }
        )
        nodes.append(node)
        node_by_key[proposal.subject_key] = (subject_id, node)

    node_input_key, _ = _local_source_contract(
        step, output_contract_key, "node"
    )
    edges: list[ValidatedRecord] = []
    for proposal in semantics.relations:
        source_id, source_node = node_by_key[proposal.source_subject_key]
        target_id, target_node = node_by_key[proposal.target_subject_key]
        provenance_descriptor, provenance_blob = _source_row_provenance(
            proposal.source_row_ids
        )
        retain(provenance_descriptor, provenance_blob)
        source_graph_inputs = [
            _local_source_input(node_input_key, output_contract_key, item.id)
            for item in (source_node, target_node)
        ]
        source_graph_inputs.sort(key=canonical_json_bytes)
        edges.append(
            seal_record(
                {
                    **header,
                    **common,
                    "logical_key": proposal.logical_key,
                    "body": {
                        "record_kind": "edge",
                        "source_subject_id": source_id,
                        "target_subject_id": target_id,
                        "source_node_record_id": source_node.id,
                        "target_node_record_id": target_node.id,
                        "predicate_id": proposal.predicate_id,
                        "direction": proposal.direction,
                    },
                    "source_graph_inputs": source_graph_inputs,
                    "qualifiers_object_descriptor_id": provenance_descriptor.id,
                }
            )
        )

    properties: list[ValidatedRecord] = []
    for proposal in semantics.properties:
        subject_id, subject_node = node_by_key[proposal.subject_key]
        (
            value_schema_descriptor,
            value_schema_blob,
            value_descriptor,
            value_blob,
        ) = _property_value(proposal.property_state, proposal.value)
        provenance_descriptor, provenance_blob = _source_row_provenance(
            proposal.source_row_ids
        )
        retain(value_schema_descriptor, value_schema_blob)
        retain(value_descriptor, value_blob)
        retain(provenance_descriptor, provenance_blob)
        properties.append(
            seal_record(
                {
                    **header,
                    **common,
                    "logical_key": proposal.logical_key,
                    "body": {
                        "record_kind": "property",
                        "subject_id": subject_id,
                        "subject_node_record_id": subject_node.id,
                        "property_id": proposal.property_key,
                        "value_schema_object_descriptor_id": value_schema_descriptor.id,
                        "value_object_descriptor_id": value_descriptor.id,
                    },
                    "source_graph_inputs": [
                        _local_source_input(
                            node_input_key, output_contract_key, subject_node.id
                        )
                    ],
                    "qualifiers_object_descriptor_id": provenance_descriptor.id,
                }
            )
        )

    base_records = [*nodes, *edges, *properties]
    evidence_links: list[ValidatedRecord] = []
    if semantics.emit_evidence_links:
        for supported in base_records:
            supported_kind = supported.to_dict()["body"]["record_kind"]
            input_key, _ = _local_source_contract(
                step, output_contract_key, supported_kind
            )
            evidence_links.append(
                seal_record(
                    {
                        **header,
                        **common,
                        "logical_key": f"evidence-link:{supported.id}",
                        "body": {
                            "record_kind": "evidence-link",
                            "supported_graph_record_id": supported.id,
                            "supporting_evidence_record_id": evidence_record_id,
                            "support_role": _EVIDENCE_SUPPORT_ROLE,
                        },
                        "source_graph_inputs": [
                            _local_source_input(
                                input_key, output_contract_key, supported.id
                            )
                        ],
                    }
                )
            )
    graph_records = tuple([*base_records, *evidence_links])
    if len(graph_records) > _MAX_GRAPH_RECORDS:
        _fail(
            "materializer.invalid-implementation-output",
            "/implementation/output",
            "sealed graph record count exceeds the declarative bound",
        )
    return graph_records, tuple(descriptors), tuple(blobs)


def execute_materialization(
    source: MaterializationInput,
    *,
    registrations: Mapping[str, RecipeImplementationRegistration]
    | RecipeImplementationRegistrySnapshot,
    prior_shards: Iterable[ShardArtifact] = (),
) -> MaterializationResult:
    """Execute one exact admitted graph recipe and assemble its K01 shards.

    ``prior_shards`` are untrusted cache hints. K01 reconstructs and validates
    every hint and reuses one only when the freshly computed coordinate and all
    identity-bearing bytes are equal. Recipe execution and the complete target
    row set are therefore still recomputed from the pinned inputs.
    """

    if type(source) is not MaterializationInput:
        _fail("materializer.invalid-input", "/source", "source must be an exact MaterializationInput")
    recipe = _record(source.recipe, "materialization-recipe", "/recipe")
    revision = _record(source.evidence_set_revision, "evidence-set-revision", "/evidence_set_revision")
    evidence_descriptor = _record(source.evidence_partition_descriptor, "object-descriptor", "/evidence_partition_descriptor")
    admission_descriptor = _record(source.admission_partition_descriptor, "object-descriptor", "/admission_partition_descriptor")
    evidence = _record(source.evidence, "evidence-record", "/evidence")
    admission = _record(source.admission, "admission-record", "/admission")
    payload_descriptor = _record(source.payload_descriptor, "object-descriptor", "/payload_descriptor")
    byte_values = (source.evidence_partition_bytes, source.admission_partition_bytes, source.payload_bytes)
    if any(type(value) is not bytes for value in byte_values):
        _fail("materializer.input-bound", "/source", "input requires exact immutable bytes")
    aggregate_input_bytes = sum(map(len, byte_values)) + sum(
        len(item.canonical_bytes) for item in (recipe, revision, evidence_descriptor, admission_descriptor, evidence, admission, payload_descriptor)
    )
    if aggregate_input_bytes > _MAX_INPUT_BYTES:
        _fail("materializer.input-bound", "/source", "input exceeds the aggregate byte budget")
    rv, ev, av = revision.to_dict(), evidence.to_dict(), admission.to_dict()
    if rv["effective_evidence_count"] != 1 or rv["effective_admission_count"] != 1:
        _fail("materializer.membership-bound", "/evidence_set_revision", "trial requires one effective evidence/admission pair")
    evidence_key, evidence_ordinal = _single_partition(rv, evidence_descriptor, source.evidence_partition_bytes, evidence, kind="evidence-record", field="evidence_partitions", path="/evidence_partition")
    admission_key, admission_ordinal = _single_partition(rv, admission_descriptor, source.admission_partition_bytes, admission, kind="admission-record", field="admission_partitions", path="/admission_partition")
    if av["candidate_record_id"] != evidence.id or av["context_ref_id"] != ev["context_ref_id"] or av["outcome"] != "admitted" or av["eligible_for_materialization"] is not True:
        _fail("materializer.not-admitted", "/admission", "evidence lacks one exact eligible admission")
    if rv["context_ref_id"] != ev["context_ref_id"]:
        _fail("materializer.context-mismatch", "/evidence_set_revision", "evidence and revision contexts differ")
    registry_snapshot = snapshot_recipe_implementation_registry(registrations)
    registration = _registration(recipe, registry_snapshot)
    binding = registration.execution_binding
    if aggregate_input_bytes > binding.bounds.max_input_bytes:
        _fail("materializer.input-bound", "/source", "input exceeds the owner-bound execution budget")
    if (
        len(source.payload_bytes) > _MAX_PAYLOAD_BYTES
        or len(source.payload_bytes) > binding.bounds.max_payload_bytes
    ):
        _fail("materializer.payload-bound", "/payload", "payload exceeds the bounded recipe profile")
    if ev["payload_object_descriptor_id"] != payload_descriptor.id:
        _fail("materializer.payload-bound", "/payload", "payload is unbound or exceeds the trial budget")
    payload_value = _verify_blob(payload_descriptor, source.payload_bytes, "/payload")
    if payload_value["representation"] != "canonical-json-value" or payload_value["canonical_item_count"] != 1:
        _fail("materializer.payload-contract", "/payload_descriptor", "payload must be one canonical JSON value")
    try:
        parse_canonical_json(source.payload_bytes)
    except CanonicalJsonError as exc:
        raise MaterializationError("materializer.payload-canonical", "/payload", "payload is not canonical JSON") from exc
    recipe_value = recipe.to_dict()
    step = next((item for item in recipe_value["derivation_steps"] if item["derivation_step_id"] == registration.derivation_step_id), None)
    output = next((item for item in recipe_value["output_contracts"] if item["output_contract_key"] == registration.output_contract_key), None)
    step_output = None if step is None else next((item for item in step["output_contracts"] if item["output_contract_key"] == registration.output_contract_key), None)
    evidence_contracts = [] if step is None else [item for item in step["input_contracts"] if item["input_mode"] == "evidence" and ev["evidence_kind"] in item["evidence_kind_ids"]]
    if (
        step is None
        or step["implementation_id"] != registration.implementation_id
        or output is None
        or step_output is None
        or len(evidence_contracts) != 1
        or len(output["subject_contracts"]) != 1
        or (
            binding.capability_id == BUILTIN_SYNTHETIC_BEFORE_CAPABILITY_ID
            and not {"node", "edge"}.issubset(
                step_output["output_record_kinds"]
            )
        )
        or (
            binding.capability_id in _WORLDGEN_VALUE_KINDS
            and "node" not in step_output["output_record_kinds"]
        )
    ):
        _fail("materializer.recipe-profile", "/recipe", "recipe does not satisfy the bounded relation profile")
    request = RecipeExecutionInput(binding.execution_binding_id, recipe.id, registration.implementation_id, registration.derivation_step_id, evidence.id, ev["evidence_kind"], ev["context_ref_id"], bytes(source.payload_bytes))
    qualification = qualify_recipe_execution(registration, request)
    semantics = _fresh_worker_call(binding, request, "execution")
    if semantics != qualification.semantic_output:
        _fail("materializer.nondeterministic-output", "/implementation", "execution differs from the qualified fresh-worker result")
    subject_contract = output["subject_contracts"][0]
    if (
        subject_contract["subject_identity_schema_object_descriptor_id"]
        == output["graph_record_schema_object_descriptor_id"]
    ):
        _fail(
            "materializer.subject-schema",
            "/recipe/output_contracts",
            "subject identity and graph-record schemas must be distinct",
        )
    header = {"kind": "graph-record", "format": "workbench-crucible-graph-record-v2", "schema_version": 2, "schema_id": "workbench://schemas/crucible/crucible-graph-record-v2.schema.json", "canonicalizer": CANONICALIZER_ID}
    common = {"authority_owner": recipe_value["authority_owner"], "graph_family_id": output["graph_family_id"], "category_id": output["category_id"], "resolution_id": output["resolution_id"], "context_ref_id": ev["context_ref_id"], "recipe_id": recipe.id, "derivation_step_id": registration.derivation_step_id, "evidence_inputs": [{"input_contract_key": evidence_contracts[0]["input_contract_key"], "evidence_record_id": evidence.id}], "qualifiers_object_descriptor_id": None, "evidence_state": ev["evidence_state"], "limitations": []}
    if type(semantics) is RecipeSemanticOutput:
        graph_records, object_descriptors, materialized_blobs = (
            _seal_synthetic_semantics(
                semantics,
                header=header,
                common=common,
                step=step,
                output_contract_key=registration.output_contract_key,
                subject_contract=subject_contract,
                subject_identity_schema_id=registration.subject_identity_schema_id,
            )
        )
    elif type(semantics) is _GraphSemanticOutput:
        graph_records, object_descriptors, materialized_blobs = (
            _seal_graph_semantics(
                semantics,
                header=header,
                common=common,
                step=step,
                step_output=step_output,
                output_contract_key=registration.output_contract_key,
                subject_contract=subject_contract,
                subject_identity_schema_id=registration.subject_identity_schema_id,
                evidence_record_id=evidence.id,
            )
        )
    else:
        _fail(
            "materializer.invalid-implementation-output",
            "/implementation/output",
            "worker returned an unknown semantic output profile",
        )
    recipe_owner = _authority(recipe_value["recipe_owner"])
    assembly = assemble_graph_partitions(
        graph_records,
        config=ShardKernelConfig(
            output["graph_record_schema_object_descriptor_id"],
            OwnerPolicyBinding(
                recipe_value["policies"]["total_order"], recipe_owner
            ),
            OwnerPolicyBinding(
                recipe_value["policies"]["partition"], recipe_owner
            ),
            registration.records_per_shard,
        ),
        logical_key_port=lambda record: record.to_dict()["logical_key"],
        partition_key_port=lambda record, key: semantics.partition_key,
        prior_shards=prior_shards,
    )
    return MaterializationResult(recipe.id, binding.execution_binding_id, qualification.qualification_root, registry_snapshot.registry_root, registration.implementation_id, registration.derivation_step_id, registration.output_contract_key, ev["context_ref_id"], revision.id, evidence.id, admission.id, _authority(recipe_value["authority_owner"]), recipe_owner, output["graph_family_id"], output["category_id"], output["resolution_id"], recipe_value["canonical_parameter_values_object_descriptor_id"], output["graph_record_schema_object_descriptor_id"], recipe_value["policies"]["partition"], recipe_value["policies"]["total_order"], recipe_value["policies"]["dependency_footprint"], recipe_value["policies"]["invalidation"], evidence_key, evidence_ordinal, admission_key, admission_ordinal, graph_records, object_descriptors, materialized_blobs, assembly)
