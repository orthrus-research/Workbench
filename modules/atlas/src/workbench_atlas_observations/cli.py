"""Installed commands over verified family-neutral observation graphs."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence, TextIO

from workbench_api import ExecutionContext
from workbench_atlas_categorical_graph import rebuild_query_index, validate_bundle_directory

from .view import ObservationError, describe_observations, open_observations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workbench atlas observations", description="Search and follow exact retained observations without inferring unobserved behavior.")
    actions = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("context", "describe graph scope, coverage and query readiness"),
        ("search", "find exact observations across families"),
        ("inspect", "inspect one observation and available relationship kinds"),
        ("relationships", "follow recorded incoming or outgoing relationships"),
        ("evidence", "read original evidence references retained by one observation"),
        ("crafting-exposure", "find named recipes retaining a selected crafting native value"),
        ("session", "reuse one verified graph for serial JSONL requests until closed"),
        ("index", "explicitly rebuild disposable query storage"),
        ("import-snapshot", "admit a retained snapshot through the selected profile adapter"),
    ):
        command = actions.add_parser(action, help=help_text)
        command.add_argument("path", type=Path)
        command.add_argument("--json", action="store_true", help="emit the complete versioned record")
        if action == "search":
            command.add_argument("query")
            command.add_argument("--kind")
        if action in {"inspect", "relationships", "evidence", "crafting-exposure"}:
            command.add_argument("selection_id")
        if action == "crafting-exposure":
            command.add_argument("--max-depth", type=int, help="explicit positive traversal depth bound; omitted means finite closure")
            command.add_argument("--max-nodes", type=int, help="explicit positive visited-node bound; omitted means finite closure")
        if action in {"search", "relationships", "evidence"}:
            command.add_argument("--limit", type=int, default=50)
            command.add_argument("--cursor")
        if action == "relationships":
            command.add_argument("--direction", choices=("incoming", "outgoing"), default="outgoing")
            command.add_argument("--relation")
        if action == "evidence":
            command.add_argument("--snapshot", type=Path, help="explicit retained source reopened by the selected profile")
            command.add_argument("--pack-profile", help="profile providing the original-evidence reader")
        if action == "index":
            command.add_argument("--max-source-bytes", type=int, default=8 * 1024**3)
            command.add_argument("--max-index-bytes", type=int, default=4 * 1024**3)
        if action == "import-snapshot":
            command.add_argument("--pack-profile", required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--side", choices=("single", "baseline", "candidate"), default="single")
    return parser


def _node_label(node: dict[str, Any]) -> str:
    label = node["properties"].get("label")
    return label if isinstance(label, str) and label else node["semantic_key"]


def _render(record: dict[str, Any]) -> str:
    context = record.get("context", record)
    lines = [f"Atlas observations: {context.get('graph_set_id', record.get('state', 'complete'))}"]
    if "claim_boundary" in context:
        lines.append(context["claim_boundary"])
    if "selection" in record:
        selected = record["selection"]
        lines.extend([f"{selected['kind']}: {_node_label(selected)}", f"Selection: {selected['id']}"])
    if "query" in record:
        lines.append(f"Search: {record['query']}")
    if record.get("format") == "workbench-atlas-crafting-reference-exposure-v1":
        lines.extend([record["claim_boundary"],
                      f"Traversal: {record['traversal']['state']}; visited nodes: {record['traversal']['visited_nodes']}",
                      f"Stored-reference evidence: {record['evidence']['crafting_reference_inventory']}",
                      f"Recorded recipes: {record['summary']['recorded_recipe_count']} ({record['summary']['status']})"])
    for row in record.get("results", []):
        if "recipe" in row:
            lines.extend([f"Crafting recipe: {_node_label(row['recipe'])}; reference distance: {row['distance']}",
                          f"  {row['recipe']['id']}", "  Witness: " + " → ".join(row["witness"]["node_ids"])])
        elif "edge" in row:
            arrow = "←" if record.get("direction") == "incoming" else "→"
            lines.append(f"{row['edge']['relation']} {arrow} {row['node']['kind']}: {_node_label(row['node'])}")
            lines.append(f"  {row['node']['id']}")
        else:
            lines.extend([f"{row['kind']}: {_node_label(row)}", f"  {row['id']}"])
    if "selection" in record and "relationships" in record:
        lines.append("Properties: " + json.dumps(record["selection"]["properties"], ensure_ascii=False, sort_keys=True))
        lines.append("Relationships: " + json.dumps(record["relationships"], sort_keys=True))
    if "references" in record:
        lines.append("Evidence: " + record["state"] + "; original records: " + record["original_record_resolution"])
        lines.extend(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in record["references"])
        if "resolution" in record:
            lines.append("Original records: " + json.dumps(record["resolution"], ensure_ascii=False, sort_keys=True))
    if "coverage" in context:
        lines.append("Coverage: " + json.dumps(context["coverage"], ensure_ascii=False, sort_keys=True))
    if "query_index" in record:
        lines.append("Query index: " + json.dumps(record["query_index"], sort_keys=True))
    for limitation in context.get("limitations", []):
        lines.append("Limitation: " + limitation)
    for frontier in record.get("frontier", []):
        lines.append(f"Frontier: {frontier['reason']}; {_node_label(frontier['observation'])}; {frontier['observation']['id']}")
    for gap in record.get("evidence_gaps", []):
        lines.append("Evidence gap: " + json.dumps(gap, ensure_ascii=False, sort_keys=True))
    page = record.get("page")
    if page is not None:
        lines.append(f"Page: {page['returned']} record(s); more: {page['truncated']}")
        if page["next_cursor"]:
            lines.append("Next cursor: " + page["next_cursor"])
    return "\n".join(lines) + "\n"


def _resolve_evidence(record: dict[str, Any], *, snapshot: Path | None, pack_profile: str | None, cancel) -> dict[str, Any]:
    if (snapshot is None) != (pack_profile is None):
        raise ObservationError("original evidence resolution requires both --snapshot and --pack-profile")
    if snapshot is not None and record["references"]:
        from workbench_api.profile_extensions import require_profile_extension
        adapter = require_profile_extension("workbench.observation_graphs", pack_profile)
        if (type(getattr(adapter, "OBSERVATION_GRAPH_API_VERSION", None)) is not int
                or adapter.OBSERVATION_GRAPH_API_VERSION != 1
                or not callable(getattr(adapter, "resolve_evidence", None))):
            raise ObservationError("selected profile has no compatible observation evidence API 1 reader")
        references = record["references"]
        resolution = adapter.resolve_evidence(snapshot, deepcopy(references), check_cancelled=cancel)
        if (type(resolution) is not dict or resolution.get("state") != "resolved"
                or type(resolution.get("snapshot_id")) is not str or not resolution["snapshot_id"]
                or type(resolution.get("records")) is not list or len(resolution["records"]) != len(references)
                or any(type(row) is not dict or "value" not in row or row.get("reference") != reference
                       or reference.get("snapshot_id") != resolution["snapshot_id"]
                       for row, reference in zip(resolution["records"], references))):
            raise ObservationError("profile returned a misbound original-evidence resolution")
        record.update(original_record_resolution="resolved", resolution=resolution)
    return record


def main(argv: Sequence[str] | None = None, *, context: ExecutionContext | None = None,
         output: TextIO | None = None, error: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    output, error = output or sys.stdout, error or sys.stderr
    cancel = context.check_cancelled if context is not None else (lambda: None)
    try:
        cancel()
        if args.action == "session":
            from .session import serve_observation_session
            print("Verifying observation graph...", file=error, flush=True)
            return serve_observation_session(args.path, output=output, check_cancelled=cancel)
        if args.action == "evidence" and ((args.snapshot is None) != (args.pack_profile is None)):
            raise ObservationError("original evidence resolution requires both --snapshot and --pack-profile")
        if args.action == "import-snapshot":
            from workbench_api.profile_extensions import require_profile_extension
            adapter = require_profile_extension("workbench.observation_graphs", args.pack_profile)
            if (type(getattr(adapter, "OBSERVATION_GRAPH_API_VERSION", None)) is not int
                    or adapter.OBSERVATION_GRAPH_API_VERSION != 1
                    or not callable(getattr(adapter, "project_snapshot", None))):
                raise ObservationError("selected profile has no compatible observation graph API 1 adapter")
            record = adapter.project_snapshot(args.path, args.output, side=args.side, check_cancelled=cancel)
            cancel()
            manifest = validate_bundle_directory(args.output, check_cancelled=cancel)
            if (type(record) is not dict or record.get("state") != "complete"
                    or record.get("root") != str(args.output.resolve())
                    or record.get("graph_set_id") != manifest["graph_set_id"]):
                raise ObservationError("profile returned an invalid observation graph projection receipt")
        elif args.action == "context":
            record = describe_observations(args.path, check_cancelled=cancel)
        elif args.action == "index":
            def progress(update: dict[str, Any]) -> None:
                cancel()
                print(f"Atlas observation index: {update.get('phase', 'working')}", file=error)
            manifest = rebuild_query_index(args.path, max_source_bytes=args.max_source_bytes,
                                           max_index_bytes=args.max_index_bytes, progress=progress,
                                           check_cancelled=cancel)
            record = {"format": "workbench-atlas-observation-index-v1", "schema_version": 1,
                      "state": "complete", "graph_set_id": manifest["graph_set_id"],
                      "query_index": manifest["query_index"], "authoritative_graph_evidence_mutated": False}
        else:
            with open_observations(args.path, check_cancelled=cancel) as view:
                if args.action == "search":
                    record = view.search(args.query, kind=args.kind, limit=args.limit, cursor=args.cursor)
                elif args.action == "inspect":
                    record = view.inspect(args.selection_id)
                elif args.action == "relationships":
                    record = view.relationships(args.selection_id, direction=args.direction, relation=args.relation,
                                                limit=args.limit, cursor=args.cursor)
                elif args.action == "crafting-exposure":
                    from .exposure import derive_crafting_reference_exposure
                    record = derive_crafting_reference_exposure(view, args.selection_id,
                                                               max_depth=args.max_depth, max_nodes=args.max_nodes)
                else:
                    record = view.evidence(args.selection_id, limit=args.limit, cursor=args.cursor)
            if args.action == "evidence":
                record = _resolve_evidence(record, snapshot=args.snapshot, pack_profile=args.pack_profile, cancel=cancel)
        cancel()
        if args.json:
            rendered = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        else:
            rendered = _render(record)
        cancel()
        output.write(rendered)
        return 0
    except (ValueError, OSError, sqlite3.Error) as failure:
        print(f"Atlas observations failed: {failure}", file=error)
        return 2
