#!/usr/bin/env python3

"""Summarize World Studio custom events from one Java Flight Recording."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable


TOOL_VERSION = "world-studio-jfr-summary-v2"
EVENT_NAMES = (
    "dev.workbench.worldgen.ChunkSample",
    "dev.workbench.worldgen.ChunkStage",
    "dev.workbench.worldgen.BiomeArea",
    "dev.workbench.worldgen.WatershedTile",
)


def _nearest_rank(values: Iterable[int], percentile: float) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, int((len(ordered) * percentile) + 0.999999999))
    return ordered[min(len(ordered), rank) - 1]


def _duration_us(values: dict[str, Any]) -> int | None:
    duration = values.get("duration")
    if not isinstance(duration, str) or not duration.startswith("PT"):
        return None
    if not duration.endswith("S"):
        return None
    try:
        return int(Decimal(duration[2:-1]) * Decimal(1_000_000))
    except InvalidOperation:
        return None


def _latency(events: list[dict[str, Any]]) -> dict[str, int | None]:
    durations = [
        duration
        for event in events
        if isinstance(event.get("values"), dict)
        and (duration := _duration_us(event["values"])) is not None
    ]
    return {
        "p50_us": _nearest_rank(durations, 0.50),
        "p95_us": _nearest_rank(durations, 0.95),
        "maximum_us": max(durations) if durations else None,
    }


def summarize_document(document: dict[str, Any]) -> dict[str, Any]:
    recording = document.get("recording")
    if not isinstance(recording, dict) or not isinstance(recording.get("events"), list):
        raise ValueError("JFR JSON has no recording.events array")

    selected: dict[str, list[dict[str, Any]]] = {name: [] for name in EVENT_NAMES}
    invalid_events = 0
    for event in recording["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("values"), dict):
            invalid_events += 1
            continue
        event_type = event.get("type")
        if event_type in selected:
            selected[event_type].append(event)

    samples = selected[EVENT_NAMES[0]]
    stages = selected[EVENT_NAMES[1]]
    biome_areas = selected[EVENT_NAMES[2]]
    watershed_tiles = selected[EVENT_NAMES[3]]
    sample_hits = [
        event for event in samples if event["values"].get("cacheHit") is True
    ]
    sample_misses = [
        event for event in samples if event["values"].get("cacheHit") is False
    ]

    stages_by_name: dict[str, list[dict[str, Any]]] = {}
    for event in stages:
        stage = str(event["values"].get("stage", "missing"))
        stages_by_name.setdefault(stage, []).append(event)

    shapes = Counter(
        (
            values.get("width"),
            values.get("height"),
            values.get("coordinateScale"),
            values.get("chunkFastPath") is True,
        )
        for event in biome_areas
        for values in [event["values"]]
    )
    request_shapes = [
        {
            "width": width,
            "height": height,
            "coordinate_scale": coordinate_scale,
            "chunk_fast_path": chunk_fast_path,
            "count": count,
        }
        for (width, height, coordinate_scale, chunk_fast_path), count in sorted(
            shapes.items(), key=lambda item: tuple(str(part) for part in item[0])
        )
    ]

    plan_identities = sorted(
        {
            (values.get("planVersion"), values.get("planHash"))
            for name in EVENT_NAMES
            for event in selected[name]
            for values in [event["values"]]
            if values.get("planVersion") is not None
            or values.get("planHash") is not None
        },
        key=lambda identity: (str(identity[0]), str(identity[1])),
    )

    return {
        "tool_version": TOOL_VERSION,
        "invalid_events": invalid_events,
        "plan_identities": [
            {"plan_version": version, "plan_hash": plan_hash}
            for version, plan_hash in plan_identities
        ],
        "event_counts": {
            name: len(selected[name]) for name in EVENT_NAMES
        },
        "chunk_sampling": {
            "requests": len(samples),
            "cache_hits": len(sample_hits),
            "cache_misses": len(sample_misses),
            "maximum_cache_size": max(
                (
                    value
                    for event in samples
                    if isinstance((value := event["values"].get("cacheSize")), int)
                ),
                default=None,
            ),
            "latency": _latency(samples),
            "cache_hit_latency": _latency(sample_hits),
            "cache_miss_latency": _latency(sample_misses),
        },
        "chunk_stages": {
            stage: {"count": len(events), "latency": _latency(events)}
            for stage, events in sorted(stages_by_name.items())
        },
        "biome_areas": {
            "recorded_requests": len(biome_areas),
            "chunk_fast_path_requests": sum(
                event["values"].get("chunkFastPath") is True
                for event in biome_areas
            ),
            "recorded_point_requests": sum(
                event["values"].get("width") == 1
                and event["values"].get("height") == 1
                for event in biome_areas
            ),
            "latency": _latency(biome_areas),
            "request_shapes": request_shapes,
        },
        "watershed_tiles": {
            "builds": len(watershed_tiles),
            "latency": _latency(watershed_tiles),
            "algorithm_versions": sorted(
                {
                    str(value)
                    for event in watershed_tiles
                    if (value := event["values"].get("algorithmVersion")) is not None
                }
            ),
            "computed_grid_cells": sum(
                value
                for event in watershed_tiles
                if isinstance(
                    (value := event["values"].get("computedGridCells")), int
                )
            ),
            "filled_core_cells": sum(
                value
                for event in watershed_tiles
                if isinstance((value := event["values"].get("filledCoreCells")), int)
            ),
            "maximum_fill_depth": max(
                (
                    value
                    for event in watershed_tiles
                    if isinstance(
                        (value := event["values"].get("maximumFillDepth")),
                        (int, float),
                    )
                ),
                default=None,
            ),
            "maximum_discharge": max(
                (
                    value
                    for event in watershed_tiles
                    if isinstance(
                        (value := event["values"].get("maximumDischarge")),
                        (int, float),
                    )
                ),
                default=None,
            ),
            "maximum_cache_size": max(
                (
                    value
                    for event in watershed_tiles
                    if isinstance((value := event["values"].get("cacheSize")), int)
                ),
                default=None,
            ),
        },
    }


def load_recording(path: Path, jfr_binary: Path | None = None) -> dict[str, Any]:
    executable = str(jfr_binary) if jfr_binary is not None else shutil.which("jfr")
    if not executable:
        raise ValueError("jfr executable not found; pass --jfr-bin from the run JDK")
    process = subprocess.run(
        [
            executable,
            "print",
            "--json",
            "--events",
            ",".join(EVENT_NAMES),
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise ValueError(f"jfr print failed: {detail}")
    try:
        document = json.loads(process.stdout)
    except json.JSONDecodeError as failure:
        raise ValueError(f"jfr print returned invalid JSON: {failure}") from failure
    if not isinstance(document, dict):
        raise ValueError("jfr print returned a non-object JSON document")
    return document


def summarize_recording(path: Path, jfr_binary: Path | None = None) -> dict[str, Any]:
    result = summarize_document(load_recording(path, jfr_binary))
    result["source"] = str(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, help="Java Flight Recording (.jfr)")
    parser.add_argument(
        "--jfr-bin",
        type=Path,
        help="jfr executable from the same JDK used for the recording",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit one compact JSON line instead of indented JSON",
    )
    arguments = parser.parse_args()
    try:
        summary = summarize_recording(arguments.recording, arguments.jfr_bin)
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
