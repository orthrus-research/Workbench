"""Transformer-chain epoch and exclusion evidence V1."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


RAW_TRANSFORMER_CHAIN_FORMAT = "workbench-cleanmix-transformer-chain-raw-v1"
TRANSFORMER_CHAIN_RECEIPT_FORMAT = (
    "workbench-crucible-mixin-transformer-chain-epoch-receipt-v1"
)
TRANSFORMER_CHAIN_RECEIPT_PREFIX = (
    "crucible-mixin-transformer-chain-epoch:sha256:"
)
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_RAW_KEYS = frozenset({"format", "capture_id", "sequence", "event", "payload"})
_RAW_EVENTS = frozenset(
    {
        "capture_start",
        "transformer_installed",
        "transform_applied",
        "transform_rejected",
        "transform_failure",
        "provider_created",
        "service_refresh_started",
        "service_refresh_completed",
        "refresh_requested",
        "exclusion_change",
        "chain_epoch",
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
_CHAIN_RAW_KEYS = frozenset(
    {
        "ordinal",
        "reported_name",
        "implementation_class",
        "wrapper_class",
        "implementation_loader_class",
        "implementation_loader_identity",
        "code_source_uri",
        "priority",
        "delegation_excluded",
    }
)
_CHAIN_RECEIPT_KEYS = frozenset(
    {
        "ordinal",
        "reported_name",
        "implementation_class",
        "wrapper_class",
        "implementation_loader_class",
        "artifact_sha256",
        "priority",
        "delegation_excluded",
    }
)

MANDATORY_LIMITATIONS = (
    "The receipt proves ordered live and delegated transformer chains only at observed Cleanroom provider rebuild epochs in the bound launch.",
    "Provider exclusions and Foundation classloader transformer exclusions are distinct layers and are retained separately.",
    "A chain entry proves installation or delegation order, not that the transformer ran for every class or completed successfully.",
    "Transformer-chain custody does not prove final class-definition bytes.",
)


class TransformerChainValidationError(ValueError):
    """Transformer-chain evidence is malformed or overclaims."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TransformerChainValidationError(message)


def canonical_transformer_chain_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TransformerChainValidationError(
            f"cannot canonically encode transformer-chain evidence: {exc}"
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


def _integer(
    value: Any, context: str, *, minimum: int = 0, nullable: bool = False
) -> int | None:
    if nullable and value is None:
        return None
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _boolean(value: Any, context: str, *, nullable: bool = False) -> bool | None:
    if nullable and value is None:
        return None
    _require(type(value) is bool, f"{context} must be boolean")
    return value


def _string_list(value: Any, context: str, *, sorted_unique: bool = False) -> list[str]:
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


def _chain_rows(value: Any, context: str, *, raw: bool) -> list[dict[str, Any]]:
    _require(isinstance(value, list), f"{context} must be an array")
    result: list[dict[str, Any]] = []
    keys = _CHAIN_RAW_KEYS if raw else _CHAIN_RECEIPT_KEYS
    for index, row in enumerate(value):
        row_context = f"{context}[{index}]"
        _require(isinstance(row, Mapping), f"{row_context} must be an object")
        _closed(row, keys, row_context)
        _require(row["ordinal"] == index, f"{context} ordinals are not contiguous")
        normalized = {
            "ordinal": index,
            "reported_name": _text(row["reported_name"], f"{row_context}.reported_name"),
            "implementation_class": _text(
                row["implementation_class"], f"{row_context}.implementation_class"
            ),
            "wrapper_class": _text(
                row["wrapper_class"], f"{row_context}.wrapper_class", nullable=True
            ),
            "implementation_loader_class": _text(
                row["implementation_loader_class"],
                f"{row_context}.implementation_loader_class",
            ),
            "priority": _integer(
                row["priority"], f"{row_context}.priority", minimum=-2**31, nullable=True
            ),
            "delegation_excluded": _boolean(
                row["delegation_excluded"],
                f"{row_context}.delegation_excluded",
                nullable=True,
            ),
        }
        if raw:
            normalized["implementation_loader_identity"] = _text(
                row["implementation_loader_identity"],
                f"{row_context}.implementation_loader_identity",
            )
            normalized["code_source_uri"] = _text(
                row["code_source_uri"], f"{row_context}.code_source_uri", nullable=True
            )
        else:
            normalized["artifact_sha256"] = _sha256(
                row["artifact_sha256"], f"{row_context}.artifact_sha256"
            )
        result.append(normalized)
    return result


def parse_raw_transformer_chain(encoded: bytes) -> list[dict[str, Any]]:
    """Strictly parse one transformer-chain NDJSON stream."""

    _require(isinstance(encoded, bytes) and bool(encoded), "raw transformer-chain trace is empty")
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise TransformerChainValidationError(
            f"raw transformer-chain trace is not UTF-8: {exc}"
        ) from exc
    _require(text.endswith("\n"), "raw transformer-chain trace lacks a terminal newline")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        _require(bool(line), f"raw transformer-chain line {index + 1} is empty")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_json_object,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite number {item}")
                ),
            )
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
            raise TransformerChainValidationError(
                f"raw transformer-chain line {index + 1} is malformed: {exc}"
            ) from exc
        _require(isinstance(value, Mapping), f"raw transformer-chain line {index + 1} is not an object")
        _closed(value, _RAW_KEYS, f"raw transformer-chain line {index + 1}")
        _require(value["format"] == RAW_TRANSFORMER_CHAIN_FORMAT, "raw transformer-chain format is not V1")
        _text(value["capture_id"], f"raw transformer-chain line {index + 1}.capture_id")
        _require(value["sequence"] == index, "raw transformer-chain sequences are not contiguous from zero")
        _require(value["event"] in _RAW_EVENTS, "raw transformer-chain event is unsupported")
        _require(isinstance(value["payload"], Mapping), "raw transformer-chain payload must be an object")
        rows.append(deepcopy(dict(value)))

    _require(len(rows) >= 6, "raw transformer-chain trace is too short")
    _require(rows[0]["event"] == "capture_start", "raw transformer-chain trace does not start with capture_start")
    _require(rows[-1]["event"] == "capture_end", "raw transformer-chain trace does not end with capture_end")
    _require(len({row["capture_id"] for row in rows}) == 1, "raw transformer-chain trace crosses capture identities")
    _require(sum(row["event"] == "capture_start" for row in rows) == 1, "raw transformer-chain repeats capture_start")
    _require(sum(row["event"] == "capture_end" for row in rows) == 1, "raw transformer-chain repeats capture_end")

    start = rows[0]["payload"]
    _closed(
        start,
        frozenset({"agent_id", "java_version", "targets", "redefine_supported", "retransform_supported"}),
        "capture_start payload",
    )
    _text(start["agent_id"], "capture_start.agent_id")
    _text(start["java_version"], "capture_start.java_version")
    _require(isinstance(start["targets"], list) and start["targets"], "capture_start.targets must be nonempty")
    expected_targets: dict[str, str] = {}
    for index, target in enumerate(start["targets"]):
        context = f"capture_start.targets[{index}]"
        _require(isinstance(target, Mapping), f"{context} must be an object")
        _closed(target, frozenset({"target_class", "expected_input_sha256"}), context)
        name = _text(target["target_class"], f"{context}.target_class")
        _require(name not in expected_targets, "capture_start repeats a target")
        expected_targets[name] = _sha256(
            target["expected_input_sha256"], f"{context}.expected_input_sha256"
        )
    _boolean(start["redefine_supported"], "capture_start.redefine_supported")
    _boolean(start["retransform_supported"], "capture_start.retransform_supported")

    refresh_ids: set[int] = set()
    superseded_refresh_ids: set[int] = set()
    rebuilt_refresh_ids: set[int] = set()
    epoch_ids: list[int] = []
    transformed: list[str] = []
    transform_failures = 0
    exclusion_changes = 0
    observer_failures = 0
    max_live_count = 0
    max_delegated_count = 0
    for row in rows[1:-1]:
        event = row["event"]
        payload = row["payload"]
        context = f"{event} payload at sequence {row['sequence']}"
        if event == "transformer_installed":
            _closed(payload, frozenset({"target_count", "retransform_requested"}), context)
            _require(payload["target_count"] == len(expected_targets), "transformer_installed target count drifted")
            _require(payload["retransform_requested"] is False, "transformer-chain observer must not request retransformation")
        elif event in {"transform_applied", "transform_rejected", "transform_failure"}:
            keys = {
                "target_class", "defining_loader_class", "defining_loader_identity",
                "target_code_source_uri", "input_sha256", "output_sha256", "reason",
            }
            if event == "transform_failure":
                keys |= {"exception_class", "message", "stack_top"}
            _closed(payload, frozenset(keys), context)
            target = _text(payload["target_class"], f"{context}.target_class")
            _require(target in expected_targets, f"{context} names an unexpected target")
            _text(payload["defining_loader_class"], f"{context}.defining_loader_class")
            _text(payload["defining_loader_identity"], f"{context}.defining_loader_identity")
            _text(payload["target_code_source_uri"], f"{context}.target_code_source_uri", nullable=True)
            observed = _sha256(payload["input_sha256"], f"{context}.input_sha256")
            _require(observed == expected_targets[target], f"{context} violates exact class-byte guard")
            if event == "transform_applied":
                _sha256(payload["output_sha256"], f"{context}.output_sha256")
                _require(payload["reason"] is None, f"{context}.reason must be null")
                transformed.append(target)
            else:
                _require(payload["output_sha256"] is None, f"{context}.output_sha256 must be null")
                _text(payload["reason"], f"{context}.reason")
                transform_failures += 1
        elif event == "provider_created":
            _closed(
                payload,
                frozenset({"provider_class", "provider_identity", "provider_loader_class", "provider_loader_identity", "provider_code_source_uri", "provider_exclusions"}),
                context,
            )
            for key in ("provider_class", "provider_identity", "provider_loader_class", "provider_loader_identity"):
                _text(payload[key], f"{context}.{key}")
            _text(payload["provider_code_source_uri"], f"{context}.provider_code_source_uri", nullable=True)
            _string_list(payload["provider_exclusions"], f"{context}.provider_exclusions", sorted_unique=True)
        elif event == "service_refresh_started":
            _closed(payload, frozenset({"service_class", "service_identity", "phase"}), context)
            for key in payload:
                _text(payload[key], f"{context}.{key}")
        elif event == "service_refresh_completed":
            keys = {"service_identity", "phase", "outcome"}
            if payload.get("outcome") == "threw":
                keys |= {"exception_class", "message", "stack_top"}
            _closed(payload, frozenset(keys), context)
            _require(payload["outcome"] in {"completed", "threw"}, f"{context}.outcome is unsupported")
        elif event == "refresh_requested":
            _closed(
                payload,
                frozenset({"refresh_id", "reason_code", "phase", "provider_identity", "delegated_cache_present", "previous_transformer_count", "live_transformer_count", "supersedes_refresh_id"}),
                context,
            )
            refresh_id = _integer(payload["refresh_id"], f"{context}.refresh_id", minimum=1)
            _require(refresh_id not in refresh_ids, "refresh identifiers are not unique")
            refresh_ids.add(refresh_id)
            _text(payload["reason_code"], f"{context}.reason_code")
            _text(payload["phase"], f"{context}.phase")
            _text(payload["provider_identity"], f"{context}.provider_identity")
            _boolean(payload["delegated_cache_present"], f"{context}.delegated_cache_present")
            _integer(payload["previous_transformer_count"], f"{context}.previous_transformer_count", minimum=-1)
            _integer(payload["live_transformer_count"], f"{context}.live_transformer_count")
            supersedes = _integer(payload["supersedes_refresh_id"], f"{context}.supersedes_refresh_id", minimum=1, nullable=True)
            _require(supersedes is None or supersedes in refresh_ids, f"{context} supersedes an unknown refresh")
            if supersedes is not None:
                _require(supersedes not in superseded_refresh_ids, f"{context} supersedes a refresh twice")
                superseded_refresh_ids.add(supersedes)
        elif event == "exclusion_change":
            _closed(payload, frozenset({"refresh_id", "action", "pattern", "present_before", "present_after", "provider_exclusions"}), context)
            _require(payload["refresh_id"] in refresh_ids, f"{context} names an unknown refresh")
            _require(payload["action"] in {"added", "reasserted"}, f"{context}.action is unsupported")
            _text(payload["pattern"], f"{context}.pattern")
            _boolean(payload["present_before"], f"{context}.present_before")
            _require(payload["present_after"] is True, f"{context} did not retain the exclusion")
            _string_list(payload["provider_exclusions"], f"{context}.provider_exclusions", sorted_unique=True)
            exclusion_changes += 1
        elif event == "chain_epoch":
            _closed(
                payload,
                frozenset({"epoch", "refresh_id", "refresh_reason", "phase", "provider_class", "provider_identity", "provider_loader_class", "provider_loader_identity", "provider_code_source_uri", "foundation_code_source_uri", "previous_transformer_count", "live_chain", "delegated_chain", "provider_exclusions", "foundation_transformer_exclusions"}),
                context,
            )
            epoch = _integer(payload["epoch"], f"{context}.epoch", minimum=1)
            epoch_ids.append(epoch)
            _require(payload["refresh_id"] in refresh_ids, f"{context} names an unknown refresh")
            _require(payload["refresh_id"] not in rebuilt_refresh_ids, f"{context} rebuilds one refresh twice")
            rebuilt_refresh_ids.add(payload["refresh_id"])
            for key in ("refresh_reason", "phase", "provider_class", "provider_identity", "provider_loader_class", "provider_loader_identity"):
                _text(payload[key], f"{context}.{key}")
            _text(payload["provider_code_source_uri"], f"{context}.provider_code_source_uri", nullable=True)
            _text(payload["foundation_code_source_uri"], f"{context}.foundation_code_source_uri", nullable=True)
            live = _chain_rows(payload["live_chain"], f"{context}.live_chain", raw=True)
            delegated = _chain_rows(payload["delegated_chain"], f"{context}.delegated_chain", raw=True)
            _require(live, f"{context}.live_chain must be nonempty")
            _require(delegated, f"{context}.delegated_chain must be nonempty")
            _require(len(delegated) <= len(live), f"{context} delegates more transformers than are live")
            max_live_count = max(max_live_count, len(live))
            max_delegated_count = max(max_delegated_count, len(delegated))
            _string_list(payload["provider_exclusions"], f"{context}.provider_exclusions", sorted_unique=True)
            _string_list(payload["foundation_transformer_exclusions"], f"{context}.foundation_transformer_exclusions", sorted_unique=True)
        elif event == "observer_failure":
            observer_failures += 1
            _closed(payload, frozenset({"operation", "exception_class", "message", "stack_top"}), context)
            _text(payload["operation"], f"{context}.operation")
            _text(payload["exception_class"], f"{context}.exception_class")

    _require(epoch_ids == list(range(1, len(epoch_ids) + 1)), "chain epoch identifiers are not contiguous")
    footer = rows[-1]["payload"]
    _closed(
        footer,
        frozenset({"health", "transformed_targets", "epoch_count", "refresh_count", "unresolved_refresh_count", "exclusion_change_count", "max_live_transformer_count", "max_delegated_transformer_count", "observer_failure_count", "write_failure"}),
        "capture_end payload",
    )
    _require(footer["health"] in {"healthy", "failed"}, "capture_end health is unsupported")
    _require(footer["transformed_targets"] == sorted(transformed), "capture_end transformed targets are stale")
    _require(_integer(footer["epoch_count"], "capture_end.epoch_count") == len(epoch_ids), "capture_end epoch count is stale")
    _require(_integer(footer["refresh_count"], "capture_end.refresh_count") == len(refresh_ids), "capture_end refresh count is stale")
    unresolved_refreshes = refresh_ids - superseded_refresh_ids - rebuilt_refresh_ids
    _require(_integer(footer["unresolved_refresh_count"], "capture_end.unresolved_refresh_count") == len(unresolved_refreshes), "capture_end unresolved refresh count is stale")
    _require(_integer(footer["exclusion_change_count"], "capture_end.exclusion_change_count") == exclusion_changes, "capture_end exclusion change count is stale")
    _require(_integer(footer["max_live_transformer_count"], "capture_end.max_live_transformer_count") == max_live_count, "capture_end maximum live transformer count is stale")
    _require(_integer(footer["max_delegated_transformer_count"], "capture_end.max_delegated_transformer_count") == max_delegated_count, "capture_end maximum delegated transformer count is stale")
    _require(_integer(footer["observer_failure_count"], "capture_end.observer_failure_count") == observer_failures, "capture_end observer failure count is stale")
    write_failure = _boolean(footer["write_failure"], "capture_end.write_failure")
    healthy = (
        transform_failures == 0
        and observer_failures == 0
        and write_failure is False
        and set(transformed) == set(expected_targets)
        and bool(epoch_ids)
        and max_live_count > 0
        and max_delegated_count > 0
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
    result: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        context = f"artifacts[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, _ARTIFACT_KEYS, context)
        result.append({
            "artifact_sha256": _sha256(row["artifact_sha256"], f"{context}.artifact_sha256"),
            "size_bytes": _integer(row["size_bytes"], f"{context}.size_bytes"),
            "label": _text(row["label"], f"{context}.label"),
            "code_source_uri": _text(row["code_source_uri"], f"{context}.code_source_uri"),
        })
    result.sort(key=lambda row: (row["artifact_sha256"], row["code_source_uri"]))
    _require(len(result) == len({row["code_source_uri"] for row in result}), "artifacts repeat a code_source_uri")
    return result


def _receipt_chain(raw: Sequence[Mapping[str, Any]], by_uri: Mapping[str, str], context: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        uri = row["code_source_uri"]
        _require(uri in by_uri, f"{context}[{index}] code source is absent from artifacts")
        result.append({
            "ordinal": row["ordinal"],
            "reported_name": row["reported_name"],
            "implementation_class": row["implementation_class"],
            "wrapper_class": row["wrapper_class"],
            "implementation_loader_class": row["implementation_loader_class"],
            "artifact_sha256": by_uri[uri],
            "priority": row["priority"],
            "delegation_excluded": row["delegation_excluded"],
        })
    return result


def build_transformer_chain_receipt(
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
    _require(normalized_inputs["candidate_lock"]["sha256"] == normalized_session["candidate_lock_sha256"], "candidate lock input and session disagree")
    _require(normalized_inputs["toolchain_lock"]["sha256"] == normalized_session["toolchain_lock_sha256"], "toolchain lock input and session disagree")
    rows = deepcopy(list(raw_events))
    _require(rows and {row.get("capture_id") for row in rows} == {normalized_session["capture_id"]}, "raw events and session capture identity disagree")
    normalized_artifacts = _artifacts(artifacts)
    by_uri = {row["code_source_uri"]: row["artifact_sha256"] for row in normalized_artifacts}
    start = rows[0]["payload"]
    footer = rows[-1]["payload"]

    transforms = [row for row in rows if row["event"] == "transform_applied"]
    targets: list[dict[str, Any]] = []
    expected = {row["target_class"]: row["expected_input_sha256"] for row in start["targets"]}
    for row in transforms:
        payload = row["payload"]
        uri = payload["target_code_source_uri"]
        _require(uri in by_uri, f"target {payload['target_class']} code source is absent from artifacts")
        targets.append({
            "target_class": payload["target_class"],
            "expected_input_sha256": expected[payload["target_class"]],
            "observed_input_sha256": payload["input_sha256"],
            "instrumented_output_sha256": payload["output_sha256"],
            "defining_loader_class": payload["defining_loader_class"],
            "target_artifact_sha256": by_uri[uri],
        })
    targets.sort(key=lambda row: row["target_class"])

    epoch_events = [row for row in rows if row["event"] == "chain_epoch"]
    epoch_by_refresh = {row["payload"]["refresh_id"]: row["payload"]["epoch"] for row in epoch_events}
    superseded = {
        row["payload"]["supersedes_refresh_id"]
        for row in rows if row["event"] == "refresh_requested"
        and row["payload"]["supersedes_refresh_id"] is not None
    }
    refreshes = []
    for row in rows:
        if row["event"] != "refresh_requested":
            continue
        payload = row["payload"]
        refresh_id = payload["refresh_id"]
        outcome = "rebuilt" if refresh_id in epoch_by_refresh else (
            "superseded" if refresh_id in superseded else "invalidated_at_shutdown"
        )
        refreshes.append({
            "sequence": row["sequence"],
            "refresh_id": refresh_id,
            "reason_code": payload["reason_code"],
            "phase": payload["phase"],
            "delegated_cache_present": payload["delegated_cache_present"],
            "previous_transformer_count": payload["previous_transformer_count"],
            "live_transformer_count": payload["live_transformer_count"],
            "supersedes_refresh_id": payload["supersedes_refresh_id"],
            "outcome": outcome,
            "epoch": epoch_by_refresh.get(refresh_id),
        })

    epochs = []
    for row in epoch_events:
        payload = row["payload"]
        provider_uri = payload["provider_code_source_uri"]
        foundation_uri = payload["foundation_code_source_uri"]
        _require(provider_uri in by_uri, f"epoch {payload['epoch']} provider code source is absent from artifacts")
        _require(foundation_uri in by_uri, f"epoch {payload['epoch']} Foundation code source is absent from artifacts")
        epochs.append({
            "epoch": payload["epoch"],
            "sequence": row["sequence"],
            "refresh_id": payload["refresh_id"],
            "refresh_reason": payload["refresh_reason"],
            "phase": payload["phase"],
            "provider_class": payload["provider_class"],
            "provider_loader_class": payload["provider_loader_class"],
            "provider_artifact_sha256": by_uri[provider_uri],
            "foundation_artifact_sha256": by_uri[foundation_uri],
            "previous_transformer_count": payload["previous_transformer_count"],
            "live_chain": _receipt_chain(payload["live_chain"], by_uri, f"epoch {payload['epoch']}.live_chain"),
            "delegated_chain": _receipt_chain(payload["delegated_chain"], by_uri, f"epoch {payload['epoch']}.delegated_chain"),
            "provider_exclusions": payload["provider_exclusions"],
            "foundation_transformer_exclusions": payload["foundation_transformer_exclusions"],
        })

    failures = []
    for row in rows:
        if row["event"] not in {"transform_rejected", "transform_failure", "observer_failure"}:
            continue
        payload = row["payload"]
        failures.append({
            "sequence": row["sequence"],
            "event": row["event"],
            "reason_code": payload.get("reason") or payload.get("operation"),
            "exception_class": payload.get("exception_class"),
        })

    state = "complete" if footer["health"] == "healthy" else "failed"
    normalized_limitations = sorted(set(MANDATORY_LIMITATIONS) | {
        _text(value, f"limitations[{index}]")
        for index, value in enumerate(limitations)
    })
    material = {
        "format": TRANSFORMER_CHAIN_RECEIPT_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": normalized_session,
        "inputs": normalized_inputs,
        "artifacts": normalized_artifacts,
        "observation": {
            "mechanism": "java_instrument_exact_cleanroom_transformer_provider",
            "agent_id": start["agent_id"],
            "java_version": start["java_version"],
            "targets": targets,
        },
        "refreshes": refreshes,
        "epochs": epochs,
        "failures": failures,
        "health": {
            "state": state,
            "start_health": "starting",
            "end_health": footer["health"],
            "write_failure": footer["write_failure"],
        },
        "summary": {
            "epoch_count": len(epochs),
            "refresh_count": len(refreshes),
            "rebuilt_refresh_count": sum(row["outcome"] == "rebuilt" for row in refreshes),
            "superseded_refresh_count": sum(row["outcome"] == "superseded" for row in refreshes),
            "unresolved_refresh_count": sum(row["outcome"] == "invalidated_at_shutdown" for row in refreshes),
            "exclusion_change_count": footer["exclusion_change_count"],
            "max_live_transformer_count": max((len(row["live_chain"]) for row in epochs), default=0),
            "max_delegated_transformer_count": max((len(row["delegated_chain"]) for row in epochs), default=0),
            "failure_count": len(failures),
        },
        "limitations": normalized_limitations,
        "boundaries": {
            "transformer_chain_epochs_proved": state == "complete" and bool(epochs),
            "provider_exclusions_proved": state == "complete" and bool(epochs),
            "foundation_transformer_exclusions_proved": state == "complete" and bool(epochs),
            "transformer_execution_completion_proved": False,
            "final_class_bytes_proved": False,
        },
    }
    return {
        **material,
        "receipt_id": TRANSFORMER_CHAIN_RECEIPT_PREFIX
        + hashlib.sha256(canonical_transformer_chain_json_bytes(material)).hexdigest(),
    }


def parse_transformer_chain_receipt(value: Any) -> dict[str, Any]:
    """Validate a closed, content-addressed transformer-chain receipt."""

    _require(isinstance(value, Mapping), "transformer-chain receipt must be an object")
    expected_keys = frozenset({
        "format", "schema_version", "canonicalization_id", "receipt_id",
        "session", "inputs", "artifacts", "observation", "refreshes", "epochs",
        "failures", "health", "summary", "limitations", "boundaries",
    })
    _closed(value, expected_keys, "transformer-chain receipt")
    _require(value["format"] == TRANSFORMER_CHAIN_RECEIPT_FORMAT, "transformer-chain receipt format is not V1")
    _require(value["schema_version"] == 1, "transformer-chain receipt schema_version is not 1")
    _require(value["canonicalization_id"] == CANONICALIZATION_ID, "transformer-chain canonicalization is unsupported")
    supplied = deepcopy(dict(value))
    receipt_id = supplied.pop("receipt_id")
    expected_id = TRANSFORMER_CHAIN_RECEIPT_PREFIX + hashlib.sha256(
        canonical_transformer_chain_json_bytes(supplied)
    ).hexdigest()
    _require(receipt_id == expected_id, "transformer-chain receipt identity mismatch")

    session = _session(value["session"])
    _require(isinstance(value["inputs"], Mapping) and set(value["inputs"]) == set(_INPUT_NAMES), "inputs are not the exact V1 input set")
    inputs = {name: _file_record(value["inputs"][name], f"inputs.{name}") for name in _INPUT_NAMES}
    _require(inputs["candidate_lock"]["sha256"] == session["candidate_lock_sha256"], "candidate lock input and session disagree")
    _require(inputs["toolchain_lock"]["sha256"] == session["toolchain_lock_sha256"], "toolchain lock input and session disagree")
    artifacts = _artifacts(value["artifacts"])
    artifact_hashes = {row["artifact_sha256"] for row in artifacts}

    observation = value["observation"]
    _require(isinstance(observation, Mapping), "observation must be an object")
    _closed(observation, frozenset({"mechanism", "agent_id", "java_version", "targets"}), "observation")
    _require(observation["mechanism"] == "java_instrument_exact_cleanroom_transformer_provider", "observation mechanism is unsupported")
    _text(observation["agent_id"], "observation.agent_id")
    _text(observation["java_version"], "observation.java_version")
    _require(isinstance(observation["targets"], list) and observation["targets"], "observation.targets must be nonempty")
    for index, target in enumerate(observation["targets"]):
        context = f"observation.targets[{index}]"
        _closed(target, frozenset({"target_class", "expected_input_sha256", "observed_input_sha256", "instrumented_output_sha256", "defining_loader_class", "target_artifact_sha256"}), context)
        _text(target["target_class"], f"{context}.target_class")
        _require(_sha256(target["expected_input_sha256"], f"{context}.expected_input_sha256") == _sha256(target["observed_input_sha256"], f"{context}.observed_input_sha256"), f"{context} exact byte guard was not met")
        _sha256(target["instrumented_output_sha256"], f"{context}.instrumented_output_sha256")
        _text(target["defining_loader_class"], f"{context}.defining_loader_class")
        _require(_sha256(target["target_artifact_sha256"], f"{context}.target_artifact_sha256") in artifact_hashes, f"{context} artifact is absent")

    refreshes = value["refreshes"]
    _require(isinstance(refreshes, list) and refreshes, "refreshes must be a nonempty array")
    refresh_ids: set[int] = set()
    refresh_sequences: list[int] = []
    for index, row in enumerate(refreshes):
        context = f"refreshes[{index}]"
        _closed(row, frozenset({"sequence", "refresh_id", "reason_code", "phase", "delegated_cache_present", "previous_transformer_count", "live_transformer_count", "supersedes_refresh_id", "outcome", "epoch"}), context)
        refresh_sequences.append(_integer(row["sequence"], f"{context}.sequence"))
        refresh_id = _integer(row["refresh_id"], f"{context}.refresh_id", minimum=1)
        _require(refresh_id not in refresh_ids, "refreshes repeat a refresh_id")
        refresh_ids.add(refresh_id)
        _text(row["reason_code"], f"{context}.reason_code")
        _text(row["phase"], f"{context}.phase")
        _boolean(row["delegated_cache_present"], f"{context}.delegated_cache_present")
        _integer(row["previous_transformer_count"], f"{context}.previous_transformer_count", minimum=-1)
        _integer(row["live_transformer_count"], f"{context}.live_transformer_count")
        _integer(row["supersedes_refresh_id"], f"{context}.supersedes_refresh_id", minimum=1, nullable=True)
        _require(row["outcome"] in {"rebuilt", "superseded", "invalidated_at_shutdown"}, f"{context}.outcome is unsupported")
        _integer(row["epoch"], f"{context}.epoch", minimum=1, nullable=True)
        _require((row["outcome"] == "rebuilt") == (row["epoch"] is not None), f"{context} outcome and epoch disagree")
    _require(refresh_sequences == sorted(refresh_sequences), "refreshes are not in source sequence order")
    _require(refresh_ids == set(range(1, len(refreshes) + 1)), "refresh identifiers are not contiguous")
    for row in refreshes:
        supersedes = row["supersedes_refresh_id"]
        if supersedes is not None:
            _require(supersedes < row["refresh_id"], "refresh supersedes a later refresh")
            prior = next(item for item in refreshes if item["refresh_id"] == supersedes)
            _require(prior["outcome"] == "superseded", "superseded refresh outcome is stale")

    epochs = value["epochs"]
    _require(isinstance(epochs, list) and epochs, "epochs must be a nonempty array")
    for index, row in enumerate(epochs):
        context = f"epochs[{index}]"
        _closed(row, frozenset({"epoch", "sequence", "refresh_id", "refresh_reason", "phase", "provider_class", "provider_loader_class", "provider_artifact_sha256", "foundation_artifact_sha256", "previous_transformer_count", "live_chain", "delegated_chain", "provider_exclusions", "foundation_transformer_exclusions"}), context)
        _require(row["epoch"] == index + 1, "epoch ordinals are not contiguous")
        _integer(row["sequence"], f"{context}.sequence")
        _require(row["refresh_id"] in refresh_ids, f"{context} names an unknown refresh")
        for key in ("refresh_reason", "phase", "provider_class", "provider_loader_class"):
            _text(row[key], f"{context}.{key}")
        _require(_sha256(row["provider_artifact_sha256"], f"{context}.provider_artifact_sha256") in artifact_hashes, f"{context} provider artifact is absent")
        _require(_sha256(row["foundation_artifact_sha256"], f"{context}.foundation_artifact_sha256") in artifact_hashes, f"{context} Foundation artifact is absent")
        _integer(row["previous_transformer_count"], f"{context}.previous_transformer_count", minimum=-1)
        live = _chain_rows(row["live_chain"], f"{context}.live_chain", raw=False)
        delegated = _chain_rows(row["delegated_chain"], f"{context}.delegated_chain", raw=False)
        _require(live and delegated and len(delegated) <= len(live), f"{context} chain cardinality is invalid")
        _require(all(item["artifact_sha256"] in artifact_hashes for item in live + delegated), f"{context} references an absent artifact")
        _string_list(row["provider_exclusions"], f"{context}.provider_exclusions", sorted_unique=True)
        _string_list(row["foundation_transformer_exclusions"], f"{context}.foundation_transformer_exclusions", sorted_unique=True)
        matching = [refresh for refresh in refreshes if refresh["refresh_id"] == row["refresh_id"]]
        _require(len(matching) == 1 and matching[0]["epoch"] == row["epoch"] and matching[0]["reason_code"] == row["refresh_reason"], f"{context} does not join its refresh")

    failures = value["failures"]
    _require(isinstance(failures, list), "failures must be an array")
    for index, row in enumerate(failures):
        context = f"failures[{index}]"
        _closed(row, frozenset({"sequence", "event", "reason_code", "exception_class"}), context)
        _integer(row["sequence"], f"{context}.sequence")
        _require(row["event"] in {"transform_rejected", "transform_failure", "observer_failure"}, f"{context}.event is unsupported")
        _text(row["reason_code"], f"{context}.reason_code")
        _text(row["exception_class"], f"{context}.exception_class", nullable=True)

    health = value["health"]
    _closed(health, frozenset({"state", "start_health", "end_health", "write_failure"}), "health")
    _require(health["state"] in {"complete", "failed"}, "health.state is unsupported")
    _require(health["start_health"] == "starting", "health.start_health is unsupported")
    _require(health["end_health"] in {"healthy", "failed"}, "health.end_health is unsupported")
    _boolean(health["write_failure"], "health.write_failure")
    expected_complete = (
        health["end_health"] == "healthy"
        and health["write_failure"] is False
        and not failures
    )
    _require((health["state"] == "complete") == expected_complete, "transformer-chain health fields disagree")

    summary = value["summary"]
    _closed(summary, frozenset({"epoch_count", "refresh_count", "rebuilt_refresh_count", "superseded_refresh_count", "unresolved_refresh_count", "exclusion_change_count", "max_live_transformer_count", "max_delegated_transformer_count", "failure_count"}), "summary")
    expected_summary = {
        "epoch_count": len(epochs),
        "refresh_count": len(refreshes),
        "rebuilt_refresh_count": sum(row["outcome"] == "rebuilt" for row in refreshes),
        "superseded_refresh_count": sum(row["outcome"] == "superseded" for row in refreshes),
        "unresolved_refresh_count": sum(row["outcome"] == "invalidated_at_shutdown" for row in refreshes),
        "exclusion_change_count": summary["exclusion_change_count"],
        "max_live_transformer_count": max(len(row["live_chain"]) for row in epochs),
        "max_delegated_transformer_count": max(len(row["delegated_chain"]) for row in epochs),
        "failure_count": len(failures),
    }
    _require(summary == expected_summary, "transformer-chain summary is stale")
    _integer(summary["exclusion_change_count"], "summary.exclusion_change_count")

    limitations = _string_list(value["limitations"], "limitations", sorted_unique=True)
    _require(set(MANDATORY_LIMITATIONS) <= set(limitations), "transformer-chain receipt omits a mandatory limitation")
    boundaries = value["boundaries"]
    _closed(boundaries, frozenset({"transformer_chain_epochs_proved", "provider_exclusions_proved", "foundation_transformer_exclusions_proved", "transformer_execution_completion_proved", "final_class_bytes_proved"}), "boundaries")
    complete = health["state"] == "complete"
    _require(boundaries == {
        "transformer_chain_epochs_proved": complete,
        "provider_exclusions_proved": complete,
        "foundation_transformer_exclusions_proved": complete,
        "transformer_execution_completion_proved": False,
        "final_class_bytes_proved": False,
    }, "transformer-chain boundaries overclaim or are stale")
    return deepcopy(dict(value))


def render_transformer_chain_receipt(value: Mapping[str, Any]) -> bytes:
    return canonical_transformer_chain_json_bytes(parse_transformer_chain_receipt(value)) + b"\n"


def write_transformer_chain_receipt(path: Path, value: Mapping[str, Any]) -> None:
    encoded = render_transformer_chain_receipt(value)
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
