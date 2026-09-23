#!/usr/bin/env python3

"""Assemble one typed Mixin transformation ledger from normalized input."""

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
    OBSERVATION_SET_FORMAT,
    LedgerValidationError,
    build_ledger,
    write_ledger,
)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise LedgerValidationError(
                f"observation set repeats JSON key {key!r}"
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
        raise LedgerValidationError(f"cannot read observation set {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LedgerValidationError("observation set must be an object")
    expected = {"format", "session", "launch_state", "observations", "limitations"}
    if set(value) != expected:
        raise LedgerValidationError("observation set surface is not exactly V1")
    if value["format"] != OBSERVATION_SET_FORMAT:
        raise LedgerValidationError("observation set format is not V1")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed Crucible Mixin transformation ledger."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    spec = _load_spec(args.input)
    ledger = build_ledger(
        session=spec["session"],
        launch_state=spec["launch_state"],
        observations=spec["observations"],
        limitations=spec["limitations"],
    )
    write_ledger(args.output, ledger)
    print(ledger["ledger_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LedgerValidationError as exc:
        print(f"Mixin ledger assembly failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
