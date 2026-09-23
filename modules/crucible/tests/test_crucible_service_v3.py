from __future__ import annotations

from workbench_crucible_service import DurableJobStore

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_jobs.synthetic import (  # noqa: E402
    build_synthetic_job_publication,
)
from workbench_crucible_service import ContextListHandler, JobCancellationHandler, JobEventPageHandler, JobSubscriptionHandler, ServiceCapabilitiesHandler, load_job_cancellation_plan, seal_job_cancellation_plan, validate_job_cancellation_arguments, validate_job_handle_result, validate_job_event_page_arguments, validate_job_event_page_result, validate_job_subscription_arguments, validate_job_subscription_result, validate_context_list_result, validate_empty_service_arguments, validate_service_capabilities_result
from workbench_core.service.runtime import LocalServiceAuthenticator, ServiceRuntimeV3
from workbench_api.service import ServiceHandlerRegistration, ServicePhysicalLeasePorts, ServiceV3Error


def _id(kind: str, value: str) -> str:
    return f"{kind}:sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _physical_leases() -> ServicePhysicalLeasePorts:
    def acquire_instance(path: Path):
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise ServiceV3Error(
                "writer-busy", "test service store is already leased"
            ) from exc

        def release() -> None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        return release

    @contextmanager
    def exclusive(path: Path):
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    return ServicePhysicalLeasePorts("test.posix-flock:v1", acquire_instance, exclusive)


class CrucibleServiceV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = Path(
            tempfile.mkdtemp(prefix="crucible-service-v3-", dir="/tmp")
        )
        os.chmod(self.temporary, 0o700)
        publication = build_synthetic_job_publication()
        self.context = publication.context_ref

        self.binding = publication.input_binding
        self.release = threading.Event()
        self.started: dict[str, threading.Event] = {
            "before": threading.Event(),
            "after": threading.Event(),
        }
        self.active_handlers = 0
        self.maximum_active_handlers = 0
        self.active_lock = threading.Lock()

        def handler(context, arguments):
            mode = arguments["mode"]
            with self.active_lock:
                self.active_handlers += 1
                self.maximum_active_handlers = max(
                    self.maximum_active_handlers, self.active_handlers
                )
            try:
                context.progress("execute", 0, 1, message="owner handler started")
                if mode == "after":
                    context.set_mutation_state("immutable-output-published")
                self.started.setdefault(mode, threading.Event()).set()
                while not self.release.wait(0.01):
                    context.checkpoint("owner.wait")
                context.checkpoint("owner.release")
                context.progress(
                    "execute", 1, 1, message="owner handler completed"
                )
                return {"mode": mode, "value": arguments.get("value")}
            finally:
                with self.active_lock:
                    self.active_handlers -= 1

        self.registration = ServiceHandlerRegistration(
            method="graph/materialize",
            capability_id=_id("capability", "service-v3-materialize"),
            capability_version="3.0.0",
            handler_id=_id("handler", "service-v3-materialize"),
            implementation_id=_id(
                "implementation", "service-v3-materialize"
            ),
            mutation_boundary="reference-update",
            asynchronous=True,
            maximum_concurrency=1,
            handler=handler,
        )

    def test_package_import_does_not_eagerly_load_service_runtime(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, sys, workbench_crucible_service; "
                    "print(json.dumps(sorted(name for name in sys.modules "
                    "if name.startswith('workbench_crucible_service.'))))"
                ),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("[]\n", result.stdout)

    def tearDown(self) -> None:
        self.release.set()
        shutil.rmtree(self.temporary, ignore_errors=True)

    def _runtime(self, *, recover=True, maximum_pending_jobs=64):
        runtime = ServiceRuntimeV3(self.temporary.resolve(), registrations=(self.registration,), physical_leases=_physical_leases(), maximum_pending_jobs=maximum_pending_jobs, recover=recover, store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
        runtime.store.register_context(
            self.context.canonical_bytes,
            self.binding.canonical_bytes,
        )
        return runtime

    def _request(self, mode: str, key: str):
        return {
            "arguments": {"mode": mode, "value": key},
            "capability_id": self.registration.capability_id,
            "context_ref_id": self.context.id,
            "idempotency_key": key,
            "input_binding_id": self.binding.id,
            "method": self.registration.method,
            "request_id": f"request.{key}",
        }

    def test_portable_storage_and_legacy_reopen_preserve_logical_ids(self) -> None:
        with self._runtime(recover=False) as runtime:
            store = runtime.store
            context_root = store._context_root(self.context.id, self.binding.id)
            self.assertRegex(context_root.name, r"^[0-9a-f]{64}$")
            self.assertEqual(store.contexts, context_root.parent)
            self.assertEqual(((self.context.id, self.binding.id),), store.list_contexts())
            queued, _ = store.create_job(
                self.registration, context_ref_id=self.context.id,
                input_binding_id=self.binding.id, arguments={"mode": "before"},
                idempotency_key="portable", actor_id=runtime.actor_id,
            )
            job_root = store._job_root(queued.job_id)
            self.assertRegex(job_root.name, r"^[0-9a-f]{32}$")
            self.assertEqual(queued.job_id, store.list_jobs()[0].job_id)
            legacy_context = store.contexts / self.context.id / self.binding.id
            legacy_context.parent.mkdir()
            context_root.rename(legacy_context)
            job_root.rename(store.jobs / queued.job_id)
        with self._runtime() as reopened:
            self.assertIn(queued.job_id, reopened.recovered_job_ids)
            self.assertEqual(((self.context.id, self.binding.id),), reopened.store.list_contexts())
            self.assertEqual(queued.job_id, reopened.store.list_jobs()[0].job_id)
            self.assertEqual(
                (self.context.canonical_bytes, self.binding.canonical_bytes),
                reopened.store.context_bytes(self.context.id, self.binding.id),
            )
            reopened.store.validate_complete_job(queued.job_id)
            context_root.mkdir()
            with self.assertRaises(ServiceV3Error):
                reopened.store.context_bytes(self.context.id, self.binding.id)

    @staticmethod
    def _wait_terminal(runtime, job_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            handle = runtime.store.handle(job_id)
            if handle.terminal_seal_id is not None:
                return handle
            time.sleep(0.01)
        raise AssertionError("job did not reach a terminal seal")

    def test_durable_cancel_reattach_subscription_and_recovery(self) -> None:
        runtime = self._runtime()
        try:
            accepted = runtime.dispatch(self._request("before", "before"))
            job_id = accepted["job"]["job_id"]
            duplicate = runtime.dispatch(self._request("before", "before"))
            self.assertEqual(job_id, duplicate["job"]["job_id"])
            self.assertTrue(self.started["before"].wait(5))
            head = runtime.store.handle(job_id)
            runtime.cancel_job(
                job_id,
                expected_event_id=head.latest_event_id,
                expected_event_ordinal=head.latest_event_ordinal,
                reason="test cancellation before mutation",
            )
            terminal = self._wait_terminal(runtime, job_id)
            self.assertEqual("cancelled-before-mutation", terminal.terminal_outcome)
            runtime.store.validate_complete_job(job_id)
            page = runtime.store.subscription_page(
                job_id, after_ordinal=-1, maximum_events=2
            )
            self.assertEqual(2, len(page.events))
            self.assertTrue(page.has_more)
            with self.assertRaises(ServiceV3Error) as backpressure:
                runtime.store.subscription_page(
                    job_id,
                    after_ordinal=-1,
                    maximum_events=1,
                    fail_on_backpressure=True,
                )
            self.assertEqual("backpressure", backpressure.exception.code)
        finally:
            runtime.close()

        reopened = self._runtime()
        try:
            duplicate = reopened.dispatch(self._request("before", "before"))
            self.assertEqual(job_id, duplicate["job"]["job_id"])
            self.assertEqual(
                "cancelled-before-mutation",
                reopened.store.handle(job_id).terminal_outcome,
            )

            queued, created = reopened.store.create_job(
                self.registration,
                context_ref_id=self.context.id,
                input_binding_id=self.binding.id,
                arguments={"mode": "before", "value": "recover"},
                idempotency_key="recover",
                actor_id=reopened.actor_id,
            )
            self.assertTrue(created)
            queued_id = queued.job_id
        finally:
            reopened.close()

        recovered = self._runtime()
        try:
            self.assertIn(queued_id, recovered.recovered_job_ids)
            handle = recovered.store.handle(queued_id)
            self.assertEqual("failed", handle.terminal_outcome)
            self.assertEqual("not-started", handle.mutation_state)
            recovered.store.validate_complete_job(queued_id)
        finally:
            recovered.close()

    def test_cancellation_after_mutation_and_local_authentication(self) -> None:
        runtime = self._runtime()
        try:
            accepted = runtime.dispatch(self._request("after", "after"))
            job_id = accepted["job"]["job_id"]
            self.assertTrue(self.started["after"].wait(5))
            head = runtime.store.handle(job_id)
            self.assertEqual("immutable-output-published", head.mutation_state)
            runtime.cancel_job(
                job_id,
                expected_event_id=head.latest_event_id,
                expected_event_ordinal=head.latest_event_ordinal,
                reason="test cancellation after mutation",
            )
            terminal = self._wait_terminal(runtime, job_id)
            self.assertEqual("cancelled-after-mutation", terminal.terminal_outcome)
            self.assertEqual("immutable-output-published", terminal.mutation_state)
        finally:
            runtime.close()

        authenticator = LocalServiceAuthenticator(
            (self.temporary / "credentials").resolve()
        )
        authenticator.authenticate(authenticator.token)
        with self.assertRaises(ServiceV3Error) as rejected:
            authenticator.authenticate("0" * 64)
        self.assertEqual("service.authentication-failed", rejected.exception.code)
        self.assertEqual(
            0,
            authenticator.path.stat().st_mode & 0o077,
        )

    def test_cancellation_retains_each_commit_boundary_mutation_state(self) -> None:
        states = {
            "temporary": "temporary-residue",
            "immutable": "immutable-output-published",
            "committed": "reference-committed",
        }
        started = {key: threading.Event() for key in states}
        releases = {key: threading.Event() for key in states}

        def handler(context, arguments):
            mode = arguments["mode"]
            if mode == "committed":
                context.set_mutation_state("immutable-output-published")
            context.set_mutation_state(states[mode])
            started[mode].set()
            self.assertTrue(releases[mode].wait(5))
            context.checkpoint(f"mutation-boundary.{mode}")
            return {"mode": mode}

        registration = ServiceHandlerRegistration(
            method="graph/materialize",
            capability_id=_id("capability", "mutation-boundary-matrix"),
            capability_version="1.0.0",
            handler_id=_id("handler", "mutation-boundary-matrix"),
            implementation_id=_id(
                "implementation", "mutation-boundary-matrix"
            ),
            mutation_boundary="reference-update",
            asynchronous=True,
            maximum_concurrency=1,
            handler=handler,
        )
        runtime = ServiceRuntimeV3(self.temporary.resolve(), registrations=(registration,), physical_leases=_physical_leases(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
        runtime.store.register_context(
            self.context.canonical_bytes,
            self.binding.canonical_bytes,
        )
        try:
            expectations = {
                "temporary": "cancelled-before-mutation",
                "immutable": "cancelled-after-mutation",
                "committed": "cancelled-after-mutation",
            }
            for mode, expected_outcome in expectations.items():
                with self.subTest(mode=mode):
                    accepted = runtime.dispatch(
                        {
                            "arguments": {"mode": mode},
                            "capability_id": registration.capability_id,
                            "context_ref_id": self.context.id,
                            "idempotency_key": f"mutation-boundary-{mode}",
                            "input_binding_id": self.binding.id,
                            "method": registration.method,
                            "request_id": f"mutation-boundary-{mode}",
                        }
                    )
                    job_id = accepted["job"]["job_id"]
                    self.assertTrue(started[mode].wait(5))
                    head = runtime.store.handle(job_id)
                    self.assertEqual(states[mode], head.mutation_state)
                    runtime.cancel_job(
                        job_id,
                        expected_event_id=head.latest_event_id,
                        expected_event_ordinal=head.latest_event_ordinal,
                        reason=f"cancel at {mode} commit boundary",
                    )
                    releases[mode].set()
                    terminal = self._wait_terminal(runtime, job_id)
                    self.assertEqual(expected_outcome, terminal.terminal_outcome)
                    self.assertEqual(states[mode], terminal.mutation_state)
                    runtime.store.validate_complete_job(job_id)
        finally:
            for event in releases.values():
                event.set()
            runtime.close()

    def test_bounded_job_admission_preserves_idempotent_reattachment(self) -> None:
        runtime = self._runtime(maximum_pending_jobs=1)
        try:
            accepted = runtime.dispatch(self._request("before", "bounded-one"))
            self.assertTrue(self.started["before"].wait(5))
            replay = runtime.dispatch(self._request("before", "bounded-one"))
            self.assertEqual(
                accepted["job"]["job_id"], replay["job"]["job_id"]
            )
            with self.assertRaises(ServiceV3Error) as rejected:
                runtime.dispatch(self._request("before", "bounded-two"))
            self.assertEqual("backpressure", rejected.exception.code)
            self.assertTrue(rejected.exception.retryable)
            self.release.set()
            self._wait_terminal(runtime, accepted["job"]["job_id"])
            deadline = time.monotonic() + 5
            while not runtime._job_admission_slots.acquire(blocking=False):
                if time.monotonic() >= deadline:
                    self.fail("completed durable job did not release admission")
                time.sleep(0.01)
            runtime._job_admission_slots.release()
            next_job = runtime.dispatch(
                self._request("before", "bounded-after-release")
            )
            self._wait_terminal(runtime, next_job["job"]["job_id"])
        finally:
            runtime.close()

    def test_context_free_discovery_uses_registered_handlers(self) -> None:
        capabilities_id = _id("capability", "service-v3-capabilities")
        context_list_id = _id("capability", "service-v3-context-list")
        registry_id = _id(
            "component-capability-registry", "service-v3-registry"
        )
        capabilities_registration = ServiceHandlerRegistration(
            method="service/capabilities",
            capability_id=capabilities_id,
            capability_version="1.0.0",
            handler_id=_id("handler", "service-v3-capabilities"),
            implementation_id=_id(
                "implementation", "service-v3-capabilities"
            ),
            mutation_boundary="none",
            asynchronous=False,
            maximum_concurrency=2,
            handler=ServiceCapabilitiesHandler(
                registry_id, tuple(sorted((capabilities_id, context_list_id)))
            ),
            request_validator=validate_empty_service_arguments,
            result_validator=validate_service_capabilities_result,
            context_binding="none",
            input_binding="none",
        )
        context_list_registration = ServiceHandlerRegistration(
            method="context/list",
            capability_id=context_list_id,
            capability_version="1.0.0",
            handler_id=_id("handler", "service-v3-context-list"),
            implementation_id=_id(
                "implementation", "service-v3-context-list"
            ),
            mutation_boundary="none",
            asynchronous=False,
            maximum_concurrency=2,
            handler=ContextListHandler(),
            request_validator=validate_empty_service_arguments,
            result_validator=validate_context_list_result,
            context_binding="none",
            input_binding="none",
        )
        runtime = ServiceRuntimeV3(self.temporary.resolve(), registrations=(capabilities_registration, context_list_registration), physical_leases=_physical_leases(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
        runtime.store.register_context(
            self.context.canonical_bytes,
            self.binding.canonical_bytes,
        )
        try:
            discovered = runtime.dispatch(
                {
                    "arguments": {},
                    "capability_id": capabilities_id,
                    "context_ref_id": None,
                    "idempotency_key": "discovery",
                    "input_binding_id": None,
                    "method": "service/capabilities",
                    "request_id": "discovery",
                }
            )["result"]
            self.assertEqual(registry_id, discovered["registry_id"])
            self.assertEqual(
                sorted((capabilities_id, context_list_id)),
                discovered["capability_ids"],
            )
            contexts = runtime.dispatch(
                {
                    "arguments": {},
                    "capability_id": context_list_id,
                    "context_ref_id": None,
                    "idempotency_key": "contexts",
                    "input_binding_id": None,
                    "method": "context/list",
                    "request_id": "contexts",
                }
            )["result"]
            self.assertEqual(
                [
                    {
                        "context_ref_id": self.context.id,
                        "input_binding_id": self.binding.id,
                    }
                ],
                contexts["contexts"],
            )
            with self.assertRaises(ServiceV3Error) as rejected:
                runtime.dispatch(
                    {
                        "arguments": {},
                        "capability_id": capabilities_id,
                        "context_ref_id": self.context.id,
                        "idempotency_key": "wrong-context-mode",
                        "input_binding_id": self.binding.id,
                        "method": "service/capabilities",
                        "request_id": "wrong-context-mode",
                    }
                )
            self.assertEqual("invalid-request", rejected.exception.code)
        finally:
            runtime.close()

    def test_plan_bound_job_cancel_handler_is_idempotent_and_head_checked(self) -> None:
        plans: dict[str, bytes] = {}
        cancellation_handler = JobCancellationHandler(plans.__getitem__)
        cancellation_registration = ServiceHandlerRegistration(
            method="job/cancel",
            capability_id=_id("capability", "service-v3-job-cancel"),
            capability_version="1.0.0",
            handler_id=_id("handler", "service-v3-job-cancel"),
            implementation_id=_id(
                "implementation", "service-v3-job-cancel"
            ),
            mutation_boundary="protected-state",
            asynchronous=False,
            maximum_concurrency=1,
            handler=cancellation_handler,
            request_validator=validate_job_cancellation_arguments,
            result_validator=validate_job_handle_result,
        )
        subscription_registration = ServiceHandlerRegistration(
            method="job/subscribe",
            capability_id=_id("capability", "service-v3-job-subscribe"),
            capability_version="1.0.0",
            handler_id=_id("handler", "service-v3-job-subscribe"),
            implementation_id=_id(
                "implementation", "service-v3-job-subscribe"
            ),
            mutation_boundary="none",
            asynchronous=False,
            maximum_concurrency=2,
            handler=JobSubscriptionHandler(),
            request_validator=validate_job_subscription_arguments,
            result_validator=validate_job_subscription_result,
        )
        event_page_registration = ServiceHandlerRegistration(
            method="job/get",
            capability_id=_id("capability", "service-v3-job-event-page"),
            capability_version="1.0.0",
            handler_id=_id("handler", "service-v3-job-event-page"),
            implementation_id=_id(
                "implementation", "service-v3-job-event-page"
            ),
            mutation_boundary="none",
            asynchronous=False,
            maximum_concurrency=2,
            handler=JobEventPageHandler(),
            request_validator=validate_job_event_page_arguments,
            result_validator=validate_job_event_page_result,
        )
        runtime = ServiceRuntimeV3(self.temporary.resolve(), registrations=(self.registration, cancellation_registration, subscription_registration, event_page_registration), physical_leases=_physical_leases(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
        runtime.store.register_context(
            self.context.canonical_bytes,
            self.binding.canonical_bytes,
        )
        try:
            accepted = runtime.dispatch(self._request("before", "hosted-cancel"))
            job_id = accepted["job"]["job_id"]
            self.assertTrue(self.started["before"].wait(5))
            head = runtime.store.handle(job_id)

            stale_plan = seal_job_cancellation_plan(
                context_ref_id=self.context.id,
                input_binding_id=self.binding.id,
                job_id=job_id,
                expected_event_id=_id("job-event", "stale-head"),
                expected_event_ordinal=head.latest_event_ordinal,
                reason="reject stale cancellation head",
            )
            plans[stale_plan.id] = stale_plan.canonical_bytes
            stale_arguments = {
                "expected_event_id": stale_plan.expected_event_id,
                "expected_event_ordinal": stale_plan.expected_event_ordinal,
                "idempotency_key": "idempotency-v3:" + "1" * 32,
                "job_id": job_id,
                "plan_id": stale_plan.id,
                "reason": stale_plan.reason,
            }
            with self.assertRaises(ServiceV3Error) as rejected:
                runtime.dispatch(
                    {
                        "arguments": stale_arguments,
                        "capability_id": cancellation_registration.capability_id,
                        "context_ref_id": self.context.id,
                        "idempotency_key": stale_arguments["idempotency_key"],
                        "input_binding_id": self.binding.id,
                        "method": "job/cancel",
                        "request_id": "request.hosted-cancel-stale",
                    }
                )
            self.assertEqual("compare-and-swap-lost", rejected.exception.code)

            plan = seal_job_cancellation_plan(
                context_ref_id=self.context.id,
                input_binding_id=self.binding.id,
                job_id=job_id,
                expected_event_id=head.latest_event_id,
                expected_event_ordinal=head.latest_event_ordinal,
                reason="cancel exact durable job before mutation",
            )
            self.assertEqual(
                plan, load_job_cancellation_plan(plan.canonical_bytes)
            )
            plans[plan.id] = plan.canonical_bytes
            arguments = {
                "expected_event_id": plan.expected_event_id,
                "expected_event_ordinal": plan.expected_event_ordinal,
                "idempotency_key": "idempotency-v3:" + "2" * 32,
                "job_id": job_id,
                "plan_id": plan.id,
                "reason": plan.reason,
            }
            request = {
                "arguments": arguments,
                "capability_id": cancellation_registration.capability_id,
                "context_ref_id": self.context.id,
                "idempotency_key": arguments["idempotency_key"],
                "input_binding_id": self.binding.id,
                "method": "job/cancel",
                "request_id": "request.hosted-cancel",
            }
            first = runtime.dispatch(request)
            request["request_id"] = "request.hosted-cancel-retry"
            second = runtime.dispatch(request)
            self.assertEqual(job_id, first["result"]["job_id"])
            self.assertEqual(job_id, second["result"]["job_id"])
            self.assertTrue(validate_job_handle_result(first["result"]))

            self.release.set()
            terminal = self._wait_terminal(runtime, job_id)
            self.assertEqual(
                "cancelled-before-mutation", terminal.terminal_outcome
            )
            runtime.store.validate_complete_job(job_id)
            start_arguments = {
                "delivery": "lossless",
                "event_families": ["job"],
                "job_id": job_id,
                "job_submission_id": terminal.job_submission_id,
                "position": {"kind": "start"},
            }
            subscription = runtime.dispatch(
                {
                    "arguments": start_arguments,
                    "capability_id": subscription_registration.capability_id,
                    "context_ref_id": self.context.id,
                    "idempotency_key": "subscription-start",
                    "input_binding_id": self.binding.id,
                    "method": "job/subscribe",
                    "request_id": "request.subscription-start",
                }
            )["result"]
            self.assertEqual(0, subscription["next_cursor"])
            page_arguments = {
                "job_id": job_id,
                "job_submission_id": terminal.job_submission_id,
                "last_cursor": -1,
                "maximum_events": 2,
                "stream_generation": subscription["stream_generation"],
                "subscription_id": subscription["subscription_id"],
            }
            start_page = runtime.dispatch(
                {
                    "arguments": page_arguments,
                    "capability_id": event_page_registration.capability_id,
                    "context_ref_id": self.context.id,
                    "idempotency_key": "subscription-page-start",
                    "input_binding_id": self.binding.id,
                    "method": "job/get",
                    "request_id": "request.subscription-page-start",
                }
            )["result"]
            self.assertTrue(start_page["has_more"])
            self.assertEqual(2, len(start_page["events"]))
            resume_arguments = {
                **start_arguments,
                "position": {
                    "kind": "resume",
                    "last_cursor": start_page["next_cursor"] - 1,
                    "stream_generation": subscription[
                        "stream_generation"
                    ],
                    "subscription_id": subscription["subscription_id"],
                },
            }
            resume_page = runtime.dispatch(
                {
                    "arguments": resume_arguments,
                    "capability_id": subscription_registration.capability_id,
                    "context_ref_id": self.context.id,
                    "idempotency_key": "subscription-resume",
                    "input_binding_id": self.binding.id,
                    "method": "job/subscribe",
                    "request_id": "request.subscription-resume",
                }
            )["result"]
            self.assertEqual(start_page["next_cursor"], resume_page["next_cursor"])
            tail_arguments = {
                **page_arguments,
                "last_cursor": start_page["next_cursor"] - 1,
                "maximum_events": 64,
            }
            tail_page = runtime.dispatch(
                {
                    "arguments": tail_arguments,
                    "capability_id": event_page_registration.capability_id,
                    "context_ref_id": self.context.id,
                    "idempotency_key": "subscription-page-resume",
                    "input_binding_id": self.binding.id,
                    "method": "job/get",
                    "request_id": "request.subscription-page-resume",
                }
            )["result"]
            self.assertFalse(tail_page["has_more"])
            ordinals = [
                row["event_ordinal"]
                for row in (*start_page["events"], *tail_page["events"])
            ]
            self.assertEqual(list(range(terminal.latest_event_ordinal + 1)), ordinals)
        finally:
            runtime.close()

    def test_one_writer_lease_and_successful_terminal_seals(self) -> None:
        self.release.set()
        runtime = self._runtime()
        try:
            with self.assertRaises(ServiceV3Error) as busy:
                self._runtime()
            self.assertEqual("writer-busy", busy.exception.code)
            first = runtime.dispatch(self._request("success-a", "success-a"))
            second = runtime.dispatch(self._request("success-b", "success-b"))
            first_handle = self._wait_terminal(runtime, first["job"]["job_id"])
            second_handle = self._wait_terminal(runtime, second["job"]["job_id"])
            self.assertEqual("succeeded", first_handle.terminal_outcome)
            self.assertEqual("succeeded", second_handle.terminal_outcome)
            self.assertEqual(1, self.maximum_active_handlers)
            runtime.store.validate_complete_job(first_handle.job_id)
            runtime.store.validate_complete_job(second_handle.job_id)
        finally:
            runtime.close()

    def test_terminal_publication_failpoints_recover_one_immutable_seal(self) -> None:
        checkpoints = (
            "terminal.before-result-object",
            "terminal.after-result-object",
            "terminal.after-terminal-event",
            "terminal.after-terminal-seal",
            "terminal.after-terminal-head",
        )
        for ordinal, checkpoint in enumerate(checkpoints):
            with self.subTest(checkpoint=checkpoint):
                root = (self.temporary / f"terminal-{ordinal}").resolve()

                def fault(actual, *, selected=checkpoint):
                    if actual == selected:
                        raise RuntimeError(f"fault at {selected}")

                runtime = ServiceRuntimeV3(root, registrations=(self.registration,), physical_leases=_physical_leases(), recover=False, store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True, fault_injector=fault))
                runtime.store.register_context(
                    self.context.canonical_bytes,
                    self.binding.canonical_bytes,
                )
                handle, created = runtime.store.create_job(
                    self.registration,
                    context_ref_id=self.context.id,
                    input_binding_id=self.binding.id,
                    arguments={"mode": "before", "value": checkpoint},
                    idempotency_key=f"terminal-{ordinal}",
                    actor_id=runtime.actor_id,
                )
                self.assertTrue(created)
                runtime.store.start_job(handle.job_id, actor_id=runtime.actor_id)
                with self.assertRaisesRegex(RuntimeError, "fault at"):
                    runtime.store.terminal(
                        handle.job_id,
                        outcome="succeeded",
                        result={"checkpoint": checkpoint},
                        actor_id=runtime.actor_id,
                    )
                runtime.close()

                reopened = ServiceRuntimeV3(root, registrations=(self.registration,), physical_leases=_physical_leases(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
                try:
                    final = reopened.store.handle(handle.job_id)
                    expected = (
                        "failed"
                        if checkpoint
                        in {
                            "terminal.before-result-object",
                            "terminal.after-result-object",
                        }
                        else "succeeded"
                    )
                    self.assertEqual(expected, final.terminal_outcome)
                    self.assertIsNotNone(final.terminal_seal_id)
                    reopened.store.validate_complete_job(handle.job_id)
                    events = reopened.store._event_values(handle.job_id)
                    self.assertEqual(
                        1,
                        sum(
                            event["event_type"] == "terminal-ready"
                            for event in events
                        ),
                    )
                finally:
                    reopened.close()


if __name__ == "__main__":
    unittest.main()
