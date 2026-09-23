"""Concise terminal rendering for Groovy Pack Program Studio reports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any

from .profile import native_filesystem_path


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_RECIPE_ROWS_PER_SIDE = 20
_MAX_RECIPE_PROPERTIES = 10
_MAX_RECIPE_PROPERTY_VALUES = 3
_MAX_RECIPE_PROPERTY_VALUE_CHARS = 80
_MAX_RECIPE_PROPERTY_SUMMARY_CHARS = 480
_MAX_RECIPE_REMOVAL_EXPRESSION_CHARS = 220
_MAX_PRESENTATION_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_LINE_ENDING_FILES = 512
_MAX_LINE_ENDING_TOTAL_BYTES = 32 * 1024 * 1024
_RECIPE_PROPERTY_ORDER = (
    "inputs",
    "fluidInputs",
    "notConsumable",
    "outputs",
    "chancedOutput",
    "fluidOutputs",
    "circuitMeta",
    "duration",
    "EUt",
    "property",
)


def render_report(
    report: Mapping[str, Any],
    *,
    recipe_review: bool = False,
    verbose: bool = False,
) -> str:
    if recipe_review:
        return _render_recipe_review_report(report, verbose=verbose)
    return _render_program_report(report)


def _render_program_report(report: Mapping[str, Any]) -> str:
    candidate = report["candidate"]
    summary = report["summary"]
    binding = candidate["binding"]
    lines = [
        "GroovyScript Pack Program Studio",
        f"Status      {summary['status']} · {summary['analysis_state']}",
        f"Program     {_short(candidate['program_id'])}",
        f"Profile     {_clean(binding['profile_id'])}",
        f"Source      {_clean(binding['groovy_root'])}",
    ]
    git = binding.get("git")
    if isinstance(git, Mapping):
        lines.append(
            f"Git         {str(git['revision'])[:12]}{' · dirty' if git['dirty'] else ' · clean'}"
        )
    lines.extend(
        [
            "",
            (
                f"{summary['files']:,} files · {summary['source_lines']:,} lines · "
                f"{summary['effects']:,} static effect/reference candidates · "
                f"{summary['collision_candidates']} collision candidates"
            ),
        ]
    )

    comparison = report["comparison"]
    lines.extend(["", "Change preview"])
    if comparison["state"] == "not-requested":
        lines.append("  No baseline supplied; semantic comparison was not requested.")
    else:
        lines.append(
            f"  {comparison['state']} · {comparison['files']['changed']} changed files · "
            f"+{comparison['effects']['added']} / -{comparison['effects']['removed']} static effects"
        )
        lines.extend(_render_recipe_review(report))
    change = report["change_assessment"]
    lines.append(f"  Reload      {change['state']}: {_clean(change['recommendation'])}")
    if change["save_risks"]:
        lines.append(
            f"  Save review {len(change['save_risks'])} identity-bearing risk(s) in the selected change scope"
        )

    lines.extend(_render_program_details(report))

    warnings = candidate["run_config"]["warnings"]
    if warnings or summary["attention_reasons"]:
        lines.extend(["", "Attention"])
        for reason in [*summary["attention_reasons"], *warnings]:
            lines.append(f"  - {_clean(reason)}")
    lines.extend(_render_report_boundary())
    return "\n".join(lines) + "\n"


def _render_recipe_review_report(
    report: Mapping[str, Any],
    *,
    verbose: bool,
) -> str:
    comparison = report["comparison"]
    counts = _recipe_review_counts(report)
    line_ending_only = 0
    line_ending_scan_complete = True
    lines: list[str]
    if counts is None:
        lines = ["Recipe review: unavailable · no baseline supplied"]
        lines.extend(
            [
                "Scope       semantic comparison was not requested",
            ]
        )
    else:
        (
            modified,
            removed,
            added,
            removal_statements_removed,
            removal_statements_added,
            removal_statements_truncated,
        ) = counts
        summary_parts = [
            f"{modified} modified",
            f"{added} added",
            f"{removed} removed",
        ]
        if removal_statements_added:
            summary_parts.append(
                f"{'at least ' if removal_statements_truncated else ''}"
                f"{_counted(removal_statements_added, 'removal statement')} added"
            )
        if removal_statements_removed:
            summary_parts.append(
                f"{'at least ' if removal_statements_truncated else ''}"
                f"{_counted(removal_statements_removed, 'removal statement')} removed"
            )
        if removal_statements_truncated:
            summary_parts.append("direct-removal count incomplete")
        byte_changed = int(comparison["files"]["changed"])
        line_ending_only, line_ending_scan_complete = (
            _line_ending_only_file_count(report)
        )
        substantive = max(0, byte_changed - line_ending_only)
        analyzed = (
            f"{substantive} other exact-byte/stage changes · "
            f"{'at least ' if not line_ending_scan_complete else ''}"
            f"{line_ending_only} line-ending-only byte changes"
            if line_ending_only
            else f"{byte_changed} changed source files"
        )
        lines = [
            "Recipe review: " + " · ".join(summary_parts),
            (
                f"Analyzed    {analyzed} · "
                f"+{comparison['effects']['added']} / "
                f"-{comparison['effects']['removed']} static effects"
            ),
        ]

    runtime = report["runtime_evidence"]
    if runtime["state"] == "not-supplied":
        runtime_summary = "runtime behavior unverified"
    else:
        acceptance = runtime.get("acceptance")
        acceptance = acceptance if isinstance(acceptance, Mapping) else {}
        runtime_summary = (
            f"runtime evidence {runtime['state']} · "
            f"{acceptance.get('state', 'candidate binding unknown')}"
        )
    lines.append(f"Evidence    static source candidates · {_clean(runtime_summary)}")

    if counts is not None:
        lines.extend(_render_recipe_review(report, heading="Findings"))

    change = report["change_assessment"]
    only_line_endings = (
        counts is not None
        and int(comparison["files"]["changed"]) > 0
        and line_ending_scan_complete
        and line_ending_only == int(comparison["files"]["changed"])
    )
    lines.extend(
        [
            "",
            "Review guidance",
            (
                "  Reload      no-action: only LF/CRLF byte representation "
                "differs; V1 JSON retains the exact source hashes."
                if only_line_endings
                else f"  Reload      {change['state']}: {_clean(change['recommendation'])}"
            ),
        ]
    )
    if change["save_risks"]:
        lines.append(
            f"  Save review {len(change['save_risks'])} identity-bearing risk(s) "
            "in the selected change scope"
        )

    candidate = report["candidate"]
    summary = report["summary"]
    warnings = candidate["run_config"]["warnings"]
    attention = summary["attention_reasons"]
    lines.extend(["", "Other report attention"])
    if attention:
        lines.append(
            "  These report-level signals drive --strict and are not necessarily "
            "introduced by this recipe diff."
        )
        for reason in attention:
            lines.append(f"  - {_clean(reason)}")
    else:
        lines.append("  None; --strict does not change the successful exit status.")
    if warnings:
        lines.extend(
            [
                "",
                "Source configuration warnings",
                "  Informational in V1; these warnings do not drive --strict.",
            ]
        )
        for warning in warnings:
            lines.append(f"  - {_clean(warning)}")

    if verbose:
        lines.extend(["", "Program details"])
        lines.extend(_render_program_provenance(report))
        lines.extend(_render_program_details(report))
    else:
        lines.extend(
            [
                "",
                "More        Use --verbose for full program context; --json for bounded V2; --full-json-v1 for complete owner evidence.",
            ]
        )
    return "\n".join(lines) + "\n"


def _verified_presentation_bytes(
    path_value: object,
    sha256_value: object,
    size_value: object | None = None,
    *,
    maximum: int = _MAX_PRESENTATION_SOURCE_BYTES,
) -> bytes | None:
    """Re-read one already-analyzed file only for conservative human display.

    The V1 owner report remains the exact authority.  If the file moved,
    changed, became indirect, exceeds the presentation bound, or cannot be
    reopened, the renderer simply declines to infer a line-ending-only change.
    """

    if not isinstance(path_value, str) or not isinstance(sha256_value, str):
        return None
    # Reports intentionally expose ordinary display paths.  Adapt the path
    # back to the native extended form for this exact-byte presentation read
    # so Windows paths beyond MAX_PATH receive the same conservative
    # line-ending classification as shorter paths.
    path = native_filesystem_path(Path(path_value))
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not 0 <= metadata.st_size <= min(
                maximum, _MAX_PRESENTATION_SOURCE_BYTES
            )
            or (
                isinstance(size_value, int)
                and not isinstance(size_value, bool)
                and metadata.st_size != size_value
            )
        ):
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if (
        len(raw) != metadata.st_size
        or hashlib.sha256(raw).hexdigest() != sha256_value
    ):
        return None
    return raw


def _normalized_line_endings(raw: bytes) -> bytes:
    # The human label is deliberately limited to the common LF/CRLF
    # representation difference.  A lone CR remains substantive rather than
    # being silently generalized into a broader newline equivalence.
    return raw.replace(b"\r\n", b"\n")


def _line_ending_only_file_count(report: Mapping[str, Any]) -> tuple[int, bool]:
    """Count exact-byte V1 changes whose only difference is LF versus CRLF."""

    baseline = report.get("baseline")
    candidate = report.get("candidate")
    comparison = report.get("comparison")
    if not all(isinstance(value, Mapping) for value in (baseline, candidate, comparison)):
        return 0, False
    file_delta = comparison.get("files")
    if not isinstance(file_delta, Mapping):
        return 0, False
    modified = file_delta.get("modified")
    if not isinstance(modified, list):
        return 0, False
    baseline_files = {
        row.get("path"): row
        for row in baseline.get("files", [])
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }
    candidate_files = {
        row.get("path"): row
        for row in candidate.get("files", [])
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }
    count = 0
    complete = len(modified) <= _MAX_LINE_ENDING_FILES
    remaining_bytes = _MAX_LINE_ENDING_TOTAL_BYTES
    for path in modified[:_MAX_LINE_ENDING_FILES]:
        if remaining_bytes <= 0:
            complete = False
            break
        if path == "runConfig.json":
            baseline_binding = baseline.get("binding")
            candidate_binding = candidate.get("binding")
            if not isinstance(baseline_binding, Mapping) or not isinstance(
                candidate_binding, Mapping
            ):
                continue
            baseline_raw = _verified_presentation_bytes(
                baseline_binding.get("run_config_path"),
                baseline_binding.get("run_config_sha256"),
                maximum=remaining_bytes,
            )
            if baseline_raw is not None:
                remaining_bytes -= len(baseline_raw)
            candidate_raw = _verified_presentation_bytes(
                candidate_binding.get("run_config_path"),
                candidate_binding.get("run_config_sha256"),
                maximum=remaining_bytes,
            )
        else:
            baseline_row = baseline_files.get(path)
            candidate_row = candidate_files.get(path)
            if not isinstance(baseline_row, Mapping) or not isinstance(
                candidate_row, Mapping
            ):
                continue
            stable_fields = (
                "path",
                "lines",
                "stage",
                "loader_entry",
                "execution_index",
                "preprocessors",
                "execution_state",
                "execution_reasons",
            )
            if any(
                baseline_row.get(field) != candidate_row.get(field)
                for field in stable_fields
            ):
                continue
            baseline_raw = _verified_presentation_bytes(
                baseline_row.get("absolute_path"),
                baseline_row.get("sha256"),
                baseline_row.get("size"),
                maximum=remaining_bytes,
            )
            if baseline_raw is not None:
                remaining_bytes -= len(baseline_raw)
            candidate_raw = _verified_presentation_bytes(
                candidate_row.get("absolute_path"),
                candidate_row.get("sha256"),
                candidate_row.get("size"),
                maximum=remaining_bytes,
            )
        if candidate_raw is not None:
            remaining_bytes -= len(candidate_raw)
        if baseline_raw is None or candidate_raw is None:
            complete = False
            continue
        if (
            baseline_raw != candidate_raw
            and _normalized_line_endings(baseline_raw)
            == _normalized_line_endings(candidate_raw)
        ):
            count += 1
    return count, complete


def _render_program_provenance(report: Mapping[str, Any]) -> list[str]:
    candidate = report["candidate"]
    summary = report["summary"]
    binding = candidate["binding"]
    lines = [
        f"  Status      {summary['status']} · {summary['analysis_state']}",
        f"  Program     {_short(candidate['program_id'])}",
        f"  Profile     {_clean(binding['profile_id'])}",
        f"  Source      {_clean(binding['groovy_root'])}",
    ]
    git = binding.get("git")
    if isinstance(git, Mapping):
        lines.append(
            f"  Git         {str(git['revision'])[:12]}"
            f"{' · dirty' if git['dirty'] else ' · clean'}"
        )
    lines.append(
        f"  Surface     {summary['files']:,} files · {summary['source_lines']:,} lines · "
        f"{summary['effects']:,} static effect/reference candidates · "
        f"{summary['collision_candidates']} collision candidates"
    )
    return lines


def _render_program_details(report: Mapping[str, Any]) -> list[str]:
    candidate = report["candidate"]
    lines: list[str] = []

    lines.extend(["", "Lifecycle"])
    for stage in candidate["stages"]:
        lines.append(
            "  "
            + f"{stage['stage']:<13} {stage['files']:>4} files · "
            + f"{stage['enabled']} enabled, {stage['conditional']} conditional, "
            + f"{stage['excluded']} excluded · {stage['reload']}"
        )

    lines.extend(["", "Program surface"])
    for category, count in sorted(
        candidate["summary"]["effects_by_category"].items(),
        key=lambda item: (-item[1], item[0]),
    ):
        lines.append(f"  {category:<16} {count:>7,}")
    references = candidate["summary"].get("literal_references", {})
    if references:
        lines.append("  Literal references")
        for kind, counts in sorted(references.items()):
            lines.append(
                f"    {kind:<14} {counts['occurrences']:>7,} occurrences · "
                f"{counts['unique_literals']:>6,} unique"
            )
    dependency = candidate["dependencies"]["summary"]
    lines.extend(
        [
            "",
            "Dependencies",
            (
                f"  {dependency['edges']:,} edges · "
                f"{dependency['files_with_dependencies']} files with dependencies · "
                f"{dependency['cycles']} circular components"
            ),
        ]
    )
    for hub in candidate["dependencies"]["hubs"][:5]:
        lines.append(f"  {hub['dependents']:>4} ← {_clean(hub['path'])}")

    collisions = candidate["collisions"]
    lines.extend(["", "Identity review"])
    if not collisions:
        lines.append("  No duplicate literal candidates under the selected profile policies.")
    else:
        for collision in collisions[:10]:
            locations = ", ".join(
                f"{row['source']['path']}:{row['source']['line']}"
                for row in collision["occurrences"][:4]
            )
            lines.append(
                f"  {collision['identity_kind']}={_clean(collision['value'])} · "
                f"{collision['execution_state']} · {locations}"
            )
        if len(collisions) > 10:
            lines.append(f"  … {len(collisions) - 10} additional candidates in JSON output")

    runtime = report["runtime_evidence"]
    lines.extend(["", "Runtime correlation"])
    if runtime["state"] == "not-supplied":
        lines.append("  No runtime evidence supplied; compiler and effective state remain unavailable.")
    else:
        groovy = runtime.get("groovy_log")
        if groovy:
            totals = groovy["totals"]
            lines.append(
                f"  GroovyScript {groovy['groovyscript_version'] or 'unknown'} · "
                f"compile {totals['compile_ms']:,}ms · run {totals['run_ms']:,}ms · "
                f"ERROR/FATAL {groovy['diagnostics']['fatal_or_error']}"
            )
        diagnosis = runtime.get("runtime_diagnosis")
        if diagnosis:
            lines.append(
                f"  Broader runtime · {diagnosis['exception_count']} exception observations · "
                f"checkpoint {'present' if diagnosis['checkpoint'] else 'absent'}"
            )
        lines.append(
            f"  Acceptance   {runtime['acceptance']['state']} · candidate source bytes are not bound"
        )

    return lines


def _render_report_boundary() -> list[str]:
    return [
        "",
        "Boundary",
        "  Static candidates are not observed registry effects. Use --json for source-linked records and explicit coverage.",
    ]


def _render_recipe_review(
    report: Mapping[str, Any],
    *,
    heading: str = "Recipe review",
) -> list[str]:
    baseline = report.get("baseline")
    candidate = report.get("candidate")
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        return []

    removed = _recipe_multiset_delta(baseline, candidate)
    added = _recipe_multiset_delta(candidate, baseline)
    removed_count = sum(count for _, count in removed)
    added_count = sum(count for _, count in added)
    modifications, remaining_removed, remaining_added = _pair_recipe_modifications(
        removed,
        added,
    )
    lines = [
        "",
        heading,
        (
            "  Exact static-candidate machine-recipe multiset · "
            f"+{added_count} / -{removed_count}"
        ),
    ]
    lines.extend(_render_recipe_modifications(modifications))
    lines.extend(_render_recipe_delta_side("Removed", "-", remaining_removed))
    lines.extend(_render_recipe_delta_side("Added", "+", remaining_added))
    if modifications:
        lines.append(
            f"  Pairing        {len(modifications)} unambiguous one-to-one "
            "property-only modification(s); unmatched or ambiguous rows remain "
            "independent multiset deltas."
        )
    else:
        lines.append(
            "  Pairing        none; no unambiguous one-to-one property-only "
            "correspondence was inferred."
        )
    lines.extend(_render_gregtech_recipe_removal_review(report))
    lines.extend(
        [
            (
                "  Boundary       static-candidate only; compiler, runtime, and effective "
                "registry state are not observed."
            ),
        ]
    )
    return lines


def _recipe_review_counts(
    report: Mapping[str, Any],
) -> tuple[int, int, int, int, int, bool] | None:
    baseline = report.get("baseline")
    candidate = report.get("candidate")
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        return None

    removed = _recipe_multiset_delta(baseline, candidate)
    added = _recipe_multiset_delta(candidate, baseline)
    modifications, remaining_removed, remaining_added = _pair_recipe_modifications(
        removed,
        added,
    )
    removal_statements_removed = _gregtech_recipe_removal_delta(
        report,
        "removed_rows",
        "baseline",
    )
    removal_statements_added = _gregtech_recipe_removal_delta(
        report,
        "added_rows",
        "candidate",
    )
    return (
        len(modifications),
        sum(count for _, count in remaining_removed),
        sum(count for _, count in remaining_added),
        sum(count for _, count in removal_statements_removed),
        sum(count for _, count in removal_statements_added),
        bool(report["comparison"]["effects"].get("truncated")),
    )


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _pair_recipe_modifications(
    removed: list[tuple[Mapping[str, Any], int]],
    added: list[tuple[Mapping[str, Any], int]],
) -> tuple[
    list[tuple[Mapping[str, Any], Mapping[str, Any]]],
    list[tuple[Mapping[str, Any], int]],
    list[tuple[Mapping[str, Any], int]],
]:
    """Pair only unique machine recipes whose non-property structure is exact.

    This is a human rendering inference over the existing V1 effects. It is
    intentionally narrower than general recipe correspondence: multiplicity or
    more than one candidate on either side leaves the whole group unpaired.
    """

    removed_by_structure: dict[str, list[int]] = {}
    added_by_structure: dict[str, list[int]] = {}
    for index, (effect, _count) in enumerate(removed):
        key = _recipe_modification_structure_key(effect)
        if key is not None:
            removed_by_structure.setdefault(key, []).append(index)
    for index, (effect, _count) in enumerate(added):
        key = _recipe_modification_structure_key(effect)
        if key is not None:
            added_by_structure.setdefault(key, []).append(index)

    paired_removed: set[int] = set()
    paired_added: set[int] = set()
    modifications: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for key in sorted(set(removed_by_structure) & set(added_by_structure)):
        removed_indexes = removed_by_structure[key]
        added_indexes = added_by_structure[key]
        if len(removed_indexes) != 1 or len(added_indexes) != 1:
            continue
        removed_index = removed_indexes[0]
        added_index = added_indexes[0]
        if removed[removed_index][1] != 1 or added[added_index][1] != 1:
            continue
        before = removed[removed_index][0]
        after = added[added_index][0]
        if _recipe_properties(before) == _recipe_properties(after):
            continue
        paired_removed.add(removed_index)
        paired_added.add(added_index)
        modifications.append((before, after))

    modifications.sort(
        key=lambda row: (
            *_recipe_source_key(row[0]),
            *_recipe_source_key(row[1]),
            str(row[0]["semantic_key"]),
            str(row[1]["semantic_key"]),
        )
    )
    return (
        modifications,
        [row for index, row in enumerate(removed) if index not in paired_removed],
        [row for index, row in enumerate(added) if index not in paired_added],
    )


def _recipe_modification_structure_key(effect: Mapping[str, Any]) -> str | None:
    recipe = effect.get("recipe")
    if not isinstance(recipe, Mapping) or not isinstance(recipe.get("properties"), Mapping):
        return None
    source = effect.get("source")
    source = source if isinstance(source, Mapping) else {}
    lifecycle = effect.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
    reload_state = effect.get("reload")
    reload_state = reload_state if isinstance(reload_state, Mapping) else {}
    recipe_structure = {
        key: value
        for key, value in recipe.items()
        if key not in {"chain_sha256", "line_end", "line_start", "properties"}
    }
    stable_structure = {
        "rule_id": effect.get("rule_id"),
        "category": effect.get("category"),
        "kind": effect.get("kind"),
        "operation": effect.get("operation"),
        "evidence_state": effect.get("evidence_state"),
        "fields": effect.get("fields"),
        "field_states": effect.get("field_states"),
        "identity": effect.get("identity"),
        "expression": effect.get("expression"),
        "source_path": source.get("path"),
        "recipe": recipe_structure,
        "lifecycle": {
            "stage": lifecycle.get("stage"),
            "execution_state": lifecycle.get("execution_state"),
            "conditions": lifecycle.get("conditions"),
        },
        "reload_state": reload_state.get("state"),
    }
    try:
        return json.dumps(stable_structure, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


def _recipe_properties(effect: Mapping[str, Any]) -> Mapping[str, Any]:
    recipe = effect.get("recipe")
    if not isinstance(recipe, Mapping):
        return {}
    properties = recipe.get("properties")
    return properties if isinstance(properties, Mapping) else {}


def _render_recipe_modifications(
    rows: list[tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> list[str]:
    if not rows:
        return ["  Modified none"]

    lines = ["  Modified"]
    visible = rows[:_MAX_RECIPE_ROWS_PER_SIDE]
    for before, after in visible:
        recipe = after.get("recipe")
        recipe = recipe if isinstance(recipe, Mapping) else {}
        lifecycle = after.get("lifecycle")
        lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
        reload_state = after.get("reload")
        reload_state = reload_state if isinstance(reload_state, Mapping) else {}
        fields = after.get("fields")
        fields = fields if isinstance(fields, Mapping) else {}
        recipe_map = recipe.get("recipe_map", fields.get("recipe_map", "unknown"))
        complete = recipe.get("complete")
        complete_text = "true" if complete is True else "false" if complete is False else "unknown"
        lines.append(
            f"    ~ {_clip(recipe_map, 80)} ×1 · {_recipe_source_span(before)} → "
            f"{_recipe_source_span(after)} · complete={complete_text} · "
            f"execution={_clip(lifecycle.get('stage', 'unknown'), 40)}/"
            f"{_clip(lifecycle.get('execution_state', 'unknown'), 40)} · "
            f"reload={_clip(reload_state.get('state', 'unknown'), 60)}"
        )
        lines.append(
            "      properties "
            + _recipe_property_change_summary(
                _recipe_properties(before),
                _recipe_properties(after),
            )
        )
    if len(rows) > len(visible):
        lines.append(
            f"    … {len(rows) - len(visible)} additional property-only modifications; "
            "use --json for the underlying independent effect records"
        )
    return lines


def _recipe_multiset_delta(
    selected_program: Mapping[str, Any],
    other_program: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], int]]:
    selected = _machine_recipe_effects(selected_program)
    other_counts = Counter(str(row["semantic_key"]) for row in _machine_recipe_effects(other_program))
    selected_counts = Counter(str(row["semantic_key"]) for row in selected)
    delta = selected_counts - other_counts
    examples: dict[str, Mapping[str, Any]] = {}
    for effect in selected:
        examples.setdefault(str(effect["semantic_key"]), effect)
    rows = [(examples[key], count) for key, count in delta.items()]
    return sorted(rows, key=lambda row: (*_recipe_source_key(row[0]), str(row[0]["semantic_key"])))


def _machine_recipe_effects(program: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    effects = program.get("effects", [])
    if not isinstance(effects, list):
        return []
    return [
        row
        for row in effects
        if isinstance(row, Mapping)
        and row.get("kind") == "machine-recipe"
        and "semantic_key" in row
    ]


def _render_recipe_delta_side(
    label: str,
    marker: str,
    rows: list[tuple[Mapping[str, Any], int]],
) -> list[str]:
    if not rows:
        return [f"  {label:<7} none"]

    lines = [f"  {label}"]
    visible = rows[:_MAX_RECIPE_ROWS_PER_SIDE]
    for effect, count in visible:
        recipe = effect.get("recipe")
        recipe = recipe if isinstance(recipe, Mapping) else {}
        lifecycle = effect.get("lifecycle")
        lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
        reload_state = effect.get("reload")
        reload_state = reload_state if isinstance(reload_state, Mapping) else {}
        recipe_map = recipe.get("recipe_map", effect.get("fields", {}).get("recipe_map", "unknown"))
        complete = recipe.get("complete")
        complete_text = "true" if complete is True else "false" if complete is False else "unknown"
        stage = lifecycle.get("stage", "unknown")
        execution = lifecycle.get("execution_state", "unknown")
        reload_value = reload_state.get("state", "unknown")
        lines.append(
            f"    {marker} {_clip(recipe_map, 80)} ×{count} · {_recipe_source_span(effect)} · "
            f"complete={complete_text} · execution={_clip(stage, 40)}/{_clip(execution, 40)} "
            f"· reload={_clip(reload_value, 60)}"
        )
        properties = recipe.get("properties")
        if isinstance(properties, Mapping) and properties:
            lines.append(f"      properties {_recipe_property_summary(properties)}")

    if len(rows) > len(visible):
        omitted = rows[len(visible) :]
        omitted_occurrences = sum(count for _, count in omitted)
        lines.append(
            f"    … {len(omitted)} additional signatures / {omitted_occurrences} occurrences; "
            "use --json for full effect records"
        )
    return lines


def _render_gregtech_recipe_removal_review(report: Mapping[str, Any]) -> list[str]:
    removed = _gregtech_recipe_removal_delta(report, "removed_rows", "baseline")
    added = _gregtech_recipe_removal_delta(report, "added_rows", "candidate")
    if not removed and not added:
        return []

    comparison = report.get("comparison")
    comparison = comparison if isinstance(comparison, Mapping) else {}
    effects = comparison.get("effects")
    effects = effects if isinstance(effects, Mapping) else {}
    truncated = effects.get("truncated") is True
    removed_count = sum(count for _, count in removed)
    added_count = sum(count for _, count in added)
    lines = [
        "  Direct GregTech recipe-removal calls",
        (
            "    Profile-classified static source statements shown · "
            f"+{added_count} / -{removed_count}"
            + (" (incomplete; at least these counts)" if truncated else "")
        ),
    ]
    lines.extend(_render_gregtech_recipe_removal_side("Removed", "-", removed))
    lines.extend(_render_gregtech_recipe_removal_side("Added", "+", added))
    lines.append(
        "    Call boundary  rows are source statements, not observed removals; loop "
        "bodies are not expanded and runtime invocation counts are unknown."
    )
    if truncated:
        lines.append(
            "    Coverage       comparison effect rows are truncated; this call view may be incomplete."
        )
    return lines


def _gregtech_recipe_removal_delta(
    report: Mapping[str, Any],
    comparison_rows_name: str,
    program_name: str,
) -> list[tuple[Mapping[str, Any], int]]:
    """Enrich owner removal rows while excluding their integration duplicate."""

    comparison = report.get("comparison")
    comparison = comparison if isinstance(comparison, Mapping) else {}
    comparison_effects = comparison.get("effects")
    comparison_effects = (
        comparison_effects if isinstance(comparison_effects, Mapping) else {}
    )
    comparison_rows = comparison_effects.get(comparison_rows_name)
    if not isinstance(comparison_rows, list):
        return []
    program = report.get(program_name)
    if not isinstance(program, Mapping):
        return []
    effects = program.get("effects")
    if not isinstance(effects, list):
        return []

    examples: dict[str, Mapping[str, Any]] = {}
    for effect in effects:
        if isinstance(effect, Mapping) and "semantic_key" in effect:
            examples.setdefault(str(effect["semantic_key"]), effect)

    result: list[tuple[Mapping[str, Any], int]] = []
    for row in comparison_rows:
        if not isinstance(row, Mapping):
            continue
        # A mods.* removal is also classified by the broad integration rule.
        # Select the profile's removal owner so each source statement renders once.
        if (
            row.get("rule_id") != "groovyscript-mod-removal"
            or row.get("kind") != "mod-registry"
            or row.get("operation") != "remove"
        ):
            continue
        effect = examples.get(str(row.get("semantic_key", "")))
        if effect is None:
            continue
        fields = effect.get("fields")
        fields = fields if isinstance(fields, Mapping) else {}
        adapter_path = str(fields.get("adapter_path", ""))
        method = str(fields.get("method", ""))
        if not adapter_path.startswith("mods.gregtech.") or not method.startswith(
            ("remove", "clear")
        ):
            continue
        count = row.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            continue
        result.append((effect, count))
    return sorted(
        result,
        key=lambda row: (*_effect_source_key(row[0]), str(row[0]["semantic_key"])),
    )


def _render_gregtech_recipe_removal_side(
    label: str,
    marker: str,
    rows: list[tuple[Mapping[str, Any], int]],
) -> list[str]:
    if not rows:
        return [f"    {label:<7} none"]

    lines = [f"    {label}"]
    visible = rows[:_MAX_RECIPE_ROWS_PER_SIDE]
    for effect, count in visible:
        lifecycle = effect.get("lifecycle")
        lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
        reload_state = effect.get("reload")
        reload_state = reload_state if isinstance(reload_state, Mapping) else {}
        expression = effect.get("expression", "unknown-call")
        lines.append(
            f"      {marker} {_clip(expression, _MAX_RECIPE_REMOVAL_EXPRESSION_CHARS)} "
            f"×{count} · {_effect_source_location(effect)} · "
            f"execution={_clip(lifecycle.get('stage', 'unknown'), 40)}/"
            f"{_clip(lifecycle.get('execution_state', 'unknown'), 40)} · "
            f"reload={_clip(reload_state.get('state', 'unknown'), 60)}"
        )
    if len(rows) > len(visible):
        omitted = rows[len(visible) :]
        omitted_occurrences = sum(count for _, count in omitted)
        lines.append(
            f"      … {len(omitted)} additional signatures / "
            f"{omitted_occurrences} source statements; use the complete V1 owner report "
            "for full effect records"
        )
    return lines


def _recipe_source_key(effect: Mapping[str, Any]) -> tuple[str, int, int]:
    source = effect.get("source")
    source = source if isinstance(source, Mapping) else {}
    recipe = effect.get("recipe")
    recipe = recipe if isinstance(recipe, Mapping) else {}
    path = str(source.get("path", ""))
    start = recipe.get("line_start", source.get("line", 0))
    end = recipe.get("line_end", start)
    return (path, start if isinstance(start, int) else 0, end if isinstance(end, int) else 0)


def _effect_source_key(effect: Mapping[str, Any]) -> tuple[str, int, int]:
    source = effect.get("source")
    source = source if isinstance(source, Mapping) else {}
    path = str(source.get("path", ""))
    line = source.get("line", 0)
    offset = source.get("offset", 0)
    return (
        path,
        line if isinstance(line, int) else 0,
        offset if isinstance(offset, int) else 0,
    )


def _recipe_source_span(effect: Mapping[str, Any]) -> str:
    source = effect.get("source")
    source = source if isinstance(source, Mapping) else {}
    recipe = effect.get("recipe")
    recipe = recipe if isinstance(recipe, Mapping) else {}
    path = _clip(source.get("path", "unknown-source"), 160)
    start = recipe.get("line_start", source.get("line"))
    end = recipe.get("line_end", start)
    if isinstance(start, int) and isinstance(end, int) and end != start:
        return f"{path}:{start}-{end}"
    if isinstance(start, int):
        return f"{path}:{start}"
    return path


def _effect_source_location(effect: Mapping[str, Any]) -> str:
    source = effect.get("source")
    source = source if isinstance(source, Mapping) else {}
    path = _clip(source.get("path", "unknown-source"), 160)
    line = source.get("line")
    return f"{path}:{line}" if isinstance(line, int) else path


def _recipe_property_summary(properties: Mapping[str, Any]) -> str:
    names = [name for name in _RECIPE_PROPERTY_ORDER if name in properties]
    names.extend(sorted(str(name) for name in properties if name not in names))
    names = names[:_MAX_RECIPE_PROPERTIES]
    parts: list[str] = []
    for name in names:
        raw_values = properties[name]
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        rendered = [
            _clip(value, _MAX_RECIPE_PROPERTY_VALUE_CHARS)
            for value in values[:_MAX_RECIPE_PROPERTY_VALUES]
        ]
        if len(values) > len(rendered):
            rendered.append(f"… +{len(values) - len(rendered)}")
        parts.append(f"{_clip(name, 40)}={' | '.join(rendered)}")
    if len(properties) > len(names):
        parts.append(f"… +{len(properties) - len(names)} properties")
    return _clip("; ".join(parts), _MAX_RECIPE_PROPERTY_SUMMARY_CHARS)


def _recipe_property_change_summary(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> str:
    all_names = set(str(name) for name in before) | set(str(name) for name in after)
    names = [name for name in _RECIPE_PROPERTY_ORDER if name in all_names]
    names.extend(sorted(all_names - set(names)))
    changes: list[str] = []
    for name in names:
        before_value = (
            _recipe_property_value_summary(before[name]) if name in before else "absent"
        )
        after_value = (
            _recipe_property_value_summary(after[name]) if name in after else "absent"
        )
        if name not in before or name not in after or before[name] != after[name]:
            changes.append(f"{_clip(name, 40)}: {before_value} → {after_value}")
    return _clip("; ".join(changes), _MAX_RECIPE_PROPERTY_SUMMARY_CHARS)


def _recipe_property_value_summary(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    rendered = [
        _clip(item, _MAX_RECIPE_PROPERTY_VALUE_CHARS)
        for item in values[:_MAX_RECIPE_PROPERTY_VALUES]
    ]
    if len(values) > len(rendered):
        rendered.append(f"… +{len(values) - len(rendered)}")
    return " | ".join(rendered)


def _clean(value: object) -> str:
    text = str(value).replace("\x1b", "\\x1b").replace("\r", "\\r").replace("\n", "\\n")
    return _CONTROL_RE.sub(lambda match: f"\\x{ord(match.group(0)):02x}", text)


def _clip(value: object, limit: int) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _short(value: str) -> str:
    return value.rsplit(":", 1)[-1][:16]


__all__ = ["render_report"]
