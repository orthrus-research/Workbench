"""Compose concrete host services at process entry, before worker startup."""

from workbench_api.host_filesystem import bind_host_filesystem
from workbench_api.processes import bind_process_host
from . import host_filesystem, tool_process


def install_local_host_services() -> None:
    bind_host_filesystem(host_filesystem)
    bind_process_host(tool_process)
