"""Bounded, durable Recipe Review V2 projection over the complete V1 report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any

from .render import (
    _gregtech_recipe_removal_delta,
    _pair_recipe_modifications,
    _recipe_multiset_delta,
    _recipe_properties,
    _recipe_review_counts,
)


FORMAT = "workbench-recipe-review-v2"
SCHEMA_VERSION = 2
MAX_ROWS_PER_KIND = 200
MAX_CHANGED_PATHS = 2_000
MAX_PROPERTIES = 32
MAX_PROPERTY_VALUES = 24
MAX_TEXT_CHARS = 1_024
MAX_REPORT_BYTES = 16 * 1024 * 1024


class RecipeReviewV2Error(ValueError):
    """The complete report cannot be projected safely into Recipe Review V2."""


def _text(value: object, *, maximum: int = MAX_TEXT_CHARS) -> str:
    rendered = str(value)
    if len(rendered) <= maximum:
        return rendered
    return rendered[: maximum - 1] + "…"


def _source(effect: Mapping[str, Any]) -> dict[str, Any]:
    source = effect.get("source")
    source = source if isinstance(source, Mapping) else {}
    return {
        "path": _text(source.get("path", "unknown")),
        "line": source.get("line") if type(source.get("line")) is int else None,
        "column": source.get("column") if type(source.get("column")) is int else None,
    }


def _properties(value: Mapping[str, Any]) -> tuple[dict[str, list[str]], bool]:
    result: dict[str, list[str]] = {}
    truncated = len(value) > MAX_PROPERTIES
    for key in sorted(value, key=str)[:MAX_PROPERTIES]:
        raw = value[key]
        values = raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else [raw]
        if len(values) > MAX_PROPERTY_VALUES:
            truncated = True
        result[_text(key, maximum=128)] = [
            _text(item) for item in list(values)[:MAX_PROPERTY_VALUES]
        ]
    return result, truncated


def _recipe(effect: Mapping[str, Any], count: int) -> dict[str, Any]:
    recipe = effect.get("recipe")
    recipe = recipe if isinstance(recipe, Mapping) else {}
    fields = effect.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    lifecycle = effect.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
    reload_state = effect.get("reload")
    reload_state = reload_state if isinstance(reload_state, Mapping) else {}
    properties, properties_truncated = _properties(_recipe_properties(effect))
    return {
        "semantic_key": _text(effect.get("semantic_key", ""), maximum=256),
        "count": count,
        "recipe_map": _text(recipe.get("recipe_map", fields.get("recipe_map", "unknown"))),
        "complete": recipe.get("complete") if type(recipe.get("complete")) is bool else None,
        "properties": properties,
        "properties_truncated": properties_truncated,
        "source": _source(effect),
        "lifecycle": {
            "stage": _text(lifecycle.get("stage", "unknown"), maximum=128),
            "execution_state": _text(
                lifecycle.get("execution_state", "unknown"), maximum=128
            ),
            "runtime_invocation_count": "unknown",
        },
        "reload_state": _text(reload_state.get("state", "unknown"), maximum=128),
        "evidence_state": _text(effect.get("evidence_state", "unknown"), maximum=128),
    }


def _modification(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    before_properties, before_truncated = _properties(_recipe_properties(before))
    after_properties, after_truncated = _properties(_recipe_properties(after))
    changed: dict[str, dict[str, list[str] | None]] = {}
    for key in sorted(set(before_properties) | set(after_properties)):
        if before_properties.get(key) != after_properties.get(key):
            changed[key] = {
                "before": before_properties.get(key),
                "after": after_properties.get(key),
            }
    projected_after = _recipe(after, 1)
    return {
        "recipe_map": projected_after["recipe_map"],
        "before_semantic_key": _text(before.get("semantic_key", ""), maximum=256),
        "after_semantic_key": projected_after["semantic_key"],
        "before_source": _source(before),
        "after_source": projected_after["source"],
        "property_changes": changed,
        "properties_truncated": before_truncated or after_truncated,
        "complete": projected_after["complete"],
        "lifecycle": projected_after["lifecycle"],
        "reload_state": projected_after["reload_state"],
        "pairing_basis": "unique-exact-non-property-structure",
    }


def _direct_removal(effect: Mapping[str, Any], count: int) -> dict[str, Any]:
    lifecycle = effect.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
    fields = effect.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    return {
        "semantic_key": _text(effect.get("semantic_key", ""), maximum=256),
        "expression": _text(effect.get("expression", "unknown-call")),
        "adapter_path": _text(fields.get("adapter_path", "unknown"), maximum=256),
        "method": _text(fields.get("method", "unknown"), maximum=128),
        "source_statement_count": count,
        "source": _source(effect),
        "stage": _text(lifecycle.get("stage", "unknown"), maximum=128),
        "execution_state": _text(
            lifecycle.get("execution_state", "unknown"), maximum=128
        ),
        "loop_expansion": "not-performed",
        "runtime_invocation_count": "unknown",
    }


def _bounded_rows(
    rows: Sequence[Any],
    projector: Any,
) -> dict[str, Any]:
    shown = list(rows[:MAX_ROWS_PER_KIND])
    return {
        "rows": [projector(*row) for row in shown],
        "row_count": len(rows),
        "truncated": len(rows) > len(shown),
    }


def _bounded_paths(value: object) -> dict[str, Any]:
    rows = value if isinstance(value, list) else []
    shown = [_text(path, maximum=4_096) for path in rows[:MAX_CHANGED_PATHS]]
    return {
        "paths": shown,
        "path_count": len(rows),
        "truncated": len(rows) > len(shown),
    }


def _selection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Admit only JSON values and reject internal materialization paths."""

    try:
        raw = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise RecipeReviewV2Error("Recipe Review selection is not JSON-compatible") from exc

    def inspect(item: object) -> None:
        if isinstance(item, str):
            if "workbench-git-tree-" in item:
                raise RecipeReviewV2Error(
                    "Recipe Review V2 selection contains an internal temporary path"
                )
            if len(item) > 16_384:
                raise RecipeReviewV2Error("Recipe Review V2 selection text is unbounded")
        elif isinstance(item, list):
            if len(item) > MAX_CHANGED_PATHS:
                raise RecipeReviewV2Error("Recipe Review V2 selection list is unbounded")
            for child in item:
                inspect(child)
        elif isinstance(item, dict):
            if len(item) > 128:
                raise RecipeReviewV2Error("Recipe Review V2 selection object is unbounded")
            for key, child in item.items():
                inspect(key)
                inspect(child)

    inspect(raw)
    return raw


def build_recipe_review_v2(
    report: Mapping[str, Any],
    *,
    selection: Mapping[str, Any],
) -> dict[str, Any]:
    """Project complete V1 evidence into a reviewer-sized additive V2 record."""

    if report.get("format") != "workbench-groovy-pack-program-report-v1":
        raise RecipeReviewV2Error("Recipe Review V2 requires the complete V1 owner report")
    baseline = report.get("baseline")
    candidate = report.get("candidate")
    comparison = report.get("comparison")
    summary = report.get("summary")
    if not all(isinstance(value, Mapping) for value in (baseline, candidate, comparison, summary)):
        raise RecipeReviewV2Error("complete V1 report lacks comparison programs")

    removed = _recipe_multiset_delta(baseline, candidate)
    added = _recipe_multiset_delta(candidate, baseline)
    modifications, remaining_removed, remaining_added = _pair_recipe_modifications(
        removed, added
    )
    direct_removed = _gregtech_recipe_removal_delta(
        report, "removed_rows", "baseline"
    )
    direct_added = _gregtech_recipe_removal_delta(
        report, "added_rows", "candidate"
    )
    counts = _recipe_review_counts(report)
    if counts is None:
        raise RecipeReviewV2Error("Recipe Review V2 requires a baseline")
    (
        modified_count,
        removed_count,
        added_count,
        removal_statements_removed,
        removal_statements_added,
        effect_rows_truncated,
    ) = counts
    comparison_files = comparison.get("files")
    comparison_files = comparison_files if isinstance(comparison_files, Mapping) else {}
    candidate_binding = candidate.get("binding")
    candidate_binding = candidate_binding if isinstance(candidate_binding, Mapping) else {}
    warnings = candidate.get("run_config")
    warnings = warnings if isinstance(warnings, Mapping) else {}
    warning_rows = warnings.get("warnings")
    warning_rows = warning_rows if isinstance(warning_rows, list) else []
    attention_rows = summary.get("attention_reasons")
    attention_rows = attention_rows if isinstance(attention_rows, list) else []
    runtime = report.get("runtime_evidence")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    change = report.get("change_assessment")
    change = change if isinstance(change, Mapping) else {}
    profile = report.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    summary_status = summary.get("status")

    body: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        "authority": {
            "analysis_owner": "Pack Program Studio",
            "git_identity_owner": "Project Intelligence",
            "construction_authority": "none",
        },
        "source_report": {
            "format": report["format"],
            "schema_version": report.get("schema_version"),
            "report_id": report.get("report_id"),
            "availability": "rerun with --full-json-v1",
        },
        "profile": {
            key: profile.get(key)
            for key in (
                "profile_id",
                "profile_sha256",
                "pack_profile_id",
                "platform_profile_id",
                "platform_profile_sha256",
            )
        },
        "selection": _selection(selection),
        "summary": {
            "status": summary_status,
            "strict_exit_code": (
                2
                if summary_status == "blocked"
                else 1
                if summary_status == "attention"
                else 0
            ),
            "analysis_state": summary.get("analysis_state"),
            "comparison_state": summary.get("comparison_state"),
            "runtime_state": summary.get("runtime_state"),
            "changed_source_files": comparison_files.get("changed", 0),
            "machine_recipes": {
                "modified": modified_count,
                "added": added_count,
                "removed": removed_count,
            },
            "direct_removal_source_statements": {
                "added": removal_statements_added,
                "removed": removal_statements_removed,
                "counts_incomplete": effect_rows_truncated,
                "runtime_invocation_counts": "unknown",
            },
        },
        "files": {
            "added": _bounded_paths(comparison_files.get("added")),
            "modified": _bounded_paths(comparison_files.get("modified")),
            "removed": _bounded_paths(comparison_files.get("removed")),
        },
        "machine_recipes": {
            "modified": _bounded_rows(modifications, _modification),
            "added": _bounded_rows(remaining_added, _recipe),
            "removed": _bounded_rows(remaining_removed, _recipe),
            "pairing_boundary": (
                "Only unique one-to-one rows with exact non-property structure are "
                "paired; ambiguous rows remain independent deltas."
            ),
        },
        "direct_removal_calls": {
            "added": _bounded_rows(direct_added, _direct_removal),
            "removed": _bounded_rows(direct_removed, _direct_removal),
            "boundary": (
                "Rows are static source statements. Loop bodies are not expanded; "
                "effective removals and runtime invocation counts remain unknown."
            ),
        },
        "attention": {
            "strict_reasons": [_text(row) for row in attention_rows[:200]],
            "strict_reasons_truncated": len(attention_rows) > 200,
            "configuration_warnings": [_text(row) for row in warning_rows[:200]],
            "configuration_warnings_truncated": len(warning_rows) > 200,
            "configuration_warnings_drive_strict": False,
        },
        "review_guidance": {
            "change_state": change.get("state"),
            "recommendation": _text(change.get("recommendation", ""), maximum=4_096),
            "save_risk_count": len(change.get("save_risks", []))
            if isinstance(change.get("save_risks"), list)
            else 0,
        },
        "evidence": {
            "candidate_program_id": candidate.get("program_id"),
            "baseline_program_id": baseline.get("program_id"),
            "candidate_git": candidate_binding.get("git"),
            "runtime_state": runtime.get("state", "not-supplied"),
            "static_effect_rows_truncated": effect_rows_truncated,
        },
        "limitations": [
            "Static source candidates are not observed registry effects.",
            "The compact record is bounded; use --full-json-v1 for complete owner evidence.",
            *[
                _text(row)
                for row in list(report.get("limitations", []))[:50]
                if isinstance(row, str)
            ],
        ],
        "next_actions": [
            {
                "id": "full-owner-report",
                "description": "Emit the complete source-linked V1 owner report when needed.",
                "command_hint": "rerun this review with --full-json-v1",
            },
            *(
                [
                    {
                        "id": "compiler-check",
                        "description": "Check selected source through a configured Groovy language server.",
                        "command_hint": "workbench groovy check --help",
                    },
                    {
                        "id": "runtime-evidence",
                        "description": "Supply a retained runtime diagnosis or Groovy log before claiming effective behavior.",
                        "command_hint": "workbench runtime-diagnose --help",
                    },
                ]
                if runtime.get("state") == "not-supplied"
                else []
            ),
        ],
    }
    body["report_id"] = ""
    identity_body = {key: value for key, value in body.items() if key != "report_id"}
    body["report_id"] = (
        "workbench-recipe-review:sha256:"
        + sha256(
            json.dumps(
                identity_body,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise RecipeReviewV2Error("Recipe Review V2 exceeds its encoded byte limit")
    return body


__all__ = [
    "FORMAT",
    "MAX_REPORT_BYTES",
    "RecipeReviewV2Error",
    "SCHEMA_VERSION",
    "build_recipe_review_v2",
]
