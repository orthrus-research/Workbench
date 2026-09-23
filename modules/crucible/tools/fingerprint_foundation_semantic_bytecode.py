#!/usr/bin/env python3
"""Build and compare policy-bound Foundation semantic-bytecode receipts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
)
from workbench_crucible_observatory.semantic_bytecode import (  # noqa: E402
    build_semantic_bytecode_manifest,
    evaluate_semantic_bytecode_manifests,
    load_semantic_bytecode_manifest,
    write_semantic_bytecode_manifest,
)


def _atomic_json(value: object, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(canonical_json_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _labeled_manifest(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label or not raw_path:
        raise argparse.ArgumentTypeError("manifest must be LABEL=PATH")
    return label, Path(raw_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Derive comparison-only semantic class-dump receipts without "
            "replacing Foundation's exact custody hashes."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="fingerprint one numbered dump")
    manifest.add_argument("class_dump", type=Path)
    manifest.add_argument("--output", type=Path, required=True)

    compare = commands.add_parser("compare", help="compare two or more manifests")
    compare.add_argument(
        "--manifest",
        type=_labeled_manifest,
        action="append",
        required=True,
        metavar="LABEL=PATH",
    )
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "manifest":
            manifest = build_semantic_bytecode_manifest(arguments.class_dump)
            write_semantic_bytecode_manifest(manifest, arguments.output)
            receipt = {
                "manifest_sha256": manifest.manifest_sha256,
                "exact_foundation_manifest_sha256": (
                    manifest.exact_foundation_manifest_sha256
                ),
                "semantic_dump_sha256": manifest.semantic_dump_sha256,
                "normalized_class_count": manifest.normalized_class_count,
                "output": str(arguments.output),
            }
        else:
            labeled = arguments.manifest
            labels = [label for label, _ in labeled]
            if len(labels) != len(set(labels)):
                raise CaptureValidationError("comparison labels must be unique")
            manifests = {
                label: load_semantic_bytecode_manifest(path)
                for label, path in labeled
            }
            receipt = evaluate_semantic_bytecode_manifests(manifests)
            _atomic_json(receipt, arguments.output)
            receipt = dict(receipt)
            receipt["output"] = str(arguments.output)
    except CaptureValidationError as exc:
        print(f"semantic-bytecode admission failed: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
