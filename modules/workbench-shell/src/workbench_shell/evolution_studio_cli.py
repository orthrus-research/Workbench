"""Evolution Studio routing that preserves Atlas recipe comparison authority."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence, TextIO

from .atlas_recipe_cli import main as atlas_recipe_main


def _help_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench evolution",
        description=(
            "Compare two explicit recipe-runtime evidence contexts. Atlas owns "
            "the comparison record, compatibility decision, and claim limits."
        ),
    )
    actions = parser.add_subparsers(dest="action", required=True)
    recipes = actions.add_parser(
        "recipes",
        help="compare finite recipes across two capture-compatible Atlas graphs",
    )
    recipes.add_argument("before_path", type=Path)
    recipes.add_argument("after_path", type=Path)
    recipes.add_argument("--max-recipes", type=int, default=100_000)
    recipes.add_argument("--max-recipe-deltas", type=int, default=500)
    recipes.add_argument("--max-resources", type=int, default=500)
    recipes.add_argument("--max-depth", type=int, default=4)
    recipes.add_argument("--max-nodes", type=int, default=2000)
    recipes.add_argument(
        "--json",
        action="store_true",
        help="emit the unchanged Atlas runtime-recipe-comparison V1 record",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    suite_root: Path | str | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
) -> int:
    """Delegate recipe evolution byte-for-byte to the Atlas public adapter.

    Apart from the action-name translation, arguments, streams, exit status,
    human rendering, JSON format, compatibility result, and limitations all
    remain owned by :mod:`workbench_atlas_recipe_health.cli`.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        _help_parser().parse_args(arguments)
        return 0
    if arguments[0] != "recipes":
        _help_parser().error("Evolution Studio currently supports: recipes")
    if len(arguments) == 1 or arguments[1] in {"-h", "--help"}:
        _help_parser().parse_args(arguments)
        return 0
    return atlas_recipe_main(
        ["compare-runtime", *arguments[1:]],
        suite_root=suite_root,
        output=output,
        error=error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
