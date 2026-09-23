"""Standalone command-line interfaces for Project Intelligence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from typing import Sequence

from .inspector import ProjectInspectionError, inspect_workspace
from .mixin_topology import ArtifactScanError, render_receipt, scan_artifact_paths


def _mixin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench-inspect-mixins",
        description=(
            "Inspect exact JAR/ZIP bytes for Mixin component topology without "
            "loading archive code."
        ),
    )
    parser.add_argument(
        "artifacts",
        metavar="ARTIFACT",
        nargs="+",
        help="regular, non-symlink JAR or ZIP file",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit canonical compact JSON instead of indented JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write atomically to this path instead of standard output",
    )
    return parser


def _project_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench-project-inspect",
        description=(
            "Inspect a project without requiring Workbench files in that project."
        ),
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--platform-profile",
        type=Path,
        required=True,
        help="platform profile supplied by the integrating product",
    )
    parser.add_argument(
        "--pack-profile",
        type=Path,
        required=True,
        help="pack profile supplied by the integrating product",
    )
    parser.add_argument(
        "--pack-selection",
        required=True,
        help="named selection within the supplied pack profile",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact JSON instead of indented JSON",
    )
    return parser


def _write_atomic(path: Path, payload: bytes) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise ArtifactScanError(f"output parent is not a directory: {parent}")
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except OSError as exc:
        raise ArtifactScanError(f"cannot publish output {path}: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def mixin_main(argv: Sequence[str] | None = None) -> int:
    """Inspect exact Mixin artifact bytes; retained for the dedicated tool."""
    parser = _mixin_parser()
    arguments = parser.parse_args(argv)
    try:
        receipt = scan_artifact_paths(arguments.artifacts)
        payload = render_receipt(receipt, compact=arguments.compact)
        if arguments.output is None:
            sys.stdout.buffer.write(payload)
        else:
            _write_atomic(arguments.output, payload)
    except ArtifactScanError as exc:
        parser.error(str(exc))
    return 0


def project_main(argv: Sequence[str] | None = None) -> int:
    """Inspect a workspace against explicitly supplied platform and pack profiles."""
    args = _project_parser().parse_args(argv)
    try:
        result = inspect_workspace(
            args.workspace,
            platform_profile_path=args.platform_profile,
            pack_profile_path=args.pack_profile,
            pack_selection=args.pack_selection,
        )
    except ProjectInspectionError as exc:
        print(f"workbench-project-inspect: {exc}", file=sys.stderr)
        return 2
    json.dump(
        result,
        sys.stdout,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


# Compatibility for tools/inspect_mixin_artifacts.py and existing callers.
main = mixin_main


if __name__ == "__main__":
    raise SystemExit(mixin_main())
