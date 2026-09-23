"""Exact worldgen identity, capture, graph, and proving-view boundary.

The package facade is lazy so consumers of capture, identity, or the proving
view do not import the synthetic-backed graph builder unless they request it.
"""

from importlib import import_module


_EXPORT_GROUPS = {
    ".identity": (
        "ENVELOPE_FORMAT",
        "TERMINAL_FORMAT",
        "WorldgenExecutionEnvelope",
        "WorldgenExecutionTerminal",
        "WorldgenIdentityError",
        "load_execution_envelope",
        "seal_execution_envelope",
        "seal_execution_terminal",
    ),
    ".capture": (
        "CAUSAL_FORMAT",
        "CAUSAL_PREFIX",
        "CausalTraceReceipt",
        "PopulationCaptureAudit",
        "SameRunJoinReceipt",
        "StabilityReceipt",
        "WorldgenCaptureError",
        "audit_population_capture",
        "compare_fresh_controls",
        "extract_causal_trace",
        "join_same_run",
        "load_same_run_join_receipt",
        "load_stability_receipt",
    ),
    ".graph": (
        "CATEGORY_CAPTURE_HEALTH",
        "CATEGORY_GENERATIVE",
        "CATEGORY_OCCURRENCE",
        "CATEGORY_REALIZED",
        "CATEGORY_STABILITY",
        "GRAPH_FAMILY_ID",
        "GRAPH_SET_FAMILY_ID",
        "RESOLUTION_CHUNK",
        "RESOLUTION_EXACT",
        "RESOLUTION_OPERATIONAL",
        "RESOLUTION_POSITION",
        "RESOLUTION_REGION",
        "WorldgenGraphBuild",
        "WorldgenGraphError",
        "WorldgenGraphMember",
        "WorldgenGraphStore",
        "WorldgenIncrementalReceipt",
        "WorldgenProofBundle",
        "WorldgenPublicationReceipt",
        "WorldgenQueryResult",
        "WorldgenStoredQueryService",
        "benchmark_worldgen_queries",
        "build_worldgen_graph_set",
        "evaluate_synthetic_tested_support_gate",
        "evaluate_worldgen_action",
        "load_worldgen_proof_bundle",
    ),
    ".view": (
        "MAX_WORLD_STUDIO_QUERY_ROW_BYTES",
        "MAX_WORLD_STUDIO_QUERY_ROWS",
        "MAX_WORLD_STUDIO_RESULT_BYTES",
        "MAX_WORLD_STUDIO_RESULT_DEPTH",
        "MAX_WORLD_STUDIO_RESULT_NODES",
        "WORLD_STUDIO_PROVING_SCHEMA_ID",
        "WorldStudioProvingViewError",
        "WorldStudioProvingViewHandler",
        "validate_world_studio_proving_request",
        "validate_world_studio_proving_result",
    ),
}
_LAZY_EXPORTS = {
    name: module_name
    for module_name, names in _EXPORT_GROUPS.items()
    for name in names
}


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "ENVELOPE_FORMAT",
    "TERMINAL_FORMAT",
    "WorldgenExecutionEnvelope",
    "WorldgenExecutionTerminal",
    "WorldgenIdentityError",
    "CAUSAL_FORMAT",
    "CAUSAL_PREFIX",
    "CausalTraceReceipt",
    "PopulationCaptureAudit",
    "SameRunJoinReceipt",
    "StabilityReceipt",
    "WorldgenCaptureError",
    "audit_population_capture",
    "compare_fresh_controls",
    "extract_causal_trace",
    "join_same_run",
    "load_same_run_join_receipt",
    "load_stability_receipt",
    "CATEGORY_CAPTURE_HEALTH",
    "CATEGORY_GENERATIVE",
    "CATEGORY_OCCURRENCE",
    "CATEGORY_REALIZED",
    "CATEGORY_STABILITY",
    "GRAPH_FAMILY_ID",
    "GRAPH_SET_FAMILY_ID",
    "RESOLUTION_CHUNK",
    "RESOLUTION_EXACT",
    "RESOLUTION_OPERATIONAL",
    "RESOLUTION_POSITION",
    "RESOLUTION_REGION",
    "WorldgenGraphBuild",
    "WorldgenGraphError",
    "WorldgenGraphMember",
    "WorldgenGraphStore",
    "WorldgenIncrementalReceipt",
    "WorldgenProofBundle",
    "WorldgenPublicationReceipt",
    "WorldgenQueryResult",
    "WorldgenStoredQueryService",
    "benchmark_worldgen_queries",
    "build_worldgen_graph_set",
    "evaluate_synthetic_tested_support_gate",
    "evaluate_worldgen_action",
    "load_worldgen_proof_bundle",
    "load_execution_envelope",
    "seal_execution_envelope",
    "seal_execution_terminal",
    "MAX_WORLD_STUDIO_QUERY_ROW_BYTES",
    "MAX_WORLD_STUDIO_QUERY_ROWS",
    "MAX_WORLD_STUDIO_RESULT_BYTES",
    "MAX_WORLD_STUDIO_RESULT_DEPTH",
    "MAX_WORLD_STUDIO_RESULT_NODES",
    "WORLD_STUDIO_PROVING_SCHEMA_ID",
    "WorldStudioProvingViewError",
    "WorldStudioProvingViewHandler",
    "validate_world_studio_proving_request",
    "validate_world_studio_proving_result",
]
