"""Shared paths for the relocated Blueprints V1 tests."""

from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_ROOT = MODULE_ROOT.parents[1]
SOURCE_ROOT = MODULE_ROOT / "src/workbench_blueprints"
SCHEMA_ROOT = MODULE_ROOT / "schemas"
CONTRACT_ROOT = MODULE_ROOT / "contracts"
EXAMPLE_ROOT = MODULE_ROOT / "examples"
FIXTURE_ROOT = MODULE_ROOT / "tests/fixtures/supersymmetry/standards"
LEDGER_PATH = (
    WORKBENCH_ROOT
    / "profiles/packs/supersymmetry/blueprints/allocation/ledger.json"
)
