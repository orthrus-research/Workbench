#!/usr/bin/env python3
"""Bind and quantify exact GTCEu 2.8.10 worldgen configuration and observations."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_gtceu_worldgen import (  # noqa: E402
    GtceuWorldgenValidationError,
    build_gtceu_worldgen_inventory,
    write_gtceu_worldgen_inventory,
)


def require_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    operational = (ROOT / ".workbench").resolve(strict=True)
    try:
        resolved.relative_to(operational)
    except ValueError as exc:
        raise GtceuWorldgenValidationError(
            f"inventory output must be inside {operational}: {resolved}"
        ) from exc
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument(
        "--config-root",
        type=Path,
        required=True,
        help="The config/gregtech directory containing dimensions.json and worldgen/.",
    )
    parser.add_argument(
        "--strataview",
        type=Path,
        help="Optional exact Strata package for candidate-only material correlation.",
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = require_output(args.out)
    report = build_gtceu_worldgen_inventory(
        jar_path=args.jar,
        config_root=args.config_root,
        strataview_path=args.strataview,
    )
    write_gtceu_worldgen_inventory(output, report)
    print(f"inventory: {output}")
    print(f"definitions: {report['summary']['definition_count']}")
    if report["observation"]:
        print(
            "observed GTCEu blocks: "
            f"{report['observation']['observed_gtceu_block_count']}"
        )
    print(report["inventory_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, GtceuWorldgenValidationError) as exc:
        print(f"GTCEu worldgen inventory failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
