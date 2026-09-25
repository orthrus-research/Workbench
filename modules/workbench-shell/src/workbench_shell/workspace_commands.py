"""Shell-owned workspace commands."""
from __future__ import annotations

import argparse
from pathlib import Path


from workbench_api.resources import repository_root

ROOT = repository_root(__file__)

def _open_parser(*, default_workspace: Path | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench open",
        description=(
            "Recognize a workspace and show the few useful things Workbench can "
            "truthfully do there now. The command is read-only."
        ),
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        default=Path.cwd() if default_workspace is None else default_workspace,
        help=(
            "workspace or path inside it (defaults to the Core-selected "
            "workspace, or the current directory for direct use)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete workspace Home projection",
    )
    parser.add_argument(
        "--session",
        help="bind Home to one exact Work Session ID (or latest)",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        help="explicit local state base for Work Session lookup",
    )
    return parser

def _open_main(argv: list[str], *, default_workspace: Path | None = None) -> int:
    args = _open_parser(default_workspace=default_workspace).parse_args(argv)
    from workbench_shell.product_spine_cli import home_main

    forwarded = [str(args.workspace)]
    if args.session is not None:
        forwarded.extend(("--session", args.session))
    if args.state_root is not None:
        forwarded.extend(("--state-root", str(args.state_root)))
    if args.json:
        forwarded.append("--json")
    return home_main(forwarded, root=ROOT)
