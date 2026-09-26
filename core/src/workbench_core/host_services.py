"""Compose concrete host services at process entry, before worker startup."""

from workbench_api.host_filesystem import bind_host_filesystem
from workbench_api.processes import bind_process_host
from workbench_api.sandboxes import bind_sandbox_host
from workbench_api.verified_artifacts import bind_verified_artifact_host
from . import axiom_sandbox, host_filesystem, tool_process, verified_artifact_host


def install_local_host_services() -> None:
    bind_host_filesystem(host_filesystem)
    bind_process_host(tool_process)
    bind_sandbox_host(axiom_sandbox)
    bind_verified_artifact_host(verified_artifact_host.HOST)
