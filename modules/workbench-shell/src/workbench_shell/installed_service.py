"""Installed, owner-private Service V3 lifecycle host."""

from __future__ import annotations

from workbench_crucible_service import DurableJobStore

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import threading
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from workbench_crucible_service import ContextListHandler, ServiceCapabilitiesHandler, validate_context_list_result, validate_empty_service_arguments, validate_service_capabilities_result
from workbench_core.service.runtime import LocalServiceAuthenticator, ServiceRuntimeV3
from workbench_api.service import ServiceHandlerRegistration
from workbench_api.host_filesystem import (
    private_path,
    secure_private_path,
)
from workbench_api.canonical import canonical_json_bytes, content_id

from workbench_core.host_adapter import inspect_local_host_adapter_v3
from .service_contract import validate_registry_v3
from workbench_core.service.host import HostedMethodBinding, LocalServiceClientV3, LocalServiceEndpointV3, OwnerBindingProjection, ServiceHostV3, local_service_physical_lease_ports
from .service_control_registry import (
    build_service_control_registry_v3,
)


REGISTRY_SCHEMA_ID = (
    "workbench://schemas/workbench-shell/"
    "component-capability-registry-v3.schema.json"
)
DISCOVERY_KEYS = frozenset(
    {"crucible.service.capabilities", "crucible.service.context-list"}
)
READY_FORMAT = "workbench-installed-service-ready-v2"
_NONCE = re.compile(r"service-process-nonce:[0-9a-f]{32}\Z")


class InstalledServiceV3Error(ValueError):
    """The installed discovery service cannot preserve its exact custody."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InstalledServiceV3Error(message)


def _ordinary_private_directory(path: Path, label: str) -> Path:
    _require(
        isinstance(path, Path) and path.is_absolute() and not path.is_symlink(),
        f"{label} must be one absolute non-symbolic directory",
    )
    try:
        return secure_private_path(path, directory=True)
    except OSError as exc:
        raise InstalledServiceV3Error(
            f"{label} is unavailable or accessible outside its owner: {exc}"
        ) from exc


def _schema_resources(repository_root: Path) -> Registry:
    resources = Registry()
    schema_roots = (
        repository_root / "modules/crucible/schemas",
        repository_root / "modules/workbench-shell/schemas",
    )
    for root in schema_roots:
        _require(root.is_dir() and not root.is_symlink(), "service schema root is unavailable")
        for path in sorted(root.glob("*.schema.json")):
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise InstalledServiceV3Error(
                    f"service schema is unreadable: {path.name}"
                ) from exc
            if "$id" in schema:
                resources = resources.with_resource(
                    schema["$id"], Resource.from_contents(schema)
                )
    return resources


def _schema_validator(resources: Registry, schema_id: str):
    resolved = resources.resolver().lookup(schema_id)
    validator = Draft202012Validator(
        resolved.contents,
        registry=resources,
        _resolver=resolved.resolver,
    )
    return lambda value: not list(validator.iter_errors(value))


def build_installed_discovery_registry_v3(
    repository_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project the two mechanical discovery handlers from exact source bytes."""

    _require(
        isinstance(repository_root, Path)
        and repository_root.is_absolute()
        and repository_root.is_dir()
        and not repository_root.is_symlink(),
        "service repository root must be an absolute ordinary directory",
    )
    upstream = build_service_control_registry_v3(repository_root).registry
    registry = deepcopy(dict(upstream))
    capabilities = [
        row
        for row in registry["capability_descriptors"]
        if row["capability_key"] in DISCOVERY_KEYS
    ]
    _require(
        {row["capability_key"] for row in capabilities} == DISCOVERY_KEYS,
        "source registry lacks the exact discovery capability set",
    )
    capability_ids = {row["capability_id"] for row in capabilities}
    registrations = [
        row
        for row in registry["handler_registrations"]
        if row["capability_id"] in capability_ids
    ]
    _require(
        len(registrations) == len(capabilities),
        "source registry discovery handlers are ambiguous",
    )
    distribution = {
        "canonicalizer": "workbench-canonical-json-v2",
        "capability_ids": sorted(capability_ids),
        "component_ids": sorted(
            {row["owner_component_id"] for row in capabilities}
        ),
        "format": "workbench-installed-service-discovery-distribution-v1",
        "handler_registration_ids": sorted(
            row["registration_id"] for row in registrations
        ),
        "kind": "service-distribution",
        "provider_ids": sorted({row["provider_id"] for row in capabilities}),
        "schema_version": 1,
    }
    distribution["id"] = content_id("service-distribution", distribution)
    registry.update(
        {
            "registry_generation": 2,
            "service_distribution_id": distribution["id"],
            "capability_descriptors": sorted(
                capabilities, key=lambda row: row["capability_key"].encode()
            ),
            "handler_registrations": sorted(
                registrations, key=lambda row: row["registration_id"].encode()
            ),
        }
    )
    registry.pop("registry_id", None)
    registry["registry_id"] = content_id(
        "component-capability-registry", registry
    )
    resources = _schema_resources(repository_root)
    diagnostics = validate_registry_v3(
        registry, resources, REGISTRY_SCHEMA_ID
    )
    _require(
        not diagnostics,
        "installed discovery registry failed its V3 contract: "
        + ", ".join(item.code for item in diagnostics[:8]),
    )
    return registry, distribution


def _descriptor(registry: Mapping[str, Any], capability_key: str) -> Mapping[str, Any]:
    rows = [
        row
        for row in registry["capability_descriptors"]
        if row["capability_key"] == capability_key
    ]
    _require(len(rows) == 1, f"registry capability is ambiguous: {capability_key}")
    return rows[0]


def _declaration(
    registry: Mapping[str, Any], capability_id: str
) -> Mapping[str, Any]:
    rows = [
        row
        for row in registry["handler_registrations"]
        if row["capability_id"] == capability_id
    ]
    _require(len(rows) == 1, "registry handler declaration is ambiguous")
    return rows[0]


def _method(descriptor: Mapping[str, Any], method: str) -> Mapping[str, Any]:
    rows = [
        row for row in descriptor["method_bindings"] if row["protocol_method"] == method
    ]
    _require(len(rows) == 1, f"registry method is ambiguous: {method}")
    return rows[0]


def _discovery_projection(_message: Mapping[str, Any]) -> OwnerBindingProjection:
    return OwnerBindingProjection(
        input_revision_refs=(),
        output_revision_refs=(),
        states={
            "action_gate": "not-required",
            "action_gate_decision_ids": [],
            "completeness": "complete",
            "consent": "not-applicable",
            "context": "not-applicable",
            "continuity": "not-applicable",
            "evidence": "not-applicable",
            "freshness": "not-applicable",
            "implementation": "available",
            "inputs": "not-applicable",
            "profile_revision_refs": [],
            "profile_support": "not-applicable",
            "support_decision_ids": [],
        },
        limitations=(
            {
                "code": "installed-discovery-only",
                "detail": (
                    "This installed composition exposes mechanical Service V3 "
                    "discovery only; feature handlers are registered separately."
                ),
                "ordinal": 0,
            },
        ),
    )


@dataclass(slots=True)
class InstalledServiceCompositionV3:
    registry: Mapping[str, Any]
    distribution: Mapping[str, Any]
    runtime: ServiceRuntimeV3
    authenticator: LocalServiceAuthenticator
    host: ServiceHostV3

    def close(self) -> None:
        self.runtime.close()


def compose_installed_discovery_service_v3(
    repository_root: Path,
    service_root: Path,
) -> InstalledServiceCompositionV3:
    """Create the source-derived discovery service over one private store."""

    root = _ordinary_private_directory(service_root, "service root")
    store = _ordinary_private_directory(root / "store", "service store")
    credentials = _ordinary_private_directory(
        root / "credentials", "service credential root"
    )
    registry, distribution = build_installed_discovery_registry_v3(
        repository_root
    )
    resources = _schema_resources(repository_root)
    capabilities_descriptor = _descriptor(
        registry, "crucible.service.capabilities"
    )
    contexts_descriptor = _descriptor(registry, "crucible.service.context-list")
    capabilities_method = _method(
        capabilities_descriptor, "service/capabilities"
    )
    contexts_method = _method(contexts_descriptor, "context/list")
    capabilities_declared = _declaration(
        registry, capabilities_descriptor["capability_id"]
    )
    contexts_declared = _declaration(
        registry, contexts_descriptor["capability_id"]
    )
    empty_validator = _schema_validator(
        resources, capabilities_method["request_schema_id"]
    )
    capabilities_result_validator = _schema_validator(
        resources, capabilities_method["result_schema_id"]
    )
    contexts_result_validator = _schema_validator(
        resources, contexts_method["result_schema_id"]
    )
    capabilities_registration = ServiceHandlerRegistration(
        method="service/capabilities",
        capability_id=capabilities_descriptor["capability_id"],
        capability_version=capabilities_descriptor["semantic_version"],
        handler_id=capabilities_declared["handler_id"],
        implementation_id=capabilities_declared["handler_implementation_id"],
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=2,
        handler=ServiceCapabilitiesHandler(
            registry["registry_id"],
            tuple(
                sorted(
                    row["capability_id"]
                    for row in registry["capability_descriptors"]
                )
            ),
        ),
        request_validator=lambda value: (
            validate_empty_service_arguments(value) and empty_validator(value)
        ),
        result_validator=lambda value: (
            validate_service_capabilities_result(value)
            and capabilities_result_validator(value)
        ),
        context_binding="none",
        input_binding="none",
    )
    contexts_registration = ServiceHandlerRegistration(
        method="context/list",
        capability_id=contexts_descriptor["capability_id"],
        capability_version=contexts_descriptor["semantic_version"],
        handler_id=contexts_declared["handler_id"],
        implementation_id=contexts_declared["handler_implementation_id"],
        mutation_boundary="none",
        asynchronous=False,
        maximum_concurrency=2,
        handler=ContextListHandler(),
        request_validator=lambda value: (
            validate_empty_service_arguments(value) and empty_validator(value)
        ),
        result_validator=lambda value: (
            validate_context_list_result(value) and contexts_result_validator(value)
        ),
        context_binding="none",
        input_binding="none",
    )
    runtime = ServiceRuntimeV3(store, registrations=(capabilities_registration, contexts_registration), physical_leases=local_service_physical_lease_ports(), maximum_workers=2, maximum_pending_jobs=8, store_factory=lambda root, leases: DurableJobStore(root, physical_leases=leases, context_publication_validator=lambda _context, _binding: False))
    authenticator = LocalServiceAuthenticator(credentials)
    try:
        host = ServiceHostV3(
            runtime,
            authenticator=authenticator,
            registry=registry,
            registry_validation_port=lambda candidate: not validate_registry_v3(
                candidate, resources, REGISTRY_SCHEMA_ID
            ),
            method_bindings=(
                HostedMethodBinding(
                    capabilities_registration,
                    capabilities_method["result_schema_id"],
                    _discovery_projection,
                ),
                HostedMethodBinding(
                    contexts_registration,
                    contexts_method["result_schema_id"],
                    _discovery_projection,
                ),
            ),
        )
    except Exception:
        runtime.close()
        raise
    return InstalledServiceCompositionV3(
        registry, distribution, runtime, authenticator, host
    )


def _write_ready(path: Path, value: Mapping[str, Any]) -> None:
    _require(path.is_absolute() and not path.is_symlink(), "ready path is unsafe")
    _require(
        private_path(path.parent, directory=True),
        "ready parent is not owner-private",
    )
    raw = canonical_json_bytes(value) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        secure_private_path(path, directory=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def run_installed_service_daemon_v3(
    repository_root: Path,
    *,
    service_root: Path,
    endpoint_path: Path,
    ready_path: Path,
    process_nonce: str,
) -> int:
    """Run until a graceful process signal closes the exact local endpoint."""

    from workbench_core.package_guard import environment_fingerprint, environment_id
    _require(_NONCE.fullmatch(process_nonce) is not None, "service process nonce is invalid")
    root = _ordinary_private_directory(service_root, "service root")
    run_root = _ordinary_private_directory(root / "run", "service run root")
    logs_root = _ordinary_private_directory(root / "logs", "service log root")
    service_log = logs_root / "service-v3.log"
    if service_log.exists():
        try:
            secure_private_path(service_log, directory=False)
        except OSError as exc:
            raise InstalledServiceV3Error(
                "service log is unavailable or accessible outside its owner"
            ) from exc
    _require(
        endpoint_path.is_absolute()
        and endpoint_path.parent.resolve(strict=True) == run_root
        and not endpoint_path.is_symlink(),
        "service endpoint is outside its owner-private run root",
    )
    from .feature_studio_service import compose_feature_studio_service_v3

    composition = compose_feature_studio_service_v3(repository_root, root)
    stop = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def request_stop(_signal: int, _frame: Any) -> None:
        stop.set()

    try:
        for selected in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[selected] = signal.signal(selected, request_stop)
        with LocalServiceEndpointV3(
            composition.host, endpoint_path=endpoint_path
        ):
            adapter = inspect_local_host_adapter_v3()
            ready_body = {
                "format": READY_FORMAT,
                "schema_version": 2,
                "owner_environment_id": environment_id(),
                "package_fingerprint": environment_fingerprint(),
                "process_nonce": process_nonce,
                "pid": os.getpid(),
                "service_instance_id": composition.runtime.service_instance_id,
                "service_distribution_id": composition.distribution["id"],
                "registry_id": composition.registry["registry_id"],
                "endpoint_path": str(endpoint_path),
                "credential_path": str(composition.authenticator.path),
                "store_path": str(composition.runtime.root),
                "host_adapter_receipt_id": adapter["receipt_id"],
                "claims": {
                    "feature_handlers_registered": True,
                    "profile_support_claimed": False,
                    "release_qualified": False,
                },
            }
            ready = {
                "ready_id": content_id("installed-service-ready", ready_body),
                **ready_body,
            }
            _write_ready(ready_path, ready)
            while not stop.wait(0.25):
                pass
    finally:
        composition.close()
        for selected, handler in previous_handlers.items():
            signal.signal(selected, handler)
    return 0


def probe_installed_service_v3(
    repository_root: Path,
    *,
    endpoint_path: Path,
    credential_path: Path,
) -> dict[str, Any]:
    """Authenticate, initialize, and reopen the exact live capability set."""

    discovery_registry, discovery_distribution = (
        build_installed_discovery_registry_v3(repository_root)
    )
    from .feature_studio_registry import build_feature_studio_registry_v3

    feature_bundle = build_feature_studio_registry_v3(repository_root)
    feature_registry = feature_bundle.registry
    feature_distribution = feature_bundle.service_distribution
    descriptor = _descriptor(
        discovery_registry,
        "crucible.service.capabilities",
    )
    _require(
        endpoint_path.is_absolute()
        and endpoint_path.exists()
        and not endpoint_path.is_symlink(),
        "installed service endpoint is unavailable",
    )
    _require(
        credential_path.is_absolute()
        and private_path(credential_path, directory=False),
        "installed service credential is unavailable or not private",
    )
    token = credential_path.read_text(encoding="ascii")
    _require(re.fullmatch(r"[0-9a-f]{64}", token) is not None, "installed service credential is corrupt")
    initialize_request_id = "request-v3:" + os.urandom(16).hex()
    capability_request_id = "request-v3:" + os.urandom(16).hex()
    messages = (
        {
            "id": 1,
            "jsonrpc": "2.0",
            "method": "service/initialize",
            "params": {
                "client": {"id": "workbench.installed-service-probe", "version": "1.0.0"},
                "maximum_frame_bytes": 4 * 1024 * 1024,
                "protocol_version": {"major": 3, "minor": 0},
                "request_id": initialize_request_id,
                "required_capability_ids": [descriptor["capability_id"]],
                "required_features": ["contexts"],
                "transport": "local-endpoint",
            },
        },
        {
            "id": 2,
            "jsonrpc": "2.0",
            "method": "service/capabilities",
            "params": {
                "arguments": {},
                "request": {
                    "capability_id": descriptor["capability_id"],
                    "capability_version": descriptor["semantic_version"],
                    "commit": None,
                    "context_ref_id": None,
                    "deadline": None,
                    "idempotency_key": None,
                    "input_binding_id": None,
                    "intent": "service.capabilities",
                    "operation_class": "inspect",
                    "protocol_version": {"major": 3, "minor": 0},
                    "request_id": capability_request_id,
                    "resource_budgets": [],
                },
            },
        },
    )
    responses = LocalServiceClientV3(endpoint_path, token).exchange(messages)
    _require(len(responses) == 2, "installed service returned an incomplete probe stream")
    initialization = responses[0].get("result")
    _require(
        type(initialization) is dict,
        "installed service returned an invalid initialization response",
    )
    registry_id = initialization.get("registry_id")
    candidates = {
        discovery_registry["registry_id"]: (
            discovery_registry,
            discovery_distribution,
            False,
        ),
        feature_registry["registry_id"]: (
            feature_registry,
            feature_distribution,
            True,
        ),
    }
    _require(
        registry_id in candidates,
        "installed service returned an unknown source registry",
    )
    registry, distribution, feature_handlers_registered = candidates[
        registry_id
    ]
    binding_result = responses[1].get("result")
    outcome = (
        binding_result.get("outcome")
        if type(binding_result) is dict
        else None
    )
    capability_result = (
        outcome.get("value") if type(outcome) is dict else None
    )
    _require(
        type(initialization) is dict
        and initialization.get("registry_id") == registry["registry_id"]
        and initialization.get("service_distribution_id") == distribution["id"]
        and type(capability_result) is dict
        and capability_result.get("registry_id") == registry["registry_id"]
        and capability_result.get("capability_ids")
        == sorted(row["capability_id"] for row in registry["capability_descriptors"]),
        "installed service probe differs from its exact source registry",
    )
    body = {
        "format": "workbench-installed-service-probe-v1",
        "schema_version": 1,
        "state": "ready",
        "service_instance_id": initialization["service_instance_id"],
        "service_distribution_id": distribution["id"],
        "registry_id": registry["registry_id"],
        "capability_ids": capability_result["capability_ids"],
        "transport": "local-endpoint",
        "authenticated": True,
        "claims": {
            "feature_handlers_registered": feature_handlers_registered,
            "profile_support_claimed": False,
            "release_qualified": False,
        },
    }
    return {"probe_id": content_id("installed-service-probe", body), **body}


__all__ = [
    "InstalledServiceCompositionV3",
    "InstalledServiceV3Error",
    "build_installed_discovery_registry_v3",
    "compose_installed_discovery_service_v3",
    "probe_installed_service_v3",
    "run_installed_service_daemon_v3",
]
