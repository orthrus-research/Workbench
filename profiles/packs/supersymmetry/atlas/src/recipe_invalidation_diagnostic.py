"""Compose profile-owned recipe invalidation observations without double counting.

The two admitted channels have different evidence and identities.  This module
keeps them separate, derives only a presentation state, and never treats their
counts as one recipe-registry total.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping


DIAGNOSTIC_FORMAT = "workbench-supersymmetry-recipe-invalidation-diagnostic-v2"
COMPARISON_FORMAT = "workbench-supersymmetry-recipe-invalidation-comparison-v2"
CHANNEL_IDS = ("groovy_postinit", "gt_startup_registration")


class RecipeInvalidationDiagnosticError(ValueError):
    """Profile channel reports cannot support the combined feature result."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_id(value: Mapping[str, Any], field: str) -> str:
    identity = dict(value)
    identity.pop(field, None)
    return "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()


def _profile_channels(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    channels = profile.get("channels")
    if (
        not isinstance(channels, Mapping)
        or set(channels) != set(CHANNEL_IDS)
        or any(not isinstance(channels.get(key), Mapping) for key in CHANNEL_IDS)
    ):
        raise RecipeInvalidationDiagnosticError(
            "recipe invalidation profile lacks its exact channel bindings"
        )
    return channels


def _validated_channel_report(
    channel_id: str,
    report: Any,
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise RecipeInvalidationDiagnosticError(
            f"{channel_id} diagnostic is not an object"
        )
    value = dict(report)
    channel_profile = _profile_channels(profile)[channel_id]
    required = {
        "format",
        "schema_version",
        "diagnostic_id",
        "operation_class",
        "authority",
        "state",
        "profile",
        "source",
        "scope",
        "summary",
        "groups",
        "frontiers",
        "recommendation",
        "limitations",
    }
    expected_states = {
        "groovy_postinit": {
            "attention",
            "inconclusive",
            "no-groovy-conflicts-observed",
        },
        "gt_startup_registration": {
            "attention",
            "inconclusive",
            "no-signals-observed",
        },
    }
    if (
        set(value) != required
        or value.get("format") != channel_profile.get("diagnostic_format")
        or value.get("schema_version") != 1
        or value.get("operation_class") != "read-only"
        or value.get("profile") != channel_profile
        or value.get("diagnostic_id") != _content_id(value, "diagnostic_id")
        or value.get("state") not in expected_states[channel_id]
        or not isinstance(value.get("scope"), Mapping)
        or not isinstance(value.get("summary"), Mapping)
        or not isinstance(value.get("groups"), list)
        or not isinstance(value.get("frontiers"), list)
        or not isinstance(value.get("limitations"), list)
    ):
        raise RecipeInvalidationDiagnosticError(
            f"{channel_id} diagnostic has an incompatible identity or shape"
        )
    return value


def _channel_incomplete(
    channel_id: str,
    report: Mapping[str, Any],
) -> bool:
    summary = report["summary"]
    scope = report["scope"]
    if (
        report["state"] == "inconclusive"
        or summary.get("incomplete_sequence_count") != 0
        or summary.get("groups_truncated") is not False
        or summary.get("frontiers_truncated") is not False
        or report["frontiers"]
    ):
        return True
    if channel_id == "groovy_postinit":
        execution_count = scope.get("post_init_execution_count")
        completed_count = scope.get("completed_post_init_execution_count")
        reload_count = scope.get("reload_execution_count")
        return bool(
            scope.get("initial_execution_completed") is not True
            or type(execution_count) is not int
            or type(completed_count) is not int
            or type(reload_count) is not int
            or execution_count != completed_count
            or reload_count != 0
            or summary.get("unbound_conflict_count") != 0
        )
    startup_boundary = scope.get("startup_boundary")
    return bool(
        not isinstance(startup_boundary, Mapping)
        or startup_boundary.get("state") != "complete"
    )


def _diagnostic_state(channels: Mapping[str, Mapping[str, Any]]) -> str:
    groovy_count = channels["groovy_postinit"]["summary"].get(
        "complete_conflict_count"
    )
    java_count = channels["gt_startup_registration"]["summary"].get(
        "complete_signal_count"
    )
    if (
        type(groovy_count) is not int
        or groovy_count < 0
        or type(java_count) is not int
        or java_count < 0
    ):
        raise RecipeInvalidationDiagnosticError(
            "recipe invalidation channel signal totals are malformed"
        )
    signal_observed = groovy_count > 0 or java_count > 0
    incomplete = any(
        _channel_incomplete(channel_id, channels[channel_id])
        for channel_id in CHANNEL_IDS
    )
    if signal_observed and incomplete:
        return "attention-incomplete"
    if signal_observed:
        return "attention"
    if incomplete:
        return "inconclusive"
    return "no-supported-signals-observed"


def build_recipe_invalidation_diagnostic(
    *,
    source: Mapping[str, Any],
    profile: Mapping[str, Any],
    channels: Mapping[str, Any],
    inherited_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Compose separately counted Groovy and GT startup observations."""

    if not isinstance(source, Mapping) or not isinstance(profile, Mapping):
        raise RecipeInvalidationDiagnosticError(
            "source and profile bindings are required"
        )
    if not isinstance(channels, Mapping) or set(channels) != set(CHANNEL_IDS):
        raise RecipeInvalidationDiagnosticError(
            "both recipe invalidation channels are required"
        )
    validated = {
        channel_id: _validated_channel_report(
            channel_id,
            channels[channel_id],
            profile=profile,
        )
        for channel_id in CHANNEL_IDS
    }
    state = _diagnostic_state(validated)
    groovy_recommendation = validated["groovy_postinit"]["recommendation"]
    if groovy_recommendation.get("state") == "restart-required":
        recommendation = {
            "state": "restart-required",
            "summary": (
                "A repeated Groovy postInit execution was observed; restart before "
                "using this launch in a cold-start count comparison."
            ),
            "actions": list(groovy_recommendation.get("actions", [])),
        }
    elif state == "attention-incomplete":
        recommendation = {
            "state": "inspect-signals-and-recapture-complete-evidence",
            "summary": (
                "Supported signals were observed, but at least one channel is "
                "incomplete and cannot support a count comparison."
            ),
            "actions": [
                "Review the observed channel-specific groups as bounded leads.",
                "Capture a complete fresh-process V3 launch before comparing counts.",
            ],
        }
    elif state == "attention":
        recommendation = {
            "state": "compare-explicit-cold-start-baseline",
            "summary": (
                "Review the channel-specific groups, then compare this completed "
                "launch with an explicitly selected cold-start baseline."
            ),
            "actions": [
                "Review newly observed or increased groups before individual examples.",
                "Reproduce in a fresh process after any Groovy postInit reload.",
            ],
        }
    elif state == "inconclusive":
        recommendation = {
            "state": "recapture-complete-runtime-evidence",
            "summary": (
                "At least one supported recipe-registration channel is incomplete."
            ),
            "actions": [
                "Capture a complete V3 launch through the loader checkpoint and process exit.",
            ],
        }
    else:
        recommendation = {
            "state": "no-supported-signals-observed",
            "summary": (
                "Neither supported channel recognized a signal in this launch; "
                "this is not a recipe-safety or effective-registry claim."
            ),
            "actions": [],
        }
    limitations = list(inherited_limitations or [])
    for report in validated.values():
        limitations.extend(
            item
            for item in report["limitations"]
            if isinstance(item, str) and item
        )
    limitations.extend((
        "Groovy postInit and minecraft-latest.log counts are separate channels and are never summed; cross-log forwarding and deduplication are unproven.",
        "Observed log attribution is not proof of causal source ownership.",
        "The effective recipe registry, recipe execution, progression impact, and client visibility are not assessed.",
        "Absence of a supported signal does not prove that a recipe change is safe.",
    ))
    report: dict[str, Any] = {
        "format": DIAGNOSTIC_FORMAT,
        "schema_version": 2,
        "diagnostic_id": "",
        "operation_class": "read-only",
        "authority": {
            "classification": "profile-runtime-observation-composition",
            "owner": "Supersymmetry Atlas profile",
            "normative": False,
            "atlas_publication": False,
        },
        "state": state,
        "profile": dict(profile),
        "source": dict(source),
        "channels": validated,
        "recommendation": recommendation,
        "limitations": sorted(set(limitations)),
    }
    report["diagnostic_id"] = _content_id(report, "diagnostic_id")
    return report


def _validated_diagnostic(
    value: Any,
    *,
    profile: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecipeInvalidationDiagnosticError(f"{label} diagnostic is not an object")
    report = dict(value)
    required = {
        "format",
        "schema_version",
        "diagnostic_id",
        "operation_class",
        "authority",
        "state",
        "profile",
        "source",
        "channels",
        "recommendation",
        "limitations",
    }
    if (
        set(report) != required
        or report.get("format") != DIAGNOSTIC_FORMAT
        or report.get("schema_version") != 2
        or report.get("operation_class") != "read-only"
        or report.get("profile") != profile.get("diagnostic_profile")
        or report.get("diagnostic_id") != _content_id(report, "diagnostic_id")
        or not isinstance(report.get("source"), Mapping)
        or not isinstance(report.get("channels"), Mapping)
    ):
        raise RecipeInvalidationDiagnosticError(
            f"{label} diagnostic has an incompatible identity or binding"
        )
    for channel_id in CHANNEL_IDS:
        _validated_channel_report(
            channel_id,
            report["channels"].get(channel_id),
            profile=profile["diagnostic_profile"],
        )
    return report


def _comparison_source(report: Mapping[str, Any]) -> dict[str, Any]:
    source = report["source"]
    return {
        "diagnostic_id": report["diagnostic_id"],
        "launch_receipt": source.get("launch_receipt"),
        "launch_id": source.get("launch_id"),
        "project": source.get("project"),
        "process_observation": source.get("process_observation"),
        "evidence": source.get("evidence"),
    }


def compare_recipe_invalidation_diagnostics(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    channels: Mapping[str, Any],
) -> dict[str, Any]:
    """Compose channel comparisons without inventing a cross-channel total."""

    if not isinstance(profile, Mapping):
        raise RecipeInvalidationDiagnosticError("comparison profile is required")
    baseline_report = _validated_diagnostic(
        baseline,
        profile=profile,
        label="baseline",
    )
    candidate_report = _validated_diagnostic(
        candidate,
        profile=profile,
        label="candidate",
    )
    if not isinstance(compatibility, Mapping) or set(compatibility) != {
        "state",
        "findings",
    }:
        raise RecipeInvalidationDiagnosticError(
            "comparison compatibility is malformed"
        )
    compatibility_state = compatibility.get("state")
    findings = compatibility.get("findings")
    if (
        compatibility_state not in {"comparable", "incomparable"}
        or not isinstance(findings, list)
        or any(not isinstance(item, str) or not item for item in findings)
        or (compatibility_state == "comparable" and findings)
        or (compatibility_state == "incomparable" and not findings)
    ):
        raise RecipeInvalidationDiagnosticError(
            "comparison compatibility is inconsistent"
        )
    if not isinstance(channels, Mapping) or set(channels) != set(CHANNEL_IDS):
        raise RecipeInvalidationDiagnosticError(
            "comparison channel results are incomplete"
        )
    if compatibility_state == "incomparable":
        if any(
            value != {"state": "not-compared"}
            for value in channels.values()
        ):
            raise RecipeInvalidationDiagnosticError(
                "incomparable inputs must not carry semantic channel deltas"
            )
        state = "incomparable"
        recommendation = {
            "state": "recapture-compatible-baseline-and-candidate",
            "summary": "The selected observations are not comparable.",
            "actions": list(findings),
        }
    else:
        channel_states: set[str] = set()
        expected_states = {
            "groovy_postinit": {
                "more-observed",
                "fewer-observed",
                "same-counts",
                "same-counts-with-resolution-count-changes",
            },
            "gt_startup_registration": {
                "more-observed",
                "fewer-observed",
                "same-counts",
            },
        }
        for channel_id in CHANNEL_IDS:
            result = channels[channel_id]
            if (
                not isinstance(result, Mapping)
                or result.get("state") not in expected_states[channel_id]
            ):
                raise RecipeInvalidationDiagnosticError(
                    f"{channel_id} comparison has an unsupported state"
                )
            channel_states.add(str(result.get("state")))
        if "more-observed" in channel_states:
            state = "more-observed"
        elif "fewer-observed" in channel_states:
            state = "fewer-observed"
        elif "same-counts-with-resolution-count-changes" in channel_states:
            state = "same-counts-with-resolution-count-changes"
        else:
            state = "same-counts"
        recommendation = {
            "state": (
                "review-new-or-increased-groups"
                if state == "more-observed"
                else "no-new-or-increased-group-counts"
            ),
            "summary": (
                "Review newly observed or increased channel groups first."
                if state == "more-observed"
                else "No supported channel has a newly observed or increased group count."
            ),
            "actions": [],
        }
    report: dict[str, Any] = {
        "format": COMPARISON_FORMAT,
        "schema_version": 2,
        "comparison_id": "",
        "operation_class": "read-only",
        "authority": {
            "classification": "profile-runtime-observation-comparison",
            "owner": "Supersymmetry Atlas profile",
            "normative": False,
            "atlas_publication": False,
        },
        "state": state,
        "profile": dict(profile),
        "source": {
            "baseline": _comparison_source(baseline_report),
            "candidate": _comparison_source(candidate_report),
            "roles": "caller-selected-explicit-baseline-and-candidate",
            "source_causality": "unbound",
        },
        "compatibility": {
            "state": compatibility_state,
            "findings": list(findings),
        },
        "channels": dict(channels),
        "recommendation": recommendation,
        "limitations": [
            "Channel counts remain separate because cross-log forwarding and deduplication are unproven.",
            "The V3 receipts do not bind the current workspace revision or launch-time pack-profile bytes.",
            "Observed count deltas are not stable per-recipe identities, source regressions, fixes, or approval evidence.",
            "The effective registry, recipe execution, and progression effects are unassessed.",
        ],
    }
    report["comparison_id"] = _content_id(report, "comparison_id")
    return report


__all__ = [
    "RecipeInvalidationDiagnosticError",
    "build_recipe_invalidation_diagnostic",
    "compare_recipe_invalidation_diagnostics",
]
