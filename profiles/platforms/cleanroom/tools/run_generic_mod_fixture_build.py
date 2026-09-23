#!/usr/bin/env python3
"""Run the installed Cleanroom profile's exact generic-mod fixture build."""

from pathlib import Path
import sys

# Source-checkout execution of this profile-owned script uses its adjacent src.
# Installed executions resolve the same package through normal installation.
_source = Path(__file__).resolve().parents[1] / "src"
if _source.is_dir() and str(_source) not in sys.path:
    sys.path.insert(0, str(_source))
_api_source = Path(__file__).resolve().parents[4] / "api/src"
if _api_source.is_dir() and str(_api_source) not in sys.path:
    sys.path.insert(0, str(_api_source))

from workbench_profile_cleanroom.fixture_build import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
