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

from workbench_profile_cleanroom.fixture_build import ROOT, main  # noqa: E402


if __name__ == "__main__":
    if "--core-supervised" in sys.argv[1:] and "--check-only" not in sys.argv[1:]:
        _core_source = Path(__file__).resolve().parents[4] / "core/src"
        if _core_source.is_dir() and str(_core_source) not in sys.path:
            sys.path.insert(0, str(_core_source))
        try:
            from workbench_api.reusable_fixture_builds import reusable_fixture_builds_scope
            from workbench_core.reusable_fixture_builds import CoreReusableFixtureBuilds
            from workbench_core.user_config_home import default_user_config_home
        except ImportError as exc:
            print(f"Cleanroom fixture build rejected: Core supervisor unavailable: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        with reusable_fixture_builds_scope(CoreReusableFixtureBuilds(
            workspace=ROOT, configuration_home=default_user_config_home(),
        )):
            raise SystemExit(main())
    raise SystemExit(main())
