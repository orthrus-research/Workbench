"""Workbench resource locations for the packaged Atlas implementation."""

from pathlib import Path
from typing import Mapping
from workbench_api.resources import module_root, repository_root


PACKAGE_ROOT = Path(__file__).resolve().parent
MODULE_ROOT = module_root(__file__, "atlas")
WORKBENCH_ROOT = repository_root(__file__)
SCHEMA_ROOT = MODULE_ROOT / "schemas"
LEXICON_SCHEMA_ROOT = SCHEMA_ROOT / "lexicon"
CONTRACT_ROOT = MODULE_ROOT / "contracts"
EXAMPLE_ROOT = MODULE_ROOT / "examples"
DATA_ROOT = MODULE_ROOT / "data"
CATALOG_ROOT = DATA_ROOT / "catalogs"
OBSERVATION_ROOT = DATA_ROOT / "observations"
PRODUCTION_ROOT = DATA_ROOT / "production"
HISTORICAL_SOURCE_LOCK_PATH = (
    DATA_ROOT / "source-locks/supersymmetry-legacy-forge-v3.json"
)


def atlas_state_root(
    *,
    state_root: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Locate mutable Atlas data beneath Core's selected user state root."""

    if state_root is None:
        from workbench_core.environment_resolution import resolve_environment

        state_root = resolve_environment(
            WORKBENCH_ROOT, environment=environment
        ).state_root
    return Path(state_root).expanduser().resolve() / "atlas"


def atlas_source_root(
    *,
    state_root: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    return atlas_state_root(
        state_root=state_root, environment=environment
    ) / "sources/legacy-forge"


def atlas_knowledge_root(
    *,
    state_root: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    return atlas_state_root(
        state_root=state_root, environment=environment
    ) / "knowledge"
