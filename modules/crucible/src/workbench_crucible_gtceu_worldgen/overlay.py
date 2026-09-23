"""Materialize checked GTCEu worldgen overlays into a disposable config root."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Mapping

from .inventory import (
    GTCEU_WORLDGEN_PREFIX,
    GtceuWorldgenValidationError,
    build_gtceu_worldgen_inventory,
    canonical_json_bytes,
    normalize_definition,
    parse_gtceu_worldgen_inventory,
    write_gtceu_worldgen_inventory,
)


OVERLAY_FORMAT = "workbench-crucible-gtceu-worldgen-overlay-v1"
MATERIALIZATION_FORMAT = "workbench-crucible-gtceu-worldgen-overlay-materialization-v1"
MATERIALIZATION_PREFIX = "crucible-gtceu-overlay:sha256:"
MATERIALIZATION_BOUNDARIES = {
    "source_configuration_modified": False,
    "output_is_disposable_materialization": True,
    "runtime_reload_or_restart_performed": False,
    "recurrent_complex_integration_added": False,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GtceuWorldgenValidationError(message)


def _safe_definition_path(value: Any, kind: str) -> PurePosixPath:
    _require(isinstance(value, str) and value, "overlay relative_path must be text")
    relative = PurePosixPath(value)
    _require(
        not relative.is_absolute() and ".." not in relative.parts,
        f"unsafe overlay path: {value}",
    )
    expected_prefix = ("worldgen", "vein" if kind == "ore" else "fluid")
    _require(
        relative.parts[:2] == expected_prefix and relative.suffix == ".json",
        f"overlay {kind} path must be under {'/'.join(expected_prefix)}: {value}",
    )
    return relative


def parse_overlay_plan(
    value: Mapping[str, Any], inventory: Mapping[str, Any]
) -> dict[str, Any]:
    plan = deepcopy(dict(value))
    _require(
        set(plan) == {"format", "schema_version", "target_inventory_id", "operations"},
        "overlay fields drift",
    )
    _require(plan.get("format") == OVERLAY_FORMAT, "overlay format drift")
    _require(plan.get("schema_version") == 1, "overlay schema version drift")
    _require(
        plan.get("target_inventory_id") == inventory.get("inventory_id"),
        "overlay target inventory ID does not match the supplied inventory",
    )
    operations = plan.get("operations")
    _require(isinstance(operations, list) and operations, "overlay needs operations")
    source_files = {
        row["relative_path"]: row
        for row in inventory.get("configuration", {}).get("files", [])
        if isinstance(row, Mapping)
    }
    seen: set[str] = set()
    for index, operation in enumerate(operations):
        context = f"operations[{index}]"
        _require(isinstance(operation, Mapping), f"{context} must be an object")
        action = operation.get("op")
        kind = operation.get("kind")
        _require(action in {"add", "replace", "remove"}, f"{context}.op invalid")
        _require(kind in {"ore", "fluid"}, f"{context}.kind invalid")
        expected_fields = {"op", "kind", "relative_path"}
        if action in {"replace", "remove"}:
            expected_fields.add("expected_sha256")
        if action in {"add", "replace"}:
            expected_fields.add("definition")
        _require(
            set(operation) == expected_fields,
            f"{context} fields drift",
        )
        relative = _safe_definition_path(operation.get("relative_path"), kind)
        relative_text = relative.as_posix()
        _require(relative_text not in seen, f"duplicate overlay path: {relative_text}")
        seen.add(relative_text)
        existing = source_files.get(relative_text)
        if action == "add":
            _require(existing is None, f"overlay add target already exists: {relative_text}")
        else:
            _require(existing is not None, f"overlay target is absent: {relative_text}")
            _require(
                operation.get("expected_sha256") == existing.get("sha256"),
                f"overlay precondition hash mismatch: {relative_text}",
            )
        if action in {"add", "replace"}:
            definition = operation.get("definition")
            _require(
                isinstance(definition, Mapping),
                f"{context}.definition must be an object",
            )
            normalize_definition(kind, relative_text, definition)
        else:
            _require(
                "definition" not in operation,
                f"{context} remove must not contain a definition",
            )
    return plan


def materialize_overlay(
    *,
    jar_path: Path,
    config_root: Path,
    inventory: Mapping[str, Any],
    plan: Mapping[str, Any],
    output_config_root: Path,
) -> dict[str, Any]:
    source_inventory = parse_gtceu_worldgen_inventory(inventory)
    checked_plan = parse_overlay_plan(plan, source_inventory)
    source_root = config_root.expanduser().resolve(strict=True)
    output_root = output_config_root.expanduser().resolve()
    current_source = build_gtceu_worldgen_inventory(
        jar_path=jar_path,
        config_root=source_root,
    )
    _require(
        current_source["artifact"] == source_inventory["artifact"],
        "supplied GTCEu jar drifted from the source inventory",
    )
    _require(
        current_source["configuration"] == source_inventory["configuration"],
        "supplied GTCEu configuration drifted from the source inventory",
    )
    try:
        output_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise GtceuWorldgenValidationError(
            "overlay output must not be inside the source configuration"
        )
    try:
        source_root.relative_to(output_root)
    except ValueError:
        pass
    else:
        raise GtceuWorldgenValidationError(
            "overlay output must not contain the source configuration"
        )
    _require(not output_root.exists(), f"overlay output already exists: {output_root}")
    output_root.mkdir(parents=True)

    for relative in ("dimensions.json", "worldgen_extracted.json"):
        source = source_root / relative
        if source.is_file():
            shutil.copy2(source, output_root / relative)
    for relative in ("worldgen/vein", "worldgen/fluid"):
        source = source_root / relative
        _require(source.is_dir(), f"missing source directory: {source}")
        shutil.copytree(source, output_root / relative)

    applied = []
    for operation in checked_plan["operations"]:
        target = output_root / PurePosixPath(operation["relative_path"])
        if operation["op"] == "remove":
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(operation["definition"], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        applied.append(
            {
                "op": operation["op"],
                "kind": operation["kind"],
                "relative_path": operation["relative_path"],
            }
        )

    output_inventory = build_gtceu_worldgen_inventory(
        jar_path=jar_path,
        config_root=output_root,
    )
    inventory_path = output_root.parent / "gtceu-worldgen-inventory-v1.json"
    write_gtceu_worldgen_inventory(inventory_path, output_inventory)
    materialization: dict[str, Any] = {
        "format": MATERIALIZATION_FORMAT,
        "schema_version": 1,
        "materialization_id": "",
        "source_inventory_id": source_inventory["inventory_id"],
        "output_inventory_id": output_inventory["inventory_id"],
        "operation_count": len(applied),
        "operations": applied,
        "output_inventory": inventory_path.name,
        "boundaries": dict(MATERIALIZATION_BOUNDARIES),
    }
    identity = deepcopy(materialization)
    identity["materialization_id"] = ""
    materialization["materialization_id"] = MATERIALIZATION_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    return parse_overlay_materialization(materialization)


def parse_overlay_materialization(value: Mapping[str, Any]) -> dict[str, Any]:
    receipt = deepcopy(dict(value))
    expected_fields = {
        "format",
        "schema_version",
        "materialization_id",
        "source_inventory_id",
        "output_inventory_id",
        "operation_count",
        "operations",
        "output_inventory",
        "boundaries",
    }
    _require(set(receipt) == expected_fields, "overlay materialization fields drift")
    _require(
        receipt.get("format") == MATERIALIZATION_FORMAT,
        "overlay materialization format drift",
    )
    _require(receipt.get("schema_version") == 1, "overlay materialization version drift")
    for field in ("source_inventory_id", "output_inventory_id"):
        value_id = receipt.get(field)
        digest = (
            value_id[len(GTCEU_WORLDGEN_PREFIX) :]
            if isinstance(value_id, str)
            and value_id.startswith(GTCEU_WORLDGEN_PREFIX)
            else ""
        )
        _require(
            len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest),
            f"invalid {field}",
        )
    operations = receipt.get("operations")
    _require(isinstance(operations, list), "materialization operations must be a list")
    _require(
        receipt.get("operation_count") == len(operations) and bool(operations),
        "materialization operation count drift",
    )
    for index, operation in enumerate(operations):
        _require(
            isinstance(operation, Mapping)
            and set(operation) == {"op", "kind", "relative_path"},
            f"materialization operations[{index}] fields drift",
        )
        action = operation.get("op")
        kind = operation.get("kind")
        _require(action in {"add", "replace", "remove"}, "materialization op invalid")
        _require(kind in {"ore", "fluid"}, "materialization kind invalid")
        _safe_definition_path(operation.get("relative_path"), kind)
    _require(
        receipt.get("output_inventory") == "gtceu-worldgen-inventory-v1.json",
        "materialization output inventory name drift",
    )
    _require(
        receipt.get("boundaries") == MATERIALIZATION_BOUNDARIES,
        "materialization boundaries drift",
    )
    identity = deepcopy(receipt)
    materialization_id = identity.get("materialization_id")
    identity["materialization_id"] = ""
    expected_id = MATERIALIZATION_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    _require(materialization_id == expected_id, "overlay materialization ID drift")
    return receipt
