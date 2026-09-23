"""Closed, offline JSON Schema registry for Crucible V2 records.

The registry is deliberately finite.  A record is dispatched only by an exact
``(kind, format, schema_version, schema_id, canonicalizer)`` header and every
``workbench://`` resource used by this record family is loaded from the source
tree.  There is no retrieval callback and therefore no network fallback.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, NoReturn

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from workbench_api.canonical import CANONICALIZER_ID, CanonicalJsonError, parse_json_strict


SCHEMA_VERSION = 2
SCHEMA_URI_PREFIX = "workbench://schemas/crucible/"
_SCHEMA_DIRECTORY = _module_resource_root(__file__, 'crucible') / "schemas"


class ValidationPhase(str, Enum):
    """Ordered validation boundaries used in machine-readable diagnostics."""

    DOMAIN = "domain"
    SCHEMA = "schema"
    SEMANTIC = "semantic"
    IDENTITY = "identity"
    RELATION = "relation"


_PHASE_ORDER = {
    ValidationPhase.DOMAIN: 0,
    ValidationPhase.SCHEMA: 1,
    ValidationPhase.SEMANTIC: 2,
    ValidationPhase.IDENTITY: 3,
    ValidationPhase.RELATION: 4,
}


@dataclass(frozen=True, slots=True)
class ValidationDiagnostic:
    """A deterministic validation failure at one exact JSON Pointer."""

    phase: ValidationPhase
    code: str
    path: str
    message: str


def _diagnostic_sort_key(diagnostic: ValidationDiagnostic) -> tuple[Any, ...]:
    return (
        _PHASE_ORDER[diagnostic.phase],
        diagnostic.path.encode("utf-8"),
        diagnostic.code.encode("utf-8"),
        diagnostic.message.encode("utf-8"),
    )


class RecordValidationError(ValueError):
    """Raised with stable, typed diagnostics for one failed boundary."""

    def __init__(self, diagnostics: Iterable[ValidationDiagnostic]):
        ordered = tuple(sorted(diagnostics, key=_diagnostic_sort_key))
        if not ordered:
            raise ValueError("RecordValidationError requires at least one diagnostic")
        self.diagnostics = ordered
        rendered = "; ".join(
            f"{item.phase.value}:{item.code}:{item.path or '/'}: {item.message}"
            for item in ordered
        )
        super().__init__(rendered)


class SchemaRegistryError(RuntimeError):
    """Raised when the checked-in C01 schema family is internally defective."""


@dataclass(frozen=True, slots=True)
class RecordContract:
    """Exact dispatch tuple for one identity-bearing record format."""

    kind: str
    format: str
    schema_id: str
    schema_filename: str
    schema_version: int = SCHEMA_VERSION
    canonicalizer: str = CANONICALIZER_ID


@dataclass(frozen=True, slots=True)
class SchemaReferenceAnnotation:
    """One runtime reference leaf compiled from normative schema annotations."""

    path: str
    record_id: str
    expected_kinds: tuple[str, ...]
    reference_class: str
    kind_source: str | None


def _contract(kind: str, stem: str) -> RecordContract:
    filename = f"crucible-{stem}-v2.schema.json"
    return RecordContract(
        kind=kind,
        format=f"workbench-crucible-{stem}-v2",
        schema_id=f"{SCHEMA_URI_PREFIX}{filename}",
        schema_filename=filename,
    )


_CONTRACT_LIST = (
    _contract("object-descriptor", "object-descriptor"),
    _contract("evidence-record", "evidence-record"),
    _contract("admission-record", "admission-record"),
    _contract("ledger-entry", "ledger-entry"),
    _contract("evidence-set-revision", "evidence-set-revision"),
    _contract("graph-record", "graph-record"),
    _contract("graph-revision", "graph-revision"),
    _contract("graph-set-revision", "graph-set-revision"),
    _contract("materialization-recipe", "materialization-recipe"),
    _contract("dependency-manifest", "dependency-manifest"),
    _contract("reference-event", "reference-event"),
)

_CONTRACTS_BY_KIND = MappingProxyType(
    {contract.kind: contract for contract in _CONTRACT_LIST}
)
_COMMON_SCHEMA_FILENAME = "crucible-v2-common.schema.json"
_COMMON_SCHEMA_ID = f"{SCHEMA_URI_PREFIX}{_COMMON_SCHEMA_FILENAME}"
_EXPECTED_RESOURCES = MappingProxyType(
    {
        _COMMON_SCHEMA_FILENAME: _COMMON_SCHEMA_ID,
        **{
            contract.schema_filename: contract.schema_id
            for contract in _CONTRACT_LIST
        },
    }
)


def registered_contracts() -> tuple[RecordContract, ...]:
    """Return the finite record-format registry in declaration order."""

    return _CONTRACT_LIST


def _pointer(parts: Iterable[object]) -> str:
    encoded: list[str] = []
    for part in parts:
        text = str(part).replace("~", "~0").replace("/", "~1")
        encoded.append(text)
    return "" if not encoded else "/" + "/".join(encoded)


def _schema_failure(code: str, path: str, message: str) -> NoReturn:
    raise RecordValidationError(
        (
            ValidationDiagnostic(
                phase=ValidationPhase.SCHEMA,
                code=code,
                path=path,
                message=message,
            ),
        )
    )


def contract_for(record: Mapping[str, Any]) -> RecordContract:
    """Resolve an exact header without inferring a schema or authority."""

    if type(record) is not dict:
        _schema_failure("schema.dispatch.object", "", "record must be an object")

    kind = record.get("kind")
    if type(kind) is not str or kind not in _CONTRACTS_BY_KIND:
        _schema_failure(
            "schema.dispatch.kind",
            "/kind",
            "kind is not registered for the Crucible V2 record family",
        )
    contract = _CONTRACTS_BY_KIND[kind]

    checks = (
        ("format", contract.format, "schema.dispatch.format"),
        ("schema_version", contract.schema_version, "schema.dispatch.schema-version"),
        ("schema_id", contract.schema_id, "schema.dispatch.schema-id"),
        ("canonicalizer", contract.canonicalizer, "schema.dispatch.canonicalizer"),
    )
    for field, expected, code in checks:
        if record.get(field) != expected:
            _schema_failure(
                code,
                f"/{field}",
                f"{field} must equal the exact registered value {expected!r}",
            )
    return contract


def _walk_schema(node: Any, path: tuple[object, ...] = ()) -> Iterable[tuple[Any, tuple[object, ...]]]:
    yield node, path
    if type(node) is dict:
        for key, child in node.items():
            yield from _walk_schema(child, (*path, key))
    elif type(node) is list:
        for index, child in enumerate(node):
            yield from _walk_schema(child, (*path, index))


_REFERENCE_CLASSES = frozenset(
    {"c01", "external", "mixed", "embedded", "subject-identity"}
)
_REFERENCE_KIND_SOURCES = frozenset(
    {
        "recipe-subject-contract",
        "sibling-candidate-kind",
        "sibling-component-kind",
        "sibling-entry-record-kind",
        "sibling-new-target-kind",
    }
)


def _assert_reference_annotation_shape(
    schema: Mapping[str, Any], filename: str
) -> None:
    for node, node_path in _walk_schema(schema):
        if type(node) is not dict:
            continue
        annotation_fields = {
            key for key in node if key.startswith("x-workbench-reference-")
        }
        if not annotation_fields:
            continue
        required = {
            "x-workbench-reference-kinds",
            "x-workbench-reference-class",
        }
        if not required.issubset(annotation_fields):
            raise SchemaRegistryError(
                f"incomplete reference annotation at {filename}:{_pointer(node_path)}"
            )
        kinds = node["x-workbench-reference-kinds"]
        reference_class = node["x-workbench-reference-class"]
        if (
            type(kinds) is not list
            or any(type(item) is not str or not item for item in kinds)
            or len(kinds) != len(set(kinds))
            or reference_class not in _REFERENCE_CLASSES
        ):
            raise SchemaRegistryError(
                f"invalid reference annotation at {filename}:{_pointer(node_path)}"
            )
        source = node.get("x-workbench-reference-kind-source")
        if reference_class in {"c01", "external", "mixed"} and not kinds:
            raise SchemaRegistryError(
                f"resolvable reference has no target kinds at {filename}:{_pointer(node_path)}"
            )
        if source is not None and source not in _REFERENCE_KIND_SOURCES:
            raise SchemaRegistryError(
                f"invalid dynamic reference source at {filename}:{_pointer(node_path)}"
            )


def _assert_content_id_surface(
    schema: Mapping[str, Any], filename: str
) -> None:
    """Prove the finite C01 content-ID schema surface is classified.

    Reusable exact content-ID patterns live only in the common schema.  Its
    generic content value and blob storage identity are deliberate non-reference
    bases; every other such definition must carry exactly one classification.
    Record schemas may declare only their own top-level ``id`` pattern directly.
    """

    for node, node_path in _walk_schema(schema):
        if type(node) is not dict:
            continue
        pattern = node.get("pattern")
        if type(pattern) is not str or "sha256:" not in pattern:
            continue
        if filename == _COMMON_SCHEMA_FILENAME:
            definition = (
                node_path[1]
                if len(node_path) == 2 and node_path[0] == "$defs"
                else None
            )
            if definition in {"contentId", "blobId"}:
                continue
            if not {
                "x-workbench-reference-kinds",
                "x-workbench-reference-class",
            }.issubset(node):
                raise SchemaRegistryError(
                    "unclassified content-ID definition at "
                    f"{filename}:{_pointer(node_path)}"
                )
        elif node_path != ("properties", "id"):
            raise SchemaRegistryError(
                "record schema declares an unaudited direct content-ID leaf at "
                f"{filename}:{_pointer(node_path)}"
            )


def _assert_resolved_content_id_surface(
    schemas: Mapping[str, dict[str, Any]], registry: Registry
) -> None:
    """Reject unclassified wrappers around the generic content-ID domain."""

    common_id = _EXPECTED_RESOURCES[_COMMON_SCHEMA_FILENAME]
    common = schemas[common_id]
    content_base = common["$defs"]["contentId"]
    blob_base = common["$defs"]["blobId"]

    def unclassified(
        node: Any, resolver: Any, active: set[int]
    ) -> frozenset[str]:
        if type(node) is not dict:
            return frozenset()
        marker = id(node)
        if marker in active or "x-workbench-reference-class" in node:
            return frozenset()
        if node is content_base:
            return frozenset({"content"})
        if node is blob_base:
            return frozenset({"blob"})
        pattern = node.get("pattern")
        if type(pattern) is str and "sha256:" in pattern:
            return frozenset({"content"})

        active.add(marker)
        found: set[str] = set()
        try:
            reference = node.get("$ref")
            if type(reference) is str:
                try:
                    resolved = resolver.lookup(reference)
                except Unresolvable as exc:
                    raise SchemaRegistryError(
                        "unresolvable reference during content-ID surface audit: "
                        f"{reference!r}"
                    ) from exc
                found.update(
                    unclassified(resolved.contents, resolved.resolver, active)
                )
            for keyword in ("allOf", "anyOf", "oneOf"):
                branches = node.get(keyword)
                if type(branches) is list:
                    for branch in branches:
                        found.update(unclassified(branch, resolver, active))
            for keyword in ("if", "then", "else"):
                branch = node.get(keyword)
                if type(branch) is dict:
                    found.update(unclassified(branch, resolver, active))
        finally:
            active.remove(marker)
        return frozenset(found)

    filename_by_id = {
        schema_id: filename
        for filename, schema_id in _EXPECTED_RESOURCES.items()
    }
    for schema_id, schema in schemas.items():
        filename = filename_by_id[schema_id]
        resolver = registry.resolver(base_uri=schema_id)
        for node, node_path in _walk_schema(schema):
            if type(node) is not dict:
                continue
            classes = unclassified(node, resolver, set())
            if not classes:
                continue
            allowed = (
                schema_id == common_id
                and node_path
                in {("$defs", "contentId"), ("$defs", "blobId")}
            ) or (
                node_path == ("properties", "id")
                and classes == frozenset({"content"})
            ) or (
                filename == "crucible-object-descriptor-v2.schema.json"
                and node_path == ("properties", "object_id")
                and classes == frozenset({"blob"})
            )
            if not allowed:
                raise SchemaRegistryError(
                    "unclassified resolved content-ID schema at "
                    f"{filename}:{_pointer(node_path)}"
                )


def _load_schema_file(
    filename: str, expected_id: str
) -> tuple[dict[str, Any], bytes]:
    path = _SCHEMA_DIRECTORY / filename
    if not path.is_file():
        raise SchemaRegistryError(f"required schema file is missing: {path}")
    try:
        raw = path.read_bytes()
        schema = parse_json_strict(raw)
    except (OSError, CanonicalJsonError) as exc:
        raise SchemaRegistryError(f"cannot load strict JSON schema {path}: {exc}") from exc
    if type(schema) is not dict:
        raise SchemaRegistryError(f"schema root must be an object: {path}")
    if schema.get("$id") != expected_id:
        raise SchemaRegistryError(
            f"schema {path} must declare exact $id {expected_id!r}"
        )
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise SchemaRegistryError(f"schema {path} is not explicit Draft 2020-12")

    for node, node_path in _walk_schema(schema):
        if type(node) is dict and node.get("type") == "object":
            if node.get("additionalProperties") is not False:
                raise SchemaRegistryError(
                    f"open object schema at {path}:{_pointer(node_path)}"
                )
    _assert_reference_annotation_shape(schema, filename)
    _assert_content_id_surface(schema, filename)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise SchemaRegistryError(f"invalid Draft 2020-12 schema {path}: {exc}") from exc
    return schema, raw


@dataclass(frozen=True, slots=True)
class _LoadedRegistry:
    schemas: Mapping[str, dict[str, Any]]
    raw_schemas: Mapping[str, bytes]
    registry: Registry
    validators: Mapping[str, Draft202012Validator]


@lru_cache(maxsize=1)
def _loaded_registry() -> _LoadedRegistry:
    schemas_by_id: dict[str, dict[str, Any]] = {}
    raw_schemas_by_id: dict[str, bytes] = {}
    for filename, expected_id in _EXPECTED_RESOURCES.items():
        schema, raw = _load_schema_file(filename, expected_id)
        if expected_id in schemas_by_id:
            raise SchemaRegistryError(f"duplicate schema resource ID: {expected_id}")
        schemas_by_id[expected_id] = schema
        raw_schemas_by_id[expected_id] = raw

    registry: Registry = Registry().with_resources(
        (
            (schema_id, Resource.from_contents(schema))
            for schema_id, schema in schemas_by_id.items()
        )
    )

    # Check every reference while the registry is constructed.  Missing local
    # resources are contract defects; they never become online lookups later.
    for schema_id, schema in schemas_by_id.items():
        resolver = registry.resolver(base_uri=schema_id)
        for node, node_path in _walk_schema(schema):
            if type(node) is not dict or "$ref" not in node:
                continue
            reference = node["$ref"]
            if type(reference) is not str:
                raise SchemaRegistryError(
                    f"non-string $ref in {schema_id}:{_pointer(node_path)}"
                )
            try:
                resolver.lookup(reference)
            except Unresolvable as exc:
                raise SchemaRegistryError(
                    f"unregistered $ref {reference!r} in "
                    f"{schema_id}:{_pointer(node_path)}"
                ) from exc

    _assert_resolved_content_id_surface(schemas_by_id, registry)

    validators = {
        contract.schema_id: Draft202012Validator(
            schemas_by_id[contract.schema_id], registry=registry
        )
        for contract in _CONTRACT_LIST
    }
    return _LoadedRegistry(
        schemas=MappingProxyType(schemas_by_id),
        raw_schemas=MappingProxyType(raw_schemas_by_id),
        registry=registry,
        validators=MappingProxyType(validators),
    )


def validate_closed_schema(record: Mapping[str, Any]) -> RecordContract:
    """Validate one complete record against its exact closed V2 schema."""

    contract = contract_for(record)
    validator = _loaded_registry().validators[contract.schema_id]
    errors = sorted(
        validator.iter_errors(record),
        key=lambda error: (
            _pointer(error.absolute_path).encode("utf-8"),
            _pointer(error.absolute_schema_path).encode("utf-8"),
            str(error.validator).encode("utf-8"),
            error.message.encode("utf-8"),
        ),
    )
    if errors:
        diagnostics = (
            ValidationDiagnostic(
                phase=ValidationPhase.SCHEMA,
                code=f"schema.{error.validator or 'invalid'}",
                path=_pointer(error.absolute_path),
                message=error.message,
            )
            for error in errors
        )
        raise RecordValidationError(diagnostics)
    return contract


def validate_candidate_schema(candidate: Mapping[str, Any]) -> RecordContract:
    """Validate a detached, ID-less body before any identity is calculated."""

    if type(candidate) is not dict:
        _schema_failure("schema.candidate.object", "", "candidate must be an object")
    if "id" in candidate:
        _schema_failure(
            "schema.candidate.id-present",
            "/id",
            "an ID-less candidate must not contain id",
        )
    contract = contract_for(candidate)
    probe = dict(candidate)
    probe["id"] = f"{contract.kind}:sha256:{'0' * 64}"
    validate_closed_schema(probe)
    return contract


def assert_schema_registry_ready() -> tuple[str, ...]:
    """Eagerly validate the exact offline resource set and return its IDs."""

    loaded = _loaded_registry()
    return tuple(sorted(loaded.schemas, key=lambda item: item.encode("utf-8")))


def registered_schema_bytes(schema_id: str) -> bytes | None:
    """Return exact checked-in bytes for a registered C01 schema, if any."""

    if type(schema_id) is not str:
        raise TypeError("schema_id must be a string")
    return _loaded_registry().raw_schemas.get(schema_id)


def _resolve_schema_node(
    reference: str, base_schema_id: str
) -> tuple[dict[str, Any], str]:
    if reference.startswith("#"):
        schema_id, fragment = base_schema_id, reference[1:]
    elif "#" in reference:
        schema_id, fragment = reference.split("#", 1)
    else:
        schema_id, fragment = reference, ""
    schema = _loaded_registry().schemas.get(schema_id)
    if schema is None:
        raise SchemaRegistryError(
            f"runtime reference compiler reached unregistered schema {schema_id!r}"
        )
    node: Any = schema
    if fragment:
        if not fragment.startswith("/"):
            raise SchemaRegistryError(
                f"unsupported non-pointer schema fragment in {reference!r}"
            )
        for token in fragment[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if type(node) is not dict or key not in node:
                raise SchemaRegistryError(
                    f"unresolved schema pointer in {reference!r}"
                )
            node = node[key]
    if type(node) is not dict:
        raise SchemaRegistryError(f"schema reference {reference!r} is not an object")
    return node, schema_id


def annotated_record_references(
    record: Mapping[str, Any],
) -> tuple[SchemaReferenceAnnotation, ...]:
    """Compile exact runtime references from resolved schema annotations.

    Values that merely resemble content IDs are never references.  This walk
    follows only instance-applicable property/item locations and normative
    ``x-workbench-reference-*`` annotations in the finite offline registry.
    """

    contract = contract_for(record)
    root = _loaded_registry().schemas[contract.schema_id]
    found: dict[
        tuple[str, str, tuple[str, ...], str, str | None],
        SchemaReferenceAnnotation,
    ] = {}
    active: set[tuple[int, str, str]] = set()

    def parent_instance(instance_path: tuple[object, ...]) -> Any:
        value: Any = record
        for token in instance_path[:-1]:
            if type(value) is dict and type(token) is str:
                value = value.get(token)
            elif type(value) is list and type(token) is int and token < len(value):
                value = value[token]
            else:
                return None
        return value

    def applies(
        branch: dict[str, Any], schema_id: str, instance: Any
    ) -> bool:
        loaded = _loaded_registry()
        validator = Draft202012Validator(
            branch,
            registry=loaded.registry,
            _resolver=loaded.registry.resolver(base_uri=schema_id),
        )
        return validator.is_valid(instance)

    def visit(
        schema: dict[str, Any],
        schema_id: str,
        instance: Any,
        instance_path: tuple[object, ...],
    ) -> None:
        marker = (id(schema), schema_id, _pointer(instance_path))
        if marker in active:
            return
        active.add(marker)
        try:
            reference = schema.get("$ref")
            if type(reference) is str:
                target, target_id = _resolve_schema_node(reference, schema_id)
                visit(target, target_id, instance, instance_path)

            if type(instance) is str and (
                "x-workbench-reference-class" in schema
            ):
                reference_class = schema["x-workbench-reference-class"]
                kinds = tuple(schema["x-workbench-reference-kinds"])
                source = schema.get("x-workbench-reference-kind-source")
                sibling_container = parent_instance(instance_path)
                if source == "sibling-candidate-kind":
                    sibling = (
                        sibling_container.get("candidate_kind")
                        if type(sibling_container) is dict
                        else None
                    )
                    if type(sibling) is str:
                        kinds = (sibling,)
                elif source == "sibling-component-kind":
                    sibling = (
                        sibling_container.get("component_kind")
                        if type(sibling_container) is dict
                        else None
                    )
                    if type(sibling) is str:
                        kinds = (sibling,)
                elif source == "sibling-entry-record-kind":
                    sibling = (
                        sibling_container.get("entry_record_kind")
                        if type(sibling_container) is dict
                        else None
                    )
                    if type(sibling) is str:
                        kinds = (sibling,)
                elif source == "sibling-new-target-kind":
                    sibling = (
                        sibling_container.get("new_target_kind")
                        if type(sibling_container) is dict
                        else None
                    )
                    if type(sibling) is str:
                        kinds = (sibling,)
                path = _pointer(instance_path)
                item = SchemaReferenceAnnotation(
                    path=path,
                    record_id=instance,
                    expected_kinds=kinds,
                    reference_class=reference_class,
                    kind_source=source,
                )
                found[(path, instance, kinds, reference_class, source)] = item

            if type(instance) is dict:
                properties = schema.get("properties")
                if type(properties) is dict:
                    for key, child_schema in properties.items():
                        if key in instance and type(child_schema) is dict:
                            visit(
                                child_schema,
                                schema_id,
                                instance[key],
                                (*instance_path, key),
                            )
            elif type(instance) is list:
                item_schema = schema.get("items")
                if type(item_schema) is dict:
                    for index, item in enumerate(instance):
                        visit(
                            item_schema,
                            schema_id,
                            item,
                            (*instance_path, index),
                        )

            branches = schema.get("allOf")
            if type(branches) is list:
                for branch in branches:
                    if type(branch) is dict:
                        visit(branch, schema_id, instance, instance_path)
            for keyword in ("anyOf", "oneOf"):
                branches = schema.get(keyword)
                if type(branches) is list:
                    for branch in branches:
                        if type(branch) is dict and applies(
                            branch, schema_id, instance
                        ):
                            visit(branch, schema_id, instance, instance_path)
            condition = schema.get("if")
            if type(condition) is dict:
                selected = "then" if applies(condition, schema_id, instance) else "else"
                branch = schema.get(selected)
                if type(branch) is dict:
                    visit(branch, schema_id, instance, instance_path)
        finally:
            active.remove(marker)

    visit(root, contract.schema_id, record, ())
    coalesced: dict[
        tuple[str, str, str], SchemaReferenceAnnotation
    ] = {}
    for item in found.values():
        key = (item.path, item.record_id, item.reference_class)
        previous = coalesced.get(key)
        if previous is not None and previous.expected_kinds != item.expected_kinds:
            raise SchemaRegistryError(
                "applicable reference annotations disagree at "
                f"{item.path or '/'}"
            )
        if previous is None or (
            previous.kind_source is None and item.kind_source is not None
        ):
            coalesced[key] = item
    return tuple(
        sorted(
            coalesced.values(),
            key=lambda item: (
                item.path.encode("utf-8"),
                item.record_id.encode("ascii"),
                item.reference_class.encode("ascii"),
            ),
        )
    )


__all__ = [
    "RecordContract",
    "RecordValidationError",
    "SchemaReferenceAnnotation",
    "SCHEMA_VERSION",
    "SchemaRegistryError",
    "ValidationDiagnostic",
    "ValidationPhase",
    "assert_schema_registry_ready",
    "annotated_record_references",
    "contract_for",
    "registered_contracts",
    "registered_schema_bytes",
    "validate_candidate_schema",
    "validate_closed_schema",
]
