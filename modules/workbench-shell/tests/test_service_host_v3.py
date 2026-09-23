from __future__ import annotations

from workbench_crucible_service import DurableJobStore

import copy
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import unittest

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[3]
SHELL_ROOT = ROOT / "modules/workbench-shell"
CRUCIBLE_ROOT = ROOT / "modules/crucible"
sys.path[:0] = [
    str(CRUCIBLE_ROOT / "src"),
    str(ROOT / "modules/project-intelligence/src"),
    str(SHELL_ROOT / "src"),
]

from workbench_crucible_jobs.synthetic import (  # noqa: E402
    build_synthetic_job_publication,
)
from workbench_core.service.runtime import LocalServiceAuthenticator, ServiceRuntimeV3
from workbench_api.service import ServiceHandlerRegistration
from workbench_core.host_adapter import (  # noqa: E402
    inspect_local_host_adapter_v3,
)
from workbench_shell.service_contract import (  # noqa: E402
    AuthorityAdmissionRequest,
    ContextInputApplicabilityRequest,
    ProfileSupportDecisionRequest,
    validate_message_v3,
    validate_registry_v3,
)
from workbench_core.service.host import (  # noqa: E402
    HostedMethodBinding,
    LocalServiceClientV3,
    LocalServiceEndpointV3,
    OwnerBindingProjection,
    _publish_windows_endpoint,
    _windows_endpoint_value,
    posix_service_physical_lease_ports,
    qualify_posix_service_provider,
    select_service_transport,
    ServiceHostV3,
    ServiceHostV3Error,
    serve_stdio_service_proxy_v3,
)
from workbench_core.service.framing import read_message, write_message


REGISTRY_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/"
    "component-capability-registry-v3.schema.json"
)
PROTOCOL_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/service-protocol-v3.schema.json"
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _resources() -> Registry:
    result = Registry()
    for path in sorted((CRUCIBLE_ROOT / "schemas").glob("*.schema.json")) + sorted(
        (SHELL_ROOT / "schemas").glob("*.schema.json")
    ):
        schema = _load(path)
        if "$id" in schema:
            result = result.with_resource(
                schema["$id"], Resource.from_contents(schema)
            )
    return result


def _owner_admission(request: AuthorityAdmissionRequest) -> bool:
    admission = json.loads(request.admission_canonical_bytes)
    capability = json.loads(request.capability_canonical_bytes)
    return (
        request.projection_kind == "authority-admission"
        and request.role in {"semantic-owner", "profile-owner"}
        and admission["capability_id"] == capability["capability_id"]
        and admission["attestation"]["capability_id"]
        == capability["capability_id"]
        and admission["attestation"]["decision"] == "approved"
    )


class ServiceHostV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = Path(
            tempfile.mkdtemp(prefix="workbench-service-host-v3-", dir="/tmp")
        ).resolve()
        os.chmod(self.temporary, 0o700)
        self.registry = _load(
            SHELL_ROOT
            / "conformance/service-protocol-v3/valid-vectors.json"
        )["registry"]
        self.resources = _resources()

        def schema_validator(schema_id):
            resolved = self.resources.resolver().lookup(schema_id)
            validator = Draft202012Validator(
                resolved.contents,
                registry=self.resources,
                _resolver=resolved.resolver,
            )
            return lambda value: not list(validator.iter_errors(value))
        self.messages = {
            row["case_id"]: row["value"]
            for row in _load(
                SHELL_ROOT
                / "conformance/service-protocol-v3/valid-vectors.json"
            )["messages"]
        }
        self.publication = build_synthetic_job_publication()
        self.release = threading.Event()

        query_descriptor = self._descriptor("atlas.graph.query")
        query_registration = self._registration(query_descriptor["capability_id"])
        query_method = self._method(query_descriptor, "graph/query")
        self.query_registration = ServiceHandlerRegistration(
            method="graph/query",
            capability_id=query_descriptor["capability_id"],
            capability_version=query_descriptor["semantic_version"],
            handler_id=query_registration["handler_id"],
            implementation_id=query_registration["handler_implementation_id"],
            mutation_boundary="none",
            asynchronous=False,
            maximum_concurrency=4,
            handler=lambda _context, arguments: {
                "graph_revision_id": arguments["graph_revision_id"],
                "result_object_id": (
                    "workbench-blob-v2:sha256:"
                    + "ba7816bf8f01cfea414140de5dae2223"
                    "b00361a396177a9cb410ff61f20015ad"
                ),
            },
            request_validator=schema_validator(
                query_method["request_schema_id"]
            ),
            result_validator=schema_validator(query_method["result_schema_id"]),
        )

        runtime_descriptor = self._descriptor("workbench.runtime.start")
        runtime_registration = self._registration(
            runtime_descriptor["capability_id"]
        )
        runtime_method = self._method(runtime_descriptor, "runtime/start")

        def run_handler(context, arguments):
            context.progress("runtime.start", 0, 1)
            self.release.wait(5)
            context.checkpoint("runtime.start.release")
            return {}

        self.runtime_registration = ServiceHandlerRegistration(
            method="runtime/start",
            capability_id=runtime_descriptor["capability_id"],
            capability_version=runtime_descriptor["semantic_version"],
            handler_id=runtime_registration["handler_id"],
            implementation_id=runtime_registration[
                "handler_implementation_id"
            ],
            mutation_boundary="external-side-effect",
            asynchronous=True,
            maximum_concurrency=1,
            handler=run_handler,
            request_validator=schema_validator(
                runtime_method["request_schema_id"]
            ),
            result_validator=schema_validator(
                runtime_method["result_schema_id"]
            ),
        )

        job_descriptor = self._descriptor("workbench.job.get")
        job_registration = self._registration(job_descriptor["capability_id"])
        job_method = self._method(job_descriptor, "job/get")
        self.job_get_registration = ServiceHandlerRegistration(
            method="job/get",
            capability_id=job_descriptor["capability_id"],
            capability_version=job_descriptor["semantic_version"],
            handler_id=job_registration["handler_id"],
            implementation_id=job_registration["handler_implementation_id"],
            mutation_boundary="none",
            asynchronous=False,
            maximum_concurrency=8,
            handler=lambda context, arguments: context.get_job(
                arguments["job_id"]
            ),
            request_validator=schema_validator(
                job_method["request_schema_id"]
            ),
            result_validator=schema_validator(job_method["result_schema_id"]),
        )
        self.runtime = ServiceRuntimeV3((self.temporary / 'service').resolve(), registrations=(self.query_registration, self.runtime_registration, self.job_get_registration), physical_leases=posix_service_physical_lease_ports(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: True))
        self.runtime.store.register_context(
            self.publication.context_ref.canonical_bytes,
            self.publication.input_binding.canonical_bytes,
        )
        self.authenticator = LocalServiceAuthenticator(
            (self.temporary / "credentials").resolve()
        )
        query_states = copy.deepcopy(
            self.messages["graph-query-response"]["result"]["binding"]["states"]
        )
        runtime_states = copy.deepcopy(
            self.messages["runtime-start-accepted-response"]["result"]["binding"]["states"]
        )
        job_states = copy.deepcopy(
            self.messages["service-capabilities-response"]["result"]["binding"]["states"]
        )
        job_states["context"] = "available"
        job_states["inputs"] = "available"
        job_states["freshness"] = "fresh"

        def query_projection(message):
            graph_revision_id = message["params"]["arguments"][
                "graph_revision_id"
            ]
            return OwnerBindingProjection(
                input_revision_refs=(
                    {
                        "record_kind": "graph-revision",
                        "record_id": graph_revision_id,
                    },
                ),
                output_revision_refs=(),
                states=copy.deepcopy(query_states),
            )

        def runtime_projection(_message):
            return OwnerBindingProjection(
                input_revision_refs=(),
                output_revision_refs=(),
                states=copy.deepcopy(runtime_states),
            )

        def job_projection(_message):
            return OwnerBindingProjection(
                input_revision_refs=(),
                output_revision_refs=(),
                states=copy.deepcopy(job_states),
            )

        self.host = ServiceHostV3(
            self.runtime,
            authenticator=self.authenticator,
            registry=self.registry,
            registry_validation_port=lambda candidate: not validate_registry_v3(
                candidate,
                self.resources,
                REGISTRY_SCHEMA_ID,
                admission_validator=_owner_admission,
            ),
            method_bindings=(
                HostedMethodBinding(
                    self.query_registration,
                    query_method["result_schema_id"],
                    query_projection,
                ),
                HostedMethodBinding(
                    self.runtime_registration,
                    runtime_method["result_schema_id"],
                    runtime_projection,
                ),
                HostedMethodBinding(
                    self.job_get_registration,
                    job_method["result_schema_id"],
                    job_projection,
                ),
            ),
        )

    def tearDown(self) -> None:
        self.release.set()
        self.runtime.close()
        shutil.rmtree(self.temporary, ignore_errors=True)

    def _descriptor(self, key):
        return next(
            row
            for row in self.registry["capability_descriptors"]
            if row["capability_key"] == key
        )

    def _registration(self, capability_id):
        return next(
            row
            for row in self.registry["handler_registrations"]
            if row["capability_id"] == capability_id
        )

    @staticmethod
    def _method(descriptor, method):
        return next(
            row
            for row in descriptor["method_bindings"]
            if row["protocol_method"] == method
        )

    def _initialize(self, transport, identifier=1):
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": "service/initialize",
            "params": {
                "protocol_version": {"major": 3, "minor": 0},
                "request_id": f"request-v3:{identifier:032x}",
                "client": {
                    "client_id": "workbench.service-host-test",
                    "kind": "test",
                    "version": "3.0.0",
                },
                "transport": transport,
                "required_capability_ids": [
                    self.query_registration.capability_id
                ],
                "required_features": ["contexts", "durable-jobs"],
                "maximum_frame_bytes": 1048576,
            },
        }

    def _query(self, identifier=2):
        graph_id = "graph-revision:sha256:" + "d" * 64
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": "graph/query",
            "params": {
                "request": {
                    "protocol_version": {"major": 3, "minor": 0},
                    "request_id": f"request-v3:{identifier:032x}",
                    "capability_id": self.query_registration.capability_id,
                    "capability_version": "3.0.0",
                    "context_ref_id": self.publication.context_ref.id,
                    "input_binding_id": self.publication.input_binding.id,
                    "intent": "graph.query",
                    "operation_class": "query",
                    "resource_budgets": [],
                    "deadline": None,
                    "idempotency_key": None,
                    "commit": None,
                },
                "arguments": {
                    "graph_revision_id": graph_id,
                    "query_expression": "category:worldgen node:ore",
                    "category_keys": ["worldgen"],
                },
            },
        }

    def _runtime_start(self, identifier, idempotency):
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": "runtime/start",
            "params": {
                "request": {
                    "protocol_version": {"major": 3, "minor": 0},
                    "request_id": f"request-v3:{identifier:032x}",
                    "capability_id": self.runtime_registration.capability_id,
                    "capability_version": "3.0.0",
                    "context_ref_id": self.publication.context_ref.id,
                    "input_binding_id": self.publication.input_binding.id,
                    "intent": "runtime.start",
                    "operation_class": "disposable-runtime-execute",
                    "resource_budgets": [],
                    "deadline": None,
                    "idempotency_key": idempotency,
                    "commit": None,
                },
                "arguments": {
                    "runtime_plan_id": "runtime-plan:sha256:" + "a" * 64
                },
            },
        }

    def _job_get(self, identifier, job_id):
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "method": "job/get",
            "params": {
                "request": {
                    "protocol_version": {"major": 3, "minor": 0},
                    "request_id": f"request-v3:{identifier:032x}",
                    "capability_id": self.job_get_registration.capability_id,
                    "capability_version": "3.0.0",
                    "context_ref_id": self.publication.context_ref.id,
                    "input_binding_id": self.publication.input_binding.id,
                    "intent": "job.get",
                    "operation_class": "inspect",
                    "resource_budgets": [],
                    "deadline": None,
                    "idempotency_key": None,
                    "commit": None,
                },
                "arguments": {"job_id": job_id},
            },
        }

    def test_embedded_endpoint_and_stdio_share_one_handler_result(self) -> None:
        direct = self.runtime.dispatch(
            {
                "arguments": self._query()["params"]["arguments"],
                "capability_id": self.query_registration.capability_id,
                "context_ref_id": self.publication.context_ref.id,
                "idempotency_key": "direct-query",
                "input_binding_id": self.publication.input_binding.id,
                "method": "graph/query",
                "request_id": "request.direct-query",
            }
        )["result"]

        embedded = self.host.new_session("embedded")
        embedded_initialize = self._initialize("embedded")
        embedded_initialize_response = embedded.handle_message(
            embedded_initialize,
            bearer_token=self.authenticator.token,
        )
        embedded_query = self._query()
        embedded_response = embedded.handle_message(
            embedded_query, bearer_token=self.authenticator.token
        )
        self.assertEqual(
            direct,
            embedded_response["result"]["outcome"]["value"],
        )
        def context_port(request: ContextInputApplicabilityRequest) -> bool:
            return (
                request.context_ref_id == self.publication.context_ref.id
                and request.input_binding_id
                == self.publication.input_binding.id
                and request.purpose in {"request", "response"}
            )

        def support_port(request: ProfileSupportDecisionRequest) -> bool:
            return (
                request.support_state == "tested-supported"
                and len(request.decision_ids) == 1
                and request.projection_kind == "profile-support-decision"
            )

        endpoint_path = self.temporary / "service.sock"
        with LocalServiceEndpointV3(
            self.host, endpoint_path=endpoint_path
        ):
            client = LocalServiceClientV3(
                endpoint_path, self.authenticator.token
            )
            endpoint_responses = client.exchange(
                (self._initialize("local-endpoint"), self._query())
            )
            self.assertEqual(
                direct,
                endpoint_responses[1]["result"]["outcome"]["value"],
            )
            local_stream = [
                self._initialize("local-endpoint"),
                endpoint_responses[0],
                self._query(),
                endpoint_responses[1],
            ]
            for index in range(len(local_stream)):
                self.assertEqual(
                    (),
                    validate_message_v3(
                        index,
                        local_stream,
                        self.registry,
                        self.resources,
                        PROTOCOL_SCHEMA_ID,
                        support_decision_validator=support_port,
                        context_input_validator=context_port,
                    ),
                    f"V3 stream diagnostic at index {index}",
                )
            with self.assertRaises(ServiceHostV3Error):
                LocalServiceClientV3(endpoint_path, "0" * 64).exchange(
                    (self._initialize("local-endpoint"),)
                )

            input_stream = BytesIO()
            write_message(input_stream, self._initialize("stdio"))
            write_message(input_stream, self._query())
            input_stream.seek(0)
            output_stream = BytesIO()
            self.assertEqual(
                0,
                serve_stdio_service_proxy_v3(
                    input_stream,
                    output_stream,
                    StringIO(),
                    client=client,
                ),
            )
            output_stream.seek(0)
            read_message(output_stream)
            stdio_response = read_message(output_stream)
            self.assertEqual(
                direct,
                stdio_response["result"]["outcome"]["value"],
            )

    def test_detached_idempotent_submission_reuses_one_durable_job(self) -> None:
        endpoint_path = self.temporary / "service.sock"
        idempotency = "idempotency-v3:" + "9" * 32
        with LocalServiceEndpointV3(
            self.host, endpoint_path=endpoint_path
        ):
            client = LocalServiceClientV3(
                endpoint_path, self.authenticator.token
            )
            first = client.exchange(
                (
                    self._initialize("local-endpoint", 10),
                    self._runtime_start(11, idempotency),
                )
            )[1]
            second = client.exchange(
                (
                    self._initialize("local-endpoint", 12),
                    self._runtime_start(13, idempotency),
                )
            )[1]
            first_job = first["result"]["outcome"]["job"]
            second_job = second["result"]["outcome"]["job"]
            self.assertEqual(first_job["job_id"], second_job["job_id"])
            self.assertEqual(
                first_job["job_submission_id"],
                second_job["job_submission_id"],
            )
            self.assertEqual(1, len(self.runtime.store.list_jobs()))
            reopened = client.exchange(
                (
                    self._initialize("local-endpoint", 14),
                    self._job_get(15, first_job["job_id"]),
                )
            )[1]
            self.assertEqual(
                first_job["job_id"],
                reopened["result"]["outcome"]["value"]["job_id"],
            )

    def test_host_adapter_selects_only_qualified_transport(self) -> None:
        receipt = inspect_local_host_adapter_v3(scratch_parent=self.temporary)
        fallback = select_service_transport(receipt)
        self.assertEqual("stdio", fallback.mode)
        self.assertIn("process-tree-termination", fallback.missing_capability_ids)
        provider = qualify_posix_service_provider(
            receipt, scratch_root=self.temporary
        )
        hosted = select_service_transport(
            receipt, provider_receipt=provider
        )
        self.assertEqual("local-endpoint", hosted.mode)
        self.assertEqual(provider.receipt_id, hosted.provider_receipt_id)
        self.assertFalse(provider.release_qualified)

    def test_windows_loopback_endpoint_record_is_exact_and_fail_closed(self) -> None:
        endpoint = self.temporary / "windows-endpoint.json"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            _publish_windows_endpoint(endpoint, listener)
            value = json.loads(endpoint.read_text(encoding="utf-8"))
            schema = _load(
                SHELL_ROOT / "schemas/windows-loopback-endpoint-v1.schema.json"
            )
            Draft202012Validator(schema).validate(value)
            self.assertEqual(listener.getsockname(), _windows_endpoint_value(endpoint))

            raw = endpoint.read_bytes()
            endpoint.write_bytes(raw.rstrip() + b" \n")
            with self.assertRaisesRegex(
                ServiceHostV3Error, "fields are invalid"
            ):
                _windows_endpoint_value(endpoint)
        finally:
            listener.close()
            endpoint.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
