"""Service handler, lease and job-store contracts; no host or domain imports."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Protocol
from .canonical import canonical_json_bytes, parse_canonical_json

_MUTATION_BOUNDARIES = {"none", "immutable-publication", "reference-update", "protected-state", "external-side-effect"}

class ServiceV3Error(ValueError):
    """Stable service rejection with explicit mutation truth."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        mutation_state: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.mutation_state = mutation_state
        self.retryable = retryable


class ServiceCancelled(ServiceV3Error):
    def __init__(self, mutation_state: str) -> None:
        super().__init__(
            "cancel-requested",
            "durable cancellation was observed by the owner handler",
            mutation_state=mutation_state,
        )


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ServiceV3Error(code, message)


def _validate_handler_value(
    validator: Callable[[Any], bool] | None,
    value: Any,
    *,
    code: str,
    label: str,
) -> Any:
    """Validate one detached canonical value and detect callback mutation."""

    if validator is None:
        return parse_canonical_json(canonical_json_bytes(value))
    try:
        isolated = parse_canonical_json(canonical_json_bytes(value))
        before = canonical_json_bytes(isolated)
        accepted = validator(isolated)
        after = canonical_json_bytes(isolated)
    except Exception as exc:
        raise ServiceV3Error(
            code,
            f"{label} validator failed with {type(exc).__name__}",
        ) from exc
    _require(
        accepted is True and before == after,
        code,
        f"{label} was rejected or mutated by its validator",
    )
    return isolated


@dataclass(frozen=True, slots=True)
class ServiceHandlerRegistration:
    method: str
    capability_id: str
    capability_version: str
    handler_id: str
    implementation_id: str
    mutation_boundary: str
    asynchronous: bool
    maximum_concurrency: int
    handler: Callable[["ServiceExecutionContext", Mapping[str, Any]], Any]
    request_validator: Callable[[Mapping[str, Any]], bool] | None = None
    result_validator: Callable[[Any], bool] | None = None
    context_binding: str = "required"
    input_binding: str = "required"

    def __post_init__(self) -> None:
        _require(
            type(self.method) is str and "/" in self.method,
            "service.invalid-handler",
            "handler method is invalid",
        )
        for value, prefix in (
            (self.capability_id, "capability:sha256:"),
            (self.handler_id, "handler:sha256:"),
            (self.implementation_id, "implementation:sha256:"),
        ):
            _require(
                type(value) is str and value.startswith(prefix),
                "service.invalid-handler",
                "handler identity has the wrong kind",
            )
        _require(
            type(self.capability_version) is str
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.capability_version)
            is not None,
            "service.invalid-handler",
            "capability version is invalid",
        )
        _require(
            self.mutation_boundary in _MUTATION_BOUNDARIES,
            "service.invalid-handler",
            "handler mutation boundary is invalid",
        )
        _require(
            type(self.asynchronous) is bool
            and type(self.maximum_concurrency) is int
            and 1 <= self.maximum_concurrency <= 64
            and callable(self.handler),
            "service.invalid-handler",
            "handler execution fields are invalid",
        )
        _require(
            (self.request_validator is None or callable(self.request_validator))
            and (self.result_validator is None or callable(self.result_validator)),
            "service.invalid-handler",
            "handler validators must be callable when supplied",
        )
        _require(
            self.context_binding in {"none", "required"}
            and self.input_binding in {"none", "required"}
            and not (
                self.context_binding == "none"
                and self.input_binding != "none"
            ),
            "service.invalid-handler",
            "handler context/input binding modes are invalid",
        )
        _require(
            self.asynchronous
            or self.mutation_boundary == "none"
            or (
                self.method in {"context/register", "job/cancel"}
                and self.mutation_boundary == "protected-state"
            ),
            "service.invalid-handler",
            (
                "only durable jobs, exact context registration, or exact job "
                "cancellation may mutate"
            ),
        )


@dataclass(frozen=True, slots=True)
class ServicePhysicalLeasePorts:
    """Host-selected physical locking without platform assumptions in core."""

    provider_id: str
    acquire_instance: Callable[[Path], Callable[[], None]]
    exclusive: Callable[[Path], Any]

    def __post_init__(self) -> None:
        _require(
            type(self.provider_id) is str
            and bool(self.provider_id)
            and callable(self.acquire_instance)
            and callable(self.exclusive),
            "service.invalid-physical-lease-provider",
            "physical lease ports must name one callable Host Adapter provider",
        )


@dataclass(frozen=True, slots=True)
class DurableJobHandle:
    job_id: str
    job_submission_id: str
    latest_event_id: str
    latest_event_ordinal: int
    lifecycle_state: str
    mutation_state: str
    terminal_seal_id: str | None
    terminal_outcome: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_submission_id": self.job_submission_id,
            "latest_event_id": self.latest_event_id,
            "latest_event_ordinal": self.latest_event_ordinal,
            "lifecycle_state": self.lifecycle_state,
            "mutation_state": self.mutation_state,
            "terminal_outcome": self.terminal_outcome,
            "terminal_seal_id": self.terminal_seal_id,
        }

    def to_protocol_dict(self) -> dict[str, Any]:
        """Return the exact public V3 job-handle shape.

        Terminal outcome remains available through the immutable terminal seal;
        it is not a field in the protocol V3 job handle.
        """

        value = self.to_dict()
        value.pop("terminal_outcome")
        return value


@dataclass(frozen=True, slots=True)
class JobSubscriptionPage:
    job_id: str
    after_ordinal: int
    events: tuple[Mapping[str, Any], ...]
    next_ordinal: int
    has_more: bool
    minimum_available_ordinal: int


class ServiceExecutionContext:
    """Owner handler view; it exposes mechanics, never semantic decisions."""

    def __init__(
        self,
        store: JobStore,
        job_id: str | None,
        *,
        actor_id: str,
        context_ref_id: str | None = None,
        input_binding_id: str | None = None,
        environment_check: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self.job_id = job_id
        self.actor_id = actor_id
        self.context_ref_id = context_ref_id
        self.input_binding_id = input_binding_id
        self._environment_check = environment_check

    @property
    def mutation_state(self) -> str:
        if self.job_id is None:
            return "not-started"
        return self._store.handle(self.job_id).mutation_state

    @property
    def cancellation_requested(self) -> bool:
        if self.job_id is None:
            return False
        return self._store.cancellation_requested(self.job_id)

    def checkpoint(self, observation_point: str) -> None:
        if self._environment_check is not None:
            self._environment_check()
        if self.cancellation_requested:
            state = self._store.observe_cancellation(
                self.job_id,
                actor_id=self.actor_id,
                observation_point=observation_point,
            )
            raise ServiceCancelled(state)

    def progress(
        self,
        phase: str,
        completed: int,
        total: int | None,
        *,
        unit: str = "operations",
        message: str | None = None,
    ) -> None:
        if self.job_id is None:
            return
        self._store.progress(
            self.job_id,
            phase=phase,
            completed=completed,
            total=total,
            unit=unit,
            message=message,
            actor_id=self.actor_id,
        )

    def set_mutation_state(self, state: str) -> None:
        if self.job_id is None:
            raise ServiceV3Error(
                "service.direct-mutation-forbidden",
                "a direct query handler cannot change mutation state",
            )
        self._store.set_mutation_state(
            self.job_id, state, actor_id=self.actor_id
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Return the bounded durable projection used by the job/get handler."""

        handle = self._store.handle(job_id)
        return {
            "job_id": handle.job_id,
            "latest_event_id": handle.latest_event_id,
        }

    def durable_job_handle(self, job_id: str) -> DurableJobHandle:
        """Return the internal typed handle for a mechanical control handler."""

        return self._store.handle(job_id)

    def job_binding(self, job_id: str) -> tuple[str, str]:
        """Reopen the exact context/input pair sealed by a target job."""

        return self._store.job_binding(job_id)

    def successful_job_result(self, job_id: str) -> Any:
        """Reopen a successful result only inside this exact context pair."""

        _require(
            self._store.job_binding(job_id)
            == (self.context_ref_id, self.input_binding_id),
            "stale-context",
            "durable job result belongs to another exact context",
        )
        return self._store.successful_result(job_id)

    def register_context(
        self,
        context_ref_bytes: bytes,
        input_binding_bytes: bytes,
    ) -> tuple[str, str]:
        """Publish one exact context/input pair through the durable store."""

        _require(
            self.job_id is None
            and self.context_ref_id is None
            and self.input_binding_id is None,
            "service.context-registration-binding",
            "context registration must run outside an existing context",
        )
        return self._store.register_context(
            context_ref_bytes,
            input_binding_bytes,
        )

    def cancel_job(
        self,
        job_id: str,
        *,
        expected_event_id: str,
        expected_event_ordinal: int,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Record one idempotent cancellation on the target durable job."""

        return self._store.cancel(
            job_id,
            expected_event_id=expected_event_id,
            expected_event_ordinal=expected_event_ordinal,
            actor_id=self.actor_id,
            reason=reason,
            idempotency_key=idempotency_key,
        ).to_dict()

    def exact_context_bytes(self) -> tuple[bytes, bytes]:
        _require(
            self.context_ref_id is not None and self.input_binding_id is not None,
            "unknown-context",
            "handler execution has no exact context/input binding",
        )
        return self._store.context_bytes(
            self.context_ref_id, self.input_binding_id
        )

    def list_jobs(self) -> tuple[dict[str, Any], ...]:
        return tuple(handle.to_dict() for handle in self._store.list_jobs())

    def list_contexts(self) -> tuple[tuple[str, str], ...]:
        """Reopen and return every exact durable context/input pair."""

        return self._store.list_contexts()

    def subscribe_job(
        self,
        job_id: str,
        *,
        after_ordinal: int,
        maximum_events: int,
        fail_on_backpressure: bool = False,
    ) -> JobSubscriptionPage:
        return self._store.subscription_page(
            job_id,
            after_ordinal=after_ordinal,
            maximum_events=maximum_events,
            fail_on_backpressure=fail_on_backpressure,
        )


class JobStore(Protocol):
    """Explicit owner port for custody, recovery and durable job records."""
    def cancel(self, job_id: str, *, expected_event_id: str, expected_event_ordinal: int,
               actor_id: str, reason: str, idempotency_key: str | None = None) -> DurableJobHandle: ...
    def cancellation_requested(self, job_id: str) -> bool: ...
    def context_bytes(self, context_ref_id: str, input_binding_id: str) -> tuple[bytes, bytes]: ...
    def create_job(self, registration: ServiceHandlerRegistration, *, context_ref_id: str,
                   input_binding_id: str, arguments: Mapping[str, Any], idempotency_key: str,
                   actor_id: str) -> tuple[DurableJobHandle, bool]: ...
    def find_idempotent_job(self, registration: ServiceHandlerRegistration, *, context_ref_id: str,
                            input_binding_id: str, idempotency_key: str) -> DurableJobHandle | None: ...
    def handle(self, job_id: str) -> DurableJobHandle: ...
    def job_binding(self, job_id: str) -> tuple[str, str]: ...
    def list_contexts(self) -> tuple[tuple[str, str], ...]: ...
    def list_jobs(self) -> tuple[DurableJobHandle, ...]: ...
    def observe_cancellation(self, job_id: str, *, actor_id: str, observation_point: str) -> str: ...
    def progress(self, job_id: str, *, phase: str, completed: int, total: int | None,
                 unit: str, message: str | None, actor_id: str) -> object: ...
    def recover_incomplete(self, *, actor_id: str) -> tuple[str, ...]: ...
    def register_context(self, context_ref_bytes: bytes, input_binding_bytes: bytes) -> tuple[str, str]: ...
    def set_mutation_state(self, job_id: str, state: str, *, actor_id: str) -> object: ...
    def start_job(self, job_id: str, *, actor_id: str) -> None: ...
    def subscription_page(self, job_id: str, *, after_ordinal: int,
                          maximum_events: int = 64, fail_on_backpressure: bool = False) -> JobSubscriptionPage: ...
    def successful_result(self, job_id: str) -> Any: ...
    def terminal(self, job_id: str, *, outcome: str, result: Any = None,
                 failure: Mapping[str, Any] | None = None, actor_id: str) -> DurableJobHandle: ...
