from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


REPO_ROOT = Path(__file__).resolve().parents[3]
SHELL_ROOT = REPO_ROOT / "modules" / "workbench-shell"
CRUCIBLE_ROOT = REPO_ROOT / "modules" / "crucible"
REGISTRY_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/component-capability-registry-v3.schema.json"
)
PROTOCOL_SCHEMA_ID = "workbench://schemas/workbench-shell/service-protocol-v3.schema.json"
REGISTRY_SCHEMA_PATH = SHELL_ROOT / "schemas" / "component-capability-registry-v3.schema.json"
PROTOCOL_SCHEMA_PATH = SHELL_ROOT / "schemas" / "service-protocol-v3.schema.json"
VALUE_SCHEMA_PATH = SHELL_ROOT / "schemas" / "service-protocol-v3-conformance-values.schema.json"
VALID_PATH = SHELL_ROOT / "conformance" / "service-protocol-v3" / "valid-vectors.json"
INVALID_PATH = SHELL_ROOT / "conformance" / "service-protocol-v3" / "invalid-vectors.json"

sys.path[:0] = [
    str(CRUCIBLE_ROOT / "src"),
    str(REPO_ROOT / "modules" / "project-intelligence" / "src"),
    str(SHELL_ROOT / "src"),
]
from workbench_shell.service_contract import (  # noqa: E402
    ActionGateDecisionRequest,
    AuthorityAdmissionRequest,
    ContextInputApplicabilityRequest,
    JobProjectionValidationRequest,
    ProfileSupportDecisionRequest,
    _message_semantic_codes,
    _registry_semantic_codes,
    schema_is_recursively_closed,
    validate_message_v3,
    validate_registry_v3,
)


STREAM_CASE_ORDER = (
    "initialize-v3-request",
    "initialize-v3-response",
    "service-capabilities-request",
    "service-capabilities-response",
    "context-resolve-request",
    "context-resolve-response",
    "context-get-request",
    "context-get-transport-error",
    "object-read-request",
    "object-read-response",
    "evidence-get-request",
    "evidence-unavailable-response",
    "graph-query-request",
    "graph-query-response",
    "operation-commit-request",
    "operation-consent-blocked-response",
    "job-get-request",
    "job-get-transport-error",
    "job-cancel-request",
    "job-cancel-failed-after-mutation-response",
    "runtime-start-request",
    "runtime-start-accepted-response",
    "job-progress-notification",
    "job-progress-notification-complete",
    "session-get-request",
    "transport-error-unavailable",
    "graph-subscribe-start-request",
    "graph-subscribe-start-response",
    "graph-subscription-event",
    "graph-subscription-event-next",
    "graph-subscribe-resume-request",
    "graph-subscription-gap-response",
)

CONTEXT_ID = "context-ref:sha256:4a42094abcea11d8adc6be9b008b03b788f6c12e0997acb33631761edcda1402"
INPUT_ID = "input-binding:sha256:4a42094abcea11d8adc6be9b008b03b788f6c12e0997acb33631761edcda1402"
JOB_ID = "job-v2:b6bd664d24747f51faa946702c0748b9"
JOB_SUBMISSION_ID = "job-submission:sha256:b6bd664d24747f51faa946702c0748b94e615e9bab4ab77755a59cb9ce1c21e0"
JOB_SEAL_ID = "job-terminal-seal:sha256:58e5137d7b2b8f657ea509c3e8fa5c36c348d98a6bf4fbdb960175a90747004a"
JOB_EVENTS = {
    (4, "job-event:sha256:4b04ae0f2d970b810c879e620dd94b71d17c878241840d092e0a9d2ae89e88a2"),
    (5, "job-event:sha256:bf432e70352bf08fb305369352c038ddcdbd61d9ea5bd23a6a6d23077bcb735d"),
}
SUPPORT_DECISION_ID = "support-decision:sha256:d1b9dfeb8de747d73ab63752274cb11013cc430b3f1a09a7cf50f6082f8bd0f5"
RUNTIME_IDEMPOTENCY_KEY = "idempotency-v3:51b9a67e209976066949f71e915ff698"
RUNTIME_PLAN_ID = "operation-plan:sha256:51b9a67e209976066949f71e915ff698336d1dab39f8ea22372f13e547487e0a"
RUNTIME_CONSENT_ID = "consent-decision:sha256:51b9a67e209976066949f71e915ff698336d1dab39f8ea22372f13e547487e0a"


def _strict_json(path: Path) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise AssertionError(f"{path}: duplicate JSON key {key!r}")
            value[key] = item
        return value

    def integer(raw: str) -> int:
        value = int(raw, 10)
        if value < -(2**63) or value > 2**63 - 1:
            raise AssertionError(f"{path}: integer outside signed 64-bit domain")
        return value

    def forbidden(raw: str) -> Any:
        raise AssertionError(f"{path}: forbidden non-integer number {raw!r}")

    return json.loads(
        path.read_bytes(),
        object_pairs_hook=unique_object,
        parse_int=integer,
        parse_float=forbidden,
        parse_constant=forbidden,
    )


def _resources() -> Registry:
    resources = Registry()
    paths = sorted((CRUCIBLE_ROOT / "schemas").glob("*.schema.json"))
    paths += sorted((SHELL_ROOT / "schemas").glob("*.schema.json"))
    for path in paths:
        schema = _strict_json(path)
        if "$id" in schema:
            resources = resources.with_resource(schema["$id"], Resource.from_contents(schema))
    return resources


def _lookup_validator(resources: Registry, schema_id: str) -> Draft202012Validator:
    resolved = resources.resolver().lookup(schema_id)
    return Draft202012Validator(
        resolved.contents,
        registry=resources,
        _resolver=resolved.resolver,
    )


def _decoded(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def _retained_owner_adapter(request: AuthorityAdmissionRequest) -> bool:
    """Test double for the exact owner-adapter validation port."""

    admission = _decoded(request.admission_canonical_bytes)
    capability = _decoded(request.capability_canonical_bytes)
    attestation = admission["attestation"]
    return (
        request.projection_kind == "authority-admission"
        and request.role in {"semantic-owner", "profile-owner"}
        and admission["capability_id"] == capability["capability_id"]
        and attestation["capability_id"] == capability["capability_id"]
        and attestation["decision"] == "approved"
    )


def _retained_profile_adapter(request: ProfileSupportDecisionRequest) -> bool:
    """Test double for profile-owned support-decision validation."""

    capability = _decoded(request.capability_canonical_bytes)
    profile_refs = _decoded(request.profile_refs_canonical_bytes)
    response = _decoded(request.result_canonical_bytes)["result"]
    correlated_request = _decoded(request.request_canonical_bytes)
    return (
        request.projection_kind == "profile-support-decision"
        and request.decision_ids == (SUPPORT_DECISION_ID,)
        and request.support_state
        in set(capability["profile"]["required_support_states"]) | {"unsupported"}
        and profile_refs == capability["profile"]["profile_revision_refs"]
        and response["binding"]["request_id"]
        == correlated_request["params"]["request"]["request_id"]
        and response["binding"]["capability_id"] == capability["capability_id"]
    )


def _retained_context_adapter(request: ContextInputApplicabilityRequest) -> bool:
    correlated_request = _decoded(request.request_canonical_bytes)
    capability = _decoded(request.capability_canonical_bytes)
    return (
        request.projection_kind == "context-input-applicability"
        and request.context_ref_id == CONTEXT_ID
        and request.input_binding_id in {None, INPUT_ID}
        and capability["capability_id"]
        == (
            correlated_request["params"]["request"]["capability_id"]
            if "request" in correlated_request.get("params", {})
            else capability["capability_id"]
        )
        and request.purpose in {"request", "response", "resolved-result"}
    )


def _retained_action_gate_adapter(request: ActionGateDecisionRequest) -> bool:
    capability = _decoded(request.capability_canonical_bytes)
    correlated_request = _decoded(request.request_canonical_bytes)
    response = _decoded(request.result_canonical_bytes)["result"]
    expected = {
        "workbench.runtime.start": (
            "allowed",
            "action-gate-decision:sha256:" + "1" * 64,
        ),
        "blueprints.operation.commit": (
            "blocked",
            "action-gate-decision:sha256:" + "2" * 64,
        ),
    }.get(capability["capability_key"])
    return (
        request.projection_kind == "action-gate-decision"
        and expected is not None
        and request.action_gate_state == expected[0]
        and request.decision_ids == (expected[1],)
        and correlated_request["params"]["request"]["capability_id"]
        == capability["capability_id"]
        and response["binding"]["capability_id"] == capability["capability_id"]
    )


def _retained_job_adapter(request: JobProjectionValidationRequest) -> bool:
    if request.job_id != JOB_ID or request.context_ref_id != CONTEXT_ID or request.input_binding_id != INPUT_ID:
        return False
    if request.request_canonical_bytes is None or request.capability_canonical_bytes is None:
        return False
    correlated_request = _decoded(request.request_canonical_bytes)
    capability = _decoded(request.capability_canonical_bytes)
    message = _decoded(request.message_canonical_bytes)
    request_binding = correlated_request["params"]["request"]
    if (
        request.capability_id != capability["capability_id"]
        or request_binding["capability_id"] != capability["capability_id"]
        or request_binding["context_ref_id"] != request.context_ref_id
        or request_binding["input_binding_id"] != request.input_binding_id
    ):
        return False
    if request.projection_kind == "accepted-handle":
        return (
            correlated_request["method"] == "runtime/start"
            and request_binding["idempotency_key"] == RUNTIME_IDEMPOTENCY_KEY
            and request_binding["commit"]["idempotency_key"] == RUNTIME_IDEMPOTENCY_KEY
            and request_binding["commit"]["plan_id"] == RUNTIME_PLAN_ID
            and request_binding["commit"]["consent_record_id"] == RUNTIME_CONSENT_ID
            and request.job_submission_id == JOB_SUBMISSION_ID
            and request.job_event_ordinal == 4
            and (request.job_event_ordinal, request.job_event_id) in JOB_EVENTS
            and request.terminal_seal_id is None
            and message["result"]["binding"]["handler_id"] == capability["handler_id"]
            and message["result"]["binding"]["handler_implementation_id"]
            == capability["handler_implementation_id"]
        )
    if request.projection_kind == "progress":
        return (
            correlated_request["method"] == "runtime/start"
            and request_binding["idempotency_key"] == RUNTIME_IDEMPOTENCY_KEY
            and request_binding["commit"]["plan_id"] == RUNTIME_PLAN_ID
            and request_binding["commit"]["consent_record_id"] == RUNTIME_CONSENT_ID
            and request.job_submission_id == JOB_SUBMISSION_ID
            and (request.job_event_ordinal, request.job_event_id) in JOB_EVENTS
            and message["params"]["job_id"] == request.job_id
        )
    if request.projection_kind == "job-bearing-failure":
        return (
            correlated_request["method"] == "job/cancel"
            and correlated_request["params"]["job_id"] == JOB_ID
            and request_binding["idempotency_key"]
            == request_binding["commit"]["idempotency_key"]
            and request.terminal_seal_id == JOB_SEAL_ID
            and message["result"]["outcome"]["failure"]["job_id"] == JOB_ID
        )
    return False


def _actual_stream(valid: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_case = {row["case_id"]: row["value"] for row in valid["messages"]}
    assert set(by_case) == set(STREAM_CASE_ORDER)
    stream = [copy.deepcopy(by_case[case_id]) for case_id in STREAM_CASE_ORDER]
    return stream, {case_id: index for index, case_id in enumerate(STREAM_CASE_ORDER)}


def _message_ports() -> dict[str, Any]:
    return {
        "support_decision_validator": _retained_profile_adapter,
        "context_input_validator": _retained_context_adapter,
        "action_gate_validator": _retained_action_gate_adapter,
        "job_projection_validator": _retained_job_adapter,
    }


def _pointer_parent(value: Any, pointer: str) -> tuple[Any, str]:
    tokens = pointer.split("/")[1:]
    current = value
    for raw in tokens[:-1]:
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current, tokens[-1].replace("~1", "/").replace("~0", "~")


def _mutate(value: Any, mutations: list[dict[str, Any]]) -> Any:
    result = copy.deepcopy(value)
    for mutation in mutations:
        parent, token = _pointer_parent(result, mutation["path"])
        if mutation["op"] == "remove":
            if isinstance(parent, list):
                parent.pop(int(token))
            else:
                del parent[token]
        elif mutation["op"] in {"add", "replace"}:
            item = copy.deepcopy(mutation["value"])
            if isinstance(parent, list):
                if token == "-":
                    parent.append(item)
                elif mutation["op"] == "add":
                    parent.insert(int(token), item)
                else:
                    parent[int(token)] = item
            else:
                parent[token] = item
        else:
            raise AssertionError(f"unsupported vector mutation {mutation['op']!r}")
    return result


class ServiceProtocolV3ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.resources = _resources()
        cls.registry_schema = _strict_json(REGISTRY_SCHEMA_PATH)
        cls.protocol_schema = _strict_json(PROTOCOL_SCHEMA_PATH)
        cls.value_schema = _strict_json(VALUE_SCHEMA_PATH)
        cls.valid = _strict_json(VALID_PATH)
        cls.invalid = _strict_json(INVALID_PATH)

    def test_schema_documents_are_strict_meta_valid_and_offline_resolvable(self) -> None:
        for schema in (self.registry_schema, self.protocol_schema, self.value_schema):
            Draft202012Validator.check_schema(schema)
        for capability in self.valid["registry"]["capability_descriptors"]:
            for binding in capability["method_bindings"]:
                for field in ("request_schema_id", "result_schema_id", "failure_schema_id"):
                    _lookup_validator(self.resources, binding[field])
                    self.assertTrue(
                        schema_is_recursively_closed(self.resources, binding[field]),
                        (capability["capability_key"], binding["protocol_method"], field),
                    )
                for field in ("plan_schema_id", "progress_schema_id"):
                    if binding[field] is not None:
                        _lookup_validator(self.resources, binding[field])
                        self.assertTrue(schema_is_recursively_closed(self.resources, binding[field]))

    def test_valid_registry_and_messages_use_the_production_validator(self) -> None:
        registry = self.valid["registry"]
        self.assertEqual(
            (),
            validate_registry_v3(
                registry,
                self.resources,
                REGISTRY_SCHEMA_ID,
                admission_validator=_retained_owner_adapter,
            ),
        )
        stream, indexes = _actual_stream(self.valid)
        for case_id, candidate_index in indexes.items():
            with self.subTest(case=case_id):
                self.assertEqual(
                    (),
                    validate_message_v3(
                        candidate_index,
                        stream,
                        registry,
                        self.resources,
                        PROTOCOL_SCHEMA_ID,
                        **_message_ports(),
                    ),
                )

    def test_invalid_vectors_fail_at_the_declared_boundary(self) -> None:
        registry = self.valid["registry"]
        by_case = {row["case_id"]: row["value"] for row in self.valid["messages"]}
        base_stream, indexes = _actual_stream(self.valid)
        for case in self.invalid["cases"]:
            with self.subTest(case=case["case_id"]):
                base = registry if case["target"] == "registry" else by_case[case["base"]]
                value = _mutate(base, case["mutations"])
                if case["target"] == "registry":
                    diagnostics = validate_registry_v3(
                        value,
                        self.resources,
                        REGISTRY_SCHEMA_ID,
                        admission_validator=_retained_owner_adapter,
                    )
                else:
                    stream = copy.deepcopy(base_stream)
                    stream[indexes[case["base"]]] = value
                    diagnostics = validate_message_v3(
                        indexes[case["base"]],
                        stream,
                        registry,
                        self.resources,
                        PROTOCOL_SCHEMA_ID,
                        **_message_ports(),
                    )
                codes = {diagnostic.code for diagnostic in diagnostics}
                boundary = "registry.schema" if case["target"] == "registry" else "message.schema"
                if case["phase"] == "schema":
                    self.assertIn(boundary, codes, sorted(codes))
                else:
                    self.assertNotIn(boundary, codes, sorted(codes))
                    self.assertIn(case["expected_code"], codes, sorted(codes))

    def test_vectors_cover_every_method_family_and_domain_disposition(self) -> None:
        methods = {
            row["value"]["method"]
            for row in self.valid["messages"]
            if isinstance(row["value"].get("params"), dict)
            and isinstance(row["value"]["params"].get("request"), dict)
        }
        self.assertEqual(
            {"service", "context", "object", "evidence", "graph", "operation", "job", "runtime", "session"},
            {method.split("/", 1)[0] for method in methods},
        )
        states = {
            row["value"]["result"]["outcome"]["state"]
            for row in self.valid["messages"]
            if isinstance(row["value"].get("result"), dict)
            and row["value"]["result"].get("format") == "workbench-service-result-v3"
        }
        self.assertEqual({"succeeded", "accepted", "unavailable", "blocked", "failed"}, states)

    def test_failure_matrix_partitions_every_kind_exactly_once(self) -> None:
        expected = set(self.protocol_schema["$defs"]["failureKind"]["enum"])
        observed: list[str] = []
        for branch in self.protocol_schema["$defs"]["failure"]["oneOf"]:
            kind = branch["properties"]["kind"]
            observed.extend(kind.get("enum", [kind.get("const")]))
        self.assertEqual(expected, set(observed))
        self.assertEqual(len(expected), len(observed))

    def test_progress_projection_and_stream_monotonicity_are_executable(self) -> None:
        base = next(
            row["value"]
            for row in self.valid["messages"]
            if row["case_id"] == "job-progress-notification"
        )
        states = _strict_json(CRUCIBLE_ROOT / "schemas" / "crucible-job-v2-common.schema.json")[
            "$defs"
        ]["mutationState"]["enum"]
        before_boundary = {"not-started", "temporary-residue"}
        validator = _lookup_validator(self.resources, PROTOCOL_SCHEMA_ID)
        for state in states:
            with self.subTest(state=state):
                message = copy.deepcopy(base)
                message["params"]["mutation_state"] = state
                message["params"]["mutation_started"] = state not in before_boundary
                self.assertEqual([], list(validator.iter_errors(message)))
                message["params"]["mutation_started"] = not message["params"]["mutation_started"]
                self.assertTrue(list(validator.iter_errors(message)))

    def test_semantic_diagnostics_are_stable_sets(self) -> None:
        registry = self.valid["registry"]
        self.assertEqual(
            set(),
            _registry_semantic_codes(
                registry, self.resources, admission_validator=_retained_owner_adapter
            ),
        )

        unverified = _registry_semantic_codes(registry, self.resources)
        self.assertIn("registry.authority-attestation-unverified", unverified)
        self.assertIn("registry.profile-attestation-unverified", unverified)
        stream, indexes = _actual_stream(self.valid)
        self.assertIn(
            "response.support-decision-unverified",
            _message_semantic_codes(
                indexes["graph-query-response"],
                stream,
                registry,
                self.resources,
                context_input_validator=_retained_context_adapter,
                action_gate_validator=_retained_action_gate_adapter,
                job_projection_validator=_retained_job_adapter,
            ),
        )
        self.assertEqual(
            set(),
            _message_semantic_codes(
                indexes["object-read-response"],
                stream,
                registry,
                self.resources,
                **_message_ports(),
            ),
        )

    def test_success_preserves_unresolved_and_conflicted_graph_knowledge(self) -> None:
        stream, indexes = _actual_stream(self.valid)
        index = indexes["graph-query-response"]
        for completeness in ("unresolved", "conflicted"):
            with self.subTest(completeness=completeness):
                candidate_stream = copy.deepcopy(stream)
                candidate_stream[index]["result"]["binding"]["states"]["completeness"] = completeness
                self.assertEqual(
                    (),
                    validate_message_v3(
                        index,
                        candidate_stream,
                        self.valid["registry"],
                        self.resources,
                        PROTOCOL_SCHEMA_ID,
                        **_message_ports(),
                    ),
                )

    def test_implicit_array_dynamic_schema_is_not_recursively_closed(self) -> None:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "workbench://schemas/audit/implicit-array.schema.json",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": False,
        }
        resources = self.resources.with_resource(
            schema["$id"], Resource.from_contents(schema)
        )
        self.assertFalse(schema_is_recursively_closed(resources, schema["$id"]))
        self.assertTrue(Draft202012Validator(schema).is_valid([{"hidden": "payload"}]))

    def test_authority_ports_are_exact_immutable_and_exception_safe(self) -> None:
        truthy = validate_registry_v3(
            self.valid["registry"],
            self.resources,
            REGISTRY_SCHEMA_ID,
            admission_validator=lambda request: "yes",
        )
        self.assertIn(
            "registry.authority-attestation-unverified",
            {item.code for item in truthy},
        )

        def raising(request):
            raise RuntimeError("owner adapter failed")

        raised = validate_registry_v3(
            self.valid["registry"],
            self.resources,
            REGISTRY_SCHEMA_ID,
            admission_validator=raising,
        )
        self.assertIn(
            "registry.authority-attestation-port-exception",
            {item.code for item in raised},
        )

        original = copy.deepcopy(self.valid["registry"])

        def mutate_private_decode(request: AuthorityAdmissionRequest) -> bool:
            decoded = _decoded(request.capability_canonical_bytes)
            decoded["semantic_version"] = "9.9.9"
            return True

        self.assertEqual(
            (),
            validate_registry_v3(
                original,
                self.resources,
                REGISTRY_SCHEMA_ID,
                admission_validator=mutate_private_decode,
            ),
        )
        self.assertEqual(self.valid["registry"], original)

    def test_required_ports_reject_force_mutation_of_detached_requests(self) -> None:
        registry = copy.deepcopy(self.valid["registry"])
        registry_before = copy.deepcopy(registry)

        def mutate_admission(request: AuthorityAdmissionRequest) -> bool:
            object.__setattr__(request, "admission_canonical_bytes", b"{}")
            return True

        diagnostics = validate_registry_v3(
            registry,
            self.resources,
            REGISTRY_SCHEMA_ID,
            admission_validator=mutate_admission,
        )
        registry_codes = {item.code for item in diagnostics}
        self.assertIn("registry.authority-attestation-port-mutation", registry_codes)
        self.assertIn("registry.profile-attestation-port-mutation", registry_codes)
        self.assertEqual(registry_before, registry)

        stream, indexes = _actual_stream(self.valid)
        stream_before = copy.deepcopy(stream)

        def mutate_context(request: ContextInputApplicabilityRequest) -> bool:
            object.__setattr__(request, "context_ref_id", "context-ref:sha256:" + "f" * 64)
            return True

        def mutate_support(request: ProfileSupportDecisionRequest) -> bool:
            replacement = {"ids": list(request.decision_ids)}
            replacement["ids"].append("support-decision:sha256:" + "f" * 64)
            object.__setattr__(request, "decision_ids", replacement)
            return True

        def mutate_gate(request: ActionGateDecisionRequest) -> bool:
            object.__setattr__(request, "action_gate_state", "tampered")
            return True

        def mutate_job(request: JobProjectionValidationRequest) -> bool:
            object.__setattr__(request, "job_id", "job-v2:" + "f" * 32)
            return True

        cases = (
            (
                "graph-query-request",
                {**_message_ports(), "context_input_validator": mutate_context},
                "request.context-input-port-mutation",
            ),
            (
                "graph-query-response",
                {**_message_ports(), "support_decision_validator": mutate_support},
                "response.support-decision-port-mutation",
            ),
            (
                "runtime-start-accepted-response",
                {**_message_ports(), "action_gate_validator": mutate_gate},
                "response.action-gate-port-mutation",
            ),
            (
                "runtime-start-accepted-response",
                {**_message_ports(), "job_projection_validator": mutate_job},
                "response.job-projection-port-mutation",
            ),
        )
        for case_id, ports, expected in cases:
            with self.subTest(case=case_id, expected=expected):
                diagnostics = validate_message_v3(
                    indexes[case_id],
                    stream,
                    registry,
                    self.resources,
                    PROTOCOL_SCHEMA_ID,
                    **ports,
                )
                self.assertIn(expected, {item.code for item in diagnostics})
                self.assertEqual(stream_before, stream)
                self.assertEqual(registry_before, registry)

    def test_context_gate_and_job_ports_reject_cross_projection_replay(self) -> None:
        stream, indexes = _actual_stream(self.valid)

        changed_context = copy.deepcopy(stream)
        changed_context[indexes["graph-query-request"]]["params"]["request"][
            "context_ref_id"
        ] = "context-ref:sha256:" + "f" * 64
        diagnostics = validate_message_v3(
            indexes["graph-query-request"],
            changed_context,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn(
            "request.context-input-unverified", {item.code for item in diagnostics}
        )

        changed_gate = copy.deepcopy(stream)
        changed_gate[indexes["runtime-start-accepted-response"]]["result"]["binding"][
            "states"
        ]["action_gate_decision_ids"] = ["action-gate-decision:sha256:" + "f" * 64]
        diagnostics = validate_message_v3(
            indexes["runtime-start-accepted-response"],
            changed_gate,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn("response.action-gate-unverified", {item.code for item in diagnostics})

        changed_support = copy.deepcopy(stream)
        changed_support[indexes["graph-query-response"]]["result"]["binding"]["states"][
            "support_decision_ids"
        ] = ["support-decision:sha256:" + "f" * 64]
        diagnostics = validate_message_v3(
            indexes["graph-query-response"],
            changed_support,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn(
            "response.support-decision-unverified", {item.code for item in diagnostics}
        )

        changed_job = copy.deepcopy(stream)
        changed_job[indexes["runtime-start-accepted-response"]]["result"]["outcome"][
            "job"
        ]["job_submission_id"] = "job-submission:sha256:" + "f" * 64
        diagnostics = validate_message_v3(
            indexes["runtime-start-accepted-response"],
            changed_job,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        codes = {item.code for item in diagnostics}
        self.assertIn("response.job-output-revision", codes)
        self.assertIn("response.job-projection-unverified", codes)

        for mutation in ("plan", "idempotency"):
            replay = copy.deepcopy(stream)
            binding = replay[indexes["runtime-start-request"]]["params"]["request"]
            if mutation == "plan":
                binding["commit"]["plan_id"] = "operation-plan:sha256:" + "f" * 64
            else:
                key = "idempotency-v3:" + "f" * 32
                binding["idempotency_key"] = key
                binding["commit"]["idempotency_key"] = key
            diagnostics = validate_message_v3(
                indexes["runtime-start-accepted-response"],
                replay,
                self.valid["registry"],
                self.resources,
                PROTOCOL_SCHEMA_ID,
                **_message_ports(),
            )
            self.assertIn(
                "response.job-projection-unverified",
                {item.code for item in diagnostics},
            )

        changed_progress = copy.deepcopy(stream)
        changed_progress[indexes["job-progress-notification-complete"]]["params"][
            "job_event_id"
        ] = "job-event:sha256:" + "f" * 64
        diagnostics = validate_message_v3(
            indexes["job-progress-notification-complete"],
            changed_progress,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn(
            "progress.job-projection-unverified", {item.code for item in diagnostics}
        )

    def test_all_required_message_ports_normalize_exceptions(self) -> None:
        stream, indexes = _actual_stream(self.valid)

        def raising(request):
            raise RuntimeError("port failed")

        cases = (
            (
                "graph-query-request",
                {**_message_ports(), "context_input_validator": raising},
                "request.context-input-port-exception",
            ),
            (
                "graph-query-response",
                {**_message_ports(), "support_decision_validator": raising},
                "response.support-decision-port-exception",
            ),
            (
                "runtime-start-accepted-response",
                {**_message_ports(), "action_gate_validator": raising},
                "response.action-gate-port-exception",
            ),
            (
                "runtime-start-accepted-response",
                {**_message_ports(), "job_projection_validator": raising},
                "response.job-projection-port-exception",
            ),
        )
        for case_id, ports, expected in cases:
            with self.subTest(case=case_id, expected=expected):
                diagnostics = validate_message_v3(
                    indexes[case_id],
                    stream,
                    self.valid["registry"],
                    self.resources,
                    PROTOCOL_SCHEMA_ID,
                    **ports,
                )
                self.assertIn(expected, {item.code for item in diagnostics})

        truthy_ports = {
            **_message_ports(),
            "support_decision_validator": lambda request: "yes",
        }
        diagnostics = validate_message_v3(
            indexes["graph-query-response"],
            stream,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **truthy_ports,
        )
        self.assertIn(
            "response.support-decision-unverified", {item.code for item in diagnostics}
        )

    def test_subscription_resume_replays_original_handle_without_last_wins(self) -> None:
        stream, indexes = _actual_stream(self.valid)
        changed = copy.deepcopy(stream)
        changed[indexes["graph-subscribe-resume-request"]]["params"]["scope"][
            "graph_revision_ids"
        ][0] = "graph-revision:sha256:" + "f" * 64
        diagnostics = validate_message_v3(
            indexes["graph-subscribe-resume-request"],
            changed,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn("subscription.scope", {item.code for item in diagnostics})

        duplicate = copy.deepcopy(stream)
        insertion = indexes["graph-subscribe-start-response"] + 1
        duplicate.insert(insertion, copy.deepcopy(stream[indexes["graph-subscribe-start-response"]]))
        diagnostics = validate_message_v3(
            insertion,
            duplicate,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn(
            "subscription.handle-duplicate", {item.code for item in diagnostics}
        )

    def test_public_message_api_uses_actual_snapshotted_streams(self) -> None:
        stream, indexes = _actual_stream(self.valid)
        self.assertEqual(
            (),
            validate_message_v3(
                indexes["graph-query-request"],
                stream,
                self.valid["registry"],
                self.resources,
                PROTOCOL_SCHEMA_ID,
                **_message_ports(),
            ),
        )

        class HostileMapping(dict):
            pass

        hostile_stream = list(stream)
        hostile_stream[indexes["graph-query-request"]] = HostileMapping(
            hostile_stream[indexes["graph-query-request"]]
        )
        diagnostics = validate_message_v3(
            indexes["graph-query-request"],
            hostile_stream,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertEqual("message.domain", diagnostics[0].code)
        self.assertEqual(
            "message.candidate-index",
            validate_message_v3(
                len(stream),
                stream,
                self.valid["registry"],
                self.resources,
                PROTOCOL_SCHEMA_ID,
                **_message_ports(),
            )[0].code,
        )

    def test_initialize_uses_global_response_and_request_id_cardinality(self) -> None:
        stream, indexes = _actual_stream(self.valid)
        duplicate_error = copy.deepcopy(
            next(
                row["value"]
                for row in self.valid["messages"]
                if row["case_id"] == "transport-error-unavailable"
            )
        )
        duplicate_error["id"] = stream[indexes["initialize-v3-request"]]["id"]
        stream.append(duplicate_error)
        initialize_diagnostics = validate_message_v3(
            indexes["initialize-v3-response"],
            stream,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        error_diagnostics = validate_message_v3(
            len(stream) - 1,
            stream,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn("response.duplicate", {item.code for item in initialize_diagnostics})
        self.assertIn("response.duplicate", {item.code for item in error_diagnostics})

        duplicate_result_stream, indexes = _actual_stream(self.valid)
        duplicate_result = copy.deepcopy(
            duplicate_result_stream[indexes["initialize-v3-response"]]
        )
        duplicate_result["id"] = 999
        duplicate_result_stream.append(duplicate_result)
        for candidate_index in (
            indexes["initialize-v3-response"],
            len(duplicate_result_stream) - 1,
        ):
            diagnostics = validate_message_v3(
                candidate_index,
                duplicate_result_stream,
                self.valid["registry"],
                self.resources,
                PROTOCOL_SCHEMA_ID,
                **_message_ports(),
            )
            self.assertIn(
                "initialize.response-cardinality", {item.code for item in diagnostics}
            )

        colliding_stream, indexes = _actual_stream(self.valid)
        initialize_request_id = colliding_stream[indexes["initialize-v3-request"]][
            "params"
        ]["request_id"]
        colliding_stream[indexes["graph-query-request"]]["params"]["request"][
            "request_id"
        ] = initialize_request_id
        for candidate_index in (
            indexes["initialize-v3-request"],
            indexes["graph-query-request"],
        ):
            diagnostics = validate_message_v3(
                candidate_index,
                colliding_stream,
                self.valid["registry"],
                self.resources,
                PROTOCOL_SCHEMA_ID,
                **_message_ports(),
            )
            self.assertIn("request.id-duplicate", {item.code for item in diagnostics})

    def test_whole_stream_revalidates_handshake_and_domain_response_binding(self) -> None:
        handshake_stream, indexes = _actual_stream(self.valid)
        handshake_stream[indexes["initialize-v3-response"]]["result"]["registry_id"] = (
            "component-capability-registry:sha256:" + "f" * 64
        )
        diagnostics = validate_message_v3(
            indexes["graph-query-request"],
            handshake_stream,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn("initialize.registry", {item.code for item in diagnostics})

        cross_wired_stream, indexes = _actual_stream(self.valid)
        operation_request = cross_wired_stream.pop(indexes["operation-commit-request"])
        graph_response = cross_wired_stream[indexes["graph-query-response"]]
        cross_wired_stream.insert(cross_wired_stream.index(graph_response), operation_request)
        graph_response = next(
            value
            for value in cross_wired_stream
            if isinstance(value.get("result"), dict)
            and value["result"].get("binding", {}).get("request_method") == "graph/query"
        )
        operation_response = next(
            value
            for value in cross_wired_stream
            if isinstance(value.get("result"), dict)
            and value["result"].get("binding", {}).get("request_method") == "operation/commit"
        )
        graph_response["id"], operation_response["id"] = (
            operation_response["id"],
            graph_response["id"],
        )
        unrelated_index = next(
            index
            for index, value in enumerate(cross_wired_stream)
            if value.get("method") == "session/get"
        )
        diagnostics = validate_message_v3(
            unrelated_index,
            cross_wired_stream,
            self.valid["registry"],
            self.resources,
            PROTOCOL_SCHEMA_ID,
            **_message_ports(),
        )
        self.assertIn("response.jsonrpc-id", {item.code for item in diagnostics})

    def test_stdio_vectors_use_exact_utf8_content_length_framing(self) -> None:
        by_case = {row["case_id"]: row["value"] for row in self.valid["messages"]}
        for vector in self.valid["stdio_frames"]:
            with self.subTest(case=vector["case_id"]):
                header, payload = vector["frame"].split("\r\n\r\n", 1)
                self.assertEqual(f"Content-Length: {vector['content_length']}", header)
                self.assertEqual(vector["content_length"], len(payload.encode("utf-8")))
                self.assertEqual(by_case[vector["payload_case_id"]], json.loads(payload))
                self.assertNotIn("\n", header)


if __name__ == "__main__":
    unittest.main()
