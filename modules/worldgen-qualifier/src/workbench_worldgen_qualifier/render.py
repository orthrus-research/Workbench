"""High-signal terminal and local HTML projections for qualification evidence."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping

try:
    from workbench_api.events import sanitize_terminal
except ImportError:
    def sanitize_terminal(value: str) -> str:
        return "".join(character if ord(character) >= 32 else "�" for character in value)


def _text(value: Any) -> str:
    return sanitize_terminal(str(value))


def render_report(report: Mapping[str, Any], *, show_sources: bool = False) -> str:
    decision = report.get("decision", {})
    matrix = report.get("matrix", {})
    lines = [
        "WORLDGEN QUALIFIER",
        f"{str(report.get('status', 'unknown')).upper()} · {report.get('assurance')} · {report.get('qualification', {}).get('suite')} / {report.get('qualification', {}).get('intent')}",
        _text(decision.get("headline", "No qualification decision is available.")),
        f"  runs={matrix.get('independent_run_count')}  comparisons={matrix.get('comparison_count')}  seeds={matrix.get('seed_count')}",
        "",
        "Critical domains",
    ]
    for gate in report.get("gates", {}).get("domains", []):
        if not gate.get("required"):
            continue
        totals = ", ".join(f"{key}={value:,}" for key, value in gate.get("observed_deltas", {}).items()) or "no delta"
        lines.append(f"  {str(gate.get('state', 'unknown')).upper():<9} {gate.get('label')} · {totals}")
    lines.extend(["", "Stage coverage"])
    for gate in report.get("gates", {}).get("stages", []):
        mark = "CHECKPOINT" if gate.get("checkpoint_observed") else "FINAL-ONLY"
        lines.append(f"  {str(gate.get('state', 'unknown')).upper():<16} {gate.get('label')} · {mark}")
    lines.extend(["", "Perturbation and evidence gates"])
    for group in ("evidence", "capabilities"):
        for gate in report.get("gates", {}).get(group, []):
            mark = "OK" if gate.get("state") == "complete" else "GAP"
            detail = gate.get("detail") or ""
            lines.append(f"  {mark:<3} {gate.get('gate_id')}" + (f" · {_text(detail)}" if detail else ""))
    risk = report.get("risk_scan")
    if isinstance(risk, Mapping):
        summary = risk.get("summary", {})
        severity = summary.get("severity", {}) if isinstance(summary, Mapping) else {}
        lines.extend(
            [
                "",
                "Exact-artifact static risks",
                f"  coverage={risk.get('coverage')}  jars={summary.get('jar_count')}  classes={summary.get('class_count')}  high={severity.get('high', 0)}  medium={severity.get('medium', 0)}",
            ]
        )
        for finding in risk.get("findings", [])[:10]:
            lines.append(f"  {str(finding.get('severity')).upper():<6} {finding.get('rule_id')} · {finding.get('jar_name')} · {finding.get('class_name')} · {finding.get('disposition')}")
        if len(risk.get("findings", [])) > 10:
            lines.append(f"  … {len(risk['findings']) - 10} more retained findings")
    blockers = decision.get("unstable_reasons", []) or decision.get("blockers", [])
    if blockers:
        lines.extend(["", "Acceptance blockers"])
        lines.extend("  - " + _text(item) for item in blockers)
    actions = decision.get("next_actions", [])
    if actions:
        lines.extend(["", "Next useful action"])
        lines.extend("  - " + _text(item) for item in actions)
    review = next((row.get("target") for row in report.get("navigation", []) if isinstance(row, Mapping) and row.get("kind") == "qualification-review"), None)
    if review:
        lines.extend(["", "Review: " + _text(review)])
    lines.append("Report: " + _text(report.get("report_id")))
    if show_sources:
        lines.extend(["", "Exact sources"])
        for source in report.get("sources", []):
            lines.append(f"  {source.get('report_id')} · {_text(source.get('path'))} · {source.get('sha256')}")
        if isinstance(risk, Mapping):
            for jar in risk.get("jars", []):
                lines.append(f"  JAR {jar.get('sha256')} · {_text(jar.get('path'))}")
    return "\n".join(lines) + "\n"


def render_risk_report(report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {})
    severity = summary.get("severity", {}) if isinstance(summary, Mapping) else {}
    lines = [
        "WORLDGEN STATIC RISK SCAN",
        f"{str(report.get('coverage', 'unknown')).upper()} · jars={summary.get('jar_count')} classes={summary.get('class_count')} findings={summary.get('finding_count')}",
        f"  high={severity.get('high', 0)} medium={severity.get('medium', 0)} low={severity.get('low', 0)}",
        "",
    ]
    for finding in report.get("findings", []):
        lines.extend(
            [
                f"{str(finding.get('severity')).upper()} · {finding.get('rule_id')} · {finding.get('disposition')}",
                f"  {finding.get('jar_name')} :: {finding.get('class_name')}",
                f"  {_text(finding.get('why'))}",
            ]
        )
    if not report.get("findings"):
        lines.append("No configured static risk candidate matched.")
    lines.extend(["", "Report: " + _text(report.get("report_id"))])
    return "\n".join(lines) + "\n"


def render_html(report: Mapping[str, Any]) -> str:
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(f"Worldgen Qualifier · {report.get('status', 'unknown')}")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:dark;--bg:#091117;--panel:#111e27;--line:#29404d;--text:#e8f3f7;--muted:#8eabb8;--ok:#70d69b;--bad:#ff7f79;--gap:#ffc66d;--accent:#76d6ef}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}}main{{max-width:1500px;margin:auto;padding:26px}}h1{{font-size:24px}}h2{{font-size:13px;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}}.hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px}}.hero{{margin-bottom:14px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}.gates{{display:grid;gap:7px}}.gate,.finding{{border:1px solid var(--line);border-radius:7px;padding:10px}}.stable,.complete{{border-left:4px solid var(--ok)}}.unstable{{border-left:4px solid var(--bad)}}.unknown,.incomplete{{border-left:4px solid var(--gap)}}.muted{{color:var(--muted)}}code{{color:#d7eef5}}a{{color:var(--accent)}}ul{{padding-left:20px}}@media(max-width:700px){{main{{padding:12px}}}}
</style></head><body><main><section class="hero"><h1>{title}</h1><p id="headline"></p><p class="muted" id="scope"></p></section>
<div class="grid"><section class="panel"><h2>Critical domain gates</h2><div class="gates" id="domains"></div></section>
<section class="panel"><h2>Coverage gates</h2><div class="gates" id="coverage"></div></section>
<section class="panel"><h2>Static risk candidates</h2><div class="gates" id="risks"></div></section>
<section class="panel"><h2>Decision</h2><div id="decision"></div></section>
<section class="panel"><h2>Matrix</h2><div id="matrix"></div></section>
<section class="panel"><h2>Exact navigation</h2><div id="nav"></div></section></div>
<script id="data" type="application/json">{payload}</script><script>
const d=JSON.parse(document.getElementById('data').textContent), esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
document.getElementById('headline').textContent=d.decision.headline;document.getElementById('scope').textContent=`${{d.qualification.suite}} / ${{d.qualification.intent}} · ${{d.assurance}} · ${{d.matrix.independent_run_count}} independent runs`;
const gate=(g)=>`<div class="gate ${{esc(g.state)}}"><b>${{esc(g.state).toUpperCase()}} · ${{esc(g.label||g.gate_id)}}</b><div class="muted">${{esc(JSON.stringify(g.observed_deltas||g.detail||g.missing_comparisons||''))}}</div></div>`;
document.getElementById('domains').innerHTML=d.gates.domains.filter(g=>g.required).map(gate).join('');document.getElementById('coverage').innerHTML=[...(d.gates.stages||[]),...d.gates.evidence,...d.gates.capabilities].map(gate).join('');
const findings=d.risk_scan?.findings||[];document.getElementById('risks').innerHTML=findings.slice(0,40).map(f=>`<div class="finding"><b>${{esc(f.severity.toUpperCase())}} · ${{esc(f.rule_id)}}</b><div>${{esc(f.jar_name)}} :: ${{esc(f.class_name)}}</div><div class="muted">${{esc(f.disposition)}} · ${{esc(f.why)}}</div></div>`).join('')||'<p>No configured finding matched.</p>';
const list=a=>'<ul>'+a.map(x=>`<li>${{esc(x)}}</li>`).join('')+'</ul>';document.getElementById('decision').innerHTML='<b>Blockers</b>'+list(d.decision.unstable_reasons.length?d.decision.unstable_reasons:d.decision.blockers)+'<b>Next actions</b>'+list(d.decision.next_actions);
document.getElementById('matrix').innerHTML=`<p>${{d.matrix.comparison_count}} comparisons · ${{d.matrix.seed_count}} seeds</p>`+d.matrix.cross_perturbation_groups.map(g=>gate({{state:g.state,label:`seed ${{g.sample.seed}} · ${{g.sample.region}}`,detail:`${{g.distinct_fingerprints.length}} final fingerprints`}})).join('');
document.getElementById('nav').innerHTML=d.navigation.map(n=>`<p><a href="${{esc(n.target)}}">${{esc(n.label)}}</a></p>`).join('');
</script></main></body></html>"""


def write_html(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ValueError(f"qualification review already exists: {path}")
    path.write_text(render_html(report), encoding="utf-8")


__all__ = ["render_html", "render_report", "render_risk_report", "write_html"]
