"""Read-only comparison of exact saved source inputs and interpreted declarations.

This is not construction authority. Unrecognized edits remain file differences;
source relationships and diagnostics are never runtime or progression evidence.
"""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json

from workbench_api.source_declarations import (
    key_identity,
    validate_navigation_declarations,
)
from .source_navigation import SourceNavigation

MAX_FILES = 1000
MAX_CHANGES = 1000
MAX_FINDINGS = 1000
MAX_TEXT_BYTES = 256 * 1024
MAX_TOTAL_TEXT = 4 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _semantic(row):
    attributes = deepcopy(row["attributes"])
    navigation = attributes.pop("navigation")
    navigation.pop("location")
    return {
        "attributes": attributes.get("comparison", attributes),
        "navigation": navigation,
        "lifecycle": row["lifecycle"],
    }


def _declarations(feed):
    if feed is None:
        return {}, {}, None
    navigation = SourceNavigation(feed)
    summaries = {
        row["source_declaration_id"]: row for row in navigation.declaration_summaries()
    }
    groups = defaultdict(list)
    for row in feed["declarations"]:
        groups[key_identity(row["semantic_descriptor"])].append(
            {
                "summary": summaries[row["source_declaration_id"]],
                "semantics": _semantic(row),
            }
        )
    return groups, summaries, navigation


def _findings(feed, summaries, navigation):
    if feed is None:
        return []
    findings = []
    seen = set()

    def add(code, message, row):
        identity = _canonical(
            [code, message, row["semantic_key"], row["location"]["path"]]
        )
        if identity not in seen:
            seen.add(identity)
            findings.append(
                {
                    "key": identity,
                    "code": code,
                    "message": message,
                    "severity": "information"
                    if "not an observed runtime" in message
                    else "warning",
                    "location": row["location"],
                    "selection_id": row["selection_id"],
                }
            )

    for row in summaries.values():
        for issue in row["issues"]:
            message = issue if isinstance(issue, str) else _canonical(issue)
            add(message[:200], message[:4000], row)
    # These are exact declared-edge conditions, not reachability claims.
    for row in summaries.values():
        if row["kind"] != "quest":
            continue
        for edge in navigation.inspect(row["selection_id"])["relationships"]:
            if edge["relation"] == "requires-quest" and edge["state"] in {
                "dangling",
                "ambiguous",
            }:
                target = navigation.inspect(edge["target"])["selection"][
                    "semantic_key"
                ]["key"]
                add(
                    "quest-prerequisite-" + edge["state"],
                    "Declared quest prerequisite "
                    + _canonical(target)
                    + " is "
                    + edge["state"]
                    + ".",
                    row,
                )
    by_selection = {row["selection_id"]: row for row in summaries.values()}
    for edge in navigation.describe()["prerequisite_cycles"]["back_edges"]:
        row = by_selection[edge["source"]]
        add(
            "quest-prerequisite-cycle",
            "Declared quest prerequisites contain a cycle; review the prerequisite edges.",
            row,
        )
    return findings


def build_source_review(
    baseline, candidate, *, before_feed=None, after_feed=None, analysis_errors=None
):
    """Both inputs are captured bytes; no live reads, imports of constructors, or writes."""
    if baseline.observation["root_uri"] != candidate.observation["root_uri"]:
        raise ValueError("local review inputs belong to different workspaces")
    for feed, inputs in ((before_feed, baseline), (after_feed, candidate)):
        if feed is not None:
            validate_navigation_declarations(feed, sources=inputs.sources)
            if feed["binding"]["source_observation"] != inputs.observation:
                raise ValueError(
                    "local review declarations belong to different source inputs"
                )
    errors = analysis_errors or {}
    before_sources, after_sources = baseline.sources, candidate.sources
    before_modes, after_modes = dict(baseline.modes), dict(candidate.modes)
    paths = [
        path
        for path in sorted(set(before_sources) | set(after_sources))
        if before_sources.get(path) != after_sources.get(path)
        or before_modes.get(path) != after_modes.get(path)
    ]
    files, text_budget = [], MAX_TOTAL_TEXT
    for path in paths[:MAX_FILES]:
        first, last = before_sources.get(path), after_sources.get(path)
        row = {
            "path": path,
            "state": "added"
            if first is None
            else "removed"
            if last is None
            else "modified",
            "before_sha256": None if first is None else sha256(first).hexdigest(),
            "after_sha256": None if last is None else sha256(last).hexdigest(),
            "before_mode": before_modes.get(path),
            "after_mode": after_modes.get(path),
        }
        for side, raw in (("before", first), ("after", last)):
            row[side + "_text"] = None
            row[side + "_text_state"] = (
                "absent" if raw is None else "over-bound-or-binary"
            )
            if (
                raw is not None
                and len(raw) <= min(MAX_TEXT_BYTES, text_budget)
                and b"\0" not in raw
            ):
                try:
                    row[side + "_text"] = raw.decode("utf-8", "strict")
                    row[side + "_text_state"] = "included"
                    text_budget -= len(raw)
                except UnicodeDecodeError:
                    pass
        files.append(row)
    old, old_summaries, old_nav = _declarations(before_feed)
    new, new_summaries, new_nav = _declarations(after_feed)
    changes = []
    comparison_available = before_feed is not None and after_feed is not None
    if comparison_available:
        for key in sorted(set(old) | set(new)):
            left, right = list(old.get(key, [])), list(new.get(key, []))
            # Cancel equal semantic occurrences as a multiset. Do not identify a
            # changed recipe by nearby line numbers or pretend duplicates are unique.
            buckets = defaultdict(list)
            for row in right:
                buckets[_canonical(row["semantics"])].append(row)
            unmatched = []
            for row in left:
                bucket = buckets[_canonical(row["semantics"])]
                if bucket:
                    bucket.pop()
                else:
                    unmatched.append(row)
            left, right = (
                unmatched,
                [row for bucket in buckets.values() for row in bucket],
            )
            if (left or right) and max(
                len(old.get(key, [])), len(new.get(key, []))
            ) > 1:
                changes.append(
                    {
                        "state": "ambiguous",
                        "before": None,
                        "after": None,
                        "before_semantics": None,
                        "after_semantics": None,
                        "reason": "duplicate-semantic-key; no unique occurrence mapping",
                        "before_candidates": [
                            row["summary"] for row in old.get(key, [])
                        ][:100],
                        "after_candidates": [
                            row["summary"] for row in new.get(key, [])
                        ][:100],
                        "before_count": len(old.get(key, [])),
                        "after_count": len(new.get(key, [])),
                        "unmatched_before_count": len(left),
                        "unmatched_after_count": len(right),
                    }
                )
                continue
            if len(old.get(key, [])) == len(new.get(key, [])) == 1 and left and right:
                pairs = [("modified", left[0], right[0])]
            else:
                pairs = [("removed", row, None) for row in left] + [
                    ("added", None, row) for row in right
                ]
            for state, first, last in pairs:
                changes.append(
                    {
                        "state": state,
                        "before": None if first is None else first["summary"],
                        "after": None if last is None else last["summary"],
                        "before_semantics": None
                        if first is None
                        else first["semantics"],
                        "after_semantics": None if last is None else last["semantics"],
                    }
                )
    previous_findings = _findings(before_feed, old_summaries, old_nav)
    current_findings = _findings(after_feed, new_summaries, new_nav)
    previous_keys = {row["key"] for row in previous_findings}
    current_keys = {row["key"] for row in current_findings}
    findings = [
        {
            **row,
            "state": "uncompared"
            if not comparison_available
            else "persisting"
            if row["key"] in previous_keys
            else "introduced",
        }
        for row in current_findings
    ]
    if comparison_available:
        findings.extend(
            {**row, "state": "resolved"}
            for row in previous_findings
            if row["key"] not in current_keys
        )
    checks = [
        {
            "id": "source-declarations",
            "state": "completed" if comparison_available else "unavailable",
            "details": errors,
        },
        {
            "id": "compiler",
            "state": "not-run",
            "reason": "No exact compiler session was selected; static interpretation is not compilation.",
        },
        {
            "id": "runtime",
            "state": "not-run",
            "reason": "Local review never launches a runtime.",
        },
    ]
    relationships = []
    if new_nav:
        # Bounded contextual links from changed declarations; the native view
        # renders them without recomputing domain relationships.
        for row in changes[:50]:
            if row["after"]:
                relationships.append(
                    new_nav.related(
                        row["after"]["selection_id"],
                        max_depth=2,
                        max_nodes=20,
                        max_edges=30,
                    )
                )
    body = {
        "format": "workbench-local-source-review-v1",
        "baseline": baseline.observation,
        "candidate": candidate.observation,
        "interpretation": {
            "before": None if before_feed is None else before_feed["binding"],
            "after": None if after_feed is None else after_feed["binding"],
        },
        "files": files,
        "changes": changes[:MAX_CHANGES],
        "findings": findings[:MAX_FINDINGS],
        "relationships": relationships,
        "checks": checks,
        "counts": {
            "files": len(paths),
            "changes": len(changes),
            "findings": len(findings),
        },
        "truncated": {
            "files": len(paths) > MAX_FILES,
            "changes": len(changes) > MAX_CHANGES,
            "findings": len(findings) > MAX_FINDINGS,
            "relationships": len(changes) > 50,
        },
        "authority": {
            "source_mutated": False,
            "construction_authorized": False,
            "runtime_authority": "none",
            "qualification_granted": False,
        },
        "limitations": [
            "Saved source only; unsaved buffers are not included.",
            "Unrecognized edits remain file changes; no semantic change is not proof of no behavior change.",
            "Changed semantic keys are additions/removals, not established cross-snapshot recipe identity.",
            "Declared relationships do not prove runtime registration, progression, or reachability.",
        ],
    }
    # Bound the whole transport, not only each collection. Text can expand
    # under JSON escaping, and a material's semantic record can be large.
    for field in ("relationships", "changes", "findings"):
        while len(_canonical(body).encode()) > 8 * 1024 * 1024 and body[field]:
            body[field] = body[field][: len(body[field]) // 2]
            body["truncated"][field] = True
    if len(_canonical(body).encode()) > 8 * 1024 * 1024:
        for row in body["files"]:
            for side in ("before", "after"):
                if row[side + "_text_state"] == "included":
                    row[side + "_text"] = None
                    row[side + "_text_state"] = "transport-bound"
    body["review_id"] = (
        "source-review:sha256:" + sha256(_canonical(body).encode()).hexdigest()
    )
    return body
