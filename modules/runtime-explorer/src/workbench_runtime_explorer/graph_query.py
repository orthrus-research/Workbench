"""V2-native embedded presentation for the registered worldgen graph query.

This module is deliberately not a provider adapter.  It executes one exact
registered M4 V01 ``graph/query`` request through a trusted in-process Service
V3 composition, retains the validated owner result as the machine-readable
answer, and renders a non-authoritative terminal view over those same bytes.
It creates no Explorer V1 records and assigns no category authority itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import json
from typing import Any, BinaryIO, Mapping, TextIO

from workbench_api.service import ServiceHandlerRegistration, ServiceV3Error
from workbench_core.service.runtime import ServiceRuntimeV3
from workbench_api.canonical import CanonicalJsonError, canonical_json_bytes, content_id as crucible_content_id, parse_canonical_json
from workbench_crucible_worldgen.view import (
    WorldStudioProvingViewHandler,
    validate_world_studio_proving_request,
    validate_world_studio_proving_result,
)
from workbench_api.events import sanitize_terminal
from workbench_crucible_worldgen.registry_contract import (
    WorldStudioPresenterBindingV3,
)

from .model import ExplorerError


PRESENTER_VERSION = 2
MAX_GRAPH_REQUEST_BYTES = 64 * 1024
MAX_GRAPH_RESULT_BYTES = 3 * 1024 * 1024
MAX_GRAPH_RESULT_RECORDS = 256
MAX_GRAPH_ROW_BYTES = 64 * 1024
MAX_GRAPH_RESULT_DEPTH = 64
MAX_GRAPH_RESULT_NODES = 8192
MAX_GRAPH_TERMINAL_BYTES = 16 * 1024 * 1024
WORLD_STUDIO_RESULT_FORMAT = "workbench-world-studio-proving-view-result-v1"
WORLD_STUDIO_RESULT_KIND = "world-studio-proving-view-result"

WORLD_STUDIO_CAPABILITY_VERSION = "1.0.0"
WORLD_STUDIO_SCHEMA_ID = (
    "workbench://schemas/crucible/"
    "crucible-world-studio-proving-view-v1.schema.json#/$defs/result"
)


def _route_material(binding: WorldStudioPresenterBindingV3) -> dict[str, Any]:
    if not _composition_matches_route(binding):
        raise ExplorerError("embedded graph/query presenter lacks the exact trusted composition")
    composition = binding.composition
    registration = binding.registration
    return {
        "accepted_operations": ["query"],
        "authority_source": "validated-owner-result",
        "capability_id": registration.capability_id,
        "capability_version": WORLD_STUDIO_CAPABILITY_VERSION,
        "handler_id": registration.handler_id,
        "implementation_id": registration.implementation_id,
        "invocation_modes": ["embedded"],
        "json_output": "exact-owner-result-bytes-with-lf",
        "json_sink": "single-write-binary",
        "method": "graph/query",
        "comparison_policy": "forbidden",
        "fallback_policy": "none",
        "presenter_version": PRESENTER_VERSION,
        "registry_id": composition.registry["registry_id"],
        "result_format": WORLD_STUDIO_RESULT_FORMAT,
        "result_kind": WORLD_STUDIO_RESULT_KIND,
        "result_schema_id": WORLD_STUDIO_SCHEMA_ID,
        "service_distribution_id": composition.service_distribution["id"],
        "source_tree_id": composition.source_tree_manifest["id"],
        "terminal_output": {
            "format": "deterministic-control-safe-view",
            "maximum_bytes": MAX_GRAPH_TERMINAL_BYTES,
            "sink_write_policy": "single-write-after-complete-validation",
        },
        "request_bounds": {
            "enforcement_phase": "before-service-dispatch",
            "maximum_depth": MAX_GRAPH_RESULT_DEPTH,
            "maximum_nodes": MAX_GRAPH_RESULT_NODES,
            "maximum_request_bytes": MAX_GRAPH_REQUEST_BYTES,
        },
        "owner_result_admission_bounds": {
            "enforcement_phase": "after-owner-service-validation",
            "maximum_depth": MAX_GRAPH_RESULT_DEPTH,
            "maximum_nodes": MAX_GRAPH_RESULT_NODES,
            "maximum_result_bytes": MAX_GRAPH_RESULT_BYTES,
            "maximum_result_rows": MAX_GRAPH_RESULT_RECORDS,
            "maximum_row_bytes": MAX_GRAPH_ROW_BYTES,
        },
    }


def graph_query_presenter_manifest_v2(binding: WorldStudioPresenterBindingV3) -> dict[str, Any]:
    """Bind a presenter route to the trusted producer's exact current identities."""

    material = _route_material(binding)
    route_id = crucible_content_id("runtime-explorer-graph-query-route", material)
    return {
        "format": "workbench-runtime-explorer-graph-query-presenter-v2",
        "kind": "runtime-explorer-graph-query-presenter",
        "route": {**material, "route_id": route_id},
        "route_id": route_id,
        "schema_version": 2,
    }


_RESULT_MINT = object()


@dataclass(frozen=True, slots=True, init=False)
class EmbeddedGraphQueryResultV2:
    """Owner-result bytes minted only after trusted embedded execution."""

    _owner_result_bytes: bytes
    _route_id: str

    def __init__(self, owner_result_bytes: bytes, *, route_id: str, _mint: object = None) -> None:
        if _mint is not _RESULT_MINT:
            raise ExplorerError(
                "embedded graph/query results can only be minted by trusted execution"
            )
        if type(owner_result_bytes) is not bytes:
            raise ExplorerError("embedded graph/query owner bytes must be exact bytes")
        object.__setattr__(self, "_owner_result_bytes", owner_result_bytes)
        object.__setattr__(self, "_route_id", route_id)

    @property
    def route_id(self) -> str:
        return self._route_id

    @property
    def owner_result_bytes(self) -> bytes:
        return self._owner_result_bytes

    @property
    def owner_result_id(self) -> str:
        return str(self.to_dict()["id"])

    def to_dict(self) -> dict[str, Any]:
        value = parse_canonical_json(self._owner_result_bytes)
        if type(value) is not dict:
            raise ExplorerError("embedded graph/query owner result is not an object")
        return value

    def write_json(self, output: BinaryIO) -> None:
        """Write exact owner bytes plus LF to one binary sink operation."""

        if not hasattr(output, "write"):
            raise ExplorerError("embedded graph/query JSON requires a binary sink")
        try:
            written = output.write(self._owner_result_bytes + b"\n")
        except TypeError as exc:
            raise ExplorerError(
                "embedded graph/query JSON requires a binary sink"
            ) from exc
        if (
            type(written) is not int
            or written != len(self._owner_result_bytes) + 1
        ):
            raise ExplorerError("embedded graph/query JSON sink performed a short write")

    def render(self, output: TextIO) -> None:
        render_embedded_graph_query_result_v2(self, output)


class EmbeddedGraphQueryPresenterV2:
    """Closed native presenter over one trusted embedded Service V3 runtime."""

    __slots__ = (
        "_action_gate_resolver",
        "_binding",
        "_handler",
        "_proof_index_resolver",
        "_query_resolver",
        "_registration",
        "_runtime",
    )

    def __init__(
        self,
        runtime: ServiceRuntimeV3,
        binding: WorldStudioPresenterBindingV3,
    ) -> None:
        if type(runtime) is not ServiceRuntimeV3:
            raise ExplorerError(
                "embedded graph/query presenter requires an exact Service V3 runtime"
            )
        registration = _registered_world_studio(runtime, binding)
        handler = registration.handler
        self._runtime = runtime
        self._binding = binding
        self._registration = registration
        self._handler = handler
        self._query_resolver = handler.query_resolver
        self._proof_index_resolver = handler.proof_index_resolver
        self._action_gate_resolver = handler.action_gate_resolver

    @property
    def manifest(self) -> Mapping[str, Any]:
        return graph_query_presenter_manifest_v2(self._binding)

    def query(
        self,
        *,
        arguments: Mapping[str, Any],
        context_ref_id: str,
        input_binding_id: str,
        request_id: str,
    ) -> EmbeddedGraphQueryResultV2:
        current = _registered_world_studio(self._runtime, self._binding)
        if not (
            current is self._registration
            and current.handler is self._handler
            and self._handler.query_resolver is self._query_resolver
            and self._handler.proof_index_resolver is self._proof_index_resolver
            and self._handler.action_gate_resolver is self._action_gate_resolver
        ):
            raise ExplorerError(
                "embedded graph/query trusted registration or resolver drifted"
            )
        request = _bound_request(
            arguments,
            context_ref_id=context_ref_id,
            input_binding_id=input_binding_id,
        )
        try:
            response = self._runtime.dispatch(
                {
                    "arguments": request,
                    "capability_id": self._registration.capability_id,
                    "context_ref_id": context_ref_id,
                    "idempotency_key": None,
                    "input_binding_id": input_binding_id,
                    "method": "graph/query",
                    "request_id": request_id,
                }
            )
        except ServiceV3Error as exc:
            if exc.code == "unavailable-capability":
                raise ExplorerError(
                    "registered embedded graph/query capability is unavailable"
                ) from None
            raise ExplorerError(
                "registered embedded graph/query execution failed"
            ) from None
        except Exception:
            raise ExplorerError(
                "registered embedded graph/query execution failed"
            ) from None
        if type(response) is not dict or response.get("outcome") != "succeeded":
            raise ExplorerError(
                "registered embedded graph/query did not return a successful result"
            )
        raw = _validated_owner_result(response.get("result"), request=request)
        return EmbeddedGraphQueryResultV2(raw, route_id=self.manifest["route_id"], _mint=_RESULT_MINT)


def execute_embedded_graph_query_v2(
    runtime: ServiceRuntimeV3,
    *,
    binding: WorldStudioPresenterBindingV3,
    arguments: Mapping[str, Any],
    context_ref_id: str,
    input_binding_id: str,
    request_id: str,
) -> EmbeddedGraphQueryResultV2:
    """Execute one exact registered query and retain its owner-native result."""

    return EmbeddedGraphQueryPresenterV2(runtime, binding).query(
        arguments=arguments,
        context_ref_id=context_ref_id,
        input_binding_id=input_binding_id,
        request_id=request_id,
    )


def _registered_world_studio(
    runtime: ServiceRuntimeV3,
    binding: WorldStudioPresenterBindingV3,
) -> ServiceHandlerRegistration:
    if not _composition_matches_route(binding):
        raise ExplorerError(
            "embedded graph/query presenter lacks the exact trusted composition"
        )
    expected = binding.registration
    registration = runtime.registrations.get((expected.capability_id, "graph/query"))
    if not (
        type(registration) is ServiceHandlerRegistration
        and registration is expected
        and registration.method == "graph/query"
        and registration.capability_id == expected.capability_id
        and registration.capability_version == WORLD_STUDIO_CAPABILITY_VERSION
        and registration.handler_id == expected.handler_id
        and registration.implementation_id == expected.implementation_id
        and registration.mutation_boundary == "none"
        and registration.asynchronous is False
        and registration.maximum_concurrency == 4
        and type(registration.handler) is WorldStudioProvingViewHandler
        and registration.request_validator
        is validate_world_studio_proving_request
        and registration.result_validator is validate_world_studio_proving_result
        and registration.context_binding == "required"
        and registration.input_binding == "required"
    ):
        raise ExplorerError(
            "embedded graph/query presenter lacks the exact trusted registration"
        )
    return registration


def _sealed_record(value: Any, *, kind: str, identity_field: str) -> bool:
    if type(value) is not dict:
        return False
    body = dict(value)
    supplied = body.pop(identity_field, None)
    try:
        return supplied == crucible_content_id(kind, body)
    except (CanonicalJsonError, RecursionError, TypeError, ValueError):
        return False


def _composition_matches_route(value: Any) -> bool:
    if type(value) is not WorldStudioPresenterBindingV3 or not value.matches_current_composition():
        return False
    expected = value.registration
    if type(expected) is not ServiceHandlerRegistration:
        return False
    value = value.composition
    registry = value.registry
    distribution = value.service_distribution
    source_tree = value.source_tree_manifest
    health = value.health_receipt
    dependency_lock = value.dependency_lock_manifest
    if not (
        type(registry) is dict
        and type(distribution) is dict
        and type(source_tree) is dict
        and type(health) is dict
        and type(dependency_lock) is dict
        and _sealed_record(
            registry,
            kind="component-capability-registry",
            identity_field="registry_id",
        )
        and _sealed_record(
            distribution,
            kind="service-distribution",
            identity_field="id",
        )
        and _sealed_record(source_tree, kind="source-tree", identity_field="id")
        and _sealed_record(health, kind="health-receipt", identity_field="id")
        and _sealed_record(
            dependency_lock,
            kind="dependency-lock",
            identity_field="id",
        )
        and registry.get("service_distribution_id")
        == distribution.get("id")
    ):
        return False
    descriptors = registry.get("capability_descriptors")
    registrations = registry.get("handler_registrations")
    providers = registry.get("providers")
    components = registry.get("components")
    if not (
        type(descriptors) is list
        and len(descriptors) == 1
        and type(descriptors[0]) is dict
        and type(registrations) is list
        and len(registrations) == 1
        and type(registrations[0]) is dict
        and type(providers) is list
        and len(providers) == 1
        and type(providers[0]) is dict
        and type(components) is list
        and len(components) == 1
        and type(components[0]) is dict
    ):
        return False
    descriptor = descriptors[0]
    declared = registrations[0]
    provider = providers[0]
    component = components[0]
    entrypoint = "workbench_crucible_worldgen.view:WorldStudioProvingViewHandler"
    methods = descriptor.get("method_bindings")
    return bool(
        _sealed_record(
            descriptor,
            kind="capability",
            identity_field="capability_id",
        )
        and _sealed_record(
            declared,
            kind="handler-registration",
            identity_field="registration_id",
        )
        and _sealed_record(provider, kind="provider", identity_field="provider_id")
        and _sealed_record(component, kind="component", identity_field="component_id")
        and descriptor.get("capability_id") == expected.capability_id
        and descriptor.get("capability_key") == "crucible.world-studio.proving-view"
        and descriptor.get("semantic_version") == WORLD_STUDIO_CAPABILITY_VERSION
        and descriptor.get("handler_id") == expected.handler_id
        and descriptor.get("handler_implementation_id")
        == expected.implementation_id
        and descriptor.get("public_entrypoint") == entrypoint
        and declared.get("capability_id") == expected.capability_id
        and declared.get("handler_id") == expected.handler_id
        and declared.get("handler_implementation_id")
        == expected.implementation_id
        and declared.get("public_entrypoint") == entrypoint
        and declared.get("state") == "available"
        and type(methods) is list and len(methods) == 1 and type(methods[0]) is dict
        and methods == declared.get("method_bindings")
        and methods[0].get("protocol_method") == "graph/query"
        and methods[0].get("request_schema_id") == WORLD_STUDIO_SCHEMA_ID.removesuffix("result") + "request"
        and methods[0].get("result_schema_id") == WORLD_STUDIO_SCHEMA_ID
        and methods[0].get("request_value_scope") == "arguments"
        and descriptor.get("owner_component_id") == component.get("component_id")
        and component.get("component_key") == "workbench.component.world-studio.proving-view"
        and component.get("provider_id") == provider.get("provider_id")
        and provider.get("source_tree_id") == source_tree.get("id")
        and provider.get("dependency_lock_id") == dependency_lock.get("id")
        and expected.handler_id == crucible_content_id("handler", {
            "entrypoint": entrypoint, "method": "graph/query", "source_tree_id": source_tree["id"],
        })
        and expected.implementation_id == crucible_content_id("implementation", {
            "entrypoint": entrypoint, "source_tree_id": source_tree["id"], "dependency_lock_id": dependency_lock["id"],
        })
        and declared.get("health_receipt_ids") == [health.get("id")]
        and health.get("capability_id") == expected.capability_id
        and health.get("source_tree_id") == source_tree.get("id")
        and health.get("dependency_lock_id") == dependency_lock.get("id")
        and distribution.get("capability_ids") == [expected.capability_id]
        and distribution.get("component_ids") == [component.get("component_id")]
        and distribution.get("provider_ids") == [provider.get("provider_id")]
        and distribution.get("handler_registration_ids")
        == [declared.get("registration_id")]
    )


def _utf8_size(
    value: str,
    *,
    label: str,
    maximum_bytes: int | None = None,
) -> int:
    size = 0
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ExplorerError(f"{label} contains a surrogate code point")
        if codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if maximum_bytes is not None and size > maximum_bytes:
            raise ExplorerError(f"{label} exceeds its UTF-8 byte bound")
    return size


def _canonical_string_size(
    value: str,
    *,
    label: str,
    maximum_bytes: int,
) -> int:
    """Measure a canonical JSON string without constructing its encoding."""

    size = 2
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ExplorerError(f"{label} contains a surrogate code point")
        if character in {'"', "\\"}:
            size += 2
        elif codepoint <= 0x1F:
            size += 6
        elif codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > maximum_bytes:
            raise ExplorerError("graph/query value exceeds the byte preflight")
    return size


def _bounded_json_value(value: Any, *, maximum_bytes: int) -> None:
    """Reject open, deep, or over-budget values before admission."""

    stack = [(value, 0)]
    nodes = 0
    measured = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_GRAPH_RESULT_NODES:
            raise ExplorerError("graph/query value exceeds the structural node bound")
        if depth > MAX_GRAPH_RESULT_DEPTH:
            raise ExplorerError("graph/query value exceeds the structural depth bound")
        if item is None:
            measured += 4
        elif type(item) is bool:
            measured += 4 if item else 5
        elif type(item) is int:
            if not -(2**63) <= item <= 2**63 - 1:
                raise ExplorerError(
                    "graph/query integer is outside the signed 64-bit domain"
                )
            measured += len(str(item))
        elif type(item) is str:
            measured += _canonical_string_size(
                item,
                label="graph/query JSON string",
                maximum_bytes=maximum_bytes,
            )
        elif type(item) is list:
            measured += max(0, len(item) - 1) + 2
            stack.extend((nested, depth + 1) for nested in item)
        elif type(item) is dict:
            measured += max(0, len(item) - 1) + 2
            for key, nested in item.items():
                if type(key) is not str:
                    raise ExplorerError("graph/query JSON keys must be strings")
                measured += _canonical_string_size(
                    key,
                    label="graph/query JSON key",
                    maximum_bytes=maximum_bytes,
                ) + 1
                stack.append((nested, depth + 1))
        else:
            raise ExplorerError("graph/query value is outside ordinary JSON")
        if measured > maximum_bytes:
            raise ExplorerError("graph/query value exceeds the byte preflight")


def _bound_request(
    arguments: Mapping[str, Any],
    *,
    context_ref_id: str,
    input_binding_id: str,
) -> dict[str, Any]:
    if type(arguments) is not dict:
        raise ExplorerError("registered graph/query arguments must be an exact object")
    _bounded_json_value(arguments, maximum_bytes=MAX_GRAPH_REQUEST_BYTES)
    try:
        raw = canonical_json_bytes(arguments)
        if len(raw) > MAX_GRAPH_REQUEST_BYTES:
            raise ExplorerError(
                "canonical graph/query request exceeds the request bound"
            )
        request = parse_canonical_json(raw)
    except (
        CanonicalJsonError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise ExplorerError(
            "registered graph/query arguments are not canonical JSON"
        ) from exc
    if not (
        type(request) is dict
        and validate_world_studio_proving_request(request)
        and request.get("operation") == "query"
        and request.get("comparison_graph_set_revision_id") is None
        and request.get("comparison_query") is None
        and request.get("context_ref_id") == context_ref_id
        and request.get("input_binding_id") == input_binding_id
    ):
        raise ExplorerError(
            "registered graph/query arguments differ from their exact binding"
        )
    return request


def _detached_result(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise ExplorerError("registered graph/query returned a non-object")
    candidate = dict(value)
    query_result = candidate.get("query_result")
    if type(query_result) is not dict:
        raise ExplorerError("V01 owner result query_result must be an object")
    rows = query_result.get("rows")
    if type(rows) is not list or len(rows) > MAX_GRAPH_RESULT_RECORDS:
        raise ExplorerError("V01 owner result exceeds the record bound")
    for row in rows:
        if type(row) is not dict:
            raise ExplorerError("V01 graph row is not an ordinary object")
        raw = row.get("record_canonical_json")
        if type(raw) is not str:
            raise ExplorerError("V01 graph row exceeds the row bound")
        _utf8_size(
            raw,
            label="V01 graph row",
            maximum_bytes=MAX_GRAPH_ROW_BYTES,
        )
    _bounded_json_value(candidate, maximum_bytes=MAX_GRAPH_RESULT_BYTES)
    try:
        raw = canonical_json_bytes(candidate)
        if len(raw) > MAX_GRAPH_RESULT_BYTES:
            raise ExplorerError("canonical V01 owner result exceeds the result bound")
        detached = parse_canonical_json(raw)
    except (
        CanonicalJsonError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise ExplorerError("V01 owner result is outside canonical JSON V2") from exc
    if type(detached) is not dict:
        raise ExplorerError("V01 owner result must be one ordinary object")
    return detached


def _result_matches_request(
    result: Mapping[str, Any],
    request: Mapping[str, Any],
) -> bool:
    action_gate = result.get("action_gate")
    query_result = result.get("query_result")
    return bool(
        type(request) is dict
        and validate_world_studio_proving_request(request)
        and request.get("operation") == "query"
        and request.get("comparison_graph_set_revision_id") is None
        and request.get("comparison_query") is None
        and result.get("operation") == request.get("operation")
        and result.get("context_ref_id") == request.get("context_ref_id")
        and result.get("input_binding_id") == request.get("input_binding_id")
        and result.get("graph_set_revision_id")
        == request.get("graph_set_revision_id")
        and result.get("proof_index_id") == request.get("proof_index_id")
        and result.get("comparison_graph_set_revision_id") is None
        and result.get("comparison_query_result") is None
        and type(action_gate) is dict
        and action_gate.get("receipt_id") == request.get("action_gate_receipt_id")
        and type(query_result) is dict
        and query_result.get("query") == request.get("query")
    )


def _validated_owner_result(
    value: Any,
    *,
    request: Mapping[str, Any],
) -> bytes:
    result = _detached_result(value)
    if not (
        result.get("format") == WORLD_STUDIO_RESULT_FORMAT
        and result.get("kind") == WORLD_STUDIO_RESULT_KIND
        and result.get("schema_version") == 1
        and result.get("operation") == "query"
    ):
        raise ExplorerError("graph/query result has no native presenter route")
    try:
        valid = validate_world_studio_proving_result(result)
    except Exception as exc:
        raise ExplorerError("V01 owner result validator failed") from exc
    if valid is not True:
        raise ExplorerError("V01 owner result failed its owning validator")
    if not _result_matches_request(result, request):
        raise ExplorerError("V01 owner result differs from the exact request")

    authority_rows = result["authority_owners"]
    owner_by_category = {
        row["category_id"]: row["owner"] for row in authority_rows
    }
    rows = result["query_result"]["rows"]
    row_ids = [row["row_id"] for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise ExplorerError("V01 owner result repeats an exact query row")
    for row in rows:
        category_id = row.get("category_id")
        if type(category_id) is not str or category_id not in owner_by_category:
            raise ExplorerError(
                "V01 graph row has no owner-result authority binding"
            )
    raw = canonical_json_bytes(result)
    if len(raw) > MAX_GRAPH_RESULT_BYTES:
        raise ExplorerError("canonical V01 owner result exceeds the result bound")
    return raw


def _safe(value: object) -> str:
    return sanitize_terminal(str(value))


def _record_label(record: Mapping[str, Any], row_id: str) -> tuple[str, str]:
    kind = record.get("record_kind") or record.get("kind") or "graph-record"
    name = (
        record.get("logical_key")
        or record.get("subject_id")
        or record.get("graph_record_id")
        or row_id
    )
    return str(kind), str(name)


def render_embedded_graph_query_result_v2(
    value: EmbeddedGraphQueryResultV2,
    output: TextIO,
) -> None:
    """Render one bounded terminal write without producing semantic records."""

    if type(value) is not EmbeddedGraphQueryResultV2:
        raise ExplorerError(
            "native graph/query rendering requires a trusted embedded result"
        )
    rendered = StringIO()
    try:
        result = value.to_dict()
    except (
        CanonicalJsonError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise ExplorerError(
            "trusted embedded graph/query result is no longer canonical"
        ) from exc
    owner_by_category = {
        row["category_id"]: row["owner"]
        for row in result["authority_owners"]
    }
    query_result = result["query_result"]
    query_text = json.dumps(
        query_result["query"],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    # The row order is part of the sealed owner query result.  Presentation
    # preserves it instead of constructing a second ranking or ordering rule.
    rows = query_result["rows"]
    rendered.write(
        "Runtime Explorer graph/query · "
        f"{len(rows)} row{'s' if len(rows) != 1 else ''}"
        f" · {'truncated' if query_result['truncated'] else 'complete'}\n"
    )
    rendered.write(f"Route: {_safe(value.route_id)}\n")
    rendered.write(f"Owner result: {_safe(result['id'])}\n")
    rendered.write(
        f"Owner query result: {_safe(query_result['query_result_id'])}\n"
    )
    rendered.write(f"Graph set: {_safe(result['graph_set_revision_id'])}\n")
    rendered.write(f"Proof index: {_safe(result['proof_index_id'])}\n")
    rendered.write(
        f"Action gate: {_safe(result['action_gate']['receipt_id'])}\n"
    )
    rendered.write(f"Context: {_safe(result['context_ref_id'])}\n")
    rendered.write(f"Input binding: {_safe(result['input_binding_id'])}\n")
    rendered.write(
        "Raw archive opens: "
        f"{_safe(query_result['raw_archive_open_count'])}\n"
    )
    rendered.write(f"Query: {_safe(query_text)}\n")
    rendered.write(
        f"Support: {_safe(result['support_state'])}"
        f" · profile {_safe(result['profile_state'])}\n"
    )
    rendered.write("Authority (owner result):\n")
    for owner_row in result["authority_owners"]:
        category_id = owner_row["category_id"]
        rendered.write(
            f"  {_safe(category_id)} -> {_safe(owner_row['owner'])}\n"
        )
    rendered.write("\n")
    if not rows:
        rendered.write("No rows returned by the pinned owner query.\n")
    for index, row in enumerate(rows, 1):
        record_text = row["record_canonical_json"]
        _utf8_size(record_text, label="validated V01 owner record")
        try:
            record = parse_canonical_json(record_text.encode("utf-8"))
        except (
            CanonicalJsonError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise ExplorerError(
                "validated V01 row no longer contains canonical JSON"
            ) from exc
        if type(record) is not dict:
            raise ExplorerError("validated V01 row no longer contains an object")
        category_id = row["category_id"]
        kind, name = _record_label(record, row["row_id"])
        rendered.write(
            f"{index:>2}. {_safe(name)} "
            f"[{_safe(kind)} · {_safe(owner_by_category[category_id])}]\n"
        )
        rendered.write(f"    Category: {_safe(category_id)}\n")
        rendered.write(f"    Row: {_safe(row['row_id'])}\n")
        if row["graph_record_id"] is not None:
            rendered.write(f"    Record: {_safe(row['graph_record_id'])}\n")
        for revision_id in row["graph_revision_ids"]:
            rendered.write(f"    Revision: {_safe(revision_id)}\n")
        rendered.write(
            f"    Owner record: {_safe(row['record_canonical_json'])}\n"
        )
    if query_result["truncated"]:
        rendered.write(
            "\n! The owner query result is truncated at its declared bound.\n"
        )
    for limitation in result["limitations"]:
        rendered.write(f"! {_safe(limitation)}\n")

    terminal_text = rendered.getvalue()
    _utf8_size(
        terminal_text,
        label="native graph/query terminal output",
        maximum_bytes=MAX_GRAPH_TERMINAL_BYTES,
    )
    try:
        output.write(terminal_text)
    except (TypeError, UnicodeError) as exc:
        raise ExplorerError(
            "native graph/query terminal output requires a text sink"
        ) from exc


__all__ = [
    "EmbeddedGraphQueryPresenterV2",
    "EmbeddedGraphQueryResultV2",
    "MAX_GRAPH_REQUEST_BYTES",
    "MAX_GRAPH_RESULT_BYTES",
    "MAX_GRAPH_RESULT_DEPTH",
    "MAX_GRAPH_RESULT_NODES",
    "MAX_GRAPH_RESULT_RECORDS",
    "MAX_GRAPH_ROW_BYTES",
    "MAX_GRAPH_TERMINAL_BYTES",
    "PRESENTER_VERSION",
    "WORLD_STUDIO_CAPABILITY_VERSION",
    "WORLD_STUDIO_SCHEMA_ID",
    "execute_embedded_graph_query_v2",
    "graph_query_presenter_manifest_v2",
    "render_embedded_graph_query_result_v2",
]
