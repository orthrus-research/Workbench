"""Bounded Supersymmetry diagnosis of GT registration signals in latest.log.

The observer recognizes two version-pinned log grammars.  It groups complete
observations by the first Java owner frame after an exact GregTech wrapper
stack.  It does not reconstruct the effective recipe registry or treat log
payloads as stable recipe identities.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
import unicodedata
from typing import Any, Mapping


REPORT_FORMAT = "workbench-supersymmetry-gt-recipe-registration-diagnostic-v1"
COMPARISON_FORMAT = (
    "workbench-supersymmetry-gt-recipe-registration-comparison-v1"
)

MAX_EMITTED_GROUPS = 100
MAX_EXAMPLES_PER_GROUP = 3
MAX_CONTEXTS_PER_GROUP = 100
MAX_EMITTED_FRONTIERS = 50
MAX_DISTINCT_GROUPS = 10_000
MAX_DISTINCT_CONTEXTS_PER_GROUP = 10_000
MAX_COMPLETE_EVENTS = 100_000
MAX_IDENTITY_CHARS = 512
MAX_ATTEMPT_CHARS = 2_048
MAX_STACK_SPAN_LINES = 128
MAX_COMPARISON_GROUPS = MAX_EMITTED_GROUPS * 2

KIND = "gt-recipe-registration"
EMPTY_OUTPUT_REASON = "empty-recipe-outputs"
DUPLICATE_FURNACE_REASON = "duplicate-furnace-recipe"
SUMMARY_REASON = "invalid-recipe-summary"
STARTUP_BOUNDARY_REASON = "startup-registration-boundary"

_LOG_ROW_RE = re.compile(
    r"^\[[^]]+\] "
    r"\[(?P<context>[^]]+)/(?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\] "
    r"\[(?P<logger>[^]]+)\]: (?P<message>.*)$"
)
_STACK_FRAME_RE = re.compile(
    r"^\s+at "
    r"(?P<class>[A-Za-z_$][A-Za-z0-9_$]*"
    r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)\."
    r"(?P<method>[A-Za-z_$<][A-Za-z0-9_$<>]*)"
    r"\((?P<source>[A-Za-z0-9_$.-]+):(?P<line>[1-9][0-9]{0,8})\)$"
)
_EMPTY_CONTEXT_RE = re.compile(
    r"^Error happened during processing ore registration of prefix "
    r"(?P<ore_prefix>[A-Za-z][A-Za-z0-9_]{0,127}/[0-9]{1,10}) "
    r"and material (?P<material>[a-z0-9_.-]+(?:/[a-z0-9_.-]+)*)\. "
    r"Seems like cross-mod compatibility issue\. Report to GTCEu github\.$"
)
_FLOAT_RE = r"-?(?:NaN|Infinity|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?)"
_DUPLICATE_RE = re.compile(
    r"^java\.lang\.IllegalArgumentException: "
    r"Tried to register duplicate Furnace Recipe: "
    r"(?P<input_count>[1-9][0-9]*)x "
    r"(?P<input_namespace>[a-z0-9_.-]+):(?P<input_display>.+?) -> "
    r"(?P<output_count>[1-9][0-9]*)x "
    r"(?P<output_namespace>[a-z0-9_.-]+):(?P<output_display>.+), "
    rf"(?P<experience>{_FLOAT_RE})exp$"
)

_EMPTY_HEADER = "Invalid amount of recipe outputs. Recipe outputs are empty."
_STARTUP_HEADER = "Registering recipes..."
_STACKTRACE_HEADER = "Stacktrace:"
_EMPTY_EXCEPTION = "java.lang.IllegalArgumentException: Invalid number of Outputs"
_INVALID_RECIPE_HEADER = "Invalid Recipe Found"
_DUPLICATE_PREFIX = (
    "java.lang.IllegalArgumentException: "
    "Tried to register duplicate Furnace Recipe: "
)
_INVALID_SUMMARY = "Seems like invalid recipe was found."
_SUMMARY_MESSAGES = (
    _INVALID_SUMMARY,
    "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
    "Ignoring invalid recipes and continuing loading",
    "Some things may lack recipes or have invalid ones, proceed at your own risk",
    "Report to GTCEu GitHub to get more help and fix the problem",
    "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~",
)

_EMPTY_WRAPPERS = (
    ("gregtech.api.recipes.RecipeMap", "postValidateRecipe"),
    ("gregtech.api.recipes.RecipeMap", "addRecipe"),
    ("gregtech.api.recipes.RecipeBuilder", "buildAndRegister"),
)
_FURNACE_WRAPPERS = (
    ("gregtech.api.recipes.ModHandler", "logInvalidRecipe"),
    ("gregtech.api.recipes.ModHandler", "addSmeltingRecipe"),
    ("gregtech.api.recipes.ModHandler", "addSmeltingRecipe"),
)


class GtRecipeRegistrationDiagnosticError(ValueError):
    """The supplied observation cannot support this bounded diagnostic."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _bounded_identity(value: str) -> str | None:
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > MAX_IDENTITY_CHARS
        or any(unicodedata.category(character).startswith("C") for character in candidate)
    ):
        return None
    return candidate


def _bounded_attempt(value: str) -> str | None:
    if (
        not value
        or len(value) > MAX_ATTEMPT_CHARS
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return None
    return value


def _parsed_row(line: str) -> re.Match[str] | None:
    return _LOG_ROW_RE.fullmatch(line.rstrip("\r"))


def _parsed_frame(line: str) -> dict[str, Any] | None:
    match = _STACK_FRAME_RE.fullmatch(line.rstrip("\r"))
    if match is None:
        return None
    owner_class = _bounded_identity(match.group("class"))
    owner_method = _bounded_identity(match.group("method"))
    source_file = _bounded_identity(match.group("source"))
    if owner_class is None or owner_method is None or source_file is None:
        return None
    return {
        "owner_class": owner_class,
        "owner_method": owner_method,
        "source_file": source_file,
        "source_line": int(match.group("line")),
    }


def _same_row(
    value: re.Match[str] | None,
    reference: re.Match[str],
    *,
    level: str,
    logger: str,
    message: str,
) -> bool:
    return bool(
        value is not None
        and value.group("context") == reference.group("context")
        and value.group("level") == level
        and value.group("logger") == logger
        and value.group("message") == message
    )


def _wrapper_matches(
    frame: Mapping[str, Any] | None,
    expected: tuple[str, str],
) -> bool:
    return bool(
        frame is not None
        and frame.get("owner_class") == expected[0]
        and frame.get("owner_method") == expected[1]
    )


def _frontier(*, line: int, reason_code: str, reason: str) -> dict[str, Any]:
    return {
        "kind": "incomplete-gt-recipe-registration-sequence",
        "reason_code": reason_code,
        "line": line,
        "reason": reason,
    }


def _empty_output_event(
    lines: list[str],
    index: int,
    header: re.Match[str],
    window_end: int,
) -> tuple[dict[str, Any] | None, str | None]:
    if index + 1 >= window_end:
        return None, "missing-stacktrace-header"
    stacktrace_header = _parsed_row(lines[index + 1])
    if not _same_row(
        stacktrace_header,
        header,
        level="ERROR",
        logger="GregTech",
        message=_STACKTRACE_HEADER,
    ):
        return None, "stacktrace-header-is-not-the-same-thread-gregtech-error"
    if index + 2 >= window_end:
        return None, "missing-invalid-output-exception"
    if lines[index + 2].rstrip("\r") != _EMPTY_EXCEPTION:
        return None, "invalid-output-exception-is-not-recognized"

    for offset, expected in enumerate(_EMPTY_WRAPPERS, start=3):
        if index + offset >= window_end:
            return None, "missing-invalid-output-wrapper-frame"
        frame = _parsed_frame(lines[index + offset])
        if not _wrapper_matches(frame, expected):
            return None, "invalid-output-wrapper-stack-is-not-recognized"
    owner_index = index + 3 + len(_EMPTY_WRAPPERS)
    if owner_index >= window_end:
        return None, "missing-invalid-output-owner-frame"
    owner = _parsed_frame(lines[owner_index])
    if owner is None or any(
        _wrapper_matches(owner, expected) for expected in _EMPTY_WRAPPERS
    ):
        return None, "invalid-output-owner-frame-is-not-recognized"

    terminal_index: int | None = None
    terminal_row: re.Match[str] | None = None
    for cursor in range(
        owner_index + 1,
        min(window_end, index + MAX_STACK_SPAN_LINES),
    ):
        candidate = _parsed_row(lines[cursor])
        if candidate is not None:
            terminal_index = cursor
            terminal_row = candidate
            break
    if terminal_index is None or terminal_row is None:
        return None, "missing-bounded-ore-registration-context"
    if (
        terminal_row.group("context") != header.group("context")
        or terminal_row.group("level") != "ERROR"
        or terminal_row.group("logger") != "GregTech"
    ):
        return None, "ore-registration-context-is-not-the-same-thread-gregtech-error"
    context = _EMPTY_CONTEXT_RE.fullmatch(terminal_row.group("message"))
    if context is None:
        return None, "ore-registration-context-is-not-recognized"
    ore_prefix = _bounded_identity(context.group("ore_prefix"))
    material = _bounded_identity(context.group("material"))
    if ore_prefix is None or material is None:
        return None, "ore-registration-context-exceeds-the-identity-bound"
    return {
        "kind": KIND,
        "reason_code": EMPTY_OUTPUT_REASON,
        **owner,
        "line_start": index + 1,
        "line_end": terminal_index + 1,
        "context": {
            "ore_prefix": ore_prefix,
            "material": material,
        },
    }, None


def _duplicate_furnace_event(
    lines: list[str],
    index: int,
    window_end: int,
) -> tuple[dict[str, Any] | None, str | None]:
    if index + 1 >= window_end:
        return None, "missing-duplicate-furnace-exception"
    raw_exception = lines[index + 1].rstrip("\r")
    if not raw_exception.startswith(_DUPLICATE_PREFIX):
        return None, "not-a-duplicate-furnace-sequence"
    duplicate = _DUPLICATE_RE.fullmatch(raw_exception)
    attempt = _bounded_attempt(raw_exception.removeprefix(_DUPLICATE_PREFIX))
    if duplicate is None or attempt is None:
        return None, "duplicate-furnace-exception-is-not-bounded-or-recognized"

    for offset, expected in enumerate(_FURNACE_WRAPPERS, start=2):
        if index + offset >= window_end:
            return None, "missing-duplicate-furnace-wrapper-frame"
        frame = _parsed_frame(lines[index + offset])
        if not _wrapper_matches(frame, expected):
            return None, "duplicate-furnace-wrapper-stack-is-not-recognized"
    owner_index = index + 2 + len(_FURNACE_WRAPPERS)
    if owner_index >= window_end:
        return None, "missing-duplicate-furnace-owner-frame"
    owner = _parsed_frame(lines[owner_index])
    if owner is None or any(
        _wrapper_matches(owner, expected) for expected in _FURNACE_WRAPPERS
    ):
        return None, "duplicate-furnace-owner-frame-is-not-recognized"
    input_namespace = _bounded_identity(duplicate.group("input_namespace"))
    output_namespace = _bounded_identity(duplicate.group("output_namespace"))
    if input_namespace is None or output_namespace is None:
        return None, "duplicate-furnace-namespace-exceeds-the-identity-bound"
    return {
        "kind": KIND,
        "reason_code": DUPLICATE_FURNACE_REASON,
        **owner,
        "line_start": index + 1,
        "line_end": owner_index + 1,
        "context": {
            "input_namespace": input_namespace,
            "output_namespace": output_namespace,
            "attempt_summary": attempt,
        },
    }, None


def _summary_complete(
    lines: list[str],
    index: int,
    first: re.Match[str],
    window_end: int,
) -> bool:
    if index + len(_SUMMARY_MESSAGES) > window_end:
        return False
    for offset, message in enumerate(_SUMMARY_MESSAGES):
        row = _parsed_row(lines[index + offset])
        if not _same_row(
            row,
            first,
            level="FATAL",
            logger="GregTech Core",
            message=message,
        ):
            return False
    return True


def _recommendation(
    *,
    empty_output_count: int,
    duplicate_furnace_count: int,
    incomplete_sequence_count: int,
) -> dict[str, Any]:
    if empty_output_count:
        return {
            "state": "repair-empty-output-registrations",
            "summary": (
                "GregTech rejected recipe registrations with no outputs; review the "
                "largest observed owner-method groups first."
            ),
            "actions": [
                "Inspect each reported owner method and its bounded material contexts.",
                "Reproduce the change in a fresh process and compare it with an explicitly selected completed baseline.",
            ],
        }
    if duplicate_furnace_count:
        return {
            "state": "review-duplicate-furnace-attempts",
            "summary": (
                "GregTech skipped duplicate furnace registration attempts; compare "
                "their owner-method counts with an explicit completed baseline."
            ),
            "actions": [
                "Review newly observed or increased owner-method groups before changing registrations.",
            ],
        }
    if incomplete_sequence_count:
        return {
            "state": "inspect-incomplete-registration-sequences",
            "summary": (
                "Potential target signals were incomplete or an invalid-recipe "
                "summary had no matched empty-output sequence."
            ),
            "actions": [
                "Inspect the retained latest.log at the reported frontier lines.",
                "Capture a complete fresh-process latest.log before comparing counts.",
            ],
        }
    return {
        "state": "no-action-from-this-diagnostic",
        "summary": "No complete target GT registration signal was recognized.",
        "actions": [],
    }


def build_gt_recipe_registration_diagnostic(
    text: str,
    *,
    source: Mapping[str, Any],
    profile: Mapping[str, Any],
    inherited_limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Reduce one verified latest.log into bounded owner-method groups."""

    if not isinstance(text, str):
        raise GtRecipeRegistrationDiagnosticError("latest.log text must be a string")
    if not isinstance(source, Mapping) or not isinstance(profile, Mapping):
        raise GtRecipeRegistrationDiagnosticError(
            "source and profile bindings are required"
        )

    lines = text.splitlines()
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    context_indexes: dict[
        tuple[str, str, str, str],
        dict[tuple[Any, ...], int | None],
    ] = {}
    frontiers: list[dict[str, Any]] = []
    frontier_count = 0
    empty_output_count = 0
    duplicate_furnace_count = 0
    complete_summary_lines: list[int] = []
    observed_event_count = 0

    def add_frontier(value: dict[str, Any]) -> None:
        nonlocal frontier_count
        frontier_count += 1
        if len(frontiers) < MAX_EMITTED_FRONTIERS:
            frontiers.append(value)

    def add_event(event: Mapping[str, Any]) -> None:
        nonlocal observed_event_count
        if observed_event_count >= MAX_COMPLETE_EVENTS:
            raise GtRecipeRegistrationDiagnosticError(
                f"latest.log exceeds the {MAX_COMPLETE_EVENTS}-event diagnostic bound"
            )
        observed_event_count += 1
        key = (
            str(event["kind"]),
            str(event["reason_code"]),
            str(event["owner_class"]),
            str(event["owner_method"]),
        )
        if key not in groups:
            if len(groups) >= MAX_DISTINCT_GROUPS:
                raise GtRecipeRegistrationDiagnosticError(
                    f"latest.log exceeds the {MAX_DISTINCT_GROUPS}-group diagnostic bound"
                )
            groups[key] = {
                "kind": key[0],
                "reason_code": key[1],
                "observed_owner_class": key[2],
                "observed_owner_method": key[3],
                "count": 0,
                "first_line": event["line_start"],
                "last_line": event["line_end"],
                "examples": [],
                "context_count": 0,
                "emitted_context_count": 0,
                "contexts_truncated": False,
                "contexts": [],
            }
            context_indexes[key] = {}
        group = groups[key]
        group["count"] += 1
        group["last_line"] = event["line_end"]
        if len(group["examples"]) < MAX_EXAMPLES_PER_GROUP:
            group["examples"].append({
                "line_start": event["line_start"],
                "line_end": event["line_end"],
                "source_file": event["source_file"],
                "source_line": event["source_line"],
                "context": dict(event["context"]),
            })
        context_value = {
            "source_file": event["source_file"],
            "source_line": event["source_line"],
            "ore_prefix": event["context"].get("ore_prefix"),
            "material": event["context"].get("material"),
            "input_namespace": event["context"].get("input_namespace"),
            "output_namespace": event["context"].get("output_namespace"),
        }
        context_key = tuple(
            context_value[field]
            for field in (
                "source_file",
                "source_line",
                "ore_prefix",
                "material",
                "input_namespace",
                "output_namespace",
            )
        )
        context_index = context_indexes[key]
        if context_key in context_index:
            emitted_index = context_index[context_key]
            if emitted_index is not None:
                group["contexts"][emitted_index]["count"] += 1
        else:
            if len(context_index) >= MAX_DISTINCT_CONTEXTS_PER_GROUP:
                raise GtRecipeRegistrationDiagnosticError(
                    "latest.log exceeds the per-group registration-context bound"
                )
            group["context_count"] += 1
            if len(group["contexts"]) < MAX_CONTEXTS_PER_GROUP:
                emitted_index = len(group["contexts"])
                group["contexts"].append({**context_value, "count": 1})
                group["emitted_context_count"] += 1
                context_index[context_key] = emitted_index
            else:
                group["contexts_truncated"] = True
                context_index[context_key] = None

    checkpoint = source.get("checkpoint")
    checkpoint_id: str | None = None
    checkpoint_marker: str | None = None
    checkpoint_source: str | None = None
    if isinstance(checkpoint, Mapping):
        raw_checkpoint_id = checkpoint.get("id")
        raw_checkpoint_marker = checkpoint.get("marker")
        raw_checkpoint_source = checkpoint.get("source")
        if (
            isinstance(raw_checkpoint_id, str)
            and _bounded_identity(raw_checkpoint_id) == raw_checkpoint_id
        ):
            checkpoint_id = raw_checkpoint_id
        if (
            isinstance(raw_checkpoint_marker, str)
            and _bounded_identity(raw_checkpoint_marker) == raw_checkpoint_marker
        ):
            checkpoint_marker = raw_checkpoint_marker
        if (
            isinstance(raw_checkpoint_source, str)
            and _bounded_identity(raw_checkpoint_source) == raw_checkpoint_source
        ):
            checkpoint_source = raw_checkpoint_source

    evidence = source.get("evidence")
    evidence_label = (
        evidence.get("label") if isinstance(evidence, Mapping) else None
    )

    parsed_rows = [
        (index, row)
        for index, line in enumerate(lines)
        if (row := _parsed_row(line)) is not None
    ]
    startup_lines = [
        index
        for index, row in parsed_rows
        if row.group("level") == "INFO"
        and row.group("logger") == "GregTech"
        and row.group("message") == _STARTUP_HEADER
    ]
    checkpoint_lines = (
        []
        if checkpoint_marker is None
        else [
            index
            for index, row in parsed_rows
            if row.group("message") == checkpoint_marker
        ]
    )
    boundary_binding_valid = bool(
        checkpoint_id is not None
        and checkpoint_marker is not None
        and checkpoint_source == "minecraft-latest-log"
        and evidence_label == "minecraft-latest-log"
    )
    if not boundary_binding_valid:
        add_frontier(_frontier(
            line=1,
            reason_code=STARTUP_BOUNDARY_REASON,
            reason="receipt-checkpoint-binding-is-not-supported",
        ))
    if len(startup_lines) != 1:
        add_frontier(_frontier(
            line=(startup_lines[1] + 1 if len(startup_lines) > 1 else 1),
            reason_code=STARTUP_BOUNDARY_REASON,
            reason=(
                "registering-recipes-boundary-is-repeated"
                if len(startup_lines) > 1
                else "registering-recipes-boundary-is-missing"
            ),
        ))
    if boundary_binding_valid and len(checkpoint_lines) != 1:
        add_frontier(_frontier(
            line=(checkpoint_lines[1] + 1 if len(checkpoint_lines) > 1 else 1),
            reason_code=STARTUP_BOUNDARY_REASON,
            reason=(
                "receipt-checkpoint-boundary-is-repeated"
                if len(checkpoint_lines) > 1
                else "receipt-checkpoint-boundary-is-missing"
            ),
        ))
    boundary_complete = bool(
        boundary_binding_valid
        and len(startup_lines) == 1
        and len(checkpoint_lines) == 1
        and startup_lines[0] < checkpoint_lines[0]
    )
    if (
        boundary_binding_valid
        and len(startup_lines) == 1
        and len(checkpoint_lines) == 1
        and startup_lines[0] >= checkpoint_lines[0]
    ):
        add_frontier(_frontier(
            line=checkpoint_lines[0] + 1,
            reason_code=STARTUP_BOUNDARY_REASON,
            reason="startup-registration-boundaries-are-reversed",
        ))

    index = startup_lines[0] + 1 if boundary_complete else len(lines)
    window_end = checkpoint_lines[0] if boundary_complete else len(lines)
    while index < window_end:
        line = lines[index]
        row = _parsed_row(line)
        if (
            row is not None
            and row.group("level") == "ERROR"
            and row.group("logger") == "GregTech"
            and row.group("message") == _EMPTY_HEADER
        ):
            event, reason = _empty_output_event(lines, index, row, window_end)
            if event is None:
                add_frontier(_frontier(
                    line=index + 1,
                    reason_code=EMPTY_OUTPUT_REASON,
                    reason=str(reason),
                ))
                index += 1
            else:
                add_event(event)
                empty_output_count += 1
                index = int(event["line_end"])
            continue

        if (
            row is not None
            and row.group("level") == "WARN"
            and row.group("logger") == "GregTech"
            and row.group("message") == _INVALID_RECIPE_HEADER
        ):
            if index + 1 >= window_end:
                add_frontier(_frontier(
                    line=index + 1,
                    reason_code=DUPLICATE_FURNACE_REASON,
                    reason="missing-duplicate-furnace-exception",
                ))
                index += 1
                continue
            if lines[index + 1].rstrip("\r").startswith(_DUPLICATE_PREFIX):
                event, reason = _duplicate_furnace_event(
                    lines,
                    index,
                    window_end,
                )
                if event is None:
                    add_frontier(_frontier(
                        line=index + 1,
                        reason_code=DUPLICATE_FURNACE_REASON,
                        reason=str(reason),
                    ))
                    index += 2
                else:
                    add_event(event)
                    duplicate_furnace_count += 1
                    index = int(event["line_end"])
                continue

        if line.rstrip("\r").startswith(_DUPLICATE_PREFIX):
            add_frontier(_frontier(
                line=index + 1,
                reason_code=DUPLICATE_FURNACE_REASON,
                reason="duplicate-furnace-exception-has-no-recognized-header",
            ))
            index += 1
            continue

        if (
            row is not None
            and row.group("level") == "FATAL"
            and row.group("logger") == "GregTech Core"
            and row.group("message") == _INVALID_SUMMARY
        ):
            if _summary_complete(lines, index, row, window_end):
                complete_summary_lines.append(index + 1)
                index += len(_SUMMARY_MESSAGES)
            else:
                add_frontier(_frontier(
                    line=index + 1,
                    reason_code=SUMMARY_REASON,
                    reason="invalid-recipe-summary-is-incomplete-or-unrecognized",
                ))
                index += 1
            continue
        index += 1

    if not empty_output_count:
        for line_number in complete_summary_lines:
            add_frontier(_frontier(
                line=line_number,
                reason_code=SUMMARY_REASON,
                reason="invalid-recipe-summary-has-no-matched-empty-output-sequence",
            ))

    reason_priority = {
        EMPTY_OUTPUT_REASON: 0,
        DUPLICATE_FURNACE_REASON: 1,
    }
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            reason_priority[group["reason_code"]],
            -group["count"],
            group["observed_owner_class"],
            group["observed_owner_method"],
        ),
    )
    emitted_groups = ordered_groups[:MAX_EMITTED_GROUPS]
    complete_signal_count = empty_output_count + duplicate_furnace_count
    recommendation = _recommendation(
        empty_output_count=empty_output_count,
        duplicate_furnace_count=duplicate_furnace_count,
        incomplete_sequence_count=frontier_count,
    )
    if complete_signal_count:
        state = "attention"
    elif frontier_count:
        state = "inconclusive"
    else:
        state = "no-signals-observed"

    limitations = list(inherited_limitations or [])
    limitations.extend([
        "Only the pinned empty-output and duplicate-furnace latest.log grammars are counted.",
        "Signals are counted only between one exact GregTech registration-start row and one later receipt checkpoint row from minecraft-latest-log; incomplete boundaries make the observation inconclusive.",
        "The V3 receipt does not bind the launch-time GTCEu artifact or version; an exact grammar match is observed syntax, not runtime-version or support proof.",
        "Owner class and method are the first observed frames after exact GregTech wrappers; they are attribution hints, not proof of causal ownership.",
        "Source files, source lines, ore-prefix/material labels, namespaces, and furnace descriptions are bounded context and are not group identity.",
        "Furnace descriptions contain localized display names and do not provide stable recipe identity.",
        "Counts describe rejected or skipped registration attempts, not effective-registry contents or distinct recipes.",
        "An unchanged method-level count can mask different attempted registrations.",
        "Absence of these exact signals does not prove recipe health, lookup activity, progression safety, or client visibility.",
    ])
    if len(ordered_groups) > len(emitted_groups):
        limitations.append(
            f"{len(ordered_groups) - len(emitted_groups)} registration group(s) were omitted by the report bound."
        )
    if frontier_count > len(frontiers):
        limitations.append(
            f"{frontier_count - len(frontiers)} incomplete sequence frontier(s) were omitted by the report bound."
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
            "evidence": "minecraft-latest-log",
            "channels": [EMPTY_OUTPUT_REASON, DUPLICATE_FURNACE_REASON],
            "group_identity": [
                "kind",
                "reason_code",
                "observed_owner_class",
                "observed_owner_method",
            ],
            "stack_grammar": "gtceu-2.8.10-observed-registration-v1",
            "startup_boundary": {
                "state": "complete" if boundary_complete else "incomplete",
                "start_message": _STARTUP_HEADER,
                "start_occurrence_count": len(startup_lines),
                "start_line": (
                    startup_lines[0] + 1 if len(startup_lines) == 1 else None
                ),
                "checkpoint_id": checkpoint_id,
                "checkpoint_marker": checkpoint_marker,
                "checkpoint_source": checkpoint_source,
                "evidence_label": evidence_label,
                "checkpoint_occurrence_count": len(checkpoint_lines),
                "checkpoint_line": (
                    checkpoint_lines[0] + 1
                    if len(checkpoint_lines) == 1
                    else None
                ),
            },
        },
        "summary": {
            "complete_signal_count": complete_signal_count,
            "empty_output_count": empty_output_count,
            "duplicate_furnace_count": duplicate_furnace_count,
            "matched_invalid_recipe_summary_count": (
                len(complete_summary_lines) if empty_output_count else 0
            ),
            "unmatched_invalid_recipe_summary_count": (
                0 if empty_output_count else len(complete_summary_lines)
            ),
            "group_count": len(ordered_groups),
            "emitted_group_count": len(emitted_groups),
            "groups_truncated": len(ordered_groups) > len(emitted_groups),
            "incomplete_sequence_count": frontier_count,
            "emitted_frontier_count": len(frontiers),
            "frontiers_truncated": frontier_count > len(frontiers),
        },
        "groups": emitted_groups,
        "frontiers": frontiers,
        "recommendation": recommendation,
        "limitations": sorted(set(limitations)),
    }
    identity = dict(report)
    identity.pop("diagnostic_id")
    report["diagnostic_id"] = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    return report


def _validated_observation(
    value: Mapping[str, Any],
    *,
    diagnostic_profile: Mapping[str, Any],
    label: str,
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise GtRecipeRegistrationDiagnosticError(
            f"{label} registration diagnostic is not an object"
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
        raise GtRecipeRegistrationDiagnosticError(
            f"{label} registration diagnostic has an incompatible identity"
        )
    source = report.get("source")
    scope = report.get("scope")
    summary = report.get("summary")
    groups = report.get("groups")
    frontiers = report.get("frontiers")
    checkpoint = source.get("checkpoint") if isinstance(source, Mapping) else None
    evidence = source.get("evidence") if isinstance(source, Mapping) else None
    startup_boundary = (
        scope.get("startup_boundary") if isinstance(scope, Mapping) else None
    )
    if (
        not isinstance(source, Mapping)
        or not isinstance(source.get("project"), Mapping)
        or not isinstance(source.get("pack_profile"), Mapping)
        or not isinstance(source.get("launch_receipt"), Mapping)
        or not isinstance(source.get("evidence"), Mapping)
        or source["evidence"].get("label") != "minecraft-latest-log"
        or not isinstance(checkpoint, Mapping)
        or not isinstance(checkpoint.get("id"), str)
        or _bounded_identity(checkpoint["id"]) != checkpoint["id"]
        or not isinstance(checkpoint.get("marker"), str)
        or _bounded_identity(checkpoint["marker"]) != checkpoint["marker"]
        or checkpoint.get("source") != "minecraft-latest-log"
        or not isinstance(source.get("launch_id"), str)
        or not source["launch_id"]
        or source.get("launch_outcome") != "checkpoint-reached"
        or not isinstance(source.get("process_observation"), Mapping)
        or source["process_observation"].get("state") != "exited"
        or not isinstance(scope, Mapping)
        or scope.get("evidence") != "minecraft-latest-log"
        or not isinstance(startup_boundary, Mapping)
        or startup_boundary.get("state") != "complete"
        or startup_boundary.get("start_message") != _STARTUP_HEADER
        or startup_boundary.get("start_occurrence_count") != 1
        or startup_boundary.get("checkpoint_occurrence_count") != 1
        or startup_boundary.get("checkpoint_id") != checkpoint["id"]
        or startup_boundary.get("checkpoint_marker") != checkpoint["marker"]
        or startup_boundary.get("checkpoint_source") != checkpoint["source"]
        or startup_boundary.get("evidence_label") != evidence["label"]
        or type(startup_boundary.get("start_line")) is not int
        or type(startup_boundary.get("checkpoint_line")) is not int
        or startup_boundary["start_line"] >= startup_boundary["checkpoint_line"]
        or not isinstance(summary, Mapping)
        or not isinstance(groups, list)
        or not isinstance(frontiers, list)
        or summary.get("incomplete_sequence_count") != 0
        or summary.get("groups_truncated") is not False
        or summary.get("frontiers_truncated") is not False
        or summary.get("emitted_group_count") != len(groups)
        or summary.get("group_count") != len(groups)
        or frontiers
    ):
        raise GtRecipeRegistrationDiagnosticError(
            f"{label} is not a complete, untruncated launch observation"
        )
    indexed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    reason_totals = {EMPTY_OUTPUT_REASON: 0, DUPLICATE_FURNACE_REASON: 0}
    for group in groups:
        if not isinstance(group, Mapping):
            raise GtRecipeRegistrationDiagnosticError(
                f"{label} contains a malformed registration group"
            )
        key = (
            group.get("kind"),
            group.get("reason_code"),
            group.get("observed_owner_class"),
            group.get("observed_owner_method"),
        )
        count = group.get("count")
        if (
            key[0] != KIND
            or key[1] not in reason_totals
            or any(not isinstance(item, str) or not item for item in key)
            or any(_bounded_identity(item) != item for item in key)
            or key in indexed
            or type(count) is not int
            or count <= 0
            or type(group.get("first_line")) is not int
            or group["first_line"] <= 0
        ):
            raise GtRecipeRegistrationDiagnosticError(
                f"{label} contains a malformed registration group"
            )
        indexed[key] = {
            "count": count,
            "first_line": group["first_line"],
        }
        reason_totals[key[1]] += count
    total = sum(item["count"] for item in indexed.values())
    if (
        summary.get("complete_signal_count") != total
        or summary.get("empty_output_count") != reason_totals[EMPTY_OUTPUT_REASON]
        or summary.get("duplicate_furnace_count")
        != reason_totals[DUPLICATE_FURNACE_REASON]
    ):
        raise GtRecipeRegistrationDiagnosticError(
            f"{label} registration totals do not match its groups"
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


def compare_gt_recipe_registration_diagnostics(
    baseline_value: Mapping[str, Any],
    candidate_value: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare method-group counts from two complete launch observations."""

    if (
        not isinstance(profile, Mapping)
        or set(profile) != {"diagnostic_profile", "function", "report_format"}
        or profile.get("function")
        != "compare_gt_recipe_registration_diagnostics"
        or profile.get("report_format") != COMPARISON_FORMAT
        or not isinstance(profile.get("diagnostic_profile"), Mapping)
    ):
        raise GtRecipeRegistrationDiagnosticError(
            "registration comparison profile is incompatible"
        )
    diagnostic_profile = profile["diagnostic_profile"]
    baseline, baseline_report = _validated_observation(
        baseline_value,
        diagnostic_profile=diagnostic_profile,
        label="baseline",
    )
    candidate, candidate_report = _validated_observation(
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
        raise GtRecipeRegistrationDiagnosticError(
            "baseline and candidate are not distinct compatible observations"
        )

    classifications = {
        "newly-observed": 0,
        "increased": 0,
        "decreased": 0,
        "no-longer-observed": 0,
        "same-count": 0,
    }
    rows: list[dict[str, Any]] = []
    for key in sorted(set(baseline) | set(candidate)):
        before = baseline.get(key)
        after = candidate.get(key)
        before_count = 0 if before is None else before["count"]
        after_count = 0 if after is None else after["count"]
        if before_count == 0:
            classification = "newly-observed"
        elif after_count == 0:
            classification = "no-longer-observed"
        elif after_count > before_count:
            classification = "increased"
        elif after_count < before_count:
            classification = "decreased"
        else:
            classification = "same-count"
        classifications[classification] += 1
        rows.append({
            "kind": key[0],
            "reason_code": key[1],
            "observed_owner_class": key[2],
            "observed_owner_method": key[3],
            "classification": classification,
            "baseline_count": before_count,
            "candidate_count": after_count,
            "delta": after_count - before_count,
            "baseline_first_line": None if before is None else before["first_line"],
            "candidate_first_line": None if after is None else after["first_line"],
        })
    if len(rows) > MAX_COMPARISON_GROUPS:
        raise GtRecipeRegistrationDiagnosticError(
            "registration comparison exceeds its complete group bound"
        )
    priority = {
        "newly-observed": 0,
        "increased": 1,
        "decreased": 2,
        "no-longer-observed": 3,
        "same-count": 4,
    }
    reason_priority = {EMPTY_OUTPUT_REASON: 0, DUPLICATE_FURNACE_REASON: 1}
    rows.sort(key=lambda item: (
        priority[item["classification"]],
        reason_priority[item["reason_code"]],
        -abs(item["delta"]),
        item["observed_owner_class"],
        item["observed_owner_method"],
    ))
    baseline_total = sum(item["count"] for item in baseline.values())
    candidate_total = sum(item["count"] for item in candidate.values())
    more = classifications["newly-observed"] + classifications["increased"]
    fewer = classifications["decreased"] + classifications["no-longer-observed"]
    if more:
        state = "more-observed"
        recommendation = {
            "state": "inspect-new-or-increased-owner-groups",
            "summary": (
                "The candidate has newly observed or increased GT registration "
                "owner-method counts."
            ),
            "actions": [
                "Review newly observed empty-output groups before duplicate-furnace groups.",
                "Use retained line references and reproduce once more before attributing the delta to source.",
            ],
        }
    elif fewer:
        state = "fewer-observed"
        recommendation = {
            "state": "no-new-or-increased-owner-group-counts",
            "summary": (
                "No owner-method count was newly observed or increased; some "
                "counts decreased or disappeared."
            ),
            "actions": [],
        }
    else:
        state = "same-counts"
        recommendation = {
            "state": "no-new-or-increased-owner-group-counts",
            "summary": "The two observations have the same owner-method counts.",
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
            "evidence": "minecraft-latest-log",
            "lifecycle": "one-completed-launch-observation-per-side",
            "group_identity": [
                "kind",
                "reason_code",
                "observed_owner_class",
                "observed_owner_method",
            ],
            "baseline_role": "explicit-selected-baseline",
            "candidate_role": "explicit-selected-candidate",
        },
        "summary": {
            "baseline_complete_signal_count": baseline_total,
            "candidate_complete_signal_count": candidate_total,
            "net_signal_count_delta": candidate_total - baseline_total,
            "group_count": len(rows),
            "newly_observed_group_count": classifications["newly-observed"],
            "increased_group_count": classifications["increased"],
            "decreased_group_count": classifications["decreased"],
            "no_longer_observed_group_count": classifications["no-longer-observed"],
            "same_count_group_count": classifications["same-count"],
        },
        "groups": rows,
        "recommendation": recommendation,
        "limitations": [
            "This is a count-only comparison of complete method-level registration groups.",
            "Source file, line, ore-prefix/material, namespace, and localized furnace-description changes are not comparison identity.",
            "Same-count groups do not prove that the same attempted registrations were observed.",
            "No-longer-observed or decreased counts are not proof that a recipe was repaired or became effective.",
            "The launch receipts do not bind source revisions, so source causality remains unbound.",
            "Effective registry contents, lookup activity, progression impact, and client visibility remain unassessed.",
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
    "GtRecipeRegistrationDiagnosticError",
    "REPORT_FORMAT",
    "build_gt_recipe_registration_diagnostic",
    "compare_gt_recipe_registration_diagnostics",
]
