#!/usr/bin/env python3
"""Assemble the exact-runtime-v2 Worldgen Observatory final proof receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import CaptureValidationError  # noqa: E402
from workbench_crucible_observatory.exact_runtime_proof import (  # noqa: E402
    assemble_exact_runtime_proof,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="absolute path to the closed, content-addressed proof spec",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        proof = assemble_exact_runtime_proof(arguments.spec)
    except (CaptureValidationError, OSError) as exc:
        print(f"Worldgen Observatory proof assembly failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "proof_id": proof["proof_id"],
                "proof_state": proof["proof_state"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
