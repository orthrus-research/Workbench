"""Deterministic dependency-impact planning over immutable C01 manifests.

This module is a pure calculation boundary. It accepts one exact canonical
``graph-set-revision`` closure, each member graph revision with its exact
``dependency-manifest``, and an immutable explicit change set. It never reads
a store, follows a moving reference, discovers an authority adapter, or
publishes its result.

The planner is deliberately conservative.  Every declared change must either
match a dependency footprint (or an exact output partition supplied as a graph
change) or the result widens to a full rebuild.  Transitive propagation uses
only graph revisions and physical partitions explicitly present in C01
manifests; record dependencies on an affected graph are widened to their whole
declared downstream footprint.

Canonical loading proves record identity and this kernel proves graph-set
membership plus graph/manifest/output agreement. Source-record membership in
the declared source shard is a C01 relational-validation responsibility because
those source rows are deliberately not an input here. A plan is mechanics, not
an authority decision or publication permission; the application must supply a
previously relationally validated closure and revalidate it at commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import hashlib
import re
from typing import Any

from workbench_api.canonical import INT64_MAX, canonical_json_bytes
from workbench_crucible import RecordValidationError, load_canonical_record

from .kernel import GRAPH_RECORD_KINDS, KernelValidationError


_DEPENDENCY_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-dependency-manifest-v2.schema.json"
)
_GRAPH_REVISION_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-graph-revision-v2.schema.json"
)
_GRAPH_SET_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-graph-set-revision-v2.schema.json"
)
_CONTEXT_ID = re.compile(r"context-ref:sha256:[0-9a-f]{64}\Z")
_GRAPH_ID = re.compile(r"graph-revision:sha256:[0-9a-f]{64}\Z")
_EVIDENCE_SET_ID = re.compile(r"evidence-set-revision:sha256:[0-9a-f]{64}\Z")
_EVIDENCE_ID = re.compile(r"evidence-record:sha256:[0-9a-f]{64}\Z")
_ADMISSION_ID = re.compile(r"admission-record:sha256:[0-9a-f]{64}\Z")
_GRAPH_RECORD_ID = re.compile(r"graph-record:sha256:[0-9a-f]{64}\Z")
_COMPONENT_ID = {
    "object-descriptor": re.compile(r"object-descriptor:sha256:[0-9a-f]{64}\Z"),
    "implementation": re.compile(r"implementation:sha256:[0-9a-f]{64}\Z"),
    "policy": re.compile(r"policy:sha256:[0-9a-f]{64}\Z"),
    "ontology": re.compile(r"ontology:sha256:[0-9a-f]{64}\Z"),
    "profile-revision": re.compile(r"profile-revision:sha256:[0-9a-f]{64}\Z"),
    "profile-adapter": re.compile(r"profile-adapter:sha256:[0-9a-f]{64}\Z"),
    "tool": re.compile(r"tool:sha256:[0-9a-f]{64}\Z"),
    "component": re.compile(r"component:sha256:[0-9a-f]{64}\Z"),
}
_KIND_ORDER = {kind: index for index, kind in enumerate(GRAPH_RECORD_KINDS)}


def _fail(code: str, path: str, message: str) -> None:
    raise KernelValidationError(code, path, message)


@dataclass(frozen=True, slots=True)
class EvidencePartitionChange:
    evidence_set_revision_id: str
    partition_key: str
    shard_ordinal: int


@dataclass(frozen=True, slots=True)
class AdmissionPartitionChange:
    evidence_set_revision_id: str
    partition_key: str
    shard_ordinal: int


@dataclass(frozen=True, slots=True)
class SourceGraphRecordChange:
    graph_input_contract_key: str
    graph_revision_id: str
    graph_record_id: str


@dataclass(frozen=True, slots=True)
class SourceGraphPartitionChange:
    graph_input_contract_key: str
    graph_revision_id: str
    partition_key: str
    shard_ordinal: int
    record_kind: str


@dataclass(frozen=True, slots=True)
class DependencyComponentChange:
    component_kind: str
    component_id: str


@dataclass(frozen=True, slots=True)
class DependencyChangeSet:
    """Exact immutable semantic changes presented to dependency planning."""

    context_ref_id: str
    evidence_record_ids: tuple[str, ...] = ()
    admission_record_ids: tuple[str, ...] = ()
    evidence_partitions: tuple[EvidencePartitionChange, ...] = ()
    admission_partitions: tuple[AdmissionPartitionChange, ...] = ()
    source_graph_records: tuple[SourceGraphRecordChange, ...] = ()
    source_graph_partitions: tuple[SourceGraphPartitionChange, ...] = ()
    components: tuple[DependencyComponentChange, ...] = ()
    has_unknown_changes: bool = False


@dataclass(frozen=True, slots=True)
class DependencyManifestSnapshot:
    """One canonical C01 graph revision and its exact dependency manifest."""

    graph_revision_bytes: bytes
    dependency_manifest_bytes: bytes


@dataclass(frozen=True, slots=True)
class DependencyImpactClosure:
    """Exact graph-set membership plus every target graph/manifest pair.

    The caller must source these bytes from one previously C01-relationally
    validated immutable closure. This value carries identity, not permission.
    """

    graph_set_revision_bytes: bytes
    manifests: tuple[DependencyManifestSnapshot, ...]


@dataclass(frozen=True, slots=True)
class DependencyImpactBounds:
    maximum_manifests: int = 1024
    maximum_manifest_bytes: int = 64 * 1024 * 1024
    maximum_changes: int = 100_000
    maximum_footprints: int = 250_000
    maximum_output_partitions: int = 500_000
    maximum_change_projection_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImpactedGraphPartition:
    graph_revision_id: str
    partition_key: str
    shard_ordinal: int
    record_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_revision_id": self.graph_revision_id,
            "partition_key": self.partition_key,
            "record_kind": self.record_kind,
            "shard_ordinal": self.shard_ordinal,
        }


@dataclass(frozen=True, slots=True)
class DependencyImpactPlan:
    """Timing-free deterministic output of dependency-impact planning."""

    context_ref_id: str
    graph_set_revision_id: str
    mode: str
    dependency_manifest_ids: tuple[str, ...]
    impacted_partitions: tuple[ImpactedGraphPartition, ...]
    reason_codes: tuple[str, ...]
    input_closure_sha256: str
    impact_root_sha256: str

    @property
    def requires_full_rebuild(self) -> bool:
        return self.mode == "full-rebuild"

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_ref_id": self.context_ref_id,
            "graph_set_revision_id": self.graph_set_revision_id,
            "dependency_manifest_ids": list(self.dependency_manifest_ids),
            "impact_root_sha256": self.impact_root_sha256,
            "impacted_partitions": [
                partition.to_dict() for partition in self.impacted_partitions
            ],
            "input_closure_sha256": self.input_closure_sha256,
            "mode": self.mode,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class _NormalizedChangeSet:
    context_ref_id: str
    evidence_record_ids: frozenset[str]
    admission_record_ids: frozenset[str]
    evidence_partitions: frozenset[tuple[str, str, int]]
    admission_partitions: frozenset[tuple[str, str, int]]
    source_graph_records: frozenset[tuple[str, str, str]]
    source_graph_partitions: frozenset[tuple[str, str, str, int, str]]
    components: frozenset[tuple[str, str]]
    tokens: frozenset[tuple[Any, ...]]
    has_unknown_changes: bool
    projection: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Footprint:
    outputs: tuple[ImpactedGraphPartition, ...]
    evidence_record_ids: frozenset[str]
    admission_record_ids: frozenset[str]
    evidence_partitions: frozenset[tuple[str, str, int]]
    admission_partitions: frozenset[tuple[str, str, int]]
    source_graph_records: frozenset[tuple[str, str, str]]
    source_graph_partitions: frozenset[tuple[str, str, str, int, str]]
    components: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class _Manifest:
    graph_revision_id: str
    graph_raw_sha256: str
    manifest_id: str
    manifest_raw_sha256: str
    footprints: tuple[_Footprint, ...]
    outputs: tuple[ImpactedGraphPartition, ...]


@dataclass(frozen=True, slots=True)
class _NormalizedClosure:
    graph_set_revision_id: str
    graph_set_raw_sha256: str
    manifests: tuple[_Manifest, ...]


def _text(value: Any, path: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 8192
        or "\r" in value
        or "\n" in value
        or "\x00" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        _fail(
            "kernel.impact-invalid-change",
            path,
            "value must be bounded semantic text containing Unicode scalar values",
        )
    return value


def _typed_id(
    value: Any,
    pattern: re.Pattern[str],
    path: str,
    *,
    code: str = "kernel.impact-invalid-change",
) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(
            code,
            path,
            "value is not the required exact typed content ID",
        )
    return value


def _ordinal(value: Any, path: str) -> int:
    if type(value) is not int or not 0 <= value <= INT64_MAX:
        _fail(
            "kernel.impact-invalid-change",
            path,
            "shard ordinal must be a non-negative signed-64-bit integer",
        )
    return value


def _record_kind(value: Any, path: str) -> str:
    if type(value) is not str or value not in _KIND_ORDER:
        _fail(
            "kernel.impact-invalid-change",
            path,
            "record kind must be an exact C01 graph record kind",
        )
    return value


def _exact_tuple(value: Any, path: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        _fail(
            "kernel.impact-invalid-change-set",
            path,
            "change collection must be an immutable tuple",
        )
    return value


def _unique(values: list[tuple[Any, ...]], path: str) -> frozenset[tuple[Any, ...]]:
    result = frozenset(values)
    if len(result) != len(values):
        _fail(
            "kernel.impact-duplicate-change",
            path,
            "one exact change may occur only once",
        )
    return result


def _snapshot_bounds(bounds: Any) -> DependencyImpactBounds:
    if type(bounds) is not DependencyImpactBounds:
        _fail(
            "kernel.impact-invalid-bounds",
            "/bounds",
            "bounds must be an exact DependencyImpactBounds",
        )
    fields = (
        ("maximum_manifests", bounds.maximum_manifests, 1, 100_000),
        (
            "maximum_manifest_bytes",
            bounds.maximum_manifest_bytes,
            1,
            16 * 1024 * 1024 * 1024,
        ),
        ("maximum_changes", bounds.maximum_changes, 0, 10_000_000),
        ("maximum_footprints", bounds.maximum_footprints, 0, 10_000_000),
        (
            "maximum_output_partitions",
            bounds.maximum_output_partitions,
            0,
            10_000_000,
        ),
        (
            "maximum_change_projection_bytes",
            bounds.maximum_change_projection_bytes,
            1,
            16 * 1024 * 1024 * 1024,
        ),
    )
    values: list[int] = []
    for field, value, minimum, maximum in fields:
        if type(value) is not int or not minimum <= value <= maximum:
            _fail(
                "kernel.impact-invalid-bounds",
                f"/bounds/{field}",
                f"bound must be an exact integer between {minimum} and {maximum}",
            )
        values.append(value)
    return DependencyImpactBounds(*values)


def _enforce_change_projection_bound(
    *,
    context_ref_id: str,
    has_unknown_changes: bool,
    evidence_values: frozenset[str],
    admission_values: frozenset[str],
    evidence_partition_values: frozenset[tuple[str, str, int]],
    admission_partition_values: frozenset[tuple[str, str, int]],
    graph_record_values: frozenset[tuple[str, str, str]],
    graph_partition_values: frozenset[tuple[str, str, str, int, str]],
    component_values: frozenset[tuple[str, str]],
    maximum_bytes: int,
) -> None:
    """Bound exact canonical change-projection bytes without joining them.

    The empty arrays establish the fixed object/key cost. Each element is then
    encoded independently, so the largest temporary canonical allocation is
    bounded by one already validated change value instead of the whole change
    set. Array commas are counted explicitly; ordering cannot affect size.
    """

    empty_projection = {
        "admission_partitions": [],
        "admission_record_ids": [],
        "components": [],
        "context_ref_id": context_ref_id,
        "evidence_partitions": [],
        "evidence_record_ids": [],
        "has_unknown_changes": has_unknown_changes,
        "source_graph_partitions": [],
        "source_graph_records": [],
    }
    projection_bytes = len(canonical_json_bytes(empty_projection))

    def add_collection(values: Any, project: Any) -> None:
        nonlocal projection_bytes
        count = 0
        for value in values:
            if count:
                projection_bytes += 1
            projection_bytes += len(canonical_json_bytes(project(value)))
            if projection_bytes > maximum_bytes:
                _fail(
                    "kernel.impact-change-projection-byte-bound",
                    "/changes",
                    "canonical change projection exceeds the configured byte bound",
                )
            count += 1

    if projection_bytes > maximum_bytes:
        _fail(
            "kernel.impact-change-projection-byte-bound",
            "/changes",
            "canonical change projection exceeds the configured byte bound",
        )
    add_collection(admission_partition_values, lambda item: {
        "evidence_set_revision_id": item[0],
        "partition_key": item[1],
        "shard_ordinal": item[2],
    })
    add_collection(admission_values, lambda item: item)
    add_collection(component_values, lambda item: {
        "component_id": item[1],
        "component_kind": item[0],
    })
    add_collection(evidence_partition_values, lambda item: {
        "evidence_set_revision_id": item[0],
        "partition_key": item[1],
        "shard_ordinal": item[2],
    })
    add_collection(evidence_values, lambda item: item)
    add_collection(graph_partition_values, lambda item: {
        "graph_input_contract_key": item[0],
        "graph_revision_id": item[1],
        "partition_key": item[2],
        "record_kind": item[4],
        "shard_ordinal": item[3],
    })
    add_collection(graph_record_values, lambda item: {
        "graph_input_contract_key": item[0],
        "graph_record_id": item[2],
        "graph_revision_id": item[1],
    })


def _normalize_change_set(
    source: Any, bounds: DependencyImpactBounds
) -> _NormalizedChangeSet:
    if type(source) is not DependencyChangeSet:
        _fail(
            "kernel.impact-invalid-change-set",
            "/changes",
            "changes must be an exact DependencyChangeSet",
        )
    context_ref_id = _typed_id(source.context_ref_id, _CONTEXT_ID, "/changes/context_ref_id")
    if type(source.has_unknown_changes) is not bool:
        _fail(
            "kernel.impact-invalid-change-set",
            "/changes/has_unknown_changes",
            "unknown-change flag must be an exact boolean",
        )

    evidence_ids_raw = _exact_tuple(
        source.evidence_record_ids, "/changes/evidence_record_ids"
    )
    admission_ids_raw = _exact_tuple(
        source.admission_record_ids, "/changes/admission_record_ids"
    )
    evidence_partition_raw = _exact_tuple(
        source.evidence_partitions, "/changes/evidence_partitions"
    )
    admission_partition_raw = _exact_tuple(
        source.admission_partitions, "/changes/admission_partitions"
    )
    graph_record_raw = _exact_tuple(
        source.source_graph_records, "/changes/source_graph_records"
    )
    graph_partition_raw = _exact_tuple(
        source.source_graph_partitions, "/changes/source_graph_partitions"
    )
    component_raw = _exact_tuple(source.components, "/changes/components")
    if sum(
        len(items)
        for items in (
            evidence_ids_raw,
            admission_ids_raw,
            evidence_partition_raw,
            admission_partition_raw,
            graph_record_raw,
            graph_partition_raw,
            component_raw,
        )
    ) > bounds.maximum_changes:
        _fail(
            "kernel.impact-change-bound",
            "/changes",
            "change count exceeds the configured bound",
        )
    evidence_ids = [
        (
            "evidence-record",
            _typed_id(value, _EVIDENCE_ID, f"/changes/evidence_record_ids/{index}"),
        )
        for index, value in enumerate(evidence_ids_raw)
    ]
    admission_ids = [
        (
            "admission-record",
            _typed_id(value, _ADMISSION_ID, f"/changes/admission_record_ids/{index}"),
        )
        for index, value in enumerate(admission_ids_raw)
    ]

    def evidence_partition(item: Any, path: str) -> tuple[str, str, int]:
        if type(item) is not EvidencePartitionChange:
            _fail(
                "kernel.impact-invalid-change", path, "entry has the wrong exact type"
            )
        return (
            _typed_id(
                item.evidence_set_revision_id,
                _EVIDENCE_SET_ID,
                path + "/evidence_set_revision_id",
            ),
            _text(item.partition_key, path + "/partition_key"),
            _ordinal(item.shard_ordinal, path + "/shard_ordinal"),
        )

    def admission_partition(item: Any, path: str) -> tuple[str, str, int]:
        if type(item) is not AdmissionPartitionChange:
            _fail(
                "kernel.impact-invalid-change", path, "entry has the wrong exact type"
            )
        return (
            _typed_id(
                item.evidence_set_revision_id,
                _EVIDENCE_SET_ID,
                path + "/evidence_set_revision_id",
            ),
            _text(item.partition_key, path + "/partition_key"),
            _ordinal(item.shard_ordinal, path + "/shard_ordinal"),
        )

    evidence_partitions = [
        evidence_partition(item, f"/changes/evidence_partitions/{index}")
        for index, item in enumerate(evidence_partition_raw)
    ]
    admission_partitions = [
        admission_partition(item, f"/changes/admission_partitions/{index}")
        for index, item in enumerate(admission_partition_raw)
    ]

    graph_records: list[tuple[str, str, str]] = []
    for index, item in enumerate(graph_record_raw):
        path = f"/changes/source_graph_records/{index}"
        if type(item) is not SourceGraphRecordChange:
            _fail(
                "kernel.impact-invalid-change", path, "entry has the wrong exact type"
            )
        graph_records.append(
            (
                _text(item.graph_input_contract_key, path + "/graph_input_contract_key"),
                _typed_id(item.graph_revision_id, _GRAPH_ID, path + "/graph_revision_id"),
                _typed_id(item.graph_record_id, _GRAPH_RECORD_ID, path + "/graph_record_id"),
            )
        )

    graph_partitions: list[tuple[str, str, str, int, str]] = []
    for index, item in enumerate(graph_partition_raw):
        path = f"/changes/source_graph_partitions/{index}"
        if type(item) is not SourceGraphPartitionChange:
            _fail(
                "kernel.impact-invalid-change", path, "entry has the wrong exact type"
            )
        graph_partitions.append(
            (
                _text(item.graph_input_contract_key, path + "/graph_input_contract_key"),
                _typed_id(item.graph_revision_id, _GRAPH_ID, path + "/graph_revision_id"),
                _text(item.partition_key, path + "/partition_key"),
                _ordinal(item.shard_ordinal, path + "/shard_ordinal"),
                _record_kind(item.record_kind, path + "/record_kind"),
            )
        )

    components: list[tuple[str, str]] = []
    for index, item in enumerate(component_raw):
        path = f"/changes/components/{index}"
        if type(item) is not DependencyComponentChange:
            _fail(
                "kernel.impact-invalid-change", path, "entry has the wrong exact type"
            )
        if type(item.component_kind) is not str or item.component_kind not in _COMPONENT_ID:
            _fail(
                "kernel.impact-invalid-change",
                path + "/component_kind",
                "component kind is not a C01 dependency component kind",
            )
        components.append(
            (
                item.component_kind,
                _typed_id(
                    item.component_id,
                    _COMPONENT_ID[item.component_kind],
                    path + "/component_id",
                ),
            )
        )

    evidence_set = _unique(evidence_ids, "/changes/evidence_record_ids")
    admission_set = _unique(admission_ids, "/changes/admission_record_ids")
    evidence_partition_set = _unique(
        [("evidence-partition", *item) for item in evidence_partitions],
        "/changes/evidence_partitions",
    )
    admission_partition_set = _unique(
        [("admission-partition", *item) for item in admission_partitions],
        "/changes/admission_partitions",
    )
    graph_record_set = _unique(
        [("source-graph-record", *item) for item in graph_records],
        "/changes/source_graph_records",
    )
    graph_partition_set = _unique(
        [("source-graph-partition", *item) for item in graph_partitions],
        "/changes/source_graph_partitions",
    )
    component_set = _unique(
        [("component", *item) for item in components], "/changes/components"
    )
    tokens = frozenset().union(
        evidence_set,
        admission_set,
        evidence_partition_set,
        admission_partition_set,
        graph_record_set,
        graph_partition_set,
        component_set,
    )
    evidence_values = frozenset(item[1] for item in evidence_set)
    admission_values = frozenset(item[1] for item in admission_set)
    evidence_partition_values = frozenset(item[1:] for item in evidence_partition_set)
    admission_partition_values = frozenset(item[1:] for item in admission_partition_set)
    graph_record_values = frozenset(item[1:] for item in graph_record_set)
    graph_partition_values = frozenset(item[1:] for item in graph_partition_set)
    component_values = frozenset(item[1:] for item in component_set)
    _enforce_change_projection_bound(
        context_ref_id=context_ref_id,
        has_unknown_changes=source.has_unknown_changes,
        evidence_values=evidence_values,
        admission_values=admission_values,
        evidence_partition_values=evidence_partition_values,
        admission_partition_values=admission_partition_values,
        graph_record_values=graph_record_values,
        graph_partition_values=graph_partition_values,
        component_values=component_values,
        maximum_bytes=bounds.maximum_change_projection_bytes,
    )
    projection = {
        "admission_partitions": [
            {
                "evidence_set_revision_id": item[0],
                "partition_key": item[1],
                "shard_ordinal": item[2],
            }
            for item in sorted(admission_partition_values)
        ],
        "admission_record_ids": sorted(admission_values),
        "components": [
            {"component_id": item[1], "component_kind": item[0]}
            for item in sorted(component_values)
        ],
        "context_ref_id": context_ref_id,
        "evidence_partitions": [
            {
                "evidence_set_revision_id": item[0],
                "partition_key": item[1],
                "shard_ordinal": item[2],
            }
            for item in sorted(evidence_partition_values)
        ],
        "evidence_record_ids": sorted(evidence_values),
        "has_unknown_changes": source.has_unknown_changes,
        "source_graph_partitions": [
            {
                "graph_input_contract_key": item[0],
                "graph_revision_id": item[1],
                "partition_key": item[2],
                "record_kind": item[4],
                "shard_ordinal": item[3],
            }
            for item in sorted(graph_partition_values)
        ],
        "source_graph_records": [
            {
                "graph_input_contract_key": item[0],
                "graph_record_id": item[2],
                "graph_revision_id": item[1],
            }
            for item in sorted(graph_record_values)
        ],
    }
    return _NormalizedChangeSet(
        context_ref_id,
        evidence_values,
        admission_values,
        evidence_partition_values,
        admission_partition_values,
        graph_record_values,
        graph_partition_values,
        component_values,
        tokens,
        source.has_unknown_changes,
        projection,
    )


def _partition_sort_key(value: ImpactedGraphPartition) -> tuple[Any, ...]:
    return (
        value.graph_revision_id.encode("utf-8"),
        value.partition_key.encode("utf-8"),
        _KIND_ORDER[value.record_kind],
        value.shard_ordinal,
    )


def _normalize_manifests(
    closure: Any,
    *,
    context_ref_id: str,
    bounds: DependencyImpactBounds,
) -> _NormalizedClosure:
    if type(closure) is not DependencyImpactClosure:
        _fail(
            "kernel.impact-invalid-closure",
            "/closure",
            "closure must be an exact immutable DependencyImpactClosure",
        )
    if type(closure.graph_set_revision_bytes) is not bytes:
        _fail(
            "kernel.impact-invalid-closure",
            "/closure/graph_set_revision_bytes",
            "graph set must be exact immutable canonical bytes",
        )
    if len(closure.graph_set_revision_bytes) > bounds.maximum_manifest_bytes:
        _fail(
            "kernel.impact-manifest-byte-bound",
            "/closure/graph_set_revision_bytes",
            "graph set bytes exceed the configured aggregate closure bound",
        )
    try:
        graph_set_record = load_canonical_record(
            closure.graph_set_revision_bytes
        )
    except (RecordValidationError, TypeError, ValueError) as exc:
        raise KernelValidationError(
            "kernel.impact-invalid-closure",
            "/closure/graph_set_revision_bytes",
            "graph set bytes are not canonical C01 data",
        ) from exc
    if (
        graph_set_record.kind != "graph-set-revision"
        or graph_set_record.schema_id != _GRAPH_SET_SCHEMA_ID
    ):
        _fail(
            "kernel.impact-wrong-graph-set-kind",
            "/closure/graph_set_revision_bytes",
            "input must be a C01 graph-set-revision V2 record",
        )
    graph_set = graph_set_record.to_dict()
    if graph_set["context_ref_id"] != context_ref_id:
        _fail(
            "kernel.impact-context-mismatch",
            "/closure/graph_set_revision_bytes/context_ref_id",
            "graph set and changes must share one exact context",
        )
    source = closure.manifests
    if type(source) is not tuple or not source:
        _fail(
            "kernel.impact-invalid-manifests",
            "/closure/manifests",
            "manifests must be one non-empty immutable tuple",
        )
    if len(source) > bounds.maximum_manifests:
        _fail(
            "kernel.impact-manifest-bound",
            "/closure/manifests",
            "manifest count exceeds the configured bound",
        )
    consumed = len(closure.graph_set_revision_bytes)
    footprint_count = 0
    output_count = 0
    seen_graph_ids: set[str] = set()
    seen_manifest_ids: set[str] = set()
    manifests: list[_Manifest] = []
    for index, snapshot in enumerate(source):
        path = f"/closure/manifests/{index}"
        if type(snapshot) is not DependencyManifestSnapshot:
            _fail(
                "kernel.impact-invalid-manifest",
                path,
                "manifest entry must be an exact DependencyManifestSnapshot",
            )
        if type(snapshot.graph_revision_bytes) is not bytes:
            _fail(
                "kernel.impact-invalid-manifest",
                path + "/graph_revision_bytes",
                "graph revision must be exact immutable canonical bytes",
            )
        if type(snapshot.dependency_manifest_bytes) is not bytes:
            _fail(
                "kernel.impact-invalid-manifest",
                path + "/dependency_manifest_bytes",
                "manifest must be exact immutable canonical bytes",
            )
        consumed += len(snapshot.graph_revision_bytes) + len(
            snapshot.dependency_manifest_bytes
        )
        if consumed > bounds.maximum_manifest_bytes:
            _fail(
                "kernel.impact-manifest-byte-bound",
                "/closure/manifests",
                "graph and manifest bytes exceed the configured aggregate bound",
            )
        try:
            graph_record = load_canonical_record(snapshot.graph_revision_bytes)
            record = load_canonical_record(snapshot.dependency_manifest_bytes)
        except (RecordValidationError, TypeError, ValueError) as exc:
            raise KernelValidationError(
                "kernel.impact-invalid-manifest",
                path,
                "graph or manifest bytes are not canonical C01 data",
            ) from exc
        if (
            graph_record.kind != "graph-revision"
            or graph_record.schema_id != _GRAPH_REVISION_SCHEMA_ID
        ):
            _fail(
                "kernel.impact-wrong-graph-kind",
                path + "/graph_revision_bytes",
                "input must be a C01 graph-revision V2 record",
            )
        if record.kind != "dependency-manifest" or record.schema_id != _DEPENDENCY_SCHEMA_ID:
            _fail(
                "kernel.impact-wrong-manifest-kind",
                path + "/dependency_manifest_bytes",
                "input must be a C01 dependency-manifest V2 record",
            )
        graph_revision_id = graph_record.id
        graph_value = graph_record.to_dict()
        value = record.to_dict()
        if (
            graph_value["context_ref_id"] != context_ref_id
            or value["context_ref_id"] != context_ref_id
        ):
            _fail(
                "kernel.impact-context-mismatch",
                path + "/context_ref_id",
                "all graphs, manifests, and changes must share one exact context",
            )
        if graph_value["dependency_manifest_id"] != record.id:
            _fail(
                "kernel.impact-graph-manifest-mismatch",
                path + "/dependency_manifest_bytes/id",
                "graph revision does not name the supplied dependency manifest",
            )
        for field in (
            "authority_owner",
            "recipe_owner",
            "recipe_id",
            "recipe_implementation_id",
            "parameter_values_object_descriptor_id",
            "context_ref_id",
            "evidence_set_revision_ids",
            "input_graph_bindings",
        ):
            if graph_value[field] != value[field]:
                _fail(
                    "kernel.impact-graph-manifest-mismatch",
                    f"{path}/{field}",
                    "graph revision and dependency manifest bindings differ",
                )
        if graph_revision_id in seen_graph_ids:
            _fail(
                "kernel.impact-duplicate-graph",
                path + "/graph_revision_id",
                "one graph revision may have only one dependency snapshot",
            )
        if record.id in seen_manifest_ids:
            _fail(
                "kernel.impact-duplicate-manifest",
                path + "/dependency_manifest_bytes/id",
                "one dependency manifest may occur only once",
            )
        seen_graph_ids.add(graph_revision_id)
        seen_manifest_ids.add(record.id)
        bindings = {
            item["graph_input_contract_key"]: item["graph_revision_id"]
            for item in value["input_graph_bindings"]
        }
        components_by_key = {
            item["component_key"]: (item["component_kind"], item["component_id"])
            for item in value["input_components"]
        }
        footprints: list[_Footprint] = []
        all_outputs: list[ImpactedGraphPartition] = []
        for footprint_index, footprint in enumerate(value["footprints"]):
            footprint_count += 1
            if footprint_count > bounds.maximum_footprints:
                _fail(
                    "kernel.impact-footprint-bound",
                    "/closure/manifests",
                    "footprint count exceeds the configured aggregate bound",
                )
            outputs = tuple(
                sorted(
                    (
                        ImpactedGraphPartition(
                            graph_revision_id,
                            item["partition_key"],
                            item["shard_ordinal"],
                            item["record_kind"],
                        )
                        for item in footprint["output_partitions"]
                    ),
                    key=_partition_sort_key,
                )
            )
            output_count += len(outputs)
            if output_count > bounds.maximum_output_partitions:
                _fail(
                    "kernel.impact-output-bound",
                    "/closure/manifests",
                    "output partition count exceeds the configured aggregate bound",
                )
            graph_records = frozenset(
                (
                    item["graph_input_contract_key"],
                    bindings[item["graph_input_contract_key"]],
                    item["graph_record_id"],
                )
                for item in footprint["source_graph_records"]
            )
            graph_partitions = frozenset(
                (
                    item["graph_input_contract_key"],
                    item["graph_revision_id"],
                    item["partition_key"],
                    item["shard_ordinal"],
                    item["record_kind"],
                )
                for item in footprint["source_graph_partitions"]
            )
            try:
                components = frozenset(
                    components_by_key[key] for key in footprint["component_keys"]
                )
            except KeyError as exc:
                raise KernelValidationError(
                    "kernel.impact-invalid-manifest",
                    f"{path}/footprints/{footprint_index}/component_keys",
                    "footprint names an undeclared component key",
                ) from exc
            normalized = _Footprint(
                outputs=outputs,
                evidence_record_ids=frozenset(footprint["evidence_record_ids"]),
                admission_record_ids=frozenset(footprint["admission_record_ids"]),
                evidence_partitions=frozenset(
                    (
                        item["evidence_set_revision_id"],
                        item["partition_key"],
                        item["shard_ordinal"],
                    )
                    for item in footprint["evidence_partitions"]
                ),
                admission_partitions=frozenset(
                    (
                        item["evidence_set_revision_id"],
                        item["partition_key"],
                        item["shard_ordinal"],
                    )
                    for item in footprint["admission_partitions"]
                ),
                source_graph_records=graph_records,
                source_graph_partitions=graph_partitions,
                components=components,
            )
            footprints.append(normalized)
            all_outputs.extend(outputs)
        if len(set(all_outputs)) != len(all_outputs):
            _fail(
                "kernel.impact-output-collision",
                path + "/dependency_manifest_bytes/footprints",
                "one output partition may occur in only one footprint",
            )
        graph_output_coordinates = {
            (
                item["partition_key"],
                item["shard_ordinal"],
                item["record_kind"],
            )
            for item in graph_value["partitions"]
        }
        manifest_output_coordinates = {
            (
                item.partition_key,
                item.shard_ordinal,
                item.record_kind,
            )
            for item in all_outputs
        }
        if graph_output_coordinates != manifest_output_coordinates:
            _fail(
                "kernel.impact-graph-manifest-mismatch",
                path + "/dependency_manifest_bytes/footprints",
                "dependency footprints do not cover every graph output partition",
            )
        manifests.append(
            _Manifest(
                graph_revision_id,
                hashlib.sha256(snapshot.graph_revision_bytes).hexdigest(),
                record.id,
                hashlib.sha256(snapshot.dependency_manifest_bytes).hexdigest(),
                tuple(footprints),
                tuple(sorted(all_outputs, key=_partition_sort_key)),
            )
        )
    normalized = tuple(
        sorted(manifests, key=lambda item: item.graph_revision_id.encode("utf-8"))
    )
    ordered_graph_ids = [
        item["graph_revision_id"]
        for collection in ("members", "join_graphs", "refinement_graphs")
        for item in graph_set[collection]
    ]
    expected_graph_ids = set(ordered_graph_ids)
    if len(expected_graph_ids) != len(ordered_graph_ids):
        _fail(
            "kernel.impact-invalid-closure",
            "/closure/graph_set_revision_bytes",
            "one graph revision may occur only once across graph-set roles",
        )
    supplied_graph_ids = {item.graph_revision_id for item in normalized}
    if supplied_graph_ids != expected_graph_ids:
        _fail(
            "kernel.impact-incomplete-closure",
            "/closure/manifests",
            "manifest snapshots must exactly cover graph-set membership",
        )
    return _NormalizedClosure(
        graph_set_record.id,
        hashlib.sha256(closure.graph_set_revision_bytes).hexdigest(),
        normalized,
    )


def _direct_tokens(
    footprint: _Footprint, changes: _NormalizedChangeSet
) -> set[tuple[Any, ...]]:
    matched: set[tuple[Any, ...]] = set()
    matched.update(
        ("evidence-record", item)
        for item in footprint.evidence_record_ids & changes.evidence_record_ids
    )
    matched.update(
        ("admission-record", item)
        for item in footprint.admission_record_ids & changes.admission_record_ids
    )
    matched.update(
        ("evidence-partition", *item)
        for item in footprint.evidence_partitions & changes.evidence_partitions
    )
    matched.update(
        ("admission-partition", *item)
        for item in footprint.admission_partitions & changes.admission_partitions
    )
    matched.update(
        ("source-graph-record", *item)
        for item in footprint.source_graph_records & changes.source_graph_records
    )
    matched.update(
        ("source-graph-partition", *item)
        for item in footprint.source_graph_partitions
        & changes.source_graph_partitions
    )
    matched.update(
        ("component", *item)
        for item in footprint.components & changes.components
    )
    return matched


def plan_dependency_impact(
    changes: DependencyChangeSet,
    closure: DependencyImpactClosure,
    *,
    bounds: DependencyImpactBounds = DependencyImpactBounds(),
) -> DependencyImpactPlan:
    """Return a bounded deterministic impact plan over exact immutable inputs.

    An unmatched or explicitly unknown change selects ``full-rebuild`` and all
    declared output partitions.  Otherwise, ``exact`` contains the directly
    matched output footprints plus a fixed-point closure across manifest-declared
    source graph partition and record dependencies.
    """

    current_bounds = _snapshot_bounds(bounds)
    current_changes = _normalize_change_set(changes, current_bounds)
    current_closure = _normalize_manifests(
        closure,
        context_ref_id=current_changes.context_ref_id,
        bounds=current_bounds,
    )
    current_manifests = current_closure.manifests
    all_outputs = frozenset(
        output for manifest in current_manifests for output in manifest.outputs
    )
    impacted: set[ImpactedGraphPartition] = set()
    matched_tokens: set[tuple[Any, ...]] = set()

    for manifest in current_manifests:
        for footprint in manifest.footprints:
            footprint_tokens = _direct_tokens(footprint, current_changes)
            if footprint_tokens:
                matched_tokens.update(footprint_tokens)
                impacted.update(footprint.outputs)

    # Build reverse indexes once. Each output coordinate enters the queue at
    # most once and each indexed dependency is visited at most once, avoiding a
    # quadratic fixed-point scan over a reverse-ordered graph chain.
    partition_dependents: dict[
        tuple[str, str, int, str], list[_Footprint]
    ] = {}
    graph_dependents: dict[str, list[_Footprint]] = {}
    for manifest in current_manifests:
        for footprint in manifest.footprints:
            for (
                _,
                graph_id,
                partition_key,
                ordinal,
                record_kind,
            ) in footprint.source_graph_partitions:
                partition_dependents.setdefault(
                    (graph_id, partition_key, ordinal, record_kind), []
                ).append(footprint)
            # A record-level footprint does not expose the source record's
            # physical partition. Any changed partition in that exact graph
            # therefore conservatively invalidates this downstream footprint.
            for graph_id in {
                item[1] for item in footprint.source_graph_records
            }:
                graph_dependents.setdefault(graph_id, []).append(footprint)

    pending = deque(sorted(impacted, key=_partition_sort_key))
    seen_graphs: set[str] = set()
    activated_footprints: set[int] = set()
    while pending:
        current = pending.popleft()
        dependents = list(
            partition_dependents.get(
                (
                    current.graph_revision_id,
                    current.partition_key,
                    current.shard_ordinal,
                    current.record_kind,
                ),
                (),
            )
        )
        if current.graph_revision_id not in seen_graphs:
            seen_graphs.add(current.graph_revision_id)
            dependents.extend(
                graph_dependents.get(current.graph_revision_id, ())
            )
        for footprint in dependents:
            footprint_identity = id(footprint)
            if footprint_identity in activated_footprints:
                continue
            activated_footprints.add(footprint_identity)
            for output in footprint.outputs:
                if output not in impacted:
                    impacted.add(output)
                    pending.append(output)

    reasons: set[str] = set()
    full_rebuild = False
    if current_changes.has_unknown_changes:
        full_rebuild = True
        reasons.add("unknown-change")
    if matched_tokens != set(current_changes.tokens):
        full_rebuild = True
        reasons.add("unmatched-change")
    if full_rebuild:
        impacted = set(all_outputs)
    mode = "full-rebuild" if full_rebuild else "exact"
    ordered_partitions = tuple(sorted(impacted, key=_partition_sort_key))
    ordered_manifest_ids = tuple(
        sorted(manifest.manifest_id for manifest in current_manifests)
    )
    input_projection = {
        "changes": current_changes.projection,
        "graph_set_revision_id": current_closure.graph_set_revision_id,
        "graph_set_revision_sha256": current_closure.graph_set_raw_sha256,
        "dependency_manifests": [
            {
                "dependency_manifest_sha256": manifest.manifest_raw_sha256,
                "dependency_manifest_id": manifest.manifest_id,
                "graph_revision_sha256": manifest.graph_raw_sha256,
                "graph_revision_id": manifest.graph_revision_id,
            }
            for manifest in current_manifests
        ],
        "format": "workbench-crucible-dependency-impact-input-v1",
        "schema_version": 1,
    }
    input_closure_sha256 = hashlib.sha256(
        canonical_json_bytes(input_projection)
    ).hexdigest()
    plan_projection = {
        "context_ref_id": current_changes.context_ref_id,
        "graph_set_revision_id": current_closure.graph_set_revision_id,
        "dependency_manifest_ids": list(ordered_manifest_ids),
        "impacted_partitions": [item.to_dict() for item in ordered_partitions],
        "input_closure_sha256": input_closure_sha256,
        "mode": mode,
        "reason_codes": sorted(reasons),
    }
    impact_root_sha256 = hashlib.sha256(
        canonical_json_bytes(plan_projection)
    ).hexdigest()
    return DependencyImpactPlan(
        current_changes.context_ref_id,
        current_closure.graph_set_revision_id,
        mode,
        ordered_manifest_ids,
        ordered_partitions,
        tuple(sorted(reasons)),
        input_closure_sha256,
        impact_root_sha256,
    )


__all__ = [
    "AdmissionPartitionChange",
    "DependencyChangeSet",
    "DependencyComponentChange",
    "DependencyImpactBounds",
    "DependencyImpactClosure",
    "DependencyImpactPlan",
    "DependencyManifestSnapshot",
    "EvidencePartitionChange",
    "ImpactedGraphPartition",
    "SourceGraphPartitionChange",
    "SourceGraphRecordChange",
    "plan_dependency_impact",
]
