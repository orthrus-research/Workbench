"""Live Feature Studio composition over the Crucible Service V3 runtime."""

from __future__ import annotations

from workbench_crucible_service import DurableJobStore

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from workbench_crucible_service import ContextListHandler, JobCancellationHandler, JobEventPageHandler, JobSubscriptionHandler, ServiceCapabilitiesHandler, validate_context_list_result, validate_empty_service_arguments, validate_job_cancellation_arguments, validate_job_event_page_arguments, validate_job_event_page_result, validate_job_handle_result, validate_job_subscription_arguments, validate_job_subscription_result, validate_service_capabilities_result
from workbench_core.service.runtime import LocalServiceAuthenticator, ServiceRuntimeV3
from workbench_api.service import ServiceCancelled, ServiceExecutionContext, ServiceHandlerRegistration
from workbench_api.canonical import canonical_json_bytes, content_id, parse_canonical_json

from .feature_studio import (
    execute_feature_request,
    validate_feature_request,
    validate_feature_result,
)
from .material_fluid_flow import require_material_fluid_profile
from .feature_studio_registry import (
    CONTEXT_REGISTRATION_CAPABILITY_KEY,
    CONTROL_CAPABILITY_KEYS,
    FEATURE_CAPABILITY_KEYS,
    FEATURE_JOB_RESULT_CAPABILITY_KEY,
    FEATURE_OPERATIONS,
    FeatureStudioRegistryBundleV3,
    build_feature_studio_registry_v3,
)
from .service_contract import validate_registry_v3
from workbench_core.service.host import HostedMethodBinding, OwnerBindingProjection, ServiceHostV3, ServiceHostV3Error, local_service_physical_lease_ports


REGISTRY_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/"
    "component-capability-registry-v3.schema.json"
)
FEATURE_SERVICE_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/feature-studio-service-v1.schema.json"
)
LIVE_BINDING_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/"
    "feature-studio-service-bindings-v3.schema.json"
)
SERVICE_PROTOCOL_VERSION = {"major": 3, "minor": 0}
_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_READ_ONLY_OPERATIONS = frozenset({"inspect", "plan", "explain"})
_DURABLE_OPERATIONS = frozenset({"verify", "export"})
_AUTHORITY_BOUNDARY = {
    "atlas_semantics_created": False,
    "blueprint_admission_created": False,
    "crucible_custody_replaced": False,
    "profile_support_promoted": False,
    "source_application_authorized": False,
}


class FeatureStudioServiceV3Error(ValueError):
    """The service cannot preserve an exact owner or review binding."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise FeatureStudioServiceV3Error(code, message)


def _snapshot(value: Any) -> Any:
    try:
        return parse_canonical_json(canonical_json_bytes(value))
    except (TypeError, ValueError) as exc:
        raise FeatureStudioServiceV3Error(
            "feature-studio.noncanonical",
            "Feature Studio service value is outside canonical JSON V2",
        ) from exc


def _schema_resources(repository_root: Path) -> Registry:
    resources = Registry()
    for schema_root in (
        repository_root / "modules/crucible/schemas",
        repository_root / "modules/workbench-shell/schemas",
    ):
        _require(
            schema_root.is_dir() and not schema_root.is_symlink(),
            "feature-studio.schema-root",
            "Feature Studio service schema root is unavailable",
        )
        for path in sorted(schema_root.glob("*.schema.json")):
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise FeatureStudioServiceV3Error(
                    "feature-studio.schema-unavailable",
                    f"Feature Studio service schema is unavailable: {path.name}",
                ) from exc
            if "$id" in schema:
                resources = resources.with_resource(
                    schema["$id"], Resource.from_contents(schema)
                )
    return resources


def _schema_validator(resources: Registry, schema_id: str) -> Callable[[Any], bool]:
    resolved = resources.resolver().lookup(schema_id)
    validator = Draft202012Validator(
        resolved.contents,
        registry=resources,
        _resolver=resolved.resolver,
        format_checker=FormatChecker(),
    )
    return lambda value: not list(validator.iter_errors(value))


def _descriptor(
    registry: Mapping[str, Any], capability_key: str
) -> Mapping[str, Any]:
    rows = [
        row
        for row in registry["capability_descriptors"]
        if row["capability_key"] == capability_key
    ]
    _require(
        len(rows) == 1,
        "feature-studio.registry-ambiguous",
        f"Feature Studio registry capability is ambiguous: {capability_key}",
    )
    return rows[0]


def _declaration(
    registry: Mapping[str, Any], capability_id: str
) -> Mapping[str, Any]:
    rows = [
        row
        for row in registry["handler_registrations"]
        if row["capability_id"] == capability_id
    ]
    _require(
        len(rows) == 1,
        "feature-studio.registry-ambiguous",
        "Feature Studio registry handler is ambiguous",
    )
    return rows[0]


def _method(
    descriptor: Mapping[str, Any], method: str
) -> Mapping[str, Any]:
    rows = [
        row
        for row in descriptor["method_bindings"]
        if row["protocol_method"] == method
    ]
    _require(
        len(rows) == 1,
        "feature-studio.registry-ambiguous",
        f"Feature Studio registry method is ambiguous: {method}",
    )
    return rows[0]


def _expected_head_refs(
    context_ref_id: str,
    input_binding_id: str,
) -> list[dict[str, str]]:
    return [
        {"record_kind": "context-ref", "record_id": context_ref_id},
        {"record_kind": "input-binding", "record_id": input_binding_id},
    ]


def _owner_request_id(owner_request: Mapping[str, Any]) -> str:
    return content_id("feature-studio-owner-request", _snapshot(owner_request))


def _owner_request_fields(owner_request: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_feature_request(owner_request)
    raw = canonical_json_bytes(request)
    return {
        "owner_request_id": content_id("feature-studio-owner-request", request),
        "owner_request_canonical_json": raw.decode("utf-8"),
        "owner_request_canonical_sha256": hashlib.sha256(raw).hexdigest(),
        "owner_request_canonical_size": len(raw),
    }


def _owner_request_from_arguments(
    arguments: Mapping[str, Any],
    *,
    expected_operation: str | None = None,
) -> dict[str, Any]:
    try:
        text = arguments["owner_request_canonical_json"]
        raw = text.encode("utf-8")
        value = parse_canonical_json(raw)
        request = validate_feature_request(
            value, expected_operation=expected_operation
        )
    except (KeyError, AttributeError, UnicodeError, TypeError, ValueError) as exc:
        raise FeatureStudioServiceV3Error(
            "feature-studio.owner-request",
            "Feature Studio canonical owner request is invalid",
        ) from exc
    expected = _owner_request_fields(request)
    _require(
        raw == canonical_json_bytes(request)
        and all(arguments.get(key) == expected[key] for key in expected),
        "feature-studio.owner-request",
        "Feature Studio owner request bytes or identity changed",
    )
    return request


def seal_context_registration_consent_v1(
    context_ref_id: str,
    input_binding_id: str,
) -> str:
    """Seal review consent for storage of one exact owner context pair."""

    return content_id(
        "consent-decision",
        {
            "decision": "granted",
            "operation": "feature-studio.context-register",
            "context_ref_id": context_ref_id,
            "input_binding_id": input_binding_id,
        },
    )


def seal_context_registration_plan_v1(
    context_ref: Mapping[str, Any],
    input_binding: Mapping[str, Any],
    *,
    consent_record_id: str | None = None,
) -> dict[str, Any]:
    """Seal one non-authorizing plan for exact durable context custody."""

    context_value = _snapshot(context_ref)
    input_value = _snapshot(input_binding)
    context_ref_id = context_value.get("id")
    input_binding_id = input_value.get("id")
    _require(
        type(context_ref_id) is str
        and context_ref_id.startswith("context-ref:sha256:")
        and _CONTENT_ID.fullmatch(context_ref_id) is not None
        and type(input_binding_id) is str
        and input_binding_id.startswith("input-binding:sha256:")
        and _CONTENT_ID.fullmatch(input_binding_id) is not None
        and input_value.get("context_ref_id") == context_ref_id,
        "feature-studio.context-binding",
        "Feature Studio context/input identities disagree",
    )
    consent = consent_record_id or seal_context_registration_consent_v1(
        context_ref_id, input_binding_id
    )
    _require(
        type(consent) is str
        and consent.startswith("consent-decision:sha256:")
        and _CONTENT_ID.fullmatch(consent) is not None,
        "feature-studio.consent",
        "Feature Studio context registration consent is invalid",
    )
    body = {
        "format": "workbench-feature-studio-context-registration-plan-v1",
        "schema_version": 1,
        "canonicalizer": "workbench-canonical-json-v2",
        "kind": "operation-plan",
        "operation_class": "stable-construct",
        "context_ref_id": context_ref_id,
        "input_binding_id": input_binding_id,
        "consent_record_id": consent,
        "expected_head_refs": _expected_head_refs(
            context_ref_id, input_binding_id
        ),
        "limitations": [
            "Registration preserves owner context bytes and does not admit profile support."
        ],
    }
    return {"id": content_id("operation-plan", body), **body}


def seal_feature_operation_consent_v1(
    operation: str,
    owner_request: Mapping[str, Any],
    context_ref_id: str,
    input_binding_id: str,
) -> str:
    """Seal explicit local review consent without granting profile authority."""

    _require(
        operation in _DURABLE_OPERATIONS,
        "feature-studio.operation",
        "Feature Studio durable consent operation is invalid",
    )
    return content_id(
        "consent-decision",
        {
            "decision": "granted",
            "operation": operation,
            "owner_request_id": _owner_request_id(owner_request),
            "context_ref_id": context_ref_id,
            "input_binding_id": input_binding_id,
            "authority_boundary": _AUTHORITY_BOUNDARY,
        },
    )


def seal_feature_operation_plan_v1(
    owner_request: Mapping[str, Any],
    context_ref_id: str,
    input_binding_id: str,
    *,
    consent_record_id: str | None = None,
) -> dict[str, Any]:
    """Seal a verify/export service plan around the exact owner request."""

    request = validate_feature_request(owner_request)
    operation = request["operation"]
    _require(
        operation in _DURABLE_OPERATIONS
        and type(request["reviewed_plan_id"]) is str,
        "feature-studio.operation-plan",
        "Feature Studio durable operation lacks its reviewed owner plan",
    )
    consent = consent_record_id or seal_feature_operation_consent_v1(
        operation,
        request,
        context_ref_id,
        input_binding_id,
    )
    _require(
        type(consent) is str
        and consent.startswith("consent-decision:sha256:")
        and _CONTENT_ID.fullmatch(consent) is not None,
        "feature-studio.consent",
        "Feature Studio operation consent is invalid",
    )
    side_effects = (
        ["reference-update", "runtime-process", "store-append"]
        if operation == "verify"
        else ["external-export", "reference-update", "store-append"]
    )
    body = {
        "format": "workbench-feature-studio-service-operation-plan-v1",
        "schema_version": 1,
        "canonicalizer": "workbench-canonical-json-v2",
        "kind": "operation-plan",
        "operation": operation,
        "operation_class": "stable-construct",
        "context_ref_id": context_ref_id,
        "input_binding_id": input_binding_id,
        "owner_request_id": _owner_request_id(request),
        "reviewed_owner_plan_id": request["reviewed_plan_id"],
        "consent_record_id": consent,
        "expected_head_refs": _expected_head_refs(
            context_ref_id, input_binding_id
        ),
        "side_effect_classes": side_effects,
        "limitations": [
            "This plan authorizes only the exact disposable verification or external export request.",
            "It does not authorize direct source application or promote profile support.",
        ],
    }
    return {"id": content_id("operation-plan", body), **body}


def feature_service_arguments_v1(
    owner_request: Mapping[str, Any],
    context_ref_id: str,
    input_binding_id: str,
) -> dict[str, Any]:
    """Wrap one immutable owner request for its live V3 capability."""

    request = validate_feature_request(owner_request)
    operation = request["operation"]
    _require(
        operation in FEATURE_OPERATIONS,
        "feature-studio.unavailable-operation",
        "Feature Studio operation has no live registration",
    )
    if operation in _DURABLE_OPERATIONS:
        plan = seal_feature_operation_plan_v1(
            request, context_ref_id, input_binding_id
        )
        plan_id: str | None = plan["id"]
        consent: str | None = plan["consent_record_id"]
        expected_heads = plan["expected_head_refs"]
    else:
        plan = None
        plan_id = None
        consent = None
        expected_heads = []
    return {
        "format": "workbench-feature-studio-service-request-v1",
        "schema_version": 1,
        "canonicalizer": "workbench-canonical-json-v2",
        "operation": operation,
        **_owner_request_fields(request),
        "plan_id": plan_id,
        "operation_plan": plan,
        "consent_record_id": consent,
        "expected_head_refs": expected_heads,
    }


def _validate_context_registration_arguments(value: Any) -> bool:
    if type(value) is not dict:
        return False
    try:
        expected = seal_context_registration_plan_v1(
            value["context_ref"],
            value["input_binding"],
            consent_record_id=value["consent_record_id"],
        )
    except (KeyError, FeatureStudioServiceV3Error):
        return False
    return (
        value.get("format")
        == "workbench-feature-studio-context-registration-v1"
        and value.get("schema_version") == 1
        and value.get("canonicalizer") == "workbench-canonical-json-v2"
        and value.get("plan_id") == expected["id"]
        and value.get("plan") == expected
        and value.get("expected_head_refs") == expected["expected_head_refs"]
        and set(value)
        == {
            "canonicalizer",
            "consent_record_id",
            "context_ref",
            "expected_head_refs",
            "format",
            "input_binding",
            "plan",
            "plan_id",
            "schema_version",
        }
    )


def _validate_feature_arguments(value: Any, operation: str) -> bool:
    if type(value) is not dict:
        return False
    try:
        request = _owner_request_from_arguments(
            value, expected_operation=operation
        )
    except (KeyError, ValueError):
        return False
    if set(value) != {
        "canonicalizer",
        "consent_record_id",
        "expected_head_refs",
        "format",
        "operation",
        "operation_plan",
        "owner_request_canonical_json",
        "owner_request_canonical_sha256",
        "owner_request_canonical_size",
        "owner_request_id",
        "plan_id",
        "schema_version",
    }:
        return False
    if (
        value["format"] != "workbench-feature-studio-service-request-v1"
        or value["schema_version"] != 1
        or value["canonicalizer"] != "workbench-canonical-json-v2"
        or value["operation"] != operation
    ):
        return False
    if operation in _READ_ONLY_OPERATIONS:
        return (
            value["plan_id"] is None
            and value["operation_plan"] is None
            and value["consent_record_id"] is None
            and value["expected_head_refs"] == []
        )
    plan = value["operation_plan"]
    if type(plan) is not dict:
        return False
    try:
        expected = seal_feature_operation_plan_v1(
            request,
            plan["context_ref_id"],
            plan["input_binding_id"],
            consent_record_id=value["consent_record_id"],
        )
    except (KeyError, FeatureStudioServiceV3Error, ValueError):
        return False
    return (
        plan == expected
        and value["plan_id"] == expected["id"]
        and value["expected_head_refs"] == expected["expected_head_refs"]
    )


def _wrap_feature_result(
    operation: str,
    owner_request: Mapping[str, Any],
    owner_result: Mapping[str, Any],
    context_ref_id: str,
    input_binding_id: str,
    operation_plan_id: str | None,
) -> dict[str, Any]:
    owner_raw = canonical_json_bytes(owner_result)
    body = {
        "format": "workbench-feature-studio-service-result-v1",
        "schema_version": 1,
        "canonicalizer": "workbench-canonical-json-v2",
        "operation": operation,
        "context_ref_id": context_ref_id,
        "input_binding_id": input_binding_id,
        "operation_plan_id": operation_plan_id,
        "owner_request_id": _owner_request_id(owner_request),
        "owner_result_id": owner_result["result_id"],
        "owner_result_canonical_json": owner_raw.decode("utf-8"),
        "owner_result_canonical_sha256": hashlib.sha256(owner_raw).hexdigest(),
        "owner_result_canonical_size": len(owner_raw),
        "authority_boundary": dict(_AUTHORITY_BOUNDARY),
        "limitations": [
            "The service wrapper preserves the owner result and creates no semantic or support authority.",
            "Crucible job and terminal records remain authoritative for execution lifecycle and cancellation state.",
        ],
    }
    return {
        "result_id": content_id("feature-studio-service-result", body),
        **body,
    }


def validate_feature_service_result_v1(
    value: Any,
    *,
    suite_root: Path | str | None = None,
) -> bool:
    """Validate the wrapper identity and the untouched embedded owner result."""

    if type(value) is not dict:
        return False
    try:
        body = dict(value)
        result_id = body.pop("result_id")
        if result_id != content_id("feature-studio-service-result", body):
            return False
        owner_raw = value["owner_result_canonical_json"].encode("utf-8")
        owner_candidate = parse_canonical_json(owner_raw)
        owner_result = validate_feature_result(
            owner_candidate, suite_root=suite_root
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        value.get("format") == "workbench-feature-studio-service-result-v1"
        and value.get("schema_version") == 1
        and value.get("canonicalizer") == "workbench-canonical-json-v2"
        and value.get("operation") == owner_result["operation"]
        and value.get("owner_result_id") == owner_result["result_id"]
        and owner_raw == canonical_json_bytes(owner_result)
        and value.get("owner_result_canonical_sha256")
        == hashlib.sha256(owner_raw).hexdigest()
        and value.get("owner_result_canonical_size") == len(owner_raw)
        and value.get("owner_request_id", "").startswith(
            "feature-studio-owner-request:sha256:"
        )
        and value.get("authority_boundary") == _AUTHORITY_BOUNDARY
    )


def owner_result_from_feature_service_result_v1(
    value: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    """Reopen the exact canonical owner result retained by a service wrapper."""

    _require(
        validate_feature_service_result_v1(value, suite_root=suite_root),
        "feature-studio.service-result",
        "Feature Studio service result is invalid",
    )
    return parse_canonical_json(
        value["owner_result_canonical_json"].encode("utf-8")
    )


class ContextRegistrationHandler:
    """Publish one reviewed exact ContextRef/InputBinding pair."""

    def __call__(
        self,
        context: ServiceExecutionContext,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            _validate_context_registration_arguments(arguments),
            "feature-studio.context-registration-request",
            "Feature Studio context registration request is invalid",
        )
        context_ref_id, input_binding_id = context.register_context(
            canonical_json_bytes(arguments["context_ref"]),
            canonical_json_bytes(arguments["input_binding"]),
        )
        _require(
            context_ref_id == arguments["plan"]["context_ref_id"]
            and input_binding_id == arguments["plan"]["input_binding_id"],
            "feature-studio.context-registration-result",
            "Feature Studio registered context differs from its reviewed plan",
        )
        return {
            "format": "workbench-feature-studio-context-registration-result-v1",
            "schema_version": 1,
            "canonicalizer": "workbench-canonical-json-v2",
            "plan_id": arguments["plan_id"],
            "context_ref_id": context_ref_id,
            "input_binding_id": input_binding_id,
            "registered": True,
        }


FeatureExecutionPort = Callable[
    [Path, Mapping[str, Any], str, ServiceExecutionContext],
    Mapping[str, Any],
]


def _embedded_feature_execution_port(
    suite_root: Path,
    owner_request: Mapping[str, Any],
    operation: str,
    _context: ServiceExecutionContext,
) -> Mapping[str, Any]:
    return execute_feature_request(
        suite_root,
        owner_request,
        expected_operation=operation,
    )


class FeatureStudioOperationHandler:
    """Execute one exact owner operation behind its admitted V3 capability."""

    def __init__(
        self,
        suite_root: Path,
        operation: str,
        *,
        execution_port: FeatureExecutionPort = _embedded_feature_execution_port,
    ) -> None:
        _require(
            isinstance(suite_root, Path)
            and suite_root.is_absolute()
            and operation in FEATURE_OPERATIONS
            and callable(execution_port),
            "feature-studio.handler",
            "Feature Studio operation handler configuration is invalid",
        )
        self.suite_root = suite_root
        self.operation = operation
        self.execution_port = execution_port

    def __call__(
        self,
        context: ServiceExecutionContext,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        require_material_fluid_profile()
        _require(
            _validate_feature_arguments(arguments, self.operation),
            "feature-studio.service-request",
            "Feature Studio service request differs from its capability",
        )
        _require(
            context.context_ref_id is not None
            and context.input_binding_id is not None,
            "feature-studio.context-binding",
            "Feature Studio service request lacks an exact context/input pair",
        )
        plan = arguments["operation_plan"]
        if self.operation in _DURABLE_OPERATIONS:
            _require(
                type(plan) is dict
                and plan["context_ref_id"] == context.context_ref_id
                and plan["input_binding_id"] == context.input_binding_id,
                "feature-studio.stale-plan",
                "Feature Studio service plan belongs to another exact context",
            )
        context.progress(
            "validating-owner-request",
            1,
            3,
            unit="owner-boundaries",
            message="Validated the closed owner request.",
        )
        context.checkpoint("feature-studio.before-owner-operation")
        mutating = self.operation in _DURABLE_OPERATIONS
        if mutating:
            context.set_mutation_state("external-mutation-started")
        context.progress(
            "executing-owner-operation",
            2,
            3,
            unit="owner-boundaries",
            message="Executing the owner composition.",
        )
        try:
            owner_request = _owner_request_from_arguments(
                arguments, expected_operation=self.operation
            )
            owner_result = self.execution_port(
                self.suite_root,
                owner_request,
                self.operation,
                context,
            )
            owner_value = validate_feature_result(
                owner_result,
                suite_root=self.suite_root,
            )
            _require(
                owner_value["operation"] == self.operation,
                "feature-studio.owner-result",
                "Feature Studio owner returned another operation result",
            )
        except ServiceCancelled:
            raise
        except Exception:
            if mutating and context.mutation_state in {
                "external-mutation-started",
                "external-mutation-partial",
            }:
                context.set_mutation_state("external-mutation-indeterminate")
            raise
        if mutating:
            context.set_mutation_state("external-mutation-completed")
        context.progress(
            "validating-owner-result",
            3,
            3,
            unit="owner-boundaries",
            message="Validated and retained the exact owner result.",
        )
        context.checkpoint("feature-studio.after-owner-operation")
        return _wrap_feature_result(
            self.operation,
            owner_request,
            owner_value,
            context.context_ref_id,
            context.input_binding_id,
            arguments["plan_id"],
        )


class FeatureStudioJobResultHandler:
    """Reopen one successful durable Feature Studio result."""

    def __init__(self, suite_root: Path) -> None:
        self.suite_root = suite_root

    def __call__(
        self,
        context: ServiceExecutionContext,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require(
            type(arguments) is dict
            and set(arguments)
            == {"canonicalizer", "format", "job_id", "schema_version"}
            and arguments["format"]
            == "workbench-feature-studio-job-result-request-v1"
            and arguments["schema_version"] == 1
            and arguments["canonicalizer"] == "workbench-canonical-json-v2"
            and type(arguments["job_id"]) is str
            and re.fullmatch(r"job-v2:[0-9a-f]{32}", arguments["job_id"])
            is not None,
            "feature-studio.job-result-request",
            "Feature Studio durable result request is invalid",
        )
        result = context.successful_job_result(arguments["job_id"])
        _require(
            validate_feature_service_result_v1(
                result, suite_root=self.suite_root
            ),
            "feature-studio.job-result",
            "Durable job result is not an exact Feature Studio wrapper",
        )
        return result


def _experimental_context_projection(
    context_value: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    profile_scope = context_value["profile_scope"]
    platform = profile_scope["platform"]
    refs = [
        {
            "profile_kind": "platform-profile-revision",
            "profile_revision_id": platform["platform_profile_revision_id"],
        }
    ]
    decisions = list(platform["support_decision_ids"])
    states = [platform["support_state"]]
    pack = profile_scope["pack"]
    if pack["kind"] == "selected":
        refs.append(
            {
                "profile_kind": "pack-profile-revision",
                "profile_revision_id": pack["pack_profile_revision_id"],
            }
        )
        decisions.extend(pack["support_decision_ids"])
        states.append(pack["support_state"])
    _require(
        set(states) == {"experimental"},
        "feature-studio.profile-scope",
        "Feature Studio accepts only its declared owner context",
    )
    return (
        sorted(
            refs,
            key=lambda row: (
                row["profile_kind"].encode("utf-8"),
                row["profile_revision_id"].encode("utf-8"),
            ),
        ),
        sorted(set(decisions)),
    )


def _context_publication_validator(context: Any, binding: Any) -> bool:
    try:
        context_value = context.to_dict()
        binding_value = binding.to_dict()
        _experimental_context_projection(context_value)
    except (AttributeError, KeyError, FeatureStudioServiceV3Error):
        return False
    return binding_value.get("context_ref_id") == context_value.get("id")


def _states(
    *,
    context_available: bool,
    profile_support: str = "not-applicable",
    profile_refs: list[dict[str, str]] | None = None,
    support_decisions: list[str] | None = None,
    consent: str = "not-applicable",
    continuity: str = "not-applicable",
    evidence: str = "not-applicable",
) -> dict[str, Any]:
    return {
        "action_gate": "not-required",
        "action_gate_decision_ids": [],
        "completeness": "bounded" if context_available else "complete",
        "consent": consent,
        "context": "available" if context_available else "not-applicable",
        "continuity": continuity,
        "evidence": evidence,
        "freshness": "fresh" if context_available else "not-applicable",
        "implementation": "available",
        "inputs": "available" if context_available else "not-applicable",
        "profile_revision_refs": profile_refs or [],
        "profile_support": profile_support,
        "support_decision_ids": support_decisions or [],
    }


def _context_projection_from_arguments(
    arguments: Mapping[str, Any],
) -> OwnerBindingProjection:
    context_value = arguments["context_ref"]
    refs, decisions = _experimental_context_projection(context_value)
    context_ref_id = context_value["id"]
    input_binding_id = arguments["input_binding"]["id"]
    return OwnerBindingProjection(
        input_revision_refs=(
            {"record_kind": "context-ref", "record_id": context_ref_id},
            {"record_kind": "input-binding", "record_id": input_binding_id},
            {"record_kind": "operation-plan", "record_id": arguments["plan_id"]},
        ),
        output_revision_refs=(
            {"record_kind": "context-ref", "record_id": context_ref_id},
            {"record_kind": "input-binding", "record_id": input_binding_id},
        ),
        states=_states(
            context_available=True,
            profile_support="experimental",
            profile_refs=refs,
            support_decisions=decisions,
            consent="granted",
            continuity="available",
            evidence="available",
        ),
        limitations=(
            {
                "ordinal": 0,
                "code": "experimental-context-custody-only",
                "detail": (
                    "Registration preserves the owner context; it is not a "
                    "profile support admission."
                ),
            },
        ),
    )


def _bound_projection(
    runtime: ServiceRuntimeV3,
    message: Mapping[str, Any],
    *,
    consent: str,
    continuity: str,
    evidence: str,
) -> OwnerBindingProjection:
    request = message["params"]["request"]
    context_ref_id = request["context_ref_id"]
    input_binding_id = request["input_binding_id"]
    context_raw, _ = runtime.store.context_bytes(
        context_ref_id, input_binding_id
    )
    context_value = parse_canonical_json(context_raw)
    refs, decisions = _experimental_context_projection(context_value)
    arguments = message["params"].get("arguments", {})
    input_refs: list[dict[str, str]] = [
        {"record_kind": "context-ref", "record_id": context_ref_id},
        {"record_kind": "input-binding", "record_id": input_binding_id},
    ]
    try:
        owner_request = _owner_request_from_arguments(arguments)
    except FeatureStudioServiceV3Error:
        owner_request = None
    if type(owner_request) is dict:
        input_refs.append(
            {
                "record_kind": "feature-studio-owner-request",
                "record_id": _owner_request_id(owner_request),
            }
        )
    plan_id = arguments.get("plan_id")
    if type(plan_id) is str:
        input_refs.append(
            {"record_kind": "operation-plan", "record_id": plan_id}
        )
    input_refs.sort(
        key=lambda row: (
            row["record_kind"].encode("utf-8"),
            row["record_id"].encode("utf-8"),
        )
    )
    return OwnerBindingProjection(
        input_revision_refs=tuple(input_refs),
        output_revision_refs=(),
        states=_states(
            context_available=True,
            profile_support="experimental",
            profile_refs=refs,
            support_decisions=decisions,
            consent=consent,
            continuity=continuity,
            evidence=evidence,
        ),
        limitations=(
            {
                "ordinal": 0,
                "code": "experimental-owner-context",
                "detail": (
                    "The response preserves experimental state from the exact "
                    "owner context and creates no support admission."
                ),
            },
        ),
    )


def _discovery_projection(_message: Mapping[str, Any]) -> OwnerBindingProjection:
    return OwnerBindingProjection(
        input_revision_refs=(),
        output_revision_refs=(),
        states=_states(context_available=False),
        limitations=(
            {
                "ordinal": 0,
                "code": "mechanical-service-discovery",
                "detail": "Discovery reports mechanics and grants no owner authority.",
            },
        ),
    )


@dataclass(slots=True)
class FeatureStudioServiceCompositionV3:
    bundle: FeatureStudioRegistryBundleV3
    runtime: ServiceRuntimeV3
    authenticator: LocalServiceAuthenticator
    host: ServiceHostV3

    @property
    def registry(self) -> Mapping[str, Any]:
        return self.bundle.registry

    @property
    def distribution(self) -> Mapping[str, Any]:
        return self.bundle.service_distribution

    def close(self) -> None:
        self.runtime.close()


def compose_feature_studio_service_v3(
    repository_root: Path,
    service_root: Path,
    *,
    execution_ports: Mapping[str, FeatureExecutionPort] | None = None,
    maximum_workers: int = 4,
    maximum_pending_jobs: int = 32,
) -> FeatureStudioServiceCompositionV3:
    """Compose the live handlers over one durable Crucible runtime."""

    _require(
        repository_root.is_absolute()
        and repository_root.is_dir()
        and service_root.is_absolute()
        and not service_root.is_symlink(),
        "feature-studio.service-root",
        "Feature Studio service roots are invalid",
    )
    service_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(service_root, 0o700)
    store = service_root / "store"
    credentials = service_root / "credentials"
    store.mkdir(mode=0o700, exist_ok=True)
    credentials.mkdir(mode=0o700, exist_ok=True)
    os.chmod(store, 0o700)
    os.chmod(credentials, 0o700)

    bundle = build_feature_studio_registry_v3(repository_root)
    registry = bundle.registry
    resources = _schema_resources(repository_root)
    diagnostics = validate_registry_v3(
        registry,
        resources,
        REGISTRY_SCHEMA_ID,
    )
    _require(
        not diagnostics,
        "feature-studio.registry-invalid",
        "Feature Studio registry failed V3 validation: "
        + ", ".join(row.code for row in diagnostics[:8]),
    )
    live_validator = _schema_validator(resources, LIVE_BINDING_SCHEMA_ID)
    _require(
        live_validator(bundle.live_binding_projection),
        "feature-studio.live-binding-invalid",
        "Feature Studio live binding projection is invalid",
    )

    registrations: list[ServiceHandlerRegistration] = []
    binding_specs: list[
        tuple[ServiceHandlerRegistration, str, Callable[[Mapping[str, Any]], OwnerBindingProjection]]
    ] = []

    def bind(
        capability_key: str,
        *,
        mutation_boundary: str,
        asynchronous: bool,
        maximum_concurrency: int,
        handler: Callable[[ServiceExecutionContext, Mapping[str, Any]], Any],
        request_validator: Callable[[Any], bool],
        result_validator: Callable[[Any], bool],
        projection: Callable[[Mapping[str, Any]], OwnerBindingProjection],
        context_binding: str | None = None,
        input_binding: str | None = None,
    ) -> None:
        descriptor = _descriptor(registry, capability_key)
        method_name = descriptor["method_bindings"][0]["protocol_method"]
        declared = _declaration(registry, descriptor["capability_id"])
        method_binding = _method(descriptor, method_name)
        request_schema_port = (
            _schema_validator(resources, method_binding["request_schema_id"])
            if method_binding["request_value_scope"] == "arguments"
            else lambda _value: True
        )
        result_schema_port = _schema_validator(
            resources, method_binding["result_schema_id"]
        )
        registration = ServiceHandlerRegistration(
            method=method_name,
            capability_id=descriptor["capability_id"],
            capability_version=descriptor["semantic_version"],
            handler_id=declared["handler_id"],
            implementation_id=declared["handler_implementation_id"],
            mutation_boundary=mutation_boundary,
            asynchronous=asynchronous,
            maximum_concurrency=maximum_concurrency,
            handler=handler,
            request_validator=lambda value: (
                request_validator(value)
                and request_schema_port(value)
            ),
            result_validator=lambda value: (
                result_validator(value)
                and result_schema_port(value)
            ),
            context_binding=(
                descriptor["context_applicability"]["context_binding"]
                if context_binding is None
                else context_binding
            ),
            input_binding=(
                descriptor["context_applicability"]["input_binding"]
                if input_binding is None
                else input_binding
            ),
        )
        registrations.append(registration)
        binding_specs.append(
            (registration, method_binding["result_schema_id"], projection)
        )

    bind(
        CONTEXT_REGISTRATION_CAPABILITY_KEY,
        mutation_boundary="protected-state",
        asynchronous=False,
        maximum_concurrency=1,
        handler=ContextRegistrationHandler(),
        request_validator=_validate_context_registration_arguments,
        result_validator=lambda value: type(value) is dict,
        projection=lambda message: _context_projection_from_arguments(
            message["params"]["arguments"]
        ),
    )

    selected_ports = dict(execution_ports or {})
    for operation in FEATURE_OPERATIONS:
        port = selected_ports.get(operation, _embedded_feature_execution_port)
        bind(
            FEATURE_CAPABILITY_KEYS[operation],
            mutation_boundary=(
                "external-side-effect"
                if operation in _DURABLE_OPERATIONS
                else "none"
            ),
            asynchronous=operation in _DURABLE_OPERATIONS,
            maximum_concurrency=1 if operation in _DURABLE_OPERATIONS else 4,
            handler=FeatureStudioOperationHandler(
                repository_root,
                operation,
                execution_port=port,
            ),
            request_validator=lambda value, selected=operation: (
                _validate_feature_arguments(value, selected)
            ),
            result_validator=lambda value: validate_feature_service_result_v1(
                value, suite_root=repository_root
            ),
            projection=lambda message, selected=operation: _bound_projection(
                runtime,
                message,
                consent=(
                    "granted" if selected in _DURABLE_OPERATIONS else "not-applicable"
                ),
                continuity=(
                    "available"
                    if selected in _DURABLE_OPERATIONS
                    else "not-applicable"
                ),
                evidence="incomplete",
            ),
        )

    bind(
        FEATURE_JOB_RESULT_CAPABILITY_KEY,
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=4,
        handler=FeatureStudioJobResultHandler(repository_root),
        request_validator=lambda value: (
            type(value) is dict
            and set(value)
            == {"canonicalizer", "format", "job_id", "schema_version"}
        ),
        result_validator=lambda value: validate_feature_service_result_v1(
            value, suite_root=repository_root
        ),
        projection=lambda message: _bound_projection(
            runtime,
            message,
            consent="not-applicable",
            continuity="available",
            evidence="available",
        ),
    )

    capability_ids = tuple(
        sorted(row["capability_id"] for row in registry["capability_descriptors"])
    )
    bind(
        "crucible.service.capabilities",
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=2,
        handler=ServiceCapabilitiesHandler(registry["registry_id"], capability_ids),
        request_validator=validate_empty_service_arguments,
        result_validator=validate_service_capabilities_result,
        projection=_discovery_projection,
    )
    bind(
        "crucible.service.context-list",
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=2,
        handler=ContextListHandler(),
        request_validator=validate_empty_service_arguments,
        result_validator=validate_context_list_result,
        projection=_discovery_projection,
    )
    bind(
        "crucible.service.job-cancel",
        mutation_boundary="protected-state",
        asynchronous=False,
        maximum_concurrency=2,
        handler=JobCancellationHandler(),
        request_validator=validate_job_cancellation_arguments,
        result_validator=validate_job_handle_result,
        projection=lambda message: _bound_projection(
            runtime,
            message,
            consent="granted",
            continuity="available",
            evidence="available",
        ),
    )
    bind(
        "crucible.service.job-subscribe",
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=4,
        handler=JobSubscriptionHandler(),
        request_validator=validate_job_subscription_arguments,
        result_validator=validate_job_subscription_result,
        projection=lambda message: _bound_projection(
            runtime,
            message,
            consent="not-applicable",
            continuity="available",
            evidence="available",
        ),
    )
    bind(
        "crucible.service.job-event-page",
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=4,
        handler=JobEventPageHandler(),
        request_validator=validate_job_event_page_arguments,
        result_validator=validate_job_event_page_result,
        projection=lambda message: _bound_projection(
            runtime,
            message,
            consent="not-applicable",
            continuity="available",
            evidence="available",
        ),
    )

    runtime = ServiceRuntimeV3(store, registrations=tuple(registrations), physical_leases=local_service_physical_lease_ports(), maximum_workers=maximum_workers, maximum_pending_jobs=maximum_pending_jobs, store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=_context_publication_validator))
    authenticator = LocalServiceAuthenticator(credentials)
    try:
        host = ServiceHostV3(
            runtime,
            authenticator=authenticator,
            registry=registry,
            registry_validation_port=lambda candidate: not validate_registry_v3(
                candidate,
                resources,
                REGISTRY_SCHEMA_ID,
            ),
            method_bindings=tuple(
                HostedMethodBinding(registration, result_schema, projection)
                for registration, result_schema, projection in binding_specs
            ),
        )
    except Exception:
        runtime.close()
        raise
    return FeatureStudioServiceCompositionV3(
        bundle=bundle,
        runtime=runtime,
        authenticator=authenticator,
        host=host,
    )


class FeatureStudioServiceClientV3:
    """IDE-neutral client for embedded, endpoint, and stdio V3 sessions."""

    def __init__(
        self,
        registry: Mapping[str, Any],
        call_port: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        *,
        transport: str,
    ) -> None:
        _require(
            type(registry) is dict
            and callable(call_port)
            and transport in {"embedded", "local-endpoint", "stdio"},
            "feature-studio.client",
            "Feature Studio service client configuration is invalid",
        )
        self.registry = _snapshot(registry)
        self.call_port = call_port
        self.transport = transport
        self._sequence = 0
        self.initialized = False
        self.service_instance_id: str | None = None
        self.actor_id = content_id(
            "actor", {"actor": "workbench-local-service-v3"}
        )

    @classmethod
    def embedded(
        cls, composition: FeatureStudioServiceCompositionV3
    ) -> "FeatureStudioServiceClientV3":
        session = composition.host.new_session("embedded")
        token = composition.authenticator.token
        return cls(
            composition.registry,
            lambda message: session.handle_message(
                dict(message), bearer_token=token
            ),
            transport="embedded",
        )

    def _next(self, prefix: str) -> tuple[int, str]:
        self._sequence += 1
        return (
            self._sequence,
            f"{prefix}:{os.urandom(16).hex()}",
        )

    def _call(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self.call_port(_snapshot(message))
        _require(
            type(response) is dict,
            "feature-studio.transport",
            "Feature Studio service returned no response",
        )
        if "error" in response:
            error = response["error"]
            kind = error.get("data", {}).get("kind", "service-error")
            raise FeatureStudioServiceV3Error(kind, error.get("message", kind))
        _require(
            set(response) == {"id", "jsonrpc", "result"}
            and response["jsonrpc"] == "2.0",
            "feature-studio.transport",
            "Feature Studio service response envelope is invalid",
        )
        return response["result"]

    def initialize(self) -> Mapping[str, Any]:
        identifier, request_id = self._next("request-v3")
        required_ids = [
            row["capability_id"]
            for row in self.registry["capability_descriptors"]
        ]
        result = self._call(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": "service/initialize",
                "params": {
                    "protocol_version": dict(SERVICE_PROTOCOL_VERSION),
                    "request_id": request_id,
                    "client": {
                        "client_id": "workbench.feature-studio",
                        "kind": "ide-neutral-client",
                        "version": "1.0.0",
                    },
                    "transport": self.transport,
                    "required_capability_ids": sorted(required_ids),
                    "required_features": [
                        "contexts",
                        "durable-jobs",
                        "progress-and-cancellation",
                        "subscriptions",
                    ],
                    "maximum_frame_bytes": 4194304,
                },
            }
        )
        _require(
            result["registry_id"] == self.registry["registry_id"],
            "feature-studio.registry-drift",
            "Feature Studio client initialized against another registry",
        )
        self.initialized = True
        self.service_instance_id = result["service_instance_id"]
        return result

    def _binding(
        self,
        capability_key: str,
        *,
        context_ref_id: str | None,
        input_binding_id: str | None,
        idempotency_key: str | None,
        commit: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        _require(
            self.initialized,
            "feature-studio.client-uninitialized",
            "Feature Studio client must initialize before dispatch",
        )
        descriptor = _descriptor(self.registry, capability_key)
        _, request_id = self._next("request-v3")
        return {
            "protocol_version": dict(SERVICE_PROTOCOL_VERSION),
            "request_id": request_id,
            "capability_id": descriptor["capability_id"],
            "capability_version": descriptor["semantic_version"],
            "context_ref_id": context_ref_id,
            "input_binding_id": input_binding_id,
            "intent": capability_key,
            "operation_class": descriptor["operation"]["operation_class"],
            "resource_budgets": [],
            "deadline": None,
            "idempotency_key": idempotency_key,
            "commit": None if commit is None else dict(commit),
        }

    def _generic(
        self,
        capability_key: str,
        arguments: Mapping[str, Any],
        *,
        context_ref_id: str | None,
        input_binding_id: str | None,
        idempotency_key: str | None = None,
        commit: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        descriptor = _descriptor(self.registry, capability_key)
        method_name = descriptor["method_bindings"][0]["protocol_method"]
        identifier, _ = self._next("jsonrpc")
        return self._call(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": method_name,
                "params": {
                    "request": self._binding(
                        capability_key,
                        context_ref_id=context_ref_id,
                        input_binding_id=input_binding_id,
                        idempotency_key=idempotency_key,
                        commit=commit,
                    ),
                    "arguments": _snapshot(arguments),
                },
            }
        )

    def register_context(
        self,
        context_ref: Mapping[str, Any],
        input_binding: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        plan = seal_context_registration_plan_v1(context_ref, input_binding)
        idempotency_key = f"idempotency-v3:{os.urandom(16).hex()}"
        arguments = {
            "format": "workbench-feature-studio-context-registration-v1",
            "schema_version": 1,
            "canonicalizer": "workbench-canonical-json-v2",
            "plan_id": plan["id"],
            "plan": plan,
            "consent_record_id": plan["consent_record_id"],
            "expected_head_refs": plan["expected_head_refs"],
            "context_ref": _snapshot(context_ref),
            "input_binding": _snapshot(input_binding),
        }
        return self._generic(
            CONTEXT_REGISTRATION_CAPABILITY_KEY,
            arguments,
            context_ref_id=None,
            input_binding_id=None,
            idempotency_key=idempotency_key,
            commit={
                "plan_id": plan["id"],
                "consent_record_id": plan["consent_record_id"],
                "expected_head_refs": plan["expected_head_refs"],
                "idempotency_key": idempotency_key,
            },
        )

    def submit(
        self,
        owner_request: Mapping[str, Any],
        *,
        context_ref_id: str,
        input_binding_id: str,
    ) -> Mapping[str, Any]:
        arguments = feature_service_arguments_v1(
            owner_request, context_ref_id, input_binding_id
        )
        operation = arguments["operation"]
        idempotency_key = (
            f"idempotency-v3:{os.urandom(16).hex()}"
            if operation in _DURABLE_OPERATIONS
            else None
        )
        commit = (
            {
                "plan_id": arguments["plan_id"],
                "consent_record_id": arguments["consent_record_id"],
                "expected_head_refs": arguments["expected_head_refs"],
                "idempotency_key": idempotency_key,
            }
            if operation in _DURABLE_OPERATIONS
            else None
        )
        return self._generic(
            FEATURE_CAPABILITY_KEYS[operation],
            arguments,
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
            idempotency_key=idempotency_key,
            commit=commit,
        )

    def subscribe(
        self,
        job: Mapping[str, Any],
        *,
        context_ref_id: str,
        input_binding_id: str,
        position: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        capability_key = "crucible.service.job-subscribe"
        identifier, _ = self._next("jsonrpc")
        return self._call(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": "job/subscribe",
                "params": {
                    "request": self._binding(
                        capability_key,
                        context_ref_id=context_ref_id,
                        input_binding_id=input_binding_id,
                        idempotency_key=None,
                        commit=None,
                    ),
                    "scope": {
                        "kind": "job",
                        "job_id": job["job_id"],
                        "job_submission_id": job["job_submission_id"],
                    },
                    "event_families": ["job"],
                    "delivery": "lossless",
                    "position": (
                        {"kind": "start"}
                        if position is None
                        else _snapshot(position)
                    ),
                },
            }
        )

    def event_page(
        self,
        job: Mapping[str, Any],
        subscription: Mapping[str, Any],
        *,
        context_ref_id: str,
        input_binding_id: str,
        last_cursor: int,
        maximum_events: int = 64,
    ) -> Mapping[str, Any]:
        return self._generic(
            "crucible.service.job-event-page",
            {
                "job_id": job["job_id"],
                "job_submission_id": job["job_submission_id"],
                "last_cursor": last_cursor,
                "maximum_events": maximum_events,
                "stream_generation": subscription["stream_generation"],
                "subscription_id": subscription["subscription_id"],
            },
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
        )

    def cancel(
        self,
        job: Mapping[str, Any],
        *,
        context_ref_id: str,
        input_binding_id: str,
        expected_event_id: str,
        expected_event_ordinal: int,
        reason: str,
    ) -> Mapping[str, Any]:
        from workbench_crucible_service import seal_job_cancellation_plan

        plan = seal_job_cancellation_plan(
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
            job_id=job["job_id"],
            expected_event_id=expected_event_id,
            expected_event_ordinal=expected_event_ordinal,
            reason=reason,
        )
        capability_key = "crucible.service.job-cancel"
        identifier, _ = self._next("jsonrpc")
        idempotency_key = f"idempotency-v3:{os.urandom(16).hex()}"
        consent_record_id = content_id(
            "consent-decision",
            {
                "decision": "granted",
                "job_id": job["job_id"],
                "operation_plan_id": plan.id,
            },
        )
        expected_heads = [
            {
                "record_kind": "job-submission",
                "record_id": job["job_submission_id"],
            }
        ]
        return self._call(
            {
                "jsonrpc": "2.0",
                "id": identifier,
                "method": "job/cancel",
                "params": {
                    "request": self._binding(
                        capability_key,
                        context_ref_id=context_ref_id,
                        input_binding_id=input_binding_id,
                        idempotency_key=idempotency_key,
                        commit={
                            "plan_id": plan.id,
                            "consent_record_id": consent_record_id,
                            "expected_head_refs": expected_heads,
                            "idempotency_key": idempotency_key,
                        },
                    ),
                    "job_id": job["job_id"],
                    "expected_event_id": expected_event_id,
                    "expected_event_ordinal": expected_event_ordinal,
                    "requester_id": self.actor_id,
                    "reason": reason,
                },
            }
        )

    def result(
        self,
        job_id: str,
        *,
        context_ref_id: str,
        input_binding_id: str,
    ) -> Mapping[str, Any]:
        return self._generic(
            FEATURE_JOB_RESULT_CAPABILITY_KEY,
            {
                "format": "workbench-feature-studio-job-result-request-v1",
                "schema_version": 1,
                "canonicalizer": "workbench-canonical-json-v2",
                "job_id": job_id,
            },
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
        )

    def wait_for_terminal(
        self,
        job: Mapping[str, Any],
        subscription: Mapping[str, Any],
        *,
        context_ref_id: str,
        input_binding_id: str,
        timeout_seconds: float = 30.0,
        last_cursor: int = -1,
    ) -> tuple[list[Mapping[str, Any]], int]:
        deadline = time.monotonic() + timeout_seconds
        cursor = last_cursor
        events: list[Mapping[str, Any]] = []
        while time.monotonic() < deadline:
            page_result = self.event_page(
                job,
                subscription,
                context_ref_id=context_ref_id,
                input_binding_id=input_binding_id,
                last_cursor=cursor,
            )
            page = page_result["outcome"]["value"]
            events.extend(page["events"])
            if page["events"]:
                cursor = page["events"][-1]["event_ordinal"]
                if page["events"][-1]["lifecycle_state"] == "terminal":
                    return events, cursor
            time.sleep(0.02)
        raise FeatureStudioServiceV3Error(
            "feature-studio.job-timeout",
            "Feature Studio durable job did not reach a terminal event in time",
        )


__all__ = [
    "ContextRegistrationHandler",
    "FeatureExecutionPort",
    "FeatureStudioJobResultHandler",
    "FeatureStudioOperationHandler",
    "FeatureStudioServiceClientV3",
    "FeatureStudioServiceCompositionV3",
    "FeatureStudioServiceV3Error",
    "compose_feature_studio_service_v3",
    "feature_service_arguments_v1",
    "owner_result_from_feature_service_result_v1",
    "seal_context_registration_consent_v1",
    "seal_context_registration_plan_v1",
    "seal_feature_operation_consent_v1",
    "seal_feature_operation_plan_v1",
    "validate_feature_service_result_v1",
]
