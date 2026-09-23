"""High-signal terminal and self-contained review rendering."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from workbench_api.events import sanitize_terminal
except ImportError:
    def sanitize_terminal(value: str) -> str:
        return "".join(character if ord(character) >= 32 else "�" for character in value)

from .model import CockpitError


HEAT = " .:-=+*#%@"


def _text(value: Any) -> str:
    return sanitize_terminal(str(value))


def _metric(label: str, value: Any) -> str:
    if isinstance(value, int):
        shown = f"{value:,}"
    elif isinstance(value, float):
        shown = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        shown = str(value)
    return f"{label}={_text(shown)}"


def _heat_character(cell: Mapping[str, Any]) -> str:
    if cell.get("outlier"):
        return "!"
    intensity = cell.get("intensity", 0.0)
    if not isinstance(intensity, (int, float)) or intensity <= 0:
        return HEAT[0]
    index = max(1, min(len(HEAT) - 1, round(float(intensity) * (len(HEAT) - 1))))
    return HEAT[index]


def _render_grid(visual: Mapping[str, Any]) -> list[str]:
    width = visual.get("width", 0)
    height = visual.get("height", 0)
    cells = visual.get("cells", [])
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return ["  unavailable (captures are not aligned)"]
    if not isinstance(cells, list) or len(cells) != width * height:
        return ["  unavailable (grid coverage drift)"]
    lines: list[str] = []
    panel_width = 64
    for start in range(0, width, panel_width):
        end = min(width, start + panel_width)
        if width > panel_width:
            lines.append(f"  columns {start + 1}-{end} of {width}")
        for row in range(height):
            segment = cells[row * width + start : row * width + end]
            chunk_z = segment[0].get("chunk_z") if segment else "?"
            lines.append(f"  z={chunk_z:>5}  " + "".join(_heat_character(cell) for cell in segment))
        min_x = cells[start].get("chunk_x")
        max_x = cells[end - 1].get("chunk_x")
        lines.append(f"           x={min_x} .. {max_x}   legend: space=unchanged . low → @ high, !=outlier")
    return lines


def render_report(report: Mapping[str, Any], *, show_sources: bool = False) -> str:
    decision = report.get("decision", {})
    evidence = report.get("evidence", {})
    lines = [
        "WORLDGEN COCKPIT",
        f"{str(report.get('status', 'unknown')).upper()} · {report.get('mode')} mode · {report.get('coverage')}",
        _text(decision.get("headline", "No decision summary is available.")),
        "  "
        + "  ".join(
            (
                _metric("comparison", decision.get("comparison_kind")),
                _metric("reproducibility", decision.get("reproducibility_status")),
            )
        ),
        "",
    ]
    alignment = report.get("alignment", {})
    if not alignment.get("aligned"):
        lines.extend(
            [
                "Alignment",
                "  REJECTED  " + ", ".join(_text(item) for item in alignment.get("failed_checks", [])),
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Aligned experiment",
                "  "
                + "  ".join(
                    (
                        _metric("seed", alignment.get("seed")),
                        _metric("dimension", alignment.get("dimension_id")),
                        _metric("region", alignment.get("chunk_region")),
                    )
                ),
                "",
            ]
        )

    final_state = evidence.get("final_state", {})
    final_summary = final_state.get("summary")
    if isinstance(final_summary, Mapping):
        categories = final_summary.get("category_totals", {})
        lines.extend(
            [
                "Exact final-state delta",
                "  "
                + "  ".join(
                    (
                        _metric("blocks", final_summary.get("changed_block_positions")),
                        _metric("chunks", final_summary.get("changed_chunks")),
                        _metric("height-columns", final_summary.get("height_changed_columns")),
                        _metric("biome-columns", final_summary.get("biome_changed_columns")),
                    )
                ),
                "  " + "  ".join(_metric(key, value) for key, value in sorted(categories.items())) if categories else "  categories: none",
                "",
                "Changed-chunk heatmap",
                *_render_grid(report.get("visual", {})),
                "",
            ]
        )

    statistical = evidence.get("statistical", {})
    if statistical.get("state") != "unavailable":
        cave = statistical.get("cave_volume", {})
        ore = statistical.get("ore_mass", {})
        height = statistical.get("height_absolute_delta", {})
        lines.extend(
            [
                "Spatial statistics",
                "  "
                + "  ".join(
                    (
                        _metric("cave-volume Δ", cave.get("delta")),
                        _metric("ore-mass Δ", ore.get("delta")),
                        _metric("height |Δ| mean", height.get("mean")),
                        _metric("height |Δ| p95", height.get("p95")),
                    )
                ),
                "",
            ]
        )

    states = []
    for key in ("semantic", "subsurface", "causal", "performance", "observer_overhead"):
        item = evidence.get(key, {})
        states.append(f"{key}={item.get('state', 'unavailable')}")
    lines.extend(["Evidence", "  " + "  ".join(states)])
    causal = evidence.get("causal", {})
    causal_summary = causal.get("summary")
    if isinstance(causal_summary, Mapping):
        lines.append("  " + _metric("Atlas first divergence", causal_summary.get("status")))
        first = causal_summary.get("first_difference")
        if isinstance(first, Mapping):
            lines.append(
                "  first: "
                + "  ".join(
                    (
                        _metric("stage", first.get("stage_id")),
                        _metric("record", first.get("record_type")),
                    )
                )
            )
    performance = evidence.get("performance", {})
    perf_summary = performance.get("summary")
    if isinstance(perf_summary, Mapping):
        lines.append("  " + _metric("performance regressions", perf_summary.get("regression_count")))
    lines.append("")

    outliers = report.get("visual", {}).get("top_outliers", [])
    if isinstance(outliers, list) and outliers:
        lines.append("Top changed chunks")
        for row in outliers[:8]:
            categories = ", ".join(f"{key}:{value}" for key, value in sorted(row.get("categories", {}).items())) or "surface/biome only"
            lines.append(
                f"  ({row.get('chunk_x')},{row.get('chunk_z')}) "
                f"blocks={row.get('changed_blocks'):,} {categories}"
            )
        lines.append("")

    actions = decision.get("next_actions", [])
    if isinstance(actions, list) and actions:
        lines.append("Next useful action")
        lines.extend("  - " + _text(action) for action in actions)
        lines.append("")

    limitations = report.get("limitations", [])
    if limitations:
        lines.append("Coverage")
        lines.extend("  - " + _text(item) for item in limitations)
        lines.append("")

    review = next(
        (
            row.get("target")
            for row in report.get("navigation", [])
            if isinstance(row, Mapping) and row.get("kind") == "cockpit-review"
        ),
        None,
    )
    if review:
        lines.append("Review: " + _text(review))
    lines.append("Report: " + _text(report.get("report_id")))
    if show_sources:
        lines.append("")
        lines.append("Exact sources")
        for name, side in report.get("sides", {}).items():
            lines.append(f"  {name}: {_text(side.get('iteration_report', {}).get('path'))}")
            lines.append(f"    manifest: {_text(side.get('strata_manifest', {}).get('path'))}")
            lines.append(f"    artifact: {_text(side.get('worldgen_artifact', {}).get('sha256'))}")
    return "\n".join(lines) + "\n"


def _safe_json_for_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def render_html(report: Mapping[str, Any]) -> str:
    payload = _safe_json_for_script(report)
    title = html.escape(f"Worldgen Cockpit · {report.get('status', 'unknown')}")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: dark; --bg:#0b1016; --panel:#121b24; --line:#273747; --text:#e8f0f6; --muted:#91a6b8; --accent:#79d5ff; --warn:#ffbd66; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace }}
main {{ max-width:1500px; margin:auto; padding:28px }} h1,h2,p {{ margin-top:0 }} h1 {{ font-size:24px }} h2 {{ font-size:14px; color:var(--accent); text-transform:uppercase; letter-spacing:.08em }}
.hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:18px }} .hero {{ margin-bottom:14px }}
.layout {{ display:grid; grid-template-columns:minmax(400px,1.3fr) minmax(320px,.7fr); gap:14px }} .stack {{ display:grid; gap:14px }}
.kpis {{ display:flex; gap:24px; flex-wrap:wrap }} .kpi b {{ display:block; font-size:22px }} .kpi span,.muted {{ color:var(--muted) }}
#grid {{ display:grid; gap:3px; width:min(100%,900px) }} .cell {{ aspect-ratio:1; min-width:11px; border:1px solid #ffffff10; background:hsl(calc(205 - var(--v)*165) 75% calc(12% + var(--v)*38%)); cursor:pointer; border-radius:2px }} .cell:hover,.cell.active {{ outline:2px solid white; z-index:2 }} .cell.outlier {{ box-shadow:inset 0 0 0 2px var(--warn) }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; color:#c8d8e4 }} a {{ color:var(--accent) }} ul {{ padding-left:20px }}
.evidence {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:8px }} .badge {{ border:1px solid var(--line); padding:8px; border-radius:6px }}
@media (max-width:900px) {{ .layout {{ grid-template-columns:1fr }} main {{ padding:14px }} }}
</style>
</head>
<body><main>
<section class="hero"><h1>{title}</h1><p id="headline"></p><div class="kpis" id="kpis"></div></section>
<div class="layout"><div class="stack">
<section class="panel"><h2>Aligned changed-chunk map</h2><div id="grid"></div><p class="muted">Click a chunk for exact category counts and sampled changed blocks. Gold outline marks a robust spatial outlier.</p></section>
<section class="panel"><h2>Evidence coverage</h2><div class="evidence" id="evidence"></div></section>
</div><div class="stack">
<section class="panel"><h2>Selected chunk</h2><pre id="detail">Select a changed cell.</pre></section>
<section class="panel"><h2>Next actions</h2><ul id="actions"></ul></section>
<section class="panel"><h2>Exact sources</h2><div id="sources"></div></section>
</div></div>
</main><script>
const report={payload};
const fmt=n=>typeof n==='number'?n.toLocaleString():String(n ?? 'unavailable');
document.getElementById('headline').textContent=report.decision.headline;
const fs=report.evidence.final_state.summary||{{}};
for(const [label,value] of [['changed blocks',fs.changed_block_positions],['changed chunks',fs.changed_chunks],['coverage',report.coverage],['mode',report.mode]]){{const d=document.createElement('div');d.className='kpi';d.innerHTML=`<b>${{fmt(value)}}</b><span>${{label}}</span>`;document.getElementById('kpis').append(d)}}
const v=report.visual,g=document.getElementById('grid');if(v.width>0){{g.style.gridTemplateColumns=`repeat(${{v.width}},minmax(8px,1fr))`;for(const c of v.cells){{const b=document.createElement('button');b.className='cell'+(c.outlier?' outlier':'');b.style.setProperty('--v',c.intensity);b.title=`chunk ${{c.chunk_x}},${{c.chunk_z}} · ${{fmt(c.changed_blocks)}} changed blocks`;b.onclick=()=>{{document.querySelectorAll('.cell.active').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById('detail').textContent=JSON.stringify(c,null,2)}};g.append(b)}}}}else g.textContent='Comparison rejected: captures are not aligned.';
for(const [name,item] of Object.entries(report.evidence)){{const d=document.createElement('div');d.className='badge';d.innerHTML=`<b>${{name}}</b><br><span class="muted">${{item.state}}</span>`;document.getElementById('evidence').append(d)}}
for(const action of report.decision.next_actions){{const li=document.createElement('li');li.textContent=action;document.getElementById('actions').append(li)}}
for(const [name,side] of Object.entries(report.sides)){{const d=document.createElement('p');const a=document.createElement('a');a.textContent=`${{name}} Strata view`;a.href=side.viewer_url;d.append(a,document.createElement('br'),side.strata_manifest.path);document.getElementById('sources').append(d)}}
</script></body></html>"""


def write_html(path: Path, report: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise CockpitError(f"review output cannot be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(render_html(report), encoding="utf-8")
    temporary.replace(path)


__all__ = ["render_html", "render_report", "write_html"]
