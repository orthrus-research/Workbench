"""Foundation final class-definition byte evidence V1."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


RAW_FINAL_DEFINITION_FORMAT = "workbench-foundation-final-class-definition-raw-v1"
FINAL_DEFINITION_RECEIPT_FORMAT = (
    "workbench-crucible-mixin-final-class-definition-receipt-v1"
)
FINAL_DEFINITION_RECEIPT_PREFIX = (
    "crucible-mixin-final-class-definition:sha256:"
)
CANONICALIZATION_ID = "workbench-canonical-json-v1"
FOUNDATION_MANIFEST_FORMAT = "workbench-foundation-class-dump-manifest-v1"

_SHA256 = frozenset("0123456789abcdef")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_RAW_KEYS = frozenset({"format", "capture_id", "sequence", "event", "payload"})
_RAW_EVENTS = frozenset(
    {
        "capture_start",
        "transformer_installed",
        "transform_applied",
        "transform_rejected",
        "transform_failure",
        "find_started",
        "final_definition",
        "cached_return",
        "definition_failure",
        "observer_failure",
        "capture_end",
    }
)
_INPUT_NAMES = (
    "agent_artifact",
    "candidate_lock",
    "fixture_result",
    "launch_log",
    "raw_trace",
    "toolchain_lock",
)
_FILE_KEYS = frozenset({"label", "sha256", "size_bytes"})
_SESSION_KEYS = frozenset(
    {
        "capture_id",
        "launch_id",
        "profile_id",
        "side",
        "candidate_lock_sha256",
        "toolchain_lock_sha256",
    }
)
_ARTIFACT_KEYS = frozenset(
    {"artifact_sha256", "size_bytes", "label", "code_source_uri"}
)

MANDATORY_LIMITATIONS = (
    "The receipt proves only the named classes that returned successfully from the exact Foundation findClass seam in the bound launch.",
    "Foundation dump bytes are joined to successful definition returns; a dump entry by itself is not treated as proof that defineClass succeeded.",
    "Launch-local Class and classloader identity strings are evidence locators, not cross-launch semantic identities.",
    "Final class bytes prove definition custody, not successful invocation or application-level behavior.",
)


class FinalDefinitionValidationError(ValueError):
    """Final-definition evidence is malformed or overclaims."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FinalDefinitionValidationError(message)


def canonical_final_definition_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FinalDefinitionValidationError(
            f"cannot canonically encode final-definition evidence: {exc}"
        ) from exc


def _closed(value: Mapping[str, Any], keys: frozenset[str], context: str) -> None:
    missing = keys - set(value)
    unknown = set(value) - keys
    _require(not missing, f"{context} lacks fields: {sorted(missing)}")
    _require(not unknown, f"{context} has unknown fields: {sorted(unknown)}")


def _text(value: Any, context: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    _require(
        isinstance(value, str)
        and bool(value)
        and len(value) <= 8192
        and not any(character in value for character in "\r\n\x00"),
        f"{context} must be bounded nonempty single-line text",
    )
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _boolean(value: Any, context: str) -> bool:
    _require(type(value) is bool, f"{context} must be boolean")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _string_list(
    value: Any, context: str, *, sorted_unique: bool = False
) -> list[str]:
    _require(isinstance(value, list), f"{context} must be an array")
    result = [_text(item, f"{context}[{index}]") for index, item in enumerate(value)]
    if sorted_unique:
        _require(result == sorted(set(result)), f"{context} must be sorted and unique")
    return result


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _transform_payload(payload: Mapping[str, Any], context: str, event: str) -> str:
    keys = {
        "target_class",
        "defining_loader_class",
        "defining_loader_identity",
        "target_code_source_uri",
        "input_sha256",
        "output_sha256",
        "reason",
    }
    if event == "transform_failure":
        keys |= {"exception_class", "message", "stack_top"}
    _closed(payload, frozenset(keys), context)
    target = _text(payload["target_class"], f"{context}.target_class")
    _text(payload["defining_loader_class"], f"{context}.defining_loader_class")
    _text(payload["defining_loader_identity"], f"{context}.defining_loader_identity")
    _text(payload["target_code_source_uri"], f"{context}.target_code_source_uri")
    _sha256(payload["input_sha256"], f"{context}.input_sha256")
    if event == "transform_applied":
        _sha256(payload["output_sha256"], f"{context}.output_sha256")
        _require(payload["reason"] is None, f"{context}.reason must be null")
    else:
        _require(payload["output_sha256"] is None, f"{context}.output_sha256 must be null")
        _text(payload["reason"], f"{context}.reason")
    return target


def parse_raw_final_definition(encoded: bytes) -> list[dict[str, Any]]:
    """Strictly parse one final-definition NDJSON stream."""

    _require(isinstance(encoded, bytes) and bool(encoded), "raw final-definition trace is empty")
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise FinalDefinitionValidationError(
            f"raw final-definition trace is not UTF-8: {exc}"
        ) from exc
    _require(text.endswith("\n"), "raw final-definition trace lacks a terminal newline")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        _require(bool(line), f"raw final-definition line {index + 1} is empty")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_json_object,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite number {item}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
            raise FinalDefinitionValidationError(
                f"raw final-definition line {index + 1} is malformed: {exc}"
            ) from exc
        _require(isinstance(value, Mapping), f"raw final-definition line {index + 1} is not an object")
        _closed(value, _RAW_KEYS, f"raw final-definition line {index + 1}")
        _require(value["format"] == RAW_FINAL_DEFINITION_FORMAT, "raw final-definition format is not V1")
        _text(value["capture_id"], f"raw final-definition line {index + 1}.capture_id")
        _require(value["sequence"] == index, "raw final-definition sequences are not contiguous from zero")
        _require(value["event"] in _RAW_EVENTS, "raw final-definition event is unsupported")
        _require(isinstance(value["payload"], Mapping), "raw final-definition payload must be an object")
        rows.append(deepcopy(dict(value)))

    _require(len(rows) >= 5, "raw final-definition trace is too short")
    _require(rows[0]["event"] == "capture_start", "raw final-definition trace does not start with capture_start")
    _require(rows[-1]["event"] == "capture_end", "raw final-definition trace does not end with capture_end")
    _require(len({row["capture_id"] for row in rows}) == 1, "raw final-definition trace crosses capture identities")
    _require(sum(row["event"] == "capture_start" for row in rows) == 1, "raw final-definition repeats capture_start")
    _require(sum(row["event"] == "capture_end" for row in rows) == 1, "raw final-definition repeats capture_end")

    start = rows[0]["payload"]
    _closed(
        start,
        frozenset(
            {
                "agent_id",
                "java_version",
                "targets",
                "foundation_dump_enabled",
                "redefine_supported",
                "retransform_supported",
            }
        ),
        "capture_start payload",
    )
    _text(start["agent_id"], "capture_start.agent_id")
    _text(start["java_version"], "capture_start.java_version")
    targets = _string_list(start["targets"], "capture_start.targets", sorted_unique=True)
    _require(bool(targets), "capture_start.targets must be nonempty")
    _require(start["foundation_dump_enabled"] is True, "Foundation dump was not enabled")
    _boolean(start["redefine_supported"], "capture_start.redefine_supported")
    _boolean(start["retransform_supported"], "capture_start.retransform_supported")

    transformed = 0
    transform_failures = 0
    definitions: dict[str, Mapping[str, Any]] = {}
    cached_targets: set[str] = set()
    definition_failures = 0
    observer_failures = 0
    find_starts: dict[str, int] = {target: 0 for target in targets}
    for row in rows[1:-1]:
        event = row["event"]
        payload = row["payload"]
        context = f"{event} payload at sequence {row['sequence']}"
        if event == "transformer_installed":
            _closed(
                payload,
                frozenset(
                    {
                        "target_class",
                        "expected_input_sha256",
                        "retransform_requested",
                    }
                ),
                context,
            )
            _text(payload["target_class"], f"{context}.target_class")
            _sha256(payload["expected_input_sha256"], f"{context}.expected_input_sha256")
            _require(payload["retransform_requested"] is False, "final-definition observer must not request retransformation")
        elif event in {"transform_applied", "transform_rejected", "transform_failure"}:
            _transform_payload(payload, context, event)
            if event == "transform_applied":
                transformed += 1
            else:
                transform_failures += 1
        elif event == "find_started":
            _closed(
                payload,
                frozenset(
                    {
                        "target_class",
                        "defining_loader_class",
                        "defining_loader_identity",
                        "cached_before",
                    }
                ),
                context,
            )
            target = _text(payload["target_class"], f"{context}.target_class")
            _require(target in find_starts, f"{context} names an unexpected target")
            _text(payload["defining_loader_class"], f"{context}.defining_loader_class")
            _text(payload["defining_loader_identity"], f"{context}.defining_loader_identity")
            _boolean(payload["cached_before"], f"{context}.cached_before")
            find_starts[target] += 1
        elif event == "final_definition":
            _closed(
                payload,
                frozenset(
                    {
                        "target_class",
                        "defined_class",
                        "class_identity",
                        "defining_loader_class",
                        "defining_loader_identity",
                        "code_source_uri",
                        "dump_relative_path",
                        "final_bytecode_sha256",
                        "final_bytecode_size",
                        "cached_before",
                    }
                ),
                context,
            )
            target = _text(payload["target_class"], f"{context}.target_class")
            _require(target in find_starts, f"{context} names an unexpected target")
            _require(target not in definitions, f"{context} repeats a target definition")
            _require(payload["defined_class"] == target, f"{context} returned a different class")
            _text(payload["class_identity"], f"{context}.class_identity")
            _text(payload["defining_loader_class"], f"{context}.defining_loader_class")
            _text(payload["defining_loader_identity"], f"{context}.defining_loader_identity")
            _text(payload["code_source_uri"], f"{context}.code_source_uri")
            expected_path = target.replace(".", "/") + ".class"
            _require(payload["dump_relative_path"] == expected_path, f"{context} dump path does not match target")
            _sha256(payload["final_bytecode_sha256"], f"{context}.final_bytecode_sha256")
            _integer(payload["final_bytecode_size"], f"{context}.final_bytecode_size", minimum=1)
            _require(payload["cached_before"] is False, f"{context} was already cached")
            _require(find_starts[target] > 0, f"{context} has no find start")
            definitions[target] = payload
        elif event == "cached_return":
            _closed(
                payload,
                frozenset(
                    {
                        "target_class",
                        "defined_class",
                        "class_identity",
                        "defining_loader_class",
                        "defining_loader_identity",
                    }
                ),
                context,
            )
            target = _text(payload["target_class"], f"{context}.target_class")
            _require(target in find_starts, f"{context} names an unexpected target")
            _require(payload["defined_class"] == target, f"{context} returned a different class")
            for key in ("class_identity", "defining_loader_class", "defining_loader_identity"):
                _text(payload[key], f"{context}.{key}")
            cached_targets.add(target)
        elif event == "definition_failure":
            _closed(
                payload,
                frozenset(
                    {
                        "target_class",
                        "defining_loader_class",
                        "defining_loader_identity",
                        "cached_before",
                        "exception_class",
                        "message",
                        "stack_top",
                    }
                ),
                context,
            )
            _require(payload["target_class"] in find_starts, f"{context} names an unexpected target")
            _text(payload["exception_class"], f"{context}.exception_class")
            definition_failures += 1
        elif event == "observer_failure":
            _closed(payload, frozenset({"operation", "exception_class", "message", "stack_top"}), context)
            _text(payload["operation"], f"{context}.operation")
            _text(payload["exception_class"], f"{context}.exception_class")
            observer_failures += 1

    _require(transformed + transform_failures == 1, "final-definition target transformation count is not one")
    footer = rows[-1]["payload"]
    _closed(
        footer,
        frozenset(
            {
                "health",
                "requested_targets",
                "defined_targets",
                "missing_targets",
                "definition_count",
                "cached_return_target_count",
                "definition_failure_count",
                "observer_failure_count",
                "write_failure",
            }
        ),
        "capture_end payload",
    )
    _require(footer["health"] in {"healthy", "failed"}, "capture_end health is unsupported")
    _require(footer["requested_targets"] == targets, "capture_end requested targets are stale")
    defined = sorted(definitions)
    missing = sorted(set(targets) - set(definitions))
    _require(footer["defined_targets"] == defined, "capture_end defined targets are stale")
    _require(footer["missing_targets"] == missing, "capture_end missing targets are stale")
    _require(_integer(footer["definition_count"], "capture_end.definition_count") == len(definitions), "capture_end definition count is stale")
    _require(_integer(footer["cached_return_target_count"], "capture_end.cached_return_target_count") == len(cached_targets), "capture_end cached target count is stale")
    _require(_integer(footer["definition_failure_count"], "capture_end.definition_failure_count") == definition_failures, "capture_end definition failure count is stale")
    _require(_integer(footer["observer_failure_count"], "capture_end.observer_failure_count") == observer_failures, "capture_end observer failure count is stale")
    write_failure = _boolean(footer["write_failure"], "capture_end.write_failure")
    healthy = (
        transformed == 1
        and transform_failures == 0
        and not missing
        and definition_failures == 0
        and observer_failures == 0
        and write_failure is False
    )
    _require((footer["health"] == "healthy") == healthy, "capture_end health is stale")
    return rows


def _file_record(value: Any, context: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    _closed(value, _FILE_KEYS, context)
    return {
        "label": _text(value["label"], f"{context}.label"),
        "sha256": _sha256(value["sha256"], f"{context}.sha256"),
        "size_bytes": _integer(value["size_bytes"], f"{context}.size_bytes"),
    }


def _session(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "session must be an object")
    _closed(value, _SESSION_KEYS, "session")
    _require(value["side"] in _SIDES, "session.side is unsupported")
    return {
        "capture_id": _text(value["capture_id"], "session.capture_id"),
        "launch_id": _text(value["launch_id"], "session.launch_id"),
        "profile_id": _text(value["profile_id"], "session.profile_id"),
        "side": value["side"],
        "candidate_lock_sha256": _sha256(value["candidate_lock_sha256"], "session.candidate_lock_sha256"),
        "toolchain_lock_sha256": _sha256(value["toolchain_lock_sha256"], "session.toolchain_lock_sha256"),
    }


def _artifacts(value: Any) -> list[dict[str, Any]]:
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)), "artifacts must be an array")
    result = []
    for index, row in enumerate(value):
        context = f"artifacts[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, _ARTIFACT_KEYS, context)
        result.append(
            {
                "artifact_sha256": _sha256(row["artifact_sha256"], f"{context}.artifact_sha256"),
                "size_bytes": _integer(row["size_bytes"], f"{context}.size_bytes"),
                "label": _text(row["label"], f"{context}.label"),
                "code_source_uri": _text(row["code_source_uri"], f"{context}.code_source_uri"),
            }
        )
    result.sort(key=lambda row: (row["artifact_sha256"], row["code_source_uri"]))
    _require(len(result) == len({row["code_source_uri"] for row in result}), "artifacts repeat a code_source_uri")
    return result


def build_final_definition_receipt(
    *,
    session: Mapping[str, Any],
    inputs: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    foundation_manifest: Mapping[str, Any],
    raw_events: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    normalized_session = _session(session)
    _require(isinstance(inputs, Mapping) and set(inputs) == set(_INPUT_NAMES), "inputs are not the exact V1 input set")
    normalized_inputs = {name: _file_record(inputs[name], f"inputs.{name}") for name in _INPUT_NAMES}
    _require(normalized_inputs["candidate_lock"]["sha256"] == normalized_session["candidate_lock_sha256"], "candidate lock input and session disagree")
    _require(normalized_inputs["toolchain_lock"]["sha256"] == normalized_session["toolchain_lock_sha256"], "toolchain lock input and session disagree")
    rows = deepcopy(list(raw_events))
    _require(rows and {row.get("capture_id") for row in rows} == {normalized_session["capture_id"]}, "raw events and session capture identity disagree")
    normalized_artifacts = _artifacts(artifacts)
    by_uri = {row["code_source_uri"]: row["artifact_sha256"] for row in normalized_artifacts}

    _require(isinstance(foundation_manifest, Mapping), "foundation manifest must be an object")
    _closed(
        foundation_manifest,
        frozenset(
            {
                "format",
                "manifest_sha256",
                "class_count",
                "total_size_bytes",
                "foundation_artifact_sha256",
            }
        ),
        "foundation manifest",
    )
    _require(foundation_manifest["format"] == FOUNDATION_MANIFEST_FORMAT, "Foundation manifest format is unsupported")
    foundation = {
        "format": FOUNDATION_MANIFEST_FORMAT,
        "manifest_sha256": _sha256(foundation_manifest["manifest_sha256"], "foundation manifest SHA-256"),
        "class_count": _integer(foundation_manifest["class_count"], "foundation class_count", minimum=1),
        "total_size_bytes": _integer(foundation_manifest["total_size_bytes"], "foundation total_size_bytes", minimum=1),
        "foundation_artifact_sha256": _sha256(foundation_manifest["foundation_artifact_sha256"], "Foundation artifact SHA-256"),
    }
    artifact_hashes = {row["artifact_sha256"] for row in normalized_artifacts}
    _require(foundation["foundation_artifact_sha256"] in artifact_hashes, "Foundation artifact is absent")

    applied = next(row for row in rows if row["event"] == "transform_applied")
    applied_payload = applied["payload"]
    _require(applied_payload["target_code_source_uri"] in by_uri, "instrumented Foundation code source is absent")
    definitions = []
    for row in rows:
        if row["event"] != "final_definition":
            continue
        payload = row["payload"]
        _require(payload["code_source_uri"] in by_uri, f"target {payload['target_class']} code source is absent")
        definitions.append(
            {
                "sequence": row["sequence"],
                "target_class": payload["target_class"],
                "defined_class": payload["defined_class"],
                "class_identity": payload["class_identity"],
                "defining_loader_class": payload["defining_loader_class"],
                "defining_loader_identity": payload["defining_loader_identity"],
                "target_artifact_sha256": by_uri[payload["code_source_uri"]],
                "dump_relative_path": payload["dump_relative_path"],
                "final_bytecode_sha256": payload["final_bytecode_sha256"],
                "final_bytecode_size": payload["final_bytecode_size"],
            }
        )
    definitions.sort(key=lambda row: row["target_class"])
    footer = rows[-1]["payload"]
    failures = []
    for row in rows:
        if row["event"] not in {"transform_rejected", "transform_failure", "definition_failure", "observer_failure"}:
            continue
        payload = row["payload"]
        failures.append(
            {
                "sequence": row["sequence"],
                "event": row["event"],
                "target_class": payload.get("target_class"),
                "reason_code": payload.get("reason") or payload.get("operation") or "definition_failure",
                "exception_class": payload.get("exception_class"),
            }
        )
    state = "complete" if footer["health"] == "healthy" else "failed"
    normalized_limitations = sorted(
        set(MANDATORY_LIMITATIONS)
        | {
            _text(value, f"limitations[{index}]")
            for index, value in enumerate(limitations)
        }
    )
    material = {
        "format": FINAL_DEFINITION_RECEIPT_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": normalized_session,
        "inputs": normalized_inputs,
        "artifacts": normalized_artifacts,
        "observation": {
            "mechanism": "exact_foundation_findclass_return_plus_dump_bytes",
            "agent_id": rows[0]["payload"]["agent_id"],
            "java_version": rows[0]["payload"]["java_version"],
            "target_class": applied_payload["target_class"],
            "expected_input_sha256": next(row for row in rows if row["event"] == "transformer_installed")["payload"]["expected_input_sha256"],
            "observed_input_sha256": applied_payload["input_sha256"],
            "instrumented_output_sha256": applied_payload["output_sha256"],
            "defining_loader_class": applied_payload["defining_loader_class"],
            "target_artifact_sha256": by_uri[applied_payload["target_code_source_uri"]],
        },
        "foundation": foundation,
        "definitions": definitions,
        "failures": failures,
        "health": {
            "state": state,
            "start_health": "starting",
            "end_health": footer["health"],
            "write_failure": footer["write_failure"],
        },
        "summary": {
            "requested_target_count": len(rows[0]["payload"]["targets"]),
            "defined_target_count": len(definitions),
            "cached_return_target_count": footer["cached_return_target_count"],
            "definition_failure_count": footer["definition_failure_count"],
            "failure_count": len(failures),
            "final_bytecode_total_size": sum(row["final_bytecode_size"] for row in definitions),
        },
        "limitations": normalized_limitations,
        "boundaries": {
            "successful_foundation_definition_proved": state == "complete" and bool(definitions),
            "final_class_bytes_proved": state == "complete" and bool(definitions),
            "application_behavior_proved": False,
        },
    }
    return {
        **material,
        "receipt_id": FINAL_DEFINITION_RECEIPT_PREFIX
        + hashlib.sha256(canonical_final_definition_json_bytes(material)).hexdigest(),
    }


def parse_final_definition_receipt(value: Any) -> dict[str, Any]:
    """Validate a closed, content-addressed final-definition receipt."""

    _require(isinstance(value, Mapping), "final-definition receipt must be an object")
    keys = frozenset(
        {
            "format",
            "schema_version",
            "canonicalization_id",
            "receipt_id",
            "session",
            "inputs",
            "artifacts",
            "observation",
            "foundation",
            "definitions",
            "failures",
            "health",
            "summary",
            "limitations",
            "boundaries",
        }
    )
    _closed(value, keys, "final-definition receipt")
    _require(value["format"] == FINAL_DEFINITION_RECEIPT_FORMAT, "final-definition receipt format is not V1")
    _require(value["schema_version"] == 1, "final-definition schema_version is not 1")
    _require(value["canonicalization_id"] == CANONICALIZATION_ID, "final-definition canonicalization is unsupported")
    supplied = deepcopy(dict(value))
    receipt_id = supplied.pop("receipt_id")
    expected_id = FINAL_DEFINITION_RECEIPT_PREFIX + hashlib.sha256(
        canonical_final_definition_json_bytes(supplied)
    ).hexdigest()
    _require(receipt_id == expected_id, "final-definition receipt identity mismatch")

    session = _session(value["session"])
    _require(isinstance(value["inputs"], Mapping) and set(value["inputs"]) == set(_INPUT_NAMES), "inputs are not the exact V1 input set")
    inputs = {name: _file_record(value["inputs"][name], f"inputs.{name}") for name in _INPUT_NAMES}
    _require(inputs["candidate_lock"]["sha256"] == session["candidate_lock_sha256"], "candidate lock input and session disagree")
    _require(inputs["toolchain_lock"]["sha256"] == session["toolchain_lock_sha256"], "toolchain lock input and session disagree")
    artifacts = _artifacts(value["artifacts"])
    artifact_hashes = {row["artifact_sha256"] for row in artifacts}

    observation = value["observation"]
    _closed(
        observation,
        frozenset(
            {
                "mechanism",
                "agent_id",
                "java_version",
                "target_class",
                "expected_input_sha256",
                "observed_input_sha256",
                "instrumented_output_sha256",
                "defining_loader_class",
                "target_artifact_sha256",
            }
        ),
        "observation",
    )
    _require(observation["mechanism"] == "exact_foundation_findclass_return_plus_dump_bytes", "observation mechanism is unsupported")
    for key in ("agent_id", "java_version", "target_class", "defining_loader_class"):
        _text(observation[key], f"observation.{key}")
    _require(_sha256(observation["expected_input_sha256"], "observation.expected_input_sha256") == _sha256(observation["observed_input_sha256"], "observation.observed_input_sha256"), "Foundation exact byte guard was not met")
    _sha256(observation["instrumented_output_sha256"], "observation.instrumented_output_sha256")
    _require(_sha256(observation["target_artifact_sha256"], "observation.target_artifact_sha256") in artifact_hashes, "observation artifact is absent")

    foundation = value["foundation"]
    _closed(foundation, frozenset({"format", "manifest_sha256", "class_count", "total_size_bytes", "foundation_artifact_sha256"}), "foundation")
    _require(foundation["format"] == FOUNDATION_MANIFEST_FORMAT, "Foundation manifest format is unsupported")
    _sha256(foundation["manifest_sha256"], "foundation.manifest_sha256")
    _integer(foundation["class_count"], "foundation.class_count", minimum=1)
    _integer(foundation["total_size_bytes"], "foundation.total_size_bytes", minimum=1)
    _require(_sha256(foundation["foundation_artifact_sha256"], "foundation.foundation_artifact_sha256") in artifact_hashes, "Foundation artifact is absent")

    definitions = value["definitions"]
    _require(isinstance(definitions, list) and definitions, "definitions must be a nonempty array")
    names = []
    for index, row in enumerate(definitions):
        context = f"definitions[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, frozenset({"sequence", "target_class", "defined_class", "class_identity", "defining_loader_class", "defining_loader_identity", "target_artifact_sha256", "dump_relative_path", "final_bytecode_sha256", "final_bytecode_size"}), context)
        _integer(row["sequence"], f"{context}.sequence")
        target = _text(row["target_class"], f"{context}.target_class")
        names.append(target)
        _require(row["defined_class"] == target, f"{context} defined class disagrees")
        for key in ("class_identity", "defining_loader_class", "defining_loader_identity"):
            _text(row[key], f"{context}.{key}")
        _require(_sha256(row["target_artifact_sha256"], f"{context}.target_artifact_sha256") in artifact_hashes, f"{context} target artifact is absent")
        _require(row["dump_relative_path"] == target.replace(".", "/") + ".class", f"{context} dump path disagrees")
        _sha256(row["final_bytecode_sha256"], f"{context}.final_bytecode_sha256")
        _integer(row["final_bytecode_size"], f"{context}.final_bytecode_size", minimum=1)
    _require(names == sorted(set(names)), "definitions must be target-sorted and unique")

    failures = value["failures"]
    _require(isinstance(failures, list), "failures must be an array")
    for index, row in enumerate(failures):
        context = f"failures[{index}]"
        _closed(row, frozenset({"sequence", "event", "target_class", "reason_code", "exception_class"}), context)
        _integer(row["sequence"], f"{context}.sequence")
        _require(row["event"] in {"transform_rejected", "transform_failure", "definition_failure", "observer_failure"}, f"{context}.event is unsupported")
        _text(row["target_class"], f"{context}.target_class", nullable=True)
        _text(row["reason_code"], f"{context}.reason_code")
        _text(row["exception_class"], f"{context}.exception_class", nullable=True)

    health = value["health"]
    _closed(health, frozenset({"state", "start_health", "end_health", "write_failure"}), "health")
    _require(health["state"] in {"complete", "failed"}, "health.state is unsupported")
    _require(health["start_health"] == "starting", "health.start_health is unsupported")
    _require(health["end_health"] in {"healthy", "failed"}, "health.end_health is unsupported")
    _boolean(health["write_failure"], "health.write_failure")
    complete = health["end_health"] == "healthy" and not health["write_failure"] and not failures
    _require((health["state"] == "complete") == complete, "final-definition health fields disagree")

    summary = value["summary"]
    _closed(summary, frozenset({"requested_target_count", "defined_target_count", "cached_return_target_count", "definition_failure_count", "failure_count", "final_bytecode_total_size"}), "summary")
    for key in summary:
        _integer(summary[key], f"summary.{key}")
    _require(summary["defined_target_count"] == len(definitions), "defined target summary is stale")
    _require(summary["requested_target_count"] >= len(definitions), "requested target summary is invalid")
    _require(summary["failure_count"] == len(failures), "failure summary is stale")
    _require(summary["definition_failure_count"] == sum(row["event"] == "definition_failure" for row in failures), "definition failure summary is stale")
    _require(summary["final_bytecode_total_size"] == sum(row["final_bytecode_size"] for row in definitions), "final byte size summary is stale")
    if complete:
        _require(summary["requested_target_count"] == len(definitions), "complete receipt omits a requested target")

    limitations = _string_list(value["limitations"], "limitations", sorted_unique=True)
    _require(set(MANDATORY_LIMITATIONS) <= set(limitations), "final-definition receipt omits a mandatory limitation")
    boundaries = value["boundaries"]
    _closed(boundaries, frozenset({"successful_foundation_definition_proved", "final_class_bytes_proved", "application_behavior_proved"}), "boundaries")
    _require(boundaries == {"successful_foundation_definition_proved": complete, "final_class_bytes_proved": complete, "application_behavior_proved": False}, "final-definition boundaries overclaim or are stale")
    return deepcopy(dict(value))


def render_final_definition_receipt(value: Mapping[str, Any]) -> bytes:
    return canonical_final_definition_json_bytes(parse_final_definition_receipt(value)) + b"\n"


def write_final_definition_receipt(path: Path, value: Mapping[str, Any]) -> None:
    encoded = render_final_definition_receipt(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
