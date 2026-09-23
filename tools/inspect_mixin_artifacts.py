#!/usr/bin/env python3

"""Repository entry point for the Project Intelligence Mixin scanner."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules/project-intelligence/src"))

from workbench_project_intelligence.cli import main  # noqa: E402


raise SystemExit(main())
