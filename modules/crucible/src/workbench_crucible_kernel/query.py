"""Deterministic revision-pinned graph query execution.

The kernel validates and isolates an immutable graph snapshot, then invokes one
explicit owner-supplied query port.  It owns transport, bounds, pinning, and
result-byte mechanics only; the port remains the authority for query meaning.
There is deliberately no store, moving-reference lookup, capability discovery,
or presentation behavior in this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import re
from types import MappingProxyType
from typing import Any, TypeAlias

from workbench_crucible import ValidatedRecord, load_canonical_record
from workbench_api.canonical import canonical_json_bytes, parse_canonical_json

from .kernel import AuthorityBinding, KernelValidationError


_CONTEXT_ID = re.compile(r"context-ref:sha256:[0-9a-f]{64}\Z")
_GRAPH_SET_ID = re.compile(r"graph-set-revision:sha256:[0-9a-f]{64}\Z")
_GRAPH_ID = re.compile(r"graph-revision:sha256:[0-9a-f]{64}\Z")
_QUERY_ALGORITHM_ID = re.compile(r"query-algorithm:sha256:[0-9a-f]{64}\Z")
_QUERY_IMPLEMENTATION_ID = re.compile(
    r"query-implementation:sha256:[0-9a-f]{64}\Z"
)
_PUBLICATION_PROOF_ID = re.compile(
    r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z"
)
_AUTHORITY_ID = re.compile(r"authority:sha256:[0-9a-f]{64}\Z")
_OWNER_REVISION_ID = re.compile(r"owner-revision:sha256:[0-9a-f]{64}\Z")
_AUTHORITY_ADAPTER_ID = re.compile(
    r"authority-adapter:sha256:[0-9a-f]{64}\Z"
)
_ROLE = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")

_MAX_MEMBERS = 16
_MAX_RECORDS = 100_000
_MAX_OBJECTS = 100_000
_MAX_INPUT_BYTES = 512 * 1024 * 1024
_MAX_OBJECT_BYTES = _MAX_INPUT_BYTES
_MAX_RESULT_BYTES = 4 * 1024 * 1024


def _fail(code: str, path: str, message: str) -> None:
    raise KernelValidationError(code, path, message)


@dataclass(frozen=True, slots=True)
class PinnedGraphQueryMember:
    """One role-keyed immutable graph revision supplied to a query."""

    role: str
    graph_revision_id: str
    graph_records: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class PinnedGraphQueryObject:
    """One descriptor and the exact immutable object bytes it describes."""

    descriptor_bytes: bytes
    object_bytes: bytes


@dataclass(frozen=True, slots=True)
class PinnedGraphQueryInput:
    """Store-independent immutable input for one revision-pinned query."""

    context_ref_id: str
    graph_set_revision_id: str
    publication_proof_id: str
    members: tuple[PinnedGraphQueryMember, ...]
    objects: tuple[PinnedGraphQueryObject, ...] = ()


@dataclass(frozen=True, slots=True)
class PinnedQueryKernelConfig:
    """Exact owner and operational bounds for one query implementation."""

    query_algorithm_id: str
    query_implementation_id: str
    query_owner: AuthorityBinding
    maximum_records: int
    maximum_input_bytes: int
    maximum_result_bytes: int


@dataclass(frozen=True, slots=True)
class PinnedQueryOwnerResult:
    """Canonical semantic answer and the graph rows inspected to derive it."""

    canonical_result_bytes: bytes
    inspected_graph_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PinnedGraphQueryView:
    """Isolated read-only view passed to the authority-owned query port."""

    context_ref_id: str
    graph_set_revision_id: str
    publication_proof_id: str
    query_algorithm_id: str
    query_implementation_id: str
    query_owner: AuthorityBinding
    input_closure_sha256: str
    parameters: Mapping[str, Any]
    parameter_bytes: bytes
    records_by_role: Mapping[str, tuple[ValidatedRecord, ...]]
    graph_revision_ids: Mapping[str, str]
    descriptor_records: Mapping[str, bytes]
    object_bytes: Mapping[str, bytes]

    def descriptor(self, record_id: str) -> bytes | None:
        return self.descriptor_records.get(record_id)

    def object(self, object_id: str) -> bytes | None:
        return self.object_bytes.get(object_id)


@dataclass(frozen=True, slots=True)
class PinnedQueryExecution:
    """Non-published deterministic execution result and its exact bindings."""

    context_ref_id: str
    graph_set_revision_id: str
    publication_proof_id: str
    graph_revision_ids: tuple[tuple[str, str], ...]
    query_algorithm_id: str
    query_implementation_id: str
    query_owner: AuthorityBinding
    parameter_sha256: str
    input_closure_sha256: str
    inspected_graph_record_ids: tuple[str, ...]
    canonical_result_bytes: bytes
    result_sha256: str


PinnedQueryPort: TypeAlias = Callable[
    [PinnedGraphQueryView], PinnedQueryOwnerResult
]


def _validate_typed_id(value: Any, pattern: re.Pattern[str], path: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail("kernel.query-invalid-id", path, "value is not the required content ID")
    return value


def _snapshot_config(config: PinnedQueryKernelConfig) -> PinnedQueryKernelConfig:
    if type(config) is not PinnedQueryKernelConfig:
        _fail(
            "kernel.query-invalid-config",
            "/config",
            "config must be an exact PinnedQueryKernelConfig",
        )
    algorithm_id = _validate_typed_id(
        config.query_algorithm_id,
        _QUERY_ALGORITHM_ID,
        "/config/query_algorithm_id",
    )
    implementation_id = _validate_typed_id(
        config.query_implementation_id,
        _QUERY_IMPLEMENTATION_ID,
        "/config/query_implementation_id",
    )
    owner = config.query_owner
    if type(owner) is not AuthorityBinding:
        _fail(
            "kernel.query-invalid-config",
            "/config/query_owner",
            "query owner must be an exact AuthorityBinding",
        )
    owner_copy = AuthorityBinding(
        _validate_typed_id(
            owner.owner_authority_id,
            _AUTHORITY_ID,
            "/config/query_owner/owner_authority_id",
        ),
        _validate_typed_id(
            owner.owner_revision_id,
            _OWNER_REVISION_ID,
            "/config/query_owner/owner_revision_id",
        ),
        _validate_typed_id(
            owner.authority_adapter_id,
            _AUTHORITY_ADAPTER_ID,
            "/config/query_owner/authority_adapter_id",
        ),
    )
    bounds = (
        ("maximum_records", config.maximum_records, 1, _MAX_RECORDS),
        (
            "maximum_input_bytes",
            config.maximum_input_bytes,
            1,
            _MAX_INPUT_BYTES,
        ),
        (
            "maximum_result_bytes",
            config.maximum_result_bytes,
            1,
            _MAX_RESULT_BYTES,
        ),
    )
    for field, value, minimum, maximum in bounds:
        if type(value) is not int or not minimum <= value <= maximum:
            _fail(
                "kernel.query-invalid-bound",
                f"/config/{field}",
                f"bound must be between {minimum} and {maximum}",
            )
    return PinnedQueryKernelConfig(
        algorithm_id,
        implementation_id,
        owner_copy,
        config.maximum_records,
        config.maximum_input_bytes,
        config.maximum_result_bytes,
    )


def _consume_input_bytes(
    consumed: int,
    added: int,
    maximum: int,
    path: str,
) -> int:
    """Account for immutable input before parsing, hashing, or copying it."""

    if added > maximum - consumed:
        _fail(
            "kernel.query-input-bound",
            path,
            "query closure exceeds the configured byte bound",
        )
    return consumed + added


def _canonical_parameters(
    raw: Any,
    *,
    maximum_input_bytes: int,
) -> tuple[bytes, Mapping[str, Any]]:
    if type(raw) is not bytes:
        _fail(
            "kernel.query-invalid-parameters",
            "/parameters",
            "parameters must be exact canonical JSON bytes",
        )
    _consume_input_bytes(0, len(raw), maximum_input_bytes, "/parameters")
    try:
        value = parse_canonical_json(raw)
    except Exception as exc:
        raise KernelValidationError(
            "kernel.query-invalid-parameters",
            "/parameters",
            "parameters are not canonical JSON V2",
        ) from exc
    if type(value) is not dict or canonical_json_bytes(value) != raw:
        _fail(
            "kernel.query-invalid-parameters",
            "/parameters",
            "parameters must be one canonical JSON object",
        )
    return bytes(raw), MappingProxyType(value)


def _snapshot_input(
    query_input: PinnedGraphQueryInput,
    config: PinnedQueryKernelConfig,
    parameter_bytes: bytes,
) -> tuple[
    tuple[tuple[str, str], ...],
    Mapping[str, tuple[ValidatedRecord, ...]],
    Mapping[str, bytes],
    Mapping[str, bytes],
    set[str],
]:
    if type(query_input) is not PinnedGraphQueryInput:
        _fail(
            "kernel.query-invalid-input",
            "/input",
            "input must be an exact PinnedGraphQueryInput",
        )
    _validate_typed_id(query_input.context_ref_id, _CONTEXT_ID, "/input/context_ref_id")
    _validate_typed_id(
        query_input.graph_set_revision_id,
        _GRAPH_SET_ID,
        "/input/graph_set_revision_id",
    )
    _validate_typed_id(
        query_input.publication_proof_id,
        _PUBLICATION_PROOF_ID,
        "/input/publication_proof_id",
    )
    if (
        type(query_input.members) is not tuple
        or not 1 <= len(query_input.members) <= _MAX_MEMBERS
    ):
        _fail(
            "kernel.query-invalid-members",
            "/input/members",
            "query requires one to sixteen immutable graph members",
        )
    if type(query_input.objects) is not tuple:
        _fail(
            "kernel.query-invalid-objects",
            "/input/objects",
            "query objects must be an immutable tuple",
        )
    if len(query_input.objects) > _MAX_OBJECTS:
        _fail(
            "kernel.query-object-bound",
            "/input/objects",
            "query objects exceed the hard count bound",
        )

    consumed = len(parameter_bytes)
    record_count = 0
    record_ids: set[str] = set()
    role_values: dict[str, tuple[ValidatedRecord, ...]] = {}
    revision_values: dict[str, str] = {}
    for index, member in enumerate(query_input.members):
        path = f"/input/members/{index}"
        if type(member) is not PinnedGraphQueryMember:
            _fail(
                "kernel.query-invalid-member",
                path,
                "member must be an exact PinnedGraphQueryMember",
            )
        role = member.role
        if (
            type(role) is not str
            or not 1 <= len(role) <= 8192
            or _ROLE.fullmatch(role) is None
            or role in role_values
        ):
            _fail(
                "kernel.query-invalid-role",
                path + "/role",
                "member role is invalid or duplicated",
            )
        consumed = _consume_input_bytes(
            consumed,
            len(role.encode("utf-8")),
            config.maximum_input_bytes,
            path + "/role",
        )
        graph_id = _validate_typed_id(
            member.graph_revision_id,
            _GRAPH_ID,
            path + "/graph_revision_id",
        )
        if graph_id in revision_values.values():
            _fail(
                "kernel.query-duplicate-revision",
                path + "/graph_revision_id",
                "one graph revision cannot occupy multiple query roles",
            )
        if type(member.graph_records) is not tuple or not member.graph_records:
            _fail(
                "kernel.query-invalid-member-records",
                path + "/graph_records",
                "each member requires a non-empty immutable record tuple",
            )
        records: list[ValidatedRecord] = []
        logical_keys: set[str] = set()
        for row_index, raw in enumerate(member.graph_records):
            row_path = f"{path}/graph_records/{row_index}"
            record_count += 1
            if record_count > config.maximum_records:
                _fail(
                    "kernel.query-record-bound",
                    row_path,
                    "graph rows exceed the configured query bound",
                )
            if type(raw) is not bytes:
                _fail(
                    "kernel.query-invalid-record",
                    row_path,
                    "graph row must be exact canonical bytes",
                )
            consumed = _consume_input_bytes(
                consumed,
                len(raw),
                config.maximum_input_bytes,
                row_path,
            )
            try:
                record = load_canonical_record(raw)
            except Exception as exc:
                raise KernelValidationError(
                    "kernel.query-invalid-record",
                    row_path,
                    "graph row is not canonical C01 data",
                ) from exc
            value = record.to_dict()
            logical_key = value.get("logical_key")
            if (
                record.kind != "graph-record"
                or value.get("context_ref_id") != query_input.context_ref_id
                or type(logical_key) is not str
                or logical_key in logical_keys
                or record.id in record_ids
            ):
                _fail(
                    "kernel.query-record-scope",
                    row_path,
                    "graph row kind, context, identity, or logical key differs",
                )
            records.append(load_canonical_record(record.canonical_bytes))
            logical_keys.add(logical_key)
            record_ids.add(record.id)
        role_values[role] = tuple(records)
        revision_values[role] = graph_id

    descriptor_values: dict[str, bytes] = {}
    object_values: dict[str, bytes] = {}
    for index, item in enumerate(query_input.objects):
        path = f"/input/objects/{index}"
        if type(item) is not PinnedGraphQueryObject:
            _fail(
                "kernel.query-invalid-object",
                path,
                "object must be an exact PinnedGraphQueryObject",
            )
        if type(item.descriptor_bytes) is not bytes or type(item.object_bytes) is not bytes:
            _fail(
                "kernel.query-invalid-object",
                path,
                "descriptor and object must be exact immutable bytes",
            )
        if len(item.object_bytes) > _MAX_OBJECT_BYTES:
            _fail(
                "kernel.query-object-byte-bound",
                path + "/object_bytes",
                "immutable object exceeds the hard byte bound",
            )
        consumed = _consume_input_bytes(
            consumed,
            len(item.descriptor_bytes) + len(item.object_bytes),
            config.maximum_input_bytes,
            path,
        )
        try:
            descriptor = load_canonical_record(item.descriptor_bytes)
        except Exception as exc:
            raise KernelValidationError(
                "kernel.query-invalid-object",
                path + "/descriptor_bytes",
                "descriptor is not canonical C01 data",
            ) from exc
        value = descriptor.to_dict()
        digest = hashlib.sha256(item.object_bytes).hexdigest()
        object_id = value.get("object_id")
        if (
            descriptor.kind != "object-descriptor"
            or descriptor.id in descriptor_values
            or type(object_id) is not str
            or object_id != f"workbench-blob-v2:sha256:{digest}"
            or value.get("sha256") != digest
            or value.get("byte_length") != len(item.object_bytes)
        ):
            _fail(
                "kernel.query-object-binding",
                path,
                "descriptor identity or immutable object binding differs",
            )
        prior = object_values.get(object_id)
        if prior is not None and prior != item.object_bytes:
            _fail(
                "kernel.query-object-collision",
                path,
                "one object ID names different bytes",
            )
        descriptor_values[descriptor.id] = descriptor.canonical_bytes
        object_values[object_id] = bytes(item.object_bytes)

    ordered_revisions = tuple(sorted(revision_values.items()))
    return (
        ordered_revisions,
        MappingProxyType(dict(sorted(role_values.items()))),
        MappingProxyType(dict(sorted(descriptor_values.items()))),
        MappingProxyType(dict(sorted(object_values.items()))),
        record_ids,
    )


def execute_pinned_graph_query(
    query_input: PinnedGraphQueryInput,
    *,
    config: PinnedQueryKernelConfig,
    parameter_bytes: bytes,
    query_port: PinnedQueryPort,
) -> PinnedQueryExecution:
    """Execute one bounded owner query over exact pinned immutable revisions."""

    current = _snapshot_config(config)
    parameters_raw, parameters = _canonical_parameters(
        parameter_bytes,
        maximum_input_bytes=current.maximum_input_bytes,
    )
    (
        revisions,
        records_by_role,
        descriptor_records,
        object_bytes,
        available_record_ids,
    ) = _snapshot_input(query_input, current, parameters_raw)
    parameter_sha256 = hashlib.sha256(parameters_raw).hexdigest()
    closure_material = {
        "context_ref_id": query_input.context_ref_id,
        "graph_set_revision_id": query_input.graph_set_revision_id,
        "publication_proof_id": query_input.publication_proof_id,
        "members": [
            {
                "role": role,
                "graph_revision_id": graph_id,
                "graph_record_ids": [
                    record.id for record in records_by_role[role]
                ],
            }
            for role, graph_id in revisions
        ],
        "objects": [
            {
                "object_descriptor_id": descriptor_id,
                "object_id": load_canonical_record(raw).to_dict()["object_id"],
            }
            for descriptor_id, raw in descriptor_records.items()
        ],
        "parameter_sha256": parameter_sha256,
        "query_algorithm_id": current.query_algorithm_id,
        "query_implementation_id": current.query_implementation_id,
        "query_owner": current.query_owner.to_dict(),
    }
    input_closure_sha256 = hashlib.sha256(
        canonical_json_bytes(closure_material)
    ).hexdigest()
    if not callable(query_port):
        _fail(
            "kernel.query-invalid-port",
            "/query_port",
            "query port must be callable",
        )
    view = PinnedGraphQueryView(
        context_ref_id=query_input.context_ref_id,
        graph_set_revision_id=query_input.graph_set_revision_id,
        publication_proof_id=query_input.publication_proof_id,
        query_algorithm_id=current.query_algorithm_id,
        query_implementation_id=current.query_implementation_id,
        query_owner=current.query_owner,
        input_closure_sha256=input_closure_sha256,
        parameters=parameters,
        parameter_bytes=parameters_raw,
        records_by_role=records_by_role,
        graph_revision_ids=MappingProxyType(dict(revisions)),
        descriptor_records=descriptor_records,
        object_bytes=object_bytes,
    )
    try:
        owner_result = query_port(view)
    except Exception as exc:
        raise KernelValidationError(
            "kernel.query-port-failed",
            "/query_port",
            "owner query port failed",
        ) from exc
    if type(owner_result) is not PinnedQueryOwnerResult:
        _fail(
            "kernel.query-invalid-result",
            "/query_port/result",
            "query port must return an exact PinnedQueryOwnerResult",
        )
    inspected = owner_result.inspected_graph_record_ids
    inspected_path = "/query_port/result/inspected_graph_record_ids"
    if type(inspected) is not tuple:
        _fail(
            "kernel.query-invalid-inspection",
            inspected_path,
            "inspected rows must be an exact immutable tuple",
        )
    if len(inspected) > _MAX_RECORDS or len(inspected) > len(available_record_ids):
        _fail(
            "kernel.query-inspection-bound",
            inspected_path,
            "inspected row count exceeds the pinned graph closure or hard bound",
        )
    try:
        parameters_unchanged = (
            canonical_json_bytes(dict(view.parameters)) == parameters_raw
        )
    except Exception:
        parameters_unchanged = False
    if not parameters_unchanged:
        _fail(
            "kernel.query-port-mutation",
            "/query_port",
            "owner query port mutated its isolated parameter view",
        )
    raw = owner_result.canonical_result_bytes
    if type(raw) is not bytes or not raw or len(raw) > current.maximum_result_bytes:
        _fail(
            "kernel.query-result-bound",
            "/query_port/result/canonical_result_bytes",
            "query result must be non-empty canonical bytes within the configured bound",
        )
    try:
        result_value = parse_canonical_json(raw)
    except Exception as exc:
        raise KernelValidationError(
            "kernel.query-invalid-result",
            "/query_port/result/canonical_result_bytes",
            "query result is not canonical JSON V2",
        ) from exc
    if type(result_value) is not dict or canonical_json_bytes(result_value) != raw:
        _fail(
            "kernel.query-invalid-result",
            "/query_port/result/canonical_result_bytes",
            "query result must be one canonical JSON object",
        )
    if (
        any(type(item) is not str for item in inspected)
        or len(inspected) != len(set(inspected))
        or any(item not in available_record_ids for item in inspected)
    ):
        _fail(
            "kernel.query-invalid-inspection",
            inspected_path,
            "inspected rows must be a unique subset of the pinned graph closure",
        )
    ordered_inspected = tuple(sorted(inspected, key=lambda item: item.encode("utf-8")))
    return PinnedQueryExecution(
        context_ref_id=query_input.context_ref_id,
        graph_set_revision_id=query_input.graph_set_revision_id,
        publication_proof_id=query_input.publication_proof_id,
        graph_revision_ids=revisions,
        query_algorithm_id=current.query_algorithm_id,
        query_implementation_id=current.query_implementation_id,
        query_owner=current.query_owner,
        parameter_sha256=parameter_sha256,
        input_closure_sha256=input_closure_sha256,
        inspected_graph_record_ids=ordered_inspected,
        canonical_result_bytes=bytes(raw),
        result_sha256=hashlib.sha256(raw).hexdigest(),
    )


__all__ = [
    "PinnedGraphQueryInput",
    "PinnedGraphQueryMember",
    "PinnedGraphQueryObject",
    "PinnedGraphQueryView",
    "PinnedQueryExecution",
    "PinnedQueryKernelConfig",
    "PinnedQueryOwnerResult",
    "PinnedQueryPort",
    "execute_pinned_graph_query",
]
