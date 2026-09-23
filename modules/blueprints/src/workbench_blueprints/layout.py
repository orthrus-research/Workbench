"""Repository resource locations for the imported Blueprints V1 engine."""

from pathlib import Path
from workbench_api.resources import module_root, repository_root


PACKAGE_ROOT = Path(__file__).resolve().parent
MODULE_ROOT = module_root(__file__, "blueprints")
WORKBENCH_ROOT = repository_root(__file__)
SCHEMA_ROOT = MODULE_ROOT / "schemas"
CONTRACT_ROOT = MODULE_ROOT / "contracts"
