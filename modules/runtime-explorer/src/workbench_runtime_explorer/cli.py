"""CLI and terminal omnibox for the Workbench Exact Runtime Explorer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Iterable, TextIO

from workbench_api.events import sanitize_terminal

from .model import ExplorerError, ExplorerRecord, ExplorerSource
from .providers import (
    ProviderResult,
    artifact_provider,
    atlas_runtime_provider,
    console_session_provider,
    manuals_provider,
    raw_log_provider,
    receipt_provider,
    workspace_provider,
)
from .query import Explorer, ExplorerRequest, parse_query
from .render import render_entity_json, render_result, render_sources


MAX_INPUTS_PER_KIND = 64
MAX_EXPLORER_SOURCES = 256
MAX_EXPLORER_RECORDS = 500_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench explore",
        description=(
            "Search exact project declarations, Atlas runtime nodes, artifacts, "
            "receipts, console output, and Manuals without changing the target."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typed identities:\n"
            "  mod class member method field mixin transformer registry resource\n"
            "  recipe block blockstate item stack metadata loot biome structure generator machine\n"
            "  config groovy fluid material ore dimension world event capability\n"
            "  profiler coordinate\n\n"
            "Filters (repeatable and usable without free text):\n"
            "  kind owner state profile side source\n\n"
            "Examples:\n"
            "  workbench explore recipe:example:machine --details\n"
            "  workbench explore 'at example.Mod.init(Mod.java:42)'\n"
            "  workbench explore --kind mixin --owner example --json"
        ),
    )
    parser.add_argument(
        "query",
        nargs="*",
        help=(
            "omnibox text; supports filters such as kind:mixin, owner:gtceu, "
            "profile:COMMON_FINAL_STATE, class:pkg.Type, and registry:mod:name"
        ),
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=Path.cwd(),
        help="workspace or source subdirectory to inventory (defaults to current directory)",
    )
    parser.add_argument(
        "--no-project",
        action="store_true",
        help="do not inventory the local project declaration surface",
    )
    parser.add_argument(
        "--runtime-db",
        type=Path,
        help="explicit immutable Atlas runtime-graph query database",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        type=Path,
        default=[],
        help="exact JAR/ZIP to inspect through Project Intelligence; repeatable",
    )
    parser.add_argument(
        "--receipt",
        action="append",
        type=Path,
        default=[],
        help="exact module receipt JSON to search; repeatable",
    )
    parser.add_argument(
        "--session",
        action="append",
        type=Path,
        default=[],
        help="retained console session directory, event JSONL, or local session ID; repeatable",
    )
    parser.add_argument(
        "--log",
        action="append",
        type=Path,
        default=[],
        help="raw build/game log to classify and search; repeatable",
    )
    parser.add_argument(
        "--no-manuals",
        action="store_true",
        help="omit checked-in Manuals teaching links",
    )
    parser.add_argument("--kind", action="append", default=[], help="entity kind filter; repeatable")
    parser.add_argument("--owner", action="append", default=[], help="owner filter; repeatable")
    parser.add_argument("--state", action="append", default=[], help="evidence-state filter; repeatable")
    parser.add_argument("--profile", action="append", default=[], help="exact runtime profile filter; repeatable")
    parser.add_argument("--side", action="append", default=[], help="exact physical-side filter; repeatable")
    parser.add_argument("--source", action="append", default=[], help="source authority/kind filter; repeatable")
    parser.add_argument("--limit", type=int, default=20, help="return 1-100 entities (default: 20)")
    parser.add_argument("--json", action="store_true", help="emit the complete V1 result envelope")
    parser.add_argument("--details", action="store_true", help="show relationships, facets, and limitations")
    parser.add_argument("--sources", action="store_true", help="also print exact source coverage")
    parser.add_argument(
        "--require-observed",
        action="store_true",
        help="return exit 1 unless at least one returned entity has a validated observed facet",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="open the repeatable terminal omnibox even when a query is supplied",
    )
    return parser


def _session_path(root: Path, value: Path) -> Path:
    if value.exists() or value.is_absolute() or len(value.parts) != 1:
        return value
    session_id = value.name
    if re.fullmatch(r"[A-Za-z0-9._-]{8,120}", session_id) is None:
        return value
    return root / ".workbench/sessions/live-console" / session_id


def _extend_unique(
    sources: list[ExplorerSource],
    records: list[ExplorerRecord],
    result: ProviderResult,
) -> None:
    if any(source.source_id == result.source.source_id for source in sources):
        return
    if len(sources) >= MAX_EXPLORER_SOURCES:
        raise ExplorerError(
            f"explorer source count exceeds {MAX_EXPLORER_SOURCES}"
        )
    if len(records) + len(result.records) > MAX_EXPLORER_RECORDS:
        raise ExplorerError(
            f"explorer record count exceeds {MAX_EXPLORER_RECORDS}"
        )
    sources.append(result.source)
    records.extend(result.records)


def _base_inputs(args: argparse.Namespace, *, root: Path) -> tuple[list[ExplorerSource], list[ExplorerRecord]]:
    for label in ("artifact", "receipt", "session", "log"):
        if len(getattr(args, label)) > MAX_INPUTS_PER_KIND:
            raise ExplorerError(
                f"--{label} may be repeated at most {MAX_INPUTS_PER_KIND} times"
            )
    sources: list[ExplorerSource] = []
    records: list[ExplorerRecord] = []
    if not args.no_project:
        _extend_unique(sources, records, workspace_provider(args.project))
    if not args.no_manuals:
        _extend_unique(sources, records, manuals_provider(root))
    if args.artifact:
        _extend_unique(sources, records, artifact_provider(tuple(args.artifact)))
    for receipt in args.receipt:
        _extend_unique(sources, records, receipt_provider(receipt))
    for session in args.session:
        _extend_unique(
            sources,
            records,
            console_session_provider(_session_path(root, session)),
        )
    for log in args.log:
        _extend_unique(
            sources,
            records,
            raw_log_provider(log, workspace=args.project),
        )
    return sources, records


def _request(raw: str, args: argparse.Namespace) -> ExplorerRequest:
    if raw.strip():
        request = parse_query(raw, limit=args.limit)
    else:
        serialized = _filter_only_query(args)
        if not serialized:
            raise ExplorerError("explorer query must contain text or a filter")
        request = ExplorerRequest(
            raw=serialized,
            text="",
            terms=(),
            filters={},
            interpreted=(),
        )
    return request.with_filters(
        kinds=_option_filter_values(args, "kind"),
        owners=_option_filter_values(args, "owner"),
        states=_option_filter_values(args, "state"),
        profiles=_option_filter_values(args, "profile"),
        sides=_option_filter_values(args, "side"),
        sources=_option_filter_values(args, "source"),
        limit=args.limit,
    )


def _option_filter_values(
    args: argparse.Namespace,
    name: str,
) -> tuple[str, ...]:
    return tuple(
        part
        for value in getattr(args, name)
        for part in value.split(",")
    )


def _filter_only_query(args: argparse.Namespace) -> str:
    """Serialize option filters into the same non-evaluating query grammar."""

    tokens: list[str] = []
    for name in ("kind", "owner", "state", "profile", "side", "source"):
        for value in _option_filter_values(args, name):
            tokens.append(shlex.quote(f"{name}:{value}"))
    return " ".join(tokens)


def _runtime_input(
    args: argparse.Namespace,
    request: ExplorerRequest,
) -> ProviderResult | None:
    if args.runtime_db is None:
        return None
    profile_filters = request.filters.get("profile", ())
    side_filters = request.filters.get("side", ())
    profile = profile_filters[0] if len(profile_filters) == 1 else None
    side = side_filters[0] if len(side_filters) == 1 else None
    kinds = tuple(request.filters.get("kind", ()))
    # Canonical runtime node IDs contain ``rg:``.  It is a bounded all-node
    # selector for filter-only queries, not a wildcard interpretation.
    query_text = request.text or "rg:"
    return atlas_runtime_provider(
        args.runtime_db,
        query_text,
        kinds=kinds,
        profile=profile,
        physical_side=side,
        limit=100,
    )


def _search(
    args: argparse.Namespace,
    raw: str,
    *,
    base_sources: list[ExplorerSource],
    base_records: list[ExplorerRecord],
) -> dict[str, object]:
    request = _request(raw, args)
    sources = list(base_sources)
    records = list(base_records)
    runtime = _runtime_input(args, request)
    if runtime is not None:
        _extend_unique(sources, records, runtime)
    explorer = Explorer(sources, records)
    return explorer.search(request)


def _emit(
    result: dict[str, object],
    args: argparse.Namespace,
    *,
    output: TextIO,
) -> None:
    if args.json:
        output.write(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        return
    render_result(result, output, details=args.details)
    if args.sources:
        output.write("\n")
        render_sources(result, output)


def _interactive(
    args: argparse.Namespace,
    initial: str | None,
    *,
    base_sources: list[ExplorerSource],
    base_records: list[ExplorerRecord],
    input_stream: TextIO,
    output: TextIO,
) -> int:
    if not input_stream.isatty():
        raise ExplorerError("interactive explorer requires a terminal input")
    output.write(
        "Exact Runtime Explorer omnibox\n"
        "Type an identity, stack frame, coordinate, or filters. "
        "Commands: :help, :sources, :json N, :quit.\n\n"
    )
    pending = initial
    last_result: dict[str, object] | None = None
    while True:
        if pending is None:
            output.write("explore> ")
            output.flush()
            line = input_stream.readline()
            if line == "":
                output.write("\n")
                return 0
            value = line.strip()
        else:
            value = pending
            pending = None
        if not value:
            continue
        if value in {":quit", ":q", "quit", "exit"}:
            return 0
        if value == ":help":
            output.write(
                "Examples:\n"
                "  example:machine\n"
                "  class:example.ExampleMod\n"
                "  kind:mixin owner:example TargetMixin\n"
                "  at example.ExampleMod.register(ExampleMod.java:42)\n"
                "  0@128,64,-32\n"
                "Typed domains include class/member/Mixin, registry/resource, "
                "recipe/item/loot/world, config/Groovy, profiler, and coordinate.\n"
                "Filters: kind, owner, state, profile, side, source.\n"
                "Use :json N to inspect the complete Nth entity from the last query.\n\n"
            )
            continue
        if value == ":sources":
            source_result = last_result or {
                "sources": [source.public() for source in base_sources]
            }
            render_sources(source_result, output)
            output.write("\n")
            continue
        if value.startswith(":json "):
            if last_result is None:
                output.write("No previous result.\n\n")
                continue
            try:
                selection = int(value.split(maxsplit=1)[1])
                render_entity_json(last_result, selection, output)
            except (ValueError, IndexError):
                output.write("Selection must name a visible result number.\n")
            output.write("\n")
            continue
        try:
            last_result = _search(
                args,
                value,
                base_sources=base_sources,
                base_records=base_records,
            )
            _emit(last_result, args, output=output)
        except ExplorerError as exc:
            output.write(f"Explorer error: {sanitize_terminal(str(exc))}\n")
        output.write("\n")


def main(
    argv: list[str] | None = None,
    *,
    root: Path | None = None,
    input_stream: TextIO | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repository_root = Path.cwd() if root is None else root
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output is None else output
    stderr = sys.stderr if error is None else error
    raw = " ".join(args.query).strip()
    option_filter_query = _filter_only_query(args)
    try:
        base_sources, base_records = _base_inputs(args, root=repository_root)
        if args.interactive or (not raw and not option_filter_query):
            return _interactive(
                args,
                raw or None,
                base_sources=base_sources,
                base_records=base_records,
                input_stream=stdin,
                output=stdout,
            )
        result = _search(
            args,
            raw,
            base_sources=base_sources,
            base_records=base_records,
        )
        _emit(result, args, output=stdout)
        if args.require_observed and result["summary"]["observed_entities"] == 0:
            return 1
        return 1 if result["summary"]["status"] == "not-found" else 0
    except (ExplorerError, OSError, ValueError) as exc:
        stderr.write(
            "Exact Runtime Explorer failed: "
            f"{sanitize_terminal(str(exc))}\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
