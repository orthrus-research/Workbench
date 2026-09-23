#!/usr/bin/env python3

"""Inspect and validate Workbench's declared public repository organization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "api/src", ROOT / "core/src"):
    sys.path.insert(0, str(source))
SHELL_SOURCE = ROOT / "modules/workbench-shell/src"
if str(SHELL_SOURCE) not in sys.path:
    sys.path.insert(0, str(SHELL_SOURCE))

from repository_policy import (  # noqa: E402
    PublicRepositoryError,
    public_repository_summary,
)


def _write_json(value: object) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--json", action="store_true")
    subparsers.add_parser("show")
    args = parser.parse_args(argv)

    try:
        result = public_repository_summary(ROOT)
        if args.command == "show" or args.json:
            _write_json(result)
        else:
            print(
                "public repository record valid: "
                f"{result['destination']}, "
                f"{len(result['release_units'])} release units, "
                f"hosting state {result['hosting_state']}"
            )
        return 0
    except (OSError, PublicRepositoryError, ValueError) as exc:
        print(f"public repository record failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
