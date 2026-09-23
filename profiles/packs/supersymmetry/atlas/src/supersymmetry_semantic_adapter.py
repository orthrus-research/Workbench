"""Compatibility imports for the native Supersymmetry semantic adapter."""

from workbench_profile_supersymmetry.semantic_projections import (
    HISTORICAL_PLATFORM_PROFILE_ID,
    PACK_PROFILE_ID,
    SupersymmetrySemanticFixtureError,
    build_fixture_projection,
    load_adapter,
    load_fixture,
    run_acceptance_gate,
)

__all__ = [
    "build_fixture_projection",
    "load_adapter",
    "load_fixture",
    "run_acceptance_gate",
]
