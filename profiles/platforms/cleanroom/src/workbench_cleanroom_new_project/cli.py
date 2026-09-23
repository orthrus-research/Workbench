"""Thin profile-local CLI for the Cleanroom construction V2 public port."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from workbench_blueprints.application_transaction import canonical_json_bytes

from .construction import (
    apply_cleanroom_mod_construction,
    build_cleanroom_mod_request,
    preview_cleanroom_mod_construction,
    recover_cleanroom_mod_construction,
)


def _record(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("construction plan must be one JSON object")
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="workbench-cleanroom-new-project-v2")
    commands = value.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview")
    preview.add_argument("target")
    preview.add_argument("--suite-root", required=True)
    preview.add_argument(
        "--output-mode", choices=("instructions", "direct-apply"), default="instructions"
    )
    preview.add_argument("--sequence", type=int, default=0)
    apply = commands.add_parser("apply")
    apply.add_argument("plan")
    apply.add_argument("--suite-root", required=True)
    apply.add_argument("--state-root", required=True)
    apply.add_argument("--consent-plan-id", required=True)
    recover = commands.add_parser("recover")
    recover.add_argument("plan")
    recover.add_argument("--suite-root", required=True)
    recover.add_argument("--state-root", required=True)
    return value


def run(argv: Sequence[str]) -> dict[str, Any]:
    arguments = parser().parse_args(list(argv))
    if arguments.command == "preview":
        request = build_cleanroom_mod_request(
            arguments.target,
            output_mode=arguments.output_mode,
            sequence=arguments.sequence,
            allow_direct_apply=arguments.output_mode == "direct-apply",
        )
        return preview_cleanroom_mod_construction(arguments.suite_root, request)
    plan = _record(arguments.plan)
    if arguments.command == "apply":
        return apply_cleanroom_mod_construction(
            arguments.suite_root,
            plan,
            arguments.state_root,
            consent_plan_id=arguments.consent_plan_id,
        )
    return recover_cleanroom_mod_construction(
        arguments.suite_root, plan, arguments.state_root
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(sys.argv[1:] if argv is None else argv)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
