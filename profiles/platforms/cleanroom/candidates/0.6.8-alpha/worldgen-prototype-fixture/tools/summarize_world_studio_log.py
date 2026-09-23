#!/usr/bin/env python3

"""Summarize development-only WORLDGEN_PROTOTYPE records from one server log."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable


PREFIX = "WORLDGEN_PROTOTYPE "
TOOL_VERSION = "world-studio-log-summary-v3"


def _nearest_rank(values: Iterable[int], percentile: float) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, int((len(ordered) * percentile) + 0.999999999))
    return ordered[min(len(ordered), rank) - 1]


def _latency(records: list[dict[str, Any]]) -> dict[str, int | None]:
    values = [
        value
        for record in records
        if isinstance((value := record.get("elapsed_us")), int)
    ]
    return {
        "p50_us": _nearest_rank(values, 0.50),
        "p95_us": _nearest_rank(values, 0.95),
        "maximum_us": max(values) if values else None,
    }


def _sorted_counts(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(
        str(record[key]) for record in records if record.get(key) is not None
    )
    return dict(sorted(counts.items()))


def summarize_log(path: Path) -> dict[str, Any]:
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
            payload = line.split(PREFIX, 1)[1].strip()
            try:
                record = json.loads(payload)
            except json.JSONDecodeError:
                invalid_records += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                invalid_records += 1

    if not records:
        raise ValueError(f"no {PREFIX.strip()} records found in {path}")

    by_event: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_event.setdefault(str(record.get("event", "missing")), []).append(record)
    generated = by_event.get("chunk.generate", [])
    populated = by_event.get("chunk.populate", [])
    failures = by_event.get("chunk.failure", [])
    publications = by_event.get("plan.publish", [])
    constructions = by_event.get("generator.construct", [])

    def positive(record: dict[str, Any], key: str) -> bool:
        value = record.get(key)
        return isinstance(value, int) and value > 0

    def total(key: str) -> int:
        return sum(
            value
            for record in generated
            if isinstance((value := record.get(key)), int)
        )

    def full(key: str) -> int:
        return sum(record.get(key) == 256 for record in generated)

    def maximum_observed(key: str) -> int | None:
        values = [
            value
            for record in generated
            if isinstance((value := record.get(key)), int)
        ]
        return max(values) if values else None

    def maximum_numeric(key: str) -> int | float | None:
        values = [
            value
            for record in generated
            if isinstance((value := record.get(key)), (int, float))
            and not isinstance(value, bool)
        ]
        return max(values) if values else None

    plan = publications[-1] if publications else None
    generator = constructions[-1] if constructions else None
    return {
        "tool_version": TOOL_VERSION,
        "source": str(path),
        "runtime": {
            "ready": ready,
            "clean_stop_observed": clean_stop,
            "invalid_prototype_records": invalid_records,
            "event_counts": dict(
                sorted((event, len(items)) for event, items in by_event.items())
            ),
        },
        "plan": plan,
        "plan_publications": {
            "count": len(publications),
            "identities": [
                {
                    "profile": profile,
                    "plan_version": version,
                    "plan_hash": plan_hash,
                }
                for profile, version, plan_hash in sorted(
                    {
                        (
                            record.get("profile"),
                            record.get("plan_version"),
                            record.get("plan_hash"),
                        )
                        for record in publications
                    },
                    key=lambda identity: tuple(str(value) for value in identity),
                )
            ],
        },
        "generator": generator,
        "sampling": {
            "shared_provider_generator": (
                generator.get("shared_sampling")
                if isinstance(generator, dict)
                else None
            ),
            "chunk_sample_cache_capacity": (
                generator.get("chunk_sample_cache_capacity")
                if isinstance(generator, dict)
                else None
            ),
            "biome_point_cache_capacity": (
                generator.get("biome_point_cache_capacity")
                if isinstance(generator, dict)
                else None
            ),
            "watershed_tile_cache_capacity": (
                generator.get("watershed_tile_cache_capacity")
                if isinstance(generator, dict)
                else None
            ),
            "maximum_observed_counters": {
                "chunk_sample_cache_hits": maximum_observed(
                    "chunk_sample_cache_hits"
                ),
                "chunk_sample_cache_misses": maximum_observed(
                    "chunk_sample_cache_misses"
                ),
                "cached_biome_lookups": maximum_observed("cached_biome_lookups"),
                "point_biome_cache_hits": maximum_observed(
                    "point_biome_cache_hits"
                ),
                "scalar_biome_samples": maximum_observed("scalar_biome_samples"),
                "watershed_tile_cache_hits": maximum_observed(
                    "watershed_tile_cache_hits"
                ),
                "watershed_tile_cache_misses": maximum_observed(
                    "watershed_tile_cache_misses"
                ),
                "watershed_tile_cache_evictions": maximum_observed(
                    "watershed_tile_cache_evictions"
                ),
                "watershed_computed_grid_cells": maximum_observed(
                    "watershed_computed_grid_cells"
                ),
            },
        },
        "generation": {
            "chunks": len(generated),
            "regions": _sorted_counts(generated, "mega_region"),
            "lithologies": _sorted_counts(generated, "lithology"),
            "biomes": _sorted_counts(generated, "biome"),
            "height_hashes": len(
                {record.get("height_hash") for record in generated}
            ),
            "watershed_fingerprints": len(
                {record.get("watershed_fingerprint") for record in generated}
            ),
            "latency": _latency(generated),
        },
        "hydrology": {
            "river_chunks": sum(
                positive(record, "river_columns") for record in generated
            ),
            "river_full_chunks": full("river_columns"),
            "river_columns": total("river_columns"),
            "stream_chunks": sum(
                positive(record, "stream_columns") for record in generated
            ),
            "stream_full_chunks": full("stream_columns"),
            "stream_columns": total("stream_columns"),
            "water_chunks": sum(
                positive(record, "water_columns") for record in generated
            ),
            "water_columns": total("water_columns"),
            "filled_depression_chunks": sum(
                positive(record, "filled_depression_columns")
                for record in generated
            ),
            "filled_depression_columns": total("filled_depression_columns"),
            "maximum_discharge": maximum_numeric("watershed_max_discharge"),
            "maximum_fill_depth": maximum_numeric("watershed_max_fill_depth"),
            "algorithm": (
                generator.get("watershed_algorithm")
                if isinstance(generator, dict)
                else None
            ),
            "grid": {
                "cell_size_blocks": (
                    generator.get("watershed_cell_size_blocks")
                    if isinstance(generator, dict)
                    else None
                ),
                "tile_size_cells": (
                    generator.get("watershed_tile_size_cells")
                    if isinstance(generator, dict)
                    else None
                ),
                "halo_cells": (
                    generator.get("watershed_halo_cells")
                    if isinstance(generator, dict)
                    else None
                ),
            },
        },
        "carvers": {
            "chunks_with_underground_air": sum(
                positive(record, "carved_blocks") for record in generated
            ),
            "underground_air_blocks": total("carved_blocks"),
            "maximum_per_chunk": max(
                (
                    record.get("carved_blocks", 0)
                    for record in generated
                    if isinstance(record.get("carved_blocks", 0), int)
                ),
                default=0,
            ),
        },
        "population": {
            "chunks": len(populated),
            "native_biome_decoration_invoked": sum(
                record.get("biome_decoration_invoked") is True
                for record in populated
            ),
            "custom_feature_placed": sum(
                record.get("feature_placed") is True for record in populated
            ),
            "latency": _latency(populated),
        },
        "prototype_failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Cleanroom latest.log or retained copy")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit one compact JSON line instead of indented JSON",
    )
    arguments = parser.parse_args()
    try:
        summary = summarize_log(arguments.log)
    except (OSError, ValueError) as failure:
        parser.error(str(failure))
    print(
        json.dumps(
            summary,
            sort_keys=True,
            indent=None if arguments.compact else 2,
            separators=(",", ":") if arguments.compact else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
