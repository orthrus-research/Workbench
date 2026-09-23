#!/usr/bin/env python3
"""Apply a checked GTCEu worldgen overlay only to fresh Workbench storage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_gtceu_worldgen import (  # noqa: E402
    GtceuWorldgenValidationError,
    materialize_overlay,
)


def require_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    operational = (ROOT / ".workbench").resolve(strict=True)
    try:
        resolved.relative_to(operational)
    except ValueError as exc:
        raise GtceuWorldgenValidationError(
            f"overlay output must be inside {operational}: {resolved}"
        ) from exc
    return resolved


def json_object(path: Path, context: str) -> dict:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GtceuWorldgenValidationError(f"cannot read {context}: {exc}") from exc
    if not isinstance(value, dict):
        raise GtceuWorldgenValidationError(f"{context} must be a JSON object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--config-root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--out-config-root",
        type=Path,
        required=True,
        help="Fresh ignored destination ending in config/gregtech by convention.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = require_output(args.out_config_root)
    materialization = materialize_overlay(
        jar_path=args.jar,
        config_root=args.config_root,
        inventory=json_object(args.inventory, "inventory"),
        plan=json_object(args.plan, "overlay plan"),
        output_config_root=output,
    )
    receipt_path = output.parent / "overlay-materialization-v1.json"
    receipt_path.write_text(
        json.dumps(materialization, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"config: {output}")
    print(f"receipt: {receipt_path}")
    print(materialization["materialization_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, GtceuWorldgenValidationError) as exc:
        print(f"GTCEu overlay failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
