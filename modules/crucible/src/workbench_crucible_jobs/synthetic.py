"""Deterministic complete publication for the durable job/session V2 contract."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from typing import Any, Mapping

from workbench_crucible_context import (
    ValidatedContextRecord,
    seal_context_ref,
    seal_input_binding,
)
from workbench_api.canonical import canonical_json_bytes

from .records import (
    JobRelationResolvers,
    ValidatedJobRecord,
    event_ledger_root,
    external_reference_expectations,
    seal_job_record,
    validate_job_publication,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _content(kind: str, label: str) -> str:
    return f"{kind}:sha256:{_digest(label)}"


def _operational(prefix: str, label: str) -> str:
    return f"{prefix}-v2:{_digest(label)[:32]}"


def _header(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "format": f"workbench-crucible-{kind}-v2",
        "schema_version": 2,
        "schema_id": f"workbench://schemas/crucible/crucible-{kind}-v2.schema.json",
        "canonicalizer": "workbench-canonical-json-v2",
    }


def _context_records() -> tuple[ValidatedContextRecord, ValidatedContextRecord]:
    authority = {
        "owner_authority_id": _content("authority", "profile-authority"),
        "owner_revision_id": _content("owner-revision", "profile-owner-revision"),
        "authority_adapter_id": _content("authority-adapter", "profile-authority-adapter"),
    }
    context = seal_context_ref(
        {
            "kind": "context-ref",
            "format": "workbench-crucible-context-ref-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-context-ref-v2.schema.json",
            "canonicalizer": "workbench-canonical-json-v2",
            "store_id": _content("store", "store"),
            "workspace_binding": {
                "workspace_id": _content("workspace", "workspace"),
                "workspace_registration_revision_id": _content("workspace-registration-revision", "workspace-registration"),
            },
            "profile_scope": {
                "platform": {
                    "platform_profile_revision_id": _content("platform-profile-revision", "cleanroom-profile"),
                    "profile_adapter_id": _content("profile-adapter", "cleanroom-adapter"),
                    "profile_authority": authority,
                    "support_decision_ids": [_content("support-decision", "experimental-support")],
                    "support_state": "experimental",
                },
                "pack": {"kind": "none"},
            },
            "source_lock_ids": [],
            "dependency_lock_ids": [],
            "artifact_lock_ids": [],
            "runtime_scope": {"kind": "none"},
            "world_scope": {"kind": "none"},
            "dimension_scope": {"kind": "none"},
            "region_scope": {"kind": "none"},
            "operation_scopes": [
                {"scope_kind": "action", "scope_id": _content("action", "synthetic-job")}
            ],
            "privacy_class_id": _content("privacy-class", "project"),
            "resource_budget_class_id": _content("resource-budget-class", "small"),
        }
    )
    binding = seal_input_binding(
        {
            "kind": "input-binding",
            "format": "workbench-crucible-input-binding-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-input-binding-v2.schema.json",
            "canonicalizer": "workbench-canonical-json-v2",
            "context_ref_id": context.id,
            "evidence_set_bindings": [],
            "graph_bindings": [],
            "graph_set_bindings": [],
            "recipe_bindings": [],
            "schema_bindings": [],
            "ontology_bindings": [],
            "adapter_bindings": [],
            "policy_bindings": [],
            "index_bindings": [],
        }
    )
    return context, binding


@dataclass(frozen=True, slots=True)
class SyntheticJobPublication:
    records: tuple[ValidatedJobRecord, ...]
    context_ref: ValidatedContextRecord
    input_binding: ValidatedContextRecord
    aliases: Mapping[str, str]

    def resolver(self, record_id: str):
        if record_id == self.context_ref.id:
            return self.context_ref.canonical_bytes
        if record_id == self.input_binding.id:
            return self.input_binding.canonical_bytes
        for record in self.records:
            if record.id == record_id:
                return record.canonical_bytes
        return None

    @property
    def relations(self) -> JobRelationResolvers:
        allowed = frozenset(
            expectation
            for record in self.records
            for expectation in external_reference_expectations(record.to_dict())
        )
        return JobRelationResolvers(
            record_resolver=self.resolver,
            external_reference_validator=lambda expectation: expectation in allowed,
            context_publication_validator=lambda request: (
                request.context_ref_id == self.context_ref.id
                and request.input_binding_id == self.input_binding.id
                and request.context_ref_bytes == self.context_ref.canonical_bytes
                and request.input_binding_bytes == self.input_binding.canonical_bytes
            ),
        )

    def record(self, alias: str) -> dict[str, Any]:
        record_id = self.aliases[alias]
        for record in self.records:
            if record.id == record_id:
                return record.to_dict()
        raise KeyError(alias)


def build_synthetic_job_publication() -> SyntheticJobPublication:
    context, binding = _context_records()
    job_id = _operational("job", "job")
    attempt_id = _operational("attempt", "attempt-0")
    session_id = _operational("session", "session-0")
    process_id = _operational("process", "process-0")
    actor_id = _content("actor", "operator")
    failure_descriptor = _content("object-descriptor", "cancelled-result")
    request_descriptor = _content("object-descriptor", "request")
    record_list: list[ValidatedJobRecord] = []
    aliases: dict[str, str] = {}

    def add(alias: str, candidate: dict[str, Any]) -> ValidatedJobRecord:
        record = seal_job_record(candidate)
        record_list.append(record)
        aliases[alias] = record.id
        return record

    submission = add(
        "submission",
        {
            **_header("job-submission"),
            "job_id": job_id,
            "context_ref_id": context.id,
            "input_binding_id": binding.id,
            "capability_id": _content("capability", "synthetic-capability"),
            "handler_id": _content("handler", "synthetic-handler"),
            "handler_implementation_id": _content("implementation", "synthetic-handler-implementation"),
            "request_object_descriptor_id": request_descriptor,
            "plan_record_id": _content("operation-plan", "synthetic-plan"),
            "consent_record_id": _content("consent-decision", "synthetic-consent"),
            "idempotency_key_digest": _digest("synthetic-idempotency"),
            "idempotency_scope": "workspace.action",
            "idempotency_collision_policy": "return-existing",
            "requested_resource_claims": [
                {
                    "claim_key": "writer",
                    "resource_class": "store-writer",
                    "scope_id": "workspace.output",
                    "mode": "exclusive",
                    "quantity": 1,
                    "unit": "slots",
                    "limit_kind": "hard",
                }
            ],
            "mutation_boundary": "reference-update",
            "retry_of_terminal_seal_id": None,
            "submitted_by_actor_id": actor_id,
            "submitted_at": "2026-08-08T12:00:00Z",
            "privacy_class": "project",
            "retention_policy_id": _content("policy", "retention-policy"),
            "evidence_eligible": False,
        },
    )
    attempt = add(
        "attempt",
        {
            **_header("job-attempt"),
            "job_id": job_id,
            "job_submission_id": submission.id,
            "attempt_id": attempt_id,
            "attempt_ordinal": 0,
            "previous_attempt_record_id": None,
            "context_ref_id": context.id,
            "input_binding_id": binding.id,
            "attempt_reason": "initial",
            "handler_implementation_id": _content("implementation", "synthetic-handler-implementation"),
            "service_instance_id": _operational("service-instance", "service"),
            "resource_claim_keys": ["writer"],
            "session_id": session_id,
            "retry_decision_id": None,
            "created_at": "2026-08-08T12:00:01Z",
            "evidence_eligible": False,
        },
    )
    session = add(
        "session",
        {
            **_header("runtime-session"),
            "session_id": session_id,
            "job_id": job_id,
            "job_submission_id": submission.id,
            "attempt_record_id": attempt.id,
            "attempt_id": attempt_id,
            "context_ref_id": context.id,
            "input_binding_id": binding.id,
            "session_kind": "process",
            "parent_session_record_id": None,
            "custodian_component_id": _content("component", "runtime-custodian"),
            "custodian_implementation_id": _content("implementation", "runtime-custodian-implementation"),
            "request_object_descriptor_id": request_descriptor,
            "raw_event_schema_object_descriptor_ids": [],
            "privacy_class": "project",
            "retention_policy_id": _content("policy", "retention-policy"),
            "opened_at": "2026-08-08T12:00:02Z",
            "evidence_eligible": False,
        },
    )
    process = add(
        "process",
        {
            **_header("process-identity"),
            "process_instance_id": process_id,
            "job_id": job_id,
            "job_submission_id": submission.id,
            "attempt_record_id": attempt.id,
            "attempt_id": attempt_id,
            "session_record_id": session.id,
            "session_id": session_id,
            "context_ref_id": context.id,
            "input_binding_id": binding.id,
            "platform": "linux",
            "host_instance_id": "host.synthetic",
            "boot_id": "boot.synthetic",
            "pid": 4242,
            "process_start_token": "linux.proc.start.9001",
            "executable_object_descriptor_id": _content("object-descriptor", "executable"),
            "argv_object_descriptor_id": _content("object-descriptor", "argv"),
            "environment_object_descriptor_id": _content("object-descriptor", "environment"),
            "working_directory_object_descriptor_id": _content("object-descriptor", "working-directory"),
            "process_group_id": "process-group.synthetic",
            "supervisor_process_identity_id": None,
            "launch_observation_id": _content("process-launch-observation", "launch"),
            "adoption_reconciliation_id": None,
            "binding_method": "launched",
            "bound_at": "2026-08-08T12:00:03Z",
            "evidence_eligible": False,
        },
    )

    event_records: list[ValidatedJobRecord] = []

    def event(
        alias: str,
        event_type: str,
        lifecycle: str,
        mutation: str,
        body: dict[str, Any],
        *,
        owner: str = "process",
    ) -> ValidatedJobRecord:
        ordinal = len(event_records)
        if owner == "none":
            attempt_record_id = attempt_value = session_record_id = session_value = process_record_id = process_value = None
        elif owner == "attempt":
            attempt_record_id, attempt_value = attempt.id, attempt_id
            session_record_id = session_value = process_record_id = process_value = None
        else:
            attempt_record_id, attempt_value = attempt.id, attempt_id
            session_record_id, session_value = session.id, session_id
            process_record_id, process_value = process.id, process_id
        result = add(
            alias,
            {
                **_header("job-event"),
                "job_id": job_id,
                "job_submission_id": submission.id,
                "context_ref_id": context.id,
                "input_binding_id": binding.id,
                "event_ordinal": ordinal,
                "previous_event_id": None if not event_records else event_records[-1].id,
                "event_type": event_type,
                "lifecycle_state": lifecycle,
                "mutation_state": mutation,
                "attempt_record_id": attempt_record_id,
                "attempt_id": attempt_value,
                "session_record_id": session_record_id,
                "session_id": session_value,
                "process_identity_record_id": process_record_id,
                "process_instance_id": process_value,
                "occurred_at": f"2026-08-08T12:00:{10 + ordinal:02d}Z",
                "actor_id": actor_id,
                "evidence_eligible": False,
                "body": body,
            },
        )
        event_records.append(result)
        return result

    event("event_queued", "queued", "queued", "not-started", {"body_kind": "queued", "queue_policy_id": _content("policy", "queue-policy"), "priority": 10, "not_before": None}, owner="none")
    event("event_starting", "attempt-starting", "starting", "not-started", {"body_kind": "attempt-starting", "attempt_reason": "initial"}, owner="attempt")
    event("event_process_bound", "process-bound", "starting", "not-started", {"body_kind": "process-bound", "binding_method": "launched"})
    claim_id = _operational("resource-claim", "writer-claim")
    acquired = event(
        "event_claim_acquired", "resource-claim-acquired", "starting", "not-started",
        {"body_kind": "resource-claim-acquired", "resource_claim_id": claim_id, "claim_key": "writer", "resource_class": "store-writer", "scope_id": "workspace.output", "mode": "exclusive", "quantity": 1, "unit": "slots", "limit_kind": "hard", "expires_at": "2026-08-08T12:10:00Z", "lease_authoritative": False},
    )
    event("event_running", "running", "running", "not-started", {"body_kind": "running", "readiness_observation_id": _content("readiness-observation", "ready")})
    event("event_progress_pre", "progress", "running", "not-started", {"body_kind": "progress", "phase": "publish", "completed": 1, "total": 2, "unit": "steps", "message": "prepared immutable bytes", "mutation_started": False})
    event("event_mutation", "mutation-state-changed", "running", "immutable-output-published", {"body_kind": "mutation-state-changed", "from_state": "not-started", "to_state": "immutable-output-published", "mutation_observation_id": _content("mutation-observation", "manifest-published")})
    event("event_progress_post", "progress", "running", "immutable-output-published", {"body_kind": "progress", "phase": "publish", "completed": 2, "total": 2, "unit": "steps", "message": "policy:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa is plain progress text", "mutation_started": True})
    cancellation_id = _operational("cancellation", "cancel-request")
    request_predecessor = event_records[-1]
    requested = event("event_cancel_requested", "cancellation-requested", "running", "immutable-output-published", {"body_kind": "cancellation-requested", "cancellation_request_id": cancellation_id, "expected_event_id": request_predecessor.id, "expected_event_ordinal": request_predecessor.to_dict()["event_ordinal"], "reason": "operator requested stop", "requested_by_actor_id": actor_id})
    event("event_cancel_observed", "cancellation-observed", "running", "immutable-output-published", {"body_kind": "cancellation-observed", "cancellation_request_id": cancellation_id, "request_event_id": requested.id, "observation_point": "before.reference.commit", "disposition": "stopping-after-mutation"})
    released = event("event_claim_released", "resource-claim-released", "running", "immutable-output-published", {"body_kind": "resource-claim-released", "resource_claim_id": claim_id, "release_reason": "cancelled", "release_observation_id": _content("resource-release-observation", "released")})
    event("event_process_exited", "process-exited", "running", "immutable-output-published", {"body_kind": "process-exited", "exit_code": 130, "signal": "sigint", "termination_observation_id": _content("termination-observation", "exit")})
    event("event_orphaned", "orphaned", "orphaned", "immutable-output-published", {"body_kind": "orphaned", "last_known_process_identity_id": process.id, "orphan_reason": "service-crash"})
    recovery = event("event_recovery", "recovery-classified", "recovering", "immutable-output-published", {"body_kind": "recovery-classified", "recovery_id": _operational("recovery", "recovery"), "classification": "manifest-published-ref-unmoved", "prior_process_identity_id": process.id, "live_process_revalidated": False, "residue_object_descriptor_id": None, "manifest_object_descriptor_id": _content("object-descriptor", "published-manifest"), "reference_event_id": None, "required_action": "verify-reference", "recovery_decision_id": _content("recovery-decision", "recovery-decision")})
    terminal = event("event_terminal", "terminal-ready", "terminal", "immutable-output-published", {"body_kind": "terminal-ready", "terminal_outcome": "cancelled-after-mutation", "result_object_descriptor_id": None, "failure_object_descriptor_id": failure_descriptor})

    seal = add(
        "terminal_seal",
        {
            **_header("job-terminal-seal"),
            "job_id": job_id,
            "job_submission_id": submission.id,
            "context_ref_id": context.id,
            "input_binding_id": binding.id,
            "terminal_event_id": terminal.id,
            "event_head_id": terminal.id,
            "event_count": len(event_records),
            "event_ledger_root": event_ledger_root(job_id, [item.id for item in event_records]),
            "attempt_record_ids": [attempt.id],
            "session_record_ids": [session.id],
            "process_identity_record_ids": [process.id],
            "terminal_outcome": "cancelled-after-mutation",
            "mutation_state": "immutable-output-published",
            "cancellation_request_ids": [cancellation_id],
            "cancellation_observed": True,
            "result_object_descriptor_id": None,
            "failure_object_descriptor_id": failure_descriptor,
            "resource_claim_accounting": [
                {"resource_claim_id": claim_id, "claim_key": "writer", "acquired_event_id": acquired.id, "released_event_id": released.id, "terminal_disposition": "released", "reconciliation_observation_id": None}
            ],
            "recovery_obligations": [
                {"ordinal": 0, "classification": "unmoved-reference", "required_action": "verify", "related_event_id": recovery.id, "detail": "Verify the immutable manifest before moving any reference."}
            ],
            "diagnostics": [
                {"ordinal": 0, "severity": "warning", "code": "cancelled.after.publish", "message": "Cancellation was observed after immutable publication.", "related_records": sorted([{"record_kind": "job-event", "record_id": recovery.id}, {"record_kind": "job-event", "record_id": requested.id}], key=canonical_json_bytes)}
            ],
            "limitations": [
                {"code": "no.reference.commit", "detail": "policy:sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa is diagnostic text, not a resolved policy."}
            ],
            "sealed_by_actor_id": actor_id,
            "sealed_at": "2026-08-08T12:01:00Z",
            "evidence_eligible": False,
        },
    )
    publication = SyntheticJobPublication(
        records=tuple(record_list),
        context_ref=context,
        input_binding=binding,
        aliases=MappingProxyType(dict(aliases)),
    )
    validate_job_publication(publication.records, relations=publication.relations)
    return publication
