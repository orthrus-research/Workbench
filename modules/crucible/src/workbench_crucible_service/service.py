"""Durable embedded Workbench service V3 mechanics.

Crucible owns handler dispatch, one-writer scheduling, exact context custody,
append-only C02 jobs, cancellation, recovery, subscriptions, and bounded query
leases.  Shell owns transport hosting and authentication presentation; owner
handlers retain all semantic and policy authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Any

from workbench_crucible_context import (
    ValidatedContextRecord,
    load_context_ref,
    load_input_binding,
)
from workbench_crucible_jobs import (
    JobContextValidationRequest,
    JobRelationResolvers,
    ValidatedJobRecord,
    event_ledger_root,
    load_job_record,
    seal_job_record,
    validate_job_publication,
)

from workbench_api.service import (DurableJobHandle, JobSubscriptionPage, ServiceCancelled, ServiceExecutionContext, ServiceHandlerRegistration, ServicePhysicalLeasePorts, ServiceV3Error, _require, _validate_handler_value)

from workbench_api.host_filesystem import fsync_directory, secure_private_path
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id, parse_canonical_json


_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_MUTATION_BOUNDARIES = {
    "none",
    "immutable-publication",
    "reference-update",
    "protected-state",
    "external-side-effect",
}
_MUTATION_STATES = {
    "not-started",
    "temporary-residue",
    "immutable-output-published",
    "reference-committed",
    "protected-mutation-started",
    "protected-mutation-partial",
    "protected-mutation-completed",
    "external-mutation-started",
    "external-mutation-partial",
    "external-mutation-completed",
    "external-mutation-indeterminate",
}
_MUTATION_TRANSITIONS = {
    "not-started": {
        "not-started",
        "temporary-residue",
        "immutable-output-published",
        "protected-mutation-started",
        "external-mutation-started",
        "external-mutation-indeterminate",
    },
    "temporary-residue": {
        "temporary-residue",
        "not-started",
        "immutable-output-published",
        "protected-mutation-started",
        "external-mutation-started",
        "external-mutation-indeterminate",
    },
    "immutable-output-published": {
        "immutable-output-published",
        "reference-committed",
        "external-mutation-indeterminate",
    },
    "reference-committed": {
        "reference-committed",
        "external-mutation-indeterminate",
    },
    "protected-mutation-started": {
        "protected-mutation-started",
        "protected-mutation-partial",
        "protected-mutation-completed",
        "external-mutation-indeterminate",
    },
    "protected-mutation-partial": {
        "protected-mutation-partial",
        "protected-mutation-completed",
        "external-mutation-indeterminate",
    },
    "protected-mutation-completed": {
        "protected-mutation-completed",
        "external-mutation-indeterminate",
    },
    "external-mutation-started": {
        "external-mutation-started",
        "external-mutation-partial",
        "external-mutation-completed",
        "external-mutation-indeterminate",
    },
    "external-mutation-partial": {
        "external-mutation-partial",
        "external-mutation-completed",
        "external-mutation-indeterminate",
    },
    "external-mutation-completed": {
        "external-mutation-completed",
        "external-mutation-indeterminate",
    },
    "external-mutation-indeterminate": {"external-mutation-indeterminate"},
}
_MUTATION_BOUNDARY_STATES = {
    "none": {"not-started", "temporary-residue"},
    "immutable-publication": {
        "not-started",
        "temporary-residue",
        "immutable-output-published",
    },
    "reference-update": {
        "not-started",
        "temporary-residue",
        "immutable-output-published",
        "reference-committed",
    },
    "protected-state": {
        "not-started",
        "temporary-residue",
        "protected-mutation-started",
        "protected-mutation-partial",
        "protected-mutation-completed",
    },
    "external-side-effect": {
        "not-started",
        "temporary-residue",
        "external-mutation-started",
        "external-mutation-partial",
        "external-mutation-completed",
        "external-mutation-indeterminate",
    },
}
_TERMINAL_OUTCOMES = {
    "succeeded",
    "failed",
    "cancelled-before-mutation",
    "cancelled-after-mutation",
    "indeterminate",
}








def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _operational(prefix: str) -> str:
    return f"{prefix}-v2:{secrets.token_hex(16)}"


def _header(kind: str) -> dict[str, Any]:
    return {
        "canonicalizer": "workbench-canonical-json-v2",
        "format": f"workbench-crucible-{kind}-v2",
        "kind": kind,
        "schema_id": (
            f"workbench://schemas/crucible/crucible-{kind}-v2.schema.json"
        ),
        "schema_version": 2,
    }


def _descriptor_id(raw: bytes, role: str) -> str:
    return content_id(
        "object-descriptor",
        {
            "byte_length": len(raw),
            "role": role,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    )










@dataclass(frozen=True, slots=True)
class JobCancellationPlan:
    """Content-addressed intent for one exact durable-job cancellation."""

    id: str
    context_ref_id: str
    input_binding_id: str
    job_id: str
    expected_event_id: str
    expected_event_ordinal: int
    reason: str
    canonical_bytes: bytes


def seal_job_cancellation_plan(
    *,
    context_ref_id: str,
    input_binding_id: str,
    job_id: str,
    expected_event_id: str,
    expected_event_ordinal: int,
    reason: str,
) -> JobCancellationPlan:
    """Seal one closed cancellation plan without granting owner authority."""

    _require(
        type(context_ref_id) is str
        and context_ref_id.startswith("context-ref:sha256:")
        and _CONTENT_ID.fullmatch(context_ref_id) is not None
        and type(input_binding_id) is str
        and input_binding_id.startswith("input-binding:sha256:")
        and _CONTENT_ID.fullmatch(input_binding_id) is not None
        and type(job_id) is str
        and re.fullmatch(r"job-v2:[0-9a-f]{32}", job_id) is not None
        and type(expected_event_id) is str
        and expected_event_id.startswith("job-event:sha256:")
        and _CONTENT_ID.fullmatch(expected_event_id) is not None
        and type(expected_event_ordinal) is int
        and expected_event_ordinal >= 0
        and type(reason) is str
        and 1 <= len(reason.encode("utf-8")) <= 1024,
        "invalid-request",
        "job cancellation plan inputs are invalid",
    )
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "context_ref_id": context_ref_id,
        "expected_event_id": expected_event_id,
        "expected_event_ordinal": expected_event_ordinal,
        "format": "workbench-crucible-job-cancellation-plan-v1",
        "input_binding_id": input_binding_id,
        "job_id": job_id,
        "kind": "operation-plan",
        "limitations": [
            "cancellation-is-observed-only-at-safe-boundaries",
            "terminal-outcome-remains-authoritative",
        ],
        "next_safe_action": (
            "Submit this exact plan through the authenticated job/cancel "
            "capability, then reopen the immutable terminal seal."
        ),
        "operation_class": "disposable-runtime-execute",
        "reason": reason,
        "schema_version": 1,
    }
    plan_id = content_id("operation-plan", body)
    raw = canonical_json_bytes({**body, "id": plan_id})
    return JobCancellationPlan(
        plan_id,
        context_ref_id,
        input_binding_id,
        job_id,
        expected_event_id,
        expected_event_ordinal,
        reason,
        raw,
    )


def load_job_cancellation_plan(raw: bytes) -> JobCancellationPlan:
    """Reopen and identity-check one exact cancellation plan."""

    _require(
        type(raw) is bytes and bool(raw),
        "invalid-request",
        "job cancellation plan bytes are absent",
    )
    value = parse_canonical_json(raw)
    expected_fields = {
        "canonicalizer",
        "context_ref_id",
        "expected_event_id",
        "expected_event_ordinal",
        "format",
        "id",
        "input_binding_id",
        "job_id",
        "kind",
        "limitations",
        "next_safe_action",
        "operation_class",
        "reason",
        "schema_version",
    }
    _require(
        type(value) is dict
        and set(value) == expected_fields
        and value["canonicalizer"] == CANONICALIZER_ID
        and value["format"] == "workbench-crucible-job-cancellation-plan-v1"
        and value["kind"] == "operation-plan"
        and value["operation_class"] == "disposable-runtime-execute"
        and value["schema_version"] == 1
        and raw == canonical_json_bytes(value),
        "invalid-request",
        "job cancellation plan is open, noncanonical, or has the wrong contract",
    )
    expected = seal_job_cancellation_plan(
        context_ref_id=value["context_ref_id"],
        input_binding_id=value["input_binding_id"],
        job_id=value["job_id"],
        expected_event_id=value["expected_event_id"],
        expected_event_ordinal=value["expected_event_ordinal"],
        reason=value["reason"],
    )
    _require(
        value["id"] == expected.id and raw == expected.canonical_bytes,
        "invalid-request",
        "job cancellation plan identity or fixed guidance differs",
    )
    return expected


def validate_job_cancellation_arguments(value: Mapping[str, Any]) -> bool:
    """Validate the normalized internal arguments for ``job/cancel``."""

    return (
        type(value) is dict
        and set(value)
        == {
            "expected_event_id",
            "expected_event_ordinal",
            "idempotency_key",
            "job_id",
            "plan_id",
            "reason",
        }
        and type(value["expected_event_id"]) is str
        and value["expected_event_id"].startswith("job-event:sha256:")
        and _CONTENT_ID.fullmatch(value["expected_event_id"]) is not None
        and type(value["expected_event_ordinal"]) is int
        and value["expected_event_ordinal"] >= 0
        and type(value["idempotency_key"]) is str
        and re.fullmatch(r"idempotency-v3:[0-9a-f]{32}", value["idempotency_key"])
        is not None
        and type(value["job_id"]) is str
        and re.fullmatch(r"job-v2:[0-9a-f]{32}", value["job_id"]) is not None
        and type(value["plan_id"]) is str
        and value["plan_id"].startswith("operation-plan:sha256:")
        and _CONTENT_ID.fullmatch(value["plan_id"]) is not None
        and type(value["reason"]) is str
        and 1 <= len(value["reason"].encode("utf-8")) <= 1024
    )


def validate_job_handle_result(value: Any) -> bool:
    """Validate the closed protocol V3 job-handle projection."""

    if type(value) is not dict or set(value) != {
        "job_id",
        "job_submission_id",
        "latest_event_id",
        "latest_event_ordinal",
        "lifecycle_state",
        "mutation_state",
        "terminal_seal_id",
    }:
        return False
    return (
        type(value["job_id"]) is str
        and re.fullmatch(r"job-v2:[0-9a-f]{32}", value["job_id"]) is not None
        and type(value["job_submission_id"]) is str
        and value["job_submission_id"].startswith("job-submission:sha256:")
        and _CONTENT_ID.fullmatch(value["job_submission_id"]) is not None
        and type(value["latest_event_id"]) is str
        and value["latest_event_id"].startswith("job-event:sha256:")
        and _CONTENT_ID.fullmatch(value["latest_event_id"]) is not None
        and type(value["latest_event_ordinal"]) is int
        and value["latest_event_ordinal"] >= 0
        and value["lifecycle_state"]
        in {
            "queued",
            "starting",
            "running",
            "recovering",
            "retrying",
            "orphaned",
            "terminal",
        }
        and value["mutation_state"] in _MUTATION_STATES
        and (
            value["terminal_seal_id"] is None
            or (
                type(value["terminal_seal_id"]) is str
                and value["terminal_seal_id"].startswith(
                    "job-terminal-seal:sha256:"
                )
                and _CONTENT_ID.fullmatch(value["terminal_seal_id"])
                is not None
            )
        )
        and (
            (value["lifecycle_state"] == "terminal")
            == (value["terminal_seal_id"] is not None)
        )
    )


def validate_job_subscription_arguments(value: Mapping[str, Any]) -> bool:
    """Validate normalized ``job/subscribe`` arguments."""

    if type(value) is not dict or set(value) != {
        "delivery",
        "event_families",
        "job_id",
        "job_submission_id",
        "position",
    }:
        return False
    position = value["position"]
    if type(position) is not dict:
        return False
    if position.get("kind") == "start":
        valid_position = set(position) == {"kind"}
    elif position.get("kind") == "resume":
        valid_position = (
            set(position)
            == {
                "kind",
                "last_cursor",
                "stream_generation",
                "subscription_id",
            }
            and type(position["last_cursor"]) is int
            and position["last_cursor"] >= 0
            and type(position["subscription_id"]) is str
            and re.fullmatch(
                r"subscription-v3:[0-9a-f]{32}",
                position["subscription_id"],
            )
            is not None
            and type(position["stream_generation"]) is str
            and re.fullmatch(
                r"stream-generation-v3:[0-9a-f]{32}",
                position["stream_generation"],
            )
            is not None
        )
    else:
        valid_position = False
    return (
        valid_position
        and value["delivery"] == "lossless"
        and value["event_families"] == ["job"]
        and type(value["job_id"]) is str
        and re.fullmatch(r"job-v2:[0-9a-f]{32}", value["job_id"])
        is not None
        and type(value["job_submission_id"]) is str
        and value["job_submission_id"].startswith("job-submission:sha256:")
        and _CONTENT_ID.fullmatch(value["job_submission_id"]) is not None
    )


def validate_job_subscription_result(value: Any) -> bool:
    """Validate the exact protocol V3 job subscription handle."""

    if type(value) is not dict or set(value) != {
        "context_ref_id",
        "delivery",
        "event_families",
        "minimum_available_cursor",
        "next_cursor",
        "retained_until",
        "scope",
        "stream_generation",
        "subscription_id",
    }:
        return False
    scope = value["scope"]
    if type(scope) is not dict or set(scope) != {
        "job_id",
        "job_submission_id",
        "kind",
    }:
        return False
    return (
        type(value["subscription_id"]) is str
        and re.fullmatch(
            r"subscription-v3:[0-9a-f]{32}",
            value["subscription_id"],
        )
        is not None
        and type(value["stream_generation"]) is str
        and re.fullmatch(
            r"stream-generation-v3:[0-9a-f]{32}",
            value["stream_generation"],
        )
        is not None
        and type(value["context_ref_id"]) is str
        and value["context_ref_id"].startswith("context-ref:sha256:")
        and _CONTENT_ID.fullmatch(value["context_ref_id"]) is not None
        and value["delivery"] == "lossless"
        and value["event_families"] == ["job"]
        and type(value["next_cursor"]) is int
        and value["next_cursor"] >= 0
        and value["minimum_available_cursor"] == 0
        and value["retained_until"] is None
        and scope["kind"] == "job"
        and type(scope["job_id"]) is str
        and re.fullmatch(r"job-v2:[0-9a-f]{32}", scope["job_id"])
        is not None
        and type(scope["job_submission_id"]) is str
        and scope["job_submission_id"].startswith("job-submission:sha256:")
        and _CONTENT_ID.fullmatch(scope["job_submission_id"]) is not None
    )


def _job_subscription_ids(
    context_ref_id: str,
    input_binding_id: str,
    handle: DurableJobHandle,
) -> tuple[str, str]:
    identity_body = {
        "context_ref_id": context_ref_id,
        "delivery": "lossless",
        "event_families": ["job"],
        "input_binding_id": input_binding_id,
        "job_id": handle.job_id,
        "job_submission_id": handle.job_submission_id,
    }
    subscription_id = "subscription-v3:" + hashlib.sha256(
        canonical_json_bytes(identity_body)
    ).hexdigest()[:32]
    stream_generation = "stream-generation-v3:" + hashlib.sha256(
        canonical_json_bytes(
            {
                "job_id": handle.job_id,
                "job_submission_id": handle.job_submission_id,
            }
        )
    ).hexdigest()[:32]
    return subscription_id, stream_generation


def validate_empty_service_arguments(value: Mapping[str, Any]) -> bool:
    """Accept the one closed, context-free discovery argument value."""

    return type(value) is dict and not value


def validate_service_capabilities_result(value: Any) -> bool:
    """Validate the exact registry/capability discovery projection."""

    return (
        type(value) is dict
        and set(value) == {"capability_ids", "registry_id"}
        and type(value["registry_id"]) is str
        and value["registry_id"].startswith(
            "component-capability-registry:sha256:"
        )
        and _CONTENT_ID.fullmatch(value["registry_id"]) is not None
        and type(value["capability_ids"]) is list
        and bool(value["capability_ids"])
        and value["capability_ids"]
        == sorted(value["capability_ids"], key=lambda item: item.encode("utf-8"))
        and len(value["capability_ids"]) == len(set(value["capability_ids"]))
        and all(
            type(item) is str
            and item.startswith("capability:sha256:")
            and _CONTENT_ID.fullmatch(item) is not None
            for item in value["capability_ids"]
        )
    )


def validate_context_list_result(value: Any) -> bool:
    """Validate one exact, ordered list of registered context/input pairs."""

    if type(value) is not dict or set(value) != {"contexts"}:
        return False
    contexts = value["contexts"]
    if type(contexts) is not list:
        return False
    logical = []
    for row in contexts:
        if (
            type(row) is not dict
            or set(row) != {"context_ref_id", "input_binding_id"}
            or type(row["context_ref_id"]) is not str
            or not row["context_ref_id"].startswith("context-ref:sha256:")
            or _CONTENT_ID.fullmatch(row["context_ref_id"]) is None
            or type(row["input_binding_id"]) is not str
            or not row["input_binding_id"].startswith("input-binding:sha256:")
            or _CONTENT_ID.fullmatch(row["input_binding_id"]) is None
        ):
            return False
        logical.append((row["context_ref_id"], row["input_binding_id"]))
    return logical == sorted(logical) and len(logical) == len(set(logical))


class ServiceCapabilitiesHandler:
    """Return the exact registry identity and its declared capability set."""

    def __init__(self, registry_id: str, capability_ids: tuple[str, ...]) -> None:
        _require(
            type(capability_ids) is tuple,
            "service.invalid-handler",
            "service capability discovery IDs must be an exact tuple",
        )
        value = {
            "capability_ids": sorted(capability_ids, key=lambda item: item.encode()),
            "registry_id": registry_id,
        }
        _require(
            validate_service_capabilities_result(value),
            "service.invalid-handler",
            "service capability discovery identity is invalid",
        )
        self._result = parse_canonical_json(canonical_json_bytes(value))

    def __call__(
        self,
        _context: "ServiceExecutionContext",
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            validate_empty_service_arguments(arguments),
            "invalid-request",
            "service capability discovery arguments are invalid or open",
        )
        return parse_canonical_json(canonical_json_bytes(self._result))


class ContextListHandler:
    """List only exact context/input pairs reopened from durable storage."""

    def __call__(
        self,
        context: "ServiceExecutionContext",
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            validate_empty_service_arguments(arguments),
            "invalid-request",
            "context discovery arguments are invalid or open",
        )
        result = {
            "contexts": [
                {
                    "context_ref_id": context_ref_id,
                    "input_binding_id": input_binding_id,
                }
                for context_ref_id, input_binding_id in context.list_contexts()
            ]
        }
        _require(
            validate_context_list_result(result),
            "service.invalid-handler-result",
            "context discovery produced an invalid projection",
        )
        return result


class JobSubscriptionHandler:
    """Establish or resume custody for one immutable durable event stream."""

    def __call__(
        self,
        context: "ServiceExecutionContext",
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            validate_job_subscription_arguments(arguments),
            "invalid-request",
            "job subscription arguments are invalid or open",
        )
        target_context_id, target_input_id = context.job_binding(
            arguments["job_id"]
        )
        handle = context.durable_job_handle(arguments["job_id"])
        _require(
            (target_context_id, target_input_id)
            == (context.context_ref_id, context.input_binding_id)
            and handle.job_submission_id == arguments["job_submission_id"],
            "stale-context",
            "job subscription target differs from the exact context or submission",
        )
        subscription_id, stream_generation = _job_subscription_ids(
            target_context_id, target_input_id, handle
        )
        position = arguments["position"]
        if position["kind"] == "start":
            next_cursor = 0
        else:
            _require(
                position["subscription_id"] == subscription_id
                and position["stream_generation"] == stream_generation,
                "subscription-gap",
                "job subscription resume identity differs",
            )
            next_cursor = position["last_cursor"] + 1
        result = {
            "context_ref_id": target_context_id,
            "delivery": "lossless",
            "event_families": ["job"],
            "minimum_available_cursor": 0,
            "next_cursor": next_cursor,
            "retained_until": None,
            "scope": {
                "job_id": handle.job_id,
                "job_submission_id": handle.job_submission_id,
                "kind": "job",
            },
            "stream_generation": stream_generation,
            "subscription_id": subscription_id,
        }
        _require(
            validate_job_subscription_result(result),
            "service.invalid-handler-result",
            "job subscription produced an invalid protocol handle",
        )
        return result


def validate_job_event_page_arguments(value: Mapping[str, Any]) -> bool:
    """Validate one bounded pull over an established subscription."""

    return (
        type(value) is dict
        and set(value)
        == {
            "job_id",
            "job_submission_id",
            "last_cursor",
            "maximum_events",
            "stream_generation",
            "subscription_id",
        }
        and type(value["job_id"]) is str
        and re.fullmatch(r"job-v2:[0-9a-f]{32}", value["job_id"])
        is not None
        and type(value["job_submission_id"]) is str
        and value["job_submission_id"].startswith("job-submission:sha256:")
        and _CONTENT_ID.fullmatch(value["job_submission_id"]) is not None
        and type(value["last_cursor"]) is int
        and value["last_cursor"] >= -1
        and type(value["maximum_events"]) is int
        and 1 <= value["maximum_events"] <= 1024
        and type(value["subscription_id"]) is str
        and re.fullmatch(
            r"subscription-v3:[0-9a-f]{32}", value["subscription_id"]
        )
        is not None
        and type(value["stream_generation"]) is str
        and re.fullmatch(
            r"stream-generation-v3:[0-9a-f]{32}",
            value["stream_generation"],
        )
        is not None
    )


def validate_job_event_page_result(value: Any) -> bool:
    """Validate a bounded lossless job-event page and its resume cursor."""

    if type(value) is not dict or set(value) != {
        "events",
        "format",
        "has_more",
        "minimum_available_cursor",
        "next_cursor",
        "schema_version",
        "stream_generation",
        "subscription_id",
    }:
        return False
    events = value["events"]
    return (
        value["format"] == "workbench-crucible-job-event-page-v1"
        and value["schema_version"] == 1
        and type(value["has_more"]) is bool
        and value["minimum_available_cursor"] == 0
        and type(value["next_cursor"]) is int
        and value["next_cursor"] >= 0
        and type(value["subscription_id"]) is str
        and re.fullmatch(
            r"subscription-v3:[0-9a-f]{32}", value["subscription_id"]
        )
        is not None
        and type(value["stream_generation"]) is str
        and re.fullmatch(
            r"stream-generation-v3:[0-9a-f]{32}",
            value["stream_generation"],
        )
        is not None
        and type(events) is list
        and all(
            type(event) is dict
            and set(event)
            == {
                "event_id",
                "event_ordinal",
                "event_type",
                "lifecycle_state",
                "mutation_state",
            }
            and type(event["event_id"]) is str
            and event["event_id"].startswith("job-event:sha256:")
            and _CONTENT_ID.fullmatch(event["event_id"]) is not None
            and type(event["event_ordinal"]) is int
            and event["event_ordinal"] >= 0
            and type(event["event_type"]) is str
            and bool(event["event_type"])
            and event["lifecycle_state"]
            in {
                "queued",
                "starting",
                "running",
                "recovering",
                "retrying",
                "orphaned",
                "terminal",
            }
            and event["mutation_state"] in _MUTATION_STATES
            for event in events
        )
    )


class JobEventPageHandler:
    """Read a bounded event page through an exact subscription identity."""

    def __call__(
        self,
        context: "ServiceExecutionContext",
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            validate_job_event_page_arguments(arguments),
            "invalid-request",
            "job event page arguments are invalid or open",
        )
        target_context_id, target_input_id = context.job_binding(
            arguments["job_id"]
        )
        handle = context.durable_job_handle(arguments["job_id"])
        subscription_id, stream_generation = _job_subscription_ids(
            target_context_id, target_input_id, handle
        )
        _require(
            (target_context_id, target_input_id)
            == (context.context_ref_id, context.input_binding_id)
            and handle.job_submission_id == arguments["job_submission_id"]
            and subscription_id == arguments["subscription_id"]
            and stream_generation == arguments["stream_generation"],
            "subscription-gap",
            "job event page differs from its exact subscription",
        )
        page = context.subscribe_job(
            handle.job_id,
            after_ordinal=arguments["last_cursor"],
            maximum_events=arguments["maximum_events"],
        )
        result = {
            "events": [
                {
                    "event_id": event["id"],
                    "event_ordinal": event["event_ordinal"],
                    "event_type": event["event_type"],
                    "lifecycle_state": event["lifecycle_state"],
                    "mutation_state": event["mutation_state"],
                }
                for event in page.events
            ],
            "format": "workbench-crucible-job-event-page-v1",
            "has_more": page.has_more,
            "minimum_available_cursor": page.minimum_available_ordinal,
            "next_cursor": page.next_ordinal + 1,
            "schema_version": 1,
            "stream_generation": stream_generation,
            "subscription_id": subscription_id,
        }
        _require(
            validate_job_event_page_result(result),
            "service.invalid-handler-result",
            "job event page result is invalid",
        )
        return result


JobCancellationPlanResolver = Callable[[str], bytes]


class JobCancellationHandler:
    """Target-record-locked, proof-bound implementation of ``job/cancel``."""

    def __init__(
        self,
        plan_resolver: JobCancellationPlanResolver | None = None,
    ) -> None:
        _require(
            plan_resolver is None or callable(plan_resolver),
            "service.invalid-handler",
            "job cancellation plan resolver is invalid",
        )
        self.plan_resolver = plan_resolver

    def __call__(
        self,
        context: "ServiceExecutionContext",
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            validate_job_cancellation_arguments(arguments),
            "invalid-request",
            "job cancellation arguments are invalid or open",
        )
        _require(
            context.context_ref_id is not None
            and context.input_binding_id is not None,
            "unknown-context",
            "job cancellation has no exact context/input binding",
        )
        plan = (
            seal_job_cancellation_plan(
                context_ref_id=context.context_ref_id,
                input_binding_id=context.input_binding_id,
                job_id=arguments["job_id"],
                expected_event_id=arguments["expected_event_id"],
                expected_event_ordinal=arguments["expected_event_ordinal"],
                reason=arguments["reason"],
            )
            if self.plan_resolver is None
            else load_job_cancellation_plan(
                self.plan_resolver(arguments["plan_id"])
            )
        )
        _require(
            plan.id == arguments["plan_id"]
            and plan.context_ref_id == context.context_ref_id
            and plan.input_binding_id == context.input_binding_id
            and plan.job_id == arguments["job_id"]
            and plan.expected_event_id == arguments["expected_event_id"]
            and plan.expected_event_ordinal
            == arguments["expected_event_ordinal"]
            and plan.reason == arguments["reason"],
            "stale-plan",
            "job cancellation request differs from its exact reviewed plan",
        )
        target_context_id, target_input_id = context.job_binding(plan.job_id)
        _require(
            (target_context_id, target_input_id)
            == (plan.context_ref_id, plan.input_binding_id),
            "stale-context",
            "job cancellation target belongs to another exact context",
        )
        result = context.cancel_job(
            plan.job_id,
            expected_event_id=plan.expected_event_id,
            expected_event_ordinal=plan.expected_event_ordinal,
            reason=plan.reason,
            idempotency_key=arguments["idempotency_key"],
        )
        result.pop("terminal_outcome")
        _require(
            validate_job_handle_result(result),
            "service.invalid-handler-result",
            "job cancellation produced an invalid public handle",
        )
        return result




ContextPublicationValidator = Callable[
    [ValidatedContextRecord, ValidatedContextRecord], bool
]


class DurableJobStore:
    """Append-only C02 job custody with atomic operational heads."""

    def __init__(
        self,
        root: Path,
        *,
        context_publication_validator: ContextPublicationValidator,
        physical_leases: ServicePhysicalLeasePorts,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        _require(
            isinstance(root, Path) and root.is_absolute(),
            "service.invalid-root",
            "durable service root must be an absolute Path",
        )
        _require(
            callable(context_publication_validator),
            "service.context-validator-unavailable",
            "context publication validator is required",
        )
        _require(
            type(physical_leases) is ServicePhysicalLeasePorts,
            "service.invalid-physical-lease-provider",
            "durable service custody requires an explicit Host Adapter lease provider",
        )
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        _require(
            root.is_dir() and not root.is_symlink(),
            "service.invalid-root",
            "durable service root must be a real directory",
        )
        secure_private_path(root, directory=True)
        self.root = root
        self.jobs = root / "jobs"
        self.contexts = root / "contexts"
        self.idempotency = root / "idempotency"
        self.locks = root / "locks"
        for directory in (
            self.jobs,
            self.contexts,
            self.idempotency,
            self.locks,
        ):
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            _require(
                directory.is_dir() and not directory.is_symlink(),
                "service.invalid-root",
                "durable service directory must not be a symbolic link",
            )
            secure_private_path(directory, directory=True)
        self._context_validator = context_publication_validator
        self._physical_leases = physical_leases
        _require(
            fault_injector is None or callable(fault_injector),
            "service.invalid-fault-injector",
            "service fault injector must be callable when supplied",
        )
        self._fault_injector = fault_injector
        self._thread_lock = threading.RLock()

    def _fault(self, checkpoint: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(checkpoint)

    @staticmethod
    def _write_immutable(path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            _require(
                path.read_bytes() == raw,
                "service.immutable-collision",
                f"immutable service object differs at {path}",
            )
            return
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                _require(
                    path.read_bytes() == raw,
                    "service.immutable-collision",
                    f"immutable service object raced at {path}",
                )
            fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace(path: Path, raw: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _file_lock(self, key: str):
        path = self.locks / f"{hashlib.sha256(key.encode()).hexdigest()}.lock"
        try:
            lease = self._physical_leases.exclusive(path)
            enter = lease.__enter__
            leave = lease.__exit__
            enter()
        except ServiceV3Error:
            raise
        except Exception as exc:
            raise ServiceV3Error(
                "service.physical-lease-failed",
                "Host Adapter record lease failed closed",
            ) from exc
        try:
            yield
        finally:
            try:
                leave(None, None, None)
            except Exception as exc:
                raise ServiceV3Error(
                    "service.physical-lease-failed",
                    "Host Adapter record lease release failed closed",
                ) from exc

    def _job_root(self, job_id: str) -> Path:
        _require(
            type(job_id) is str
            and re.fullmatch(r"job-v2:[0-9a-f]{32}", job_id) is not None,
            "service.invalid-job-id",
            "job ID is invalid",
        )
        portable = self.jobs / job_id.removeprefix("job-v2:")
        return self._existing_storage_root(portable, self.jobs / job_id)

    @staticmethod
    def _existing_storage_root(portable: Path, legacy: Path) -> Path:
        # Colon-bearing legacy names can only exist on the older POSIX layout.
        # New stores use portable keys while logical IDs and record bytes stay exact.
        if os.name != "nt" and legacy.exists():
            _require(
                not portable.exists(),
                "service.storage-ambiguous",
                "both legacy and portable storage exist for the same identity",
            )
            return legacy
        return portable

    @staticmethod
    def _stored_job_id(name: str) -> str:
        return f"job-v2:{name}" if re.fullmatch(r"[0-9a-f]{32}", name) else name

    def _context_root(self, context_ref_id: str, input_binding_id: str) -> Path:
        _require(
            type(context_ref_id) is str
            and context_ref_id.startswith("context-ref:sha256:")
            and _CONTENT_ID.fullmatch(context_ref_id) is not None
            and type(input_binding_id) is str
            and input_binding_id.startswith("input-binding:sha256:")
            and _CONTENT_ID.fullmatch(input_binding_id) is not None,
            "unknown-context",
            "context/input identity is invalid",
        )
        key = hashlib.sha256(
            canonical_json_bytes([context_ref_id, input_binding_id])
        ).hexdigest()
        return self._existing_storage_root(
            self.contexts / key, self.contexts / context_ref_id / input_binding_id
        )

    def register_context(
        self, context_ref_bytes: bytes, input_binding_bytes: bytes
    ) -> tuple[str, str]:
        try:
            context = load_context_ref(context_ref_bytes)
            binding = load_input_binding(input_binding_bytes)
        except Exception as exc:
            raise ServiceV3Error(
                "service.context-rejected",
                "context or input binding bytes are invalid",
            ) from exc
        _require(
            binding.to_dict()["context_ref_id"] == context.id,
            "service.context-rejected",
            "input binding belongs to another context",
        )
        accepted = self._context_validator(context, binding)
        _require(
            accepted is True,
            "service.context-rejected",
            "context authority rejected the exact pair",
        )
        root = self._context_root(context.id, binding.id)
        self._write_immutable(root / "context-ref.json", context.canonical_bytes)
        self._write_immutable(root / "input-binding.json", binding.canonical_bytes)
        return context.id, binding.id

    def context_bytes(
        self, context_ref_id: str, input_binding_id: str
    ) -> tuple[bytes, bytes]:
        root = self._context_root(context_ref_id, input_binding_id)
        context_path = root / "context-ref.json"
        binding_path = root / "input-binding.json"
        _require(
            context_path.is_file() and binding_path.is_file(),
            "unknown-context",
            "exact context/input pair is not registered",
        )
        context_raw, binding_raw = (
            context_path.read_bytes(),
            binding_path.read_bytes(),
        )
        context = load_context_ref(context_raw)
        binding = load_input_binding(binding_raw)
        _require(
            context.id == context_ref_id
            and binding.id == input_binding_id
            and binding.to_dict()["context_ref_id"] == context.id,
            "service.context-corrupt",
            "registered context/input pair is corrupt",
        )
        return context_raw, binding_raw

    def list_contexts(self) -> tuple[tuple[str, str], ...]:
        rows = []
        for context in sorted(self.contexts.iterdir(), key=lambda path: path.name):
            _require(
                not context.is_symlink(),
                "service.context-corrupt",
                "registered context storage contains a symbolic link",
            )
            if not context.is_dir():
                continue
            if re.fullmatch(r"[0-9a-f]{64}", context.name):
                context_id = load_context_ref(
                    (context / "context-ref.json").read_bytes()
                ).id
                binding_id = load_input_binding(
                    (context / "input-binding.json").read_bytes()
                ).id
                _require(
                    self._context_root(context_id, binding_id) == context,
                    "service.context-corrupt",
                    "registered context storage key differs from its exact pair",
                )
                self.context_bytes(context_id, binding_id)
                rows.append((context_id, binding_id))
                continue
            for binding in sorted(context.iterdir(), key=lambda path: path.name):
                _require(
                    not binding.is_symlink(),
                    "service.context-corrupt",
                    "registered input storage contains a symbolic link",
                )
                if binding.is_dir():
                    self.context_bytes(context.name, binding.name)
                    rows.append((context.name, binding.name))
        return tuple(sorted(rows))

    def _load_head(self, job_id: str) -> dict[str, Any]:
        root = self._job_root(job_id)
        path = root / "head.json"
        _require(
            root.is_dir(),
            "service.unknown-job",
            "durable job is unavailable",
        )
        raw: bytes | None = None
        for _attempt in range(50):
            try:
                raw = path.read_bytes()
                break
            except FileNotFoundError:
                # Some supported host filesystems expose a tiny visibility
                # window while replacing an operational head. Immutable job
                # records remain authoritative, so retry the bounded head read.
                time.sleep(0.001)
        _require(
            raw is not None,
            "service.job-head-unavailable",
            "durable job head was transiently unavailable",
        )
        value = parse_canonical_json(raw)
        _require(type(value) is dict, "service.job-corrupt", "job head is corrupt")
        return value

    def handle(self, job_id: str) -> DurableJobHandle:
        head = self._load_head(job_id)
        return DurableJobHandle(
            job_id,
            head["job_submission_id"],
            head["latest_event_id"],
            head["latest_event_ordinal"],
            head["lifecycle_state"],
            head["mutation_state"],
            head["terminal_seal_id"],
            head["terminal_outcome"],
        )

    def list_jobs(self) -> tuple[DurableJobHandle, ...]:
        result = []
        for path in sorted(self.jobs.iterdir()):
            if path.is_dir():
                result.append(self.handle(self._stored_job_id(path.name)))
        return tuple(result)

    def _attempt(self, job_id: str) -> ValidatedJobRecord:
        return load_job_record((self._job_root(job_id) / "attempt.json").read_bytes())

    def job_binding(self, job_id: str) -> tuple[str, str]:
        """Return the exact context/input pair sealed by a job submission."""

        submission = load_job_record(
            (self._job_root(job_id) / "submission.json").read_bytes()
        ).to_dict()
        context_ref_id = submission["context_ref_id"]
        input_binding_id = submission["input_binding_id"]
        self.context_bytes(context_ref_id, input_binding_id)
        return context_ref_id, input_binding_id

    def successful_result(self, job_id: str) -> Any:
        """Reopen one successful result through its immutable terminal seal."""

        handle = self.handle(job_id)
        _require(
            handle.lifecycle_state == "terminal"
            and handle.terminal_outcome == "succeeded"
            and handle.terminal_seal_id is not None,
            "service.job-result-unavailable",
            "durable job has no successful immutable result",
        )
        self.validate_complete_job(job_id)
        root = self._job_root(job_id)
        result_raw = (root / "result.json").read_bytes()
        terminal = load_job_record((root / "terminal.json").read_bytes())
        _require(
            terminal.id == handle.terminal_seal_id
            and terminal.to_dict()["result_object_descriptor_id"]
            == _descriptor_id(result_raw, "service-job-result"),
            "service.job-corrupt",
            "durable result bytes differ from the immutable terminal seal",
        )
        return parse_canonical_json(result_raw)

    def _append_event_locked(
        self,
        job_id: str,
        *,
        body: Mapping[str, Any],
        lifecycle_state: str,
        mutation_state: str,
        actor_id: str,
        owner_attempt: bool = True,
    ) -> ValidatedJobRecord:
        root = self._job_root(job_id)
        head_path = root / "head.json"
        head = (
            None
            if not head_path.exists()
            else parse_canonical_json(head_path.read_bytes())
        )
        ordinal = 0 if head is None else head["latest_event_ordinal"] + 1
        attempt = self._attempt(job_id) if owner_attempt else None
        submission = load_job_record((root / "submission.json").read_bytes())
        submission_value = submission.to_dict()
        event = seal_job_record(
            {
                **_header("job-event"),
                "actor_id": actor_id,
                "attempt_id": (
                    None if attempt is None else attempt.to_dict()["attempt_id"]
                ),
                "attempt_record_id": None if attempt is None else attempt.id,
                "body": dict(body),
                "context_ref_id": submission_value["context_ref_id"],
                "event_ordinal": ordinal,
                "event_type": body["body_kind"],
                "evidence_eligible": False,
                "input_binding_id": submission_value["input_binding_id"],
                "job_id": job_id,
                "job_submission_id": submission.id,
                "lifecycle_state": lifecycle_state,
                "mutation_state": mutation_state,
                "occurred_at": _now(),
                "previous_event_id": (
                    None if head is None else head["latest_event_id"]
                ),
                "process_identity_record_id": None,
                "process_instance_id": None,
                "session_id": None,
                "session_record_id": None,
            }
        )
        self._write_immutable(
            root / "events" / f"{ordinal:020d}.json",
            event.canonical_bytes,
        )
        next_head = {
            "attempt_record_id": None if attempt is None else attempt.id,
            "cancellation_event_id": (
                None if head is None else head.get("cancellation_event_id")
            ),
            "cancellation_observed": (
                False if head is None else head.get("cancellation_observed", False)
            ),
            "cancellation_request_id": (
                None if head is None else head.get("cancellation_request_id")
            ),
            "job_submission_id": submission.id,
            "latest_event_id": event.id,
            "latest_event_ordinal": ordinal,
            "lifecycle_state": lifecycle_state,
            "mutation_state": mutation_state,
            "terminal_outcome": (
                None if head is None else head.get("terminal_outcome")
            ),
            "terminal_seal_id": (
                None if head is None else head.get("terminal_seal_id")
            ),
        }
        self._replace(head_path, canonical_json_bytes(next_head))
        return event

    def create_job(
        self,
        registration: ServiceHandlerRegistration,
        *,
        context_ref_id: str,
        input_binding_id: str,
        arguments: Mapping[str, Any],
        idempotency_key: str,
        actor_id: str,
    ) -> tuple[DurableJobHandle, bool]:
        self.context_bytes(context_ref_id, input_binding_id)
        _require(
            type(arguments) is dict,
            "invalid-request",
            "job arguments must be an ordinary object",
        )
        _require(
            type(idempotency_key) is str
            and 1 <= len(idempotency_key.encode("utf-8")) <= 512,
            "invalid-request",
            "idempotency key is invalid",
        )
        scope = canonical_json_bytes(
            {
                "capability_id": registration.capability_id,
                "context_ref_id": context_ref_id,
                "idempotency_key": idempotency_key,
                "input_binding_id": input_binding_id,
                "method": registration.method,
            }
        )
        digest = hashlib.sha256(scope).hexdigest()
        link = self.idempotency / f"{digest}.json"
        with self._thread_lock, self._file_lock("idempotency"):
            if link.is_file():
                value = parse_canonical_json(link.read_bytes())
                return self.handle(value["job_id"]), False
            job_id = _operational("job")
            root = self._job_root(job_id)
            root.mkdir(mode=0o700)
            request_bytes = canonical_json_bytes(dict(arguments))
            self._write_immutable(root / "request.json", request_bytes)
            submission = seal_job_record(
                {
                    **_header("job-submission"),
                    "capability_id": registration.capability_id,
                    "consent_record_id": None,
                    "context_ref_id": context_ref_id,
                    "evidence_eligible": False,
                    "handler_id": registration.handler_id,
                    "handler_implementation_id": registration.implementation_id,
                    "idempotency_collision_policy": "return-existing",
                    "idempotency_key_digest": digest,
                    "idempotency_scope": "workspace.action",
                    "input_binding_id": input_binding_id,
                    "job_id": job_id,
                    "mutation_boundary": registration.mutation_boundary,
                    "plan_record_id": None,
                    "privacy_class": "project",
                    "request_object_descriptor_id": _descriptor_id(
                        request_bytes, "service-request"
                    ),
                    "requested_resource_claims": [],
                    "retention_policy_id": content_id(
                        "policy", {"policy": "service-job-retention-v1"}
                    ),
                    "retry_of_terminal_seal_id": None,
                    "submitted_at": _now(),
                    "submitted_by_actor_id": actor_id,
                }
            )
            self._write_immutable(
                root / "submission.json", submission.canonical_bytes
            )
            attempt = seal_job_record(
                {
                    **_header("job-attempt"),
                    "attempt_id": _operational("attempt"),
                    "attempt_ordinal": 0,
                    "attempt_reason": "initial",
                    "context_ref_id": context_ref_id,
                    "created_at": _now(),
                    "evidence_eligible": False,
                    "handler_implementation_id": registration.implementation_id,
                    "input_binding_id": input_binding_id,
                    "job_id": job_id,
                    "job_submission_id": submission.id,
                    "previous_attempt_record_id": None,
                    "resource_claim_keys": [],
                    "retry_decision_id": None,
                    "service_instance_id": _operational("service-instance"),
                    "session_id": None,
                }
            )
            self._write_immutable(root / "attempt.json", attempt.canonical_bytes)
            self._append_event_locked(
                job_id,
                body={
                    "body_kind": "queued",
                    "not_before": None,
                    "priority": 10,
                    "queue_policy_id": content_id(
                        "policy", {"policy": "service-job-queue-v1"}
                    ),
                },
                lifecycle_state="queued",
                mutation_state="not-started",
                actor_id=actor_id,
                owner_attempt=False,
            )
            self._write_immutable(
                link,
                canonical_json_bytes(
                    {
                        "job_id": job_id,
                        "job_submission_id": submission.id,
                    }
                ),
            )
            return self.handle(job_id), True

    def find_idempotent_job(
        self,
        registration: ServiceHandlerRegistration,
        *,
        context_ref_id: str,
        input_binding_id: str,
        idempotency_key: str,
    ) -> DurableJobHandle | None:
        """Resolve an existing replay without consuming fresh queue capacity."""

        scope = canonical_json_bytes(
            {
                "capability_id": registration.capability_id,
                "context_ref_id": context_ref_id,
                "idempotency_key": idempotency_key,
                "input_binding_id": input_binding_id,
                "method": registration.method,
            }
        )
        link = self.idempotency / f"{hashlib.sha256(scope).hexdigest()}.json"
        with self._thread_lock, self._file_lock("idempotency"):
            if not link.is_file():
                return None
            value = parse_canonical_json(link.read_bytes())
            _require(
                type(value) is dict
                and set(value) == {"job_id", "job_submission_id"},
                "service.job-corrupt",
                "idempotency link is malformed",
            )
            handle = self.handle(value["job_id"])
            _require(
                handle.job_submission_id == value["job_submission_id"],
                "service.job-corrupt",
                "idempotency link names another submission",
            )
            return handle

    def start_job(self, job_id: str, *, actor_id: str) -> None:
        with self._thread_lock, self._file_lock(job_id):
            head = self._load_head(job_id)
            if head["terminal_seal_id"] is not None:
                return
            self._append_event_locked(
                job_id,
                body={
                    "attempt_reason": "initial",
                    "body_kind": "attempt-starting",
                },
                lifecycle_state="starting",
                mutation_state=head["mutation_state"],
                actor_id=actor_id,
            )
            self._append_event_locked(
                job_id,
                body={
                    "body_kind": "running",
                    "readiness_observation_id": content_id(
                        "readiness-observation",
                        {"job_id": job_id, "ready": True},
                    ),
                },
                lifecycle_state="running",
                mutation_state=head["mutation_state"],
                actor_id=actor_id,
            )

    def progress(
        self,
        job_id: str,
        *,
        phase: str,
        completed: int,
        total: int | None,
        unit: str,
        message: str | None,
        actor_id: str,
    ) -> ValidatedJobRecord:
        with self._thread_lock, self._file_lock(job_id):
            head = self._load_head(job_id)
            _require(
                head["terminal_seal_id"] is None,
                "service.job-terminal",
                "terminal job cannot emit progress",
            )
            return self._append_event_locked(
                job_id,
                body={
                    "body_kind": "progress",
                    "completed": completed,
                    "message": message,
                    "mutation_started": head["mutation_state"]
                    not in {"not-started", "temporary-residue"},
                    "phase": phase,
                    "total": total,
                    "unit": unit,
                },
                lifecycle_state=head["lifecycle_state"],
                mutation_state=head["mutation_state"],
                actor_id=actor_id,
            )

    def set_mutation_state(
        self, job_id: str, state: str, *, actor_id: str
    ) -> ValidatedJobRecord:
        _require(
            state in _MUTATION_STATES,
            "service.invalid-mutation-state",
            "mutation state is invalid",
        )
        with self._thread_lock, self._file_lock(job_id):
            head = self._load_head(job_id)
            submission = load_job_record(
                (self._job_root(job_id) / "submission.json").read_bytes()
            ).to_dict()
            _require(
                head["terminal_seal_id"] is None
                and state != head["mutation_state"],
                "service.invalid-mutation-transition",
                "mutation transition is unavailable",
            )
            _require(
                state in _MUTATION_TRANSITIONS[head["mutation_state"]]
                and state
                in _MUTATION_BOUNDARY_STATES[
                    submission["mutation_boundary"]
                ],
                "service.invalid-mutation-transition",
                "mutation transition differs from its prior state or boundary",
            )
            return self._append_event_locked(
                job_id,
                body={
                    "body_kind": "mutation-state-changed",
                    "from_state": head["mutation_state"],
                    "mutation_observation_id": content_id(
                        "mutation-observation",
                        {"job_id": job_id, "state": state},
                    ),
                    "to_state": state,
                },
                lifecycle_state=head["lifecycle_state"],
                mutation_state=state,
                actor_id=actor_id,
            )

    def cancel(
        self,
        job_id: str,
        *,
        expected_event_id: str,
        expected_event_ordinal: int,
        actor_id: str,
        reason: str,
        idempotency_key: str | None = None,
    ) -> DurableJobHandle:
        with self._thread_lock, self._file_lock(job_id):
            head = self._load_head(job_id)
            if head["terminal_seal_id"] is not None:
                return self.handle(job_id)
            _require(
                idempotency_key is None
                or (
                    type(idempotency_key) is str
                    and 1 <= len(idempotency_key.encode("utf-8")) <= 512
                ),
                "invalid-request",
                "cancellation idempotency key is invalid",
            )
            cancellation_id = (
                _operational("cancellation")
                if idempotency_key is None
                else "cancellation-v2:"
                + hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "actor_id": actor_id,
                            "idempotency_key": idempotency_key,
                            "job_id": job_id,
                        }
                    )
                ).hexdigest()[:32]
            )
            if head["cancellation_request_id"] is not None:
                _require(
                    head["cancellation_request_id"] == cancellation_id,
                    "compare-and-swap-lost",
                    "another cancellation request already owns the durable head",
                )
                return self.handle(job_id)
            _require(
                head["latest_event_id"] == expected_event_id
                and head["latest_event_ordinal"] == expected_event_ordinal,
                "compare-and-swap-lost",
                "cancellation expected event differs from the durable head",
            )
            event = self._append_event_locked(
                job_id,
                body={
                    "body_kind": "cancellation-requested",
                    "cancellation_request_id": cancellation_id,
                    "expected_event_id": expected_event_id,
                    "expected_event_ordinal": expected_event_ordinal,
                    "reason": reason,
                    "requested_by_actor_id": actor_id,
                },
                lifecycle_state=head["lifecycle_state"],
                mutation_state=head["mutation_state"],
                actor_id=actor_id,
            )
            head = self._load_head(job_id)
            head["cancellation_event_id"] = event.id
            head["cancellation_request_id"] = cancellation_id
            self._replace(
                self._job_root(job_id) / "head.json",
                canonical_json_bytes(head),
            )
            return self.handle(job_id)

    def cancellation_requested(self, job_id: str) -> bool:
        return self._load_head(job_id)["cancellation_request_id"] is not None

    def _observe_cancellation_locked(
        self, job_id: str, *, actor_id: str, observation_point: str
    ) -> None:
        head = self._load_head(job_id)
        if (
            head["cancellation_request_id"] is None
            or head["cancellation_observed"]
        ):
            return
        before = head["mutation_state"] in {"not-started", "temporary-residue"}
        self._append_event_locked(
            job_id,
            body={
                "body_kind": "cancellation-observed",
                "cancellation_request_id": head["cancellation_request_id"],
                "disposition": (
                    "stopping-before-mutation"
                    if before
                    else "stopping-after-mutation"
                ),
                "observation_point": observation_point,
                "request_event_id": head["cancellation_event_id"],
            },
            lifecycle_state=head["lifecycle_state"],
            mutation_state=head["mutation_state"],
            actor_id=actor_id,
        )
        head = self._load_head(job_id)
        head["cancellation_observed"] = True
        self._replace(
            self._job_root(job_id) / "head.json", canonical_json_bytes(head)
        )

    def observe_cancellation(
        self, job_id: str, *, actor_id: str, observation_point: str
    ) -> str:
        with self._thread_lock, self._file_lock(job_id):
            self._observe_cancellation_locked(
                job_id,
                actor_id=actor_id,
                observation_point=observation_point,
            )
            return self._load_head(job_id)["mutation_state"]

    def _event_values(self, job_id: str) -> list[dict[str, Any]]:
        root = self._job_root(job_id) / "events"
        values = []
        for path in sorted(root.glob("*.json")):
            values.append(load_job_record(path.read_bytes()).to_dict())
        return values

    @staticmethod
    def _recovery_obligations(
        events: list[dict[str, Any]], mutation_state: str
    ) -> list[dict[str, Any]]:
        obligations: list[tuple[str, str, str]] = []
        by_recovery = {
            "create-new-residue": ("temporary-residue", "clean"),
            "manifest-published-ref-unmoved": (
                "unmoved-reference",
                "verify",
            ),
            "external-mutation-unproven": (
                "external-mutation-indeterminate",
                "manual-reconcile",
            ),
            "terminal-corrupt-or-incomplete": (
                "manual-verification",
                "manual-reconcile",
            ),
        }
        for event in events:
            if event["event_type"] == "recovery-classified":
                row = by_recovery.get(event["body"]["classification"])
                if row is not None:
                    obligations.append((row[0], row[1], event["id"]))
        if not obligations:
            fallback = {
                "temporary-residue": ("temporary-residue", "clean"),
                "protected-mutation-started": (
                    "partial-protected-mutation",
                    "manual-reconcile",
                ),
                "protected-mutation-partial": (
                    "partial-protected-mutation",
                    "manual-reconcile",
                ),
                "external-mutation-indeterminate": (
                    "external-mutation-indeterminate",
                    "manual-reconcile",
                ),
                "external-mutation-started": (
                    "external-mutation-partial",
                    "manual-reconcile",
                ),
                "external-mutation-partial": (
                    "external-mutation-partial",
                    "manual-reconcile",
                ),
            }.get(mutation_state)
            if fallback is not None:
                obligations.append((fallback[0], fallback[1], events[-1]["id"]))
        return [
            {
                "classification": classification,
                "detail": "Durable recovery requires the declared follow-up action.",
                "ordinal": ordinal,
                "related_event_id": event_id,
                "required_action": action,
            }
            for ordinal, (classification, action, event_id) in enumerate(obligations)
        ]

    def _seal_terminal_event_locked(
        self,
        job_id: str,
        terminal_event: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> ValidatedJobRecord:
        root = self._job_root(job_id)
        terminal_path = root / "terminal.json"
        if terminal_path.is_file():
            return load_job_record(terminal_path.read_bytes())
        head = self._load_head(job_id)
        body = terminal_event["body"]
        _require(
            terminal_event["event_type"] == "terminal-ready"
            and terminal_event["id"] == head["latest_event_id"]
            and terminal_event["event_ordinal"] == head["latest_event_ordinal"],
            "service.job-corrupt",
            "terminal-ready event is not the durable job head",
        )
        submission = load_job_record((root / "submission.json").read_bytes())
        attempt = self._attempt(job_id)
        events = self._event_values(job_id)
        cancellation_ids = sorted(
            event["body"]["cancellation_request_id"]
            for event in events
            if event["event_type"] == "cancellation-requested"
        )
        seal = seal_job_record(
            {
                **_header("job-terminal-seal"),
                "attempt_record_ids": [attempt.id],
                "cancellation_observed": any(
                    event["event_type"] == "cancellation-observed"
                    for event in events
                ),
                "cancellation_request_ids": cancellation_ids,
                "context_ref_id": submission.to_dict()["context_ref_id"],
                "diagnostics": [],
                "event_count": len(events),
                "event_head_id": terminal_event["id"],
                "event_ledger_root": event_ledger_root(
                    job_id, [event["id"] for event in events]
                ),
                "evidence_eligible": False,
                "failure_object_descriptor_id": body[
                    "failure_object_descriptor_id"
                ],
                "input_binding_id": submission.to_dict()["input_binding_id"],
                "job_id": job_id,
                "job_submission_id": submission.id,
                "limitations": [],
                "mutation_state": terminal_event["mutation_state"],
                "process_identity_record_ids": [],
                "recovery_obligations": self._recovery_obligations(
                    events, terminal_event["mutation_state"]
                ),
                "resource_claim_accounting": [],
                "result_object_descriptor_id": body[
                    "result_object_descriptor_id"
                ],
                "sealed_at": _now(),
                "sealed_by_actor_id": actor_id,
                "session_record_ids": [],
                "terminal_event_id": terminal_event["id"],
                "terminal_outcome": body["terminal_outcome"],
            }
        )
        self._write_immutable(terminal_path, seal.canonical_bytes)
        return seal

    def _publish_terminal_head_locked(
        self,
        job_id: str,
        seal: ValidatedJobRecord,
    ) -> DurableJobHandle:
        root = self._job_root(job_id)
        value = seal.to_dict()
        head = self._load_head(job_id)
        head["terminal_outcome"] = value["terminal_outcome"]
        head["terminal_seal_id"] = seal.id
        self._replace(root / "head.json", canonical_json_bytes(head))
        self.validate_complete_job(job_id)
        return self.handle(job_id)

    def _recover_terminal_locked(
        self, job_id: str, *, actor_id: str
    ) -> DurableJobHandle:
        events = self._event_values(job_id)
        _require(
            bool(events) and events[-1]["event_type"] == "terminal-ready",
            "service.job-corrupt",
            "terminal lifecycle has no terminal-ready event",
        )
        seal = self._seal_terminal_event_locked(
            job_id, events[-1], actor_id=actor_id
        )
        return self._publish_terminal_head_locked(job_id, seal)

    def terminal(
        self,
        job_id: str,
        *,
        outcome: str,
        result: Any = None,
        failure: Mapping[str, Any] | None = None,
        actor_id: str,
    ) -> DurableJobHandle:
        _require(
            outcome in _TERMINAL_OUTCOMES,
            "service.invalid-terminal-outcome",
            "terminal outcome is invalid",
        )
        with self._thread_lock, self._file_lock(job_id):
            head = self._load_head(job_id)
            if head["terminal_seal_id"] is not None:
                return self.handle(job_id)
            if head["lifecycle_state"] == "terminal":
                return self._recover_terminal_locked(job_id, actor_id=actor_id)
            if head["cancellation_request_id"] is not None:
                self._observe_cancellation_locked(
                    job_id,
                    actor_id=actor_id,
                    observation_point="before.terminal-seal",
                )
                head = self._load_head(job_id)
                outcome = (
                    "cancelled-before-mutation"
                    if head["mutation_state"]
                    in {"not-started", "temporary-residue"}
                    else "cancelled-after-mutation"
                )
                result = None
                failure = None
            result_descriptor_id = None
            failure_descriptor_id = None
            root = self._job_root(job_id)
            self._fault("terminal.before-result-object")
            if outcome == "succeeded":
                result_bytes = canonical_json_bytes(result)
                self._write_immutable(root / "result.json", result_bytes)
                result_descriptor_id = _descriptor_id(
                    result_bytes, "service-job-result"
                )
            else:
                failure_bytes = canonical_json_bytes(
                    dict(
                        failure
                        or {
                            "code": outcome,
                            "message": "job ended without a successful result",
                        }
                    )
                )
                self._write_immutable(root / "failure.json", failure_bytes)
                failure_descriptor_id = _descriptor_id(
                    failure_bytes, "service-job-failure"
                )
            self._fault("terminal.after-result-object")
            terminal_event = self._append_event_locked(
                job_id,
                body={
                    "body_kind": "terminal-ready",
                    "failure_object_descriptor_id": failure_descriptor_id,
                    "result_object_descriptor_id": result_descriptor_id,
                    "terminal_outcome": outcome,
                },
                lifecycle_state="terminal",
                mutation_state=head["mutation_state"],
                actor_id=actor_id,
            )
            self._fault("terminal.after-terminal-event")
            seal = self._seal_terminal_event_locked(
                job_id,
                terminal_event.to_dict(),
                actor_id=actor_id,
            )
            self._fault("terminal.after-terminal-seal")
            handle = self._publish_terminal_head_locked(job_id, seal)
            self._fault("terminal.after-terminal-head")
            return handle

    def validate_complete_job(self, job_id: str) -> None:
        root = self._job_root(job_id)
        records = [
            load_job_record((root / "submission.json").read_bytes()),
            load_job_record((root / "attempt.json").read_bytes()),
            *(load_job_record(path.read_bytes()) for path in sorted((root / "events").glob("*.json"))),
            load_job_record((root / "terminal.json").read_bytes()),
        ]
        by_id = {record.id: record.canonical_bytes for record in records}
        submission = records[0].to_dict()
        context_raw, binding_raw = self.context_bytes(
            submission["context_ref_id"], submission["input_binding_id"]
        )

        def resolver(record_id: str):
            if record_id == submission["context_ref_id"]:
                return context_raw
            if record_id == submission["input_binding_id"]:
                return binding_raw
            return by_id.get(record_id)

        def context_validator(request: JobContextValidationRequest) -> bool:
            return (
                request.context_ref_id == submission["context_ref_id"]
                and request.input_binding_id == submission["input_binding_id"]
                and request.context_ref_bytes == context_raw
                and request.input_binding_bytes == binding_raw
            )

        validate_job_publication(
            records,
            relations=JobRelationResolvers(
                record_resolver=resolver,
                external_reference_validator=lambda _expectation: True,
                context_publication_validator=context_validator,
            ),
        )

    def subscription_page(
        self,
        job_id: str,
        *,
        after_ordinal: int,
        maximum_events: int = 64,
        fail_on_backpressure: bool = False,
    ) -> JobSubscriptionPage:
        _require(
            type(after_ordinal) is int and after_ordinal >= -1,
            "subscription-gap",
            "subscription cursor is invalid",
        )
        _require(
            type(maximum_events) is int and 1 <= maximum_events <= 1024,
            "budget-exceeded",
            "subscription event budget is invalid",
        )
        values = self._event_values(job_id)
        available = [
            value for value in values if value["event_ordinal"] > after_ordinal
        ]
        if fail_on_backpressure and len(available) > maximum_events:
            raise ServiceV3Error(
                "backpressure",
                "subscription consumer exceeded its declared event budget",
                retryable=True,
            )
        selected = tuple(available[:maximum_events])
        next_ordinal = (
            after_ordinal
            if not selected
            else selected[-1]["event_ordinal"]
        )
        return JobSubscriptionPage(
            job_id,
            after_ordinal,
            selected,
            next_ordinal,
            len(available) > len(selected),
            0,
        )

    def recover_incomplete(self, *, actor_id: str) -> tuple[str, ...]:
        recovered = []
        for path in sorted(self.jobs.iterdir()):
            if not path.is_dir():
                continue
            job_id = self._stored_job_id(path.name)
            with self._thread_lock, self._file_lock(job_id):
                head = self._load_head(job_id)
                if head["terminal_seal_id"] is not None:
                    continue
                if head["lifecycle_state"] == "terminal":
                    self._recover_terminal_locked(job_id, actor_id=actor_id)
                    recovered.append(job_id)
                    continue
                if head["lifecycle_state"] == "queued":
                    self._append_event_locked(
                        job_id,
                        body={
                            "attempt_reason": "initial",
                            "body_kind": "attempt-starting",
                        },
                        lifecycle_state="starting",
                        mutation_state=head["mutation_state"],
                        actor_id=actor_id,
                    )
                    head = self._load_head(job_id)
                self._append_event_locked(
                    job_id,
                    body={
                        "body_kind": "orphaned",
                        "last_known_process_identity_id": None,
                        "orphan_reason": "service-crash",
                    },
                    lifecycle_state="orphaned",
                    mutation_state=head["mutation_state"],
                    actor_id=actor_id,
                )
                state = head["mutation_state"]
                if state == "not-started":
                    classification = "no-mutation-began"
                    required_action = "seal-terminal"
                elif state == "temporary-residue":
                    classification = "create-new-residue"
                    required_action = "clean-residue"
                elif state == "immutable-output-published":
                    classification = "manifest-published-ref-unmoved"
                    required_action = "verify-reference"
                elif state == "reference-committed":
                    classification = "ref-committed"
                    required_action = "seal-terminal"
                elif state == "external-mutation-indeterminate":
                    classification = "external-mutation-unproven"
                    required_action = "manual-reconcile"
                else:
                    classification = "terminal-corrupt-or-incomplete"
                    required_action = "manual-reconcile"
                descriptor_id = _descriptor_id(
                    canonical_json_bytes({"job_id": job_id, "state": state}),
                    "service-recovery-observation",
                )
                recovery_state = (
                    "external-mutation-indeterminate"
                    if classification == "external-mutation-unproven"
                    else state
                )
                self._append_event_locked(
                    job_id,
                    body={
                        "body_kind": "recovery-classified",
                        "classification": classification,
                        "live_process_revalidated": False,
                        "manifest_object_descriptor_id": (
                            descriptor_id
                            if classification
                            == "manifest-published-ref-unmoved"
                            else None
                        ),
                        "prior_process_identity_id": None,
                        "recovery_decision_id": content_id(
                            "recovery-decision",
                            {"classification": classification, "job_id": job_id},
                        ),
                        "recovery_id": _operational("recovery"),
                        "reference_event_id": (
                            content_id(
                                "reference-event",
                                {"job_id": job_id, "recovered": True},
                            )
                            if classification == "ref-committed"
                            else None
                        ),
                        "required_action": required_action,
                        "residue_object_descriptor_id": (
                            descriptor_id
                            if classification == "create-new-residue"
                            else None
                        ),
                    },
                    lifecycle_state="recovering",
                    mutation_state=recovery_state,
                    actor_id=actor_id,
                )
            self.terminal(
                job_id,
                outcome=(
                    "indeterminate"
                    if recovery_state == "external-mutation-indeterminate"
                    else "failed"
                ),
                failure={
                    "code": "service-recovered-incomplete-job",
                    "classification": classification,
                },
                actor_id=actor_id,
            )
            recovered.append(job_id)
        return tuple(recovered)








__all__ = [
    "ContextListHandler",
    "DurableJobStore",
    "JobCancellationHandler",
    "JobCancellationPlan",
    "JobCancellationPlanResolver",
    "JobEventPageHandler",
    "JobSubscriptionHandler",
    "ServiceCapabilitiesHandler",
    "load_job_cancellation_plan",
    "seal_job_cancellation_plan",
    "validate_job_cancellation_arguments",
    "validate_job_handle_result",
    "validate_job_event_page_arguments",
    "validate_job_event_page_result",
    "validate_job_subscription_arguments",
    "validate_job_subscription_result",
    "validate_context_list_result",
    "validate_empty_service_arguments",
    "validate_service_capabilities_result",
]
