"""Shared paths for the relocated Blueprints V1 tests."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from workbench_api.host_filesystem import bind_host_filesystem
from workbench_api.record_stores import record_store_scope
from workbench_api.source_transactions import source_transactions_scope
from workbench_core import host_filesystem
from workbench_core.source_transactions import CoreSourceTransactions
from workbench_core.storage.record_stores import CoreRecordStores


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


@contextmanager
def sealed_store_scope(workspace: Path, configuration_home: Path) -> Iterator[None]:
    """Compose the same Core custody ports used by installed dispatch."""

    bind_host_filesystem(host_filesystem)
    with record_store_scope(CoreRecordStores(
        workspace=workspace,
        configuration_home=configuration_home,
        owner_id="blueprints",
    )), source_transactions_scope(CoreSourceTransactions(owner_id="blueprints")):
        yield
