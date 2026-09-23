"""Source-derived live Service V3 registry for Feature Studio."""

from __future__ import annotations

from copy import deepcopy
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
from .feature_studio import feature_studio_capabilities
from .runtime_dependency_identity import (
    build_runtime_dependency_lock_manifest_v2,
)
from .service_control_registry import (
    FAILURE_SCHEMA_ID,
    SERVICE_CONTROL_CAPABILITY_KEYS,
    build_service_control_registry_v3,
)


FEATURE_SERVICE_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/feature-studio-service-v1.schema.json"
)
FEATURE_ARGUMENTS_SCHEMA_ID = FEATURE_SERVICE_SCHEMA_ID + "#/$defs/featureArguments"
FEATURE_OPERATION_PLAN_SCHEMA_ID = (
    FEATURE_SERVICE_SCHEMA_ID + "#/$defs/featureOperationPlan"
)
FEATURE_PROGRESS_SCHEMA_ID = FEATURE_SERVICE_SCHEMA_ID + "#/$defs/featureProgress"
FEATURE_RESULT_SCHEMA_ID = FEATURE_SERVICE_SCHEMA_ID + "#/$defs/featureResult"
FEATURE_JOB_RESULT_ARGUMENTS_SCHEMA_ID = (
    FEATURE_SERVICE_SCHEMA_ID + "#/$defs/jobResultArguments"
)
CONTEXT_REGISTRATION_ARGUMENTS_SCHEMA_ID = (
    FEATURE_SERVICE_SCHEMA_ID + "#/$defs/contextRegistrationArguments"
)
CONTEXT_REGISTRATION_PLAN_SCHEMA_ID = (
    FEATURE_SERVICE_SCHEMA_ID + "#/$defs/contextRegistrationPlan"
)
CONTEXT_REGISTRATION_RESULT_SCHEMA_ID = (
    FEATURE_SERVICE_SCHEMA_ID + "#/$defs/contextRegistrationResult"
)

CONTROL_CAPABILITY_KEYS = SERVICE_CONTROL_CAPABILITY_KEYS
FEATURE_OPERATIONS = ("inspect", "plan", "verify", "explain", "export")
FEATURE_CAPABILITY_KEYS = {
    operation: f"workbench.feature-studio.{operation}"
    for operation in FEATURE_OPERATIONS
}
CONTEXT_REGISTRATION_CAPABILITY_KEY = (
    "workbench.feature-studio.context-register"
)
FEATURE_JOB_RESULT_CAPABILITY_KEY = "workbench.feature-studio.job-result"
FEATURE_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2 = (
    "workbench-feature-studio-runtime-dependency-lock-manifest-v2"
)


class FeatureStudioRegistryV3Error(ValueError):
    """The current source tree cannot produce an exact registry."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FeatureStudioRegistryV3Error(message)


def _seal(kind: str, body: Mapping[str, Any], identity_field: str) -> dict[str, Any]:
    value = dict(body)
    value[identity_field] = content_id(kind, value)
    return value


def _file_rows(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: item.as_posix().encode("utf-8")):
        resolved = path.resolve()
        _require(
            resolved.is_file()
            and not path.is_symlink()
            and resolved.is_relative_to(root),
            f"Feature Studio registry source input is unsafe or absent: {path}",
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
class FeatureStudioRegistryBundleV3:
    registry: Mapping[str, Any]
    service_distribution: Mapping[str, Any]
    source_tree_manifest: Mapping[str, Any]
    dependency_lock_manifest: Mapping[str, Any]
    health_receipts: tuple[Mapping[str, Any], ...]
    live_binding_projection: Mapping[str, Any]


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


def _descriptor_pair(
    *,
    capability_key: str,
    method: str,
    entrypoint: str,
    source_tree_id: str,
    dependency_lock_id: str,
    provider_id: str,
    component_id: str,
    request_schema_id: str,
    result_schema_id: str,
    plan_schema_id: str | None,
    progress_schema_id: str | None,
    required_record_kinds: list[str],
    produced_record_kinds: list[str],
    context_binding: str,
    input_binding: str,
    operation_class: str,
    side_effect_classes: list[str],
    preview: str,
    consent: str,
    idempotency: str,
    freshness_checks: list[str],
    asynchronous: bool,
    resource_class: str,
    claims: list[str],
    budgets: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    handler_id = content_id(
        "handler",
        {
            "capability_key": capability_key,
            "entrypoint": entrypoint,
            "method": method,
            "source_tree_id": source_tree_id,
        },
    )
    implementation_id = content_id(
        "implementation",
        {
            "capability_key": capability_key,
            "dependency_lock_id": dependency_lock_id,
            "entrypoint": entrypoint,
            "source_tree_id": source_tree_id,
        },
    )
    binding = {
        "protocol_method": method,
        "request_value_scope": "arguments",
        "request_schema_id": request_schema_id,
        "plan_schema_id": plan_schema_id,
        "progress_schema_id": progress_schema_id,
        "result_schema_id": result_schema_id,
        "failure_schema_id": FAILURE_SCHEMA_ID,
    }
    descriptor = _seal(
        "capability",
        {
            "descriptor_format": "workbench-capability-descriptor-v3",
            "canonicalizer": CANONICALIZER_ID,
            "capability_key": capability_key,
            "semantic_version": "1.0.0",
            "owner_component_id": component_id,
            "semantic_authority_id": None,
            "semantic_authority_binding": None,
            "provider_id": provider_id,
            "handler_id": handler_id,
            "public_entrypoint": entrypoint,
            "handler_implementation_id": implementation_id,
            "method_bindings": [binding],
            "record_flow": {
                "required_record_kinds": sorted(required_record_kinds),
                "produced_record_kinds": sorted(produced_record_kinds),
            },
            "context_applicability": {
                "required_context_fields": (
                    ["profile_scope", "workspace_binding"]
                    if context_binding == "required"
                    else []
                ),
                "input_binding": input_binding,
                "scope_compatibility_policy_id": None,
                "context_binding": context_binding,
                "scope_compatibility_authority": None,
            },
            "operation": {
                "operation_class": operation_class,
                "side_effect_classes": sorted(side_effect_classes),
                "preview": preview,
                "consent": consent,
                "idempotency": idempotency,
                "freshness_checks": sorted(freshness_checks),
            },
            "invocation_modes": [
                "batch",
                "cli",
                "embedded",
                "local-endpoint",
                "stdio",
            ],
            "async_behavior": {
                "progress": "durable-job-events" if asynchronous else "none",
                "cancellation": "safe-boundaries" if asynchronous else "unsupported",
                "continuation": "none",
                "subscription": "none",
            },
            "resources": {
                "resource_class": resource_class,
                "claims": sorted(claims),
                "default_budgets": sorted(
                    budgets,
                    key=lambda row: (
                        row["budget_key"].encode("utf-8"),
                        row["unit"].encode("utf-8"),
                    ),
                ),
                "backpressure": "reject-before-mutation",
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
            "format": "workbench-feature-studio-handler-health-receipt-v1",
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
            "method_bindings": [binding],
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


def _live_projection(
    registry: Mapping[str, Any],
    distribution: Mapping[str, Any],
    source_tree_id: str,
    dependency_lock_id: str,
) -> dict[str, Any]:
    capability_projection = feature_studio_capabilities()
    live_by_key = {
        row["capability_key"]: row
        for row in registry["capability_descriptors"]
    }
    declared_by_operation = {
        row["operation"]: row for row in capability_projection["descriptors"]
    }
    descriptors: list[dict[str, Any]] = []
    for operation in FEATURE_OPERATIONS:
        declared_row = declared_by_operation[operation]
        capability_key = FEATURE_CAPABILITY_KEYS[operation]
        live = live_by_key[capability_key]
        descriptors.append(
            {
                "operation": operation,
                "capability_descriptor_id": declared_row["descriptor_id"],
                "state": "available",
                "reason": "feature-studio.service-v3-registered",
                "capability_id": live["capability_id"],
                "capability_key": live["capability_key"],
                "handler_id": live["handler_id"],
                "handler_implementation_id": live["handler_implementation_id"],
                "protocol_method": live["method_bindings"][0]["protocol_method"],
                "asynchronous": operation in {"verify", "export"},
            }
        )
    controls = {
        key: live_by_key[key]["capability_id"]
        for key in sorted(
            CONTROL_CAPABILITY_KEYS
            | {
                CONTEXT_REGISTRATION_CAPABILITY_KEY,
                FEATURE_JOB_RESULT_CAPABILITY_KEY,
            }
        )
    }
    body = {
        "format": "workbench-feature-studio-service-binding-projection-v3",
        "schema_version": 3,
        "canonicalizer": CANONICALIZER_ID,
        "capability_projection_id": capability_projection["projection_id"],
        "registry_id": registry["registry_id"],
        "service_distribution_id": distribution["id"],
        "source_tree_id": source_tree_id,
        "dependency_lock_id": dependency_lock_id,
        "descriptors": descriptors,
        "control_capability_ids": controls,
        "authority_boundary": {
            "descriptor_authority_admissions": False,
            "profile_policy_admissions": False,
            "direct_source_application": False,
        },
        "limitations": [
            "Profile state is preserved from an exact registered owner context and is not a support admission.",
            "Verification and export observe cancellation only at declared safe owner boundaries.",
        ],
    }
    return {
        "projection_id": content_id(
            "feature-studio-service-binding-projection", body
        ),
        **body,
    }


def build_feature_studio_registry_v3(
    repository_root: Path,
) -> FeatureStudioRegistryBundleV3:
    """Build the current Feature Studio Service V3 registry."""

    _require(
        isinstance(repository_root, Path)
        and repository_root.is_absolute()
        and repository_root.is_dir()
        and not repository_root.is_symlink(),
        "Feature Studio repository root must be an absolute ordinary directory",
    )
    root = repository_root.resolve()
    shell = root / "modules/workbench-shell"
    try:
        package_name, package_version = load_core_component_identity(root)
    except CoreComponentIdentityError as exc:
        raise FeatureStudioRegistryV3Error(str(exc)) from exc
    source_names = [
        "blueprint_stage.py",
        "feature_studio.py",
        "feature_studio_registry.py",
        "feature_studio_service.py",
        "material_fluid_flow.py",
        "runtime_dependency_identity.py",
        "runtime_observe.py",
        "runtime_plan.py",
        "stdio_host.py",
    ]
    source_paths = [shell / "src/workbench_shell" / name for name in source_names]
    source_paths.extend(root / "core/src/workbench_core" / name for name in (
        "component_identity.py", "pixi_lock.py", "host_adapter.py",
        "host_filesystem.py", "host_services.py", "service/host.py",
        "service/runtime.py", "service/framing.py", "package_guard.py",
    ))
    source_paths.extend(root / "api/src/workbench_api" / name for name in (
        "canonical.py", "service.py", "host_filesystem.py", "__init__.py", "modules.py",
        "profiles.py", "resources.py",
    ))
    # Optional owner bytes affect this binding when the complete owner is
    # present, independently of enable-state. Missing/broken optional profiles
    # must not disable generic service discovery or context/job-control APIs.
    profile_root = root / "profiles/packs/supersymmetry"
    profile_sources = [profile_root / path for path in (
        "pyproject.toml",
        "src/workbench_profile_supersymmetry/__init__.py",
    )]
    if all(path.is_file() and not path.is_symlink() for path in profile_sources):
        source_paths.extend(profile_sources)
    from workbench_api.profile_extensions import installed_profile_code_identity
    profile_code = installed_profile_code_identity("supersymmetry")
    schema_names = [
        "component-capability-registry-v3.schema.json",
        "feature-studio-capabilities-v2.schema.json",
        "feature-studio-request-v2.schema.json",
        "feature-studio-result-v2.schema.json",
        "feature-studio-service-bindings-v3.schema.json",
        "feature-studio-service-v1.schema.json",
        "service-protocol-v3.schema.json",
    ]
    schema_paths = [shell / "schemas" / name for name in schema_names]
    for path in schema_paths:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FeatureStudioRegistryV3Error(
                f"Feature Studio registry schema is unavailable: {path.name}"
            ) from exc
    source_manifest = _seal(
        "source-tree",
        {
            "canonicalizer": CANONICALIZER_ID,
            "native_profile_owners": [] if profile_code is None else [profile_code],
            "files": _file_rows(
                root,
                source_paths
                + schema_paths
                + [root / path for path in ("api/pyproject.toml", "core/pyproject.toml", "modules/crucible/pyproject.toml", "modules/workbench-shell/pyproject.toml")],
            ),
            "format": "workbench-feature-studio-source-tree-manifest-v1",
            "kind": "source-tree",
            "schema_version": 1,
        },
        "id",
    )
    dependency_manifest = build_runtime_dependency_lock_manifest_v2(
        root,
        format_name=FEATURE_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
        error_type=FeatureStudioRegistryV3Error,
    )
    provider = _seal(
        "provider",
        {
            "provider_format": "workbench-capability-provider-v3",
            "canonicalizer": CANONICALIZER_ID,
            "provider_key": "workbench.provider.shell.feature-studio",
            "distribution_id": None,
            "source_tree_id": source_manifest["id"],
            "dependency_lock_id": dependency_manifest["id"],
            "package_name": package_name,
            "package_version": package_version,
        },
        "provider_id",
    )

    upstream = deepcopy(
        dict(build_service_control_registry_v3(root).registry)
    )
    upstream_capabilities = [
        row
        for row in upstream["capability_descriptors"]
        if row["capability_key"] in CONTROL_CAPABILITY_KEYS
    ]
    _require(
        {row["capability_key"] for row in upstream_capabilities}
        == CONTROL_CAPABILITY_KEYS,
        "accepted Crucible registry lacks the exact Feature Studio control set",
    )
    upstream_capability_ids = {
        row["capability_id"] for row in upstream_capabilities
    }
    upstream_registrations = [
        row
        for row in upstream["handler_registrations"]
        if row["capability_id"] in upstream_capability_ids
    ]
    _require(
        len(upstream_registrations) == len(upstream_capabilities),
        "accepted Crucible control registrations are ambiguous",
    )
    upstream_component_ids = {
        row["owner_component_id"] for row in upstream_capabilities
    }
    _require(
        len(upstream_component_ids) == 1,
        "accepted Crucible controls do not have one exact component",
    )
    component = _seal(
        "component",
        {
            "component_format": "workbench-capability-component-v3",
            "canonicalizer": CANONICALIZER_ID,
            "component_key": "workbench.component.shell.feature-studio-service",
            "component_kind": "application-service",
            "provider_id": provider["provider_id"],
            "semantic_authority_id": None,
            "authority_adapter_id": None,
            "semantic_authority_binding": None,
            "public_contract_schema_ids": sorted(
                {
                    FEATURE_SERVICE_SCHEMA_ID,
                    "workbench://schemas/workbench-shell/feature-studio-request-v2.schema.json",
                    "workbench://schemas/workbench-shell/feature-studio-result-v2.schema.json",
                }
            ),
            "depends_on_component_ids": sorted(upstream_component_ids),
        },
        "component_id",
    )

    descriptors: list[dict[str, Any]] = []
    registrations: list[dict[str, Any]] = []
    health_receipts: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        descriptor, registration, health = _descriptor_pair(
            source_tree_id=source_manifest["id"],
            dependency_lock_id=dependency_manifest["id"],
            provider_id=provider["provider_id"],
            component_id=component["component_id"],
            **kwargs,
        )
        descriptors.append(descriptor)
        registrations.append(registration)
        health_receipts.append(health)

    add(
        capability_key=CONTEXT_REGISTRATION_CAPABILITY_KEY,
        method="context/register",
        entrypoint="workbench_shell.feature_studio_service:ContextRegistrationHandler",
        request_schema_id=CONTEXT_REGISTRATION_ARGUMENTS_SCHEMA_ID,
        result_schema_id=CONTEXT_REGISTRATION_RESULT_SCHEMA_ID,
        plan_schema_id=CONTEXT_REGISTRATION_PLAN_SCHEMA_ID,
        progress_schema_id=None,
        required_record_kinds=["context-ref", "input-binding", "operation-plan"],
        produced_record_kinds=["context-ref", "input-binding"],
        context_binding="none",
        input_binding="none",
        operation_class="stable-construct",
        side_effect_classes=["store-append"],
        preview="required",
        consent="explicit-plan",
        idempotency="keyed-replay",
        freshness_checks=["expected-heads", "physical-binding", "writer-lease"],
        asynchronous=False,
        resource_class="service-context-registration",
        claims=["service-writer"],
        budgets=[
            {"budget_key": "bytes", "unit": "bytes", "limit": 4194304},
            {"budget_key": "records", "unit": "records", "limit": 2},
            {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
        ],
    )

    for operation in FEATURE_OPERATIONS:
        asynchronous = operation in {"verify", "export"}
        effects = (
            ["reference-update", "runtime-process", "store-append"]
            if operation == "verify"
            else ["external-export", "reference-update", "store-append"]
        )
        add(
            capability_key=FEATURE_CAPABILITY_KEYS[operation],
            method="operation/commit" if asynchronous else "operation/plan",
            entrypoint="workbench_shell.feature_studio_service:FeatureStudioOperationHandler",
            request_schema_id=FEATURE_ARGUMENTS_SCHEMA_ID,
            result_schema_id=FEATURE_RESULT_SCHEMA_ID,
            plan_schema_id=(
                FEATURE_OPERATION_PLAN_SCHEMA_ID if asynchronous else None
            ),
            progress_schema_id=FEATURE_PROGRESS_SCHEMA_ID if asynchronous else None,
            required_record_kinds=[
                "context-ref",
                "feature-studio-owner-request",
                "input-binding",
                *( ["operation-plan"] if asynchronous else [] ),
            ],
            produced_record_kinds=[
                "feature-studio-result",
                *( ["job-terminal-seal"] if asynchronous else [] ),
            ],
            context_binding="required",
            input_binding="required",
            operation_class="stable-construct" if asynchronous else "inspect",
            side_effect_classes=effects if asynchronous else ["none"],
            preview="required" if asynchronous else "none",
            consent="explicit-plan" if asynchronous else "none",
            idempotency="keyed-replay" if asynchronous else "none",
            freshness_checks=(
                ["context", "expected-heads", "input-binding", "writer-lease"]
                if asynchronous
                else ["context", "input-binding"]
            ),
            asynchronous=asynchronous,
            resource_class=f"feature-studio-{operation}",
            claims=[f"feature-studio-{operation}-owner"] if asynchronous else [],
            budgets=[
                {"budget_key": "bytes", "unit": "bytes", "limit": 134217728},
                {"budget_key": "records", "unit": "records", "limit": 100000},
                {
                    "budget_key": "time",
                    "unit": "milliseconds",
                    "limit": 86400000 if asynchronous else 600000,
                },
            ],
        )

    add(
        capability_key=FEATURE_JOB_RESULT_CAPABILITY_KEY,
        method="job/get",
        entrypoint="workbench_shell.feature_studio_service:FeatureStudioJobResultHandler",
        request_schema_id=FEATURE_JOB_RESULT_ARGUMENTS_SCHEMA_ID,
        result_schema_id=FEATURE_RESULT_SCHEMA_ID,
        plan_schema_id=None,
        progress_schema_id=None,
        required_record_kinds=[
            "context-ref",
            "input-binding",
            "job-submission",
            "job-terminal-seal",
        ],
        produced_record_kinds=[],
        context_binding="required",
        input_binding="required",
        operation_class="inspect",
        side_effect_classes=["store-read"],
        preview="none",
        consent="none",
        idempotency="none",
        freshness_checks=["context", "input-binding"],
        asynchronous=False,
        resource_class="feature-studio-job-result",
        claims=[],
        budgets=[
            {"budget_key": "bytes", "unit": "bytes", "limit": 134217728},
            {"budget_key": "records", "unit": "records", "limit": 1},
            {"budget_key": "time", "unit": "milliseconds", "limit": 30000},
        ],
    )

    capabilities = sorted(
        [*upstream_capabilities, *descriptors],
        key=lambda row: row["capability_key"].encode("utf-8"),
    )
    all_registrations = sorted(
        [*upstream_registrations, *registrations],
        key=lambda row: row["registration_id"].encode("utf-8"),
    )
    upstream_components = [
        row
        for row in upstream["components"]
        if row["component_id"] in upstream_component_ids
    ]
    upstream_provider_ids = {row["provider_id"] for row in upstream_components}
    upstream_providers = [
        row
        for row in upstream["providers"]
        if row["provider_id"] in upstream_provider_ids
    ]
    providers = sorted(
        [*upstream_providers, provider],
        key=lambda row: row["provider_key"].encode("utf-8"),
    )
    components = sorted(
        [*upstream_components, component],
        key=lambda row: row["component_key"].encode("utf-8"),
    )
    distribution = _seal(
        "service-distribution",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_ids": sorted(
                row["capability_id"] for row in capabilities
            ),
            "component_ids": sorted(row["component_id"] for row in components),
            "format": "workbench-feature-studio-service-distribution-v1",
            "handler_registration_ids": sorted(
                row["registration_id"] for row in all_registrations
            ),
            "kind": "service-distribution",
            "provider_ids": sorted(row["provider_id"] for row in providers),
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
            "registry_generation": 3,
            "service_distribution_id": distribution["id"],
            "providers": providers,
            "components": components,
            "capability_descriptors": capabilities,
            "handler_registrations": all_registrations,
            "legacy_v2_gateway": deepcopy(upstream["legacy_v2_gateway"]),
            "descriptor_authority_admissions": [],
            "profile_policy_admissions": [],
        },
        "registry_id",
    )
    projection = _live_projection(
        registry,
        distribution,
        source_manifest["id"],
        dependency_manifest["id"],
    )
    return FeatureStudioRegistryBundleV3(
        registry=registry,
        service_distribution=distribution,
        source_tree_manifest=source_manifest,
        dependency_lock_manifest=dependency_manifest,
        health_receipts=tuple(health_receipts),
        live_binding_projection=projection,
    )


__all__ = [
    "CONTEXT_REGISTRATION_ARGUMENTS_SCHEMA_ID",
    "CONTEXT_REGISTRATION_CAPABILITY_KEY",
    "CONTEXT_REGISTRATION_PLAN_SCHEMA_ID",
    "CONTEXT_REGISTRATION_RESULT_SCHEMA_ID",
    "CONTROL_CAPABILITY_KEYS",
    "FEATURE_ARGUMENTS_SCHEMA_ID",
    "FEATURE_CAPABILITY_KEYS",
    "FEATURE_JOB_RESULT_ARGUMENTS_SCHEMA_ID",
    "FEATURE_JOB_RESULT_CAPABILITY_KEY",
    "FEATURE_OPERATION_PLAN_SCHEMA_ID",
    "FEATURE_OPERATIONS",
    "FEATURE_PROGRESS_SCHEMA_ID",
    "FEATURE_RESULT_SCHEMA_ID",
    "FEATURE_SERVICE_SCHEMA_ID",
    "FEATURE_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2",
    "FeatureStudioRegistryBundleV3",
    "FeatureStudioRegistryV3Error",
    "build_feature_studio_registry_v3",
]
