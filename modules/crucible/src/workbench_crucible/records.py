"""Validated, immutable Crucible V2 record snapshots.

This module is the high-level record boundary.  Callers should use
``seal_record`` or ``load_canonical_record`` rather than the low-level hashing
helpers in :mod:`workbench_api.canonical`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import re
from types import MappingProxyType
from typing import Any, TypeAlias, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from workbench_api.canonical import CanonicalJsonError, canonical_json_bytes, content_id, parse_canonical_json, parse_json_strict, record_content_id, validate_content_id
from .schema_registry import (
    RecordValidationError,
    ValidationDiagnostic,
    ValidationPhase,
    annotated_record_references,
    registered_contracts,
    registered_schema_bytes,
    validate_candidate_schema,
    validate_closed_schema,
)


SEMANTIC_ROOT_DOMAIN = b"workbench-semantic-root-v2\n"
_CONTENT_ID_RE = re.compile(
    r"(?P<kind>[a-z][a-z0-9-]*):sha256:(?P<digest>[0-9a-f]{64})\Z"
)
_GRAPH_RECORD_KIND_ORDER = {
    "node": 0,
    "edge": 1,
    "property": 2,
    "evidence-link": 3,
    "refinement-mapping": 4,
    "frontier": 5,
    "conflict": 6,
}
_COUNT_FIELD_BY_RECORD_KIND = {
    "node": "nodes",
    "edge": "edges",
    "property": "properties",
    "evidence-link": "evidence_links",
    "refinement-mapping": "refinement_mappings",
    "frontier": "frontiers",
    "conflict": "conflicts",
}
_C01_RECORD_KINDS = frozenset(contract.kind for contract in registered_contracts())
_AUTHORITY_SCOPED_EXTERNAL_KINDS = frozenset(
    {
        "authority",
        "owner-revision",
        "authority-adapter",
        "policy",
        "producer",
        "transport-normalizer",
        "validator",
        "validation-receipt",
        "ontology",
        "validation-requirement",
        "merge-recipe",
        "selector",
        "conformance-case",
        "compatibility-decision",
    }
)
_RECIPE_OWNER_POLICY_FIELDS = frozenset(
    {
        "total_order",
        "partition",
        "deduplication",
        "dependency_footprint",
        "invalidation",
        "deterministic_failure",
    }
)
_SEMANTIC_AUTHORITY_POLICY_FIELDS = frozenset(
    {
        "identity",
        "category_membership",
        "category_overlap",
        "cross_category_edges",
        "resolution_aggregation",
        "refinement_mapping",
        "evidence_propagation",
        "conflict_propagation",
        "frontier_propagation",
    }
)

_BOUND_SCHEMA_ALLOWED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$defs",
        "$ref",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "type",
        "const",
        "enum",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "format",
        "maxItems",
        "minItems",
        "uniqueItems",
        "maxProperties",
        "minProperties",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "prefixItems",
        "oneOf",
        "anyOf",
        "allOf",
    }
)


def _bound_schema_pointer(root: dict[str, Any], reference: str) -> Any:
    """Resolve one same-resource JSON Pointer for the restricted profile."""

    if reference == "#":
        return root
    if not reference.startswith("#/"):
        raise CanonicalJsonError(
            "bound schemas may use only same-resource JSON Pointer references"
        )
    value: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if type(value) is not dict or part not in value:
            raise CanonicalJsonError(
                f"bound schema reference does not resolve: {reference}"
            )
        value = value[part]
    return value


def _assert_closed_bound_schema(schema: dict[str, Any]) -> None:
    """Prove recursive object closedness in a finite offline schema profile.

    This is intentionally more restrictive than Draft 2020-12.  A bound
    schema is an identity-bearing validation dependency, so constructs whose
    object closure cannot be established locally are rejected rather than
    treated as permission to admit arbitrary keys.
    """

    active_refs: set[str] = set()

    def fail(path: tuple[str | int, ...], detail: str) -> None:
        raise CanonicalJsonError(
            f"{detail} at {_pointer(path) or '/'}"
        )

    def admits_object(node: bool | dict[str, Any]) -> bool:
        if node is False:
            return False
        if node is True:
            return True
        if "const" in node:
            return type(node["const"]) is dict
        if "enum" in node:
            return any(type(item) is dict for item in node["enum"])
        declared_type = node.get("type")
        if type(declared_type) is str:
            return declared_type == "object"
        if type(declared_type) is list:
            return "object" in declared_type
        if "$ref" in node:
            target = _bound_schema_pointer(schema, node["$ref"])
            if type(target) not in (bool, dict):
                return True
            return admits_object(target)
        for keyword in ("oneOf", "anyOf", "allOf"):
            if keyword in node:
                return any(
                    admits_object(item)
                    for item in node[keyword]
                    if type(item) in (bool, dict)
                )
        # With no exclusion, JSON Schema admits every JSON instance type.
        return True

    def admits_array(node: bool | dict[str, Any]) -> bool:
        if node is False:
            return False
        if node is True:
            return True
        # A const/enum value is already a finite exact value projection; it
        # cannot acquire additional nested object members at validation time.
        if "const" in node or "enum" in node:
            return False
        declared_type = node.get("type")
        if type(declared_type) is str:
            return declared_type == "array"
        if type(declared_type) is list:
            return "array" in declared_type
        if "$ref" in node:
            target = _bound_schema_pointer(schema, node["$ref"])
            if type(target) not in (bool, dict):
                return True
            return admits_array(target)
        for keyword in ("oneOf", "anyOf", "allOf"):
            if keyword in node:
                return any(
                    admits_array(item)
                    for item in node[keyword]
                    if type(item) in (bool, dict)
                )
        # An untyped schema admits arrays as well as objects and scalars.
        return True

    def visit(node: Any, path: tuple[str | int, ...]) -> None:
        if type(node) is bool:
            if node:
                fail(path, "unconstrained true schema admits open objects")
            return
        if type(node) is not dict:
            fail(path, "schema node must be an object or boolean")
        unknown = sorted(set(node) - _BOUND_SCHEMA_ALLOWED_KEYWORDS)
        if unknown:
            fail(
                path,
                "unsupported bound-schema keyword " + repr(unknown[0]),
            )

        reference = node.get("$ref")
        if reference is not None:
            if type(reference) is not str:
                fail((*path, "$ref"), "$ref must be a string")
            assertion_siblings = set(node) - {
                "$ref",
                "$schema",
                "$id",
                "$anchor",
                "title",
                "description",
                "default",
                "examples",
                "deprecated",
                "readOnly",
                "writeOnly",
            }
            if assertion_siblings:
                fail(
                    path,
                    "restricted bound-schema $ref may not have assertion siblings",
                )
            if reference in active_refs:
                fail((*path, "$ref"), "recursive bound-schema reference is unsupported")
            active_refs.add(reference)
            try:
                visit(_bound_schema_pointer(schema, reference), (*path, "$ref"))
            finally:
                active_refs.remove(reference)
            return

        for keyword in ("oneOf", "anyOf", "allOf"):
            if keyword not in node:
                continue
            branches = node[keyword]
            if type(branches) is not list or not branches:
                fail((*path, keyword), f"{keyword} must be a non-empty array")
            for index, branch in enumerate(branches):
                visit(branch, (*path, keyword, index))

        if admits_object(node):
            combinator_only = any(
                keyword in node for keyword in ("oneOf", "anyOf", "allOf")
            ) and not any(
                keyword in node
                for keyword in ("type", "properties", "additionalProperties")
            )
            if not combinator_only and node.get("additionalProperties") is not False:
                fail(path, "object-capable bound schema is not closed")

        properties = node.get("properties", {})
        if type(properties) is not dict:
            fail((*path, "properties"), "properties must be an object")
        for name, child in properties.items():
            visit(child, (*path, "properties", name))

        array_combinator_only = any(
            keyword in node for keyword in ("oneOf", "anyOf", "allOf")
        ) and not any(
            keyword in node
            for keyword in ("type", "items", "prefixItems")
        )
        if admits_array(node) and not array_combinator_only:
            if "items" not in node:
                fail(path, "array-capable schema has unconstrained items")
            prefix_items = node.get("prefixItems", [])
            if type(prefix_items) is not list:
                fail((*path, "prefixItems"), "prefixItems must be an array")
            for index, child in enumerate(prefix_items):
                visit(child, (*path, "prefixItems", index))
            visit(node["items"], (*path, "items"))

    visit(schema, ())


def semantic_root_v2(domain: str, value: Any) -> str:
    """Compute the exact C01 semantic-root algorithm for a value projection."""

    if type(domain) is not str or not domain or "\n" in domain or "\r" in domain:
        raise CanonicalJsonError("semantic-root domain must be a non-empty single line")
    try:
        domain_bytes = domain.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise CanonicalJsonError("semantic-root domain must be ASCII") from exc
    return hashlib.sha256(
        SEMANTIC_ROOT_DOMAIN
        + domain_bytes
        + b"\n"
        + canonical_json_bytes(value)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ReferenceExpectation:
    """One immutable reference and the exact target kinds allowed for it."""

    path: str
    record_id: str
    expected_kinds: tuple[str, ...]
    expected_authority_id: str | None = None
    expected_owner_revision_id: str | None = None
    expected_adapter_id: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    """Exact authority binding under which an owner decision is validated."""

    owner_authority_id: str
    owner_revision_id: str
    authority_adapter_id: str


@dataclass(frozen=True, slots=True)
class ValidatedExternalReference:
    """Exact external bytes returned by the pinned structural registry."""

    record_id: str
    kind: str
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        value = parse_canonical_json(self.canonical_bytes)
        return cast(dict[str, Any], value)


@dataclass(frozen=True, slots=True)
class AdapterValidatedReference:
    """Exact external record bytes accepted by an owning authority adapter."""

    record_id: str
    kind: str
    canonical_bytes: bytes
    adapter_id: str
    owner_authority_id: str
    owner_revision_id: str

    def to_dict(self) -> dict[str, Any]:
        value = parse_canonical_json(self.canonical_bytes)
        return cast(dict[str, Any], value)


@dataclass(frozen=True, slots=True)
class TrustedAuthorityAdapter:
    """Pinned bootstrap registration for one owner-approved authority adapter.

    The mapping containing this value is the application trust root.  Crucible
    revalidates the adapter bytes and identity; it never asks the generic
    external resolver to attest an adapter to itself.  The mapping's explicit
    adapter-to-authority entry is the C01 bootstrap trust root.  A formal
    adapter descriptor/registration belongs to the later capability contract.
    """

    adapter_id: str
    owner_authority_id: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class ValidatedRecord:
    """An immutable canonical byte snapshot of one fully validated record."""

    id: str
    kind: str
    format: str
    schema_id: str
    canonical_bytes: bytes
    references: tuple[ReferenceExpectation, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh mutable copy; mutation cannot affect this snapshot."""

        value = parse_canonical_json(self.canonical_bytes)
        return cast(dict[str, Any], value)

    def __bytes__(self) -> bytes:
        return self.canonical_bytes


RecordResolution: TypeAlias = (
    ValidatedRecord | Mapping[str, Any] | bytes | bytearray | memoryview
)
RecordResolver: TypeAlias = Callable[[str], RecordResolution | None]
BlobResolver: TypeAlias = Callable[[str], bytes | bytearray | memoryview | None]
ExternalReferenceResolver: TypeAlias = Callable[
    [ReferenceExpectation],
    ValidatedExternalReference | AdapterValidatedReference | None,
]
ExternalReferenceKey: TypeAlias = tuple[
    str, tuple[str, ...], str | None, str | None, str | None
]


@dataclass(frozen=True, slots=True)
class PolicyValidationRequest:
    """Pinned relation view supplied to an owner-policy validation port."""

    rule: str
    record: ValidatedRecord
    records: Mapping[str, ValidatedRecord]
    external_references: Mapping[
        ExternalReferenceKey, ValidatedExternalReference | AdapterValidatedReference
    ]
    object_bytes: Mapping[str, bytes]
    authority_scope: AuthorityScope | None
    authority_adapter: TrustedAuthorityAdapter | None


PolicyValidationResult: TypeAlias = bool | Iterable[ValidationDiagnostic]
PolicyValidator: TypeAlias = Callable[[PolicyValidationRequest], PolicyValidationResult]


@dataclass(frozen=True, slots=True)
class RelationResolvers:
    """Optional pinned resolvers for cross-record and exact-object validation.

    Supplying a resolver is a fail-closed promise: a referenced value returning
    ``None`` is reported as unavailable.  Omitting a resolver leaves that
    publication-level relation for a later authority-aware boundary.
    """

    record_resolver: RecordResolver | None = None
    blob_resolver: BlobResolver | None = None
    external_reference_resolver: ExternalReferenceResolver | None = None
    trusted_adapters: Mapping[str, TrustedAuthorityAdapter] | None = None
    policy_validator: PolicyValidator | None = None
    require_complete: bool = False


def _reference_expectation_metadata(
    value: Any,
) -> tuple[str, str, tuple[str, ...], str | None, str | None, str | None]:
    if type(value) is not ReferenceExpectation:
        raise TypeError("references must contain exact ReferenceExpectation values")
    if type(value.path) is not str or type(value.record_id) is not str:
        raise TypeError("reference path and record_id must be exact strings")
    if (
        type(value.expected_kinds) is not tuple
        or not value.expected_kinds
        or any(type(item) is not str for item in value.expected_kinds)
    ):
        raise TypeError("reference expected_kinds must be a non-empty tuple of strings")
    optional_fields = (
        value.expected_authority_id,
        value.expected_owner_revision_id,
        value.expected_adapter_id,
    )
    if any(type(item) is not str and item is not None for item in optional_fields):
        raise TypeError("reference authority metadata must be exact strings or null")
    return (
        value.path,
        value.record_id,
        value.expected_kinds,
        value.expected_authority_id,
        value.expected_owner_revision_id,
        value.expected_adapter_id,
    )


def _validated_record_metadata(value: Any) -> tuple[Any, ...]:
    if type(value) is not ValidatedRecord:
        raise TypeError("record resolver must return an exact ValidatedRecord")
    for field_name in ("id", "kind", "format", "schema_id"):
        if type(getattr(value, field_name)) is not str:
            raise TypeError(f"validated record {field_name} must be an exact string")
    if type(value.canonical_bytes) is not bytes:
        raise TypeError("validated record canonical_bytes must be exact bytes")
    if type(value.references) is not tuple:
        raise TypeError("validated record references must be an exact tuple")
    references = tuple(
        _reference_expectation_metadata(item) for item in value.references
    )
    return (
        value.id,
        value.kind,
        value.format,
        value.schema_id,
        references,
    )


def _trusted_adapter_metadata(value: Any) -> tuple[str, str, bytes]:
    if type(value) is not TrustedAuthorityAdapter:
        raise TypeError("trusted adapter must use the exact registration type")
    if type(value.adapter_id) is not str or type(value.owner_authority_id) is not str:
        raise TypeError("trusted adapter IDs must be exact strings")
    if type(value.canonical_bytes) is not bytes:
        raise TypeError("trusted adapter canonical_bytes must be exact bytes")
    return value.adapter_id, value.owner_authority_id, value.canonical_bytes


def _external_reference_metadata(value: Any) -> tuple[Any, ...]:
    if type(value) is ValidatedExternalReference:
        fields = (value.record_id, value.kind)
        if any(type(item) is not str for item in fields):
            raise TypeError("external reference identity fields must be exact strings")
        if type(value.canonical_bytes) is not bytes:
            raise TypeError("external reference canonical_bytes must be exact bytes")
        return (*fields, value.canonical_bytes)
    if type(value) is AdapterValidatedReference:
        fields = (
            value.record_id,
            value.kind,
            value.adapter_id,
            value.owner_authority_id,
            value.owner_revision_id,
        )
        if any(type(item) is not str for item in fields):
            raise TypeError("adapter-validated reference fields must be exact strings")
        if type(value.canonical_bytes) is not bytes:
            raise TypeError("adapter-validated canonical_bytes must be exact bytes")
        return (*fields, value.canonical_bytes)
    raise TypeError("external registry returned an unsupported validation result")


_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


def _copy_reference_expectation(value: Any) -> ReferenceExpectation:
    (
        path,
        record_id,
        expected_kinds,
        expected_authority_id,
        expected_owner_revision_id,
        expected_adapter_id,
    ) = _reference_expectation_metadata(value)
    return ReferenceExpectation(
        path=path,
        record_id=record_id,
        expected_kinds=expected_kinds,
        expected_authority_id=expected_authority_id,
        expected_owner_revision_id=expected_owner_revision_id,
        expected_adapter_id=expected_adapter_id,
    )


def _copy_validated_record(value: Any) -> ValidatedRecord:
    (
        record_id,
        kind,
        record_format,
        schema_id,
        _,
    ) = _validated_record_metadata(value)
    return ValidatedRecord(
        id=record_id,
        kind=kind,
        format=record_format,
        schema_id=schema_id,
        canonical_bytes=value.canonical_bytes,
        references=tuple(
            _copy_reference_expectation(item) for item in value.references
        ),
    )


def _copy_external_reference(
    value: Any,
) -> ValidatedExternalReference | AdapterValidatedReference:
    metadata = _external_reference_metadata(value)
    if type(value) is ValidatedExternalReference:
        record_id, kind, canonical_bytes = metadata
        return ValidatedExternalReference(
            record_id=record_id,
            kind=kind,
            canonical_bytes=canonical_bytes,
        )
    (
        record_id,
        kind,
        adapter_id,
        owner_authority_id,
        owner_revision_id,
        canonical_bytes,
    ) = metadata
    return AdapterValidatedReference(
        record_id=record_id,
        kind=kind,
        canonical_bytes=canonical_bytes,
        adapter_id=adapter_id,
        owner_authority_id=owner_authority_id,
        owner_revision_id=owner_revision_id,
    )


def _authority_scope_metadata(value: Any) -> tuple[str, str, str]:
    if type(value) is not AuthorityScope:
        raise TypeError("policy authority scope must use the exact scope type")
    fields = (
        value.owner_authority_id,
        value.owner_revision_id,
        value.authority_adapter_id,
    )
    if any(type(item) is not str for item in fields):
        raise TypeError("policy authority scope IDs must be exact strings")
    return fields


def _external_reference_key_metadata(value: Any) -> ExternalReferenceKey:
    if type(value) is not tuple or len(value) != 5:
        raise TypeError("policy external-reference keys must be exact 5-tuples")
    record_id, expected_kinds, authority_id, owner_revision_id, adapter_id = value
    if type(record_id) is not str:
        raise TypeError("policy external-reference record IDs must be exact strings")
    if type(expected_kinds) is not tuple or any(
        type(item) is not str for item in expected_kinds
    ):
        raise TypeError("policy external-reference kinds must be exact string tuples")
    if any(
        type(item) is not str and item is not None
        for item in (authority_id, owner_revision_id, adapter_id)
    ):
        raise TypeError("policy external-reference authority IDs must be strings or null")
    return cast(ExternalReferenceKey, value)


def _snapshot_policy_request(
    value: Any,
) -> tuple[PolicyValidationRequest, tuple[Any, ...]]:
    """Return an isolated policy request and its exact structural snapshot."""

    if type(value) is not PolicyValidationRequest or type(value.rule) is not str:
        raise TypeError("policy validation request has an invalid exact type")
    if type(value.records) is not _MAPPING_PROXY_TYPE:
        raise TypeError("policy record view must be an exact read-only mapping")
    if type(value.external_references) is not _MAPPING_PROXY_TYPE:
        raise TypeError("policy external-reference view must be an exact read-only mapping")
    if type(value.object_bytes) is not _MAPPING_PROXY_TYPE:
        raise TypeError("policy object-byte view must be an exact read-only mapping")

    records: dict[str, ValidatedRecord] = {}
    for key, record in value.records.items():
        if type(key) is not str:
            raise TypeError("policy record keys must be exact strings")
        copied = _copy_validated_record(record)
        if copied.id != key:
            raise ValueError("policy record key does not match its exact record ID")
        records[key] = copied

    record = _copy_validated_record(value.record)
    indexed_record = records.get(record.id)
    if (
        indexed_record is None
        or _validated_record_metadata(indexed_record)
        != _validated_record_metadata(record)
        or indexed_record.canonical_bytes != record.canonical_bytes
    ):
        raise ValueError("policy subject record is not its exact indexed record")
    # Preserve one object identity for the subject and its indexed view.  A
    # callback may still force-mutate the frozen value, but the post-call
    # snapshot below will detect that mutation and none of these copies are
    # shared with the relation validator's authoritative state.
    record = indexed_record

    external_references: dict[
        ExternalReferenceKey,
        ValidatedExternalReference | AdapterValidatedReference,
    ] = {}
    for raw_key, external in value.external_references.items():
        key = _external_reference_key_metadata(raw_key)
        external_references[key] = _copy_external_reference(external)

    object_bytes: dict[str, bytes] = {}
    for object_id, raw in value.object_bytes.items():
        if type(object_id) is not str or type(raw) is not bytes:
            raise TypeError("policy object-byte entries must use exact strings and bytes")
        object_bytes[object_id] = raw

    scope_fields = _authority_scope_metadata(value.authority_scope)
    authority_scope = AuthorityScope(*scope_fields)
    adapter_id, owner_authority_id, adapter_bytes = _trusted_adapter_metadata(
        value.authority_adapter
    )
    authority_adapter = TrustedAuthorityAdapter(
        adapter_id=adapter_id,
        owner_authority_id=owner_authority_id,
        canonical_bytes=adapter_bytes,
    )

    request = PolicyValidationRequest(
        rule=value.rule,
        record=record,
        records=MappingProxyType(records),
        external_references=MappingProxyType(external_references),
        object_bytes=MappingProxyType(object_bytes),
        authority_scope=authority_scope,
        authority_adapter=authority_adapter,
    )
    signature = (
        request.rule,
        (_validated_record_metadata(record), record.canonical_bytes),
        tuple(
            (key, _validated_record_metadata(item), item.canonical_bytes)
            for key, item in sorted(
                records.items(), key=lambda entry: entry[0].encode("utf-8")
            )
        ),
        tuple(
            (key, _external_reference_metadata(item))
            for key, item in sorted(
                external_references.items(),
                key=lambda entry: (
                    entry[0][0].encode("utf-8"),
                    tuple(item.encode("utf-8") for item in entry[0][1]),
                    b"" if entry[0][2] is None else entry[0][2].encode("utf-8"),
                    b"" if entry[0][3] is None else entry[0][3].encode("utf-8"),
                    b"" if entry[0][4] is None else entry[0][4].encode("utf-8"),
                ),
            )
        ),
        tuple(
            sorted(
                object_bytes.items(), key=lambda entry: entry[0].encode("utf-8")
            )
        ),
        scope_fields,
        (adapter_id, owner_authority_id, adapter_bytes),
    )
    return request, signature


def _diagnostic(
    phase: ValidationPhase, code: str, path: str, message: str
) -> ValidationDiagnostic:
    return ValidationDiagnostic(phase=phase, code=code, path=path, message=message)


def _domain_snapshot(value: Any) -> dict[str, Any]:
    try:
        snapshot = parse_canonical_json(canonical_json_bytes(value))
    except (CanonicalJsonError, TypeError) as exc:
        raise RecordValidationError(
            (_diagnostic(ValidationPhase.DOMAIN, "domain.invalid", "", str(exc)),)
        ) from exc
    if type(snapshot) is not dict:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.record-object",
                    "",
                    "record must be an ordinary JSON object",
                ),
            )
        )
    return snapshot


def _kind_of(record_id: str) -> str | None:
    match = _CONTENT_ID_RE.fullmatch(record_id)
    return None if match is None else match.group("kind")


def _canonical_set_key(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _walk_pattern(
    value: Any,
    pattern: tuple[str, ...],
    path: tuple[object, ...] = (),
):
    if not pattern:
        yield value, path
        return
    head, *tail = pattern
    if head == "*":
        if type(value) is list:
            for index, item in enumerate(value):
                yield from _walk_pattern(item, tuple(tail), (*path, index))
        return
    if type(value) is dict and head in value:
        yield from _walk_pattern(value[head], tuple(tail), (*path, head))


def _pointer(parts: tuple[object, ...]) -> str:
    return "" if not parts else "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _walk_json(value: Any, path: tuple[object, ...] = ()):
    yield value, path
    if type(value) is dict:
        for key, item in value.items():
            yield from _walk_json(item, (*path, key))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from _walk_json(item, (*path, index))


_SET_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    "evidence-record": (
        ("provenance_record_ids",),
        ("corrects_record_ids",),
        ("supersedes_record_ids",),
        ("legacy_bindings",),
        ("legacy_bindings", "*", "limitations"),
        ("limitations",),
    ),
    "admission-record": (
        ("evaluated_schema_object_descriptor_ids",),
        ("policy_ids",),
        ("profile_adapter_bindings",),
        ("diagnostics", "*", "supporting_record_ids"),
        ("limitations",),
        ("supporting_receipt_ids",),
    ),
    "evidence-set-revision": (
        ("parent_evidence_set_revision_ids",),
        ("limitations",),
    ),
    "graph-record": (
        ("evidence_inputs",),
        ("source_graph_inputs",),
        ("limitations",),
        ("body", "fine_endpoints"),
        ("body", "omitted_conflict_record_ids"),
        ("body", "subject_ids"),
        ("body", "conflicting_graph_record_ids"),
    ),
    "graph-revision": (
        ("ontology_ids",),
        ("record_schema_object_descriptor_ids",),
        ("profile_support_bindings",),
        ("profile_support_bindings", "*", "support_decision_ids"),
        ("tool_ids",),
        ("evidence_set_revision_ids",),
        ("input_graph_bindings",),
        ("parent_graph_revision_ids",),
        ("limitations",),
        ("frontier_record_ids",),
        ("conflict_record_ids",),
        ("semantic_validation_requirement_ids",),
    ),
    "graph-set-revision": (
        ("evidence_set_revision_ids",),
        ("profile_support_bindings",),
        ("profile_support_bindings", "*", "support_decision_ids"),
        ("members", "*", "alignment", "member_evidence_set_revision_ids"),
        ("members", "*", "alignment", "target_evidence_set_revision_ids"),
        ("join_graphs", "*", "alignment", "member_evidence_set_revision_ids"),
        ("join_graphs", "*", "alignment", "target_evidence_set_revision_ids"),
        ("refinement_graphs", "*", "alignment", "member_evidence_set_revision_ids"),
        ("refinement_graphs", "*", "alignment", "target_evidence_set_revision_ids"),
        ("limitations",),
        ("conflict_record_ids",),
    ),
    "materialization-recipe": (
        ("accepted_inputs", "evidence_kind_ids"),
        ("accepted_inputs", "graph_input_contracts"),
        ("accepted_inputs", "graph_input_contracts", "*", "record_kinds"),
        ("schema_object_descriptor_ids",),
        ("ontology_ids",),
        ("profile_adapter_bindings",),
        ("tool_ids",),
        ("derivation_steps", "*", "input_contracts"),
        ("derivation_steps", "*", "input_contracts", "*", "evidence_kind_ids"),
        ("derivation_steps", "*", "input_contracts", "*", "source_contracts"),
        ("derivation_steps", "*", "input_contracts", "*", "source_contracts", "*", "record_kinds"),
        ("derivation_steps", "*", "output_contracts"),
        ("derivation_steps", "*", "output_contracts", "*", "output_record_kinds"),
        ("output_contracts", "*", "subject_contracts"),
        ("full_build_conformance_case_ids",),
        ("incremental_equivalence_case_ids",),
    ),
    "dependency-manifest": (
        ("evidence_set_revision_ids",),
        ("input_graph_bindings",),
        ("input_components",),
        ("footprints", "*", "output_partitions"),
        ("footprints", "*", "evidence_record_ids"),
        ("footprints", "*", "admission_record_ids"),
        ("footprints", "*", "admission_partitions"),
        ("footprints", "*", "evidence_partitions"),
        ("footprints", "*", "source_graph_records"),
        ("footprints", "*", "source_graph_partitions"),
        ("footprints", "*", "component_keys"),
    ),
}


def _check_set_order(record: dict[str, Any], errors: list[ValidationDiagnostic]) -> None:
    for pattern in _SET_PATHS.get(record["kind"], ()):
        for value, parts in _walk_pattern(record, pattern):
            if type(value) is not list:
                continue
            if value != sorted(value, key=_canonical_set_key):
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.set-order",
                        _pointer(parts),
                        "schema-declared set must be sorted by canonical item bytes",
                    )
                )


def _expect(
    expectations: list[ReferenceExpectation],
    errors: list[ValidationDiagnostic],
    path: str,
    record_id: Any,
    expected_kinds: str | tuple[str, ...],
) -> None:
    if record_id is None:
        return
    kinds = (expected_kinds,) if type(expected_kinds) is str else expected_kinds
    actual_kind = _kind_of(record_id) if type(record_id) is str else None
    if actual_kind not in kinds:
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.reference-kind",
                path,
                f"reference must have target kind {' or '.join(kinds)}",
            )
        )
        return
    expectations.append(
        ReferenceExpectation(path=path, record_id=record_id, expected_kinds=kinds)
    )


def _expect_array(
    expectations: list[ReferenceExpectation],
    errors: list[ValidationDiagnostic],
    path: str,
    values: list[Any],
    expected_kinds: str | tuple[str, ...],
) -> None:
    for index, record_id in enumerate(values):
        _expect(expectations, errors, f"{path}/{index}", record_id, expected_kinds)


def _expect_kind_only(
    errors: list[ValidationDiagnostic],
    path: str,
    record_id: Any,
    expected_kind: str,
) -> None:
    """Check a C02-or-later reference kind without pretending to resolve it."""

    if _kind_of(record_id) != expected_kind:
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.reference-kind",
                path,
                f"reference must have target kind {expected_kind}",
            )
        )


def _check_root(
    record: dict[str, Any],
    field: str,
    domain: str,
    errors: list[ValidationDiagnostic],
) -> None:
    projection = dict(record)
    projection.pop("id", None)
    projection.pop(field, None)
    expected = semantic_root_v2(domain, projection)
    if record[field] != expected:
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.root-mismatch",
                f"/{field}",
                f"root must equal root_v2({domain!r}, the contract projection)",
            )
        )


def _check_coverage(
    coverage: dict[str, Any], path: str, errors: list[ValidationDiagnostic]
) -> None:
    if coverage["state"] == "complete" and coverage["omitted_at_least"] is not None:
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.coverage-complete-omission",
                f"{path}/omitted_at_least",
                "complete coverage cannot declare omitted members",
            )
        )


def _check_object_descriptor(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    if record["object_id"] != f"workbench-blob-v2:sha256:{record['sha256']}":
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.object-digest-agreement",
                "/object_id",
                "object_id digest suffix must equal sha256",
            )
        )
    representation = record["representation"]
    nullable_fields = (
        "described_schema_id",
        "described_schema_object_descriptor_id",
        "described_record_kind",
        "canonical_item_count",
        "total_order_policy_id",
        "total_order_authority",
        "minimum_record_key",
        "maximum_record_key",
    )
    if representation == "exact-bytes" and any(
        record[field] is not None for field in nullable_fields
    ):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.object-representation",
                "/representation",
                "exact-bytes metadata fields must all be null",
            )
        )
    if representation == "canonical-json-record":
        required = nullable_fields[:4]
        if any(record[field] is None for field in required) or record["canonical_item_count"] != 1:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.object-representation",
                    "/representation",
                    "canonical-json-record must bind schema, kind, and item count 1",
                )
            )
    if representation == "canonical-json-value":
        if (
            record["described_schema_id"] is None
            or record["described_schema_object_descriptor_id"] is None
            or record["described_record_kind"] is not None
            or record["canonical_item_count"] != 1
            or any(record[field] is not None for field in nullable_fields[4:])
        ):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.object-representation",
                    "/representation",
                    "canonical-json-value must bind one schema-validated value without record or order metadata",
                )
            )
    if representation == "canonical-ndjson-shard":
        if any(record[field] is None for field in nullable_fields):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.object-representation",
                    "/representation",
                    "canonical-ndjson-shard metadata fields must all be non-null",
                )
            )
        if record["minimum_record_key"] > record["maximum_record_key"]:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.object-key-bounds",
                    "/minimum_record_key",
                    "minimum_record_key must not exceed maximum_record_key",
                )
            )
    _expect(
        refs,
        errors,
        "/described_schema_object_descriptor_id",
        record["described_schema_object_descriptor_id"],
        "object-descriptor",
    )


def _check_evidence(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    bindings = record["source_bindings"]
    binding_key = lambda item: (
        item["role"].encode("utf-8"),
        item["object_descriptor_id"].encode("ascii"),
        canonical_json_bytes(item["locator"]),
    )
    if bindings != sorted(bindings, key=binding_key):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.source-binding-order",
                "/source_bindings",
                "source bindings must be sorted by role, descriptor ID, and locator bytes",
            )
        )
    for index, binding in enumerate(bindings):
        _expect(
            refs,
            errors,
            f"/source_bindings/{index}/object_descriptor_id",
            binding["object_descriptor_id"],
            "object-descriptor",
        )
        locator = binding["locator"]
        if locator["kind"] == "coordinate-range":
            for axis, (minimum, maximum) in enumerate(
                zip(locator["minimum"], locator["maximum"], strict=True)
            ):
                if minimum > maximum:
                    errors.append(
                        _diagnostic(
                            ValidationPhase.SEMANTIC,
                            "semantic.coordinate-bounds",
                            f"/source_bindings/{index}/locator/minimum/{axis}",
                            "minimum coordinate must not exceed maximum coordinate",
                        )
                    )
    _expect(refs, errors, "/payload_object_descriptor_id", record["payload_object_descriptor_id"], "object-descriptor")
    capture = record["capture_condition"]
    _check_coverage(capture["coverage"], "/capture_condition/coverage", errors)
    _expect(
        refs,
        errors,
        "/capture_condition/coverage/coverage_object_descriptor_id",
        capture["coverage"]["coverage_object_descriptor_id"],
        "object-descriptor",
    )
    if capture["completion"] == "complete" and (
        capture["loss"] != "none" or capture["truncated"]
    ):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.capture-complete",
                "/capture_condition",
                "complete capture requires no loss and truncated=false",
            )
        )
    if record["evidence_state"] == "truncated" and (
        not capture["truncated"] or capture["coverage"]["state"] != "truncated"
    ):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.capture-truncated",
                "/evidence_state",
                "truncated evidence requires a truncated capture and coverage state",
            )
        )
    _expect_array(refs, errors, "/corrects_record_ids", record["corrects_record_ids"], "evidence-record")
    _expect_array(refs, errors, "/supersedes_record_ids", record["supersedes_record_ids"], "evidence-record")
    for index, binding in enumerate(record["legacy_bindings"]):
        for field in (
            "artifact_object_descriptor_id",
            "mapping_manifest_object_descriptor_id",
        ):
            _expect(refs, errors, f"/legacy_bindings/{index}/{field}", binding[field], "object-descriptor")
    _expect(
        refs,
        errors,
        "/migration_manifest_object_descriptor_id",
        record["migration_manifest_object_descriptor_id"],
        "object-descriptor",
    )


def _check_admission(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect(refs, errors, "/candidate_record_id", record["candidate_record_id"], record["candidate_kind"])
    _expect_array(refs, errors, "/evaluated_schema_object_descriptor_ids", record["evaluated_schema_object_descriptor_ids"], "object-descriptor")
    _expect(refs, errors, "/prior_admission_record_id", record["prior_admission_record_id"], "admission-record")
    _expect(refs, errors, "/validation_result_object_descriptor_id", record["validation_result_object_descriptor_id"], "object-descriptor")
    for index, item in enumerate(record["diagnostics"]):
        if item["ordinal"] != index:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.diagnostic-ordinal",
                    f"/diagnostics/{index}/ordinal",
                    "diagnostic ordinals must be contiguous from zero",
                )
            )
    if record["outcome"] == "admitted" and any(
        item["severity"] == "error" for item in record["diagnostics"]
    ):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.admitted-error",
                "/diagnostics",
                "an admitted decision cannot retain an error diagnostic",
            )
        )


def _check_ledger_entry(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect(
        refs,
        errors,
        "/previous_entry_id",
        record["previous_entry_id"],
        "ledger-entry",
    )
    _expect(
        refs,
        errors,
        "/entry_record_id",
        record["entry_record_id"],
        record["entry_record_kind"],
    )


def _check_evidence_set(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect_array(refs, errors, "/parent_evidence_set_revision_ids", record["parent_evidence_set_revision_ids"], "evidence-set-revision")
    heads = record["ledger_heads"]
    if heads != sorted(heads, key=lambda item: item["ledger_namespace"].encode("utf-8")):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.ledger-order", "/ledger_heads", "ledger heads must be ordered by namespace"))
    namespaces = [item["ledger_namespace"] for item in heads]
    if len(namespaces) != len(set(namespaces)):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.ledger-namespace", "/ledger_heads", "ledger namespaces must be unique"))
    for index, head in enumerate(heads):
        _expect(
            refs,
            errors,
            f"/ledger_heads/{index}/head_record_id",
            head["head_record_id"],
            "ledger-entry",
        )
    for label, count_field, root_field, domain in (
        (
            "admission_partitions",
            "effective_admission_count",
            "effective_admission_root",
            "evidence-set-revision/effective-admissions",
        ),
        (
            "evidence_partitions",
            "effective_evidence_count",
            "effective_evidence_root",
            "evidence-set-revision/effective-evidence",
        ),
    ):
        partitions = record[label]
        key = lambda item: (
            item["partition_key"].encode("utf-8"), item["shard_ordinal"]
        )
        if partitions != sorted(partitions, key=key):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.partition-order",
                    f"/{label}",
                    "membership partitions must be ordered by key and shard ordinal",
                )
            )
        logical_keys = [
            (item["partition_key"], item["shard_ordinal"])
            for item in partitions
        ]
        if len(logical_keys) != len(set(logical_keys)):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.partition-key",
                    f"/{label}",
                    "membership partition keys and ordinals must be unique",
                )
            )
        groups: dict[str, list[int]] = {}
        for index, item in enumerate(partitions):
            groups.setdefault(item["partition_key"], []).append(item["shard_ordinal"])
            _expect(
                refs,
                errors,
                f"/{label}/{index}/object_descriptor_id",
                item["object_descriptor_id"],
                "object-descriptor",
            )
            if item["minimum_record_key"] > item["maximum_record_key"]:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.partition-key-bounds",
                        f"/{label}/{index}/minimum_record_key",
                        "minimum record ID must not exceed maximum record ID",
                    )
                )
        if any(ordinals != list(range(len(ordinals))) for ordinals in groups.values()):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.shard-ordinal",
                    f"/{label}",
                    "membership shard ordinals must be contiguous from zero per key",
                )
            )
        total = sum(item["record_count"] for item in partitions)
        if total != record[count_field]:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.membership-count",
                    f"/{count_field}",
                    "effective count must equal the sum of membership partitions",
                )
            )
        summaries = [
            {
                "partition_key": item["partition_key"],
                "shard_ordinal": item["shard_ordinal"],
                "record_count": item["record_count"],
                "semantic_root": item["semantic_root"],
            }
            for item in partitions
        ]
        expected_root = None if not summaries else semantic_root_v2(domain, summaries)
        if record[root_field] != expected_root:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.membership-root",
                    f"/{root_field}",
                    "effective membership root does not match ordered partition summaries",
                )
            )
    _expect(
        refs,
        errors,
        "/evidence_frontier_object_descriptor_id",
        record["evidence_frontier_object_descriptor_id"],
        "object-descriptor",
    )
    _check_coverage(record["coverage"], "/coverage", errors)
    _expect(
        refs,
        errors,
        "/coverage/coverage_object_descriptor_id",
        record["coverage"]["coverage_object_descriptor_id"],
        "object-descriptor",
    )
    _check_root(record, "semantic_root", "evidence-set-revision/aggregate", errors)


def _check_graph_record(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect(refs, errors, "/recipe_id", record["recipe_id"], "materialization-recipe")
    evidence_ids = [item["evidence_record_id"] for item in record["evidence_inputs"]]
    source_ids = [item["graph_record_id"] for item in record["source_graph_inputs"]]
    for index, item in enumerate(record["evidence_inputs"]):
        _expect(
            refs,
            errors,
            f"/evidence_inputs/{index}/evidence_record_id",
            item["evidence_record_id"],
            "evidence-record",
        )
    for index, item in enumerate(record["source_graph_inputs"]):
        _expect(
            refs,
            errors,
            f"/source_graph_inputs/{index}/graph_record_id",
            item["graph_record_id"],
            "graph-record",
        )
    _expect(refs, errors, "/qualifiers_object_descriptor_id", record["qualifiers_object_descriptor_id"], "object-descriptor")
    body = record["body"]
    body_kind = body["record_kind"]
    if body_kind == "node":
        _expect(refs, errors, "/body/subject_identity_object_descriptor_id", body["subject_identity_object_descriptor_id"], "object-descriptor")
    elif body_kind == "edge":
        for field in ("source_node_record_id", "target_node_record_id"):
            _expect(
                refs,
                errors,
                f"/body/{field}",
                body[field],
                "graph-record",
            )
            if body[field] not in source_ids:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.endpoint-source-closure",
                        f"/body/{field}",
                        "edge endpoint node must occur in flattened source_graph_inputs",
                    )
                )
    elif body_kind == "property":
        _expect(
            refs,
            errors,
            "/body/subject_node_record_id",
            body["subject_node_record_id"],
            "graph-record",
        )
        if body["subject_node_record_id"] not in source_ids:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.property-source-closure",
                    "/body/subject_node_record_id",
                    "property subject node must occur in flattened source_graph_inputs",
                )
            )
        _expect(refs, errors, "/body/value_schema_object_descriptor_id", body["value_schema_object_descriptor_id"], "object-descriptor")
        _expect(refs, errors, "/body/value_object_descriptor_id", body["value_object_descriptor_id"], "object-descriptor")
    elif body_kind == "evidence-link":
        _expect(refs, errors, "/body/supported_graph_record_id", body["supported_graph_record_id"], "graph-record")
        _expect(refs, errors, "/body/supporting_evidence_record_id", body["supporting_evidence_record_id"], "evidence-record")
        if body["supported_graph_record_id"] not in source_ids:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.evidence-link-source-closure",
                    "/body/supported_graph_record_id",
                    "supported graph record must occur in flattened source_graph_inputs",
                )
            )
        if body["supporting_evidence_record_id"] not in evidence_ids:
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.evidence-link-closure", "/body/supporting_evidence_record_id", "supporting evidence must occur in the envelope evidence closure"))
    elif body_kind == "refinement-mapping":
        endpoints = (body["coarse_endpoint"], *body["fine_endpoints"])
        for index, endpoint in enumerate(endpoints):
            endpoint_path = (
                "/body/coarse_endpoint"
                if index == 0
                else f"/body/fine_endpoints/{index - 1}"
            )
            source_id = endpoint["source_node_record_id"]
            _expect(
                refs,
                errors,
                f"{endpoint_path}/source_graph_revision_id",
                endpoint["source_graph_revision_id"],
                "graph-revision",
            )
            _expect(
                refs,
                errors,
                f"{endpoint_path}/source_node_record_id",
                source_id,
                "graph-record",
            )
            if source_id not in source_ids:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.refinement-source-closure",
                        f"{endpoint_path}/source_node_record_id",
                        "refinement node must occur in flattened source_graph_inputs",
                    )
                )
            if (
                index > 0
                and endpoint["resolution_id"]
                == body["coarse_endpoint"]["resolution_id"]
            ):
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.refinement-resolution",
                        f"{endpoint_path}/resolution_id",
                        "fine endpoint resolution must differ from the coarse endpoint resolution",
                    )
                )
        _expect_array(refs, errors, "/body/omitted_conflict_record_ids", body["omitted_conflict_record_ids"], "graph-record")
        if not set(body["omitted_conflict_record_ids"]).issubset(source_ids):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.refinement-conflict-closure",
                    "/body/omitted_conflict_record_ids",
                    "omitted conflicts must occur in flattened source_graph_inputs",
                )
            )
        _expect(refs, errors, "/body/summary_object_descriptor_id", body["summary_object_descriptor_id"], "object-descriptor")
    elif body_kind == "frontier":
        _expect(refs, errors, "/body/continuation_object_descriptor_id", body["continuation_object_descriptor_id"], "object-descriptor")
        if body["state"] in ("bounded", "truncated") and body["omitted_at_least"] is None:
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.frontier-omission", "/body/omitted_at_least", "bounded or truncated frontier requires omitted_at_least >= 1"))
    elif body_kind == "conflict":
        _expect_array(refs, errors, "/body/conflicting_graph_record_ids", body["conflicting_graph_record_ids"], "graph-record")
        if not set(body["conflicting_graph_record_ids"]).issubset(source_ids):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.conflict-source-closure",
                    "/body/conflicting_graph_record_ids",
                    "conflicting graph records must occur in flattened source_graph_inputs",
                )
            )


def _check_graph_revision(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect(refs, errors, "/recipe_id", record["recipe_id"], "materialization-recipe")
    _expect(refs, errors, "/parameter_values_object_descriptor_id", record["parameter_values_object_descriptor_id"], "object-descriptor")
    _expect_array(refs, errors, "/record_schema_object_descriptor_ids", record["record_schema_object_descriptor_ids"], "object-descriptor")
    _expect_array(refs, errors, "/evidence_set_revision_ids", record["evidence_set_revision_ids"], "evidence-set-revision")
    input_contract_keys: list[str] = []
    for index, binding in enumerate(record["input_graph_bindings"]):
        input_contract_keys.append(binding["graph_input_contract_key"])
        _expect(
            refs,
            errors,
            f"/input_graph_bindings/{index}/graph_revision_id",
            binding["graph_revision_id"],
            "graph-revision",
        )
    if len(input_contract_keys) != len(set(input_contract_keys)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.graph-input-contract-key",
                "/input_graph_bindings",
                "graph input contract keys must be unique",
            )
        )
    _expect_array(refs, errors, "/parent_graph_revision_ids", record["parent_graph_revision_ids"], "graph-revision")
    _expect(refs, errors, "/dependency_manifest_id", record["dependency_manifest_id"], "dependency-manifest")
    _expect_array(refs, errors, "/frontier_record_ids", record["frontier_record_ids"], "graph-record")
    _expect_array(refs, errors, "/conflict_record_ids", record["conflict_record_ids"], "graph-record")
    partitions = record["partitions"]
    key = lambda item: (
        item["partition_key"].encode("utf-8"),
        _GRAPH_RECORD_KIND_ORDER[item["record_kind"]],
        item["shard_ordinal"],
    )
    if partitions != sorted(partitions, key=key):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.partition-order", "/partitions", "graph partitions must be ordered by key, record kind, and shard ordinal"))
    logical_keys = [(item["partition_key"], item["record_kind"], item["shard_ordinal"]) for item in partitions]
    if len(logical_keys) != len(set(logical_keys)):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.partition-key", "/partitions", "graph partition keys, kinds, and ordinals must be unique"))
    ordinal_groups: dict[tuple[str, str], list[int]] = {}
    for item in partitions:
        ordinal_groups.setdefault(
            (item["partition_key"], item["record_kind"]), []
        ).append(item["shard_ordinal"])
    for ordinals in ordinal_groups.values():
        if ordinals != list(range(len(ordinals))):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.shard-ordinal",
                    "/partitions",
                    "shard ordinals must be contiguous from zero per partition key and record kind",
                )
            )
            break
    totals = {field: 0 for field in _COUNT_FIELD_BY_RECORD_KIND.values()}
    for index, item in enumerate(partitions):
        _expect(refs, errors, f"/partitions/{index}/object_descriptor_id", item["object_descriptor_id"], "object-descriptor")
        if item["minimum_record_key"] > item["maximum_record_key"]:
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.partition-key-bounds", f"/partitions/{index}/minimum_record_key", "minimum record key must not exceed maximum record key"))
        totals[_COUNT_FIELD_BY_RECORD_KIND[item["record_kind"]]] += item["record_count"]
    for field, count in totals.items():
        if record["record_counts"][field] != count:
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.record-count", f"/record_counts/{field}", "record count must equal the sum of matching partitions"))
        root = record["record_roots"][field]
        if (count == 0) != (root is None):
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.record-root-nullability", f"/record_roots/{field}", "record root must be null exactly when its count is zero"))
    _check_coverage(record["coverage"], "/coverage", errors)
    _expect(
        refs,
        errors,
        "/coverage/coverage_object_descriptor_id",
        record["coverage"]["coverage_object_descriptor_id"],
        "object-descriptor",
    )
    _check_root(record, "aggregate_semantic_root", "graph-revision/aggregate", errors)


def _check_graph_set(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect_array(refs, errors, "/evidence_set_revision_ids", record["evidence_set_revision_ids"], "evidence-set-revision")
    _expect(refs, errors, "/compatibility_constraints_object_descriptor_id", record["compatibility_constraints_object_descriptor_id"], "object-descriptor")
    for collection_name in ("members", "join_graphs", "refinement_graphs"):
        graph_ids: list[str] = []
        for index, member in enumerate(record[collection_name]):
            graph_ids.append(member["graph_revision_id"])
            _expect(
                refs,
                errors,
                f"/{collection_name}/{index}/graph_revision_id",
                member["graph_revision_id"],
                "graph-revision",
            )
            alignment = member["alignment"]
            _expect(
                refs,
                errors,
                f"/{collection_name}/{index}/alignment/compatibility_constraints_object_descriptor_id",
                alignment["compatibility_constraints_object_descriptor_id"],
                "object-descriptor",
            )
            _expect_array(
                refs,
                errors,
                f"/{collection_name}/{index}/alignment/member_evidence_set_revision_ids",
                alignment["member_evidence_set_revision_ids"],
                "evidence-set-revision",
            )
            _expect_array(
                refs,
                errors,
                f"/{collection_name}/{index}/alignment/target_evidence_set_revision_ids",
                alignment["target_evidence_set_revision_ids"],
                "evidence-set-revision",
            )
        if len(graph_ids) != len(set(graph_ids)):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.graph-member-id",
                    f"/{collection_name}",
                    "graph revision IDs must be unique within the ordered collection",
                )
            )
    _expect_array(refs, errors, "/conflict_record_ids", record["conflict_record_ids"], "graph-record")
    member_keys = [(item["category_id"], item["resolution_id"]) for item in record["members"]]
    if len(member_keys) != len(set(member_keys)):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.graph-member-key", "/members", "member category and resolution pairs must be unique"))
    _check_coverage(record["coverage"], "/coverage", errors)
    _expect(
        refs,
        errors,
        "/coverage/coverage_object_descriptor_id",
        record["coverage"]["coverage_object_descriptor_id"],
        "object-descriptor",
    )
    _check_root(record, "aggregate_semantic_root", "graph-set-revision/aggregate", errors)


def _check_recipe(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    steps = record["derivation_steps"]
    step_ids = [item["derivation_step_id"] for item in steps]
    step_keys = [item["step_key"] for item in steps]
    if len(step_ids) != len(set(step_ids)) or len(step_keys) != len(set(step_keys)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.derivation-step-identity",
                "/derivation_steps",
                "derivation step IDs and step keys must each be unique",
            )
        )
    for index, step in enumerate(steps):
        body = dict(step)
        body.pop("derivation_step_id")
        expected_id = content_id("derivation-step", body)
        if step["derivation_step_id"] != expected_id:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-step-id",
                    f"/derivation_steps/{index}/derivation_step_id",
                    "derivation_step_id must equal the V2 ID of the exact closed step body",
                )
            )

    by_id = {item["derivation_step_id"]: item for item in steps}
    top_outputs_by_key = {
        item["output_contract_key"]: item for item in record["output_contracts"]
    }
    accepted_graph_inputs = record["accepted_inputs"]["graph_input_contracts"]
    accepted_graph_by_key = {
        item["graph_input_contract_key"]: item for item in accepted_graph_inputs
    }
    accepted_graph_keys = [
        item["graph_input_contract_key"] for item in accepted_graph_inputs
    ]
    if len(accepted_graph_keys) != len(set(accepted_graph_keys)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.accepted-graph-input-key",
                "/accepted_inputs/graph_input_contracts",
                "accepted graph input contract keys must be unique",
            )
        )
    def predecessor_ids(step: dict[str, Any]) -> set[str]:
        return {
            source["predecessor_step_id"]
            for input_contract in step["input_contracts"]
            for source in input_contract["source_contracts"]
            if source["source_kind"] == "predecessor-output"
            and type(source["predecessor_step_id"]) is str
        }

    predecessors_by_id = {
        step["derivation_step_id"]: predecessor_ids(step) for step in steps
    }
    for index, step in enumerate(steps):
        predecessors = predecessors_by_id[step["derivation_step_id"]]
        if any(item not in by_id for item in predecessors):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-predecessor",
                    f"/derivation_steps/{index}/input_contracts",
                    "every predecessor-output source must name a step in the exact recipe",
                )
            )
        if step["derivation_step_id"] in predecessors:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-self-edge",
                    f"/derivation_steps/{index}/input_contracts",
                    "a derivation step cannot consume its own output as a predecessor",
                )
            )

    processed: set[str] = set()
    remaining = list(steps)
    for index, actual in enumerate(steps):
        ready = [
            item
            for item in remaining
            if predecessors_by_id[item["derivation_step_id"]].issubset(processed)
        ]
        if not ready:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-cycle",
                    "/derivation_steps",
                    "derivation steps must form an acyclic topological order",
                )
            )
            break
        expected = min(ready, key=lambda item: item["step_key"].encode("utf-8"))
        if actual["derivation_step_id"] != expected["derivation_step_id"]:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-topological-order",
                    f"/derivation_steps/{index}",
                    "concurrently ready derivation steps must be ordered by step_key UTF-8 bytes",
                )
            )
            break
        processed.add(actual["derivation_step_id"])
        remaining.remove(actual)

    step_outputs_by_id = {
        step["derivation_step_id"]: {
            output["output_contract_key"]: output
            for output in step["output_contracts"]
        }
        for step in steps
    }
    producer_by_output: dict[str, str] = {}
    for index, step in enumerate(steps):
        input_keys = [
            item["input_contract_key"] for item in step["input_contracts"]
        ]
        if len(input_keys) != len(set(input_keys)):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-input-key",
                    f"/derivation_steps/{index}/input_contracts",
                    "derivation input contract keys must be unique per step",
                )
            )
        step_output_keys = [
            item["output_contract_key"] for item in step["output_contracts"]
        ]
        if len(step_output_keys) != len(set(step_output_keys)):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.derivation-output-key",
                    f"/derivation_steps/{index}/output_contracts",
                    "derivation output contract keys must be unique per step",
                )
            )
        for output_index, step_output in enumerate(step["output_contracts"]):
            output_key = step_output["output_contract_key"]
            if output_key not in top_outputs_by_key:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.derivation-output-contract",
                        f"/derivation_steps/{index}/output_contracts/{output_index}/output_contract_key",
                        "step output must name an exact recipe output contract",
                    )
                )
            if output_key in producer_by_output:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.derivation-output-producer",
                        f"/derivation_steps/{index}/output_contracts",
                        "each recipe output contract must have exactly one producing step",
                    )
                )
            producer_by_output[output_key] = step["derivation_step_id"]
        for input_index, input_contract in enumerate(step["input_contracts"]):
            input_path = f"/derivation_steps/{index}/input_contracts/{input_index}"
            if input_contract["input_mode"] == "evidence":
                if not set(input_contract["evidence_kind_ids"]).issubset(
                    record["accepted_inputs"]["evidence_kind_ids"]
                ):
                    errors.append(
                        _diagnostic(
                            ValidationPhase.SEMANTIC,
                            "semantic.derivation-evidence-contract",
                            input_path,
                            "step evidence kinds must be accepted recipe inputs",
                        )
                    )
                continue

            source_identities = [
                (
                    source["source_kind"],
                    source["contract_key"],
                    source["predecessor_step_id"],
                )
                for source in input_contract["source_contracts"]
            ]
            if len(source_identities) != len(set(source_identities)):
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.derivation-source-identity",
                        f"{input_path}/source_contracts",
                        "source contract kind, key, and predecessor identity must be unique per input",
                    )
                )
            for source_index, source in enumerate(input_contract["source_contracts"]):
                source_path = f"{input_path}/source_contracts/{source_index}"
                source_kind = source["source_kind"]
                contract_key = source["contract_key"]
                declared_kinds: set[str] | None = None
                if source_kind == "accepted-graph-input":
                    accepted = accepted_graph_by_key.get(contract_key)
                    if accepted is not None:
                        declared_kinds = set(accepted["record_kinds"])
                elif source_kind == "local-output":
                    local = step_outputs_by_id[step["derivation_step_id"]].get(
                        contract_key
                    )
                    if local is not None:
                        declared_kinds = set(local["output_record_kinds"])
                else:
                    predecessor_id = source["predecessor_step_id"]
                    predecessor_outputs = step_outputs_by_id.get(predecessor_id, {})
                    predecessor = predecessor_outputs.get(contract_key)
                    if predecessor is not None:
                        declared_kinds = set(predecessor["output_record_kinds"])
                    if predecessor_id not in processed and predecessor_id not in by_id:
                        # The missing-reference diagnostic above is the primary
                        # error; retain a precise edge error at this source.
                        declared_kinds = None
                if declared_kinds is None:
                    errors.append(
                        _diagnostic(
                            ValidationPhase.SEMANTIC,
                            "semantic.derivation-source-edge",
                            source_path,
                            "source contract key must name the exact accepted, predecessor, or local output contract",
                        )
                    )
                    continue
                if not set(source["record_kinds"]).issubset(declared_kinds):
                    errors.append(
                        _diagnostic(
                            ValidationPhase.SEMANTIC,
                            "semantic.derivation-source-kind",
                            source_path,
                            "source record kinds must be a subset of the named contract",
                        )
                    )
    if set(producer_by_output) != set(top_outputs_by_key):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.derivation-output-coverage",
                "/derivation_steps",
                "derivation steps must produce every recipe output contract exactly once",
            )
        )

    descriptor_fields = (
        ("/implementation/executable_object_descriptor_id", record["implementation"]["executable_object_descriptor_id"]),
        ("/implementation/source_tree_object_descriptor_id", record["implementation"]["source_tree_object_descriptor_id"]),
        ("/implementation/dependency_lock_object_descriptor_id", record["implementation"]["dependency_lock_object_descriptor_id"]),
        ("/implementation/runtime_environment_object_descriptor_id", record["implementation"]["runtime_environment_object_descriptor_id"]),
        ("/input_selector_object_descriptor_id", record["input_selector_object_descriptor_id"]),
        ("/parameter_schema_object_descriptor_id", record["parameter_schema_object_descriptor_id"]),
        ("/canonical_parameter_values_object_descriptor_id", record["canonical_parameter_values_object_descriptor_id"]),
        ("/resource_limits_object_descriptor_id", record["resource_limits_object_descriptor_id"]),
    )
    for path, value in descriptor_fields:
        _expect(refs, errors, path, value, "object-descriptor")
    _expect_array(refs, errors, "/schema_object_descriptor_ids", record["schema_object_descriptor_ids"], "object-descriptor")
    contract_keys: list[tuple[str, str, str]] = []
    output_contract_keys: list[str] = []
    for index, output in enumerate(record["output_contracts"]):
        output_contract_keys.append(output["output_contract_key"])
        contract_keys.append((output["graph_family_id"], output["category_id"], output["resolution_id"]))
        _expect(refs, errors, f"/output_contracts/{index}/graph_record_schema_object_descriptor_id", output["graph_record_schema_object_descriptor_id"], "object-descriptor")
        node_kinds = [item["node_kind"] for item in output["subject_contracts"]]
        if len(node_kinds) != len(set(node_kinds)):
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.subject-contract-kind", f"/output_contracts/{index}/subject_contracts", "subject contracts must have unique node_kind values"))
        for subject_index, subject in enumerate(output["subject_contracts"]):
            _expect(refs, errors, f"/output_contracts/{index}/subject_contracts/{subject_index}/subject_identity_schema_object_descriptor_id", subject["subject_identity_schema_object_descriptor_id"], "object-descriptor")
    if len(contract_keys) != len(set(contract_keys)):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.output-contract-key", "/output_contracts", "output family, category, and resolution tuples must be unique"))
    if len(output_contract_keys) != len(set(output_contract_keys)):
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.output-contract-identity", "/output_contracts", "output_contract_key values must be unique"))


def _check_dependency(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect(refs, errors, "/recipe_id", record["recipe_id"], "materialization-recipe")
    _expect_array(refs, errors, "/evidence_set_revision_ids", record["evidence_set_revision_ids"], "evidence-set-revision")
    input_contract_keys: list[str] = []
    for index, binding in enumerate(record["input_graph_bindings"]):
        input_contract_keys.append(binding["graph_input_contract_key"])
        _expect(
            refs,
            errors,
            f"/input_graph_bindings/{index}/graph_revision_id",
            binding["graph_revision_id"],
            "graph-revision",
        )
    if len(input_contract_keys) != len(set(input_contract_keys)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.graph-input-contract-key",
                "/input_graph_bindings",
                "graph input contract keys must be unique",
            )
        )
    graph_revision_by_contract = {
        item["graph_input_contract_key"]: item["graph_revision_id"]
        for item in record["input_graph_bindings"]
    }
    _expect(refs, errors, "/parameter_values_object_descriptor_id", record["parameter_values_object_descriptor_id"], "object-descriptor")
    _expect(refs, errors, "/parent_dependency_manifest_id", record["parent_dependency_manifest_id"], "dependency-manifest")
    component_keys = [item["component_key"] for item in record["input_components"]]
    if len(component_keys) != len(set(component_keys)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.dependency-component-key",
                "/input_components",
                "dependency component keys must be unique",
            )
        )
    for index, component in enumerate(record["input_components"]):
        if _kind_of(component["component_id"]) != component["component_kind"]:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.dependency-component-kind",
                    f"/input_components/{index}/component_id",
                    "dependency component ID must have the declared component kind",
                )
            )
    output_partitions: list[tuple[str, int, str]] = []
    used_component_keys: set[str] = set()
    for index, footprint in enumerate(record["footprints"]):
        output_partitions.extend(
            (
                item["partition_key"],
                item["shard_ordinal"],
                item["record_kind"],
            )
            for item in footprint["output_partitions"]
        )
        used_component_keys.update(footprint["component_keys"])
        _expect_array(refs, errors, f"/footprints/{index}/evidence_record_ids", footprint["evidence_record_ids"], "evidence-record")
        _expect_array(refs, errors, f"/footprints/{index}/admission_record_ids", footprint["admission_record_ids"], "admission-record")
        for record_index, source in enumerate(footprint["source_graph_records"]):
            _expect(
                refs,
                errors,
                f"/footprints/{index}/source_graph_records/{record_index}/graph_record_id",
                source["graph_record_id"],
                "graph-record",
            )
            if source["graph_input_contract_key"] not in graph_revision_by_contract:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.dependency-graph-contract-key",
                        f"/footprints/{index}/source_graph_records/{record_index}/graph_input_contract_key",
                        "source graph record must name a declared graph input contract",
                    )
                )
        for part_index, part in enumerate(footprint["evidence_partitions"]):
            _expect(refs, errors, f"/footprints/{index}/evidence_partitions/{part_index}/evidence_set_revision_id", part["evidence_set_revision_id"], "evidence-set-revision")
        for part_index, part in enumerate(footprint["admission_partitions"]):
            _expect(refs, errors, f"/footprints/{index}/admission_partitions/{part_index}/evidence_set_revision_id", part["evidence_set_revision_id"], "evidence-set-revision")
        for part_index, part in enumerate(footprint["source_graph_partitions"]):
            _expect(refs, errors, f"/footprints/{index}/source_graph_partitions/{part_index}/graph_revision_id", part["graph_revision_id"], "graph-revision")
            if graph_revision_by_contract.get(
                part["graph_input_contract_key"]
            ) != part["graph_revision_id"]:
                errors.append(
                    _diagnostic(
                        ValidationPhase.SEMANTIC,
                        "semantic.dependency-graph-contract-binding",
                        f"/footprints/{index}/source_graph_partitions/{part_index}",
                        "source graph partition contract key must bind its exact declared graph revision",
                    )
                )
        projection = dict(footprint)
        projection.pop("footprint_root")
        expected = semantic_root_v2("dependency-manifest/footprint", projection)
        if footprint["footprint_root"] != expected:
            errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.root-mismatch", f"/footprints/{index}/footprint_root", "footprint_root does not match its contract projection"))
    if len(output_partitions) != len(set(output_partitions)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.footprint-partition",
                "/footprints",
                "each exact output physical partition may occur in only one footprint",
            )
        )
    if record["footprints"] and used_component_keys != set(component_keys):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.dependency-component-closure",
                "/footprints",
                "footprints must reference exactly the declared dependency component keys",
            )
        )
    _check_root(record, "semantic_root", "dependency-manifest/aggregate", errors)


def _check_reference_event(
    record: dict[str, Any],
    errors: list[ValidationDiagnostic],
    refs: list[ReferenceExpectation],
) -> None:
    _expect_kind_only(errors, "/context_ref_id", record["context_ref_id"], "context-ref")
    _expect(refs, errors, "/prior_event_id", record["prior_event_id"], "reference-event")
    _expect(refs, errors, "/new_target_id", record["new_target_id"], record["new_target_kind"])


def _collect_remaining_references(
    record: dict[str, Any],
    refs: list[ReferenceExpectation],
) -> None:
    """Merge references compiled from normative resolved-schema annotations."""

    existing = {(item.path, item.record_id) for item in refs}
    for annotated in annotated_record_references(record):
        if annotated.reference_class in {"embedded", "subject-identity"}:
            continue
        if (annotated.path, annotated.record_id) in existing:
            continue
        refs.append(
            ReferenceExpectation(
                path=annotated.path,
                record_id=annotated.record_id,
                expected_kinds=annotated.expected_kinds,
            )
        )
        existing.add((annotated.path, annotated.record_id))


def _scope_from_binding(value: Any) -> AuthorityScope | None:
    if type(value) is not dict:
        return None
    fields = (
        value.get("owner_authority_id"),
        value.get("owner_revision_id"),
        value.get("authority_adapter_id"),
    )
    if not all(type(item) is str for item in fields):
        return None
    return AuthorityScope(
        owner_authority_id=cast(str, fields[0]),
        owner_revision_id=cast(str, fields[1]),
        authority_adapter_id=cast(str, fields[2]),
    )


def _primary_authority_scope(record: dict[str, Any]) -> AuthorityScope | None:
    binding_by_kind = {
        "object-descriptor": "total_order_authority",
        "evidence-record": "authority_owner",
        "admission-record": "decision_owner",
        "evidence-set-revision": "authority_owner",
        "graph-record": "authority_owner",
        "graph-revision": "authority_owner",
        "graph-set-revision": "composition_owner",
        "materialization-recipe": "authority_owner",
        "dependency-manifest": "authority_owner",
        "reference-event": "authorization_authority",
    }
    field = binding_by_kind.get(record.get("kind"))
    return None if field is None else _scope_from_binding(record.get(field))


def _scope_for_reference(
    record: dict[str, Any], expectation: ReferenceExpectation
) -> AuthorityScope | None:
    """Select only an explicitly declared authority scope for a reference.

    Context, component, and implementation records remain structural external
    references.  They are validated by the application's pinned external
    registry and are not silently re-authorized by an unrelated domain owner.
    """

    parts = tuple(
        item.replace("~1", "/").replace("~0", "~")
        for item in expectation.path.lstrip("/").split("/")
        if item
    )
    actual_kind = _kind_of(expectation.record_id)
    if record.get("kind") == "graph-set-revision" and (
        (parts == ("compatibility_rule_id",))
        or ("alignment" in parts and parts[-1:] == ("compatibility_rule_id",))
    ):
        return _scope_from_binding(record.get("compatibility_rule_authority"))
    if (
        "alignment" in parts
        and parts
        and (
            actual_kind in _C01_RECORD_KINDS
            or actual_kind in _AUTHORITY_SCOPED_EXTERNAL_KINDS
        )
    ):
        try:
            collection = record[parts[0]]
            member = collection[int(parts[1])]
            scope = _scope_from_binding(member["alignment"]["decision_authority"])
        except (KeyError, IndexError, TypeError, ValueError):
            scope = None
        if scope is not None:
            return scope

    if parts and parts[0] in {
        "profile_adapter_bindings",
        "profile_support_bindings",
    }:
        try:
            binding = record[parts[0]][int(parts[1])]
            scope = _scope_from_binding(binding["profile_authority"])
        except (KeyError, IndexError, TypeError, ValueError):
            scope = None
        if scope is not None:
            return scope

    if parts and parts[0] == "legacy_bindings" and "profile_adapter_binding" in parts:
        try:
            binding = record["legacy_bindings"][int(parts[1])][
                "profile_adapter_binding"
            ]
            scope = _scope_from_binding(binding["profile_authority"])
        except (KeyError, IndexError, TypeError, ValueError):
            scope = None
        if scope is not None:
            return scope

    if (
        record.get("kind") == "dependency-manifest"
        and parts
        and parts[0] == "input_components"
    ):
        try:
            component = record["input_components"][int(parts[1])]
            scope = _scope_from_binding(component["authority_binding"])
        except (KeyError, IndexError, TypeError, ValueError):
            scope = None
        return scope

    for binding_field in (
        "authority_owner",
        "decision_owner",
        "composition_owner",
        "recipe_owner",
        "total_order_authority",
        "authorization_authority",
        "compatibility_rule_authority",
    ):
        if parts and parts[0] == binding_field:
            return _scope_from_binding(record.get(binding_field))

    # C01 children inherit an enclosing scope so authority-less descriptors,
    # ledgers, and dependency manifests can validate scoped policy references
    # when reached from their authoritative publication root.
    if actual_kind in _C01_RECORD_KINDS:
        return _primary_authority_scope(record)
    record_kind = record["kind"]
    if record_kind == "reference-event" and parts and parts[0] in {
        "actor_id",
        "tool_id",
        "authorization_decision_id",
    }:
        return _scope_from_binding(record.get("authorization_authority"))
    if record_kind == "materialization-recipe" and parts:
        if parts[0] == "tool_ids":
            return None
        if parts[0] in {
            "full_build_conformance_case_ids",
            "incremental_equivalence_case_ids",
        } or (
            parts[0] == "implementation" and parts[-1] == "implementation_id"
        ) or (
            parts[0] == "derivation_steps"
            and parts[-1] == "implementation_id"
        ) or parts == ("accepted_inputs", "scope_compatibility_rule_id") or (
            len(parts) == 2
            and parts[0] == "policies"
            and parts[1] in _RECIPE_OWNER_POLICY_FIELDS
        ):
            return _scope_from_binding(record.get("recipe_owner"))
        if (
            len(parts) == 2
            and parts[0] == "policies"
            and parts[1] in _SEMANTIC_AUTHORITY_POLICY_FIELDS
        ):
            return _scope_from_binding(record.get("authority_owner"))
    if (
        record_kind == "graph-revision"
        and parts
        and parts[0]
        in {
            "recipe_implementation_id",
            "partition_policy_id",
            "total_order_policy_id",
        }
    ):
        return _scope_from_binding(record.get("recipe_owner"))
    if record_kind == "dependency-manifest" and parts:
        if parts[0] in {"recipe_implementation_id", "invalidation_policy_id"}:
            return _scope_from_binding(record.get("recipe_owner"))
    if actual_kind not in _AUTHORITY_SCOPED_EXTERNAL_KINDS:
        return None
    return _primary_authority_scope(record)


def _bind_reference_scopes(
    record: dict[str, Any], refs: list[ReferenceExpectation]
) -> tuple[ReferenceExpectation, ...]:
    scoped: list[ReferenceExpectation] = []
    for expectation in refs:
        scope = _scope_for_reference(record, expectation)
        if scope is None:
            scoped.append(expectation)
        else:
            scoped.append(
                ReferenceExpectation(
                    path=expectation.path,
                    record_id=expectation.record_id,
                    expected_kinds=expectation.expected_kinds,
                    expected_authority_id=scope.owner_authority_id,
                    expected_owner_revision_id=scope.owner_revision_id,
                    expected_adapter_id=scope.authority_adapter_id,
                )
            )
    return tuple(sorted(scoped, key=lambda ref: ref.path.encode("utf-8")))


def _semantic_validate(
    record: dict[str, Any],
) -> tuple[ReferenceExpectation, ...]:
    errors: list[ValidationDiagnostic] = []
    refs: list[ReferenceExpectation] = []
    _check_set_order(record, errors)
    kind = record["kind"]
    if kind == "object-descriptor":
        _check_object_descriptor(record, errors, refs)
    elif kind == "evidence-record":
        _check_evidence(record, errors, refs)
    elif kind == "admission-record":
        _check_admission(record, errors, refs)
    elif kind == "ledger-entry":
        _check_ledger_entry(record, errors, refs)
    elif kind == "evidence-set-revision":
        _check_evidence_set(record, errors, refs)
    elif kind == "graph-record":
        _check_graph_record(record, errors, refs)
    elif kind == "graph-revision":
        _check_graph_revision(record, errors, refs)
    elif kind == "graph-set-revision":
        _check_graph_set(record, errors, refs)
    elif kind == "materialization-recipe":
        _check_recipe(record, errors, refs)
    elif kind == "dependency-manifest":
        _check_dependency(record, errors, refs)
    elif kind == "reference-event":
        _check_reference_event(record, errors, refs)
    else:  # contract_for already failed closed; retain a defensive boundary.
        errors.append(_diagnostic(ValidationPhase.SEMANTIC, "semantic.unknown-kind", "/kind", "no semantic validator is registered"))
    if errors:
        raise RecordValidationError(errors)
    _collect_remaining_references(record, refs)
    return _bind_reference_scopes(record, refs)


def _validate_object_bytes(
    descriptor: dict[str, Any],
    blob_resolver: BlobResolver,
) -> tuple[bytes | None, list[ValidationDiagnostic]]:
    path = "/object_id"
    try:
        resolved = blob_resolver(descriptor["object_id"])
    except Exception as exc:
        return None, [
            _diagnostic(
                ValidationPhase.RELATION,
                "relation.blob-resolver-failure",
                path,
                f"blob resolver failed with {type(exc).__name__}",
            )
        ]
    if resolved is None:
        return None, [_diagnostic(ValidationPhase.RELATION, "relation.blob-unavailable", path, "exact object bytes are unavailable")]
    if type(resolved) not in (bytes, bytearray, memoryview):
        return None, [_diagnostic(ValidationPhase.RELATION, "relation.blob-type", path, "blob resolver must return bytes-like data")]
    try:
        raw = bytes(resolved)
    except Exception as exc:
        return None, [
            _diagnostic(
                ValidationPhase.RELATION,
                "relation.blob-type",
                path,
                f"blob snapshot failed with {type(exc).__name__}",
            )
        ]
    errors: list[ValidationDiagnostic] = []
    if len(raw) != descriptor["byte_length"]:
        errors.append(_diagnostic(ValidationPhase.RELATION, "relation.blob-length", "/byte_length", "byte_length does not equal the exact object length"))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != descriptor["sha256"]:
        errors.append(_diagnostic(ValidationPhase.RELATION, "relation.blob-digest", "/sha256", "sha256 does not equal the digest of exact object bytes"))
    if errors:
        return raw, errors
    representation = descriptor["representation"]
    try:
        if representation == "canonical-json-record":
            child = parse_canonical_json(raw)
            if type(child) is not dict:
                raise CanonicalJsonError("canonical record object must be an object")
            if child.get("kind") != descriptor["described_record_kind"]:
                errors.append(_diagnostic(ValidationPhase.RELATION, "relation.described-kind", path, "stored record kind disagrees with its descriptor"))
            if child.get("schema_id") != descriptor["described_schema_id"]:
                errors.append(_diagnostic(ValidationPhase.RELATION, "relation.described-schema", path, "stored record schema_id disagrees with its descriptor"))
            validate_content_id(child)
        elif representation == "canonical-json-value":
            parse_canonical_json(raw)
        elif representation == "canonical-ndjson-shard":
            if not raw.endswith(b"\n"):
                raise CanonicalJsonError("canonical NDJSON shard must end with LF")
            rows = raw[:-1].split(b"\n")
            if len(rows) != descriptor["canonical_item_count"]:
                errors.append(_diagnostic(ValidationPhase.RELATION, "relation.shard-count", path, "canonical record count disagrees with shard rows"))
            for row in rows:
                child = parse_canonical_json(row)
                if type(child) is not dict:
                    raise CanonicalJsonError("canonical shard row must be an object")
                if child.get("kind") != descriptor["described_record_kind"]:
                    errors.append(_diagnostic(ValidationPhase.RELATION, "relation.described-kind", path, "shard row kind disagrees with its descriptor"))
                    break
                if child.get("schema_id") != descriptor["described_schema_id"]:
                    errors.append(_diagnostic(ValidationPhase.RELATION, "relation.described-schema", path, "shard row schema_id disagrees with its descriptor"))
                    break
                validate_content_id(child)
    except (CanonicalJsonError, TypeError) as exc:
        errors.append(_diagnostic(ValidationPhase.RELATION, "relation.object-framing", path, str(exc)))
    return raw, errors


def _coerce_resolved_record(value: RecordResolution) -> ValidatedRecord:
    if type(value) is ValidatedRecord:
        # ValidatedRecord is a public transport snapshot, not an unforgeable
        # capability.  Re-load its exact bytes and prove that all convenience
        # metadata is the deterministic result of validation.
        metadata = _validated_record_metadata(value)
        reloaded = load_canonical_record(value.canonical_bytes)
        expected = (
            reloaded.id,
            reloaded.kind,
            reloaded.format,
            reloaded.schema_id,
            tuple(
                _reference_expectation_metadata(item)
                for item in reloaded.references
            ),
        )
        if metadata != expected:
            raise ValueError(
                "validated record metadata does not match its revalidated canonical bytes"
            )
        return reloaded
    if type(value) in (bytes, bytearray, memoryview):
        return load_canonical_record(bytes(value))
    return validate_record(value)


class _RelationValidator:
    """Pinned, recursive publication validator with per-call resolution caches."""

    def __init__(self, relations: RelationResolvers):
        self.relations = relations
        self.errors: list[ValidationDiagnostic] = []
        self.resolved: dict[str, ValidatedRecord] = {}
        self.external: dict[
            ExternalReferenceKey,
            ValidatedExternalReference | AdapterValidatedReference,
        ] = {}
        self.adapters: dict[str, TrustedAuthorityAdapter] = {}
        self.blobs: dict[str, bytes] = {}
        self.rows: dict[str, tuple[ValidatedRecord, ...]] = {}
        self.row_visits: set[tuple[str, AuthorityScope | None]] = set()
        self.visited: set[tuple[str, AuthorityScope | None]] = set()
        self.active: set[str] = set()
        self.current_scope: AuthorityScope | None = None
        self.policy_requirements: dict[
            tuple[str, str, AuthorityScope | None], None
        ] = {}
        try:
            adapter_snapshot = dict(relations.trusted_adapters or {})
        except Exception as exc:
            adapter_snapshot = {}
            self._error(
                "relation.adapter-registry",
                "",
                f"cannot snapshot trusted adapter registry: {type(exc).__name__}",
            )
        trusted_snapshot: dict[str, TrustedAuthorityAdapter] = {}
        for adapter_id, registration in adapter_snapshot.items():
            try:
                registered_id, owner_authority_id, canonical_bytes = (
                    _trusted_adapter_metadata(registration)
                )
            except (TypeError, ValueError):
                self._error(
                    "relation.adapter-registration",
                    "",
                    "trusted adapter registry has an invalid exact key or value",
                )
                continue
            if (
                type(adapter_id) is not str
                or registered_id != adapter_id
                or _kind_of(adapter_id) != "authority-adapter"
                or _kind_of(owner_authority_id) != "authority"
            ):
                self._error(
                    "relation.adapter-registration",
                    "",
                    "trusted adapter registry has an invalid exact key or value",
                )
                continue
            pinned = TrustedAuthorityAdapter(
                adapter_id=registered_id,
                owner_authority_id=owner_authority_id,
                canonical_bytes=canonical_bytes,
            )
            value = self._validate_external_record_bytes(
                record_id=adapter_id,
                kind="authority-adapter",
                canonical_bytes=canonical_bytes,
                path="",
                label="authority adapter",
            )
            if value is not None:
                trusted_snapshot[adapter_id] = pinned
                self.adapters[adapter_id] = pinned
        self.trusted_adapter_registry: Mapping[
            str, TrustedAuthorityAdapter
        ] = MappingProxyType(trusted_snapshot)

    def validate(
        self, record: dict[str, Any], refs: tuple[ReferenceExpectation, ...]
    ) -> None:
        self.validate_many(((record, refs),))

    def validate_many(
        self,
        records: Iterable[tuple[dict[str, Any], tuple[ReferenceExpectation, ...]]],
    ) -> None:
        values = tuple(records)
        # Visit explicit authority roots before authority-less closure records.
        # Topological publications normally place objects first; this ordering
        # lets those objects inherit the exact owner scope that references them.
        ordered = sorted(
            enumerate(values),
            key=lambda item: (
                _primary_authority_scope(item[1][0]) is None,
                item[0],
            ),
        )
        for _, (record, refs) in ordered:
            self._visit(record, refs, _primary_authority_scope(record))
        self._run_policy_validation()
        if self.errors:
            raise RecordValidationError(self.errors)

    def _error(self, code: str, path: str, message: str) -> None:
        self.errors.append(
            _diagnostic(ValidationPhase.RELATION, code, path, message)
        )

    @staticmethod
    def _scope_from_expectation(
        expectation: ReferenceExpectation,
    ) -> AuthorityScope | None:
        fields = (
            expectation.expected_authority_id,
            expectation.expected_owner_revision_id,
            expectation.expected_adapter_id,
        )
        if all(type(item) is str for item in fields):
            return AuthorityScope(
                owner_authority_id=cast(str, fields[0]),
                owner_revision_id=cast(str, fields[1]),
                authority_adapter_id=cast(str, fields[2]),
            )
        return None

    def _effective_expectation(
        self, expectation: ReferenceExpectation
    ) -> ReferenceExpectation:
        if self._scope_from_expectation(expectation) is not None:
            return expectation
        actual_kind = _kind_of(expectation.record_id)
        if self.current_scope is None or (
            actual_kind not in _C01_RECORD_KINDS
            and actual_kind not in _AUTHORITY_SCOPED_EXTERNAL_KINDS
        ):
            return expectation
        return ReferenceExpectation(
            path=expectation.path,
            record_id=expectation.record_id,
            expected_kinds=expectation.expected_kinds,
            expected_authority_id=self.current_scope.owner_authority_id,
            expected_owner_revision_id=self.current_scope.owner_revision_id,
            expected_adapter_id=self.current_scope.authority_adapter_id,
        )

    def _resolve(self, expectation: ReferenceExpectation) -> ValidatedRecord | None:
        expectation = self._effective_expectation(expectation)
        referenced_kind = _kind_of(expectation.record_id)
        if referenced_kind not in _C01_RECORD_KINDS:
            self._resolve_external(expectation)
            return None
        cached = self.resolved.get(expectation.record_id)
        if cached is not None:
            if cached.kind not in expectation.expected_kinds:
                self._error(
                    "relation.kind-mismatch",
                    expectation.path,
                    "resolved record has the wrong target kind",
                )
                return None
            return cached
        resolver = self.relations.record_resolver
        if resolver is None:
            return None
        try:
            target = resolver(expectation.record_id)
        except Exception as exc:
            self._error(
                "relation.record-resolver-failure",
                expectation.path,
                f"record resolver failed with {type(exc).__name__}",
            )
            return None
        if target is None:
            self._error(
                "relation.unavailable",
                expectation.path,
                "referenced immutable record is unavailable",
            )
            return None
        try:
            validated = _coerce_resolved_record(target)
        except Exception as exc:
            self._error(
                "relation.invalid-target",
                expectation.path,
                f"referenced record is invalid ({type(exc).__name__})",
            )
            return None
        if validated.id != expectation.record_id:
            self._error(
                "relation.id-mismatch",
                expectation.path,
                "resolver returned a different immutable record ID",
            )
            return None
        if validated.kind not in expectation.expected_kinds:
            self._error(
                "relation.kind-mismatch",
                expectation.path,
                "resolved record has the wrong target kind",
            )
            return None
        self.resolved[validated.id] = validated
        return validated

    @staticmethod
    def _external_key(expectation: ReferenceExpectation) -> ExternalReferenceKey:
        return (
            expectation.record_id,
            expectation.expected_kinds,
            expectation.expected_authority_id,
            expectation.expected_owner_revision_id,
            expectation.expected_adapter_id,
        )

    def _validate_external_record_bytes(
        self,
        *,
        record_id: str,
        kind: str,
        canonical_bytes: Any,
        path: str,
        label: str,
    ) -> dict[str, Any] | None:
        if type(canonical_bytes) is not bytes:
            self._error(
                "relation.external-record",
                path,
                f"{label} canonical bytes must be exact bytes",
            )
            return None
        try:
            value = parse_canonical_json(canonical_bytes)
            if type(value) is not dict:
                raise CanonicalJsonError(f"{label} must be an object")
            validate_content_id(value)
        except (CanonicalJsonError, TypeError) as exc:
            self._error(
                "relation.external-record",
                path,
                f"invalid {label} canonical bytes: {exc}",
            )
            return None
        if value.get("id") != record_id or value.get("kind") != kind:
            self._error(
                "relation.external-record",
                path,
                f"{label} canonical record does not match its exact binding",
            )
            return None
        return value

    def _trusted_adapter(
        self, scope: AuthorityScope, path: str
    ) -> TrustedAuthorityAdapter | None:
        cached = self.adapters.get(scope.authority_adapter_id)
        if cached is not None:
            if cached.owner_authority_id != scope.owner_authority_id:
                self._error(
                    "relation.adapter-authority",
                    path,
                    "trusted adapter is registered to a different owner authority",
                )
                return None
            return cached
        registry = self.trusted_adapter_registry
        if registry is None or scope.authority_adapter_id not in registry:
            self._error(
                "relation.adapter-unavailable",
                path,
                "exact authority_adapter_id is absent from the pinned trusted adapter registry",
            )
            return None
        registration = registry[scope.authority_adapter_id]
        if type(registration) is not TrustedAuthorityAdapter:
            self._error(
                "relation.adapter-registration",
                path,
                "trusted adapter registry contains an invalid registration value",
            )
            return None
        if (
            registration.adapter_id != scope.authority_adapter_id
            or registration.owner_authority_id != scope.owner_authority_id
            or _kind_of(registration.adapter_id) != "authority-adapter"
        ):
            self._error(
                "relation.adapter-registration",
                path,
                "trusted adapter registry key, adapter ID, and owner authority must agree",
            )
            return None
        adapter = self._validate_external_record_bytes(
            record_id=registration.adapter_id,
            kind="authority-adapter",
            canonical_bytes=registration.canonical_bytes,
            path=path,
            label="authority adapter",
        )
        if adapter is None:
            return None
        self.adapters[registration.adapter_id] = registration
        return registration

    def _resolve_external(self, expectation: ReferenceExpectation) -> None:
        try:
            expectation_snapshot = _reference_expectation_metadata(expectation)
        except (TypeError, ValueError):
            self._error(
                "relation.external-binding",
                "",
                "external reference expectation metadata is invalid",
            )
            return
        (
            path,
            record_id,
            expected_kinds,
            expected_authority_id,
            expected_owner_revision_id,
            expected_adapter_id,
        ) = expectation_snapshot
        resolver_request = ReferenceExpectation(
            path=path,
            record_id=record_id,
            expected_kinds=expected_kinds,
            expected_authority_id=expected_authority_id,
            expected_owner_revision_id=expected_owner_revision_id,
            expected_adapter_id=expected_adapter_id,
        )
        key = self._external_key(resolver_request)
        if key in self.external:
            return
        scope_fields = (
            expected_authority_id,
            expected_owner_revision_id,
            expected_adapter_id,
        )
        if any(item is not None for item in scope_fields) and not all(
            type(item) is str for item in scope_fields
        ):
            self._error(
                "relation.external-scope",
                path,
                "authority-scoped reference requires authority, owner revision, and adapter",
            )
            return
        scope = self._scope_from_expectation(resolver_request)
        actual_kind = _kind_of(record_id)
        if (
            actual_kind in _AUTHORITY_SCOPED_EXTERNAL_KINDS
            and scope is None
            and self.relations.require_complete
        ):
            self._error(
                "relation.external-scope-unavailable",
                path,
                "authority-scoped external reference has no declared owner binding",
            )
            return
        trusted_adapter = None if scope is None else self._trusted_adapter(
            scope, path
        )
        if scope is not None and trusted_adapter is None:
            return
        if actual_kind == "authority-adapter":
            if scope is None or record_id != scope.authority_adapter_id:
                self._error(
                    "relation.adapter-binding",
                    path,
                    "authority adapter reference must equal its exact trusted binding",
                )
                return
            assert trusted_adapter is not None
            result = AdapterValidatedReference(
                record_id=trusted_adapter.adapter_id,
                kind="authority-adapter",
                canonical_bytes=trusted_adapter.canonical_bytes,
                adapter_id=trusted_adapter.adapter_id,
                owner_authority_id=scope.owner_authority_id,
                owner_revision_id=scope.owner_revision_id,
            )
            self.external[key] = result
            return
        resolver = self.relations.external_reference_resolver
        if resolver is None:
            if self.relations.require_complete:
                self._error(
                    "relation.external-port-unavailable",
                    path,
                    "external semantic reference requires an owner-adapter resolver",
                )
            return
        try:
            result = resolver(resolver_request)
        except Exception as exc:
            self._error(
                "relation.external-resolver-failure",
                path,
                f"external registry resolver failed with {type(exc).__name__}",
            )
            return
        try:
            request_unchanged = (
                _reference_expectation_metadata(resolver_request)
                == expectation_snapshot
            )
        except (TypeError, ValueError):
            request_unchanged = False
        if not request_unchanged:
            self._error(
                "relation.external-resolver-mutation",
                path,
                "external registry mutated its isolated reference expectation",
            )
            return
        if result is None:
            self._error(
                "relation.external-unavailable",
                path,
                "owner adapter could not resolve and validate the external record",
            )
            return
        try:
            result_snapshot = _external_reference_metadata(result)
        except (TypeError, ValueError):
            self._error(
                "relation.external-binding",
                path,
                "external registry returned an unsupported validation result",
            )
            return
        if type(result) is ValidatedExternalReference:
            pinned_result: ValidatedExternalReference | AdapterValidatedReference = (
                ValidatedExternalReference(
                    record_id=result_snapshot[0],
                    kind=result_snapshot[1],
                    canonical_bytes=result_snapshot[2],
                )
            )
        else:
            pinned_result = AdapterValidatedReference(
                record_id=result_snapshot[0],
                kind=result_snapshot[1],
                adapter_id=result_snapshot[2],
                owner_authority_id=result_snapshot[3],
                owner_revision_id=result_snapshot[4],
                canonical_bytes=result_snapshot[5],
            )
        if (
            pinned_result.record_id != record_id
            or pinned_result.kind not in expected_kinds
            or _kind_of(pinned_result.record_id) != pinned_result.kind
        ):
            self._error(
                "relation.external-binding",
                path,
                "adapter-validated external binding has inconsistent identity or kind",
            )
            return
        if scope is None and type(pinned_result) is not ValidatedExternalReference:
            self._error(
                "relation.external-structural-trust",
                path,
                "structural external registry result must not self-declare an authority adapter",
            )
            return
        if scope is not None and (
            type(pinned_result) is not AdapterValidatedReference
            or pinned_result.adapter_id != scope.authority_adapter_id
            or pinned_result.owner_authority_id != scope.owner_authority_id
            or pinned_result.owner_revision_id != scope.owner_revision_id
        ):
            self._error(
                "relation.external-authority-binding",
                path,
                "adapter result must match exact authority, owner revision, and trusted adapter",
            )
            return
        value = self._validate_external_record_bytes(
            record_id=pinned_result.record_id,
            kind=pinned_result.kind,
            canonical_bytes=pinned_result.canonical_bytes,
            path=path,
            label="external record",
        )
        if value is None:
            return
        self.external[key] = pinned_result

    def _visit(
        self,
        record: dict[str, Any],
        refs: tuple[ReferenceExpectation, ...],
        inherited_scope: AuthorityScope | None = None,
    ) -> None:
        record_id = record["id"]
        scope = _primary_authority_scope(record) or inherited_scope
        visit_key = (record_id, scope)
        if visit_key in self.visited:
            return
        if scope is None and any(item[0] == record_id for item in self.visited):
            # An authority-less closure record already received a stronger,
            # explicitly inherited validation.  Do not manufacture a weaker
            # unscoped second interpretation merely because it is also listed
            # as a publication root.
            return
        if record_id in self.active:
            self._error(
                "relation.content-cycle",
                "/id",
                f"cyclic immutable record reference reaches {record_id}",
            )
            return
        self.active.add(record_id)
        previous_scope = self.current_scope
        self.current_scope = scope
        self.resolved.setdefault(
            record_id,
            ValidatedRecord(
                id=record_id,
                kind=record["kind"],
                format=record["format"],
                schema_id=record["schema_id"],
                canonical_bytes=canonical_json_bytes(record),
                references=refs,
            ),
        )
        for expectation in refs:
            effective = self._effective_expectation(expectation)
            target = self._resolve(effective)
            if target is not None:
                if target.id in self.active:
                    self._error(
                        "relation.content-cycle",
                        expectation.path,
                        "immutable content-record references must be acyclic",
                    )
                else:
                    self._visit(
                        target.to_dict(),
                        target.references,
                        self._scope_from_expectation(effective),
                    )
        if record["kind"] == "object-descriptor":
            self._visit_object(record)
        self._cross_record(record)
        self.active.remove(record_id)
        self.visited.add(visit_key)
        self.current_scope = previous_scope

    def _visit_object(self, descriptor: dict[str, Any]) -> None:
        resolver = self.relations.blob_resolver
        if resolver is None or descriptor["id"] in self.blobs:
            return
        raw, diagnostics = _validate_object_bytes(descriptor, resolver)
        self.errors.extend(diagnostics)
        if raw is not None and not diagnostics:
            self.blobs[descriptor["id"]] = raw
        if descriptor["representation"] not in (
            "canonical-json-record",
            "canonical-json-value",
            "canonical-ndjson-shard",
        ):
            return
        schema_descriptor_id = descriptor["described_schema_object_descriptor_id"]
        schema_descriptor = self._target(schema_descriptor_id)
        if schema_descriptor is None:
            return
        if schema_descriptor["representation"] != "exact-bytes":
            self._error(
                "relation.schema-representation",
                "/described_schema_object_descriptor_id",
                "the schema descriptor must describe exact schema bytes",
            )
            return
        schema_raw = self.blobs.get(schema_descriptor_id)
        if schema_raw is None:
            return
        try:
            schema = parse_json_strict(schema_raw)
        except (CanonicalJsonError, TypeError) as exc:
            self._error(
                "relation.schema-bytes",
                "/described_schema_object_descriptor_id",
                f"described schema bytes are invalid: {exc}",
            )
            return
        if type(schema) is not dict or schema.get("$id") != descriptor["described_schema_id"]:
            self._error(
                "relation.schema-id",
                "/described_schema_id",
                "described schema bytes must declare the bound $id",
            )
            return
        registered_raw = registered_schema_bytes(descriptor["described_schema_id"])
        if registered_raw is not None and schema_raw != registered_raw:
            self._error(
                "relation.registered-schema-bytes",
                "/described_schema_object_descriptor_id",
                "a registered C01 schema descriptor must bind the exact checked-in bytes",
            )
            return
        if descriptor["representation"] in (
            "canonical-json-record",
            "canonical-json-value",
        ):
            child_raw = self.blobs.get(descriptor["id"])
            if child_raw is not None:
                self._validate_bound_child(descriptor, schema, child_raw)
        elif descriptor["representation"] == "canonical-ndjson-shard":
            child_raw = self.blobs.get(descriptor["id"])
            if child_raw is not None and child_raw.endswith(b"\n"):
                for row in child_raw[:-1].split(b"\n"):
                    self._validate_bound_child(descriptor, schema, row)

    def _validate_bound_child(
        self,
        descriptor: dict[str, Any],
        schema: dict[str, Any],
        child_raw: bytes,
    ) -> None:
        try:
            child = parse_canonical_json(child_raw)
            if (
                descriptor["representation"]
                in {"canonical-json-record", "canonical-ndjson-shard"}
                and registered_schema_bytes(descriptor["described_schema_id"])
                is not None
            ):
                validated = validate_record(child)
                if validated.schema_id != descriptor["described_schema_id"]:
                    raise CanonicalJsonError(
                        "registered child schema_id differs from its descriptor"
                    )
                return
            try:
                _assert_closed_bound_schema(schema)
            except CanonicalJsonError as exc:
                self._error(
                    "relation.open-bound-schema",
                    "/described_schema_object_descriptor_id",
                    f"bound schema is outside the recursively closed profile: {exc}",
                )
                return
            Draft202012Validator.check_schema(schema)
            resource = Resource.from_contents(schema)
            registry = Registry().with_resource(schema["$id"], resource)
            validator = Draft202012Validator(schema, registry=registry)
            violations = sorted(
                validator.iter_errors(child),
                key=lambda error: (
                    _pointer(tuple(error.absolute_path)).encode("utf-8"),
                    _pointer(tuple(error.absolute_schema_path)).encode("utf-8"),
                    str(error.validator).encode("utf-8"),
                    error.message.encode("utf-8"),
                ),
            )
        except (CanonicalJsonError, SchemaError, Unresolvable, KeyError) as exc:
            self._error(
                "relation.bound-schema-validation",
                "/object_id",
                f"cannot validate canonical record against its exact schema: {exc}",
            )
            return
        except Exception as exc:
            # jsonschema wraps some referencing failures in a version-specific
            # exception.  They are still a fail-closed unavailable schema, not
            # permission to fetch it or skip validation.
            self._error(
                "relation.bound-schema-validation",
                "/object_id",
                f"offline bound-schema validation failed: {exc}",
            )
            return
        if violations:
            first = violations[0]
            self._error(
                "relation.bound-schema-instance",
                "/object_id",
                "canonical record does not satisfy its exact bound schema at "
                f"{_pointer(tuple(first.absolute_path)) or '/'}: {first.message}",
            )

    def _target(self, record_id: str | None) -> dict[str, Any] | None:
        if record_id is None:
            return None
        target = self.resolved.get(record_id)
        return None if target is None else target.to_dict()

    def _shard_rows(
        self, descriptor_id: str, path: str, *, visit: bool = True
    ) -> tuple[ValidatedRecord, ...] | None:
        if descriptor_id in self.rows:
            result = self.rows[descriptor_id]
            visit_key = (descriptor_id, self.current_scope)
            if visit and visit_key not in self.row_visits:
                for validated in result:
                    self._visit(
                        validated.to_dict(),
                        validated.references,
                        self.current_scope,
                    )
                self.row_visits.add(visit_key)
            return result
        descriptor = self._target(descriptor_id)
        if descriptor is None:
            return None
        if descriptor["representation"] != "canonical-ndjson-shard":
            self._error(
                "relation.partition-representation",
                path,
                "partition descriptor must name a canonical NDJSON shard",
            )
            return None
        raw = self.blobs.get(descriptor_id)
        if raw is None:
            return None
        if not raw.endswith(b"\n"):
            self._error(
                "relation.shard-framing", path, "canonical NDJSON shard must end with LF"
            )
            return None
        validated_rows: list[ValidatedRecord] = []
        for ordinal, row in enumerate(raw[:-1].split(b"\n")):
            try:
                validated = load_canonical_record(row)
            except RecordValidationError as exc:
                self._error(
                    "relation.shard-record",
                    f"{path}/{ordinal}",
                    f"invalid canonical shard record: {exc}",
                )
                return None
            validated_rows.append(validated)
        row_ids = [item.id for item in validated_rows]
        if len(row_ids) != len(set(row_ids)):
            self._error(
                "relation.shard-duplicate-record",
                path,
                "canonical shard record IDs must be unique",
            )
            return None
        # Index the complete shard before visiting any row.  Forward endpoint,
        # conflict, and refinement references within one immutable shard are
        # valid and must not depend on redundant standalone resolver entries.
        for validated in validated_rows:
            existing = self.resolved.get(validated.id)
            if existing is not None and existing != validated:
                self._error(
                    "relation.shard-record-collision",
                    path,
                    "shard record metadata or bytes disagree with an indexed record of the same ID",
                )
                return None
            self.resolved[validated.id] = validated
        result = tuple(validated_rows)
        self.rows[descriptor_id] = result
        if visit:
            for validated in validated_rows:
                self._visit(
                    validated.to_dict(), validated.references, self.current_scope
                )
            self.row_visits.add((descriptor_id, self.current_scope))
        return result

    def _cross_record(self, record: dict[str, Any]) -> None:
        kind = record["kind"]
        if kind == "admission-record":
            self._cross_admission(record)
        elif kind == "ledger-entry":
            self._cross_ledger_entry(record)
        elif kind == "evidence-set-revision":
            self._cross_evidence_set(record)
        elif kind == "graph-record":
            self._cross_graph_record(record)
        elif kind == "graph-revision":
            self._cross_graph_revision(record)
        elif kind == "graph-set-revision":
            self._cross_graph_set(record)
        elif kind == "dependency-manifest":
            self._cross_dependency(record)
        elif kind == "reference-event":
            self._cross_reference(record)
        if (
            kind == "object-descriptor"
            and record["representation"] == "canonical-ndjson-shard"
        ):
            self._require_policy(
                "canonical-total-order",
                record,
                _scope_from_binding(record["total_order_authority"]),
            )
        if kind in {
            "evidence-record",
            "evidence-set-revision",
            "graph-record",
            "graph-revision",
            "graph-set-revision",
        }:
            self._require_policy(
                "state-reduction", record, _primary_authority_scope(record)
            )
        for profile_binding in record.get("profile_support_bindings", ()):
            profile_scope = _scope_from_binding(
                profile_binding.get("profile_authority")
            )
            self._require_policy("state-reduction", record, profile_scope)
        if kind == "admission-record":
            self._require_policy(
                "admission-validation",
                record,
                _scope_from_binding(record["decision_owner"]),
            )
        elif kind == "evidence-record":
            self._require_policy(
                "evidence-assertion",
                record,
                _scope_from_binding(record["authority_owner"]),
            )
        elif kind == "graph-record":
            self._require_policy(
                "graph-record-semantics",
                record,
                _scope_from_binding(record["authority_owner"]),
            )
        elif kind == "reference-event":
            self._require_policy(
                "reference-authorization",
                record,
                _scope_from_binding(record["authorization_authority"]),
            )
        elif kind == "materialization-recipe":
            self._require_policy(
                "recipe-definition",
                record,
                _scope_from_binding(record["authority_owner"]),
            )
            self._require_policy(
                "recipe-definition",
                record,
                _scope_from_binding(record["recipe_owner"]),
            )
        if kind == "evidence-set-revision":
            self._require_policy(
                "evidence-selection-history",
                record,
                _scope_from_binding(record["authority_owner"]),
            )
        elif kind == "graph-set-revision":
            self._require_policy(
                "graph-set-composition",
                record,
                _scope_from_binding(record["composition_owner"]),
            )
            self._require_policy(
                "compatibility-rule-application",
                record,
                _scope_from_binding(record["compatibility_rule_authority"]),
            )
        elif kind == "dependency-manifest":
            self._require_policy(
                "dependency-semantics",
                record,
                _scope_from_binding(record["authority_owner"]),
            )

    @staticmethod
    def _matching_source_contract_kinds(
        recipe: dict[str, Any],
        step: dict[str, Any],
        target: dict[str, Any],
        source: dict[str, Any],
        source_binding: dict[str, Any] | None = None,
    ) -> set[str]:
        """Return the exact recipe source modes satisfied by one graph source."""

        accepted_by_key = {
            item["graph_input_contract_key"]: item
            for item in recipe["accepted_inputs"]["graph_input_contracts"]
        }
        steps_by_id = {
            item["derivation_step_id"]: item
            for item in recipe["derivation_steps"]
        }
        outputs_by_key = {
            item["output_contract_key"]: item
            for item in recipe["output_contracts"]
        }
        source_coordinate = (
            source["graph_family_id"],
            source["category_id"],
            source["resolution_id"],
        )
        source_record_kind = source["body"]["record_kind"]
        matches: set[str] = set()
        for input_contract in step["input_contracts"]:
            if input_contract["input_mode"] != "source-graph-record":
                continue
            if (
                source_binding is not None
                and input_contract["input_contract_key"]
                != source_binding["input_contract_key"]
            ):
                continue
            for contract in input_contract["source_contracts"]:
                if source_record_kind not in contract["record_kinds"]:
                    continue
                source_kind = contract["source_kind"]
                contract_key = contract["contract_key"]
                if source_binding is not None and (
                    source_kind != source_binding["source_kind"]
                    or contract_key != source_binding["contract_key"]
                    or contract["predecessor_step_id"]
                    != source_binding["predecessor_step_id"]
                ):
                    continue
                if source_kind == "accepted-graph-input":
                    accepted = accepted_by_key.get(contract_key)
                    if accepted is None:
                        continue
                    accepted_coordinate = (
                        accepted["graph_family_id"],
                        accepted["category_id"],
                        accepted["resolution_id"],
                    )
                    if (
                        source_coordinate == accepted_coordinate
                        and source_record_kind in accepted["record_kinds"]
                    ):
                        matches.add(source_kind)
                    continue

                producer = (
                    step
                    if source_kind == "local-output"
                    else steps_by_id.get(contract["predecessor_step_id"])
                )
                if producer is None:
                    continue
                producer_output = next(
                    (
                        item
                        for item in producer["output_contracts"]
                        if item["output_contract_key"] == contract_key
                    ),
                    None,
                )
                output = outputs_by_key.get(contract_key)
                if producer_output is None or output is None:
                    continue
                output_coordinate = (
                    output["graph_family_id"],
                    output["category_id"],
                    output["resolution_id"],
                )
                if (
                    source_coordinate == output_coordinate
                    and source_record_kind
                    in producer_output["output_record_kinds"]
                    and source.get("recipe_id") == recipe["id"]
                    and source.get("derivation_step_id")
                    == producer["derivation_step_id"]
                    and source.get("context_ref_id") == target["context_ref_id"]
                ):
                    matches.add(source_kind)
        return matches

    def _cross_graph_record(self, record: dict[str, Any]) -> None:
        recipe = self._target(record["recipe_id"])
        recipe_scope = (
            None
            if recipe is None
            else _scope_from_binding(recipe.get("recipe_owner"))
        )
        self._require_policy("derivation", record, recipe_scope)
        if recipe is not None:
            step = next(
                (
                    item
                    for item in recipe["derivation_steps"]
                    if item["derivation_step_id"] == record["derivation_step_id"]
                ),
                None,
            )
            if step is None:
                self._error(
                    "relation.derivation-step",
                    "/derivation_step_id",
                    "graph record derivation step must occur in the exact recipe",
                )
            else:
                body_kind = record["body"]["record_kind"]
                output_contract = next(
                    (
                        item
                        for item in recipe["output_contracts"]
                        if item["graph_family_id"] == record["graph_family_id"]
                        and item["category_id"] == record["category_id"]
                        and item["resolution_id"] == record["resolution_id"]
                    ),
                    None,
                )
                step_output = None if output_contract is None else next(
                    (
                        item
                        for item in step["output_contracts"]
                        if item["output_contract_key"]
                        == output_contract["output_contract_key"]
                    ),
                    None,
                )
                if step_output is None or body_kind not in step_output[
                    "output_record_kinds"
                ]:
                    self._error(
                        "relation.derivation-output-kind",
                        "/derivation_step_id",
                        "derivation step must declare the exact output coordinate and graph body kind",
                    )
                for evidence_input in record["evidence_inputs"]:
                    evidence_id = evidence_input["evidence_record_id"]
                    evidence = self._target(evidence_id)
                    input_contract = next(
                        (
                            item
                            for item in step["input_contracts"]
                            if item["input_contract_key"]
                            == evidence_input["input_contract_key"]
                        ),
                        None,
                    )
                    if evidence is not None and (
                        input_contract is None
                        or input_contract["input_mode"] != "evidence"
                        or evidence["evidence_kind"]
                        not in input_contract["evidence_kind_ids"]
                    ):
                        self._error(
                            "relation.derivation-evidence-contract",
                            "/evidence_inputs",
                            "evidence input key and kind must satisfy its exact step input contract",
                        )
                for source_input in record["source_graph_inputs"]:
                    source_id = source_input["graph_record_id"]
                    source = self._target(source_id)
                    if source is None:
                        continue
                    matches = self._matching_source_contract_kinds(
                        recipe, step, record, source, source_input
                    )
                    if source_input["source_kind"] not in matches:
                        self._error(
                            "relation.derivation-source-contract",
                            "/source_graph_inputs",
                            "source graph input must byte-for-byte select an exact accepted, predecessor, or local step input edge",
                        )

        body = record["body"]
        body_kind = body["record_kind"]

        def node(node_id: str, path: str) -> dict[str, Any] | None:
            value = self._target(node_id)
            if value is None:
                return None
            if value["kind"] != "graph-record" or value["body"]["record_kind"] != "node":
                self._error(
                    "relation.endpoint-node-kind",
                    path,
                    "endpoint reference must resolve to a node graph record",
                )
                return None
            return value

        if body_kind == "edge":
            for prefix in ("source", "target"):
                endpoint = node(
                    body[f"{prefix}_node_record_id"],
                    f"/body/{prefix}_node_record_id",
                )
                if (
                    endpoint is not None
                    and endpoint["body"]["subject_id"]
                    != body[f"{prefix}_subject_id"]
                ):
                    self._error(
                        "relation.endpoint-subject",
                        f"/body/{prefix}_subject_id",
                        "edge subject must equal its exact endpoint node subject",
                    )
        elif body_kind == "property":
            subject_node = node(
                body["subject_node_record_id"],
                "/body/subject_node_record_id",
            )
            if (
                subject_node is not None
                and subject_node["body"]["subject_id"] != body["subject_id"]
            ):
                self._error(
                    "relation.property-subject",
                    "/body/subject_id",
                    "property subject must equal its exact node subject",
                )
            value_descriptor = self._target(
                body["value_object_descriptor_id"]
            )
            value_schema_descriptor = self._target(
                body["value_schema_object_descriptor_id"]
            )
            if value_descriptor is not None and (
                value_descriptor["representation"] != "canonical-json-value"
                or value_descriptor["described_schema_object_descriptor_id"]
                != body["value_schema_object_descriptor_id"]
                or value_descriptor["described_record_kind"] is not None
            ):
                self._error(
                    "relation.property-value-descriptor",
                    "/body/value_object_descriptor_id",
                    "property value must be a canonical JSON value bound to the exact declared value schema descriptor",
                )
            if value_schema_descriptor is not None and (
                value_schema_descriptor["representation"] != "exact-bytes"
                or body["value_schema_object_descriptor_id"] not in self.blobs
            ):
                self._error(
                    "relation.property-value-schema",
                    "/body/value_schema_object_descriptor_id",
                    "property value schema must resolve to exact pinned schema bytes",
                )
        elif body_kind == "refinement-mapping":
            endpoints = (
                ("coarse", body["coarse_endpoint"], "/body/coarse_endpoint"),
                *(
                    ("fine", endpoint, f"/body/fine_endpoints/{index}")
                    for index, endpoint in enumerate(body["fine_endpoints"])
                ),
            )
            for label, endpoint, endpoint_path in endpoints:
                endpoint_node = node(
                    endpoint["source_node_record_id"],
                    f"{endpoint_path}/source_node_record_id",
                )
                source_revision = self._target(
                    endpoint["source_graph_revision_id"]
                )
                if endpoint_node is not None and (
                    endpoint_node["body"]["subject_id"]
                    != endpoint["subject_id"]
                    or endpoint_node["graph_family_id"]
                    != endpoint["graph_family_id"]
                    or endpoint_node["category_id"] != endpoint["category_id"]
                    or endpoint_node["resolution_id"]
                    != endpoint["resolution_id"]
                ):
                    self._error(
                        f"relation.refinement-{label}-node",
                        endpoint_path,
                        f"{label} node must bind the declared subject and graph coordinate",
                    )
                if source_revision is None:
                    continue
                revision_coordinate = (
                    source_revision["graph_family_id"],
                    source_revision["category_id"],
                    source_revision["resolution_id"],
                )
                endpoint_coordinate = (
                    endpoint["graph_family_id"],
                    endpoint["category_id"],
                    endpoint["resolution_id"],
                )
                revision_ids = {
                    row.id
                    for partition in source_revision["partitions"]
                    for row in (
                        self.rows.get(partition["object_descriptor_id"]) or ()
                    )
                }
                scope_mismatch = revision_coordinate != endpoint_coordinate
                if endpoint_node is not None:
                    scope_mismatch = scope_mismatch or (
                        source_revision["context_ref_id"]
                        != endpoint_node["context_ref_id"]
                    )
                if scope_mismatch:
                    self._error(
                        "relation.refinement-source-revision-scope",
                        f"{endpoint_path}/source_graph_revision_id",
                        "refinement source revision must match the exact endpoint graph coordinate and node context",
                    )
                if endpoint["source_node_record_id"] not in revision_ids:
                    self._error(
                        "relation.refinement-source-revision-membership",
                        f"{endpoint_path}/source_graph_revision_id",
                        "refinement source node must belong to its exact source graph revision",
                    )

    def _require_policy(
        self,
        rule: str,
        record: dict[str, Any],
        scope: AuthorityScope | None,
    ) -> None:
        self.policy_requirements[(rule, record["id"], scope)] = None

    def _run_policy_validation(self) -> None:
        if not self.policy_requirements:
            return
        validator = self.relations.policy_validator
        if validator is None:
            if self.relations.require_complete:
                for rule, record_id, _ in self.policy_requirements:
                    self._error(
                        "relation.policy-port-unavailable",
                        "/id",
                        f"owner-policy validation {rule!r} is unavailable for {record_id}",
                    )
            return
        records_view = MappingProxyType(dict(self.resolved))
        external_view = MappingProxyType(dict(self.external))
        blobs_view = MappingProxyType(dict(self.blobs))
        def policy_key(
            item: tuple[str, str, AuthorityScope | None]
        ) -> tuple[bytes, ...]:
            rule, record_id, scope = item
            return (
                rule.encode("utf-8"),
                record_id.encode("ascii"),
                b"" if scope is None else scope.owner_authority_id.encode("ascii"),
                b"" if scope is None else scope.owner_revision_id.encode("ascii"),
                b"" if scope is None else scope.authority_adapter_id.encode("ascii"),
            )

        for rule, record_id, scope in sorted(
            self.policy_requirements,
            key=policy_key,
        ):
            record = self.resolved[record_id]
            if scope is None:
                self._error(
                    "relation.policy-scope-unavailable",
                    "/id",
                    f"owner-policy validation {rule!r} has no exact authority binding",
                )
                continue
            adapter = self._trusted_adapter(scope, "/id")
            if adapter is None:
                continue
            request = PolicyValidationRequest(
                rule=rule,
                record=record,
                records=records_view,
                external_references=external_view,
                object_bytes=blobs_view,
                authority_scope=scope,
                authority_adapter=adapter,
            )
            try:
                request, request_snapshot = _snapshot_policy_request(request)
            except (CanonicalJsonError, TypeError, ValueError):
                self._error(
                    "relation.policy-request",
                    "/id",
                    f"owner-policy request could not be isolated for {rule!r}",
                )
                continue
            try:
                result = validator(request)
            except Exception as exc:
                self._error(
                    "relation.policy-validator-failure",
                    "/id",
                    f"owner-policy validator failed for {rule!r} with {type(exc).__name__}",
                )
                continue

            def request_was_mutated() -> bool:
                try:
                    _, current_snapshot = _snapshot_policy_request(request)
                except (CanonicalJsonError, TypeError, ValueError):
                    return True
                return current_snapshot != request_snapshot

            if request_was_mutated():
                self._error(
                    "relation.policy-validator-mutation",
                    "/id",
                    f"owner-policy validator mutated its isolated request for {rule!r}",
                )
                continue
            if result is True:
                continue
            if result is False:
                self._error(
                    "relation.policy-rejected",
                    "/id",
                    f"owner-policy validator rejected {rule!r}",
                )
                continue
            try:
                diagnostics = tuple(result)
            except Exception:
                self._error(
                    "relation.policy-result",
                    "/id",
                    f"owner-policy validator returned an invalid result for {rule!r}",
                )
                continue
            if request_was_mutated():
                self._error(
                    "relation.policy-validator-mutation",
                    "/id",
                    f"owner-policy validator mutated its isolated request for {rule!r}",
                )
                continue
            if any(
                type(item) is not ValidationDiagnostic
                or type(item.phase) is not ValidationPhase
                or type(item.code) is not str
                or type(item.path) is not str
                or type(item.message) is not str
                for item in diagnostics
            ):
                self._error(
                    "relation.policy-result",
                    "/id",
                    f"owner-policy validator returned non-diagnostic values for {rule!r}",
                )
                continue
            self.errors.extend(
                ValidationDiagnostic(
                    phase=item.phase,
                    code=item.code,
                    path=item.path,
                    message=item.message,
                )
                for item in diagnostics
            )

    def _cross_admission(self, record: dict[str, Any]) -> None:
        candidate = self._target(record["candidate_record_id"])
        if (
            candidate is not None
            and record["candidate_kind"] == "evidence-record"
            and candidate["context_ref_id"] != record["context_ref_id"]
        ):
            self._error(
                "relation.admission-context",
                "/context_ref_id",
                "admission context must equal its evidence candidate context",
            )
        prior = self._target(record["prior_admission_record_id"])
        if prior is not None and any(
            prior[field] != record[field]
            for field in (
                "decision_owner",
                "context_ref_id",
                "candidate_kind",
                "candidate_record_id",
            )
        ):
            self._error(
                "relation.admission-prior-scope",
                "/prior_admission_record_id",
                "prior admission must be an earlier decision for the exact owner, context, and candidate",
            )

    def _cross_ledger_entry(self, record: dict[str, Any]) -> None:
        entry_record = self._target(record["entry_record_id"])
        if entry_record is not None and entry_record.get("context_ref_id") != record["context_ref_id"]:
            self._error(
                "relation.ledger-entry-context",
                "/entry_record_id",
                "ledger entry record must have the ledger context",
            )
        previous = self._target(record["previous_entry_id"])
        if previous is None:
            return
        if previous["context_ref_id"] != record["context_ref_id"]:
            self._error(
                "relation.ledger-context",
                "/previous_entry_id",
                "ledger predecessor must have the same context",
            )
        if previous["ledger_namespace"] != record["ledger_namespace"]:
            self._error(
                "relation.ledger-namespace",
                "/previous_entry_id",
                "ledger predecessor must have the same namespace",
            )
        if previous["ordinal"] + 1 != record["ordinal"]:
            self._error(
                "relation.ledger-ordinal",
                "/ordinal",
                "ledger predecessor ordinal must be exactly one lower",
            )

    def _cross_evidence_set(self, record: dict[str, Any]) -> None:
        ledger_members: set[str] = set()
        walked_entry_ids_by_namespace: dict[str, set[str]] = {}
        walked_entry_records: dict[str, dict[str, Any]] = {}
        for index, head in enumerate(record["ledger_heads"]):
            entry = self._target(head["head_record_id"])
            if entry is None:
                continue
            if (
                entry["context_ref_id"] != record["context_ref_id"]
                or entry["ledger_namespace"] != head["ledger_namespace"]
                or entry["ordinal"] != head["ordinal"]
            ):
                self._error(
                    "relation.ledger-head",
                    f"/ledger_heads/{index}",
                    "ledger head context, namespace, and ordinal must equal its entry",
                )
            walked: set[str] = set()
            ordinals: set[int] = set()
            cursor = entry
            while cursor is not None and cursor["id"] not in walked:
                walked.add(cursor["id"])
                walked_entry_ids_by_namespace.setdefault(
                    head["ledger_namespace"], set()
                ).add(cursor["id"])
                walked_entry_records[cursor["id"]] = cursor
                if cursor["ordinal"] in ordinals:
                    self._error(
                        "relation.ledger-duplicate-ordinal",
                        f"/ledger_heads/{index}",
                        "a ledger chain may contain each ordinal only once",
                    )
                ordinals.add(cursor["ordinal"])
                ledger_members.add(cursor["entry_record_id"])
                cursor = self._target(cursor["previous_entry_id"])
            if cursor is not None:
                self._error(
                    "relation.ledger-cycle",
                    f"/ledger_heads/{index}",
                    "ledger chain must be acyclic",
                )
            if entry is not None and 0 not in ordinals:
                self._error(
                    "relation.ledger-origin",
                    f"/ledger_heads/{index}",
                    "ledger chain must reach its zero ordinal origin",
                )

        parent_ids = record["parent_evidence_set_revision_ids"]
        if (len(parent_ids) > 1) != (record["merge_recipe_id"] is not None):
            self._error(
                "relation.evidence-merge-recipe",
                "/merge_recipe_id",
                "merge_recipe_id must be non-null exactly for a multi-parent evidence revision",
            )
        for parent_index, parent_id in enumerate(parent_ids):
            parent = self._target(parent_id)
            if parent is None:
                continue
            if (
                parent["authority_owner"] != record["authority_owner"]
                or parent["context_ref_id"] != record["context_ref_id"]
            ):
                self._error(
                    "relation.evidence-parent-scope",
                    f"/parent_evidence_set_revision_ids/{parent_index}",
                    "parent evidence revision must have the exact child authority and context",
                )
            for parent_head in parent["ledger_heads"]:
                child_entries = walked_entry_ids_by_namespace.get(
                    parent_head["ledger_namespace"]
                )
                if (
                    child_entries is None
                    or parent_head["head_record_id"] not in child_entries
                ):
                    self._error(
                        "relation.evidence-parent-ledger",
                        f"/parent_evidence_set_revision_ids/{parent_index}",
                        "every parent ledger head must be reachable from the child's matching namespace head",
                    )

        history_members: dict[str, set[str]] = {
            "rejected": set(),
            "quarantined": set(),
            "superseded": set(),
        }
        for entry_record in walked_entry_records.values():
            member = self._target(entry_record["entry_record_id"])
            if member is None:
                continue
            if member["kind"] == "admission-record":
                if member["outcome"] in {"rejected", "quarantined"}:
                    history_members[member["outcome"]].add(member["id"])
                if member["prior_admission_record_id"] is not None:
                    history_members["superseded"].add(
                        member["prior_admission_record_id"]
                    )
            elif member["kind"] == "evidence-record":
                history_members["superseded"].update(
                    member["corrects_record_ids"]
                )
                history_members["superseded"].update(
                    member["supersedes_record_ids"]
                )
        for bucket, member_ids in history_members.items():
            ordered = sorted(member_ids, key=lambda item: item.encode("utf-8"))
            expected = None if not ordered else semantic_root_v2(
                f"evidence-set-revision/history/{bucket}", ordered
            )
            if record["history_roots"][bucket] != expected:
                self._error(
                    "relation.evidence-history-root",
                    f"/history_roots/{bucket}",
                    "mechanical evidence history root does not match the complete pinned ledger chains",
                )
        for label in ("admission_partitions", "evidence_partitions"):
            for index, partition in enumerate(record[label]):
                self._shard_rows(
                    partition["object_descriptor_id"],
                    f"/{label}/{index}",
                    visit=False,
                )
        memberships: dict[str, list[str]] = {}
        for label, row_kind, domain in (
            (
                "admission_partitions",
                "admission-record",
                "evidence-set-revision/effective-admissions/partition",
            ),
            (
                "evidence_partitions",
                "evidence-record",
                "evidence-set-revision/effective-evidence/partition",
            ),
        ):
            members: list[str] = []
            for index, partition in enumerate(record[label]):
                path = f"/{label}/{index}"
                descriptor = self._target(partition["object_descriptor_id"])
                rows = self._shard_rows(partition["object_descriptor_id"], path)
                if descriptor is None or rows is None:
                    continue
                ids = [item.id for item in rows]
                members.extend(ids)
                values = [item.to_dict() for item in rows]
                order_keys = (
                    [item["candidate_record_id"] for item in values]
                    if row_kind == "admission-record"
                    else ids
                )
                if order_keys != sorted(
                    order_keys, key=lambda item: item.encode("utf-8")
                ) or len(order_keys) != len(set(order_keys)):
                    self._error(
                        "relation.membership-shard-order",
                        path,
                        "membership shard keys must be strict unique UTF-8 order",
                    )
                if descriptor["described_record_kind"] != row_kind:
                    self._error(
                        "relation.partition-record-kind",
                        path,
                        f"membership partition must describe {row_kind} rows",
                    )
                if (
                    descriptor["canonical_item_count"]
                    != partition["record_count"]
                    or len(ids) != partition["record_count"]
                ):
                    self._error(
                        "relation.partition-count",
                        path,
                        "partition and descriptor counts must equal exact shard membership",
                    )
                if descriptor["total_order_authority"] != record["authority_owner"]:
                    self._error(
                        "relation.membership-order-authority",
                        path,
                        "membership shard order authority must equal the evidence-set authority owner",
                    )
                if ids and (
                    descriptor["minimum_record_key"] != order_keys[0]
                    or descriptor["maximum_record_key"] != order_keys[-1]
                    or partition["minimum_record_key"] != order_keys[0]
                    or partition["maximum_record_key"] != order_keys[-1]
                ):
                    self._error(
                        "relation.partition-key-bounds",
                        path,
                        "membership min/max keys must equal first/last exact record IDs",
                    )
                expected_root = semantic_root_v2(
                    domain,
                    {
                        "partition_key": partition["partition_key"],
                        "record_ids": ids,
                        "shard_ordinal": partition["shard_ordinal"],
                    },
                )
                if partition["semantic_root"] != expected_root:
                    self._error(
                        "relation.partition-root",
                        f"{path}/semantic_root",
                        "membership partition root does not match exact shard order",
                    )
                for row in rows:
                    value = row.to_dict()
                    if (
                        value["kind"] != row_kind
                        or value["context_ref_id"] != record["context_ref_id"]
                    ):
                        self._error(
                            "relation.membership-partition-scope",
                            path,
                            "membership rows must have the exact kind and evidence-set context",
                        )
                        break
            if len(members) != len(set(members)):
                self._error(
                    "relation.membership-duplicate",
                    f"/{label}",
                    "effective membership may contain each record ID only once",
                )
            memberships[label] = members

        admissions = memberships["admission_partitions"]
        evidence_ids = memberships["evidence_partitions"]
        effective_evidence = set(evidence_ids)
        selected: list[str] = []
        for index, admission_id in enumerate(admissions):
            admission = self._target(admission_id)
            if admission is None:
                continue
            path = f"/admission_partitions/{index}"
            if (
                admission["outcome"] != "admitted"
                or not admission["eligible_for_materialization"]
                or admission["candidate_kind"] != "evidence-record"
            ):
                self._error(
                    "relation.effective-admission-outcome",
                    path,
                    "effective admission must admit an eligible evidence record",
                )
            candidate_id = admission["candidate_record_id"]
            selected.append(candidate_id)
            if candidate_id not in effective_evidence:
                self._error(
                    "relation.effective-admission-membership",
                    path,
                    "effective admission target must occur in effective evidence membership",
                )
            if admission["context_ref_id"] != record["context_ref_id"]:
                self._error(
                    "relation.effective-admission-context",
                    path,
                    "effective admission must have the evidence-set context",
                )
            if admission_id not in ledger_members:
                self._error(
                    "relation.effective-admission-ledger",
                    path,
                    "effective admission must occur in a pinned ledger chain",
                )
        if sorted(selected, key=lambda item: item.encode("utf-8")) != sorted(
            evidence_ids, key=lambda item: item.encode("utf-8")
        ):
            self._error(
                "relation.effective-selection",
                "/admission_partitions",
                "selection must contain exactly one admission per effective evidence record",
            )
        for index, evidence_id in enumerate(evidence_ids):
            evidence = self._target(evidence_id)
            if (
                evidence is not None
                and evidence["context_ref_id"] != record["context_ref_id"]
            ):
                self._error(
                    "relation.effective-evidence-context",
                    f"/evidence_partitions/{index}",
                    "effective evidence must have the evidence-set context",
                )
            if evidence_id not in ledger_members:
                self._error(
                    "relation.effective-evidence-ledger",
                    f"/evidence_partitions/{index}",
                    "effective evidence must occur in a pinned ledger chain",
                )

    def _cross_dependency(self, record: dict[str, Any]) -> None:
        self._require_policy(
            "dependency-closure",
            record,
            _scope_from_binding(record["recipe_owner"]),
        )
        components_by_key = {
            item["component_key"]: item for item in record["input_components"]
        }
        component_ids = {
            item["component_id"] for item in record["input_components"]
        }
        recipe = self._target(record["recipe_id"])
        if recipe is not None:
            expected_components = {
                recipe["implementation"]["implementation_id"],
                *(item["implementation_id"] for item in recipe["derivation_steps"]),
            }
            if not expected_components.issubset(component_ids):
                self._error(
                    "relation.dependency-implementation-closure",
                    "/input_components",
                    "dependency components must include recipe and step implementations",
                )
            for component in record["input_components"]:
                if component["component_id"] in expected_components and (
                    component["component_kind"] != "implementation"
                    or component["authority_binding"] != record["recipe_owner"]
                ):
                    self._error(
                        "relation.dependency-implementation-authority",
                        "/input_components",
                        "recipe and step implementation components must use the exact recipe owner binding",
                    )
            invalidation_components = [
                item
                for item in record["input_components"]
                if item["component_id"] == record["invalidation_policy_id"]
            ]
            if len(invalidation_components) != 1 or (
                invalidation_components[0]["component_kind"] != "policy"
                or invalidation_components[0]["authority_binding"]
                != record["recipe_owner"]
            ):
                self._error(
                    "relation.dependency-invalidation-authority",
                    "/input_components",
                    "dependency components must bind the invalidation policy under the exact recipe owner",
                )
            if record["recipe_owner"] != recipe["recipe_owner"]:
                self._error(
                    "relation.dependency-recipe-owner",
                    "/recipe_owner",
                    "dependency recipe_owner must equal the exact recipe owner",
                )
            if (
                record["recipe_implementation_id"]
                != recipe["implementation"]["implementation_id"]
                or record["parameter_values_object_descriptor_id"]
                != recipe["canonical_parameter_values_object_descriptor_id"]
                or record["invalidation_policy_id"]
                != recipe["policies"]["invalidation"]
            ):
                self._error(
                    "relation.dependency-recipe-binding",
                    "/recipe_id",
                    "dependency must bind exact recipe implementation, parameters, and invalidation policy",
                )

        admission_partitions: dict[tuple[str, str, int], set[str]] = {}
        evidence_partitions: dict[tuple[str, str, int], set[str]] = {}
        for evidence_set_id in record["evidence_set_revision_ids"]:
            evidence_set = self._target(evidence_set_id)
            if evidence_set is None:
                continue
            for label, target in (
                ("admission_partitions", admission_partitions),
                ("evidence_partitions", evidence_partitions),
            ):
                for partition in evidence_set[label]:
                    rows = self.rows.get(partition["object_descriptor_id"]) or ()
                    target[
                        (
                            evidence_set_id,
                            partition["partition_key"],
                            partition["shard_ordinal"],
                        )
                    ] = {item.id for item in rows}
        graph_revision_by_contract = {
            item["graph_input_contract_key"]: item["graph_revision_id"]
            for item in record["input_graph_bindings"]
        }
        graph_partitions: dict[
            tuple[str, str, str, int, str], set[str]
        ] = {}
        for contract_key, graph_revision_id in graph_revision_by_contract.items():
            revision = self._target(graph_revision_id)
            if revision is None:
                continue
            for partition in revision["partitions"]:
                rows = self.rows.get(partition["object_descriptor_id"]) or ()
                graph_partitions[
                    (
                        contract_key,
                        graph_revision_id,
                        partition["partition_key"],
                        partition["shard_ordinal"],
                        partition["record_kind"],
                    )
                ] = {item.id for item in rows}

        for index, footprint in enumerate(record["footprints"]):
            path = f"/footprints/{index}"
            selected_admissions: set[str] = set()
            for item in footprint["admission_partitions"]:
                key = (
                    item["evidence_set_revision_id"],
                    item["partition_key"],
                    item["shard_ordinal"],
                )
                if key not in admission_partitions:
                    self._error(
                        "relation.dependency-admission-partition",
                        path,
                        "admission partition must name an exact declared input shard",
                    )
                selected_admissions.update(admission_partitions.get(key, ()))
            selected_evidence: set[str] = set()
            for item in footprint["evidence_partitions"]:
                key = (
                    item["evidence_set_revision_id"],
                    item["partition_key"],
                    item["shard_ordinal"],
                )
                if key not in evidence_partitions:
                    self._error(
                        "relation.dependency-evidence-partition",
                        path,
                        "evidence partition must name an exact declared input shard",
                    )
                selected_evidence.update(evidence_partitions.get(key, ()))
            selected_graph: dict[str, set[str]] = {}
            for item in footprint["source_graph_partitions"]:
                key = (
                    item["graph_input_contract_key"],
                    item["graph_revision_id"],
                    item["partition_key"],
                    item["shard_ordinal"],
                    item["record_kind"],
                )
                if (
                    graph_revision_by_contract.get(
                        item["graph_input_contract_key"]
                    )
                    != item["graph_revision_id"]
                    or key not in graph_partitions
                ):
                    self._error(
                        "relation.dependency-graph-partition",
                        path,
                        "source graph partition must name an exact shard under its declared graph input contract",
                    )
                selected_graph.setdefault(
                    item["graph_input_contract_key"], set()
                ).update(graph_partitions.get(key, ()))
            for ids, available, code in (
                (
                    footprint["admission_record_ids"],
                    selected_admissions,
                    "relation.dependency-admission-membership",
                ),
                (
                    footprint["evidence_record_ids"],
                    selected_evidence,
                    "relation.dependency-evidence-membership",
                ),
            ):
                if not set(ids).issubset(available):
                    self._error(
                        code,
                        path,
                        "record-level dependency must occur in a named exact physical input partition",
                    )
            for source in footprint["source_graph_records"]:
                contract_key = source["graph_input_contract_key"]
                if (
                    contract_key not in graph_revision_by_contract
                    or source["graph_record_id"]
                    not in selected_graph.get(contract_key, set())
                ):
                    self._error(
                        "relation.dependency-graph-membership",
                        path,
                        "source graph dependency must occur in an exact physical input partition under the same contract key",
                    )
            if not set(footprint["component_keys"]).issubset(
                components_by_key
            ):
                self._error(
                    "relation.dependency-component-membership",
                    path,
                    "footprint component keys must resolve exact declared input component bindings",
                )

    def _cross_graph_revision(self, record: dict[str, Any]) -> None:
        self._require_policy(
            "graph-materialization",
            record,
            _scope_from_binding(record["recipe_owner"]),
        )
        self._require_policy(
            "graph-category-semantics",
            record,
            _scope_from_binding(record["authority_owner"]),
        )
        dependency = self._target(record["dependency_manifest_id"])
        if dependency is not None:
            field_pairs = (
                "authority_owner",
                "recipe_owner",
                "recipe_id",
                "recipe_implementation_id",
                "parameter_values_object_descriptor_id",
                "context_ref_id",
                "evidence_set_revision_ids",
                "input_graph_bindings",
            )
            for field in field_pairs:
                if dependency[field] != record[field]:
                    self._error(
                        "relation.dependency-agreement",
                        f"/{field}",
                        f"graph revision and dependency manifest must bind the same {field}",
                    )
            graph_partitions = [
                (
                    item["partition_key"],
                    item["shard_ordinal"],
                    item["record_kind"],
                )
                for item in record["partitions"]
            ]
            footprint_partitions = [
                (
                    item["partition_key"],
                    item["shard_ordinal"],
                    item["record_kind"],
                )
                for footprint in dependency["footprints"]
                for item in footprint["output_partitions"]
            ]
            if set(graph_partitions) != set(footprint_partitions) or len(
                footprint_partitions
            ) != len(set(footprint_partitions)):
                self._error(
                    "relation.dependency-partitions",
                    "/dependency_manifest_id",
                    "dependency footprints must cover every exact graph output physical partition once",
                )
            else:
                rank = {item: index for index, item in enumerate(graph_partitions)}
                first_ranks = [
                    min(
                        rank[
                            (
                                item["partition_key"],
                                item["shard_ordinal"],
                                item["record_kind"],
                            )
                        ]
                        for item in footprint["output_partitions"]
                    )
                    for footprint in dependency["footprints"]
                ]
                if first_ranks != sorted(first_ranks):
                    self._error(
                        "relation.dependency-footprint-order",
                        "/dependency_manifest_id",
                        "dependency footprints must follow graph output partition production order",
                    )
        recipe = self._target(record["recipe_id"])
        if recipe is not None:
            if record["recipe_owner"] != recipe["recipe_owner"]:
                self._error(
                    "relation.recipe-owner",
                    "/recipe_owner",
                    "graph revision recipe_owner must equal the exact recipe owner",
                )
            if recipe["implementation"]["implementation_id"] != record["recipe_implementation_id"]:
                self._error(
                    "relation.recipe-implementation",
                    "/recipe_implementation_id",
                    "graph revision must bind the selected recipe implementation",
                )
            if recipe["canonical_parameter_values_object_descriptor_id"] != record["parameter_values_object_descriptor_id"]:
                self._error(
                    "relation.recipe-parameters",
                    "/parameter_values_object_descriptor_id",
                    "graph revision must bind the recipe canonical parameter values",
                )
            output_contract = next(
                (
                    item
                    for item in recipe["output_contracts"]
                    if item["graph_family_id"] == record["graph_family_id"]
                    and item["category_id"] == record["category_id"]
                    and item["resolution_id"] == record["resolution_id"]
                ),
                None,
            )
            if output_contract is None:
                self._error(
                    "relation.recipe-output-contract",
                    "/recipe_id",
                    "graph revision coordinate has no exact recipe output contract",
                )
            elif output_contract[
                "graph_record_schema_object_descriptor_id"
            ] not in record["record_schema_object_descriptor_ids"]:
                self._error(
                    "relation.recipe-output-schema",
                    "/record_schema_object_descriptor_ids",
                    "graph revision must bind its recipe output record schema",
                )
            accepted_graph_inputs = {
                item["graph_input_contract_key"]: item
                for item in recipe["accepted_inputs"]["graph_input_contracts"]
            }
            for index, binding in enumerate(record["input_graph_bindings"]):
                accepted = accepted_graph_inputs.get(
                    binding["graph_input_contract_key"]
                )
                revision = self._target(binding["graph_revision_id"])
                if accepted is None:
                    self._error(
                        "relation.graph-input-contract",
                        f"/input_graph_bindings/{index}/graph_input_contract_key",
                        "graph input binding key must name an exact accepted recipe graph contract",
                    )
                    continue
                if revision is None:
                    continue
                accepted_coordinate = (
                    accepted["graph_family_id"],
                    accepted["category_id"],
                    accepted["resolution_id"],
                )
                revision_coordinate = (
                    revision["graph_family_id"],
                    revision["category_id"],
                    revision["resolution_id"],
                )
                if revision_coordinate != accepted_coordinate:
                    self._error(
                        "relation.graph-input-contract-scope",
                        f"/input_graph_bindings/{index}",
                        "bound graph revision coordinate must satisfy the keyed accepted graph contract",
                    )

        ids_by_kind: dict[str, list[str]] = {
            item: [] for item in _GRAPH_RECORD_KIND_ORDER
        }
        for index, partition in enumerate(record["partitions"]):
            self._shard_rows(
                partition["object_descriptor_id"],
                f"/partitions/{index}",
                visit=False,
            )
        all_candidate_rows = [
            row
            for partition in record["partitions"]
            for row in (
                self.rows.get(partition["object_descriptor_id"]) or ()
            )
        ]
        all_candidate_ids = [row.id for row in all_candidate_rows]
        all_logical_keys = [
            row.to_dict()["logical_key"] for row in all_candidate_rows
        ]
        if len(all_candidate_ids) != len(set(all_candidate_ids)):
            self._error(
                "relation.graph-record-duplicate",
                "/partitions",
                "a graph record ID may occur in only one candidate shard",
            )
        if len(all_logical_keys) != len(set(all_logical_keys)):
            self._error(
                "relation.graph-logical-key-duplicate",
                "/partitions",
                "candidate graph logical keys must be globally unique across shards",
            )
        range_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for partition in record["partitions"]:
            range_groups.setdefault(
                (partition["partition_key"], partition["record_kind"]), []
            ).append(partition)
        for shards in range_groups.values():
            for previous, current in zip(shards, shards[1:]):
                if previous["maximum_record_key"].encode("utf-8") >= current[
                    "minimum_record_key"
                ].encode("utf-8"):
                    self._error(
                        "relation.graph-shard-range",
                        "/partitions",
                        "graph shard key ranges must be strictly monotonic and nonoverlapping by ordinal",
                    )
                    break
        current_graph_ids = {
            row.id for row in all_candidate_rows
        }
        input_revision_by_contract = {
            item["graph_input_contract_key"]: item["graph_revision_id"]
            for item in record["input_graph_bindings"]
        }
        input_graph_ids_by_contract: dict[str, set[str]] = {}
        for contract_key, revision_id in input_revision_by_contract.items():
            input_revision = self._target(revision_id)
            if input_revision is None:
                continue
            record_ids = input_graph_ids_by_contract.setdefault(
                contract_key, set()
            )
            for partition in input_revision["partitions"]:
                record_ids.update(
                    row.id
                    for row in (
                        self.rows.get(partition["object_descriptor_id"]) or ()
                    )
                )
        input_graph_ids = set().union(
            *input_graph_ids_by_contract.values()
        ) if input_graph_ids_by_contract else set()
        available_evidence_ids: set[str] = set()
        for evidence_set_id in record["evidence_set_revision_ids"]:
            evidence_set = self._target(evidence_set_id)
            if evidence_set is None:
                continue
            for partition in evidence_set["evidence_partitions"]:
                available_evidence_ids.update(
                    row.id
                    for row in (
                        self.rows.get(partition["object_descriptor_id"]) or ()
                    )
                )
        for index, partition in enumerate(record["partitions"]):
            path = f"/partitions/{index}"
            descriptor = self._target(partition["object_descriptor_id"])
            rows = self._shard_rows(partition["object_descriptor_id"], path)
            if descriptor is None or rows is None:
                continue
            if descriptor["described_record_kind"] != "graph-record":
                self._error(
                    "relation.partition-record-kind",
                    path,
                    "graph partition descriptor must describe graph-record rows",
                )
            if descriptor["described_schema_object_descriptor_id"] not in record["record_schema_object_descriptor_ids"]:
                self._error(
                    "relation.partition-schema",
                    path,
                    "partition schema descriptor must be declared by the graph revision",
                )
            if descriptor["canonical_item_count"] != partition["record_count"] or len(rows) != partition["record_count"]:
                self._error(
                    "relation.partition-count",
                    path,
                    "partition and descriptor counts must equal exact shard membership",
                )
            if (
                descriptor["minimum_record_key"] != partition["minimum_record_key"]
                or descriptor["maximum_record_key"] != partition["maximum_record_key"]
                or descriptor["total_order_policy_id"] != record["total_order_policy_id"]
                or descriptor["total_order_authority"] != record["recipe_owner"]
            ):
                self._error(
                    "relation.partition-descriptor",
                    path,
                    "partition key bounds, order policy, and recipe order authority must agree with its descriptor",
                )
            ids: list[str] = []
            logical_keys: list[str] = []
            for row in rows:
                value = row.to_dict()
                ids.append(row.id)
                logical_keys.append(value.get("logical_key", ""))
                body_kind = value.get("body", {}).get("record_kind")
                if body_kind in ids_by_kind:
                    ids_by_kind[body_kind].append(row.id)
                required_pairs = (
                    ("authority_owner", record["authority_owner"]),
                    ("graph_family_id", record["graph_family_id"]),
                    ("category_id", record["category_id"]),
                    ("resolution_id", record["resolution_id"]),
                    ("context_ref_id", record["context_ref_id"]),
                    ("recipe_id", record["recipe_id"]),
                )
                if value["kind"] != "graph-record" or any(
                    value[field] != expected for field, expected in required_pairs
                ) or body_kind != partition["record_kind"]:
                    self._error(
                        "relation.graph-partition-scope",
                        path,
                        "graph rows must match revision owner, family, category, resolution, context, recipe, and record kind",
                    )
                    break
                row_evidence_ids = {
                    item["evidence_record_id"]
                    for item in value["evidence_inputs"]
                }
                row_source_ids = {
                    item["graph_record_id"]
                    for item in value["source_graph_inputs"]
                }
                if not row_evidence_ids.issubset(available_evidence_ids):
                    self._error(
                        "relation.graph-evidence-input",
                        path,
                        "graph row evidence must belong to declared evidence-set inputs",
                    )
                if not row_source_ids.issubset(
                    current_graph_ids | input_graph_ids
                ):
                    self._error(
                        "relation.graph-source-input",
                        path,
                        "graph row sources must belong to the candidate revision or declared input graphs",
                    )
                if recipe is not None:
                    step = next(
                        (
                            item
                            for item in recipe["derivation_steps"]
                            if item["derivation_step_id"]
                            == value["derivation_step_id"]
                        ),
                        None,
                    )
                    if step is not None:
                        for source_input in value["source_graph_inputs"]:
                            source_id = source_input["graph_record_id"]
                            source = self._target(source_id)
                            if source is None:
                                continue
                            allowed_modes: set[str] = set()
                            if source_id in input_graph_ids_by_contract.get(
                                source_input["contract_key"], set()
                            ):
                                allowed_modes.add("accepted-graph-input")
                            if source_id in current_graph_ids:
                                allowed_modes.update(
                                    {"predecessor-output", "local-output"}
                                )
                            matches = self._matching_source_contract_kinds(
                                recipe, step, value, source, source_input
                            )
                            if (
                                source_input["source_kind"] not in matches
                                or source_input["source_kind"] not in allowed_modes
                            ):
                                self._error(
                                    "relation.derivation-source-membership",
                                    path,
                                    "source contract mode must agree with candidate or declared input graph membership",
                                )
                if body_kind == "refinement-mapping":
                    endpoints = (
                        value["body"]["coarse_endpoint"],
                        *value["body"]["fine_endpoints"],
                    )
                    for endpoint in endpoints:
                        endpoint_sources = [
                            item
                            for item in value["source_graph_inputs"]
                            if item["graph_record_id"]
                            == endpoint["source_node_record_id"]
                            and item["source_kind"] == "accepted-graph-input"
                            and input_revision_by_contract.get(
                                item["contract_key"]
                            )
                            == endpoint["source_graph_revision_id"]
                        ]
                        if not endpoint_sources:
                            self._error(
                                "relation.refinement-input-revision",
                                path,
                                "every refinement endpoint must pair its node with the exact keyed accepted graph revision",
                            )
                if body_kind == "node":
                    self._cross_node(value, recipe, path)
            if logical_keys and (
                logical_keys[0] != partition["minimum_record_key"]
                or logical_keys[-1] != partition["maximum_record_key"]
            ):
                self._error(
                    "relation.partition-key-bounds",
                    path,
                    "partition min/max keys must equal the first/last exact shard record keys",
                )
            if logical_keys != sorted(
                logical_keys, key=lambda item: item.encode("utf-8")
            ) or len(logical_keys) != len(set(logical_keys)):
                self._error(
                    "relation.graph-shard-order",
                    path,
                    "graph shard logical keys must be strict UTF-8 order",
                )
            expected_root = semantic_root_v2(
                f"graph-revision/partition/{partition['record_kind']}",
                {
                    "partition_key": partition["partition_key"],
                    "record_ids": ids,
                    "shard_ordinal": partition["shard_ordinal"],
                },
            )
            if partition["semantic_root"] != expected_root:
                self._error(
                    "relation.partition-root",
                    f"{path}/semantic_root",
                    "graph partition root does not match exact shard order",
                )

        for record_kind, count_field in _COUNT_FIELD_BY_RECORD_KIND.items():
            ids = ids_by_kind[record_kind]
            declared = record["record_roots"][count_field]
            expected = None if not ids else semantic_root_v2(
                f"graph-revision/records/{record_kind}", ids
            )
            if declared != expected:
                self._error(
                    "relation.record-root",
                    f"/record_roots/{count_field}",
                    "per-kind record root does not match canonical shard membership",
                )
        expected_frontiers = sorted(
            ids_by_kind["frontier"], key=lambda item: item.encode("utf-8")
        )
        expected_conflicts = sorted(
            ids_by_kind["conflict"], key=lambda item: item.encode("utf-8")
        )
        if record["frontier_record_ids"] != expected_frontiers:
            self._error(
                "relation.frontier-membership",
                "/frontier_record_ids",
                "frontier IDs must equal frontier records in the revision",
            )
        if record["conflict_record_ids"] != expected_conflicts:
            self._error(
                "relation.conflict-membership",
                "/conflict_record_ids",
                "conflict IDs must equal conflict records in the revision",
            )

    def _cross_node(
        self,
        graph_record: dict[str, Any],
        recipe: dict[str, Any] | None,
        path: str,
    ) -> None:
        if recipe is None:
            return
        output = next(
            (
                item
                for item in recipe["output_contracts"]
                if item["graph_family_id"] == graph_record["graph_family_id"]
                and item["category_id"] == graph_record["category_id"]
                and item["resolution_id"] == graph_record["resolution_id"]
            ),
            None,
        )
        if output is None:
            self._error(
                "relation.recipe-output-contract",
                path,
                "node graph scope has no matching recipe output contract",
            )
            return
        body = graph_record["body"]
        subject = next(
            (
                item
                for item in output["subject_contracts"]
                if item["node_kind"] == body["node_kind"]
            ),
            None,
        )
        if subject is None:
            self._error(
                "relation.subject-contract",
                path,
                "node kind has no exact recipe subject contract",
            )
            return
        descriptor = self._target(body["subject_identity_object_descriptor_id"])
        raw = self.blobs.get(body["subject_identity_object_descriptor_id"])
        if descriptor is None or raw is None:
            return
        if (
            descriptor["representation"] != "canonical-json-value"
            or descriptor["described_schema_object_descriptor_id"]
            != subject["subject_identity_schema_object_descriptor_id"]
            or descriptor["described_record_kind"] is not None
        ):
            self._error(
                "relation.subject-descriptor",
                path,
                "node identity descriptor must match the recipe subject kind and schema",
            )
            return
        try:
            identity = parse_canonical_json(raw)
            if type(identity) is not dict:
                raise CanonicalJsonError("subject identity must be an object")
            expected_id = content_id(subject["subject_kind"], identity)
        except (CanonicalJsonError, TypeError) as exc:
            self._error(
                "relation.subject-identity",
                path,
                f"subject identity bytes are invalid: {exc}",
            )
            return
        if body["subject_id"] != expected_id:
            self._error(
                "relation.subject-id",
                path,
                "node subject_id must equal the V2 identity of the exact subject tuple",
            )

    def _cross_graph_set(self, record: dict[str, Any]) -> None:
        member_revisions: list[dict[str, Any]] = []
        for collection_name in ("members", "join_graphs", "refinement_graphs"):
            for index, member in enumerate(record[collection_name]):
                path = f"/{collection_name}/{index}"
                revision = self._target(member["graph_revision_id"])
                if revision is None:
                    continue
                member_revisions.append(revision)
                if collection_name == "members" and (
                    revision["category_id"] != member["category_id"]
                    or revision["resolution_id"] != member["resolution_id"]
                ):
                    self._error(
                        "relation.graph-member-scope",
                        path,
                        "member category and resolution must match its graph revision",
                    )
                alignment = member["alignment"]
                exact_pairs = (
                    (
                        "compatibility_rule_id",
                        alignment["compatibility_rule_id"],
                        record["compatibility_rule_id"],
                    ),
                    (
                        "compatibility_constraints_object_descriptor_id",
                        alignment[
                            "compatibility_constraints_object_descriptor_id"
                        ],
                        record[
                            "compatibility_constraints_object_descriptor_id"
                        ],
                    ),
                    (
                        "member_context_ref_id",
                        alignment["member_context_ref_id"],
                        revision["context_ref_id"],
                    ),
                    (
                        "target_context_ref_id",
                        alignment["target_context_ref_id"],
                        record["context_ref_id"],
                    ),
                    (
                        "member_evidence_set_revision_ids",
                        alignment["member_evidence_set_revision_ids"],
                        revision["evidence_set_revision_ids"],
                    ),
                    (
                        "target_evidence_set_revision_ids",
                        alignment["target_evidence_set_revision_ids"],
                        record["evidence_set_revision_ids"],
                    ),
                )
                for field, actual, expected in exact_pairs:
                    if actual != expected:
                        self._error(
                            "relation.alignment-input",
                            f"{path}/alignment/{field}",
                            "alignment input must equal the exact member or graph-set value",
                        )
                if (
                    alignment["context_result"] == "exact"
                    and revision["context_ref_id"] != record["context_ref_id"]
                ):
                    self._error(
                        "relation.alignment-context-exact",
                        f"{path}/alignment/context_result",
                        "exact context alignment requires byte-equal context IDs",
                    )
                if (
                    alignment["evidence_result"] == "exact"
                    and revision["evidence_set_revision_ids"]
                    != record["evidence_set_revision_ids"]
                ):
                    self._error(
                        "relation.alignment-evidence-exact",
                        f"{path}/alignment/evidence_result",
                        "exact evidence alignment requires byte-equal evidence-set arrays",
                    )
                if (
                    alignment["context_result"] == "exact"
                    and revision["profile_support_bindings"]
                    != record["profile_support_bindings"]
                ):
                    self._error(
                        "relation.alignment-profile-exact",
                        f"{path}/alignment/context_result",
                        "exact context alignment requires byte-equal profile support bindings",
                    )
                decision_scope = _scope_from_binding(
                    alignment["decision_authority"]
                )
                self._require_policy(
                    "compatibility-alignment", record, decision_scope
                )
        available_conflicts = {
            item
            for revision in member_revisions
            for item in revision["conflict_record_ids"]
        }
        if set(record["conflict_record_ids"]) != available_conflicts:
            self._error(
                "relation.graph-set-conflicts",
                "/conflict_record_ids",
                "graph-set conflicts must equal the complete retained union from member and auxiliary revisions",
            )

    def _cross_reference(self, record: dict[str, Any]) -> None:
        prior = self._target(record["prior_event_id"])
        if prior is not None:
            for field in (
                "context_ref_id",
                "reference_namespace",
                "reference_name",
            ):
                if prior[field] != record[field]:
                    self._error(
                        "relation.reference-chain-scope",
                        f"/{field}",
                        f"{field} must equal the prior event",
                    )
            if prior["transition_sequence"] + 1 != record["transition_sequence"]:
                self._error(
                    "relation.reference-sequence",
                    "/transition_sequence",
                    "reference sequence must be contiguous",
                )
            if prior["new_target_id"] != record["expected_old_target_id"]:
                self._error(
                    "relation.reference-cas-target",
                    "/expected_old_target_id",
                    "expected old target must equal the prior event target",
                )
        target = self._target(record["new_target_id"])
        if target is not None and target.get("context_ref_id") != record["context_ref_id"]:
            self._error(
                "relation.reference-target-context",
                "/new_target_id",
                "new reference target must have the reference context",
            )


def _validate_relations(
    record: dict[str, Any],
    refs: tuple[ReferenceExpectation, ...],
    relations: RelationResolvers,
) -> None:
    _RelationValidator(relations).validate(record, refs)


def _finish(
    record: dict[str, Any],
    references: tuple[ReferenceExpectation, ...],
    relations: RelationResolvers | None,
) -> ValidatedRecord:
    canonical = canonical_json_bytes(record)
    result = ValidatedRecord(
        id=record["id"],
        kind=record["kind"],
        format=record["format"],
        schema_id=record["schema_id"],
        canonical_bytes=canonical,
        references=references,
    )
    if relations is not None:
        _validate_relations(record, references, relations)
    return result


def seal_record(
    candidate: Mapping[str, Any],
    *,
    relations: RelationResolvers | None = None,
) -> ValidatedRecord:
    """Validate and seal one ID-less candidate from a detached snapshot.

    Unknown fields and every other closed-schema failure are rejected before
    ``record_content_id`` is invoked.  The caller's mapping is never mutated.
    """

    snapshot = _domain_snapshot(candidate)
    validate_candidate_schema(snapshot)
    probe = dict(snapshot)
    probe["id"] = f"{probe['kind']}:sha256:{'0' * 64}"
    references = _semantic_validate(probe)
    try:
        snapshot["id"] = record_content_id(snapshot)
    except CanonicalJsonError as exc:
        raise RecordValidationError(
            (_diagnostic(ValidationPhase.IDENTITY, "identity.compute", "/id", str(exc)),)
        ) from exc
    validate_closed_schema(snapshot)
    # Re-run over the exact sealed value so no ID-dependent future invariant
    # can accidentally be checked only against the placeholder.
    references = _semantic_validate(snapshot)
    return _finish(snapshot, references, relations)


def validate_record(
    record: Mapping[str, Any],
    *,
    relations: RelationResolvers | None = None,
) -> ValidatedRecord:
    """Validate a semantic record value and return an immutable snapshot."""

    snapshot = _domain_snapshot(record)
    validate_closed_schema(snapshot)
    references = _semantic_validate(snapshot)
    try:
        validate_content_id(snapshot)
    except CanonicalJsonError as exc:
        raise RecordValidationError(
            (_diagnostic(ValidationPhase.IDENTITY, "identity.mismatch", "/id", str(exc)),)
        ) from exc
    return _finish(snapshot, references, relations)


def load_canonical_record(
    raw: bytes | bytearray | memoryview,
    *,
    relations: RelationResolvers | None = None,
) -> ValidatedRecord:
    """Load a record only from its exact canonical V2 byte representation."""

    if type(raw) not in (bytes, bytearray, memoryview):
        raise TypeError("canonical record input must be bytes-like")
    try:
        encoded = bytes(raw)
    except Exception as exc:
        raise TypeError(
            f"canonical record byte snapshot failed with {type(exc).__name__}"
        ) from None
    try:
        value = parse_canonical_json(encoded)
    except CanonicalJsonError as exc:
        raise RecordValidationError(
            (_diagnostic(ValidationPhase.DOMAIN, "domain.noncanonical-bytes", "", str(exc)),)
        ) from exc
    if type(value) is not dict:
        raise RecordValidationError(
            (_diagnostic(ValidationPhase.DOMAIN, "domain.record-object", "", "record bytes must encode an object"),)
        )
    result = validate_record(value, relations=relations)
    if result.canonical_bytes != encoded:
        # parse_canonical_json already proves this; retain a defensive exact-byte
        # assertion so later refactors cannot turn this into a normalizing load.
        raise RecordValidationError(
            (_diagnostic(ValidationPhase.DOMAIN, "domain.byte-snapshot", "", "loaded bytes changed during validation"),)
        )
    return result


PublicationRecord: TypeAlias = (
    ValidatedRecord | Mapping[str, Any] | bytes | bytearray | memoryview
)


def validate_publication(
    records: Iterable[PublicationRecord],
    *,
    relations: RelationResolvers,
) -> tuple[ValidatedRecord, ...]:
    """Validate one closed C01 publication record set.

    The returned tuple preserves input order.  Publication requires pinned
    C01 records, exact object bytes, a structural external-record registry,
    explicit trusted authority-adapter registrations, and owner-policy
    validation.  Single-record callers may omit them and remain explicitly
    pre-publication.  The two-pass local index permits forward references
    inside the candidate batch; a storage publisher must still persist
    immutable dependencies before exposing an authoritative head.
    """

    if (
        relations.record_resolver is None
        or relations.blob_resolver is None
        or relations.external_reference_resolver is None
        or relations.trusted_adapters is None
        or relations.policy_validator is None
    ):
        raise ValueError(
            "publication validation requires pinned record_resolver and blob_resolver "
            "plus a pinned structural external registry, trusted authority-adapter "
            "registrations, and owner-policy validation port"
        )
    try:
        publication_items = tuple(records)
    except Exception as exc:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.publication-input",
                    "",
                    f"publication iterable failed with {type(exc).__name__}",
                ),
            )
        ) from None
    validated: list[ValidatedRecord] = []
    local_by_id: dict[str, ValidatedRecord] = {}
    for index, item in enumerate(publication_items):
        try:
            if type(item) is ValidatedRecord:
                result = _coerce_resolved_record(item)
            elif type(item) in (bytes, bytearray, memoryview):
                result = load_canonical_record(item)
            else:
                result = validate_record(item)
        except RecordValidationError:
            raise
        except Exception as exc:
            raise RecordValidationError(
                (
                    _diagnostic(
                        ValidationPhase.DOMAIN,
                        "domain.publication-record",
                        f"/{index}",
                        f"publication record coercion failed with {type(exc).__name__}",
                    ),
                )
            ) from None
        if result.id in local_by_id:
            raise RecordValidationError(
                (
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.duplicate-publication-record",
                        f"/{index}",
                        "publication record IDs must be unique",
                    ),
                )
            )
        local_by_id[result.id] = result
        validated.append(result)
    if not validated:
        raise ValueError("publication validation requires at least one record")

    external_resolver = relations.record_resolver

    def publication_resolver(record_id: str) -> RecordResolution | None:
        local = local_by_id.get(record_id)
        if local is not None:
            return local
        return external_resolver(record_id)

    pinned_relations = RelationResolvers(
        record_resolver=publication_resolver,
        blob_resolver=relations.blob_resolver,
        external_reference_resolver=relations.external_reference_resolver,
        trusted_adapters=relations.trusted_adapters,
        policy_validator=relations.policy_validator,
        require_complete=True,
    )
    validator = _RelationValidator(pinned_relations)
    validator.validate_many(
        (item.to_dict(), item.references) for item in validated
    )
    return tuple(validated)


__all__ = [
    "AdapterValidatedReference",
    "AuthorityScope",
    "BlobResolver",
    "ExternalReferenceKey",
    "ExternalReferenceResolver",
    "PolicyValidationRequest",
    "PolicyValidationResult",
    "PolicyValidator",
    "PublicationRecord",
    "RecordResolution",
    "RecordResolver",
    "ReferenceExpectation",
    "RelationResolvers",
    "SEMANTIC_ROOT_DOMAIN",
    "TrustedAuthorityAdapter",
    "ValidatedExternalReference",
    "ValidatedRecord",
    "load_canonical_record",
    "seal_record",
    "semantic_root_v2",
    "validate_publication",
    "validate_record",
]
