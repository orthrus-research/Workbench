"""Identity-bearing evidence for Java Mixin service-provider enumeration."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence


PROVIDER_ENUMERATION_EVIDENCE_FORMAT = (
    "workbench-crucible-mixin-service-provider-enumeration-evidence-v1"
)
PROVIDER_ENUMERATION_EVIDENCE_PREFIX = (
    "crucible-mixin-service-provider-enumeration:sha256:"
)
PROVIDER_ENUMERATION_PROBE_ID = "workbench-mixin-service-provider-enumerator-v1"
SERVICE_LOADER_ITERATOR_MECHANISM = "java.util.ServiceLoader.iterator"
MIXIN_SERVICE_INTERFACE = "org.spongepowered.asm.service.IMixinService"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
COMPONENT_TOPOLOGY_RECEIPT_PREFIX = "workbench-mixin-topology-receipt:sha256:"

_SHA256_CHARACTERS = frozenset("0123456789abcdef")
_SIDES = frozenset({"client", "dedicated_server", "integrated_server"})
_STATES = frozenset({"enumerated", "failed"})
_FAILURE_STAGES = frozenset(
    {
        "service_interface_load",
        "service_loader_create",
        "service_loader_iteration",
        "provider_name",
        "provider_code_source",
        "artifact_measurement",
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
_PROBE_KEYS = frozenset(
    {
        "probe_id",
        "mechanism",
        "service_interface",
        "class_loader_kind",
        "class_loader_class",
    }
)
_PROVIDER_KEYS = frozenset(
    {
        "service_class",
        "service_name",
        "source_uri",
        "provider_artifact_sha256",
        "provider_artifact_size_bytes",
    }
)
_FAILURE_KEYS = frozenset({"stage", "exception_class", "message"})


class ProviderEnumerationValidationError(ValueError):
    """Raised when provider-enumeration evidence is not exact V1 material."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProviderEnumerationValidationError(message)


def canonical_provider_enumeration_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderEnumerationValidationError(
            f"cannot canonically encode provider enumeration: {exc}"
        ) from exc


def _closed_keys(value: Mapping[str, Any], required: frozenset[str], context: str) -> None:
    missing = required - set(value)
    unknown = set(value) - required
    _require(not missing, f"{context} lacks fields: {sorted(missing)}")
    _require(not unknown, f"{context} has unknown fields: {sorted(unknown)}")


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    _require("\x00" not in value, f"{context} cannot contain a NUL byte")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= _SHA256_CHARACTERS,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _size(value: Any, context: str) -> int:
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
    _closed_keys(value, _SESSION_KEYS, "session")
    side = value["side"]
    _require(side in _SIDES, f"session.side is unsupported: {side!r}")
    topology_id = _text(
        value["component_topology_receipt_id"],
        "session.component_topology_receipt_id",
    )
    _require(
        topology_id.startswith(COMPONENT_TOPOLOGY_RECEIPT_PREFIX),
        "session.component_topology_receipt_id has an unsupported prefix",
    )
    _sha256(
        topology_id[len(COMPONENT_TOPOLOGY_RECEIPT_PREFIX) :],
        "session.component_topology_receipt_id digest",
    )
    return {
        "session_id": _text(value["session_id"], "session.session_id"),
        "launch_id": _text(value["launch_id"], "session.launch_id"),
        "profile_id": _text(value["profile_id"], "session.profile_id"),
        "side": side,
        "candidate_toolchain_lock_sha256": _sha256(
            value["candidate_toolchain_lock_sha256"],
            "session.candidate_toolchain_lock_sha256",
        ),
        "component_topology_receipt_id": topology_id,
        "component_topology_receipt_sha256": _sha256(
            value["component_topology_receipt_sha256"],
            "session.component_topology_receipt_sha256",
        ),
    }


def _normalize_probe(value: Any) -> dict[str, str]:
    _require(isinstance(value, Mapping), "probe must be an object")
    _closed_keys(value, _PROBE_KEYS, "probe")
    _require(value["probe_id"] == PROVIDER_ENUMERATION_PROBE_ID, "probe_id is not V1")
    _require(
        value["mechanism"] == SERVICE_LOADER_ITERATOR_MECHANISM,
        "probe mechanism is not Java ServiceLoader iterator V1",
    )
    _require(
        value["service_interface"] == MIXIN_SERVICE_INTERFACE,
        "probe service_interface is not IMixinService",
    )
    _require(
        value["class_loader_kind"] == "thread_context",
        "probe class_loader_kind is not thread_context",
    )
    return {
        "probe_id": PROVIDER_ENUMERATION_PROBE_ID,
        "mechanism": SERVICE_LOADER_ITERATOR_MECHANISM,
        "service_interface": MIXIN_SERVICE_INTERFACE,
        "class_loader_kind": "thread_context",
        "class_loader_class": _text(
            value["class_loader_class"], "probe.class_loader_class"
        ),
    }


def _normalize_providers(value: Any) -> list[dict[str, Any]]:
    rows = _array(value, "providers")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        context = f"providers[{index}]"
        _require(isinstance(row, Mapping), f"{context} must be an object")
        _closed_keys(row, _PROVIDER_KEYS, context)
        service_class = _text(row["service_class"], f"{context}.service_class")
        artifact_sha = _sha256(
            row["provider_artifact_sha256"],
            f"{context}.provider_artifact_sha256",
        )
        identity = (service_class, artifact_sha)
        _require(identity not in seen, f"{context} repeats provider identity")
        seen.add(identity)
        source_uri = _text(row["source_uri"], f"{context}.source_uri")
        _require(source_uri.startswith("file:"), f"{context}.source_uri must be a file URI")
        normalized.append(
            {
                "service_class": service_class,
                "service_name": _text(
                    row["service_name"], f"{context}.service_name"
                ),
                "source_uri": source_uri,
                "provider_artifact_sha256": artifact_sha,
                "provider_artifact_size_bytes": _size(
                    row["provider_artifact_size_bytes"],
                    f"{context}.provider_artifact_size_bytes",
                ),
            }
        )
    return sorted(
        normalized,
        key=lambda row: (
            row["service_class"],
            row["provider_artifact_sha256"],
            row["service_name"],
        ),
    )


def _normalize_failure(value: Any) -> dict[str, str]:
    _require(isinstance(value, Mapping), "failure must be an object")
    _closed_keys(value, _FAILURE_KEYS, "failure")
    stage = value["stage"]
    _require(stage in _FAILURE_STAGES, f"failure.stage is unsupported: {stage!r}")
    return {
        "stage": stage,
        "exception_class": _text(value["exception_class"], "failure.exception_class"),
        "message": _text(value["message"], "failure.message"),
    }


def _summary(providers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["service_name"]) for row in providers)
    return {
        "provider_count": len(providers),
        "distinct_service_name_count": len(counts),
        "duplicate_service_names": sorted(
            name for name, count in counts.items() if count > 1
        ),
    }


def _material(
    *,
    session: Mapping[str, Any],
    probe: Mapping[str, Any],
    state: str,
    providers: Sequence[Mapping[str, Any]],
    failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "format": PROVIDER_ENUMERATION_EVIDENCE_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "session": deepcopy(dict(session)),
        "probe": deepcopy(dict(probe)),
        "state": state,
        "providers": deepcopy(list(providers)),
        "failure": deepcopy(dict(failure)) if failure is not None else None,
        "summary": _summary(providers),
        "boundaries": {
            "runtime_selection_proved": False,
            "provider_source_from_implementation_protection_domain": True,
            "mixin_code_source_used_for_provider_attribution": False,
            "partial_provider_set_published": False,
        },
    }


def build_provider_enumeration_evidence(
    *,
    session: Mapping[str, Any],
    probe: Mapping[str, Any],
    state: str,
    providers: Sequence[Mapping[str, Any]],
    failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Construct canonical evidence from one complete or failed probe run."""

    normalized_session = _normalize_session(session)
    normalized_probe = _normalize_probe(probe)
    _require(state in _STATES, f"state is unsupported: {state!r}")
    normalized_providers = _normalize_providers(providers)
    if state == "enumerated":
        _require(failure is None, "enumerated evidence must have null failure")
        normalized_failure = None
    else:
        _require(not normalized_providers, "failed evidence cannot publish partial providers")
        normalized_failure = _normalize_failure(failure)
    material = _material(
        session=normalized_session,
        probe=normalized_probe,
        state=state,
        providers=normalized_providers,
        failure=normalized_failure,
    )
    evidence_id = PROVIDER_ENUMERATION_EVIDENCE_PREFIX + hashlib.sha256(
        canonical_provider_enumeration_json_bytes(material)
    ).hexdigest()
    return {
        "format": material["format"],
        "schema_version": material["schema_version"],
        "evidence_id": evidence_id,
        "canonicalization_id": material["canonicalization_id"],
        "session": material["session"],
        "probe": material["probe"],
        "state": material["state"],
        "providers": material["providers"],
        "failure": material["failure"],
        "summary": material["summary"],
        "boundaries": material["boundaries"],
    }


def parse_provider_enumeration_evidence(value: Any) -> dict[str, Any]:
    """Validate identity, derived fields, ordering, and closed V1 semantics."""

    _require(isinstance(value, Mapping), "provider enumeration must be an object")
    expected = {
        "format",
        "schema_version",
        "evidence_id",
        "canonicalization_id",
        "session",
        "probe",
        "state",
        "providers",
        "failure",
        "summary",
        "boundaries",
    }
    _require(set(value) == expected, "provider enumeration surface is not exactly V1")
    _require(
        value["format"] == PROVIDER_ENUMERATION_EVIDENCE_FORMAT,
        "provider enumeration format is not V1",
    )
    _require(value["schema_version"] == 1, "provider enumeration schema_version is not 1")
    _require(
        value["canonicalization_id"] == CANONICALIZATION_ID,
        "provider enumeration canonicalization_id is unsupported",
    )
    material = deepcopy(dict(value))
    supplied_id = material.pop("evidence_id")
    expected_id = PROVIDER_ENUMERATION_EVIDENCE_PREFIX + hashlib.sha256(
        canonical_provider_enumeration_json_bytes(material)
    ).hexdigest()
    _require(supplied_id == expected_id, "provider enumeration evidence identity mismatch")
    rebuilt = build_provider_enumeration_evidence(
        session=value["session"],
        probe=value["probe"],
        state=value["state"],
        providers=value["providers"],
        failure=value["failure"],
    )
    _require(value == rebuilt, "provider enumeration is not canonical V1 material")
    return rebuilt


def render_provider_enumeration_evidence(value: Mapping[str, Any]) -> bytes:
    """Render an admitted evidence document as canonical JSON plus newline."""

    admitted = parse_provider_enumeration_evidence(value)
    return canonical_provider_enumeration_json_bytes(admitted) + b"\n"
