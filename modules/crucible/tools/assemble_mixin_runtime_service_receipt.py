#!/usr/bin/env python3

"""Assemble one generic Mixin runtime-service receipt from normalized input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_mixin_custody import (  # noqa: E402
    RUNTIME_SERVICE_OBSERVATION_SET_FORMAT,
    RuntimeServiceValidationError,
    build_runtime_service_receipt,
    write_runtime_service_receipt,
)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeServiceValidationError(
                f"runtime-service observation set repeats JSON key {key!r}"
            )
        value[key] = item
    return value


def _load_spec(path: Path) -> dict:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeServiceValidationError(
            f"cannot read runtime-service observation set {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeServiceValidationError(
            "runtime-service observation set must be an object"
        )
    expected = {
        "format",
        "session",
        "artifacts",
        "evidence",
        "observations",
        "provider_enumeration",
        "limitations",
    }
    if set(value) != expected:
        raise RuntimeServiceValidationError(
            "runtime-service observation set surface is not exactly V1"
        )
    if value["format"] != RUNTIME_SERVICE_OBSERVATION_SET_FORMAT:
        raise RuntimeServiceValidationError(
            "runtime-service observation set format is not V1"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a fail-closed Crucible Mixin runtime-service receipt "
            "without inferring provider uniqueness or final transformations."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    spec = _load_spec(args.input)
    receipt = build_runtime_service_receipt(
        session=spec["session"],
        artifacts=spec["artifacts"],
        evidence=spec["evidence"],
        observations=spec["observations"],
        provider_enumeration=spec["provider_enumeration"],
        limitations=spec["limitations"],
    )
    write_runtime_service_receipt(args.output, receipt)
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeServiceValidationError as exc:
        print(f"Mixin runtime-service receipt assembly failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
