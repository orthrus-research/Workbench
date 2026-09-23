"""Profile-owned diagnosis of Supersymmetry GT recipe reload conflicts.

This observer intentionally recognizes one narrow, stable log grammar.  It
does not reconstruct the recipe registry and it does not authorize a reload.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
import unicodedata
from typing import Any, Mapping


REPORT_FORMAT = "workbench-supersymmetry-recipe-reload-diagnostic-v1"
COMPARISON_FORMAT = (
    "workbench-supersymmetry-groovy-conflict-group-comparison-v1"
)
MAX_EMITTED_GROUPS = 100
MAX_EXAMPLES_PER_GROUP = 3
MAX_EMITTED_FRONTIERS = 50
MAX_DISTINCT_GROUPS = 10_000
MAX_IDENTITY_CHARS = 512
MAX_COMPARISON_GROUPS = MAX_EMITTED_GROUPS * 2

_LOG_ROW_RE = re.compile(
    r"^\[[^]]+\] \[[^]]+/(?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\] "
    r"\[(?P<logger>[^]]+)\]: (?P<message>.*)$"
)
_POST_INIT_RE = re.compile(r"^Running scripts in loader ['\"]postInit['\"]$")
_POST_INIT_COMPLETE_RE = re.compile(
    r"^Groovy scripts took [0-9]+ms to compile and [0-9]+ms to run in postInit\.$"
)
_CONFLICT_RE = re.compile(
    r"^Recipe duplicate or conflict found in RecipeMap (?P<recipe_map>.+?) "
    r"and was not added\. See next lines for details$"
)
_ATTEMPT_PREFIX = "Attempted to add Recipe: "
_EXACT_CONFLICT_PREFIX = "Which conflicts with: "
_UNKNOWN_CONFLICT = "Could not find exact duplicate/conflict."


class RecipeReloadDiagnosticError(ValueError):
    """The supplied observation cannot support this bounded diagnostic."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _lifecycle(execution_ordinal: int | None) -> str:
    if execution_ordinal is None:
        return "unbound"
    return "initial" if execution_ordinal == 1 else "reload"


def _bounded_identity(value: str) -> str | None:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > MAX_IDENTITY_CHARS
        or any(
            unicodedata.category(character).startswith("C")
            for character in candidate
        )
    ):
        return None
    return candidate


def _parsed_row(line: str) -> re.Match[str] | None:
    return _LOG_ROW_RE.fullmatch(line.rstrip("\r"))


def _frontier(
    *,
    line: int,
    execution_ordinal: int | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "kind": "incomplete-gt-recipe-conflict-sequence",
        "line": line,
        "post_init_execution_ordinal": execution_ordinal,
        "lifecycle": _lifecycle(execution_ordinal),
        "reason": reason,
    }


def _continuation(
    lines: list[str],
    index: int,
    logger: str,
) -> tuple[str | None, str | None]:
    if index + 1 >= len(lines):
        return None, "missing-attempt-and-resolution-rows"
    attempt = _parsed_row(lines[index + 1])
    if attempt is None:
        return None, "attempt-row-is-not-a-recognized-log-row"
    if attempt.group("level") != "WARN" or attempt.group("logger") != logger:
        return None, "attempt-row-does-not-share-warning-logger"
    if not attempt.group("message").startswith(_ATTEMPT_PREFIX):
        return None, "attempt-row-has-unrecognized-message"
    if index + 2 >= len(lines):
        return None, "missing-resolution-row"
    resolution = _parsed_row(lines[index + 2])
    if resolution is None:
        return None, "resolution-row-is-not-a-recognized-log-row"
    if resolution.group("level") != "WARN" or resolution.group("logger") != logger:
        return None, "resolution-row-does-not-share-warning-logger"
    message = resolution.group("message")
    if message.startswith(_EXACT_CONFLICT_PREFIX):
        return "exact-conflict", None
    if message == _UNKNOWN_CONFLICT:
        return "conflict-not-identified", None
    return None, "resolution-row-has-unrecognized-message"


def _recommendation(
    *,
    post_init_execution_count: int,
    initial_conflicts: int,
    unbound_conflicts: int,
    incomplete_sequences: int,
    initial_execution_completed: bool,
) -> dict[str, Any]:
    if post_init_execution_count > 1:
        return {
            "state": "restart-required",
            "summary": (
                "A repeated postInit execution was observed. Supersymmetry's "
                "experimental reload evidence is divergent, so discard this "
                "process and reproduce from a cold start before attributing "
                "recipe conflicts to a source change."
            ),
            "actions": [
                "Stop the current client or server instead of reloading postInit again.",
                "Reproduce the candidate in a fresh disposable process.",
                "Compare the cold-start groups with an explicitly selected completed baseline; only newly observed or increased group counts are comparison findings.",
            ],
        }
    if initial_conflicts:
        return {
            "state": "review-cold-start-conflicts",
            "summary": (
                "GT recipe conflicts were observed during the initial postInit "
                "execution and should be reviewed by script logger and recipe map."
            ),
            "actions": [
                "Start with the largest observed logger and recipe-map group.",
                "Compare against an explicitly selected completed cold-start baseline.",
            ],
        }
    if unbound_conflicts:
        return {
            "state": "inspect-unbound-conflicts",
            "summary": (
                "GT recipe conflicts were recognized, but no postInit execution "
                "boundary was available to classify them as initial or reload."
            ),
            "actions": [
                "Inspect the retained log around the reported line ranges.",
                "Capture a complete cold-start log with loader boundaries before comparing regressions.",
            ],
        }
    if incomplete_sequences:
        return {
            "state": "inspect-incomplete-log-sequences",
            "summary": (
                "Potential GT recipe conflict headers were observed, but their "
                "three-line sequences were incomplete."
            ),
            "actions": [
                "Inspect the retained log at the reported frontier lines.",
                "Capture a complete cold-start log before making a recipe-health claim.",
            ],
        }
    if not initial_execution_completed:
        return {
            "state": "capture-complete-post-init-log",
            "summary": (
                "The initial postInit execution did not have a recognized "
                "completion row, so an empty conflict set is inconclusive."
            ),
            "actions": [
                "Capture the Groovy log through the postInit timing/completion row.",
            ],
        }
    return {
        "state": "no-action-from-this-diagnostic",
        "summary": (
            "No complete Groovy-log GT duplicate/conflict sequence was recognized; "
            "other recipe-registration channels were not assessed."
        ),
        "actions": [],
    }


def build_recipe_reload_diagnostic(
    text: str,
    *,
    source: Mapping[str, Any],
    profile: Mapping[str, Any],
    inherited_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Reduce one verified Groovy log into bounded recipe-conflict groups."""

    if not isinstance(text, str):
        raise RecipeReloadDiagnosticError("Groovy log text must be a string")
    if not isinstance(source, Mapping) or not isinstance(profile, Mapping):
        raise RecipeReloadDiagnosticError("source and profile bindings are required")

    lines = text.splitlines()
    execution_ordinal: int | None = None
    post_init_execution_count = 0
    completed_execution_ordinals: set[int] = set()
    complete_event_count = 0
    initial_event_count = 0
    reload_event_count = 0
    unbound_event_count = 0
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    frontiers: list[dict[str, Any]] = []
    frontier_count = 0

    def add_frontier(frontier: dict[str, Any]) -> None:
        nonlocal frontier_count
        frontier_count += 1
        if len(frontiers) < MAX_EMITTED_FRONTIERS:
            frontiers.append(frontier)

    for index, line in enumerate(lines):
        row = _parsed_row(line)
        if row is None:
            continue
        if (
            row.group("level") == "INFO"
            and row.group("logger") == "supersymmetry"
            and _POST_INIT_RE.fullmatch(row.group("message"))
        ):
            post_init_execution_count += 1
            execution_ordinal = post_init_execution_count
            continue
        if (
            execution_ordinal is not None
            and row.group("level") == "INFO"
            and row.group("logger") == "supersymmetry"
            and _POST_INIT_COMPLETE_RE.fullmatch(row.group("message"))
        ):
            completed_execution_ordinals.add(execution_ordinal)
            execution_ordinal = None
            continue
        if row.group("level") != "WARN":
            continue
        header = _CONFLICT_RE.fullmatch(row.group("message"))
        if header is None:
            continue
        logger = _bounded_identity(row.group("logger"))
        recipe_map = _bounded_identity(header.group("recipe_map"))
        if logger is None or recipe_map is None:
            add_frontier(
                _frontier(
                    line=index + 1,
                    execution_ordinal=execution_ordinal,
                    reason="script-logger-or-recipe-map-exceeds-the-identity-bound",
                )
            )
            continue
        resolution, reason = _continuation(lines, index, logger)
        if resolution is None:
            add_frontier(
                _frontier(
                    line=index + 1,
                    execution_ordinal=execution_ordinal,
                    reason=str(reason),
                )
            )
            continue

        key = (logger, recipe_map)
        if key not in groups:
            if len(groups) >= MAX_DISTINCT_GROUPS:
                raise RecipeReloadDiagnosticError(
                    f"Groovy log exceeds the {MAX_DISTINCT_GROUPS}-group diagnostic bound"
                )
            groups[key] = {
                "kind": "gt-recipe-duplicate-or-conflict",
                "script_logger": logger,
                "recipe_map": recipe_map,
                "counts": {
                    "total": 0,
                    "initial": 0,
                    "reload": 0,
                    "unbound": 0,
                },
                "resolutions": {
                    "exact-conflict": 0,
                    "conflict-not-identified": 0,
                },
                "first_line": index + 1,
                "last_line": index + 3,
                "examples": [],
            }
        group = groups[key]
        lifecycle = _lifecycle(execution_ordinal)
        group["counts"]["total"] += 1
        group["counts"][lifecycle] += 1
        group["resolutions"][resolution] += 1
        group["last_line"] = index + 3
        if len(group["examples"]) < MAX_EXAMPLES_PER_GROUP:
            group["examples"].append({
                "line_start": index + 1,
                "line_end": index + 3,
                "post_init_execution_ordinal": execution_ordinal,
                "lifecycle": lifecycle,
                "resolution": resolution,
            })

        complete_event_count += 1
        if lifecycle == "initial":
            initial_event_count += 1
        elif lifecycle == "reload":
            reload_event_count += 1
        else:
            unbound_event_count += 1

    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            -group["counts"]["reload"],
            -group["counts"]["total"],
            group["script_logger"],
            group["recipe_map"],
        ),
    )
    emitted_groups = ordered_groups[:MAX_EMITTED_GROUPS]
    emitted_frontiers = frontiers
    recommendation = _recommendation(
        post_init_execution_count=post_init_execution_count,
        initial_conflicts=initial_event_count,
        unbound_conflicts=unbound_event_count,
        incomplete_sequences=frontier_count,
        initial_execution_completed=1 in completed_execution_ordinals,
    )
    if complete_event_count or post_init_execution_count > 1:
        state = "attention"
    elif (
        frontier_count
        or post_init_execution_count == 0
        or 1 not in completed_execution_ordinals
    ):
        state = "inconclusive"
    else:
        state = "no-groovy-conflicts-observed"
    limitations = list(inherited_limitations or [])
    limitations.extend([
        "Only complete three-line GregTech duplicate/conflict log sequences are counted.",
        "The script logger is an observed attribution label, not proof of a source file or causal owner.",
        "This diagnostic does not measure effective registry deltas, lookup activity, progression impact, or client visibility.",
        "Absence of a recognized conflict does not prove that a recipe change is safe.",
    ])
    if len(ordered_groups) > len(emitted_groups):
        limitations.append(
            f"{len(ordered_groups) - len(emitted_groups)} conflict group(s) were omitted by the report bound."
        )
    if frontier_count > len(emitted_frontiers):
        limitations.append(
            f"{frontier_count - len(emitted_frontiers)} incomplete sequence frontier(s) were omitted by the report bound."
        )

    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "schema_version": 1,
        "diagnostic_id": "",
        "operation_class": "read-only",
        "authority": {
            "classification": "profile-runtime-observation",
            "owner": "Supersymmetry Atlas profile",
            "normative": False,
            "atlas_publication": False,
        },
        "state": state,
        "profile": dict(profile),
        "source": dict(source),
        "scope": {
            "loader": "postInit",
            "post_init_execution_count": post_init_execution_count,
            "completed_post_init_execution_count": len(
                completed_execution_ordinals
            ),
            "initial_execution_ordinal": (
                1 if post_init_execution_count else None
            ),
            "initial_execution_completed": 1 in completed_execution_ordinals,
            "reload_execution_count": max(0, post_init_execution_count - 1),
            "completed_reload_execution_count": len(
                completed_execution_ordinals - {1}
            ),
        },
        "summary": {
            "complete_conflict_count": complete_event_count,
            "initial_conflict_count": initial_event_count,
            "reload_conflict_count": reload_event_count,
            "unbound_conflict_count": unbound_event_count,
            "group_count": len(ordered_groups),
            "emitted_group_count": len(emitted_groups),
            "groups_truncated": len(ordered_groups) > len(emitted_groups),
            "incomplete_sequence_count": frontier_count,
            "emitted_frontier_count": len(emitted_frontiers),
            "frontiers_truncated": frontier_count > len(emitted_frontiers),
        },
        "groups": emitted_groups,
        "frontiers": emitted_frontiers,
        "recommendation": recommendation,
        "limitations": sorted(set(limitations)),
    }
    identity_payload = dict(report)
    identity_payload.pop("diagnostic_id")
    report["diagnostic_id"] = "sha256:" + sha256(
        _canonical_bytes(identity_payload)
    ).hexdigest()
    return report


def _validated_cold_start(
    value: Mapping[str, Any],
    *,
    diagnostic_profile: Mapping[str, Any],
    label: str,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise RecipeReloadDiagnosticError(
            f"{label} recipe diagnostic is not an object"
        )
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
        "scope",
        "summary",
        "groups",
        "frontiers",
        "recommendation",
        "limitations",
    }
    identity = dict(report)
    identity.pop("diagnostic_id", None)
    if (
        set(report) != required
        or report.get("format") != REPORT_FORMAT
        or report.get("schema_version") != 1
        or report.get("operation_class") != "read-only"
        or report.get("profile") != diagnostic_profile
        or report.get("diagnostic_id")
        != "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()
    ):
        raise RecipeReloadDiagnosticError(
            f"{label} recipe diagnostic has an incompatible identity"
        )
    source = report.get("source")
    scope = report.get("scope")
    summary = report.get("summary")
    groups = report.get("groups")
    frontiers = report.get("frontiers")
    if (
        not isinstance(source, Mapping)
        or not isinstance(scope, Mapping)
        or not isinstance(summary, Mapping)
        or not isinstance(groups, list)
        or not isinstance(frontiers, list)
        or source.get("launch_outcome") != "checkpoint-reached"
        or not isinstance(source.get("process_observation"), Mapping)
        or source["process_observation"].get("state") != "exited"
        or scope.get("post_init_execution_count") != 1
        or scope.get("completed_post_init_execution_count") != 1
        or scope.get("initial_execution_ordinal") != 1
        or scope.get("initial_execution_completed") is not True
        or scope.get("reload_execution_count") != 0
        or scope.get("completed_reload_execution_count") != 0
        or summary.get("reload_conflict_count") != 0
        or summary.get("unbound_conflict_count") != 0
        or summary.get("incomplete_sequence_count") != 0
        or summary.get("groups_truncated") is not False
        or summary.get("frontiers_truncated") is not False
        or summary.get("emitted_group_count") != len(groups)
        or summary.get("group_count") != len(groups)
        or frontiers
    ):
        raise RecipeReloadDiagnosticError(
            f"{label} is not a complete, untruncated initial postInit observation"
        )
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    total = 0
    for group in groups:
        if not isinstance(group, Mapping):
            raise RecipeReloadDiagnosticError(
                f"{label} contains a malformed conflict group"
            )
        kind = group.get("kind")
        logger = group.get("script_logger")
        recipe_map = group.get("recipe_map")
        counts = group.get("counts")
        resolutions = group.get("resolutions")
        if (
            not all(isinstance(item, str) and item for item in (kind, logger, recipe_map))
            or not isinstance(counts, Mapping)
            or set(counts) != {"total", "initial", "reload", "unbound"}
            or any(type(counts.get(key)) is not int for key in counts)
            or counts.get("total", 0) <= 0
            or counts.get("initial") != counts.get("total")
            or counts.get("reload") != 0
            or counts.get("unbound") != 0
            or not isinstance(resolutions, Mapping)
            or set(resolutions)
            != {"exact-conflict", "conflict-not-identified"}
            or any(type(resolutions.get(key)) is not int for key in resolutions)
            or sum(resolutions.values()) != counts.get("total")
            or type(group.get("first_line")) is not int
            or group["first_line"] <= 0
        ):
            raise RecipeReloadDiagnosticError(
                f"{label} contains a malformed cold-start conflict group"
            )
        key = (kind, logger, recipe_map)
        if key in indexed:
            raise RecipeReloadDiagnosticError(
                f"{label} contains duplicate conflict-group identities"
            )
        indexed[key] = {
            "count": counts["total"],
            "first_line": group["first_line"],
            "resolutions": dict(resolutions),
        }
        total += counts["total"]
    if (
        summary.get("complete_conflict_count") != total
        or summary.get("initial_conflict_count") != total
    ):
        raise RecipeReloadDiagnosticError(
            f"{label} conflict totals do not match its complete groups"
        )
    return indexed, report


def _comparison_source(report: Mapping[str, Any]) -> dict[str, Any]:
    source = report["source"]
    return {
        "diagnostic_id": report["diagnostic_id"],
        "launch_receipt": dict(source["launch_receipt"]),
        "launch_id": source["launch_id"],
        "evidence": dict(source["evidence"]),
    }


def compare_recipe_reload_diagnostics(
    baseline_value: Mapping[str, Any],
    candidate_value: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare complete cold-start conflict-group counts from two receipts."""

    if (
        not isinstance(profile, Mapping)
        or set(profile)
        != {"diagnostic_profile", "function", "report_format"}
        or profile.get("function") != "compare_recipe_reload_diagnostics"
        or profile.get("report_format") != COMPARISON_FORMAT
        or not isinstance(profile.get("diagnostic_profile"), Mapping)
    ):
        raise RecipeReloadDiagnosticError(
            "recipe conflict comparison profile is incompatible"
        )
    diagnostic_profile = profile["diagnostic_profile"]
    baseline, baseline_report = _validated_cold_start(
        baseline_value,
        diagnostic_profile=diagnostic_profile,
        label="baseline",
    )
    candidate, candidate_report = _validated_cold_start(
        candidate_value,
        diagnostic_profile=diagnostic_profile,
        label="candidate",
    )
    baseline_source = baseline_report["source"]
    candidate_source = candidate_report["source"]
    if (
        baseline_source.get("project") != candidate_source.get("project")
        or baseline_source.get("pack_profile")
        != candidate_source.get("pack_profile")
        or baseline_source.get("launch_id") == candidate_source.get("launch_id")
        or baseline_source.get("launch_receipt")
        == candidate_source.get("launch_receipt")
    ):
        raise RecipeReloadDiagnosticError(
            "baseline and candidate are not distinct compatible observations"
        )

    rows: list[dict[str, Any]] = []
    classification_counts = {
        "newly-observed": 0,
        "increased": 0,
        "decreased": 0,
        "no-longer-observed": 0,
        "same-count": 0,
    }
    for kind, logger, recipe_map in sorted(set(baseline) | set(candidate)):
        before = baseline.get((kind, logger, recipe_map))
        after = candidate.get((kind, logger, recipe_map))
        baseline_count = 0 if before is None else before["count"]
        candidate_count = 0 if after is None else after["count"]
        if baseline_count == 0:
            classification = "newly-observed"
        elif candidate_count == 0:
            classification = "no-longer-observed"
        elif candidate_count > baseline_count:
            classification = "increased"
        elif candidate_count < baseline_count:
            classification = "decreased"
        else:
            classification = "same-count"
        classification_counts[classification] += 1
        baseline_resolutions = (
            {"exact-conflict": 0, "conflict-not-identified": 0}
            if before is None
            else before["resolutions"]
        )
        candidate_resolutions = (
            {"exact-conflict": 0, "conflict-not-identified": 0}
            if after is None
            else after["resolutions"]
        )
        resolution_delta = {
            key: candidate_resolutions[key] - baseline_resolutions[key]
            for key in ("exact-conflict", "conflict-not-identified")
        }
        rows.append({
            "kind": kind,
            "script_logger": logger,
            "recipe_map": recipe_map,
            "classification": classification,
            "baseline_count": baseline_count,
            "candidate_count": candidate_count,
            "delta": candidate_count - baseline_count,
            "baseline_first_line": (
                None if before is None else before["first_line"]
            ),
            "candidate_first_line": (
                None if after is None else after["first_line"]
            ),
            "resolution_count_delta": resolution_delta,
            "resolution_counts_changed": any(resolution_delta.values()),
        })
    if len(rows) > MAX_COMPARISON_GROUPS:
        raise RecipeReloadDiagnosticError(
            "recipe conflict comparison exceeds its complete group bound"
        )
    priority = {
        "newly-observed": 0,
        "increased": 1,
        "decreased": 2,
        "no-longer-observed": 3,
        "same-count": 4,
    }
    rows.sort(key=lambda row: (
        priority[row["classification"]],
        -abs(row["delta"]),
        row["script_logger"],
        row["recipe_map"],
    ))
    baseline_total = sum(item["count"] for item in baseline.values())
    candidate_total = sum(item["count"] for item in candidate.values())
    more = (
        classification_counts["newly-observed"]
        + classification_counts["increased"]
    )
    fewer = (
        classification_counts["decreased"]
        + classification_counts["no-longer-observed"]
    )
    same_count_resolution_changes = sum(
        1
        for row in rows
        if row["classification"] == "same-count"
        and row["resolution_counts_changed"]
    )
    if more:
        state = "more-observed"
        recommendation = {
            "state": "inspect-new-or-increased-groups",
            "summary": (
                "The candidate has newly observed or increased Groovy conflict-group counts."
            ),
            "actions": [
                "Review the largest newly observed and increased logger/map groups first.",
                "Use the retained line references to inspect both exact Groovy logs.",
                "Reproduce from another fresh process before attributing the delta to a source edit.",
            ],
        }
    elif fewer:
        state = "fewer-observed"
        recommendation = {
            "state": "no-new-or-increased-group-counts",
            "summary": (
                "No Groovy conflict-group count was newly observed or increased; some counts decreased or disappeared."
            ),
            "actions": [],
        }
    elif same_count_resolution_changes:
        state = "same-counts-with-resolution-count-changes"
        recommendation = {
            "state": "inspect-resolution-count-changes",
            "summary": (
                "No group count increased, but one or more conflict-resolution count shapes changed."
            ),
            "actions": [
                "Inspect the retained baseline and candidate log line references.",
            ],
        }
    else:
        state = "same-counts"
        recommendation = {
            "state": "no-new-or-increased-group-counts",
            "summary": (
                "The two cold-start observations have the same conflict-group counts."
            ),
            "actions": [],
        }

    report: dict[str, Any] = {
        "format": COMPARISON_FORMAT,
        "schema_version": 1,
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
            "selection": "explicit-user-supplied-receipts",
            "source_causality": "unbound",
            "project": dict(baseline_source["project"]),
            "baseline": _comparison_source(baseline_report),
            "candidate": _comparison_source(candidate_report),
        },
        "scope": {
            "loader": "postInit",
            "lifecycle": "one-completed-initial-execution-per-side",
            "group_identity": ["kind", "script_logger", "recipe_map"],
            "baseline_role": "explicit-selected-baseline",
            "candidate_role": "explicit-selected-candidate",
        },
        "summary": {
            "baseline_complete_conflict_count": baseline_total,
            "candidate_complete_conflict_count": candidate_total,
            "net_conflict_count_delta": candidate_total - baseline_total,
            "group_count": len(rows),
            "newly_observed_group_count": classification_counts["newly-observed"],
            "increased_group_count": classification_counts["increased"],
            "decreased_group_count": classification_counts["decreased"],
            "no_longer_observed_group_count": classification_counts[
                "no-longer-observed"
            ],
            "same_count_group_count": classification_counts["same-count"],
            "same_count_resolution_changed_group_count": (
                same_count_resolution_changes
            ),
        },
        "groups": rows,
        "recommendation": recommendation,
        "limitations": [
            "The baseline and candidate roles are explicit selections, not a persisted or verified last-green designation.",
            "The V3 receipts bind runtime evidence but do not bind the workspace revision or selected pack-profile bytes at launch.",
            "Group-count deltas do not identify stable individual recipes and do not prove source causality.",
            "The script logger is an observed attribution label, not proof of a source file or causal owner.",
            "Java/latest.log recipe-registration signals and the effective recipe registry are outside this comparison.",
            "No new or increased group count does not prove that a recipe change is safe.",
        ],
    }
    identity = dict(report)
    identity.pop("comparison_id")
    report["comparison_id"] = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    return report


__all__ = [
    "COMPARISON_FORMAT",
    "REPORT_FORMAT",
    "RecipeReloadDiagnosticError",
    "build_recipe_reload_diagnostic",
    "compare_recipe_reload_diagnostics",
]
