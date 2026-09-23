"""Fail-closed construction of a typed Mixin transformation ledger.

The ledger deliberately records observations rather than inventing a generic
"mixin applied successfully" state.  In particular, a CleanMix audit APPLY
line is only an ``apply_started`` observation and Mixin-stage exports are not
host-final class bytes.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


LEDGER_FORMAT = "workbench-crucible-mixin-transformation-ledger-v1"
OBSERVATION_SET_FORMAT = "workbench-crucible-mixin-observation-set-v1"
COMPONENT_RECEIPT_FORMAT = (
    "workbench-project-intelligence-mixin-component-topology-receipt-v1"
)
LEDGER_PREFIX = "crucible-mixin-ledger:sha256:"

_SHA256_CHARACTERS = frozenset("0123456789abcdef")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_LAUNCH_STATES = frozenset({"complete", "crashed", "incomplete"})
_OUTCOMES = frozenset({"observed", "failed", "unknown"})
_PHASES = frozenset({"PREINIT", "INIT", "DEFAULT", "UNKNOWN"})
_STAGES = (
    "artifact_discovered",
    "config_declared",
    "config_registered",
    "config_prepared",
    "apply_started",
    "plugin_post_apply_seen",
    "mixin_postprocess_seen",
    "generated",
    "final_class_defined",
    "final_member_attributed",
    "failure",
)
_STAGE_ORDER = {stage: index for index, stage in enumerate(_STAGES)}
_EVIDENCE_KINDS = frozenset(
    {
        "artifact_scan",
        "manifest",
        "config_lifecycle_probe",
        "cleanmix_audit",
        "plugin_callback",
        "mixin_stage_export",
        "foundation_final_bytes",
        "crash_report",
    }
)
_EVIDENCE_KINDS_BY_STAGE = {
    "artifact_discovered": frozenset({"artifact_scan"}),
    "config_declared": frozenset({"manifest"}),
    "config_registered": frozenset({"config_lifecycle_probe"}),
    "config_prepared": frozenset({"config_lifecycle_probe"}),
    "apply_started": frozenset({"cleanmix_audit", "mixin_stage_export"}),
    "plugin_post_apply_seen": frozenset({"plugin_callback"}),
    "mixin_postprocess_seen": frozenset({"cleanmix_audit"}),
    "generated": frozenset({"cleanmix_audit"}),
    "final_class_defined": frozenset({"foundation_final_bytes"}),
    "final_member_attributed": frozenset({"foundation_final_bytes"}),
    "failure": frozenset({"crash_report"}),
}
_SESSION_KEYS = frozenset(
    {
        "session_id",
        "launch_id",
        "profile_id",
        "side",
        "component_receipt_sha256",
    }
)
_SUBJECT_KEYS = frozenset(
    {
        "artifact_sha256",
        "owner_label",
        "config",
        "mixin",
        "target_class",
        "target_member",
        "generated_class",
    }
)
_EVIDENCE_KEYS = frozenset(
    {"kind", "source_sha256", "source_record", "limitations"}
)
_OBSERVATION_REQUIRED = frozenset(
    {"sequence", "stage", "outcome", "phase", "subject", "evidence"}
)
_OBSERVATION_OPTIONAL = frozenset(
    {
        "thread",
        "wallclock",
        "input_bytecode_sha256",
        "stage_bytecode_sha256",
        "final_bytecode_sha256",
        "message",
    }
)
MANDATORY_LIMITATIONS = (
    "A CleanMix APPLY audit entry proves only that application started, not that the applicator or later transformer chain completed.",
    "Mixin-stage exports and plugin callbacks are intermediate observations; Foundation final bytes retain final class-definition custody.",
)


class LedgerValidationError(ValueError):
    """Raised when ledger material cannot be admitted without ambiguity."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LedgerValidationError(message)


def canonical_json_bytes(value: Any) -> bytes:
    """Encode canonical JSON used for ledger identity."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LedgerValidationError(f"cannot canonically encode ledger: {exc}") from exc


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_CHARACTERS,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _plain_int(value: Any, context: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{context} must be an integer",
    )
    return value


def _closed_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    context: str,
) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    _require(not missing, f"{context} lacks fields: {sorted(missing)}")
    _require(not unknown, f"{context} has unknown fields: {sorted(unknown)}")


def _normalize_string_set(value: Any, context: str) -> list[str]:
    _require(isinstance(value, list), f"{context} must be an array")
    normalized = [_text(item, f"{context}[{index}]") for index, item in enumerate(value)]
    _require(len(normalized) == len(set(normalized)), f"{context} contains duplicates")
    return sorted(normalized)


def _normalize_session(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "session must be an object")
    _closed_keys(value, required=_SESSION_KEYS, context="session")
    side = value["side"]
    _require(side in _SIDES, f"session.side is unsupported: {side!r}")
    return {
        "session_id": _text(value["session_id"], "session.session_id"),
        "launch_id": _text(value["launch_id"], "session.launch_id"),
        "profile_id": _text(value["profile_id"], "session.profile_id"),
        "side": side,
        "component_receipt_sha256": _sha256(
            value["component_receipt_sha256"],
            "session.component_receipt_sha256",
        ),
    }


def _normalize_subject(value: Any, context: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    keys = set(value)
    _require(bool(keys), f"{context} cannot be empty")
    _require(not keys - _SUBJECT_KEYS, f"{context} has unknown fields: {sorted(keys - _SUBJECT_KEYS)}")
    normalized: dict[str, Any] = {}
    for key in sorted(keys):
        if key == "artifact_sha256":
            normalized[key] = _sha256(value[key], f"{context}.{key}")
        else:
            normalized[key] = _text(value[key], f"{context}.{key}")
    return normalized


def _normalize_evidence(value: Any, context: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    _closed_keys(value, required=_EVIDENCE_KEYS, context=context)
    kind = value["kind"]
    _require(kind in _EVIDENCE_KINDS, f"{context}.kind is unsupported: {kind!r}")
    return {
        "kind": kind,
        "source_sha256": _sha256(value["source_sha256"], f"{context}.source_sha256"),
        "source_record": _text(value["source_record"], f"{context}.source_record"),
        "limitations": _normalize_string_set(value["limitations"], f"{context}.limitations"),
    }


def _validate_stage_semantics(observation: Mapping[str, Any], context: str) -> None:
    stage = observation["stage"]
    outcome = observation["outcome"]
    subject = observation["subject"]
    evidence_kind = observation["evidence"]["kind"]

    if stage == "failure":
        _require(outcome == "failed", f"{context} failure must have outcome failed")
        _require("message" in observation, f"{context} failure requires message")
    else:
        _require(outcome != "failed", f"{context} non-failure cannot have outcome failed")

    required_subjects: dict[str, frozenset[str]] = {
        "artifact_discovered": frozenset({"artifact_sha256"}),
        "config_declared": frozenset({"config"}),
        "config_registered": frozenset({"config"}),
        "config_prepared": frozenset({"config"}),
        "apply_started": frozenset({"config", "mixin", "target_class"}),
        "plugin_post_apply_seen": frozenset({"config", "mixin", "target_class"}),
        "mixin_postprocess_seen": frozenset({"target_class"}),
        "generated": frozenset({"generated_class"}),
        "final_class_defined": frozenset({"target_class"}),
        "final_member_attributed": frozenset({"mixin", "target_class", "target_member"}),
    }
    missing = required_subjects.get(stage, frozenset()) - set(subject)
    _require(not missing, f"{context} {stage} lacks subject fields: {sorted(missing)}")

    valid_evidence = _EVIDENCE_KINDS_BY_STAGE[stage]
    evidence_requirement = (
        next(iter(valid_evidence))
        if len(valid_evidence) == 1
        else f"one of {sorted(valid_evidence)}"
    )
    _require(
        evidence_kind in valid_evidence,
        f"{context} {stage} requires {evidence_requirement} evidence, "
        f"not {evidence_kind!r}",
    )
    if stage in {"final_class_defined", "final_member_attributed"}:
        _require(
            outcome == "observed",
            f"{context} {stage} requires outcome observed",
        )
        _require(
            "final_bytecode_sha256" in observation,
            f"{context} {stage} requires final_bytecode_sha256",
        )


def _normalize_observation(value: Any, index: int) -> dict[str, Any]:
    context = f"observations[{index}]"
    _require(isinstance(value, Mapping), f"{context} must be an object")
    _closed_keys(
        value,
        required=_OBSERVATION_REQUIRED,
        optional=_OBSERVATION_OPTIONAL,
        context=context,
    )
    sequence = _plain_int(value["sequence"], f"{context}.sequence")
    _require(sequence == index + 1, f"{context}.sequence must be {index + 1}")
    stage = value["stage"]
    outcome = value["outcome"]
    phase = value["phase"]
    _require(stage in _STAGE_ORDER, f"{context}.stage is unsupported: {stage!r}")
    _require(outcome in _OUTCOMES, f"{context}.outcome is unsupported: {outcome!r}")
    _require(phase in _PHASES, f"{context}.phase is unsupported: {phase!r}")
    normalized: dict[str, Any] = {
        "sequence": sequence,
        "stage": stage,
        "outcome": outcome,
        "phase": phase,
        "subject": _normalize_subject(value["subject"], f"{context}.subject"),
        "evidence": _normalize_evidence(value["evidence"], f"{context}.evidence"),
    }
    for key in sorted(_OBSERVATION_OPTIONAL & set(value)):
        if key.endswith("_sha256"):
            normalized[key] = _sha256(value[key], f"{context}.{key}")
        else:
            normalized[key] = _text(value[key], f"{context}.{key}")
    _validate_stage_semantics(normalized, context)
    return normalized


def _correlation_key(observation: Mapping[str, Any]) -> tuple[str, ...] | None:
    subject = observation["subject"]
    target = subject.get("target_class")
    if target is None:
        return None
    return (
        str(subject.get("artifact_sha256", "")),
        str(subject.get("config", "")),
        str(subject.get("mixin", "")),
        str(target),
    )


def _validate_order(observations: Sequence[Mapping[str, Any]]) -> None:
    latest: dict[tuple[str, ...], int] = {}
    for observation in observations:
        stage = observation["stage"]
        if stage == "failure":
            continue
        key = _correlation_key(observation)
        if key is None:
            continue
        ordinal = _STAGE_ORDER[stage]
        previous = latest.get(key)
        _require(
            previous is None or ordinal >= previous,
            "observation lifecycle moves backward for correlation "
            + "/".join(key),
        )
        latest[key] = ordinal


def _summary(observations: Sequence[Mapping[str, Any]], launch_state: str) -> dict[str, Any]:
    counts = Counter(str(observation["stage"]) for observation in observations)
    failure_count = counts.get("failure", 0)
    if launch_state != "complete":
        state = "incomplete"
    elif failure_count:
        state = "conflicted"
    else:
        state = "bounded"
    return {
        "observation_count": len(observations),
        "stage_counts": {key: counts[key] for key in sorted(counts)},
        "apply_started_count": counts.get("apply_started", 0),
        "final_class_defined_count": counts.get("final_class_defined", 0),
        "failure_count": failure_count,
        "evidence_state": state,
    }


def _material_without_id(
    *,
    session: Mapping[str, Any],
    launch_state: str,
    observations: Sequence[Mapping[str, Any]],
    limitations: Sequence[str],
) -> dict[str, Any]:
    return {
        "format": LEDGER_FORMAT,
        "schema_version": 1,
        "session": deepcopy(dict(session)),
        "launch_state": launch_state,
        "observations": deepcopy(list(observations)),
        "summary": _summary(observations, launch_state),
        "limitations": list(limitations),
    }


def build_ledger(
    *,
    session: Mapping[str, Any],
    launch_state: str,
    observations: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and construct one canonical ledger document."""

    normalized_session = _normalize_session(session)
    _require(launch_state in _LAUNCH_STATES, f"unsupported launch_state {launch_state!r}")
    _require(
        isinstance(observations, Sequence)
        and not isinstance(observations, (str, bytes, bytearray)),
        "observations must be an array",
    )
    normalized_observations = [
        _normalize_observation(value, index)
        for index, value in enumerate(observations)
    ]
    _validate_order(normalized_observations)
    normalized_limitations = sorted(
        set(MANDATORY_LIMITATIONS)
        | set(
            _text(value, f"limitations[{index}]")
            for index, value in enumerate(limitations)
        )
    )
    material = _material_without_id(
        session=normalized_session,
        launch_state=launch_state,
        observations=normalized_observations,
        limitations=normalized_limitations,
    )
    digest = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    return {
        "format": material["format"],
        "schema_version": material["schema_version"],
        "ledger_id": LEDGER_PREFIX + digest,
        "session": material["session"],
        "launch_state": material["launch_state"],
        "observations": material["observations"],
        "summary": material["summary"],
        "limitations": material["limitations"],
    }


def parse_ledger(value: Any) -> dict[str, Any]:
    """Fail-closed parse and identity verification of a ledger object."""

    _require(isinstance(value, Mapping), "ledger must be an object")
    expected_keys = {
        "format",
        "schema_version",
        "ledger_id",
        "session",
        "launch_state",
        "observations",
        "summary",
        "limitations",
    }
    _require(set(value) == expected_keys, "ledger surface is not exactly V1")
    _require(value["format"] == LEDGER_FORMAT, "ledger format is not V1")
    _require(value["schema_version"] == 1, "ledger schema_version is not 1")
    supplied_material = deepcopy(dict(value))
    supplied_id = supplied_material.pop("ledger_id")
    supplied_digest = hashlib.sha256(
        canonical_json_bytes(supplied_material)
    ).hexdigest()
    _require(
        supplied_id == LEDGER_PREFIX + supplied_digest,
        "ledger identity mismatch",
    )
    rebuilt = build_ledger(
        session=value["session"],
        launch_state=value["launch_state"],
        observations=value["observations"],
        limitations=value["limitations"],
    )
    _require(value == rebuilt, "ledger is not canonical V1 material")
    return rebuilt


def write_ledger(path: Path, ledger: Mapping[str, Any]) -> None:
    """Atomically write an already valid ledger without following a symlink."""

    admitted = parse_ledger(ledger)
    if path.is_symlink():
        raise LedgerValidationError(f"ledger destination cannot be a symlink: {path}")
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
        prefix=".mixin-ledger-",
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
