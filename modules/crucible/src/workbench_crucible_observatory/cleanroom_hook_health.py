"""Streaming hook-health evaluation for exact Cleanroom raw captures.

The evaluator deliberately proves one narrow fact: every hook declared by an
exact probe plan reached its planned injection point in each required runtime
case.  It does not replace full raw admission or canonical normalization.

Only ``probe_health`` and ``capture_control`` rows are evaluated against the
complete raw JSON Schema.  Every row is nevertheless parsed from the same
stream, byte-hashed, and checked for the closed transport envelope, contiguous
global sequence, stable capture identity, complete coverage, and zero drops.
This keeps memory proportional to the number of declared hooks rather than the
size of the raw capture.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Sequence

from .bundle import (
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
)
from .cleanroom_raw import (
    _bind_plan_to_schema,
    _capture_nonce_field,
    _plan_hooks,
    _record_types,
    _schema_validator,
    _validate_schema_surface,
)


HOOK_HEALTH_EVALUATION_SCHEMA = (
    "workbench.crucible.cleanroom-hook-health-evaluation.v1"
)
HOOK_HEALTH_EVALUATION_PREFIX = (
    "crucible-cleanroom-hook-health-evaluation:sha256:"
)
SESSION_AUDIT_SCHEMA = "workbench.crucible.exact-runtime-session-audit.v1"
SESSION_AUDIT_PREFIX = "crucible-exact-runtime-session-audit:sha256:"

REQUIRED_CASE_ROLE = "required-complete"
EXCLUDED_INCOMPLETE_CASE_ROLE = "excluded-incomplete"
_CASE_ROLES = frozenset({REQUIRED_CASE_ROLE, EXCLUDED_INCOMPLETE_CASE_ROLE})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ZERO_SHA256 = "0" * 64
_READ_SIZE = 1024 * 1024
_MAX_NDJSON_LINE_BYTES = 16 * 1024 * 1024
_MAX_STATIC_JSON_BYTES = 64 * 1024 * 1024
_MAX_THREAD_COUNT = 4096
_RAW_ENVELOPE_KEYS = {
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
_ORDER_KEYS = {
    "thread_name",
    "thread_id",
    "thread_sequence",
    "lamport",
    "monotonic_ns",
}
_COVERAGE_KEYS = {"mode", "detail_state", "dropped_record_count"}
_HEALTH_PAYLOAD_KEYS = {
    "hook_id",
    "health_state",
    "target_class",
    "target_method",
    "target_descriptor",
    "original_class_sha256",
    "transformed_class_sha256",
    "expected_injection_count",
    "observed_injection_count",
}
_SESSION_KEYS = {
    "audit_id",
    "schema",
    "case_id",
    "outcome",
    "observer_enabled",
    "candidate_lock",
    "fixture_artifact",
    "installed_mod_set",
    "configuration_set",
    "launch_log",
    "raw_capture",
    "fixture_result",
    "foundation_class_dump",
}


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CleanroomHookHealthCase:
    """One raw case and its independently generated session-custody receipt."""

    case_id: str
    case_role: str
    raw_path: str | os.PathLike[str]
    expected_raw_sha256: str
    session_audit_path: str | os.PathLike[str]
    expected_session_audit_sha256: str


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _FileReceipt:
    path: Path
    snapshot: _FileSnapshot
    sha256: str
    encoded: bytes | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _exact_keys(value: Any, expected: set[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == expected,
        f"{context} fields mismatch: missing={sorted(expected - actual)!r}, "
        f"unknown={sorted(actual - expected)!r}",
    )
    return value


def _nonempty(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token}")


def _reject_float(token: str) -> None:
    raise ValueError(f"JSON number is not an integer: {token}")


def _parse_json(encoded: bytes, *, context: str) -> Any:
    try:
        return json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse {context}: {exc}") from exc


def _snapshot(metadata: os.stat_result) -> _FileSnapshot:
    return _FileSnapshot(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _open_regular(path: Path, *, context: str) -> tuple[int, _FileSnapshot]:
    try:
        scanned = _snapshot(path.lstat())
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(scanned.mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISREG(scanned.mode), f"{context} is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = _snapshot(os.fstat(descriptor))
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise CaptureValidationError(f"cannot open {context} {path}: {exc}") from exc
    assert descriptor is not None
    if opened != scanned:
        os.close(descriptor)
        raise CaptureValidationError(f"{context} changed before it was opened: {path}")
    return descriptor, opened


def _finish_regular(
    path: Path,
    descriptor: int,
    before: _FileSnapshot,
    *,
    context: str,
) -> None:
    try:
        after = _snapshot(os.fstat(descriptor))
        path_after = _snapshot(path.lstat())
    except OSError as exc:
        raise CaptureValidationError(f"cannot re-inspect {context} {path}: {exc}") from exc
    _require(before == after == path_after, f"{context} changed while it was read: {path}")


def _read_small_file(
    value: str | os.PathLike[str],
    *,
    expected_sha256: str,
    context: str,
) -> _FileReceipt:
    path = Path(value)
    expected = _sha256(expected_sha256, f"expected {context} digest")
    descriptor, before = _open_regular(path, context=context)
    try:
        _require(
            before.size <= _MAX_STATIC_JSON_BYTES,
            f"{context} exceeds the static JSON size bound: {path}",
        )
        blocks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, _READ_SIZE)
            if not block:
                break
            total += len(block)
            _require(total <= _MAX_STATIC_JSON_BYTES, f"{context} grew too large: {path}")
            digest.update(block)
            blocks.append(block)
        _finish_regular(path, descriptor, before, context=context)
    except BaseException:
        os.close(descriptor)
        raise
    os.close(descriptor)
    observed = digest.hexdigest()
    _require(observed == expected, f"{context} digest mismatch: {path}")
    return _FileReceipt(path, before, observed, b"".join(blocks))


def _schema_errors(validator: Any, row: Mapping[str, Any], line_number: int) -> None:
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
            f"raw schema evaluation failed on NDJSON line {line_number}: {exc}"
        ) from exc
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path)
        suffix = f" at {location}" if location else ""
        raise CaptureValidationError(
            f"raw schema violation on NDJSON line {line_number}{suffix}: {first.message}"
        )


def _session_audit(
    case: CleanroomHookHealthCase,
    *,
    candidate_file_sha256: str,
    candidate_canonical_sha256: str,
) -> tuple[dict[str, Any], _FileReceipt]:
    receipt = _read_small_file(
        case.session_audit_path,
        expected_sha256=case.expected_session_audit_sha256,
        context=f"session audit for {case.case_id}",
    )
    assert receipt.encoded is not None
    value = _parse_json(receipt.encoded, context=f"session audit for {case.case_id}")
    audit = _exact_keys(value, _SESSION_KEYS, f"session audit for {case.case_id}")
    _require(audit["schema"] == SESSION_AUDIT_SCHEMA, "session audit schema mismatch")
    _require(audit["case_id"] == case.case_id, "session audit case ID mismatch")
    material = dict(audit)
    audit_id = material.pop("audit_id")
    _require(
        audit_id == SESSION_AUDIT_PREFIX + canonical_json_sha256(material),
        f"session audit content identity mismatch for {case.case_id}",
    )

    candidate = _exact_keys(
        audit["candidate_lock"],
        {"canonical_sha256", "file_sha256"},
        f"session audit candidate for {case.case_id}",
    )
    _require(
        candidate["file_sha256"] == candidate_file_sha256
        and candidate["canonical_sha256"] == candidate_canonical_sha256,
        f"session audit candidate binding mismatch for {case.case_id}",
    )
    _require(audit["observer_enabled"] is True, f"observer is disabled for {case.case_id}")

    raw = _exact_keys(
        audit["raw_capture"],
        {
            "capture_id",
            "file_sha256",
            "fixture_completion_marker_count",
            "row_count",
            "size_bytes",
            "stop_control_count",
            "terminal_sequence",
            "terminal_state",
        },
        f"session audit raw capture for {case.case_id}",
    )
    _require(
        raw["file_sha256"] == case.expected_raw_sha256,
        f"session audit raw digest mismatch for {case.case_id}",
    )
    for field in (
        "fixture_completion_marker_count",
        "row_count",
        "size_bytes",
        "stop_control_count",
        "terminal_sequence",
    ):
        _integer(raw[field], f"session audit raw {field} for {case.case_id}")
    _nonempty(raw["capture_id"], f"session audit capture ID for {case.case_id}")

    foundation = _exact_keys(
        audit["foundation_class_dump"],
        {"class_count", "format", "manifest_sha256", "total_size_bytes"},
        f"session audit Foundation custody for {case.case_id}",
    )
    _require(
        foundation["format"] == "workbench-foundation-class-dump-manifest-v1",
        f"Foundation custody format mismatch for {case.case_id}",
    )
    _integer(foundation["class_count"], f"Foundation class count for {case.case_id}", minimum=1)
    _integer(
        foundation["total_size_bytes"],
        f"Foundation byte count for {case.case_id}",
        minimum=1,
    )
    _sha256(foundation["manifest_sha256"], f"Foundation manifest for {case.case_id}")

    fixture = _exact_keys(
        audit["fixture_artifact"],
        {"file_sha256", "size_bytes"},
        f"session audit fixture artifact for {case.case_id}",
    )
    fixture_sha256 = _sha256(
        fixture["file_sha256"], f"session audit fixture artifact for {case.case_id}"
    )
    _integer(fixture["size_bytes"], f"fixture artifact size for {case.case_id}", minimum=1)
    installed = _exact_keys(
        audit["installed_mod_set"],
        {
            "canonical_sha256",
            "file_sha256",
            "runtime_class_source_sha256",
            "verified_artifacts",
        },
        f"session audit installed mod set for {case.case_id}",
    )
    runtime_class_source_sha256 = _sha256(
        installed["runtime_class_source_sha256"],
        f"runtime class source for {case.case_id}",
    )
    verified_artifacts = installed["verified_artifacts"]
    _require(
        isinstance(verified_artifacts, list)
        and any(
            isinstance(item, Mapping)
            and item.get("sha256") == fixture_sha256
            for item in verified_artifacts
        ),
        f"session audit installed mod set lacks the fixture for {case.case_id}",
    )

    if case.case_role == REQUIRED_CASE_ROLE:
        _require(audit["outcome"] == "completed", f"required case did not complete: {case.case_id}")
        _require(
            raw["terminal_state"] == "complete_and_stopped"
            and raw["stop_control_count"] == 1,
            f"required case lacks a complete raw terminal state: {case.case_id}",
        )
        fixture_result = audit["fixture_result"]
        _require(isinstance(fixture_result, Mapping), f"required case lacks a fixture result: {case.case_id}")
        _require(
            fixture_result.get("completion_state") == "complete",
            f"required fixture result is incomplete: {case.case_id}",
        )
        runtime_inventory_sha256 = _sha256(
            fixture_result.get("runtime_inventory_sha256"),
            f"runtime mod inventory for {case.case_id}",
        )
    else:
        _require(audit["outcome"] == "crash", f"excluded case is not a crash: {case.case_id}")
        _require(audit["fixture_result"] is None, f"crash case unexpectedly has a result: {case.case_id}")
        _require(
            raw["terminal_state"] == "incomplete_without_stop"
            and raw["stop_control_count"] == 0,
            f"excluded crash does not have incomplete raw residue: {case.case_id}",
        )
        runtime_inventory_sha256 = None

    return (
        {
            "audit_id": audit_id,
            "file_sha256": receipt.sha256,
            "fixture_artifact_file_sha256": fixture_sha256,
            "runtime_class_source_sha256": runtime_class_source_sha256,
            "foundation_class_dump": {
                "format": foundation["format"],
                "class_count": foundation["class_count"],
                "total_size_bytes": foundation["total_size_bytes"],
                "manifest_sha256": foundation["manifest_sha256"],
            },
            "runtime_mod_inventory_sha256": runtime_inventory_sha256,
            "raw_capture": dict(raw),
        },
        receipt,
    )


def _health_summary(
    hook_id: str,
    planned: Mapping[str, Any],
    prior: Mapping[str, Any] | None,
    row: Mapping[str, Any],
    *,
    fixture_artifact_sha256: str,
    admitted_actor_source_sha256: frozenset[str],
) -> dict[str, Any]:
    payload = _exact_keys(
        row["payload"], _HEALTH_PAYLOAD_KEYS, f"probe health payload for {hook_id}"
    )
    observed_target = (
        payload["target_class"],
        payload["target_method"],
        payload["target_descriptor"],
    )
    planned_target = (
        planned["target_class"],
        planned["target_method"],
        planned["target_descriptor"],
    )
    _require(observed_target == planned_target, f"probe health target mismatch for {hook_id}")
    expected_count = planned["expected_cardinality"]
    _require(
        payload["expected_injection_count"] == expected_count
        and payload["observed_injection_count"] == expected_count,
        f"probe health cardinality mismatch for {hook_id}",
    )
    original = _sha256(payload["original_class_sha256"], f"original class for {hook_id}")
    transformed = _sha256(
        payload["transformed_class_sha256"], f"transformed class for {hook_id}"
    )
    _require(
        original != _ZERO_SHA256
        and transformed != _ZERO_SHA256
        and original != transformed,
        f"probe health lacks a transformed class identity for {hook_id}",
    )
    outcome = row["outcome"]
    _require(
        outcome.get("state") == "observed"
        and outcome.get("exception_class") is None
        and outcome.get("exception_message_sha256") is None,
        f"probe health outcome is not a clean observation for {hook_id}",
    )

    state = payload["health_state"]
    _require(state != "failed", f"probe health reported failure for {hook_id}")
    _require(
        state in {"applied_not_reached", "reached"},
        f"probe health state is invalid for {hook_id}",
    )
    actor = row["actor"]
    for field in ("binding", "class_name", "method_name", "method_descriptor"):
        _nonempty(actor.get(field), f"probe health actor {field} for {hook_id}")
    actor_source = actor.get("code_source_sha256")
    _require(
        actor_source in admitted_actor_source_sha256,
        f"probe health actor lacks session custody for {hook_id}",
    )
    if state == "applied_not_reached":
        _require(
            actor.get("binding") == "workbench"
            and actor_source == fixture_artifact_sha256,
            f"probe installation health lacks fixture custody for {hook_id}",
        )
    if prior is None:
        states: list[str] = []
        first_sequence = row["sequence"]
    else:
        states = list(prior["states"])
        first_sequence = prior["first_sequence"]
        _require(state not in states, f"duplicate probe health state {state} for {hook_id}")
        _require("reached" not in states, f"probe health follows terminal reached for {hook_id}")
        _require(
            prior["original_class_sha256"] == original
            and prior["transformed_class_sha256"] == transformed,
            f"probe health class identity changed for {hook_id}",
        )
    states.append(state)
    return {
        "hook_id": hook_id,
        "canonical_role": planned["canonical_role"],
        "target_class": planned_target[0],
        "target_method": planned_target[1],
        "target_descriptor": planned_target[2],
        "expected_injection_count": expected_count,
        "observed_injection_count": payload["observed_injection_count"],
        "original_class_sha256": original,
        "transformed_class_sha256": transformed,
        "states": states,
        "first_sequence": first_sequence,
        "latest_sequence": row["sequence"],
        "latest_state": state,
    }


def _validate_controls(
    controls: Sequence[Mapping[str, Any]],
    *,
    case: CleanroomHookHealthCase,
    row_count: int,
    capture_id: str,
    requested_mode: str,
) -> dict[str, Any]:
    _require(bool(controls), f"raw capture lacks a start control for {case.case_id}")
    start = controls[0]
    start_payload = start["payload"]
    _require(
        start["sequence"] == 0
        and start_payload.get("control") == "start"
        and start_payload.get("requested_mode") == requested_mode
        and start["outcome"].get("state") == "entered",
        f"raw capture start control is invalid for {case.case_id}",
    )
    control_id = start_payload.get("capture_control_id")
    _require(
        isinstance(control_id, str) and control_id.startswith(capture_id + ":"),
        f"raw capture control ID is foreign for {case.case_id}",
    )

    if case.case_role == REQUIRED_CASE_ROLE:
        _require(len(controls) == 2, f"required case needs exactly two controls: {case.case_id}")
        stop = controls[1]
        payload = stop["payload"]
        _require(
            stop["sequence"] == row_count - 1
            and payload.get("control") == "stop"
            and payload.get("capture_control_id") == control_id
            and payload.get("controller") == start_payload.get("controller")
            and payload.get("requested_mode") == requested_mode
            and payload.get("route_order") == start_payload.get("route_order")
            and payload.get("route_sha256") == start_payload.get("route_sha256")
            and payload.get("completion_state") == "complete"
            and payload.get("open_span_count") == 0
            and payload.get("open_write_count") == 0
            and stop["outcome"].get("state") == "returned",
            f"raw capture stop control is invalid for {case.case_id}",
        )
        return {
            "completion_state": "complete",
            "start_sequence": 0,
            "stop_sequence": stop["sequence"],
        }

    _require(len(controls) == 1, f"excluded crash must lack a stop control: {case.case_id}")
    return {
        "completion_state": "incomplete",
        "start_sequence": 0,
        "stop_sequence": None,
    }


def _stream_case(
    case: CleanroomHookHealthCase,
    *,
    raw_format: str,
    nonce_field: str,
    admitted_record_types: frozenset[str],
    validator: Any,
    hooks: Mapping[str, Mapping[str, Any]],
    session: Mapping[str, Any],
) -> dict[str, Any]:
    expected_raw = _sha256(case.expected_raw_sha256, f"expected raw digest for {case.case_id}")
    path = Path(case.raw_path)
    descriptor, before = _open_regular(path, context=f"raw NDJSON for {case.case_id}")
    digest = hashlib.sha256()
    row_count = 0
    byte_count = 0
    capture_id: str | None = None
    requested_mode: str | None = None
    thread_sequences: dict[tuple[str, int], int] = {}
    thread_monotonic: dict[tuple[str, int], int] = {}
    health: dict[str, dict[str, Any]] = {}
    health_record_count = 0
    controls: list[Mapping[str, Any]] = []
    last_had_newline = False
    fixture_sha256 = session["fixture_artifact_file_sha256"]
    admitted_actor_sources = frozenset(
        {
            fixture_sha256,
            session["runtime_class_source_sha256"],
        }
    )

    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                encoded = stream.readline(_MAX_NDJSON_LINE_BYTES + 1)
                if not encoded:
                    break
                _require(
                    len(encoded) <= _MAX_NDJSON_LINE_BYTES,
                    f"raw NDJSON line {row_count + 1} exceeds the line-size bound",
                )
                digest.update(encoded)
                byte_count += len(encoded)
                last_had_newline = encoded.endswith(b"\n")
                _require(last_had_newline, f"raw NDJSON lacks a terminal newline at line {row_count + 1}")
                body = encoded[:-1]
                if body.endswith(b"\r"):
                    body = body[:-1]
                _require(bool(body.strip()), f"raw NDJSON line {row_count + 1} is blank")
                value = _parse_json(body, context=f"raw NDJSON line {row_count + 1}")
                row = _exact_keys(
                    value,
                    _RAW_ENVELOPE_KEYS | {nonce_field},
                    f"raw NDJSON line {row_count + 1}",
                )
                _require(row["format"] == raw_format, f"raw format mismatch on line {row_count + 1}")
                record_type = row["record_type"]
                _require(
                    isinstance(record_type, str) and record_type in admitted_record_types,
                    f"raw record type is not admitted on line {row_count + 1}",
                )
                _require(
                    row["sequence"] == row_count,
                    f"raw global sequence is not contiguous on line {row_count + 1}",
                )
                row_capture_id = _nonempty(row[nonce_field], f"raw capture ID on line {row_count + 1}")
                _require(row_capture_id != "unbound", f"raw capture ID is unbound on line {row_count + 1}")
                if capture_id is None:
                    capture_id = row_capture_id
                _require(row_capture_id == capture_id, f"raw capture ID changed on line {row_count + 1}")

                coverage = _exact_keys(
                    row["coverage"], _COVERAGE_KEYS, f"raw coverage on line {row_count + 1}"
                )
                mode = _nonempty(coverage["mode"], f"raw coverage mode on line {row_count + 1}")
                if requested_mode is None:
                    requested_mode = mode
                _require(mode == requested_mode, f"raw coverage mode changed on line {row_count + 1}")
                _require(
                    coverage["detail_state"] == "complete"
                    and coverage["dropped_record_count"] == 0,
                    f"raw coverage is incomplete or dropped records on line {row_count + 1}",
                )

                order = _exact_keys(row["order"], _ORDER_KEYS, f"raw order on line {row_count + 1}")
                thread_name = _nonempty(order["thread_name"], f"thread name on line {row_count + 1}")
                thread_id = _integer(order["thread_id"], f"thread ID on line {row_count + 1}")
                thread_key = (thread_name, thread_id)
                if thread_key not in thread_sequences:
                    _require(
                        len(thread_sequences) < _MAX_THREAD_COUNT,
                        f"raw thread count exceeds the admission bound for {case.case_id}",
                    )
                expected_thread_sequence = thread_sequences.get(thread_key, 0)
                _require(
                    order["thread_sequence"] == expected_thread_sequence,
                    f"raw thread sequence is not contiguous for {thread_key}",
                )
                thread_sequences[thread_key] = expected_thread_sequence + 1
                monotonic = _integer(order["monotonic_ns"], f"monotonic time on line {row_count + 1}")
                _require(
                    monotonic >= thread_monotonic.get(thread_key, -1),
                    f"raw monotonic time moved backward for {thread_key}",
                )
                thread_monotonic[thread_key] = monotonic
                _integer(order["lamport"], f"Lamport time on line {row_count + 1}")

                if record_type in {"probe_health", "capture_control"}:
                    _schema_errors(validator, row, row_count + 1)
                if record_type == "capture_control":
                    controls.append(dict(row))
                    _require(len(controls) <= 2, f"too many capture controls for {case.case_id}")
                elif record_type == "probe_health":
                    health_record_count += 1
                    payload = row["payload"]
                    hook_id = payload.get("hook_id") if isinstance(payload, Mapping) else None
                    _require(
                        isinstance(hook_id, str) and hook_id in hooks,
                        f"probe health names an unplanned hook {hook_id!r}",
                    )
                    health[hook_id] = _health_summary(
                        hook_id,
                        hooks[hook_id],
                        health.get(hook_id),
                        row,
                        fixture_artifact_sha256=fixture_sha256,
                        admitted_actor_source_sha256=admitted_actor_sources,
                    )
                row_count += 1

        _require(row_count > 0 and last_had_newline, f"raw NDJSON is empty for {case.case_id}")
        _finish_regular(path, descriptor, before, context=f"raw NDJSON for {case.case_id}")
    except BaseException:
        os.close(descriptor)
        raise
    os.close(descriptor)

    observed_raw = digest.hexdigest()
    _require(observed_raw == expected_raw, f"raw NDJSON digest mismatch for {case.case_id}")
    _require(byte_count == before.size, f"raw NDJSON size changed for {case.case_id}")
    assert capture_id is not None and requested_mode is not None
    audited_raw = session["raw_capture"]
    _require(
        audited_raw["capture_id"] == capture_id
        and audited_raw["size_bytes"] == byte_count
        and audited_raw["row_count"] == row_count
        and audited_raw["terminal_sequence"] == row_count - 1,
        f"session audit raw facts mismatch for {case.case_id}",
    )
    control_summary = _validate_controls(
        controls,
        case=case,
        row_count=row_count,
        capture_id=capture_id,
        requested_mode=requested_mode,
    )

    missing = sorted(set(hooks) - set(health))
    not_reached = sorted(
        hook_id for hook_id, summary in health.items() if summary["latest_state"] != "reached"
    )
    if case.case_role == REQUIRED_CASE_ROLE:
        _require(not missing, f"required hooks lack health records: {', '.join(missing)}")
        _require(not not_reached, f"required hooks were not latest reached: {', '.join(not_reached)}")
        verdict = "all_declared_hooks_reached"
    else:
        verdict = "excluded_incomplete_case"

    hook_rows = []
    for hook_id in sorted(health):
        summary = dict(health[hook_id])
        summary["states"] = list(summary["states"])
        hook_rows.append(summary)
    return {
        "case_id": case.case_id,
        "case_role": case.case_role,
        "verdict": verdict,
        "raw_capture": {
            "file_sha256": observed_raw,
            "size_bytes": byte_count,
            "row_count": row_count,
            "terminal_sequence": row_count - 1,
            "capture_id": capture_id,
            "requested_mode": requested_mode,
            "thread_count": len(thread_sequences),
            **control_summary,
        },
        "session_custody": {
            key: value for key, value in session.items() if key != "raw_capture"
        },
        "hook_health": {
            "declared_hook_count": len(hooks),
            "observed_hook_count": len(health),
            "health_record_count": health_record_count,
            "missing_hook_ids": missing,
            "latest_not_reached_hook_ids": not_reached,
            "hooks": hook_rows,
        },
    }


def evaluate_cleanroom_hook_health(
    *,
    schema_path: str | os.PathLike[str],
    expected_schema_sha256: str,
    expected_schema_id: str,
    probe_plan_path: str | os.PathLike[str],
    expected_probe_plan_sha256: str,
    expected_probe_plan_id: str,
    candidate_lock_path: str | os.PathLike[str],
    expected_candidate_lock_sha256: str,
    expected_candidate_id: str,
    cases: Sequence[CleanroomHookHealthCase],
) -> dict[str, Any]:
    """Stream and evaluate a closed set of exact-runtime hook-health cases."""

    _require(bool(cases), "hook-health evaluation requires at least one case")
    _nonempty(expected_schema_id, "expected raw schema ID")
    _nonempty(expected_probe_plan_id, "expected probe plan ID")
    _nonempty(expected_candidate_id, "expected candidate ID")
    case_ids: set[str] = set()
    for case in cases:
        _require(isinstance(case, CleanroomHookHealthCase), "invalid hook-health case input")
        _require(
            _CASE_ID_RE.fullmatch(case.case_id) is not None,
            f"invalid hook-health case ID: {case.case_id!r}",
        )
        _require(case.case_id not in case_ids, f"duplicate hook-health case ID: {case.case_id}")
        case_ids.add(case.case_id)
        _require(case.case_role in _CASE_ROLES, f"invalid role for case {case.case_id}")
        _sha256(case.expected_raw_sha256, f"expected raw digest for {case.case_id}")
        _sha256(
            case.expected_session_audit_sha256,
            f"expected session audit digest for {case.case_id}",
        )

    schema_receipt = _read_small_file(
        schema_path,
        expected_sha256=expected_schema_sha256,
        context="raw schema",
    )
    plan_receipt = _read_small_file(
        probe_plan_path,
        expected_sha256=expected_probe_plan_sha256,
        context="probe plan",
    )
    candidate_receipt = _read_small_file(
        candidate_lock_path,
        expected_sha256=expected_candidate_lock_sha256,
        context="candidate lock",
    )
    assert schema_receipt.encoded is not None
    assert plan_receipt.encoded is not None
    assert candidate_receipt.encoded is not None
    schema = _parse_json(schema_receipt.encoded, context="raw schema")
    plan = _parse_json(plan_receipt.encoded, context="probe plan")
    candidate = _parse_json(candidate_receipt.encoded, context="candidate lock")
    _require(isinstance(schema, Mapping), "raw schema must be an object")
    _require(isinstance(plan, Mapping), "probe plan must be an object")
    _require(isinstance(candidate, Mapping), "candidate lock must be an object")

    _require(schema.get("$id") == expected_schema_id, "raw schema ID mismatch")
    hooks, _ = _plan_hooks(plan)
    _require(plan.get("plan_id") == expected_probe_plan_id, "probe plan ID mismatch")
    schema_id, bound_schema_sha = _bind_plan_to_schema(
        plan, schema, schema_receipt.encoded
    )
    _require(
        schema_id == expected_schema_id and bound_schema_sha == schema_receipt.sha256,
        "probe plan raw transport binding mismatch",
    )
    candidate_id = candidate.get("candidate_id")
    _require(candidate_id == expected_candidate_id, "candidate lock ID mismatch")
    binding = plan.get("binding")
    _require(isinstance(binding, Mapping), "probe plan lacks a candidate binding")
    _require(
        binding.get("candidate_id") == candidate_id
        and binding.get("candidate_lock_sha256") == candidate_receipt.sha256
        and binding.get("candidate_lock_path") == candidate_receipt.path.name,
        "probe plan candidate binding mismatch",
    )

    validator = _schema_validator(schema)
    record_types = _record_types(schema)
    nonce_field = _capture_nonce_field(schema)
    _validate_schema_surface(schema, record_types=record_types, nonce_field=nonce_field)
    properties = schema.get("properties")
    assert isinstance(properties, Mapping)
    format_declaration = properties.get("format")
    _require(isinstance(format_declaration, Mapping), "raw schema lacks a format declaration")
    raw_format = _nonempty(format_declaration.get("const"), "raw schema format")

    candidate_canonical_sha = canonical_json_sha256(candidate)
    case_results: list[dict[str, Any]] = []
    for case in sorted(cases, key=lambda item: item.case_id):
        session, _ = _session_audit(
            case,
            candidate_file_sha256=candidate_receipt.sha256,
            candidate_canonical_sha256=candidate_canonical_sha,
        )
        case_results.append(
            _stream_case(
                case,
                raw_format=raw_format,
                nonce_field=nonce_field,
                admitted_record_types=record_types,
                validator=validator,
                hooks=hooks,
                session=session,
            )
        )

    required_count = sum(
        result["case_role"] == REQUIRED_CASE_ROLE for result in case_results
    )
    _require(required_count > 0, "hook-health evaluation requires a required-complete case")
    material = {
        "schema": HOOK_HEALTH_EVALUATION_SCHEMA,
        "status": "passed",
        "claim_scope": (
            "all probe-plan hooks latest reached in every required-complete case; "
            "excluded-incomplete cases make no reachability claim"
        ),
        "validation_scope": {
            "all_rows": (
                "streamed exact bytes, strict JSON, closed envelope, contiguous global and "
                "thread sequences, stable capture/mode, complete detail, zero drops"
            ),
            "schema_validated_rows": ["capture_control", "probe_health"],
            "full_raw_admission": "not_claimed",
        },
        "candidate": {
            "candidate_id": candidate_id,
            "file_sha256": candidate_receipt.sha256,
            "canonical_sha256": candidate_canonical_sha,
        },
        "raw_contract": {
            "raw_format": raw_format,
            "schema_id": schema_id,
            "schema_file_sha256": schema_receipt.sha256,
            "probe_plan_id": plan["plan_id"],
            "probe_plan_file_sha256": plan_receipt.sha256,
            "declared_hook_count": len(hooks),
        },
        "required_case_count": required_count,
        "excluded_incomplete_case_count": len(case_results) - required_count,
        "cases": case_results,
    }
    return parse_cleanroom_hook_health_evaluation({
        "evaluation_id": HOOK_HEALTH_EVALUATION_PREFIX
        + canonical_json_sha256(material),
        **material,
    })


def parse_cleanroom_hook_health_evaluation(value: Any) -> dict[str, Any]:
    """Validate a decoded hook-health evaluation and all retained semantics."""

    evaluation = _exact_keys(
        value,
        {
            "evaluation_id", "schema", "status", "claim_scope",
            "validation_scope", "candidate", "raw_contract",
            "required_case_count", "excluded_incomplete_case_count", "cases",
        },
        "hook-health evaluation",
    )
    _require(
        evaluation["schema"] == HOOK_HEALTH_EVALUATION_SCHEMA,
        "hook-health evaluation schema mismatch",
    )
    material = deepcopy(dict(evaluation))
    evaluation_id = material.pop("evaluation_id")
    _require(
        isinstance(evaluation_id, str)
        and evaluation_id
        == HOOK_HEALTH_EVALUATION_PREFIX + canonical_json_sha256(material),
        "hook-health evaluation content identity mismatch",
    )
    _require(evaluation["status"] == "passed", "hook-health evaluation did not pass")
    _require(
        evaluation["claim_scope"]
        == (
            "all probe-plan hooks latest reached in every required-complete case; "
            "excluded-incomplete cases make no reachability claim"
        ),
        "hook-health claim scope is not exact V1",
    )
    _require(
        evaluation["validation_scope"]
        == {
            "all_rows": (
                "streamed exact bytes, strict JSON, closed envelope, contiguous global and "
                "thread sequences, stable capture/mode, complete detail, zero drops"
            ),
            "schema_validated_rows": ["capture_control", "probe_health"],
            "full_raw_admission": "not_claimed",
        },
        "hook-health validation scope is not exact V1",
    )

    candidate = _exact_keys(
        evaluation["candidate"],
        {"candidate_id", "file_sha256", "canonical_sha256"},
        "hook-health candidate",
    )
    _nonempty(candidate["candidate_id"], "hook-health candidate ID")
    _sha256(candidate["file_sha256"], "hook-health candidate file SHA-256")
    _sha256(candidate["canonical_sha256"], "hook-health candidate canonical SHA-256")

    raw_contract = _exact_keys(
        evaluation["raw_contract"],
        {
            "raw_format", "schema_id", "schema_file_sha256", "probe_plan_id",
            "probe_plan_file_sha256", "declared_hook_count",
        },
        "hook-health raw contract",
    )
    for field in ("raw_format", "schema_id", "probe_plan_id"):
        _nonempty(raw_contract[field], f"hook-health raw contract {field}")
    for field in ("schema_file_sha256", "probe_plan_file_sha256"):
        _sha256(raw_contract[field], f"hook-health raw contract {field}")
    declared_hook_count = _integer(
        raw_contract["declared_hook_count"],
        "hook-health raw contract declared hook count",
        minimum=1,
    )

    cases = evaluation["cases"]
    _require(isinstance(cases, list) and bool(cases), "hook-health cases must be nonempty")
    case_ids: list[str] = []
    required_count = 0
    excluded_count = 0
    for case_index, case_value in enumerate(cases):
        context = f"hook-health case {case_index}"
        case = _exact_keys(
            case_value,
            {"case_id", "case_role", "verdict", "raw_capture", "session_custody", "hook_health"},
            context,
        )
        case_id = _nonempty(case["case_id"], f"{context} ID")
        _require(_CASE_ID_RE.fullmatch(case_id) is not None, f"{context} ID is invalid")
        case_ids.append(case_id)
        role = case["case_role"]
        _require(role in _CASE_ROLES, f"{context} role is invalid")

        raw = _exact_keys(
            case["raw_capture"],
            {
                "file_sha256", "size_bytes", "row_count", "terminal_sequence",
                "capture_id", "requested_mode", "thread_count", "completion_state",
                "start_sequence", "stop_sequence",
            },
            f"{context} raw capture",
        )
        _sha256(raw["file_sha256"], f"{context} raw SHA-256")
        _integer(raw["size_bytes"], f"{context} raw byte count", minimum=1)
        row_count = _integer(raw["row_count"], f"{context} raw row count", minimum=1)
        terminal_sequence = _integer(raw["terminal_sequence"], f"{context} terminal sequence")
        _require(terminal_sequence == row_count - 1, f"{context} raw sequence count is stale")
        _nonempty(raw["capture_id"], f"{context} capture ID")
        _nonempty(raw["requested_mode"], f"{context} requested mode")
        _integer(raw["thread_count"], f"{context} thread count", minimum=1)
        _require(raw["start_sequence"] == 0, f"{context} does not start at sequence zero")

        custody = _exact_keys(
            case["session_custody"],
            {
                "audit_id", "file_sha256", "fixture_artifact_file_sha256",
                "runtime_class_source_sha256", "foundation_class_dump",
                "runtime_mod_inventory_sha256",
            },
            f"{context} session custody",
        )
        _require(
            isinstance(custody["audit_id"], str)
            and custody["audit_id"].startswith(SESSION_AUDIT_PREFIX),
            f"{context} session audit ID is invalid",
        )
        for field in ("file_sha256", "fixture_artifact_file_sha256", "runtime_class_source_sha256"):
            _sha256(custody[field], f"{context} session custody {field}")
        foundation = _exact_keys(
            custody["foundation_class_dump"],
            {"format", "class_count", "total_size_bytes", "manifest_sha256"},
            f"{context} Foundation custody",
        )
        _require(
            foundation["format"] == "workbench-foundation-class-dump-manifest-v1",
            f"{context} Foundation format is invalid",
        )
        _integer(foundation["class_count"], f"{context} Foundation class count", minimum=1)
        _integer(foundation["total_size_bytes"], f"{context} Foundation byte count", minimum=1)
        _sha256(foundation["manifest_sha256"], f"{context} Foundation manifest")

        health = _exact_keys(
            case["hook_health"],
            {
                "declared_hook_count", "observed_hook_count", "health_record_count",
                "missing_hook_ids", "latest_not_reached_hook_ids", "hooks",
            },
            f"{context} hook health",
        )
        _require(
            _integer(health["declared_hook_count"], f"{context} declared hooks", minimum=1)
            == declared_hook_count,
            f"{context} declared hook count drifted",
        )
        observed_count = _integer(health["observed_hook_count"], f"{context} observed hooks")
        health_record_count = _integer(health["health_record_count"], f"{context} health rows")
        hooks = health["hooks"]
        _require(isinstance(hooks, list), f"{context} hooks must be an array")
        _require(observed_count == len(hooks), f"{context} observed hook count is stale")
        _require(health_record_count >= observed_count, f"{context} health row count is stale")
        for field in ("missing_hook_ids", "latest_not_reached_hook_ids"):
            values = health[field]
            _require(
                isinstance(values, list)
                and all(isinstance(item, str) and bool(item) for item in values)
                and values == sorted(set(values)),
                f"{context} {field} is not a canonical string set",
            )
        hook_ids: list[str] = []
        for hook_index, hook_value in enumerate(hooks):
            hook_context = f"{context} hook {hook_index}"
            hook = _exact_keys(
                hook_value,
                {
                    "hook_id", "canonical_role", "target_class", "target_method",
                    "target_descriptor", "expected_injection_count",
                    "observed_injection_count", "original_class_sha256",
                    "transformed_class_sha256", "states", "first_sequence",
                    "latest_sequence", "latest_state",
                },
                hook_context,
            )
            hook_ids.append(_nonempty(hook["hook_id"], f"{hook_context} ID"))
            canonical_role = hook["canonical_role"]
            _require(
                canonical_role is None
                or (isinstance(canonical_role, str) and bool(canonical_role)),
                f"{hook_context} canonical_role must be null or nonempty",
            )
            for field in ("target_class", "target_method", "target_descriptor"):
                _nonempty(hook[field], f"{hook_context} {field}")
            expected_count = _integer(
                hook["expected_injection_count"], f"{hook_context} expected injection count"
            )
            _require(
                _integer(hook["observed_injection_count"], f"{hook_context} observed injection count")
                == expected_count,
                f"{hook_context} injection cardinality differs",
            )
            original = _sha256(hook["original_class_sha256"], f"{hook_context} original class")
            transformed = _sha256(hook["transformed_class_sha256"], f"{hook_context} transformed class")
            _require(
                original != _ZERO_SHA256 and transformed != _ZERO_SHA256 and original != transformed,
                f"{hook_context} lacks a transformed class identity",
            )
            states = hook["states"]
            _require(
                states in (["reached"], ["applied_not_reached"], ["applied_not_reached", "reached"]),
                f"{hook_context} state progression is invalid",
            )
            first_sequence = _integer(hook["first_sequence"], f"{hook_context} first sequence")
            latest_sequence = _integer(hook["latest_sequence"], f"{hook_context} latest sequence")
            _require(first_sequence <= latest_sequence, f"{hook_context} sequences regress")
            _require(hook["latest_state"] == states[-1], f"{hook_context} latest state is stale")
        _require(hook_ids == sorted(set(hook_ids)), f"{context} hook IDs are not canonical and unique")

        if role == REQUIRED_CASE_ROLE:
            required_count += 1
            _require(
                case["verdict"] == "all_declared_hooks_reached"
                and raw["completion_state"] == "complete"
                and raw["stop_sequence"] == terminal_sequence
                and custody["runtime_mod_inventory_sha256"] is not None
                and observed_count == declared_hook_count
                and health["missing_hook_ids"] == []
                and health["latest_not_reached_hook_ids"] == []
                and all(hook["latest_state"] == "reached" for hook in hooks),
                f"{context} does not close every declared hook in a completed capture",
            )
            _sha256(custody["runtime_mod_inventory_sha256"], f"{context} runtime inventory")
        else:
            excluded_count += 1
            _require(
                case["verdict"] == "excluded_incomplete_case"
                and raw["completion_state"] == "incomplete"
                and raw["stop_sequence"] is None
                and custody["runtime_mod_inventory_sha256"] is None,
                f"{context} excluded case is not explicit incomplete residue",
            )

    _require(case_ids == sorted(set(case_ids)), "hook-health case IDs are not canonical and unique")
    _require(required_count > 0, "hook-health evaluation lacks a required-complete case")
    _require(
        evaluation["required_case_count"] == required_count
        and evaluation["excluded_incomplete_case_count"] == excluded_count,
        "hook-health case-role counts are stale",
    )
    return deepcopy(dict(evaluation))


def write_cleanroom_hook_health_evaluation(
    path: str | os.PathLike[str], value: Mapping[str, Any]
) -> None:
    """Atomically write a freshly evaluated content-addressed receipt."""

    value = parse_cleanroom_hook_health_evaluation(value)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "CleanroomHookHealthCase",
    "EXCLUDED_INCOMPLETE_CASE_ROLE",
    "HOOK_HEALTH_EVALUATION_PREFIX",
    "HOOK_HEALTH_EVALUATION_SCHEMA",
    "REQUIRED_CASE_ROLE",
    "evaluate_cleanroom_hook_health",
    "parse_cleanroom_hook_health_evaluation",
    "write_cleanroom_hook_health_evaluation",
]
