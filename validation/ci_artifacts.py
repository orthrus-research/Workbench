#!/usr/bin/env python3
"""Select CI uploads from source-Core custody and producer manifests.

This only discovers a completed native assembly. Failed build and validation
diagnostics continue to use their existing always-on CI upload paths.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "tools", ROOT / "api/src", ROOT / "core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from native_distribution import verify as verify_wheelhouse  # noqa: E402
from validation_diagnostics import load_diagnostic_report  # noqa: E402
from workbench_core.storage.registered import ResourceCatalog  # noqa: E402
from workbench_core.user_config_home import default_user_config_home  # noqa: E402


class CiArtifactError(ValueError):
    """The producer did not expose one exact uploadable artifact."""


def native_assembly_upload(
    diagnostics: Path, *, workspace: Path = ROOT,
    configuration_home: Path | None = None,
) -> dict[str, str]:
    """Verify the native producer, Core tree and every manifest-listed wheel."""

    workspace = Path(workspace).resolve(strict=True)
    report = load_diagnostic_report(Path(diagnostics).absolute() / "report.json")
    phases = report["phases"]
    metadata: Any = report.get("metadata")
    if (
        report["lane"] != "native-build"
        or report["state"] != "passed"
        or not isinstance(phases, list)
        or not any(
            isinstance(row, dict) and row.get("name") == "assembly"
            and row.get("state") == "passed" for row in phases
        )
        or not isinstance(metadata, dict)
        or not isinstance(metadata.get("artifact_path"), str)
        or not isinstance(metadata.get("artifact_tree_id"), str)
    ):
        raise CiArtifactError("native build has no completed Core artifact binding")
    path_text = metadata["artifact_path"]
    if any(character in path_text for character in ("\r", "\n", "\x00")):
        raise CiArtifactError("native artifact path cannot be emitted to GitHub output")
    path = Path(path_text)
    if (not path.is_absolute() or not path.is_relative_to(workspace / ".workbench")
            or path != path.resolve(strict=True)):
        raise CiArtifactError("native artifact is outside this checkout's CI custody")
    home = Path(configuration_home or default_user_config_home()).absolute()
    reference = ResourceCatalog(home).trees.describe(
        metadata["artifact_tree_id"], workspace=workspace,
    )
    if (
        reference.path != path
        or reference.owner_id != "native-build"
        or reference.role != "artifacts"
        or reference.derived_status != "current"
    ):
        raise CiArtifactError("native artifact differs from its Core custody reference")
    manifest = verify_wheelhouse(path)
    if any(manifest.get(key) != metadata.get(key) for key in ("source_sha256", "target", "wheels")):
        raise CiArtifactError("native artifact differs from the completed build report")
    manifest_sha256 = sha256((path / "wheelhouse.json").read_bytes()).hexdigest()
    if reference.domain_id != "workbench-native-wheelhouse-v1:sha256:" + manifest_sha256:
        raise CiArtifactError("native artifact domain identity changed")
    return {
        "path": str(path),
        "tree_id": reference.tree_id,
        "manifest_sha256": manifest_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", choices=("native-assembly",))
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args(argv)
    selection = native_assembly_upload(args.diagnostics)
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write("path=" + selection["path"] + "\n")
    print(json.dumps(selection, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
