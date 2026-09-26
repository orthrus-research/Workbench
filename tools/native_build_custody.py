"""Source-available Core custody for a native wheelhouse build.

The wheelhouse builder and its standalone verifier remain usable before an
installed Workbench exists. This adapter binds a source checkout to Core's
managed-tree service, so the CLI publishes a verified assembly at its selected
destination and records the exact tree in the resource catalog.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
from typing import Callable

from native_distribution import DistributionError, ROOT, verify


def build_managed_assembly(
    output: Path,
    produce: Callable[[Path], dict],
    *,
    workspace: Path = ROOT,
    configuration_home: Path | None = None,
):
    """Build into Core staging and return the manifest and cataloged tree.

    ``produce`` receives a nonexistent path, just like the existing native
    builder. An interrupted or rejected build leaves the staged bytes for
    inspection, without presenting an incomplete destination as an assembly.
    """

    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise DistributionError("build output must be a new directory")
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
        location_sources={"artifacts": "native-build-output"},
        owner_id="native-build",
    )

    def validate(path: Path, expected: dict) -> None:
        if verify(path) != expected:
            raise DistributionError("native wheelhouse changed during Core publication")

    with host.stage("artifacts", output.name, requested_path=output) as stage:
        manifest = produce(stage.path)
        validate(stage.path, manifest)
        manifest_digest = sha256((stage.path / "wheelhouse.json").read_bytes()).hexdigest()
        reference = stage.publish(
            validate=lambda path: validate(path, manifest),
            domain_id=f"workbench-native-wheelhouse-v1:sha256:{manifest_digest}",
        )
    validate(output, manifest)
    if host.describe(reference.tree_id) != reference:
        raise DistributionError("native wheelhouse Core record changed after publication")
    return manifest, reference


__all__ = ["build_managed_assembly"]
