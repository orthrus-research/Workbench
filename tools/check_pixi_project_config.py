#!/usr/bin/env python3
"""Reject Pixi project-local configuration before exact Workbench operations."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
for _source in ("api/src", "core/src"):
    sys.path.insert(0, str(ROOT / _source))
SHELL_SOURCE = ROOT / "modules/workbench-shell/src"
if str(SHELL_SOURCE) not in sys.path:
    sys.path.insert(0, str(SHELL_SOURCE))

from workbench_core.pixi_config_guard import (  # noqa: E402
    PixiProjectConfigError,
    require_project_local_config_absent,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project_root",
        nargs="?",
        type=Path,
        default=ROOT,
        help="Pixi project root; defaults to the Workbench checkout",
    )
    args = parser.parse_args(argv)
    try:
        require_project_local_config_absent(args.project_root)
    except PixiProjectConfigError as error:
        print(f"Workbench Pixi configuration preflight failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
