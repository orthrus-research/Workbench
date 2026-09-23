#!/usr/bin/env python3
"""Assemble one Crucible runtime snapshot V2 from validated receipt files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_runtime_snapshot import (  # noqa: E402
    RuntimeSnapshotValidationError,
    bind_known_receipt,
    build_runtime_snapshot,
    capability_from_receipts,
    capability_status,
    write_runtime_snapshot,
)


PLAN_FORMAT = "workbench-crucible-runtime-snapshot-plan-v2"
MAX_INPUT_BYTES = 128 * 1024 * 1024


class _DuplicateJsonKey(ValueError):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> dict[str, Any]:
    encoded = _safe_read(path, label)
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite number {item}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise RuntimeSnapshotValidationError(f"{label} is malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeSnapshotValidationError(f"{label} root must be an object")
    return value


def _safe_read(path: Path, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise RuntimeSnapshotValidationError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeSnapshotValidationError(f"{label} must be a regular non-symlink file")
    if before.st_size > MAX_INPUT_BYTES:
        raise RuntimeSnapshotValidationError(f"{label} exceeds {MAX_INPUT_BYTES} bytes")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise RuntimeSnapshotValidationError(f"{label} changed while opening")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise RuntimeSnapshotValidationError(f"{label} ended while reading")
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise RuntimeSnapshotValidationError(f"{label} changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise RuntimeSnapshotValidationError(f"cannot read {label}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _closed(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        raise RuntimeSnapshotValidationError(
            f"{label} fields differ; missing={missing}, unknown={unknown}"
        )


def _assemble(plan_path: Path, output: Path) -> dict[str, Any]:
    plan = _load_json(plan_path, "runtime snapshot plan")
    _closed(
        plan,
        {
            "format",
            "schema_version",
            "runtime_identity",
            "epoch",
            "receipt_sources",
            "capabilities",
            "capture_health",
            "limitations",
        },
        "runtime snapshot plan",
    )
    if plan["format"] != PLAN_FORMAT or plan["schema_version"] != 2:
        raise RuntimeSnapshotValidationError("runtime snapshot plan format is not V2")
    if not isinstance(plan["receipt_sources"], list):
        raise RuntimeSnapshotValidationError("receipt_sources must be an array")
    bindings: list[dict[str, Any]] = []
    for index, source in enumerate(plan["receipt_sources"]):
        if not isinstance(source, Mapping):
            raise RuntimeSnapshotValidationError(
                f"receipt_sources[{index}] must be an object"
            )
        _closed(dict(source), {"path", "label"}, f"receipt_sources[{index}]")
        source_path = Path(str(source["path"]))
        if not source_path.is_absolute():
            source_path = plan_path.parent / source_path
        encoded = _safe_read(source_path, f"receipt_sources[{index}]")
        bindings.append(
            bind_known_receipt(encoded, source_label=str(source["label"]))
        )
    roles: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        roles.setdefault(binding["role"], []).append(binding)

    if not isinstance(plan["capabilities"], list):
        raise RuntimeSnapshotValidationError("capabilities must be an array")
    capabilities: list[dict[str, Any]] = []
    for index, row in enumerate(plan["capabilities"]):
        if not isinstance(row, Mapping):
            raise RuntimeSnapshotValidationError(f"capabilities[{index}] must be an object")
        _closed(
            dict(row),
            {"name", "state", "receipt_roles", "reason_code", "limitations"},
            f"capabilities[{index}]",
        )
        state = row["state"]
        receipt_roles = row["receipt_roles"]
        if not isinstance(receipt_roles, list):
            raise RuntimeSnapshotValidationError(
                f"capabilities[{index}].receipt_roles must be an array"
            )
        selected = [binding for role in receipt_roles for binding in roles.get(role, ())]
        if any(role not in roles for role in receipt_roles):
            missing = sorted(role for role in receipt_roles if role not in roles)
            raise RuntimeSnapshotValidationError(
                f"capabilities[{index}] references missing receipt roles: {missing}"
            )
        if state in {"observed", "partial"}:
            capabilities.append(
                capability_from_receipts(
                    str(row["name"]),
                    state=str(state),
                    receipt_bindings=selected,
                    limitations=row["limitations"],
                )
            )
        elif state in {"not_observed", "unavailable"}:
            if receipt_roles:
                raise RuntimeSnapshotValidationError(
                    f"capabilities[{index}] evidence-free state cites receipt roles"
                )
            capabilities.append(
                capability_status(
                    str(row["name"]),
                    state=str(state),
                    reason_code=str(row["reason_code"]),
                )
            )
        elif state == "failed":
            if not selected:
                raise RuntimeSnapshotValidationError(
                    f"capabilities[{index}] failed state lacks a receipt role"
                )
            capabilities.append(
                {
                    "name": row["name"],
                    "state": "failed",
                    "receipt_ids": sorted(binding["receipt_id"] for binding in selected),
                    "semantic_fingerprint": None,
                    "reason_code": row["reason_code"],
                    "limitations": row["limitations"],
                }
            )
        else:
            raise RuntimeSnapshotValidationError(
                f"capabilities[{index}] has unsupported state {state!r}"
            )

    snapshot = build_runtime_snapshot(
        runtime_identity=plan["runtime_identity"],
        epoch=plan["epoch"],
        receipt_bindings=bindings,
        capabilities=capabilities,
        capture_health=plan["capture_health"],
        limitations=plan["limitations"],
    )
    if output.exists() or output.is_symlink():
        raise RuntimeSnapshotValidationError(
            f"runtime snapshot output already exists: {output}"
        )
    write_runtime_snapshot(output, snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        snapshot = _assemble(arguments.plan, arguments.output)
    except (OSError, RuntimeSnapshotValidationError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "output": str(arguments.output),
                "summary": snapshot["summary"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
