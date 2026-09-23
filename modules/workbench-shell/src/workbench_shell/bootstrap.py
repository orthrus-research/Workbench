"""Compose the current read-only Workbench foundation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workbench_project_intelligence import inspect_workspace

from .component_graph import load_component_graph
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)


REGISTRY_PATH = Path(
    "modules/workbench-shell/data/component-registry-v2.json"
)


def discover_suite_root() -> Path:
    """Find the current source-checkout integration root."""

    for candidate in Path(__file__).resolve().parents:
        if (candidate / REGISTRY_PATH).is_file():
            return candidate
    raise ValueError(
        "Workbench suite root cannot be discovered from this distribution"
    )


def inspect_project(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compose Workbench context for a separately owned project."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).resolve()
    if configuration is not None and config_path is not None:
        raise WorkbenchConfigurationError(
            "configuration and config_path are mutually exclusive"
        )
    active_configuration = configuration or load_workbench_configuration(
        suite,
        CONFIGURATION_PATH if config_path is None else config_path,
    )
    graph = load_component_graph(suite / REGISTRY_PATH, suite)
    clients = tuple(
        component for component in graph.components if component.kind == "client"
    )
    workspace_context = inspect_workspace(
        workspace,
        platform_profile_path=active_configuration.platform_document.source.path,
        pack_profile_path=active_configuration.pack_document.source.path,
        pack_selection=active_configuration.pack_variant,
        platform_profile_bytes=(
            active_configuration.platform_document.source.source_bytes
        ),
        pack_profile_bytes=active_configuration.pack_document.source.source_bytes,
    )
    observed_digests = {
        "pack": workspace_context["pack"]["document_sha256"],
        "platform": workspace_context["platform"]["document_sha256"],
    }
    expected_digests = {
        "pack": active_configuration.pack_document.source.sha256,
        "platform": active_configuration.platform_document.source.sha256,
    }
    changed = sorted(
        name
        for name, expected in expected_digests.items()
        if observed_digests[name] != expected
    )
    if changed:
        raise WorkbenchConfigurationError(
            "selected profile document changed after configuration snapshot: "
            + ", ".join(changed)
        )
    return {
        "format": "workbench-foundation-bootstrap-v2",
        "schema_version": 2,
        "component_registry_id": graph.registry_id,
        "dependency_order": list(graph.dependency_order()),
        "clients": [
            {
                "client_id": client.id.removeprefix("client-"),
                "component_id": client.id,
                "lifecycle": client.state,
            }
            for client in clients
        ],
        "workspace_context": workspace_context,
    }
