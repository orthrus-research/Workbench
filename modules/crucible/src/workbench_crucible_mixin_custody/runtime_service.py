"""Fail-closed construction of a generic Mixin runtime-service receipt.

Runtime selection reports and provider enumeration are deliberately separate.
Neither surface can prove a final transformation or retained class bytes.
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

from .provider_enumeration import (
    PROVIDER_ENUMERATION_EVIDENCE_PREFIX,
    SERVICE_LOADER_ITERATOR_MECHANISM,
)


RUNTIME_SERVICE_RECEIPT_FORMAT = (
    "workbench-crucible-mixin-runtime-service-receipt-v1"
)
RUNTIME_SERVICE_OBSERVATION_SET_FORMAT = (
    "workbench-crucible-mixin-runtime-service-observation-set-v1"
)
RUNTIME_SERVICE_RECEIPT_PREFIX = (
    "crucible-mixin-runtime-service-receipt:sha256:"
)
COMPONENT_TOPOLOGY_RECEIPT_PREFIX = "workbench-mixin-topology-receipt:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_SHA256_CHARACTERS = frozenset("0123456789abcdef")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_ARTIFACT_ROLES = frozenset(
    {"mixin_runtime", "service_provider", "runtime_dependency", "other"}
)
_EVIDENCE_KINDS = frozenset(
    {"runtime_log", "runtime_probe", "service_loader_enumeration"}
)
_OBSERVATION_KINDS = frozenset(
    {"initialization_report", "selection_report", "subsystem_report"}
)
_ENUMERATION_STATES = frozenset({"not_enumerated", "enumerated", "failed"})
_OBSERVATION_REQUIRED = frozenset(
    {"sequence", "kind", "service_name", "evidence_sha256", "source_record"}
)
_OBSERVATION_OPTIONAL = frozenset(
    {
        "service_class",
        "subsystem_version",
        "environment",
        "source_artifact_sha256",
        "logger",
        "level",
        "thread",
        "wallclock",
    }
)
_SESSION_KEYS = frozenset(
    {
        "session_id",
        "launch_id",
        "profile_id",
        "side",
        "candidate_toolchain_lock_sha256",
        "component_topology_receipt_id",
        "component_topology_receipt_sha256",
    }
)
_ARTIFACT_KEYS = frozenset({"artifact_sha256", "size_bytes", "role", "label"})
_EVIDENCE_KEYS = frozenset({"source_sha256", "size_bytes", "kind", "label"})
_ENUMERATION_KEYS = frozenset(
    {
        "state",
        "mechanism",
        "providers",
        "evidence_sha256",
        "evidence_id",
        "source_record",
        "failure",
    }
)
_PROVIDER_REQUIRED = frozenset(
    {"service_class", "service_name", "provider_artifact_sha256"}
)
_PROVIDER_OPTIONAL: frozenset[str] = frozenset()
_ENUMERATION_FAILURE_KEYS = frozenset({"stage", "exception_class", "message"})
_ENUMERATION_FAILURE_STAGES = frozenset(
    {
        "service_interface_load",
        "service_loader_create",
        "service_loader_iteration",
        "provider_name",
        "provider_code_source",
        "artifact_measurement",
    }
)

MANDATORY_RUNTIME_SERVICE_LIMITATIONS = (
    "A bound candidate toolchain lock frames artifact expectations but does not prove the launch was provisioned from that lock or loaded every locked artifact.",
    "A runtime-reported selected service name does not establish enumerated provider uniqueness.",
    "A reported service name and subsystem code-source artifact do not establish the service implementation class or provider artifact.",
    "An enumerated provider set does not by itself prove which provider the runtime selected.",
    "A failed provider enumeration publishes no partial provider set and leaves provider uniqueness unknown.",
    "This receipt does not prove any Mixin transformation completed or survived into final host-classloader bytes.",
)


class RuntimeServiceValidationError(ValueError):
    """Raised when runtime-service material cannot be admitted exactly."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeServiceValidationError(message)


def canonical_runtime_service_json_bytes(value: Any) -> bytes:
    """Encode canonical JSON used for runtime-service receipt identity."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeServiceValidationError(
            f"cannot canonically encode runtime-service receipt: {exc}"
        ) from exc


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


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_CHARACTERS,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _plain_nonnegative_int(value: Any, context: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
        f"{context} must be a nonnegative integer",
    )
    return value


def _array(value: Any, context: str) -> Sequence[Any]:
    _require(
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray)),
        f"{context} must be an array",
    )
    return value


def _normalize_session(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "session must be an object")
    _closed_keys(value, required=_SESSION_KEYS, context="session")
    side = value["side"]
    _require(side in _SIDES, f"session.side is unsupported: {side!r}")
    component_id = _text(
        value["component_topology_receipt_id"],
        "session.component_topology_receipt_id",
    )
    _require(
        component_id.startswith(COMPONENT_TOPOLOGY_RECEIPT_PREFIX),
        "session.component_topology_receipt_id has an unsupported prefix",
    )
    suffix = component_id.removeprefix(COMPONENT_TOPOLOGY_RECEIPT_PREFIX)
    _sha256(suffix, "session.component_topology_receipt_id digest")
    return {
        "session_id": _text(value["session_id"], "session.session_id"),
        "launch_id": _text(value["launch_id"], "session.launch_id"),
        "profile_id": _text(value["profile_id"], "session.profile_id"),
        "side": side,
        "candidate_toolchain_lock_sha256": _sha256(
            value["candidate_toolchain_lock_sha256"],
            "session.candidate_toolchain_lock_sha256",
        ),
        "component_topology_receipt_id": component_id,
        "component_topology_receipt_sha256": _sha256(
            value["component_topology_receipt_sha256"],
            "session.component_topology_receipt_sha256",
        ),
    }


def _normalize_artifacts(value: Any) -> list[dict[str, Any]]:
    rows = _array(value, "artifacts")
    _require(bool(rows), "artifacts cannot be empty")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        context = f"artifacts[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed_keys(row, required=_ARTIFACT_KEYS, context=context)
        digest = _sha256(row["artifact_sha256"], f"{context}.artifact_sha256")
        _require(digest not in seen, f"artifacts repeats SHA-256 {digest}")
        seen.add(digest)
        role = row["role"]
        _require(role in _ARTIFACT_ROLES, f"{context}.role is unsupported: {role!r}")
        normalized.append(
            {
                "artifact_sha256": digest,
                "size_bytes": _plain_nonnegative_int(
                    row["size_bytes"], f"{context}.size_bytes"
                ),
                "role": role,
                "label": _text(row["label"], f"{context}.label"),
            }
        )
    return sorted(normalized, key=lambda row: row["artifact_sha256"])


def _normalize_evidence(value: Any) -> list[dict[str, Any]]:
    rows = _array(value, "evidence")
    _require(bool(rows), "evidence cannot be empty")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        context = f"evidence[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed_keys(row, required=_EVIDENCE_KEYS, context=context)
        digest = _sha256(row["source_sha256"], f"{context}.source_sha256")
        _require(digest not in seen, f"evidence repeats SHA-256 {digest}")
        seen.add(digest)
        kind = row["kind"]
        _require(
            kind in _EVIDENCE_KINDS,
            f"{context}.kind is unsupported: {kind!r}",
        )
        normalized.append(
            {
                "source_sha256": digest,
                "size_bytes": _plain_nonnegative_int(
                    row["size_bytes"], f"{context}.size_bytes"
                ),
                "kind": kind,
                "label": _text(row["label"], f"{context}.label"),
            }
        )
    return sorted(normalized, key=lambda row: row["source_sha256"])


def _normalize_observations(
    value: Any,
    *,
    artifact_roles: Mapping[str, str],
    evidence_kinds: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows = _array(value, "observations")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        context = f"observations[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed_keys(
            row,
            required=_OBSERVATION_REQUIRED,
            optional=_OBSERVATION_OPTIONAL,
            context=context,
        )
        sequence = _plain_nonnegative_int(row["sequence"], f"{context}.sequence")
        _require(sequence == index + 1, f"{context}.sequence must be {index + 1}")
        kind = row["kind"]
        _require(
            kind in _OBSERVATION_KINDS,
            f"{context}.kind is unsupported: {kind!r}",
        )
        evidence_sha = _sha256(
            row["evidence_sha256"], f"{context}.evidence_sha256"
        )
        _require(
            evidence_sha in evidence_kinds,
            f"{context}.evidence_sha256 does not resolve to evidence",
        )
        _require(
            evidence_kinds[evidence_sha] in {"runtime_log", "runtime_probe"},
            f"{context} cannot use provider-enumeration evidence as a service report",
        )
        result: dict[str, Any] = {
            "sequence": sequence,
            "kind": kind,
            "service_name": _text(row["service_name"], f"{context}.service_name"),
            "evidence_sha256": evidence_sha,
            "source_record": _text(row["source_record"], f"{context}.source_record"),
        }
        for key in sorted(_OBSERVATION_OPTIONAL & set(row)):
            if key == "source_artifact_sha256":
                digest = _sha256(row[key], f"{context}.{key}")
                _require(
                    digest in artifact_roles,
                    f"{context}.{key} does not resolve to artifacts",
                )
                result[key] = digest
            else:
                result[key] = _text(row[key], f"{context}.{key}")
        if kind == "subsystem_report":
            for key in ("subsystem_version", "environment", "source_artifact_sha256"):
                _require(key in result, f"{context} subsystem_report requires {key}")
        normalized.append(result)
    return normalized


def _provider_evidence_id(value: Any) -> str:
    evidence_id = _text(value, "provider_enumeration.evidence_id")
    _require(
        evidence_id.startswith(PROVIDER_ENUMERATION_EVIDENCE_PREFIX),
        "provider_enumeration.evidence_id has an unsupported prefix",
    )
    _sha256(
        evidence_id[len(PROVIDER_ENUMERATION_EVIDENCE_PREFIX) :],
        "provider_enumeration.evidence_id digest",
    )
    return evidence_id


def _normalize_enumeration_failure(value: Any) -> dict[str, str]:
    _require(isinstance(value, Mapping), "provider_enumeration.failure must be an object")
    _closed_keys(
        value,
        required=_ENUMERATION_FAILURE_KEYS,
        context="provider_enumeration.failure",
    )
    stage = value["stage"]
    _require(
        stage in _ENUMERATION_FAILURE_STAGES,
        f"provider_enumeration.failure.stage is unsupported: {stage!r}",
    )
    return {
        "stage": stage,
        "exception_class": _text(
            value["exception_class"],
            "provider_enumeration.failure.exception_class",
        ),
        "message": _text(
            value["message"], "provider_enumeration.failure.message"
        ),
    }


def _normalize_provider_enumeration(
    value: Any,
    *,
    artifact_roles: Mapping[str, str],
    evidence_kinds: Mapping[str, str],
) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "provider_enumeration must be an object")
    _closed_keys(value, required=_ENUMERATION_KEYS, context="provider_enumeration")
    state = value["state"]
    _require(
        state in _ENUMERATION_STATES,
        f"provider_enumeration.state is unsupported: {state!r}",
    )
    mechanism = value["mechanism"]
    evidence_id = value["evidence_id"]
    failure = value["failure"]
    providers_value = _array(value["providers"], "provider_enumeration.providers")
    providers: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(providers_value):
        context = f"provider_enumeration.providers[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed_keys(
            row,
            required=_PROVIDER_REQUIRED,
            optional=_PROVIDER_OPTIONAL,
            context=context,
        )
        service_class = _text(row["service_class"], f"{context}.service_class")
        artifact_sha = _sha256(
            row["provider_artifact_sha256"],
            f"{context}.provider_artifact_sha256",
        )
        _require(
            artifact_sha in artifact_roles,
            f"{context}.provider_artifact_sha256 does not resolve to artifacts",
        )
        _require(
            artifact_roles[artifact_sha] == "service_provider",
            f"{context}.provider_artifact_sha256 does not name a service_provider artifact",
        )
        identity = (service_class, artifact_sha)
        _require(identity not in seen, f"{context} repeats provider identity")
        seen.add(identity)
        provider = {
            "service_class": service_class,
            "service_name": _text(
                row["service_name"], f"{context}.service_name"
            ),
            "provider_artifact_sha256": artifact_sha,
        }
        providers.append(provider)
    providers.sort(
        key=lambda row: (
            row["service_class"],
            row["provider_artifact_sha256"],
            row.get("service_name", ""),
        )
    )

    evidence_sha = value["evidence_sha256"]
    source_record = value["source_record"]
    if state == "not_enumerated":
        _require(not providers, "not_enumerated cannot contain providers")
        _require(mechanism is None, "not_enumerated mechanism must be null")
        _require(evidence_sha is None, "not_enumerated evidence_sha256 must be null")
        _require(evidence_id is None, "not_enumerated evidence_id must be null")
        _require(source_record is None, "not_enumerated source_record must be null")
        _require(failure is None, "not_enumerated failure must be null")
    elif state == "failed":
        _require(not providers, "failed enumeration cannot publish a partial provider set")
        _require(
            mechanism == SERVICE_LOADER_ITERATOR_MECHANISM,
            "failed enumeration mechanism is not ServiceLoader iterator V1",
        )
        evidence_sha = _sha256(
            evidence_sha, "provider_enumeration.evidence_sha256"
        )
        _require(
            evidence_sha in evidence_kinds,
            "provider_enumeration.evidence_sha256 does not resolve to evidence",
        )
        _require(
            evidence_kinds[evidence_sha] == "service_loader_enumeration",
            "failed enumeration requires service-loader enumeration evidence",
        )
        evidence_id = _provider_evidence_id(evidence_id)
        source_record = _text(
            source_record, "provider_enumeration.source_record"
        )
        _require(
            source_record == evidence_id,
            "failed enumeration source_record must equal evidence_id",
        )
        failure = _normalize_enumeration_failure(failure)
    else:
        _require(
            mechanism == SERVICE_LOADER_ITERATOR_MECHANISM,
            "enumerated provider mechanism is not ServiceLoader iterator V1",
        )
        evidence_sha = _sha256(
            evidence_sha, "provider_enumeration.evidence_sha256"
        )
        _require(
            evidence_sha in evidence_kinds,
            "provider_enumeration.evidence_sha256 does not resolve to evidence",
        )
        _require(
            evidence_kinds[evidence_sha] == "service_loader_enumeration",
            "enumerated providers require service-loader enumeration evidence",
        )
        evidence_id = _provider_evidence_id(evidence_id)
        source_record = _text(
            source_record, "provider_enumeration.source_record"
        )
        _require(
            source_record == evidence_id,
            "enumerated provider source_record must equal evidence_id",
        )
        _require(failure is None, "enumerated provider failure must be null")

    return {
        "state": state,
        "mechanism": mechanism,
        "providers": providers,
        "evidence_sha256": evidence_sha,
        "evidence_id": evidence_id,
        "source_record": source_record,
        "failure": failure,
    }


def _summary(
    observations: Sequence[Mapping[str, Any]],
    provider_enumeration: Mapping[str, Any],
) -> dict[str, Any]:
    counts = Counter(str(row["kind"]) for row in observations)
    selection_kinds = {"selection_report", "subsystem_report"}
    reported_names = sorted(
        {
            str(row["service_name"])
            for row in observations
            if row["kind"] in selection_kinds
        }
    )
    reported_count = sum(
        count for kind, count in counts.items() if kind in selection_kinds
    )
    if not reported_names:
        reported_state = "not_reported"
    elif len(reported_names) == 1:
        reported_state = "one_name_reported"
    else:
        reported_state = "conflicting_names_reported"

    if provider_enumeration["state"] != "enumerated":
        uniqueness = "unknown"
        distinct_enumerated_name_count = None
        duplicate_enumerated_names = None
    else:
        provider_count = len(provider_enumeration["providers"])
        enumerated_name_counts = Counter(
            str(row["service_name"])
            for row in provider_enumeration["providers"]
        )
        distinct_enumerated_name_count = len(enumerated_name_counts)
        duplicate_enumerated_names = sorted(
            name for name, count in enumerated_name_counts.items() if count > 1
        )
        if provider_count == 0:
            uniqueness = "none"
        elif provider_count == 1:
            uniqueness = "exactly_one"
        else:
            uniqueness = "multiple"
    return {
        "observation_count": len(observations),
        "observation_kind_counts": {key: counts[key] for key in sorted(counts)},
        "reported_selection_report_count": reported_count,
        "reported_service_names": reported_names,
        "reported_selection_state": reported_state,
        "enumerated_provider_count": (
            len(provider_enumeration["providers"])
            if provider_enumeration["state"] == "enumerated"
            else None
        ),
        "enumerated_provider_uniqueness": uniqueness,
        "enumerated_distinct_service_name_count": distinct_enumerated_name_count,
        "duplicate_enumerated_service_names": duplicate_enumerated_names,
    }


def _material_without_id(
    *,
    session: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    provider_enumeration: Mapping[str, Any],
    limitations: Sequence[str],
) -> dict[str, Any]:
    return {
        "format": RUNTIME_SERVICE_RECEIPT_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": deepcopy(dict(session)),
        "artifacts": deepcopy(list(artifacts)),
        "evidence": deepcopy(list(evidence)),
        "observations": deepcopy(list(observations)),
        "provider_enumeration": deepcopy(dict(provider_enumeration)),
        "summary": _summary(observations, provider_enumeration),
        "limitations": list(limitations),
        "boundaries": {
            "reported_selection_is_not_provider_enumeration": True,
            "reported_service_name_establishes_implementation_class": False,
            "mixin_runtime_code_source_is_not_service_provider_attribution": True,
            "provider_enumeration_is_not_runtime_selection": True,
            "provider_enumeration_mechanism_bound": True,
            "failed_enumeration_partial_set_published": False,
            "final_transformations_proved": False,
            "final_class_bytes_captured": False,
        },
    }


def build_runtime_service_receipt(
    *,
    session: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    provider_enumeration: Mapping[str, Any],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and construct one canonical runtime-service receipt."""

    normalized_session = _normalize_session(session)
    normalized_artifacts = _normalize_artifacts(artifacts)
    normalized_evidence = _normalize_evidence(evidence)
    artifact_roles = {
        row["artifact_sha256"]: row["role"] for row in normalized_artifacts
    }
    evidence_kinds = {
        row["source_sha256"]: row["kind"] for row in normalized_evidence
    }
    normalized_observations = _normalize_observations(
        observations,
        artifact_roles=artifact_roles,
        evidence_kinds=evidence_kinds,
    )
    normalized_enumeration = _normalize_provider_enumeration(
        provider_enumeration,
        artifact_roles=artifact_roles,
        evidence_kinds=evidence_kinds,
    )
    _require(
        bool(normalized_observations)
        or normalized_enumeration["state"] != "not_enumerated",
        "receipt requires a runtime observation or enumeration attempt",
    )
    normalized_limitations = sorted(
        set(MANDATORY_RUNTIME_SERVICE_LIMITATIONS)
        | {
            _text(item, f"limitations[{index}]")
            for index, item in enumerate(_array(limitations, "limitations"))
        }
    )
    material = _material_without_id(
        session=normalized_session,
        artifacts=normalized_artifacts,
        evidence=normalized_evidence,
        observations=normalized_observations,
        provider_enumeration=normalized_enumeration,
        limitations=normalized_limitations,
    )
    receipt_id = RUNTIME_SERVICE_RECEIPT_PREFIX + hashlib.sha256(
        canonical_runtime_service_json_bytes(material)
    ).hexdigest()
    return {
        "format": material["format"],
        "schema_version": material["schema_version"],
        "receipt_id": receipt_id,
        "canonicalization_id": material["canonicalization_id"],
        "session": material["session"],
        "artifacts": material["artifacts"],
        "evidence": material["evidence"],
        "observations": material["observations"],
        "provider_enumeration": material["provider_enumeration"],
        "summary": material["summary"],
        "limitations": material["limitations"],
        "boundaries": material["boundaries"],
    }


def parse_runtime_service_receipt(value: Any) -> dict[str, Any]:
    """Fail-closed parse, semantic validation, and identity verification."""

    _require(isinstance(value, Mapping), "runtime-service receipt must be an object")
    expected_keys = {
        "format",
        "schema_version",
        "receipt_id",
        "canonicalization_id",
        "session",
        "artifacts",
        "evidence",
        "observations",
        "provider_enumeration",
        "summary",
        "limitations",
        "boundaries",
    }
    _require(set(value) == expected_keys, "runtime-service receipt surface is not exactly V1")
    _require(
        value["format"] == RUNTIME_SERVICE_RECEIPT_FORMAT,
        "runtime-service receipt format is not V1",
    )
    _require(value["schema_version"] == 1, "runtime-service schema_version is not 1")
    _require(
        value["canonicalization_id"] == CANONICALIZATION_ID,
        "runtime-service canonicalization_id is unsupported",
    )
    material = deepcopy(dict(value))
    supplied_id = material.pop("receipt_id")
    expected_id = RUNTIME_SERVICE_RECEIPT_PREFIX + hashlib.sha256(
        canonical_runtime_service_json_bytes(material)
    ).hexdigest()
    _require(supplied_id == expected_id, "runtime-service receipt identity mismatch")
    rebuilt = build_runtime_service_receipt(
        session=value["session"],
        artifacts=value["artifacts"],
        evidence=value["evidence"],
        observations=value["observations"],
        provider_enumeration=value["provider_enumeration"],
        limitations=value["limitations"],
    )
    _require(value == rebuilt, "runtime-service receipt is not canonical V1 material")
    return rebuilt


def write_runtime_service_receipt(
    path: Path, receipt: Mapping[str, Any]
) -> None:
    """Atomically write an admitted receipt without following a symlink."""

    admitted = parse_runtime_service_receipt(receipt)
    if path.is_symlink():
        raise RuntimeServiceValidationError(
            f"runtime-service destination cannot be a symlink: {path}"
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
        prefix=".mixin-runtime-service-",
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
