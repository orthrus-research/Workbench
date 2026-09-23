"""Plain Process Studio route over Atlas-owned runtime recipe comparison.

Process Studio is a workflow composition, not another recipe database.  This
module deliberately delegates the complete comparison to Atlas and forwards
its human or JSON output unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence, TextIO

from .atlas_recipe_cli import main as atlas_recipe_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench process recipes compare",
        description=(
            "Compare finite recipes in two explicit Atlas runtime graphs. Atlas "
            "remains the evidence authority; this bounded comparison does not "
            "prove causality, balance, execution, or global reachability."
        ),
    )
    parser.add_argument("baseline", type=Path, help="earlier explicit Atlas graph")
    parser.add_argument("candidate", type=Path, help="later explicit Atlas graph")
    parser.add_argument(
        "--max-recipes",
        type=int,
        default=100_000,
        help="maximum finite recipes scanned per graph (default: 100000)",
    )
    parser.add_argument(
        "--max-recipe-deltas",
        type=int,
        default=500,
        help="maximum exact signature delta details (default: 500)",
    )
    parser.add_argument(
        "--max-resources",
        type=int,
        default=500,
        help="maximum resource-flow delta details (default: 500)",
    )
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--max-nodes", type=int, default=2000)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete unchanged Atlas runtime comparison record",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | str,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    """Delegate one exact Process Studio recipe flow to Atlas."""

    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    forwarded = [
        "compare-runtime",
        str(args.baseline),
        str(args.candidate),
        "--max-recipes",
        str(args.max_recipes),
        "--max-recipe-deltas",
        str(args.max_recipe_deltas),
        "--max-resources",
        str(args.max_resources),
        "--max-depth",
        str(args.max_depth),
        "--max-nodes",
        str(args.max_nodes),
    ]
    if args.json:
        forwarded.append("--json")
    return atlas_recipe_main(
        forwarded,
        suite_root=root,
        output=output,
        error=error,
    )


__all__ = ["build_parser", "main"]
