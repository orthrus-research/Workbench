#!/usr/bin/env python3
"""Inventory every bounded GTCEu 2.8.10 worldgen effect and integration candidate."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_gtceu_worldgen_impact import (  # noqa: E402
    GtceuWorldgenImpactValidationError,
    build_gtceu_worldgen_impact_inventory,
    write_gtceu_worldgen_impact_inventory,
)


def require_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    operational = (ROOT / ".workbench").resolve(strict=True)
    try:
        resolved.relative_to(operational)
    except ValueError as exc:
        raise GtceuWorldgenImpactValidationError(
            f"impact inventory output must be inside {operational}: {resolved}"
        ) from exc
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument(
        "--config-root",
        type=Path,
        required=True,
        help="The config/gregtech directory containing gregtech.cfg and worldgen/.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help="Optional exact runtime root for mod, mixin, config, and GroovyScript context.",
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
    report = build_gtceu_worldgen_impact_inventory(
        jar_path=args.jar,
        config_root=args.config_root,
        runtime_root=args.runtime_root,
        strataview_path=args.strataview,
    )
    write_gtceu_worldgen_impact_inventory(output, report)
    print(f"impact inventory: {output}")
    print(f"effects: {report['summary']['effect_count']}")
    print(f"ore definitions: {report['summary']['ore_definition_count']}")
    print(
        "bedrock-fluid definitions: "
        f"{report['summary']['bedrock_fluid_definition_count']}"
    )
    candidates = report["summary"]["runtime_integration_candidate_count"]
    if candidates is not None:
        print(f"runtime integration candidates: {candidates}")
    print(report["inventory_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, GtceuWorldgenImpactValidationError) as exc:
        print(f"GTCEu worldgen impact inventory failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
