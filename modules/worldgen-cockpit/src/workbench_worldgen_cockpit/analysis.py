"""Aligned final-state, semantic, causal, and performance comparison."""

from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any, Mapping, Sequence

from workbench_subsurface_studio.model import load_profile as load_subsurface_profile
from workbench_subsurface_studio.strata import StrataRegion, load_strata_region
from workbench_subsurface_studio.studio import compare as compare_subsurface
from workbench_subsurface_studio.studio import load_dataset as load_subsurface_dataset

from .model import (
    CockpitError,
    FileBinding,
    REPORT_FORMAT,
    load_json_file,
    make_report_id,
    read_regular_file,
    require,
)


ITERATION_FORMAT = "workbench-worldgen-iteration-report-v1"
ATLAS_QUERY_CONTRACT = "WORKBENCH-ATLAS-WORLDGEN-OBSERVATORY-QUERY-V1"
AIR_FALLBACK = "minecraft:air"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _binding(path: Path | str, context: str) -> tuple[dict[str, Any], FileBinding]:
    return load_json_file(path, context=context)


def _stage(report: Mapping[str, Any], stage_id: str) -> Mapping[str, Any] | None:
    stages = report.get("stages")
    if not isinstance(stages, list):
        return None
    matches = [row for row in stages if isinstance(row, Mapping) and row.get("id") == stage_id]
    return matches[0] if len(matches) == 1 else None


def _output_path(report: Mapping[str, Any], key: str, context: str) -> Path:
    outputs = report.get("outputs")
    require(isinstance(outputs, Mapping), "iteration report outputs must be an object")
    raw = outputs.get(key)
    require(isinstance(raw, str) and bool(raw), f"iteration report lacks {context}")
    path = Path(raw).expanduser()
    require(not path.is_symlink(), f"{context} cannot be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CockpitError(f"cannot resolve {context}: {path}: {exc}") from exc
    require(resolved.is_file() or resolved.is_dir(), f"{context} is unavailable: {resolved}")
    return resolved


def _verify_input_file(
    value: Any,
    *,
    context: str,
    path_key: str,
    require_size: bool,
) -> FileBinding:
    require(isinstance(value, Mapping), f"{context} binding is invalid")
    raw_path = value.get(path_key)
    digest = value.get("sha256")
    require(isinstance(raw_path, str) and bool(raw_path), f"{context} binding lacks {path_key}")
    require(isinstance(digest, str) and len(digest) == 64, f"{context} binding lacks SHA-256")
    _, binding = read_regular_file(raw_path, context=context)
    require(binding.sha256 == digest, f"{context} content drifted after the iteration completed")
    if require_size:
        require(value.get("size_bytes") == binding.size_bytes, f"{context} size drifted after the iteration completed")
    return binding


@dataclass
class Side:
    name: str
    report: dict[str, Any]
    report_binding: FileBinding
    summary: dict[str, Any]
    summary_binding: FileBinding
    handoff: dict[str, Any]
    handoff_binding: FileBinding
    manifest_path: Path
    region: StrataRegion
    jfr: dict[str, Any] | None
    jfr_binding: FileBinding | None

    def public(self) -> dict[str, Any]:
        inputs = self.report["inputs"]
        outputs = self.report["outputs"]
        artifact = inputs["worldgen_artifact"]
        plan = inputs["plan"]
        return {
            "label": self.report["label"],
            "iteration_report": self.report_binding.public(
                kind="worldgen-iteration-report", authority="Crucible"
            ),
            "profile": deepcopy(inputs["profile"]),
            "sample": deepcopy(inputs["sample"]),
            "plan": {
                "path": plan.get("source"),
                "sha256": plan.get("sha256"),
            },
            "worldgen_artifact": {
                "path": artifact.get("path"),
                "sha256": artifact.get("sha256"),
                "size_bytes": artifact.get("size_bytes"),
                "build_skipped": artifact.get("build_skipped"),
            },
            "runtime": outputs.get("runtime"),
            "summary": self.summary_binding.public(
                kind="world-studio-diagnostic-summary", authority="World Studio"
            ),
            "strata_manifest": {
                **self.region.manifest_binding.source(
                    source_id=f"strata-region-manifest:sha256:{self.region.manifest_binding.sha256}",
                    kind="strata-region-manifest",
                    authority="Strata",
                    state="observed-final",
                ),
                "tile_set_sha256": self.region.tile_set_sha256,
                "tile_set_size_bytes": self.region.tile_set_size_bytes,
            },
            "viewer_handoff": self.handoff_binding.public(
                kind="strata-viewer-handoff", authority="Crucible / Strata"
            ),
            "viewer_url": self.handoff.get("url"),
            "jfr_summary": (
                None
                if self.jfr_binding is None
                else self.jfr_binding.public(
                    kind="world-studio-jfr-summary", authority="JDK Flight Recorder"
                )
            ),
        }


def _load_side(name: str, report_path: Path | str, *, subsurface_profile: Mapping[str, Any]) -> Side:
    report, report_binding = _binding(report_path, f"{name} iteration report")
    require(report.get("format") == ITERATION_FORMAT and report.get("schema_version") == 1, f"{name} is not a Worldgen Iteration V1 report")
    require(report.get("status") == "complete" and report.get("failure") is None, f"{name} iteration did not complete successfully")
    require(isinstance(report.get("inputs"), Mapping), f"{name} iteration inputs are invalid")
    inputs = report["inputs"]
    _verify_input_file(
        inputs.get("profile"),
        context=f"{name} iteration profile",
        path_key="file",
        require_size=False,
    )
    _verify_input_file(
        inputs.get("plan"),
        context=f"{name} frozen worldgen plan",
        path_key="source",
        require_size=False,
    )
    _verify_input_file(
        inputs.get("worldgen_artifact"),
        context=f"{name} worldgen artifact",
        path_key="path",
        require_size=True,
    )
    for stage_id in ("preflight", "build", "provision", "configure", "capture", "summarize", "handoff"):
        stage = _stage(report, stage_id)
        require(stage is not None and stage.get("status") == "complete", f"{name} iteration lacks a complete {stage_id} stage")

    summary_path = _output_path(report, "world_studio_summary", f"{name} World Studio summary")
    summary, summary_binding = _binding(summary_path, f"{name} World Studio summary")
    runtime = summary.get("runtime")
    require(
        isinstance(runtime, Mapping)
        and runtime.get("ready") is True
        and runtime.get("clean_stop_observed") is True
        and runtime.get("invalid_prototype_records") == 0,
        f"{name} diagnostic summary is not a healthy completed run",
    )
    require(summary.get("prototype_failures") == [], f"{name} recorded World Studio failures")

    handoff_path = _output_path(report, "viewer_handoff", f"{name} Strata viewer handoff")
    handoff, handoff_binding = _binding(handoff_path, f"{name} Strata viewer handoff")
    manifest_raw = handoff.get("manifest")
    require(isinstance(manifest_raw, str) and bool(manifest_raw), f"{name} viewer handoff lacks a manifest")
    manifest_path = Path(manifest_raw).expanduser()
    require(not manifest_path.is_symlink(), f"{name} Strata manifest cannot be a symlink")
    manifest_path = manifest_path.resolve(strict=True)
    require(manifest_path.is_file(), f"{name} Strata manifest is unavailable")
    external_root = handoff.get("externalArtifactRoot")
    require(isinstance(external_root, str), f"{name} handoff lacks externalArtifactRoot")
    require(manifest_path.parent == Path(external_root).resolve(strict=True), f"{name} handoff manifest/root binding drift")
    region = load_strata_region(manifest_path, profile=subsurface_profile)

    outputs = report["outputs"]
    runtime_path = Path(str(outputs.get("runtime", ""))).expanduser()
    require(bool(str(outputs.get("runtime", ""))), f"{name} iteration lacks its runtime path")
    require(not runtime_path.is_symlink(), f"{name} runtime cannot be a symlink")
    runtime_path = runtime_path.resolve(strict=True)
    require(runtime_path.is_dir(), f"{name} runtime is unavailable")
    jfr_path_raw = outputs.get("jfr_summary")
    if jfr_path_raw is None:
        jfr = None
        jfr_binding = None
    else:
        jfr, jfr_binding = _binding(Path(jfr_path_raw), f"{name} JFR summary")
        require(jfr.get("invalid_events") == 0, f"{name} JFR summary contains invalid events")

    return Side(
        name=name,
        report=report,
        report_binding=report_binding,
        summary=summary,
        summary_binding=summary_binding,
        handoff=handoff,
        handoff_binding=handoff_binding,
        manifest_path=manifest_path,
        region=region,
        jfr=jfr,
        jfr_binding=jfr_binding,
    )


def _runtime_mod_identity(side: Side) -> list[tuple[tuple[str, ...], str]]:
    artifact_sha = side.report["inputs"]["worldgen_artifact"].get("sha256")
    rows = []
    for mod in side.report["inputs"].get("runtime_mods", []):
        if not isinstance(mod, Mapping) or mod.get("sha256") == artifact_sha:
            continue
        mod_ids = mod.get("mod_ids")
        digest = mod.get("sha256")
        if isinstance(mod_ids, list) and isinstance(digest, str):
            rows.append((tuple(sorted(str(item) for item in mod_ids)), digest))
    return sorted(rows)


def _server_jar_sha(side: Side) -> str | None:
    provision = _stage(side.report, "provision")
    if not isinstance(provision, Mapping):
        return None
    details = provision.get("details")
    audit = details.get("template_audit") if isinstance(details, Mapping) else None
    server = audit.get("server_jar") if isinstance(audit, Mapping) else None
    return server.get("sha256") if isinstance(server, Mapping) else None


def _alignment(
    baseline: Side,
    candidate: Side,
    *,
    profile: Mapping[str, Any],
    iteration_profile: Mapping[str, Any],
    iteration_profile_binding: FileBinding,
) -> dict[str, Any]:
    left_inputs = baseline.report["inputs"]
    right_inputs = candidate.report["inputs"]
    left_sample = left_inputs.get("sample")
    right_sample = right_inputs.get("sample")
    checks: list[dict[str, Any]] = []

    def check(
        check_id: str,
        left: Any,
        right: Any,
        *,
        required_value: Any = None,
        require_present: bool = False,
    ) -> None:
        aligned = (
            left == right
            and (required_value is None or left == required_value)
            and (not require_present or left is not None)
        )
        checks.append(
            {
                "check_id": check_id,
                "aligned": aligned,
                "baseline": deepcopy(left),
                "candidate": deepcopy(right),
            }
        )

    check(
        "iteration-profile-id",
        left_inputs["profile"].get("profile_id"),
        right_inputs["profile"].get("profile_id"),
        required_value=iteration_profile.get("profile_id"),
        require_present=True,
    )
    check(
        "iteration-profile-sha256",
        left_inputs["profile"].get("sha256"),
        right_inputs["profile"].get("sha256"),
        required_value=iteration_profile_binding.sha256,
        require_present=True,
    )
    check("platform-profile", left_inputs["profile"].get("platform_profile_id"), right_inputs["profile"].get("platform_profile_id"))
    check("mode", baseline.report.get("mode"), candidate.report.get("mode"))
    check("seed", left_sample.get("seed") if isinstance(left_sample, Mapping) else None, right_sample.get("seed") if isinstance(right_sample, Mapping) else None)
    check("chunk-region", left_sample.get("region") if isinstance(left_sample, Mapping) else None, right_sample.get("region") if isinstance(right_sample, Mapping) else None)
    check("strata-scope", baseline.region.scope, candidate.region.scope)
    check("dimension", baseline.region.scope["dimension_id"], candidate.region.scope["dimension_id"], required_value=profile["dimension_id"])
    check("runtime-mod-set-excluding-subject", _runtime_mod_identity(baseline), _runtime_mod_identity(candidate))
    check(
        "cleanroom-server",
        _server_jar_sha(baseline),
        _server_jar_sha(candidate),
        require_present=True,
    )
    check(
        "cleanroom-java",
        left_inputs.get("toolchains", {}).get("java", {}).get("sha256"),
        right_inputs.get("toolchains", {}).get("java", {}).get("sha256"),
        require_present=True,
    )
    check("separate-runtime", baseline.report["outputs"].get("runtime") != candidate.report["outputs"].get("runtime"), True, required_value=True)
    check("separate-final-capture", str(baseline.manifest_path) != str(candidate.manifest_path), True, required_value=True)

    seed = left_sample.get("seed") if isinstance(left_sample, Mapping) else None
    region = left_sample.get("region") if isinstance(left_sample, Mapping) else None
    return {
        "aligned": all(row["aligned"] for row in checks),
        "seed": seed,
        "dimension_id": baseline.region.scope["dimension_id"],
        "chunk_region": deepcopy(region),
        "chunk_window": deepcopy(baseline.region.scope["chunk_window"]),
        "checks": checks,
        "failed_checks": [row["check_id"] for row in checks if not row["aligned"]],
    }


def _properties(block_state: str) -> dict[str, str]:
    if "[" not in block_state or not block_state.endswith("]"):
        return {}
    result: dict[str, str] = {}
    for item in block_state.split("[", 1)[1][:-1].split(","):
        key, separator, value = item.partition("=")
        if separator and key and value:
            result[key] = value
    return result


def _material(block_state: str, prefix: str) -> str | None:
    if not block_state.startswith(prefix):
        return None
    token = block_state[len(prefix) :].split("[", 1)[0]
    stem, separator, suffix = token.rpartition("_")
    return stem if separator and stem and suffix.isdigit() else token


def _host(block_state: str, semantics: Mapping[str, Any]) -> str | None:
    known = set(semantics["known_host_variants"])
    properties = _properties(block_state)
    for key in semantics["host_properties"]:
        if properties.get(key) in known:
            return properties[key]
    return None


@dataclass
class _TopologyScan:
    air: set[tuple[int, int, int]]
    ore: set[tuple[int, int, int]]
    fluid: set[tuple[int, int, int]]
    entrances: set[tuple[int, int, int]]
    ore_materials: Counter[str]
    ore_heights: Counter[int]
    truncated: bool = False


def _new_scan() -> _TopologyScan:
    return _TopologyScan(set(), set(), set(), set(), Counter(), Counter())


def _record_topology(
    scan: _TopologyScan,
    *,
    point: tuple[int, int, int],
    state: str,
    surface: int,
    air_states: set[str],
    ore_prefix: str,
    fluid_prefixes: tuple[str, ...],
    limit: int,
) -> None:
    x, y, z = point
    material = _material(state, ore_prefix)
    if material is not None:
        scan.ore_materials[material] += 1
        scan.ore_heights[y // 8 * 8] += 1
    if scan.truncated:
        return
    target: set[tuple[int, int, int]] | None = None
    if y < surface and state in air_states:
        target = scan.air
        if y == surface - 1:
            scan.entrances.add(point)
    elif material is not None:
        target = scan.ore
    elif state.startswith(fluid_prefixes):
        target = scan.fluid
    if target is None:
        return
    if len(scan.air) + len(scan.ore) + len(scan.fluid) >= limit:
        scan.air.clear()
        scan.ore.clear()
        scan.fluid.clear()
        scan.entrances.clear()
        scan.truncated = True
        return
    target.add((x, y, z))


def _neighbors(point: tuple[int, int, int]) -> tuple[tuple[int, int, int], ...]:
    x, y, z = point
    return (
        (x - 1, y, z),
        (x + 1, y, z),
        (x, y - 1, z),
        (x, y + 1, z),
        (x, y, z - 1),
        (x, y, z + 1),
    )


def _quantile(values: Sequence[float | int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _components(
    points: set[tuple[int, int, int]], *, entrances: set[tuple[int, int, int]] | None = None
) -> dict[str, Any]:
    remaining = set(points)
    sizes: list[int] = []
    accessible = 0
    accessible_components = 0
    while remaining:
        first = remaining.pop()
        queue = deque([first])
        size = 0
        reaches_entrance = False
        while queue:
            point = queue.popleft()
            size += 1
            if entrances is not None and point in entrances:
                reaches_entrance = True
            for neighbor in _neighbors(point):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        sizes.append(size)
        if reaches_entrance:
            accessible += size
            accessible_components += 1
    return {
        "component_count": len(sizes),
        "largest_component_blocks": max(sizes, default=0),
        "component_size_p50": int(_quantile(sizes, 0.50)),
        "component_size_p95": int(_quantile(sizes, 0.95)),
        "accessible_component_count": accessible_components,
        "accessible_blocks": accessible,
        "accessible_fraction": round(accessible / len(points), 6) if points else 0.0,
    }


def _topology(scan: _TopologyScan) -> dict[str, Any]:
    if scan.truncated:
        return {
            "state": "unavailable",
            "limitations": ["Exact topology exceeded the pack-profile block bound; volume and distribution counts remain available."],
            "caves": None,
            "ore": None,
        }
    cave_components = _components(scan.air, entrances=scan.entrances)
    cave_water_faces = sum(
        1 for point in scan.air for neighbor in _neighbors(point) if neighbor in scan.fluid
    )
    exposed_ore = sum(
        1 for point in scan.ore if any(neighbor in scan.air for neighbor in _neighbors(point))
    )
    ore_components = _components(scan.ore)
    return {
        "state": "derived-presentation",
        "limitations": [
            "Connected components are exact final-state geometry, not generator deposit or carver identities.",
            "An entrance is a final subsurface-air component touching the captured height surface.",
        ],
        "caves": {
            "subsurface_air_blocks": len(scan.air),
            "entrance_blocks": len(scan.entrances),
            "water_intersection_faces": cave_water_faces,
            **cave_components,
        },
        "ore": {
            "ore_blocks": len(scan.ore),
            "cave_accessible_blocks": exposed_ore,
            "cave_accessible_fraction": round(exposed_ore / len(scan.ore), 6) if scan.ore else 0.0,
            "materials": [
                {"material": material, "count": count}
                for material, count in sorted(scan.ore_materials.items(), key=lambda row: (-row[1], row[0]))
            ],
            "vertical_bands": [
                {"min_y": band, "max_y": band + 7, "count": count}
                for band, count in sorted(scan.ore_heights.items())
            ],
            **ore_components,
        },
    }


def _counter_delta(left: Counter[str], right: Counter[str], *, limit: int = 64) -> list[dict[str, Any]]:
    rows = [
        {
            "value": key,
            "baseline": left[key],
            "candidate": right[key],
            "delta": right[key] - left[key],
        }
        for key in set(left) | set(right)
        if left[key] != right[key]
    ]
    rows.sort(key=lambda row: (-abs(row["delta"]), row["value"]))
    return rows[:limit]


def _mad_outlier_scores(values: Sequence[int], threshold: float) -> list[float]:
    if not values:
        return []
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    if mad == 0:
        return [float("inf") if value > median else 0.0 for value in values]
    return [round(0.6745 * (value - median) / mad, 6) for value in values]


def _compare_final_state(
    baseline: StrataRegion,
    candidate: StrataRegion,
    *,
    semantics: Mapping[str, Any],
    limits: Mapping[str, int],
    thresholds: Mapping[str, float],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    window = baseline.window
    chunks = window["chunk_size_x"] * window["chunk_size_z"]
    comparisons = chunks * 16 * 4096
    require(comparisons <= limits["max_exact_block_comparisons"], f"exact block comparison requires {comparisons} cells, above the profile limit")
    require(chunks <= limits["max_chunks"], "chunk comparison exceeds the profile limit")

    left_scan = _new_scan()
    right_scan = _new_scan()
    chunk_rows: list[dict[str, Any]] = []
    total_changed = 0
    category_totals: Counter[str] = Counter()
    height_changed_columns = 0
    biome_changed_columns = 0
    height_absolute_deltas: list[int] = []
    left_biomes: Counter[str] = Counter()
    right_biomes: Counter[str] = Counter()
    sample_limit = limits["max_samples_per_chunk"]
    air_states = set(semantics["air_states"])
    ore_prefix = semantics["ore_prefix"]
    fluid_prefixes = tuple(semantics["fluid_prefixes"])

    for chunk_z in range(window["min_chunk_z"], window["min_chunk_z"] + window["chunk_size_z"]):
        for chunk_x in range(window["min_chunk_x"], window["min_chunk_x"] + window["chunk_size_x"]):
            chunk = (chunk_x, chunk_z)
            left_heights = baseline.heights[chunk]
            right_heights = candidate.heights[chunk]
            left_chunk_biomes = baseline.biomes[chunk]
            right_chunk_biomes = candidate.biomes[chunk]
            left_biomes.update(left_chunk_biomes)
            right_biomes.update(right_chunk_biomes)
            chunk_height_changed = sum(a != b for a, b in zip(left_heights, right_heights))
            chunk_biome_changed = sum(a != b for a, b in zip(left_chunk_biomes, right_chunk_biomes))
            height_changed_columns += chunk_height_changed
            biome_changed_columns += chunk_biome_changed
            height_absolute_deltas.extend(abs(b - a) for a, b in zip(left_heights, right_heights))

            categories: Counter[str] = Counter()
            samples: list[dict[str, Any]] = []
            for y_section in range(16):
                left_indices = baseline.dense_section_indices(
                    chunk_x, chunk_z, y_section
                )
                right_indices = candidate.dense_section_indices(
                    chunk_x, chunk_z, y_section
                )
                for offset in range(4096):
                    local_y = offset // 256
                    remainder = offset % 256
                    local_z = remainder // 16
                    local_x = remainder % 16
                    world_x = chunk_x * 16 + local_x
                    world_y = y_section * 16 + local_y
                    world_z = chunk_z * 16 + local_z
                    surface_index = local_z * 16 + local_x
                    left_state = (
                        AIR_FALLBACK
                        if left_indices is None
                        else baseline.block_palette[left_indices[offset]]
                    )
                    right_state = (
                        AIR_FALLBACK
                        if right_indices is None
                        else candidate.block_palette[right_indices[offset]]
                    )
                    point = (world_x, world_y, world_z)
                    _record_topology(
                        left_scan,
                        point=point,
                        state=left_state,
                        surface=left_heights[surface_index],
                        air_states=air_states,
                        ore_prefix=ore_prefix,
                        fluid_prefixes=fluid_prefixes,
                        limit=limits["max_topology_blocks"],
                    )
                    _record_topology(
                        right_scan,
                        point=point,
                        state=right_state,
                        surface=right_heights[surface_index],
                        air_states=air_states,
                        ore_prefix=ore_prefix,
                        fluid_prefixes=fluid_prefixes,
                        limit=limits["max_topology_blocks"],
                    )
                    if left_state == right_state:
                        continue
                    left_air = world_y < left_heights[surface_index] and left_state in air_states
                    right_air = world_y < right_heights[surface_index] and right_state in air_states
                    if left_air != right_air:
                        category = "cave-space"
                    elif left_state.startswith(ore_prefix) or right_state.startswith(ore_prefix):
                        category = "ore"
                    elif _host(left_state, semantics) != _host(right_state, semantics) and (
                        _host(left_state, semantics) is not None or _host(right_state, semantics) is not None
                    ):
                        category = "lithology"
                    elif left_state.startswith(fluid_prefixes) or right_state.startswith(fluid_prefixes):
                        category = "physical-fluid"
                    else:
                        category = "other-block"
                    categories[category] += 1
                    category_totals[category] += 1
                    total_changed += 1
                    if len(samples) < sample_limit:
                        samples.append(
                            {
                                "position": [world_x, world_y, world_z],
                                "category": category,
                                "baseline": left_state,
                                "candidate": right_state,
                            }
                        )

            left_metric = baseline.chunk_metrics[chunk]
            right_metric = candidate.chunk_metrics[chunk]
            changed_blocks = sum(categories.values())
            if changed_blocks or chunk_height_changed or chunk_biome_changed:
                chunk_rows.append(
                    {
                        "chunk_x": chunk_x,
                        "chunk_z": chunk_z,
                        "changed_blocks": changed_blocks,
                        "change_rate": round(changed_blocks / 65536, 8),
                        "categories": dict(sorted(categories.items())),
                        "height_changed_columns": chunk_height_changed,
                        "mean_height_delta": round(right_metric.mean_height - left_metric.mean_height, 6),
                        "biome_changed_columns": chunk_biome_changed,
                        "cave_air": {
                            "baseline": left_metric.subsurface_air,
                            "candidate": right_metric.subsurface_air,
                            "delta": right_metric.subsurface_air - left_metric.subsurface_air,
                        },
                        "ore_blocks": {
                            "baseline": sum(left_metric.ore_counts.values()),
                            "candidate": sum(right_metric.ore_counts.values()),
                            "delta": sum(right_metric.ore_counts.values()) - sum(left_metric.ore_counts.values()),
                        },
                        "dominant_biome": {
                            "baseline": left_metric.dominant_biome,
                            "candidate": right_metric.dominant_biome,
                        },
                        "samples": samples,
                    }
                )

    scores = _mad_outlier_scores(
        [row["changed_blocks"] + row["height_changed_columns"] + row["biome_changed_columns"] for row in chunk_rows],
        thresholds["chunk_outlier_mad"],
    )
    for row, score in zip(chunk_rows, scores):
        row["outlier_score"] = score
        row["outlier"] = score == float("inf") or score >= thresholds["chunk_outlier_mad"]
    chunk_rows.sort(key=lambda row: (-row["changed_blocks"], row["chunk_z"], row["chunk_x"]))

    left_topology = _topology(left_scan)
    right_topology = _topology(right_scan)
    block_deltas = _counter_delta(baseline.block_counts, candidate.block_counts)
    total_cells = comparisons
    final = {
        "state": "observed-final",
        "authority": "Strata",
        "equivalent": (
            total_changed == 0 and height_changed_columns == 0 and biome_changed_columns == 0
        ),
        "summary": {
            "compared_chunks": chunks,
            "compared_block_positions": total_cells,
            "changed_block_positions": total_changed,
            "changed_block_fraction": round(total_changed / total_cells, 10),
            "height_changed_columns": height_changed_columns,
            "biome_changed_columns": biome_changed_columns,
            "changed_chunks": len(chunk_rows),
            "category_totals": dict(sorted(category_totals.items())),
        },
        "block_state_deltas": block_deltas,
        "chunks": chunk_rows,
        "topology": {"baseline": left_topology, "candidate": right_topology},
        "limitations": [
            "Final-state differences do not by themselves identify the responsible generator.",
            "Cave and ore components are geometric final-state components, not carver or GTCEu deposit instance identities.",
        ],
    }

    def relative(before: int, after: int) -> float | None:
        return None if before == 0 else round((after - before) / before, 6)

    left_caves = left_topology.get("caves") or {}
    right_caves = right_topology.get("caves") or {}
    left_ore = left_topology.get("ore") or {}
    right_ore = right_topology.get("ore") or {}
    cave_before = int(left_caves.get("subsurface_air_blocks", sum(row.subsurface_air for row in baseline.chunk_metrics.values())))
    cave_after = int(right_caves.get("subsurface_air_blocks", sum(row.subsurface_air for row in candidate.chunk_metrics.values())))
    ore_before = int(left_ore.get("ore_blocks", sum(baseline.resource_counts.values())))
    ore_after = int(right_ore.get("ore_blocks", sum(candidate.resource_counts.values())))
    cave_relative = relative(cave_before, cave_after)
    ore_relative = relative(ore_before, ore_after)
    statistics_result = {
        "state": "derived-presentation",
        "authority": "Workbench over aligned Strata observations",
        "paired_sample": {"seed_count": 1, "chunk_count": chunks},
        "height_absolute_delta": {
            "mean": round(statistics.fmean(height_absolute_deltas), 6) if height_absolute_deltas else 0.0,
            "p50": _quantile(height_absolute_deltas, 0.50),
            "p95": _quantile(height_absolute_deltas, 0.95),
            "maximum": max(height_absolute_deltas, default=0),
        },
        "cave_volume": {
            "baseline": cave_before,
            "candidate": cave_after,
            "delta": cave_after - cave_before,
            "relative_delta": cave_relative,
            "meaningful": cave_relative is not None and abs(cave_relative) >= thresholds["cave_volume_relative"],
        },
        "ore_mass": {
            "baseline": ore_before,
            "candidate": ore_after,
            "delta": ore_after - ore_before,
            "relative_delta": ore_relative,
            "meaningful": ore_relative is not None and abs(ore_relative) >= thresholds["ore_mass_relative"],
        },
        "biome_distribution": {
            "baseline": dict(sorted(left_biomes.items())),
            "candidate": dict(sorted(right_biomes.items())),
            "total_variation_distance": _total_variation(left_biomes, right_biomes),
        },
        "changed_blocks_per_changed_chunk": {
            "p50": _quantile([row["changed_blocks"] for row in chunk_rows], 0.50),
            "p95": _quantile([row["changed_blocks"] for row in chunk_rows], 0.95),
            "maximum": max((row["changed_blocks"] for row in chunk_rows), default=0),
        },
        "outliers": [
            deepcopy(row)
            for row in chunk_rows
            if row["outlier"]
        ][: limits["max_outliers"]],
        "limitations": [
            "One fixed seed supports paired spatial statistics, not population-level claims across seeds.",
            "Meaningful flags use pack-owned practical thresholds; they are not statistical significance tests.",
        ],
    }
    visual = _visual(window, chunk_rows, limits["max_outliers"])
    return final, statistics_result, visual


def _total_variation(left: Counter[str], right: Counter[str]) -> float:
    left_total = sum(left.values())
    right_total = sum(right.values())
    if not left_total and not right_total:
        return 0.0
    keys = set(left) | set(right)
    distance = sum(
        abs((left[key] / left_total if left_total else 0.0) - (right[key] / right_total if right_total else 0.0))
        for key in keys
    ) / 2
    return round(distance, 8)


def _visual(window: Mapping[str, int], changed_rows: Sequence[Mapping[str, Any]], max_outliers: int) -> dict[str, Any]:
    by_chunk = {(row["chunk_x"], row["chunk_z"]): row for row in changed_rows}
    cells = []
    maximum = max((row["changed_blocks"] for row in changed_rows), default=0)
    for chunk_z in range(window["min_chunk_z"], window["min_chunk_z"] + window["chunk_size_z"]):
        for chunk_x in range(window["min_chunk_x"], window["min_chunk_x"] + window["chunk_size_x"]):
            row = by_chunk.get((chunk_x, chunk_z))
            changed = 0 if row is None else row["changed_blocks"]
            cells.append(
                {
                    "chunk_x": chunk_x,
                    "chunk_z": chunk_z,
                    "changed_blocks": changed,
                    "intensity": round(changed / maximum, 6) if maximum else 0.0,
                    "categories": {} if row is None else deepcopy(row["categories"]),
                    "outlier": False if row is None else row["outlier"],
                    "samples": [] if row is None else deepcopy(row["samples"]),
                }
            )
    return {
        "kind": "aligned-chunk-difference-grid",
        "min_chunk_x": window["min_chunk_x"],
        "min_chunk_z": window["min_chunk_z"],
        "width": window["chunk_size_x"],
        "height": window["chunk_size_z"],
        "cells": cells,
        "legend": ["unchanged", "low", "medium", "high", "outlier"],
        "top_outliers": [deepcopy(row) for row in changed_rows[:max_outliers]],
    }


def _semantic_comparison(root: Path, baseline: Side, candidate: Side, iteration_profile: Mapping[str, Any]) -> dict[str, Any]:
    fixture_raw = iteration_profile.get("fixture")
    require(isinstance(fixture_raw, str), "iteration profile fixture is invalid")
    script = root / fixture_raw / "tools/compare_world_studio_logs.py"
    require(script.is_file(), f"World Studio semantic comparator is unavailable: {script}")
    baseline_log = _output_path(baseline.report, "cleanroom_launch_log", "baseline launch log")
    candidate_log = _output_path(candidate.report, "cleanroom_launch_log", "candidate launch log")
    completed = subprocess.run(
        [sys.executable, str(script), str(baseline_log), str(candidate_log), "--compact"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    require(completed.returncode in {0, 1}, f"World Studio semantic comparator failed: {completed.stderr.strip() or completed.stdout.strip()}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CockpitError(f"World Studio semantic comparator returned invalid JSON: {exc}") from exc
    require(isinstance(value, dict) and value.get("runtime", {}).get("usable") is True, "World Studio semantic comparison is unusable")
    changed_chunks = sum(
        len(record.get("changed_chunks", []))
        + len(record.get("missing_from_candidate", []))
        + len(record.get("missing_from_baseline", []))
        for record in value.get("records", {}).values()
        if isinstance(record, Mapping)
    )
    return {
        "state": "observed-diagnostic",
        "authority": "World Studio diagnostics retained by Crucible",
        "equivalent": value.get("equivalent") is True,
        "summary": {
            "changed_or_missing_chunk_records": changed_chunks,
            "plan_identity_equivalent": value.get("plan_identity", {}).get("equivalent"),
            "generator_identity_equivalent": value.get("generator_identity", {}).get("equivalent"),
            "excluded_telemetry_fields": value.get("excluded_telemetry_fields", []),
        },
        "details": value,
        "limitations": [
            "Diagnostic equality is bounded to emitted World Studio records and the comparator telemetry allowlist.",
            "A first causal divergence requires separately admitted Worldgen Observatory evidence.",
        ],
    }


def _flatten_jfr(summary: Mapping[str, Any]) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {}
    stages = summary.get("chunk_stages")
    if isinstance(stages, Mapping):
        for stage_id, value in stages.items():
            if not isinstance(value, Mapping):
                continue
            count = value.get("count")
            if isinstance(count, (int, float)) and not isinstance(count, bool):
                metrics[f"chunk_stages.{stage_id}.count"] = count
            latency = value.get("latency")
            if isinstance(latency, Mapping):
                for key in ("p50_us", "p95_us", "maximum_us"):
                    number = latency.get(key)
                    if isinstance(number, (int, float)) and not isinstance(number, bool):
                        metrics[f"chunk_stages.{stage_id}.{key}"] = number
    for group in ("chunk_sampling", "biome_areas", "watershed_tiles"):
        value = summary.get(group)
        if not isinstance(value, Mapping):
            continue
        latency = value.get("latency")
        if isinstance(latency, Mapping):
            for key in ("p50_us", "p95_us", "maximum_us"):
                number = latency.get(key)
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    metrics[f"{group}.{key}"] = number
    return metrics


def _jfr_delta(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], *, thresholds: Mapping[str, float]
) -> dict[str, Any]:
    left = _flatten_jfr(baseline)
    right = _flatten_jfr(candidate)
    rows: list[dict[str, Any]] = []
    for metric in sorted(set(left) | set(right)):
        before = left.get(metric)
        after = right.get(metric)
        if before is None or after is None:
            rows.append({"metric": metric, "baseline": before, "candidate": after, "delta": None, "ratio": None, "regression": False})
            continue
        delta = after - before
        ratio = None if before == 0 else round(after / before, 6)
        regression = (
            metric.endswith(("p50_us", "p95_us", "maximum_us"))
            and ratio is not None
            and ratio >= thresholds["performance_regression_ratio"]
            and delta >= thresholds["performance_regression_min_us"]
        )
        rows.append(
            {
                "metric": metric,
                "baseline": before,
                "candidate": after,
                "delta": delta,
                "ratio": ratio,
                "regression": regression,
            }
        )
    rows.sort(key=lambda row: (not row["regression"], -(row["delta"] or 0), row["metric"]))
    return {
        "metric_count": len(rows),
        "regression_count": sum(bool(row["regression"]) for row in rows),
        "regressions": [row for row in rows if row["regression"]],
        "metrics": rows,
    }


def _performance(baseline: Side, candidate: Side, *, thresholds: Mapping[str, float]) -> dict[str, Any]:
    if baseline.jfr is None or candidate.jfr is None:
        return {
            "state": "unavailable",
            "authority": "JDK Flight Recorder",
            "summary": None,
            "details": None,
            "limitations": ["Both aligned sides need bounded JFR summaries; rerun in performance mode."],
        }
    delta = _jfr_delta(baseline.jfr, candidate.jfr, thresholds=thresholds)
    return {
        "state": "observed-jfr",
        "authority": "JDK Flight Recorder / World Studio summarizer",
        "summary": {
            "metric_count": delta["metric_count"],
            "regression_count": delta["regression_count"],
        },
        "details": delta,
        "limitations": [
            "One process per side exposes practical regressions but not a stable benchmark confidence interval.",
            "Maximum latency is retained as an outlier signal and should not be read as a central tendency.",
        ],
    }


def _observer_overhead(
    baseline: Side,
    candidate: Side,
    *,
    baseline_off: Path | None,
    candidate_off: Path | None,
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    if baseline_off is None or candidate_off is None:
        return {
            "state": "unavailable",
            "authority": "JDK Flight Recorder",
            "summary": None,
            "details": None,
            "limitations": ["Observer-off JFR summaries were not supplied for both sides; instrumentation overhead is not measured."],
        }
    require(baseline.jfr is not None and candidate.jfr is not None, "observer overhead requires paired observer-on JFR summaries")
    left_off, left_binding = _binding(baseline_off, "baseline observer-off JFR summary")
    right_off, right_binding = _binding(candidate_off, "candidate observer-off JFR summary")
    require(left_off.get("invalid_events") == 0 and right_off.get("invalid_events") == 0, "observer-off JFR summary contains invalid events")
    details = {
        "baseline": _jfr_delta(left_off, baseline.jfr, thresholds=thresholds),
        "candidate": _jfr_delta(right_off, candidate.jfr, thresholds=thresholds),
        "sources": {
            "baseline_observer_off": left_binding.public(kind="observer-off-jfr-summary", authority="JDK Flight Recorder"),
            "candidate_observer_off": right_binding.public(kind="observer-off-jfr-summary", authority="JDK Flight Recorder"),
        },
    }
    return {
        "state": "observed-jfr",
        "authority": "JDK Flight Recorder",
        "summary": {
            "baseline_regression_count": details["baseline"]["regression_count"],
            "candidate_regression_count": details["candidate"]["regression_count"],
        },
        "details": details,
        "limitations": ["Observer overhead is bounded to the supplied observer-on/off summaries and their JFR event domains."],
    }


def _input_relation(baseline: Side, candidate: Side) -> dict[str, Any]:
    """Classify an A/A control separately from a changed-input experiment.

    Fixed seed and matching runtime dependencies are necessary for comparison,
    but they do not prove that a final-state delta came from the candidate.
    When the subject artifact and frozen plan are also byte-identical, any
    observed delta is a reproducibility failure rather than a candidate effect.
    """

    left_inputs = baseline.report["inputs"]
    right_inputs = candidate.report["inputs"]
    checks = {
        "plan_sha256": (
            left_inputs.get("plan", {}).get("sha256"),
            right_inputs.get("plan", {}).get("sha256"),
        ),
        "worldgen_artifact_sha256": (
            left_inputs.get("worldgen_artifact", {}).get("sha256"),
            right_inputs.get("worldgen_artifact", {}).get("sha256"),
        ),
    }
    changed = [name for name, (left, right) in checks.items() if left != right]
    return {
        "kind": "identical-input-control" if not changed else "before-after",
        "changed_inputs": changed,
        "checks": [
            {
                "input": name,
                "identical": left == right,
                "baseline": left,
                "candidate": right,
            }
            for name, (left, right) in checks.items()
        ],
    }


def _causal(
    baseline: Side,
    candidate: Side,
    baseline_bundle: Path | None,
    candidate_bundle: Path | None,
    *,
    comparison_scope_sha256: str | None,
) -> dict[str, Any]:
    if baseline_bundle is None and candidate_bundle is None:
        return {
            "state": "unavailable",
            "authority": "Atlas",
            "summary": None,
            "details": None,
            "sources": [],
            "limitations": ["No paired Worldgen Observatory bundles were supplied; no causal first-divergence claim is made."],
        }
    require(baseline_bundle is not None and candidate_bundle is not None, "causal comparison requires both Observatory bundles")
    left, left_binding = _binding(baseline_bundle, "baseline Worldgen Observatory bundle")
    right, right_binding = _binding(candidate_bundle, "candidate Worldgen Observatory bundle")
    left_scopes = {
        row.get("scope", {}).get("comparison_scope_sha256")
        for row in left.get("semantic_fingerprints", [])
        if isinstance(row, Mapping)
    }
    right_scopes = {
        row.get("scope", {}).get("comparison_scope_sha256")
        for row in right.get("semantic_fingerprints", [])
        if isinstance(row, Mapping)
    }
    common = sorted(item for item in left_scopes & right_scopes if isinstance(item, str))
    if comparison_scope_sha256 is None:
        require(len(common) == 1, "Observatory bundles do not have exactly one common comparison scope; pass --comparison-scope-sha256")
        comparison_scope_sha256 = common[0]
    require(comparison_scope_sha256 in common, "selected Observatory comparison scope is not present in both bundles")

    def relation(bundle: Mapping[str, Any], side: Side, label: str) -> dict[str, Any]:
        """Bind an Observatory scope to this experiment without claiming same-process provenance."""

        run = bundle.get("run")
        require(isinstance(run, Mapping), f"{label} Observatory bundle lacks a run manifest")
        environment = run.get("environment")
        world = run.get("world")
        require(isinstance(environment, Mapping), f"{label} Observatory bundle lacks its environment identity")
        require(isinstance(world, Mapping), f"{label} Observatory bundle lacks its world identity")

        inputs = side.report["inputs"]
        sample = inputs.get("sample")
        require(isinstance(sample, Mapping), f"{label} iteration sample is invalid")
        seed = sample.get("seed")
        require(isinstance(seed, int) and not isinstance(seed, bool), f"{label} iteration seed is invalid")
        expected_seed_sha256 = hashlib.sha256(str(seed).encode("utf-8")).hexdigest()
        require(
            world.get("world_seed_sha256") == expected_seed_sha256,
            f"{label} Observatory seed does not match the iteration seed",
        )

        expected_platform = inputs.get("profile", {}).get("platform_profile_id")
        require(
            isinstance(expected_platform, str)
            and environment.get("platform_profile_id") == expected_platform,
            f"{label} Observatory platform does not match the iteration platform",
        )

        dimension_id = side.region.scope["dimension_id"]
        dimensions = world.get("dimension_ids")
        require(
            isinstance(dimensions, list) and dimension_id in dimensions,
            f"{label} Observatory capture does not include iteration dimension {dimension_id}",
        )
        fingerprints = [
            row
            for row in bundle.get("semantic_fingerprints", [])
            if isinstance(row, Mapping)
            and isinstance(row.get("scope"), Mapping)
            and row["scope"].get("comparison_scope_sha256") == comparison_scope_sha256
        ]
        require(fingerprints, f"{label} Observatory bundle lacks the selected fingerprint cohort")

        window = side.region.window
        min_x = window["min_chunk_x"]
        min_z = window["min_chunk_z"]
        max_x = min_x + window["chunk_size_x"]
        max_z = min_z + window["chunk_size_z"]
        chunks: set[tuple[int, int, int]] = set()
        for fingerprint in fingerprints:
            scope = fingerprint["scope"]
            chunk = scope.get("chunk")
            scope_dimension = scope.get("dimension_id")
            require(
                scope_dimension == dimension_id,
                f"{label} Observatory fingerprint dimension is outside the iteration scope",
            )
            require(
                isinstance(chunk, Mapping)
                and set(chunk) == {"x", "z"}
                and all(
                    isinstance(chunk[key], int) and not isinstance(chunk[key], bool)
                    for key in ("x", "z")
                ),
                f"{label} Observatory fingerprint chunk is invalid",
            )
            chunk_x, chunk_z = chunk["x"], chunk["z"]
            require(
                min_x <= chunk_x < max_x and min_z <= chunk_z < max_z,
                f"{label} Observatory fingerprint chunk {chunk_x},{chunk_z} is outside the iteration window",
            )
            chunks.add((scope_dimension, chunk_x, chunk_z))
        return {
            "iteration_seed_sha256": expected_seed_sha256,
            "platform_profile_id": expected_platform,
            "dimension_id": dimension_id,
            "chunks": [list(item) for item in sorted(chunks)],
            "same_process_bound": False,
        }

    left_relation = relation(left, baseline, "baseline")
    right_relation = relation(right, candidate, "candidate")
    require(
        left_relation["chunks"] == right_relation["chunks"],
        "selected Observatory fingerprint cohorts do not cover the same chunks",
    )
    from workbench_atlas_worldgen import first_divergence

    answer = first_divergence(left, right, comparison_scope_sha256=comparison_scope_sha256)
    require(answer.get("contract_id") == ATLAS_QUERY_CONTRACT, "Atlas returned an unsupported causal answer")
    status = answer.get("status")
    closed = status in {"equal", "diverged"}
    return {
        "state": "evidence-closed" if closed else "unresolved",
        "authority": "Atlas",
        "summary": {
            "status": status,
            "comparison_scope_sha256": comparison_scope_sha256,
            "first_difference": (answer.get("result") or {}).get("first_difference"),
            "experiment_relation": {
                "state": "seed-platform-dimension-window-aligned",
                "baseline": left_relation,
                "candidate": right_relation,
            },
        },
        "details": answer,
        "sources": [
            left_binding.public(kind="worldgen-observatory-bundle", authority="Crucible"),
            right_binding.public(kind="worldgen-observatory-bundle", authority="Crucible"),
        ],
        "limitations": sorted(
            set(answer.get("limitations", []))
            | {
                "The Observatory evidence is aligned to the experiment seed, platform, dimension, and chunk window but is not claimed to come from the same runtime process."
            }
        ),
    }


def _subsurface(
    *,
    root: Path,
    profile: Mapping[str, Any],
    profile_binding: Any,
    baseline: Side,
    candidate: Side,
    baseline_inventory: Path | None,
    candidate_inventory: Path | None,
    baseline_impact: Path | None,
    candidate_impact: Path | None,
    baseline_trace: Path | None,
    candidate_trace: Path | None,
) -> dict[str, Any]:
    if baseline_inventory is None and candidate_inventory is None:
        return {
            "state": "unavailable",
            "authority": "Subsurface Studio composition",
            "summary": None,
            "details": None,
            "limitations": ["Exact aligned GTCEu inventories were not supplied; generic ore/lithology/cave geometry remains available from Strata."],
        }
    require(baseline_inventory is not None and candidate_inventory is not None, "Subsurface Studio comparison requires both GTCEu inventories")
    left = load_subsurface_dataset(
        root=root,
        profile=profile,
        profile_binding=profile_binding,
        inventory_path=baseline_inventory,
        impact_path=baseline_impact,
        manifest_path=baseline.manifest_path,
        trace_path=baseline_trace,
    )
    right = load_subsurface_dataset(
        root=root,
        profile=profile,
        profile_binding=profile_binding,
        inventory_path=candidate_inventory,
        impact_path=candidate_impact,
        manifest_path=candidate.manifest_path,
        trace_path=candidate_trace,
    )
    result = compare_subsurface(left, right)
    return {
        "state": "observed-controlled" if baseline_trace is not None and candidate_trace is not None else "observed-final",
        "authority": "Subsurface Studio composition over Strata and Crucible",
        "summary": deepcopy(result.get("result", {}).get("summary")),
        "details": result,
        "limitations": list(result.get("limitations", [])),
    }


def _unavailable_evidence(authority: str, limitation: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "authority": authority,
        "summary": None,
        "details": None,
        "limitations": [limitation],
    }


def analyze_pair(
    *,
    root: Path,
    cockpit_profile: Mapping[str, Any],
    cockpit_profile_binding: FileBinding,
    baseline_report: Path,
    candidate_report: Path,
    reproduction_command: str,
    baseline_observatory_bundle: Path | None = None,
    candidate_observatory_bundle: Path | None = None,
    comparison_scope_sha256: str | None = None,
    baseline_inventory: Path | None = None,
    candidate_inventory: Path | None = None,
    baseline_impact: Path | None = None,
    candidate_impact: Path | None = None,
    baseline_trace: Path | None = None,
    candidate_trace: Path | None = None,
    baseline_observer_off_jfr: Path | None = None,
    candidate_observer_off_jfr: Path | None = None,
    review_path: Path | None = None,
) -> dict[str, Any]:
    subsurface_profile, subsurface_profile_binding = load_subsurface_profile(
        Path(cockpit_profile["_subsurface_profile"])
    )
    require(subsurface_profile["pack_profile"] == cockpit_profile["pack_profile"], "cockpit and subsurface pack profiles differ")
    iteration_profile, iteration_profile_binding = _binding(
        Path(cockpit_profile["_iteration_profile"]), "worldgen iteration profile"
    )
    baseline = _load_side("baseline", baseline_report, subsurface_profile=subsurface_profile)
    candidate = _load_side("candidate", candidate_report, subsurface_profile=subsurface_profile)
    mode = baseline.report.get("mode")
    require(mode in {"fast", "debug", "performance"}, "baseline iteration mode is unsupported")
    alignment = _alignment(
        baseline,
        candidate,
        profile=cockpit_profile,
        iteration_profile=iteration_profile,
        iteration_profile_binding=iteration_profile_binding,
    )
    limitations: list[str] = []

    if not alignment["aligned"]:
        reason = "Aligned comparison rejected: " + ", ".join(alignment["failed_checks"])
        limitations.append(reason)
        evidence = {
            "semantic": _unavailable_evidence("World Studio diagnostics", reason),
            "final_state": _unavailable_evidence("Strata", reason),
            "statistical": _unavailable_evidence("Workbench", reason),
            "subsurface": _unavailable_evidence("Subsurface Studio", reason),
            "causal": _unavailable_evidence("Atlas", reason),
            "performance": _unavailable_evidence("JDK Flight Recorder", reason),
            "observer_overhead": _unavailable_evidence("JDK Flight Recorder", reason),
        }
        visual = {
            "kind": "aligned-chunk-difference-grid",
            "min_chunk_x": 0,
            "min_chunk_z": 0,
            "width": 0,
            "height": 0,
            "cells": [],
            "legend": [],
            "top_outliers": [],
        }
        status = "incomparable"
        coverage = "incomparable"
        decision = {
            "headline": "The captures are not aligned and cannot support a before/after verdict.",
            "behavior_changed": None,
            "comparison_kind": "incomparable",
            "reproducibility_status": "unavailable",
            "performance_regression_count": None,
            "causal_status": "unavailable",
            "next_actions": ["Regenerate both sides from the same frozen seed, profile, dimension, mode, runtime dependency set, and chunk window."],
        }
    else:
        semantic = _semantic_comparison(root, baseline, candidate, iteration_profile)
        final_state, statistical, visual = _compare_final_state(
            baseline.region,
            candidate.region,
            semantics=subsurface_profile["state_semantics"],
            limits=cockpit_profile["limits"],
            thresholds=cockpit_profile["thresholds"],
        )
        subsurface = _subsurface(
            root=root,
            profile=subsurface_profile,
            profile_binding=subsurface_profile_binding,
            baseline=baseline,
            candidate=candidate,
            baseline_inventory=baseline_inventory,
            candidate_inventory=candidate_inventory,
            baseline_impact=baseline_impact,
            candidate_impact=candidate_impact,
            baseline_trace=baseline_trace,
            candidate_trace=candidate_trace,
        )
        causal = _causal(
            baseline,
            candidate,
            baseline_observatory_bundle,
            candidate_observatory_bundle,
            comparison_scope_sha256=comparison_scope_sha256,
        )
        performance = _performance(
            baseline, candidate, thresholds=cockpit_profile["thresholds"]
        )
        overhead = _observer_overhead(
            baseline,
            candidate,
            baseline_off=baseline_observer_off_jfr,
            candidate_off=candidate_observer_off_jfr,
            thresholds=cockpit_profile["thresholds"],
        )
        evidence = {
            "semantic": semantic,
            "final_state": final_state,
            "statistical": statistical,
            "subsurface": subsurface,
            "causal": causal,
            "performance": performance,
            "observer_overhead": overhead,
        }
        input_relation = _input_relation(baseline, candidate)
        observed_difference = not semantic["equivalent"] or not final_state["equivalent"]
        identical_control = input_relation["kind"] == "identical-input-control"
        if identical_control and observed_difference:
            status = "unstable"
            behavior_changed = None
            reproducibility_status = "failed-in-scope"
            limitations.append(
                "The frozen plan and subject artifact are byte-identical, so the observed delta is run-to-run instability and cannot be attributed to a candidate change."
            )
        elif identical_control:
            status = "equivalent"
            behavior_changed = False
            reproducibility_status = "stable-in-scope"
        else:
            behavior_changed = observed_difference
            status = "changed" if behavior_changed else "equivalent"
            reproducibility_status = "not-calibrated"
            limitations.append(
                "This changed-input pair does not estimate run-to-run noise; use an identical-input control before attributing small final-state deltas."
            )
        required_evidence = cockpit_profile["modes"][mode]["required_evidence"]
        complete = all(
            evidence[key]["state"] not in {"unavailable", "unresolved"}
            for key in required_evidence
        )
        coverage = "complete-for-mode" if complete else "partial"
        missing = [key for key in required_evidence if evidence[key]["state"] in {"unavailable", "unresolved"}]
        if missing:
            limitations.append("Mode evidence is incomplete: " + ", ".join(missing) + ".")
        regression_count = (
            performance.get("summary", {}).get("regression_count")
            if isinstance(performance.get("summary"), Mapping)
            else None
        )
        if status == "unstable":
            changed = final_state["summary"]["changed_block_positions"]
            chunks = final_state["summary"]["changed_chunks"]
            headline = (
                f"Identical frozen inputs diverged at {changed:,} exact block positions "
                f"across {chunks} captured chunks; this is a reproducibility failure, not a candidate effect."
            )
        elif status == "equivalent":
            headline = "No semantic or exact final-state difference was observed in the aligned window."
        else:
            changed = final_state["summary"]["changed_block_positions"]
            chunks = final_state["summary"]["changed_chunks"]
            headline = f"The candidate changed {changed:,} exact block positions across {chunks} captured chunks."
        next_actions: list[str] = []
        if status == "unstable":
            next_actions.append(
                "Keep this A/A control as the noise baseline and investigate unseeded RNG, population order, asynchronous writes, or capture timing before interpreting an A/B final-state delta."
            )
            if causal["state"] != "evidence-closed":
                next_actions.append(
                    "Repeat the control in debug mode with paired Worldgen Observatory bundles over a changed chunk to locate the first observed divergence."
                )
        elif causal["state"] != "evidence-closed" and behavior_changed:
            next_actions.append("Capture paired Worldgen Observatory bundles over a changed chunk to locate the first causal divergence.")
        if subsurface["state"] == "unavailable" and final_state["summary"]["category_totals"].get("ore", 0):
            next_actions.append("Supply aligned GTCEu inventories and optional traces to connect changed ore blocks to exact definitions and decisions.")
        if mode == "performance" and overhead["state"] == "unavailable":
            next_actions.append("Supply observer-off JFR summaries before attributing a regression to production code rather than instrumentation.")
        if not next_actions:
            next_actions.append("Open the review grid and jump from any outlier chunk to its exact Strata capture or retained diagnostic source.")
        decision = {
            "headline": headline,
            "behavior_changed": behavior_changed,
            "comparison_kind": input_relation["kind"],
            "reproducibility_status": reproducibility_status,
            "performance_regression_count": regression_count,
            "causal_status": causal.get("summary", {}).get("status") if isinstance(causal.get("summary"), Mapping) else "unavailable",
            "next_actions": next_actions,
        }

    navigation = [
        {"kind": "baseline-iteration", "label": "Open baseline iteration report", "target": str(baseline.report_binding.path)},
        {"kind": "candidate-iteration", "label": "Open candidate iteration report", "target": str(candidate.report_binding.path)},
        {"kind": "baseline-strata", "label": "Open baseline Strata viewer", "target": str(baseline.handoff.get("url"))},
        {"kind": "candidate-strata", "label": "Open candidate Strata viewer", "target": str(candidate.handoff.get("url"))},
        {"kind": "baseline-manifest", "label": "Open baseline exact manifest", "target": str(baseline.manifest_path)},
        {"kind": "candidate-manifest", "label": "Open candidate exact manifest", "target": str(candidate.manifest_path)},
    ]
    if review_path is not None:
        navigation.insert(0, {"kind": "cockpit-review", "label": "Open synchronized cockpit review", "target": str(review_path)})

    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "schema_version": 1,
        "report_id": "",
        "created_at": _utc_now(),
        "mode": mode,
        "status": status,
        "coverage": coverage,
        "profile": {
            "profile_id": cockpit_profile["profile_id"],
            "pack_profile": cockpit_profile["pack_profile"],
            "path": str(cockpit_profile_binding.path),
            "sha256": cockpit_profile_binding.sha256,
        },
        "alignment": alignment,
        "sides": {"baseline": baseline.public(), "candidate": candidate.public()},
        "evidence": evidence,
        "decision": decision,
        "visual": visual,
        "navigation": navigation,
        "limitations": sorted(set(limitations)),
        "reproduction_command": reproduction_command,
    }
    report["report_id"] = make_report_id(report)
    return report


__all__ = ["analyze_pair"]
