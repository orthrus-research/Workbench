"""Strict admission for the Cleanroom observer's raw NDJSON transport.

This module is intentionally a pre-contract boundary.  Successfully admitted
rows are still raw observer output: they are not Crucible records, a sealed
capture, or a claim that a Cleanroom runtime observation has been proven.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .bundle import CaptureValidationError


PROBE_PLAN_FORMAT = "workbench-cleanroom-worldgen-observatory-probe-plan-v1"
PROBE_PLAN_PREFIX = "cleanroom-worldgen-observatory-probe-plan:sha256:"
_CAPTURE_NONCE_FIELDS = ("capture_nonce", "capture_id")
_ZERO_SHA256 = "0" * 64
_CORE_ROW_FIELDS = {
    "format",
    "record_type",
    "sequence",
    "scope",
    "causality",
    "actor",
    "order",
    "outcome",
    "coverage",
    "payload",
}


@dataclass(frozen=True, slots=True)
class RawAdmission:
    """Immutable view of raw rows that passed transport admission.

    The summaries contain only facts present in the raw transport and its
    supplied static probe plan.  A later, separately authorized normalizer is
    responsible for constructing any canonical Crucible evidence.
    """

    rows: tuple[Mapping[str, Any], ...]
    capture_nonce: str
    requested_mode: str
    schema_id: str
    schema_sha256: str
    probe_plan_id: str
    control_summary: Mapping[str, Any]
    coverage_summary: Mapping[str, Any]
    health_summary: Mapping[str, Mapping[str, Any]]


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _parse_json(text: str, *, source: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse {source}: {exc}") from exc


def _read_json(path: Path, *, kind: str) -> tuple[dict[str, Any], bytes]:
    try:
        encoded = path.read_bytes()
        value = _parse_json(encoded.decode("utf-8"), source=str(path))
    except (OSError, UnicodeError) as exc:
        raise CaptureValidationError(f"cannot load {kind} {path}: {exc}") from exc
    _require(isinstance(value, dict), f"{kind} must be a JSON object: {path}")
    return value, encoded


def _canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CaptureValidationError(
            f"probe plan cannot be canonically encoded: {exc}"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _schema_validator(schema: Mapping[str, Any]) -> Any:
    try:
        from jsonschema import Draft202012Validator, SchemaError
    except ModuleNotFoundError as exc:
        raise CaptureValidationError(
            "jsonschema is required for raw Cleanroom admission"
        ) from exc
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise CaptureValidationError(
            f"invalid raw Draft 2020-12 schema: {exc.message}"
        ) from exc
    return Draft202012Validator(schema)


def _record_types(schema: Mapping[str, Any]) -> frozenset[str]:
    properties = schema.get("properties")
    _require(isinstance(properties, Mapping), "raw schema lacks object properties")
    record_type = properties.get("record_type")
    _require(
        isinstance(record_type, Mapping),
        "raw schema lacks a record_type declaration",
    )
    values = record_type.get("enum")
    _require(
        isinstance(values, list)
        and bool(values)
        and all(isinstance(item, str) and item for item in values)
        and len(values) == len(set(values)),
        "raw schema record_type must be a unique nonempty string enum",
    )
    return frozenset(values)


def _capture_nonce_field(schema: Mapping[str, Any]) -> str:
    properties = schema.get("properties")
    required = schema.get("required")
    _require(isinstance(properties, Mapping), "raw schema lacks object properties")
    _require(isinstance(required, list), "raw schema lacks required fields")
    present = [field for field in _CAPTURE_NONCE_FIELDS if field in properties]
    _require(
        len(present) == 1 and present[0] in required,
        "raw schema must require exactly one capture nonce field",
    )
    return present[0]


def _payload_schema_is_closed(
    value: Any,
    definitions: Mapping[str, Any],
    visited: frozenset[str] = frozenset(),
) -> bool:
    if not isinstance(value, Mapping):
        return False
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference[len("#/$defs/") :].replace("~1", "/").replace("~0", "~")
        if name in visited or name not in definitions:
            return False
        return _payload_schema_is_closed(
            definitions[name], definitions, visited | {name}
        )
    if value.get("additionalProperties") is False:
        return True
    variants = value.get("oneOf")
    return (
        isinstance(variants, list)
        and bool(variants)
        and all(
            _payload_schema_is_closed(variant, definitions, visited)
            for variant in variants
        )
    )


def _validate_schema_surface(
    schema: Mapping[str, Any],
    *,
    record_types: frozenset[str],
    nonce_field: str,
) -> None:
    properties = schema.get("properties")
    required = schema.get("required")
    _require(
        schema.get("additionalProperties") is False,
        "raw schema must reject unknown top-level fields",
    )
    _require(
        isinstance(properties, Mapping)
        and _CORE_ROW_FIELDS | {nonce_field} <= set(properties),
        "raw schema lacks required transport properties",
    )
    _require(
        isinstance(required, list)
        and _CORE_ROW_FIELDS | {nonce_field} <= set(required),
        "raw schema does not require the complete transport surface",
    )

    definitions = schema.get("$defs")
    clauses = schema.get("allOf")
    _require(
        isinstance(definitions, Mapping) and isinstance(clauses, list),
        "raw schema lacks closed record payload definitions",
    )
    payload_refs: dict[str, str] = {}
    for clause in clauses:
        if not isinstance(clause, Mapping):
            continue
        condition = clause.get("if")
        consequence = clause.get("then")
        if not isinstance(condition, Mapping) or not isinstance(consequence, Mapping):
            continue
        condition_properties = condition.get("properties")
        consequence_properties = consequence.get("properties")
        if not isinstance(condition_properties, Mapping) or not isinstance(
            consequence_properties, Mapping
        ):
            continue
        record_declaration = condition_properties.get("record_type")
        payload_declaration = consequence_properties.get("payload")
        if not isinstance(record_declaration, Mapping) or not isinstance(
            payload_declaration, Mapping
        ):
            continue
        record_type = record_declaration.get("const")
        reference = payload_declaration.get("$ref")
        if isinstance(record_type, str) and isinstance(reference, str):
            _require(
                record_type not in payload_refs,
                f"raw schema duplicates payload binding for {record_type}",
            )
            payload_refs[record_type] = reference
    _require(
        set(payload_refs) == set(record_types),
        "raw schema does not bind every admitted record type to one payload schema",
    )
    for record_type, reference in payload_refs.items():
        prefix = "#/$defs/"
        _require(
            reference.startswith(prefix) and "/" not in reference[len(prefix) :],
            f"raw schema payload reference is not local for {record_type}",
        )
        definition_name = reference[len(prefix) :].replace("~1", "/").replace(
            "~0", "~"
        )
        definition = definitions.get(definition_name)
        _require(
            _payload_schema_is_closed(
                definition, definitions, frozenset({definition_name})
            ),
            f"raw schema payload is not closed for {record_type}",
        )


def _plan_hooks(plan: Mapping[str, Any]) -> tuple[
    dict[str, dict[str, Any]], dict[str, str]
]:
    _require(
        plan.get("format") == PROBE_PLAN_FORMAT,
        "candidate probe plan format mismatch",
    )
    plan_id = plan.get("plan_id")
    _require(
        isinstance(plan_id, str) and plan_id.startswith(PROBE_PLAN_PREFIX),
        "candidate probe plan lacks a valid plan ID",
    )
    material = dict(plan)
    material.pop("plan_id", None)
    _require(
        plan_id == PROBE_PLAN_PREFIX + _canonical_sha256(material),
        "candidate probe plan ID digest mismatch",
    )

    hooks_value = plan.get("hooks")
    _require(
        isinstance(hooks_value, list) and bool(hooks_value),
        "candidate probe plan has no hooks",
    )
    hooks: dict[str, dict[str, Any]] = {}
    roles: dict[str, str] = {}
    for index, value in enumerate(hooks_value):
        _require(
            isinstance(value, Mapping),
            f"candidate probe hook {index} must be an object",
        )
        hook_id = value.get("raw_hook_id")
        _require(
            isinstance(hook_id, str) and bool(hook_id.strip()),
            f"candidate probe hook {index} lacks a raw hook ID",
        )
        _require(
            hook_id not in hooks,
            f"candidate probe plan duplicates hook {hook_id}",
        )
        for field in ("target_class", "target_method", "target_descriptor"):
            _require(
                isinstance(value.get(field), str) and bool(value[field]),
                f"candidate probe hook {hook_id} lacks {field}",
            )
        cardinality = value.get("expected_cardinality")
        _require(
            isinstance(cardinality, int)
            and not isinstance(cardinality, bool)
            and cardinality > 0,
            f"candidate probe hook {hook_id} has invalid cardinality",
        )
        role = value.get("canonical_role")
        _require(
            role is None or (isinstance(role, str) and bool(role.strip())),
            f"candidate probe hook {hook_id} has invalid canonical role",
        )
        if role is not None:
            _require(
                role not in roles,
                f"candidate probe plan duplicates canonical role {role}",
            )
            roles[role] = hook_id
        hooks[hook_id] = {
            "raw_hook_id": hook_id,
            "canonical_role": role,
            "target_class": value["target_class"],
            "target_method": value["target_method"],
            "target_descriptor": value["target_descriptor"],
            "expected_cardinality": cardinality,
        }
    return hooks, roles


def _bind_plan_to_schema(
    plan: Mapping[str, Any],
    schema: Mapping[str, Any],
    schema_bytes: bytes,
) -> tuple[str, str]:
    transport = plan.get("raw_transport")
    _require(
        isinstance(transport, Mapping),
        "candidate probe plan lacks a raw transport binding",
    )
    schema_id = schema.get("$id")
    _require(
        isinstance(schema_id, str) and bool(schema_id),
        "raw schema lacks an ID",
    )
    schema_sha256 = hashlib.sha256(schema_bytes).hexdigest()
    _require(
        transport.get("schema_id") == schema_id,
        "candidate probe plan raw schema ID mismatch",
    )
    _require(
        transport.get("schema_sha256") == schema_sha256,
        "candidate probe plan raw schema digest mismatch",
    )
    properties = schema.get("properties", {})
    format_schema = properties.get("format", {}) if isinstance(properties, Mapping) else {}
    raw_format = format_schema.get("const") if isinstance(format_schema, Mapping) else None
    _require(
        isinstance(raw_format, str)
        and bool(raw_format)
        and transport.get("format") == raw_format,
        "candidate probe plan raw format mismatch",
    )
    return schema_id, schema_sha256


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                _require(
                    bool(line.strip()),
                    f"raw NDJSON line {line_number} is blank",
                )
                value = _parse_json(
                    line,
                    source=f"raw NDJSON line {line_number}",
                )
                _require(
                    isinstance(value, dict),
                    f"raw NDJSON line {line_number} must contain one JSON object",
                )
                rows.append(value)
    except (OSError, UnicodeError) as exc:
        raise CaptureValidationError(f"cannot read raw NDJSON {path}: {exc}") from exc
    _require(bool(rows), "raw NDJSON is empty")
    return rows


def _validate_rows(
    rows: list[dict[str, Any]],
    *,
    validator: Any,
    nonce_field: str,
    requested_mode: str,
) -> tuple[str, dict[tuple[str, int], int]]:
    expected_thread_sequences: dict[tuple[str, int], int] = {}
    last_monotonic: dict[tuple[str, int], int] = {}
    nonces: set[str] = set()

    for index, row in enumerate(rows):
        try:
            errors = sorted(
                validator.iter_errors(row),
                key=lambda error: (
                    tuple(str(item) for item in error.absolute_path),
                    error.message,
                ),
            )
        except Exception as exc:
            raise CaptureValidationError(
                f"raw schema evaluation failed on NDJSON line {index + 1}: {exc}"
            ) from exc
        if errors:
            first = errors[0]
            location = ".".join(str(item) for item in first.absolute_path)
            suffix = f" at {location}" if location else ""
            raise CaptureValidationError(
                f"raw schema violation on NDJSON line {index + 1}{suffix}: "
                f"{first.message}"
            )

        sequence = row["sequence"]
        _require(
            sequence >= index,
            f"duplicate global sequence {sequence} on NDJSON line {index + 1}",
        )
        _require(
            sequence == index,
            f"global sequence is not contiguous at NDJSON line {index + 1}: "
            f"expected {index}, found {sequence}",
        )

        order = row["order"]
        thread_key = (order["thread_name"], order["thread_id"])
        thread_sequence = order["thread_sequence"]
        expected = expected_thread_sequences.get(thread_key, 0)
        _require(
            thread_sequence >= expected,
            f"duplicate thread sequence {thread_sequence} for {thread_key}",
        )
        _require(
            thread_sequence == expected,
            f"thread sequence is not contiguous for {thread_key}: "
            f"expected {expected}, found {thread_sequence}",
        )
        expected_thread_sequences[thread_key] = expected + 1
        monotonic_ns = order["monotonic_ns"]
        _require(
            monotonic_ns >= last_monotonic.get(thread_key, -1),
            f"monotonic time moved backward for {thread_key}",
        )
        last_monotonic[thread_key] = monotonic_ns

        nonce = row[nonce_field]
        _require(
            isinstance(nonce, str)
            and bool(nonce.strip())
            and nonce.strip() != "unbound",
            f"raw capture nonce is empty or unbound on NDJSON line {index + 1}",
        )
        nonces.add(nonce)
        coverage = row["coverage"]
        _require(
            coverage["mode"] == requested_mode,
            f"raw requested mode mismatch on NDJSON line {index + 1}",
        )
        _require(
            coverage["detail_state"] == "complete",
            f"raw detail is not complete on NDJSON line {index + 1}",
        )
        _require(
            coverage["dropped_record_count"] == 0,
            f"raw cumulative dropped count is nonzero on NDJSON line {index + 1}",
        )

    _require(len(nonces) == 1, "raw NDJSON contains multiple capture nonces")
    return next(iter(nonces)), expected_thread_sequences


def _validate_controls(
    rows: list[dict[str, Any]],
    *,
    control_admitted: bool,
    capture_nonce: str,
    requested_mode: str,
    allow_incomplete: bool,
) -> dict[str, Any]:
    if not control_admitted:
        _require(
            not allow_incomplete,
            "incomplete raw admission requires a typed capture-control schema",
        )
        return {"present": False}

    controls = [row for row in rows if row["record_type"] == "capture_control"]
    expected_counts = {1, 2} if allow_incomplete else {2}
    _require(len(controls) in expected_counts, (
        "incomplete raw capture requires one start and at most one stop control"
        if allow_incomplete
        else "raw capture requires exactly one start and one stop control"
    ))
    start = controls[0]
    stop = controls[1] if len(controls) == 2 else None
    _require(
        start is rows[0],
        "raw capture start control must be the first record",
    )
    start_payload = start["payload"]
    _require(
        start_payload.get("control") == "start",
        "raw capture lacks a typed start control",
    )
    control_id = start_payload.get("capture_control_id")
    controller = start_payload.get("controller")
    _require(
        isinstance(control_id, str)
        and bool(control_id.strip())
        and control_id.startswith(capture_nonce + ":"),
        "raw capture control ID is empty or foreign",
    )
    _require(
        isinstance(controller, str) and bool(controller.strip()),
        "raw capture controller is empty",
    )
    _require(
        start_payload.get("requested_mode") == requested_mode,
        "raw capture start requested mode mismatch",
    )
    _require(
        start["outcome"]["state"] == "entered",
        "raw capture start outcome is not entered",
    )

    if stop is None:
        _require(
            allow_incomplete,
            "raw capture requires exactly one start and one stop control",
        )
        return {
            "present": True,
            "start_sequence": start["sequence"],
            "stop_sequence": None,
            "capture_control_id": control_id,
            "controller": controller,
            "completion_state": "incomplete",
            "incomplete_reason": "missing_stop",
            "route_order": start_payload.get("route_order"),
            "route_sha256": start_payload.get("route_sha256"),
            "start_payload": start_payload,
            "stop_payload": None,
        }

    _require(
        stop is rows[-1],
        "raw capture stop control must be the last record",
    )
    stop_payload = stop["payload"]
    _require(
        stop_payload.get("control") == "stop",
        "raw capture controls are not a typed start/stop pair",
    )
    _require(
        stop_payload.get("capture_control_id") == control_id,
        "raw capture control ID is mismatched",
    )
    _require(
        stop_payload.get("controller") == controller,
        "raw capture controller is mismatched",
    )
    _require(
        stop_payload.get("requested_mode") == requested_mode,
        "raw capture stop requested mode mismatch",
    )
    for field in ("route_order", "route_sha256"):
        _require(
            start_payload.get(field) == stop_payload.get(field),
            f"raw capture control {field} mismatch",
        )

    if allow_incomplete:
        _require(
            stop_payload.get("completion_state") == "incomplete"
            and stop["outcome"]["state"] == "incomplete",
            "raw residue stop is not explicitly incomplete",
        )
        completion_state = "incomplete"
        incomplete_reason = "explicit_incomplete_stop"
    else:
        _require(
            stop_payload.get("completion_state") == "complete",
            "raw capture stop is not complete",
        )
        _require(
            stop["outcome"]["state"] == "returned",
            "raw capture control outcomes do not prove a completed pair",
        )
        _require(
            stop_payload.get("open_span_count") == 0
            and stop_payload.get("open_write_count") == 0,
            "raw capture stop reports open spans or writes",
        )
        completion_state = "complete"
        incomplete_reason = None
    return {
        "present": True,
        "start_sequence": start["sequence"],
        "stop_sequence": stop["sequence"],
        "capture_control_id": control_id,
        "controller": controller,
        "completion_state": completion_state,
        "incomplete_reason": incomplete_reason,
        "route_order": stop_payload.get("route_order"),
        "route_sha256": stop_payload.get("route_sha256"),
        "start_payload": start_payload,
        "stop_payload": stop_payload,
    }


def _validate_probe_health(
    rows: list[dict[str, Any]],
    *,
    hooks: Mapping[str, Mapping[str, Any]],
    roles: Mapping[str, str],
    required_roles: Iterable[str] | None,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    if required_roles is None:
        selected_roles = tuple(sorted(roles))
    elif isinstance(required_roles, str):
        selected_roles = (required_roles,)
    else:
        try:
            requested_roles = tuple(required_roles)
        except TypeError as exc:
            raise CaptureValidationError("required probe roles are not iterable") from exc
        _require(
            all(isinstance(role, str) and bool(role.strip()) for role in requested_roles),
            "required probe roles must be nonempty strings",
        )
        selected_roles = tuple(sorted(set(requested_roles)))
    _require(
        all(isinstance(role, str) and bool(role.strip()) for role in selected_roles),
        "required probe roles must be nonempty strings",
    )
    unknown_roles = sorted(set(selected_roles) - set(roles))
    _require(
        not unknown_roles,
        "candidate probe plan lacks required roles: " + ", ".join(unknown_roles),
    )

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["record_type"] != "probe_health":
            continue
        payload = row["payload"]
        hook_id = payload.get("hook_id")
        _require(
            isinstance(hook_id, str) and hook_id in hooks,
            f"raw probe health names unplanned hook {hook_id!r}",
        )
        planned = hooks[hook_id]
        observed_tuple = (
            payload.get("target_class"),
            payload.get("target_method"),
            payload.get("target_descriptor"),
        )
        planned_tuple = (
            planned["target_class"],
            planned["target_method"],
            planned["target_descriptor"],
        )
        _require(
            observed_tuple == planned_tuple,
            f"raw probe health target tuple mismatch for {hook_id}",
        )
        expected = planned["expected_cardinality"]
        _require(
            payload.get("expected_injection_count") == expected
            and payload.get("observed_injection_count") == expected,
            f"raw probe health cardinality mismatch for {hook_id}",
        )
        original_digest = payload.get("original_class_sha256")
        transformed_digest = payload.get("transformed_class_sha256")
        _require(
            original_digest != _ZERO_SHA256
            and transformed_digest != _ZERO_SHA256
            and original_digest != transformed_digest,
            f"raw probe health lacks a transformed class identity for {hook_id}",
        )
        latest[hook_id] = {
            "raw_hook_id": hook_id,
            "canonical_role": planned["canonical_role"],
            "health_state": payload.get("health_state"),
            "sequence": row["sequence"],
            "target_class": observed_tuple[0],
            "target_method": observed_tuple[1],
            "target_descriptor": observed_tuple[2],
            "expected_injection_count": expected,
            "observed_injection_count": payload["observed_injection_count"],
            "original_class_sha256": original_digest,
            "transformed_class_sha256": transformed_digest,
        }

    for role in selected_roles:
        hook_id = roles[role]
        summary = latest.get(hook_id)
        _require(
            summary is not None and summary["health_state"] == "reached",
            f"required raw probe role {role} was not latest reached",
        )
    return {hook_id: latest[hook_id] for hook_id in sorted(latest)}, selected_roles


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list) or isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def admit_cleanroom_raw(
    path: str | os.PathLike[str],
    *,
    schema_path: str | os.PathLike[str],
    probe_plan_path: str | os.PathLike[str],
    requested_mode: str,
    required_roles: Iterable[str] | None = None,
    allow_incomplete: bool = False,
) -> RawAdmission:
    """Admit raw Cleanroom NDJSON without constructing Crucible evidence.

    All paths are explicit caller inputs.  The candidate plan must byte-bind
    the supplied schema, and every raw row is validated against that exact
    Draft 2020-12 schema before any cross-row invariant is evaluated.
    """

    _require(
        isinstance(requested_mode, str) and bool(requested_mode.strip()),
        "requested raw capture mode must be a nonempty string",
    )
    _require(
        isinstance(allow_incomplete, bool),
        "allow_incomplete must be a boolean",
    )
    raw_path = Path(path)
    exact_schema_path = Path(schema_path)
    exact_plan_path = Path(probe_plan_path)
    schema, schema_bytes = _read_json(exact_schema_path, kind="raw schema")
    plan, _ = _read_json(exact_plan_path, kind="candidate probe plan")
    validator = _schema_validator(schema)
    admitted_record_types = _record_types(schema)
    nonce_field = _capture_nonce_field(schema)
    _validate_schema_surface(
        schema,
        record_types=admitted_record_types,
        nonce_field=nonce_field,
    )
    hooks, roles = _plan_hooks(plan)
    schema_id, schema_sha256 = _bind_plan_to_schema(plan, schema, schema_bytes)

    rows = _read_rows(raw_path)
    capture_nonce, thread_counts = _validate_rows(
        rows,
        validator=validator,
        nonce_field=nonce_field,
        requested_mode=requested_mode,
    )
    control_summary = _validate_controls(
        rows,
        control_admitted="capture_control" in admitted_record_types,
        capture_nonce=capture_nonce,
        requested_mode=requested_mode,
        allow_incomplete=allow_incomplete,
    )
    effective_required_roles = (
        () if allow_incomplete and required_roles is None else required_roles
    )
    health_summary, selected_roles = _validate_probe_health(
        rows,
        hooks=hooks,
        roles=roles,
        required_roles=effective_required_roles,
    )
    coverage_summary = {
        "capture_nonce_field": nonce_field,
        "row_count": len(rows),
        "first_sequence": rows[0]["sequence"],
        "last_sequence": rows[-1]["sequence"],
        "thread_count": len(thread_counts),
        "requested_mode": requested_mode,
        "detail_state": "complete",
        "dropped_record_count": 0,
        "required_roles": list(selected_roles),
        "completion_state": control_summary.get("completion_state"),
    }
    return RawAdmission(
        rows=tuple(_freeze(row) for row in rows),
        capture_nonce=capture_nonce,
        requested_mode=requested_mode,
        schema_id=schema_id,
        schema_sha256=schema_sha256,
        probe_plan_id=plan["plan_id"],
        control_summary=_freeze(control_summary),
        coverage_summary=_freeze(coverage_summary),
        health_summary=_freeze(health_summary),
    )


__all__ = ["RawAdmission", "admit_cleanroom_raw"]
