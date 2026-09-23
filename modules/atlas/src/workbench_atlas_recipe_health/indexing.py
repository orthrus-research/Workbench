"""Bounded public operation for rebuilding disposable recipe query storage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from workbench_atlas_categorical_graph import (
    rebuild_query_index,
)


INDEX_OPERATION_FORMAT = "workbench-atlas-recipe-index-operation-v1"
DEFAULT_MAX_SOURCE_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MAX_INDEX_BYTES = 4 * 1024 * 1024 * 1024

IndexProgress = Callable[[Mapping[str, Any]], None]


def _positive_bound(value: object, label: str) -> int:
    from .view import RecipeHealthError

    if type(value) is not int or value < 1:
        raise RecipeHealthError(f"recipe index {label} must be a positive integer")
    return value


def _operation_id(record: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        record,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "workbench-atlas-recipe-index-operation:sha256:" + hashlib.sha256(
        encoded
    ).hexdigest()


def rebuild_recipe_health_index(
    path: Path,
    *,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_index_bytes: int = DEFAULT_MAX_INDEX_BYTES,
    progress: IndexProgress | None = None,
) -> dict[str, Any]:
    """Rebuild a verified derived index under explicit source/disk byte bounds."""

    max_source_bytes = _positive_bound(max_source_bytes, "source-byte bound")
    max_index_bytes = _positive_bound(max_index_bytes, "index-byte bound")
    if progress is not None and not callable(progress):
        from .view import RecipeHealthError

        raise RecipeHealthError("recipe index progress callback must be callable")

    requested_root = Path(path)
    progress_updates = 0
    phases: list[str] = []
    validated: dict[str, Any] | None = None

    def observe(update: Mapping[str, Any]) -> None:
        nonlocal progress_updates, validated
        progress_updates += 1
        phase = str(update.get("phase", "unknown"))
        if phase not in phases:
            phases.append(phase)
        if phase == "validated":
            validated = dict(update)
        if progress is not None:
            progress(update)

    after = rebuild_query_index(
        requested_root,
        max_source_bytes=max_source_bytes,
        max_index_bytes=max_index_bytes,
        progress=observe,
    )
    if validated is None:
        from .view import RecipeHealthError

        raise RecipeHealthError("recipe index rebuild omitted validated custody")
    root = Path(validated["root"])
    graph_set_id = validated["graph_set_id"]
    source_bytes = validated["source_bytes_total"]
    source_records = validated["records_total"]
    previous_descriptor = validated["previous_query_index"]
    free_bytes_before = validated["free_bytes_before"]
    required_free_bytes = validated["required_free_bytes"]
    if after["graph_set_id"] != graph_set_id:
        from .view import RecipeHealthError

        raise RecipeHealthError("recipe index rebuild changed graph identity")
    descriptor = after["query_index"]
    record: dict[str, Any] = {
        "format": INDEX_OPERATION_FORMAT,
        "schema_version": 1,
        "operation": "rebuild-derived-query-index",
        "state": "complete",
        "root": str(root),
        "graph_set_id": graph_set_id,
        "bounds": {
            "max_source_bytes": max_source_bytes,
            "max_index_bytes": max_index_bytes,
            "source_stream_bytes": source_bytes,
            "source_record_count": source_records,
            "free_bytes_before": free_bytes_before,
            "required_free_bytes": required_free_bytes,
        },
        "custody": {
            "authoritative_graph_evidence_mutated": False,
            "graph_identity_preserved": True,
            "derived_index_replaced": True,
            "manifest_change_scope": "query_index descriptor only",
            "derived_paths": ["query-index.sqlite3", "manifest.json#query_index"],
            "previous_query_index": previous_descriptor,
            "published_query_index": descriptor,
        },
        "progress": {
            "phases": phases,
            "update_count": progress_updates,
            "terminal_phase": phases[-1] if phases else None,
        },
    }
    record["operation_id"] = _operation_id(record)
    return record
