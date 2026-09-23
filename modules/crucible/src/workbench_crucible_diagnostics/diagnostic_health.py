"""Build a bounded diagnostic-health receipt from exact retained evidence.

The profile policy is the only authority for diagnostic matching. Runtime log
text is retained evidence, not a free-form source of claims. V1 deliberately
supports only exact logger/level equality and a literal message prefix.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping, Sequence


DIAGNOSTIC_HEALTH_RECEIPT_FORMAT = (
    "workbench-crucible-external-diagnostic-health-receipt-v1"
)
DIAGNOSTIC_HEALTH_RECEIPT_PREFIX = (
    "crucible-external-diagnostic-health:sha256:"
)
LITERAL_DIAGNOSTIC_POLICY_FORMAT = (
    "workbench-crucible-literal-diagnostic-policy-v1"
)
SESSION_AUDIT_SCHEMA = "workbench.crucible.exact-runtime-session-audit.v1"
SESSION_AUDIT_PREFIX = "crucible-exact-runtime-session-audit:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
LOG_FORMAT = "forge-log4j2-bracketed-v1"
MATCH_SEMANTICS = "literal-logger-level-message-prefix-v1"

_SHA256_CHARS = frozenset("0123456789abcdef")
_LEVELS = frozenset({"TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"})
_DISPOSITIONS = frozenset({"fail", "review"})
_GATE_STATES = frozenset({"fail", "review", "inconclusive"})

_AUDIT_KEYS = frozenset(
    {
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
)
_CANDIDATE_KEYS = frozenset({"file_sha256", "canonical_sha256"})
_FIXTURE_ARTIFACT_KEYS = frozenset({"file_sha256", "size_bytes"})
_INSTALLED_SET_KEYS = frozenset(
    {
        "file_sha256",
        "canonical_sha256",
        "runtime_class_source_sha256",
        "verified_artifacts",
    }
)
_INSTALLED_ARTIFACT_KEYS = frozenset(
    {"relative_path", "sha256", "size_bytes"}
)
_CONFIGURATION_KEYS = frozenset(
    {
        "file_sha256",
        "canonical_sha256",
        "runtime_settings_sha256",
        "verified_files",
    }
)
_CONFIG_FILE_KEYS = frozenset({"relative_path", "sha256", "size_bytes"})
_LAUNCH_LOG_KEYS = frozenset(
    {
        "file_sha256",
        "size_bytes",
        "java_tool_options_occurrences",
        "java_tool_options_line_sha256",
        "java_properties_sha256",
        "gradle",
    }
)
_COMPLETED_GRADLE_KEYS = frozenset(
    {"terminal_status", "exit_code", "exit_code_evidence"}
)
_RAW_KEYS = frozenset(
    {
        "file_sha256",
        "size_bytes",
        "capture_id",
        "row_count",
        "terminal_sequence",
        "terminal_state",
        "stop_control_count",
        "fixture_completion_marker_count",
    }
)
_FIXTURE_RESULT_KEYS = frozenset(
    {
        "file_sha256",
        "canonical_document_sha256",
        "completion_state",
        "save_state",
        "shutdown_state",
        "route_order",
        "route_sha256",
        "semantic_result_sha256",
        "runtime_inventory_sha256",
        "runtime_inventory_count",
    }
)
_FOUNDATION_KEYS = frozenset(
    {"format", "class_count", "total_size_bytes", "manifest_sha256"}
)
_POLICY_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "policy_id",
        "owner_profile_id",
        "subject_artifact_sha256",
        "log_format",
        "match_semantics",
        "zero_match_state",
        "rules",
    }
)
_RULE_KEYS = frozenset(
    {"rule_id", "disposition", "logger", "level", "message_prefix"}
)
_RECEIPT_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "receipt_id",
        "canonicalization_id",
        "session",
        "subject",
        "inputs",
        "policy",
        "rule_results",
        "summary",
        "boundaries",
    }
)
_SESSION_KEYS = frozenset(
    {
        "audit_id",
        "case_id",
        "outcome",
        "observer_enabled",
        "candidate_lock_file_sha256",
        "candidate_lock_canonical_sha256",
        "installed_mod_set_file_sha256",
        "installed_mod_set_canonical_sha256",
    }
)
_SUBJECT_KEYS = frozenset(
    {"artifact_sha256", "installed_relative_path", "installed_size_bytes"}
)
_INPUTS_KEYS = frozenset({"session_audit", "launch_log", "policy"})
_FILE_BINDING_KEYS = frozenset({"file_sha256", "size_bytes"})
_POLICY_BINDING_KEYS = frozenset(
    {"file_sha256", "size_bytes", "canonical_sha256"}
)
_RULE_RESULT_KEYS = frozenset(
    {
        "rule_id",
        "disposition",
        "occurrence_count",
        "first_line",
        "last_line",
        "message_sha256s",
    }
)
_SUMMARY_KEYS = frozenset(
    {
        "gate_state",
        "rule_count",
        "matched_rule_count",
        "unmatched_rule_count",
        "fail_matched_rule_count",
        "review_matched_rule_count",
        "fail_occurrence_count",
        "review_occurrence_count",
        "total_match_occurrence_count",
        "matched_log_line_count",
        "total_log_line_count",
        "parsed_log_line_count",
        "unparsed_log_line_count",
        "clean_compatibility_seal_allowed",
    }
)

_BOUNDARIES = {
    "arbitrary_executable_patterns_supported": False,
    "fail_match_blocks_clean_compatibility_seal": True,
    "free_form_log_is_authoritative": False,
    "log_absence_proves_diagnostic_absence": False,
    "profile_literal_policy_is_gate_authority": True,
    "review_match_requires_review": True,
    "subject_artifact_execution_proved": False,
    "zero_matches_prove_health": False,
}


class DiagnosticHealthValidationError(ValueError):
    """Raised when diagnostic-health evidence is not exact V1 material."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticHealthValidationError(message)


def canonical_diagnostic_health_json_bytes(value: Any) -> bytes:
    """Encode canonical JSON used for policy and receipt identities."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DiagnosticHealthValidationError(
            f"cannot canonically encode diagnostic-health material: {exc}"
        ) from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_number(token: str) -> None:
    raise ValueError(f"unsupported JSON number {token}")


def _parse_json_bytes(data: bytes, context: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
            parse_float=_reject_number,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise DiagnosticHealthValidationError(f"cannot parse {context}: {exc}") from exc


def _closed(value: Any, keys: frozenset[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == keys,
        f"{context} fields mismatch: missing={sorted(keys - actual)!r}, "
        f"unknown={sorted(actual - keys)!r}",
    )
    return value


def _array(value: Any, context: str) -> Sequence[Any]:
    _require(
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray)),
        f"{context} must be an array",
    )
    return value


def _text(value: Any, context: str, *, line_safe: bool = False) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    _require("\x00" not in value, f"{context} cannot contain a NUL byte")
    if line_safe:
        _require("\r" not in value and "\n" not in value, f"{context} must be one line")
    return value


def _identifier(value: Any, context: str) -> str:
    result = _text(value, context, line_safe=True)
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    )
    _require(
        result[0].isalnum() and set(result) <= allowed,
        f"{context} is not a bounded identifier",
    )
    return result


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_CHARS,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _boolean(value: Any, context: str) -> bool:
    _require(type(value) is bool, f"{context} must be a boolean")
    return value


def _content_identity(
    value: Mapping[str, Any],
    *,
    field: str,
    prefix: str,
    context: str,
) -> str:
    identity = _text(value[field], f"{context}.{field}")
    _require(identity.startswith(prefix), f"{context}.{field} prefix mismatch")
    _sha256(identity[len(prefix) :], f"{context}.{field} digest")
    material = deepcopy(dict(value))
    material.pop(field)
    expected = prefix + hashlib.sha256(
        canonical_diagnostic_health_json_bytes(material)
    ).hexdigest()
    _require(identity == expected, f"{context} content identity mismatch")
    return identity


def _normalize_policy(value: Any) -> dict[str, Any]:
    policy = _closed(value, _POLICY_KEYS, "literal diagnostic policy")
    _require(
        policy["format"] == LITERAL_DIAGNOSTIC_POLICY_FORMAT,
        "literal diagnostic policy format is not V1",
    )
    _require(policy["schema_version"] == 1, "literal diagnostic policy schema_version is not 1")
    _require(policy["log_format"] == LOG_FORMAT, "literal diagnostic policy log_format is unsupported")
    _require(
        policy["match_semantics"] == MATCH_SEMANTICS,
        "literal diagnostic policy match_semantics is unsupported",
    )
    _require(
        policy["zero_match_state"] == "inconclusive",
        "literal diagnostic policy zero_match_state must be inconclusive",
    )
    rows = _array(policy["rules"], "literal diagnostic policy rules")
    _require(0 < len(rows) <= 256, "literal diagnostic policy must contain 1..256 rules")
    normalized_rules: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_predicates: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(rows):
        context = f"literal diagnostic policy rules[{index}]"
        row = _closed(raw, _RULE_KEYS, context)
        rule_id = _identifier(row["rule_id"], f"{context}.rule_id")
        _require(rule_id not in seen_ids, f"duplicate literal diagnostic rule_id {rule_id!r}")
        seen_ids.add(rule_id)
        disposition = row["disposition"]
        _require(disposition in _DISPOSITIONS, f"{context}.disposition is unsupported")
        logger = _text(row["logger"], f"{context}.logger", line_safe=True)
        level = row["level"]
        _require(level in _LEVELS, f"{context}.level is unsupported")
        message_prefix = _text(
            row["message_prefix"], f"{context}.message_prefix", line_safe=True
        )
        _require(
            len(message_prefix.encode("utf-8")) <= 4096,
            f"{context}.message_prefix exceeds 4096 UTF-8 bytes",
        )
        predicate = (logger, level, message_prefix)
        _require(
            predicate not in seen_predicates,
            f"literal diagnostic policy repeats match predicate at {context}",
        )
        seen_predicates.add(predicate)
        normalized_rules.append(
            {
                "rule_id": rule_id,
                "disposition": disposition,
                "logger": logger,
                "level": level,
                "message_prefix": message_prefix,
            }
        )
    return {
        "format": LITERAL_DIAGNOSTIC_POLICY_FORMAT,
        "schema_version": 1,
        "policy_id": _identifier(policy["policy_id"], "literal diagnostic policy.policy_id"),
        "owner_profile_id": _identifier(
            policy["owner_profile_id"], "literal diagnostic policy.owner_profile_id"
        ),
        "subject_artifact_sha256": _sha256(
            policy["subject_artifact_sha256"],
            "literal diagnostic policy.subject_artifact_sha256",
        ),
        "log_format": LOG_FORMAT,
        "match_semantics": MATCH_SEMANTICS,
        "zero_match_state": "inconclusive",
        "rules": sorted(normalized_rules, key=lambda row: row["rule_id"]),
    }


def parse_literal_diagnostic_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize an already-decoded V1 policy."""

    return _normalize_policy(value)


def _verify_sha_object(value: Any, keys: frozenset[str], context: str) -> Mapping[str, Any]:
    row = _closed(value, keys, context)
    for key in keys:
        if key.endswith("sha256"):
            _sha256(row[key], f"{context}.{key}")
    return row


def _admit_completed_session_audit(
    value: Any,
    *,
    subject_artifact_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]:
    audit = _closed(value, _AUDIT_KEYS, "session audit")
    _require(audit["schema"] == SESSION_AUDIT_SCHEMA, "session audit schema is not exact V1")
    audit_id = _content_identity(
        audit,
        field="audit_id",
        prefix=SESSION_AUDIT_PREFIX,
        context="session audit",
    )
    case_id = _identifier(audit["case_id"], "session audit.case_id")
    _require(audit["outcome"] == "completed", "session audit outcome is not completed")
    observer_enabled = _boolean(audit["observer_enabled"], "session audit.observer_enabled")

    candidate = _verify_sha_object(
        audit["candidate_lock"], _CANDIDATE_KEYS, "session audit.candidate_lock"
    )
    fixture = _verify_sha_object(
        audit["fixture_artifact"],
        _FIXTURE_ARTIFACT_KEYS,
        "session audit.fixture_artifact",
    )
    _integer(fixture["size_bytes"], "session audit.fixture_artifact.size_bytes", minimum=1)

    installed = _verify_sha_object(
        audit["installed_mod_set"],
        _INSTALLED_SET_KEYS,
        "session audit.installed_mod_set",
    )
    artifacts = _array(
        installed["verified_artifacts"],
        "session audit.installed_mod_set.verified_artifacts",
    )
    _require(bool(artifacts), "session audit installed artifact set cannot be empty")
    normalized_artifacts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, raw in enumerate(artifacts):
        context = f"session audit installed artifact[{index}]"
        row = _closed(raw, _INSTALLED_ARTIFACT_KEYS, context)
        relative_path = _text(row["relative_path"], f"{context}.relative_path", line_safe=True)
        _require(relative_path not in seen_paths, f"session audit repeats installed path {relative_path!r}")
        seen_paths.add(relative_path)
        normalized_artifacts.append(
            {
                "relative_path": relative_path,
                "sha256": _sha256(row["sha256"], f"{context}.sha256"),
                "size_bytes": _integer(row["size_bytes"], f"{context}.size_bytes", minimum=1),
            }
        )
    subject_matches = [
        row for row in normalized_artifacts if row["sha256"] == subject_artifact_sha256
    ]
    _require(
        len(subject_matches) == 1,
        "policy subject artifact SHA-256 is not bound exactly once in the audited installed set",
    )
    _require(
        [row["relative_path"] for row in normalized_artifacts]
        == sorted(row["relative_path"] for row in normalized_artifacts),
        "session audit installed artifacts are not in canonical path order",
    )
    _require(
        sum(row["sha256"] == fixture["file_sha256"] for row in normalized_artifacts) == 1,
        "session audit fixture artifact is not bound exactly once in the installed set",
    )

    configuration = _verify_sha_object(
        audit["configuration_set"],
        _CONFIGURATION_KEYS,
        "session audit.configuration_set",
    )
    files = _array(
        configuration["verified_files"], "session audit.configuration_set.verified_files"
    )
    _require(bool(files), "session audit configuration file set cannot be empty")
    seen_config_paths: set[str] = set()
    config_paths: list[str] = []
    for index, raw in enumerate(files):
        context = f"session audit configuration file[{index}]"
        row = _closed(raw, _CONFIG_FILE_KEYS, context)
        relative_path = _text(row["relative_path"], f"{context}.relative_path", line_safe=True)
        _require(relative_path not in seen_config_paths, f"session audit repeats configuration path {relative_path!r}")
        seen_config_paths.add(relative_path)
        config_paths.append(relative_path)
        _sha256(row["sha256"], f"{context}.sha256")
        _integer(row["size_bytes"], f"{context}.size_bytes", minimum=1)
    _require(
        config_paths == sorted(config_paths),
        "session audit configuration files are not in canonical path order",
    )

    launch = _verify_sha_object(
        audit["launch_log"], _LAUNCH_LOG_KEYS, "session audit.launch_log"
    )
    _integer(launch["size_bytes"], "session audit.launch_log.size_bytes", minimum=1)
    _require(
        launch["java_tool_options_occurrences"] == 2,
        "completed session audit does not retain two JAVA_TOOL_OPTIONS observations",
    )
    gradle = _closed(
        launch["gradle"], _COMPLETED_GRADLE_KEYS, "session audit.launch_log.gradle"
    )
    _require(
        gradle["terminal_status"] == "BUILD SUCCESSFUL"
        and gradle["exit_code"] == 0
        and gradle["exit_code_evidence"] == "gradle_success_terminal_record",
        "session audit launch completion evidence is not clean",
    )

    result = _closed(
        audit["fixture_result"], _FIXTURE_RESULT_KEYS, "session audit.fixture_result"
    )
    for key in (
        "file_sha256",
        "canonical_document_sha256",
        "route_sha256",
        "semantic_result_sha256",
        "runtime_inventory_sha256",
    ):
        _sha256(result[key], f"session audit.fixture_result.{key}")
    _require(
        result["completion_state"] == "complete"
        and result["save_state"] == "flushed"
        and result["shutdown_state"] == "requested",
        "session audit fixture result is not complete, flushed, and shutdown-requested",
    )
    _require(
        result["route_order"] in {"forward", "reverse"},
        "session audit fixture route order is invalid",
    )
    _integer(result["runtime_inventory_count"], "session audit.fixture_result.runtime_inventory_count", minimum=1)

    raw = audit["raw_capture"]
    if observer_enabled:
        raw = _closed(raw, _RAW_KEYS, "session audit.raw_capture")
        _sha256(raw["file_sha256"], "session audit.raw_capture.file_sha256")
        _integer(raw["size_bytes"], "session audit.raw_capture.size_bytes", minimum=1)
        row_count = _integer(raw["row_count"], "session audit.raw_capture.row_count", minimum=1)
        terminal = _integer(raw["terminal_sequence"], "session audit.raw_capture.terminal_sequence")
        _require(terminal == row_count - 1, "session audit raw terminal sequence is not contiguous")
        _require(
            raw["terminal_state"] == "complete_and_stopped"
            and raw["stop_control_count"] == 1
            and raw["fixture_completion_marker_count"] == 1,
            "session audit raw capture is not complete and stopped",
        )
        _text(raw["capture_id"], "session audit.raw_capture.capture_id", line_safe=True)
    else:
        _require(raw is None, "observer-disabled completed session audit must have null raw_capture")

    foundation = _closed(
        audit["foundation_class_dump"], _FOUNDATION_KEYS, "session audit.foundation_class_dump"
    )
    _require(
        foundation["format"] == "workbench-foundation-class-dump-manifest-v1",
        "session audit Foundation format is not V1",
    )
    _integer(foundation["class_count"], "session audit Foundation class_count", minimum=1)
    _integer(foundation["total_size_bytes"], "session audit Foundation total_size_bytes", minimum=1)
    _sha256(foundation["manifest_sha256"], "session audit Foundation manifest_sha256")

    session = {
        "audit_id": audit_id,
        "case_id": case_id,
        "outcome": "completed",
        "observer_enabled": observer_enabled,
        "candidate_lock_file_sha256": candidate["file_sha256"],
        "candidate_lock_canonical_sha256": candidate["canonical_sha256"],
        "installed_mod_set_file_sha256": installed["file_sha256"],
        "installed_mod_set_canonical_sha256": installed["canonical_sha256"],
    }
    subject = {
        "artifact_sha256": subject_artifact_sha256,
        "installed_relative_path": subject_matches[0]["relative_path"],
        "installed_size_bytes": subject_matches[0]["size_bytes"],
    }
    return session, subject, launch


def admit_completed_session_audit(
    value: Any,
    *,
    subject_artifact_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]:
    """Admit a decoded completed exact-runtime audit for evidence composition."""

    return _admit_completed_session_audit(
        value,
        subject_artifact_sha256=_sha256(
            subject_artifact_sha256,
            "session-audit subject artifact SHA-256",
        ),
    )


def _log_lines(data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise DiagnosticHealthValidationError(f"launch log is not UTF-8: {exc}") from exc
    _require("\x00" not in text, "launch log contains a NUL byte")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _parse_forge_header(line: str) -> tuple[str, str, str] | None:
    """Parse `[time] [thread/LEVEL] [logger]: message` without policy regex."""

    if not line.startswith("["):
        return None
    first = line.find("] [", 1)
    if first <= 1:
        return None
    second_start = first + 3
    second = line.find("] [", second_start)
    if second <= second_start:
        return None
    third_start = second + 3
    third = line.find("]: ", third_start)
    if third <= third_start:
        return None
    thread_level = line[second_start:second]
    if "/" not in thread_level:
        return None
    thread, level = thread_level.rsplit("/", 1)
    logger = line[third_start:third]
    if not thread or level not in _LEVELS or not logger:
        return None
    return logger, level, line[third + 3 :]


def _evaluate_log(
    data: bytes,
    policy: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lines = _log_lines(data)
    state: dict[str, dict[str, Any]] = {
        row["rule_id"]: {
            "rule": row,
            "count": 0,
            "first": None,
            "last": None,
            "message_sha256s": set(),
            "lines": set(),
        }
        for row in policy["rules"]
    }
    parsed_count = 0
    matched_lines: set[int] = set()
    for line_number, line in enumerate(lines, 1):
        parsed = _parse_forge_header(line)
        if parsed is None:
            continue
        parsed_count += 1
        logger, level, message = parsed
        for row in policy["rules"]:
            if (
                logger == row["logger"]
                and level == row["level"]
                and message.startswith(row["message_prefix"])
            ):
                result = state[row["rule_id"]]
                result["count"] += 1
                if result["first"] is None:
                    result["first"] = line_number
                result["last"] = line_number
                result["message_sha256s"].add(
                    hashlib.sha256(message.encode("utf-8")).hexdigest()
                )
                result["lines"].add(line_number)
                matched_lines.add(line_number)

    results: list[dict[str, Any]] = []
    for rule_id in sorted(state):
        item = state[rule_id]
        row = item["rule"]
        results.append(
            {
                "rule_id": rule_id,
                "disposition": row["disposition"],
                "occurrence_count": item["count"],
                "first_line": item["first"],
                "last_line": item["last"],
                "message_sha256s": sorted(item["message_sha256s"]),
            }
        )
    summary = _derive_summary(
        results,
        total_log_line_count=len(lines),
        parsed_log_line_count=parsed_count,
        matched_log_line_count=len(matched_lines),
    )
    return results, summary


def _derive_summary(
    results: Sequence[Mapping[str, Any]],
    *,
    total_log_line_count: int,
    parsed_log_line_count: int,
    matched_log_line_count: int,
) -> dict[str, Any]:
    fail = [row for row in results if row["disposition"] == "fail"]
    review = [row for row in results if row["disposition"] == "review"]
    fail_occurrences = sum(int(row["occurrence_count"]) for row in fail)
    review_occurrences = sum(int(row["occurrence_count"]) for row in review)
    matched = sum(int(row["occurrence_count"]) > 0 for row in results)
    if fail_occurrences:
        gate_state = "fail"
    elif review_occurrences:
        gate_state = "review"
    else:
        gate_state = "inconclusive"
    return {
        "gate_state": gate_state,
        "rule_count": len(results),
        "matched_rule_count": matched,
        "unmatched_rule_count": len(results) - matched,
        "fail_matched_rule_count": sum(int(row["occurrence_count"]) > 0 for row in fail),
        "review_matched_rule_count": sum(int(row["occurrence_count"]) > 0 for row in review),
        "fail_occurrence_count": fail_occurrences,
        "review_occurrence_count": review_occurrences,
        "total_match_occurrence_count": fail_occurrences + review_occurrences,
        "matched_log_line_count": matched_log_line_count,
        "total_log_line_count": total_log_line_count,
        "parsed_log_line_count": parsed_log_line_count,
        "unparsed_log_line_count": total_log_line_count - parsed_log_line_count,
        "clean_compatibility_seal_allowed": False,
    }


def build_external_diagnostic_health_receipt(
    *,
    session_audit_bytes: bytes,
    launch_log_bytes: bytes,
    policy_bytes: bytes,
) -> dict[str, Any]:
    """Build one content-addressed receipt from the three exact V1 inputs."""

    _require(isinstance(session_audit_bytes, bytes), "session_audit_bytes must be bytes")
    _require(isinstance(launch_log_bytes, bytes), "launch_log_bytes must be bytes")
    _require(isinstance(policy_bytes, bytes), "policy_bytes must be bytes")
    _require(bool(session_audit_bytes), "session audit input cannot be empty")
    _require(bool(launch_log_bytes), "launch log input cannot be empty")
    _require(bool(policy_bytes), "literal policy input cannot be empty")

    raw_policy = _parse_json_bytes(policy_bytes, "literal diagnostic policy")
    policy = _normalize_policy(raw_policy)
    raw_audit = _parse_json_bytes(session_audit_bytes, "session audit")
    session, subject, audited_launch = _admit_completed_session_audit(
        raw_audit,
        subject_artifact_sha256=policy["subject_artifact_sha256"],
    )
    launch_sha256 = hashlib.sha256(launch_log_bytes).hexdigest()
    _require(
        launch_sha256 == audited_launch["file_sha256"],
        "exact launch log SHA-256 does not match the completed session audit",
    )
    _require(
        len(launch_log_bytes) == audited_launch["size_bytes"],
        "exact launch log size does not match the completed session audit",
    )
    results, summary = _evaluate_log(launch_log_bytes, policy)
    material = {
        "format": DIAGNOSTIC_HEALTH_RECEIPT_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": session,
        "subject": subject,
        "inputs": {
            "session_audit": {
                "file_sha256": hashlib.sha256(session_audit_bytes).hexdigest(),
                "size_bytes": len(session_audit_bytes),
            },
            "launch_log": {
                "file_sha256": launch_sha256,
                "size_bytes": len(launch_log_bytes),
            },
            "policy": {
                "file_sha256": hashlib.sha256(policy_bytes).hexdigest(),
                "size_bytes": len(policy_bytes),
                "canonical_sha256": hashlib.sha256(
                    canonical_diagnostic_health_json_bytes(policy)
                ).hexdigest(),
            },
        },
        "policy": policy,
        "rule_results": results,
        "summary": summary,
        "boundaries": deepcopy(_BOUNDARIES),
    }
    receipt_id = DIAGNOSTIC_HEALTH_RECEIPT_PREFIX + hashlib.sha256(
        canonical_diagnostic_health_json_bytes(material)
    ).hexdigest()
    return {"receipt_id": receipt_id, **material}


def _normalize_file_binding(value: Any, context: str) -> dict[str, Any]:
    row = _closed(value, _FILE_BINDING_KEYS, context)
    return {
        "file_sha256": _sha256(row["file_sha256"], f"{context}.file_sha256"),
        "size_bytes": _integer(row["size_bytes"], f"{context}.size_bytes", minimum=1),
    }


def parse_external_diagnostic_health_receipt(value: Any) -> dict[str, Any]:
    """Validate a decoded receipt and recompute all identity/summary fields."""

    receipt = _closed(value, _RECEIPT_KEYS, "diagnostic-health receipt")
    _require(receipt["format"] == DIAGNOSTIC_HEALTH_RECEIPT_FORMAT, "receipt format is not V1")
    _require(receipt["schema_version"] == 1, "receipt schema_version is not 1")
    _require(receipt["canonicalization_id"] == CANONICALIZATION_ID, "receipt canonicalization_id is unsupported")

    session_raw = _closed(receipt["session"], _SESSION_KEYS, "receipt.session")
    audit_id = _text(session_raw["audit_id"], "receipt.session.audit_id")
    _require(audit_id.startswith(SESSION_AUDIT_PREFIX), "receipt session audit ID prefix mismatch")
    _sha256(audit_id[len(SESSION_AUDIT_PREFIX) :], "receipt session audit ID digest")
    session = {
        "audit_id": audit_id,
        "case_id": _identifier(session_raw["case_id"], "receipt.session.case_id"),
        "outcome": session_raw["outcome"],
        "observer_enabled": _boolean(session_raw["observer_enabled"], "receipt.session.observer_enabled"),
        "candidate_lock_file_sha256": _sha256(session_raw["candidate_lock_file_sha256"], "receipt.session.candidate_lock_file_sha256"),
        "candidate_lock_canonical_sha256": _sha256(session_raw["candidate_lock_canonical_sha256"], "receipt.session.candidate_lock_canonical_sha256"),
        "installed_mod_set_file_sha256": _sha256(session_raw["installed_mod_set_file_sha256"], "receipt.session.installed_mod_set_file_sha256"),
        "installed_mod_set_canonical_sha256": _sha256(session_raw["installed_mod_set_canonical_sha256"], "receipt.session.installed_mod_set_canonical_sha256"),
    }
    _require(session["outcome"] == "completed", "receipt session outcome is not completed")

    subject_raw = _closed(receipt["subject"], _SUBJECT_KEYS, "receipt.subject")
    subject = {
        "artifact_sha256": _sha256(subject_raw["artifact_sha256"], "receipt.subject.artifact_sha256"),
        "installed_relative_path": _text(subject_raw["installed_relative_path"], "receipt.subject.installed_relative_path", line_safe=True),
        "installed_size_bytes": _integer(subject_raw["installed_size_bytes"], "receipt.subject.installed_size_bytes", minimum=1),
    }

    inputs_raw = _closed(receipt["inputs"], _INPUTS_KEYS, "receipt.inputs")
    inputs = {
        "session_audit": _normalize_file_binding(inputs_raw["session_audit"], "receipt.inputs.session_audit"),
        "launch_log": _normalize_file_binding(inputs_raw["launch_log"], "receipt.inputs.launch_log"),
    }
    policy_binding_raw = _closed(inputs_raw["policy"], _POLICY_BINDING_KEYS, "receipt.inputs.policy")
    inputs["policy"] = {
        "file_sha256": _sha256(policy_binding_raw["file_sha256"], "receipt.inputs.policy.file_sha256"),
        "size_bytes": _integer(policy_binding_raw["size_bytes"], "receipt.inputs.policy.size_bytes", minimum=1),
        "canonical_sha256": _sha256(policy_binding_raw["canonical_sha256"], "receipt.inputs.policy.canonical_sha256"),
    }

    policy = _normalize_policy(receipt["policy"])
    _require(policy["subject_artifact_sha256"] == subject["artifact_sha256"], "receipt policy subject does not match installed subject binding")
    expected_policy_sha = hashlib.sha256(canonical_diagnostic_health_json_bytes(policy)).hexdigest()
    _require(inputs["policy"]["canonical_sha256"] == expected_policy_sha, "receipt policy canonical SHA-256 mismatch")

    raw_results = _array(receipt["rule_results"], "receipt.rule_results")
    _require(len(raw_results) == len(policy["rules"]), "receipt rule result count does not match policy")
    policy_by_id = {row["rule_id"]: row for row in policy["rules"]}
    results: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_results):
        context = f"receipt.rule_results[{index}]"
        row = _closed(raw, _RULE_RESULT_KEYS, context)
        rule_id = _identifier(row["rule_id"], f"{context}.rule_id")
        _require(rule_id in policy_by_id, f"{context} is not declared by the policy")
        _require(row["disposition"] == policy_by_id[rule_id]["disposition"], f"{context}.disposition differs from policy")
        count = _integer(row["occurrence_count"], f"{context}.occurrence_count")
        hashes = _array(row["message_sha256s"], f"{context}.message_sha256s")
        normalized_hashes = [_sha256(item, f"{context}.message_sha256s") for item in hashes]
        _require(normalized_hashes == sorted(set(normalized_hashes)), f"{context}.message_sha256s must be sorted and unique")
        first = row["first_line"]
        last = row["last_line"]
        if count == 0:
            _require(first is None and last is None and not normalized_hashes, f"{context} zero occurrence result has retained match data")
        else:
            first = _integer(first, f"{context}.first_line", minimum=1)
            last = _integer(last, f"{context}.last_line", minimum=first)
            _require(bool(normalized_hashes), f"{context} matched result has no message hashes")
        results.append(
            {
                "rule_id": rule_id,
                "disposition": row["disposition"],
                "occurrence_count": count,
                "first_line": first,
                "last_line": last,
                "message_sha256s": normalized_hashes,
            }
        )
    _require([row["rule_id"] for row in results] == sorted(policy_by_id), "receipt rule results are not in canonical rule order")

    summary_raw = _closed(receipt["summary"], _SUMMARY_KEYS, "receipt.summary")
    gate_state = summary_raw["gate_state"]
    _require(gate_state in _GATE_STATES, "receipt summary gate_state is unsupported")
    total_lines = _integer(summary_raw["total_log_line_count"], "receipt.summary.total_log_line_count")
    parsed_lines = _integer(summary_raw["parsed_log_line_count"], "receipt.summary.parsed_log_line_count")
    matched_lines = _integer(summary_raw["matched_log_line_count"], "receipt.summary.matched_log_line_count")
    _require(parsed_lines <= total_lines, "receipt parsed log line count exceeds total")
    _require(matched_lines <= parsed_lines, "receipt matched log line count exceeds parsed")
    expected_summary = _derive_summary(
        results,
        total_log_line_count=total_lines,
        parsed_log_line_count=parsed_lines,
        matched_log_line_count=matched_lines,
    )
    _require(dict(summary_raw) == expected_summary, "receipt summary is not derived from rule results")
    _require(dict(receipt["boundaries"]) == _BOUNDARIES, "receipt boundaries are not exact V1")

    normalized = {
        "format": DIAGNOSTIC_HEALTH_RECEIPT_FORMAT,
        "schema_version": 1,
        "receipt_id": receipt["receipt_id"],
        "canonicalization_id": CANONICALIZATION_ID,
        "session": session,
        "subject": subject,
        "inputs": inputs,
        "policy": policy,
        "rule_results": results,
        "summary": expected_summary,
        "boundaries": deepcopy(_BOUNDARIES),
    }
    _content_identity(
        normalized,
        field="receipt_id",
        prefix=DIAGNOSTIC_HEALTH_RECEIPT_PREFIX,
        context="diagnostic-health receipt",
    )
    return normalized


def write_external_diagnostic_health_receipt(
    path: str | os.PathLike[str],
    receipt: Mapping[str, Any],
) -> None:
    """Validate and atomically write one pretty V1 receipt."""

    normalized = parse_external_diagnostic_health_receipt(receipt)
    destination = Path(path)
    if destination.exists() and destination.is_symlink():
        raise DiagnosticHealthValidationError(f"receipt output cannot be a symlink: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".external-diagnostic-health-",
        suffix=".json.tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_stable_regular_file(
    path: str | os.PathLike[str],
    *,
    context: str,
    maximum_size: int,
) -> bytes:
    """Read a bounded regular non-symlink file and reject concurrent changes."""

    source = Path(path)
    try:
        before = source.lstat()
    except OSError as exc:
        raise DiagnosticHealthValidationError(f"cannot inspect {context} {source}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"{context} cannot be a symlink: {source}")
    _require(stat.S_ISREG(before.st_mode), f"{context} must be a regular file: {source}")
    _require(0 < before.st_size <= maximum_size, f"{context} size is outside bounds: {source}")
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    try:
        data = source.read_bytes()
        after = source.lstat()
    except OSError as exc:
        raise DiagnosticHealthValidationError(f"cannot read {context} {source}: {exc}") from exc
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    _require(after_identity == identity and len(data) == before.st_size, f"{context} changed while reading: {source}")
    return data
