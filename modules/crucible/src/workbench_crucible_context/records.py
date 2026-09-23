"""Executable ContextRef and InputBinding V2 record validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias, cast

from workbench_api.canonical import (
    CANONICALIZER_ID,
    CanonicalJsonError,
    canonical_json_bytes,
    parse_canonical_json,
    record_content_id,
    validate_content_id,
)
from workbench_crucible.schema_registry import (
    RecordValidationError,
    ValidationDiagnostic,
    ValidationPhase,
)

from .schema_registry import validate_schema


_OPERATION_SCOPE_ORDER = {
    "experiment": 0,
    "fixture": 1,
    "action": 2,
    "capture-scope": 3,
}
_INPUT_ARRAYS = (
    "evidence_set_bindings",
    "graph_bindings",
    "graph_set_bindings",
    "recipe_bindings",
    "schema_bindings",
    "ontology_bindings",
    "adapter_bindings",
    "policy_bindings",
    "index_bindings",
)


def _diagnostic(
    phase: ValidationPhase, code: str, path: str, message: str
) -> ValidationDiagnostic:
    return ValidationDiagnostic(phase=phase, code=code, path=path, message=message)


def _snapshot(value: Any) -> dict[str, Any]:
    try:
        result = parse_canonical_json(canonical_json_bytes(value))
    except (CanonicalJsonError, TypeError) as exc:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-value",
                    "",
                    str(exc),
                ),
            )
        ) from exc
    if type(result) is not dict:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-record",
                    "",
                    "context record must be an ordinary object",
                ),
            )
        )
    return result


def _kind_of(record_id: Any) -> str | None:
    if type(record_id) is not str or ":sha256:" not in record_id:
        return None
    return record_id.split(":sha256:", 1)[0]


@dataclass(frozen=True, slots=True)
class ContextAuthorityScope:
    owner_authority_id: str
    owner_revision_id: str
    authority_adapter_id: str


@dataclass(frozen=True, slots=True)
class ContextReferenceExpectation:
    path: str
    record_id: str
    expected_kind: str
    authority_scope: ContextAuthorityScope | None = None


@dataclass(frozen=True, slots=True)
class ValidatedContextReference:
    """Exact bytes from a pinned, schema-valid external record registry."""

    record_id: str
    kind: str
    canonical_bytes: bytes
    owner_authority_id: str | None = None
    owner_revision_id: str | None = None
    authority_adapter_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedContextRecord:
    id: str
    kind: str
    format: str
    schema_id: str
    canonical_bytes: bytes
    references: tuple[ContextReferenceExpectation, ...]

    def to_dict(self) -> dict[str, Any]:
        value = parse_canonical_json(self.canonical_bytes)
        return cast(dict[str, Any], value)

    def __bytes__(self) -> bytes:
        return self.canonical_bytes


@dataclass(frozen=True, slots=True)
class SupportValidationRequest:
    context_ref: ValidatedContextRecord
    profile_kind: str
    profile_revision_id: str
    profile_adapter_id: str
    support_decision_ids: tuple[str, ...]
    support_state: str
    authority_scope: ContextAuthorityScope
    external_references: Mapping[str, ValidatedContextReference]


@dataclass(frozen=True, slots=True)
class ContextAncestryValidationRequest:
    context_ref: ValidatedContextRecord
    external_references: Mapping[str, ValidatedContextReference]


@dataclass(frozen=True, slots=True)
class ContextBindingValidationRequest:
    """Whole-record applicability request for every exact ContextRef binding."""

    context_ref: ValidatedContextRecord
    external_references: Mapping[str, ValidatedContextReference]


@dataclass(frozen=True, slots=True)
class InputBindingValidationRequest:
    context_ref: ValidatedContextRecord
    input_binding: ValidatedContextRecord
    external_references: Mapping[str, ValidatedContextReference]


ContextReferenceResolver: TypeAlias = Callable[
    [ContextReferenceExpectation], ValidatedContextReference | None
]
SupportValidator: TypeAlias = Callable[[SupportValidationRequest], bool]
ContextAncestryValidator: TypeAlias = Callable[
    [ContextAncestryValidationRequest], bool
]
ContextBindingValidator: TypeAlias = Callable[[ContextBindingValidationRequest], bool]
InputBindingValidator: TypeAlias = Callable[[InputBindingValidationRequest], bool]
ContextRecordResolution: TypeAlias = (
    ValidatedContextRecord | Mapping[str, Any] | bytes | bytearray | memoryview
)


@dataclass(frozen=True, slots=True)
class ContextRelationResolvers:
    """Pinned validation ports; callbacks attest external catalog semantics."""

    reference_resolver: ContextReferenceResolver | None = None
    support_validator: SupportValidator | None = None
    context_binding_validator: ContextBindingValidator | None = None
    ancestry_validator: ContextAncestryValidator | None = None
    input_binding_validator: InputBindingValidator | None = None


def _scope(value: Mapping[str, Any]) -> ContextAuthorityScope:
    return ContextAuthorityScope(
        owner_authority_id=cast(str, value["owner_authority_id"]),
        owner_revision_id=cast(str, value["owner_revision_id"]),
        authority_adapter_id=cast(str, value["authority_adapter_id"]),
    )


def _expect(
    result: list[ContextReferenceExpectation],
    path: str,
    record_id: str,
    expected_kind: str,
    authority_scope: ContextAuthorityScope | None = None,
) -> None:
    result.append(
        ContextReferenceExpectation(
            path=path,
            record_id=record_id,
            expected_kind=expected_kind,
            authority_scope=authority_scope,
        )
    )


def _profile_references(
    result: list[ContextReferenceExpectation],
    path: str,
    profile_kind: str,
    value: Mapping[str, Any],
) -> None:
    scope = _scope(cast(Mapping[str, Any], value["profile_authority"]))
    revision_field = f"{profile_kind}_profile_revision_id"
    _expect(
        result,
        f"{path}/{revision_field}",
        cast(str, value[revision_field]),
        f"{profile_kind}-profile-revision",
        scope,
    )
    _expect(
        result,
        f"{path}/profile_adapter_id",
        cast(str, value["profile_adapter_id"]),
        "profile-adapter",
        scope,
    )
    for field, kind in (
        ("owner_authority_id", "authority"),
        ("owner_revision_id", "owner-revision"),
        ("authority_adapter_id", "authority-adapter"),
    ):
        _expect(
            result,
            f"{path}/profile_authority/{field}",
            cast(str, value["profile_authority"][field]),
            kind,
            scope,
        )
    for index, decision_id in enumerate(value["support_decision_ids"]):
        _expect(
            result,
            f"{path}/support_decision_ids/{index}",
            decision_id,
            "support-decision",
            scope,
        )


def _context_references(record: dict[str, Any]) -> tuple[ContextReferenceExpectation, ...]:
    result: list[ContextReferenceExpectation] = []
    _expect(result, "/store_id", record["store_id"], "store")
    workspace = record["workspace_binding"]
    _expect(result, "/workspace_binding/workspace_id", workspace["workspace_id"], "workspace")
    _expect(
        result,
        "/workspace_binding/workspace_registration_revision_id",
        workspace["workspace_registration_revision_id"],
        "workspace-registration-revision",
    )
    profiles = record["profile_scope"]
    _profile_references(result, "/profile_scope/platform", "platform", profiles["platform"])
    if profiles["pack"]["kind"] == "selected":
        _profile_references(result, "/profile_scope/pack", "pack", profiles["pack"])
    for field, kind in (
        ("source_lock_ids", "source-lock"),
        ("dependency_lock_ids", "dependency-lock"),
        ("artifact_lock_ids", "artifact-lock"),
    ):
        for index, record_id in enumerate(record[field]):
            _expect(result, f"/{field}/{index}", record_id, kind)
    runtime = record["runtime_scope"]
    if runtime["kind"] == "selected":
        for field, kind in (
            ("runtime_target_id", "runtime-target"),
            ("installed_runtime_id", "installed-runtime"),
            ("runtime_epoch_id", "runtime-epoch"),
        ):
            _expect(result, f"/runtime_scope/{field}", runtime[field], kind)
        for index, lock_id in enumerate(runtime["configuration_lock_ids"]):
            _expect(
                result,
                f"/runtime_scope/configuration_lock_ids/{index}",
                lock_id,
                "configuration-lock",
            )
    world = record["world_scope"]
    if world["kind"] == "selected":
        for field, kind in (
            ("runtime_epoch_id", "runtime-epoch"),
            ("world_instance_id", "world-instance"),
            ("world_creation_receipt_id", "world-creation-receipt"),
            ("save_lineage_id", "save-lineage"),
            ("world_epoch_id", "world-epoch"),
        ):
            _expect(result, f"/world_scope/{field}", world[field], kind)
        generation = world["generation"]
        if generation["kind"] == "applicable":
            for field, kind in (
                ("world_type_id", "world-type"),
                ("generator_id", "world-generator"),
                (
                    "generator_configuration_object_descriptor_id",
                    "object-descriptor",
                ),
            ):
                _expect(result, f"/world_scope/generation/{field}", generation[field], kind)
    dimension = record["dimension_scope"]
    if dimension["kind"] == "selected":
        for field, kind in (
            ("world_epoch_id", "world-epoch"),
            ("dimension_instance_id", "dimension-instance"),
            ("dimension_registry_id", "dimension-registry"),
            ("provider_id", "world-provider"),
        ):
            _expect(result, f"/dimension_scope/{field}", dimension[field], kind)
    region = record["region_scope"]
    if region["kind"] == "chunk-bounds":
        _expect(
            result,
            "/region_scope/dimension_instance_id",
            region["dimension_instance_id"],
            "dimension-instance",
        )
    for index, operation in enumerate(record["operation_scopes"]):
        _expect(
            result,
            f"/operation_scopes/{index}/scope_id",
            operation["scope_id"],
            operation["scope_kind"],
        )
    _expect(result, "/privacy_class_id", record["privacy_class_id"], "privacy-class")
    _expect(
        result,
        "/resource_budget_class_id",
        record["resource_budget_class_id"],
        "resource-budget-class",
    )
    return tuple(result)


def _authority_binding_references(
    result: list[ContextReferenceExpectation],
    path: str,
    binding: Mapping[str, Any],
) -> ContextAuthorityScope:
    scope = _scope(binding)
    for field, kind in (
        ("owner_authority_id", "authority"),
        ("owner_revision_id", "owner-revision"),
        ("authority_adapter_id", "authority-adapter"),
    ):
        _expect(result, f"{path}/{field}", cast(str, binding[field]), kind, scope)
    return scope


def _input_references(record: dict[str, Any]) -> tuple[ContextReferenceExpectation, ...]:
    result: list[ContextReferenceExpectation] = []
    simple_arrays = (
        ("evidence_set_bindings", "evidence_set_revision_id", "evidence-set-revision"),
        ("graph_bindings", "graph_revision_id", "graph-revision"),
        ("graph_set_bindings", "graph_set_revision_id", "graph-set-revision"),
        ("recipe_bindings", "materialization_recipe_id", "materialization-recipe"),
        ("index_bindings", "query_index_descriptor_id", "query-index-descriptor"),
    )
    for array_name, field, kind in simple_arrays:
        for index, item in enumerate(record[array_name]):
            _expect(result, f"/{array_name}/{index}/{field}", item[field], kind)
    for index, item in enumerate(record["schema_bindings"]):
        _expect(
            result,
            f"/schema_bindings/{index}/schema_object_descriptor_id",
            item["schema_object_descriptor_id"],
            "object-descriptor",
        )
    for array_name, field, kind in (
        ("ontology_bindings", "ontology_id", "ontology"),
        ("policy_bindings", "policy_id", "policy"),
    ):
        for index, item in enumerate(record[array_name]):
            scope = _authority_binding_references(
                result,
                f"/{array_name}/{index}/authority_binding",
                item["authority_binding"],
            )
            _expect(result, f"/{array_name}/{index}/{field}", item[field], kind, scope)
    for index, item in enumerate(record["adapter_bindings"]):
        scope = _authority_binding_references(
            result,
            f"/adapter_bindings/{index}/authority_binding",
            item["authority_binding"],
        )
        _expect(
            result,
            f"/adapter_bindings/{index}/adapter_id",
            item["adapter_id"],
            item["adapter_kind"],
            scope,
        )
    return tuple(result)


def _check_sorted_strings(
    record: dict[str, Any], path: str, errors: list[ValidationDiagnostic]
) -> None:
    values = record[path]
    if values != sorted(values, key=lambda item: item.encode("utf-8")):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.context-set-order",
                f"/{path}",
                "identity set must be strict UTF-8 order",
            )
        )


def _semantic_context(record: dict[str, Any]) -> tuple[ContextReferenceExpectation, ...]:
    errors: list[ValidationDiagnostic] = []
    for field in ("source_lock_ids", "dependency_lock_ids", "artifact_lock_ids"):
        _check_sorted_strings(record, field, errors)
    profiles = record["profile_scope"]
    support_ids: list[str] = []
    for path, binding in (("platform", profiles["platform"]), ("pack", profiles["pack"])):
        if binding.get("kind") == "none":
            continue
        ids = binding["support_decision_ids"]
        support_ids.extend(ids)
        if ids != sorted(ids, key=lambda item: item.encode("utf-8")):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.support-decision-order",
                    f"/profile_scope/{path}/support_decision_ids",
                    "support decision IDs must be strict UTF-8 order",
                )
            )
    if len(support_ids) != len(set(support_ids)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.support-decision-identity",
                "/profile_scope",
                "platform and pack bindings may not reuse one support decision ID",
            )
        )
    runtime = record["runtime_scope"]
    world = record["world_scope"]
    dimension = record["dimension_scope"]
    region = record["region_scope"]
    if runtime["kind"] == "selected":
        locks = runtime["configuration_lock_ids"]
        if locks != sorted(locks, key=lambda item: item.encode("utf-8")):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.configuration-lock-order",
                    "/runtime_scope/configuration_lock_ids",
                    "configuration lock IDs must be strict UTF-8 order",
                )
            )
    if world["kind"] == "selected":
        if runtime["kind"] != "selected":
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.world-runtime-required",
                    "/world_scope",
                    "a world scope requires an exact runtime scope",
                )
            )
        elif world["runtime_epoch_id"] != runtime["runtime_epoch_id"]:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.world-runtime-ancestry",
                    "/world_scope/runtime_epoch_id",
                    "world scope must repeat the exact selected runtime epoch",
                )
            )
    elif dimension["kind"] != "none" or region["kind"] != "none":
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.world-scope-required",
                "/world_scope",
                "dimension and region scopes require an exact world scope",
            )
        )
    if dimension["kind"] == "selected":
        if world["kind"] != "selected" or dimension["world_epoch_id"] != world.get("world_epoch_id"):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.dimension-world-ancestry",
                    "/dimension_scope/world_epoch_id",
                    "dimension scope must repeat the exact selected world epoch",
                )
            )
    elif region["kind"] != "none":
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.dimension-scope-required",
                "/region_scope",
                "region scope requires an exact dimension scope",
            )
        )
    if region["kind"] == "chunk-bounds":
        if (
            dimension["kind"] != "selected"
            or region["dimension_instance_id"]
            != dimension.get("dimension_instance_id")
        ):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.region-dimension-ancestry",
                    "/region_scope/dimension_instance_id",
                    "region scope must repeat the exact selected dimension instance",
                )
            )
        minimum = region["minimum_chunk_inclusive"]
        maximum = region["maximum_chunk_exclusive"]
        if minimum["x"] >= maximum["x"] or minimum["z"] >= maximum["z"]:
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.region-bounds",
                    "/region_scope",
                    "minimum chunk is inclusive and must be strictly below the exclusive maximum on both axes",
                )
            )
    operations = record["operation_scopes"]
    expected_operations = sorted(
        operations,
        key=lambda item: (
            _OPERATION_SCOPE_ORDER[item["scope_kind"]],
            item["scope_id"].encode("utf-8"),
        ),
    )
    if operations != expected_operations:
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.operation-scope-order",
                "/operation_scopes",
                "operation scopes must follow experiment, fixture, action, capture-scope order",
            )
        )
    kinds = [item["scope_kind"] for item in operations]
    if len(kinds) != len(set(kinds)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.operation-scope-kind",
                "/operation_scopes",
                "each operation scope kind may occur at most once",
            )
        )
    if errors:
        raise RecordValidationError(tuple(errors))
    return _context_references(record)


def _semantic_input(record: dict[str, Any]) -> tuple[ContextReferenceExpectation, ...]:
    errors: list[ValidationDiagnostic] = []
    keys: list[str] = []
    for array_name in _INPUT_ARRAYS:
        values = record[array_name]
        if values != sorted(values, key=canonical_json_bytes):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.input-binding-order",
                    f"/{array_name}",
                    "input bindings must be sorted by canonical item bytes",
                )
            )
        keys.extend(item["input_key"] for item in values)
    if len(keys) != len(set(keys)):
        errors.append(
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.input-key",
                "",
                "input_key values must be globally unique across every binding class",
            )
        )
    for index, item in enumerate(record["adapter_bindings"]):
        if (
            item["adapter_kind"] == "authority-adapter"
            and item["adapter_id"]
            != item["authority_binding"]["authority_adapter_id"]
        ):
            errors.append(
                _diagnostic(
                    ValidationPhase.SEMANTIC,
                    "semantic.authority-adapter-binding",
                    f"/adapter_bindings/{index}/adapter_id",
                    "an authority-adapter input must equal its exact authority binding adapter",
                )
            )
    if errors:
        raise RecordValidationError(tuple(errors))
    return _input_references(record)


def _semantic(record: dict[str, Any]) -> tuple[ContextReferenceExpectation, ...]:
    if record["kind"] == "context-ref":
        return _semantic_context(record)
    if record["kind"] == "input-binding":
        return _semantic_input(record)
    raise RecordValidationError(
        (
            _diagnostic(
                ValidationPhase.SEMANTIC,
                "semantic.context-kind",
                "/kind",
                "unsupported C02 context record kind",
            ),
        )
    )


def _finish(
    record: dict[str, Any], references: tuple[ContextReferenceExpectation, ...]
) -> ValidatedContextRecord:
    return ValidatedContextRecord(
        id=record["id"],
        kind=record["kind"],
        format=record["format"],
        schema_id=record["schema_id"],
        canonical_bytes=canonical_json_bytes(record),
        references=references,
    )


def _seal(candidate: Mapping[str, Any], expected_kind: str) -> ValidatedContextRecord:
    snapshot = _snapshot(candidate)
    if "id" in snapshot:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.preexisting-id",
                    "/id",
                    "seal candidate must not contain id",
                ),
            )
        )
    if snapshot.get("kind") != expected_kind:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-kind",
                    "/kind",
                    f"candidate must have kind {expected_kind!r}",
                ),
            )
        )
    snapshot["id"] = f"{expected_kind}:sha256:{'0' * 64}"
    validate_schema(snapshot)
    _semantic(snapshot)
    snapshot.pop("id")
    snapshot["id"] = record_content_id(snapshot)
    validate_schema(snapshot)
    references = _semantic(snapshot)
    return _finish(snapshot, references)


def _validate(record: Mapping[str, Any], expected_kind: str) -> ValidatedContextRecord:
    snapshot = _snapshot(record)
    if snapshot.get("kind") != expected_kind:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-kind",
                    "/kind",
                    f"record must have kind {expected_kind!r}",
                ),
            )
        )
    validate_schema(snapshot)
    references = _semantic(snapshot)
    try:
        validate_content_id(snapshot)
    except CanonicalJsonError as exc:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.IDENTITY,
                    "identity.context-mismatch",
                    "/id",
                    str(exc),
                ),
            )
        ) from exc
    return _finish(snapshot, references)


def _load(raw: bytes | bytearray | memoryview, expected_kind: str) -> ValidatedContextRecord:
    if type(raw) not in (bytes, bytearray, memoryview):
        raise TypeError("canonical context record input must be bytes-like")
    encoded = bytes(raw)
    try:
        value = parse_canonical_json(encoded)
    except CanonicalJsonError as exc:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-noncanonical",
                    "",
                    str(exc),
                ),
            )
        ) from exc
    if type(value) is not dict:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-record",
                    "",
                    "canonical context bytes must encode an object",
                ),
            )
        )
    result = _validate(value, expected_kind)
    if result.canonical_bytes != encoded:
        raise RecordValidationError(
            (
                _diagnostic(
                    ValidationPhase.DOMAIN,
                    "domain.context-byte-snapshot",
                    "",
                    "canonical bytes changed during validation",
                ),
            )
        )
    return result


def seal_context_ref(candidate: Mapping[str, Any]) -> ValidatedContextRecord:
    return _seal(candidate, "context-ref")


def validate_context_ref(record: Mapping[str, Any]) -> ValidatedContextRecord:
    return _validate(record, "context-ref")


def load_context_ref(raw: bytes | bytearray | memoryview) -> ValidatedContextRecord:
    return _load(raw, "context-ref")


def seal_input_binding(candidate: Mapping[str, Any]) -> ValidatedContextRecord:
    return _seal(candidate, "input-binding")


def validate_input_binding(record: Mapping[str, Any]) -> ValidatedContextRecord:
    return _validate(record, "input-binding")


def load_input_binding(raw: bytes | bytearray | memoryview) -> ValidatedContextRecord:
    return _load(raw, "input-binding")


def _coerce_context_record(
    value: ContextRecordResolution, expected_kind: str
) -> ValidatedContextRecord:
    if type(value) is ValidatedContextRecord:
        if (
            type(value.id) is not str
            or type(value.kind) is not str
            or type(value.format) is not str
            or type(value.schema_id) is not str
            or type(value.canonical_bytes) is not bytes
            or type(value.references) is not tuple
        ):
            raise TypeError("validated context record metadata is malformed")
        supplied_references = tuple(
            _reference_expectation_shape(item) for item in value.references
        )
        reloaded = _load(value.canonical_bytes, expected_kind)
        if (
            value.id,
            value.kind,
            value.format,
            value.schema_id,
        ) != (
            reloaded.id,
            reloaded.kind,
            reloaded.format,
            reloaded.schema_id,
        ):
            raise ValueError("validated context record metadata disagrees with bytes")
        reloaded_references = tuple(
            _reference_expectation_shape(item) for item in reloaded.references
        )
        if supplied_references != reloaded_references:
            raise ValueError("validated context record references disagree with bytes")
        return reloaded
    if type(value) in (bytes, bytearray, memoryview):
        return _load(value, expected_kind)
    if expected_kind == "context-ref":
        return validate_context_ref(value)
    return validate_input_binding(value)


def _authority_scope_shape(value: Any) -> tuple[str, str, str]:
    if type(value) is not ContextAuthorityScope:
        raise TypeError("reference authority scope must be ContextAuthorityScope")
    fields = (
        value.owner_authority_id,
        value.owner_revision_id,
        value.authority_adapter_id,
    )
    if any(type(item) is not str for item in fields):
        raise TypeError("reference authority scope fields must be exact strings")
    return fields


def _reference_expectation_shape(value: Any) -> tuple[Any, ...]:
    if type(value) is not ContextReferenceExpectation:
        raise TypeError("record reference metadata must use ContextReferenceExpectation")
    if (
        type(value.path) is not str
        or type(value.record_id) is not str
        or type(value.expected_kind) is not str
    ):
        raise TypeError("record reference metadata fields must be exact strings")
    scope = (
        None
        if value.authority_scope is None
        else _authority_scope_shape(value.authority_scope)
    )
    return value.path, value.record_id, value.expected_kind, scope


def _validate_reference_shape(value: Any) -> tuple[Any, ...]:
    if type(value) is not ValidatedContextReference:
        raise TypeError("reference resolver must return ValidatedContextReference")
    if type(value.record_id) is not str or type(value.kind) is not str:
        raise TypeError("resolved reference identity must use exact strings")
    if type(value.canonical_bytes) is not bytes:
        raise TypeError("resolved reference canonical_bytes must be exact bytes")
    scope_fields = (
        value.owner_authority_id,
        value.owner_revision_id,
        value.authority_adapter_id,
    )
    if any(type(item) is not str and item is not None for item in scope_fields):
        raise TypeError("resolved authority fields must be exact strings or null")
    return value.record_id, value.kind, value.canonical_bytes, *scope_fields


def _resolve_references(
    records: tuple[ValidatedContextRecord, ...],
    relations: ContextRelationResolvers,
) -> tuple[dict[str, ValidatedContextReference], list[ValidationDiagnostic]]:
    result: dict[str, ValidatedContextReference] = {}
    errors: list[ValidationDiagnostic] = []
    resolver = relations.reference_resolver
    for record in records:
        for expectation in record.references:
            try:
                expectation_snapshot = _reference_expectation_shape(expectation)
            except (TypeError, ValueError):
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-invalid",
                        "",
                        "reference expectation metadata is invalid",
                    )
                )
                continue
            path, record_id, expected_kind, scope_snapshot = expectation_snapshot
            expected_scope = (
                (None, None, None)
                if scope_snapshot is None
                else scope_snapshot
            )
            resolver_request = ContextReferenceExpectation(
                path=path,
                record_id=record_id,
                expected_kind=expected_kind,
                authority_scope=(
                    None
                    if scope_snapshot is None
                    else ContextAuthorityScope(*scope_snapshot)
                ),
            )
            cached = result.get(record_id)
            if cached is not None:
                cached_scope = (
                    cached.owner_authority_id,
                    cached.owner_revision_id,
                    cached.authority_adapter_id,
                )
                if (
                    cached.kind != expected_kind
                    or cached_scope != expected_scope
                ):
                    errors.append(
                        _diagnostic(
                            ValidationPhase.RELATION,
                            "relation.context-reference-reuse",
                            path,
                            "one immutable ID cannot be reused under an incompatible kind or authority scope",
                        )
                    )
                continue
            if resolver is None:
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-port",
                        path,
                        "pinned context reference registry is unavailable",
                    )
                )
                continue
            try:
                resolved = resolver(resolver_request)
            except Exception as exc:
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-failure",
                        path,
                        f"reference resolver failed with {type(exc).__name__}",
                    )
                )
                continue
            try:
                request_unchanged = (
                    _reference_expectation_shape(resolver_request)
                    == expectation_snapshot
                )
            except (TypeError, ValueError):
                request_unchanged = False
            if not request_unchanged:
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-mutation",
                        path,
                        "reference resolver mutated its isolated expectation",
                    )
                )
                continue
            if resolved is None:
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-unavailable",
                        path,
                        "exact referenced context input is unavailable",
                    )
                )
                continue
            try:
                resolved_snapshot = _validate_reference_shape(resolved)
                pinned = ValidatedContextReference(*resolved_snapshot)
                value = parse_canonical_json(pinned.canonical_bytes)
                if type(value) is not dict:
                    raise CanonicalJsonError("resolved reference must be an object")
                validate_content_id(value)
            except Exception as exc:
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-invalid",
                        path,
                        f"resolved reference is invalid ({type(exc).__name__})",
                    )
                )
                continue
            if (
                pinned.record_id != record_id
                or pinned.kind != expected_kind
                or value.get("id") != record_id
                or value.get("kind") != expected_kind
            ):
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-binding",
                        path,
                        "resolved reference identity and kind must equal the exact expectation",
                    )
                )
                continue
            actual_scope = (
                pinned.owner_authority_id,
                pinned.owner_revision_id,
                pinned.authority_adapter_id,
            )
            if actual_scope != expected_scope:
                errors.append(
                    _diagnostic(
                        ValidationPhase.RELATION,
                        "relation.context-reference-authority",
                        path,
                        "resolved reference must match the exact declared authority scope",
                    )
                )
                continue
            result[pinned.record_id] = pinned
    return result, errors


def _snapshot_context_callback_record(
    value: Any, expected_kind: str
) -> tuple[ValidatedContextRecord, tuple[Any, ...]]:
    pinned = _coerce_context_record(value, expected_kind)
    shape = (
        pinned.id,
        pinned.kind,
        pinned.format,
        pinned.schema_id,
        pinned.canonical_bytes,
        tuple(_reference_expectation_shape(item) for item in pinned.references),
    )
    return pinned, shape


def _snapshot_context_callback_externals(
    value: Any,
) -> tuple[Mapping[str, ValidatedContextReference], tuple[Any, ...]]:
    try:
        items = dict(value)
    except Exception as exc:
        raise TypeError("external reference view cannot be snapshotted") from exc
    pinned: dict[str, ValidatedContextReference] = {}
    shape: list[tuple[Any, ...]] = []
    for record_id in sorted(items, key=lambda item: item.encode("utf-8")):
        if type(record_id) is not str:
            raise TypeError("external reference keys must be exact strings")
        snapshot = _validate_reference_shape(items[record_id])
        if snapshot[0] != record_id:
            raise ValueError("external reference key and record ID disagree")
        pinned[record_id] = ValidatedContextReference(*snapshot)
        shape.append((record_id, *snapshot))
    return MappingProxyType(pinned), tuple(shape)


def _snapshot_context_callback_request(request: Any) -> tuple[Any, tuple[Any, ...]]:
    if type(request) is SupportValidationRequest:
        context, context_shape = _snapshot_context_callback_record(
            request.context_ref, "context-ref"
        )
        external, external_shape = _snapshot_context_callback_externals(
            request.external_references
        )
        primitive_fields = (
            request.profile_kind,
            request.profile_revision_id,
            request.profile_adapter_id,
            request.support_state,
        )
        if any(type(item) is not str for item in primitive_fields):
            raise TypeError("support request fields must be exact strings")
        if (
            type(request.support_decision_ids) is not tuple
            or any(type(item) is not str for item in request.support_decision_ids)
        ):
            raise TypeError("support decisions must be an exact tuple of strings")
        authority_shape = _authority_scope_shape(request.authority_scope)
        isolated = SupportValidationRequest(
            context_ref=context,
            profile_kind=request.profile_kind,
            profile_revision_id=request.profile_revision_id,
            profile_adapter_id=request.profile_adapter_id,
            support_decision_ids=request.support_decision_ids,
            support_state=request.support_state,
            authority_scope=ContextAuthorityScope(*authority_shape),
            external_references=external,
        )
        return isolated, (
            "support",
            context_shape,
            *primitive_fields,
            request.support_decision_ids,
            authority_shape,
            external_shape,
        )
    if type(request) in {
        ContextBindingValidationRequest,
        ContextAncestryValidationRequest,
    }:
        context, context_shape = _snapshot_context_callback_record(
            request.context_ref, "context-ref"
        )
        external, external_shape = _snapshot_context_callback_externals(
            request.external_references
        )
        request_type = type(request)
        isolated = request_type(
            context_ref=context,
            external_references=external,
        )
        return isolated, (request_type.__name__, context_shape, external_shape)
    if type(request) is InputBindingValidationRequest:
        context, context_shape = _snapshot_context_callback_record(
            request.context_ref, "context-ref"
        )
        binding, binding_shape = _snapshot_context_callback_record(
            request.input_binding, "input-binding"
        )
        external, external_shape = _snapshot_context_callback_externals(
            request.external_references
        )
        isolated = InputBindingValidationRequest(
            context_ref=context,
            input_binding=binding,
            external_references=external,
        )
        return isolated, (
            "input-binding",
            context_shape,
            binding_shape,
            external_shape,
        )
    raise TypeError("unsupported context validation request")


def _run_bool_port(
    callback: Callable[[Any], bool] | None,
    request: Any,
    *,
    relations: ContextRelationResolvers,
    code: str,
    path: str,
    label: str,
    errors: list[ValidationDiagnostic],
) -> None:
    if callback is None:
        errors.append(
            _diagnostic(
                ValidationPhase.RELATION,
                f"{code}-port",
                path,
                f"{label} validation port is unavailable",
            )
        )
        return
    try:
        isolated_request, expected_shape = _snapshot_context_callback_request(
            request
        )
    except Exception as exc:
        errors.append(
            _diagnostic(
                ValidationPhase.RELATION,
                f"{code}-request",
                path,
                f"{label} request snapshot failed with {type(exc).__name__}",
            )
        )
        return
    try:
        accepted = callback(isolated_request)
    except Exception as exc:
        errors.append(
            _diagnostic(
                ValidationPhase.RELATION,
                f"{code}-failure",
                path,
                f"{label} validator failed with {type(exc).__name__}",
            )
        )
        return
    try:
        _, observed_shape = _snapshot_context_callback_request(isolated_request)
    except Exception:
        observed_shape = None
    if observed_shape != expected_shape:
        errors.append(
            _diagnostic(
                ValidationPhase.RELATION,
                f"{code}-mutation",
                path,
                f"{label} validator mutated its isolated request",
            )
        )
        return
    if accepted is not True:
        errors.append(
            _diagnostic(
                ValidationPhase.RELATION,
                code,
                path,
                f"{label} validator rejected the exact immutable binding",
            )
        )


def validate_context_publication(
    context_ref: ContextRecordResolution,
    input_binding: ContextRecordResolution,
    *,
    relations: ContextRelationResolvers,
) -> tuple[ValidatedContextRecord, ValidatedContextRecord]:
    """Validate one exact ContextRef/InputBinding pair and all trusted ports."""

    if type(relations) is not ContextRelationResolvers:
        raise TypeError("relations must be an exact ContextRelationResolvers value")

    context = _coerce_context_record(context_ref, "context-ref")
    binding = _coerce_context_record(input_binding, "input-binding")
    errors: list[ValidationDiagnostic] = []
    if binding.to_dict()["context_ref_id"] != context.id:
        errors.append(
            _diagnostic(
                ValidationPhase.RELATION,
                "relation.input-context",
                "/context_ref_id",
                "InputBinding.context_ref_id must equal the exact ContextRef ID",
            )
        )
    external, reference_errors = _resolve_references((context, binding), relations)
    errors.extend(reference_errors)
    external_view = MappingProxyType(dict(external))
    context_value = context.to_dict()
    profile_bindings = [
        ("platform", context_value["profile_scope"]["platform"])
    ]
    pack = context_value["profile_scope"]["pack"]
    if pack["kind"] == "selected":
        profile_bindings.append(("pack", pack))
    for profile_kind, profile in profile_bindings:
        revision_field = f"{profile_kind}_profile_revision_id"
        request = SupportValidationRequest(
            context_ref=context,
            profile_kind=profile_kind,
            profile_revision_id=profile[revision_field],
            profile_adapter_id=profile["profile_adapter_id"],
            support_decision_ids=tuple(profile["support_decision_ids"]),
            support_state=profile["support_state"],
            authority_scope=_scope(profile["profile_authority"]),
            external_references=external_view,
        )
        _run_bool_port(
            relations.support_validator,
            request,
            relations=relations,
            code="relation.profile-support",
            path=f"/profile_scope/{profile_kind}",
            label=f"{profile_kind} profile support",
            errors=errors,
        )
    _run_bool_port(
        relations.context_binding_validator,
        ContextBindingValidationRequest(
            context_ref=context,
            external_references=external_view,
        ),
        relations=relations,
        code="relation.context-binding",
        path="",
        label="whole ContextRef applicability",
        errors=errors,
    )
    _run_bool_port(
        relations.ancestry_validator,
        ContextAncestryValidationRequest(
            context_ref=context,
            external_references=external_view,
        ),
        relations=relations,
        code="relation.context-ancestry",
        path="",
        label="runtime/world/dimension ancestry",
        errors=errors,
    )
    _run_bool_port(
        relations.input_binding_validator,
        InputBindingValidationRequest(
            context_ref=context,
            input_binding=binding,
            external_references=external_view,
        ),
        relations=relations,
        code="relation.input-binding-applicability",
        path="",
        label="input binding applicability",
        errors=errors,
    )
    for index, schema_binding in enumerate(binding.to_dict()["schema_bindings"]):
        resolved = external.get(schema_binding["schema_object_descriptor_id"])
        if resolved is None:
            continue
        descriptor = parse_canonical_json(resolved.canonical_bytes)
        if (
            type(descriptor) is not dict
            or descriptor.get("described_schema_id") != schema_binding["schema_id"]
        ):
            errors.append(
                _diagnostic(
                    ValidationPhase.RELATION,
                    "relation.input-schema-binding",
                    f"/schema_bindings/{index}",
                    "schema binding URI must equal the exact schema object descriptor declaration",
                )
            )
    if errors:
        raise RecordValidationError(tuple(errors))
    return context, binding


__all__ = [
    "ContextAncestryValidationRequest",
    "ContextAncestryValidator",
    "ContextAuthorityScope",
    "ContextBindingValidationRequest",
    "ContextBindingValidator",
    "ContextRecordResolution",
    "ContextReferenceExpectation",
    "ContextReferenceResolver",
    "ContextRelationResolvers",
    "InputBindingValidationRequest",
    "InputBindingValidator",
    "SupportValidationRequest",
    "SupportValidator",
    "ValidatedContextRecord",
    "ValidatedContextReference",
    "load_context_ref",
    "load_input_binding",
    "seal_context_ref",
    "seal_input_binding",
    "validate_context_publication",
    "validate_context_ref",
    "validate_input_binding",
]
