"""Source-available Core custody for contributor build directory publication.

The builders and their standalone verifiers remain usable before an installed
Workbench exists. This adapter binds source Core's managed-tree service so a
CLI can publish one verified artifact directory at its selected destination.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable, TypeVar

ROOT = Path(__file__).resolve().parents[1]
Result = TypeVar("Result")


def publish_build_tree(
    output: Path,
    produce: Callable[[Path], Result],
    validate: Callable[[Path, Result], None],
    domain_id: Callable[[Path, Result], str],
    *,
    owner_id: str,
    workspace: Path = ROOT,
    configuration_home: Path | None = None,
):
    """Build into Core staging and return the result and cataloged tree.

    ``produce`` receives a nonexistent path. An interrupted or rejected build
    retains staged bytes for inspection without presenting a complete output.
    """

    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("build output must be a new directory")
    workspace = Path(workspace).resolve(strict=True)
    source_root = Path(__file__).resolve().parents[1]
    for source in (source_root / "api/src", source_root / "core/src"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))

    from workbench_core.managed_trees import CoreManagedTrees
    from workbench_core.user_config_home import default_user_config_home

    home = Path(configuration_home or default_user_config_home()).absolute()
    host = CoreManagedTrees(
        workspace=workspace,
        configuration_home=home,
        locations={"artifacts": output.parent},
        location_sources={"artifacts": "contributor-build-output"},
        owner_id=owner_id,
    )

    with host.stage("artifacts", output.name, requested_path=output) as stage:
        result = produce(stage.path)
        validate(stage.path, result)
        reference = stage.publish(
            validate=lambda path: validate(path, result),
            domain_id=domain_id(stage.path, result),
        )
    validate(output, result)
    if host.describe(reference.tree_id) != reference:
        raise ValueError("build output Core record changed after publication")
    return result, reference


__all__ = ["publish_build_tree"]
