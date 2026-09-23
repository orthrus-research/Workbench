#!/usr/bin/env python3
"""Run and atomically publish synthetic Worldgen Observatory contract fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory.synthetic_matrix import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    EVIDENCE_CLASS,
    SYNTHETIC_DISCLAIMER,
    build_synthetic_matrix,
    publish_synthetic_matrix,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic synthetic contract fixtures. This does not launch "
            "or observe Cleanroom."
        )
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPOSITORY_ROOT / DEFAULT_OUTPUT_ROOT,
        help="publication root (default: repository .workbench/evidence storage)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    matrix = build_synthetic_matrix()
    destination = publish_synthetic_matrix(matrix, arguments.output_root)
    print(
        json.dumps(
            {
                "matrix_id": matrix.summary["matrix_id"],
                "evidence_class": EVIDENCE_CLASS,
                "disclaimer": SYNTHETIC_DISCLAIMER,
                "overall_outcome": matrix.summary["overall_outcome"],
                "output_directory": str(destination),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
