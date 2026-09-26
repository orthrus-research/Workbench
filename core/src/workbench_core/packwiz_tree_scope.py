"""Direct Shell runtime composition for Core-cataloged Packwiz fixtures."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from workbench_api.managed_trees import managed_trees_bound, managed_trees_scope
from workbench_api.state_paths import default_suite_state_root

from .managed_trees import CoreManagedTrees
from .user_config_home import default_user_config_home
from .host_services import direct_packwiz_scratch_scope


@contextmanager
def direct_packwiz_tree_scope(
    *, workspace: Path, suite_root: Path | None = None,
    state_root: Path | None = None, configuration_home: Path | None = None,
) -> Iterator[None]:
    if managed_trees_bound():
        yield
        return
    if not isinstance(workspace, Path) or not workspace.is_absolute():
        raise ValueError("Packwiz tree custody requires an absolute workspace")
    selected_home = configuration_home or default_user_config_home()
    if not isinstance(selected_home, Path) or not selected_home.is_absolute():
        raise ValueError("Packwiz tree custody requires an absolute configuration home")
    if state_root is None:
        if not isinstance(suite_root, Path) or not suite_root.is_absolute():
            raise ValueError("Packwiz tree custody requires an absolute suite root")
        selected_state = default_suite_state_root(suite_root)
    else:
        if not isinstance(state_root, Path) or not state_root.is_absolute():
            raise ValueError("Packwiz tree custody requires an absolute state root")
        selected_state = state_root
    with managed_trees_scope(CoreManagedTrees(
        workspace=workspace,
        configuration_home=selected_home,
        locations={"artifacts": workspace, "evidence": selected_state / "evidence"},
        owner_id="workbench-shell",
        location_sources={"artifacts": "direct-workspace", "evidence": "direct-state"},
    )):
        yield


@contextmanager
def direct_packwiz_custody_scope(
    *, workspace: Path, suite_root: Path,
    state_root: Path | None = None, configuration_home: Path | None = None,
) -> Iterator[None]:
    """Bind the direct runtime's source lease and result catalog together."""

    with direct_packwiz_scratch_scope(configuration_home=configuration_home), direct_packwiz_tree_scope(
        workspace=workspace, suite_root=suite_root,
        state_root=state_root, configuration_home=configuration_home,
    ):
        yield


__all__ = ["direct_packwiz_custody_scope", "direct_packwiz_tree_scope"]
