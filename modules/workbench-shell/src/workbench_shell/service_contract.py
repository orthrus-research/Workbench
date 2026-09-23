"""Executable Workbench service V3 contract validation.

This module validates immutable registry composition and correlated protocol
values.  It deliberately contains no transport loop, handler dispatch, owner
semantics, consent UI, or stdio implementation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, fields
import hashlib
from typing import Any, Callable, Literal, Mapping, Sequence

from jsonschema import Draft202012Validator
from referencing import Registry

from workbench_api.canonical import (
    canonical_json_bytes,
    content_id,
    parse_canonical_json,
)


MUTATING_OPERATION_CLASSES = frozenset(
    {
        "experimental-materialize",
        "disposable-runtime-execute",
        "developer-world-mutate",
        "stable-construct",
        "publish-or-release",
    }
)
HAZARDOUS_SIDE_EFFECTS = frozenset(
    {
        "store-append",
        "reference-update",
        "managed-target-create",
        "runtime-process",
        "instrumentation",
        "world-mutation",
        "external-export",
    }
)


@dataclass(frozen=True)
class AuthorityAdmissionRequest:
    projection_kind: Literal["authority-admission"]
    role: Literal["semantic-owner", "profile-owner"]
    admission_canonical_bytes: bytes
    capability_canonical_bytes: bytes


@dataclass(frozen=True)
class ProfileSupportDecisionRequest:
    projection_kind: Literal["profile-support-decision"]
    decision_ids: tuple[str, ...]
    support_state: str
    profile_refs_canonical_bytes: bytes
    request_canonical_bytes: bytes
    result_canonical_bytes: bytes
    capability_canonical_bytes: bytes


@dataclass(frozen=True)
class ContextInputApplicabilityRequest:
    projection_kind: Literal["context-input-applicability"]
    purpose: Literal["request", "response", "resolved-result"]
    context_ref_id: str
    input_binding_id: str | None
    request_canonical_bytes: bytes
    result_canonical_bytes: bytes | None
    capability_canonical_bytes: bytes


@dataclass(frozen=True)
class ActionGateDecisionRequest:
    projection_kind: Literal["action-gate-decision"]
    decision_ids: tuple[str, ...]
    action_gate_state: str
    request_canonical_bytes: bytes
    result_canonical_bytes: bytes
    capability_canonical_bytes: bytes


@dataclass(frozen=True)
class JobProjectionValidationRequest:
    projection_kind: Literal["accepted-handle", "progress", "job-bearing-failure"]
    job_id: str
    job_submission_id: str | None
    job_event_id: str | None
    job_event_ordinal: int | None
    terminal_seal_id: str | None
    context_ref_id: str | None
    input_binding_id: str | None
    capability_id: str | None
    request_canonical_bytes: bytes | None
    message_canonical_bytes: bytes
    capability_canonical_bytes: bytes | None


AdmissionValidator = Callable[[AuthorityAdmissionRequest], bool]
SupportDecisionValidator = Callable[[ProfileSupportDecisionRequest], bool]
ContextInputApplicabilityValidator = Callable[[ContextInputApplicabilityRequest], bool]
ActionGateDecisionValidator = Callable[[ActionGateDecisionRequest], bool]
JobProjectionValidator = Callable[[JobProjectionValidationRequest], bool]

_PORT_REQUEST_TYPES = frozenset(
    {
        AuthorityAdmissionRequest,
        ProfileSupportDecisionRequest,
        ContextInputApplicabilityRequest,
        ActionGateDecisionRequest,
        JobProjectionValidationRequest,
    }
)

# These are lower bounds.  An owner may declare a more restrictive operation,
# but never a weaker one for a method with known effects.
METHOD_OPERATION_MINIMUMS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "evidence/commitAdmission": (
        frozenset({"stable-construct", "publish-or-release"}),
        frozenset({"store-append"}),
    ),
    "graph/materialize": (
        frozenset({"experimental-materialize", "stable-construct"}),
        frozenset({"store-append"}),
    ),
    "operation/commit": (
        frozenset({"stable-construct", "developer-world-mutate", "publish-or-release"}),
        frozenset({"store-append", "reference-update"}),
    ),
    "job/cancel": (
        frozenset({"disposable-runtime-execute"}),
        frozenset({"store-append"}),
    ),
    "runtime/start": (
        frozenset({"disposable-runtime-execute"}),
        frozenset({"runtime-process"}),
    ),
    "runtime/stop": (
        frozenset({"disposable-runtime-execute"}),
        frozenset({"runtime-process"}),
    ),
    "runtime/instrument": (
        frozenset({"disposable-runtime-execute"}),
        frozenset({"instrumentation"}),
    ),
    "context/register": (
        frozenset({"stable-construct"}),
        frozenset({"store-append"}),
    ),
    "session/export": (
        frozenset({"publish-or-release"}),
        frozenset({"external-export"}),
    ),
}


@dataclass(frozen=True, order=True)
class ContractDiagnostic:
    """A stable, machine-comparable contract rejection."""

    code: str
    path: str = "$"
    detail: str = ""


def _canonical_snapshot(value: Any) -> Any:
    """Return an isolated exact-JSON snapshot or raise CanonicalJsonError."""

    return parse_canonical_json(canonical_json_bytes(value))


def _snapshot_port_value(
    value: Any, active: set[int]
) -> tuple[Any, tuple[Any, ...]]:
    """Reconstruct a supported port value and return an exact-type signature."""

    if value is None:
        return None, ("none",)
    if type(value) is bool:
        return value, ("bool", value)
    if type(value) is int:
        return value, ("int", value)
    if type(value) is str:
        return value, ("str", value)
    if type(value) is bytes:
        return bytes(value), ("bytes", value)

    marker = id(value)
    if marker in active:
        raise TypeError("cyclic port request value")
    active.add(marker)
    try:
        if type(value) is tuple:
            items = [_snapshot_port_value(item, active) for item in value]
            return (
                tuple(item[0] for item in items),
                ("tuple", tuple(item[1] for item in items)),
            )
        if type(value) is list:
            items = [_snapshot_port_value(item, active) for item in value]
            return (
                [item[0] for item in items],
                ("list", tuple(item[1] for item in items)),
            )
        if type(value) is dict:
            items = [
                (
                    _snapshot_port_value(key, active),
                    _snapshot_port_value(item, active),
                )
                for key, item in value.items()
            ]
            return (
                {key[0]: item[0] for key, item in items},
                (
                    "dict",
                    tuple((key[1], item[1]) for key, item in items),
                ),
            )
    finally:
        active.remove(marker)
    raise TypeError(f"unsupported port request value {type(value).__name__}")


def _snapshot_port_request(request: Any) -> tuple[Any, tuple[Any, ...]]:
    """Return a detached request plus a signature that detects force-mutation."""

    request_type = type(request)
    if request_type not in _PORT_REQUEST_TYPES:
        raise TypeError(f"unsupported port request {request_type.__name__}")
    values: dict[str, Any] = {}
    signatures: list[tuple[str, tuple[Any, ...]]] = []
    for field in fields(request_type):
        value, signature = _snapshot_port_value(getattr(request, field.name), set())
        values[field.name] = value
        signatures.append((field.name, signature))
    return request_type(**values), (request_type.__name__, tuple(signatures))


def _call_required_port(
    validator: Callable[[Any], bool] | None,
    request: Any,
    errors: set[str],
    *,
    unverified_code: str,
    exception_code: str,
    mutation_code: str,
) -> None:
    if validator is None:
        errors.add(unverified_code)
        return

    try:
        isolated_request, before = _snapshot_port_request(request)
    except Exception:
        errors.add(mutation_code)
        return

    accepted: Any = None
    callback_failed = False
    try:
        accepted = validator(isolated_request)
    except Exception:
        callback_failed = True
        errors.add(exception_code)

    mutated = False
    try:
        _, after = _snapshot_port_request(isolated_request)
        mutated = after != before
    except Exception:
        mutated = True
    if mutated:
        errors.add(mutation_code)
    if not callback_failed and not mutated and accepted is not True:
        errors.add(unverified_code)


def _validator(resources: Registry, schema_id: str) -> Draft202012Validator:
    resolved = resources.resolver().lookup(schema_id)
    return Draft202012Validator(
        resolved.contents,
        registry=resources,
        _resolver=resolved.resolver,
    )


def _json_key(value: Any) -> bytes:
    if isinstance(value, tuple):
        return b"\0".join(str(item).encode("utf-8") for item in value)
    return str(value).encode("utf-8")


def _ordered_unique(
    rows: Sequence[Any], key: Any, code: str, errors: set[str]
) -> None:
    keys = [key(row) for row in rows]
    encoded = [_json_key(value) for value in keys]
    if len(encoded) != len(set(encoded)) or encoded != sorted(encoded):
        errors.add(code)


def _record_ref_errors(value: Any, code: str) -> set[str]:
    errors: set[str] = set()
    if isinstance(value, dict):
        if "record_kind" in value and "record_id" in value:
            prefix = value["record_id"].split(":sha256:", 1)[0]
            if prefix != value["record_kind"]:
                errors.add(code)
        if "profile_kind" in value and "profile_revision_id" in value:
            prefix = value["profile_revision_id"].split(":sha256:", 1)[0]
            if prefix != value["profile_kind"]:
                errors.add(code)
        for item in value.values():
            errors.update(_record_ref_errors(item, code))
    elif isinstance(value, list):
        for item in value:
            errors.update(_record_ref_errors(item, code))
    return errors


def schema_is_recursively_closed(resources: Registry, schema_id: str) -> bool:
    """Return whether every value-bearing object/array path is fail-closed."""

    resolved = resources.resolver().lookup(schema_id)
    active: set[int] = set()

    def closed(schema: Any, resolver: Any) -> bool:
        if schema is False:
            return True
        if schema is True or not isinstance(schema, dict):
            return False
        marker = id(schema)
        if marker in active:
            return False
        active.add(marker)
        try:
            if "$ref" in schema:
                target = resolver.lookup(schema["$ref"])
                return closed(target.contents, target.resolver)
            if "$dynamicRef" in schema:
                return False
            declared = schema.get("type")
            if isinstance(declared, list):
                return all(closed({**schema, "type": item}, resolver) for item in declared)
            if declared is None and any(
                key in schema
                for key in (
                    "properties",
                    "patternProperties",
                    "additionalProperties",
                    "dependentSchemas",
                    "items",
                    "prefixItems",
                )
            ):
                # Object/array applicators do not constrain instances of the
                # other JSON types.  Requiring an explicit type prevents an
                # apparently closed object schema from admitting arbitrary
                # arrays (and vice versa).
                return False
            if declared == "object":
                if schema.get("additionalProperties") is not False and schema.get(
                    "unevaluatedProperties"
                ) is not False:
                    return False
                children = list(schema.get("properties", {}).values())
                children.extend(schema.get("patternProperties", {}).values())
                children.extend(schema.get("dependentSchemas", {}).values())
                return all(value_closed(item, resolver) for item in children)
            if declared == "array":
                if "items" not in schema:
                    return False
                if schema["items"] is not False and not value_closed(schema["items"], resolver):
                    return False
                return all(value_closed(item, resolver) for item in schema.get("prefixItems", []))
            if "oneOf" in schema or "anyOf" in schema:
                branches = schema.get("oneOf", schema.get("anyOf", []))
                return bool(branches) and all(closed(item, resolver) for item in branches)
            if "allOf" in schema:
                return any(closed(item, resolver) for item in schema["allOf"])
            if declared in {"string", "integer", "number", "boolean", "null"}:
                return True
            return "const" in schema or "enum" in schema
        finally:
            active.remove(marker)

    def value_closed(schema: Any, resolver: Any) -> bool:
        if schema is False:
            return True
        if schema is True or not isinstance(schema, dict):
            return False
        if "$ref" in schema or "$dynamicRef" in schema:
            return closed(schema, resolver)
        if "oneOf" in schema or "anyOf" in schema or "allOf" in schema:
            return closed(schema, resolver)
        declared = schema.get("type")
        if declared in {"object", "array"} or isinstance(declared, list):
            return closed(schema, resolver)
        if "properties" in schema or "items" in schema or "prefixItems" in schema:
            return closed(schema, resolver)
        return "const" in schema or "enum" in schema or declared in {
            "string",
            "integer",
            "number",
            "boolean",
            "null",
        }

    return closed(resolved.contents, resolved.resolver)


def _authority_tuple(binding: Mapping[str, Any] | None) -> tuple[Any, Any, Any] | None:
    if binding is None:
        return None
    return (
        binding["owner_authority_id"],
        binding["owner_revision_id"],
        binding["authority_adapter_id"],
    )


def _method_index(
    registry: Mapping[str, Any],
) -> dict[tuple[str, str], tuple[Mapping[str, Any], Mapping[str, Any]]]:
    index: dict[tuple[str, str], tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    for capability in registry["capability_descriptors"]:
        for binding in capability["method_bindings"]:
            index[(capability["capability_id"], binding["protocol_method"])] = (
                capability,
                binding,
            )
    return index


def _registry_semantic_codes(
    registry: Mapping[str, Any],
    resources: Registry,
    admission_validator: AdmissionValidator | None = None,
) -> set[str]:
    """Validate registry identity, authority, topology, and dispatch semantics."""

    errors: set[str] = set()
    body = dict(registry)
    body.pop("registry_id", None)
    if registry.get("registry_id") != content_id("component-capability-registry", body):
        errors.add("registry.identity")

    providers = registry["providers"]
    components = registry["components"]
    capabilities = registry["capability_descriptors"]
    admissions = registry["descriptor_authority_admissions"]
    profile_admissions = registry["profile_policy_admissions"]
    handlers = registry["handler_registrations"]

    _ordered_unique(providers, lambda row: row["provider_key"], "registry.provider-order", errors)
    _ordered_unique(components, lambda row: row["component_key"], "registry.component-order", errors)
    _ordered_unique(capabilities, lambda row: row["capability_key"], "registry.capability-order", errors)
    _ordered_unique(admissions, lambda row: row["capability_id"], "registry.admission-order", errors)
    _ordered_unique(
        profile_admissions,
        lambda row: (
            row["capability_id"],
            row["profile_revision_ref"]["profile_kind"],
            row["profile_revision_ref"]["profile_revision_id"],
        ),
        "registry.profile-admission-order",
        errors,
    )
    _ordered_unique(handlers, lambda row: row["registration_id"], "registry.handler-order", errors)

    for rows, key, code in (
        (providers, "provider_id", "registry.provider-id-duplicate"),
        (components, "component_id", "registry.component-id-duplicate"),
        (capabilities, "capability_id", "registry.capability-id-duplicate"),
        (capabilities, "capability_key", "registry.capability-key-duplicate"),
        (handlers, "registration_id", "registry.registration-id-duplicate"),
    ):
        values = [row[key] for row in rows]
        if len(values) != len(set(values)):
            errors.add(code)

    provider_ids = {row["provider_id"] for row in providers}
    component_by_id = {row["component_id"]: row for row in components}
    capability_by_id = {row["capability_id"]: row for row in capabilities}
    admission_by_capability = {row["capability_id"]: row for row in admissions}
    profile_admission_by_key = {
        (
            row["capability_id"],
            row["profile_revision_ref"]["profile_kind"],
            row["profile_revision_ref"]["profile_revision_id"],
        ): row
        for row in profile_admissions
    }
    if len(admission_by_capability) != len(admissions):
        errors.add("registry.admission-duplicate")
    if len(profile_admission_by_key) != len(profile_admissions):
        errors.add("registry.profile-admission-duplicate")

    for provider in providers:
        provider_body = dict(provider)
        provider_body.pop("provider_id", None)
        if provider["provider_id"] != content_id("provider", provider_body):
            errors.add("registry.provider-identity")

    for component in components:
        component_body = dict(component)
        component_body.pop("component_id", None)
        if component["component_id"] != content_id("component", component_body):
            errors.add("registry.component-identity")
        _ordered_unique(
            component["public_contract_schema_ids"],
            lambda value: value,
            "registry.component-schema-order",
            errors,
        )
        _ordered_unique(
            component["depends_on_component_ids"],
            lambda value: value,
            "registry.component-dependency-order",
            errors,
        )
        if component["provider_id"] not in provider_ids:
            errors.add("registry.provider-reference")
        if component["component_id"] in component["depends_on_component_ids"]:
            errors.add("registry.topology-self-edge")
        if any(dep not in component_by_id for dep in component["depends_on_component_ids"]):
            errors.add("registry.component-reference")
        authority = component["semantic_authority_binding"]
        if authority is not None and (
            authority["owner_authority_id"] != component["semantic_authority_id"]
            or authority["authority_adapter_id"] != component["authority_adapter_id"]
        ):
            errors.add("registry.component-authority-binding")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visiting:
            errors.add("registry.topology-cycle")
            return
        if component_id in visited:
            return
        visiting.add(component_id)
        for dependency in component_by_id[component_id]["depends_on_component_ids"]:
            if dependency in component_by_id:
                visit(dependency)
        visiting.remove(component_id)
        visited.add(component_id)

    for component_id in component_by_id:
        visit(component_id)

    for capability in capabilities:
        cap_body = dict(capability)
        cap_body.pop("capability_id", None)
        if capability["capability_id"] != content_id("capability", cap_body):
            errors.add("registry.capability-identity")
        component = component_by_id.get(capability["owner_component_id"])
        if component is None:
            errors.add("registry.owner-component")
            continue
        if capability["provider_id"] != component["provider_id"]:
            errors.add("registry.provider")
        if capability["semantic_authority_id"] != component["semantic_authority_id"]:
            errors.add("registry.authority")
        if capability["semantic_authority_binding"] != component["semantic_authority_binding"]:
            errors.add("registry.authority-binding")

        authority = capability["semantic_authority_binding"]
        admission = admission_by_capability.get(capability["capability_id"])
        if authority is None:
            if admission is not None:
                errors.add("registry.mechanical-authority-admission")
        elif admission is None:
            errors.add("registry.authority-attestation-missing")
        else:
            attestation = admission["attestation"]
            attestation_body = dict(attestation)
            attestation_body.pop("attestation_id", None)
            if attestation["attestation_id"] != content_id(
                "capability-authority-attestation", attestation_body
            ):
                errors.add("registry.authority-attestation-identity")
            if (
                admission["authority_binding"] != authority
                or attestation["authority_binding"] != authority
                or attestation["capability_id"] != capability["capability_id"]
            ):
                errors.add("registry.authority-attestation-binding")
            _call_required_port(
                admission_validator,
                AuthorityAdmissionRequest(
                    projection_kind="authority-admission",
                    role="semantic-owner",
                    admission_canonical_bytes=canonical_json_bytes(admission),
                    capability_canonical_bytes=canonical_json_bytes(capability),
                ),
                errors,
                unverified_code="registry.authority-attestation-unverified",
                exception_code="registry.authority-attestation-port-exception",
                mutation_code="registry.authority-attestation-port-mutation",
            )

        method_bindings = capability["method_bindings"]
        _ordered_unique(
            method_bindings,
            lambda row: row["protocol_method"],
            "registry.method-order",
            errors,
        )
        flow = capability["record_flow"]
        _ordered_unique(flow["required_record_kinds"], lambda value: value, "registry.record-flow-order", errors)
        _ordered_unique(flow["produced_record_kinds"], lambda value: value, "registry.record-flow-order", errors)
        applicability = capability["context_applicability"]
        _ordered_unique(applicability["required_context_fields"], lambda value: value, "registry.context-field-order", errors)
        _ordered_unique(capability["invocation_modes"], lambda value: value, "registry.invocation-order", errors)
        operation = capability["operation"]
        _ordered_unique(operation["side_effect_classes"], lambda value: value, "registry.side-effect-order", errors)
        _ordered_unique(operation["freshness_checks"], lambda value: value, "registry.freshness-order", errors)
        resources_contract = capability["resources"]
        _ordered_unique(resources_contract["claims"], lambda value: value, "registry.resource-claim-order", errors)
        _ordered_unique(resources_contract["default_budgets"], lambda row: row["budget_key"], "registry.budget-order", errors)
        profile = capability["profile"]
        _ordered_unique(
            profile["profile_revision_refs"],
            lambda row: (row["profile_kind"], row["profile_revision_id"]),
            "registry.profile-revision-order",
            errors,
        )
        _ordered_unique(profile["required_support_states"], lambda value: value, "registry.support-state-order", errors)

        policy = applicability["scope_compatibility_policy_id"]
        policy_authority = applicability["scope_compatibility_authority"]
        if authority is None and (policy is not None or policy_authority is not None):
            errors.add("registry.mechanical-scope-policy")
        if policy is not None and policy_authority != authority:
            errors.add("registry.scope-policy-authority")

        for binding in method_bindings:
            for field in ("request_schema_id", "result_schema_id", "failure_schema_id"):
                try:
                    validator = _validator(resources, binding[field])
                    Draft202012Validator.check_schema(validator.schema)
                    if not schema_is_recursively_closed(resources, binding[field]):
                        errors.add("registry.schema-open")
                except Exception:
                    errors.add("registry.schema-unresolved")
            for field in ("plan_schema_id", "progress_schema_id"):
                if binding[field] is not None:
                    try:
                        validator = _validator(resources, binding[field])
                        Draft202012Validator.check_schema(validator.schema)
                        if not schema_is_recursively_closed(resources, binding[field]):
                            errors.add("registry.schema-open")
                    except Exception:
                        errors.add("registry.schema-unresolved")

        effects = set(operation["side_effect_classes"])
        if "none" in effects and effects != {"none"}:
            errors.add("registry.side-effects-none-mixed")
        hazardous = bool(effects & HAZARDOUS_SIDE_EFFECTS)
        if hazardous and operation["operation_class"] not in MUTATING_OPERATION_CLASSES:
            errors.add("registry.side-effect-operation")
        if hazardous or operation["operation_class"] in MUTATING_OPERATION_CLASSES:
            if operation["preview"] == "none":
                errors.add("registry.mutation-preview")
            if operation["consent"] == "none":
                errors.add("registry.mutation-consent")
            if operation["idempotency"] == "none":
                errors.add("registry.mutation-idempotency")
            if not operation["freshness_checks"]:
                errors.add("registry.mutation-freshness")
        for binding in method_bindings:
            minimum = METHOD_OPERATION_MINIMUMS.get(binding["protocol_method"])
            if minimum is not None:
                allowed_classes, required_effects = minimum
                if operation["operation_class"] not in allowed_classes:
                    errors.add("registry.method-operation-minimum")
                if not required_effects.issubset(effects):
                    errors.add("registry.method-side-effect-minimum")
        if operation["preview"] != "none" and any(
            binding["plan_schema_id"] is None for binding in method_bindings
        ):
            errors.add("registry.plan-schema")
        progress = capability["async_behavior"]["progress"]
        if progress != "none" and any(
            binding["progress_schema_id"] is None for binding in method_bindings
        ):
            errors.add("registry.progress-schema")
        subscription_methods = [
            binding["protocol_method"]
            for binding in method_bindings
            if binding["protocol_method"].endswith("/subscribe")
        ]
        subscription = capability["async_behavior"]["subscription"]
        if (subscription == "none") == bool(subscription_methods):
            errors.add("registry.subscription-contract")

        for field in (
            "applicability_authority",
            "support_predicate_authority",
            "action_gate_authority",
        ):
            declared = profile[field]
            if declared is not None and _authority_tuple(declared) is None:
                errors.add("registry.profile-authority")
        # A profile decision authority need not be the semantic owner, but it
        # must be explicit.  A semantic owner cannot stand in for a missing
        # profile authority.
        if profile["applicability_policy_id"] is not None and profile["applicability_authority"] is None:
            errors.add("registry.profile-authority")
        if profile["support_predicate_policy_id"] is not None and profile["support_predicate_authority"] is None:
            errors.add("registry.profile-authority")
        if profile["action_gate_policy_id"] is not None and profile["action_gate_authority"] is None:
            errors.add("registry.profile-authority")

        if profile["applicability_mode"] == "explicit-revisions":
            for profile_ref in profile["profile_revision_refs"]:
                key = (
                    capability["capability_id"],
                    profile_ref["profile_kind"],
                    profile_ref["profile_revision_id"],
                )
                profile_admission = profile_admission_by_key.get(key)
                if profile_admission is None:
                    errors.add("registry.profile-admission-missing")
                    continue
                profile_attestation = profile_admission["attestation"]
                profile_body = dict(profile_attestation)
                profile_body.pop("attestation_id", None)
                if profile_attestation["attestation_id"] != content_id(
                    "profile-policy-attestation", profile_body
                ):
                    errors.add("registry.profile-attestation-identity")
                if (
                    profile_admission["profile_revision_ref"] != profile_ref
                    or profile_admission["profile_authority"]
                    != profile["support_predicate_authority"]
                    or profile_admission["profile_adapter_id"] != profile["profile_adapter_id"]
                    or profile_admission["profile_validation_entrypoint"]
                    != profile["profile_validation_entrypoint"]
                    or profile_attestation["capability_id"] != capability["capability_id"]
                    or profile_attestation["profile_revision_ref"] != profile_ref
                    or profile_attestation["profile_authority"]
                    != profile_admission["profile_authority"]
                    or profile_attestation["support_predicate_policy_id"]
                    != profile["support_predicate_policy_id"]
                    or profile_attestation["action_gate_policy_id"]
                    != profile["action_gate_policy_id"]
                ):
                    errors.add("registry.profile-attestation-binding")
                if (
                    profile["action_gate_authority"] is not None
                    and profile["action_gate_authority"]
                    != profile_admission["profile_authority"]
                ):
                    errors.add("registry.profile-gate-authority")
                _call_required_port(
                    admission_validator,
                    AuthorityAdmissionRequest(
                        projection_kind="authority-admission",
                        role="profile-owner",
                        admission_canonical_bytes=canonical_json_bytes(profile_admission),
                        capability_canonical_bytes=canonical_json_bytes(capability),
                    ),
                    errors,
                    unverified_code="registry.profile-attestation-unverified",
                    exception_code="registry.profile-attestation-port-exception",
                    mutation_code="registry.profile-attestation-port-mutation",
                )

    if set(admission_by_capability) - set(capability_by_id):
        errors.add("registry.admission-capability")
    expected_profile_keys = {
        (capability["capability_id"], ref["profile_kind"], ref["profile_revision_id"])
        for capability in capabilities
        if capability["profile"]["applicability_mode"] == "explicit-revisions"
        for ref in capability["profile"]["profile_revision_refs"]
    }
    if set(profile_admission_by_key) - expected_profile_keys:
        errors.add("registry.profile-admission-capability")

    handlers_by_capability: dict[str, list[Mapping[str, Any]]] = {}
    for handler in handlers:
        handler_body = dict(handler)
        handler_body.pop("registration_id", None)
        if handler["registration_id"] != content_id("handler-registration", handler_body):
            errors.add("registry.handler-registration-identity")
        handlers_by_capability.setdefault(handler["capability_id"], []).append(handler)
        capability = capability_by_id.get(handler["capability_id"])
        if capability is None:
            errors.add("registry.handler-capability")
            continue
        for field in (
            "handler_id",
            "provider_id",
            "public_entrypoint",
            "handler_implementation_id",
            "method_bindings",
        ):
            if handler[field] != capability[field]:
                errors.add(
                    "registry.handler-method-drift"
                    if field == "method_bindings"
                    else "registry.handler-drift"
                )
        if not set(handler["invocation_modes"]).issubset(capability["invocation_modes"]):
            errors.add("registry.handler-mode")
        if handler["state"] == "available" and not handler["health_receipt_ids"]:
            errors.add("registry.handler-health")
        _ordered_unique(handler["method_bindings"], lambda row: row["protocol_method"], "registry.handler-method-order", errors)
        _ordered_unique(handler["invocation_modes"], lambda value: value, "registry.handler-mode-order", errors)
        ordinals = [reason["ordinal"] for reason in handler["reasons"]]
        if ordinals != list(range(len(ordinals))):
            errors.add("registry.reason-order")
        _ordered_unique(handler["conformance_case_ids"], lambda value: value, "registry.conformance-order", errors)
        _ordered_unique(handler["health_receipt_ids"], lambda value: value, "registry.health-order", errors)
        for reason in handler["reasons"]:
            _ordered_unique(
                reason["related_records"],
                lambda row: (row["record_kind"], row["record_id"]),
                "registry.reason-record-order",
                errors,
            )
    for capability_id in capability_by_id:
        if len(handlers_by_capability.get(capability_id, [])) != 1:
            errors.add("registry.handler-cardinality")

    mappings = registry["legacy_v2_gateway"]["method_mappings"]
    _ordered_unique(mappings, lambda row: row["legacy_method"], "registry.legacy-method-order", errors)
    for mapping in mappings:
        if mapping["disposition"] == "shared-handler" and mapping["capability_id"] not in capability_by_id:
            errors.add("registry.legacy-capability")
    errors.update(_record_ref_errors(registry, "registry.record-ref-kind"))
    return errors


def _request_values(
    values: Sequence[Mapping[str, Any]],
    *,
    before_index: int | None = None,
) -> tuple[dict[str, tuple[int, Mapping[str, Any]]], set[str]]:
    requests: dict[str, tuple[int, Mapping[str, Any]]] = {}
    duplicates: set[str] = set()
    limit = len(values) if before_index is None else before_index
    for index, value in enumerate(values[:limit]):
        params = value.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("request"), dict):
            continue
        request_id = params["request"]["request_id"]
        if request_id in requests:
            duplicates.add(request_id)
        requests[request_id] = (index, value)
    return requests, duplicates


def _subscription_history_codes(
    values: Sequence[Mapping[str, Any]],
) -> set[str]:
    """Replay subscription custody in actual stream order."""

    errors: set[str] = set()
    requests, _ = _request_values(values)
    active: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        method = value.get("method")
        params = value.get("params")
        if (
            isinstance(method, str)
            and method.endswith("/subscribe")
            and isinstance(params, dict)
            and isinstance(params.get("position"), dict)
        ):
            position = params["position"]
            if position["kind"] == "resume":
                state = active.get(position["subscription_id"])
                if state is None:
                    errors.add("subscription.resume-handle")
                else:
                    request_binding = params["request"]
                    if position["stream_generation"] != state["stream_generation"]:
                        errors.add("subscription.generation")
                    if request_binding["context_ref_id"] != state["context_ref_id"]:
                        errors.add("subscription.context")
                    if request_binding["input_binding_id"] != state["input_binding_id"]:
                        errors.add("subscription.input-binding")
                    if params["scope"] != state["scope"]:
                        errors.add("subscription.scope")
                    if params["event_families"] != state["event_families"]:
                        errors.add("subscription.event-family")
                    if params["delivery"] != state["delivery"]:
                        errors.add("subscription.delivery")
                    if position["last_cursor"] != state["next_cursor"] - 1:
                        errors.add("subscription.resume-cursor")

        result = value.get("result")
        if isinstance(result, dict) and result.get("format") == "workbench-service-result-v3":
            outcome = result["outcome"]
            request_entry = requests.get(result["binding"]["request_id"])
            if (
                outcome["state"] == "succeeded"
                and isinstance(outcome.get("value"), dict)
                and "subscription_id" in outcome["value"]
                and request_entry is not None
                and request_entry[1].get("method", "").endswith("/subscribe")
            ):
                request_index, request = request_entry
                if request_index >= index:
                    errors.add("response.request-order")
                    continue
                request_params = request["params"]
                position = request_params["position"]
                handle = outcome["value"]
                subscription_id = handle["subscription_id"]
                state = active.get(subscription_id)
                if position["kind"] == "start":
                    if state is not None:
                        errors.add("subscription.handle-duplicate")
                else:
                    if state is None or position["subscription_id"] != subscription_id:
                        errors.add("subscription.identity")
                    elif position["stream_generation"] != state["stream_generation"]:
                        errors.add("subscription.generation")
                    if handle["stream_generation"] != position["stream_generation"]:
                        errors.add("subscription.generation")
                if handle["context_ref_id"] != request_params["request"]["context_ref_id"]:
                    errors.add("subscription.context")
                if handle["scope"] != request_params["scope"]:
                    errors.add("subscription.scope")
                if handle["event_families"] != request_params["event_families"]:
                    errors.add("subscription.event-family")
                if handle["delivery"] != request_params["delivery"]:
                    errors.add("subscription.delivery")
                expected_next = 0 if position["kind"] == "start" else position["last_cursor"] + 1
                if handle["next_cursor"] != expected_next:
                    errors.add("subscription.resume-cursor")
                active[subscription_id] = {
                    "stream_generation": handle["stream_generation"],
                    "context_ref_id": handle["context_ref_id"],
                    "input_binding_id": request_params["request"]["input_binding_id"],
                    "scope": handle["scope"],
                    "event_families": handle["event_families"],
                    "delivery": handle["delivery"],
                    "next_cursor": handle["next_cursor"],
                }

        if method == "subscription/event" and isinstance(params, dict):
            state = active.get(params["subscription_id"])
            if state is None:
                errors.add("subscription.handle")
                continue
            if params["stream_generation"] != state["stream_generation"]:
                errors.add("subscription.generation")
            if params["context_ref_id"] != state["context_ref_id"]:
                errors.add("subscription.context")
            if params["scope"] != state["scope"]:
                errors.add("subscription.scope")
            if params["event_family"] not in state["event_families"]:
                errors.add("subscription.event-family")
            expected = state["next_cursor"]
            for dropped in params["dropped_cursor_ranges"]:
                if dropped["first"] != expected or dropped["first"] > dropped["last"]:
                    errors.add("subscription.cursor-continuity")
                expected = dropped["last"] + 1
            if params["cursor"] != expected:
                errors.add("subscription.cursor-continuity")
            state["next_cursor"] = params["cursor"] + 1
    return errors


def _initialize_binding_codes(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> set[str]:
    """Validate the one negotiated handshake as a whole-stream anchor."""

    errors: set[str] = set()
    result = response["result"]
    params = request["params"]
    capability_ids = {
        item["capability_id"] for item in registry["capability_descriptors"]
    }
    if not set(params["required_capability_ids"]).issubset(capability_ids):
        errors.add("initialize.required-capability")
    if response.get("id") != request.get("id"):
        errors.add("initialize.response-request")
    if result["request_id"] != params["request_id"]:
        errors.add("initialize.request-id")
    if result["protocol_version"] != params["protocol_version"]:
        errors.add("initialize.protocol-version")
    if result["service_distribution_id"] != registry["service_distribution_id"]:
        errors.add("initialize.service-distribution")
    if result["registry_id"] != registry["registry_id"]:
        errors.add("initialize.registry")
    if result["negotiated_features"] != params["required_features"]:
        errors.add("initialize.features")
    if result["limits"]["maximum_frame_bytes"] > params["maximum_frame_bytes"]:
        errors.add("initialize.frame-limit")
    gateway = registry["legacy_v2_gateway"]
    if result["legacy_v2_gateway"] != {
        "available": gateway["enabled"],
        "protocol_major": gateway["protocol_major"],
        "disposition": gateway["disposition"],
    }:
        errors.add("initialize.legacy-gateway")
    return errors


def _domain_response_stream_codes(
    values: Sequence[Mapping[str, Any]],
    request_by_id: Mapping[str, tuple[int, Mapping[str, Any]]],
    method_index: Mapping[
        tuple[str, str], tuple[Mapping[str, Any], Mapping[str, Any]]
    ],
) -> set[str]:
    """Bind every domain result to the same prior request under both IDs."""

    errors: set[str] = set()
    for response_index, response_value in enumerate(values):
        result = response_value.get("result")
        if not isinstance(result, dict) or result.get("format") != "workbench-service-result-v3":
            continue
        binding = result["binding"]
        request_entry = request_by_id.get(binding["request_id"])
        if request_entry is None:
            errors.add("response.request")
            continue
        request_index, request_value = request_entry
        if request_index >= response_index:
            errors.add("response.request-order")
        if response_value.get("id") != request_value.get("id"):
            errors.add("response.jsonrpc-id")
        if binding["request_method"] != request_value["method"]:
            errors.add("response.method")
        request_binding = request_value["params"]["request"]
        if binding["capability_id"] != request_binding["capability_id"]:
            errors.add("response.capability")
        if binding["capability_version"] != request_binding["capability_version"]:
            errors.add("response.capability-version")
        if binding["protocol_version"] != request_binding["protocol_version"]:
            errors.add("response.protocol-version")
        selected = method_index.get(
            (request_binding["capability_id"], request_value["method"])
        )
        if selected is not None:
            capability, _ = selected
            if binding["handler_id"] != capability["handler_id"]:
                errors.add("response.handler")
            if binding["handler_implementation_id"] != capability["handler_implementation_id"]:
                errors.add("response.handler")
        if request_value["method"] in {"context/resolve", "context/register"}:
            outcome = result["outcome"]
            resolved_context = (
                outcome.get("value", {}).get("context_ref_id")
                if outcome.get("state") == "succeeded"
                else None
            )
            if resolved_context is not None and binding["context_ref_id"] != resolved_context:
                errors.add("response.context")
            if request_value["method"] == "context/resolve" and outcome.get("state") == "succeeded":
                resolved_input = outcome.get("value", {}).get("input_binding_id")
                if resolved_input is None or binding["input_binding_id"] != resolved_input:
                    errors.add("response.input-binding")
            elif binding["input_binding_id"] is not None:
                errors.add("response.input-binding")
        else:
            if binding["context_ref_id"] != request_binding["context_ref_id"]:
                errors.add("response.context")
            if binding["input_binding_id"] != request_binding["input_binding_id"]:
                errors.add("response.input-binding")
    return errors


def _message_semantic_codes(
    candidate_index: int,
    messages: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    resources: Registry,
    support_decision_validator: SupportDecisionValidator | None = None,
    context_input_validator: ContextInputApplicabilityValidator | None = None,
    action_gate_validator: ActionGateDecisionValidator | None = None,
    job_projection_validator: JobProjectionValidator | None = None,
) -> set[str]:
    """Validate one candidate in its exact request/response stream context."""

    errors: set[str] = set()
    values = list(messages)
    candidate = values[candidate_index]
    method_index = _method_index(registry)
    request_by_id, duplicate_request_ids = _request_values(values)
    logical_request_ids: list[str] = []
    for value in values:
        value_params = value.get("params")
        if not isinstance(value_params, dict):
            continue
        if value.get("method") == "service/initialize":
            request_id = value_params.get("request_id")
        else:
            request_binding = value_params.get("request")
            request_id = (
                request_binding.get("request_id")
                if isinstance(request_binding, dict)
                else None
            )
        if isinstance(request_id, str):
            logical_request_ids.append(request_id)
    if duplicate_request_ids or len(logical_request_ids) != len(
        set(logical_request_ids)
    ):
        errors.add("request.id-duplicate")
    jsonrpc_requests: dict[Any, list[tuple[int, Mapping[str, Any]]]] = {}
    jsonrpc_responses: dict[Any, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, value in enumerate(values):
        if "method" in value and "id" in value:
            jsonrpc_requests.setdefault(value["id"], []).append((index, value))
        if value.get("id") is not None and ("result" in value or "error" in value):
            jsonrpc_responses.setdefault(value["id"], []).append((index, value))
    if any(len(items) > 1 for items in jsonrpc_requests.values()):
        errors.add("request.jsonrpc-id-duplicate")
    for rpc_id, requests_for_id in jsonrpc_requests.items():
        responses_for_id = jsonrpc_responses.get(rpc_id, [])
        if len(responses_for_id) > 1:
            errors.add("response.duplicate")
        elif not responses_for_id:
            errors.add("response.missing")
        elif len(requests_for_id) == 1 and responses_for_id[0][0] <= requests_for_id[0][0]:
            errors.add("response.request-order")
    for rpc_id, responses_for_id in jsonrpc_responses.items():
        requests_for_id = jsonrpc_requests.get(rpc_id, [])
        for response_index, response_value in responses_for_id:
            prior = [
                request_value
                for request_index, request_value in requests_for_id
                if request_index < response_index
            ]
            if len(prior) != 1:
                errors.add(
                    "error.request-correlation"
                    if "error" in response_value
                    else "response.request-correlation"
                )
    ordered_requests = [
        (index, value)
        for index, value in enumerate(values)
        if "method" in value and "id" in value
    ]
    initialize_requests = [
        (index, value)
        for index, value in ordered_requests
        if value.get("method") == "service/initialize"
    ]
    if not ordered_requests or ordered_requests[0][1].get("method") != "service/initialize":
        errors.add("initialize.first-request")
    if len(initialize_requests) != 1:
        errors.add("initialize.request-cardinality")
    else:
        initialize_index, initialize_request = initialize_requests[0]
        initialize_responses = [
            (index, value)
            for index, value in enumerate(values)
            if isinstance(value.get("result"), dict)
            and value["result"].get("format") == "workbench-service-initialize-result-v3"
        ]
        if len(initialize_responses) != 1:
            errors.add("initialize.response-cardinality")
        else:
            initialize_response_index, initialize_response = initialize_responses[0]
            if any(
                index != initialize_index and index < initialize_response_index
                for index, _ in ordered_requests
            ):
                errors.add("initialize.not-complete")
            errors.update(
                _initialize_binding_codes(
                    initialize_request, initialize_response, registry
                )
            )
    errors.update(_domain_response_stream_codes(values, request_by_id, method_index))
    errors.update(_subscription_history_codes(values))

    method = candidate.get("method")
    params = candidate.get("params")
    if isinstance(params, dict) and isinstance(params.get("request"), dict):
        request = params["request"]
        selected = method_index.get((request["capability_id"], method))
        if selected is None:
            errors.add("request.method-binding")
        else:
            capability, binding = selected
            if request["capability_version"] != capability["semantic_version"]:
                errors.add("request.capability-version")
            if request["operation_class"] != capability["operation"]["operation_class"]:
                errors.add("request.operation-class")
            dynamic_value = params if binding["request_value_scope"] == "params" else params.get("arguments")
            if list(_validator(resources, binding["request_schema_id"]).iter_errors(dynamic_value)):
                errors.add("request.value-schema")
            applicability = capability["context_applicability"]
            context_id = request["context_ref_id"]
            input_id = request["input_binding_id"]
            if applicability["context_binding"] == "none" and context_id is not None:
                errors.add("request.context-applicability")
            if applicability["context_binding"] == "required" and context_id is None:
                errors.add("request.context-applicability")
            if applicability["input_binding"] == "none" and input_id is not None:
                errors.add("request.input-applicability")
            if applicability["input_binding"] == "required" and input_id is None:
                errors.add("request.input-applicability")
            if context_id is not None:
                _call_required_port(
                    context_input_validator,
                    ContextInputApplicabilityRequest(
                        projection_kind="context-input-applicability",
                        purpose="request",
                        context_ref_id=context_id,
                        input_binding_id=input_id,
                        request_canonical_bytes=canonical_json_bytes(candidate),
                        result_canonical_bytes=None,
                        capability_canonical_bytes=canonical_json_bytes(capability),
                    ),
                    errors,
                    unverified_code="request.context-input-unverified",
                    exception_code="request.context-input-port-exception",
                    mutation_code="request.context-input-port-mutation",
                )
            operation = capability["operation"]
            hazardous = bool(set(operation["side_effect_classes"]) & HAZARDOUS_SIDE_EFFECTS)
            if hazardous or operation["operation_class"] in MUTATING_OPERATION_CLASSES:
                if request["commit"] is None:
                    errors.add("request.commit-required")
                if request["idempotency_key"] is None:
                    errors.add("request.idempotency-required")
            if request["commit"] is not None and request["idempotency_key"] != request["commit"]["idempotency_key"]:
                errors.add("request.idempotency-mismatch")
            if method.endswith("/subscribe"):
                declared_delivery = capability["async_behavior"]["subscription"]
                if params["delivery"] == "lossy-declared" and declared_delivery != "lossy-declared":
                    errors.add("request.subscription-delivery")
        errors.update(_record_ref_errors(candidate, "message.record-ref-kind"))

    if method == "service/initialize":
        if any("method" in value and "id" in value for value in values[:candidate_index]):
            errors.add("initialize.first-request")
        capability_ids = {item["capability_id"] for item in registry["capability_descriptors"]}
        if not set(params["required_capability_ids"]).issubset(capability_ids):
            errors.add("initialize.required-capability")

    result = candidate.get("result")
    if isinstance(result, dict) and result.get("format") == "workbench-service-result-v3":
        response = result["binding"]
        request_entry = request_by_id.get(response["request_id"])
        request: Mapping[str, Any] | None = None
        selected = None
        if request_entry is None:
            errors.add("response.request")
        else:
            request_index, request = request_entry
            if request_index >= candidate_index:
                errors.add("response.request-order")
            request_binding = request["params"]["request"]
            if candidate.get("id") != request.get("id"):
                errors.add("response.jsonrpc-id")
            if response["request_method"] != request["method"]:
                errors.add("response.method")
            if response["capability_id"] != request_binding["capability_id"]:
                errors.add("response.capability")
            if response["capability_version"] != request_binding["capability_version"]:
                errors.add("response.capability-version")
            if response["protocol_version"] != request_binding["protocol_version"]:
                errors.add("response.protocol-version")
            selected = method_index.get((request_binding["capability_id"], request["method"]))
            if request["method"] in {"context/resolve", "context/register"}:
                outcome = result["outcome"]
                resolved_context = (
                    outcome.get("value", {}).get("context_ref_id")
                    if outcome.get("state") == "succeeded"
                    else None
                )
                if resolved_context is not None and response["context_ref_id"] != resolved_context:
                    errors.add("response.context")
                if request["method"] == "context/resolve" and outcome.get("state") == "succeeded":
                    resolved_input = outcome.get("value", {}).get("input_binding_id")
                    if resolved_input is None or response["input_binding_id"] != resolved_input:
                        errors.add("response.input-binding")
                elif response["input_binding_id"] is not None:
                    errors.add("response.input-binding")
            else:
                if response["context_ref_id"] != request_binding["context_ref_id"]:
                    errors.add("response.context")
                if response["input_binding_id"] != request_binding["input_binding_id"]:
                    errors.add("response.input-binding")
        if selected is not None:
            capability, binding = selected
            if response["handler_id"] != capability["handler_id"]:
                errors.add("response.handler")
            if response["handler_implementation_id"] != capability["handler_implementation_id"]:
                errors.add("response.handler")
            outcome = result["outcome"]
            if request is not None:
                context_id = response["context_ref_id"]
                input_id = response["input_binding_id"]
                purpose: Literal["response", "resolved-result"] = "response"
                if request["method"] == "context/resolve" and outcome.get("state") == "succeeded":
                    context_id = outcome["value"]["context_ref_id"]
                    input_id = outcome["value"]["input_binding_id"]
                    purpose = "resolved-result"
                if context_id is not None:
                    _call_required_port(
                        context_input_validator,
                        ContextInputApplicabilityRequest(
                            projection_kind="context-input-applicability",
                            purpose=purpose,
                            context_ref_id=context_id,
                            input_binding_id=input_id,
                            request_canonical_bytes=canonical_json_bytes(request),
                            result_canonical_bytes=canonical_json_bytes(candidate),
                            capability_canonical_bytes=canonical_json_bytes(capability),
                        ),
                        errors,
                        unverified_code="response.context-input-unverified",
                        exception_code="response.context-input-port-exception",
                        mutation_code="response.context-input-port-mutation",
                    )
            if outcome["state"] == "succeeded":
                if outcome["result_schema_id"] != binding["result_schema_id"]:
                    errors.add("response.result-schema")
                if list(_validator(resources, binding["result_schema_id"]).iter_errors(outcome["value"])):
                    errors.add("response.value-schema")
            elif outcome["state"] in {"unavailable", "blocked", "failed"}:
                if list(_validator(resources, binding["failure_schema_id"]).iter_errors(outcome["failure"])):
                    errors.add("response.failure-schema")
                failure = outcome["failure"]
                if failure["kind"] == "unsupported-profile" and outcome["state"] != capability["profile"]["unsupported_result"]:
                    errors.add("response.unsupported-profile-disposition")

            states = response["states"]
            profile = capability["profile"]
            if profile["applicability_mode"] == "profile-independent":
                if (
                    states["profile_support"] != "not-applicable"
                    or states["profile_revision_refs"]
                    or states["support_decision_ids"]
                ):
                    errors.add("response.profile-state")
            else:
                if states["profile_support"] != "unsupported" and states["profile_support"] not in profile["required_support_states"]:
                    errors.add("response.profile-state")
                if profile["applicability_mode"] == "explicit-revisions" and states["profile_revision_refs"] != profile["profile_revision_refs"]:
                    errors.add("response.profile-revisions")
                if not states["support_decision_ids"]:
                    errors.add("response.support-decision")
                else:
                    _call_required_port(
                        support_decision_validator,
                        ProfileSupportDecisionRequest(
                            projection_kind="profile-support-decision",
                            decision_ids=tuple(states["support_decision_ids"]),
                            support_state=states["profile_support"],
                            profile_refs_canonical_bytes=canonical_json_bytes(states["profile_revision_refs"]),
                            request_canonical_bytes=canonical_json_bytes(request),
                            result_canonical_bytes=canonical_json_bytes(candidate),
                            capability_canonical_bytes=canonical_json_bytes(capability),
                        ),
                        errors,
                        unverified_code="response.support-decision-unverified",
                        exception_code="response.support-decision-port-exception",
                        mutation_code="response.support-decision-port-mutation",
                    )

            gate_decision_ids = states["action_gate_decision_ids"]
            if profile["action_gate_policy_id"] is None:
                if states["action_gate"] != "not-required" or gate_decision_ids:
                    errors.add("response.action-gate-state")
            elif not gate_decision_ids:
                errors.add("response.action-gate-decision")
            else:
                _call_required_port(
                    action_gate_validator,
                    ActionGateDecisionRequest(
                        projection_kind="action-gate-decision",
                        decision_ids=tuple(gate_decision_ids),
                        action_gate_state=states["action_gate"],
                        request_canonical_bytes=canonical_json_bytes(request),
                        result_canonical_bytes=canonical_json_bytes(candidate),
                        capability_canonical_bytes=canonical_json_bytes(capability),
                    ),
                    errors,
                    unverified_code="response.action-gate-unverified",
                    exception_code="response.action-gate-port-exception",
                    mutation_code="response.action-gate-port-mutation",
                )

            if outcome["state"] == "accepted":
                job = outcome["job"]
                if not any(
                    item["record_kind"] == "job-submission"
                    and item["record_id"] == job["job_submission_id"]
                    for item in response["output_revision_refs"]
                ):
                    errors.add("response.job-output-revision")
                _call_required_port(
                    job_projection_validator,
                    JobProjectionValidationRequest(
                        projection_kind="accepted-handle",
                        job_id=job["job_id"],
                        job_submission_id=job["job_submission_id"],
                        job_event_id=job["latest_event_id"],
                        job_event_ordinal=job["latest_event_ordinal"],
                        terminal_seal_id=job["terminal_seal_id"],
                        context_ref_id=response["context_ref_id"],
                        input_binding_id=response["input_binding_id"],
                        capability_id=capability["capability_id"],
                        request_canonical_bytes=canonical_json_bytes(request),
                        message_canonical_bytes=canonical_json_bytes(candidate),
                        capability_canonical_bytes=canonical_json_bytes(capability),
                    ),
                    errors,
                    unverified_code="response.job-projection-unverified",
                    exception_code="response.job-projection-port-exception",
                    mutation_code="response.job-projection-port-mutation",
                )
            elif outcome["state"] in {"unavailable", "blocked", "failed"}:
                failure = outcome["failure"]
                if outcome["state"] == "failed" and failure["job_id"] is not None and failure["job_terminal_seal_id"] is None:
                    errors.add("response.completed-job-seal")
                if failure["job_id"] is not None:
                    _call_required_port(
                        job_projection_validator,
                        JobProjectionValidationRequest(
                            projection_kind="job-bearing-failure",
                            job_id=failure["job_id"],
                            job_submission_id=None,
                            job_event_id=None,
                            job_event_ordinal=None,
                            terminal_seal_id=failure["job_terminal_seal_id"],
                            context_ref_id=response["context_ref_id"],
                            input_binding_id=response["input_binding_id"],
                            capability_id=capability["capability_id"],
                            request_canonical_bytes=canonical_json_bytes(request),
                            message_canonical_bytes=canonical_json_bytes(candidate),
                            capability_canonical_bytes=canonical_json_bytes(capability),
                        ),
                        errors,
                        unverified_code="response.job-projection-unverified",
                        exception_code="response.job-projection-port-exception",
                        mutation_code="response.job-projection-port-mutation",
                    )

            if response["request_method"] == "object/read" and outcome["state"] == "succeeded":
                chunk = outcome["value"]
                try:
                    decoded = base64.b64decode(chunk["data_base64"], validate=True)
                except Exception:
                    decoded = b""
                if len(decoded) != chunk["length"]:
                    errors.add("object.decoded-length")
                object_ref = chunk["object"]
                if object_ref["object_id"].split(":sha256:", 1)[1] != object_ref["sha256"]:
                    errors.add("object.digest")
                end = chunk["offset"] + chunk["length"]
                if end > object_ref["byte_length"]:
                    errors.add("object.range")
                if chunk["offset"] == 0 and chunk["eof"] and len(decoded) == object_ref["byte_length"]:
                    if hashlib.sha256(decoded).hexdigest() != object_ref["sha256"]:
                        errors.add("object.digest")
                if request is not None:
                    requested = request["params"]["range"]
                    if object_ref["object_id"] != request["params"]["object_id"]:
                        errors.add("object.identity")
                    if chunk["offset"] != requested["offset"] or chunk["length"] > requested["length"]:
                        errors.add("object.range")
                if chunk["eof"] != (end == object_ref["byte_length"]):
                    errors.add("object.eof")
                if chunk["eof"]:
                    if chunk["next_offset"] is not None:
                        errors.add("object.next-offset")
                else:
                    if chunk["length"] == 0:
                        errors.add("object.non-progress")
                    if chunk["next_offset"] != end:
                        errors.add("object.next-offset")

            if response["request_method"].endswith("/subscribe"):
                if request is not None and outcome["state"] == "succeeded":
                    position = request["params"]["position"]
                    handle = outcome["value"]
                    if handle["context_ref_id"] != request["params"]["request"]["context_ref_id"]:
                        errors.add("subscription.context")
                    if handle["scope"] != request["params"]["scope"]:
                        errors.add("subscription.scope")
                    if handle["event_families"] != request["params"]["event_families"]:
                        errors.add("subscription.event-family")
                    if handle["delivery"] != request["params"]["delivery"]:
                        errors.add("subscription.delivery")
                    if position["kind"] == "resume":
                        if handle["subscription_id"] != position["subscription_id"]:
                            errors.add("subscription.identity")
                        if handle["stream_generation"] != position["stream_generation"]:
                            errors.add("subscription.generation")
                        if handle["next_cursor"] != position["last_cursor"] + 1:
                            errors.add("subscription.resume-cursor")
                if request is not None and outcome["state"] == "blocked" and outcome["failure"]["kind"] == "subscription-gap":
                    minimum = outcome["failure"]["minimum_available_cursor"]
                    position = request["params"]["position"]
                    if position["kind"] != "resume" or minimum <= position["last_cursor"]:
                        errors.add("subscription.gap-cursor")
        errors.update(_record_ref_errors(candidate, "message.record-ref-kind"))

    if "error" in candidate:
        rpc_id = candidate.get("id")
        if rpc_id is None:
            if candidate["error"]["code"] not in {-32700, -32600}:
                errors.add("error.null-id")
        else:
            prior = [
                item
                for index, item in jsonrpc_requests.get(rpc_id, [])
                if index < candidate_index
            ]
            if len(prior) != 1:
                errors.add("error.request-correlation")

    if method == "job/progress":
        if params["total"] is not None and params["completed"] > params["total"]:
            errors.add("progress.total")
        handles: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
        progress: list[Mapping[str, Any]] = []
        for index, value in enumerate(values):
            item_result = value.get("result")
            if index < candidate_index and isinstance(item_result, dict) and item_result.get("format") == "workbench-service-result-v3":
                outcome = item_result["outcome"]
                if outcome["state"] == "accepted" and outcome["job"]["job_id"] == params["job_id"]:
                    request_entry = request_by_id.get(item_result["binding"]["request_id"])
                    if request_entry is not None:
                        handles.append((outcome["job"], value, request_entry[1]))
            if value.get("method") == "job/progress" and value["params"]["job_id"] == params["job_id"]:
                progress.append(value["params"])
        if not handles:
            errors.add("progress.job-link")
        else:
            latest, accepted_message, accepted_request = max(
                handles,
                key=lambda item: -1 if item[0]["latest_event_ordinal"] is None else item[0]["latest_event_ordinal"],
            )
            if latest["latest_event_ordinal"] is None:
                errors.add("progress.event-link")
            else:
                if params["event_ordinal"] < latest["latest_event_ordinal"]:
                    errors.add("progress.event-ordinal")
                if params["event_ordinal"] == latest["latest_event_ordinal"] and params["job_event_id"] != latest["latest_event_id"]:
                    errors.add("progress.event-link")
            accepted_result = accepted_message["result"]
            accepted_capability = method_index.get(
                (
                    accepted_result["binding"]["capability_id"],
                    accepted_result["binding"]["request_method"],
                )
            )
            capability = None if accepted_capability is None else accepted_capability[0]
            _call_required_port(
                job_projection_validator,
                JobProjectionValidationRequest(
                    projection_kind="progress",
                    job_id=params["job_id"],
                    job_submission_id=latest["job_submission_id"],
                    job_event_id=params["job_event_id"],
                    job_event_ordinal=params["event_ordinal"],
                    terminal_seal_id=latest["terminal_seal_id"],
                    context_ref_id=accepted_result["binding"]["context_ref_id"],
                    input_binding_id=accepted_result["binding"]["input_binding_id"],
                    capability_id=accepted_result["binding"]["capability_id"],
                    request_canonical_bytes=canonical_json_bytes(accepted_request),
                    message_canonical_bytes=canonical_json_bytes(candidate),
                    capability_canonical_bytes=None if capability is None else canonical_json_bytes(capability),
                ),
                errors,
                unverified_code="progress.job-projection-unverified",
                exception_code="progress.job-projection-port-exception",
                mutation_code="progress.job-projection-port-mutation",
            )
        ordinals = [item["event_ordinal"] for item in progress]
        if ordinals != sorted(set(ordinals)):
            errors.add("progress.event-ordinal")
        phases: dict[str, tuple[int, int | None, bool]] = {}
        for item in progress:
            previous = phases.get(item["phase_id"])
            if previous is not None:
                previous_completed, previous_total, previous_mutation = previous
                if item["completed"] < previous_completed or item["total"] != previous_total:
                    errors.add("progress.monotonic")
                if previous_mutation and not item["mutation_started"]:
                    errors.add("progress.mutation-monotonic")
            phases[item["phase_id"]] = (item["completed"], item["total"], item["mutation_started"])

    if method == "subscription/event":
        ranges = params["dropped_cursor_ranges"]
        previous_last = -1
        for dropped in ranges:
            if dropped["first"] > dropped["last"]:
                errors.add("subscription.cursor-range")
            if dropped["first"] <= previous_last:
                errors.add("subscription.cursor-range-order")
            previous_last = dropped["last"]
        linked_request: Mapping[str, Any] | None = None
        for index, value in enumerate(values[:candidate_index]):
            item_result = value.get("result")
            if not isinstance(item_result, dict) or item_result.get("format") != "workbench-service-result-v3":
                continue
            outcome = item_result["outcome"]
            if (
                outcome["state"] == "succeeded"
                and isinstance(outcome.get("value"), dict)
                and outcome["value"].get("subscription_id") == params["subscription_id"]
            ):
                request_entry = request_by_id.get(item_result["binding"]["request_id"])
                if request_entry is not None and request_entry[0] < index:
                    linked_request = request_entry[1]
        if linked_request is not None:
            capability = method_index[
                (
                    linked_request["params"]["request"]["capability_id"],
                    linked_request["method"],
                )
            ][0]
            if ranges and capability["async_behavior"]["subscription"] != "lossy-declared":
                errors.add("subscription.loss-not-declared")
        errors.update(_record_ref_errors(candidate, "message.record-ref-kind"))
    return errors


def _schema_diagnostics(
    validator: Draft202012Validator, value: Any, code: str
) -> list[ContractDiagnostic]:
    diagnostics: list[ContractDiagnostic] = []
    for error in validator.iter_errors(value):
        path = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}" for item in error.absolute_path
        )
        diagnostics.append(ContractDiagnostic(code, path, error.validator or "schema"))
    return diagnostics


def validate_registry_v3(
    registry: Mapping[str, Any],
    resources: Registry,
    schema_id: str,
    admission_validator: AdmissionValidator | None = None,
) -> tuple[ContractDiagnostic, ...]:
    """Return stable diagnostics for a complete registry candidate."""

    try:
        snapshot = _canonical_snapshot(registry)
    except Exception as exc:
        return (ContractDiagnostic("registry.domain", "$", type(exc).__name__),)
    if type(snapshot) is not dict:
        return (ContractDiagnostic("registry.domain", "$", "registry must be an object"),)
    try:
        diagnostics = _schema_diagnostics(
            _validator(resources, schema_id), snapshot, "registry.schema"
        )
    except Exception as exc:
        return (ContractDiagnostic("registry.schema-resolution", "$", type(exc).__name__),)
    if not diagnostics:
        diagnostics.extend(
            ContractDiagnostic(code)
            for code in _registry_semantic_codes(
                snapshot, resources, admission_validator=admission_validator
            )
        )
    return tuple(sorted(diagnostics))


def validate_message_v3(
    candidate_index: int,
    messages: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
    resources: Registry,
    schema_id: str,
    support_decision_validator: SupportDecisionValidator | None = None,
    context_input_validator: ContextInputApplicabilityValidator | None = None,
    action_gate_validator: ActionGateDecisionValidator | None = None,
    job_projection_validator: JobProjectionValidator | None = None,
) -> tuple[ContractDiagnostic, ...]:
    """Validate one indexed actual message in an exact actual-message stream."""

    if type(candidate_index) is not int:
        return (ContractDiagnostic("message.candidate-index", "$", "index must be an integer"),)
    try:
        stream = _canonical_snapshot(messages)
        registry_snapshot = _canonical_snapshot(registry)
    except Exception as exc:
        return (ContractDiagnostic("message.domain", "$", type(exc).__name__),)
    if type(stream) is not list or not all(type(item) is dict for item in stream):
        return (ContractDiagnostic("message.domain", "$", "stream must contain ordinary objects"),)
    if type(registry_snapshot) is not dict:
        return (ContractDiagnostic("message.domain", "$.registry", "registry must be an object"),)
    if candidate_index < 0 or candidate_index >= len(stream):
        return (ContractDiagnostic("message.candidate-index", "$", "index is outside the stream"),)
    try:
        validator = _validator(resources, schema_id)
        diagnostics = _schema_diagnostics(
            validator, stream[candidate_index], "message.schema"
        )
        for index, value in enumerate(stream):
            if index == candidate_index:
                continue
            diagnostics.extend(
                _schema_diagnostics(validator, value, "message.stream-schema")
            )
    except Exception as exc:
        return (ContractDiagnostic("message.schema-resolution", "$", type(exc).__name__),)
    if not diagnostics:
        try:
            diagnostics.extend(
                ContractDiagnostic(code)
                for code in _message_semantic_codes(
                    candidate_index,
                    stream,
                    registry_snapshot,
                    resources,
                    support_decision_validator=support_decision_validator,
                    context_input_validator=context_input_validator,
                    action_gate_validator=action_gate_validator,
                    job_projection_validator=job_projection_validator,
                )
            )
        except Exception as exc:
            diagnostics.append(
                ContractDiagnostic("message.semantic-exception", "$", type(exc).__name__)
            )
    return tuple(sorted(diagnostics))


__all__ = [
    "ActionGateDecisionRequest",
    "ActionGateDecisionValidator",
    "AdmissionValidator",
    "AuthorityAdmissionRequest",
    "ContractDiagnostic",
    "ContextInputApplicabilityRequest",
    "ContextInputApplicabilityValidator",
    "JobProjectionValidationRequest",
    "JobProjectionValidator",
    "METHOD_OPERATION_MINIMUMS",
    "ProfileSupportDecisionRequest",
    "SupportDecisionValidator",
    "schema_is_recursively_closed",
    "validate_message_v3",
    "validate_registry_v3",
]
