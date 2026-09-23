"""Fail-closed composition and comparison of Crucible runtime evidence.

V2 is a manifest over existing typed receipts.  It does not copy their facts or
silently reinterpret an older receipt version.  Profile adapters name runtime
epochs and produce bounded semantic fingerprints; Atlas remains the authority
for interpretation of admitted snapshots.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Mapping, Sequence


RUNTIME_SNAPSHOT_FORMAT = "workbench-crucible-runtime-snapshot-v2"
RUNTIME_SNAPSHOT_PREFIX = "crucible-runtime-snapshot:sha256:"
COMPARISON_FORMAT = "workbench-crucible-runtime-snapshot-comparison-v2"
COMPARISON_PREFIX = "crucible-runtime-snapshot-comparison:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_PROCESS_OUTCOMES = frozenset(
    {"running", "complete", "crashed", "failed", "incomplete"}
)
_CAPABILITY_STATES = frozenset(
    {"observed", "partial", "not_observed", "unavailable", "failed"}
)
_OBSERVER_STATES = frozenset({"healthy", "degraded", "failed", "incomplete"})
_COMPARISON_STATES = frozenset(
    {"equivalent", "changed", "unavailable", "not-observed"}
)

MANDATORY_LIMITATIONS = (
    "The snapshot composes semantically validated receipts; it does not replace their facts, custody, or limitations.",
    "Atlas may interpret this immutable Crucible snapshot but cannot rewrite it into Atlas-owned runtime truth.",
    "Unavailable means the profile epoch adapter declares no producer for the capability; not_observed means a producer is declared but this capture did not observe it.",
    "A semantic fingerprint compares only the bounded profile-adapter projection for one capability, not the whole runtime.",
)


class RuntimeSnapshotValidationError(ValueError):
    """V2 material cannot truthfully represent one runtime snapshot."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeSnapshotValidationError(message)


def canonical_runtime_snapshot_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeSnapshotValidationError(
            f"cannot canonically encode runtime snapshot: {exc}"
        ) from exc


def _content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_runtime_snapshot_json_bytes(value)).hexdigest()


def _closed(
    value: Mapping[str, Any],
    required: frozenset[str],
    context: str,
) -> None:
    missing = required - set(value)
    unknown = set(value) - required
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


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _string_set(value: Any, context: str) -> list[str]:
    _require(isinstance(value, list), f"{context} must be an array")
    result = [_text(item, f"{context}[{index}]") for index, item in enumerate(value)]
    _require(len(result) == len(set(result)), f"{context} contains duplicates")
    return sorted(result)


def _capability_name(value: Any, context: str) -> str:
    name = _text(value, context, maximum=128)
    _require(
        _CAPABILITY_RE.fullmatch(name) is not None,
        f"{context} is not a canonical capability name",
    )
    return name


def _source_label(value: Any) -> str:
    label = _text(value, "receipt source_label", maximum=1024)
    path = PurePosixPath(label)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in label,
        "receipt source_label must be a portable relative logical path",
    )
    return label


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _strict_json(encoded: bytes) -> dict[str, Any]:
    _require(
        isinstance(encoded, bytes) and bool(encoded),
        "receipt bytes must be nonempty bytes",
    )
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite number {item}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise RuntimeSnapshotValidationError(f"receipt is malformed JSON: {exc}") from exc
    _require(isinstance(value, dict), "receipt root must be an object")
    return value


def _semantic_projection(
    format_name: str,
    admitted: Mapping[str, Any],
) -> tuple[str, str, Mapping[str, Any], list[dict[str, str]], list[str]]:
    """Return receipt ID, role, bounded semantics, locks, and limitations."""

    if format_name == "workbench-crucible-mixin-transformation-ledger-v1":
        from workbench_crucible_mixin_custody import parse_ledger

        receipt = parse_ledger(admitted)
        observations = []
        for row in receipt["observations"]:
            projection = {
                key: deepcopy(row[key])
                for key in (
                    "sequence",
                    "stage",
                    "outcome",
                    "phase",
                    "subject",
                    "input_bytecode_sha256",
                    "stage_bytecode_sha256",
                    "final_bytecode_sha256",
                )
                if key in row
            }
            projection["evidence_kind"] = row["evidence"]["kind"]
            observations.append(projection)
        semantic = {
            "launch_state": receipt["launch_state"],
            "observations": observations,
            "summary": receipt["summary"],
        }
        return (
            receipt["ledger_id"],
            "mixin.transformation-lifecycle",
            semantic,
            [],
            list(receipt["limitations"]),
        )

    if format_name == "workbench-crucible-mixin-runtime-service-receipt-v1":
        from workbench_crucible_mixin_custody import parse_runtime_service_receipt

        receipt = parse_runtime_service_receipt(admitted)
        semantic_observations = []
        for row in receipt["observations"]:
            semantic_observations.append(
                {
                    key: deepcopy(row[key])
                    for key in (
                        "sequence",
                        "kind",
                        "service_name",
                        "service_class",
                        "subsystem_version",
                        "environment",
                        "source_artifact_sha256",
                    )
                    if key in row
                }
            )
        enumeration = receipt["provider_enumeration"]
        semantic_enumeration = {
            key: deepcopy(enumeration[key])
            for key in ("state", "mechanism", "providers", "failure")
        }
        semantic = {
            "observations": semantic_observations,
            "provider_enumeration": semantic_enumeration,
            "summary": receipt["summary"],
        }
        return (
            receipt["receipt_id"],
            "mixin.runtime-service",
            semantic,
            [
                {
                    "kind": "transformer-toolchain",
                    "sha256": receipt["session"]["candidate_toolchain_lock_sha256"],
                }
            ],
            list(receipt["limitations"]),
        )

    if (
        format_name
        == "workbench-crucible-mixin-defining-loader-discovery-trace-receipt-v2"
    ):
        from workbench_crucible_mixins import parse_defining_loader_trace_receipt

        receipt = parse_defining_loader_trace_receipt(admitted)
        observation = receipt["observation"]
        semantic_attempts = []
        for attempt in receipt["ordered_attempts"]:
            events = []
            for event in attempt["events"]:
                payload = event["payload"]
                events.append(
                    {
                        "event": event["event"],
                        "payload": {
                            key: deepcopy(payload[key])
                            for key in (
                                "provider_class",
                                "provider_loader_class",
                                "service_class_name",
                                "service_name",
                                "valid",
                                "outcome",
                                "exception_class",
                            )
                            if key in payload
                        },
                    }
                )
            semantic_attempts.append(
                {
                    "attempt_id": attempt["attempt_id"],
                    "stage": attempt["stage"],
                    "mechanism": attempt["mechanism"],
                    "terminal_outcome": attempt["terminal_outcome"],
                    "events": events,
                }
            )
        semantic_failures = []
        for failure in receipt["failures"]:
            semantic_failures.append(
                {
                    key: deepcopy(failure[key])
                    for key in (
                        "stage",
                        "event",
                        "attempt_id",
                        "exception_class",
                    )
                    if key in failure
                }
            )
        semantic = {
            "target_class": observation["target_class"],
            "defining_loader_class": observation["defining_loader_class"],
            "bypass_properties": receipt["bypass_properties"],
            "ordered_attempts": semantic_attempts,
            "selection": {
                key: deepcopy(receipt["selection"][key])
                for key in (
                    "attempt_id",
                    "provider_class",
                    "provider_loader_class",
                    "provider_artifact_sha256",
                )
            },
            "failures": semantic_failures,
            "health": receipt["health"],
        }
        return (
            receipt["receipt_id"],
            "mixin.service-discovery",
            semantic,
            [
                {
                    "kind": "candidate",
                    "sha256": receipt["session"]["candidate_lock_sha256"],
                },
                {
                    "kind": "transformer-toolchain",
                    "sha256": receipt["session"]["toolchain_lock_sha256"],
                },
            ],
            [
                "This trace proves one defining-loader discovery path, not transformation completion or final defined bytes."
            ],
        )

    if (
        format_name
        == "workbench-crucible-mixin-selected-service-components-receipt-v1"
    ):
        from workbench_crucible_mixins import parse_service_components_receipt

        receipt = parse_service_components_receipt(admitted)
        observation = receipt["observation"]
        semantic = {
            "observation": {
                key: deepcopy(observation[key])
                for key in (
                    "target_class",
                    "observed_input_sha256",
                    "instrumented_output_sha256",
                    "defining_loader_class",
                    "target_artifact_sha256",
                )
            },
            "components": [
                {
                    key: deepcopy(row[key])
                    for key in (
                        "role",
                        "implementation_class",
                        "implementation_loader_class",
                        "artifact_sha256",
                        "reported_name",
                    )
                }
                for row in receipt["components"]
            ],
            "failures": [
                {
                    key: deepcopy(row[key])
                    for key in ("role", "operation", "exception_class")
                }
                for row in receipt["failures"]
            ],
            "health": receipt["health"],
            "summary": receipt["summary"],
        }
        return (
            receipt["receipt_id"],
            "mixin.service-components",
            semantic,
            [
                {
                    "kind": "candidate",
                    "sha256": receipt["session"]["candidate_lock_sha256"],
                },
                {
                    "kind": "transformer-toolchain",
                    "sha256": receipt["session"]["toolchain_lock_sha256"],
                },
            ],
            list(receipt["limitations"]),
        )

    if format_name == "workbench-crucible-mixin-config-lifecycle-receipt-v1":
        from workbench_crucible_mixins import parse_config_lifecycle_receipt

        receipt = parse_config_lifecycle_receipt(admitted)
        semantic_configurations = []
        for row in receipt["configurations"]:
            semantic_configurations.append(
                {
                    "requested_config": row["requested_config"],
                    "config_name": row["config_name"],
                    "fallback_phase": row["fallback_phase"],
                    "resource": {
                        key: deepcopy(row["resource"][key])
                        for key in (
                            "url_basis",
                            "owner_id",
                            "artifact_sha256",
                            "entry_sha256",
                        )
                    },
                    "feature_check": deepcopy(row["feature_check"]),
                    "admission": deepcopy(row["admission"]),
                    "phase_checks": [
                        {
                            key: deepcopy(check[key])
                            for key in (
                                "queued_phase",
                                "consumption_phase",
                                "eligible",
                                "outcome",
                                "reason_code",
                            )
                        }
                        for check in row["phase_checks"]
                    ],
                    "stages": [
                        {
                            key: deepcopy(stage[key])
                            for key in ("stage", "outcome", "consumption_phase")
                        }
                        for stage in row["stages"]
                    ],
                    "terminal": deepcopy(row["terminal"]),
                }
            )
        semantic = {
            "configurations": semantic_configurations,
            "failures": [
                {
                    key: deepcopy(row[key])
                    for key in ("event", "reason_code", "exception_class")
                }
                for row in receipt["failures"]
            ],
            "health": receipt["health"],
            "summary": receipt["summary"],
        }
        return (
            receipt["receipt_id"],
            "mixin.config-lifecycle",
            semantic,
            [
                {
                    "kind": "candidate",
                    "sha256": receipt["session"]["candidate_lock_sha256"],
                },
                {
                    "kind": "transformer-toolchain",
                    "sha256": receipt["session"]["toolchain_lock_sha256"],
                },
            ],
            list(receipt["limitations"]),
        )

    if (
        format_name
        == "workbench-crucible-mixin-transformer-chain-epoch-receipt-v1"
    ):
        from workbench_crucible_mixins import parse_transformer_chain_receipt

        receipt = parse_transformer_chain_receipt(admitted)
        semantic_epochs = []
        for epoch in receipt["epochs"]:
            semantic_epochs.append(
                {
                    "epoch": epoch["epoch"],
                    "refresh_reason": epoch["refresh_reason"],
                    "phase": epoch["phase"],
                    "provider_class": epoch["provider_class"],
                    "provider_loader_class": epoch["provider_loader_class"],
                    "provider_artifact_sha256": epoch["provider_artifact_sha256"],
                    "foundation_artifact_sha256": epoch[
                        "foundation_artifact_sha256"
                    ],
                    "live_chain": deepcopy(epoch["live_chain"]),
                    "delegated_chain": deepcopy(epoch["delegated_chain"]),
                    "provider_exclusions": deepcopy(epoch["provider_exclusions"]),
                    "foundation_transformer_exclusions": deepcopy(
                        epoch["foundation_transformer_exclusions"]
                    ),
                }
            )
        semantic = {
            "epochs": semantic_epochs,
            "refreshes": [
                {
                    key: deepcopy(row[key])
                    for key in (
                        "refresh_id",
                        "reason_code",
                        "phase",
                        "live_transformer_count",
                        "outcome",
                        "epoch",
                    )
                }
                for row in receipt["refreshes"]
            ],
            "failures": [
                {
                    key: deepcopy(row[key])
                    for key in ("event", "reason_code", "exception_class")
                }
                for row in receipt["failures"]
            ],
            "health": receipt["health"],
            "summary": receipt["summary"],
        }
        return (
            receipt["receipt_id"],
            "mixin.transformer-chain",
            semantic,
            [
                {
                    "kind": "candidate",
                    "sha256": receipt["session"]["candidate_lock_sha256"],
                },
                {
                    "kind": "transformer-toolchain",
                    "sha256": receipt["session"]["toolchain_lock_sha256"],
                },
            ],
            list(receipt["limitations"]),
        )

    if (
        format_name
        == "workbench-crucible-mixin-final-class-definition-receipt-v1"
    ):
        from workbench_crucible_mixins import parse_final_definition_receipt

        receipt = parse_final_definition_receipt(admitted)
        semantic = {
            "foundation": deepcopy(receipt["foundation"]),
            "definitions": [
                {
                    key: deepcopy(row[key])
                    for key in (
                        "target_class",
                        "defined_class",
                        "defining_loader_class",
                        "target_artifact_sha256",
                        "dump_relative_path",
                        "final_bytecode_sha256",
                        "final_bytecode_size",
                    )
                }
                for row in receipt["definitions"]
            ],
            "failures": [
                {
                    key: deepcopy(row[key])
                    for key in ("event", "target_class", "reason_code", "exception_class")
                }
                for row in receipt["failures"]
            ],
            "health": receipt["health"],
            "summary": receipt["summary"],
        }
        return (
            receipt["receipt_id"],
            "mixin.final-class-definition",
            semantic,
            [
                {
                    "kind": "candidate",
                    "sha256": receipt["session"]["candidate_lock_sha256"],
                },
                {
                    "kind": "transformer-toolchain",
                    "sha256": receipt["session"]["toolchain_lock_sha256"],
                },
            ],
            list(receipt["limitations"]),
        )

    raise RuntimeSnapshotValidationError(
        f"runtime snapshot V2 has no semantic adapter for receipt format {format_name!r}"
    )


def _receipt_scope(format_name: str, admitted: Mapping[str, Any]) -> dict[str, Any]:
    if format_name in {
        "workbench-crucible-mixin-transformation-ledger-v1",
        "workbench-crucible-mixin-runtime-service-receipt-v1",
    }:
        session = admitted["session"]
        return {
            "launch_id": session["launch_id"],
            "profile_id": session["profile_id"],
            "physical_side": session["side"],
            "session_id": session["session_id"],
            "capture_id": None,
        }
    session = admitted["session"]
    return {
        "launch_id": session["launch_id"],
        "profile_id": session["profile_id"],
        "physical_side": session["side"],
        "session_id": None,
        "capture_id": session["capture_id"],
    }


def bind_known_receipt(encoded: bytes, *, source_label: str) -> dict[str, Any]:
    """Validate a known typed receipt and return one V2 manifest binding."""

    value = _strict_json(encoded)
    format_name = _text(value.get("format"), "receipt format", maximum=256)
    try:
        receipt_id, role, semantic, locks, limitations = _semantic_projection(
            format_name, value
        )
    except RuntimeSnapshotValidationError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeSnapshotValidationError(
            f"{format_name} semantic validation failed: {exc}"
        ) from exc
    scope = _receipt_scope(format_name, value)
    binding = {
        "role": role,
        "format": format_name,
        "receipt_id": receipt_id,
        "source_label": _source_label(source_label),
        "source_sha256": hashlib.sha256(encoded).hexdigest(),
        "semantic_fingerprint": _content_digest(semantic),
        "validator": {
            "state": "passed",
            "adapter": f"workbench-crucible-runtime-snapshot-v2:{format_name}",
        },
        "scope": scope,
        "lock_bindings": locks,
        "limitations": sorted(set(limitations)),
    }
    return _normalize_binding(binding)


def _normalize_binding(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "receipt binding must be an object")
    _closed(
        value,
        frozenset(
            {
                "role",
                "format",
                "receipt_id",
                "source_label",
                "source_sha256",
                "semantic_fingerprint",
                "validator",
                "scope",
                "lock_bindings",
                "limitations",
            }
        ),
        "receipt binding",
    )
    role = _capability_name(value["role"], "receipt binding role")
    validator = value["validator"]
    _require(isinstance(validator, Mapping), "receipt binding validator must be an object")
    _closed(validator, frozenset({"state", "adapter"}), "receipt binding validator")
    _require(
        validator["state"] == "passed",
        "receipt binding must come from a passed semantic validator",
    )
    scope = value["scope"]
    _require(isinstance(scope, Mapping), "receipt binding scope must be an object")
    _closed(
        scope,
        frozenset(
            {"launch_id", "profile_id", "physical_side", "session_id", "capture_id"}
        ),
        "receipt binding scope",
    )
    _require(scope["physical_side"] in _SIDES, "receipt scope physical_side is unsupported")
    normalized_scope = {
        "launch_id": _text(scope["launch_id"], "receipt scope launch_id"),
        "profile_id": _text(scope["profile_id"], "receipt scope profile_id"),
        "physical_side": scope["physical_side"],
        "session_id": _nullable_text(scope["session_id"], "receipt scope session_id"),
        "capture_id": _nullable_text(scope["capture_id"], "receipt scope capture_id"),
    }
    locks = value["lock_bindings"]
    _require(isinstance(locks, list), "receipt lock_bindings must be an array")
    normalized_locks: list[dict[str, str]] = []
    for index, row in enumerate(locks):
        _require(isinstance(row, Mapping), f"receipt lock_bindings[{index}] must be an object")
        _closed(row, frozenset({"kind", "sha256"}), f"receipt lock_bindings[{index}]")
        normalized_locks.append(
            {
                "kind": _text(row["kind"], f"receipt lock_bindings[{index}].kind", maximum=128),
                "sha256": _sha256(row["sha256"], f"receipt lock_bindings[{index}].sha256"),
            }
        )
    normalized_locks.sort(key=lambda row: (row["kind"], row["sha256"]))
    _require(
        len(normalized_locks)
        == len({(row["kind"], row["sha256"]) for row in normalized_locks}),
        "receipt lock_bindings contain duplicates",
    )
    limitations = _string_set(value["limitations"], "receipt binding limitations")
    return {
        "role": role,
        "format": _text(value["format"], "receipt binding format", maximum=256),
        "receipt_id": _text(value["receipt_id"], "receipt binding receipt_id"),
        "source_label": _source_label(value["source_label"]),
        "source_sha256": _sha256(value["source_sha256"], "receipt binding source_sha256"),
        "semantic_fingerprint": _sha256(
            value["semantic_fingerprint"], "receipt binding semantic_fingerprint"
        ),
        "validator": {
            "state": "passed",
            "adapter": _text(validator["adapter"], "receipt binding validator adapter"),
        },
        "scope": normalized_scope,
        "lock_bindings": normalized_locks,
        "limitations": limitations,
    }


def capability_from_receipts(
    name: str,
    *,
    state: str,
    receipt_bindings: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one evidence-backed bounded capability projection."""

    _require(state in {"observed", "partial"}, "receipt-backed state must be observed or partial")
    normalized = [_normalize_binding(value) for value in receipt_bindings]
    _require(bool(normalized), "receipt-backed capability needs at least one receipt")
    receipt_ids = sorted({row["receipt_id"] for row in normalized})
    _require(len(receipt_ids) == len(normalized), "receipt-backed capability repeats a receipt")
    semantic_fingerprint = _content_digest(
        [
            {
                "role": row["role"],
                "semantic_fingerprint": row["semantic_fingerprint"],
            }
            for row in sorted(
                normalized,
                key=lambda item: (item["role"], item["semantic_fingerprint"]),
            )
        ]
    )
    return _normalize_capability(
        {
            "name": name,
            "state": state,
            "receipt_ids": receipt_ids,
            "semantic_fingerprint": semantic_fingerprint,
            "reason_code": None,
            "limitations": list(limitations),
        }
    )


def capability_status(name: str, *, state: str, reason_code: str) -> dict[str, Any]:
    """Declare a capability that was unavailable or not observed."""

    _require(
        state in {"not_observed", "unavailable"},
        "evidence-free capability state must be not_observed or unavailable",
    )
    return _normalize_capability(
        {
            "name": name,
            "state": state,
            "receipt_ids": [],
            "semantic_fingerprint": None,
            "reason_code": reason_code,
            "limitations": [],
        }
    )


def _normalize_capability(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "capability must be an object")
    _closed(
        value,
        frozenset(
            {
                "name",
                "state",
                "receipt_ids",
                "semantic_fingerprint",
                "reason_code",
                "limitations",
            }
        ),
        "capability",
    )
    name = _capability_name(value["name"], "capability name")
    state = value["state"]
    _require(state in _CAPABILITY_STATES, f"capability {name} has unsupported state")
    receipt_ids = _string_set(value["receipt_ids"], f"capability {name} receipt_ids")
    fingerprint = value["semantic_fingerprint"]
    reason = value["reason_code"]
    if state in {"observed", "partial"}:
        _require(bool(receipt_ids), f"capability {name} lacks evidence receipts")
        normalized_fingerprint: str | None = _sha256(
            fingerprint, f"capability {name} semantic_fingerprint"
        )
        _require(reason is None, f"capability {name} observed state cannot have a reason_code")
        normalized_reason = None
    elif state == "failed":
        _require(bool(receipt_ids), f"failed capability {name} lacks a failure receipt")
        _require(fingerprint is None, f"failed capability {name} cannot have a fingerprint")
        normalized_fingerprint = None
        normalized_reason = _text(reason, f"capability {name} reason_code", maximum=128)
    else:
        _require(not receipt_ids, f"capability {name} {state} state cannot cite receipts")
        _require(fingerprint is None, f"capability {name} {state} state cannot have a fingerprint")
        normalized_fingerprint = None
        normalized_reason = _text(reason, f"capability {name} reason_code", maximum=128)
    return {
        "name": name,
        "state": state,
        "receipt_ids": receipt_ids,
        "semantic_fingerprint": normalized_fingerprint,
        "reason_code": normalized_reason,
        "limitations": _string_set(value["limitations"], f"capability {name} limitations"),
    }


def _normalize_runtime_identity(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "runtime_identity must be an object")
    _closed(
        value,
        frozenset(
            {
                "launch_id",
                "platform_profile_id",
                "pack_profile_id",
                "physical_side",
                "process_outcome",
                "candidate_lock_sha256",
                "transformer_toolchain_lock_sha256",
                "java_runtime_id",
                "session_ids",
                "capture_ids",
            }
        ),
        "runtime_identity",
    )
    _require(value["physical_side"] in _SIDES, "runtime physical_side is unsupported")
    _require(value["process_outcome"] in _PROCESS_OUTCOMES, "runtime process_outcome is unsupported")
    return {
        "launch_id": _text(value["launch_id"], "runtime launch_id"),
        "platform_profile_id": _text(
            value["platform_profile_id"], "runtime platform_profile_id"
        ),
        "pack_profile_id": _nullable_text(value["pack_profile_id"], "runtime pack_profile_id"),
        "physical_side": value["physical_side"],
        "process_outcome": value["process_outcome"],
        "candidate_lock_sha256": _sha256(
            value["candidate_lock_sha256"], "runtime candidate_lock_sha256"
        ),
        "transformer_toolchain_lock_sha256": _sha256(
            value["transformer_toolchain_lock_sha256"],
            "runtime transformer_toolchain_lock_sha256",
        ),
        "java_runtime_id": _text(value["java_runtime_id"], "runtime java_runtime_id"),
        "session_ids": _string_set(value["session_ids"], "runtime session_ids"),
        "capture_ids": _string_set(value["capture_ids"], "runtime capture_ids"),
    }


def _normalize_epoch(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "epoch must be an object")
    _closed(
        value,
        frozenset(
            {"profile_epoch_id", "adapter_id", "adapter_version", "axes", "capabilities"}
        ),
        "epoch",
    )
    axes = value["axes"]
    _require(isinstance(axes, list) and bool(axes), "epoch axes must be a nonempty array")
    normalized_axes: list[dict[str, str]] = []
    for index, axis in enumerate(axes):
        _require(isinstance(axis, Mapping), f"epoch axes[{index}] must be an object")
        _closed(axis, frozenset({"name", "value"}), f"epoch axes[{index}]")
        normalized_axes.append(
            {
                "name": _capability_name(axis["name"], f"epoch axes[{index}].name"),
                "value": _text(axis["value"], f"epoch axes[{index}].value", maximum=256),
            }
        )
    normalized_axes.sort(key=lambda row: row["name"])
    _require(
        len(normalized_axes) == len({row["name"] for row in normalized_axes}),
        "epoch axes contain duplicate names",
    )
    capabilities = _string_set(value["capabilities"], "epoch capabilities")
    for index, item in enumerate(capabilities):
        _capability_name(item, f"epoch capabilities[{index}]")
    _require(bool(capabilities), "epoch capabilities must be nonempty")
    return {
        "profile_epoch_id": _text(value["profile_epoch_id"], "epoch profile_epoch_id"),
        "adapter_id": _text(value["adapter_id"], "epoch adapter_id"),
        "adapter_version": _text(value["adapter_version"], "epoch adapter_version", maximum=128),
        "axes": normalized_axes,
        "capabilities": capabilities,
    }


def _normalize_capture_health(value: Any, capabilities: set[str]) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "capture_health must be an object")
    _closed(
        value,
        frozenset({"observer_state", "started", "completed", "errors"}),
        "capture_health",
    )
    state = value["observer_state"]
    _require(state in _OBSERVER_STATES, "capture_health observer_state is unsupported")
    _require(type(value["started"]) is bool and value["started"], "a published snapshot requires observer start")
    _require(type(value["completed"]) is bool, "capture_health completed must be boolean")
    errors = value["errors"]
    _require(isinstance(errors, list), "capture_health errors must be an array")
    normalized_errors: list[dict[str, Any]] = []
    for index, row in enumerate(errors):
        context = f"capture_health errors[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed(row, frozenset({"capability", "code", "message", "receipt_id"}), context)
        capability = _capability_name(row["capability"], f"{context}.capability")
        _require(capability in capabilities, f"{context} names an undeclared capability")
        normalized_errors.append(
            {
                "capability": capability,
                "code": _text(row["code"], f"{context}.code", maximum=128),
                "message": _text(row["message"], f"{context}.message"),
                "receipt_id": _nullable_text(row["receipt_id"], f"{context}.receipt_id"),
            }
        )
    normalized_errors.sort(
        key=lambda row: (row["capability"], row["code"], row["receipt_id"] or "")
    )
    _require(
        len(normalized_errors)
        == len(
            {
                (row["capability"], row["code"], row["receipt_id"])
                for row in normalized_errors
            }
        ),
        "capture_health errors contain duplicates",
    )
    if state == "healthy":
        _require(value["completed"] and not normalized_errors, "healthy capture must complete without errors")
    if not value["completed"]:
        _require(state in {"failed", "incomplete"}, "unfinished capture must be failed or incomplete")
    if state == "failed":
        _require(bool(normalized_errors), "failed capture must retain at least one error")
    return {
        "observer_state": state,
        "started": True,
        "completed": value["completed"],
        "errors": normalized_errors,
    }


def _validate_binding_scope(
    binding: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> None:
    scope = binding["scope"]
    _require(scope["launch_id"] == runtime["launch_id"], "receipt binding crosses launch identity")
    _require(
        scope["profile_id"] == runtime["platform_profile_id"],
        "receipt binding crosses platform profile",
    )
    _require(scope["physical_side"] == runtime["physical_side"], "receipt binding crosses physical side")
    if scope["session_id"] is not None:
        _require(scope["session_id"] in runtime["session_ids"], "receipt session is absent from runtime identity")
    if scope["capture_id"] is not None:
        _require(scope["capture_id"] in runtime["capture_ids"], "receipt capture is absent from runtime identity")
    for lock in binding["lock_bindings"]:
        if lock["kind"] == "candidate":
            _require(
                lock["sha256"] == runtime["candidate_lock_sha256"],
                "receipt candidate lock crosses runtime identity",
            )
        elif lock["kind"] == "transformer-toolchain":
            _require(
                lock["sha256"] == runtime["transformer_toolchain_lock_sha256"],
                "receipt transformer toolchain lock crosses runtime identity",
            )


def _summary(
    bindings: Sequence[Mapping[str, Any]],
    capabilities: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = Counter(row["state"] for row in capabilities)
    return {
        "receipt_count": len(bindings),
        "capability_count": len(capabilities),
        "capability_states": {state: counts.get(state, 0) for state in sorted(_CAPABILITY_STATES)},
        "evidence_backed_capabilities": sum(
            counts.get(state, 0) for state in ("observed", "partial", "failed")
        ),
    }


def build_runtime_snapshot(
    *,
    runtime_identity: Mapping[str, Any],
    epoch: Mapping[str, Any],
    receipt_bindings: Sequence[Mapping[str, Any]],
    capabilities: Sequence[Mapping[str, Any]],
    capture_health: Mapping[str, Any],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one canonical V2 manifest over validated runtime receipts."""

    runtime = _normalize_runtime_identity(runtime_identity)
    normalized_epoch = _normalize_epoch(epoch)
    _require(
        isinstance(receipt_bindings, Sequence)
        and not isinstance(receipt_bindings, (str, bytes, bytearray)),
        "receipt_bindings must be an array",
    )
    bindings = [_normalize_binding(value) for value in receipt_bindings]
    bindings.sort(key=lambda row: (row["role"], row["receipt_id"]))
    receipt_ids = [row["receipt_id"] for row in bindings]
    _require(len(receipt_ids) == len(set(receipt_ids)), "receipt bindings repeat an identity")
    for binding in bindings:
        _validate_binding_scope(binding, runtime)

    _require(
        isinstance(capabilities, Sequence)
        and not isinstance(capabilities, (str, bytes, bytearray)),
        "capabilities must be an array",
    )
    normalized_capabilities = [_normalize_capability(value) for value in capabilities]
    normalized_capabilities.sort(key=lambda row: row["name"])
    names = [row["name"] for row in normalized_capabilities]
    _require(len(names) == len(set(names)), "capabilities repeat a name")
    _require(
        names == normalized_epoch["capabilities"],
        "snapshot capabilities differ from the profile epoch contract",
    )
    bound_ids = set(receipt_ids)
    for capability in normalized_capabilities:
        _require(
            set(capability["receipt_ids"]) <= bound_ids,
            f"capability {capability['name']} cites an unbound receipt",
        )

    health = _normalize_capture_health(capture_health, set(names))
    error_receipts = {
        row["receipt_id"] for row in health["errors"] if row["receipt_id"] is not None
    }
    _require(error_receipts <= bound_ids, "capture_health cites an unbound receipt")
    normalized_limitations = sorted(
        set(MANDATORY_LIMITATIONS)
        | {
            _text(value, f"limitations[{index}]")
            for index, value in enumerate(limitations)
        }
    )
    material = {
        "format": RUNTIME_SNAPSHOT_FORMAT,
        "schema_version": 2,
        "canonicalization_id": CANONICALIZATION_ID,
        "authority": {
            "owner": "Crucible",
            "claim": "validated-receipt-composition",
            "interpretation_authority": "Atlas",
            "construction_authority": "Blueprints",
        },
        "runtime_identity": runtime,
        "epoch": normalized_epoch,
        "receipt_bindings": bindings,
        "capabilities": normalized_capabilities,
        "capture_health": health,
        "summary": _summary(bindings, normalized_capabilities),
        "limitations": normalized_limitations,
    }
    return {**material, "snapshot_id": RUNTIME_SNAPSHOT_PREFIX + _content_digest(material)}


def parse_runtime_snapshot(value: Any) -> dict[str, Any]:
    """Fail closed over V2 identity, derived fields, and receipt joins."""

    _require(isinstance(value, Mapping), "runtime snapshot must be an object")
    expected = frozenset(
        {
            "format",
            "schema_version",
            "canonicalization_id",
            "snapshot_id",
            "authority",
            "runtime_identity",
            "epoch",
            "receipt_bindings",
            "capabilities",
            "capture_health",
            "summary",
            "limitations",
        }
    )
    _closed(value, expected, "runtime snapshot")
    _require(value["format"] == RUNTIME_SNAPSHOT_FORMAT, "runtime snapshot format is not V2")
    _require(value["schema_version"] == 2, "runtime snapshot schema_version is not 2")
    _require(value["canonicalization_id"] == CANONICALIZATION_ID, "runtime snapshot canonicalization is unsupported")
    authority = value["authority"]
    _require(
        authority
        == {
            "owner": "Crucible",
            "claim": "validated-receipt-composition",
            "interpretation_authority": "Atlas",
            "construction_authority": "Blueprints",
        },
        "runtime snapshot authority boundary changed",
    )
    supplied_id = value["snapshot_id"]
    rebuilt = build_runtime_snapshot(
        runtime_identity=value["runtime_identity"],
        epoch=value["epoch"],
        receipt_bindings=value["receipt_bindings"],
        capabilities=value["capabilities"],
        capture_health=value["capture_health"],
        limitations=value["limitations"],
    )
    _require(supplied_id == rebuilt["snapshot_id"], "runtime snapshot identity mismatch")
    _require(value == rebuilt, "runtime snapshot is not canonical V2 material")
    return rebuilt


def compare_runtime_snapshots(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare stable capability roles without comparing implementation class names."""

    admitted_left = parse_runtime_snapshot(left)
    admitted_right = parse_runtime_snapshot(right)
    by_side = [
        {row["name"]: row for row in snapshot["capabilities"]}
        for snapshot in (admitted_left, admitted_right)
    ]
    rows: list[dict[str, Any]] = []
    for name in sorted(set(by_side[0]) | set(by_side[1])):
        left_row = by_side[0].get(name)
        right_row = by_side[1].get(name)
        left_state = None if left_row is None else left_row["state"]
        right_state = None if right_row is None else right_row["state"]
        left_fingerprint = None if left_row is None else left_row["semantic_fingerprint"]
        right_fingerprint = None if right_row is None else right_row["semantic_fingerprint"]
        limitations: list[str] = []
        if "unavailable" in {left_state, right_state}:
            status = "unavailable"
            limitations.append("At least one profile epoch adapter declares no producer for this capability.")
        elif (
            left_row is None
            or right_row is None
            or left_state in {"not_observed", "failed"}
            or right_state in {"not_observed", "failed"}
        ):
            status = "not-observed"
            limitations.append("Both snapshots do not contain comparable successful observations for this capability.")
        elif left_fingerprint == right_fingerprint:
            status = "equivalent"
            if "partial" in {left_state, right_state}:
                limitations.append("Equivalence is limited to the partial bounded projections retained by both snapshots.")
        else:
            status = "changed"
            limitations.append("The bounded semantic fingerprints differ; the comparison does not infer a cause.")
        _require(status in _COMPARISON_STATES, "internal comparison state is unsupported")
        rows.append(
            {
                "capability": name,
                "status": status,
                "left_state": left_state,
                "right_state": right_state,
                "left_semantic_fingerprint": left_fingerprint,
                "right_semantic_fingerprint": right_fingerprint,
                "limitations": limitations,
            }
        )
    counts = Counter(row["status"] for row in rows)
    material = {
        "format": COMPARISON_FORMAT,
        "schema_version": 2,
        "left_snapshot_id": admitted_left["snapshot_id"],
        "right_snapshot_id": admitted_right["snapshot_id"],
        "left_epoch_id": admitted_left["epoch"]["profile_epoch_id"],
        "right_epoch_id": admitted_right["epoch"]["profile_epoch_id"],
        "capabilities": rows,
        "summary": {state: counts.get(state, 0) for state in sorted(_COMPARISON_STATES)},
        "limitations": [
            "Comparison is by stable capability role and bounded semantic fingerprint, never by ownership class name alone."
        ],
    }
    return {**material, "comparison_id": COMPARISON_PREFIX + _content_digest(material)}


def write_runtime_snapshot(path: Path, snapshot: Mapping[str, Any]) -> None:
    """Atomically write one already valid V2 snapshot."""

    admitted = parse_runtime_snapshot(snapshot)
    if path.is_symlink():
        raise RuntimeSnapshotValidationError(
            f"runtime snapshot destination cannot be a symlink: {path}"
        )
    destination = path.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        admitted,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=".runtime-snapshot-v2-",
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
