"""Fail-closed qualification over repeated Worldgen Cockpit controls."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from workbench_worldgen_cockpit.model import FileBinding

from .model import (
    QUALIFICATION_FORMAT,
    QUALIFICATION_PREFIX,
    content_id,
    require,
    utc_now,
)


UNAVAILABLE_STATES = {"unavailable", "unresolved", "incomparable", None}


def _side_value(report: Mapping[str, Any], side: str, *path: str) -> Any:
    value: Any = report.get("sides", {}).get(side, {})
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _input_relation(report: Mapping[str, Any]) -> str:
    decision = report.get("decision")
    if isinstance(decision, Mapping) and decision.get("comparison_kind") in {
        "identical-input-control", "before-after", "incomparable"
    }:
        return str(decision["comparison_kind"])
    pairs = (
        (
            _side_value(report, "baseline", "plan", "sha256"),
            _side_value(report, "candidate", "plan", "sha256"),
        ),
        (
            _side_value(report, "baseline", "worldgen_artifact", "sha256"),
            _side_value(report, "candidate", "worldgen_artifact", "sha256"),
        ),
    )
    if any(left is None or right is None for left, right in pairs):
        return "incomparable"
    return "identical-input-control" if all(left == right for left, right in pairs) else "before-after"


def _alignment_check(report: Mapping[str, Any], check_id: str) -> bool:
    rows = report.get("alignment", {}).get("checks", [])
    return any(
        isinstance(row, Mapping)
        and row.get("check_id") == check_id
        and row.get("aligned") is True
        for row in rows
    )


def _fingerprints(report: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for side in ("baseline", "candidate"):
        digest = _side_value(report, side, "strata_manifest", "tile_set_sha256")
        if isinstance(digest, str) and digest:
            values.append(digest)
    return values


def _summary(report: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = report.get("evidence", {}).get("final_state", {}).get("summary")
    return value if isinstance(value, Mapping) else None


def _control_row(entry: Mapping[str, Any], index: int) -> dict[str, Any]:
    report = entry.get("report")
    require(isinstance(report, Mapping), f"cockpit entry {index} lacks a report")
    alignment = report.get("alignment")
    aligned = isinstance(alignment, Mapping) and alignment.get("aligned") is True
    relation = _input_relation(report)
    summary = _summary(report)
    categories = summary.get("category_totals", {}) if summary else {}
    if not isinstance(categories, Mapping):
        categories = {}
    semantic = report.get("evidence", {}).get("semantic", {})
    semantic_equivalent = semantic.get("equivalent") if isinstance(semantic, Mapping) else None
    evidence_states = {
        key: (value.get("state") if isinstance(value, Mapping) else None)
        for key, value in report.get("evidence", {}).items()
    } if isinstance(report.get("evidence"), Mapping) else {}
    causal = report.get("evidence", {}).get("causal", {})
    causal_summary = causal.get("summary") if isinstance(causal, Mapping) else None
    path = entry.get("path")
    binding_sha = entry.get("sha256")
    perturbations = deepcopy(entry.get("perturbations", {}))
    run_paths = [str(item) for item in entry.get("run_paths", []) if isinstance(item, (str, Path))]
    seed = alignment.get("seed") if isinstance(alignment, Mapping) else None
    region = alignment.get("chunk_region") if isinstance(alignment, Mapping) else None
    changed_positions = summary.get("changed_block_positions") if summary else None
    exact_delta = isinstance(changed_positions, int) and changed_positions > 0
    semantic_delta = semantic_equivalent is False
    return {
        "comparison_id": report.get("report_id") or f"comparison-{index + 1}",
        "path": str(path) if path is not None else None,
        "sha256": binding_sha,
        "cockpit_status": report.get("status"),
        "coverage": report.get("coverage"),
        "aligned": aligned,
        "input_relation": relation,
        "subject_plan_sha256": _side_value(report, "baseline", "plan", "sha256"),
        "subject_artifact_sha256": _side_value(report, "baseline", "worldgen_artifact", "sha256"),
        "seed": seed,
        "dimension_id": alignment.get("dimension_id") if isinstance(alignment, Mapping) else None,
        "region": deepcopy(region),
        "mode": report.get("mode"),
        "perturbations": perturbations,
        "run_paths": run_paths,
        "evidence_states": evidence_states,
        "causal_status": causal_summary.get("status") if isinstance(causal_summary, Mapping) else None,
        "semantic_equivalent": semantic_equivalent,
        "changed_block_positions": changed_positions,
        "height_changed_columns": summary.get("height_changed_columns") if summary else None,
        "biome_changed_columns": summary.get("biome_changed_columns") if summary else None,
        "category_totals": dict(categories),
        "final_fingerprints": _fingerprints(report),
        "unstable": bool(aligned and relation == "identical-input-control" and (exact_delta or semantic_delta)),
        "fresh_runtime_observed": _alignment_check(report, "separate-runtime"),
        "fresh_capture_observed": _alignment_check(report, "separate-final-capture"),
    }


def _signal_value(row: Mapping[str, Any], signal: str) -> int | None:
    if signal == "semantic":
        equivalent = row.get("semantic_equivalent")
        return None if equivalent is None else (0 if equivalent else 1)
    if signal in {"changed_block_positions", "height_changed_columns", "biome_changed_columns"}:
        value = row.get(signal)
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    categories = row.get("category_totals")
    if not isinstance(categories, Mapping):
        return None
    value = categories.get(signal, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _domain_gates(profile: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], required: set[str], cross_unstable: bool) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for domain_id, definition in profile["domains"].items():
        signals = definition["signals"]
        totals: dict[str, int] = {}
        unknown: list[str] = []
        for signal in signals:
            values = [_signal_value(row, signal) for row in rows]
            if any(value is None for value in values):
                unknown.append(signal)
            else:
                totals[signal] = sum(int(value) for value in values if value is not None)
        state = "unknown" if unknown else ("unstable" if any(value > 0 for value in totals.values()) else "stable")
        if cross_unstable and "changed_block_positions" in signals:
            state = "unstable"
            totals["cross_perturbation_fingerprint_sets"] = 1
        gates.append(
            {
                "gate_id": f"domain.{domain_id}",
                "domain": domain_id,
                "label": definition["label"],
                "required": domain_id in required,
                "state": state,
                "signals": list(signals),
                "observed_deltas": totals,
                "unknown_signals": unknown,
            }
        )
    return gates


def _cross_fingerprint_groups(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        region = row.get("region")
        key = (
            row.get("seed"),
            row.get("dimension_id"),
            tuple(region) if isinstance(region, list) else str(region),
            row.get("mode"),
            row.get("subject_plan_sha256"),
            row.get("subject_artifact_sha256"),
        )
        groups[key].append(row)
    result: list[dict[str, Any]] = []
    unstable = False
    for key, members in sorted(groups.items(), key=lambda item: repr(item[0])):
        fingerprints = sorted({value for row in members for value in row.get("final_fingerprints", []) if isinstance(value, str)})
        missing = sum(1 for row in members if len(row.get("final_fingerprints", [])) != 2)
        state = "unknown" if missing else ("unstable" if len(fingerprints) > 1 else "stable")
        # A single Cockpit member already evaluates its two sides.  This flag
        # is reserved for otherwise hidden differences between matrix cells.
        unstable = unstable or (len(members) > 1 and state == "unstable")
        result.append(
            {
                "sample": {
                    "seed": key[0],
                    "dimension_id": key[1],
                    "region": list(key[2]) if isinstance(key[2], tuple) else key[2],
                    "mode": key[3],
                    "subject_plan_sha256": key[4],
                    "subject_artifact_sha256": key[5],
                },
                "comparison_count": len(members),
                "run_fingerprint_count": sum(len(row.get("final_fingerprints", [])) for row in members),
                "distinct_fingerprints": fingerprints,
                "missing_fingerprint_comparisons": missing,
                "state": state,
            }
        )
    return result, unstable


def _observed_capabilities(
    rows: Sequence[Mapping[str, Any]],
    cross_groups: Sequence[Mapping[str, Any]],
    risk_scan: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    seeds = {row.get("seed") for row in rows if isinstance(row.get("seed"), int)}
    orders = {row.get("perturbations", {}).get("order") for row in rows if isinstance(row.get("perturbations"), Mapping)}
    orders.discard(None)
    heaps = {row.get("perturbations", {}).get("heap") for row in rows if isinstance(row.get("perturbations"), Mapping)}
    heaps.discard(None)
    repeat_counts = Counter(
        (
            row.get("seed"),
            repr(row.get("region")),
            row.get("perturbations", {}).get("order") if isinstance(row.get("perturbations"), Mapping) else None,
            row.get("perturbations", {}).get("heap") if isinstance(row.get("perturbations"), Mapping) else None,
        )
        for row in rows
    )
    causal_closed = bool(rows) and all(row.get("evidence_states", {}).get("causal") == "evidence-closed" for row in rows)
    final_exact = bool(rows) and all(row.get("evidence_states", {}).get("final_state") not in UNAVAILABLE_STATES and len(row.get("final_fingerprints", [])) == 2 for row in rows)
    return {
        "independent-jvm": {"observed": bool(rows) and all(row["fresh_runtime_observed"] for row in rows), "detail": "all comparisons bind distinct runtime paths"},
        "fresh-world": {"observed": bool(rows) and all(row["fresh_capture_observed"] for row in rows), "detail": "all comparisons bind distinct final captures"},
        "final-exact-capture": {"observed": final_exact, "detail": "Strata tile-set fingerprints are present for both sides"},
        "multi-seed": {"observed": len(seeds) >= 2, "detail": f"{len(seeds)} distinct seeds"},
        "execution-order": {"observed": {"baseline-first", "candidate-first"} <= orders, "detail": f"orders={sorted(orders)}"},
        "heap-shape": {"observed": len(heaps) >= 2, "detail": f"heaps={sorted(str(value) for value in heaps)}"},
        "repeated-pairs": {"observed": bool(repeat_counts) and max(repeat_counts.values()) >= 2, "detail": f"maximum identical-cell pair count={max(repeat_counts.values(), default=0)}"},
        "static-risk-scan": {"observed": risk_scan is not None and risk_scan.get("coverage") == "complete", "detail": "exact runtime JAR class coverage" if risk_scan else "no static scan supplied"},
        "stage-checkpoints": {"observed": causal_closed, "detail": "paired Atlas stage evidence is closed" if causal_closed else "causal stage evidence is unavailable or unresolved"},
        "cross-perturbation-final-state": {"observed": bool(cross_groups) and all(row["state"] != "unknown" for row in cross_groups), "detail": f"{len(cross_groups)} seed/scope cohorts"},
        "traversal-order": {"observed": False, "detail": "the current capture runner cannot command chunk traversal order"},
        "scheduled-settle": {"observed": False, "detail": "the current capture runner lacks a profile-bound settle/quiescence checkpoint"},
        "warm-cache-replay": {"observed": False, "detail": "V1 provisions cold fresh runtimes only"},
    }


def _stage_gates(
    rows: Sequence[Mapping[str, Any]],
    domain_gates: Sequence[Mapping[str, Any]],
    required_capabilities: set[str],
) -> list[dict[str, Any]]:
    domains = {row["domain"]: row["state"] for row in domain_gates}
    causal_closed = bool(rows) and all(
        row["evidence_states"].get("causal") == "evidence-closed" for row in rows
    )
    causal_diverged = causal_closed and any(row.get("causal_status") == "diverged" for row in rows)
    causal_equal = causal_closed and all(row.get("causal_status") == "equal" for row in rows)

    def proxy(stage_id: str, label: str, domain_ids: Sequence[str]) -> dict[str, Any]:
        states = [domains.get(domain_id, "unknown") for domain_id in domain_ids]
        if causal_diverged:
            state = "diverged"
        elif causal_equal:
            state = "checkpoint-equal"
        else:
            state = "unknown" if "unknown" in states else ("diverged-final" if "unstable" in states else "final-proxy-only")
        return {
            "gate_id": f"stage.{stage_id}",
            "label": label,
            "required": "stage-checkpoints" in required_capabilities,
            "state": state,
            "checkpoint_observed": causal_closed,
            "final_proxy_domains": list(domain_ids),
            "limitation": "Exact final state constrains the outcome but does not expose the intermediate stage buffer or first write.",
        }

    acquisition_complete = bool(rows) and all(
        row["aligned"]
        and row["evidence_states"].get("final_state") not in UNAVAILABLE_STATES
        for row in rows
    )
    settled = domains.get("settled-final", "unknown")
    return [
        {
            "gate_id": "stage.acquisition-health",
            "label": "Fresh runtime, generation, capture, summarize, and handoff",
            "required": True,
            "state": "complete" if acquisition_complete else "incomplete",
            "checkpoint_observed": acquisition_complete,
            "final_proxy_domains": [],
            "limitation": None,
        },
        proxy("terrain-biomes", "Base terrain and biome allocation", ("terrain", "biomes")),
        proxy("lithology", "Lithology assignment", ("lithology",)),
        proxy("caves-carvers", "Caves and carvers", ("caves",)),
        proxy("ore-decoration-population", "Ore, fluids, structures, decoration, and population", ("ore", "fluids", "decoration", "semantic")),
        {
            "gate_id": "stage.causal-checkpoints",
            "label": "Admitted per-stage causal fingerprints",
            "required": "stage-checkpoints" in required_capabilities,
            "state": "diverged" if causal_diverged else ("complete" if causal_equal else "unavailable"),
            "checkpoint_observed": causal_closed,
            "final_proxy_domains": [],
            "limitation": None if causal_closed else "Paired evidence-closed Atlas stage cohorts were not supplied for every comparison.",
        },
        {
            "gate_id": "stage.scheduled-settle",
            "label": "Scheduled ticks, asynchronous work, and quiescence",
            "required": "scheduled-settle" in required_capabilities,
            "state": "unavailable",
            "checkpoint_observed": False,
            "final_proxy_domains": [],
            "limitation": "The current runner has no profile-bound settle/quiescence checkpoint.",
        },
        {
            "gate_id": "stage.saved-final",
            "label": "Exact captured final block state",
            "required": True,
            "state": "unknown" if settled == "unknown" else ("diverged" if settled == "unstable" else "complete"),
            "checkpoint_observed": settled != "unknown",
            "final_proxy_domains": ["settled-final"],
            "limitation": None,
        },
    ]


def assess_matrix(
    *,
    profile: Mapping[str, Any],
    profile_binding: FileBinding,
    cockpit_entries: Sequence[Mapping[str, Any]],
    suite_id: str,
    intent_id: str,
    risk_scan: Mapping[str, Any] | None,
    reproduction_command: str,
    navigation: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    require(suite_id in profile["suites"], f"unknown qualification suite: {suite_id}")
    require(intent_id in profile["intents"], f"unknown qualification intent: {intent_id}")
    require(bool(cockpit_entries), "qualification requires at least one completed A/A cockpit comparison")
    suite = profile["suites"][suite_id]
    intent = profile["intents"][intent_id]
    rows = [_control_row(entry, index) for index, entry in enumerate(cockpit_entries)]
    cross_groups, cross_unstable = _cross_fingerprint_groups(rows)
    required_domains = set(intent["required_domains"])
    domain_gates = _domain_gates(profile, rows, required_domains, cross_unstable)

    limitations: list[str] = []
    blockers: list[str] = []
    unstable_reasons: list[str] = []
    if any(not row["aligned"] for row in rows):
        blockers.append("one or more cockpit comparisons failed alignment")
    if any(row["input_relation"] != "identical-input-control" for row in rows):
        blockers.append("one or more comparisons is not a byte-identical plan/artifact A/A control")
    subject_identities = {
        (row.get("subject_plan_sha256"), row.get("subject_artifact_sha256"))
        for row in rows
    }
    if len(subject_identities) != 1 or any(None in identity for identity in subject_identities):
        blockers.append("qualification controls do not bind one common exact subject plan and artifact")
    if any(row["unstable"] for row in rows):
        unstable_reasons.append("an identical-input pair changed semantic or exact final state")
    if cross_unstable:
        unstable_reasons.append("exact final-state fingerprints differ across perturbations for the same seed and scope")
    required_unstable = [gate["gate_id"] for gate in domain_gates if gate["required"] and gate["state"] == "unstable"]
    required_unknown = [gate["gate_id"] for gate in domain_gates if gate["required"] and gate["state"] == "unknown"]
    if required_unstable:
        unstable_reasons.append("critical domain gates diverged: " + ", ".join(required_unstable))
    if required_unknown:
        blockers.append("critical domain evidence is unknown: " + ", ".join(required_unknown))

    evidence_gates: list[dict[str, Any]] = []
    for evidence_id in intent["required_evidence"]:
        missing = [row["comparison_id"] for row in rows if row["evidence_states"].get(evidence_id) in UNAVAILABLE_STATES]
        state = "complete" if not missing else "incomplete"
        evidence_gates.append({"gate_id": f"evidence.{evidence_id}", "required": True, "state": state, "missing_comparisons": missing})
        if missing:
            blockers.append(f"required {evidence_id} evidence is incomplete")

    seeds = {row["seed"] for row in rows if isinstance(row["seed"], int)}
    unique_run_paths = {
        path
        for row in rows
        for path in row.get("run_paths", [])
        if isinstance(path, str) and path
    }
    independent_run_count = len(unique_run_paths) if unique_run_paths else len(rows) * 2
    if len(seeds) < intent["minimum_seeds"]:
        blockers.append(f"intent requires {intent['minimum_seeds']} seeds; observed {len(seeds)}")

    observed = _observed_capabilities(rows, cross_groups, risk_scan)
    capability_gates: list[dict[str, Any]] = []
    required_capabilities = list(dict.fromkeys([*suite["required_capabilities"], *intent["required_capabilities"]]))
    stage_gates = _stage_gates(rows, domain_gates, set(required_capabilities))
    for gate in stage_gates:
        if not gate["required"]:
            continue
        if gate["state"] in {"diverged", "diverged-final"}:
            unstable_reasons.append(f"required stage gate diverged: {gate['gate_id']}")
        elif gate["state"] not in {"complete", "checkpoint-equal"}:
            blockers.append(f"required stage gate is incomplete: {gate['gate_id']} ({gate['state']})")
    for capability_id in required_capabilities:
        declared = profile["capabilities"].get(capability_id, "unsupported")
        evidence = observed.get(capability_id, {"observed": False, "detail": "no V1 observation adapter"})
        state = "complete" if declared == "supported" and evidence["observed"] else "incomplete"
        capability_gates.append(
            {
                "gate_id": f"capability.{capability_id}",
                "required": True,
                "declared_support": declared,
                "observed": evidence["observed"],
                "state": state,
                "detail": evidence["detail"],
            }
        )
        if state != "complete":
            blockers.append(f"required perturbation/evidence capability {capability_id} is not complete ({declared})")

    risk_gate = intent["risk_gate"]
    risk_state = "not-required"
    risk_blockers: list[str] = []
    if risk_scan is not None:
        summary = risk_scan.get("summary", {})
        disposition_counts = summary.get("dispositions", {}) if isinstance(summary, Mapping) else {}
        unreviewed_high = sum(
            1 for row in risk_scan.get("findings", [])
            if isinstance(row, Mapping) and row.get("severity") == "high" and row.get("disposition") == "unreviewed"
        )
        rejected = int(disposition_counts.get("rejected", 0)) if isinstance(disposition_counts, Mapping) else 0
        if risk_scan.get("coverage") != "complete":
            risk_blockers.append("static risk scan has partial byte coverage")
        if rejected:
            risk_blockers.append(f"{rejected} exact static findings have a rejected profile disposition")
        if risk_gate in {"review", "reject"} and unreviewed_high:
            risk_blockers.append(f"{unreviewed_high} high-severity exact static findings are unreviewed")
        risk_state = "clear" if not risk_blockers else "review-required"
    elif risk_gate in {"review", "reject"} or "static-risk-scan" in required_capabilities:
        risk_blockers.append("required exact static risk scan is absent")
        risk_state = "missing"
    blockers.extend(risk_blockers)
    risk_gate_record = {
        "gate_id": "risk.static-exact-artifacts",
        "required": risk_gate != "report" or "static-risk-scan" in required_capabilities,
        "policy": risk_gate,
        "state": risk_state,
        "blockers": risk_blockers,
    }

    limitations.extend(
        [
            "Acceptance is bounded to the exact profile, artifacts, seeds, regions, modes, and perturbations retained by this report.",
            "A stable final capture does not identify the causal generator; Atlas remains the authority for admitted first-divergence evidence.",
            "Unknown evidence and unsupported perturbations are not treated as noise or equivalence.",
        ]
    )
    if risk_scan is not None:
        limitations.extend(str(item) for item in risk_scan.get("limitations", []))
    for gate in capability_gates:
        if gate["state"] != "complete":
            limitations.append(f"Coverage gap: {gate['gate_id']} — {gate['detail']}")

    if unstable_reasons:
        status = "rejected-unstable-critical"
        assurance = "none"
        headline = "Qualification rejected: byte-identical world-generation inputs diverged in a critical observed domain."
        next_actions = [
            "Open the first unstable Cockpit comparison and localize the earliest changed chunk/domain before evaluating any A/B effect.",
            "Use exact static findings as investigation leads, then confirm the executed route with Atlas or a bounded runtime probe.",
        ]
    elif blockers:
        status = "inconclusive"
        assurance = "none"
        headline = "Qualification is inconclusive because required evidence or perturbation coverage is incomplete."
        next_actions = [
            "Close the listed capability and evidence gaps; do not convert them into an allowed noise budget.",
            "Rerun only the missing matrix cells or supply exact reviewed dispositions for bound static findings.",
        ]
    elif intent["assurance"] == "stage-exact":
        status = "accepted-exact"
        assurance = "exact-in-declared-matrix"
        headline = "Qualification accepted with exact equality across every required stage/domain in the declared matrix."
        next_actions = ["Retain this report with the exact artifact/profile identities used by the downstream acceptance decision."]
    else:
        status = "accepted-empirical"
        assurance = "empirical-in-declared-matrix"
        headline = "Qualification accepted empirically: all required A/A controls were exact in the declared runtime matrix."
        next_actions = ["Escalate to a broader suite or stage-exact intent when the cost and release risk justify it."]

    blockers = sorted(set(blockers))
    unstable_reasons = sorted(set(unstable_reasons))
    report: dict[str, Any] = {
        "format": QUALIFICATION_FORMAT,
        "schema_version": 1,
        "report_id": "",
        "created_at": utc_now(),
        "status": status,
        "assurance": assurance,
        "profile": {
            "profile_id": profile["profile_id"],
            "pack_profile": profile["pack_profile"],
            "path": str(profile_binding.path),
            "sha256": profile_binding.sha256,
        },
        "qualification": {
            "suite": suite_id,
            "intent": intent_id,
            "comparison_model": "identical-plan-and-artifact fresh-JVM controls plus same-seed cross-perturbation final fingerprints",
            "acceptance_scope": "declared-matrix-only",
        },
        "matrix": {
            "subject": {
                "plan_sha256": next(iter(subject_identities))[0] if len(subject_identities) == 1 else None,
                "artifact_sha256": next(iter(subject_identities))[1] if len(subject_identities) == 1 else None,
            },
            "comparison_count": len(rows),
            "independent_run_count": independent_run_count,
            "seed_count": len(seeds),
            "seeds": sorted(seeds),
            "controls": rows,
            "cross_perturbation_groups": cross_groups,
            "observed_capabilities": observed,
        },
        "gates": {
            "stages": stage_gates,
            "domains": domain_gates,
            "evidence": evidence_gates,
            "capabilities": capability_gates,
            "risk": risk_gate_record,
        },
        "risk_scan": None if risk_scan is None else {
            "report_id": risk_scan.get("report_id"),
            "coverage": risk_scan.get("coverage"),
            "summary": deepcopy(risk_scan.get("summary")),
            "findings": deepcopy(risk_scan.get("findings", [])),
            "jars": deepcopy(risk_scan.get("jars", [])),
        },
        "decision": {
            "headline": headline,
            "blockers": blockers,
            "unstable_reasons": unstable_reasons,
            "required_domain_states": {
                gate["domain"]: gate["state"] for gate in domain_gates if gate["required"]
            },
            "next_actions": next_actions,
        },
        "sources": [
            {
                "kind": "worldgen-cockpit-report",
                "authority": "Workbench composition over Crucible / Strata / Atlas",
                "path": row["path"],
                "sha256": row["sha256"],
                "report_id": row["comparison_id"],
            }
            for row in rows
        ],
        "navigation": [deepcopy(dict(row)) for row in navigation],
        "limitations": sorted(set(limitations)),
        "reproduction_command": reproduction_command,
    }
    report["report_id"] = content_id(QUALIFICATION_PREFIX, report, "report_id")
    return report


__all__ = ["assess_matrix"]
