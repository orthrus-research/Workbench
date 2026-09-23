"""Source-derived registry for the five live Crucible service controls."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from workbench_api.canonical import CANONICALIZER_ID, content_id

from workbench_core.component_identity import (
    CoreComponentIdentityError,
    load_core_component_identity,
)
from .runtime_dependency_identity import (
    build_runtime_dependency_lock_manifest_v2,
)


SERVICE_CONTROL_SCHEMA_ID = (
    "workbench://schemas/crucible/crucible-service-control-v1.schema.json"
)
SERVICE_PROTOCOL_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/service-protocol-v3.schema.json"
)
EMPTY_SERVICE_ARGUMENTS_SCHEMA_ID = (
    SERVICE_CONTROL_SCHEMA_ID + "#/$defs/emptyArguments"
)
SERVICE_CAPABILITIES_RESULT_SCHEMA_ID = (
    SERVICE_CONTROL_SCHEMA_ID + "#/$defs/serviceCapabilitiesResult"
)
CONTEXT_LIST_RESULT_SCHEMA_ID = (
    SERVICE_CONTROL_SCHEMA_ID + "#/$defs/contextListResult"
)
JOB_CANCELLATION_PLAN_SCHEMA_ID = (
    SERVICE_CONTROL_SCHEMA_ID + "#/$defs/jobCancellationPlan"
)
JOB_EVENT_PAGE_ARGUMENTS_SCHEMA_ID = (
    SERVICE_CONTROL_SCHEMA_ID + "#/$defs/jobEventPageArguments"
)
JOB_EVENT_PAGE_RESULT_SCHEMA_ID = (
    SERVICE_CONTROL_SCHEMA_ID + "#/$defs/jobEventPage"
)
JOB_CANCELLATION_REQUEST_SCHEMA_ID = (
    SERVICE_PROTOCOL_SCHEMA_ID + "#/$defs/jobCancelParams"
)
JOB_HANDLE_SCHEMA_ID = SERVICE_PROTOCOL_SCHEMA_ID + "#/$defs/jobHandle"
JOB_SUBSCRIPTION_REQUEST_SCHEMA_ID = (
    SERVICE_PROTOCOL_SCHEMA_ID + "#/$defs/subscriptionParams"
)
JOB_SUBSCRIPTION_RESULT_SCHEMA_ID = (
    SERVICE_PROTOCOL_SCHEMA_ID + "#/$defs/subscriptionHandle"
)
FAILURE_SCHEMA_ID = SERVICE_PROTOCOL_SCHEMA_ID + "#/$defs/failure"
SERVICE_CONTROL_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2 = (
    "workbench-service-control-runtime-dependency-lock-manifest-v2"
)
SERVICE_CONTROL_CAPABILITY_KEYS = frozenset(
    {
        "crucible.service.capabilities",
        "crucible.service.context-list",
        "crucible.service.job-cancel",
        "crucible.service.job-event-page",
        "crucible.service.job-subscribe",
    }
)

_CRUCIBLE_SOURCE_RELATIVE_PATHS = (
    "modules/crucible/src/workbench_crucible_context/__init__.py",
    "modules/crucible/src/workbench_crucible_context/records.py",
    "modules/crucible/src/workbench_crucible_context/schema_registry.py",
    "modules/crucible/src/workbench_crucible_jobs/__init__.py",
    "modules/crucible/src/workbench_crucible_jobs/records.py",
    "modules/crucible/src/workbench_crucible_service/__init__.py",
    "api/src/workbench_api/host_filesystem.py",
    "api/src/workbench_api/service.py",
    "api/src/workbench_api/__init__.py",
    "api/src/workbench_api/modules.py",
    "core/src/workbench_core/package_guard.py",
    "core/src/workbench_core/service/runtime.py",
    "core/src/workbench_core/service/framing.py",
    "core/src/workbench_core/host_filesystem.py",
    "core/src/workbench_core/host_services.py",
    "modules/crucible/src/workbench_crucible_service/service.py",
    "modules/crucible/src/workbench_crucible/__init__.py",
    "api/src/workbench_api/canonical.py",
    "modules/crucible/src/workbench_crucible/records.py",
    "modules/crucible/src/workbench_crucible/schema_registry.py",
)
_CRUCIBLE_SCHEMA_RELATIVE_PATHS = (
    "modules/crucible/schemas/crucible-admission-record-v2.schema.json",
    "modules/crucible/schemas/crucible-context-ref-v2.schema.json",
    "modules/crucible/schemas/crucible-context-v2-common.schema.json",
    "modules/crucible/schemas/crucible-dependency-manifest-v2.schema.json",
    "modules/crucible/schemas/crucible-evidence-record-v2.schema.json",
    "modules/crucible/schemas/crucible-evidence-set-revision-v2.schema.json",
    "modules/crucible/schemas/crucible-graph-record-v2.schema.json",
    "modules/crucible/schemas/crucible-graph-revision-v2.schema.json",
    "modules/crucible/schemas/crucible-graph-set-revision-v2.schema.json",
    "modules/crucible/schemas/crucible-input-binding-v2.schema.json",
    "modules/crucible/schemas/crucible-job-attempt-v2.schema.json",
    "modules/crucible/schemas/crucible-job-event-v2.schema.json",
    "modules/crucible/schemas/crucible-job-submission-v2.schema.json",
    "modules/crucible/schemas/crucible-job-terminal-seal-v2.schema.json",
    "modules/crucible/schemas/crucible-job-v2-common.schema.json",
    "modules/crucible/schemas/crucible-ledger-entry-v2.schema.json",
    "modules/crucible/schemas/crucible-materialization-recipe-v2.schema.json",
    "modules/crucible/schemas/crucible-object-descriptor-v2.schema.json",
    "modules/crucible/schemas/crucible-process-identity-v2.schema.json",
    "modules/crucible/schemas/crucible-reference-event-v2.schema.json",
    "modules/crucible/schemas/crucible-runtime-session-v2.schema.json",
    "modules/crucible/schemas/crucible-service-control-v1.schema.json",
    "modules/crucible/schemas/crucible-v2-common.schema.json",
)
_SHELL_SOURCE_RELATIVE_PATHS = (
    "modules/workbench-shell/src/workbench_shell/__init__.py",
    "core/src/workbench_core/component_identity.py",
    "core/src/workbench_core/pixi_lock.py",
    "modules/workbench-shell/src/workbench_shell/runtime_dependency_identity.py",
    "modules/workbench-shell/src/workbench_shell/service_control_registry.py",
)
_SHELL_SCHEMA_RELATIVE_PATHS = (
    "modules/workbench-shell/schemas/component-capability-registry-v3.schema.json",
    "modules/workbench-shell/schemas/service-protocol-v3.schema.json",
)
_SCHEMA_RELATIVE_PATHS = (
    *_CRUCIBLE_SCHEMA_RELATIVE_PATHS,
    *_SHELL_SCHEMA_RELATIVE_PATHS,
)
_SOURCE_RELATIVE_PATHS = (
    *_CRUCIBLE_SOURCE_RELATIVE_PATHS,
    *_SCHEMA_RELATIVE_PATHS,
    *_SHELL_SOURCE_RELATIVE_PATHS,
    "api/pyproject.toml",
    "core/pyproject.toml",
    "modules/crucible/pyproject.toml",
    "modules/workbench-shell/pyproject.toml",
)


class ServiceControlRegistryV3Error(ValueError):
    """The current source tree cannot produce the service-control registry."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ServiceControlRegistryV3Error(message)


def _seal(
    kind: str,
    body: Mapping[str, Any],
    identity_field: str,
) -> dict[str, Any]:
    value = dict(body)
    value[identity_field] = content_id(kind, value)
    return value


def _file_rows(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    _require(
        len(paths) == len(set(paths)),
        "service-control source closure contains a duplicate path",
    )
    rows: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: item.as_posix().encode("utf-8")):
        resolved = path.resolve()
        _require(
            resolved.is_file()
            and not path.is_symlink()
            and resolved.is_relative_to(root),
            f"service-control registry source input is unsafe or absent: {path}",
        )
        raw = resolved.read_bytes()
        rows.append(
            {
                "byte_length": len(raw),
                "path": resolved.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return rows


@dataclass(frozen=True, slots=True)
class ServiceControlRegistryBundleV3:
    registry: Mapping[str, Any]
    source_tree_manifest: Mapping[str, Any]
    dependency_lock_manifest: Mapping[str, Any]
    health_receipts: tuple[Mapping[str, Any], ...]
    service_distribution: Mapping[str, Any]


def _profile_independent() -> dict[str, Any]:
    return {
        "applicability_mode": "profile-independent",
        "profile_revision_refs": [],
        "applicability_policy_id": None,
        "required_support_states": [],
        "support_predicate_policy_id": None,
        "unsupported_result": "unavailable",
        "action_gate_policy_id": None,
        "applicability_authority": None,
        "support_predicate_authority": None,
        "action_gate_authority": None,
        "profile_adapter_id": None,
        "profile_validation_entrypoint": None,
    }


def _control_definition_rows() -> tuple[dict[str, Any], ...]:
    return (
        {
            "capability_key": "crucible.service.capabilities",
            "method": "service/capabilities",
            "entrypoint": "workbench_crucible_service:ServiceCapabilitiesHandler",
            "request_value_scope": "arguments",
            "request_schema_id": EMPTY_SERVICE_ARGUMENTS_SCHEMA_ID,
            "plan_schema_id": None,
            "result_schema_id": SERVICE_CAPABILITIES_RESULT_SCHEMA_ID,
            "required_record_kinds": [],
            "produced_record_kinds": [],
            "context_binding": "none",
            "input_binding": "none",
            "required_context_fields": [],
            "operation_class": "inspect",
            "side_effect_classes": ["none"],
            "preview": "none",
            "consent": "none",
            "idempotency": "none",
            "freshness_checks": [],
            "cancellation": "unsupported",
            "subscription": "none",
            "resource_class": "service-capability-discovery",
            "claims": [],
            "backpressure": "reject-before-mutation",
            "budgets": [
                {"budget_key": "bytes", "unit": "bytes", "limit": 1048576},
                {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
            ],
        },
        {
            "capability_key": "crucible.service.context-list",
            "method": "context/list",
            "entrypoint": "workbench_crucible_service:ContextListHandler",
            "request_value_scope": "arguments",
            "request_schema_id": EMPTY_SERVICE_ARGUMENTS_SCHEMA_ID,
            "plan_schema_id": None,
            "result_schema_id": CONTEXT_LIST_RESULT_SCHEMA_ID,
            "required_record_kinds": ["context-ref", "input-binding"],
            "produced_record_kinds": [],
            "context_binding": "none",
            "input_binding": "none",
            "required_context_fields": [],
            "operation_class": "inspect",
            "side_effect_classes": ["store-read"],
            "preview": "none",
            "consent": "none",
            "idempotency": "none",
            "freshness_checks": [],
            "cancellation": "unsupported",
            "subscription": "none",
            "resource_class": "service-context-discovery",
            "claims": [],
            "backpressure": "reject-before-mutation",
            "budgets": [
                {"budget_key": "bytes", "unit": "bytes", "limit": 1048576},
                {"budget_key": "records", "unit": "records", "limit": 100000},
                {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
            ],
        },
        {
            "capability_key": "crucible.service.job-cancel",
            "method": "job/cancel",
            "entrypoint": "workbench_crucible_service:JobCancellationHandler",
            "request_value_scope": "params",
            "request_schema_id": JOB_CANCELLATION_REQUEST_SCHEMA_ID,
            "plan_schema_id": JOB_CANCELLATION_PLAN_SCHEMA_ID,
            "result_schema_id": JOB_HANDLE_SCHEMA_ID,
            "required_record_kinds": [
                "context-ref",
                "input-binding",
                "job-event",
                "job-submission",
                "operation-plan",
            ],
            "produced_record_kinds": ["job-event"],
            "context_binding": "required",
            "input_binding": "required",
            "required_context_fields": ["profile_scope", "store_id"],
            "operation_class": "disposable-runtime-execute",
            "side_effect_classes": ["store-append"],
            "preview": "required",
            "consent": "explicit-plan",
            "idempotency": "keyed-replay",
            "freshness_checks": [
                "context",
                "expected-heads",
                "input-binding",
                "physical-binding",
            ],
            "cancellation": "safe-boundaries",
            "subscription": "none",
            "resource_class": "durable-job-control",
            "claims": ["service-writer"],
            "backpressure": "reject-before-mutation",
            "budgets": [
                {"budget_key": "events", "unit": "events", "limit": 1},
                {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
            ],
        },
        {
            "capability_key": "crucible.service.job-event-page",
            "method": "job/get",
            "entrypoint": "workbench_crucible_service:JobEventPageHandler",
            "request_value_scope": "arguments",
            "request_schema_id": JOB_EVENT_PAGE_ARGUMENTS_SCHEMA_ID,
            "plan_schema_id": None,
            "result_schema_id": JOB_EVENT_PAGE_RESULT_SCHEMA_ID,
            "required_record_kinds": [
                "context-ref",
                "input-binding",
                "job-event",
                "job-submission",
            ],
            "produced_record_kinds": [],
            "context_binding": "required",
            "input_binding": "required",
            "required_context_fields": ["profile_scope", "store_id"],
            "operation_class": "inspect",
            "side_effect_classes": ["none"],
            "preview": "none",
            "consent": "none",
            "idempotency": "none",
            "freshness_checks": ["context", "input-binding"],
            "cancellation": "unsupported",
            "subscription": "none",
            "resource_class": "durable-job-event-page",
            "claims": [],
            "backpressure": "pause-producer",
            "budgets": [
                {"budget_key": "events", "unit": "events", "limit": 64},
                {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
            ],
        },
        {
            "capability_key": "crucible.service.job-subscribe",
            "method": "job/subscribe",
            "entrypoint": "workbench_crucible_service:JobSubscriptionHandler",
            "request_value_scope": "params",
            "request_schema_id": JOB_SUBSCRIPTION_REQUEST_SCHEMA_ID,
            "plan_schema_id": None,
            "result_schema_id": JOB_SUBSCRIPTION_RESULT_SCHEMA_ID,
            "required_record_kinds": [
                "context-ref",
                "input-binding",
                "job-event",
                "job-submission",
            ],
            "produced_record_kinds": [],
            "context_binding": "required",
            "input_binding": "required",
            "required_context_fields": ["profile_scope", "store_id"],
            "operation_class": "inspect",
            "side_effect_classes": ["none"],
            "preview": "none",
            "consent": "none",
            "idempotency": "none",
            "freshness_checks": ["context", "input-binding"],
            "cancellation": "unsupported",
            "subscription": "resumable",
            "resource_class": "durable-job-subscription",
            "claims": [],
            "backpressure": "pause-producer",
            "budgets": [
                {"budget_key": "events", "unit": "events", "limit": 64},
                {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
            ],
        },
    )


def _descriptor_registration_health(
    *,
    definition: Mapping[str, Any],
    provider_id: str,
    component_id: str,
    source_tree_id: str,
    dependency_lock_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    entrypoint = definition["entrypoint"]
    method = definition["method"]
    handler_id = content_id(
        "handler",
        {
            "entrypoint": entrypoint,
            "method": method,
            "source_tree_id": source_tree_id,
        },
    )
    implementation_id = content_id(
        "implementation",
        {
            "dependency_lock_id": dependency_lock_id,
            "entrypoint": entrypoint,
            "source_tree_id": source_tree_id,
        },
    )
    method_binding = {
        "protocol_method": method,
        "request_value_scope": definition["request_value_scope"],
        "request_schema_id": definition["request_schema_id"],
        "plan_schema_id": definition["plan_schema_id"],
        "progress_schema_id": None,
        "result_schema_id": definition["result_schema_id"],
        "failure_schema_id": FAILURE_SCHEMA_ID,
    }
    descriptor = _seal(
        "capability",
        {
            "descriptor_format": "workbench-capability-descriptor-v3",
            "canonicalizer": CANONICALIZER_ID,
            "capability_key": definition["capability_key"],
            "semantic_version": "1.0.0",
            "owner_component_id": component_id,
            "semantic_authority_id": None,
            "semantic_authority_binding": None,
            "provider_id": provider_id,
            "handler_id": handler_id,
            "public_entrypoint": entrypoint,
            "handler_implementation_id": implementation_id,
            "method_bindings": [method_binding],
            "record_flow": {
                "required_record_kinds": definition["required_record_kinds"],
                "produced_record_kinds": definition["produced_record_kinds"],
            },
            "context_applicability": {
                "required_context_fields": definition[
                    "required_context_fields"
                ],
                "input_binding": definition["input_binding"],
                "scope_compatibility_policy_id": None,
                "context_binding": definition["context_binding"],
                "scope_compatibility_authority": None,
            },
            "operation": {
                "operation_class": definition["operation_class"],
                "side_effect_classes": definition["side_effect_classes"],
                "preview": definition["preview"],
                "consent": definition["consent"],
                "idempotency": definition["idempotency"],
                "freshness_checks": definition["freshness_checks"],
            },
            "invocation_modes": [
                "batch",
                "cli",
                "embedded",
                "local-endpoint",
                "stdio",
            ],
            "async_behavior": {
                "progress": "none",
                "cancellation": definition["cancellation"],
                "continuation": "none",
                "subscription": definition["subscription"],
            },
            "resources": {
                "resource_class": definition["resource_class"],
                "claims": definition["claims"],
                "default_budgets": definition["budgets"],
                "backpressure": definition["backpressure"],
            },
            "profile": _profile_independent(),
            "maturity": "experimental",
        },
        "capability_id",
    )
    health = _seal(
        "health-receipt",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_id": descriptor["capability_id"],
            "checks": [
                {"check": "dependency-lock-bytes-readable", "outcome": "passed"},
                {"check": "source-tree-bytes-readable", "outcome": "passed"},
                {"check": "source-json-schemas-parse", "outcome": "passed"},
            ],
            "dependency_lock_id": dependency_lock_id,
            "format": "workbench-service-control-handler-health-receipt-v1",
            "kind": "health-receipt",
            "source_tree_id": source_tree_id,
        },
        "id",
    )
    registration = _seal(
        "handler-registration",
        {
            "registration_format": "workbench-handler-registration-v3",
            "canonicalizer": CANONICALIZER_ID,
            "capability_id": descriptor["capability_id"],
            "handler_id": handler_id,
            "provider_id": provider_id,
            "public_entrypoint": entrypoint,
            "handler_implementation_id": implementation_id,
            "method_bindings": [method_binding],
            "invocation_modes": [
                "batch",
                "cli",
                "embedded",
                "local-endpoint",
                "stdio",
            ],
            "state": "available",
            "reasons": [],
            "conformance_case_ids": [],
            "health_receipt_ids": [health["id"]],
        },
        "registration_id",
    )
    return descriptor, registration, health


def build_service_control_registry_v3(
    repository_root: Path,
) -> ServiceControlRegistryBundleV3:
    """Build the exact registry consumed by installed Service V3 hosts."""

    _require(
        isinstance(repository_root, Path)
        and repository_root.is_absolute()
        and repository_root.is_dir()
        and not repository_root.is_symlink(),
        "service-control repository root must be an absolute ordinary directory",
    )
    root = repository_root.resolve()
    try:
        package_name, package_version = load_core_component_identity(root)
    except CoreComponentIdentityError as exc:
        raise ServiceControlRegistryV3Error(str(exc)) from exc

    source_paths = [root / relative for relative in _SOURCE_RELATIVE_PATHS]
    schema_paths = [root / relative for relative in _SCHEMA_RELATIVE_PATHS]
    source_rows = _file_rows(root, source_paths)
    for path in schema_paths:
        try:
            json.loads(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ServiceControlRegistryV3Error(
                f"service-control registry schema is unreadable or invalid: {path}"
            ) from exc

    source_manifest = _seal(
        "source-tree",
        {
            "canonicalizer": CANONICALIZER_ID,
            "files": source_rows,
            "format": "workbench-service-control-source-tree-manifest-v1",
            "kind": "source-tree",
            "schema_version": 1,
        },
        "id",
    )
    dependency_manifest = build_runtime_dependency_lock_manifest_v2(
        root,
        format_name=SERVICE_CONTROL_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
        error_type=ServiceControlRegistryV3Error,
    )
    provider = _seal(
        "provider",
        {
            "provider_format": "workbench-capability-provider-v3",
            "canonicalizer": CANONICALIZER_ID,
            "provider_key": "workbench.provider.crucible.service-control",
            "distribution_id": None,
            "source_tree_id": source_manifest["id"],
            "dependency_lock_id": dependency_manifest["id"],
            "package_name": package_name,
            "package_version": package_version,
        },
        "provider_id",
    )
    component = _seal(
        "component",
        {
            "component_format": "workbench-capability-component-v3",
            "canonicalizer": CANONICALIZER_ID,
            "component_key": "workbench.component.crucible.service-control",
            "component_kind": "application-service",
            "provider_id": provider["provider_id"],
            "semantic_authority_id": None,
            "authority_adapter_id": None,
            "semantic_authority_binding": None,
            "public_contract_schema_ids": [
                SERVICE_CONTROL_SCHEMA_ID,
                SERVICE_PROTOCOL_SCHEMA_ID,
            ],
            "depends_on_component_ids": [],
        },
        "component_id",
    )

    triples = [
        _descriptor_registration_health(
            definition=definition,
            provider_id=provider["provider_id"],
            component_id=component["component_id"],
            source_tree_id=source_manifest["id"],
            dependency_lock_id=dependency_manifest["id"],
        )
        for definition in _control_definition_rows()
    ]
    capabilities = sorted(
        (row[0] for row in triples),
        key=lambda value: value["capability_key"].encode("utf-8"),
    )
    registrations = sorted(
        (row[1] for row in triples),
        key=lambda value: value["registration_id"].encode("utf-8"),
    )
    health_receipts = tuple(
        sorted(
            (row[2] for row in triples),
            key=lambda value: value["id"].encode("utf-8"),
        )
    )
    distribution = _seal(
        "service-distribution",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_ids": sorted(
                value["capability_id"] for value in capabilities
            ),
            "component_ids": [component["component_id"]],
            "format": "workbench-service-control-distribution-v1",
            "handler_registration_ids": sorted(
                value["registration_id"] for value in registrations
            ),
            "kind": "service-distribution",
            "provider_ids": [provider["provider_id"]],
            "schema_version": 1,
        },
        "id",
    )
    registry = _seal(
        "component-capability-registry",
        {
            "format": "workbench-component-capability-registry-v3",
            "schema_version": 3,
            "canonicalizer": CANONICALIZER_ID,
            "registry_generation": 1,
            "service_distribution_id": distribution["id"],
            "providers": [provider],
            "components": [component],
            "capability_descriptors": capabilities,
            "handler_registrations": registrations,
            "legacy_v2_gateway": {
                "enabled": False,
                "disposition": "partial-historical-compatibility-gateway",
                "protocol_major": 2,
                "framing": "content-length-jsonrpc-2.0",
                "method_mappings": [],
                "excluded_v3_features": [
                    "contexts",
                    "object-range-reads",
                    "durable-jobs",
                    "progress-and-cancellation",
                    "continuations",
                    "subscriptions",
                ],
            },
            "descriptor_authority_admissions": [],
            "profile_policy_admissions": [],
        },
        "registry_id",
    )
    return ServiceControlRegistryBundleV3(
        registry=registry,
        source_tree_manifest=source_manifest,
        dependency_lock_manifest=dependency_manifest,
        health_receipts=health_receipts,
        service_distribution=distribution,
    )


__all__ = [
    "CONTEXT_LIST_RESULT_SCHEMA_ID",
    "EMPTY_SERVICE_ARGUMENTS_SCHEMA_ID",
    "FAILURE_SCHEMA_ID",
    "JOB_CANCELLATION_PLAN_SCHEMA_ID",
    "JOB_CANCELLATION_REQUEST_SCHEMA_ID",
    "JOB_EVENT_PAGE_ARGUMENTS_SCHEMA_ID",
    "JOB_EVENT_PAGE_RESULT_SCHEMA_ID",
    "JOB_HANDLE_SCHEMA_ID",
    "JOB_SUBSCRIPTION_REQUEST_SCHEMA_ID",
    "JOB_SUBSCRIPTION_RESULT_SCHEMA_ID",
    "SERVICE_CAPABILITIES_RESULT_SCHEMA_ID",
    "SERVICE_CONTROL_CAPABILITY_KEYS",
    "SERVICE_CONTROL_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2",
    "SERVICE_CONTROL_SCHEMA_ID",
    "SERVICE_PROTOCOL_SCHEMA_ID",
    "ServiceControlRegistryBundleV3",
    "ServiceControlRegistryV3Error",
    "build_service_control_registry_v3",
]
