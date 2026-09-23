"""Source-derived Service V3 registry for the World Studio proving view."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from workbench_api.service import ServiceHandlerRegistration
from workbench_api.canonical import CANONICALIZER_ID, content_id
from workbench_api.resources import repository_root
from workbench_crucible_worldgen.view import (
    WORLD_STUDIO_PROVING_SCHEMA_ID,
    WorldStudioProvingViewHandler,
    validate_world_studio_proving_request,
    validate_world_studio_proving_result,
)

from workbench_core.component_identity import (
    CoreComponentIdentityError,
    load_core_component_identity,
)
from .runtime_dependency_identity import (
    build_runtime_dependency_lock_manifest_v2,
)

REQUEST_SCHEMA_ID = WORLD_STUDIO_PROVING_SCHEMA_ID + "#/$defs/request"
RESULT_SCHEMA_ID = WORLD_STUDIO_PROVING_SCHEMA_ID + "#/$defs/result"
FAILURE_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/"
    "service-protocol-v3.schema.json#/$defs/failure"
)
WORLD_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2 = (
    "workbench-world-studio-runtime-dependency-lock-manifest-v2"
)

_WORLD_STUDIO_CRUCIBLE_SOURCE_RELATIVE_PATHS = (
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
    "api/src/workbench_api/resources.py",
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
    "modules/crucible/src/workbench_crucible_worldgen/__init__.py",
    "modules/crucible/src/workbench_crucible_worldgen/registry_contract.py",
    "modules/crucible/src/workbench_crucible_worldgen/view.py",
)
_WORLD_STUDIO_CRUCIBLE_SCHEMA_RELATIVE_PATHS = (
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
    "modules/crucible/schemas/crucible-v2-common.schema.json",
    "modules/crucible/schemas/crucible-world-studio-proving-view-v1.schema.json",
)
_WORLD_STUDIO_SHELL_SOURCE_RELATIVE_PATHS = (
    "modules/workbench-shell/src/workbench_shell/__init__.py",
    "core/src/workbench_core/component_identity.py",
    "core/src/workbench_core/pixi_lock.py",
    "modules/workbench-shell/src/workbench_shell/runtime_dependency_identity.py",
    "modules/workbench-shell/src/workbench_shell/world_studio_registry.py",
)
_WORLD_STUDIO_SHELL_SCHEMA_RELATIVE_PATHS = (
    "modules/workbench-shell/schemas/component-capability-registry-v3.schema.json",
    "modules/workbench-shell/schemas/service-protocol-v3.schema.json",
)
_WORLD_STUDIO_SCHEMA_RELATIVE_PATHS = (
    *_WORLD_STUDIO_CRUCIBLE_SCHEMA_RELATIVE_PATHS,
    *_WORLD_STUDIO_SHELL_SCHEMA_RELATIVE_PATHS,
)
_WORLD_STUDIO_SOURCE_RELATIVE_PATHS = (
    *_WORLD_STUDIO_CRUCIBLE_SOURCE_RELATIVE_PATHS,
    *_WORLD_STUDIO_SCHEMA_RELATIVE_PATHS,
    *_WORLD_STUDIO_SHELL_SOURCE_RELATIVE_PATHS,
    "api/pyproject.toml",
    "core/pyproject.toml",
    "modules/crucible/pyproject.toml",
    "modules/workbench-shell/pyproject.toml",
)


class WorldStudioRegistryV3Error(ValueError):
    """The exact World Studio source closure cannot produce a registry."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorldStudioRegistryV3Error(message)


from workbench_crucible_worldgen.registry_contract import (
    WorldStudioRegistryBundleV3,
    WorldStudioPresenterBindingV3,
    _mint_world_studio_presenter_binding,
)


def _seal(kind: str, body: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = dict(body)
    value[field] = content_id(kind, value)
    return value


def _file_rows(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    _require(
        len(paths) == len(set(paths)),
        "World Studio registry source closure contains a duplicate path",
    )
    rows: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: item.as_posix().encode("utf-8")):
        resolved = path.resolve()
        _require(
            resolved.is_file()
            and not path.is_symlink()
            and resolved.is_relative_to(root),
            f"World Studio registry source input is unsafe or absent: {path}",
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


def build_world_studio_registry_v3(
    repository_root: Path,
) -> WorldStudioRegistryBundleV3:
    """Build the exact experimental World Studio query/view distribution."""

    _require(
        isinstance(repository_root, Path)
        and repository_root.is_absolute()
        and repository_root.is_dir()
        and not repository_root.is_symlink(),
        "World Studio registry root must be an absolute ordinary directory",
    )
    root = repository_root.resolve()
    try:
        package_name, package_version = load_core_component_identity(root)
    except CoreComponentIdentityError as exc:
        raise WorldStudioRegistryV3Error(str(exc)) from exc
    source_paths = [
        root / relative for relative in _WORLD_STUDIO_SOURCE_RELATIVE_PATHS
    ]
    schema_paths = [
        root / relative for relative in _WORLD_STUDIO_SCHEMA_RELATIVE_PATHS
    ]
    for path in schema_paths:
        try:
            json.loads(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorldStudioRegistryV3Error(
                "World Studio registry schema input is unreadable or invalid: "
                f"{path}"
            ) from exc
    source_manifest = _seal(
        "source-tree",
        {
            "canonicalizer": CANONICALIZER_ID,
            "files": _file_rows(root, source_paths),
            "format": "workbench-world-studio-source-tree-manifest-v1",
            "kind": "source-tree",
            "schema_version": 1,
        },
        "id",
    )
    dependency_manifest = build_runtime_dependency_lock_manifest_v2(
        root,
        format_name=WORLD_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2,
        error_type=WorldStudioRegistryV3Error,
    )
    provider = _seal(
        "provider",
        {
            "canonicalizer": CANONICALIZER_ID,
            "dependency_lock_id": dependency_manifest["id"],
            "distribution_id": None,
            "package_name": package_name,
            "package_version": package_version,
            "provider_format": "workbench-capability-provider-v3",
            "provider_key": "workbench.provider.world-studio",
            "source_tree_id": source_manifest["id"],
        },
        "provider_id",
    )
    component = _seal(
        "component",
        {
            "authority_adapter_id": None,
            "canonicalizer": CANONICALIZER_ID,
            "component_format": "workbench-capability-component-v3",
            "component_key": "workbench.component.world-studio.proving-view",
            "component_kind": "application-service",
            "depends_on_component_ids": [],
            "provider_id": provider["provider_id"],
            "public_contract_schema_ids": [WORLD_STUDIO_PROVING_SCHEMA_ID],
            "semantic_authority_binding": None,
            "semantic_authority_id": None,
        },
        "component_id",
    )
    entrypoint = (
        "workbench_crucible_worldgen.view:WorldStudioProvingViewHandler"
    )
    handler_id = content_id(
        "handler",
        {
            "entrypoint": entrypoint,
            "method": "graph/query",
            "source_tree_id": source_manifest["id"],
        },
    )
    implementation_id = content_id(
        "implementation",
        {
            "dependency_lock_id": dependency_manifest["id"],
            "entrypoint": entrypoint,
            "source_tree_id": source_manifest["id"],
        },
    )
    method_binding = {
        "failure_schema_id": FAILURE_SCHEMA_ID,
        "plan_schema_id": None,
        "progress_schema_id": None,
        "protocol_method": "graph/query",
        "request_schema_id": REQUEST_SCHEMA_ID,
        "request_value_scope": "arguments",
        "result_schema_id": RESULT_SCHEMA_ID,
    }
    capability = _seal(
        "capability",
        {
            "async_behavior": {
                "cancellation": "unsupported",
                "continuation": "none",
                "progress": "none",
                "subscription": "none",
            },
            "canonicalizer": CANONICALIZER_ID,
            "capability_key": "crucible.world-studio.proving-view",
            "context_applicability": {
                "context_binding": "required",
                "input_binding": "required",
                "required_context_fields": [
                    "dimension_scope",
                    "profile_scope",
                    "store_id",
                    "world_scope",
                ],
                "scope_compatibility_authority": None,
                "scope_compatibility_policy_id": None,
            },
            "descriptor_format": "workbench-capability-descriptor-v3",
            "handler_id": handler_id,
            "handler_implementation_id": implementation_id,
            "invocation_modes": [
                "batch",
                "cli",
                "embedded",
                "local-endpoint",
                "stdio",
            ],
            "maturity": "experimental",
            "method_bindings": [method_binding],
            "operation": {
                "consent": "none",
                "freshness_checks": [
                    "action-gate",
                    "context",
                    "input-binding",
                ],
                "idempotency": "content-deterministic",
                "operation_class": "inspect",
                "preview": "none",
                "side_effect_classes": ["store-read"],
            },
            "owner_component_id": component["component_id"],
            "profile": {
                "action_gate_authority": None,
                "action_gate_policy_id": None,
                "applicability_authority": None,
                "applicability_mode": "profile-independent",
                "applicability_policy_id": None,
                "profile_adapter_id": None,
                "profile_revision_refs": [],
                "profile_validation_entrypoint": None,
                "required_support_states": [],
                "support_predicate_authority": None,
                "support_predicate_policy_id": None,
                "unsupported_result": "unavailable",
            },
            "provider_id": provider["provider_id"],
            "public_entrypoint": entrypoint,
            "record_flow": {
                "produced_record_kinds": [],
                "required_record_kinds": [
                    "context-ref",
                    "graph-set-revision",
                    "input-binding",
                    "worldgen-action-gate-receipt",
                    "worldgen-w01-proof-index",
                ],
            },
            "resources": {
                "backpressure": "reject-before-mutation",
                "claims": [],
                "default_budgets": [
                    {"budget_key": "bytes", "limit": 4194304, "unit": "bytes"},
                    {"budget_key": "records", "limit": 256, "unit": "records"},
                    {"budget_key": "time", "limit": 30000, "unit": "milliseconds"},
                ],
                "resource_class": "world-studio-proving-view",
            },
            "semantic_authority_binding": None,
            "semantic_authority_id": None,
            "semantic_version": "1.0.0",
        },
        "capability_id",
    )
    health = _seal(
        "health-receipt",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_id": capability["capability_id"],
            "checks": [
                {"check": "dependency-lock-bytes-readable", "outcome": "passed"},
                {"check": "source-tree-bytes-readable", "outcome": "passed"},
                {"check": "source-json-schemas-parse", "outcome": "passed"},
            ],
            "dependency_lock_id": dependency_manifest["id"],
            "format": "workbench-world-studio-handler-health-receipt-v1",
            "kind": "health-receipt",
            "source_tree_id": source_manifest["id"],
        },
        "id",
    )
    registration = _seal(
        "handler-registration",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_id": capability["capability_id"],
            "conformance_case_ids": [],
            "handler_id": handler_id,
            "handler_implementation_id": implementation_id,
            "health_receipt_ids": [health["id"]],
            "invocation_modes": [
                "batch",
                "cli",
                "embedded",
                "local-endpoint",
                "stdio",
            ],
            "method_bindings": [method_binding],
            "provider_id": provider["provider_id"],
            "public_entrypoint": entrypoint,
            "reasons": [],
            "registration_format": "workbench-handler-registration-v3",
            "state": "available",
        },
        "registration_id",
    )
    distribution = _seal(
        "service-distribution",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_ids": [capability["capability_id"]],
            "component_ids": [component["component_id"]],
            "format": "workbench-world-studio-service-distribution-v1",
            "handler_registration_ids": [registration["registration_id"]],
            "kind": "service-distribution",
            "provider_ids": [provider["provider_id"]],
            "schema_version": 1,
        },
        "id",
    )
    registry = _seal(
        "component-capability-registry",
        {
            "canonicalizer": CANONICALIZER_ID,
            "capability_descriptors": [capability],
            "components": [component],
            "descriptor_authority_admissions": [],
            "format": "workbench-component-capability-registry-v3",
            "handler_registrations": [registration],
            "legacy_v2_gateway": {
                "disposition": "partial-historical-compatibility-gateway",
                "enabled": False,
                "excluded_v3_features": [
                    "contexts",
                    "object-range-reads",
                    "durable-jobs",
                    "progress-and-cancellation",
                    "continuations",
                    "subscriptions",
                ],
                "framing": "content-length-jsonrpc-2.0",
                "method_mappings": [],
                "protocol_major": 2,
            },
            "profile_policy_admissions": [],
            "providers": [provider],
            "registry_generation": 2,
            "schema_version": 3,
            "service_distribution_id": distribution["id"],
        },
        "registry_id",
    )
    return WorldStudioRegistryBundleV3(
        registry,
        source_manifest,
        dependency_manifest,
        health,
        distribution,
    )


def world_studio_service_registration(
    bundle: WorldStudioRegistryBundleV3,
    handler: WorldStudioProvingViewHandler,
) -> ServiceHandlerRegistration:
    """Bind the proving-view handler to its exact declared identities."""

    descriptor = bundle.registry["capability_descriptors"][0]
    declared = bundle.registry["handler_registrations"][0]
    return ServiceHandlerRegistration(
        method="graph/query",
        capability_id=descriptor["capability_id"],
        capability_version=descriptor["semantic_version"],
        handler_id=declared["handler_id"],
        implementation_id=declared["handler_implementation_id"],
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=4,
        handler=handler,
        request_validator=validate_world_studio_proving_request,
        result_validator=validate_world_studio_proving_result,
    )


def build_world_studio_presenter_binding(
    handler: WorldStudioProvingViewHandler,
) -> WorldStudioPresenterBindingV3:
    """Mint presenter custody from this loaded producer's current owner bytes.

    There is deliberately no supplied-bundle or alternate-source-root input.
    A content-addressed assertion received from a caller is not producer trust.
    """

    bundle = build_world_studio_registry_v3(repository_root(__file__))
    registration = world_studio_service_registration(bundle, handler)
    return _mint_world_studio_presenter_binding(bundle, registration)


__all__ = [
    "REQUEST_SCHEMA_ID",
    "RESULT_SCHEMA_ID",
    "WORLD_STUDIO_RUNTIME_DEPENDENCY_LOCK_FORMAT_V2",
    "WorldStudioRegistryBundleV3",
    "WorldStudioRegistryV3Error",
    "build_world_studio_registry_v3",
    "build_world_studio_presenter_binding",
    "world_studio_service_registration",
]
