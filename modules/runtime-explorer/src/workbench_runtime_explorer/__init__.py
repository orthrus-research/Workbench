"""Workbench Exact Runtime Explorer."""

from importlib import import_module

from .model import (
    ExplorerError,
    ExplorerRecord,
    ExplorerSource,
    RESULT_FORMAT,
    RESULT_SCHEMA_VERSION,
    validate_result,
)
from .query import Explorer, ExplorerRequest, parse_query


_GRAPH_QUERY_EXPORTS = frozenset(
    {
        "EmbeddedGraphQueryPresenterV2",
        "EmbeddedGraphQueryResultV2",
        "MAX_GRAPH_TERMINAL_BYTES",
        "execute_embedded_graph_query_v2",
        "graph_query_presenter_manifest_v2",
        "render_embedded_graph_query_result_v2",
    }
)


def __getattr__(name: str):
    if name not in _GRAPH_QUERY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".graph_query", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "Explorer",
    "ExplorerError",
    "ExplorerRecord",
    "ExplorerRequest",
    "ExplorerSource",
    "EmbeddedGraphQueryPresenterV2",
    "EmbeddedGraphQueryResultV2",
    "MAX_GRAPH_TERMINAL_BYTES",
    "RESULT_FORMAT",
    "RESULT_SCHEMA_VERSION",
    "execute_embedded_graph_query_v2",
    "graph_query_presenter_manifest_v2",
    "render_embedded_graph_query_result_v2",
    "validate_result",
    "parse_query",
]
