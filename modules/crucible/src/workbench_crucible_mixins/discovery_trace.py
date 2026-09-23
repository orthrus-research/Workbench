"""Fail-closed receipt for one defining-loader Mixin discovery trace.

This V2 surface is deliberately separate from the V1 thread-context provider
enumeration.  Its raw input comes from observation calls placed around the
runtime's own iterator and provider calls.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from urllib.request import url2pathname
import zipfile


RAW_TRACE_FORMAT = "workbench-cleanmix-defining-loader-discovery-trace-raw-v2"
DEFINING_LOADER_TRACE_RECEIPT_FORMAT = (
    "workbench-crucible-mixin-defining-loader-discovery-trace-receipt-v2"
)
DEFINING_LOADER_TRACE_RECEIPT_PREFIX = (
    "crucible-mixin-defining-loader-discovery-trace:sha256:"
)
CANONICALIZATION_ID = "workbench-canonical-json-v1"
MIXIN_SERVICE_CLASS = "org.spongepowered.asm.service.MixinService"
MIXIN_SERVICE_ENTRY = "org/spongepowered/asm/service/MixinService.class"
BOOTSTRAP_INTERFACE = "org.spongepowered.asm.service.IMixinServiceBootstrap"
SERVICE_INTERFACE = "org.spongepowered.asm.service.IMixinService"
AGENT_PAYLOAD_ENTRY = (
    "workbench/cleanmix-discovery-trace/instrumented/"
    "org/spongepowered/asm/service/MixinService.class"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_STAGES = frozenset({"bootstrap", "service"})
_EVENTS = frozenset(
    {
        "capture_start",
        "transformer_installed",
        "transform_applied",
        "transform_rejected",
        "transform_failure",
        "stage_start",
        "attempt_start",
        "provider_constructed",
        "bootstrap_returned",
        "bootstrap_service_class_name",
        "service_name_returned",
        "validity_returned",
        "provider_selected",
        "attempt_end",
        "attempt_failure",
        "stage_failure",
        "stage_end",
        "observer_failure",
        "capture_end",
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {"format", "capture_id", "sequence", "event", "stage", "attempt", "payload"}
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
_OBSERVATION_KEYS = frozenset(
    {
        "mechanism",
        "target_class",
        "target_input_sha256",
        "target_output_sha256",
        "target_code_source_uri",
        "defining_loader_class",
        "defining_loader_identity",
        "defining_loader_parent_class",
        "defining_loader_parent_identity",
        "java_version",
    }
)
_BYPASS_KEYS = frozenset(
    {"stage", "property_name", "property_value", "mechanism"}
)
_ATTEMPT_KEYS = frozenset(
    {
        "attempt_id",
        "stage",
        "mechanism",
        "start_sequence",
        "terminal_sequence",
        "terminal_outcome",
        "events",
    }
)
_ATTEMPT_EVENT_KEYS = frozenset({"sequence", "event", "payload"})
_SELECTION_KEYS = frozenset(
    {
        "sequence",
        "attempt_id",
        "provider_class",
        "provider_loader_class",
        "provider_loader_identity",
        "provider_code_source_uri",
        "provider_artifact_sha256",
        "provider_artifact_size_bytes",
    }
)
_FAILURE_KEYS = frozenset({"sequence", "event", "stage", "attempt", "payload"})
_HEALTH_KEYS = frozenset(
    {
        "start_health",
        "end_health",
        "transform_applied_count",
        "bootstrap_started",
        "bootstrap_outcome",
        "service_started",
        "service_outcome",
        "selected_count",
        "stage_failure",
        "write_failure",
    }
)
_SUMMARY_KEYS = frozenset(
    {
        "raw_event_count",
        "bootstrap_attempt_count",
        "service_attempt_count",
        "failed_attempt_count",
        "failure_event_count",
    }
)
_BOUNDARY_KEYS = frozenset(
    {
        "cleanmix_defining_loader_path_observed",
        "thread_context_enumeration_performed",
        "provider_or_descriptor_injected",
        "bypass_property_modified",
        "selected_service_proved_for_this_launch",
        "mixin_transformation_completion_proved",
        "final_class_bytes_proved",
    }
)
_INPUT_NAMES = (
    "raw_trace",
    "candidate_lock",
    "toolchain_lock",
    "observer_agent",
    "mixin_engine_artifact",
    "selected_provider_artifact",
    "launch_log",
    "fixture_result",
)


class DefiningLoaderTraceValidationError(ValueError):
    """Raised when raw or normalized V2 discovery evidence is not exact."""


class _DuplicateKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DefiningLoaderTraceValidationError(message)


def canonical_discovery_trace_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DefiningLoaderTraceValidationError(
            f"cannot canonically encode discovery trace: {exc}"
        ) from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_number(token: str) -> None:
    raise ValueError(f"unsupported JSON number {token}")


def _decode_line(encoded: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        raise DefiningLoaderTraceValidationError(f"cannot parse {context}: {exc}") from exc
    _require(isinstance(value, dict), f"{context} must be an object")
    return value


def _closed(value: Mapping[str, Any], keys: frozenset[str], context: str) -> None:
    actual = set(value)
    _require(
        actual == keys,
        f"{context} fields mismatch: missing={sorted(keys - actual)!r}, "
        f"unknown={sorted(actual - keys)!r}",
    )


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    _require("\x00" not in value, f"{context} cannot contain NUL")
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
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _payload_keys(
    payload: Mapping[str, Any], required: set[str], context: str
) -> None:
    _closed(payload, frozenset(required), context)


def _validate_payload(row: Mapping[str, Any], context: str) -> None:
    event = row["event"]
    payload = row["payload"]
    _require(isinstance(payload, Mapping), f"{context}.payload must be an object")
    provider = {
        "provider_class",
        "provider_loader_class",
        "provider_loader_identity",
        "provider_code_source_uri",
    }
    failure = {"exception_class", "message", "stack_top", "operation"}
    expected: dict[str, set[str]] = {
        "capture_start": {
            "agent_id",
            "health",
            "java_version",
            "redefine_supported",
            "retransform_supported",
        },
        "transformer_installed": {
            "target_class",
            "expected_input_sha256",
            "retransform_requested",
        },
        "transform_applied": {
            "target_class",
            "defining_loader_class",
            "defining_loader_identity",
            "defining_loader_parent_class",
            "defining_loader_parent_identity",
            "target_code_source_uri",
            "input_sha256",
            "output_sha256",
            "reason",
        },
        "transform_rejected": {
            "target_class",
            "defining_loader_class",
            "defining_loader_identity",
            "defining_loader_parent_class",
            "defining_loader_parent_identity",
            "target_code_source_uri",
            "input_sha256",
            "output_sha256",
            "reason",
        },
        "transform_failure": {
            "target_class",
            "defining_loader_class",
            "defining_loader_identity",
            "defining_loader_parent_class",
            "defining_loader_parent_identity",
            "target_code_source_uri",
            "input_sha256",
            "output_sha256",
            "reason",
            "exception_class",
            "message",
            "stack_top",
        },
        "stage_start": {
            "defining_loader_class",
            "defining_loader_identity",
            "defining_loader_parent_class",
            "defining_loader_parent_identity",
            "bypass_property_name",
            "bypass_property_value",
            "mechanism",
        },
        "attempt_start": {"mechanism"},
        "provider_constructed": provider,
        "bootstrap_returned": provider,
        "bootstrap_service_class_name": provider | {"service_class_name"},
        "service_name_returned": provider | {"service_name"},
        "validity_returned": provider | {"valid"},
        "provider_selected": provider,
        "attempt_end": {"outcome"},
        "attempt_failure": failure,
        "stage_failure": failure,
        "observer_failure": failure,
        "stage_end": {"outcome"},
        "capture_end": {
            "health",
            "transform_applied_count",
            "bootstrap_started",
            "bootstrap_outcome",
            "service_started",
            "service_outcome",
            "selected_count",
            "stage_failure",
            "write_failure",
        },
    }
    _payload_keys(payload, expected[event], f"{context}.payload")

    for key in provider:
        if key in payload:
            _nullable_text(payload[key], f"{context}.payload.{key}")
    for key in ("target_class", "defining_loader_class", "defining_loader_identity"):
        if key in payload:
            _text(payload[key], f"{context}.payload.{key}")
    for key in ("input_sha256", "output_sha256", "expected_input_sha256"):
        if key in payload and payload[key] is not None:
            _sha256(payload[key], f"{context}.payload.{key}")
    if event == "stage_start":
        _require(payload["mechanism"] in {"service_loader", "system_property"},
                 f"{context} has unsupported mechanism")
        _text(payload["bypass_property_name"], f"{context}.payload.bypass_property_name")
        _nullable_text(payload["bypass_property_value"], f"{context}.payload.bypass_property_value")
        _require(
            (payload["bypass_property_value"] is None) == (payload["mechanism"] == "service_loader"),
            f"{context} bypass property and mechanism disagree",
        )
    if event == "attempt_start":
        _require(payload["mechanism"] in {"service_loader", "system_property"},
                 f"{context} has unsupported attempt mechanism")
    if event == "validity_returned":
        _require(isinstance(payload["valid"], bool), f"{context}.payload.valid must be boolean")


def parse_raw_discovery_trace(encoded: bytes) -> list[dict[str, Any]]:
    """Parse strict, complete LF-delimited raw V2 events."""

    _require(bool(encoded), "raw discovery trace is empty")
    _require(encoded.endswith(b"\n"), "raw discovery trace lacks terminal LF")
    _require(b"\r" not in encoded, "raw discovery trace contains CR bytes")
    lines = encoded[:-1].split(b"\n")
    _require(all(lines), "raw discovery trace contains a blank line")
    rows: list[dict[str, Any]] = []
    capture_id: str | None = None
    for index, line in enumerate(lines):
        context = f"raw line {index + 1}"
        row = _decode_line(line, context)
        _closed(row, _TOP_LEVEL_KEYS, context)
        _require(row["format"] == RAW_TRACE_FORMAT, f"{context} has wrong format")
        current_capture = _text(row["capture_id"], f"{context}.capture_id")
        if capture_id is None:
            capture_id = current_capture
        _require(current_capture == capture_id, f"{context} changes capture_id")
        _require(row["sequence"] == index, f"{context} sequence is not contiguous")
        event = row["event"]
        _require(event in _EVENTS, f"{context} has unsupported event {event!r}")
        stage = row["stage"]
        _require(stage is None or stage in _STAGES, f"{context} has unsupported stage")
        attempt = row["attempt"]
        if attempt is not None:
            _integer(attempt, f"{context}.attempt", minimum=1)
        _validate_payload(row, context)
        rows.append(row)

    _require(rows[0]["event"] == "capture_start", "first raw event is not capture_start")
    _require(rows[-1]["event"] == "capture_end", "last raw event is not capture_end")
    counts = Counter(row["event"] for row in rows)
    _require(counts["capture_start"] == 1, "raw trace does not have one capture_start")
    _require(counts["capture_end"] == 1, "raw trace does not have one capture_end")
    _require(counts["transformer_installed"] == 1, "raw trace lacks one transformer installation")
    _require(
        counts["transform_applied"] + counts["transform_rejected"] + counts["transform_failure"] == 1,
        "raw trace does not have one transform outcome",
    )

    active_stage: str | None = None
    open_attempts: dict[int, str] = {}
    seen_attempts: set[int] = set()
    for row in rows:
        event, stage, attempt = row["event"], row["stage"], row["attempt"]
        if event == "stage_start":
            _require(active_stage is None, "raw trace nests discovery stages")
            _require(stage in _STAGES and attempt is None, "stage_start has wrong scope")
            active_stage = stage
        elif event == "stage_end":
            _require(stage == active_stage, "stage_end does not close active stage")
            _require(not open_attempts, "stage_end occurs with an open attempt")
            active_stage = None
        elif event == "attempt_start":
            _require(stage == active_stage, "attempt_start is outside its active stage")
            _require(attempt not in seen_attempts, "attempt identifier is reused")
            seen_attempts.add(attempt)
            open_attempts[attempt] = stage
        elif attempt is not None:
            _require(open_attempts.get(attempt) == stage, "attempt event is outside its attempt")
            if event in {"attempt_end", "attempt_failure"}:
                del open_attempts[attempt]
        elif event in {"stage_failure"}:
            _require(stage == active_stage, "stage_failure is outside its active stage")
        elif event in {
            "capture_start",
            "capture_end",
            "transformer_installed",
            "transform_applied",
            "transform_rejected",
            "transform_failure",
            "observer_failure",
        }:
            _require(stage is None, f"{event} unexpectedly has a stage")
    _require(active_stage is None and not open_attempts, "raw trace ends with open state")
    _require(sorted(seen_attempts) == list(range(1, len(seen_attempts) + 1)),
             "attempt identifiers are not contiguous")

    footer = rows[-1]["payload"]
    _require(footer["health"] in {"healthy", "failed"}, "capture end health is unsupported")
    _require(
        footer["transform_applied_count"] == counts["transform_applied"],
        "capture footer transform count disagrees with events",
    )
    _require(footer["selected_count"] == counts["provider_selected"],
             "capture footer selection count disagrees with events")
    stage_ends = {row["stage"]: row["payload"]["outcome"]
                  for row in rows if row["event"] == "stage_end"}
    _require(footer["bootstrap_started"] == any(
        row["event"] == "stage_start" and row["stage"] == "bootstrap" for row in rows
    ), "capture footer bootstrap start state disagrees with events")
    _require(footer["service_started"] == any(
        row["event"] == "stage_start" and row["stage"] == "service" for row in rows
    ), "capture footer service start state disagrees with events")
    _require(footer["bootstrap_outcome"] == stage_ends.get("bootstrap"),
             "capture footer bootstrap outcome disagrees with events")
    _require(footer["service_outcome"] == stage_ends.get("service"),
             "capture footer service outcome disagrees with events")
    has_stage_failure = counts["stage_failure"] > 0
    _require(footer["stage_failure"] == has_stage_failure,
             "capture footer stage-failure state disagrees with events")
    if footer["health"] == "healthy":
        _require(counts["transform_applied"] == 1, "healthy trace lacks applied transform")
        _require(stage_ends == {"bootstrap": "completed", "service": "selected"},
                 "healthy trace has incomplete discovery stages")
        _require(counts["provider_selected"] == 1, "healthy trace lacks one selection")
        _require(not has_stage_failure and not footer["write_failure"],
                 "healthy trace reports observer or stage failure")
        selected = next(row for row in rows if row["event"] == "provider_selected")
        validity = [
            row for row in rows
            if row["event"] == "validity_returned"
            and row["attempt"] == selected["attempt"]
            and row["sequence"] < selected["sequence"]
        ]
        _require(validity and validity[-1]["payload"]["valid"] is True,
                 "healthy selection is not preceded by isValid=true")
    return deepcopy(rows)


def _measure(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DefiningLoaderTraceValidationError(f"cannot inspect {label}: {exc}") from exc
    _require(stat.S_ISREG(metadata.st_mode), f"{label} is not a regular file")
    _require(not stat.S_ISLNK(metadata.st_mode), f"{label} cannot be a symlink")
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise DefiningLoaderTraceValidationError(f"cannot read {label}: {exc}") from exc
    return {
        "label": path.name,
        "sha256": hashlib.sha256(value).hexdigest(),
        "size_bytes": len(value),
    }, value


def _artifact_path_from_uri(value: str, context: str) -> Path:
    if value.startswith("jar:"):
        nested, marker, entry = value[4:].partition("!/")
        _require(bool(marker) and bool(entry), f"{context} is not a complete jar entry URI")
        value = nested
    parsed = urlsplit(value)
    _require(parsed.scheme == "file", f"{context} does not use file: storage")
    _require(parsed.netloc in {"", "localhost"}, f"{context} has a remote authority")
    _require(not parsed.query and not parsed.fragment, f"{context} has query or fragment")
    path = Path(url2pathname(parsed.path))
    _require(path.is_absolute(), f"{context} does not resolve to an absolute path")
    return path


def _same_source(uri: str, path: Path, context: str) -> None:
    source = _artifact_path_from_uri(uri, context)
    try:
        same = os.path.samefile(source, path)
    except OSError as exc:
        raise DefiningLoaderTraceValidationError(f"cannot compare {context}: {exc}") from exc
    _require(same, f"{context} does not name the measured artifact")


def _zip_entry(artifact: bytes, entry: str, context: str) -> bytes:
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(artifact)) as archive:
            matches = [name for name in archive.namelist() if name == entry]
            _require(len(matches) == 1, f"{context} does not contain exactly one {entry}")
            return archive.read(entry)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DefiningLoaderTraceValidationError(f"cannot inspect {context}: {exc}") from exc


def _descriptor_members(artifact: bytes, interface: str, context: str) -> list[str]:
    entry = "META-INF/services/" + interface
    encoded = _zip_entry(artifact, entry, context)
    try:
        lines = encoded.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise DefiningLoaderTraceValidationError(f"{context} descriptor is not UTF-8") from exc
    return [line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()]


def _ordered_attempts(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    attempts: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in rows:
        attempt = row["attempt"]
        if row["event"] == "attempt_start":
            order.append(attempt)
            attempts[attempt] = {
                "attempt_id": attempt,
                "stage": row["stage"],
                "mechanism": row["payload"]["mechanism"],
                "start_sequence": row["sequence"],
                "terminal_sequence": None,
                "terminal_outcome": "incomplete",
                "events": [],
            }
        elif attempt is not None:
            projection = {
                "sequence": row["sequence"],
                "event": row["event"],
                "payload": deepcopy(row["payload"]),
            }
            attempts[attempt]["events"].append(projection)
            if row["event"] == "attempt_end":
                attempts[attempt]["terminal_sequence"] = row["sequence"]
                attempts[attempt]["terminal_outcome"] = row["payload"]["outcome"]
            elif row["event"] == "attempt_failure":
                attempts[attempt]["terminal_sequence"] = row["sequence"]
                attempts[attempt]["terminal_outcome"] = "failed"
    return [attempts[item] for item in order]


def _file_record(value: Any, context: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    _closed(value, _FILE_KEYS, context)
    return {
        "label": _text(value["label"], f"{context}.label"),
        "sha256": _sha256(value["sha256"], f"{context}.sha256"),
        "size_bytes": _integer(value["size_bytes"], f"{context}.size_bytes"),
    }


def build_defining_loader_trace_receipt(
    *,
    raw_trace_path: Path,
    candidate_lock_path: Path,
    toolchain_lock_path: Path,
    observer_agent_path: Path,
    mixin_engine_artifact_path: Path,
    selected_provider_artifact_path: Path,
    launch_log_path: Path,
    fixture_result_path: Path,
    launch_id: str,
    profile_id: str,
    side: str,
) -> dict[str, Any]:
    """Measure inputs and build a content-addressed V2 receipt."""

    measured: dict[str, dict[str, Any]] = {}
    contents: dict[str, bytes] = {}
    for name, path in (
        ("raw_trace", raw_trace_path),
        ("candidate_lock", candidate_lock_path),
        ("toolchain_lock", toolchain_lock_path),
        ("observer_agent", observer_agent_path),
        ("mixin_engine_artifact", mixin_engine_artifact_path),
        ("selected_provider_artifact", selected_provider_artifact_path),
        ("launch_log", launch_log_path),
        ("fixture_result", fixture_result_path),
    ):
        measured[name], contents[name] = _measure(path, name.replace("_", " "))

    rows = parse_raw_discovery_trace(contents["raw_trace"])
    capture_id = rows[0]["capture_id"]
    transform = next(row for row in rows if row["event"].startswith("transform_"))
    _require(transform["event"] == "transform_applied", "target transform was not applied")
    _require(transform["payload"]["target_class"] == MIXIN_SERVICE_CLASS,
             "transform target is not MixinService")

    engine_class = _zip_entry(
        contents["mixin_engine_artifact"], MIXIN_SERVICE_ENTRY, "Mixin engine artifact"
    )
    _require(
        hashlib.sha256(engine_class).hexdigest() == transform["payload"]["input_sha256"],
        "MixinService input bytes do not match the measured engine artifact",
    )
    agent_payload = _zip_entry(
        contents["observer_agent"], AGENT_PAYLOAD_ENTRY, "observer agent"
    )
    _require(
        hashlib.sha256(agent_payload).hexdigest() == transform["payload"]["output_sha256"],
        "MixinService output bytes do not match the agent payload",
    )
    _same_source(
        transform["payload"]["target_code_source_uri"],
        mixin_engine_artifact_path,
        "MixinService target code source",
    )

    selections = [row for row in rows if row["event"] == "provider_selected"]
    _require(len(selections) == 1, "raw trace does not select exactly one service")
    selection = selections[0]
    selected_class = selection["payload"]["provider_class"]
    selected_uri = selection["payload"]["provider_code_source_uri"]
    _require(selected_class is not None and selected_uri is not None,
             "selected provider identity is incomplete")
    _same_source(selected_uri, selected_provider_artifact_path, "selected provider code source")
    _zip_entry(
        contents["selected_provider_artifact"],
        selected_class.replace(".", "/") + ".class",
        "selected provider artifact",
    )
    _require(
        selected_class
        in _descriptor_members(
            contents["selected_provider_artifact"], SERVICE_INTERFACE,
            "selected provider artifact",
        ),
        "selected provider is absent from the measured service descriptor",
    )

    bootstrap_constructed = [
        row for row in rows
        if row["event"] == "provider_constructed" and row["stage"] == "bootstrap"
    ]
    for row in bootstrap_constructed:
        provider_class = row["payload"]["provider_class"]
        provider_uri = row["payload"]["provider_code_source_uri"]
        _require(provider_class is not None and provider_uri is not None,
                 "bootstrap provider identity is incomplete")
        _same_source(provider_uri, selected_provider_artifact_path, "bootstrap provider code source")
        _zip_entry(
            contents["selected_provider_artifact"],
            provider_class.replace(".", "/") + ".class",
            "selected provider artifact",
        )
        _require(
            provider_class
            in _descriptor_members(
                contents["selected_provider_artifact"], BOOTSTRAP_INTERFACE,
                "selected provider artifact",
            ),
            "bootstrap provider is absent from the measured service descriptor",
        )

    stage_starts = [row for row in rows if row["event"] == "stage_start"]
    bypass = [
        {
            "stage": row["stage"],
            "property_name": row["payload"]["bypass_property_name"],
            "property_value": row["payload"]["bypass_property_value"],
            "mechanism": row["payload"]["mechanism"],
        }
        for row in stage_starts
    ]
    failures = [
        {
            "sequence": row["sequence"],
            "event": row["event"],
            "stage": row["stage"],
            "attempt": row["attempt"],
            "payload": deepcopy(row["payload"]),
        }
        for row in rows
        if row["event"] in {
            "transform_rejected",
            "transform_failure",
            "attempt_failure",
            "stage_failure",
            "observer_failure",
        }
    ]
    footer = rows[-1]["payload"]
    health = {
        "start_health": rows[0]["payload"]["health"],
        "end_health": footer["health"],
        "transform_applied_count": footer["transform_applied_count"],
        "bootstrap_started": footer["bootstrap_started"],
        "bootstrap_outcome": footer["bootstrap_outcome"],
        "service_started": footer["service_started"],
        "service_outcome": footer["service_outcome"],
        "selected_count": footer["selected_count"],
        "stage_failure": footer["stage_failure"],
        "write_failure": footer["write_failure"],
    }

    session = {
        "capture_id": capture_id,
        "launch_id": _text(launch_id, "launch_id"),
        "profile_id": _text(profile_id, "profile_id"),
        "side": side,
        "candidate_lock_sha256": measured["candidate_lock"]["sha256"],
        "toolchain_lock_sha256": measured["toolchain_lock"]["sha256"],
    }
    _require(side in _SIDES, f"side is unsupported: {side!r}")
    attempts = _ordered_attempts(rows)
    material = {
        "format": DEFINING_LOADER_TRACE_RECEIPT_FORMAT,
        "schema_version": 2,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": session,
        "inputs": {name: measured[name] for name in _INPUT_NAMES},
        "observation": {
            "mechanism": "java_instrument_exact_class_substitution",
            "target_class": transform["payload"]["target_class"],
            "target_input_sha256": transform["payload"]["input_sha256"],
            "target_output_sha256": transform["payload"]["output_sha256"],
            "target_code_source_uri": transform["payload"]["target_code_source_uri"],
            "defining_loader_class": transform["payload"]["defining_loader_class"],
            "defining_loader_identity": transform["payload"]["defining_loader_identity"],
            "defining_loader_parent_class": transform["payload"]["defining_loader_parent_class"],
            "defining_loader_parent_identity": transform["payload"]["defining_loader_parent_identity"],
            "java_version": rows[0]["payload"]["java_version"],
        },
        "bypass_properties": bypass,
        "ordered_attempts": attempts,
        "selection": {
            "sequence": selection["sequence"],
            "attempt_id": selection["attempt"],
            "provider_class": selected_class,
            "provider_loader_class": selection["payload"]["provider_loader_class"],
            "provider_loader_identity": selection["payload"]["provider_loader_identity"],
            "provider_code_source_uri": selected_uri,
            "provider_artifact_sha256": measured["selected_provider_artifact"]["sha256"],
            "provider_artifact_size_bytes": measured["selected_provider_artifact"]["size_bytes"],
        },
        "failures": failures,
        "health": health,
        "summary": {
            "raw_event_count": len(rows),
            "bootstrap_attempt_count": sum(row["stage"] == "bootstrap" for row in attempts),
            "service_attempt_count": sum(row["stage"] == "service" for row in attempts),
            "failed_attempt_count": sum(row["terminal_outcome"] == "failed" for row in attempts),
            "failure_event_count": len(failures),
        },
        "boundaries": {
            "cleanmix_defining_loader_path_observed": True,
            "thread_context_enumeration_performed": False,
            "provider_or_descriptor_injected": False,
            "bypass_property_modified": False,
            "selected_service_proved_for_this_launch": health["end_health"] == "healthy",
            "mixin_transformation_completion_proved": False,
            "final_class_bytes_proved": False,
        },
    }
    receipt_id = DEFINING_LOADER_TRACE_RECEIPT_PREFIX + hashlib.sha256(
        canonical_discovery_trace_json_bytes(material)
    ).hexdigest()
    return {**material, "receipt_id": receipt_id}


def parse_defining_loader_trace_receipt(value: Any) -> dict[str, Any]:
    """Validate receipt identity and closed derived V2 state."""

    _require(isinstance(value, Mapping), "discovery trace receipt must be an object")
    expected = {
        "format", "schema_version", "receipt_id", "canonicalization_id", "session",
        "inputs", "observation", "bypass_properties", "ordered_attempts", "selection",
        "failures", "health", "summary", "boundaries",
    }
    _require(set(value) == expected, "discovery trace receipt surface is not exactly V2")
    _require(value["format"] == DEFINING_LOADER_TRACE_RECEIPT_FORMAT,
             "discovery trace receipt format is not V2")
    _require(value["schema_version"] == 2, "discovery trace schema_version is not 2")
    _require(value["canonicalization_id"] == CANONICALIZATION_ID,
             "discovery trace canonicalization is unsupported")
    session = value["session"]
    _require(isinstance(session, Mapping), "session must be an object")
    _closed(session, _SESSION_KEYS, "session")
    _text(session["capture_id"], "session.capture_id")
    _text(session["launch_id"], "session.launch_id")
    _text(session["profile_id"], "session.profile_id")
    _require(session["side"] in _SIDES, "session.side is unsupported")
    _sha256(session["candidate_lock_sha256"], "session.candidate_lock_sha256")
    _sha256(session["toolchain_lock_sha256"], "session.toolchain_lock_sha256")
    inputs = value["inputs"]
    _require(isinstance(inputs, Mapping) and set(inputs) == set(_INPUT_NAMES),
             "inputs are not the exact V2 input set")
    normalized_inputs = {
        name: _file_record(inputs[name], f"inputs.{name}") for name in _INPUT_NAMES
    }
    _require(normalized_inputs == inputs, "input file records are not canonical")
    _require(inputs["candidate_lock"]["sha256"] == session["candidate_lock_sha256"],
             "candidate lock input and session disagree")
    _require(inputs["toolchain_lock"]["sha256"] == session["toolchain_lock_sha256"],
             "toolchain lock input and session disagree")
    observation = value["observation"]
    _require(isinstance(observation, Mapping), "observation must be an object")
    _closed(observation, _OBSERVATION_KEYS, "observation")
    _require(observation["mechanism"] == "java_instrument_exact_class_substitution",
             "observation mechanism is unsupported")
    _require(observation["target_class"] == MIXIN_SERVICE_CLASS,
             "observation target is not MixinService")
    _sha256(observation["target_input_sha256"], "observation.target_input_sha256")
    _sha256(observation["target_output_sha256"], "observation.target_output_sha256")
    for key in (
        "target_code_source_uri", "defining_loader_class", "defining_loader_identity",
        "java_version",
    ):
        _text(observation[key], f"observation.{key}")
    for key in ("defining_loader_parent_class", "defining_loader_parent_identity"):
        _nullable_text(observation[key], f"observation.{key}")

    bypass = value["bypass_properties"]
    _require(isinstance(bypass, list), "bypass_properties must be an array")
    _require([row.get("stage") for row in bypass if isinstance(row, Mapping)]
             == ["bootstrap", "service"],
             "bypass properties are not ordered bootstrap then service")
    for index, row in enumerate(bypass):
        context = f"bypass_properties[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, _BYPASS_KEYS, context)
        _text(row["property_name"], f"{context}.property_name")
        _nullable_text(row["property_value"], f"{context}.property_value")
        _require(row["mechanism"] in {"service_loader", "system_property"},
                 f"{context}.mechanism is unsupported")
        _require((row["property_value"] is None) == (row["mechanism"] == "service_loader"),
                 f"{context} mechanism disagrees with bypass value")

    attempts = value["ordered_attempts"]
    _require(isinstance(attempts, list), "ordered_attempts must be an array")
    _require([row.get("attempt_id") for row in attempts if isinstance(row, Mapping)]
             == list(range(1, len(attempts) + 1)),
             "ordered attempt identifiers are not contiguous")
    selected_events: list[tuple[int, Mapping[str, Any]]] = []
    for index, row in enumerate(attempts):
        context = f"ordered_attempts[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, _ATTEMPT_KEYS, context)
        _require(row["stage"] in _STAGES, f"{context}.stage is unsupported")
        _require(row["mechanism"] in {"service_loader", "system_property"},
                 f"{context}.mechanism is unsupported")
        _integer(row["start_sequence"], f"{context}.start_sequence")
        _integer(row["terminal_sequence"], f"{context}.terminal_sequence")
        _text(row["terminal_outcome"], f"{context}.terminal_outcome")
        events = row["events"]
        _require(isinstance(events, list) and bool(events), f"{context}.events must be nonempty")
        sequences: list[int] = []
        for event_index, event in enumerate(events):
            event_context = f"{context}.events[{event_index}]"
            _require(isinstance(event, Mapping), f"{event_context} must be an object")
            _closed(event, _ATTEMPT_EVENT_KEYS, event_context)
            sequences.append(_integer(event["sequence"], f"{event_context}.sequence"))
            _require(event["event"] in _EVENTS, f"{event_context}.event is unsupported")
            _require(isinstance(event["payload"], Mapping),
                     f"{event_context}.payload must be an object")
            if event["event"] == "provider_selected":
                selected_events.append((row["attempt_id"], event))
        _require(sequences == sorted(sequences) and len(sequences) == len(set(sequences)),
                 f"{context}.events are not in unique sequence order")
        _require(row["terminal_sequence"] == sequences[-1],
                 f"{context}.terminal_sequence is not the last event")

    selection = value["selection"]
    _require(isinstance(selection, Mapping), "selection must be an object")
    _closed(selection, _SELECTION_KEYS, "selection")
    _integer(selection["sequence"], "selection.sequence")
    _integer(selection["attempt_id"], "selection.attempt_id", minimum=1)
    for key in (
        "provider_class", "provider_loader_class", "provider_loader_identity",
        "provider_code_source_uri",
    ):
        _text(selection[key], f"selection.{key}")
    _sha256(selection["provider_artifact_sha256"], "selection.provider_artifact_sha256")
    _integer(selection["provider_artifact_size_bytes"],
             "selection.provider_artifact_size_bytes")
    _require(
        selection["provider_artifact_sha256"]
        == inputs["selected_provider_artifact"]["sha256"]
        and selection["provider_artifact_size_bytes"]
        == inputs["selected_provider_artifact"]["size_bytes"],
        "selection artifact identity disagrees with input",
    )
    _require(len(selected_events) == 1, "ordered attempts do not retain one selection event")
    selected_attempt, selected_event = selected_events[0]
    _require(
        selected_attempt == selection["attempt_id"]
        and selected_event["sequence"] == selection["sequence"]
        and selected_event["payload"]["provider_class"] == selection["provider_class"]
        and selected_event["payload"]["provider_code_source_uri"]
        == selection["provider_code_source_uri"],
        "selection projection disagrees with ordered attempt event",
    )

    failures = value["failures"]
    _require(isinstance(failures, list), "failures must be an array")
    for index, row in enumerate(failures):
        context = f"failures[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, _FAILURE_KEYS, context)
        _require(row["event"] in {
            "transform_rejected", "transform_failure", "attempt_failure",
            "stage_failure", "observer_failure",
        }, f"{context}.event is not a failure")

    health = value["health"]
    _require(isinstance(health, Mapping), "health must be an object")
    _closed(health, _HEALTH_KEYS, "health")
    _require(health["start_health"] == "starting", "start health is invalid")
    _require(health["end_health"] in {"healthy", "failed"}, "end health is invalid")
    for key in ("bootstrap_started", "service_started", "stage_failure", "write_failure"):
        _require(isinstance(health[key], bool), f"health.{key} must be boolean")
    for key in ("transform_applied_count", "selected_count"):
        _integer(health[key], f"health.{key}")

    summary = value["summary"]
    _require(isinstance(summary, Mapping), "summary must be an object")
    _closed(summary, _SUMMARY_KEYS, "summary")
    expected_summary = {
        "raw_event_count": summary["raw_event_count"],
        "bootstrap_attempt_count": sum(row["stage"] == "bootstrap" for row in attempts),
        "service_attempt_count": sum(row["stage"] == "service" for row in attempts),
        "failed_attempt_count": sum(row["terminal_outcome"] == "failed" for row in attempts),
        "failure_event_count": len(failures),
    }
    _integer(summary["raw_event_count"], "summary.raw_event_count", minimum=1)
    _require(summary == expected_summary, "summary does not match retained attempts and failures")

    boundaries = value["boundaries"]
    _require(isinstance(boundaries, Mapping), "boundaries must be an object")
    _closed(boundaries, _BOUNDARY_KEYS, "boundaries")
    _require(boundaries == {
        "cleanmix_defining_loader_path_observed": True,
        "thread_context_enumeration_performed": False,
        "provider_or_descriptor_injected": False,
        "bypass_property_modified": False,
        "selected_service_proved_for_this_launch": health["end_health"] == "healthy",
        "mixin_transformation_completion_proved": False,
        "final_class_bytes_proved": False,
    }, "receipt boundaries are not exact V2 semantics")
    material = deepcopy(dict(value))
    supplied = material.pop("receipt_id")
    _require(
        supplied == DEFINING_LOADER_TRACE_RECEIPT_PREFIX
        + hashlib.sha256(canonical_discovery_trace_json_bytes(material)).hexdigest(),
        "discovery trace receipt identity mismatch",
    )
    return deepcopy(dict(value))


def render_defining_loader_trace_receipt(value: Mapping[str, Any]) -> bytes:
    admitted = parse_defining_loader_trace_receipt(value)
    return canonical_discovery_trace_json_bytes(admitted) + b"\n"
