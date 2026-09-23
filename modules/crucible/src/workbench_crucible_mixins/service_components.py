"""Selected Mixin service-component capture and receipt V1."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


RAW_SERVICE_COMPONENTS_FORMAT = "workbench-cleanmix-selected-service-components-raw-v1"
SERVICE_COMPONENTS_RECEIPT_FORMAT = (
    "workbench-crucible-mixin-selected-service-components-receipt-v1"
)
SERVICE_COMPONENTS_RECEIPT_PREFIX = (
    "crucible-mixin-selected-service-components:sha256:"
)
CANONICALIZATION_ID = "workbench-canonical-json-v1"

REQUIRED_COMPONENT_ROLES = (
    "audit_trail",
    "bytecode_provider",
    "class_provider",
    "class_tracker",
    "logger",
    "service",
    "service_classloader",
    "transformer_provider",
)

_ROLE_SET = frozenset(REQUIRED_COMPONENT_ROLES)
_SHA256_CHARACTERS = frozenset("0123456789abcdef")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_RAW_EVENTS = frozenset(
    {
        "capture_start",
        "transformer_installed",
        "transform_applied",
        "transform_rejected",
        "transform_failure",
        "component_observed",
        "component_failure",
        "observer_failure",
        "capture_end",
    }
)
_RAW_KEYS = frozenset({"format", "capture_id", "sequence", "event", "payload"})
_FILE_KEYS = frozenset({"label", "sha256", "size_bytes"})
_INPUT_NAMES = (
    "agent_artifact",
    "candidate_lock",
    "fixture_result",
    "launch_log",
    "raw_trace",
    "toolchain_lock",
)
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
_COMPONENT_KEYS = frozenset(
    {
        "role",
        "implementation_class",
        "object_identity",
        "implementation_loader_class",
        "implementation_loader_identity",
        "code_source_uri",
        "artifact_sha256",
        "reported_name",
        "sequence",
    }
)
_FAILURE_KEYS = frozenset(
    {"role", "operation", "exception_class", "message", "sequence"}
)

MANDATORY_LIMITATIONS = (
    "The receipt proves selected service-component objects only for the bound launch and observation point.",
    "Component implementation and defining-loader identity do not prove configuration admission, transformer order, or transformation completion.",
    "Artifact hashes bind component code sources; they do not prove every class or resource loaded from those artifacts.",
    "The service classloader row identifies the selected service object's defining loader, not every loader participating in the game runtime.",
)


class ServiceComponentsValidationError(ValueError):
    """Selected service-component evidence is malformed or overclaims."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ServiceComponentsValidationError(message)


def canonical_service_components_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ServiceComponentsValidationError(
            f"cannot canonically encode selected service components: {exc}"
        ) from exc


def _closed(value: Mapping[str, Any], expected: frozenset[str], context: str) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
    _require(not missing, f"{context} lacks fields: {sorted(missing)}")
    _require(not unknown, f"{context} has unknown fields: {sorted(unknown)}")


def _text(value: Any, context: str, *, maximum: int = 8192) -> str:
    _require(
        isinstance(value, str)
        and bool(value)
        and len(value) <= maximum
        and not any(character in value for character in "\r\n\x00"),
        f"{context} must be bounded nonempty single-line text",
    )
    return value


def _nullable_text(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _text(value, context)


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_CHARACTERS,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def parse_raw_service_components(encoded: bytes) -> list[dict[str, Any]]:
    """Parse the exact profile-owned component event stream."""

    _require(isinstance(encoded, bytes) and bool(encoded), "raw component trace is empty")
    rows: list[dict[str, Any]] = []
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise ServiceComponentsValidationError(
            f"raw component trace is not UTF-8: {exc}"
        ) from exc
    _require(text.endswith("\n"), "raw component trace lacks a terminal newline")
    for index, line in enumerate(text.splitlines()):
        _require(bool(line), f"raw component trace line {index + 1} is empty")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_json_object,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite number {item}")
                ),
            )
        except (json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
            raise ServiceComponentsValidationError(
                f"raw component trace line {index + 1} is malformed: {exc}"
            ) from exc
        _require(isinstance(value, Mapping), f"raw component trace line {index + 1} is not an object")
        _closed(value, _RAW_KEYS, f"raw component trace line {index + 1}")
        _require(value["format"] == RAW_SERVICE_COMPONENTS_FORMAT, "raw component format is not V1")
        _text(value["capture_id"], f"raw component trace line {index + 1}.capture_id")
        _require(value["sequence"] == index, "raw component sequences are not contiguous from zero")
        _require(value["event"] in _RAW_EVENTS, "raw component event is unsupported")
        _require(isinstance(value["payload"], Mapping), "raw component payload must be an object")
        rows.append(deepcopy(dict(value)))

    _require(len(rows) >= 3, "raw component trace is too short")
    capture_ids = {row["capture_id"] for row in rows}
    _require(len(capture_ids) == 1, "raw component trace crosses capture identities")
    _require(rows[0]["event"] == "capture_start", "raw component trace does not start with capture_start")
    _require(rows[-1]["event"] == "capture_end", "raw component trace does not end with capture_end")
    _require(
        sum(row["event"] == "capture_start" for row in rows) == 1
        and sum(row["event"] == "capture_end" for row in rows) == 1,
        "raw component trace repeats a terminal event",
    )

    observed_roles: list[str] = []
    failed_roles: list[str] = []
    transformer_installed = 0
    transform_applied = 0
    transform_failed = False
    observer_failed = False
    for row in rows:
        event = row["event"]
        payload = row["payload"]
        if event == "capture_start":
            _closed(
                payload,
                frozenset(
                    {
                        "agent_id",
                        "java_version",
                        "target_class",
                        "expected_input_sha256",
                    }
                ),
                "capture_start payload",
            )
            for key in ("agent_id", "java_version", "target_class"):
                _text(payload[key], f"capture_start.{key}")
            _sha256(
                payload["expected_input_sha256"],
                "capture_start.expected_input_sha256",
            )
        elif event == "transformer_installed":
            transformer_installed += 1
            _closed(
                payload,
                frozenset(
                    {
                        "target_class",
                        "expected_input_sha256",
                        "retransform_requested",
                    }
                ),
                "transformer_installed payload",
            )
            _text(payload["target_class"], "transformer_installed.target_class")
            _sha256(
                payload["expected_input_sha256"],
                "transformer_installed.expected_input_sha256",
            )
            _require(
                type(payload["retransform_requested"]) is bool,
                "transformer_installed.retransform_requested must be boolean",
            )
        elif event in {"transform_applied", "transform_rejected", "transform_failure"}:
            expected = {
                "defining_loader_class",
                "defining_loader_identity",
                "target_code_source_uri",
                "input_sha256",
                "output_sha256",
                "reason",
            }
            if event == "transform_failure":
                expected |= {"exception_class", "message"}
            _closed(payload, frozenset(expected), f"{event} payload")
            for key in ("defining_loader_class", "defining_loader_identity"):
                _text(payload[key], f"{event}.{key}")
            _nullable_text(payload["target_code_source_uri"], f"{event}.target_code_source_uri")
            _sha256(payload["input_sha256"], f"{event}.input_sha256")
            if event == "transform_applied":
                transform_applied += 1
                _sha256(payload["output_sha256"], "transform_applied.output_sha256")
                _require(payload["reason"] is None, "transform_applied.reason must be null")
            else:
                transform_failed = True
                _require(payload["output_sha256"] is None, f"{event}.output_sha256 must be null")
                _text(payload["reason"], f"{event}.reason")
                if event == "transform_failure":
                    _text(payload["exception_class"], "transform_failure.exception_class")
                    _nullable_text(payload["message"], "transform_failure.message")
        elif event == "component_observed":
            role = payload.get("role")
            _require(role in _ROLE_SET, "component_observed role is unsupported")
            expected = frozenset(
                {
                    "role",
                    "implementation_class",
                    "object_identity",
                    "implementation_loader_class",
                    "implementation_loader_identity",
                    "code_source_uri",
                    "reported_name",
                }
            )
            _closed(payload, expected, f"component_observed {role}")
            for key in (
                "implementation_class",
                "object_identity",
                "implementation_loader_class",
                "implementation_loader_identity",
                "code_source_uri",
            ):
                _text(payload[key], f"component_observed {role}.{key}")
            _nullable_text(payload["reported_name"], f"component_observed {role}.reported_name")
            observed_roles.append(role)
        elif event == "component_failure":
            expected = frozenset({"role", "operation", "exception_class", "message"})
            _closed(payload, expected, "component_failure")
            role = payload.get("role")
            _require(role in _ROLE_SET, "component_failure role is unsupported")
            _text(payload["operation"], f"component_failure {role}.operation")
            _text(payload["exception_class"], f"component_failure {role}.exception_class")
            _nullable_text(payload["message"], f"component_failure {role}.message")
            failed_roles.append(role)
        elif event == "observer_failure":
            observer_failed = True
            _closed(
                payload,
                frozenset({"operation", "exception_class", "message"}),
                "observer_failure payload",
            )
            _text(payload["operation"], "observer_failure.operation")
            _text(payload["exception_class"], "observer_failure.exception_class")
            _nullable_text(payload["message"], "observer_failure.message")
    _require(len(observed_roles) == len(set(observed_roles)), "raw component trace repeats an observed role")
    _require(not (set(observed_roles) & set(failed_roles)), "raw component role is both observed and failed")
    start_payload = rows[0]["payload"]
    installed_rows = [row for row in rows if row["event"] == "transformer_installed"]
    _require(transformer_installed <= 1, "raw component trace repeats transformer_installed")
    if installed_rows:
        installed = installed_rows[0]["payload"]
        _require(
            installed["target_class"] == start_payload["target_class"]
            and installed["expected_input_sha256"]
            == start_payload["expected_input_sha256"],
            "transformer installation and capture start disagree",
        )
    applied_rows = [row for row in rows if row["event"] == "transform_applied"]
    for applied in applied_rows:
        _require(
            applied["payload"]["input_sha256"]
            == start_payload["expected_input_sha256"],
            "applied transform input does not match the exact byte guard",
        )

    footer = rows[-1]["payload"]
    _closed(
        footer,
        frozenset(
            {
                "health",
                "transform_applied_count",
                "observed_roles",
                "failed_roles",
                "write_failure",
            }
        ),
        "capture_end payload",
    )
    _require(footer["health"] in {"healthy", "failed"}, "capture_end health is unsupported")
    _require(footer["transform_applied_count"] == transform_applied, "capture_end transform count is stale")
    _require(footer["observed_roles"] == sorted(observed_roles), "capture_end observed roles are stale")
    _require(footer["failed_roles"] == sorted(failed_roles), "capture_end failed roles are stale")
    _require(type(footer["write_failure"]) is bool, "capture_end write_failure must be boolean")
    if footer["health"] == "healthy":
        _require(
            transformer_installed == 1,
            "healthy component trace lacks one installed transformer",
        )
        _require(transform_applied == 1 and not transform_failed, "healthy component trace lacks one exact transform")
        _require(set(observed_roles) == _ROLE_SET, "healthy component trace lacks a required role")
        _require(not failed_roles and not observer_failed and not footer["write_failure"], "healthy component trace reports failure")
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
        "candidate_lock_sha256": _sha256(
            value["candidate_lock_sha256"], "session.candidate_lock_sha256"
        ),
        "toolchain_lock_sha256": _sha256(
            value["toolchain_lock_sha256"], "session.toolchain_lock_sha256"
        ),
    }


def _artifacts(value: Any) -> list[dict[str, Any]]:
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)), "artifacts must be an array")
    result: list[dict[str, Any]] = []
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
    _require(
        len(result) == len({row["code_source_uri"] for row in result}),
        "artifacts repeat a code_source_uri",
    )
    return result


def _project_components(
    rows: Sequence[Mapping[str, Any]], artifacts: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_uri = {row["code_source_uri"]: row for row in artifacts}
    components: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    observation_sequence = 0
    for row in rows:
        if row["event"] == "component_observed":
            observation_sequence += 1
            payload = row["payload"]
            artifact = by_uri.get(payload["code_source_uri"])
            _require(
                artifact is not None,
                f"component {payload['role']} code source is absent from artifacts",
            )
            components.append(
                {
                    "role": payload["role"],
                    "implementation_class": payload["implementation_class"],
                    "object_identity": payload["object_identity"],
                    "implementation_loader_class": payload["implementation_loader_class"],
                    "implementation_loader_identity": payload["implementation_loader_identity"],
                    "code_source_uri": payload["code_source_uri"],
                    "artifact_sha256": artifact["artifact_sha256"],
                    "reported_name": payload["reported_name"],
                    "sequence": observation_sequence,
                }
            )
        elif row["event"] == "component_failure":
            observation_sequence += 1
            payload = row["payload"]
            failures.append(
                {
                    "role": payload["role"],
                    "operation": payload["operation"],
                    "exception_class": payload["exception_class"],
                    "message": payload["message"],
                    "sequence": observation_sequence,
                }
            )
    components.sort(key=lambda row: row["role"])
    failures.sort(key=lambda row: (row["role"], row["sequence"]))
    return components, failures


def build_service_components_receipt(
    *,
    session: Mapping[str, Any],
    inputs: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    raw_events: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    normalized_session = _session(session)
    _require(isinstance(inputs, Mapping) and set(inputs) == set(_INPUT_NAMES), "inputs are not the exact V1 input set")
    normalized_inputs = {name: _file_record(inputs[name], f"inputs.{name}") for name in _INPUT_NAMES}
    _require(
        normalized_inputs["candidate_lock"]["sha256"]
        == normalized_session["candidate_lock_sha256"],
        "candidate lock input and session disagree",
    )
    _require(
        normalized_inputs["toolchain_lock"]["sha256"]
        == normalized_session["toolchain_lock_sha256"],
        "toolchain lock input and session disagree",
    )
    rows = deepcopy(list(raw_events))
    _require(bool(rows), "raw_events must be nonempty")
    _require(
        {row.get("capture_id") for row in rows if isinstance(row, Mapping)}
        == {normalized_session["capture_id"]},
        "raw events and session capture identity disagree",
    )
    normalized_artifacts = _artifacts(artifacts)
    components, failures = _project_components(rows, normalized_artifacts)
    footer = rows[-1]["payload"]
    start = rows[0]["payload"]
    transforms = [row["payload"] for row in rows if row["event"] == "transform_applied"]
    _require(
        len(transforms) == 1,
        "service-component receipt requires one applied exact substitution",
    )
    transform = transforms[0]
    artifact_by_uri = {
        row["code_source_uri"]: row["artifact_sha256"]
        for row in normalized_artifacts
    }
    target_artifact_sha256 = artifact_by_uri.get(transform["target_code_source_uri"])
    _require(
        target_artifact_sha256 is not None,
        "transformed MixinService code source is absent from artifacts",
    )
    roles = [row["role"] for row in components]
    state = "complete" if footer["health"] == "healthy" else "failed"
    normalized_limitations = sorted(
        set(MANDATORY_LIMITATIONS)
        | {
            _text(value, f"limitations[{index}]")
            for index, value in enumerate(limitations)
        }
    )
    material = {
        "format": SERVICE_COMPONENTS_RECEIPT_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": normalized_session,
        "inputs": normalized_inputs,
        "artifacts": normalized_artifacts,
        "observation": {
            "mechanism": "java_instrument_exact_mixin_service_substitution",
            "agent_id": _text(start["agent_id"], "capture_start.agent_id"),
            "java_version": _text(start["java_version"], "capture_start.java_version"),
            "target_class": _text(start["target_class"], "capture_start.target_class"),
            "expected_input_sha256": _sha256(
                start["expected_input_sha256"], "capture_start.expected_input_sha256"
            ),
            "observed_input_sha256": _sha256(
                transform["input_sha256"], "transform_applied.input_sha256"
            ),
            "instrumented_output_sha256": _sha256(
                transform["output_sha256"], "transform_applied.output_sha256"
            ),
            "defining_loader_class": _text(
                transform["defining_loader_class"],
                "transform_applied.defining_loader_class",
            ),
            "target_code_source_uri": _text(
                transform["target_code_source_uri"],
                "transform_applied.target_code_source_uri",
            ),
            "target_artifact_sha256": target_artifact_sha256,
        },
        "components": components,
        "failures": failures,
        "health": {
            "state": state,
            "start_health": "starting",
            "end_health": footer["health"],
            "write_failure": footer["write_failure"],
        },
        "summary": {
            "component_count": len(components),
            "observed_roles": roles,
            "missing_roles": sorted(_ROLE_SET - set(roles)),
            "failure_count": len(failures),
            "all_required_components_observed": set(roles) == _ROLE_SET,
        },
        "limitations": normalized_limitations,
        "boundaries": {
            "selected_service_components_proved": state == "complete",
            "configuration_admission_proved": False,
            "transformer_order_proved": False,
            "mixin_transformation_completion_proved": False,
            "final_class_bytes_proved": False,
        },
    }
    return {
        **material,
        "receipt_id": SERVICE_COMPONENTS_RECEIPT_PREFIX
        + hashlib.sha256(canonical_service_components_json_bytes(material)).hexdigest(),
    }


def parse_service_components_receipt(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "service components receipt must be an object")
    expected = frozenset(
        {
            "format",
            "schema_version",
            "canonicalization_id",
            "receipt_id",
            "session",
            "inputs",
            "artifacts",
            "observation",
            "components",
            "failures",
            "health",
            "summary",
            "limitations",
            "boundaries",
        }
    )
    _closed(value, expected, "service components receipt")
    _require(value["format"] == SERVICE_COMPONENTS_RECEIPT_FORMAT, "service components receipt format is not V1")
    _require(value["schema_version"] == 1, "service components receipt schema_version is not 1")
    _require(value["canonicalization_id"] == CANONICALIZATION_ID, "service components canonicalization is unsupported")
    supplied = deepcopy(dict(value))
    receipt_id = supplied.pop("receipt_id")
    expected_id = SERVICE_COMPONENTS_RECEIPT_PREFIX + hashlib.sha256(
        canonical_service_components_json_bytes(supplied)
    ).hexdigest()
    _require(receipt_id == expected_id, "service components receipt identity mismatch")
    rebuilt = build_service_components_receipt(
        session=value["session"],
        inputs=value["inputs"],
        artifacts=value["artifacts"],
        raw_events=_raw_events_from_receipt(value),
        limitations=value["limitations"],
    )
    _require(value == rebuilt, "service components receipt is not canonical V1 material")
    return rebuilt


def _raw_events_from_receipt(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct the normalized event projection used by the receipt identity.

    The raw bytes stay in Crucible custody. This reconstruction exists only so
    the semantic parser can recompute all derived receipt fields without them.
    """

    capture_id = value["session"]["capture_id"]
    observation = value["observation"]
    events: list[dict[str, Any]] = [
        {
            "format": RAW_SERVICE_COMPONENTS_FORMAT,
            "capture_id": capture_id,
            "sequence": 0,
            "event": "capture_start",
            "payload": {
                "agent_id": observation["agent_id"],
                "java_version": observation["java_version"],
                "target_class": observation["target_class"],
                "expected_input_sha256": observation["expected_input_sha256"],
            },
        }
    ]
    events.append(
        {
            "format": RAW_SERVICE_COMPONENTS_FORMAT,
            "capture_id": capture_id,
            "sequence": 1,
            "event": "transformer_installed",
            "payload": {
                "target_class": observation["target_class"],
                "expected_input_sha256": observation["expected_input_sha256"],
                "retransform_requested": False,
            },
        }
    )
    events.append(
        {
            "format": RAW_SERVICE_COMPONENTS_FORMAT,
            "capture_id": capture_id,
            "sequence": 2,
            "event": "transform_applied",
            "payload": {
                "defining_loader_class": observation["defining_loader_class"],
                "defining_loader_identity": "receipt-local-not-retained",
                "target_code_source_uri": observation["target_code_source_uri"],
                "input_sha256": observation["observed_input_sha256"],
                "output_sha256": observation["instrumented_output_sha256"],
                "reason": None,
            },
        }
    )
    rows = sorted(
        [
            (row["sequence"], "component_observed", row)
            for row in value["components"]
        ]
        + [
            (row["sequence"], "component_failure", row)
            for row in value["failures"]
        ]
    )
    for _, event, row in rows:
        if event == "component_observed":
            payload = {
                key: row[key]
                for key in (
                    "role",
                    "implementation_class",
                    "object_identity",
                    "implementation_loader_class",
                    "implementation_loader_identity",
                    "code_source_uri",
                    "reported_name",
                )
            }
        else:
            payload = {
                key: row[key]
                for key in ("role", "operation", "exception_class", "message")
            }
        events.append(
            {
                "format": RAW_SERVICE_COMPONENTS_FORMAT,
                "capture_id": capture_id,
                "sequence": len(events),
                "event": event,
                "payload": payload,
            }
        )
    events.append(
        {
            "format": RAW_SERVICE_COMPONENTS_FORMAT,
            "capture_id": capture_id,
            "sequence": len(events),
            "event": "capture_end",
            "payload": {
                "health": value["health"]["end_health"],
                "transform_applied_count": 1,
                "observed_roles": value["summary"]["observed_roles"],
                "failed_roles": sorted({row["role"] for row in value["failures"]}),
                "write_failure": value["health"]["write_failure"],
            },
        }
    )
    return events


def render_service_components_receipt(value: Mapping[str, Any]) -> bytes:
    admitted = parse_service_components_receipt(value)
    return json.dumps(
        admitted,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def write_service_components_receipt(path: Path, value: Mapping[str, Any]) -> None:
    admitted = parse_service_components_receipt(value)
    if path.is_symlink():
        raise ServiceComponentsValidationError(
            f"service components destination cannot be a symlink: {path}"
        )
    destination = path.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = render_service_components_receipt(admitted)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".mixin-service-components-",
        suffix=".json.tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
