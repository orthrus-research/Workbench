"""Closed, offline Draft 2020-12 registry for Crucible C02 context records."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

from workbench_api.canonical import CanonicalJsonError, parse_json_strict
from workbench_crucible.schema_registry import (
    RecordValidationError,
    ValidationDiagnostic,
    ValidationPhase,
)


CONTEXT_REF_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-context-ref-v2.schema.json"
)
INPUT_BINDING_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-input-binding-v2.schema.json"
)
COMMON_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-context-v2-common.schema.json"
)


@dataclass(frozen=True, slots=True)
class ContextRecordContract:
    kind: str
    format: str
    schema_id: str
    filename: str


_CONTRACTS = (
    ContextRecordContract(
        kind="context-ref",
        format="workbench-crucible-context-ref-v2",
        schema_id=CONTEXT_REF_SCHEMA_ID,
        filename="crucible-context-ref-v2.schema.json",
    ),
    ContextRecordContract(
        kind="input-binding",
        format="workbench-crucible-input-binding-v2",
        schema_id=INPUT_BINDING_SCHEMA_ID,
        filename="crucible-input-binding-v2.schema.json",
    ),
)
_RESOURCE_FILES = {
    COMMON_SCHEMA_ID: "crucible-context-v2-common.schema.json",
    **{item.schema_id: item.filename for item in _CONTRACTS},
}


def _schema_root() -> Path:
    return _module_resource_root(__file__, 'crucible') / "schemas"


def _pointer(parts: tuple[Any, ...]) -> str:
    if not parts:
        return ""
    return "/" + "/".join(
        str(item).replace("~", "~0").replace("/", "~1") for item in parts
    )


def _walk(value: Any, path: tuple[Any, ...] = ()):
    yield value, path
    if type(value) is dict:
        for key, child in value.items():
            yield from _walk(child, (*path, key))
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _walk(child, (*path, index))


def _assert_recursive_closed_objects(schema: dict[str, Any], filename: str) -> None:
    for node, path in _walk(schema):
        if type(node) is not dict:
            continue
        # Conditional branches are assertion fragments conjoined with the
        # already-closed containing object; they do not independently admit an
        # object instance.
        if any(item in {"if", "then", "else"} for item in path):
            continue
        object_capable = node.get("type") == "object" or any(
            key in node for key in ("properties", "required")
        )
        if object_capable and node.get("additionalProperties") is not False:
            raise RuntimeError(
                f"open object schema in {filename} at {_pointer(path) or '/'}"
            )


@lru_cache(maxsize=1)
def _resources() -> tuple[dict[str, dict[str, Any]], dict[str, bytes], Registry]:
    root = _schema_root()
    schemas: dict[str, dict[str, Any]] = {}
    raw_by_id: dict[str, bytes] = {}
    registry = Registry()
    for expected_id, filename in _RESOURCE_FILES.items():
        path = root / filename
        if not path.is_file():
            raise RuntimeError(f"missing C02 context schema: {path}")
        raw = path.read_bytes()
        try:
            value = parse_json_strict(raw)
        except CanonicalJsonError as exc:
            raise RuntimeError(f"invalid JSON schema {path}: {exc}") from exc
        if type(value) is not dict or value.get("$id") != expected_id:
            raise RuntimeError(
                f"schema {path} must declare exact offline ID {expected_id}"
            )
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as exc:
            raise RuntimeError(f"invalid Draft 2020-12 schema {path}: {exc}") from exc
        _assert_recursive_closed_objects(value, filename)
        schemas[expected_id] = value
        raw_by_id[expected_id] = raw
        registry = registry.with_resource(expected_id, Resource.from_contents(value))
    return schemas, raw_by_id, registry


def contract_for(kind: Any, format_name: Any, schema_id: Any) -> ContextRecordContract:
    matches = [
        item
        for item in _CONTRACTS
        if item.kind == kind
        and item.format == format_name
        and item.schema_id == schema_id
    ]
    if len(matches) != 1:
        raise RecordValidationError(
            (
                ValidationDiagnostic(
                    phase=ValidationPhase.SCHEMA,
                    code="schema.context-format-registry",
                    path="",
                    message="kind, format, and schema_id must name one exact C02 context contract",
                ),
            )
        )
    return matches[0]


def validate_schema(record: dict[str, Any]) -> ContextRecordContract:
    contract = contract_for(
        record.get("kind"), record.get("format"), record.get("schema_id")
    )
    schemas, _, registry = _resources()
    validator = Draft202012Validator(schemas[contract.schema_id], registry=registry)
    try:
        violations = sorted(
            validator.iter_errors(record),
            key=lambda error: (
                _pointer(tuple(error.absolute_path)).encode("utf-8"),
                _pointer(tuple(error.absolute_schema_path)).encode("utf-8"),
                str(error.validator).encode("utf-8"),
                error.message.encode("utf-8"),
            ),
        )
    except Unresolvable as exc:
        raise RuntimeError(f"offline context schema reference is unresolved: {exc}") from exc
    if violations:
        diagnostics = tuple(
            ValidationDiagnostic(
                phase=ValidationPhase.SCHEMA,
                code=f"schema.{error.validator}",
                path=_pointer(tuple(error.absolute_path)),
                message=error.message,
            )
            for error in violations
        )
        raise RecordValidationError(diagnostics)
    return contract


def registered_schema_bytes(schema_id: str) -> bytes | None:
    _, raw_by_id, _ = _resources()
    return raw_by_id.get(schema_id)


def registered_context_contracts() -> tuple[ContextRecordContract, ...]:
    return _CONTRACTS


def assert_context_schema_registry_ready() -> tuple[str, ...]:
    schemas, _, _ = _resources()
    return tuple(sorted(schemas))


__all__ = [
    "COMMON_SCHEMA_ID",
    "CONTEXT_REF_SCHEMA_ID",
    "ContextRecordContract",
    "INPUT_BINDING_SCHEMA_ID",
    "assert_context_schema_registry_ready",
    "contract_for",
    "registered_context_contracts",
    "registered_schema_bytes",
    "validate_schema",
]
