#!/usr/bin/env python3

"""Verify one component candidate directory against Descriptor V2."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import component_versions  # noqa: E402


MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024


class ComponentArtifactError(ValueError):
    """A collected candidate differs from its component authority."""


def expected_filenames(
    component_id: str,
) -> tuple[str, ...]:
    """Render the exact ordered artifact family for one component."""

    _authority, components = component_versions.load_authority()
    try:
        component = components[component_id]
    except KeyError as exc:
        raise ComponentArtifactError(
            f"unknown release component: {component_id}"
        ) from exc
    version = component["version"]
    return tuple(
        artifact["filename_template"].format(version=version)
        for artifact in component["artifacts"]
    )


def verify_component_directory(
    component_id: str,
    directory: Path,
) -> tuple[Path, ...]:
    """Require exactly the descriptor-rendered regular files and no extras."""

    directory = directory.expanduser().absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise ComponentArtifactError(
            f"candidate directory is missing or indirect: {directory}"
        )
    expected = expected_filenames(component_id)
    entries = tuple(
        sorted(directory.iterdir(), key=lambda path: path.name.encode("utf-8"))
    )
    actual_names = tuple(path.name for path in entries)
    expected_names = set(expected)
    actual_name_set = set(actual_names)
    if len(actual_names) != len(actual_name_set):  # pragma: no cover - filesystem invariant
        raise ComponentArtifactError("candidate directory repeats an artifact name")
    missing = sorted(expected_names - actual_name_set)
    extra = sorted(actual_name_set - expected_names)
    if missing or extra:
        detail: list[str] = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("extra: " + ", ".join(extra))
        raise ComponentArtifactError(
            f"{component_id} candidate artifact set differs ({'; '.join(detail)})"
        )
    by_name = {path.name: path for path in entries}
    result: list[Path] = []
    for filename in expected:
        artifact = by_name[filename]
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or not 1 <= artifact.stat().st_size <= MAX_ARTIFACT_BYTES
        ):
            raise ComponentArtifactError(
                f"candidate artifact is not one bounded regular file: {filename}"
            )
        result.append(artifact)
    return tuple(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--component",
        required=True,
    )
    parser.add_argument("--directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        artifacts = verify_component_directory(
            arguments.component,
            arguments.directory,
        )
    except (ComponentArtifactError, component_versions.ComponentVersionError, OSError) as exc:
        print(f"component artifact verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"{arguments.component} candidate artifacts verified: "
        + ", ".join(path.name for path in artifacts)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
