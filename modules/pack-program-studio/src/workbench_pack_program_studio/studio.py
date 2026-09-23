"""Pack Program Studio report composition."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .analyzer import AnalysisContext, analyze_program, assess_change, compare_programs
from .model import (
    REPORT_FORMAT,
    REPORT_SCHEMA_VERSION,
    report_identity,
    validate_report,
)
from .profile import LoadedProfile
from .runtime import runtime_evidence


def build_report(
    *,
    source: Path,
    profile: LoadedProfile,
    context: AnalysisContext,
    baseline: Path | None = None,
    changed_paths: Sequence[str] | None = None,
    groovy_log: Path | None = None,
    runtime_diagnosis: Path | None = None,
    candidate_git_binding: Mapping[str, Any] | None = None,
    baseline_git_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = analyze_program(
        source,
        profile,
        context=context,
        git_binding_override=candidate_git_binding,
    )
    baseline_program = (
        None
        if baseline is None
        else analyze_program(
            baseline,
            profile,
            context=context,
            git_binding_override=baseline_git_binding,
        )
    )
    comparison = (
        {
            "state": "not-requested",
            "baseline_program_id": None,
            "candidate_program_id": candidate["program_id"],
            "files": {"added": [], "modified": [], "removed": [], "changed": 0},
            "effects": {
                "added": 0,
                "removed": 0,
                "added_rows": [],
                "removed_rows": [],
                "truncated": False,
            },
            "summary_delta": None,
            "limitations": ["No baseline source was requested."],
        }
        if baseline_program is None
        else compare_programs(baseline_program, candidate)
    )
    change = assess_change(
        candidate,
        profile,
        baseline=baseline_program,
        comparison=comparison,
        changed_paths=changed_paths,
    )
    observed = runtime_evidence(
        groovy_log=groovy_log,
        runtime_diagnosis=runtime_diagnosis,
    )
    collisions = candidate["collisions"]
    incomplete_recipes = sum(
        1
        for effect in candidate["effects"]
        if effect["kind"] == "machine-recipe"
        and effect["recipe"] is not None
        and not effect["recipe"]["complete"]
    )
    attention_reasons: list[str] = []
    if collisions:
        attention_reasons.append(
            f"{len(collisions)} profile-classified duplicate-identity candidate(s) require review."
        )
    if incomplete_recipes:
        attention_reasons.append(
            f"{incomplete_recipes} recipe-builder candidate(s) have no bounded buildAndRegister terminator."
        )
    if observed["state"] == "attention":
        attention_reasons.append("Supplied runtime evidence contains failure or exception observations.")
    status = "attention" if attention_reasons else "ready"
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": "",
        "operation_class": "read-only",
        "authority": {
            "orchestrator": "Workbench Pack Program Studio",
            "static_surface": "Project Intelligence-style lexical observation",
            "profile_classification": profile.pack_profile_id,
            "platform_lifecycle": profile.platform_profile_id,
            "runtime_observation": "supplied evidence only; Atlas interpretation not implied",
            "construction": "none; Blueprints remains the construction authority",
        },
        "profile": {
            "profile_id": profile.profile_id,
            "pack_profile_id": profile.pack_profile_id,
            "platform_profile_id": profile.platform_profile_id,
            "profile_path": str(profile.path),
            "profile_sha256": profile.sha256,
            "platform_profile_path": str(profile.platform_path),
            "platform_profile_sha256": profile.platform_sha256,
        },
        "request": {
            "source": str(source.expanduser().resolve()),
            "baseline": None if baseline is None else str(baseline.expanduser().resolve()),
            "side": context.side,
            "packmode": context.packmode,
            "debug": context.debug,
            "installed_mods": (
                None if context.installed_mods is None else sorted(context.installed_mods)
            ),
            "changed_paths": None if changed_paths is None else list(changed_paths),
            "groovy_log": None if groovy_log is None else str(groovy_log.expanduser().resolve()),
            "runtime_diagnosis": (
                None
                if runtime_diagnosis is None
                else str(runtime_diagnosis.expanduser().resolve())
            ),
        },
        "candidate": candidate,
        "baseline": baseline_program,
        "comparison": comparison,
        "change_assessment": change,
        "runtime_evidence": observed,
        "summary": {
            "status": status,
            "analysis_state": "static-candidate",
            "candidate_program_id": candidate["program_id"],
            "files": candidate["summary"]["files"],
            "source_lines": candidate["summary"]["source_lines"],
            "effects": candidate["summary"]["effects"],
            "collision_candidates": len(collisions),
            "incomplete_recipe_candidates": incomplete_recipes,
            "comparison_state": comparison["state"],
            "change_state": change["state"],
            "runtime_state": observed["state"],
            "attention_reasons": attention_reasons,
        },
        "limitations": [
            "This static V1 report does not invoke the compiler or execute the candidate and therefore cannot claim Groovy validity or effective registry state.",
            "Use the separate workbench groovy check operation for exact canonicalization diagnostics; the instrumented effect ledger, authenticated runtime source manifest, and reload transaction remain future integrations.",
            "Static identity collisions remain candidates until lifecycle-aware execution or the owning registry confirms them.",
        ],
        "horizons": [
            {
                "capability": "exact-compiler-service",
                "state": "available-separate-operation",
                "next_contract": "Use workbench groovy check for source-bound canonicalization diagnostics, then add a managed client launch receipt that authenticates the endpoint to its runtime.",
            },
            {
                "capability": "observed-effect-ledger",
                "state": "unavailable",
                "next_contract": "Capture before/after registry and recipe mutations with source spans in a disposable runtime.",
            },
            {
                "capability": "reload-qualifier",
                "state": "planned",
                "next_contract": "Compare cold start, first reload, and second reload under one exact runtime binding.",
            },
            {
                "capability": "guided-authoring",
                "state": "planned",
                "next_contract": "Route profile-owned material, recipe, event, and integration plans through Blueprints.",
            },
            {
                "capability": "ide-language-service",
                "state": "broker-foundation-available",
                "next_contract": "Expose completion, hover, signatures, and diagnostics from the same exact broker through IDE and terminal transports.",
            },
        ],
    }
    report["report_id"] = report_identity(report)
    return validate_report(report)


__all__ = ["build_report"]
