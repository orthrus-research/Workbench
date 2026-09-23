from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
import unittest

from workbench_crucible_jobs import (
    JobExternalReferenceExpectation,
    JobRelationResolvers,
    JobValidationError,
    ValidatedJobRecord,
    event_ledger_root,
    load_job_record,
    registered_job_kinds,
    seal_job_record,
    validate_job_publication,
    validate_job_record,
    validate_evidence_admission_candidate,
)
from workbench_crucible_jobs.synthetic import build_synthetic_job_publication
from workbench_crucible_context import seal_context_ref, seal_input_binding
from workbench_api.canonical import canonical_json_bytes


FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "crucible-job-v2"


def _permissive_relations(publication, *additional_records: ValidatedJobRecord) -> JobRelationResolvers:
    additional_by_id = {
        record.id: record.canonical_bytes
        for record in additional_records
    }

    def resolve(record_id: str):
        return additional_by_id.get(record_id) or publication.resolver(record_id)

    return JobRelationResolvers(
        record_resolver=resolve,
        external_reference_validator=lambda expectation: True,
        context_publication_validator=lambda request: True,
    )


def _replacement(publication, alias: str, mutate):
    original_id = publication.aliases[alias]
    candidate = publication.record(alias)
    candidate.pop("id")
    mutate(candidate)
    replacement = seal_job_record(candidate)
    records = [replacement if item.id == original_id else item for item in publication.records]
    return records, replacement


def _expect_error(call, phase: str, code: str) -> None:
    try:
        call()
    except JobValidationError as raised:
        error = raised
    else:
        raise AssertionError(f"expected {phase}:{code}")
    assert any(
        item.phase.value == phase and item.code == code
        for item in error.diagnostics
    ), error


def _job_header(kind: str) -> dict[str, object]:
    return {
        "kind": kind,
        "format": f"workbench-crucible-{kind}-v2",
        "schema_version": 2,
        "schema_id": f"workbench://schemas/crucible/crucible-{kind}-v2.schema.json",
        "canonicalizer": "workbench-canonical-json-v2",
    }


def _build_two_attempt_publication(
    publication,
    *,
    next_ordinal: int = 1,
    predecessor_id: str | None = None,
    attempt_retry_decision: str | None = None,
    event_retry_decision: str | None = None,
    retry_after_second_running: bool = False,
    orphan_before_terminal: bool = False,
    recover_after_orphan: bool = False,
    recovery_uses_first_attempt: bool = False,
    first_resource_claim_keys: list[str] | None = None,
    include_claim_events: bool = False,
    old_attempt_runs_after_second_start: bool = False,
    cancel_before_second_start: bool = False,
    unstarted_terminal_outcome: str = "cancelled-before-mutation",
):
    """Build a compact, completely sealed two-attempt ledger."""

    submission = next(item for item in publication.records if item.kind == "job-submission")
    anchor = submission.to_dict()
    actor_id = anchor["submitted_by_actor_id"]
    decision = attempt_retry_decision or "retry-decision:sha256:" + "4" * 64
    event_decision = event_retry_decision or decision

    first_candidate = publication.record("attempt")
    first_candidate.pop("id")
    first_candidate["session_id"] = None
    if first_resource_claim_keys is not None:
        first_candidate["resource_claim_keys"] = first_resource_claim_keys
    first = seal_job_record(first_candidate)
    second_candidate = dict(first_candidate)
    second_candidate.update(
        {
            "attempt_id": "attempt-v2:" + "5" * 32,
            "attempt_ordinal": next_ordinal,
            "previous_attempt_record_id": first.id if predecessor_id is None else predecessor_id,
            "attempt_reason": "manual-retry",
            "retry_decision_id": decision,
            "created_at": "2026-08-08T14:00:02Z",
        }
    )
    second = seal_job_record(second_candidate)
    events = []

    def add_event(event_type, lifecycle, body, owner=None):
        ordinal = len(events)
        record = seal_job_record(
            {
                **_job_header("job-event"),
                "job_id": anchor["job_id"],
                "job_submission_id": submission.id,
                "context_ref_id": anchor["context_ref_id"],
                "input_binding_id": anchor["input_binding_id"],
                "event_ordinal": ordinal,
                "previous_event_id": None if not events else events[-1].id,
                "event_type": event_type,
                "lifecycle_state": lifecycle,
                "mutation_state": "not-started",
                "attempt_record_id": None if owner is None else owner.id,
                "attempt_id": None if owner is None else owner.to_dict()["attempt_id"],
                "session_record_id": None,
                "session_id": None,
                "process_identity_record_id": None,
                "process_instance_id": None,
                "occurred_at": f"2026-08-08T14:00:{10 + ordinal:02d}Z",
                "actor_id": actor_id,
                "evidence_eligible": False,
                "body": body,
            }
        )
        events.append(record)

    add_event(
        "queued",
        "queued",
        {"body_kind": "queued", "queue_policy_id": "policy:sha256:" + "6" * 64, "priority": 0, "not_before": None},
    )
    add_event("attempt-starting", "starting", {"body_kind": "attempt-starting", "attempt_reason": "initial"}, first)
    acquired_event = None
    released_event = None
    claim_id = "resource-claim-v2:" + "3" * 32
    if include_claim_events:
        add_event(
            "resource-claim-acquired",
            "starting",
            {
                "body_kind": "resource-claim-acquired",
                "resource_claim_id": claim_id,
                "claim_key": "writer",
                "resource_class": "store-writer",
                "scope_id": "workspace.output",
                "mode": "exclusive",
                "quantity": 1,
                "unit": "slots",
                "limit_kind": "hard",
                "expires_at": "2026-08-08T14:10:00Z",
                "lease_authoritative": False,
            },
            first,
        )
        acquired_event = events[-1]
        add_event(
            "resource-claim-released",
            "starting",
            {
                "body_kind": "resource-claim-released",
                "resource_claim_id": claim_id,
                "release_reason": "completed",
                "release_observation_id": "resource-release-observation:sha256:" + "3" * 64,
            },
            first,
        )
        released_event = events[-1]
    retry_body = {
        "body_kind": "retrying",
        "retry_decision_id": event_decision,
        "next_attempt_record_id": second.id,
        "next_attempt_id": second.to_dict()["attempt_id"],
        "next_attempt_ordinal": 1,
    }
    if retry_after_second_running:
        add_event("attempt-starting", "starting", {"body_kind": "attempt-starting", "attempt_reason": "manual-retry"}, second)
        add_event(
            "running",
            "running",
            {"body_kind": "running", "readiness_observation_id": "readiness-observation:sha256:" + "8" * 64},
            first if old_attempt_runs_after_second_start else second,
        )
        add_event("retrying", "retrying", retry_body, first)
    else:
        add_event("running", "running", {"body_kind": "running", "readiness_observation_id": "readiness-observation:sha256:" + "7" * 64}, first)
        add_event("retrying", "retrying", retry_body, first)
        if not cancel_before_second_start:
            add_event("attempt-starting", "starting", {"body_kind": "attempt-starting", "attempt_reason": "manual-retry"}, second)
            add_event(
                "running",
                "running",
                {"body_kind": "running", "readiness_observation_id": "readiness-observation:sha256:" + "8" * 64},
                first if old_attempt_runs_after_second_start else second,
            )
    if orphan_before_terminal:
        add_event(
            "orphaned",
            "orphaned",
            {"body_kind": "orphaned", "last_known_process_identity_id": None, "orphan_reason": "custody-lost"},
            second,
        )
        if recover_after_orphan:
            add_event(
                "recovery-classified",
                "recovering",
                {
                    "body_kind": "recovery-classified",
                    "recovery_id": "recovery-v2:" + "4" * 32,
                    "classification": "no-mutation-began",
                    "prior_process_identity_id": None,
                    "live_process_revalidated": False,
                    "residue_object_descriptor_id": None,
                    "manifest_object_descriptor_id": None,
                    "reference_event_id": None,
                    "required_action": "retry-new-attempt",
                    "recovery_decision_id": "recovery-decision:sha256:" + "4" * 64,
                },
                first if recovery_uses_first_attempt else second,
            )
    cancellation_request_ids = []
    cancellation_observed = False
    terminal_outcome = "failed"
    terminal_owner = second
    if cancel_before_second_start:
        cancellation_id = "cancellation-v2:" + "9" * 32
        predecessor = events[-1]
        add_event(
            "cancellation-requested",
            predecessor.to_dict()["lifecycle_state"],
            {
                "body_kind": "cancellation-requested",
                "cancellation_request_id": cancellation_id,
                "expected_event_id": predecessor.id,
                "expected_event_ordinal": predecessor.to_dict()["event_ordinal"],
                "reason": "cancel before retry dispatch",
                "requested_by_actor_id": actor_id,
            },
            first,
        )
        requested = events[-1]
        add_event(
            "cancellation-observed",
            requested.to_dict()["lifecycle_state"],
            {
                "body_kind": "cancellation-observed",
                "cancellation_request_id": cancellation_id,
                "request_event_id": requested.id,
                "observation_point": "retry.dispatch.cas",
                "disposition": "stopping-before-mutation",
            },
            first,
        )
        cancellation_request_ids = [cancellation_id]
        cancellation_observed = True
        terminal_outcome = unstarted_terminal_outcome
        terminal_owner = None
    failure_id = "object-descriptor:sha256:" + "9" * 64
    add_event(
        "terminal-ready",
        "terminal",
        {"body_kind": "terminal-ready", "terminal_outcome": terminal_outcome, "result_object_descriptor_id": None, "failure_object_descriptor_id": failure_id},
        terminal_owner,
    )
    seal = seal_job_record(
        {
            **_job_header("job-terminal-seal"),
            "job_id": anchor["job_id"],
            "job_submission_id": submission.id,
            "context_ref_id": anchor["context_ref_id"],
            "input_binding_id": anchor["input_binding_id"],
            "terminal_event_id": events[-1].id,
            "event_head_id": events[-1].id,
            "event_count": len(events),
            "event_ledger_root": event_ledger_root(anchor["job_id"], [item.id for item in events]),
            "attempt_record_ids": [first.id, second.id],
            "session_record_ids": [],
            "process_identity_record_ids": [],
            "terminal_outcome": terminal_outcome,
            "mutation_state": "not-started",
            "cancellation_request_ids": cancellation_request_ids,
            "cancellation_observed": cancellation_observed,
            "result_object_descriptor_id": None,
            "failure_object_descriptor_id": failure_id,
            "resource_claim_accounting": [] if acquired_event is None else [
                {
                    "resource_claim_id": claim_id,
                    "claim_key": "writer",
                    "acquired_event_id": acquired_event.id,
                    "released_event_id": released_event.id,
                    "terminal_disposition": "released",
                    "reconciliation_observation_id": None,
                }
            ],
            "recovery_obligations": [],
            "diagnostics": [],
            "limitations": [],
            "sealed_by_actor_id": actor_id,
            "sealed_at": "2026-08-08T14:01:00Z",
            "evidence_eligible": False,
        }
    )
    return [submission, first, second, *events, seal]


def _retry_submission(publication, **overrides):
    candidate = publication.record("submission")
    candidate.pop("id")
    candidate.update(
        {
            "job_id": "job-v2:" + "a" * 32,
            "idempotency_collision_policy": "retry-after-terminal",
            "retry_of_terminal_seal_id": publication.aliases["terminal_seal"],
            "submitted_at": "2026-08-08T15:00:00Z",
        }
    )
    candidate.update(overrides)
    return seal_job_record(candidate)


def _build_process_order_publication(
    publication,
    *,
    exit_before_bind=False,
    duplicate_exit=False,
    running_after_exit=False,
):
    records_by_kind = {
        item.kind: item
        for item in publication.records
        if item.kind in {"job-submission", "job-attempt", "runtime-session", "process-identity"}
    }
    submission = records_by_kind["job-submission"]
    attempt = records_by_kind["job-attempt"]
    session = records_by_kind["runtime-session"]
    process = records_by_kind["process-identity"]
    source_aliases = ["event_queued", "event_starting"]
    source_aliases.extend(
        ["event_process_exited", "event_process_bound"]
        if exit_before_bind
        else ["event_process_bound", "event_process_exited"]
    )
    if duplicate_exit:
        source_aliases.append("event_process_exited")
    if running_after_exit:
        source_aliases.append("event_running")
    source_aliases.append("event_terminal")
    events = []
    failure_id = "object-descriptor:sha256:" + "2" * 64
    for ordinal, alias in enumerate(source_aliases):
        candidate = publication.record(alias)
        candidate.pop("id")
        candidate["event_ordinal"] = ordinal
        candidate["previous_event_id"] = None if not events else events[-1].id
        candidate["occurred_at"] = f"2026-08-08T16:00:{ordinal:02d}Z"
        candidate["mutation_state"] = "not-started"
        if alias == "event_queued":
            candidate["lifecycle_state"] = "queued"
        elif alias == "event_terminal":
            candidate["lifecycle_state"] = "terminal"
            candidate["body"] = {
                "body_kind": "terminal-ready",
                "terminal_outcome": "failed",
                "result_object_descriptor_id": None,
                "failure_object_descriptor_id": failure_id,
            }
        elif alias == "event_running":
            candidate["lifecycle_state"] = "running"
        else:
            candidate["lifecycle_state"] = "starting"
        events.append(seal_job_record(candidate))
    seal = seal_job_record(
        {
            **_job_header("job-terminal-seal"),
            "job_id": submission.to_dict()["job_id"],
            "job_submission_id": submission.id,
            "context_ref_id": submission.to_dict()["context_ref_id"],
            "input_binding_id": submission.to_dict()["input_binding_id"],
            "terminal_event_id": events[-1].id,
            "event_head_id": events[-1].id,
            "event_count": len(events),
            "event_ledger_root": event_ledger_root(submission.to_dict()["job_id"], [item.id for item in events]),
            "attempt_record_ids": [attempt.id],
            "session_record_ids": [session.id],
            "process_identity_record_ids": [process.id],
            "terminal_outcome": "failed",
            "mutation_state": "not-started",
            "cancellation_request_ids": [],
            "cancellation_observed": False,
            "result_object_descriptor_id": None,
            "failure_object_descriptor_id": failure_id,
            "resource_claim_accounting": [],
            "recovery_obligations": [],
            "diagnostics": [],
            "limitations": [],
            "sealed_by_actor_id": submission.to_dict()["submitted_by_actor_id"],
            "sealed_at": "2026-08-08T16:01:00Z",
            "evidence_eligible": False,
        }
    )
    return [submission, attempt, session, process, *events, seal]


def test_schema_family_and_complete_publication_are_executable() -> None:
    publication = build_synthetic_job_publication()
    assert registered_job_kinds() == (
        "job-submission",
        "job-attempt",
        "runtime-session",
        "process-identity",
        "job-event",
        "job-terminal-seal",
    )
    validated = validate_job_publication(publication.records, relations=publication.relations)
    assert [item.id for item in validated] == [item.id for item in publication.records]
    assert len(validated) == 20


def test_two_clean_builds_are_byte_identical() -> None:
    first = build_synthetic_job_publication()
    second = build_synthetic_job_publication()
    assert first.context_ref.canonical_bytes == second.context_ref.canonical_bytes
    assert first.input_binding.canonical_bytes == second.input_binding.canonical_bytes
    assert [item.canonical_bytes for item in first.records] == [item.canonical_bytes for item in second.records]


def test_record_round_trip_and_caller_mutation_are_isolated() -> None:
    publication = build_synthetic_job_publication()
    progress = next(item for item in publication.records if item.id == publication.aliases["event_progress_post"])
    loaded = load_job_record(progress.canonical_bytes)
    assert loaded == progress
    mutable = progress.to_dict()
    mutable["body"]["message"] = "caller mutation"
    assert progress.to_dict()["body"]["message"] != "caller mutation"
    assert validate_job_record(progress.to_dict()) == progress
    candidate = publication.record("event_progress_post")
    candidate.pop("id")
    sealed = seal_job_record(candidate)
    candidate["body"]["message"] = "mutated after sealing"
    assert sealed.to_dict()["body"]["message"] != "mutated after sealing"


def test_event_ledger_root_is_exact_and_domain_separated() -> None:
    publication = build_synthetic_job_publication()
    events = [item.to_dict() for item in publication.records if item.kind == "job-event"]
    events.sort(key=lambda item: item["event_ordinal"])
    seal = publication.record("terminal_seal")
    assert seal["event_ledger_root"] == event_ledger_root(seal["job_id"], [item["id"] for item in events])
    assert seal["event_ledger_root"].startswith("job-event-ledger-root-v2:sha256:")


def test_active_prefix_is_valid_but_terminal_ready_requires_seal() -> None:
    publication = build_synthetic_job_publication()
    active = [
        item for item in publication.records
        if item.id not in {publication.aliases["event_terminal"], publication.aliases["terminal_seal"]}
    ]
    validate_job_publication(active, relations=_permissive_relations(publication))
    terminal_without_seal = [item for item in publication.records if item.id != publication.aliases["terminal_seal"]]
    _expect_error(
        lambda: validate_job_publication(terminal_without_seal, relations=_permissive_relations(publication)),
        "relation",
        "relation.missing-terminal-seal",
    )


def test_external_reference_port_is_required_and_bound_to_exact_projection() -> None:
    publication = build_synthetic_job_publication()
    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.external-validator-required",
    )
    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=lambda expectation: False,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.external-reference-rejected",
    )
    known = next(
        expectation
        for record in publication.records
        for expectation in __import__("workbench_crucible_jobs").external_reference_expectations(record.to_dict())
    )
    assert publication.relations.external_reference_validator(known) is True
    assert publication.relations.external_reference_validator(
        replace(known, containing_record_id="job-submission:sha256:" + "f" * 64)
    ) is False


def test_external_reference_validator_cannot_mutate_its_isolated_expectation() -> None:
    publication = build_synthetic_job_publication()
    original_records = tuple(item.canonical_bytes for item in publication.records)

    def mutating_validator(expectation):
        object.__setattr__(expectation, "record_id", "capability:sha256:" + "f" * 64)
        return True

    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=mutating_validator,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.external-validator-mutation",
    )
    assert tuple(item.canonical_bytes for item in publication.records) == original_records
    validate_job_publication(publication.records, relations=_permissive_relations(publication))


def test_lane_a_context_publication_port_is_mandatory_and_fail_closed() -> None:
    publication = build_synthetic_job_publication()
    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=lambda expectation: True,
            ),
        ),
        "relation",
        "relation.context-publication-validator-required",
    )
    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=lambda expectation: True,
                context_publication_validator=lambda request: False,
            ),
        ),
        "relation",
        "relation.context-publication-rejected",
    )


def test_context_publication_validator_cannot_mutate_its_isolated_request() -> None:
    publication = build_synthetic_job_publication()
    original_context = publication.context_ref.canonical_bytes
    original_binding = publication.input_binding.canonical_bytes

    def mutating_validator(request):
        object.__setattr__(request, "context_ref_id", "context-ref:sha256:" + "f" * 64)
        return True

    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=lambda expectation: True,
                context_publication_validator=mutating_validator,
            ),
        ),
        "relation",
        "relation.context-publication-validator-mutation",
    )
    assert publication.context_ref.canonical_bytes == original_context
    assert publication.input_binding.canonical_bytes == original_binding
    validate_job_publication(publication.records, relations=_permissive_relations(publication))


def test_resolver_and_external_validator_exceptions_are_stable_diagnostics() -> None:
    publication = build_synthetic_job_publication()

    def broken_resolver(record_id):
        raise RuntimeError("offline registry failed")

    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=broken_resolver,
                external_reference_validator=lambda expectation: True,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.resolver-exception",
    )

    def broken_validator(expectation):
        raise RuntimeError("owner adapter failed")

    _expect_error(
        lambda: validate_job_publication(
            publication.records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=broken_validator,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.external-validator-exception",
    )


def test_seal_rejects_preexisting_id_before_hashing() -> None:
    publication = build_synthetic_job_publication()
    candidate = publication.record("submission")
    _expect_error(lambda: seal_job_record(candidate), "domain", "domain.preexisting-id")


def test_hostile_public_validated_record_metadata_is_rejected_before_comparison() -> None:
    publication = build_synthetic_job_publication()

    class Hostile:
        def __eq__(self, other):
            raise AssertionError("hostile equality must not run")

        def __ne__(self, other):
            raise AssertionError("hostile inequality must not run")

    original = publication.records[0]
    forged = ValidatedJobRecord(Hostile(), original.kind, original.canonical_bytes)
    _expect_error(
        lambda: validate_job_publication(
            [forged, *publication.records[1:]],
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.record-snapshot",
    )
    forged_bytes = ValidatedJobRecord(original.id, original.kind, Hostile())
    _expect_error(
        lambda: validate_job_publication(
            [forged_bytes, *publication.records[1:]],
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.record-snapshot",
    )


def test_semantic_content_id_literal_remains_plain_text() -> None:
    publication = build_synthetic_job_publication()
    post = publication.record("event_progress_post")
    assert "policy:sha256:" in post["body"]["message"]
    seal = publication.record("terminal_seal")
    assert "policy:sha256:" in seal["limitations"][0]["detail"]
    observed = []

    def validator(expectation):
        observed.append(expectation.record_id)
        return True

    validate_job_publication(
        publication.records,
        relations=JobRelationResolvers(
            record_resolver=publication.resolver,
            external_reference_validator=validator,
            context_publication_validator=lambda request: True,
        ),
    )
    assert not any(item == "policy:sha256:" + "a" * 64 for item in observed)


def test_terminal_outcome_mutation_and_result_failure_matrix() -> None:
    publication = build_synthetic_job_publication()
    base = publication.record("terminal_seal")
    base.pop("id")
    valid = [
        ("succeeded", "reference-committed", "result"),
        ("failed", "protected-mutation-partial", "failure"),
        ("cancelled-before-mutation", "not-started", "failure"),
        ("cancelled-after-mutation", "protected-mutation-started", "failure"),
        ("indeterminate", "external-mutation-indeterminate", "failure"),
    ]
    for outcome, mutation, descriptor_side in valid:
        candidate = json.loads(json.dumps(base))
        candidate["terminal_outcome"] = outcome
        candidate["mutation_state"] = mutation
        candidate["cancellation_observed"] = outcome.startswith("cancelled-")
        candidate["result_object_descriptor_id"] = "object-descriptor:sha256:" + "1" * 64 if descriptor_side == "result" else None
        candidate["failure_object_descriptor_id"] = "object-descriptor:sha256:" + "2" * 64 if descriptor_side == "failure" else None
        seal_job_record(candidate)
    invalid = json.loads(json.dumps(base))
    invalid["terminal_outcome"] = "cancelled-before-mutation"
    invalid["mutation_state"] = "reference-committed"
    _expect_error(lambda: seal_job_record(invalid), "schema", "schema.enum")


def test_progress_partition_treats_indeterminate_as_boundary_crossed() -> None:
    publication = build_synthetic_job_publication()
    candidate = publication.record("event_progress_post")
    candidate.pop("id")
    candidate["mutation_state"] = "external-mutation-indeterminate"
    candidate["body"]["mutation_started"] = True
    seal_job_record(candidate)
    candidate["body"]["mutation_started"] = False
    _expect_error(lambda: seal_job_record(candidate), "semantic", "semantic.progress-mutation")


def test_context_and_input_binding_must_agree_exactly() -> None:
    publication = build_synthetic_job_publication()
    other_binding = seal_input_binding(
        {
            "kind": "input-binding",
            "format": "workbench-crucible-input-binding-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-input-binding-v2.schema.json",
            "canonicalizer": "workbench-canonical-json-v2",
            "context_ref_id": "context-ref:sha256:" + "f" * 64,
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
    submission = publication.record("submission")
    submission.pop("id")
    submission["input_binding_id"] = other_binding.id
    altered = seal_job_record(submission)

    def resolver(record_id):
        if record_id == other_binding.id:
            return other_binding.canonical_bytes
        return publication.resolver(record_id)

    _expect_error(
        lambda: validate_job_publication(
            [altered],
            relations=JobRelationResolvers(
                record_resolver=resolver,
                external_reference_validator=lambda expectation: True,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.input-context",
    )


def test_typed_authority_roles_reject_unrelated_content_ids() -> None:
    publication = build_synthetic_job_publication()
    candidate = publication.record("submission")
    candidate.pop("id")
    candidate["plan_record_id"] = "evidence-record:sha256:" + "f" * 64
    _expect_error(lambda: seal_job_record(candidate), "schema", "schema.oneOf")


def test_attempt_anchor_relations_are_exact() -> None:
    cases = [
        ("attempt", "handler_implementation_id", "implementation:sha256:" + "f" * 64, "relation.attempt-implementation"),
        ("attempt", "attempt_reason", "manual-retry", "relation.initial-attempt"),
        ("attempt", "session_id", "session-v2:" + "f" * 32, "relation.attempt-session"),
    ]
    for alias, field, value, code in cases:
        publication = build_synthetic_job_publication()
        records, _ = _replacement(publication, alias, lambda item, field=field, value=value: item.__setitem__(field, value))
        _expect_error(
            lambda records=records: validate_job_publication(records, relations=_permissive_relations(publication)),
            "relation",
            code,
        )


def test_two_attempt_retry_chain_is_complete_and_exact() -> None:
    publication = build_synthetic_job_publication()
    records = _build_two_attempt_publication(publication)
    validated = validate_job_publication(records, relations=_permissive_relations(publication))
    attempts = [item.to_dict() for item in validated if item.kind == "job-attempt"]
    assert [item["attempt_ordinal"] for item in attempts] == [0, 1]
    assert attempts[1]["previous_attempt_record_id"] == attempts[0]["id"]


def test_two_attempt_retry_predecessor_ordinal_and_decision_fail_closed() -> None:
    publication = build_synthetic_job_publication()
    cases = (
        (
            {"predecessor_id": "job-attempt:sha256:" + "f" * 64},
            "relation.attempt-predecessor",
        ),
        ({"next_ordinal": 2}, "relation.attempt-ordinal"),
        (
            {
                "attempt_retry_decision": "retry-decision:sha256:" + "4" * 64,
                "event_retry_decision": "retry-decision:sha256:" + "5" * 64,
            },
            "relation.retry-next-attempt",
        ),
        ({"retry_after_second_running": True}, "relation.retry-before-attempt"),
        ({"old_attempt_runs_after_second_start": True}, "relation.inactive-attempt-event"),
    )
    for options, code in cases:
        records = _build_two_attempt_publication(publication, **options)
        _expect_error(
            lambda records=records: validate_job_publication(records, relations=_permissive_relations(publication)),
            "relation",
            code,
        )


def test_retry_authorized_attempt_may_be_cancelled_before_it_starts() -> None:
    publication = build_synthetic_job_publication()
    records = _build_two_attempt_publication(
        publication,
        cancel_before_second_start=True,
    )
    validated = validate_job_publication(
        records,
        relations=_permissive_relations(publication),
    )
    starts = [
        item.to_dict()["attempt_record_id"]
        for item in validated
        if item.kind == "job-event"
        and item.to_dict()["event_type"] == "attempt-starting"
    ]
    attempts = [item for item in validated if item.kind == "job-attempt"]
    assert len(attempts) == 2
    assert len(starts) == 1

    non_cancelled = _build_two_attempt_publication(
        publication,
        cancel_before_second_start=True,
        unstarted_terminal_outcome="failed",
    )
    _expect_error(
        lambda: validate_job_publication(
            non_cancelled,
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.seal-unstarted-attempt",
    )


def test_orphan_cannot_reach_terminal_before_exact_recovery_classification() -> None:
    publication = build_synthetic_job_publication()
    records = _build_two_attempt_publication(publication, orphan_before_terminal=True)
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.recovery-required",
    )


def test_recovery_classification_cannot_change_orphan_owner_tuple() -> None:
    publication = build_synthetic_job_publication()
    records = _build_two_attempt_publication(
        publication,
        orphan_before_terminal=True,
        recover_after_orphan=True,
        recovery_uses_first_attempt=True,
    )
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.recovery-target",
    )


def test_exited_process_cannot_be_reclassified_as_still_live() -> None:
    publication = build_synthetic_job_publication()

    def claim_still_live(item) -> None:
        item["body"].update(
            {
                "classification": "exact-process-still-live",
                "live_process_revalidated": True,
                "manifest_object_descriptor_id": None,
                "required_action": "resume",
            }
        )

    records, _ = _replacement(publication, "event_recovery", claim_still_live)
    _expect_error(
        lambda: validate_job_publication(
            records,
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.recovery-exited-process",
    )


def test_cross_job_retry_lineage_preserves_exact_request_and_idempotency() -> None:
    publication = build_synthetic_job_publication()
    retry = _retry_submission(publication)
    validate_job_publication(
        [*publication.records, retry],
        relations=_permissive_relations(publication),
    )
    for override in (
        {"request_object_descriptor_id": "object-descriptor:sha256:" + "b" * 64},
        {"idempotency_key_digest": "b" * 64},
        {"idempotency_scope": "workspace.different-action"},
    ):
        mismatched = _retry_submission(publication, **override)
        _expect_error(
            lambda mismatched=mismatched: validate_job_publication(
                [*publication.records, mismatched],
                relations=_permissive_relations(publication),
            ),
            "relation",
            "relation.retry-lineage",
        )

    missing = _retry_submission(
        publication,
        retry_of_terminal_seal_id="job-terminal-seal:sha256:" + "f" * 64,
    )
    _expect_error(
        lambda: validate_job_publication([missing], relations=_permissive_relations(publication)),
        "relation",
        "relation.missing-record",
    )


def test_cross_job_retry_cannot_change_context_or_input_binding() -> None:
    publication = build_synthetic_job_publication()
    context_candidate = publication.context_ref.to_dict()
    context_candidate.pop("id")
    context_candidate["store_id"] = "store:sha256:" + "c" * 64
    other_context = seal_context_ref(context_candidate)
    binding_candidate = publication.input_binding.to_dict()
    binding_candidate.pop("id")
    binding_candidate["context_ref_id"] = other_context.id
    other_binding = seal_input_binding(binding_candidate)
    retry = _retry_submission(
        publication,
        context_ref_id=other_context.id,
        input_binding_id=other_binding.id,
    )

    def resolver(record_id):
        if record_id == other_context.id:
            return other_context.canonical_bytes
        if record_id == other_binding.id:
            return other_binding.canonical_bytes
        return publication.resolver(record_id)

    _expect_error(
        lambda: validate_job_publication(
            [*publication.records, retry],
            relations=JobRelationResolvers(
                record_resolver=resolver,
                external_reference_validator=lambda expectation: True,
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.retry-lineage",
    )


def test_external_applicability_receives_exact_submission_bytes_and_rejects_replay() -> None:
    publication = build_synthetic_job_publication()
    original = publication.record("submission")
    expectations = __import__("workbench_crucible_jobs").external_reference_expectations(original)
    capability = next(item for item in expectations if item.path == "/capability_id")
    assert capability.containing_record_bytes == canonical_json_bytes(original)

    mutations = (
        lambda item: item.__setitem__("mutation_boundary", "protected-state"),
        lambda item: item.__setitem__("handler_id", "handler:sha256:" + "d" * 64),
        lambda item: item["requested_resource_claims"][0].__setitem__("quantity", 2),
    )
    for mutate in mutations:
        candidate = json.loads(json.dumps(original))
        candidate.pop("id")
        mutate(candidate)
        replay = seal_job_record(candidate)
        _expect_error(
            lambda replay=replay: validate_job_publication([replay], relations=publication.relations),
            "relation",
            "relation.external-reference-rejected",
        )


def test_process_adoption_requires_exact_reconciliation_projection() -> None:
    publication = build_synthetic_job_publication()
    candidate = publication.record("process")
    candidate.pop("id")
    candidate["binding_method"] = "adopted-after-exact-reconciliation"
    candidate["launch_observation_id"] = None
    candidate["adoption_reconciliation_id"] = "process-adoption-reconciliation:sha256:" + "e" * 64
    adopted = seal_job_record(candidate)
    records = [
        item
        for item in publication.records
        if item.kind in {"job-submission", "job-attempt", "runtime-session"}
    ] + [adopted]
    observed = []

    def exact_validator(expectation):
        if expectation.expected_kind != "process-adoption-reconciliation":
            return True
        record = json.loads(expectation.containing_record_bytes)
        observed.append(expectation)
        return (
            record["id"] == adopted.id
            and record["binding_method"] == "adopted-after-exact-reconciliation"
            and record["launch_observation_id"] is None
            and record["adoption_reconciliation_id"] == expectation.record_id
            and record["process_start_token"] == candidate["process_start_token"]
        )

    relations = JobRelationResolvers(
        record_resolver=publication.resolver,
        external_reference_validator=exact_validator,
        context_publication_validator=lambda request: True,
    )
    validate_job_publication(records, relations=relations)
    assert len(observed) == 1
    _expect_error(
        lambda: validate_job_publication(
            records,
            relations=JobRelationResolvers(
                record_resolver=publication.resolver,
                external_reference_validator=lambda expectation: expectation.expected_kind != "process-adoption-reconciliation",
                context_publication_validator=lambda request: True,
            ),
        ),
        "relation",
        "relation.external-reference-rejected",
    )


def test_session_and_process_operational_id_reuse_is_rejected() -> None:
    publication = build_synthetic_job_publication()
    session = publication.record("session")
    session.pop("id")
    session["parent_session_record_id"] = publication.aliases["session"]
    duplicate_session = seal_job_record(session)
    _expect_error(
        lambda: validate_job_publication(
            [*publication.records, duplicate_session],
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.duplicate-operational-id",
    )
    process = publication.record("process")
    process.pop("id")
    process["process_instance_id"] = "process-v2:" + "f" * 32
    duplicate_tuple = seal_job_record(process)
    _expect_error(
        lambda: validate_job_publication(
            [*publication.records, duplicate_tuple],
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.duplicate-process-tuple",
    )


def test_duplicate_cancellation_observation_and_claim_release_are_rejected() -> None:
    publication = build_synthetic_job_publication()
    observed = publication.record("event_cancel_observed")
    request_id = observed["body"]["cancellation_request_id"]
    request_event_id = observed["body"]["request_event_id"]

    def another_observation(item):
        item["event_type"] = "cancellation-observed"
        item["body"] = {
            "body_kind": "cancellation-observed",
            "cancellation_request_id": request_id,
            "request_event_id": request_event_id,
            "observation_point": "duplicate.observer",
            "disposition": "stopping-after-mutation",
        }

    records, _ = _replacement(publication, "event_claim_released", another_observation)
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.cancellation-observation-duplicate",
    )
    released = publication.record("event_claim_released")["body"]

    def another_release(item):
        item["event_type"] = "resource-claim-released"
        item["body"] = dict(released)

    records, _ = _replacement(publication, "event_process_exited", another_release)
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.claim-release-duplicate",
    )


def test_cancellation_disposition_and_recovery_obligations_are_exact() -> None:
    publication = build_synthetic_job_publication()
    records, _ = _replacement(
        publication,
        "event_cancel_observed",
        lambda item: item["body"].__setitem__("disposition", "stopping-before-mutation"),
    )
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.cancellation-disposition",
    )
    records, _ = _replacement(
        publication,
        "terminal_seal",
        lambda item: item.__setitem__("recovery_obligations", []),
    )
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.recovery-obligations",
    )


def test_queued_cancellation_can_terminally_seal_with_zero_attempts() -> None:
    publication = build_synthetic_job_publication()
    submission = next(item for item in publication.records if item.kind == "job-submission")
    anchor = submission.to_dict()
    actor = anchor["submitted_by_actor_id"]
    events = []

    def append_event(event_type, lifecycle, body):
        candidate = {
            "kind": "job-event",
            "format": "workbench-crucible-job-event-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-job-event-v2.schema.json",
            "canonicalizer": "workbench-canonical-json-v2",
            "job_id": anchor["job_id"],
            "job_submission_id": submission.id,
            "context_ref_id": anchor["context_ref_id"],
            "input_binding_id": anchor["input_binding_id"],
            "event_ordinal": len(events),
            "previous_event_id": None if not events else events[-1].id,
            "event_type": event_type,
            "lifecycle_state": lifecycle,
            "mutation_state": "not-started",
            "attempt_record_id": None,
            "attempt_id": None,
            "session_record_id": None,
            "session_id": None,
            "process_identity_record_id": None,
            "process_instance_id": None,
            "occurred_at": f"2026-08-08T13:00:0{len(events)}Z",
            "actor_id": actor,
            "evidence_eligible": False,
            "body": body,
        }
        events.append(seal_job_record(candidate))

    append_event("queued", "queued", {"body_kind": "queued", "queue_policy_id": "policy:sha256:" + "1" * 64, "priority": 0, "not_before": None})
    cancellation_id = "cancellation-v2:" + "1" * 32
    append_event("cancellation-requested", "queued", {"body_kind": "cancellation-requested", "cancellation_request_id": cancellation_id, "expected_event_id": events[0].id, "expected_event_ordinal": 0, "reason": "cancel before dispatch", "requested_by_actor_id": actor})
    append_event("cancellation-observed", "queued", {"body_kind": "cancellation-observed", "cancellation_request_id": cancellation_id, "request_event_id": events[1].id, "observation_point": "queue.cas", "disposition": "stopping-before-mutation"})
    failure = "object-descriptor:sha256:" + "3" * 64
    append_event("terminal-ready", "terminal", {"body_kind": "terminal-ready", "terminal_outcome": "cancelled-before-mutation", "result_object_descriptor_id": None, "failure_object_descriptor_id": failure})
    seal = seal_job_record(
        {
            "kind": "job-terminal-seal",
            "format": "workbench-crucible-job-terminal-seal-v2",
            "schema_version": 2,
            "schema_id": "workbench://schemas/crucible/crucible-job-terminal-seal-v2.schema.json",
            "canonicalizer": "workbench-canonical-json-v2",
            "job_id": anchor["job_id"],
            "job_submission_id": submission.id,
            "context_ref_id": anchor["context_ref_id"],
            "input_binding_id": anchor["input_binding_id"],
            "terminal_event_id": events[-1].id,
            "event_head_id": events[-1].id,
            "event_count": len(events),
            "event_ledger_root": event_ledger_root(anchor["job_id"], [item.id for item in events]),
            "attempt_record_ids": [],
            "session_record_ids": [],
            "process_identity_record_ids": [],
            "terminal_outcome": "cancelled-before-mutation",
            "mutation_state": "not-started",
            "cancellation_request_ids": [cancellation_id],
            "cancellation_observed": True,
            "result_object_descriptor_id": None,
            "failure_object_descriptor_id": failure,
            "resource_claim_accounting": [],
            "recovery_obligations": [],
            "diagnostics": [],
            "limitations": [],
            "sealed_by_actor_id": actor,
            "sealed_at": "2026-08-08T13:01:00Z",
            "evidence_eligible": False,
        }
    )
    validate_job_publication(
        [submission, *events, seal],
        relations=_permissive_relations(publication),
    )


def test_complete_seal_requires_all_reachable_owners_and_ancestors_local() -> None:
    publication = build_synthetic_job_publication()
    omitted_process = [
        item for item in publication.records
        if item.id != publication.aliases["process"]
    ]
    _expect_error(
        lambda: validate_job_publication(omitted_process, relations=_permissive_relations(publication)),
        "relation",
        "relation.incomplete-seal-closure",
    )

    child_session_candidate = publication.record("session")
    child_session_candidate.pop("id")
    child_session_candidate["parent_session_record_id"] = publication.aliases["session"]
    child_session = seal_job_record(child_session_candidate)
    omitted_parent = [
        child_session if item.id == publication.aliases["session"] else item
        for item in publication.records
    ]
    _expect_error(
        lambda: validate_job_publication(omitted_parent, relations=_permissive_relations(publication)),
        "relation",
        "relation.incomplete-seal-closure",
    )

    child_process_candidate = publication.record("process")
    child_process_candidate.pop("id")
    child_process_candidate["supervisor_process_identity_id"] = publication.aliases["process"]
    child_process = seal_job_record(child_process_candidate)
    omitted_supervisor = [
        child_process if item.id == publication.aliases["process"] else item
        for item in publication.records
    ]
    _expect_error(
        lambda: validate_job_publication(omitted_supervisor, relations=_permissive_relations(publication)),
        "relation",
        "relation.incomplete-seal-closure",
    )


def test_parent_ancestry_has_a_finite_validation_budget() -> None:
    publication = build_synthetic_job_publication()
    submission = next(item for item in publication.records if item.kind == "job-submission")
    attempt = next(item for item in publication.records if item.kind == "job-attempt")
    original_session = next(item for item in publication.records if item.kind == "runtime-session")
    sessions = [original_session]
    parent_id = original_session.id
    template = original_session.to_dict()
    for ordinal in range(65):
        candidate = json.loads(json.dumps(template))
        candidate.pop("id")
        candidate["session_id"] = f"session-v2:{ordinal + 1:032x}"
        candidate["parent_session_record_id"] = parent_id
        child = seal_job_record(candidate)
        sessions.append(child)
        parent_id = child.id
    _expect_error(
        lambda: validate_job_publication(
            [submission, attempt, *sessions],
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.ancestry-budget-exceeded",
    )


def test_three_hop_session_ancestry_cannot_cross_job_anchor() -> None:
    publication = build_synthetic_job_publication()
    submission = next(item for item in publication.records if item.kind == "job-submission")
    attempt = next(item for item in publication.records if item.kind == "job-attempt")
    template = publication.record("session")

    hostile_candidate = dict(template)
    hostile_candidate.pop("id")
    hostile_candidate.update(
        {
            "session_id": "session-v2:" + "d" * 32,
            "job_id": "job-v2:" + "d" * 32,
            "parent_session_record_id": None,
        }
    )
    hostile = seal_job_record(hostile_candidate)

    ancestors = []
    parent_id = hostile.id
    for marker in ("c", "b"):
        candidate = dict(template)
        candidate.pop("id")
        candidate["session_id"] = "session-v2:" + marker * 32
        candidate["parent_session_record_id"] = parent_id
        ancestor = seal_job_record(candidate)
        ancestors.append(ancestor)
        parent_id = ancestor.id

    leaf_candidate = dict(template)
    leaf_candidate.pop("id")
    leaf_candidate["parent_session_record_id"] = parent_id
    leaf = seal_job_record(leaf_candidate)

    _expect_error(
        lambda: validate_job_publication(
            [submission, attempt, leaf],
            relations=_permissive_relations(publication, *ancestors, hostile),
        ),
        "relation",
        "relation.anchor-mismatch",
    )


def test_three_hop_supervisor_ancestry_cannot_cross_job_anchor() -> None:
    publication = build_synthetic_job_publication()
    submission = next(item for item in publication.records if item.kind == "job-submission")
    attempt = next(item for item in publication.records if item.kind == "job-attempt")
    session = next(item for item in publication.records if item.kind == "runtime-session")
    template = publication.record("process")

    hostile_candidate = dict(template)
    hostile_candidate.pop("id")
    hostile_candidate.update(
        {
            "process_instance_id": "process-v2:" + "d" * 32,
            "job_id": "job-v2:" + "d" * 32,
            "supervisor_process_identity_id": None,
        }
    )
    hostile = seal_job_record(hostile_candidate)

    supervisors = []
    supervisor_id = hostile.id
    for marker in ("c", "b"):
        candidate = dict(template)
        candidate.pop("id")
        candidate["process_instance_id"] = "process-v2:" + marker * 32
        candidate["supervisor_process_identity_id"] = supervisor_id
        supervisor = seal_job_record(candidate)
        supervisors.append(supervisor)
        supervisor_id = supervisor.id

    leaf_candidate = dict(template)
    leaf_candidate.pop("id")
    leaf_candidate["supervisor_process_identity_id"] = supervisor_id
    leaf = seal_job_record(leaf_candidate)

    _expect_error(
        lambda: validate_job_publication(
            [submission, attempt, session, leaf],
            relations=_permissive_relations(publication, *supervisors, hostile),
        ),
        "relation",
        "relation.anchor-mismatch",
    )


def test_event_body_owner_custody_is_mechanically_coupled() -> None:
    publication = build_synthetic_job_publication()
    cases = [
        (
            "event_starting",
            lambda item: item["body"].__setitem__("attempt_reason", "manual-retry"),
            "relation.attempt-starting-reason",
        ),
        (
            "event_process_bound",
            lambda item: item["body"].__setitem__("binding_method", "adopted-after-exact-reconciliation"),
            "relation.process-binding-method",
        ),
        (
            "event_claim_acquired",
            lambda item: item["body"].__setitem__("quantity", 2),
            "relation.claim-request-mismatch",
        ),
    ]
    for alias, mutation, code in cases:
        records, _ = _replacement(publication, alias, mutation)
        _expect_error(
            lambda records=records: validate_job_publication(records, relations=_permissive_relations(publication)),
            "relation",
            code,
        )


def test_process_binding_precedes_exit_and_exit_is_unique() -> None:
    publication = build_synthetic_job_publication()
    for options, code in (
        ({"exit_before_bind": True}, "relation.process-before-bind"),
        ({"duplicate_exit": True}, "relation.process-exit-duplicate"),
        ({"running_after_exit": True}, "relation.process-after-exit"),
    ):
        records = _build_process_order_publication(publication, **options)
        _expect_error(
            lambda records=records: validate_job_publication(records, relations=_permissive_relations(publication)),
            "relation",
            code,
        )


def test_resource_acquisition_is_within_exact_attempt_claim_scope() -> None:
    publication = build_synthetic_job_publication()
    records = _build_two_attempt_publication(
        publication,
        first_resource_claim_keys=[],
        include_claim_events=True,
    )
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.attempt-claim-scope",
    )


def test_exact_os_process_tuple_is_unique_across_jobs() -> None:
    publication = build_synthetic_job_publication()
    first = {
        item.kind: item
        for item in publication.records
        if item.kind in {"job-submission", "job-attempt", "runtime-session", "process-identity"}
    }
    second_submission_candidate = first["job-submission"].to_dict()
    second_submission_candidate.pop("id")
    second_submission_candidate["job_id"] = "job-v2:" + "b" * 32
    second_submission_candidate["idempotency_key_digest"] = "c" * 64
    second_submission_candidate["submitted_at"] = "2026-08-08T17:00:00Z"
    second_submission = seal_job_record(second_submission_candidate)
    second_attempt_candidate = first["job-attempt"].to_dict()
    second_attempt_candidate.pop("id")
    second_attempt_candidate.update(
        {
            "job_id": second_submission_candidate["job_id"],
            "job_submission_id": second_submission.id,
            "attempt_id": "attempt-v2:" + "b" * 32,
            "session_id": "session-v2:" + "b" * 32,
            "created_at": "2026-08-08T17:00:01Z",
        }
    )
    second_attempt = seal_job_record(second_attempt_candidate)
    second_session_candidate = first["runtime-session"].to_dict()
    second_session_candidate.pop("id")
    second_session_candidate.update(
        {
            "job_id": second_submission_candidate["job_id"],
            "job_submission_id": second_submission.id,
            "attempt_record_id": second_attempt.id,
            "attempt_id": second_attempt_candidate["attempt_id"],
            "session_id": second_attempt_candidate["session_id"],
            "opened_at": "2026-08-08T17:00:02Z",
        }
    )
    second_session = seal_job_record(second_session_candidate)
    second_process_candidate = first["process-identity"].to_dict()
    second_process_candidate.pop("id")
    second_process_candidate.update(
        {
            "job_id": second_submission_candidate["job_id"],
            "job_submission_id": second_submission.id,
            "attempt_record_id": second_attempt.id,
            "attempt_id": second_attempt_candidate["attempt_id"],
            "session_record_id": second_session.id,
            "session_id": second_session_candidate["session_id"],
            "process_instance_id": "process-v2:" + "b" * 32,
            "bound_at": "2026-08-08T17:00:03Z",
        }
    )
    second_process = seal_job_record(second_process_candidate)
    _expect_error(
        lambda: validate_job_publication(
            [*first.values(), second_submission, second_attempt, second_session, second_process],
            relations=_permissive_relations(publication),
        ),
        "relation",
        "relation.duplicate-process-tuple",
    )


def test_nonphase_events_cannot_advance_lifecycle_or_mutation() -> None:
    publication = build_synthetic_job_publication()
    records, _ = _replacement(
        publication,
        "event_progress_pre",
        lambda item: item.__setitem__("lifecycle_state", "recovering"),
    )
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.lifecycle-preservation",
    )
    records, _ = _replacement(
        publication,
        "event_claim_acquired",
        lambda item: item.__setitem__("mutation_state", "immutable-output-published"),
    )
    _expect_error(
        lambda: validate_job_publication(records, relations=_permissive_relations(publication)),
        "relation",
        "relation.mutation-preservation",
    )


def test_submission_mutation_boundary_rejects_another_branch() -> None:
    publication = build_synthetic_job_publication()
    injected, _ = _replacement(
        publication,
        "event_mutation",
        lambda item: (
            item.__setitem__("mutation_state", "protected-mutation-started"),
            item["body"].__setitem__("to_state", "protected-mutation-started"),
        ),
    )
    _expect_error(
        lambda: validate_job_publication(injected, relations=_permissive_relations(publication)),
        "relation",
        "relation.mutation-boundary",
    )


def test_no_c02_operational_record_can_be_presented_as_an_evidence_admission_candidate() -> None:
    publication = build_synthetic_job_publication()
    representatives = {
        item.kind: item.id
        for item in publication.records
    }
    assert set(representatives) == set(registered_job_kinds())
    for candidate_id in representatives.values():
        _expect_error(
            lambda candidate_id=candidate_id: validate_evidence_admission_candidate(candidate_id),
            "relation",
            "relation.evidence-ineligible",
        )
    _expect_error(
        lambda: validate_job_publication(
            [*publication.records, {"kind": "admission-record", "candidate_record_id": publication.aliases["event_progress_post"]}],
            relations=_permissive_relations(publication),
        ),
        "schema",
        "schema.unknown-kind",
    )


def _apply_case(publication, case):
    mutation = case["mutation"]
    alias = case["alias"]
    relations = _permissive_relations(publication)
    if mutation == "terminal-without-seal":
        records = [item for item in publication.records if item.id != publication.aliases["terminal_seal"]]
        return lambda: validate_job_publication(records, relations=relations)
    if mutation == "progress-admission":
        return lambda: validate_evidence_admission_candidate(publication.aliases[alias])
    if mutation == "external-validator-rejects":
        rejected = JobRelationResolvers(
            record_resolver=publication.resolver,
            external_reference_validator=lambda expectation: False,
            context_publication_validator=lambda request: True,
        )
        return lambda: validate_job_publication(publication.records, relations=rejected)
    if mutation == "second-terminal-seal":
        records, replacement = _replacement(
            publication,
            alias,
            lambda item: item.__setitem__("sealed_at", "2026-08-08T12:01:01Z"),
        )
        original = next(item for item in publication.records if item.id == publication.aliases[alias])
        records.append(original)
        return lambda: validate_job_publication(records, relations=relations)

    def mutate(item):
        if mutation == "unknown-top-field":
            item["unexpected"] = True
        elif mutation == "mutable-status-field":
            item["current_status"] = "running"
        elif mutation == "lease-owner-field":
            item["body"]["lease_owner"] = "worker.mutable"
        elif mutation == "event-evidence-eligible":
            item["evidence_eligible"] = True
        elif mutation == "event-ordinal-gap":
            item["event_ordinal"] = 2
        elif mutation == "event-context-mismatch":
            item["context_ref_id"] = "context-ref:sha256:" + "f" * 64
        elif mutation == "process-attempt-mismatch":
            item["attempt_id"] = "attempt-v2:" + "f" * 32
        elif mutation == "wrong-event-root":
            item["event_ledger_root"] = "job-event-ledger-root-v2:sha256:" + "f" * 64
        elif mutation == "progress-regression":
            item["body"]["completed"] = 0
        elif mutation == "cancellation-cas-replay":
            item["body"]["expected_event_id"] = publication.aliases["event_queued"]
            item["body"]["expected_event_ordinal"] = 0
        elif mutation == "cancellation-observation-replay":
            item["body"]["cancellation_request_id"] = "cancellation-v2:" + "f" * 32
        elif mutation == "recovery-state-lie":
            item["mutation_state"] = "not-started"
        else:
            raise AssertionError(mutation)

    if mutation in {"unknown-top-field", "mutable-status-field", "lease-owner-field", "event-evidence-eligible", "recovery-state-lie"}:
        candidate = publication.record(alias)
        candidate.pop("id")
        mutate(candidate)
        return lambda: seal_job_record(candidate)
    records, _ = _replacement(publication, alias, mutate)
    return lambda: validate_job_publication(records, relations=relations)


def test_retained_malicious_cases_fail_at_the_exact_boundary() -> None:
    publication = build_synthetic_job_publication()
    fixture = json.loads((FIXTURE_DIRECTORY / "malicious-cases.json").read_text(encoding="utf-8"))
    assert fixture["format"] == "workbench-crucible-job-v2-malicious-cases-v1"
    for case in fixture["cases"]:
        _expect_error(
            _apply_case(publication, case),
            case["expected_phase"],
            case["expected_code"],
        )


def test_expected_publication_identity_oracle() -> None:
    publication = build_synthetic_job_publication()
    oracle = json.loads((FIXTURE_DIRECTORY / "expected-publication.json").read_text(encoding="utf-8"))
    actual = {
        "format": "workbench-crucible-job-v2-expected-publication-v1",
        "context_ref_id": publication.context_ref.id,
        "input_binding_id": publication.input_binding.id,
        "ordered_record_ids": [item.id for item in publication.records],
        "aliases": dict(sorted(publication.aliases.items())),
    }
    assert actual == oracle


class CrucibleJobV2ContractsTest(unittest.TestCase):
    test_schema_family_and_complete_publication_are_executable = staticmethod(test_schema_family_and_complete_publication_are_executable)
    test_two_clean_builds_are_byte_identical = staticmethod(test_two_clean_builds_are_byte_identical)
    test_record_round_trip_and_caller_mutation_are_isolated = staticmethod(test_record_round_trip_and_caller_mutation_are_isolated)
    test_event_ledger_root_is_exact_and_domain_separated = staticmethod(test_event_ledger_root_is_exact_and_domain_separated)
    test_active_prefix_is_valid_but_terminal_ready_requires_seal = staticmethod(test_active_prefix_is_valid_but_terminal_ready_requires_seal)
    test_external_reference_port_is_required_and_bound_to_exact_projection = staticmethod(test_external_reference_port_is_required_and_bound_to_exact_projection)
    test_external_reference_validator_cannot_mutate_its_isolated_expectation = staticmethod(test_external_reference_validator_cannot_mutate_its_isolated_expectation)
    test_lane_a_context_publication_port_is_mandatory_and_fail_closed = staticmethod(test_lane_a_context_publication_port_is_mandatory_and_fail_closed)
    test_context_publication_validator_cannot_mutate_its_isolated_request = staticmethod(test_context_publication_validator_cannot_mutate_its_isolated_request)
    test_resolver_and_external_validator_exceptions_are_stable_diagnostics = staticmethod(test_resolver_and_external_validator_exceptions_are_stable_diagnostics)
    test_seal_rejects_preexisting_id_before_hashing = staticmethod(test_seal_rejects_preexisting_id_before_hashing)
    test_hostile_public_validated_record_metadata_is_rejected_before_comparison = staticmethod(test_hostile_public_validated_record_metadata_is_rejected_before_comparison)
    test_semantic_content_id_literal_remains_plain_text = staticmethod(test_semantic_content_id_literal_remains_plain_text)
    test_terminal_outcome_mutation_and_result_failure_matrix = staticmethod(test_terminal_outcome_mutation_and_result_failure_matrix)
    test_progress_partition_treats_indeterminate_as_boundary_crossed = staticmethod(test_progress_partition_treats_indeterminate_as_boundary_crossed)
    test_context_and_input_binding_must_agree_exactly = staticmethod(test_context_and_input_binding_must_agree_exactly)
    test_typed_authority_roles_reject_unrelated_content_ids = staticmethod(test_typed_authority_roles_reject_unrelated_content_ids)
    test_attempt_anchor_relations_are_exact = staticmethod(test_attempt_anchor_relations_are_exact)
    test_two_attempt_retry_chain_is_complete_and_exact = staticmethod(test_two_attempt_retry_chain_is_complete_and_exact)
    test_two_attempt_retry_predecessor_ordinal_and_decision_fail_closed = staticmethod(test_two_attempt_retry_predecessor_ordinal_and_decision_fail_closed)
    test_retry_authorized_attempt_may_be_cancelled_before_it_starts = staticmethod(test_retry_authorized_attempt_may_be_cancelled_before_it_starts)
    test_orphan_cannot_reach_terminal_before_exact_recovery_classification = staticmethod(test_orphan_cannot_reach_terminal_before_exact_recovery_classification)
    test_recovery_classification_cannot_change_orphan_owner_tuple = staticmethod(test_recovery_classification_cannot_change_orphan_owner_tuple)
    test_exited_process_cannot_be_reclassified_as_still_live = staticmethod(test_exited_process_cannot_be_reclassified_as_still_live)
    test_cross_job_retry_lineage_preserves_exact_request_and_idempotency = staticmethod(test_cross_job_retry_lineage_preserves_exact_request_and_idempotency)
    test_cross_job_retry_cannot_change_context_or_input_binding = staticmethod(test_cross_job_retry_cannot_change_context_or_input_binding)
    test_external_applicability_receives_exact_submission_bytes_and_rejects_replay = staticmethod(test_external_applicability_receives_exact_submission_bytes_and_rejects_replay)
    test_process_adoption_requires_exact_reconciliation_projection = staticmethod(test_process_adoption_requires_exact_reconciliation_projection)
    test_session_and_process_operational_id_reuse_is_rejected = staticmethod(test_session_and_process_operational_id_reuse_is_rejected)
    test_duplicate_cancellation_observation_and_claim_release_are_rejected = staticmethod(test_duplicate_cancellation_observation_and_claim_release_are_rejected)
    test_cancellation_disposition_and_recovery_obligations_are_exact = staticmethod(test_cancellation_disposition_and_recovery_obligations_are_exact)
    test_queued_cancellation_can_terminally_seal_with_zero_attempts = staticmethod(test_queued_cancellation_can_terminally_seal_with_zero_attempts)
    test_complete_seal_requires_all_reachable_owners_and_ancestors_local = staticmethod(test_complete_seal_requires_all_reachable_owners_and_ancestors_local)
    test_parent_ancestry_has_a_finite_validation_budget = staticmethod(test_parent_ancestry_has_a_finite_validation_budget)
    test_three_hop_session_ancestry_cannot_cross_job_anchor = staticmethod(test_three_hop_session_ancestry_cannot_cross_job_anchor)
    test_three_hop_supervisor_ancestry_cannot_cross_job_anchor = staticmethod(test_three_hop_supervisor_ancestry_cannot_cross_job_anchor)
    test_event_body_owner_custody_is_mechanically_coupled = staticmethod(test_event_body_owner_custody_is_mechanically_coupled)
    test_process_binding_precedes_exit_and_exit_is_unique = staticmethod(test_process_binding_precedes_exit_and_exit_is_unique)
    test_resource_acquisition_is_within_exact_attempt_claim_scope = staticmethod(test_resource_acquisition_is_within_exact_attempt_claim_scope)
    test_exact_os_process_tuple_is_unique_across_jobs = staticmethod(test_exact_os_process_tuple_is_unique_across_jobs)
    test_nonphase_events_cannot_advance_lifecycle_or_mutation = staticmethod(test_nonphase_events_cannot_advance_lifecycle_or_mutation)
    test_submission_mutation_boundary_rejects_another_branch = staticmethod(test_submission_mutation_boundary_rejects_another_branch)
    test_no_c02_operational_record_can_be_presented_as_an_evidence_admission_candidate = staticmethod(test_no_c02_operational_record_can_be_presented_as_an_evidence_admission_candidate)
    test_retained_malicious_cases_fail_at_the_exact_boundary = staticmethod(test_retained_malicious_cases_fail_at_the_exact_boundary)
    test_expected_publication_identity_oracle = staticmethod(test_expected_publication_identity_oracle)
