"""Mixin configuration admission, ownership, and phase receipt V1."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


RAW_CONFIG_LIFECYCLE_FORMAT = "workbench-cleanmix-config-lifecycle-raw-v1"
CONFIG_LIFECYCLE_RECEIPT_FORMAT = (
    "workbench-crucible-mixin-config-lifecycle-receipt-v1"
)
CONFIG_LIFECYCLE_RECEIPT_PREFIX = "crucible-mixin-config-lifecycle:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_RAW_KEYS = frozenset({"format", "capture_id", "sequence", "event", "payload"})
_EVENTS = frozenset(
    {
        "capture_start",
        "transformer_installed",
        "transform_applied",
        "transform_rejected",
        "transform_failure",
        "config_create_started",
        "resource_located",
        "feature_check_started",
        "feature_check_completed",
        "config_create_completed",
        "registration_started",
        "admission_decision",
        "phase_pass_started",
        "phase_pass_completed",
        "phase_eligibility",
        "config_stage",
        "batch_promotion",
        "terminal_state",
        "observer_failure",
        "capture_end",
    }
)
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_SHA256 = frozenset("0123456789abcdef")
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
_RESOURCE_EVIDENCE_KEYS = frozenset(
    {"attempt", "resource_url", "artifact_sha256", "resource_entry_sha256"}
)
_STAGES = ("onSelect", "prepare", "post_initialise")
_TERMINALS = frozenset({"active", "deferred", "duplicate", "rejected", "threw"})

MANDATORY_LIMITATIONS = (
    "The receipt proves configuration lifecycle events only for the bound launch and instrumented CleanMix seams.",
    "A derived resource URL is accepted only when it joins the observed config source description to the exact requested archive entry and the importer verifies both bytes.",
    "A phase-not-reached decision is a nonterminal deferral; only the terminal state says whether the config later became active.",
    "Active configuration promotion does not prove that every declared mixin transformed a target or that final class bytes were defined.",
)


class ConfigLifecycleValidationError(ValueError):
    """Configuration lifecycle evidence is malformed or overclaims."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigLifecycleValidationError(message)


def canonical_config_lifecycle_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigLifecycleValidationError(
            f"cannot canonically encode config lifecycle evidence: {exc}"
        ) from exc


def _closed(value: Mapping[str, Any], expected: frozenset[str], context: str) -> None:
    missing = expected - set(value)
    unknown = set(value) - expected
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


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256,
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


def _strict_ndjson(encoded: bytes) -> list[dict[str, Any]]:
    _require(isinstance(encoded, bytes) and bool(encoded), "raw lifecycle trace is empty")
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise ConfigLifecycleValidationError(
            f"raw lifecycle trace is not UTF-8: {exc}"
        ) from exc
    _require(text.endswith("\n"), "raw lifecycle trace lacks a terminal newline")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        _require(bool(line), f"raw lifecycle trace line {index + 1} is empty")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_json_object,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite number {item}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
            raise ConfigLifecycleValidationError(
                f"raw lifecycle trace line {index + 1} is malformed: {exc}"
            ) from exc
        _require(isinstance(value, Mapping), f"raw lifecycle line {index + 1} is not an object")
        _closed(value, _RAW_KEYS, f"raw lifecycle line {index + 1}")
        _require(value["format"] == RAW_CONFIG_LIFECYCLE_FORMAT, "raw lifecycle format is not V1")
        _text(value["capture_id"], f"raw lifecycle line {index + 1}.capture_id")
        _require(value["sequence"] == index, "raw lifecycle sequences are not contiguous from zero")
        _require(value["event"] in _EVENTS, "raw lifecycle event is unsupported")
        _require(isinstance(value["payload"], Mapping), "raw lifecycle payload must be an object")
        rows.append(deepcopy(dict(value)))
    return rows


def _attempt(value: Any, context: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    return _integer(value, context, minimum=1)


def _validate_feature_rows(value: Any, context: str) -> None:
    _require(isinstance(value, list), f"{context} must be an array")
    seen: set[str] = set()
    for index, row in enumerate(value):
        item_context = f"{context}[{index}]"
        _require(isinstance(row, Mapping), f"{item_context} must be an object")
        _closed(row, frozenset({"id", "normalized_id", "active"}), item_context)
        feature_id = _text(row["id"], f"{item_context}.id")
        _text(row["normalized_id"], f"{item_context}.normalized_id")
        _require(type(row["active"]) is bool, f"{item_context}.active must be boolean")
        _require(feature_id not in seen, f"{context} repeats feature {feature_id}")
        seen.add(feature_id)


def _validate_payload(row: Mapping[str, Any]) -> None:
    event = row["event"]
    payload = row["payload"]
    sequence = row["sequence"]
    context = f"{event} payload at sequence {sequence}"
    payload_keys: dict[str, frozenset[str]] = {
        "capture_start": frozenset(
            {"agent_id", "java_version", "targets", "redefine_supported", "retransform_supported"}
        ),
        "transformer_installed": frozenset({"target_count", "retransform_requested"}),
        "transform_applied": frozenset(
            {"target_class", "defining_loader_class", "defining_loader_identity", "target_code_source_uri", "input_sha256", "output_sha256", "reason"}
        ),
        "transform_rejected": frozenset(
            {"target_class", "defining_loader_class", "defining_loader_identity", "target_code_source_uri", "input_sha256", "output_sha256", "reason"}
        ),
        "transform_failure": frozenset(
            {"target_class", "defining_loader_class", "defining_loader_identity", "target_code_source_uri", "input_sha256", "output_sha256", "reason", "exception_class", "message", "stack_top"}
        ),
        "config_create_started": frozenset(
            {"attempt", "requested_config", "fallback_phase", "source_id", "source_description", "resolved_resource_url", "resource_url_basis"}
        ),
        "resource_located": frozenset(
            {"attempt", "requested_config", "resource_url", "resource_url_basis"}
        ),
        "feature_check_started": frozenset(
            {"attempt", "config_name", "required", "required_features", "outcome"}
        ),
        "feature_check_completed": frozenset(
            {"attempt", "config_name", "required", "required_features", "outcome"}
        ),
        "config_create_completed": frozenset(
            {"attempt", "requested_config", "config_name", "config_identity", "resource_url", "resource_url_basis", "required", "queued_phase", "source_id", "source_description", "outcome"}
        ),
        "registration_started": frozenset(
            {"attempt", "requested_config", "config_name", "config_identity", "previously_admitted"}
        ),
        "admission_decision": frozenset(
            {"attempt", "requested_config", "config_name", "config_identity", "resource_url", "resource_url_basis", "required", "queued_phase", "source_id", "source_description", "decision", "reason_code"}
        ),
        "phase_pass_started": frozenset({"consumption_phase", "pending_count"}),
        "phase_pass_completed": frozenset({"consumption_phase", "outcome", "pending_count"}),
        "phase_eligibility": frozenset(
            {"attempt", "config_name", "config_identity", "queued_phase", "consumption_phase", "eligible", "outcome", "reason_code"}
        ),
        "config_stage": frozenset(
            {"attempt", "config_name", "config_identity", "stage", "outcome", "consumption_phase"}
        ),
        "batch_promotion": frozenset(
            {"attempt", "config_name", "config_identity", "consumption_phase", "outcome", "reason_code"}
        ),
        "terminal_state": frozenset(
            {"attempt", "requested_config", "config_name", "outcome", "reason_code"}
        ),
        "observer_failure": frozenset(
            {"operation", "exception_class", "message", "stack_top"}
        ),
        "capture_end": frozenset(
            {"health", "transformed_targets", "attempt_count", "admitted_count", "active_count", "deferred_count", "failure_count", "observer_failure_count", "write_failure"}
        ),
    }
    expected = payload_keys[event]
    if event in {"feature_check_completed", "config_stage", "batch_promotion", "phase_pass_completed", "admission_decision", "config_create_completed"} and "exception_class" in payload:
        expected |= frozenset({"exception_class", "message", "stack_top"})
    _closed(payload, expected, context)
    if "attempt" in payload:
        _attempt(payload["attempt"], f"{context}.attempt", nullable=event in {"resource_located"})
    if event == "capture_start":
        _text(payload["agent_id"], f"{context}.agent_id")
        _text(payload["java_version"], f"{context}.java_version")
        _require(isinstance(payload["targets"], list) and payload["targets"], f"{context}.targets must be nonempty")
        for index, target in enumerate(payload["targets"]):
            _closed(target, frozenset({"target_class", "expected_input_sha256"}), f"{context}.targets[{index}]")
            _text(target["target_class"], f"{context}.targets[{index}].target_class")
            _sha256(target["expected_input_sha256"], f"{context}.targets[{index}].expected_input_sha256")
    elif event.startswith("transform_") and event != "transformer_installed":
        _text(payload["target_class"], f"{context}.target_class")
        _sha256(payload["input_sha256"], f"{context}.input_sha256")
        if payload["output_sha256"] is not None:
            _sha256(payload["output_sha256"], f"{context}.output_sha256")
    elif event in {"feature_check_started", "feature_check_completed"}:
        _validate_feature_rows(payload["required_features"], f"{context}.required_features")
        _require(payload["required"] is None or type(payload["required"]) is bool, f"{context}.required must be boolean or null")
    elif event == "phase_eligibility":
        _require(type(payload["eligible"]) is bool, f"{context}.eligible must be boolean")
        _require(
            (payload["eligible"] and payload["outcome"] == "consumed")
            or (not payload["eligible"] and payload["outcome"] == "deferred"),
            f"{context} eligibility and outcome disagree",
        )
    elif event == "capture_end":
        _require(
            isinstance(payload["transformed_targets"], list)
            and all(isinstance(item, str) and item for item in payload["transformed_targets"]),
            f"{context}.transformed_targets must be a class-name array",
        )


def parse_raw_config_lifecycle(encoded: bytes) -> list[dict[str, Any]]:
    """Parse and semantically validate one exact lifecycle event stream."""

    rows = _strict_ndjson(encoded)
    _require(len(rows) >= 8, "raw lifecycle trace is too short")
    _require(rows[0]["event"] == "capture_start", "raw lifecycle trace does not start with capture_start")
    _require(rows[-1]["event"] == "capture_end", "raw lifecycle trace does not end with capture_end")
    _require(len({row["capture_id"] for row in rows}) == 1, "raw lifecycle trace crosses capture identities")
    for row in rows:
        _validate_payload(row)

    counts = Counter(row["event"] for row in rows)
    _require(counts["capture_start"] == counts["capture_end"] == 1, "raw lifecycle trace repeats terminal events")
    _require(counts["transformer_installed"] == 1, "raw lifecycle trace lacks one transformer installation")
    start_targets = {
        target["target_class"]: target["expected_input_sha256"]
        for target in rows[0]["payload"]["targets"]
    }
    _require(len(start_targets) == len(rows[0]["payload"]["targets"]), "capture start repeats a transform target")
    transforms = [row["payload"] for row in rows if row["event"] == "transform_applied"]
    _require(len(transforms) == len(start_targets), "raw lifecycle trace lacks an exact transform target")
    _require(
        {row["target_class"] for row in transforms} == set(start_targets),
        "applied lifecycle transform target set is stale",
    )
    for transform in transforms:
        _require(
            transform["input_sha256"] == start_targets[transform["target_class"]],
            f"transform input for {transform['target_class']} bypasses its exact guard",
        )

    starts = {
        row["payload"]["attempt"]: row
        for row in rows
        if row["event"] == "config_create_started"
    }
    _require(len(starts) == counts["config_create_started"], "raw lifecycle repeats a config attempt")
    completed_creates = {
        row["payload"]["attempt"]: row
        for row in rows
        if row["event"] == "config_create_completed"
    }
    _require(
        set(starts) == set(completed_creates),
        "raw lifecycle attempts lack one config creation outcome each",
    )
    _require(
        len(completed_creates) == counts["config_create_completed"],
        "raw lifecycle repeats a config creation outcome",
    )
    terminals = {
        row["payload"]["attempt"]: row
        for row in rows
        if row["event"] == "terminal_state"
    }
    _require(set(starts) == set(terminals), "raw lifecycle attempts lack one terminal state each")
    decisions = {
        row["payload"]["attempt"]: row
        for row in rows
        if row["event"] == "admission_decision"
    }
    _require(set(starts) == set(decisions), "raw lifecycle attempts lack one admission decision each")
    _require(len(decisions) == counts["admission_decision"], "raw lifecycle repeats an admission decision")

    for attempt, terminal_row in terminals.items():
        terminal = terminal_row["payload"]
        decision = decisions[attempt]["payload"]
        _require(terminal["outcome"] in _TERMINALS, f"attempt {attempt} has unsupported terminal outcome")
        if terminal["outcome"] == "active":
            checks = [
                row["payload"]
                for row in rows
                if row["event"] == "phase_eligibility"
                and row["payload"]["attempt"] == attempt
            ]
            _require(any(row["eligible"] for row in checks), f"active attempt {attempt} was never phase-eligible")
            completed = {
                row["payload"]["stage"]
                for row in rows
                if row["event"] == "config_stage"
                and row["payload"]["attempt"] == attempt
                and row["payload"]["outcome"] == "completed"
            }
            _require(completed == set(_STAGES), f"active attempt {attempt} lacks a completed preparation stage")
            _require(
                any(
                    row["event"] == "batch_promotion"
                    and row["payload"]["attempt"] == attempt
                    and row["payload"]["outcome"] == "active"
                    for row in rows
                ),
                f"active attempt {attempt} lacks batch promotion",
            )
        if decision["decision"] == "duplicate":
            _require(terminal["outcome"] == "duplicate", f"duplicate attempt {attempt} has inconsistent terminal state")

    footer = rows[-1]["payload"]
    _require(footer["health"] in {"healthy", "failed"}, "capture_end health is unsupported")
    _require(footer["attempt_count"] == len(starts), "capture_end attempt count is stale")
    _require(
        sorted(item.replace("/", ".") for item in footer["transformed_targets"])
        == sorted(start_targets),
        "capture_end transformed target set is stale",
    )
    terminal_counts = Counter(row["payload"]["outcome"] for row in terminals.values())
    _require(footer["active_count"] == terminal_counts["active"], "capture_end active count is stale")
    _require(footer["deferred_count"] == terminal_counts["deferred"], "capture_end deferred count is stale")
    _require(
        footer["admitted_count"]
        == sum(row["payload"]["decision"] == "admitted" for row in decisions.values()),
        "capture_end admitted count is stale",
    )
    if footer["health"] == "healthy":
        _require(
            not any(row["event"] in {"transform_rejected", "transform_failure", "observer_failure"} for row in rows),
            "healthy lifecycle trace reports observer or transform failure",
        )
        _require(not footer["write_failure"], "healthy lifecycle trace reports write failure")
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
    _require(len(result) == len({row["code_source_uri"] for row in result}), "artifacts repeat a code_source_uri")
    return result


def _resource_evidence(value: Any) -> dict[int, dict[str, Any]]:
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)), "resource_evidence must be an array")
    result: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(value):
        context = f"resource_evidence[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, _RESOURCE_EVIDENCE_KEYS, context)
        attempt = _attempt(row["attempt"], f"{context}.attempt")
        assert attempt is not None
        _require(attempt not in result, f"resource_evidence repeats attempt {attempt}")
        result[attempt] = {
            "resource_url": _text(row["resource_url"], f"{context}.resource_url"),
            "artifact_sha256": _sha256(row["artifact_sha256"], f"{context}.artifact_sha256"),
            "resource_entry_sha256": _sha256(row["resource_entry_sha256"], f"{context}.resource_entry_sha256"),
        }
    return result


def _project_configurations(
    rows: Sequence[Mapping[str, Any]],
    evidence: Mapping[int, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_attempt: dict[int, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for row in rows:
        event = row["event"]
        payload = row["payload"]
        attempt = payload.get("attempt")
        if event == "config_create_started":
            by_attempt[attempt] = {
                "attempt": attempt,
                "requested_config": payload["requested_config"],
                "fallback_phase": payload["fallback_phase"],
                "resource": {
                    "url": payload["resolved_resource_url"],
                    "url_basis": payload["resource_url_basis"],
                    "owner_id": payload["source_id"],
                    "owner_description": payload["source_description"],
                    "artifact_sha256": None,
                    "entry_sha256": None,
                },
                "feature_check": {"state": "not_run_cached", "outcome": None, "features": []},
                "admission": None,
                "phase_checks": [],
                "stages": [],
                "terminal": None,
                "sequence_start": row["sequence"],
                "sequence_end": None,
            }
        elif event == "feature_check_completed":
            config = by_attempt[attempt]
            config["feature_check"] = {
                "state": "observed",
                "outcome": payload["outcome"],
                "features": deepcopy(payload["required_features"]),
            }
        elif event == "config_create_completed":
            config = by_attempt[attempt]
            config["config_name"] = payload["config_name"]
            config["create_outcome"] = payload["outcome"]
            resource = config["resource"]
            for target, source in (
                ("url", "resource_url"),
                ("url_basis", "resource_url_basis"),
                ("owner_id", "source_id"),
                ("owner_description", "source_description"),
            ):
                if payload[source] is not None:
                    resource[target] = payload[source]
        elif event == "admission_decision":
            config = by_attempt[attempt]
            config["admission"] = {
                "decision": payload["decision"],
                "reason_code": payload["reason_code"],
                "required": payload["required"],
                "queued_phase": payload["queued_phase"],
            }
        elif event == "phase_eligibility":
            by_attempt[attempt]["phase_checks"].append(
                {
                    "sequence": row["sequence"],
                    "queued_phase": payload["queued_phase"],
                    "consumption_phase": payload["consumption_phase"],
                    "eligible": payload["eligible"],
                    "outcome": payload["outcome"],
                    "reason_code": payload["reason_code"],
                }
            )
        elif event == "config_stage" and payload["outcome"] != "started":
            by_attempt[attempt]["stages"].append(
                {
                    "sequence": row["sequence"],
                    "stage": payload["stage"],
                    "outcome": payload["outcome"],
                    "consumption_phase": payload["consumption_phase"],
                }
            )
        elif event == "terminal_state":
            config = by_attempt[attempt]
            config["terminal"] = {
                "outcome": payload["outcome"],
                "reason_code": payload["reason_code"],
            }
            config["sequence_end"] = row["sequence"]
        if event in {"transform_rejected", "transform_failure", "observer_failure"} or payload.get("outcome") == "threw":
            failure = {
                "sequence": row["sequence"],
                "event": event,
                "attempt": attempt,
                "reason_code": payload.get("reason_code"),
                "exception_class": payload.get("exception_class"),
                "message": payload.get("message"),
            }
            failures.append(failure)

    for attempt, binding in evidence.items():
        _require(attempt in by_attempt, f"resource evidence references unknown attempt {attempt}")
        resource = by_attempt[attempt]["resource"]
        _require(resource["url"] == binding["resource_url"], f"attempt {attempt} resource URL and verified evidence disagree")
        resource["artifact_sha256"] = binding["artifact_sha256"]
        resource["entry_sha256"] = binding["resource_entry_sha256"]
    return [by_attempt[key] for key in sorted(by_attempt)], failures


def build_config_lifecycle_receipt(
    *,
    session: Mapping[str, Any],
    inputs: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    resource_evidence: Sequence[Mapping[str, Any]],
    raw_events: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    normalized_session = _session(session)
    _require(isinstance(inputs, Mapping) and set(inputs) == set(_INPUT_NAMES), "inputs are not the exact V1 input set")
    normalized_inputs = {name: _file_record(inputs[name], f"inputs.{name}") for name in _INPUT_NAMES}
    _require(normalized_inputs["candidate_lock"]["sha256"] == normalized_session["candidate_lock_sha256"], "candidate lock input and session disagree")
    _require(normalized_inputs["toolchain_lock"]["sha256"] == normalized_session["toolchain_lock_sha256"], "toolchain lock input and session disagree")
    rows = deepcopy(list(raw_events))
    _require(bool(rows), "raw_events must be nonempty")
    _require({row.get("capture_id") for row in rows} == {normalized_session["capture_id"]}, "raw events and session capture identity disagree")
    normalized_artifacts = _artifacts(artifacts)
    evidence = _resource_evidence(resource_evidence)
    configurations, failures = _project_configurations(rows, evidence)
    artifact_hashes = {row["artifact_sha256"] for row in normalized_artifacts}
    _require(all(row["artifact_sha256"] in artifact_hashes for row in evidence.values()), "resource evidence artifact is absent from artifacts")

    start = rows[0]["payload"]
    transforms = [row["payload"] for row in rows if row["event"] == "transform_applied"]
    artifact_by_uri = {row["code_source_uri"]: row["artifact_sha256"] for row in normalized_artifacts}
    targets = []
    expected_by_class = {row["target_class"]: row["expected_input_sha256"] for row in start["targets"]}
    for transform in sorted(transforms, key=lambda row: row["target_class"]):
        artifact_sha = artifact_by_uri.get(transform["target_code_source_uri"])
        _require(artifact_sha is not None, f"transform target {transform['target_class']} code source is absent from artifacts")
        targets.append(
            {
                "target_class": transform["target_class"],
                "expected_input_sha256": expected_by_class[transform["target_class"]],
                "observed_input_sha256": transform["input_sha256"],
                "instrumented_output_sha256": transform["output_sha256"],
                "defining_loader_class": transform["defining_loader_class"],
                "code_source_uri": transform["target_code_source_uri"],
                "artifact_sha256": artifact_sha,
            }
        )
    footer = rows[-1]["payload"]
    state = "complete" if footer["health"] == "healthy" else "failed"
    terminal_counts = Counter(row["terminal"]["outcome"] for row in configurations)
    normalized_limitations = sorted(
        set(MANDATORY_LIMITATIONS)
        | {_text(value, f"limitations[{index}]") for index, value in enumerate(limitations)}
    )
    material = {
        "format": CONFIG_LIFECYCLE_RECEIPT_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": normalized_session,
        "inputs": normalized_inputs,
        "artifacts": normalized_artifacts,
        "observation": {
            "mechanism": "java_instrument_exact_cleanmix_config_lifecycle_weave",
            "agent_id": start["agent_id"],
            "java_version": start["java_version"],
            "targets": targets,
        },
        "configurations": configurations,
        "failures": failures,
        "health": {
            "state": state,
            "end_health": footer["health"],
            "write_failure": footer["write_failure"],
            "observer_failure_count": footer["observer_failure_count"],
        },
        "summary": {
            "attempt_count": len(configurations),
            "admitted_count": sum(row["admission"]["decision"] == "admitted" for row in configurations),
            "terminal_outcomes": {key: terminal_counts[key] for key in sorted(terminal_counts)},
            "verified_resource_count": len(evidence),
            "failure_count": len(failures),
        },
        "limitations": normalized_limitations,
        "boundaries": {
            "configuration_lifecycle_proved": state == "complete",
            "transformer_order_proved": False,
            "mixin_transformation_completion_proved": False,
            "final_class_bytes_proved": False,
        },
    }
    receipt_id = CONFIG_LIFECYCLE_RECEIPT_PREFIX + hashlib.sha256(
        canonical_config_lifecycle_json_bytes(material)
    ).hexdigest()
    return {**material, "receipt_id": receipt_id}


def parse_config_lifecycle_receipt(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "config lifecycle receipt must be an object")
    expected = frozenset(
        {
            "format", "schema_version", "canonicalization_id", "receipt_id",
            "session", "inputs", "artifacts", "observation", "configurations",
            "failures", "health", "summary", "limitations", "boundaries",
        }
    )
    _closed(value, expected, "config lifecycle receipt")
    _require(value["format"] == CONFIG_LIFECYCLE_RECEIPT_FORMAT, "config lifecycle receipt format is not V1")
    _require(value["schema_version"] == 1, "config lifecycle receipt schema version is not 1")
    _require(value["canonicalization_id"] == CANONICALIZATION_ID, "config lifecycle canonicalization is unsupported")
    supplied = deepcopy(dict(value))
    receipt_id = supplied.pop("receipt_id")
    expected_id = CONFIG_LIFECYCLE_RECEIPT_PREFIX + hashlib.sha256(
        canonical_config_lifecycle_json_bytes(supplied)
    ).hexdigest()
    _require(receipt_id == expected_id, "config lifecycle receipt identity mismatch")
    normalized_session = _session(value["session"])
    _require(set(value["inputs"]) == set(_INPUT_NAMES), "config lifecycle receipt inputs are incomplete")
    normalized_inputs = {}
    for name in _INPUT_NAMES:
        normalized_inputs[name] = _file_record(value["inputs"][name], f"inputs.{name}")
    _require(
        normalized_inputs["candidate_lock"]["sha256"]
        == normalized_session["candidate_lock_sha256"],
        "config lifecycle candidate lock input and session disagree",
    )
    _require(
        normalized_inputs["toolchain_lock"]["sha256"]
        == normalized_session["toolchain_lock_sha256"],
        "config lifecycle toolchain lock input and session disagree",
    )
    artifacts = _artifacts(value["artifacts"])
    _require(artifacts == value["artifacts"], "config lifecycle artifacts are not canonical")
    artifact_hashes = {row["artifact_sha256"] for row in artifacts}

    observation = value["observation"]
    _require(isinstance(observation, Mapping), "config lifecycle observation must be an object")
    _closed(
        observation,
        frozenset({"mechanism", "agent_id", "java_version", "targets"}),
        "config lifecycle observation",
    )
    _require(
        observation["mechanism"]
        == "java_instrument_exact_cleanmix_config_lifecycle_weave",
        "config lifecycle observation mechanism is unsupported",
    )
    _text(observation["agent_id"], "observation.agent_id")
    _text(observation["java_version"], "observation.java_version")
    _require(isinstance(observation["targets"], list) and observation["targets"], "config lifecycle observation lacks targets")
    target_classes: list[str] = []
    for index, target in enumerate(observation["targets"]):
        context = f"observation.targets[{index}]"
        _require(isinstance(target, Mapping), f"{context} must be an object")
        _closed(
            target,
            frozenset(
                {
                    "target_class", "expected_input_sha256", "observed_input_sha256",
                    "instrumented_output_sha256", "defining_loader_class",
                    "code_source_uri", "artifact_sha256",
                }
            ),
            context,
        )
        target_classes.append(_text(target["target_class"], f"{context}.target_class"))
        expected_sha = _sha256(target["expected_input_sha256"], f"{context}.expected_input_sha256")
        _require(
            target["observed_input_sha256"] == expected_sha,
            f"{context} bypasses its exact input guard",
        )
        _sha256(target["instrumented_output_sha256"], f"{context}.instrumented_output_sha256")
        _text(target["defining_loader_class"], f"{context}.defining_loader_class")
        _text(target["code_source_uri"], f"{context}.code_source_uri")
        _require(
            _sha256(target["artifact_sha256"], f"{context}.artifact_sha256")
            in artifact_hashes,
            f"{context} references an absent artifact",
        )
    _require(target_classes == sorted(set(target_classes)), "config lifecycle targets are not unique canonical class order")

    _require(isinstance(value["configurations"], list) and value["configurations"], "config lifecycle receipt lacks configurations")
    attempts = [row.get("attempt") for row in value["configurations"] if isinstance(row, Mapping)]
    _require(attempts == sorted(set(attempts)), "config lifecycle configurations are not unique attempt order")
    terminal_counts: Counter[str] = Counter()
    admitted_count = 0
    verified_resource_count = 0
    for index, row in enumerate(value["configurations"]):
        context = f"configurations[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(
            row,
            frozenset(
                {"attempt", "requested_config", "fallback_phase", "resource", "feature_check", "admission", "phase_checks", "stages", "terminal", "sequence_start", "sequence_end", "config_name", "create_outcome"}
            ),
            context,
        )
        _attempt(row["attempt"], f"{context}.attempt")
        _text(row["requested_config"], f"{context}.requested_config")
        _text(row["fallback_phase"], f"{context}.fallback_phase")
        _text(row["config_name"], f"{context}.config_name", nullable=True)
        _text(row["create_outcome"], f"{context}.create_outcome")
        sequence_start = _integer(row["sequence_start"], f"{context}.sequence_start")
        sequence_end = _integer(row["sequence_end"], f"{context}.sequence_end")
        _require(sequence_start < sequence_end, f"{context} sequence bounds are inverted")

        resource = row["resource"]
        _require(isinstance(resource, Mapping), f"{context}.resource must be an object")
        _closed(resource, frozenset({"url", "url_basis", "owner_id", "owner_description", "artifact_sha256", "entry_sha256"}), f"{context}.resource")
        for key in ("url", "url_basis", "owner_id", "owner_description"):
            _text(resource[key], f"{context}.resource.{key}", nullable=True)
        for key in ("artifact_sha256", "entry_sha256"):
            if resource[key] is not None:
                _sha256(resource[key], f"{context}.resource.{key}")
        _require(
            (resource["artifact_sha256"] is None) == (resource["entry_sha256"] is None),
            f"{context}.resource verification hashes must be paired",
        )
        if resource["artifact_sha256"] is not None:
            verified_resource_count += 1
            _require(resource["url"] is not None, f"{context}.resource verification lacks a URL")
            _require(resource["artifact_sha256"] in artifact_hashes, f"{context}.resource references an absent artifact")

        feature_check = row["feature_check"]
        _require(isinstance(feature_check, Mapping), f"{context}.feature_check must be an object")
        _closed(feature_check, frozenset({"state", "outcome", "features"}), f"{context}.feature_check")
        _require(feature_check["state"] in {"observed", "not_run_cached"}, f"{context}.feature_check state is unsupported")
        _require(feature_check["outcome"] in {None, "passed", "failed", "threw"}, f"{context}.feature_check outcome is unsupported")
        _validate_feature_rows(feature_check["features"], f"{context}.feature_check.features")

        admission = row["admission"]
        _require(isinstance(admission, Mapping), f"{context}.admission must be an object")
        _closed(admission, frozenset({"decision", "reason_code", "required", "queued_phase"}), f"{context}.admission")
        _require(admission["decision"] in {"admitted", "duplicate", "rejected", "threw"}, f"{context}.admission decision is unsupported")
        _text(admission["reason_code"], f"{context}.admission.reason_code")
        _require(admission["required"] is None or type(admission["required"]) is bool, f"{context}.admission.required must be boolean or null")
        _text(admission["queued_phase"], f"{context}.admission.queued_phase", nullable=True)
        admitted_count += admission["decision"] == "admitted"

        _require(isinstance(row["phase_checks"], list), f"{context}.phase_checks must be an array")
        phase_sequences: list[int] = []
        for phase_index, check in enumerate(row["phase_checks"]):
            phase_context = f"{context}.phase_checks[{phase_index}]"
            _require(isinstance(check, Mapping), f"{phase_context} must be an object")
            _closed(check, frozenset({"sequence", "queued_phase", "consumption_phase", "eligible", "outcome", "reason_code"}), phase_context)
            phase_sequences.append(_integer(check["sequence"], f"{phase_context}.sequence"))
            _text(check["queued_phase"], f"{phase_context}.queued_phase")
            _text(check["consumption_phase"], f"{phase_context}.consumption_phase")
            _require(type(check["eligible"]) is bool, f"{phase_context}.eligible must be boolean")
            _require((check["eligible"] and check["outcome"] == "consumed") or (not check["eligible"] and check["outcome"] == "deferred"), f"{phase_context} eligibility and outcome disagree")
            _text(check["reason_code"], f"{phase_context}.reason_code")
        _require(phase_sequences == sorted(set(phase_sequences)), f"{context}.phase_checks are not unique sequence order")

        _require(isinstance(row["stages"], list), f"{context}.stages must be an array")
        stage_sequences: list[int] = []
        for stage_index, stage in enumerate(row["stages"]):
            stage_context = f"{context}.stages[{stage_index}]"
            _require(isinstance(stage, Mapping), f"{stage_context} must be an object")
            _closed(stage, frozenset({"sequence", "stage", "outcome", "consumption_phase"}), stage_context)
            stage_sequences.append(_integer(stage["sequence"], f"{stage_context}.sequence"))
            _require(stage["stage"] in _STAGES, f"{stage_context}.stage is unsupported")
            _require(stage["outcome"] in {"completed", "threw"}, f"{stage_context}.outcome is unsupported")
            _text(stage["consumption_phase"], f"{stage_context}.consumption_phase", nullable=True)
        _require(stage_sequences == sorted(set(stage_sequences)), f"{context}.stages are not unique sequence order")

        terminal = row["terminal"]
        _require(isinstance(terminal, Mapping), f"{context}.terminal must be an object")
        _closed(terminal, frozenset({"outcome", "reason_code"}), f"{context}.terminal")
        _require(terminal["outcome"] in _TERMINALS, f"{context}.terminal outcome is unsupported")
        _text(terminal["reason_code"], f"{context}.terminal.reason_code")
        terminal_counts[terminal["outcome"]] += 1
        if terminal["outcome"] == "active":
            _require(admission["decision"] == "admitted", f"{context} is active without admission")
            _require(any(check["eligible"] for check in row["phase_checks"]), f"{context} is active without eligibility")
            _require({stage["stage"] for stage in row["stages"] if stage["outcome"] == "completed"} == set(_STAGES), f"{context} is active without complete stages")
        if terminal["outcome"] == "duplicate":
            _require(admission["decision"] == "duplicate", f"{context} duplicate terminal disagrees with admission")

    failures = value["failures"]
    _require(isinstance(failures, list), "config lifecycle failures must be an array")
    failure_sequences: list[int] = []
    for index, failure in enumerate(failures):
        context = f"failures[{index}]"
        _require(isinstance(failure, Mapping), f"{context} must be an object")
        _closed(failure, frozenset({"sequence", "event", "attempt", "reason_code", "exception_class", "message"}), context)
        failure_sequences.append(_integer(failure["sequence"], f"{context}.sequence"))
        _text(failure["event"], f"{context}.event")
        _attempt(failure["attempt"], f"{context}.attempt", nullable=True)
        for key in ("reason_code", "exception_class", "message"):
            _text(failure[key], f"{context}.{key}", nullable=True)
    _require(failure_sequences == sorted(set(failure_sequences)), "config lifecycle failures are not unique sequence order")

    health = value["health"]
    _require(isinstance(health, Mapping), "config lifecycle health must be an object")
    _closed(health, frozenset({"state", "end_health", "write_failure", "observer_failure_count"}), "config lifecycle health")
    _require(health["state"] in {"complete", "failed"}, "config lifecycle health state is unsupported")
    _require(health["end_health"] in {"healthy", "failed"}, "config lifecycle end health is unsupported")
    _require((health["state"] == "complete") == (health["end_health"] == "healthy"), "config lifecycle health states disagree")
    _require(type(health["write_failure"]) is bool, "config lifecycle write_failure must be boolean")
    _integer(health["observer_failure_count"], "config lifecycle observer_failure_count")

    summary = value["summary"]
    _require(isinstance(summary, Mapping), "config lifecycle summary must be an object")
    _closed(summary, frozenset({"attempt_count", "admitted_count", "terminal_outcomes", "verified_resource_count", "failure_count"}), "config lifecycle summary")
    _require(summary["attempt_count"] == len(value["configurations"]), "config lifecycle summary attempt count is stale")
    _require(summary["admitted_count"] == admitted_count, "config lifecycle summary admitted count is stale")
    _require(summary["verified_resource_count"] == verified_resource_count, "config lifecycle summary verified resource count is stale")
    _require(summary["failure_count"] == len(failures), "config lifecycle summary failure count is stale")
    _require(summary["terminal_outcomes"] == {key: terminal_counts[key] for key in sorted(terminal_counts)}, "config lifecycle terminal outcome summary is stale")

    limitations = value["limitations"]
    _require(isinstance(limitations, list), "config lifecycle limitations must be an array")
    normalized_limitations = [_text(item, f"limitations[{index}]") for index, item in enumerate(limitations)]
    _require(normalized_limitations == sorted(set(normalized_limitations)), "config lifecycle limitations are not canonical")
    _require(set(MANDATORY_LIMITATIONS) <= set(normalized_limitations), "config lifecycle mandatory limitations are absent")

    boundaries = value["boundaries"]
    _require(isinstance(boundaries, Mapping), "config lifecycle boundaries must be an object")
    _closed(boundaries, frozenset({"configuration_lifecycle_proved", "transformer_order_proved", "mixin_transformation_completion_proved", "final_class_bytes_proved"}), "config lifecycle boundaries")
    _require(boundaries["configuration_lifecycle_proved"] is (health["state"] == "complete"), "config lifecycle proved boundary and health disagree")
    _require(boundaries["transformer_order_proved"] is False, "config lifecycle receipt overclaims transformer order")
    _require(boundaries["mixin_transformation_completion_proved"] is False, "config lifecycle receipt overclaims transformation completion")
    _require(boundaries["final_class_bytes_proved"] is False, "config lifecycle receipt overclaims final bytes")
    return deepcopy(dict(value))


def render_config_lifecycle_receipt(value: Mapping[str, Any]) -> bytes:
    admitted = parse_config_lifecycle_receipt(value)
    return json.dumps(
        admitted, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"


def write_config_lifecycle_receipt(path: Path, value: Mapping[str, Any]) -> None:
    payload = render_config_lifecycle_receipt(value)
    if path.is_symlink():
        raise ConfigLifecycleValidationError(
            f"config lifecycle destination cannot be a symlink: {path}"
        )
    destination = path.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".mixin-config-lifecycle-", suffix=".json.tmp", dir=destination.parent
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
