"""Compose concrete host services at process entry, before worker startup."""

from pathlib import Path

from workbench_api.working_allocations import WorkingAllocations

from workbench_api.host_filesystem import bind_host_filesystem
from workbench_api.processes import bind_process_host
from workbench_api.sandboxes import bind_sandbox_host
from workbench_api.verified_artifacts import bind_verified_artifact_host
from workbench_api.sessions import bind_retained_session_reader
from workbench_api.source_transactions import bind_source_transactions
from workbench_api.source_checkouts import bind_source_checkouts
from . import axiom_sandbox, host_filesystem, live_console_reader, tool_process, verified_artifact_host
from .source_transactions import CoreSourceTransactions
from .source_checkouts import CoreSourceCheckouts


def install_local_host_services() -> None:
    bind_host_filesystem(host_filesystem)
    bind_process_host(tool_process)
    bind_sandbox_host(axiom_sandbox)
    bind_verified_artifact_host(verified_artifact_host.HOST)
    bind_retained_session_reader(live_console_reader.HOST)
    bind_source_transactions(CoreSourceTransactions(owner_id="local-host"))
    bind_source_checkouts(CoreSourceCheckouts())


def resolve_local_working_allocations(
    suite_root: Path, *, owner_id: str,
) -> WorkingAllocations:
    """Compose Core custody for a supported direct module command."""

    from .working_allocations import resolve_direct_working_allocations

    return resolve_direct_working_allocations(suite_root, owner_id=owner_id)
