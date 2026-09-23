"""Core-owned bounded scheduling, cancellation coordination and credentials."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
import hmac
import os
import re
import secrets
import threading
from types import MappingProxyType
from typing import Any
from workbench_api.canonical import content_id
from workbench_api.host_filesystem import secure_private_path
from workbench_api.service import (JobStore, ServiceExecutionContext, ServiceHandlerRegistration, ServicePhysicalLeasePorts, ServiceV3Error, ServiceCancelled, DurableJobHandle, _require, _validate_handler_value)
from workbench_api import ModuleError
from ..package_guard import PackageActivity

def _operational(prefix: str) -> str:
    return f"{prefix}-v2:{secrets.token_hex(16)}"

class ServiceRuntimeV3:
    """One embedded handler graph shared by all authenticated transports."""

    def __init__(
        self, root: Path, *, registrations: tuple[ServiceHandlerRegistration, ...],
        store_factory: Callable[[Path, ServicePhysicalLeasePorts], JobStore],
        physical_leases: ServicePhysicalLeasePorts, maximum_workers: int = 8,
        maximum_pending_jobs: int = 64, recover: bool = True,
    ) -> None:
        self._package_activity = PackageActivity()
        self._activity_condition = threading.Condition()
        self._active_dispatches = 0
        self._close_complete = threading.Event()
        # Transport threads must retain the host's explicit profile policy.
        self._owner_context = copy_context()
        try:
            self._initialize(root, registrations=registrations, store_factory=store_factory,
                             physical_leases=physical_leases, maximum_workers=maximum_workers,
                             maximum_pending_jobs=maximum_pending_jobs, recover=recover)
        except BaseException:
            self._package_activity.close()
            raise

    def _initialize(
        self,
        root: Path,
        *,
        registrations: tuple[ServiceHandlerRegistration, ...],
        store_factory: Callable[[Path, ServicePhysicalLeasePorts], JobStore],
        physical_leases: ServicePhysicalLeasePorts,
        maximum_workers: int = 8,
        maximum_pending_jobs: int = 64,
        recover: bool = True,
    ) -> None:
        _require(
            type(registrations) is tuple
            and all(type(item) is ServiceHandlerRegistration for item in registrations),
            "service.invalid-registry",
            "service registrations must be an exact tuple",
        )
        _require(
            type(maximum_workers) is int and 1 <= maximum_workers <= 64,
            "service.invalid-budget",
            "service worker bound is invalid",
        )
        _require(
            type(maximum_pending_jobs) is int
            and 1 <= maximum_pending_jobs <= 4096,
            "service.invalid-budget",
            "service pending-job bound is invalid",
        )
        _require(
            type(physical_leases) is ServicePhysicalLeasePorts,
            "service.invalid-physical-lease-provider",
            "service runtime requires an explicit Host Adapter lease provider",
        )
        index: dict[tuple[str, str], ServiceHandlerRegistration] = {}
        for registration in registrations:
            key = (registration.capability_id, registration.method)
            _require(
                key not in index,
                "service.invalid-registry",
                "service handler registration is duplicated",
            )
            index[key] = registration
        self.root = root
        self.store = store_factory(root, physical_leases)
        self.registrations = MappingProxyType(index)
        self.actor_id = content_id(
            "actor", {"actor": "workbench-local-service-v3"}
        )
        self.service_instance_id = _operational("service-instance")
        self.physical_lease_provider_id = physical_leases.provider_id
        self.maximum_pending_jobs = maximum_pending_jobs
        self._physical_leases = physical_leases
        self._query_slots = threading.BoundedSemaphore(maximum_workers)
        self._job_admission_slots = threading.BoundedSemaphore(
            maximum_pending_jobs
        )
        self._registration_slots = {
            key: threading.BoundedSemaphore(value.maximum_concurrency)
            for key, value in index.items()
        }
        self._writer_lock_path = root / "service-writer.lock"
        self._writer_operation_lock = threading.RLock()
        try:
            self._release_instance_lease = physical_leases.acquire_instance(
                self._writer_lock_path
            )
            _require(
                callable(self._release_instance_lease),
                "service.invalid-physical-lease-provider",
                "Host Adapter instance lease did not return a release port",
            )
        except ServiceV3Error:
            raise
        except Exception as exc:
            raise ServiceV3Error(
                "writer-busy",
                "another service instance owns the exact durable store",
                retryable=True,
            ) from exc
        self._executor = ThreadPoolExecutor(
            max_workers=maximum_workers,
            thread_name_prefix="workbench-service-v3",
        )
        self._closed = False
        try:
            if recover:
                self.recovered_job_ids = self.store.recover_incomplete(
                    actor_id=self.actor_id
                )
            else:
                self.recovered_job_ids = ()
        except Exception:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._release_instance_lease()
            self._release_instance_lease = lambda: None
            raise

    def close(self, *, wait: bool = True) -> None:
        with self._activity_condition:
            already_closing = self._closed
            self._closed = True
        if not already_closing:
            if wait:
                self._finish_close()
            else:
                threading.Thread(target=self._finish_close, name="workbench-service-close", daemon=False).start()
        if wait:
            self._close_complete.wait()

    def _finish_close(self) -> None:
        try:
            with self._activity_condition:
                self._activity_condition.wait_for(lambda: self._active_dispatches == 0)
            self._executor.shutdown(wait=True, cancel_futures=False)
        finally:
            self._release_instance_lease()
            self._release_instance_lease = lambda: None
            self._package_activity.close()
            self._close_complete.set()

    def _check_environment(self) -> None:
        try:
            self._package_activity.check()
        except ModuleError as exc:
            raise ServiceV3Error("service.environment-changed", str(exc)) from exc

    def __enter__(self) -> "ServiceRuntimeV3":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @contextmanager
    def _writer_lease(self):
        with self._writer_operation_lock:
            yield

    @contextmanager
    def _direct_registration_lease(
        self, key: tuple[str, str], *, query: bool
    ):
        registration_slot = self._registration_slots[key]
        if not registration_slot.acquire(blocking=False):
            raise ServiceV3Error(
                "backpressure",
                "capability concurrency is at its declared bound",
                retryable=True,
            )
        query_acquired = False
        try:
            if query:
                query_acquired = self._query_slots.acquire(blocking=False)
                if not query_acquired:
                    raise ServiceV3Error(
                        "backpressure",
                        "bounded service query leases are exhausted",
                        retryable=True,
                    )
            yield
        finally:
            if query_acquired:
                self._query_slots.release()
            registration_slot.release()

    def capabilities(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "asynchronous": item.asynchronous,
                "capability_id": item.capability_id,
                "capability_version": item.capability_version,
                "handler_id": item.handler_id,
                "implementation_id": item.implementation_id,
                "context_binding": item.context_binding,
                "input_binding": item.input_binding,
                "maximum_concurrency": item.maximum_concurrency,
                "method": item.method,
                "mutation_boundary": item.mutation_boundary,
            }
            for item in sorted(
                self.registrations.values(),
                key=lambda value: (
                    value.capability_id.encode(), value.method.encode()
                ),
            )
        )

    def _run_job(
        self,
        registration: ServiceHandlerRegistration,
        job_id: str,
        arguments: Mapping[str, Any],
    ) -> None:
        context_ref_id, input_binding_id = self.store.job_binding(job_id)
        context = ServiceExecutionContext(
            self.store,
            job_id,
            actor_id=self.actor_id,
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
            environment_check=self._check_environment,
        )
        slot = self._registration_slots[
            (registration.capability_id, registration.method)
        ]
        try:
            self.store.start_job(job_id, actor_id=self.actor_id)
            context.checkpoint("before.owner-handler")
            with slot:
                if registration.mutation_boundary == "none":
                    with self._query_slots:
                        result = registration.handler(context, arguments)
                else:
                    with self._writer_lease():
                        context.checkpoint("after.writer-lease")
                        result = registration.handler(context, arguments)
            result = _validate_handler_value(
                registration.result_validator,
                result,
                code="service.invalid-handler-result",
                label="owner handler result",
            )
            context.checkpoint("after.owner-handler")
            self.store.terminal(
                job_id,
                outcome="succeeded",
                result=result,
                actor_id=self.actor_id,
            )
        except ServiceCancelled:
            self.store.terminal(
                job_id,
                outcome="cancelled-before-mutation",
                actor_id=self.actor_id,
            )
        except Exception as exc:
            state = self.store.handle(job_id).mutation_state
            self.store.terminal(
                job_id,
                outcome=(
                    "indeterminate"
                    if state == "external-mutation-indeterminate"
                    else "failed"
                ),
                failure={
                    "code": getattr(exc, "code", "internal-failure"),
                    "message": str(exc)[:1024],
                    "type": type(exc).__name__,
                },
                actor_id=self.actor_id,
            )
        finally:
            self._job_admission_slots.release()

    def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        with self._activity_condition:
            _require(not self._closed, "service.closed", "service no longer admits requests")
            self._active_dispatches += 1
        try:
            self._check_environment()
            result = self._owner_context.copy().run(self._dispatch, request)
            self._check_environment()
            return result
        finally:
            with self._activity_condition:
                self._active_dispatches -= 1
                self._activity_condition.notify_all()

    def _dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        _require(
            type(request) is dict,
            "invalid-request",
            "service request must be an ordinary object",
        )
        required = {
            "arguments",
            "capability_id",
            "context_ref_id",
            "idempotency_key",
            "input_binding_id",
            "method",
            "request_id",
        }
        _require(
            set(request) == required,
            "invalid-request",
            "service request fields are not closed",
        )
        method = request["method"]
        if method == "service/health":
            return {
                "outcome": "succeeded",
                "result": {
                    "service_instance_id": self.service_instance_id,
                    "state": "ready",
                },
            }
        key = (request["capability_id"], method)
        registration = self.registrations.get(key)
        _require(
            registration is not None,
            "unavailable-capability",
            "no exact capability handler is registered for this method",
        )
        _require(
            type(request["arguments"]) is dict
            and type(request["request_id"]) is str,
            "invalid-request",
            "request arguments or request ID are invalid",
        )
        context_ref_id = request["context_ref_id"]
        input_binding_id = request["input_binding_id"]
        context_valid = (
            registration.context_binding == "none"
            and context_ref_id is None
        ) or (
            registration.context_binding == "required"
            and type(context_ref_id) is str
        )
        input_valid = (
            registration.input_binding == "none"
            and input_binding_id is None
        ) or (
            registration.input_binding == "required"
            and type(input_binding_id) is str
        )
        _require(
            context_valid and input_valid,
            "invalid-request",
            "request context/input binding differs from the handler contract",
        )
        if context_ref_id is not None and input_binding_id is not None:
            self.store.context_bytes(context_ref_id, input_binding_id)
        else:
            _require(
                context_ref_id is None and input_binding_id is None,
                "invalid-request",
                "partial context/input bindings are not executable",
            )
        arguments = _validate_handler_value(
            registration.request_validator,
            request["arguments"],
            code="invalid-request",
            label="owner handler request",
        )
        if not registration.asynchronous:
            context = ServiceExecutionContext(
                self.store,
                None,
                actor_id=self.actor_id,
                context_ref_id=request["context_ref_id"],
                input_binding_id=request["input_binding_id"],
                environment_check=self._check_environment,
            )
            with self._direct_registration_lease(
                key, query=registration.mutation_boundary == "none"
            ):
                if registration.mutation_boundary == "none":
                    result = registration.handler(context, arguments)
                elif registration.method == "job/cancel":
                    # Cancellation must be able to signal a job while that job
                    # owns the graph writer lease.  Its own append/CAS is
                    # serialized by the target job's physical record lock.
                    result = registration.handler(context, arguments)
                else:
                    with self._writer_lease():
                        result = registration.handler(context, arguments)
            result = _validate_handler_value(
                registration.result_validator,
                result,
                code="service.invalid-handler-result",
                label="owner handler result",
            )
            return {"outcome": "succeeded", "result": result}
        admitted = self._job_admission_slots.acquire(blocking=False)
        if not admitted:
            existing = self.store.find_idempotent_job(
                registration,
                context_ref_id=request["context_ref_id"],
                input_binding_id=request["input_binding_id"],
                idempotency_key=request["idempotency_key"],
            )
            if existing is not None:
                return {"outcome": "accepted", "job": existing.to_dict()}
            raise ServiceV3Error(
                "backpressure",
                "durable job admission is at its declared bound",
                retryable=True,
            )
        try:
            handle, created = self.store.create_job(
                registration,
                context_ref_id=request["context_ref_id"],
                input_binding_id=request["input_binding_id"],
                arguments=arguments,
                idempotency_key=request["idempotency_key"],
                actor_id=self.actor_id,
            )
        except Exception:
            self._job_admission_slots.release()
            raise
        if not created:
            self._job_admission_slots.release()
        if created:
            try:
                self._executor.submit(
                    copy_context().run,
                    self._run_job,
                    registration,
                    handle.job_id,
                    arguments,
                )
            except Exception:
                self._job_admission_slots.release()
                raise
        return {"outcome": "accepted", "job": handle.to_dict()}

    def cancel_job(
        self,
        job_id: str,
        *,
        expected_event_id: str,
        expected_event_ordinal: int,
        reason: str,
        idempotency_key: str | None = None,
    ) -> DurableJobHandle:
        with self._activity_condition:
            _require(not self._closed, "service.closed", "service no longer admits requests")
            self._active_dispatches += 1
        try:
            self._check_environment()
            return self.store.cancel(
                job_id,
                expected_event_id=expected_event_id,
                expected_event_ordinal=expected_event_ordinal,
                actor_id=self.actor_id,
                reason=reason,
                idempotency_key=idempotency_key,
            )
        finally:
            with self._activity_condition:
                self._active_dispatches -= 1
                self._activity_condition.notify_all()


class LocalServiceAuthenticator:
    """One private bearer token for Shell-owned local transports."""

    def __init__(self, root: Path) -> None:
        _require(
            isinstance(root, Path)
            and root.is_absolute()
            and not root.is_symlink(),
            "service.invalid-root",
            "credential root must be absolute",
        )
        root.mkdir(parents=True, exist_ok=True)
        _require(
            root.is_dir(),
            "service.invalid-root",
            "credential root must be an ordinary directory",
        )
        try:
            secure_private_path(root, directory=True)
        except OSError as exc:
            raise ServiceV3Error(
                "service.credential-permissions",
                "credential root is accessible outside its owner",
            ) from exc
        self.path = root / "local-service-v3.token"
        if not self.path.exists():
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_hex(32).encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
        _require(
            self.path.is_file() and not self.path.is_symlink(),
            "service.credential-corrupt",
            "local service credential is not an ordinary file",
        )
        try:
            secure_private_path(self.path, directory=False)
        except OSError as exc:
            raise ServiceV3Error(
                "service.credential-permissions",
                "local service credential is accessible outside its owner",
            ) from exc
        self._token = self.path.read_text(encoding="ascii")
        _require(
            re.fullmatch(r"[0-9a-f]{64}", self._token) is not None,
            "service.credential-corrupt",
            "local service credential is invalid",
        )

    @property
    def token(self) -> str:
        return self._token

    def authenticate(self, candidate: str) -> None:
        _require(
            type(candidate) is str
            and hmac.compare_digest(candidate, self._token),
            "service.authentication-failed",
            "local service authentication failed",
        )
