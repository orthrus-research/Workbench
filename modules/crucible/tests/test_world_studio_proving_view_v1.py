from __future__ import annotations

from workbench_crucible_service import DurableJobStore

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[3]
CRUCIBLE_ROOT = ROOT / "modules/crucible"
SHELL_ROOT = ROOT / "modules/workbench-shell"
sys.path[:0] = [
    str(CRUCIBLE_ROOT / "src"),
    str(ROOT / "modules/project-intelligence/src"),
    str(SHELL_ROOT / "src"),
]

from workbench_core.service.runtime import LocalServiceAuthenticator, ServiceRuntimeV3
from workbench_api.service import ServiceV3Error
from workbench_api.canonical import canonical_json_bytes
from workbench_crucible_worldgen import (  # noqa: E402
    WorldgenGraphStore,
    WorldgenStoredQueryService,
    WorldStudioProvingViewError,
    WorldStudioProvingViewHandler,
)
from workbench_shell.service_contract import (  # noqa: E402
    ContextInputApplicabilityRequest,
    validate_message_v3,
    validate_registry_v3,
)
from workbench_core.service.host import (  # noqa: E402
    HostedMethodBinding,
    LocalServiceClientV3,
    LocalServiceEndpointV3,
    OwnerBindingProjection,
    ServiceHostV3,
    posix_service_physical_lease_ports,
)
from workbench_shell.world_studio_registry import (  # noqa: E402
    RESULT_SCHEMA_ID,
    build_world_studio_registry_v3,
    world_studio_service_registration,
)


REGISTRY_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/"
    "component-capability-registry-v3.schema.json"
)
PROTOCOL_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/service-protocol-v3.schema.json"
)
EVIDENCE_ROOT = (
    ROOT / ".workbench/evidence/stage3/worldgen-w01-20260813"
)
ENVELOPE_PATH = (
    ROOT
    / ".workbench/iterations/worldgen/"
    "stage3-w01-proof-a-20260813/artifacts/worldgen-v2/"
    "execution-envelope-v2.json"
)
EXPECTED_PROOF_ID = (
    "worldgen-w01-proof-index:sha256:"
    "2eb19f5d8f03b1bbdf9e9cf48ea9c7ae1c47663f6264f8bcf61badf351a38b92"
)
EXPECTED_GRAPH_ID = (
    "graph-set-revision:sha256:"
    "276ffd93a9db8bcdcb1295e172f4f79d024d0d958ff2fd4278075ecf43e20881"
)
EXPECTED_QUERY_RESULT_ID = (
    "worldgen-query-result:sha256:"
    "31aeb5011e0e7b10dd127d806ed94beb68ad910d64ab4fc2a1e96a216b2b425e"
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


@unittest.skipUnless(
    os.name == "posix"
    and EVIDENCE_ROOT.is_dir()
    and ENVELOPE_PATH.is_file(),
    "exact retained W01 proof and POSIX local endpoint are required",
)
class WorldStudioProvingViewV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = Path(
            tempfile.mkdtemp(prefix="workbench-world-studio-v01-", dir="/tmp")
        ).resolve()
        self.resources = _resources()
        self.proof = _load(EVIDENCE_ROOT / "worldgen-w01-proof-index-v1.json")
        self.action = _load(
            EVIDENCE_ROOT / "actions/read-query-visualize.json"
        )
        self.envelope = _load(ENVELOPE_PATH)
        self.assertEqual(EXPECTED_PROOF_ID, self.proof["id"])
        self.assertEqual(EXPECTED_GRAPH_ID, self.proof["graph_set_revision_id"])

        retained_digest = EXPECTED_GRAPH_ID.rsplit(":", 1)[1]
        retained_store = WorldgenGraphStore(
            EVIDENCE_ROOT
            / "clean-stores"
            / retained_digest
            / "control-1"
        )
        prior_store = WorldgenGraphStore(
            EVIDENCE_ROOT / "clean-stores/control-1"
        )
        prior_id = prior_store.resolve_current()["manifest"][
            "graph_set_revision_id"
        ]
        self.prior_graph_id = prior_id
        self.query_services = {
            EXPECTED_GRAPH_ID: WorldgenStoredQueryService(
                retained_store,
                graph_set_revision_id=EXPECTED_GRAPH_ID,
            ),
            prior_id: WorldgenStoredQueryService(
                prior_store,
                graph_set_revision_id=prior_id,
            ),
        }
        self.handler = WorldStudioProvingViewHandler(
            query_resolver=lambda graph_id, query: self.query_services[
                graph_id
            ].query(query).to_dict(),
            proof_index_resolver=lambda proof_id: (
                deepcopy(self.proof)
                if proof_id == self.proof["id"]
                else (_ for _ in ()).throw(KeyError(proof_id))
            ),
            action_gate_resolver=lambda receipt_id: (
                deepcopy(self.action)
                if receipt_id == self.action["id"]
                else (_ for _ in ()).throw(KeyError(receipt_id))
            ),
        )
        self.bundle = build_world_studio_registry_v3(ROOT.resolve())
        self.registration = world_studio_service_registration(
            self.bundle, self.handler
        )
        context_ref = self.envelope["context_ref"]
        input_binding = self.envelope["input_binding"]
        self.runtime = ServiceRuntimeV3((self.temporary / 'service').resolve(), registrations=(self.registration,), physical_leases=posix_service_physical_lease_ports(), store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda context, binding: context.id == context_ref['id'] and binding.id == input_binding['id']))
        self.runtime.store.register_context(
            canonical_json_bytes(context_ref),
            canonical_json_bytes(input_binding),
        )
        self.authenticator = LocalServiceAuthenticator(
            (self.temporary / "credentials").resolve()
        )

        def owner_projection(message):
            arguments = message["params"]["arguments"]
            return OwnerBindingProjection(
                input_revision_refs=(
                    {
                        "record_kind": "graph-set-revision",
                        "record_id": arguments["graph_set_revision_id"],
                    },
                    {
                        "record_kind": "worldgen-action-gate-receipt",
                        "record_id": arguments["action_gate_receipt_id"],
                    },
                    {
                        "record_kind": "worldgen-w01-proof-index",
                        "record_id": arguments["proof_index_id"],
                    },
                ),
                output_revision_refs=(),
                states={
                    "action_gate": "not-required",
                    "action_gate_decision_ids": [],
                    "completeness": "bounded",
                    "consent": "not-applicable",
                    "context": "available",
                    "continuity": "not-applicable",
                    "evidence": "available",
                    "freshness": "fresh",
                    "implementation": "available",
                    "inputs": "available",
                    "profile_revision_refs": [],
                    "profile_support": "not-applicable",
                    "support_decision_ids": [],
                },
                limitations=(
                    {
                        "code": "experimental-unadmitted",
                        "detail": "The retained W01 profile remains experimental and unadmitted.",
                        "ordinal": 0,
                    },
                ),
            )

        self.host = ServiceHostV3(
            self.runtime,
            authenticator=self.authenticator,
            registry=self.bundle.registry,
            registry_validation_port=lambda candidate: not validate_registry_v3(
                candidate,
                self.resources,
                REGISTRY_SCHEMA_ID,
            ),
            method_bindings=(
                HostedMethodBinding(
                    self.registration,
                    RESULT_SCHEMA_ID,
                    owner_projection,
                ),
            ),
        )

    def tearDown(self) -> None:
        self.runtime.close()
        shutil.rmtree(self.temporary, ignore_errors=True)

    def _arguments(self, operation: str):
        result = {
            "action_gate_receipt_id": self.action["id"],
            "comparison_graph_set_revision_id": None,
            "comparison_query": None,
            "context_ref_id": self.action["context_ref_id"],
            "format": "workbench-world-studio-proving-request-v1",
            "graph_set_revision_id": EXPECTED_GRAPH_ID,
            "input_binding_id": self.action["input_binding_id"],
            "operation": operation,
            "proof_index_id": EXPECTED_PROOF_ID,
            "query": {"query": "known-absence"},
            "schema_version": 1,
        }
        if operation == "diff":
            result["comparison_graph_set_revision_id"] = self.prior_graph_id
            result["comparison_query"] = {"query": "known-absence"}
        return result

    def _direct(self, operation: str):
        return self.runtime.dispatch(
            {
                "arguments": self._arguments(operation),
                "capability_id": self.registration.capability_id,
                "context_ref_id": self.action["context_ref_id"],
                "idempotency_key": None,
                "input_binding_id": self.action["input_binding_id"],
                "method": "graph/query",
                "request_id": f"request.direct.{operation}",
            }
        )["result"]

    def _initialize(self):
        return {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "service/initialize",
            "params": {
                "client": {
                    "client_id": "workbench.world-studio-v01-test",
                    "kind": "test",
                    "version": "1.0.0",
                },
                "maximum_frame_bytes": 4194304,
                "protocol_version": {"major": 3, "minor": 0},
                "request_id": "request-v3:00000000000000000000000000000001",
                "required_capability_ids": [
                    self.registration.capability_id
                ],
                "required_features": ["contexts"],
                "transport": "local-endpoint",
            },
        }

    def _hosted(self, identifier: int, operation: str):
        return {
            "id": identifier,
            "jsonrpc": "2.0",
            "method": "graph/query",
            "params": {
                "arguments": self._arguments(operation),
                "request": {
                    "capability_id": self.registration.capability_id,
                    "capability_version": self.registration.capability_version,
                    "commit": None,
                    "context_ref_id": self.action["context_ref_id"],
                    "deadline": None,
                    "idempotency_key": None,
                    "input_binding_id": self.action["input_binding_id"],
                    "intent": f"world-studio.{operation}",
                    "operation_class": "inspect",
                    "protocol_version": {"major": 3, "minor": 0},
                    "request_id": f"request-v3:{identifier:032x}",
                    "resource_budgets": [],
                },
            },
        }

    def test_registry_is_source_bound_recursively_closed_and_stable(self) -> None:
        rebuilt = build_world_studio_registry_v3(ROOT.resolve())
        self.assertEqual(
            canonical_json_bytes(self.bundle.registry),
            canonical_json_bytes(rebuilt.registry),
        )
        self.assertEqual(
            (),
            validate_registry_v3(
                self.bundle.registry,
                self.resources,
                REGISTRY_SCHEMA_ID,
            ),
        )
        self.assertEqual(2, self.bundle.registry["registry_generation"])

    def test_retained_query_diff_and_visualization_match_local_host(self) -> None:
        direct = {
            operation: self._direct(operation)
            for operation in ("query", "diff", "visualization")
        }
        endpoint_path = self.temporary / "world-studio.sock"
        requests = [
            self._initialize(),
            self._hosted(2, "query"),
            self._hosted(3, "diff"),
            self._hosted(4, "visualization"),
        ]
        with LocalServiceEndpointV3(self.host, endpoint_path=endpoint_path):
            client = LocalServiceClientV3(
                endpoint_path, self.authenticator.token
            )
            responses = client.exchange(tuple(requests))

        for offset, operation in enumerate(
            ("query", "diff", "visualization"), start=1
        ):
            self.assertEqual(
                canonical_json_bytes(direct[operation]),
                canonical_json_bytes(
                    responses[offset]["result"]["outcome"]["value"]
                ),
            )
            self.assertEqual(
                EXPECTED_QUERY_RESULT_ID,
                direct[operation]["query_result"]["query_result_id"],
            )
            self.assertEqual(EXPECTED_PROOF_ID, direct[operation]["proof_index_id"])
            self.assertEqual(
                self.action["id"],
                direct[operation]["action_gate"]["receipt_id"],
            )
            self.assertEqual(
                self.action["context_ref_id"],
                direct[operation]["authority_inputs"]["context_ref_id"],
            )
        self.assertTrue(direct["diff"]["diff"]["added_row_ids"])
        self.assertTrue(direct["diff"]["diff"]["removed_row_ids"])
        self.assertTrue(direct["visualization"]["presentation"]["groups"])
        self.assertEqual(
            0,
            sum(
                service.raw_archive_open_count
                for service in self.query_services.values()
            ),
        )

        stream = []
        for request, response in zip(requests, responses, strict=True):
            stream.extend((request, response))

        def context_port(request: ContextInputApplicabilityRequest) -> bool:
            return (
                request.context_ref_id == self.action["context_ref_id"]
                and request.input_binding_id == self.action["input_binding_id"]
                and request.purpose in {"request", "response"}
            )

        for index in range(len(stream)):
            self.assertEqual(
                (),
                validate_message_v3(
                    index,
                    stream,
                    self.bundle.registry,
                    self.resources,
                    PROTOCOL_SCHEMA_ID,
                    context_input_validator=context_port,
                ),
                f"V3 stream diagnostic at index {index}",
            )

    def test_drift_and_open_requests_fail_closed(self) -> None:
        original = deepcopy(self.action)
        self.action["policy_sha256"] = "0" * 64
        with self.assertRaises(WorldStudioProvingViewError):
            self._direct("query")
        self.action = original

        opened = self._arguments("query")
        opened["caller_answer"] = "trust me"
        with self.assertRaises(ServiceV3Error) as rejected:
            self.runtime.dispatch(
                {
                    "arguments": opened,
                    "capability_id": self.registration.capability_id,
                    "context_ref_id": self.action["context_ref_id"],
                    "idempotency_key": None,
                    "input_binding_id": self.action["input_binding_id"],
                    "method": "graph/query",
                    "request_id": "request.open",
                }
            )
        self.assertEqual("invalid-request", rejected.exception.code)


if __name__ == "__main__":
    unittest.main()
