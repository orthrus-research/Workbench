"""Shared CLI/service composition, with no domain parsing or retained index."""

from __future__ import annotations

import argparse
from workbench_atlas.source_navigation import SourceNavigation
from workbench_pack_program_studio.source_intelligence import (
    build_navigation_declarations,
)
from workbench_project_intelligence.working_tree import capture_source_inputs
from .developer_context import DeveloperContextError, observe_developer_context


def run_source_action(selection, argv):
    parser = argparse.ArgumentParser(prog="context run source")
    commands = parser.add_subparsers(dest="action", required=True)
    search = commands.add_parser("search")
    search.add_argument("text", nargs="?", default="")
    search.add_argument(
        "--kind",
        choices=(
            "recipe",
            "material",
            "quest",
            "quest-line",
            "resource",
            "unsupported",
        ),
    )
    search.add_argument("--limit", type=int, default=50)
    for action in ("inspect", "location", "related"):
        command = commands.add_parser(action)
        command.add_argument("selection")
        if action == "related":
            command.add_argument("--max-depth", type=int, default=2)
            command.add_argument("--max-nodes", type=int, default=100)
            command.add_argument("--max-edges", type=int, default=500)
    options = parser.parse_args(list(argv))
    operation = observe_developer_context(selection)
    inputs = capture_source_inputs(selection.workspace)
    if inputs.observation != operation.as_dict()["source"]:
        raise DeveloperContextError("source changed before declaration normalization")
    declarations = build_navigation_declarations(
        inputs,
        pack_profile=selection.pack_profile,
        platform_profile=selection.platform_profile,
        variant=selection.variant,
    )
    view = SourceNavigation(declarations)
    if options.action == "search":
        result = view.search(options.text, kind=options.kind, limit=options.limit)
    elif options.action == "related":
        result = view.related(
            options.selection,
            max_depth=options.max_depth,
            max_nodes=options.max_nodes,
            max_edges=options.max_edges,
        )
    else:
        result = getattr(view, options.action)(options.selection)
    operation.require_fresh()
    return {
        "format": "workbench-developer-action-v1",
        "operation_id": operation.id,
        "context": operation.as_dict(),
        "exit_code": 0,
        "result": result,
        "owner_record_ref": None,
        "diagnostics": "",
    }
