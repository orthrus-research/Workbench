#!/usr/bin/env python3

"""Assemble one bounded controlled GTCEu subsurface trace from observed records."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_gtceu_subsurface import (  # noqa: E402
    GtceuSubsurfaceTraceValidationError,
    build_gtceu_subsurface_trace,
    write_gtceu_subsurface_trace,
)


INPUT_FORMAT = "workbench-crucible-gtceu-subsurface-trace-input-v1"
MAX_INPUT_BYTES = 128 * 1024 * 1024
_INPUT_KEYS = {
    "format",
    "adapter_profile",
    "capture",
    "coverage",
    "deposits",
    "decisions",
}


def _safe_text(value: object) -> str:
    return "".join(character if ord(character) >= 32 else "�" for character in str(value))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GtceuSubsurfaceTraceValidationError(
                f"trace input repeats JSON key {key!r}"
            )
        value[key] = item
    return value


def _read_input(path: Path) -> dict[str, Any]:
    requested = path.expanduser()
    if requested.is_symlink():
        raise GtceuSubsurfaceTraceValidationError(
            f"trace input cannot be a symlink: {requested}"
        )
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise GtceuSubsurfaceTraceValidationError(
            f"cannot resolve trace input {requested}: {exc}"
        ) from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise GtceuSubsurfaceTraceValidationError(
            f"cannot open trace input {resolved}: {exc}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GtceuSubsurfaceTraceValidationError(
                f"trace input is not a regular file: {resolved}"
            )
        if before.st_size <= 0 or before.st_size > MAX_INPUT_BYTES:
            raise GtceuSubsurfaceTraceValidationError(
                f"trace input size must be in 1..{MAX_INPUT_BYTES} bytes"
            )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise GtceuSubsurfaceTraceValidationError(
                    "trace input ended before its declared size"
                )
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise GtceuSubsurfaceTraceValidationError(
                "trace input grew while being read"
            )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda row: (
        row.st_dev,
        row.st_ino,
        row.st_mode,
        row.st_size,
        row.st_mtime_ns,
        row.st_ctime_ns,
    )
    if identity(before) != identity(after) or identity(before) != identity(resolved.stat()):
        raise GtceuSubsurfaceTraceValidationError(
            f"trace input changed while being read: {resolved}"
        )
    try:
        value = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GtceuSubsurfaceTraceValidationError(
            f"cannot parse trace input {resolved}: {exc}"
        ) from exc
    if not isinstance(value, dict) or set(value) != _INPUT_KEYS:
        actual = set(value) if isinstance(value, dict) else set()
        raise GtceuSubsurfaceTraceValidationError(
            "trace input fields mismatch: "
            f"missing={sorted(_INPUT_KEYS - actual)!r}, unknown={sorted(actual - _INPUT_KEYS)!r}"
        )
    if value["format"] != INPUT_FORMAT:
        raise GtceuSubsurfaceTraceValidationError(
            f"trace input format must be {INPUT_FORMAT}"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate already observed GTCEu selection/placement records and create "
            "one fresh content-addressed Crucible trace."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        value = _read_input(arguments.input)
        trace = build_gtceu_subsurface_trace(
            adapter_profile=value["adapter_profile"],
            capture=value["capture"],
            coverage=value["coverage"],
            deposits=value["deposits"],
            decisions=value["decisions"],
        )
        write_gtceu_subsurface_trace(arguments.output, trace)
    except (OSError, GtceuSubsurfaceTraceValidationError) as exc:
        print(f"GTCEu subsurface trace assembly failed: {_safe_text(exc)}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "trace_id": trace["trace_id"],
                "deposit_count": len(trace["deposits"]),
                "decision_count": len(trace["decisions"]),
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
