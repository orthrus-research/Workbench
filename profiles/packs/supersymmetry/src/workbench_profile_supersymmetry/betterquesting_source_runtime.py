#!/usr/bin/env python3
"""Normalize frozen BetterQuesting source definitions and reconcile runtime state."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any, Iterable, Mapping, Sequence

from workbench_api.canonical import canonical_json_bytes


class BetterQuestingSourceRuntimeError(ValueError):
    pass


def _fail(message: str) -> None:
    raise BetterQuestingSourceRuntimeError(message)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        _fail(f"{path} must contain an ordinary object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _typed_name(key: str) -> str:
    semantic, separator, tag_id = key.rpartition(":")
    return semantic if separator and tag_id.isdigit() else key


def _typed_get(value: object, name: str, default: Any = None) -> Any:
    if type(value) is not dict:
        return default
    matches = [item for key, item in value.items() if _typed_name(key) == name]
    if len(matches) > 1:
        _fail(f"typed JSON object repeats semantic key {name}")
    return matches[0] if matches else default


def _typed_object(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("typed JSON value must be an object")
    result: dict[str, Any] = {}
    for key, item in value.items():
        semantic = _typed_name(key)
        if semantic in result:
            _fail(f"typed JSON object repeats semantic key {semantic}")
        result[semantic] = _typed_value(item)
    return result


def _typed_value(value: object) -> Any:
    if type(value) is dict:
        numeric = [
            (int(_typed_name(key)), item)
            for key, item in value.items()
            if _typed_name(key).isdigit()
        ]
        if value and len(numeric) == len(value):
            numeric.sort(key=lambda row: row[0])
            if [row[0] for row in numeric] == list(range(len(numeric))):
                return [_typed_value(item) for _, item in numeric]
        return _typed_object(value)
    if type(value) is list:
        return [_typed_value(item) for item in value]
    if type(value) is float:
        return {"value_kind": "floating", "decimal": repr(value)}
    return value


def _decode_nbt(value: object) -> Any:
    if type(value) is dict and set(value) == {"tag_id", "value"}:
        return _decode_nbt(value["value"])
    if (
        type(value) is dict
        and value.get("value_kind") in {"float32", "float64"}
        and type(value.get("decimal")) is str
    ):
        return {"value_kind": "floating", "decimal": value["decimal"]}
    if type(value) is dict:
        return {key: _decode_nbt(item) for key, item in value.items()}
    if type(value) is list:
        return [_decode_nbt(item) for item in value]
    return value


def _integer_key(key: str, label: str) -> int:
    try:
        return int(_typed_name(key))
    except ValueError as exc:
        raise BetterQuestingSourceRuntimeError(f"{label} has non-integer key {key}") from exc


def _ordered_entries(value: object, label: str) -> list[tuple[int, dict[str, Any]]]:
    if type(value) is not dict:
        _fail(f"{label} must be an object")
    rows: list[tuple[int, dict[str, Any]]] = []
    for key, item in value.items():
        if type(item) is not dict:
            _fail(f"{label} member must be an object")
        rows.append((_integer_key(key, label), item))
    rows.sort(key=lambda row: row[0])
    if len({row[0] for row in rows}) != len(rows):
        _fail(f"{label} repeats a numeric identity")
    return rows


def _item(value: object) -> dict[str, Any]:
    row = _typed_object(value)
    if type(row.get("id")) is not str or not row["id"]:
        _fail("BetterQuesting item stack lacks an item ID")
    result = {
        "item_id": row["id"],
        "count": row.get("Count", 1),
        "damage": row.get("Damage", 0),
        "ore_dictionary": row.get("OreDict", ""),
        "tag": row.get("tag"),
    }
    if type(result["count"]) is not int or type(result["damage"]) is not int:
        _fail("BetterQuesting item count or damage is not integral")
    if type(result["ore_dictionary"]) is not str:
        _fail("BetterQuesting ore-dictionary selector is not text")
    return result


def _runtime_item(value: object) -> dict[str, Any]:
    decoded = _decode_nbt(value)
    if type(decoded) is not dict:
        _fail("runtime BetterQuesting item stack is not compound NBT")
    return _item({key: item for key, item in decoded.items()})


def _fluid(value: object) -> dict[str, Any]:
    row = _typed_object(value)
    if type(row.get("FluidName")) is not str or type(row.get("Amount")) is not int:
        _fail("BetterQuesting fluid requirement is malformed")
    return {"fluid_name": row["FluidName"], "amount": row["Amount"]}


def _runtime_fluid(value: object) -> dict[str, Any]:
    decoded = _decode_nbt(value)
    if type(decoded) is not dict:
        _fail("runtime BetterQuesting fluid stack is not compound NBT")
    return _fluid(decoded)


def _semantic_key(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _subset(source: object, runtime: object) -> bool:
    if type(source) is dict:
        if type(runtime) is not dict:
            return False
        return all(key in runtime and _subset(item, runtime[key]) for key, item in source.items())
    if type(source) is list:
        return type(runtime) is list and len(source) == len(runtime) and all(
            _subset(left, right) for left, right in zip(source, runtime)
        )
    return source == runtime


def _verified_source_files(
    runtime_root: Path,
    input_manifest: Mapping[str, Any],
) -> list[tuple[Path, str, str]]:
    prefix = "config/betterquesting/DefaultQuests/"
    frozen = {
        row["relative_path"]: row
        for row in input_manifest.get("runtime_files", [])
        if type(row) is dict
        and type(row.get("relative_path")) is str
        and row["relative_path"].startswith(prefix)
        and row["relative_path"].endswith(".json")
    }
    root = runtime_root / prefix
    observed = sorted(
        path.relative_to(runtime_root).as_posix()
        for path in root.rglob("*.json")
        if path.is_file()
    )
    if observed != sorted(frozen):
        _fail("frozen BetterQuesting source file universe differs from runtime tree")
    result: list[tuple[Path, str, str]] = []
    for relative in observed:
        path = runtime_root / relative
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _fail(f"BetterQuesting source is not a regular file: {relative}")
        digest = _sha256(path)
        if digest != frozen[relative].get("sha256") or metadata.st_size != frozen[relative].get("size"):
            _fail(f"BetterQuesting source changed after input freeze: {relative}")
        result.append((path, relative[len(prefix):], digest))
    return result


def _runtime_records(capture: Path, admission: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    receipt_path = admission / "admission-receipt-v1.json"
    receipt = _load(receipt_path)
    if receipt.get("format") not in {
        "workbench-supersymmetry-progression-capture-admission-v1",
        "workbench-supersymmetry-companion-capture-admission-v1",
        "workbench-supersymmetry-server-companion-capture-admission-v1",
        "workbench-supersymmetry-world-authority-capture-admission-v1",
        "workbench-supersymmetry-realized-world-capture-admission-v1",
    }:
        _fail("progression admission receipt format differs")
    manifest_path = capture / "manifest.json"
    manifest = _load(manifest_path)
    if manifest.get("manifest_sha256") != receipt.get("raw_manifest_sha256"):
        _fail("raw capture manifest differs from progression admission")
    payloads = {
        row.get("file"): row
        for row in manifest.get("payloads", [])
        if type(row) is dict
    }
    result_path = capture / "betterquesting-definitions.json"
    payload = payloads.get(result_path.name)
    if type(payload) is not dict or payload.get("sha256") != _sha256(result_path):
        _fail("raw manifest does not bind BetterQuesting definitions")
    result = _load(result_path)
    if (
        result.get("adapter_id") != "betterquesting-definitions"
        or result.get("category_id") != "pack-progression-definitions"
        or result.get("status") != "complete"
        or result.get("stable") is not True
        or result.get("unsupported_value_count") != 0
        or result.get("diagnostics") != []
    ):
        _fail("BetterQuesting runtime category is not complete")
    records = result.get("records")
    if type(records) is not list or any(type(row) is not dict for row in records):
        _fail("BetterQuesting runtime records are malformed")
    return list(records), {
        "capture_id": receipt["capture_id"],
        "cohort_id": receipt["cohort_id"],
        "coverage_ledger_id": receipt["coverage_ledger_id"],
        "admission_receipt_sha256": _sha256(receipt_path),
        "runtime_result_sha256": result["result_sha256"],
        "runtime_records_sha256": result["records_sha256"],
    }


def _comparison(
    source: list[dict[str, Any]], runtime: list[dict[str, Any]], fields: Sequence[str]
) -> dict[str, Any]:
    source_keys = Counter(_semantic_key({key: row[key] for key in fields}) for row in source)
    runtime_keys = Counter(_semantic_key({key: row[key] for key in fields}) for row in runtime)
    missing = list((source_keys - runtime_keys).elements())
    extra = list((runtime_keys - source_keys).elements())
    return {
        "source_count": len(source),
        "runtime_count": len(runtime),
        "matched_count": sum((source_keys & runtime_keys).values()),
        "source_only_count": len(missing),
        "runtime_only_count": len(extra),
        "source_only": [json.loads(value) for value in sorted(missing)],
        "runtime_only": [json.loads(value) for value in sorted(extra)],
    }


def _cycles(quest_ids: set[int], prerequisites: Iterable[dict[str, Any]]) -> list[list[int]]:
    graph = {quest_id: [] for quest_id in quest_ids}
    for row in prerequisites:
        if row["required_quest_id"] in quest_ids:
            graph[row["quest_id"]].append(row["required_quest_id"])
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    result: list[list[int]] = []

    def visit(node: int) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for target in graph[node]:
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] != index[node]:
            return
        component: list[int] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or node in graph[node]:
            result.append(sorted(component))

    for quest_id in sorted(quest_ids):
        if quest_id not in index:
            visit(quest_id)
    return sorted(result)


def extract_betterquesting_sources(sources: Mapping[str, bytes]) -> dict[str, Any]:
    """Decode exact source definitions without capture or runtime admission."""
    from workbench_pack_program_studio.source_locations import JsonSource

    if len(sources) > 10_000 or sum(len(raw) for raw in sources.values()) > 128 * 1024 * 1024:
        _fail("quest sources exceed their input bound")

    quests: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    rewards: list[dict[str, Any]] = []
    prerequisites: list[dict[str, Any]] = []
    item_requirements: list[dict[str, Any]] = []
    fluid_requirements: list[dict[str, Any]] = []
    reward_items: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    placements: list[dict[str, Any]] = []
    settings: list[dict[str, Any]] = []
    requirement_names = {0: "NORMAL", 1: "IMPLICIT", 2: "HIDDEN"}

    for relative, raw in sorted(sources.items()):
        value = JsonSource(raw, relative).value
        digest = hashlib.sha256(raw).hexdigest()
        quest_id = _typed_get(value, "questID")
        line_id = _typed_get(value, "lineID")
        if quest_id is not None:
            if type(quest_id) is not int or Path(relative).stem != str(quest_id):
                _fail(f"quest source identity differs from filename: {relative}")
            parts = Path(relative).parts
            if len(parts) != 3 or parts[0] != "Quests":
                _fail(f"quest source path is outside the classified quest tree: {relative}")
            folder = parts[1]
            properties = _typed_value(_typed_get(value, "properties", {}))
            quests.append(
                {
                    "quest_id": quest_id,
                    "relative_path": relative,
                    "folder_classification": folder,
                    "source_sha256": digest,
                    "declared_properties": properties,
                }
            )
            requirement_ids = _typed_get(value, "preRequisites", [])
            requirement_types = _typed_get(value, "preRequisiteTypes", [])
            if type(requirement_ids) is not list or type(requirement_types) is not list:
                _fail(f"quest prerequisites are malformed: {relative}")
            for ordinal, required in enumerate(requirement_ids):
                code = requirement_types[ordinal] if ordinal < len(requirement_types) else 0
                if type(required) is not int or code not in requirement_names:
                    _fail(f"quest prerequisite is malformed: {relative}")
                prerequisites.append(
                    {
                        "quest_id": quest_id,
                        "ordinal": ordinal,
                        "required_quest_id": required,
                        "requirement_type": requirement_names[code],
                    }
                )
            for source_task_id, task in _ordered_entries(
                _typed_get(value, "tasks", {}), "tasks"
            ):
                source_task_type = _typed_get(task, "taskID")
                if type(source_task_type) is not str:
                    _fail(f"quest task lacks a type: {relative}")
                task_id = _typed_get(task, "index", source_task_id)
                if type(task_id) is not int:
                    _fail(f"quest task index is not integral: {relative}")
                task_type = (
                    "bq_standard:retrieval"
                    if source_task_type == "bq_standard:optional_retrieval"
                    else source_task_type
                )
                definition = _typed_object(task)
                definition.pop("taskID", None)
                definition.pop("index", None)
                definition.pop("requiredItems", None)
                definition.pop("requiredFluids", None)
                if source_task_type == "bq_standard:optional_retrieval":
                    definition["optional"] = 1
                tasks.append(
                    {
                        "quest_id": quest_id,
                        "task_id": task_id,
                        "task_type": task_type,
                        "source_task_type": source_task_type,
                        "source_storage_id": source_task_id,
                        "declared_definition": definition,
                    }
                )
                for ordinal, item in _ordered_entries(
                    _typed_get(task, "requiredItems", {}), "required items"
                ):
                    item_requirements.append(
                        {
                            "quest_id": quest_id,
                            "task_id": task_id,
                            "ordinal": ordinal,
                            "item_stack": _item(item),
                        }
                    )
                for ordinal, fluid in _ordered_entries(
                    _typed_get(task, "requiredFluids", {}), "required fluids"
                ):
                    fluid_requirements.append(
                        {
                            "quest_id": quest_id,
                            "task_id": task_id,
                            "ordinal": ordinal,
                            "fluid_stack": _fluid(fluid),
                        }
                    )
            for reward_id, reward in _ordered_entries(
                _typed_get(value, "rewards", {}), "rewards"
            ):
                reward_type = _typed_get(reward, "rewardID")
                if type(reward_type) is not str:
                    _fail(f"quest reward lacks a type: {relative}")
                definition = _typed_object(reward)
                definition.pop("rewardID", None)
                definition.pop("index", None)
                definition.pop("rewards", None)
                definition.pop("choices", None)
                rewards.append(
                    {
                        "quest_id": quest_id,
                        "reward_id": reward_id,
                        "reward_type": reward_type,
                        "declared_definition": definition,
                    }
                )
                for list_name, semantics in (
                    ("rewards", "grant-all"),
                    ("choices", "choose-one-option"),
                ):
                    for ordinal, item in _ordered_entries(
                        _typed_get(reward, list_name, {}), f"reward {list_name}"
                    ):
                        reward_items.append(
                            {
                                "quest_id": quest_id,
                                "reward_id": reward_id,
                                "ordinal": ordinal,
                                "selection_semantics": semantics,
                                "item_stack": _item(item),
                            }
                        )
        elif line_id is not None:
            if (
                type(line_id) is not int
                or Path(relative).parent.as_posix() != "QuestLines"
                or Path(relative).stem != str(line_id)
            ):
                _fail(f"quest-line source identity differs from filename: {relative}")
            lines.append(
                {
                    "line_id": line_id,
                    "order_index": _typed_get(value, "order"),
                    "relative_path": relative,
                    "source_sha256": digest,
                    "declared_properties": _typed_value(_typed_get(value, "properties", {})),
                }
            )
            for placement_id, placement in _ordered_entries(
                _typed_get(value, "quests", {}), "quest-line placements"
            ):
                row = _typed_object(placement)
                placements.append(
                    {
                        "line_id": line_id,
                        "placement_id": placement_id,
                        "quest_id": row["id"],
                        "x": row["x"],
                        "y": row["y"],
                        "size_x": row["sizeX"],
                        "size_y": row["sizeY"],
                    }
                )
        else:
            settings.append(
                {"relative_path": relative, "source_sha256": digest, "definition": _typed_value(value)}
            )

    return {
        "quests": quests,
        "tasks": tasks,
        "rewards": rewards,
        "prerequisites": prerequisites,
        "item_requirements": item_requirements,
        "fluid_requirements": fluid_requirements,
        "reward_items": reward_items,
        "lines": lines,
        "placements": placements,
        "settings": settings,
    }


def build_betterquesting_source_runtime_reconciliation(
    runtime_root: Path,
    capture: Path,
    admission: Path,
    input_manifest_path: Path,
) -> dict[str, Any]:
    input_manifest = _load(input_manifest_path)
    source_files = _verified_source_files(runtime_root, input_manifest)
    runtime_records, binding = _runtime_records(capture, admission)
    if (
        input_manifest.get("capture_id") != binding["capture_id"]
        or _sha256(input_manifest_path)
        != _load(admission / "admission-receipt-v1.json").get("input_manifest_sha256")
    ):
        _fail("frozen input manifest differs from progression admission")

    verified_sources = {}
    for path, relative, digest in source_files:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise BetterQuestingSourceRuntimeError("source changed after manifest verification")
        verified_sources[relative] = raw
    source_catalog = extract_betterquesting_sources(verified_sources)
    quests = source_catalog["quests"]
    tasks = source_catalog["tasks"]
    rewards = source_catalog["rewards"]
    prerequisites = source_catalog["prerequisites"]
    item_requirements = source_catalog["item_requirements"]
    fluid_requirements = source_catalog["fluid_requirements"]
    reward_items = source_catalog["reward_items"]
    lines = source_catalog["lines"]
    placements = source_catalog["placements"]
    settings = source_catalog["settings"]

    runtime_by_type: dict[str, list[dict[str, Any]]] = {}
    for row in runtime_records:
        runtime_by_type.setdefault(row.get("record_type", ""), []).append(row)
    runtime_quests = [
        {
            "quest_id": row["quest_id"],
            "definition": _decode_nbt(row["definition_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-quest-definition", [])
    ]
    runtime_tasks = [
        {
            "quest_id": row["quest_id"],
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "definition": _decode_nbt(row["definition_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-task-occurrence", [])
    ]
    runtime_rewards = [
        {
            "quest_id": row["quest_id"],
            "reward_id": row["reward_id"],
            "reward_type": row["reward_type"],
            "definition": _decode_nbt(row["definition_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-reward-occurrence", [])
    ]
    runtime_lines = [
        {
            "line_id": row["line_id"],
            "order_index": row["order_index"],
            "definition": _decode_nbt(row["definition_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-quest-line-definition", [])
    ]
    runtime_prerequisites = [
        {key: row[key] for key in ("quest_id", "ordinal", "required_quest_id", "requirement_type")}
        for row in runtime_by_type.get("betterquesting-prerequisite-occurrence", [])
    ]
    runtime_placements = [
        {key: row[key] for key in ("line_id", "quest_id", "x", "y", "size_x", "size_y")}
        for row in runtime_by_type.get("betterquesting-line-placement-occurrence", [])
    ]
    runtime_items = [
        {
            "quest_id": row["quest_id"],
            "task_id": row["task_id"],
            "ordinal": row["ordinal"],
            "item_stack": _runtime_item(row["item_stack_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-item-requirement-occurrence", [])
    ]
    runtime_fluids = [
        {
            "quest_id": row["quest_id"],
            "task_id": row["task_id"],
            "ordinal": row["ordinal"],
            "fluid_stack": _runtime_fluid(row["fluid_stack_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-fluid-requirement-occurrence", [])
    ]
    runtime_reward_items = [
        {
            "quest_id": row["quest_id"],
            "reward_id": row["reward_id"],
            "ordinal": row["ordinal"],
            "selection_semantics": row["selection_semantics"],
            "item_stack": _runtime_item(row["item_stack_nbt"]),
        }
        for row in runtime_by_type.get("betterquesting-reward-item-occurrence", [])
    ]

    comparisons = {
        "quests": _comparison(quests, runtime_quests, ("quest_id",)),
        "tasks": _comparison(tasks, runtime_tasks, ("quest_id", "task_id", "task_type")),
        "rewards": _comparison(rewards, runtime_rewards, ("quest_id", "reward_id", "reward_type")),
        "prerequisites": _comparison(
            prerequisites,
            runtime_prerequisites,
            ("quest_id", "ordinal", "required_quest_id", "requirement_type"),
        ),
        "line_placements": _comparison(
            placements,
            runtime_placements,
            ("line_id", "quest_id", "x", "y", "size_x", "size_y"),
        ),
        "quest_lines": _comparison(lines, runtime_lines, ("line_id", "order_index")),
        "item_requirements": _comparison(
            item_requirements,
            runtime_items,
            ("quest_id", "task_id", "ordinal", "item_stack"),
        ),
        "fluid_requirements": _comparison(
            fluid_requirements,
            runtime_fluids,
            ("quest_id", "task_id", "ordinal", "fluid_stack"),
        ),
        "reward_items": _comparison(
            reward_items,
            runtime_reward_items,
            ("quest_id", "reward_id", "ordinal", "selection_semantics", "item_stack"),
        ),
    }

    runtime_quest_by_id = {row["quest_id"]: row["definition"] for row in runtime_quests}
    runtime_task_by_id = {
        (row["quest_id"], row["task_id"]): row["definition"] for row in runtime_tasks
    }
    runtime_reward_by_id = {
        (row["quest_id"], row["reward_id"]): row["definition"] for row in runtime_rewards
    }
    runtime_line_by_id = {row["line_id"]: row["definition"] for row in runtime_lines}
    property_mismatches = [
        row["quest_id"]
        for row in quests
        if row["quest_id"] not in runtime_quest_by_id
        or not _subset(
            row["declared_properties"],
            runtime_quest_by_id[row["quest_id"]].get("properties", {}),
        )
    ]
    task_definition_mismatches = [
        [row["quest_id"], row["task_id"]]
        for row in tasks
        if (row["quest_id"], row["task_id"]) not in runtime_task_by_id
        or not _subset(
            row["declared_definition"],
            runtime_task_by_id[(row["quest_id"], row["task_id"])],
        )
    ]
    reward_definition_mismatches = [
        [row["quest_id"], row["reward_id"]]
        for row in rewards
        if (row["quest_id"], row["reward_id"]) not in runtime_reward_by_id
        or not _subset(
            row["declared_definition"],
            runtime_reward_by_id[(row["quest_id"], row["reward_id"])],
        )
    ]
    line_property_mismatches = [
        row["line_id"]
        for row in lines
        if row["line_id"] not in runtime_line_by_id
        or not _subset(
            row["declared_properties"],
            runtime_line_by_id[row["line_id"]].get("properties", {}),
        )
    ]
    semantic_mismatches = {
        "quest_declared_property_mismatches": property_mismatches,
        "task_declared_definition_mismatches": task_definition_mismatches,
        "reward_declared_definition_mismatches": reward_definition_mismatches,
        "quest_line_declared_property_mismatches": line_property_mismatches,
    }
    quest_ids = {row["quest_id"] for row in quests}
    cycles = _cycles(quest_ids, prerequisites)
    placement_counts = Counter(row["quest_id"] for row in placements)
    folder_mismatches = []
    for row in quests:
        count = placement_counts[row["quest_id"]]
        folder = row["folder_classification"]
        valid = (
            count == 0
            if folder == "NoQuestLine"
            else count > 1
            if folder == "MultipleQuestLine"
            else count == 1
        )
        if not valid:
            folder_mismatches.append(row["quest_id"])
    all_exact = all(
        row["source_only_count"] == row["runtime_only_count"] == 0
        for row in comparisons.values()
    )
    catalog = {
        "quests": sorted(quests, key=lambda row: row["quest_id"]),
        "tasks": sorted(tasks, key=lambda row: (row["quest_id"], row["task_id"])),
        "rewards": sorted(rewards, key=lambda row: (row["quest_id"], row["reward_id"])),
        "prerequisites": sorted(prerequisites, key=lambda row: (row["quest_id"], row["ordinal"])),
        "quest_lines": sorted(lines, key=lambda row: row["line_id"]),
        "line_placements": sorted(placements, key=lambda row: (row["line_id"], row["placement_id"])),
        "item_requirements": sorted(item_requirements, key=lambda row: (row["quest_id"], row["task_id"], row["ordinal"])),
        "fluid_requirements": sorted(fluid_requirements, key=lambda row: (row["quest_id"], row["task_id"], row["ordinal"])),
        "reward_items": sorted(reward_items, key=lambda row: (row["quest_id"], row["reward_id"], row["ordinal"])),
        "settings": settings,
    }
    result: dict[str, Any] = {
        "format": "workbench-atlas-betterquesting-source-runtime-reconciliation-v1",
        "schema_version": 1,
        "authority": {
            "source_syntax": "BetterQuesting typed split JSON",
            "runtime_semantics": "BetterQuestingUnofficial 4.3.2 definition databases",
            "semantic_authority": "Atlas",
            "player_progress_captured": False,
        },
        "binding": {
            **binding,
            "input_manifest_sha256": _sha256(input_manifest_path),
            "pack_source_revision": input_manifest["pack_source"]["revision"],
            "source_file_count": len(source_files),
            "source_file_union_sha256": hashlib.sha256(
                canonical_json_bytes(
                    [
                        {"relative_path": relative, "sha256": digest}
                        for _, relative, digest in source_files
                    ]
                )
            ).hexdigest(),
        },
        "source_catalog": catalog,
        "comparison": comparisons,
        "semantic_subset_comparison": semantic_mismatches,
        "structural_findings": {
            "dangling_prerequisites": sorted(
                [
                    {key: row[key] for key in ("quest_id", "ordinal", "required_quest_id", "requirement_type")}
                    for row in prerequisites
                    if row["required_quest_id"] not in quest_ids
                ],
                key=lambda row: (row["quest_id"], row["ordinal"]),
            ),
            "prerequisite_cycles": cycles,
            "folder_classification_mismatches": folder_mismatches,
        },
        "summary": {
            "source_quest_count": len(quests),
            "source_quest_line_count": len(lines),
            "source_task_count": len(tasks),
            "source_reward_count": len(rewards),
            "source_prerequisite_count": len(prerequisites),
            "source_line_placement_count": len(placements),
            "source_item_requirement_count": len(item_requirements),
            "source_fluid_requirement_count": len(fluid_requirements),
            "source_reward_item_count": len(reward_items),
            "dangling_prerequisite_count": sum(
                row["required_quest_id"] not in quest_ids for row in prerequisites
            ),
            "prerequisite_cycle_count": len(cycles),
            "largest_prerequisite_cycle_size": max(map(len, cycles), default=0),
            "folder_classification_mismatch_count": len(folder_mismatches),
            "all_structural_occurrences_match_runtime": all_exact,
        },
        "limitations": [
            "Definition equality does not establish quest feasibility or player reachability.",
            "Prerequisite cycles and dangling targets are reported structurally without declaring author intent.",
            "Localization keys are preserved; localized presentation text is outside this dedicated-server capture.",
            "Player, party, life, completion, claim, and task-progress state are excluded.",
        ],
    }
    identity = dict(result)
    result["reconciliation_id"] = (
        "workbench-atlas-betterquesting-source-runtime:sha256:"
        + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--admission", required=True, type=Path)
    parser.add_argument("--input-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = build_betterquesting_source_runtime_reconciliation(
            arguments.runtime_root,
            arguments.capture,
            arguments.admission,
            arguments.input_manifest,
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"BetterQuesting source/runtime reconciliation failed: {exc}", file=sys.stderr)
        return 2
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"reconciliation_id": result["reconciliation_id"], **result["summary"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BetterQuestingSourceRuntimeError",
    "build_betterquesting_source_runtime_reconciliation",
    "main",
]
