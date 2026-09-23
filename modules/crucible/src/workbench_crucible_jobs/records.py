"""Closed/offline validation for the durable Crucible job V2 record family.

This is intentionally separate from ``workbench_crucible.schema_registry``:
C02 consumes the C01 canonical byte domain but does not add runtime records to
the immutable graph/evidence registry.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from workbench_api.canonical import (
    CANONICALIZER_ID,
    CanonicalJsonError,
    canonical_json_bytes,
    content_id,
    parse_canonical_json,
    parse_json_strict,
    record_content_id,
    validate_content_id,
)


SCHEMA_VERSION = 2
SCHEMA_URI_PREFIX = "workbench://schemas/crucible/"
_SCHEMA_DIRECTORY = _module_resource_root(__file__, 'crucible') / "schemas"
MAX_ANCESTRY_RECORDS = 64


@dataclass(frozen=True, slots=True)
class _Contract:
    kind: str
    stem: str

    @property
    def format(self) -> str:
        return f"workbench-crucible-{self.stem}-v2"

    @property
    def filename(self) -> str:
        return f"crucible-{self.stem}-v2.schema.json"

    @property
    def schema_id(self) -> str:
        return f"{SCHEMA_URI_PREFIX}{self.filename}"


_CONTRACTS = (
    _Contract("job-submission", "job-submission"),
    _Contract("job-attempt", "job-attempt"),
    _Contract("runtime-session", "runtime-session"),
    _Contract("process-identity", "process-identity"),
    _Contract("job-event", "job-event"),
    _Contract("job-terminal-seal", "job-terminal-seal"),
)
_CONTRACT_BY_KIND = {contract.kind: contract for contract in _CONTRACTS}


def registered_job_kinds() -> tuple[str, ...]:
    return tuple(contract.kind for contract in _CONTRACTS)


class JobValidationPhase(str, Enum):
    DOMAIN = "domain"
    SCHEMA = "schema"
    SEMANTIC = "semantic"
    IDENTITY = "identity"
    RELATION = "relation"


_PHASE_ORDER = {
    JobValidationPhase.DOMAIN: 0,
    JobValidationPhase.SCHEMA: 1,
    JobValidationPhase.SEMANTIC: 2,
    JobValidationPhase.IDENTITY: 3,
    JobValidationPhase.RELATION: 4,
}


@dataclass(frozen=True, slots=True)
class JobValidationDiagnostic:
    phase: JobValidationPhase
    code: str
    path: str
    message: str


class JobValidationError(ValueError):
    def __init__(self, diagnostics: Iterable[JobValidationDiagnostic]):
        ordered = tuple(
            sorted(
                diagnostics,
                key=lambda item: (
                    _PHASE_ORDER[item.phase],
                    item.path.encode("utf-8"),
                    item.code.encode("utf-8"),
                    item.message.encode("utf-8"),
                ),
            )
        )
        if not ordered:
            raise ValueError("JobValidationError requires a diagnostic")
        self.diagnostics = ordered
        super().__init__(
            "; ".join(
                f"{item.phase.value}:{item.code}:{item.path or '/'}: {item.message}"
                for item in ordered
            )
        )


def _fail(
    phase: JobValidationPhase,
    code: str,
    path: str,
    message: str,
) -> NoReturn:
    raise JobValidationError((JobValidationDiagnostic(phase, code, path, message),))


def _pointer(parts: Iterable[object]) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not encoded else "/" + "/".join(encoded)


_RESOURCE_FILES = (
    "crucible-context-v2-common.schema.json",
    "crucible-context-ref-v2.schema.json",
    "crucible-input-binding-v2.schema.json",
    "crucible-job-v2-common.schema.json",
    *tuple(contract.filename for contract in _CONTRACTS),
)


@lru_cache(maxsize=1)
def _schema_resources() -> tuple[Registry, dict[str, dict[str, Any]]]:
    registry = Registry()
    schemas: dict[str, dict[str, Any]] = {}
    for filename in _RESOURCE_FILES:
        path = _SCHEMA_DIRECTORY / filename
        try:
            schema = parse_json_strict(path.read_bytes())
        except (OSError, CanonicalJsonError) as exc:
            raise RuntimeError(f"cannot load C02 schema {path}: {exc}") from exc
        if type(schema) is not dict or type(schema.get("$id")) is not str:
            raise RuntimeError(f"C02 schema {path} has no exact $id")
        Draft202012Validator.check_schema(schema)
        schema_id = schema["$id"]
        registry = registry.with_resource(schema_id, Resource.from_contents(schema))
        schemas[schema_id] = schema
    return registry, schemas


def _schema_validate(record: Mapping[str, Any], schema_id: str) -> None:
    registry, schemas = _schema_resources()
    validator = Draft202012Validator(schemas[schema_id], registry=registry)
    errors = sorted(
        validator.iter_errors(record),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.validator or "",
            error.message,
        ),
    )
    if errors:
        diagnostics = []
        for error in errors:
            diagnostics.append(
                JobValidationDiagnostic(
                    JobValidationPhase.SCHEMA,
                    f"schema.{error.validator or 'invalid'}",
                    _pointer(error.absolute_path),
                    error.message,
                )
            )
        raise JobValidationError(diagnostics)


def _contract_for(record: Mapping[str, Any]) -> _Contract:
    kind = record.get("kind")
    if type(kind) is not str or kind not in _CONTRACT_BY_KIND:
        _fail(JobValidationPhase.SCHEMA, "schema.unknown-kind", "/kind", f"unknown C02 job kind {kind!r}")
    contract = _CONTRACT_BY_KIND[kind]
    expected = {
        "format": contract.format,
        "schema_version": SCHEMA_VERSION,
        "schema_id": contract.schema_id,
        "canonicalizer": CANONICALIZER_ID,
    }
    for field, wanted in expected.items():
        if record.get(field) != wanted:
            _fail(JobValidationPhase.SCHEMA, "schema.header", f"/{field}", f"must equal {wanted!r}")
    return contract


def _validate_timestamp(value: str, path: str) -> None:
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (TypeError, ValueError) as exc:
        _fail(JobValidationPhase.SEMANTIC, "semantic.timestamp", path, f"invalid UTC timestamp: {exc}")
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _fail(JobValidationPhase.SEMANTIC, "semantic.timestamp", path, "timestamp is not UTC")


def _require_utf8_order(values: list[str], path: str) -> None:
    if values != sorted(values, key=lambda item: item.encode("utf-8")):
        _fail(JobValidationPhase.SEMANTIC, "semantic.order", path, "values are not in strict UTF-8 byte order")


def _validate_all_timestamps(value: Any, path: str = "") -> None:
    if type(value) is dict:
        for key, item in value.items():
            child = f"{path}/{key}"
            if (key.endswith("_at") or key == "not_before") and type(item) is str:
                _validate_timestamp(item, child)
            _validate_all_timestamps(item, child)
    elif type(value) is list:
        for index, item in enumerate(value):
            _validate_all_timestamps(item, f"{path}/{index}")


def _validate_local_semantics(record: Mapping[str, Any]) -> None:
    _validate_all_timestamps(record)
    kind = record["kind"]
    if record.get("evidence_eligible") is not False:
        _fail(JobValidationPhase.SEMANTIC, "semantic.evidence-ineligible", "/evidence_eligible", "runtime records cannot be admitted as evidence")

    if kind == "job-submission":
        claims = record["requested_resource_claims"]
        keys = [item["claim_key"] for item in claims]
        if len(keys) != len(set(keys)):
            _fail(JobValidationPhase.SEMANTIC, "semantic.duplicate-claim-key", "/requested_resource_claims", "claim_key values must be unique")
        if keys != sorted(keys, key=lambda item: item.encode("utf-8")):
            _fail(JobValidationPhase.SEMANTIC, "semantic.order", "/requested_resource_claims", "claims must be ordered by claim_key UTF-8 bytes")
    elif kind == "job-attempt":
        _require_utf8_order(record["resource_claim_keys"], "/resource_claim_keys")
        if record["attempt_ordinal"] == 0 and record["previous_attempt_record_id"] is not None:
            _fail(JobValidationPhase.SEMANTIC, "semantic.attempt-predecessor", "/previous_attempt_record_id", "attempt zero has no predecessor")
        if record["attempt_ordinal"] > 0 and record["previous_attempt_record_id"] is None:
            _fail(JobValidationPhase.SEMANTIC, "semantic.attempt-predecessor", "/previous_attempt_record_id", "later attempt requires a predecessor")
    elif kind == "runtime-session":
        _require_utf8_order(record["raw_event_schema_object_descriptor_ids"], "/raw_event_schema_object_descriptor_ids")
    elif kind == "job-event":
        ordinal = record["event_ordinal"]
        if (ordinal == 0) != (record["previous_event_id"] is None):
            _fail(JobValidationPhase.SEMANTIC, "semantic.event-predecessor", "/previous_event_id", "only event zero has a null predecessor")
        for record_field, value_field in (
            ("attempt_record_id", "attempt_id"),
            ("session_record_id", "session_id"),
            ("process_identity_record_id", "process_instance_id"),
        ):
            if (record[record_field] is None) != (record[value_field] is None):
                _fail(JobValidationPhase.SEMANTIC, "semantic.partial-owner-pair", f"/{record_field}", f"{record_field} and {value_field} must both be null or present")
        if record["session_record_id"] is not None and record["attempt_record_id"] is None:
            _fail(JobValidationPhase.SEMANTIC, "semantic.owner-hierarchy", "/session_record_id", "an event session requires an attempt")
        if record["process_identity_record_id"] is not None and record["session_record_id"] is None:
            _fail(JobValidationPhase.SEMANTIC, "semantic.owner-hierarchy", "/process_identity_record_id", "an event process requires a session")
        body = record["body"]
        if body["body_kind"] != record["event_type"]:
            _fail(JobValidationPhase.SEMANTIC, "semantic.event-discriminator", "/body/body_kind", "body_kind must equal event_type")
        if record["event_type"] == "queued" and any(
            record[field] is not None
            for field in ("attempt_record_id", "session_record_id", "process_identity_record_id")
        ):
            _fail(JobValidationPhase.SEMANTIC, "semantic.queued-owner", "/attempt_record_id", "queued genesis cannot claim an execution owner")
        if record["event_type"] in {
            "attempt-starting", "running", "progress", "mutation-state-changed",
            "process-bound", "process-exited", "recovery-classified", "retrying", "orphaned",
        } and record["attempt_record_id"] is None:
            _fail(JobValidationPhase.SEMANTIC, "semantic.missing-attempt-owner", "/attempt_record_id", f"{record['event_type']} requires an attempt owner")
        if record["event_type"] in {"process-bound", "process-exited"} and record["process_identity_record_id"] is None:
            _fail(JobValidationPhase.SEMANTIC, "semantic.missing-process-owner", "/process_identity_record_id", f"{record['event_type']} requires an exact process owner")
        if record["event_type"] == "progress":
            total = body["total"]
            if total is not None and body["completed"] > total:
                _fail(JobValidationPhase.SEMANTIC, "semantic.progress-total", "/body/completed", "completed exceeds total")
            crossed = record["mutation_state"] not in {"not-started", "temporary-residue"}
            if body["mutation_started"] != crossed:
                _fail(JobValidationPhase.SEMANTIC, "semantic.progress-mutation", "/body/mutation_started", "mutation_started disagrees with mutation_state")
        elif record["event_type"] == "mutation-state-changed":
            if body["to_state"] != record["mutation_state"]:
                _fail(JobValidationPhase.SEMANTIC, "semantic.mutation-body", "/body/to_state", "to_state must equal the event mutation_state")
            if body["from_state"] == body["to_state"]:
                _fail(JobValidationPhase.SEMANTIC, "semantic.mutation-body", "/body/from_state", "a mutation change must change state")
        elif record["event_type"] == "recovery-classified":
            _validate_recovery_body(record)
    elif kind == "job-terminal-seal":
        _require_utf8_order(record["session_record_ids"], "/session_record_ids")
        _require_utf8_order(record["process_identity_record_ids"], "/process_identity_record_ids")
        _require_utf8_order(record["cancellation_request_ids"], "/cancellation_request_ids")
        claim_ids = [item["resource_claim_id"] for item in record["resource_claim_accounting"]]
        if claim_ids != sorted(claim_ids, key=lambda item: item.encode("utf-8")):
            _fail(JobValidationPhase.SEMANTIC, "semantic.order", "/resource_claim_accounting", "claim accounting must be ordered by resource_claim_id")
        for index, diagnostic in enumerate(record["diagnostics"]):
            if diagnostic["ordinal"] != index:
                _fail(JobValidationPhase.SEMANTIC, "semantic.diagnostic-order", f"/diagnostics/{index}/ordinal", "diagnostic ordinals must be contiguous")
            if diagnostic["related_records"] != sorted(diagnostic["related_records"], key=canonical_json_bytes):
                _fail(JobValidationPhase.SEMANTIC, "semantic.order", f"/diagnostics/{index}/related_records", "related records must be ordered by canonical item bytes")
        for index, obligation in enumerate(record["recovery_obligations"]):
            if obligation["ordinal"] != index:
                _fail(JobValidationPhase.SEMANTIC, "semantic.obligation-order", f"/recovery_obligations/{index}/ordinal", "obligation ordinals must be contiguous")
        if record["limitations"] != sorted(record["limitations"], key=canonical_json_bytes):
            _fail(JobValidationPhase.SEMANTIC, "semantic.order", "/limitations", "limitations must be ordered by canonical item bytes")


def _validate_recovery_body(event: Mapping[str, Any]) -> None:
    body = event["body"]
    classification = body["classification"]
    expected_states = {
        "no-mutation-began": {"not-started"},
        "create-new-residue": {"temporary-residue"},
        "manifest-published-ref-unmoved": {"immutable-output-published"},
        "ref-committed": {"reference-committed"},
        "exact-process-still-live": set(),
        "external-mutation-unproven": {"external-mutation-indeterminate"},
        "terminal-corrupt-or-incomplete": set(),
    }
    if expected_states[classification] and event["mutation_state"] not in expected_states[classification]:
        _fail(JobValidationPhase.SEMANTIC, "semantic.recovery-state", "/mutation_state", f"state does not match {classification}")
    requirements = {
        "create-new-residue": "residue_object_descriptor_id",
        "manifest-published-ref-unmoved": "manifest_object_descriptor_id",
        "ref-committed": "reference_event_id",
    }
    required = requirements.get(classification)
    if required is not None and body[required] is None:
        _fail(JobValidationPhase.SEMANTIC, "semantic.recovery-proof", f"/body/{required}", f"{classification} requires {required}")
    if classification == "exact-process-still-live":
        if body["prior_process_identity_id"] is None or body["live_process_revalidated"] is not True or body["required_action"] != "resume":
            _fail(JobValidationPhase.SEMANTIC, "semantic.recovery-live-process", "/body", "live-process recovery requires an exact revalidated process and resume action")
    elif body["live_process_revalidated"]:
        _fail(JobValidationPhase.SEMANTIC, "semantic.recovery-live-process", "/body/live_process_revalidated", "only exact-process-still-live may claim revalidation")


@dataclass(frozen=True, slots=True)
class ValidatedJobRecord:
    id: str
    kind: str
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        value = parse_canonical_json(self.canonical_bytes)
        if type(value) is not dict:
            raise AssertionError("validated record bytes no longer encode an object")
        return value


def validate_job_record(record: Mapping[str, Any]) -> ValidatedJobRecord:
    if type(record) is not dict:
        _fail(JobValidationPhase.DOMAIN, "domain.record-object", "", "record must be an ordinary object")
    try:
        canonical = canonical_json_bytes(record)
        snapshot = parse_canonical_json(canonical)
    except CanonicalJsonError as exc:
        _fail(JobValidationPhase.DOMAIN, "domain.canonical", "", str(exc))
    if type(snapshot) is not dict:
        _fail(JobValidationPhase.DOMAIN, "domain.record-object", "", "record snapshot is not an object")
    contract = _contract_for(snapshot)
    _schema_validate(snapshot, contract.schema_id)
    _validate_local_semantics(snapshot)
    try:
        record_id = validate_content_id(snapshot)
    except CanonicalJsonError as exc:
        _fail(JobValidationPhase.IDENTITY, "identity.content-id", "/id", str(exc))
    return ValidatedJobRecord(record_id, contract.kind, canonical)


def seal_job_record(candidate: Mapping[str, Any]) -> ValidatedJobRecord:
    if type(candidate) is not dict:
        _fail(JobValidationPhase.DOMAIN, "domain.record-object", "", "candidate must be an ordinary object")
    if "id" in candidate:
        _fail(JobValidationPhase.DOMAIN, "domain.preexisting-id", "/id", "seal candidate must not contain id")
    try:
        record = parse_canonical_json(canonical_json_bytes(candidate))
    except CanonicalJsonError as exc:
        _fail(JobValidationPhase.DOMAIN, "domain.canonical", "", str(exc))
    if type(record) is not dict:
        _fail(JobValidationPhase.DOMAIN, "domain.record-object", "", "candidate snapshot is not an object")
    try:
        record["id"] = record_content_id(record)
    except CanonicalJsonError as exc:
        _fail(JobValidationPhase.DOMAIN, "domain.canonical", "", str(exc))
    return validate_job_record(record)


def load_job_record(raw: bytes | bytearray | memoryview | str) -> ValidatedJobRecord:
    try:
        value = parse_canonical_json(raw)
    except CanonicalJsonError as exc:
        _fail(JobValidationPhase.DOMAIN, "domain.canonical-bytes", "", str(exc))
    if type(value) is not dict:
        _fail(JobValidationPhase.DOMAIN, "domain.record-object", "", "canonical bytes do not encode an object")
    return validate_job_record(value)


RecordResolver = Callable[[str], Mapping[str, Any] | ValidatedJobRecord | bytes | bytearray | memoryview | None]


@dataclass(frozen=True, slots=True)
class JobExternalReferenceExpectation:
    path: str
    record_id: str
    expected_kind: str
    containing_record_id: str
    containing_record_bytes: bytes
    job_id: str
    context_ref_id: str
    input_binding_id: str


ExternalReferenceValidator = Callable[[JobExternalReferenceExpectation], bool]


@dataclass(frozen=True, slots=True)
class JobContextValidationRequest:
    context_ref_id: str
    input_binding_id: str
    context_ref_bytes: bytes
    input_binding_bytes: bytes


ContextPublicationValidator = Callable[[JobContextValidationRequest], bool]


@dataclass(frozen=True, slots=True)
class JobRelationResolvers:
    record_resolver: RecordResolver
    external_reference_validator: ExternalReferenceValidator | None = None
    context_publication_validator: ContextPublicationValidator | None = None


def _external_expectation_metadata(value: Any) -> tuple[str, str, str, str, bytes, str, str, str]:
    if type(value) is not JobExternalReferenceExpectation:
        raise TypeError("external applicability requests must use the exact expectation type")
    string_fields = (
        value.path,
        value.record_id,
        value.expected_kind,
        value.containing_record_id,
        value.job_id,
        value.context_ref_id,
        value.input_binding_id,
    )
    if any(type(item) is not str for item in string_fields):
        raise TypeError("external applicability request metadata must use exact strings")
    if type(value.containing_record_bytes) is not bytes:
        raise TypeError("external applicability containing record bytes must be exact bytes")
    return (
        value.path,
        value.record_id,
        value.expected_kind,
        value.containing_record_id,
        value.containing_record_bytes,
        value.job_id,
        value.context_ref_id,
        value.input_binding_id,
    )


def _context_validation_request_metadata(value: Any) -> tuple[str, str, bytes, bytes]:
    if type(value) is not JobContextValidationRequest:
        raise TypeError("context publication requests must use the exact request type")
    if type(value.context_ref_id) is not str or type(value.input_binding_id) is not str:
        raise TypeError("context publication request IDs must be exact strings")
    if type(value.context_ref_bytes) is not bytes or type(value.input_binding_bytes) is not bytes:
        raise TypeError("context publication request records must be exact bytes")
    return (
        value.context_ref_id,
        value.input_binding_id,
        value.context_ref_bytes,
        value.input_binding_bytes,
    )


def _coerce_job_record(value: Mapping[str, Any] | ValidatedJobRecord | bytes | bytearray | memoryview) -> ValidatedJobRecord:
    if type(value) is ValidatedJobRecord:
        if type(value.id) is not str or type(value.kind) is not str or type(value.canonical_bytes) is not bytes:
            _fail(JobValidationPhase.RELATION, "relation.record-snapshot", "", "ValidatedJobRecord metadata and bytes must have exact public types")
        fresh = load_job_record(value.canonical_bytes)
        if fresh.id != value.id or fresh.kind != value.kind:
            _fail(JobValidationPhase.RELATION, "relation.record-snapshot", "", "ValidatedJobRecord metadata disagrees with canonical bytes")
        return fresh
    if type(value) in (bytes, bytearray, memoryview):
        return load_job_record(value)
    return validate_job_record(value)


def _coerce_external(value: Mapping[str, Any] | bytes | bytearray | memoryview, expected_kind: str) -> dict[str, Any]:
    if type(value) in (bytes, bytearray, memoryview):
        try:
            parsed = parse_canonical_json(value)
        except CanonicalJsonError as exc:
            _fail(JobValidationPhase.RELATION, "relation.external-canonical", "", str(exc))
    else:
        if type(value) is not dict:
            _fail(JobValidationPhase.RELATION, "relation.external-object", "", "external record must be an ordinary object or canonical bytes")
        try:
            parsed = parse_canonical_json(canonical_json_bytes(value))
        except CanonicalJsonError as exc:
            _fail(JobValidationPhase.RELATION, "relation.external-canonical", "", str(exc))
    if type(parsed) is not dict or parsed.get("kind") != expected_kind:
        _fail(JobValidationPhase.RELATION, "relation.external-kind", "/kind", f"expected {expected_kind}")
    schema_id = f"{SCHEMA_URI_PREFIX}crucible-{expected_kind}-v2.schema.json"
    _schema_validate(parsed, schema_id)
    try:
        validate_content_id(parsed)
    except CanonicalJsonError as exc:
        _fail(JobValidationPhase.RELATION, "relation.external-identity", "/id", str(exc))
    return dict(parsed)


def event_ledger_root(job_id: str, event_ids: Iterable[str]) -> str:
    return content_id(
        "job-event-ledger-root-v2",
        {"event_ids": list(event_ids), "job_id": job_id},
    )


def validate_evidence_admission_candidate(candidate_record_id: str) -> None:
    """Fail if a C01 admission attempts to use an operational C02 record.

    This explicit interop assertion keeps unrelated admission envelopes out of
    the closed job-publication API while giving callers one deterministic
    boundary check before invoking C01 admission validation.
    """

    if type(candidate_record_id) is not str:
        _fail(JobValidationPhase.DOMAIN, "domain.candidate-id", "", "candidate_record_id must be an exact string")
    forbidden_prefixes = tuple(f"{kind}:sha256:" for kind in registered_job_kinds())
    if candidate_record_id.startswith(forbidden_prefixes):
        _fail(JobValidationPhase.RELATION, "relation.evidence-ineligible", "/candidate_record_id", "C02 operational job/session records cannot be evidence admission candidates")


def external_reference_expectations(
    record: Mapping[str, Any],
) -> tuple[JobExternalReferenceExpectation, ...]:
    """Compile every typed non-C02 reference that needs owner applicability.

    ContextRef/InputBinding and job-family links are validated by their own
    resolvers. This list is the explicit fail-closed port for application-owned
    plans, decisions, observations, descriptors, policies, and implementations.
    """

    result: list[JobExternalReferenceExpectation] = []
    containing_record_bytes = canonical_json_bytes(record)

    def add(path: str, record_id: Any, expected_kind: str) -> None:
        if record_id is None:
            return
        result.append(
            JobExternalReferenceExpectation(
                path=path,
                record_id=record_id,
                expected_kind=expected_kind,
                containing_record_id=record["id"],
                containing_record_bytes=containing_record_bytes,
                job_id=record["job_id"],
                context_ref_id=record["context_ref_id"],
                input_binding_id=record["input_binding_id"],
            )
        )

    kind = record["kind"]
    if kind == "job-submission":
        for field, expected in (
            ("capability_id", "capability"),
            ("handler_id", "handler"),
            ("handler_implementation_id", "implementation"),
            ("request_object_descriptor_id", "object-descriptor"),
            ("plan_record_id", "operation-plan"),
            ("consent_record_id", "consent-decision"),
            ("submitted_by_actor_id", "actor"),
            ("retention_policy_id", "policy"),
        ):
            add(f"/{field}", record[field], expected)
    elif kind == "job-attempt":
        add("/handler_implementation_id", record["handler_implementation_id"], "implementation")
        add("/retry_decision_id", record["retry_decision_id"], "retry-decision")
    elif kind == "runtime-session":
        for field, expected in (
            ("custodian_component_id", "component"),
            ("custodian_implementation_id", "implementation"),
            ("request_object_descriptor_id", "object-descriptor"),
            ("retention_policy_id", "policy"),
        ):
            add(f"/{field}", record[field], expected)
        for index, record_id in enumerate(record["raw_event_schema_object_descriptor_ids"]):
            add(f"/raw_event_schema_object_descriptor_ids/{index}", record_id, "object-descriptor")
    elif kind == "process-identity":
        for field in (
            "executable_object_descriptor_id",
            "argv_object_descriptor_id",
            "environment_object_descriptor_id",
            "working_directory_object_descriptor_id",
        ):
            add(f"/{field}", record[field], "object-descriptor")
        add("/launch_observation_id", record["launch_observation_id"], "process-launch-observation")
        add(
            "/adoption_reconciliation_id",
            record["adoption_reconciliation_id"],
            "process-adoption-reconciliation",
        )
    elif kind == "job-event":
        add("/actor_id", record["actor_id"], "actor")
        body = record["body"]
        event_type = record["event_type"]
        fields: tuple[tuple[str, str], ...] = ()
        if event_type == "queued":
            fields = (("queue_policy_id", "policy"),)
        elif event_type == "running":
            fields = (("readiness_observation_id", "readiness-observation"),)
        elif event_type == "resource-claim-released":
            fields = (("release_observation_id", "resource-release-observation"),)
        elif event_type == "cancellation-requested":
            fields = (("requested_by_actor_id", "actor"),)
        elif event_type == "mutation-state-changed":
            fields = (("mutation_observation_id", "mutation-observation"),)
        elif event_type == "process-exited":
            fields = (("termination_observation_id", "termination-observation"),)
        elif event_type == "recovery-classified":
            fields = (
                ("residue_object_descriptor_id", "object-descriptor"),
                ("manifest_object_descriptor_id", "object-descriptor"),
                ("reference_event_id", "reference-event"),
                ("recovery_decision_id", "recovery-decision"),
            )
        elif event_type == "retrying":
            fields = (("retry_decision_id", "retry-decision"),)
        elif event_type == "terminal-ready":
            fields = (
                ("result_object_descriptor_id", "object-descriptor"),
                ("failure_object_descriptor_id", "object-descriptor"),
            )
        for field, expected in fields:
            add(f"/body/{field}", body[field], expected)
    elif kind == "job-terminal-seal":
        add("/result_object_descriptor_id", record["result_object_descriptor_id"], "object-descriptor")
        add("/failure_object_descriptor_id", record["failure_object_descriptor_id"], "object-descriptor")
        add("/sealed_by_actor_id", record["sealed_by_actor_id"], "actor")
        for index, accounting in enumerate(record["resource_claim_accounting"]):
            add(
                f"/resource_claim_accounting/{index}/reconciliation_observation_id",
                accounting["reconciliation_observation_id"],
                "resource-claim-reconciliation",
            )
        for diagnostic_index, diagnostic in enumerate(record["diagnostics"]):
            for related_index, related in enumerate(diagnostic["related_records"]):
                if related["record_kind"] == "object-descriptor":
                    add(
                        f"/diagnostics/{diagnostic_index}/related_records/{related_index}/record_id",
                        related["record_id"],
                        "object-descriptor",
                    )
    return tuple(result)


def _validate_external_applicability(
    records: Iterable[Mapping[str, Any]],
    relations: JobRelationResolvers,
) -> None:
    expectations = tuple(
        expectation
        for record in records
        for expectation in external_reference_expectations(record)
    )
    if expectations and relations.external_reference_validator is None:
        _fail(
            JobValidationPhase.RELATION,
            "relation.external-validator-required",
            "",
            "typed external references require an application-owned applicability validator",
        )
    validator = relations.external_reference_validator
    if validator is None:
        return
    for expectation in expectations:
        (
            path,
            record_id,
            expected_kind,
            containing_record_id,
            containing_record_bytes,
            job_id,
            context_ref_id,
            input_binding_id,
        ) = _external_expectation_metadata(expectation)
        request = JobExternalReferenceExpectation(
            path=path,
            record_id=record_id,
            expected_kind=expected_kind,
            containing_record_id=containing_record_id,
            containing_record_bytes=bytes(bytearray(containing_record_bytes)),
            job_id=job_id,
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
        )
        request_snapshot = _external_expectation_metadata(request)
        try:
            accepted = validator(request)
        except Exception as exc:
            _fail(
                JobValidationPhase.RELATION,
                "relation.external-validator-exception",
                path,
                f"external applicability validator failed: {type(exc).__name__}: {exc}",
            )
        try:
            request_unchanged = _external_expectation_metadata(request) == request_snapshot
        except (AttributeError, TypeError, ValueError):
            request_unchanged = False
        if not request_unchanged:
            _fail(
                JobValidationPhase.RELATION,
                "relation.external-validator-mutation",
                path,
                "external applicability validator mutated its isolated expectation",
            )
        if accepted is not True:
            _fail(
                JobValidationPhase.RELATION,
                "relation.external-reference-rejected",
                path,
                f"{expected_kind} reference was not accepted for its exact record projection",
            )


_LIFECYCLE_TRANSITIONS = {
    "queued": {"queued", "starting", "orphaned", "terminal"},
    "starting": {"starting", "running", "recovering", "orphaned", "terminal"},
    "running": {"running", "recovering", "retrying", "orphaned", "terminal"},
    "recovering": {"recovering", "running", "retrying", "orphaned", "terminal"},
    "retrying": {"retrying", "starting", "orphaned", "terminal"},
    "orphaned": {"orphaned", "recovering"},
    "terminal": set(),
}

_MUTATION_TRANSITIONS = {
    "not-started": {"not-started", "temporary-residue", "immutable-output-published", "protected-mutation-started", "external-mutation-started", "external-mutation-indeterminate"},
    "temporary-residue": {"temporary-residue", "not-started", "immutable-output-published", "protected-mutation-started", "external-mutation-started", "external-mutation-indeterminate"},
    "immutable-output-published": {"immutable-output-published", "reference-committed", "external-mutation-indeterminate"},
    "reference-committed": {"reference-committed", "external-mutation-indeterminate"},
    "protected-mutation-started": {"protected-mutation-started", "protected-mutation-partial", "protected-mutation-completed", "external-mutation-indeterminate"},
    "protected-mutation-partial": {"protected-mutation-partial", "protected-mutation-completed", "external-mutation-indeterminate"},
    "protected-mutation-completed": {"protected-mutation-completed", "external-mutation-indeterminate"},
    "external-mutation-started": {"external-mutation-started", "external-mutation-partial", "external-mutation-completed", "external-mutation-indeterminate"},
    "external-mutation-partial": {"external-mutation-partial", "external-mutation-completed", "external-mutation-indeterminate"},
    "external-mutation-completed": {"external-mutation-completed", "external-mutation-indeterminate"},
    "external-mutation-indeterminate": {"external-mutation-indeterminate"},
}

_MUTATION_BOUNDARY_STATES = {
    "none": {"not-started", "temporary-residue"},
    "immutable-publication": {"not-started", "temporary-residue", "immutable-output-published"},
    "reference-update": {"not-started", "temporary-residue", "immutable-output-published", "reference-committed"},
    "protected-state": {"not-started", "temporary-residue", "protected-mutation-started", "protected-mutation-partial", "protected-mutation-completed"},
    "external-side-effect": {"not-started", "temporary-residue", "external-mutation-started", "external-mutation-partial", "external-mutation-completed", "external-mutation-indeterminate"},
}

_PRESERVE_LIFECYCLE_EVENTS = {
    "resource-claim-acquired",
    "resource-claim-released",
    "progress",
    "cancellation-requested",
    "cancellation-observed",
    "mutation-state-changed",
    "process-bound",
    "process-exited",
}

_RETIRED_ATTEMPT_CLEANUP_EVENTS = {
    "resource-claim-released",
    "process-exited",
    "orphaned",
    "recovery-classified",
    "cancellation-requested",
    "cancellation-observed",
}

_EXITED_PROCESS_CLEANUP_EVENTS = {
    "resource-claim-released",
    "orphaned",
    "recovery-classified",
    "cancellation-requested",
    "cancellation-observed",
    "terminal-ready",
}

_FIXED_LIFECYCLE = {
    "queued": "queued",
    "attempt-starting": "starting",
    "running": "running",
    "recovery-classified": "recovering",
    "retrying": "retrying",
    "orphaned": "orphaned",
    "terminal-ready": "terminal",
}


def _same_anchor(record: Mapping[str, Any], anchor: Mapping[str, Any], path: str) -> None:
    for field in ("job_id", "context_ref_id", "input_binding_id"):
        if record[field] != anchor[field]:
            _fail(JobValidationPhase.RELATION, "relation.anchor-mismatch", f"{path}/{field}", f"does not match job submission {field}")
    if record.get("job_submission_id") != anchor["id"]:
        _fail(JobValidationPhase.RELATION, "relation.submission", f"{path}/job_submission_id", "does not bind the job submission")


def _resolve_job(
    record_id: str,
    expected_kind: str,
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    path: str,
) -> dict[str, Any]:
    value: Mapping[str, Any] | ValidatedJobRecord | bytes | bytearray | memoryview | None
    value = local.get(record_id)
    if value is None:
        try:
            value = relations.record_resolver(record_id)
        except Exception as exc:
            _fail(JobValidationPhase.RELATION, "relation.resolver-exception", path, f"record resolver failed: {type(exc).__name__}: {exc}")
    if value is None:
        _fail(JobValidationPhase.RELATION, "relation.missing-record", path, f"cannot resolve {record_id}")
    validated = _coerce_job_record(value)
    if validated.id != record_id or validated.kind != expected_kind:
        _fail(JobValidationPhase.RELATION, "relation.record-target", path, f"expected {expected_kind} {record_id}")
    return validated.to_dict()


def _resolve_external(
    record_id: str,
    expected_kind: str,
    relations: JobRelationResolvers,
    path: str,
) -> dict[str, Any]:
    try:
        value = relations.record_resolver(record_id)
    except Exception as exc:
        _fail(JobValidationPhase.RELATION, "relation.resolver-exception", path, f"record resolver failed: {type(exc).__name__}: {exc}")
    if value is None or type(value) is ValidatedJobRecord:
        _fail(JobValidationPhase.RELATION, "relation.missing-external", path, f"cannot resolve {expected_kind} {record_id}")
    record = _coerce_external(value, expected_kind)
    if record["id"] != record_id:
        _fail(JobValidationPhase.RELATION, "relation.external-target", path, f"resolved record ID differs from {record_id}")
    return record


def _validate_context_input(anchor: Mapping[str, Any], relations: JobRelationResolvers) -> None:
    context = _resolve_external(anchor["context_ref_id"], "context-ref", relations, "/context_ref_id")
    binding = _resolve_external(anchor["input_binding_id"], "input-binding", relations, "/input_binding_id")
    if binding["context_ref_id"] != anchor["context_ref_id"]:
        _fail(JobValidationPhase.RELATION, "relation.input-context", "/input_binding_id", "InputBinding belongs to another ContextRef")
    validator = relations.context_publication_validator
    if validator is None:
        _fail(JobValidationPhase.RELATION, "relation.context-publication-validator-required", "", "job publication requires an already-authoritative lane-A ContextRef/InputBinding validation port")
    context_ref_bytes = canonical_json_bytes(context)
    input_binding_bytes = canonical_json_bytes(binding)
    request = JobContextValidationRequest(
        context_ref_id=context["id"],
        input_binding_id=binding["id"],
        context_ref_bytes=bytes(bytearray(context_ref_bytes)),
        input_binding_bytes=bytes(bytearray(input_binding_bytes)),
    )
    request_snapshot = _context_validation_request_metadata(request)
    try:
        accepted = validator(request)
    except Exception as exc:
        _fail(JobValidationPhase.RELATION, "relation.context-publication-validator-exception", "", f"context publication validator failed: {type(exc).__name__}: {exc}")
    try:
        request_unchanged = _context_validation_request_metadata(request) == request_snapshot
    except (AttributeError, TypeError, ValueError):
        request_unchanged = False
    if not request_unchanged:
        _fail(JobValidationPhase.RELATION, "relation.context-publication-validator-mutation", "", "context publication validator mutated its isolated request")
    if accepted is not True:
        _fail(JobValidationPhase.RELATION, "relation.context-publication-rejected", "", "lane-A authority validation rejected the exact ContextRef/InputBinding pair")


def _validate_retry_lineage(
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
) -> None:
    prior_seal_id = anchor["retry_of_terminal_seal_id"]
    if prior_seal_id is None:
        return
    if anchor["idempotency_collision_policy"] != "retry-after-terminal":
        _fail(JobValidationPhase.RELATION, "relation.retry-lineage", "/idempotency_collision_policy", "retry lineage requires retry-after-terminal policy")
    prior_seal = _resolve_job(prior_seal_id, "job-terminal-seal", local, relations, "/retry_of_terminal_seal_id")
    if prior_seal["job_id"] == anchor["job_id"]:
        _fail(JobValidationPhase.RELATION, "relation.retry-lineage", "/retry_of_terminal_seal_id", "retry must create a distinct job identity")
    for field in ("context_ref_id", "input_binding_id"):
        if prior_seal[field] != anchor[field]:
            _fail(JobValidationPhase.RELATION, "relation.retry-lineage", f"/{field}", "retry changed its exact context or inputs")
    prior_submission = _resolve_job(prior_seal["job_submission_id"], "job-submission", local, relations, "/retry_of_terminal_seal_id")
    for field in (
        "capability_id",
        "handler_id",
        "handler_implementation_id",
        "request_object_descriptor_id",
        "idempotency_key_digest",
        "idempotency_scope",
    ):
        if prior_submission[field] != anchor[field]:
            _fail(JobValidationPhase.RELATION, "relation.retry-lineage", f"/{field}", f"retry changed prior {field}")


def _require_complete_seal_closure(
    attempts: Iterable[Mapping[str, Any]],
    sessions: Iterable[Mapping[str, Any]],
    processes: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    local: Mapping[str, ValidatedJobRecord],
) -> None:
    def require(record_id: str | None, expected_kind: str, path: str) -> None:
        if record_id is None:
            return
        record = local.get(record_id)
        if record is None or record.kind != expected_kind:
            _fail(JobValidationPhase.RELATION, "relation.incomplete-seal-closure", path, f"sealed publication omits reachable {expected_kind} {record_id}")

    for index, session in enumerate(sessions):
        require(session["attempt_record_id"], "job-attempt", f"/sessions/{index}/attempt_record_id")
        require(session["parent_session_record_id"], "runtime-session", f"/sessions/{index}/parent_session_record_id")
    for index, process in enumerate(processes):
        require(process["attempt_record_id"], "job-attempt", f"/processes/{index}/attempt_record_id")
        require(process["session_record_id"], "runtime-session", f"/processes/{index}/session_record_id")
        require(process["supervisor_process_identity_id"], "process-identity", f"/processes/{index}/supervisor_process_identity_id")
    for index, event in enumerate(events):
        require(event["attempt_record_id"], "job-attempt", f"/events/{index}/attempt_record_id")
        require(event["session_record_id"], "runtime-session", f"/events/{index}/session_record_id")
        require(event["process_identity_record_id"], "process-identity", f"/events/{index}/process_identity_record_id")
        if event["event_type"] == "orphaned":
            require(event["body"]["last_known_process_identity_id"], "process-identity", f"/events/{index}/body/last_known_process_identity_id")
        elif event["event_type"] == "recovery-classified":
            require(event["body"]["prior_process_identity_id"], "process-identity", f"/events/{index}/body/prior_process_identity_id")
    attempt_ids = {item["id"] for item in attempts}
    if any(event["attempt_record_id"] is not None and event["attempt_record_id"] not in attempt_ids for event in events):
        _fail(JobValidationPhase.RELATION, "relation.incomplete-seal-closure", "/events", "sealed event owner is absent from attempt membership")


def _validate_attempts(
    attempts: list[dict[str, Any]],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
) -> None:
    attempts.sort(key=lambda item: item["attempt_ordinal"])
    seen_ids: set[str] = set()
    prior: dict[str, Any] | None = None
    allowed_claims = {item["claim_key"] for item in anchor["requested_resource_claims"]}
    for index, attempt in enumerate(attempts):
        _same_anchor(attempt, anchor, f"/attempts/{index}")
        if attempt["attempt_ordinal"] != index:
            _fail(JobValidationPhase.RELATION, "relation.attempt-ordinal", f"/attempts/{index}/attempt_ordinal", "attempt ordinals must be contiguous from zero")
        if attempt["attempt_id"] in seen_ids:
            _fail(JobValidationPhase.RELATION, "relation.duplicate-attempt-id", f"/attempts/{index}/attempt_id", "attempt_id is reused")
        seen_ids.add(attempt["attempt_id"])
        if attempt["handler_implementation_id"] != anchor["handler_implementation_id"]:
            _fail(JobValidationPhase.RELATION, "relation.attempt-implementation", f"/attempts/{index}/handler_implementation_id", "attempt implementation differs from the submitted implementation")
        if index == 0:
            if attempt["attempt_reason"] != "initial" or attempt["retry_decision_id"] is not None:
                _fail(JobValidationPhase.RELATION, "relation.initial-attempt", f"/attempts/{index}", "attempt zero must be initial and have no retry decision")
        elif attempt["attempt_reason"] == "initial" or attempt["retry_decision_id"] is None:
            _fail(JobValidationPhase.RELATION, "relation.retry-attempt", f"/attempts/{index}", "later attempts require a retry/recovery reason and exact retry decision")
        expected_previous = None if prior is None else prior["id"]
        if attempt["previous_attempt_record_id"] != expected_previous:
            _fail(JobValidationPhase.RELATION, "relation.attempt-predecessor", f"/attempts/{index}/previous_attempt_record_id", "attempt predecessor is not the prior ordinal")
        if not set(attempt["resource_claim_keys"]).issubset(allowed_claims):
            _fail(JobValidationPhase.RELATION, "relation.attempt-claims", f"/attempts/{index}/resource_claim_keys", "attempt requests an undeclared claim key")
        if prior is not None:
            _resolve_job(attempt["previous_attempt_record_id"], "job-attempt", local, relations, f"/attempts/{index}/previous_attempt_record_id")
        prior = attempt


def _validate_attempt_session_bindings(
    attempts: Iterable[Mapping[str, Any]],
    sessions: Iterable[Mapping[str, Any]],
) -> None:
    sessions_by_id: dict[str, list[Mapping[str, Any]]] = {}
    for session in sessions:
        sessions_by_id.setdefault(session["session_id"], []).append(session)
    for index, attempt in enumerate(attempts):
        if attempt["session_id"] is None:
            continue
        matches = sessions_by_id.get(attempt["session_id"], [])
        if len(matches) != 1 or matches[0]["attempt_record_id"] != attempt["id"]:
            _fail(JobValidationPhase.RELATION, "relation.attempt-session", f"/attempts/{index}/session_id", "attempt session_id must identify exactly one session owned by that attempt")


def _validate_session_owner(
    session: Mapping[str, Any],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    path: str,
) -> dict[str, Any]:
    _same_anchor(session, anchor, path)
    attempt_path = f"{path}/attempt_record_id"
    attempt = _resolve_job(session["attempt_record_id"], "job-attempt", local, relations, attempt_path)
    _same_anchor(attempt, anchor, attempt_path)
    if attempt["attempt_id"] != session["attempt_id"]:
        _fail(JobValidationPhase.RELATION, "relation.session-attempt", f"{path}/attempt_id", "session attempt ID disagrees with its attempt record")
    return attempt


def _validate_process_owner(
    process: Mapping[str, Any],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    path: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _same_anchor(process, anchor, path)
    attempt_path = f"{path}/attempt_record_id"
    session_path = f"{path}/session_record_id"
    attempt = _resolve_job(process["attempt_record_id"], "job-attempt", local, relations, attempt_path)
    session = _resolve_job(process["session_record_id"], "runtime-session", local, relations, session_path)
    _same_anchor(attempt, anchor, attempt_path)
    _same_anchor(session, anchor, session_path)
    if (
        process["attempt_id"] != attempt["attempt_id"]
        or process["session_id"] != session["session_id"]
        or session["attempt_record_id"] != attempt["id"]
        or session["attempt_id"] != attempt["attempt_id"]
    ):
        _fail(JobValidationPhase.RELATION, "relation.process-owner", path, "process attempt/session ownership is inconsistent")
    return attempt, session


def _validate_parent_cycles(
    records: Iterable[Mapping[str, Any]],
    parent_field: str,
    code: str,
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    expected_kind: str,
) -> None:
    for start in records:
        seen: set[str] = set()
        current = dict(start)
        while True:
            current_id = current["id"]
            if current_id in seen:
                _fail(JobValidationPhase.RELATION, code, f"/{parent_field}", f"{expected_kind} ancestry contains a cycle")
            seen.add(current_id)
            if len(seen) > MAX_ANCESTRY_RECORDS:
                _fail(JobValidationPhase.RELATION, "relation.ancestry-budget-exceeded", f"/{parent_field}", f"{expected_kind} ancestry exceeds {MAX_ANCESTRY_RECORDS} records")
            parent_id = current[parent_field]
            if parent_id is None:
                break
            current = _resolve_job(parent_id, expected_kind, local, relations, f"/{parent_field}")
            if expected_kind == "runtime-session":
                _validate_session_owner(current, anchor, local, relations, f"/{parent_field}")
            else:
                _validate_process_owner(current, anchor, local, relations, f"/{parent_field}")


def _validate_session(
    session: Mapping[str, Any],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    path: str,
) -> None:
    _validate_session_owner(session, anchor, local, relations, path)
    if session["parent_session_record_id"] is not None:
        parent = _resolve_job(session["parent_session_record_id"], "runtime-session", local, relations, f"{path}/parent_session_record_id")
        _same_anchor(parent, anchor, f"{path}/parent_session_record_id")
        if parent["id"] == session["id"]:
            _fail(JobValidationPhase.RELATION, "relation.session-cycle", f"{path}/parent_session_record_id", "session cannot parent itself")


def _validate_process(
    process: Mapping[str, Any],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    path: str,
) -> None:
    _validate_process_owner(process, anchor, local, relations, path)
    if process["supervisor_process_identity_id"] is not None:
        supervisor = _resolve_job(process["supervisor_process_identity_id"], "process-identity", local, relations, f"{path}/supervisor_process_identity_id")
        _same_anchor(supervisor, anchor, f"{path}/supervisor_process_identity_id")
        if supervisor["id"] == process["id"]:
            _fail(JobValidationPhase.RELATION, "relation.process-cycle", f"{path}/supervisor_process_identity_id", "process cannot supervise itself")


def _validate_event_owner(
    event: Mapping[str, Any],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
    path: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    _same_anchor(event, anchor, path)
    attempt: dict[str, Any] | None = None
    session: dict[str, Any] | None = None
    process: dict[str, Any] | None = None
    if event["attempt_record_id"] is not None:
        attempt = _resolve_job(event["attempt_record_id"], "job-attempt", local, relations, f"{path}/attempt_record_id")
        if attempt["attempt_id"] != event["attempt_id"]:
            _fail(JobValidationPhase.RELATION, "relation.event-attempt", f"{path}/attempt_id", "event attempt pair disagrees")
    if event["session_record_id"] is not None:
        session = _resolve_job(event["session_record_id"], "runtime-session", local, relations, f"{path}/session_record_id")
        if session["session_id"] != event["session_id"] or session["attempt_record_id"] != event["attempt_record_id"]:
            _fail(JobValidationPhase.RELATION, "relation.event-session", f"{path}/session_id", "event session pair disagrees")
    if event["process_identity_record_id"] is not None:
        process = _resolve_job(event["process_identity_record_id"], "process-identity", local, relations, f"{path}/process_identity_record_id")
        if process["process_instance_id"] != event["process_instance_id"] or process["session_record_id"] != event["session_record_id"]:
            _fail(JobValidationPhase.RELATION, "relation.event-process", f"{path}/process_instance_id", "event process pair disagrees")
    return attempt, session, process


def _validate_events(
    events: list[dict[str, Any]],
    anchor: Mapping[str, Any],
    local: Mapping[str, ValidatedJobRecord],
    relations: JobRelationResolvers,
) -> None:
    events.sort(key=lambda item: item["event_ordinal"])
    progress: dict[tuple[Any, ...], tuple[int, int | None]] = {}
    requests: dict[str, dict[str, Any]] = {}
    observations: set[str] = set()
    acquired: dict[str, dict[str, Any]] = {}
    released_claims: set[str] = set()
    authorized_attempt_ids = {
        item.id
        for item in local.values()
        if item.kind == "job-attempt"
        and item.to_dict()["job_id"] == anchor["job_id"]
        and item.to_dict()["attempt_ordinal"] == 0
    }
    started_attempt_ids: set[str] = set()
    retired_attempt_ids: set[str] = set()
    active_attempt_id: str | None = None
    bound_process_ids: set[str] = set()
    exited_process_ids: set[str] = set()
    pending_orphan: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    for index, event in enumerate(events):
        path = f"/events/{index}"
        attempt_owner, _, process_owner = _validate_event_owner(event, anchor, local, relations, path)
        if event["event_ordinal"] != index:
            _fail(JobValidationPhase.RELATION, "relation.event-ordinal", f"{path}/event_ordinal", "event ordinals must be contiguous from zero")
        if index > 0 and event["event_type"] == "queued":
            _fail(JobValidationPhase.RELATION, "relation.duplicate-genesis", path, "queued is the unique genesis event")
        expected_previous = None if previous is None else previous["id"]
        if event["previous_event_id"] != expected_previous:
            _fail(JobValidationPhase.RELATION, "relation.event-predecessor", f"{path}/previous_event_id", "event does not point to the prior ordinal")
        if pending_orphan is not None and event["event_type"] != "recovery-classified" and event["lifecycle_state"] != "orphaned":
            _fail(JobValidationPhase.RELATION, "relation.recovery-required", path, "an orphaned owner requires exact recovery classification before resume, retry, or terminal")
        fixed = _FIXED_LIFECYCLE.get(event["event_type"])
        if fixed is not None and event["lifecycle_state"] != fixed:
            _fail(JobValidationPhase.RELATION, "relation.event-lifecycle", f"{path}/lifecycle_state", f"{event['event_type']} requires {fixed}")
        if previous is not None:
            if event["lifecycle_state"] not in _LIFECYCLE_TRANSITIONS[previous["lifecycle_state"]]:
                _fail(JobValidationPhase.RELATION, "relation.lifecycle-transition", f"{path}/lifecycle_state", "invalid lifecycle transition")
            if event["mutation_state"] not in _MUTATION_TRANSITIONS[previous["mutation_state"]]:
                _fail(JobValidationPhase.RELATION, "relation.mutation-transition", f"{path}/mutation_state", "invalid mutation-state transition")
            if event["event_type"] in _PRESERVE_LIFECYCLE_EVENTS and event["lifecycle_state"] != previous["lifecycle_state"]:
                _fail(JobValidationPhase.RELATION, "relation.lifecycle-preservation", f"{path}/lifecycle_state", f"{event['event_type']} cannot advance lifecycle")
            if event["event_type"] not in {"mutation-state-changed", "recovery-classified"} and event["mutation_state"] != previous["mutation_state"]:
                _fail(JobValidationPhase.RELATION, "relation.mutation-preservation", f"{path}/mutation_state", f"{event['event_type']} cannot advance mutation state")
            if event["event_type"] == "mutation-state-changed" and event["body"]["from_state"] != previous["mutation_state"]:
                _fail(JobValidationPhase.RELATION, "relation.mutation-predecessor", f"{path}/body/from_state", "from_state does not equal prior event state")
            if event["event_type"] == "recovery-classified":
                if previous["lifecycle_state"] != "orphaned":
                    _fail(JobValidationPhase.RELATION, "relation.recovery-without-orphan", path, "crash reconciliation must follow an orphaned phase")
                recovery_state = {
                    "no-mutation-began": "not-started",
                    "create-new-residue": "temporary-residue",
                    "manifest-published-ref-unmoved": "immutable-output-published",
                    "ref-committed": "reference-committed",
                    "exact-process-still-live": previous["mutation_state"],
                    "external-mutation-unproven": "external-mutation-indeterminate",
                    "terminal-corrupt-or-incomplete": previous["mutation_state"],
                }[event["body"]["classification"]]
                if event["mutation_state"] != recovery_state:
                    _fail(JobValidationPhase.RELATION, "relation.recovery-transition", f"{path}/mutation_state", "recovery classification does not mechanically justify its state")
        elif event["event_type"] != "queued" or event["lifecycle_state"] != "queued" or event["mutation_state"] != "not-started":
            _fail(JobValidationPhase.RELATION, "relation.event-genesis", path, "event zero must queue an unmutated job")

        body = event["body"]
        event_type = event["event_type"]
        if attempt_owner is not None:
            attempt_record_id = attempt_owner["id"]
            if attempt_record_id not in authorized_attempt_ids:
                _fail(JobValidationPhase.RELATION, "relation.retry-before-attempt", f"{path}/attempt_record_id", "a later attempt emitted an event before its predecessor retry decision")
            if event_type == "attempt-starting":
                if attempt_record_id in started_attempt_ids:
                    _fail(JobValidationPhase.RELATION, "relation.attempt-start-duplicate", f"{path}/attempt_record_id", "an attempt has more than one starting event")
                if active_attempt_id is not None and attempt_owner["previous_attempt_record_id"] != active_attempt_id:
                    _fail(JobValidationPhase.RELATION, "relation.inactive-attempt-event", f"{path}/attempt_record_id", "a later attempt did not start from the active predecessor")
                started_attempt_ids.add(attempt_record_id)
                active_attempt_id = attempt_record_id
            elif attempt_record_id not in started_attempt_ids:
                _fail(JobValidationPhase.RELATION, "relation.attempt-before-start", f"{path}/attempt_record_id", "an attempt emitted an event before its starting event")
            elif (
                attempt_record_id in retired_attempt_ids
                or (active_attempt_id is not None and attempt_record_id != active_attempt_id)
            ) and event_type not in _RETIRED_ATTEMPT_CLEANUP_EVENTS:
                _fail(JobValidationPhase.RELATION, "relation.inactive-attempt-event", f"{path}/attempt_record_id", "a retired attempt emitted a non-cleanup event")
        if process_owner is not None:
            process_record_id = process_owner["id"]
            if event_type == "process-bound":
                if process_record_id in bound_process_ids:
                    _fail(JobValidationPhase.RELATION, "relation.process-bind-duplicate", f"{path}/process_identity_record_id", "a process has more than one binding event")
                bound_process_ids.add(process_record_id)
            elif process_record_id not in bound_process_ids:
                _fail(JobValidationPhase.RELATION, "relation.process-before-bind", f"{path}/process_identity_record_id", "a process-owned event precedes its exact binding event")
            if event_type == "process-exited":
                if process_record_id in exited_process_ids:
                    _fail(JobValidationPhase.RELATION, "relation.process-exit-duplicate", f"{path}/process_identity_record_id", "a process has more than one exit event")
                exited_process_ids.add(process_record_id)
            elif process_record_id in exited_process_ids and event_type not in _EXITED_PROCESS_CLEANUP_EVENTS:
                _fail(JobValidationPhase.RELATION, "relation.process-after-exit", f"{path}/process_identity_record_id", "an exited process emitted a new-work event")
        if event["mutation_state"] not in _MUTATION_BOUNDARY_STATES[anchor["mutation_boundary"]]:
            _fail(JobValidationPhase.RELATION, "relation.mutation-boundary", f"{path}/mutation_state", "event mutation state is outside the submitted mutation boundary")
        if event_type == "attempt-starting" and attempt_owner is not None and body["attempt_reason"] != attempt_owner["attempt_reason"]:
            _fail(JobValidationPhase.RELATION, "relation.attempt-starting-reason", f"{path}/body/attempt_reason", "event reason differs from its exact attempt record")
        if event_type == "process-bound" and process_owner is not None and body["binding_method"] != process_owner["binding_method"]:
            _fail(JobValidationPhase.RELATION, "relation.process-binding-method", f"{path}/body/binding_method", "event binding method differs from process identity")
        if event_type == "retrying":
            if attempt_owner is None or body["next_attempt_ordinal"] != attempt_owner["attempt_ordinal"] + 1:
                _fail(JobValidationPhase.RELATION, "relation.retry-next-attempt", f"{path}/body/next_attempt_ordinal", "retry event does not advance exactly one attempt ordinal")
            next_attempts = [
                item.to_dict()
                for item in local.values()
                if item.kind == "job-attempt"
                and item.to_dict()["job_id"] == event["job_id"]
                and item.to_dict()["attempt_ordinal"] == body["next_attempt_ordinal"]
            ]
            if len(next_attempts) != 1:
                _fail(JobValidationPhase.RELATION, "relation.retry-next-attempt", f"{path}/body/retry_decision_id", "retry event does not bind the exact next attempt decision")
            next_attempt = next_attempts[0]
            if (
                next_attempt["previous_attempt_record_id"] != attempt_owner["id"]
                or next_attempt["retry_decision_id"] != body["retry_decision_id"]
                or next_attempt["id"] != body["next_attempt_record_id"]
                or next_attempt["attempt_id"] != body["next_attempt_id"]
            ):
                _fail(JobValidationPhase.RELATION, "relation.retry-next-attempt", f"{path}/body", "retry event does not bind the exact predecessor, next attempt, and decision")
            retired_attempt_ids.add(attempt_owner["id"])
            authorized_attempt_ids.add(next_attempt["id"])
        if event_type == "orphaned" and body["last_known_process_identity_id"] is not None:
            last_process = _resolve_job(body["last_known_process_identity_id"], "process-identity", local, relations, f"{path}/body/last_known_process_identity_id")
            _same_anchor(last_process, anchor, f"{path}/body/last_known_process_identity_id")
        if event_type == "recovery-classified" and body["prior_process_identity_id"] is not None:
            prior_process = _resolve_job(body["prior_process_identity_id"], "process-identity", local, relations, f"{path}/body/prior_process_identity_id")
            _same_anchor(prior_process, anchor, f"{path}/body/prior_process_identity_id")
            if (
                body["classification"] == "exact-process-still-live"
                and prior_process["id"] in exited_process_ids
            ):
                _fail(
                    JobValidationPhase.RELATION,
                    "relation.recovery-exited-process",
                    f"{path}/body/classification",
                    "a process with an exact prior exit observation cannot be classified as still live",
                )
        if event_type == "orphaned":
            if pending_orphan is not None:
                _fail(JobValidationPhase.RELATION, "relation.recovery-required", path, "a second orphan event cannot replace an unreconciled orphan")
            pending_orphan = event
        elif event_type == "recovery-classified":
            if pending_orphan is None:
                _fail(JobValidationPhase.RELATION, "relation.recovery-without-orphan", path, "recovery classification has no preceding unreconciled orphan")
            for owner_field in (
                "attempt_record_id", "attempt_id", "session_record_id", "session_id",
                "process_identity_record_id", "process_instance_id",
            ):
                if event[owner_field] != pending_orphan[owner_field]:
                    _fail(JobValidationPhase.RELATION, "relation.recovery-target", f"{path}/{owner_field}", "recovery classification changed the orphaned owner target")
            if event["body"]["prior_process_identity_id"] != pending_orphan["body"]["last_known_process_identity_id"]:
                _fail(JobValidationPhase.RELATION, "relation.recovery-target", f"{path}/body/prior_process_identity_id", "recovery classification changed the orphaned process target")
            pending_orphan = None
        if event_type == "progress":
            key = (event["attempt_id"], body["phase"], body["unit"])
            prior = progress.get(key)
            if prior is not None and body["completed"] < prior[0]:
                _fail(JobValidationPhase.RELATION, "relation.progress-regression", f"{path}/body/completed", "progress completed regressed")
            if prior is not None and prior[1] is not None and body["total"] != prior[1]:
                _fail(JobValidationPhase.RELATION, "relation.progress-total", f"{path}/body/total", "progress total changed")
            progress[key] = (body["completed"], body["total"] if body["total"] is not None else (prior[1] if prior else None))
        elif event_type == "cancellation-requested":
            if previous is None or body["expected_event_id"] != previous["id"] or body["expected_event_ordinal"] != previous["event_ordinal"]:
                _fail(JobValidationPhase.RELATION, "relation.cancellation-cas", f"{path}/body", "cancellation request does not bind the immediately prior head")
            if body["cancellation_request_id"] in requests:
                _fail(JobValidationPhase.RELATION, "relation.cancellation-duplicate", f"{path}/body/cancellation_request_id", "cancellation request ID is reused")
            requests[body["cancellation_request_id"]] = event
        elif event_type == "cancellation-observed":
            request = requests.get(body["cancellation_request_id"])
            if request is None or request["id"] != body["request_event_id"]:
                _fail(JobValidationPhase.RELATION, "relation.cancellation-observation", f"{path}/body/request_event_id", "observation does not bind an earlier request")
            if body["cancellation_request_id"] in observations:
                _fail(JobValidationPhase.RELATION, "relation.cancellation-observation-duplicate", f"{path}/body/cancellation_request_id", "one cancellation request has multiple observations")
            observations.add(body["cancellation_request_id"])
            before = event["mutation_state"] in {"not-started", "temporary-residue"}
            disposition = body["disposition"]
            if disposition == "stopping-before-mutation" and not before:
                _fail(JobValidationPhase.RELATION, "relation.cancellation-disposition", f"{path}/body/disposition", "before-mutation disposition disagrees with mutation state")
            if disposition == "stopping-after-mutation" and before:
                _fail(JobValidationPhase.RELATION, "relation.cancellation-disposition", f"{path}/body/disposition", "after-mutation disposition disagrees with mutation state")
            if disposition == "cannot-prove" and event["mutation_state"] != "external-mutation-indeterminate":
                _fail(JobValidationPhase.RELATION, "relation.cancellation-disposition", f"{path}/body/disposition", "cannot-prove requires indeterminate mutation state")
        elif event_type == "resource-claim-acquired":
            claim_id = body["resource_claim_id"]
            if claim_id in acquired:
                _fail(JobValidationPhase.RELATION, "relation.claim-duplicate", f"{path}/body/resource_claim_id", "resource claim ID was already acquired")
            requested = {item["claim_key"]: item for item in anchor["requested_resource_claims"]}
            if body["claim_key"] not in requested:
                _fail(JobValidationPhase.RELATION, "relation.claim-undeclared", f"{path}/body/claim_key", "claim key was not requested")
            if attempt_owner is None or body["claim_key"] not in attempt_owner["resource_claim_keys"]:
                _fail(JobValidationPhase.RELATION, "relation.attempt-claim-scope", f"{path}/body/claim_key", "acquisition is outside the exact owning attempt claim set")
            declared = requested[body["claim_key"]]
            for field in ("resource_class", "scope_id", "mode", "quantity", "unit", "limit_kind"):
                if body[field] != declared[field]:
                    _fail(JobValidationPhase.RELATION, "relation.claim-request-mismatch", f"{path}/body/{field}", f"acquisition does not repeat requested {field}")
            acquired[claim_id] = event
        elif event_type == "resource-claim-released":
            if body["resource_claim_id"] not in acquired:
                _fail(JobValidationPhase.RELATION, "relation.claim-release", f"{path}/body/resource_claim_id", "release has no prior acquisition")
            if body["resource_claim_id"] in released_claims:
                _fail(JobValidationPhase.RELATION, "relation.claim-release-duplicate", f"{path}/body/resource_claim_id", "resource claim was released more than once")
            released_claims.add(body["resource_claim_id"])
        previous = event


def _validate_seal(
    seal: Mapping[str, Any],
    anchor: Mapping[str, Any],
    attempts: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    processes: list[dict[str, Any]],
    events: list[dict[str, Any]],
    local: Mapping[str, ValidatedJobRecord],
) -> None:
    _same_anchor(seal, anchor, "/terminal_seal")
    if not events:
        _fail(JobValidationPhase.RELATION, "relation.seal-empty", "/terminal_seal", "terminal seal requires events")
    ordered_events = sorted(events, key=lambda item: item["event_ordinal"])
    terminal = ordered_events[-1]
    if terminal["event_type"] != "terminal-ready":
        _fail(JobValidationPhase.RELATION, "relation.seal-terminal-event", "/terminal_seal/terminal_event_id", "sealed head is not terminal-ready")
    if seal["terminal_event_id"] != terminal["id"] or seal["event_head_id"] != terminal["id"]:
        _fail(JobValidationPhase.RELATION, "relation.seal-head", "/terminal_seal/event_head_id", "seal head differs from terminal event")
    if seal["event_count"] != len(ordered_events):
        _fail(JobValidationPhase.RELATION, "relation.seal-count", "/terminal_seal/event_count", "event_count is not the complete prefix size")
    expected_root = event_ledger_root(anchor["job_id"], [item["id"] for item in ordered_events])
    if seal["event_ledger_root"] != expected_root:
        _fail(JobValidationPhase.RELATION, "relation.seal-root", "/terminal_seal/event_ledger_root", "event ledger root mismatch")
    if seal["attempt_record_ids"] != [item["id"] for item in sorted(attempts, key=lambda item: item["attempt_ordinal"])]:
        _fail(JobValidationPhase.RELATION, "relation.seal-attempts", "/terminal_seal/attempt_record_ids", "seal does not enumerate attempts by ordinal")
    if set(seal["session_record_ids"]) != {item["id"] for item in sessions}:
        _fail(JobValidationPhase.RELATION, "relation.seal-sessions", "/terminal_seal/session_record_ids", "seal session membership is incomplete")
    if set(seal["process_identity_record_ids"]) != {item["id"] for item in processes}:
        _fail(JobValidationPhase.RELATION, "relation.seal-processes", "/terminal_seal/process_identity_record_ids", "seal process membership is incomplete")
    attempt_starts = [item["attempt_record_id"] for item in ordered_events if item["event_type"] == "attempt-starting"]
    attempt_ids = {item["id"] for item in attempts}
    if len(attempt_starts) != len(set(attempt_starts)) or not set(attempt_starts).issubset(attempt_ids):
        _fail(JobValidationPhase.RELATION, "relation.seal-attempt-custody", "/terminal_seal/attempt_record_ids", "started attempt custody is duplicated or references an unsealed attempt")
    unstarted_attempts = [item for item in attempts if item["id"] not in set(attempt_starts)]
    if unstarted_attempts:
        highest_ordinal = max(item["attempt_ordinal"] for item in attempts)
        if (
            len(unstarted_attempts) != 1
            or unstarted_attempts[0]["attempt_ordinal"] == 0
            or unstarted_attempts[0]["attempt_ordinal"] != highest_ordinal
            or not seal["terminal_outcome"].startswith("cancelled-")
            or any(
                event["attempt_record_id"] == unstarted_attempts[0]["id"]
                for event in ordered_events
            )
        ):
            _fail(
                JobValidationPhase.RELATION,
                "relation.seal-unstarted-attempt",
                "/terminal_seal/attempt_record_ids",
                "only one exact final retry-authorized attempt may remain unstarted, and only when cancellation seals before it emits any event",
            )
    process_binds = [item["process_identity_record_id"] for item in ordered_events if item["event_type"] == "process-bound"]
    if len(process_binds) != len(set(process_binds)) or set(process_binds) != {item["id"] for item in processes}:
        _fail(JobValidationPhase.RELATION, "relation.seal-process-custody", "/terminal_seal/process_identity_record_ids", "every sealed process requires exactly one process-bound event")
    referenced_sessions = {item["session_record_id"] for item in ordered_events if item["session_record_id"] is not None}
    if referenced_sessions != {item["id"] for item in sessions}:
        _fail(JobValidationPhase.RELATION, "relation.seal-session-custody", "/terminal_seal/session_record_ids", "every sealed session must occur in the event prefix")
    retry_events = [item for item in ordered_events if item["event_type"] == "retrying"]
    for attempt in attempts:
        if attempt["attempt_ordinal"] == 0:
            continue
        matches = [item for item in retry_events if item["body"]["next_attempt_ordinal"] == attempt["attempt_ordinal"] and item["body"]["retry_decision_id"] == attempt["retry_decision_id"]]
        if len(matches) != 1:
            _fail(JobValidationPhase.RELATION, "relation.seal-retry-custody", "/terminal_seal/attempt_record_ids", "each later attempt requires one exact retrying event")
    terminal_body = terminal["body"]
    for seal_field, body_field in (
        ("terminal_outcome", "terminal_outcome"),
        ("result_object_descriptor_id", "result_object_descriptor_id"),
        ("failure_object_descriptor_id", "failure_object_descriptor_id"),
    ):
        if seal[seal_field] != terminal_body[body_field]:
            _fail(JobValidationPhase.RELATION, "relation.seal-terminal-agreement", f"/terminal_seal/{seal_field}", "seal disagrees with terminal-ready event")
    if seal["mutation_state"] != terminal["mutation_state"]:
        _fail(JobValidationPhase.RELATION, "relation.seal-terminal-agreement", "/terminal_seal/mutation_state", "seal mutation state disagrees with terminal-ready event")
    for diagnostic_index, diagnostic in enumerate(seal["diagnostics"]):
        for related_index, related in enumerate(diagnostic["related_records"]):
            if related["record_kind"] == "object-descriptor":
                continue
            related_record = local.get(related["record_id"])
            if related_record is None or related_record.kind != related["record_kind"]:
                _fail(JobValidationPhase.RELATION, "relation.diagnostic-reference", f"/terminal_seal/diagnostics/{diagnostic_index}/related_records/{related_index}", "diagnostic reference is absent or has the wrong kind")
            if related_record.to_dict()["job_id"] != anchor["job_id"]:
                _fail(JobValidationPhase.RELATION, "relation.diagnostic-reference", f"/terminal_seal/diagnostics/{diagnostic_index}/related_records/{related_index}", "diagnostic references another job")
    request_ids = sorted(
        [item["body"]["cancellation_request_id"] for item in ordered_events if item["event_type"] == "cancellation-requested"],
        key=lambda item: item.encode("utf-8"),
    )
    observation_events = [item for item in ordered_events if item["event_type"] == "cancellation-observed"]
    observed = {item["body"]["cancellation_request_id"] for item in observation_events}
    if seal["cancellation_request_ids"] != request_ids or seal["cancellation_observed"] != bool(observed):
        _fail(JobValidationPhase.RELATION, "relation.seal-cancellation", "/terminal_seal/cancellation_request_ids", "seal cancellation summary is not exact")
    if seal["terminal_outcome"].startswith("cancelled-") and not observed:
        _fail(JobValidationPhase.RELATION, "relation.cancelled-unobserved", "/terminal_seal/terminal_outcome", "cancelled outcome requires an observation")
    if seal["terminal_outcome"] == "cancelled-before-mutation" and not any(
        item["body"]["disposition"] == "stopping-before-mutation"
        for item in observation_events
    ):
        _fail(JobValidationPhase.RELATION, "relation.cancellation-outcome", "/terminal_seal/terminal_outcome", "before-mutation cancellation requires a matching observation disposition")
    if seal["terminal_outcome"] == "cancelled-after-mutation" and not any(
        item["body"]["disposition"] == "stopping-after-mutation"
        for item in observation_events
    ):
        _fail(JobValidationPhase.RELATION, "relation.cancellation-outcome", "/terminal_seal/terminal_outcome", "after-mutation cancellation requires a matching observation disposition")

    acquisitions = {item["body"]["resource_claim_id"]: item for item in ordered_events if item["event_type"] == "resource-claim-acquired"}
    releases = {item["body"]["resource_claim_id"]: item for item in ordered_events if item["event_type"] == "resource-claim-released"}
    accounting = {item["resource_claim_id"]: item for item in seal["resource_claim_accounting"]}
    if len(accounting) != len(seal["resource_claim_accounting"]) or set(accounting) != set(acquisitions):
        _fail(JobValidationPhase.RELATION, "relation.seal-claims", "/terminal_seal/resource_claim_accounting", "seal claim membership is not exact")
    for claim_id, acquired in acquisitions.items():
        item = accounting[claim_id]
        released = releases.get(claim_id)
        if item["claim_key"] != acquired["body"]["claim_key"] or item["acquired_event_id"] != acquired["id"]:
            _fail(JobValidationPhase.RELATION, "relation.seal-claims", "/terminal_seal/resource_claim_accounting", "claim accounting does not bind acquisition")
        if released is not None:
            if item["released_event_id"] != released["id"] or item["terminal_disposition"] != "released" or item["reconciliation_observation_id"] is not None:
                _fail(JobValidationPhase.RELATION, "relation.seal-claims", "/terminal_seal/resource_claim_accounting", "released claim accounting is incorrect")
        elif item["released_event_id"] is not None or item["terminal_disposition"] == "released" or item["reconciliation_observation_id"] is None:
            _fail(JobValidationPhase.RELATION, "relation.seal-claims", "/terminal_seal/resource_claim_accounting", "unreleased claim is falsely marked released")

    expected_obligations: list[tuple[str, str, str]] = []
    obligation_by_recovery = {
        "create-new-residue": ("temporary-residue", "clean"),
        "manifest-published-ref-unmoved": ("unmoved-reference", "verify"),
        "external-mutation-unproven": ("external-mutation-indeterminate", "manual-reconcile"),
        "terminal-corrupt-or-incomplete": ("manual-verification", "manual-reconcile"),
    }
    for event in ordered_events:
        if event["event_type"] == "recovery-classified":
            expected = obligation_by_recovery.get(event["body"]["classification"])
            if expected is not None:
                expected_obligations.append((expected[0], expected[1], event["id"]))
    if not expected_obligations:
        fallback = {
            "temporary-residue": ("temporary-residue", "clean"),
            "protected-mutation-started": ("partial-protected-mutation", "manual-reconcile"),
            "protected-mutation-partial": ("partial-protected-mutation", "manual-reconcile"),
            "external-mutation-indeterminate": ("external-mutation-indeterminate", "manual-reconcile"),
            "external-mutation-started": ("external-mutation-partial", "manual-reconcile"),
            "external-mutation-partial": ("external-mutation-partial", "manual-reconcile"),
        }.get(seal["mutation_state"])
        if fallback is not None:
            expected_obligations.append((fallback[0], fallback[1], terminal["id"]))
    actual_obligations = [
        (item["classification"], item["required_action"], item["related_event_id"])
        for item in seal["recovery_obligations"]
    ]
    if actual_obligations != expected_obligations:
        _fail(JobValidationPhase.RELATION, "relation.recovery-obligations", "/terminal_seal/recovery_obligations", "recovery obligations are not the exact mechanically required list")


def validate_job_publication(
    records: Iterable[Mapping[str, Any] | ValidatedJobRecord | bytes | bytearray | memoryview],
    *,
    relations: JobRelationResolvers,
) -> tuple[ValidatedJobRecord, ...]:
    if type(relations) is not JobRelationResolvers:
        raise TypeError("relations must be an exact JobRelationResolvers value")
    supplied = list(records)
    job_values: list[ValidatedJobRecord] = []
    for value in supplied:
        job_values.append(_coerce_job_record(value))

    local: dict[str, ValidatedJobRecord] = {}
    for record in job_values:
        if record.id in local:
            _fail(JobValidationPhase.RELATION, "relation.duplicate-record", "/id", f"duplicate record {record.id}")
        local[record.id] = record
    decoded = [item.to_dict() for item in job_values]
    global_operational_ids: list[tuple[str, str, str]] = []
    for item in decoded:
        if item["kind"] == "job-attempt":
            global_operational_ids.append(("attempt", item["attempt_id"], "/attempt_id"))
        elif item["kind"] == "runtime-session":
            global_operational_ids.append(("session", item["session_id"], "/session_id"))
        elif item["kind"] == "process-identity":
            global_operational_ids.append(("process", item["process_instance_id"], "/process_instance_id"))
        elif item["kind"] == "job-event":
            if item["event_type"] == "cancellation-requested":
                global_operational_ids.append(("cancellation", item["body"]["cancellation_request_id"], "/body/cancellation_request_id"))
            elif item["event_type"] == "resource-claim-acquired":
                global_operational_ids.append(("resource-claim", item["body"]["resource_claim_id"], "/body/resource_claim_id"))
            elif item["event_type"] == "recovery-classified":
                global_operational_ids.append(("recovery", item["body"]["recovery_id"], "/body/recovery_id"))
    seen_operational: set[tuple[str, str]] = set()
    for category, value, path in global_operational_ids:
        key = (category, value)
        if key in seen_operational:
            _fail(JobValidationPhase.RELATION, "relation.duplicate-operational-id", path, f"{category} operational ID is reused")
        seen_operational.add(key)
    process_tuples = [
        (
            item["platform"], item["host_instance_id"], item["boot_id"],
            item["pid"], item["process_start_token"],
        )
        for item in decoded
        if item["kind"] == "process-identity"
    ]
    if len(process_tuples) != len(set(process_tuples)):
        _fail(JobValidationPhase.RELATION, "relation.duplicate-process-tuple", "/processes", "one exact OS process tuple has multiple identities in the publication")
    jobs = sorted({item["job_id"] for item in decoded}, key=lambda item: item.encode("utf-8"))
    for job_index, job_id in enumerate(jobs):
        group = [item for item in decoded if item["job_id"] == job_id]
        submissions = [item for item in group if item["kind"] == "job-submission"]
        if len(submissions) != 1:
            _fail(JobValidationPhase.RELATION, "relation.submission-count", f"/jobs/{job_index}", "each job publication requires exactly one submission")
        anchor = submissions[0]
        _validate_context_input(anchor, relations)
        _validate_retry_lineage(anchor, local, relations)
        _validate_external_applicability(group, relations)
        attempts = [item for item in group if item["kind"] == "job-attempt"]
        sessions = [item for item in group if item["kind"] == "runtime-session"]
        processes = [item for item in group if item["kind"] == "process-identity"]
        events = [item for item in group if item["kind"] == "job-event"]
        seals = [item for item in group if item["kind"] == "job-terminal-seal"]
        if len(seals) > 1:
            _fail(JobValidationPhase.RELATION, "relation.multiple-terminal-seals", f"/jobs/{job_index}", "job has more than one logical terminal seal")
        _validate_attempts(attempts, anchor, local, relations)
        session_ids = [item["session_id"] for item in sessions]
        if len(session_ids) != len(set(session_ids)):
            _fail(JobValidationPhase.RELATION, "relation.duplicate-session-id", f"/jobs/{job_index}", "session_id is reused")
        process_ids = [item["process_instance_id"] for item in processes]
        if len(process_ids) != len(set(process_ids)):
            _fail(JobValidationPhase.RELATION, "relation.duplicate-process-id", f"/jobs/{job_index}", "process_instance_id is reused")
        _validate_attempt_session_bindings(attempts, sessions)
        for index, session in enumerate(sessions):
            _validate_session(session, anchor, local, relations, f"/sessions/{index}")
        for index, process in enumerate(processes):
            _validate_process(process, anchor, local, relations, f"/processes/{index}")
        _validate_parent_cycles(sessions, "parent_session_record_id", "relation.session-cycle", anchor, local, relations, "runtime-session")
        _validate_parent_cycles(processes, "supervisor_process_identity_id", "relation.process-cycle", anchor, local, relations, "process-identity")
        _validate_events(events, anchor, local, relations)
        if seals:
            _require_complete_seal_closure(attempts, sessions, processes, events, local)
            _validate_seal(seals[0], anchor, attempts, sessions, processes, events, local)
        elif events and events[-1]["lifecycle_state"] == "terminal":
            _fail(JobValidationPhase.RELATION, "relation.missing-terminal-seal", f"/jobs/{job_index}", "terminal-ready history requires its immutable seal")
    return tuple(job_values)
