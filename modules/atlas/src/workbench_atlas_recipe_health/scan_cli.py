"""Standalone commands for verified completed scans and their stored findings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence, TextIO

from workbench_api import ExecutionContext

from . import completed_scan


_FINDINGS = (
    "missing-producer-candidate", "no-output-use-candidate",
    "both-sides-candidate", "stranded-output-candidate", "structural-cycle",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench atlas scans",
        description="Import and inspect a completed scan with its original scope and stored audit.",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    descriptions = (
        ("import", "verify and import a scan archive into a new directory"),
        ("show", "show a scan's provenance, coverage and local graph path"),
        ("audit", "read or filter stored audit rows without recomputing findings"),
        ("export", "export a verified scan while preserving its original envelope"),
    )
    for action, description in descriptions:
        command = actions.add_parser(action, help=description)
        command.add_argument("path", type=Path, metavar="ARCHIVE" if action == "import" else "SCAN_DIRECTORY")
        command.add_argument("--json", action="store_true", help="emit the complete versioned result")
        if action == "import":
            command.add_argument("--destination", type=Path, required=True, metavar="NEW_DIRECTORY")
        elif action == "export":
            command.add_argument("--output", type=Path, required=True, metavar="NEW_ARCHIVE")
        elif action == "audit":
            command.description = (
                "Show saved audit totals by default. Filters or explicit paging select stored recipe rows. "
                "Filters do not change the original totals or recompute classifications."
            )
            command.add_argument("--summary", action="store_true", help="show totals and matched count without recipe rows")
            command.add_argument("--finding", choices=_FINDINGS, help="select recipes with this stored finding")
            command.add_argument("--lookup-state", choices=("active", "inactive", "unknown"))
            command.add_argument("--text", help="case-insensitive match in saved recipe/map IDs and related item/fluid names")
            command.add_argument("--offset", type=int, help="zero-based offset in matching rows (default: 0)")
            command.add_argument("--limit", type=int, help="recipe rows to return, 0..10000 (default with selection: 100)")
    return parser


def _row_label(row: dict[str, Any]) -> str:
    names = [name for name in row.get("observed_names", []) if isinstance(name, str) and name]
    if names:
        return ", ".join(names)
    maps = [item.get("label") or item.get("semantic_key") for item in row.get("recipe_maps", [])]
    labels = list(dict.fromkeys(name for name in maps if isinstance(name, str) and name))
    return ", ".join(labels) if labels else row["semantic_key"]


def _render(record: dict[str, Any], action: str) -> str:
    domain = record.get("metadata", {}).get("domain", {})
    summary = record.get("summary", domain.get("summary", {}))
    coverage = record.get("coverage", domain.get("coverage", {}))
    scope = domain.get("scope", {})
    titles = {"import": "Imported Atlas scan", "export": "Exported Atlas scan",
              "show": "Atlas completed scan", "audit": "Atlas stored audit"}
    lines = [titles[action], "Historical snapshot; stored findings have not been recomputed."]
    if record.get("manifest_id"):
        lines.append("Scan: " + record["manifest_id"])
    if record.get("graph_path"):
        lines.append("Graph path: " + record["graph_path"])
    if domain.get("original_result_id"):
        lines.append("Original result: " + domain["original_result_id"])
    if scope.get("pack_profile_id") or scope.get("lifecycle_checkpoint_id"):
        lines.append(f"Profile: {scope.get('pack_profile_id', 'unrecorded')}; checkpoint: {scope.get('lifecycle_checkpoint_id', 'unrecorded')}")
    if coverage:
        lines.append(f"Coverage: {coverage.get('status', 'unrecorded')}; recipes: {summary.get('recipe_count', 'unrecorded')}")
    if summary:
        lines.extend([
            "Saved audit totals: "
            f"{summary.get('missing_producer_candidate_count', 'unrecorded')} missing-producer candidates; "
            f"{summary.get('no_output_use_candidate_count', 'unrecorded')} no-output-use candidates; "
            f"{summary.get('both_sides_candidate_count', 'unrecorded')} both-sided candidates.",
            f"Unresolved recipes: {summary.get('unresolved_recipe_count', 'unrecorded')}; "
            f"structural cycle components: {summary.get('cycle_component_count', 'unrecorded')}.",
        ])
    if coverage.get("interpretation"):
        lines.append(coverage["interpretation"])
    if record.get("policy_status"):
        lines.append("Stored audit policy: " + record["policy_status"])
    trust = record.get("trust", {})
    if trust:
        lines.append(f"Integrity: {trust.get('content_integrity', 'unassessed')}; "
                     f"publisher: {trust.get('publisher_authenticity', 'unassessed')}; "
                     f"current target: {trust.get('current_target_match', 'unassessed')}.")
    for row in record.get("items", []):
        findings = ", ".join(row["findings"]) or "no stored finding"
        lines.extend([f"{_row_label(row)} [{row['lookup_state']}]: {findings}",
                      "  " + row["selection_id"]])
    if "page" in record:
        page = record["page"]
        lines.append(f"Stored rows: {page['returned']} returned; {page['total_matching']} matching; offset {page['offset']}.")
        if page["next_offset"] is not None:
            lines.append(f"Next offset: {page['next_offset']}")
    if record.get("archive"):
        lines.append("Archive: " + record["archive"]["path"])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, *, context: ExecutionContext | None = None,
         output: TextIO | None = None, error: TextIO | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    output = sys.stdout if output is None else output
    error = sys.stderr if error is None else error
    if args.action == "audit" and args.summary and (args.offset is not None or args.limit is not None):
        parser.error("--summary cannot be combined with --offset or --limit")
    cancel = None if context is None else context.check_cancelled
    try:
        if cancel is not None:
            cancel()
        if not args.json:
            notice = {
                "import": "Verifying scan archive and captured evidence...",
                "export": "Verifying saved scan before export...",
                "show": "Verifying saved scan and its provenance...",
                "audit": "Verifying saved scan and reading stored findings...",
            }
            print(notice[args.action], file=error, flush=True)
        if args.action == "import":
            record = completed_scan.import_scan(args.path, args.destination, check_cancelled=cancel)
        elif args.action == "export":
            record = completed_scan.export_scan(args.path, args.output, check_cancelled=cancel)
        elif args.action == "show":
            record = completed_scan.show_scan(args.path, check_cancelled=cancel)
        else:
            selected = any(value is not None for value in
                           (args.finding, args.lookup_state, args.text, args.offset, args.limit))
            limit = 0 if args.summary or not selected else 100 if args.limit is None else args.limit
            record = completed_scan.read_cached_recipe_audit(
                args.path, finding=args.finding, lookup_state=args.lookup_state, text=args.text,
                offset=0 if args.offset is None else args.offset, limit=limit,
                check_cancelled=cancel,
            )
        if cancel is not None:
            cancel()
        if args.json:
            output.write(json.dumps(record, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n")
        else:
            output.write(_render(record, args.action))
        return 0
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Atlas scans failed: {exc}", file=error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
