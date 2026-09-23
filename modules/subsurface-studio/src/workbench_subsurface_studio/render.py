"""High-signal terminal rendering for GTCEu Subsurface Studio results."""

from __future__ import annotations

import shutil
from typing import Any, Iterable, Mapping

try:
    from workbench_api.events import sanitize_terminal
except ImportError:  # Keep the owner module usable outside the aggregate router.
    def sanitize_terminal(value: str) -> str:
        return "".join(
            character
            if character in "\t\n" or ord(character) >= 32
            else "�"
            for character in value
        )


_HEAT = " .:-=+*#%@"
_CATEGORY_SYMBOLS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _clean(value: Any) -> str:
    return sanitize_terminal(str(value)).replace("\r", " ")


def _number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return _clean(value)


def _lines(title: str, rows: Iterable[str]) -> list[str]:
    materialized = list(rows)
    return [title, *materialized] if materialized else [title, "  none"]


def _scope(report: Mapping[str, Any]) -> str:
    scope = report["scope"]
    window = scope["chunk_window"]
    return (
        f"seed {_clean(scope['world_seed'])} · dim {_clean(scope['dimension_id'])} · "
        f"chunks {window['min_chunk_x']},{window['min_chunk_z']} +"
        f"{window['chunk_size_x']}×{window['chunk_size_z']} (halo {window['halo_chunks']})"
    )


def _render_summary(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    final = value["final_state"]
    definitions = value["definitions"]
    attribution = value["attribution"]
    rows = [
        (
            f"Definitions  {definitions['ore_definition_count']:,} ore · "
            f"{definitions['material_token_count']:,} materials · "
            f"controls {'exact' if definitions['exact_controls_available'] else 'partial'}"
        ),
        (
            f"Final state  {final['ore_block_count']:,} ore blocks · "
            f"{final['ore_material_count']:,} materials · "
            f"{final['subsurface_air_count']:,} subsurface air · "
            f"{final['exposed_ore_face_count']:,} exposed ore faces"
        ),
        f"Indicators   {final['surface_indicator_count']:,} exact GTCEu surface rocks",
        (
            f"Fluids      {final['virtual_fluid_cell_count']:,} virtual cells · "
            f"{final['virtual_fluid_yield_total']:,} total yield"
        ),
        (
            f"Attribution {_clean(attribution['state'])} · "
            f"deposits {_clean(attribution['deposit_count'])} · "
            f"decisions {_clean(attribution['decision_count'])} · "
            f"absence {'closed' if attribution['absence_closed'] else 'open'}"
        ),
    ]
    materials = final["ore_materials"]
    shown = materials[:12]
    rows.extend(
        _lines(
            f"Top ore materials ({len(shown)} of {len(materials)})",
            (
                "  " + " · ".join(
                    f"{_clean(row['material'])} {_number(row['count'])}" for row in shown
                ),
            ),
        )
    )
    lithology = final["captured_lithology"]
    rows.extend(
        _lines(
            "Captured lithology",
            (
                "  " + " · ".join(
                    f"{_clean(row['lithology'])} {_number(row['count'])}"
                    for row in lithology
                ),
            ),
        )
    )
    return rows


def _render_layers(report: Mapping[str, Any]) -> list[str]:
    return [
        f"{_clean(row['layer_id']):<18} {_clean(row['evidence_state']):<21} "
        f"{_clean(row['title'])}\n  {_clean(row['description'])}"
        for row in report["result"]["layers"]
    ]


def _heat_symbol(value: float, minimum: float, maximum: float) -> str:
    if maximum == minimum:
        return _HEAT[-1] if value else _HEAT[0]
    ratio = (value - minimum) / (maximum - minimum)
    index = min(len(_HEAT) - 1, max(0, int(round(ratio * (len(_HEAT) - 1)))))
    return _HEAT[index]


def _render_map(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    width = value["width"]
    cells = value["cells"]
    numeric = value["numeric_range"]
    rows = [
        f"{_clean(value['layer']['title'])}"
        + (f" · material {_clean(value['material'])}" if value["material"] else "")
    ]
    symbols: dict[str, str] = {}
    if numeric is None:
        categories = value["categories"]
        symbols = {
            category: _CATEGORY_SYMBOLS[index]
            for index, category in enumerate(categories)
        }
    for offset in range(0, len(cells), width):
        line_cells = cells[offset : offset + width]
        z = line_cells[0]["chunk_z"]
        if numeric is not None:
            glyphs = "".join(
                _heat_symbol(
                    float(cell["value"]),
                    float(numeric["minimum"]),
                    float(numeric["maximum"]),
                )
                for cell in line_cells
            )
        else:
            glyphs = "".join(symbols[str(cell["value"])] for cell in line_cells)
        rows.append(f"z={z:>6}  {glyphs}")
    first_x = cells[0]["chunk_x"]
    last_x = cells[width - 1]["chunk_x"]
    rows.append(f"          x {first_x} … {last_x}")
    if numeric is not None:
        rows.append(
            f"Range {_number(numeric['minimum'])} … {_number(numeric['maximum'])}   {_HEAT}"
        )
    else:
        rows.extend(
            f"  {symbol}  {_clean(category)}" for category, symbol in symbols.items()
        )
    return rows


def _render_explain(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    position = value["position"]
    final = value["final_state"]
    surface = value["surface"]
    cave = value["cave_context"]
    attribution = value["attribution"]
    rows = [
        f"Position     {position['x']}, {position['y']}, {position['z']}",
        f"Final block  {_clean(final['block_state'])}",
        (
            f"Class        {_clean(final['kind'])}"
            + (f" · material {_clean(final['material'])}" if final["material"] else "")
            + (f" · lithology {_clean(final['lithology'])}" if final["lithology"] else "")
        ),
        (
            f"Surface      Y={surface['height']} · {_clean(surface['biome'])} · "
            f"{'below' if surface['below_surface'] else 'at/above'} surface"
        ),
    ]
    nearest = cave["nearest_final_subsurface_air"]
    if nearest:
        near_position = nearest["position"]
        nearest_text = (
            f"{near_position['x']},{near_position['y']},{near_position['z']} "
            f"at {nearest['distance']} blocks"
        )
    else:
        nearest_text = f"none within {cave['search_radius']} blocks"
    rows.extend(
        [
            (
                f"Cave context {'is subsurface air' if cave['is_final_subsurface_air'] else 'solid/non-cave'} · "
                f"nearest {nearest_text}"
            ),
            (
                "Exposure     "
                + (
                    ", ".join(_clean(item) for item in cave["immediate_subsurface_air_directions"])
                    if cave["immediate_subsurface_air_directions"]
                    else "no face adjacent to captured subsurface air"
                )
            ),
            f"Attribution  {_clean(attribution['state'])} · {_clean(attribution['reason'])}",
        ]
    )
    grid = value["grid_context"]["ore_grid"]
    rows.append(
        f"Ore grid     {grid['grid_x']},{grid['grid_z']} · {grid['size_chunks']}×{grid['size_chunks']} chunks · "
        f"{len(grid['consulted_grids'])} consulted grids"
    )
    trace = value["trace"]
    rows.append(
        f"Trace        {'available' if trace['available'] else 'unavailable'} · "
        f"{len(trace['exact_position_decisions'])} exact decisions · "
        f"{len(trace['relevant_position_decisions'])} relevant · "
        f"absence {'closed' if trace['absence_closed_for_selector'] else 'open'}"
    )
    for decision in trace["exact_position_decisions"]:
        rows.append(
            f"  {_clean(decision['outcome']):<16} {_clean(decision['definition_path'])} · "
            f"{_clean(decision['reason'])} · {_clean(decision['final_state_relation'])}"
        )
    query = value["definition_query"]
    rows.append(
        f"Definition candidates  {query['candidate_count']} for {_clean(query['material'])}"
    )
    for definition in query["candidates"]:
        rows.append(
            f"  {_clean(definition['definition_path'])} · weight {_clean(definition['weight'])} · "
            f"Y {definition['height']['minimum']}..{definition['height']['maximum']} · "
            f"dim {_clean(definition['dimension_state'])}"
        )
    return rows


def _run_for_y(column: Mapping[str, Any], y: int) -> Mapping[str, Any]:
    for run in column["runs"]:
        if run["min_y"] <= y <= run["max_y"]:
            return run
    raise ValueError(f"section run coverage is incomplete at Y={y}")


def _section_symbols(value: Mapping[str, Any]) -> tuple[dict[tuple[str, str | None], str], list[str]]:
    identities: set[tuple[str, str | None]] = set()
    for column in value["columns"]:
        for run in column["runs"]:
            if run["category"] == "lithology":
                identities.add(("lithology", run.get("lithology")))
    reserved = {" ", "·", "O", "o", "@", "~", "^", "#", "?"}
    available = [character for character in _CATEGORY_SYMBOLS if character not in reserved]
    mapping: dict[tuple[str, str | None], str] = {}
    legend = [
        "  O focus/ore · o other ore · · subsurface air · space open air · ~ physical fluid · ^ surface indicator · # other"
    ]
    for index, identity in enumerate(sorted(identities, key=lambda row: str(row[1]))):
        symbol = available[index] if index < len(available) else "?"
        mapping[identity] = symbol
        legend.append(f"  {symbol} {_clean(identity[1] or 'unknown lithology')}")
    return mapping, legend


def _section_glyph(run: Mapping[str, Any], lithology_symbols: Mapping[tuple[str, str | None], str]) -> str:
    category = run["category"]
    if category == "open-air":
        return " "
    if category == "subsurface-air":
        return "·"
    if category in {"ore", "ore-focus"}:
        return "O"
    if category == "ore-other":
        return "o"
    if category == "physical-fluid":
        return "~"
    if category == "surface-indicator":
        return "^"
    if category == "lithology":
        return lithology_symbols.get(("lithology", run.get("lithology")), "?")
    return "#"


def _render_section(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    columns = value["columns"]
    terminal_width = max(40, shutil.get_terminal_size(fallback=(100, 30)).columns)
    panel_width = max(16, min(96, terminal_width - 12))
    lithology_symbols, legend = _section_symbols(value)
    rows = [
        f"{_clean(value['orientation'])} {_clean(value['fixed_coordinate'])} · "
        f"{_clean(value['axis'])} {value['axis_minimum']}..{value['axis_maximum']} · "
        f"Y {value['minimum_y']}..{value['maximum_y']} · {value['cell_count']:,} exact cells"
    ]
    if value["material_focus"]:
        rows.append(f"Ore focus: {_clean(value['material_focus'])}")
    for start in range(0, len(columns), panel_width):
        panel = columns[start : start + panel_width]
        rows.append(
            f"{value['axis']} {panel[0]['axis']} … {panel[-1]['axis']}"
        )
        for y in range(value["maximum_y"], value["minimum_y"] - 1, -1):
            glyphs = "".join(
                _section_glyph(_run_for_y(column, y), lithology_symbols)
                for column in panel
            )
            rows.append(f"{y:>3} │{glyphs}│")
        rows.append("    └" + "─" * len(panel) + "┘")
    rows.extend(legend)
    return rows


def _render_definitions(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    rows = [
        f"{value['definition_count']} definitions"
        + (f" · material {_clean(value['material'])}" if value["material"] else "")
        + (f" · query {_clean(value['query'])}" if value["query"] else "")
    ]
    for definition in value["definitions"]:
        generator = definition.get("generator") or {}
        rows.extend(
            [
                f"{_clean(definition['definition_path'])}",
                (
                    f"  materials {', '.join(_clean(item) for item in definition['materials']) or 'none'} · "
                    f"weight {_clean(definition['weight'])} · priority {_clean(definition['priority'])} · "
                    f"density {_clean(definition['density'])}"
                ),
                (
                    f"  Y {definition['height']['minimum']}..{definition['height']['maximum']} · "
                    f"dimension {_clean(definition['dimension_state'])} · "
                    f"generator {_clean(generator.get('type'))} · filler {_clean(definition['filler_type'])}"
                ),
            ]
        )
        indicators = sum(
            row["count"] for row in definition["observed_surface_indicator_counts"]
        )
        if indicators:
            rows.append(f"  final surface indicators {indicators:,}")
        trace_selection = definition["trace_selection"]
        if trace_selection["available"]:
            rows.append(
                f"  controlled selections {trace_selection['deposit_instance_count']} · "
                f"absence {'closed' if trace_selection['absence_closed_for_selector'] else 'open'} for selector"
            )
    return rows


def _render_fluids(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    rows = [
        f"{value['cell_count']} virtual cells · total yield {_number(value['yield_total'])} · no physical blocks"
    ]
    for cell in value["cells"]:
        rows.append(
            f"grid {cell.get('veinX')},{cell.get('veinZ')} · query chunk "
            f"{cell.get('queryChunkX')},{cell.get('queryChunkZ')} · "
            f"{_clean(cell.get('fluid'))} yield {_number(cell.get('fluidYield'))} · "
            f"{_clean(cell.get('depositName'))}"
        )
    rows.append(f"{value['definition_count']} declared bedrock-fluid definitions")
    return rows


def _delta(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:+,}"
    return _clean(value)


def _render_compare(report: Mapping[str, Any]) -> list[str]:
    value = report["result"]
    if not value["aligned"]:
        return ["Inputs are not aligned.", *[f"  {_clean(row)}" for row in value["reasons"]]]
    summary = value["summary"]
    rows = [
        (
            f"Ore blocks      {_number(summary['ore_blocks']['baseline'])} → "
            f"{_number(summary['ore_blocks']['candidate'])} ({_delta(summary['ore_blocks']['delta'])})"
        ),
        (
            f"Subsurface air  {_number(summary['subsurface_air']['baseline'])} → "
            f"{_number(summary['subsurface_air']['candidate'])} ({_delta(summary['subsurface_air']['delta'])})"
        ),
        (
            f"Indicators      {_number(summary['surface_indicators']['baseline'])} → "
            f"{_number(summary['surface_indicators']['candidate'])} ({_delta(summary['surface_indicators']['delta'])})"
        ),
        (
            f"Changed         {summary['changed_chunk_count']} chunks · "
            f"{summary['definition_change_count']} definitions · "
            f"{summary['fluid_change_count']} fluid cells"
        ),
    ]
    if value["material"]:
        rows.append(f"Material filter  {_clean(value['material'])}")
    rows.extend(
        f"  {_clean(row['material'])}: {_number(row['baseline'])} → {_number(row['candidate'])} ({_delta(row['delta'])})"
        for row in value["materials"]
    )
    rows.extend(
        f"  chunk {row['chunk_x']},{row['chunk_z']}: ore {_delta(row['ore_blocks']['delta'])}, "
        f"air {_delta(row['subsurface_air']['delta'])}, exposure {_delta(row['exposed_ore_faces']['delta'])}"
        for row in value["chunks"][:40]
    )
    if len(value["chunks"]) > 40:
        rows.append(f"  … {len(value['chunks']) - 40} more changed chunks; use --json for the exact set")
    return rows


_RENDERERS = {
    "summary": _render_summary,
    "layers": _render_layers,
    "map": _render_map,
    "explain": _render_explain,
    "section": _render_section,
    "definitions": _render_definitions,
    "fluids": _render_fluids,
    "compare": _render_compare,
}


def render_report(report: Mapping[str, Any], *, show_sources: bool = False) -> str:
    """Render one validated Studio result without terminal-control injection."""

    operation = report["operation"]
    renderer = _RENDERERS[operation]
    output = [
        f"GTCEu Subsurface Studio · {_clean(operation)} · {_clean(report['status'])}",
        _scope(report),
        "",
        *renderer(report),
    ]
    if report["uncertainty"]:
        output.extend(
            ["", "Uncertainty", *[f"  • {_clean(row)}" for row in report["uncertainty"]]]
        )
    if report["limitations"]:
        output.extend(
            ["", "Boundaries", *[f"  • {_clean(row)}" for row in report["limitations"]]]
        )
    if show_sources:
        output.extend(["", "Exact sources"])
        for source in report["sources"]:
            digest = source["sha256"][:12] if source["sha256"] else "unavailable"
            output.append(
                f"  {_clean(source['state']):<19} {_clean(source['kind']):<30} {digest}  {_clean(source['path'])}"
            )
        output.extend(["", f"Result ID  {_clean(report['result_id'])}"])
    return "\n".join(output).rstrip() + "\n"


__all__ = ["render_report"]
