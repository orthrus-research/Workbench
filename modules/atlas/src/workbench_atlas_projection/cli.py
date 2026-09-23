"""First developer-facing semantic check, why, and impact commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from workbench_api.profile_extensions import require_profile_extension

from .projection import AtlasProjectionError
from .queries import explain_why, impact_report


def _profile_adapter(profile: str) -> Any:
    adapter = require_profile_extension("workbench.semantic_projections", profile)
    if (
        type(getattr(adapter, "SEMANTIC_PROJECTION_API_VERSION", None)) is not int
        or adapter.SEMANTIC_PROJECTION_API_VERSION != 1
        or not callable(getattr(adapter, "run_acceptance_gate", None))
        or not callable(getattr(adapter, "build_fixture_projection", None))
    ):
        raise AtlasProjectionError(
            f"profile {profile!r} has no compatible semantic projection API; "
            "install a profile adapter supporting semantic projection API 1"
        )
    return adapter


def _common(parser: argparse.ArgumentParser, *, fixture_required: bool) -> None:
    parser.add_argument("--profile", required=True, help="explicit pack profile")
    parser.add_argument(
        "--fixture",
        required=fixture_required,
        help="profile-owned semantic regression fixture name",
    )
    parser.add_argument("--json", action="store_true", help="emit the complete JSON result")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workbench semantic")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="replay an admitted semantic acceptance gate")
    _common(check, fixture_required=False)
    check.add_argument(
        "--include-next",
        action="store_true",
        help="also replay profile fixtures queued after the first acceptance gate",
    )
    why = commands.add_parser("why", help="trace one identity across all three layers")
    why.add_argument("query")
    _common(why, fixture_required=True)
    impact = commands.add_parser("impact", help="show the bounded semantic blast radius")
    impact.add_argument("query", nargs="?")
    _common(impact, fixture_required=True)
    return parser


def main(argv: list[str] | None = None, *, root: Path) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        adapter = _profile_adapter(args.profile)
        if args.command == "check":
            fixture_names = None if args.fixture is None else [args.fixture]
            result = adapter.run_acceptance_gate(
                root,
                fixture_names=fixture_names,
                include_next=args.include_next,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"Semantic acceptance: {result['summary']['status']}")
                for row in result["fixtures"]:
                    codes = ", ".join(row["actual_diagnostic_codes"]) or "none"
                    print(f"  {row['fixture']}: {row['status']} ({codes})")
            return 0 if result["summary"]["status"] == "pass" else 1
        projection = adapter.build_fixture_projection(root, args.fixture)
        result = (
            explain_why(projection, args.query)
            if args.command == "why"
            else impact_report(projection, args.query)
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "why":
            print(f"{args.query}: {result['conclusion']}")
            print(f"  semantic identity: {result['semantic_id']}")
            for name in ("SOURCE", "RUNTIME", "PLAYABLE"):
                print(f"  {name}: {len(result['layers'][name])} record(s)")
            for diagnostic in result["diagnostics"]:
                print(f"  {diagnostic['code']}: {diagnostic['message']}")
        else:
            print(f"Affected semantics: {result['summary']['affected_semantics']}")
            print(f"Diagnostics: {result['summary']['diagnostics']}")
            for diagnostic in result["diagnostics"]:
                print(f"  {diagnostic['code']}: {diagnostic['message']}")
        return 0
    except (AtlasProjectionError, OSError, ValueError) as exc:
        print(f"Workbench {args.command} failed: {exc}", file=sys.stderr)
        return 2


__all__ = ["main"]
