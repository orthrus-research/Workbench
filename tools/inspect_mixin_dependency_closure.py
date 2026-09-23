#!/usr/bin/env python3

"""Publish a dependency-closure receipt from a strict caller spec."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules/project-intelligence/src"))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactScanError,
    ClassResolutionRequest,
    ClosureArtifactInput,
    DependencyEdgeInput,
    build_dependency_closure_receipt,
    render_dependency_closure_receipt,
)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactScanError(f"closure spec repeats JSON key {key!r}")
        value[key] = item
    return value


def _spec(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ArtifactScanError(f"closure spec is not a regular file: {path}")
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=_unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactScanError(f"cannot read closure spec: {exc}") from exc
    expected = {
        "artifacts", "closure_complete", "dependencies", "format", "requests",
        "schema_version",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("format") != "workbench-mixin-dependency-closure-input-v1"
        or value.get("schema_version") != 1
        or not isinstance(value.get("closure_complete"), bool)
    ):
        raise ArtifactScanError("closure spec has an invalid V1 shape")
    return value


def _records(path: Path, spec: Mapping[str, Any]):
    artifacts: list[ClosureArtifactInput] = []
    for row in spec["artifacts"]:
        if not isinstance(row, dict) or set(row) != {"label", "path", "role"}:
            raise ArtifactScanError("closure artifact declaration is malformed")
        source = Path(row["path"])
        source = source if source.is_absolute() else path.parent / source
        if source.is_symlink() or not source.is_file():
            raise ArtifactScanError(f"closure artifact is not a regular file: {source}")
        artifacts.append(ClosureArtifactInput(row["label"], source.read_bytes(), row["role"]))
    edges = []
    for row in spec["dependencies"]:
        if not isinstance(row, dict) or set(row) != {"relationship", "source", "target"}:
            raise ArtifactScanError("closure dependency declaration is malformed")
        edges.append(DependencyEdgeInput(row["source"], row["target"], row["relationship"]))
    requests = []
    for row in spec["requests"]:
        if not isinstance(row, dict) or set(row) != {"class_name", "purpose", "requester"}:
            raise ArtifactScanError("closure request declaration is malformed")
        requests.append(ClassResolutionRequest(row["requester"], row["class_name"], row["purpose"]))
    return artifacts, edges, requests


def _publish(path: Path, payload: bytes) -> None:
    if path.is_symlink() or not path.parent.is_dir():
        raise ArtifactScanError(f"closure output path is unsafe: {path}")
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inspect-mixin-dependency-closure")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = _spec(args.spec)
        artifacts, edges, requests = _records(args.spec, spec)
        receipt = build_dependency_closure_receipt(
            artifacts, edges, requests,
            closure_complete=spec["closure_complete"],
        )
        _publish(args.output, render_dependency_closure_receipt(receipt, compact=args.compact))
    except (ArtifactScanError, OSError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
