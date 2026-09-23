"""Deterministic, transport-independent C01 graph-shard assembly.

This module is deliberately smaller than a recipe executor.  It accepts only
canonical, already sealed C01 graph records, asks explicit owner-supplied ports
for their logical key and partition key, and constructs exact canonical NDJSON
shards plus the partition/count/root fragments consumed by a graph revision.

There is no storage, publication, reference, job, clock, workspace, daemon, or
authority-adapter discovery behavior here.  In particular, returning an
``object-descriptor`` does not publish its bytes or attest the owner policies
named by that descriptor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import hashlib
import re
from typing import Any, TypeAlias

from workbench_api.canonical import CANONICALIZER_ID, INT64_MAX
from workbench_crucible import ReferenceExpectation, RecordValidationError, ValidatedRecord, load_canonical_record, seal_record, semantic_root_v2


GRAPH_RECORD_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-graph-record-v2.schema.json"
)
OBJECT_DESCRIPTOR_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-object-descriptor-v2.schema.json"
)

GRAPH_RECORD_KINDS = (
    "node",
    "edge",
    "property",
    "evidence-link",
    "refinement-mapping",
    "frontier",
    "conflict",
)
_GRAPH_KIND_ORDER = {kind: index for index, kind in enumerate(GRAPH_RECORD_KINDS)}
_COUNT_FIELD_BY_KIND = {
    "node": "nodes",
    "edge": "edges",
    "property": "properties",
    "evidence-link": "evidence_links",
    "refinement-mapping": "refinement_mappings",
    "frontier": "frontiers",
    "conflict": "conflicts",
}

# These ceilings are operational safety limits, not graph identity inputs.  A
# caller may choose a smaller bound for one operation, but cannot turn this
# in-memory kernel into an unbounded parser or accumulator.
_MAX_KERNEL_RECORDS = 100_000
_MAX_KERNEL_RECORD_BYTES = 64 * 1024 * 1024
_MAX_KERNEL_INPUT_BYTES = 512 * 1024 * 1024
_MAX_KERNEL_PRIOR_SHARDS = 100_000
_MAX_KERNEL_PRIOR_BYTES = 512 * 1024 * 1024
_MAX_KERNEL_OUTPUT_SHARDS = 100_000
_MAX_KERNEL_SHARD_BYTES = 128 * 1024 * 1024

_CONTENT_ID_PATTERNS = {
    "record_schema_object_descriptor_id": re.compile(
        r"object-descriptor:sha256:[0-9a-f]{64}\Z"
    ),
    "total_order_policy_id": re.compile(r"policy:sha256:[0-9a-f]{64}\Z"),
    "owner_authority_id": re.compile(r"authority:sha256:[0-9a-f]{64}\Z"),
    "owner_revision_id": re.compile(r"owner-revision:sha256:[0-9a-f]{64}\Z"),
    "authority_adapter_id": re.compile(
        r"authority-adapter:sha256:[0-9a-f]{64}\Z"
    ),
}

GraphRecordInput: TypeAlias = ValidatedRecord | bytes | bytearray | memoryview
LogicalKeyPort: TypeAlias = Callable[[ValidatedRecord], str]
PartitionKeyPort: TypeAlias = Callable[[ValidatedRecord, str], str]


class KernelValidationError(ValueError):
    """One stable, fail-closed deterministic-kernel diagnostic."""

    def __init__(self, code: str, path: str, message: str):
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code: str, path: str, message: str) -> None:
    raise KernelValidationError(code, path, message)


@dataclass(frozen=True, slots=True)
class AuthorityBinding:
    """Exact owner binding copied into every emitted shard descriptor."""

    owner_authority_id: str
    owner_revision_id: str
    authority_adapter_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "owner_authority_id": self.owner_authority_id,
            "owner_revision_id": self.owner_revision_id,
            "authority_adapter_id": self.authority_adapter_id,
        }


@dataclass(frozen=True, slots=True)
class OwnerPolicyBinding:
    """One exact policy ID and the owner binding that supplies its port."""

    policy_id: str
    authority: AuthorityBinding


@dataclass(frozen=True, slots=True)
class ShardKernelConfig:
    """Identity-bearing shard policy inputs pinned by the caller."""

    record_schema_object_descriptor_id: str
    total_order_policy: OwnerPolicyBinding
    partition_policy: OwnerPolicyBinding
    records_per_shard: int


@dataclass(frozen=True, slots=True)
class ShardKernelBounds:
    """Non-identity resource limits for one in-memory shard assembly.

    Defaults preserve the existing API while placing hard ceilings around all
    caller-controlled iteration and byte-retention paths.  Bounds affect only
    whether an operation is admitted; they never appear in descriptors,
    semantic roots, graph-revision fields, or reuse equality.
    """

    maximum_records: int = _MAX_KERNEL_RECORDS
    maximum_record_bytes: int = _MAX_KERNEL_RECORD_BYTES
    maximum_input_bytes: int = _MAX_KERNEL_INPUT_BYTES
    maximum_prior_shards: int = _MAX_KERNEL_PRIOR_SHARDS
    maximum_prior_bytes: int = _MAX_KERNEL_PRIOR_BYTES
    maximum_output_shards: int = _MAX_KERNEL_OUTPUT_SHARDS
    maximum_shard_bytes: int = _MAX_KERNEL_SHARD_BYTES


@dataclass(frozen=True, slots=True)
class ShardCoordinate:
    partition_key: str
    record_kind: str
    shard_ordinal: int


@dataclass(frozen=True, slots=True)
class GraphAssemblyScope:
    """Exact graph-revision scope shared by every non-empty input row."""

    authority_owner: AuthorityBinding
    graph_family_id: str
    category_id: str
    resolution_id: str
    context_ref_id: str
    recipe_id: str

    def graph_revision_fields(self) -> dict[str, Any]:
        return {
            "authority_owner": self.authority_owner.to_dict(),
            "graph_family_id": self.graph_family_id,
            "category_id": self.category_id,
            "resolution_id": self.resolution_id,
            "context_ref_id": self.context_ref_id,
            "recipe_id": self.recipe_id,
        }


@dataclass(frozen=True, slots=True)
class GraphPartition:
    """Exact C01 ``graphPartition`` projection."""

    coordinate: ShardCoordinate
    object_descriptor_id: str
    record_count: int
    minimum_record_key: str
    maximum_record_key: str
    semantic_root: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_key": self.coordinate.partition_key,
            "shard_ordinal": self.coordinate.shard_ordinal,
            "record_kind": self.coordinate.record_kind,
            "object_descriptor_id": self.object_descriptor_id,
            "record_count": self.record_count,
            "minimum_record_key": self.minimum_record_key,
            "maximum_record_key": self.maximum_record_key,
            "semantic_root": self.semantic_root,
        }


@dataclass(frozen=True, slots=True)
class ShardArtifact:
    """One in-memory, pre-publication canonical shard and its descriptor."""

    coordinate: ShardCoordinate
    record_ids: tuple[str, ...]
    logical_keys: tuple[str, ...]
    ndjson_bytes: bytes
    object_descriptor: ValidatedRecord
    partition: GraphPartition


@dataclass(frozen=True, slots=True)
class GraphRecordCounts:
    nodes: int
    edges: int
    properties: int
    evidence_links: int
    refinement_mappings: int
    frontiers: int
    conflicts: int

    def to_dict(self) -> dict[str, int]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "properties": self.properties,
            "evidence_links": self.evidence_links,
            "refinement_mappings": self.refinement_mappings,
            "frontiers": self.frontiers,
            "conflicts": self.conflicts,
        }


@dataclass(frozen=True, slots=True)
class GraphRecordRoots:
    nodes: str | None
    edges: str | None
    properties: str | None
    evidence_links: str | None
    refinement_mappings: str | None
    frontiers: str | None
    conflicts: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "properties": self.properties,
            "evidence_links": self.evidence_links,
            "refinement_mappings": self.refinement_mappings,
            "frontiers": self.frontiers,
            "conflicts": self.conflicts,
        }


@dataclass(frozen=True, slots=True)
class PartitionAssembly:
    """Complete deterministic output of this bounded kernel stage.

    ``reused_coordinates`` is non-identity execution metadata.  Everything in
    ``graph_revision_fields`` and every shard/descriptor byte remains exactly
    the same between a clean and equivalent incremental assembly.
    """

    scope: GraphAssemblyScope | None
    record_schema_object_descriptor_id: str
    total_order_policy: OwnerPolicyBinding
    partition_policy: OwnerPolicyBinding
    shards: tuple[ShardArtifact, ...]
    record_counts: GraphRecordCounts
    record_roots: GraphRecordRoots
    reused_coordinates: tuple[ShardCoordinate, ...]

    def graph_revision_fields(self) -> dict[str, Any]:
        """Return fresh C01 graph-revision partition/count/root fragments."""

        fields = {
            "recipe_owner": self.total_order_policy.authority.to_dict(),
            "record_schema_object_descriptor_ids": [
                self.record_schema_object_descriptor_id
            ],
            "partition_policy_id": self.partition_policy.policy_id,
            "total_order_policy_id": self.total_order_policy.policy_id,
            "partitions": [shard.partition.to_dict() for shard in self.shards],
            "record_counts": self.record_counts.to_dict(),
            "record_roots": self.record_roots.to_dict(),
        }
        if self.scope is not None:
            fields.update(self.scope.graph_revision_fields())
        return fields


@dataclass(frozen=True, slots=True)
class _PreparedRecord:
    record: ValidatedRecord
    value: dict[str, Any]
    logical_key: str
    partition_key: str
    record_kind: str


def _scope_from_record(value: dict[str, Any]) -> GraphAssemblyScope:
    authority = value["authority_owner"]
    return GraphAssemblyScope(
        authority_owner=AuthorityBinding(
            owner_authority_id=authority["owner_authority_id"],
            owner_revision_id=authority["owner_revision_id"],
            authority_adapter_id=authority["authority_adapter_id"],
        ),
        graph_family_id=value["graph_family_id"],
        category_id=value["category_id"],
        resolution_id=value["resolution_id"],
        context_ref_id=value["context_ref_id"],
        recipe_id=value["recipe_id"],
    )


def _validate_semantic_text(value: Any, path: str) -> str:
    if type(value) is not str:
        _fail("kernel.invalid-semantic-text", path, "value must be a string")
    if not value or len(value) > 8192:
        _fail(
            "kernel.invalid-semantic-text",
            path,
            "value length must be between 1 and 8192 characters",
        )
    if "\r" in value or "\n" in value or "\x00" in value:
        _fail(
            "kernel.invalid-semantic-text",
            path,
            "value must not contain carriage return, line feed, or NUL",
        )
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        _fail(
            "kernel.invalid-semantic-text",
            path,
            "value must contain only Unicode scalar values",
        )
    return value


def _snapshot_config(config: ShardKernelConfig) -> ShardKernelConfig:
    if type(config) is not ShardKernelConfig:
        _fail("kernel.invalid-config", "/config", "config must be ShardKernelConfig")
    values: dict[str, Any] = {
        "record_schema_object_descriptor_id": config.record_schema_object_descriptor_id,
    }
    for policy_name, binding in (
        ("total_order_policy", config.total_order_policy),
        ("partition_policy", config.partition_policy),
    ):
        if type(binding) is not OwnerPolicyBinding:
            _fail(
                "kernel.invalid-config",
                f"/config/{policy_name}",
                "policy must be an immutable OwnerPolicyBinding",
            )
        if type(binding.authority) is not AuthorityBinding:
            _fail(
                "kernel.invalid-config",
                f"/config/{policy_name}/authority",
                "policy authority must be an immutable AuthorityBinding",
            )
        values[f"{policy_name}/policy_id"] = binding.policy_id
        for field, value in binding.authority.to_dict().items():
            values[f"{policy_name}/authority/{field}"] = value
    for path, value in values.items():
        field = path.rsplit("/", 1)[-1]
        pattern_field = (
            "total_order_policy_id" if field == "policy_id" else field
        )
        pattern = _CONTENT_ID_PATTERNS[pattern_field]
        if type(value) is not str or pattern.fullmatch(value) is None:
            _fail(
                "kernel.invalid-config",
                f"/config/{path}",
                "value is not the required exact typed content ID",
            )
    total_authority = config.total_order_policy.authority
    partition_authority = config.partition_policy.authority
    if (
        total_authority.owner_authority_id,
        total_authority.owner_revision_id,
        total_authority.authority_adapter_id,
    ) != (
        partition_authority.owner_authority_id,
        partition_authority.owner_revision_id,
        partition_authority.authority_adapter_id,
    ):
        _fail(
            "kernel.policy-authority-mismatch",
            "/config/partition_policy/authority",
            "C01 total-order and partition policies must share one recipe owner",
        )
    if (
        type(config.records_per_shard) is not int
        or config.records_per_shard < 1
        or config.records_per_shard > INT64_MAX
    ):
        _fail(
            "kernel.invalid-config",
            "/config/records_per_shard",
            "records_per_shard must be a positive signed-64-bit integer",
        )
    total_authority_copy = AuthorityBinding(
        total_authority.owner_authority_id,
        total_authority.owner_revision_id,
        total_authority.authority_adapter_id,
    )
    partition_authority_copy = AuthorityBinding(
        partition_authority.owner_authority_id,
        partition_authority.owner_revision_id,
        partition_authority.authority_adapter_id,
    )
    return ShardKernelConfig(
        record_schema_object_descriptor_id=config.record_schema_object_descriptor_id,
        total_order_policy=OwnerPolicyBinding(
            config.total_order_policy.policy_id, total_authority_copy
        ),
        partition_policy=OwnerPolicyBinding(
            config.partition_policy.policy_id, partition_authority_copy
        ),
        records_per_shard=config.records_per_shard,
    )


def _snapshot_bounds(bounds: ShardKernelBounds | None) -> ShardKernelBounds:
    if bounds is None:
        bounds = ShardKernelBounds()
    if type(bounds) is not ShardKernelBounds:
        _fail(
            "kernel.invalid-bounds",
            "/bounds",
            "bounds must be an exact immutable ShardKernelBounds",
        )
    fields = (
        ("maximum_records", bounds.maximum_records, _MAX_KERNEL_RECORDS),
        (
            "maximum_record_bytes",
            bounds.maximum_record_bytes,
            _MAX_KERNEL_RECORD_BYTES,
        ),
        ("maximum_input_bytes", bounds.maximum_input_bytes, _MAX_KERNEL_INPUT_BYTES),
        (
            "maximum_prior_shards",
            bounds.maximum_prior_shards,
            _MAX_KERNEL_PRIOR_SHARDS,
        ),
        ("maximum_prior_bytes", bounds.maximum_prior_bytes, _MAX_KERNEL_PRIOR_BYTES),
        (
            "maximum_output_shards",
            bounds.maximum_output_shards,
            _MAX_KERNEL_OUTPUT_SHARDS,
        ),
        ("maximum_shard_bytes", bounds.maximum_shard_bytes, _MAX_KERNEL_SHARD_BYTES),
    )
    for field, value, maximum in fields:
        if type(value) is not int or not 1 <= value <= maximum:
            _fail(
                "kernel.invalid-bounds",
                f"/bounds/{field}",
                f"bound must be an exact integer between 1 and {maximum}",
            )
    return ShardKernelBounds(
        maximum_records=bounds.maximum_records,
        maximum_record_bytes=bounds.maximum_record_bytes,
        maximum_input_bytes=bounds.maximum_input_bytes,
        maximum_prior_shards=bounds.maximum_prior_shards,
        maximum_prior_bytes=bounds.maximum_prior_bytes,
        maximum_output_shards=bounds.maximum_output_shards,
        maximum_shard_bytes=bounds.maximum_shard_bytes,
    )


def _bounded_input_snapshot(
    source: Any, path: str
) -> tuple[GraphRecordInput, int]:
    """Pin mutable byte carriers and size them without parsing or copying data."""

    if type(source) is ValidatedRecord:
        raw = source.canonical_bytes
        if type(raw) is not bytes:
            _fail(
                "kernel.invalid-record",
                path,
                "ValidatedRecord canonical_bytes must be exact immutable bytes",
            )
        return source, len(raw)
    if type(source) is bytes:
        return source, len(source)
    if type(source) is bytearray:
        # A memoryview pins the bytearray against resizing between this bound
        # check and the one exact bytes snapshot made by the C01 loader.
        pinned = memoryview(source)
        return pinned, pinned.nbytes
    if type(source) is memoryview:
        # ``len(view)`` is only its first dimension; nbytes is the allocation
        # that ``bytes(view)`` will actually make.
        try:
            byte_length = source.nbytes
        except (TypeError, ValueError) as exc:
            raise KernelValidationError(
                "kernel.invalid-record-input",
                path,
                "memoryview input is not readable",
            ) from exc
        return source, byte_length
    _fail(
        "kernel.invalid-record-input",
        path,
        "input must be canonical bytes or an exact ValidatedRecord",
    )


def _bounded_prior_row_count(
    raw: bytes, *, maximum_record_bytes: int, path: str
) -> int:
    """Count prior rows while rejecting an oversized row before splitting it."""

    count = 0
    start = 0
    while start < len(raw):
        newline = raw.find(b"\n", start)
        end = len(raw) if newline < 0 else newline
        if end - start > maximum_record_bytes:
            _fail(
                "kernel.prior-record-byte-bound",
                path,
                "one prior shard row exceeds the configured record byte bound",
            )
        count += 1
        if newline < 0:
            break
        start = newline + 1
    return count


def _load_graph_record(
    source: GraphRecordInput, path: str
) -> tuple[ValidatedRecord, dict[str, Any]]:
    if type(source) is ValidatedRecord:
        if type(source.canonical_bytes) is not bytes:
            _fail(
                "kernel.invalid-record",
                path,
                "ValidatedRecord canonical_bytes must be exact immutable bytes",
            )
        try:
            record = load_canonical_record(source.canonical_bytes)
        except (RecordValidationError, TypeError) as exc:
            raise KernelValidationError(
                "kernel.invalid-record", path, "record snapshot is not canonical C01 data"
            ) from exc
    elif type(source) in (bytes, bytearray, memoryview):
        try:
            record = load_canonical_record(bytes(source))
        except (RecordValidationError, TypeError) as exc:
            raise KernelValidationError(
                "kernel.invalid-record", path, "record bytes are not exact canonical C01 data"
            ) from exc
    else:
        _fail(
            "kernel.invalid-record-input",
            path,
            "input must be canonical bytes or an exact ValidatedRecord",
        )
    value = record.to_dict()
    if record.kind != "graph-record" or record.schema_id != GRAPH_RECORD_SCHEMA_ID:
        _fail(
            "kernel.wrong-record-kind",
            path,
            "only C01 graph-record snapshots are accepted",
        )
    record_kind = value.get("body", {}).get("record_kind")
    if record_kind not in _GRAPH_KIND_ORDER:
        _fail(
            "kernel.wrong-graph-record-kind",
            f"{path}/body/record_kind",
            "graph record body kind is not a C01 graph partition kind",
        )
    return record, value


def _port_record_snapshot(record: Any) -> tuple[Any, ...]:
    if type(record) is not ValidatedRecord:
        raise TypeError("owner port record must use the exact transport type")
    fields = (record.id, record.kind, record.format, record.schema_id)
    if any(type(item) is not str for item in fields):
        raise TypeError("owner port record metadata must use exact strings")
    if type(record.canonical_bytes) is not bytes or type(record.references) is not tuple:
        raise TypeError("owner port record bytes and references must be immutable")
    references: list[tuple[Any, ...]] = []
    for reference in record.references:
        if type(reference) is not ReferenceExpectation:
            raise TypeError("owner port references must use the exact transport type")
        reference_fields = (
            reference.path,
            reference.record_id,
            reference.expected_kinds,
            reference.expected_authority_id,
            reference.expected_owner_revision_id,
            reference.expected_adapter_id,
        )
        if (
            type(reference.path) is not str
            or type(reference.record_id) is not str
            or type(reference.expected_kinds) is not tuple
            or any(type(item) is not str for item in reference.expected_kinds)
            or any(
                type(item) is not str and item is not None
                for item in reference_fields[3:]
            )
        ):
            raise TypeError("owner port reference metadata has invalid exact types")
        references.append(reference_fields)
    return (*fields, record.canonical_bytes, tuple(references))


def _isolate_port_record(
    record: ValidatedRecord, path: str
) -> tuple[ValidatedRecord, tuple[Any, ...]]:
    try:
        snapshot = _port_record_snapshot(record)
        isolated = load_canonical_record(record.canonical_bytes)
        if _port_record_snapshot(isolated) != snapshot:
            raise ValueError("record transport metadata disagrees with canonical bytes")
    except (RecordValidationError, TypeError, ValueError) as exc:
        raise KernelValidationError(
            "kernel.invalid-port-request",
            path,
            "owner port record could not be isolated",
        ) from exc
    return isolated, snapshot


def _port_record_unchanged(
    record: Any, snapshot: tuple[Any, ...]
) -> bool:
    try:
        return _port_record_snapshot(record) == snapshot
    except (TypeError, ValueError):
        return False


def _call_logical_key_port(
    port: LogicalKeyPort,
    record: ValidatedRecord,
    path: str,
) -> str:
    if not callable(port):
        _fail("kernel.invalid-key-port", "/logical_key_port", "port must be callable")
    isolated, snapshot = _isolate_port_record(record, path)
    try:
        value = port(isolated)
    except Exception as exc:
        raise KernelValidationError(
            "kernel.key-port-failed", path, "owner logical-key port failed"
        ) from exc
    if not _port_record_unchanged(isolated, snapshot):
        _fail(
            "kernel.key-port-mutation",
            path,
            "owner logical-key port mutated its isolated record request",
        )
    return _validate_semantic_text(value, path)


def _call_partition_port(
    port: PartitionKeyPort,
    record: ValidatedRecord,
    logical_key: str,
    path: str,
) -> str:
    if not callable(port):
        _fail(
            "kernel.invalid-partition-port",
            "/partition_key_port",
            "port must be callable",
        )
    isolated, snapshot = _isolate_port_record(record, path)
    try:
        value = port(isolated, logical_key)
    except Exception as exc:
        raise KernelValidationError(
            "kernel.partition-port-failed", path, "owner partition-key port failed"
        ) from exc
    if not _port_record_unchanged(isolated, snapshot):
        _fail(
            "kernel.partition-port-mutation",
            path,
            "owner partition-key port mutated its isolated record request",
        )
    return _validate_semantic_text(value, path)


def _coordinate_sort_key(coordinate: ShardCoordinate) -> tuple[bytes, int, int]:
    return (
        coordinate.partition_key.encode("utf-8"),
        _GRAPH_KIND_ORDER[coordinate.record_kind],
        coordinate.shard_ordinal,
    )


def _descriptor_for_rows(
    config: ShardKernelConfig,
    rows: tuple[_PreparedRecord, ...],
    raw: bytes,
) -> ValidatedRecord:
    digest = hashlib.sha256(raw).hexdigest()
    candidate = {
        "kind": "object-descriptor",
        "format": "workbench-crucible-object-descriptor-v2",
        "schema_version": 2,
        "schema_id": OBJECT_DESCRIPTOR_SCHEMA_ID,
        "canonicalizer": CANONICALIZER_ID,
        "object_id": f"workbench-blob-v2:sha256:{digest}",
        "sha256": digest,
        "byte_length": len(raw),
        "media_type": "application/x-ndjson",
        "representation": "canonical-ndjson-shard",
        "semantic_role": "graph-record-shard",
        "described_schema_id": GRAPH_RECORD_SCHEMA_ID,
        "described_schema_object_descriptor_id": (
            config.record_schema_object_descriptor_id
        ),
        "described_record_kind": "graph-record",
        "canonical_item_count": len(rows),
        "total_order_policy_id": config.total_order_policy.policy_id,
        "total_order_authority": config.total_order_policy.authority.to_dict(),
        "minimum_record_key": rows[0].logical_key,
        "maximum_record_key": rows[-1].logical_key,
    }
    try:
        return seal_record(candidate)
    except RecordValidationError as exc:
        raise KernelValidationError(
            "kernel.descriptor-rejected",
            "/object_descriptor",
            "C01 rejected the constructed shard descriptor",
        ) from exc


def _build_shard(
    config: ShardKernelConfig,
    partition_key: str,
    record_kind: str,
    shard_ordinal: int,
    rows: tuple[_PreparedRecord, ...],
) -> ShardArtifact:
    coordinate = ShardCoordinate(partition_key, record_kind, shard_ordinal)
    raw = b"".join(row.record.canonical_bytes + b"\n" for row in rows)
    descriptor = _descriptor_for_rows(config, rows, raw)
    record_ids = tuple(row.record.id for row in rows)
    logical_keys = tuple(row.logical_key for row in rows)
    partition = GraphPartition(
        coordinate=coordinate,
        object_descriptor_id=descriptor.id,
        record_count=len(rows),
        minimum_record_key=logical_keys[0],
        maximum_record_key=logical_keys[-1],
        semantic_root=semantic_root_v2(
            f"graph-revision/partition/{record_kind}",
            {
                "partition_key": partition_key,
                "record_ids": list(record_ids),
                "shard_ordinal": shard_ordinal,
            },
        ),
    )
    return ShardArtifact(
        coordinate=coordinate,
        record_ids=record_ids,
        logical_keys=logical_keys,
        ndjson_bytes=raw,
        object_descriptor=descriptor,
        partition=partition,
    )


def _normalize_coordinate(value: Any, path: str) -> ShardCoordinate:
    if type(value) is not ShardCoordinate:
        _fail(
            "kernel.invalid-prior-shard",
            path,
            "coordinate metadata is not an exact ShardCoordinate",
        )
    partition_key = _validate_semantic_text(
        value.partition_key, f"{path}/partition_key"
    )
    if type(value.record_kind) is not str or value.record_kind not in _GRAPH_KIND_ORDER:
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/record_kind",
            "coordinate record kind is not a C01 graph kind",
        )
    if (
        type(value.shard_ordinal) is not int
        or not 0 <= value.shard_ordinal <= INT64_MAX
    ):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/shard_ordinal",
            "shard ordinal must be a non-negative signed-64-bit integer",
        )
    return ShardCoordinate(partition_key, value.record_kind, value.shard_ordinal)


def _validate_prior_artifact(artifact: Any, path: str) -> ShardArtifact:
    """Reconstruct a safe artifact using only exact canonical bytes/primitives."""

    if type(artifact) is not ShardArtifact:
        _fail(
            "kernel.invalid-prior-shard",
            path,
            "prior entry must be an exact ShardArtifact",
        )
    coordinate = _normalize_coordinate(artifact.coordinate, f"{path}/coordinate")
    raw = artifact.ndjson_bytes
    if type(raw) is not bytes or not raw:
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/ndjson_bytes",
            "prior canonical NDJSON bytes must be non-empty immutable bytes",
        )
    if not raw.endswith(b"\n"):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/ndjson_bytes",
            "prior shard must end in exactly one row line feed",
        )
    lines = raw.split(b"\n")
    if lines[-1] != b"" or any(not line for line in lines[:-1]):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/ndjson_bytes",
            "prior shard framing contains an empty or unterminated row",
        )
    records: list[ValidatedRecord] = []
    values: list[dict[str, Any]] = []
    for index, line in enumerate(lines[:-1]):
        record, value = _load_graph_record(line, f"{path}/rows/{index}")
        records.append(record)
        values.append(value)
    record_ids = tuple(record.id for record in records)
    logical_keys = tuple(value["logical_key"] for value in values)
    if type(artifact.record_ids) is not tuple or not all(
        type(item) is str for item in artifact.record_ids
    ):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/record_ids",
            "record ID metadata must be an exact tuple of strings",
        )
    if type(artifact.logical_keys) is not tuple or not all(
        type(item) is str for item in artifact.logical_keys
    ):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/logical_keys",
            "logical-key metadata must be an exact tuple of strings",
        )
    if record_ids != artifact.record_ids or logical_keys != artifact.logical_keys:
        _fail(
            "kernel.invalid-prior-shard",
            path,
            "prior row identity metadata does not match exact NDJSON rows",
        )
    if len(record_ids) != len(set(record_ids)) or len(logical_keys) != len(
        set(logical_keys)
    ):
        _fail(
            "kernel.invalid-prior-shard",
            path,
            "prior shard contains duplicate record IDs or logical keys",
        )
    if logical_keys != tuple(
        sorted(logical_keys, key=lambda item: item.encode("utf-8"))
    ):
        _fail(
            "kernel.invalid-prior-shard",
            path,
            "prior shard keys are not in strict UTF-8 order",
        )
    if any(
        value["body"]["record_kind"] != coordinate.record_kind for value in values
    ):
        _fail(
            "kernel.invalid-prior-shard",
            path,
            "prior shard row kind disagrees with its coordinate",
        )
    descriptor_snapshot = artifact.object_descriptor
    if type(descriptor_snapshot) is not ValidatedRecord or type(
        descriptor_snapshot.canonical_bytes
    ) is not bytes:
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/object_descriptor",
            "descriptor must carry exact immutable canonical bytes",
        )
    try:
        descriptor = load_canonical_record(descriptor_snapshot.canonical_bytes)
    except (RecordValidationError, TypeError) as exc:
        raise KernelValidationError(
            "kernel.invalid-prior-shard",
            f"{path}/object_descriptor",
            "descriptor is not canonical C01 data",
        ) from exc
    if descriptor.kind != "object-descriptor":
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/object_descriptor",
            "descriptor bytes do not encode an object-descriptor record",
        )
    descriptor_value = descriptor.to_dict()
    digest = hashlib.sha256(raw).hexdigest()
    expected_descriptor_fields = {
        "object_id": f"workbench-blob-v2:sha256:{digest}",
        "sha256": digest,
        "byte_length": len(raw),
        "media_type": "application/x-ndjson",
        "representation": "canonical-ndjson-shard",
        "semantic_role": "graph-record-shard",
        "described_schema_id": GRAPH_RECORD_SCHEMA_ID,
        "described_record_kind": "graph-record",
        "canonical_item_count": len(records),
        "minimum_record_key": logical_keys[0],
        "maximum_record_key": logical_keys[-1],
    }
    if any(
        descriptor_value.get(field) != expected
        for field, expected in expected_descriptor_fields.items()
    ):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/object_descriptor",
            "descriptor does not bind the exact prior shard bytes and row metadata",
        )
    if any(
        value["schema_id"] != descriptor_value["described_schema_id"]
        for value in values
    ):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/object_descriptor/described_schema_id",
            "descriptor schema does not match every prior row",
        )
    partition_snapshot = artifact.partition
    if type(partition_snapshot) is not GraphPartition:
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/partition",
            "partition metadata must be an exact GraphPartition",
        )
    partition_coordinate = _normalize_coordinate(
        partition_snapshot.coordinate, f"{path}/partition/coordinate"
    )
    if (
        partition_coordinate.partition_key != coordinate.partition_key
        or partition_coordinate.record_kind != coordinate.record_kind
        or partition_coordinate.shard_ordinal != coordinate.shard_ordinal
    ):
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/partition/coordinate",
            "partition metadata has a different coordinate",
        )
    primitive_partition_fields = (
        ("object_descriptor_id", partition_snapshot.object_descriptor_id, str),
        ("record_count", partition_snapshot.record_count, int),
        ("minimum_record_key", partition_snapshot.minimum_record_key, str),
        ("maximum_record_key", partition_snapshot.maximum_record_key, str),
        ("semantic_root", partition_snapshot.semantic_root, str),
    )
    for field, value, expected_type in primitive_partition_fields:
        if type(value) is not expected_type:
            _fail(
                "kernel.invalid-prior-shard",
                f"{path}/partition/{field}",
                "partition field is not an exact primitive value",
            )
    expected_partition = GraphPartition(
        coordinate=coordinate,
        object_descriptor_id=descriptor.id,
        record_count=len(records),
        minimum_record_key=logical_keys[0],
        maximum_record_key=logical_keys[-1],
        semantic_root=semantic_root_v2(
            f"graph-revision/partition/{coordinate.record_kind}",
            {
                "partition_key": coordinate.partition_key,
                "record_ids": list(record_ids),
                "shard_ordinal": coordinate.shard_ordinal,
            },
        ),
    )
    actual_partition_values = (
        partition_snapshot.object_descriptor_id,
        partition_snapshot.record_count,
        partition_snapshot.minimum_record_key,
        partition_snapshot.maximum_record_key,
        partition_snapshot.semantic_root,
    )
    expected_partition_values = (
        expected_partition.object_descriptor_id,
        expected_partition.record_count,
        expected_partition.minimum_record_key,
        expected_partition.maximum_record_key,
        expected_partition.semantic_root,
    )
    if actual_partition_values != expected_partition_values:
        _fail(
            "kernel.invalid-prior-shard",
            f"{path}/partition",
            "partition metadata does not match exact prior shard membership",
        )
    return ShardArtifact(
        coordinate=coordinate,
        record_ids=record_ids,
        logical_keys=logical_keys,
        ndjson_bytes=raw,
        object_descriptor=descriptor,
        partition=expected_partition,
    )


def _counts_and_roots(
    shards: tuple[ShardArtifact, ...],
) -> tuple[GraphRecordCounts, GraphRecordRoots]:
    ids_by_kind: dict[str, list[str]] = {kind: [] for kind in GRAPH_RECORD_KINDS}
    for shard in shards:
        ids_by_kind[shard.coordinate.record_kind].extend(shard.record_ids)
    counts_by_field = {
        _COUNT_FIELD_BY_KIND[kind]: len(ids) for kind, ids in ids_by_kind.items()
    }
    roots_by_field = {
        _COUNT_FIELD_BY_KIND[kind]: (
            None
            if not ids
            else semantic_root_v2(f"graph-revision/records/{kind}", ids)
        )
        for kind, ids in ids_by_kind.items()
    }
    return GraphRecordCounts(**counts_by_field), GraphRecordRoots(**roots_by_field)


def _artifact_identity(artifact: ShardArtifact) -> tuple[Any, ...]:
    """Return only normalized built-in primitives used for reuse equality."""

    coordinate = artifact.coordinate
    partition = artifact.partition
    return (
        coordinate.partition_key,
        coordinate.record_kind,
        coordinate.shard_ordinal,
        artifact.record_ids,
        artifact.logical_keys,
        artifact.ndjson_bytes,
        artifact.object_descriptor.canonical_bytes,
        partition.object_descriptor_id,
        partition.record_count,
        partition.minimum_record_key,
        partition.maximum_record_key,
        partition.semantic_root,
    )


class GraphShardAssembler:
    """Atomic-batch accumulator for one deterministic partition assembly.

    The supplied ports are invoked once for each accepted input record.  They
    must therefore be pinned, deterministic, side-effect-free owner adapter
    functions.  This kernel validates their result shapes and agreement with
    the sealed record but does not authenticate the adapters themselves.
    """

    def __init__(
        self,
        *,
        config: ShardKernelConfig,
        logical_key_port: LogicalKeyPort,
        partition_key_port: PartitionKeyPort,
        bounds: ShardKernelBounds | None = None,
    ):
        normalized_config = _snapshot_config(config)
        normalized_bounds = _snapshot_bounds(bounds)
        if not callable(logical_key_port):
            _fail("kernel.invalid-key-port", "/logical_key_port", "port must be callable")
        if not callable(partition_key_port):
            _fail(
                "kernel.invalid-partition-port",
                "/partition_key_port",
                "port must be callable",
            )
        self._config = normalized_config
        self._bounds = normalized_bounds
        self._logical_key_port = logical_key_port
        self._partition_key_port = partition_key_port
        self._records_by_id: dict[str, _PreparedRecord] = {}
        self._records_by_key: dict[str, _PreparedRecord] = {}
        self._retained_input_bytes = 0
        self._scope: GraphAssemblyScope | None = None
        self._finished = False

    def add_batch(self, records: Iterable[GraphRecordInput]) -> None:
        """Validate and atomically add one arbitrary input batch."""

        if self._finished:
            _fail(
                "kernel.assembler-finished",
                "/records",
                "records cannot be added after successful assembly",
            )
        try:
            iterator = iter(records)
        except Exception as exc:
            raise KernelValidationError(
                "kernel.invalid-batch", "/records", "record batch is not iterable"
            ) from exc
        pending: list[_PreparedRecord] = []
        pending_input_bytes = 0
        index = 0
        while True:
            try:
                source = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                raise KernelValidationError(
                    "kernel.batch-read-failed",
                    f"/records/{index}",
                    "record batch failed while being read",
                ) from exc
            path = f"/records/{index}"
            if len(self._records_by_id) + len(pending) + 1 > self._bounds.maximum_records:
                _fail(
                    "kernel.record-bound",
                    path,
                    "graph records exceed the configured assembly count bound",
                )
            bounded_source, byte_length = _bounded_input_snapshot(source, path)
            if byte_length > self._bounds.maximum_record_bytes:
                _fail(
                    "kernel.record-byte-bound",
                    path,
                    "graph record exceeds the configured per-record byte bound",
                )
            if (
                self._retained_input_bytes + pending_input_bytes + byte_length
                > self._bounds.maximum_input_bytes
            ):
                _fail(
                    "kernel.input-byte-bound",
                    path,
                    "graph records exceed the configured aggregate input byte bound",
                )
            record, value = _load_graph_record(bounded_source, path)
            logical_key = _call_logical_key_port(
                self._logical_key_port, record, f"{path}/logical_key"
            )
            if logical_key != value["logical_key"]:
                _fail(
                    "kernel.logical-key-disagreement",
                    f"{path}/logical_key",
                    "owner key port must equal the sealed graph-record logical_key",
                )
            partition_key = _call_partition_port(
                self._partition_key_port,
                record,
                logical_key,
                f"{path}/partition_key",
            )
            pending.append(
                _PreparedRecord(
                    record=record,
                    value=value,
                    logical_key=logical_key,
                    partition_key=partition_key,
                    record_kind=value["body"]["record_kind"],
                )
            )
            pending_input_bytes += byte_length
            index += 1

        pending_ids: set[str] = set()
        pending_keys: set[str] = set()
        expected_scope = self._scope
        for index, prepared in enumerate(pending):
            if prepared.record.id in self._records_by_id or prepared.record.id in pending_ids:
                _fail(
                    "kernel.duplicate-record-id",
                    f"/records/{index}/id",
                    "a graph record ID may occur only once in an assembly",
                )
            if (
                prepared.logical_key in self._records_by_key
                or prepared.logical_key in pending_keys
            ):
                _fail(
                    "kernel.logical-key-collision",
                    f"/records/{index}/logical_key",
                    "a graph logical key may occur only once across all partitions",
                )
            pending_ids.add(prepared.record.id)
            pending_keys.add(prepared.logical_key)
            candidate_scope = _scope_from_record(prepared.value)
            if expected_scope is None:
                expected_scope = candidate_scope
            elif candidate_scope != expected_scope:
                _fail(
                    "kernel.graph-scope-mismatch",
                    f"/records/{index}",
                    "all rows must share owner, family, category, resolution, context, and recipe",
                )

        for prepared in pending:
            self._records_by_id[prepared.record.id] = prepared
            self._records_by_key[prepared.logical_key] = prepared
        self._retained_input_bytes += pending_input_bytes
        self._scope = expected_scope

    def finish(
        self,
        *,
        prior_shards: Iterable[ShardArtifact] = (),
    ) -> PartitionAssembly:
        """Assemble exact shards and reuse only byte-identical prior artifacts."""

        if self._finished:
            _fail(
                "kernel.assembler-finished",
                "/prior_shards",
                "an assembler can finish only once",
            )
        try:
            prior_iterator = iter(prior_shards)
        except Exception as exc:
            raise KernelValidationError(
                "kernel.invalid-prior-shards",
                "/prior_shards",
                "prior shards are not iterable",
            ) from exc
        prior_by_coordinate: dict[ShardCoordinate, ShardArtifact] = {}
        prior_bytes = 0
        prior_records = 0
        index = 0
        while True:
            try:
                candidate = next(prior_iterator)
            except StopIteration:
                break
            except Exception as exc:
                raise KernelValidationError(
                    "kernel.prior-shard-read-failed",
                    f"/prior_shards/{index}",
                    "prior shard iterable failed while being read",
                ) from exc
            if index + 1 > self._bounds.maximum_prior_shards:
                _fail(
                    "kernel.prior-shard-bound",
                    f"/prior_shards/{index}",
                    "prior shard hints exceed the configured count bound",
                )
            if type(candidate) is not ShardArtifact:
                _fail(
                    "kernel.invalid-prior-shard",
                    f"/prior_shards/{index}",
                    "prior entry must be an exact ShardArtifact",
                )
            if type(candidate.ndjson_bytes) is not bytes:
                _fail(
                    "kernel.invalid-prior-shard",
                    f"/prior_shards/{index}/ndjson_bytes",
                    "prior canonical NDJSON bytes must be non-empty immutable bytes",
                )
            descriptor = candidate.object_descriptor
            if (
                type(descriptor) is not ValidatedRecord
                or type(descriptor.canonical_bytes) is not bytes
            ):
                _fail(
                    "kernel.invalid-prior-shard",
                    f"/prior_shards/{index}/object_descriptor",
                    "descriptor must carry exact immutable canonical bytes",
                )
            if len(descriptor.canonical_bytes) > self._bounds.maximum_record_bytes:
                _fail(
                    "kernel.prior-record-byte-bound",
                    f"/prior_shards/{index}/object_descriptor",
                    "prior descriptor exceeds the configured record byte bound",
                )
            if len(candidate.ndjson_bytes) > self._bounds.maximum_shard_bytes:
                _fail(
                    "kernel.prior-shard-byte-bound",
                    f"/prior_shards/{index}",
                    "one prior shard hint exceeds the configured shard byte bound",
                )
            candidate_bytes = len(candidate.ndjson_bytes) + len(
                descriptor.canonical_bytes
            )
            if prior_bytes + candidate_bytes > self._bounds.maximum_prior_bytes:
                _fail(
                    "kernel.prior-byte-bound",
                    f"/prior_shards/{index}",
                    "prior shard hints exceed the configured aggregate byte bound",
                )
            row_count = _bounded_prior_row_count(
                candidate.ndjson_bytes,
                maximum_record_bytes=self._bounds.maximum_record_bytes,
                path=f"/prior_shards/{index}/ndjson_bytes",
            )
            if prior_records + row_count > self._bounds.maximum_records:
                _fail(
                    "kernel.prior-record-bound",
                    f"/prior_shards/{index}/ndjson_bytes",
                    "prior shard rows exceed the configured record count bound",
                )
            if (
                type(candidate.record_ids) is not tuple
                or len(candidate.record_ids) != row_count
                or type(candidate.logical_keys) is not tuple
                or len(candidate.logical_keys) != row_count
            ):
                _fail(
                    "kernel.invalid-prior-shard",
                    f"/prior_shards/{index}",
                    "prior row metadata must be exact tuples matching the bounded row count",
                )
            artifact = _validate_prior_artifact(candidate, f"/prior_shards/{index}")
            if artifact.coordinate in prior_by_coordinate:
                _fail(
                    "kernel.duplicate-prior-coordinate",
                    f"/prior_shards/{index}/coordinate",
                    "prior shard coordinates must be unique",
                )
            prior_by_coordinate[artifact.coordinate] = artifact
            prior_bytes += candidate_bytes
            prior_records += row_count
            index += 1

        groups: dict[tuple[str, str], list[_PreparedRecord]] = {}
        for prepared in self._records_by_id.values():
            groups.setdefault(
                (prepared.partition_key, prepared.record_kind), []
            ).append(prepared)
        desired: list[ShardArtifact] = []
        desired_shard_count = sum(
            (len(rows) + self._config.records_per_shard - 1)
            // self._config.records_per_shard
            for rows in groups.values()
        )
        if desired_shard_count > self._bounds.maximum_output_shards:
            _fail(
                "kernel.output-shard-bound",
                "/records",
                "assembly would exceed the configured output shard count bound",
            )
        for partition_key, record_kind in sorted(
            groups,
            key=lambda item: (
                item[0].encode("utf-8"),
                _GRAPH_KIND_ORDER[item[1]],
            ),
        ):
            rows = sorted(
                groups[(partition_key, record_kind)],
                key=lambda item: item.logical_key.encode("utf-8"),
            )
            width = self._config.records_per_shard
            for ordinal, start in enumerate(range(0, len(rows), width)):
                shard_rows = tuple(rows[start : start + width])
                shard_bytes = sum(
                    len(row.record.canonical_bytes) + 1 for row in shard_rows
                )
                if shard_bytes > self._bounds.maximum_shard_bytes:
                    _fail(
                        "kernel.output-shard-byte-bound",
                        "/records",
                        "one output shard would exceed the configured shard byte bound",
                    )
                desired.append(
                    _build_shard(
                        self._config,
                        partition_key,
                        record_kind,
                        ordinal,
                        shard_rows,
                    )
                )

        desired.sort(key=lambda artifact: _coordinate_sort_key(artifact.coordinate))
        final_shards: list[ShardArtifact] = []
        reused: list[ShardCoordinate] = []
        for artifact in desired:
            prior = prior_by_coordinate.get(artifact.coordinate)
            if prior is not None and _artifact_identity(prior) == _artifact_identity(
                artifact
            ):
                final_shards.append(prior)
                reused.append(artifact.coordinate)
            else:
                final_shards.append(artifact)
        shards = tuple(final_shards)
        counts, roots = _counts_and_roots(shards)
        self._finished = True
        return PartitionAssembly(
            scope=self._scope,
            record_schema_object_descriptor_id=(
                self._config.record_schema_object_descriptor_id
            ),
            total_order_policy=self._config.total_order_policy,
            partition_policy=self._config.partition_policy,
            shards=shards,
            record_counts=counts,
            record_roots=roots,
            reused_coordinates=tuple(reused),
        )


def assemble_graph_partitions(
    records: Iterable[GraphRecordInput],
    *,
    config: ShardKernelConfig,
    logical_key_port: LogicalKeyPort,
    partition_key_port: PartitionKeyPort,
    prior_shards: Iterable[ShardArtifact] = (),
    bounds: ShardKernelBounds | None = None,
) -> PartitionAssembly:
    """One-shot convenience wrapper over :class:`GraphShardAssembler`."""

    assembler = GraphShardAssembler(
        config=config,
        logical_key_port=logical_key_port,
        partition_key_port=partition_key_port,
        bounds=bounds,
    )
    assembler.add_batch(records)
    return assembler.finish(prior_shards=prior_shards)


def assemble_incremental_graph_partitions(
    records: Iterable[GraphRecordInput],
    *,
    impacted_partition_groups: Iterable[tuple[str, str]],
    prior_assembly: PartitionAssembly,
    config: ShardKernelConfig,
    logical_key_port: LogicalKeyPort,
    partition_key_port: PartitionKeyPort,
    bounds: ShardKernelBounds | None = None,
) -> PartitionAssembly:
    """Rebuild only impacted partition groups and retain all others verbatim.

    A partition group is the owner-declared ``(partition_key, record_kind)``
    pair.  All shard ordinals in an impacted group are replaced because an
    insertion or retraction may shift later ordinal boundaries.  Every prior
    shard is fully reopened and validated before it can be retained.  Records
    supplied by the caller must belong to an impacted group; this prevents an
    allegedly incremental request from silently rebuilding unrelated output.

    This function deliberately does not decide impact.  Its caller must derive
    the group set from exact old/new inputs and must compare the result with an
    independent clean build before publication.
    """

    checked_config = _snapshot_config(config)
    checked_bounds = _snapshot_bounds(bounds)
    if type(prior_assembly) is not PartitionAssembly:
        _fail(
            "kernel.incremental-invalid-prior",
            "/prior_assembly",
            "prior assembly must be an exact PartitionAssembly",
        )
    if (
        prior_assembly.record_schema_object_descriptor_id
        != checked_config.record_schema_object_descriptor_id
        or prior_assembly.total_order_policy != checked_config.total_order_policy
        or prior_assembly.partition_policy != checked_config.partition_policy
    ):
        _fail(
            "kernel.incremental-config-drift",
            "/prior_assembly",
            "prior assembly uses another schema or owner policy binding",
        )

    try:
        group_iterator = iter(impacted_partition_groups)
    except Exception as exc:
        raise KernelValidationError(
            "kernel.incremental-invalid-impact",
            "/impacted_partition_groups",
            "impacted partition groups are not iterable",
        ) from exc
    impacted: set[tuple[str, str]] = set()
    for index, item in enumerate(group_iterator):
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not str
        ):
            _fail(
                "kernel.incremental-invalid-impact",
                f"/impacted_partition_groups/{index}",
                "one impacted group must be an exact string pair",
            )
        partition_key = _validate_semantic_text(
            item[0], f"/impacted_partition_groups/{index}/partition_key"
        )
        if item[1] not in _GRAPH_KIND_ORDER:
            _fail(
                "kernel.incremental-invalid-impact",
                f"/impacted_partition_groups/{index}/record_kind",
                "impacted group names an unknown graph record kind",
            )
        group = (partition_key, item[1])
        if group in impacted:
            _fail(
                "kernel.incremental-duplicate-impact",
                f"/impacted_partition_groups/{index}",
                "impacted partition groups must be unique",
            )
        impacted.add(group)
        if len(impacted) > checked_bounds.maximum_output_shards:
            _fail(
                "kernel.incremental-impact-bound",
                "/impacted_partition_groups",
                "impacted partition groups exceed the output bound",
            )

    prior_shards: list[ShardArtifact] = []
    prior_bytes = 0
    prior_records = 0
    prior_scope: GraphAssemblyScope | None = None
    prior_coordinates: set[ShardCoordinate] = set()
    for index, candidate in enumerate(prior_assembly.shards):
        if index >= checked_bounds.maximum_prior_shards:
            _fail(
                "kernel.prior-shard-bound",
                "/prior_assembly/shards",
                "prior shard count exceeds the configured bound",
            )
        artifact = _validate_prior_artifact(
            candidate, f"/prior_assembly/shards/{index}"
        )
        if artifact.coordinate in prior_coordinates:
            _fail(
                "kernel.duplicate-prior-coordinate",
                f"/prior_assembly/shards/{index}/coordinate",
                "prior shard coordinates must be unique",
            )
        prior_coordinates.add(artifact.coordinate)
        prior_bytes += len(artifact.ndjson_bytes) + len(
            artifact.object_descriptor.canonical_bytes
        )
        prior_records += len(artifact.record_ids)
        if prior_bytes > checked_bounds.maximum_prior_bytes:
            _fail(
                "kernel.prior-byte-bound",
                "/prior_assembly/shards",
                "prior shard bytes exceed the configured bound",
            )
        if prior_records > checked_bounds.maximum_records:
            _fail(
                "kernel.prior-record-bound",
                "/prior_assembly/shards",
                "prior shard records exceed the configured bound",
            )
        first_line = artifact.ndjson_bytes.split(b"\n", 1)[0]
        _, first_value = _load_graph_record(
            first_line, f"/prior_assembly/shards/{index}/rows/0"
        )
        artifact_scope = _scope_from_record(first_value)
        if prior_scope is None:
            prior_scope = artifact_scope
        elif artifact_scope != prior_scope:
            _fail(
                "kernel.graph-scope-mismatch",
                f"/prior_assembly/shards/{index}",
                "prior shards do not share one exact graph scope",
            )
        prior_shards.append(artifact)
    if prior_scope != prior_assembly.scope:
        _fail(
            "kernel.incremental-prior-scope",
            "/prior_assembly/scope",
            "prior assembly scope differs from its exact shard rows",
        )

    def checked_partition_port(record: ValidatedRecord, logical_key: str) -> str:
        partition_key = _call_partition_port(
            partition_key_port,
            record,
            logical_key,
            "/records/partition_key",
        )
        record_kind = record.to_dict()["body"]["record_kind"]
        if (partition_key, record_kind) not in impacted:
            _fail(
                "kernel.incremental-record-outside-impact",
                "/records",
                "incremental input contains a record outside impacted groups",
            )
        return partition_key

    partial = assemble_graph_partitions(
        records,
        config=checked_config,
        logical_key_port=logical_key_port,
        partition_key_port=checked_partition_port,
        bounds=checked_bounds,
    )
    retains_prior_scope = any(
        (artifact.coordinate.partition_key, artifact.coordinate.record_kind)
        not in impacted
        for artifact in prior_shards
    )
    if (
        partial.scope is not None
        and prior_scope is not None
        and retains_prior_scope
        and partial.scope != prior_scope
    ):
        _fail(
            "kernel.graph-scope-mismatch",
            "/records",
            "incremental rows differ from the prior graph scope",
        )

    retained = [
        artifact
        for artifact in prior_shards
        if (artifact.coordinate.partition_key, artifact.coordinate.record_kind)
        not in impacted
    ]
    combined = retained + list(partial.shards)
    combined.sort(key=lambda artifact: _coordinate_sort_key(artifact.coordinate))
    if len(combined) > checked_bounds.maximum_output_shards:
        _fail(
            "kernel.output-shard-bound",
            "/records",
            "incremental assembly exceeds the output shard bound",
        )
    seen_record_ids: set[str] = set()
    seen_logical_keys: set[str] = set()
    for index, artifact in enumerate(combined):
        if any(record_id in seen_record_ids for record_id in artifact.record_ids):
            _fail(
                "kernel.duplicate-record-id",
                f"/result/shards/{index}",
                "incremental assembly contains a duplicate graph record",
            )
        if any(key in seen_logical_keys for key in artifact.logical_keys):
            _fail(
                "kernel.logical-key-collision",
                f"/result/shards/{index}",
                "incremental assembly contains a duplicate logical key",
            )
        seen_record_ids.update(artifact.record_ids)
        seen_logical_keys.update(artifact.logical_keys)
    if len(seen_record_ids) > checked_bounds.maximum_records:
        _fail(
            "kernel.output-record-bound",
            "/result/shards",
            "incremental assembly exceeds the output record bound",
        )
    shards = tuple(combined)
    counts, roots = _counts_and_roots(shards)
    return PartitionAssembly(
        scope=partial.scope if partial.scope is not None else prior_scope,
        record_schema_object_descriptor_id=(
            checked_config.record_schema_object_descriptor_id
        ),
        total_order_policy=checked_config.total_order_policy,
        partition_policy=checked_config.partition_policy,
        shards=shards,
        record_counts=counts,
        record_roots=roots,
        reused_coordinates=tuple(artifact.coordinate for artifact in retained),
    )


__all__ = [
    "AuthorityBinding",
    "GRAPH_RECORD_KINDS",
    "GRAPH_RECORD_SCHEMA_ID",
    "GraphAssemblyScope",
    "GraphPartition",
    "GraphRecordCounts",
    "GraphRecordInput",
    "GraphRecordRoots",
    "GraphShardAssembler",
    "KernelValidationError",
    "LogicalKeyPort",
    "OBJECT_DESCRIPTOR_SCHEMA_ID",
    "OwnerPolicyBinding",
    "PartitionAssembly",
    "PartitionKeyPort",
    "ShardArtifact",
    "ShardCoordinate",
    "ShardKernelBounds",
    "ShardKernelConfig",
    "assemble_graph_partitions",
    "assemble_incremental_graph_partitions",
]
