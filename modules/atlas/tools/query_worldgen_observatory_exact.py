#!/usr/bin/env python3
"""Run Atlas's exact multi-bundle Worldgen Observatory query suite."""

from __future__ import annotations

from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))
sys.path.insert(0, str(MODULE_ROOT.parent / "crucible" / "src"))

from workbench_atlas_worldgen.exact_suite import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
