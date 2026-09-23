"""Developer-facing why and impact projections over Atlas semantic V1."""

from __future__ import annotations

from typing import Any, Mapping

from .projection import AtlasProjectionError, validate_projection


def explain_why(projection: Mapping[str, Any], query: str) -> dict[str, Any]:
    value = validate_projection(projection)
    semantic_id = _resolve(value, query)
    layers = {
        name: [row for row in layer["records"] if row["semantic_id"] == semantic_id]
        for name, layer in value["layers"].items()
    }
    descriptor = next(
        row["semantic_descriptor"]
        for rows in layers.values()
        for row in rows
    )
    diagnostics = [
        row
        for row in value["diagnostics"]
        if row["semantic_id"] == semantic_id
        or row.get("details", {}).get("requirement_semantic_id") == semantic_id
    ]
    playable = layers["PLAYABLE"]
    return {
        "format": "workbench-atlas-why-v1",
        "projection_id": value["projection_id"],
        "query": query,
        "semantic_id": semantic_id,
        "semantic_descriptor": descriptor,
        "layers": layers,
        "diagnostics": diagnostics,
        "conclusion": playable[0]["status"] if playable else "not-derived",
        "authority": {
            "SOURCE": "Pack Program Studio",
            "RUNTIME": "Crucible",
            "PLAYABLE": "Atlas derived",
        },
    }


def impact_report(projection: Mapping[str, Any], query: str | None = None) -> dict[str, Any]:
    value = validate_projection(projection)
    selected = None if query is None else _resolve(value, query)
    affected: set[str] = set()
    diagnostics = []
    for diagnostic in value["diagnostics"]:
        dependency = diagnostic.get("details", {}).get("requirement_semantic_id")
        if selected is None or diagnostic["semantic_id"] == selected or dependency == selected:
            diagnostics.append(diagnostic)
            affected.add(diagnostic["semantic_id"])
            if dependency:
                affected.add(dependency)
    changed_layers = {
        name: sum(row["semantic_id"] in affected for row in layer["records"])
        for name, layer in value["layers"].items()
    }
    return {
        "format": "workbench-atlas-impact-v1",
        "projection_id": value["projection_id"],
        "query": query,
        "selected_semantic_id": selected,
        "affected_semantic_ids": sorted(affected),
        "diagnostics": diagnostics,
        "summary": {
            "affected_semantics": len(affected),
            "diagnostics": len(diagnostics),
            "records_by_layer": changed_layers,
        },
        "limitations": [
            "This impact report is bounded to relationships and diagnostics materialized in the selected projection."
        ],
    }


def _resolve(projection: Mapping[str, Any], query: str) -> str:
    all_records = [
        row
        for layer in projection["layers"].values()
        for row in layer["records"]
    ]
    if any(row["semantic_id"] == query for row in all_records):
        return query
    normalized = query.casefold()
    candidates = {
        row["semantic_id"]
        for row in all_records
        if normalized in _search_terms(row)
    }
    if not candidates:
        raise AtlasProjectionError(f"no semantic identity matches {query!r}")
    if len(candidates) > 1:
        raise AtlasProjectionError(f"semantic query is ambiguous: {query!r}")
    return next(iter(candidates))


def _search_terms(record: Mapping[str, Any]) -> set[str]:
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            values.append(value.casefold())
        elif isinstance(value, Mapping):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(record["semantic_descriptor"])
    if record.get("attributes"):
        visit(record["attributes"].get("label"))
    return set(values)


__all__ = ["explain_why", "impact_report"]
