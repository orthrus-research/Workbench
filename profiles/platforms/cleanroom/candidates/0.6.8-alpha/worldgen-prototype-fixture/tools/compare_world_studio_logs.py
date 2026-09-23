#!/usr/bin/env python3

"""Compare fixed-seed World Studio logs while excluding declared telemetry."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


PREFIX = "WORLDGEN_PROTOTYPE "
TOOL_VERSION = "world-studio-log-comparison-v2"
CHUNK_EVENTS = ("chunk.generate", "chunk.populate")
TELEMETRY_FIELDS = frozenset(
    {
        "elapsed_us",
        "chunk_sample_cache_hits",
        "chunk_sample_cache_misses",
        "cached_biome_lookups",
        "point_biome_cache_hits",
        "scalar_biome_samples",
        "watershed_tile_cache_hits",
        "watershed_tile_cache_misses",
        "watershed_tile_cache_evictions",
        "watershed_computed_grid_cells",
    }
)
PLAN_IDENTITY_FIELDS = ("profile", "plan_version", "plan_hash")
GENERATOR_IDENTITY_FIELDS = (
    "seed",
    "dimension",
    "world_type",
    "provider",
    "generator",
    "profile",
    "plan_version",
    "plan_hash",
    "cave_generator",
    "ravine_generator",
    "watershed_algorithm",
    "watershed_cell_size_blocks",
    "watershed_tile_size_cells",
    "watershed_halo_cells",
)


def _read_log(path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    invalid_records = 0
    ready = False
    clean_stop = False
    with path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            if re.search(r"\bDone \([0-9.]+s\)!", line):
                ready = True
            if "Stopping the server" in line:
                clean_stop = True
            if PREFIX not in line:
                continue
            try:
                record = json.loads(line.split(PREFIX, 1)[1].strip())
            except json.JSONDecodeError:
                invalid_records += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                invalid_records += 1
    if not records:
        raise ValueError(f"no {PREFIX.strip()} records found in {path}")
    return {
        "path": str(path),
        "records": records,
        "ready": ready,
        "clean_stop": clean_stop,
        "invalid_records": invalid_records,
    }


def _event_records(log: dict[str, Any], event: str) -> list[dict[str, Any]]:
    return [record for record in log["records"] if record.get("event") == event]


def _identity(
    log: dict[str, Any], event: str, fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    identities = {
        tuple(record.get(field) for field in fields)
        for record in _event_records(log, event)
    }
    return [
        dict(zip(fields, values))
        for values in sorted(identities, key=lambda item: tuple(str(value) for value in item))
    ]


def _chunk_map(
    log: dict[str, Any], event: str
) -> tuple[dict[tuple[int, int, int], dict[str, Any]], list[list[int]], int]:
    records = _event_records(log, event)
    keyed_records: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    invalid_coordinates = 0
    for record in records:
        if not isinstance(record.get("chunk_x"), int) or not isinstance(
            record.get("chunk_z"), int
        ):
            invalid_coordinates += 1
            continue
        key = (int(record.get("dimension", 0)), record["chunk_x"], record["chunk_z"])
        keyed_records.append((key, record))
    keys = [key for key, _ in keyed_records]
    counts = Counter(keys)
    duplicates = [list(key) for key, count in sorted(counts.items()) if count > 1]
    return dict(keyed_records), duplicates, invalid_coordinates


def _semantic_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in TELEMETRY_FIELDS
    }


def _changed_fields(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    changed = []
    for field in sorted(set(baseline) | set(candidate)):
        if baseline.get(field) != candidate.get(field):
            changed.append(
                {
                    "field": field,
                    "baseline": baseline.get(field),
                    "candidate": candidate.get(field),
                }
            )
    return changed


def _compare_chunks(
    baseline: dict[str, Any], candidate: dict[str, Any], event: str
) -> dict[str, Any]:
    left, left_duplicates, left_invalid = _chunk_map(baseline, event)
    right, right_duplicates, right_invalid = _chunk_map(candidate, event)
    left_keys = set(left)
    right_keys = set(right)
    changed = []
    for key in sorted(left_keys & right_keys):
        differences = _changed_fields(
            _semantic_record(left[key]), _semantic_record(right[key])
        )
        if differences:
            changed.append(
                {
                    "dimension": key[0],
                    "chunk_x": key[1],
                    "chunk_z": key[2],
                    "fields": differences,
                }
            )
    missing_from_candidate = [list(key) for key in sorted(left_keys - right_keys)]
    missing_from_baseline = [list(key) for key in sorted(right_keys - left_keys)]
    equivalent = not any(
        (
            left_duplicates,
            right_duplicates,
            left_invalid,
            right_invalid,
            missing_from_candidate,
            missing_from_baseline,
            changed,
        )
    )
    return {
        "equivalent": equivalent,
        "baseline_records": len(_event_records(baseline, event)),
        "candidate_records": len(_event_records(candidate, event)),
        "baseline_duplicate_chunks": left_duplicates,
        "candidate_duplicate_chunks": right_duplicates,
        "baseline_invalid_coordinate_records": left_invalid,
        "candidate_invalid_coordinate_records": right_invalid,
        "missing_from_candidate": missing_from_candidate,
        "missing_from_baseline": missing_from_baseline,
        "changed_chunks": changed,
    }


def compare_logs(baseline_path: Path, candidate_path: Path) -> dict[str, Any]:
    baseline = _read_log(baseline_path)
    candidate = _read_log(candidate_path)
    plan_identity = {
        "baseline": _identity(baseline, "plan.publish", PLAN_IDENTITY_FIELDS),
        "candidate": _identity(candidate, "plan.publish", PLAN_IDENTITY_FIELDS),
    }
    plan_identity["equivalent"] = (
        plan_identity["baseline"] == plan_identity["candidate"]
        and len(plan_identity["baseline"]) == 1
    )
    generator_identity = {
        "baseline": _identity(
            baseline, "generator.construct", GENERATOR_IDENTITY_FIELDS
        ),
        "candidate": _identity(
            candidate, "generator.construct", GENERATOR_IDENTITY_FIELDS
        ),
    }
    generator_identity["equivalent"] = (
        generator_identity["baseline"] == generator_identity["candidate"]
        and bool(generator_identity["baseline"])
    )
    runtime = {
        "baseline": {
            "ready": baseline["ready"],
            "clean_stop_observed": baseline["clean_stop"],
            "invalid_prototype_records": baseline["invalid_records"],
            "prototype_failures": len(_event_records(baseline, "chunk.failure")),
        },
        "candidate": {
            "ready": candidate["ready"],
            "clean_stop_observed": candidate["clean_stop"],
            "invalid_prototype_records": candidate["invalid_records"],
            "prototype_failures": len(_event_records(candidate, "chunk.failure")),
        },
    }
    runtime["usable"] = all(
        side["ready"]
        and side["clean_stop_observed"]
        and side["invalid_prototype_records"] == 0
        and side["prototype_failures"] == 0
        for side in (runtime["baseline"], runtime["candidate"])
    )
    comparisons = {
        event: _compare_chunks(baseline, candidate, event) for event in CHUNK_EVENTS
    }
    equivalent = (
        runtime["usable"]
        and plan_identity["equivalent"]
        and generator_identity["equivalent"]
        and all(result["equivalent"] for result in comparisons.values())
    )
    return {
        "tool_version": TOOL_VERSION,
        "baseline": str(baseline_path),
        "candidate": str(candidate_path),
        "excluded_telemetry_fields": sorted(TELEMETRY_FIELDS),
        "equivalent": equivalent,
        "runtime": runtime,
        "plan_identity": plan_identity,
        "generator_identity": generator_identity,
        "records": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit one compact JSON line instead of indented JSON",
    )
    arguments = parser.parse_args()
    try:
        result = compare_logs(arguments.baseline, arguments.candidate)
    except (OSError, ValueError) as failure:
        parser.error(str(failure))
    print(
        json.dumps(
            result,
            sort_keys=True,
            indent=None if arguments.compact else 2,
            separators=(",", ":") if arguments.compact else None,
        )
    )
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
