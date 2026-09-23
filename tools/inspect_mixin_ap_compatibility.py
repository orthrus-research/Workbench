#!/usr/bin/env python3

"""Publish a packaged Mixin AP compatibility conformance receipt."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules/project-intelligence/src"))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactInput,
    ArtifactScanError,
    build_ap_compatibility_conformance_receipt,
    render_ap_compatibility_conformance_receipt,
)


def _regular(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ArtifactScanError(f"{label} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactScanError(f"cannot read {label} {path}: {exc}") from exc


def _publish(path: Path, payload: bytes) -> None:
    if path.is_symlink() or not path.parent.is_dir():
        raise ArtifactScanError(f"AP compatibility output path is unsafe: {path}")
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
    parser = argparse.ArgumentParser(prog="inspect-mixin-ap-compatibility")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--allow-nonconformant", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = build_ap_compatibility_conformance_receipt(
            ArtifactInput(args.artifact.name, _regular(args.artifact, "artifact")),
            _regular(args.policy, "policy"),
        )
        _publish(
            args.output,
            render_ap_compatibility_conformance_receipt(
                receipt, compact=args.compact
            ),
        )
    except (ArtifactScanError, OSError, TypeError, KeyError, ValueError) as exc:
        parser.error(str(exc))
    if (
        receipt["summary"]["conformance_state"] == "nonconformant"
        and not args.allow_nonconformant
    ):
        print(
            f"AP compatibility metadata is nonconformant; receipt published to {args.output}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
