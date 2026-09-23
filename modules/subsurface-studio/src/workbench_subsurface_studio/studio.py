"""Authority-preserving GTCEu subsurface composition and query operations."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from workbench_crucible_gtceu_subsurface import (
    GtceuSubsurfaceTraceValidationError,
    parse_gtceu_subsurface_trace,
)
from workbench_crucible_gtceu_worldgen import (
    GtceuWorldgenValidationError,
    parse_gtceu_worldgen_inventory,
)
from workbench_crucible_gtceu_worldgen_impact import (
    GtceuWorldgenImpactValidationError,
    parse_gtceu_worldgen_impact_inventory,
)

from .model import (
    FileBinding,
    SubsurfaceStudioError,
    load_json_file,
    make_result,
    normalize_window,
    require,
    sha256_json,
    unavailable_source,
    window_contains_chunk,
)
from .strata import (
    ChunkMetrics,
    StrataRegion,
    host_from_state,
    load_strata_region,
    material_from_state,
)


MAX_TRACE_BYTES = 128 * 1024 * 1024
MAX_SECTION_COLUMNS = 256
MAX_CAVE_RADIUS = 32

LAYER_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "layer_id": "ore-blocks",
        "title": "Final ore blocks",
        "evidence_state": "observed-final",
        "value_kind": "count",
        "description": "Exact GTCEu ore voxels in each captured chunk; optionally filtered by material.",
    },
    {
        "layer_id": "ore-materials",
        "title": "Ore material diversity",
        "evidence_state": "derived-presentation",
        "value_kind": "count",
        "description": "Distinct GTCEu ore material tokens observed in each final chunk.",
    },
    {
        "layer_id": "lithology",
        "title": "Dominant captured lithology",
        "evidence_state": "derived-presentation",
        "value_kind": "category",
        "description": "Pack-profile host classification over exact final block states.",
    },
    {
        "layer_id": "subsurface-air",
        "title": "Final subsurface air",
        "evidence_state": "derived-presentation",
        "value_kind": "count",
        "description": "Final air below the exact captured height surface; not cave-generator attribution.",
    },
    {
        "layer_id": "ore-exposure",
        "title": "Exposed final ore faces",
        "evidence_state": "derived-presentation",
        "value_kind": "count",
        "description": "Exact final ore faces exposed by the Strata terrain mesh.",
    },
    {
        "layer_id": "surface-indicators",
        "title": "Final GTCEu surface indicators",
        "evidence_state": "observed-final",
        "value_kind": "count",
        "description": "Exact captured GTCEu surface-rock indicator voxels; optionally filtered by material.",
    },
    {
        "layer_id": "height",
        "title": "Mean exact surface height",
        "evidence_state": "observed-final",
        "value_kind": "number",
        "description": "Mean Chunk.getHeightValue across each captured chunk.",
    },
    {
        "layer_id": "biome",
        "title": "Dominant biome",
        "evidence_state": "observed-final",
        "value_kind": "category",
        "description": "Dominant exact biome column identity in each captured chunk.",
    },
    {
        "layer_id": "fluid-yield",
        "title": "Virtual bedrock-fluid yield",
        "evidence_state": "observed-final",
        "value_kind": "count",
        "description": "Yield of queried GTCEu virtual fluid cells; never physical underground blocks.",
    },
    {
        "layer_id": "ore-attempts",
        "title": "Observed ore position decisions",
        "evidence_state": "observed-controlled",
        "value_kind": "count",
        "description": "Exact controlled density, host, write-failure, and successful-write position decisions; requires a validated trace.",
    },
)
_LAYER_BY_ID = {row["layer_id"]: row for row in LAYER_DEFINITIONS}


def _aggregate_counter(counters: Iterable[Counter[str]]) -> Counter[str]:
    result: Counter[str] = Counter()
    for counter in counters:
        result.update(counter)
    return result


def _public_counter(
    counter: Counter[str], *, key_name: str, limit: int | None = None
) -> list[dict[str, Any]]:
    rows = [
        {key_name: key, "count": count}
        for key, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))
    ]
    return rows if limit is None else rows[:limit]


def _dimension_match(
    clauses: Any, dimension_id: int, profile: Mapping[str, Any]
) -> str:
    if clauses is None or clauses == []:
        return "eligible"
    if not isinstance(clauses, list) or not all(
        isinstance(item, str) and item for item in clauses
    ):
        return "unknown"
    semantics = {
        row["dimension_id"]: set(row["aliases"])
        for row in profile["dimension_semantics"]
    }
    known_aliases = set().union(*semantics.values()) if semantics else set()
    if any(clause in semantics.get(dimension_id, set()) for clause in clauses):
        return "eligible"
    if all(clause in known_aliases for clause in clauses):
        return "ineligible"
    return "unknown"


def _properties(block_state: str) -> dict[str, str]:
    if "[" not in block_state or not block_state.endswith("]"):
        return {}
    result: dict[str, str] = {}
    for item in block_state.split("[", 1)[1][:-1].split(","):
        key, separator, value = item.partition("=")
        if separator and key and value:
            result[key] = value
    return result


@dataclass
class TraceIndex:
    trace: dict[str, Any]
    binding: FileBinding
    deposits: dict[str, dict[str, Any]]
    decisions_by_position: dict[tuple[int, int, int], list[dict[str, Any]]]
    deposits_by_position: dict[tuple[int, int, int], list[dict[str, Any]]] = field(
        default_factory=dict
    )

    def containing(self, position: tuple[int, int, int]) -> list[dict[str, Any]]:
        existing = self.deposits_by_position.get(position)
        if existing is not None:
            return existing
        x, y, z = position
        result = []
        for deposit in self.deposits.values():
            bounds = deposit["bounds"]
            if (
                bounds["min_x"] <= x <= bounds["max_x"]
                and bounds["min_y"] <= y <= bounds["max_y"]
                and bounds["min_z"] <= z <= bounds["max_z"]
            ):
                result.append(deposit)
        result.sort(key=lambda row: row["deposit_instance_id"])
        self.deposits_by_position[position] = result
        return result


@dataclass
class StudioDataset:
    root: Path
    profile: dict[str, Any]
    profile_binding: FileBinding
    inventory: dict[str, Any]
    inventory_binding: FileBinding
    impact: dict[str, Any] | None
    impact_binding: FileBinding | None
    region: StrataRegion
    trace: TraceIndex | None
    definitions: list[dict[str, Any]]
    definition_by_path: dict[str, dict[str, Any]]
    material_definitions: dict[str, list[dict[str, Any]]]
    limitations: list[str]

    @property
    def scope(self) -> dict[str, Any]:
        return self.region.scope

    def sources(self) -> list[dict[str, Any]]:
        rows = [
            self.profile_binding.source(
                source_id=f"subsurface-profile:{self.profile['profile_id']}:{self.profile_binding.sha256}",
                kind="pack-profile",
                authority=f"{self.profile['pack_profile']} pack profile",
                state="declared",
            ),
            self.inventory_binding.source(
                source_id=self.inventory["inventory_id"],
                kind="gtceu-definition-inventory",
                authority="Crucible",
                state="declared",
                limitations=(
                    "Definition and material relations do not prove a runtime selection.",
                ),
            ),
        ]
        if self.impact is not None and self.impact_binding is not None:
            rows.append(
                self.impact_binding.source(
                    source_id=self.impact["inventory_id"],
                    kind="gtceu-impact-inventory",
                    authority="Crucible",
                    state="declared",
                    limitations=(
                        "Source-derived control flow remains version-bounded and is not a deposit occurrence.",
                    ),
                )
            )
        else:
            rows.append(
                unavailable_source(
                    source_id="gtceu-impact-inventory:unavailable",
                    kind="gtceu-impact-inventory",
                    authority="Crucible",
                    limitations=[
                        "No GTCEu impact inventory was supplied; priority, shape, biome, and full filter controls are partial."
                    ],
                )
            )
        rows.extend(
            [
                self.region.manifest_binding.source(
                    source_id=f"strata-region-manifest:sha256:{self.region.manifest_binding.sha256}",
                    kind="strata-region-manifest",
                    authority="Strata",
                    state="observed-final",
                    limitations=(
                        "Final state does not by itself establish which generator caused a block.",
                    ),
                ),
                {
                    "source_id": f"strata-tile-set:sha256:{self.region.tile_set_sha256}",
                    "kind": "strata-exact-tile-set",
                    "authority": "Strata",
                    "state": "observed-final",
                    "sha256": self.region.tile_set_sha256,
                    "size_bytes": self.region.tile_set_size_bytes,
                    "path": str(self.region.manifest_binding.path.parent),
                    "limitations": [
                        "Subsurface-air and lithology layers are deterministic presentation derivations over final state."
                    ],
                },
            ]
        )
        if self.trace is not None:
            state = (
                "observed-controlled"
                if self.trace.trace["capture"]["state"] == "complete"
                else "observed-controlled"
            )
            trace_limits = []
            if self.trace.trace["capture"]["state"] != "complete":
                trace_limits.append(
                    f"Trace capture state is {self.trace.trace['capture']['state']}."
                )
            if self.trace.trace["coverage"]["truncated"]:
                trace_limits.append("Trace decision coverage is truncated.")
            rows.append(
                self.trace.binding.source(
                    source_id=self.trace.trace["trace_id"],
                    kind="gtceu-subsurface-trace",
                    authority="Crucible",
                    state=state,
                    limitations=trace_limits,
                )
            )
        else:
            rows.append(
                unavailable_source(
                    source_id="gtceu-subsurface-trace:unavailable",
                    kind="gtceu-subsurface-trace",
                    authority="Crucible",
                    limitations=[
                        "No controlled selection/placement trace was supplied; final ore states retain candidate-only definition attribution."
                    ],
                )
            )
        return rows

    def navigation(self) -> list[dict[str, str]]:
        return [
            {
                "kind": "inventory",
                "label": "Open exact GTCEu inventory",
                "target": str(self.inventory_binding.path),
            },
            {
                "kind": "manifest",
                "label": "Open exact Strata region manifest",
                "target": str(self.region.manifest_binding.path),
            },
            {
                "kind": "tile-root",
                "label": "Open exact Strata shard directory",
                "target": str(self.region.manifest_binding.path.parent),
            },
        ]

    def make(
        self,
        *,
        operation: str,
        status: str,
        result: Mapping[str, Any],
        uncertainty: Iterable[str] = (),
        limitations: Iterable[str] = (),
        navigation: Iterable[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        combined_limits = sorted(set(self.limitations) | set(limitations))
        return make_result(
            operation=operation,
            status=status,
            profile=self.profile,
            profile_binding=self.profile_binding,
            scope=self.scope,
            sources=self.sources(),
            result=result,
            uncertainty=list(uncertainty),
            limitations=combined_limits,
            navigation=list(self.navigation() if navigation is None else navigation),
        )


def _load_inventory(path: Path) -> tuple[dict[str, Any], FileBinding]:
    value, binding = load_json_file(path, context="GTCEu worldgen inventory")
    try:
        return parse_gtceu_worldgen_inventory(value), binding
    except (GtceuWorldgenValidationError, KeyError, TypeError, ValueError) as exc:
        raise SubsurfaceStudioError(f"invalid GTCEu worldgen inventory: {exc}") from exc


def _load_impact(path: Path) -> tuple[dict[str, Any], FileBinding]:
    value, binding = load_json_file(path, context="GTCEu impact inventory")
    try:
        return parse_gtceu_worldgen_impact_inventory(value), binding
    except (GtceuWorldgenImpactValidationError, KeyError, TypeError, ValueError) as exc:
        raise SubsurfaceStudioError(f"invalid GTCEu impact inventory: {exc}") from exc


def _load_trace(path: Path) -> TraceIndex:
    value, binding = load_json_file(
        path, maximum_bytes=MAX_TRACE_BYTES, context="GTCEu subsurface trace"
    )
    try:
        trace = parse_gtceu_subsurface_trace(value)
    except (GtceuSubsurfaceTraceValidationError, KeyError, TypeError, ValueError) as exc:
        raise SubsurfaceStudioError(f"invalid GTCEu subsurface trace: {exc}") from exc
    deposits = {row["deposit_instance_id"]: row for row in trace["deposits"]}
    decisions_by_position: dict[
        tuple[int, int, int], list[dict[str, Any]]
    ] = defaultdict(list)
    for row in trace["decisions"]:
        position = row["position"]
        decisions_by_position[(position["x"], position["y"], position["z"])].append(row)
    for rows in decisions_by_position.values():
        rows.sort(key=lambda row: (row["deposit_instance_id"], row["decision_id"]))
    return TraceIndex(
        trace=trace,
        binding=binding,
        deposits=deposits,
        decisions_by_position=dict(decisions_by_position),
    )


def _definition_rows(
    inventory: Mapping[str, Any], impact: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    inventory_rows = {
        row["relative_path"]: row
        for row in inventory["definitions"]
        if row.get("kind") == "ore"
    }
    if impact is None:
        return [deepcopy(inventory_rows[path]) for path in sorted(inventory_rows)]
    controls = impact["definition_controls"]["ore_definitions"]
    control_paths = {row["relative_path"] for row in controls}
    require(
        control_paths == set(inventory_rows),
        "GTCEu impact definition paths disagree with the exact inventory",
    )
    result = []
    for control in controls:
        inventory_row = inventory_rows[control["relative_path"]]
        merged = deepcopy(dict(control))
        merged["material_tokens"] = list(inventory_row["material_tokens"])
        merged["source_sha256"] = inventory_row["source_sha256"]
        merged["source_size_bytes"] = inventory_row["source_size_bytes"]
        result.append(merged)
    return sorted(result, key=lambda row: row["relative_path"])


def _validate_inventory_region_correlation(
    inventory: Mapping[str, Any], region: StrataRegion, profile: Mapping[str, Any]
) -> None:
    observation = inventory.get("observation")
    require(
        isinstance(observation, Mapping),
        "Subsurface Studio requires a GTCEu inventory with exact Strata observation correlation",
    )
    inventory_window = normalize_window(observation.get("chunk_window"), camel_case=True)
    require(
        inventory_window == region.window,
        "GTCEu inventory and Strata manifest chunk windows differ",
    )
    state_semantics = profile["state_semantics"]
    ore_prefix = state_semantics["ore_prefix"]
    surface_prefix = state_semantics["surface_rock_prefix"]
    expected: Counter[str] = Counter()
    for row in observation.get("states") or []:
        require(isinstance(row, Mapping), "GTCEu observation state row must be an object")
        state = row.get("block_state")
        count = row.get("count")
        require(
            isinstance(state, str)
            and state
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 0,
            "GTCEu observation state row is invalid",
        )
        require(state not in expected, f"GTCEu observation repeats state {state}")
        expected[state] = count
    observed = Counter(
        {
            state: count
            for state, count in region.resource_counts.items()
            if state.startswith(ore_prefix) or state.startswith(surface_prefix)
        }
    )
    require(
        observed == expected,
        "GTCEu inventory observation counts disagree with the exact Strata shards",
    )
    require(
        observation.get("observed_gtceu_block_count") == sum(observed.values()),
        "GTCEu inventory observed block total drift",
    )


def _trace_contains_region(trace_window: Mapping[str, int], region_window: Mapping[str, int]) -> bool:
    return all(
        window_contains_chunk(
            trace_window,
            chunk_x,
            chunk_z,
            include_halo=True,
        )
        for chunk_z in range(
            region_window["min_chunk_z"],
            region_window["min_chunk_z"] + region_window["chunk_size_z"],
        )
        for chunk_x in range(
            region_window["min_chunk_x"],
            region_window["min_chunk_x"] + region_window["chunk_size_x"],
        )
    )


def load_dataset(
    *,
    root: Path,
    profile: Mapping[str, Any],
    profile_binding: FileBinding,
    inventory_path: Path,
    manifest_path: Path,
    impact_path: Path | None = None,
    trace_path: Path | None = None,
) -> StudioDataset:
    inventory, inventory_binding = _load_inventory(inventory_path)
    require(
        inventory["adapter_profile"]["id"]
        == profile["adapter"]["inventory_profile_id"],
        "GTCEu inventory does not match the selected pack profile",
    )
    impact: dict[str, Any] | None = None
    impact_binding: FileBinding | None = None
    limitations: list[str] = []
    if impact_path is not None:
        impact, impact_binding = _load_impact(impact_path)
        require(
            impact["adapter_profile"]["id"]
            == profile["adapter"]["impact_profile_id"],
            "GTCEu impact inventory does not match the selected pack profile",
        )
        embedded_inventory = impact["definition_inventory"]
        require(
            all(
                embedded_inventory[key] == inventory[key]
                for key in (
                    "adapter_profile",
                    "artifact",
                    "configuration",
                    "definitions",
                )
            ),
            "GTCEu impact inventory embeds a different definition surface",
        )
        if embedded_inventory["inventory_id"] != inventory["inventory_id"]:
            limitations.append(
                "The impact inventory embeds the same exact GTCEu artifact, configuration, and definitions but a differently scoped historical Strata observation; final-state correlation uses the separately supplied inventory."
            )
    else:
        limitations.append(
            "GTCEu impact inventory is unavailable; definition controls are limited to the V1 inventory surface."
        )
    region = load_strata_region(manifest_path, profile=profile)
    _validate_inventory_region_correlation(inventory, region, profile)
    definitions = _definition_rows(inventory, impact)
    definition_by_path = {row["relative_path"]: row for row in definitions}
    material_definitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for definition in definitions:
        for material in definition.get("material_tokens") or []:
            material_definitions[material].append(definition)
    for rows in material_definitions.values():
        rows.sort(key=lambda row: row["relative_path"])

    trace = _load_trace(trace_path) if trace_path is not None else None
    if trace is not None:
        trace_value = trace.trace
        require(
            trace_value["adapter_profile"]["id"]
            == profile["adapter"]["trace_profile_id"],
            "GTCEu trace does not match the selected pack profile",
        )
        require(
            trace_value["adapter_profile"]["inventory_id"] == inventory["inventory_id"],
            "GTCEu trace binds a different definition inventory",
        )
        require(impact is not None, "GTCEu trace requires its exact impact inventory")
        require(
            trace_value["adapter_profile"]["impact_inventory_id"]
            == impact["inventory_id"],
            "GTCEu trace binds a different impact inventory",
        )
        capture = trace_value["capture"]
        require(
            capture["world_seed"] == region.manifest["worldSeed"],
            "GTCEu trace and Strata world seeds differ",
        )
        require(
            capture["dimension_id"] == region.manifest["dimensionId"],
            "GTCEu trace and Strata dimensions differ",
        )
        trace_window = normalize_window(capture["chunk_window"])
        require(
            _trace_contains_region(trace_window, region.window),
            "GTCEu trace does not contain the Strata region",
        )
        unknown = sorted(
            {
                row["definition_path"]
                for row in trace_value["deposits"]
                if row["definition_path"] not in definition_by_path
            }
        )
        require(not unknown, f"GTCEu trace references unknown definitions: {unknown}")
        if capture["state"] != "complete":
            limitations.append(
                f"GTCEu trace capture is {capture['state']}; absence is not closed."
            )
        if trace_value["coverage"]["truncated"]:
            limitations.append("GTCEu trace is truncated; missing decisions are unavailable.")
    else:
        limitations.append(
            "No controlled GTCEu decision trace is available; definition attribution remains candidate-only."
        )
    return StudioDataset(
        root=root.resolve(),
        profile=deepcopy(dict(profile)),
        profile_binding=profile_binding,
        inventory=inventory,
        inventory_binding=inventory_binding,
        impact=impact,
        impact_binding=impact_binding,
        region=region,
        trace=trace,
        definitions=definitions,
        definition_by_path=definition_by_path,
        material_definitions=dict(material_definitions),
        limitations=sorted(set(limitations)),
    )


def classify_state(dataset: StudioDataset, block_state: str) -> dict[str, Any]:
    semantics = dataset.profile["state_semantics"]
    material = material_from_state(block_state, semantics["ore_prefix"])
    host = host_from_state(
        block_state,
        semantics["host_properties"],
        set(semantics["known_host_variants"]),
    )
    if block_state in set(semantics["air_states"]):
        kind = "air"
    elif material is not None:
        kind = "ore"
    elif block_state.startswith(semantics["surface_rock_prefix"]):
        kind = "surface-indicator"
        properties = _properties(block_state)
        material = properties.get("variant")
        if material and "__" in material:
            material = material.split("__", 1)[1]
    elif any(block_state.startswith(prefix) for prefix in semantics["fluid_prefixes"]):
        kind = "physical-fluid"
    elif host is not None:
        kind = "lithology"
    else:
        kind = "other"
    return {
        "kind": kind,
        "block_state": block_state,
        "material": material,
        "lithology": host,
    }


def _definition_public(
    dataset: StudioDataset, definition: Mapping[str, Any], *, y: int | None = None
) -> dict[str, Any]:
    dimension_state = _dimension_match(
        definition.get("dimension_filter")
        if "dimension_filter" in definition
        else (
            [f"dimension_id:{value}" for value in definition.get("dimension_ids") or []]
            if definition.get("has_dimension_filter")
            else []
        ),
        dataset.region.manifest["dimensionId"],
        dataset.profile,
    )
    min_height = definition.get("min_height")
    max_height = definition.get("max_height")
    center_height_compatible = (
        None
        if y is None
        or not isinstance(min_height, int)
        or not isinstance(max_height, int)
        else min_height <= y <= max_height
    )
    observed_counts = Counter()
    for material in definition.get("material_tokens") or []:
        observed_counts[material] = sum(
            metric.ore_counts[material]
            for metric in dataset.region.chunk_metrics.values()
        )
    observed_indicators = Counter()
    for material in definition.get("material_tokens") or []:
        observed_indicators[material] = sum(
            metric.surface_indicator_counts[material]
            for metric in dataset.region.chunk_metrics.values()
        )
    selected_deposits = (
        [
            row["deposit_instance_id"]
            for row in dataset.trace.deposits.values()
            if row["definition_path"] == definition["relative_path"]
        ]
        if dataset.trace is not None
        else []
    )
    selected_deposits.sort()
    return {
        "definition_path": definition["relative_path"],
        "source_sha256": definition["source_sha256"],
        "materials": list(definition.get("material_tokens") or []),
        "weight": definition.get("weight"),
        "priority": definition.get("priority"),
        "density": definition.get("density"),
        "count_as_vein": definition.get("count_as_vein"),
        "height": {"minimum": min_height, "maximum": max_height},
        "center_height_compatible": center_height_compatible,
        "dimension_filter": deepcopy(
            definition.get("dimension_filter", definition.get("dimension_ids", []))
        ),
        "dimension_state": dimension_state,
        "biome_modifier": deepcopy(definition.get("biome_modifier")),
        "generator": deepcopy(
            definition.get("generator", {"type": definition.get("generator_type")})
        ),
        "filler_type": definition.get("filler_type"),
        "generation_predicate": definition.get("generation_predicate"),
        "vein_populator": deepcopy(
            definition.get(
                "vein_populator",
                (
                    {"type": definition.get("populator_type")}
                    if definition.get("populator_type")
                    else None
                ),
            )
        ),
        "observed_material_counts": _public_counter(
            observed_counts, key_name="material"
        ),
        "observed_surface_indicator_counts": _public_counter(
            observed_indicators, key_name="material"
        ),
        "evidence_state": "candidate-only",
        "trace_selection": {
            "available": dataset.trace is not None,
            "deposit_instance_count": len(selected_deposits),
            "deposit_instance_ids": selected_deposits,
            "absence_closed_for_selector": _trace_is_absence_closed(dataset.trace),
            "evidence_state": (
                "observed-controlled" if dataset.trace is not None else "unavailable"
            ),
        },
    }


def summary(dataset: StudioDataset) -> dict[str, Any]:
    ore_counts = _aggregate_counter(
        metric.ore_counts for metric in dataset.region.chunk_metrics.values()
    )
    ore_hosts = _aggregate_counter(
        metric.ore_host_counts for metric in dataset.region.chunk_metrics.values()
    )
    lithology = _aggregate_counter(
        metric.host_counts for metric in dataset.region.chunk_metrics.values()
    )
    surface_indicators = _aggregate_counter(
        metric.surface_indicator_counts
        for metric in dataset.region.chunk_metrics.values()
    )
    result = {
        "dataset": {
            "inventory_id": dataset.inventory["inventory_id"],
            "impact_inventory_id": (
                dataset.impact["inventory_id"] if dataset.impact is not None else None
            ),
            "trace_id": (
                dataset.trace.trace["trace_id"] if dataset.trace is not None else None
            ),
            "manifest_sha256": dataset.region.manifest_binding.sha256,
            "tile_set_sha256": dataset.region.tile_set_sha256,
            "provider": dataset.region.manifest["providerName"],
            "chunk_generator": dataset.region.manifest["chunkGeneratorClass"],
        },
        "definitions": {
            "ore_definition_count": len(dataset.definitions),
            "material_token_count": len(dataset.material_definitions),
            "exact_controls_available": dataset.impact is not None,
        },
        "final_state": {
            "ore_block_count": sum(ore_counts.values()),
            "ore_material_count": len(ore_counts),
            "ore_materials": _public_counter(ore_counts, key_name="material"),
            "ore_hosts": _public_counter(ore_hosts, key_name="lithology"),
            "surface_indicator_count": sum(surface_indicators.values()),
            "surface_indicators": _public_counter(
                surface_indicators, key_name="material"
            ),
            "captured_lithology": _public_counter(lithology, key_name="lithology"),
            "subsurface_air_count": sum(
                metric.subsurface_air
                for metric in dataset.region.chunk_metrics.values()
            ),
            "exposed_ore_face_count": sum(
                metric.exposed_ore_faces
                for metric in dataset.region.chunk_metrics.values()
            ),
            "virtual_fluid_cell_count": len(dataset.region.fluid_cells),
            "virtual_fluid_yield_total": sum(
                row.get("fluidYield", 0)
                for row in dataset.region.fluid_cells
                if isinstance(row.get("fluidYield"), int)
            ),
        },
        "attribution": {
            "state": (
                "observed-controlled"
                if dataset.trace is not None
                else "candidate-only"
            ),
            "deposit_count": (
                len(dataset.trace.deposits) if dataset.trace is not None else None
            ),
            "decision_count": (
                len(dataset.trace.trace["decisions"])
                if dataset.trace is not None
                else None
            ),
            "absence_closed": bool(
                dataset.trace is not None
                and dataset.trace.trace["capture"]["state"] == "complete"
                and dataset.trace.trace["coverage"]["position_decisions_complete"]
                and not dataset.trace.trace["coverage"]["truncated"]
            ),
        },
    }
    return dataset.make(operation="summary", status="answered", result=result)


def layers(dataset: StudioDataset) -> dict[str, Any]:
    rows = [deepcopy(row) for row in LAYER_DEFINITIONS]
    attempts = next(row for row in rows if row["layer_id"] == "ore-attempts")
    if dataset.trace is None:
        attempts["evidence_state"] = "unavailable"
        attempts["description"] = (
            "Requires a validated Crucible GTCEu subsurface trace; no attempt values are inferred from final blocks."
        )
    return dataset.make(
        operation="layers",
        status="answered",
        result={"layers": rows, "layer_count": len(rows)},
    )


def chunk_map(
    dataset: StudioDataset, *, layer: str, material: str | None = None
) -> dict[str, Any]:
    require(layer in _LAYER_BY_ID, f"unknown subsurface layer: {layer}")
    require(
        material is None
        or layer
        in {
            "ore-blocks",
            "ore-materials",
            "ore-exposure",
            "ore-attempts",
            "surface-indicators",
        },
        "--material applies only to ore layers",
    )
    require(
        layer != "ore-attempts" or dataset.trace is not None,
        "ore-attempts requires a validated controlled GTCEu trace",
    )
    decision_counts: Counter[tuple[int, int]] = Counter()
    decision_outcomes: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    if layer == "ore-attempts":
        require(dataset.trace is not None, "ore-attempts requires a trace")
        for decisions_at_position in dataset.trace.decisions_by_position.values():
            for decision in decisions_at_position:
                deposit = dataset.trace.deposits[decision["deposit_instance_id"]]
                definition = dataset.definition_by_path[deposit["definition_path"]]
                if material is not None and material not in (
                    definition.get("material_tokens") or []
                ):
                    continue
                position = decision["position"]
                chunk = (position["x"] // 16, position["z"] // 16)
                if chunk in dataset.region.chunk_metrics:
                    decision_counts[chunk] += 1
                    decision_outcomes[chunk][decision["outcome"]] += 1
    cells: list[dict[str, Any]] = []
    values: list[int | float] = []
    categories: set[str] = set()
    window = dataset.region.window
    for chunk_z in range(
        window["min_chunk_z"], window["min_chunk_z"] + window["chunk_size_z"]
    ):
        for chunk_x in range(
            window["min_chunk_x"], window["min_chunk_x"] + window["chunk_size_x"]
        ):
            metric = dataset.region.chunk_metrics[(chunk_x, chunk_z)]
            if layer == "ore-blocks":
                value: Any = (
                    metric.ore_counts[material]
                    if material is not None
                    else sum(metric.ore_counts.values())
                )
            elif layer == "ore-materials":
                value = (
                    1 if material is not None and metric.ore_counts[material] else 0
                ) if material is not None else len(metric.ore_counts)
            elif layer == "lithology":
                value = (
                    sorted(
                        metric.host_counts.items(),
                        key=lambda row: (-row[1], row[0]),
                    )[0][0]
                    if metric.host_counts
                    else "unknown"
                )
            elif layer == "subsurface-air":
                value = metric.subsurface_air
            elif layer == "ore-exposure":
                value = (
                    metric.exposed_ore_faces_by_material[material]
                    if material is not None
                    else metric.exposed_ore_faces
                )
            elif layer == "surface-indicators":
                value = (
                    metric.surface_indicator_counts[material]
                    if material is not None
                    else sum(metric.surface_indicator_counts.values())
                )
            elif layer == "height":
                value = metric.mean_height
            elif layer == "biome":
                value = metric.dominant_biome
            elif layer == "fluid-yield":
                value = metric.fluid_yield
            elif layer == "ore-attempts":
                value = decision_counts[(chunk_x, chunk_z)]
            else:  # pragma: no cover - guarded by layer table
                raise SubsurfaceStudioError(f"unsupported layer: {layer}")
            cell = {"chunk_x": chunk_x, "chunk_z": chunk_z, "value": value}
            if layer in {"ore-blocks", "ore-materials", "ore-exposure"}:
                cell["ore_blocks"] = (
                    metric.ore_counts[material]
                    if material is not None
                    else sum(metric.ore_counts.values())
                )
            elif layer == "surface-indicators":
                cell["surface_indicators"] = value
            elif layer == "ore-attempts":
                cell["decision_outcomes"] = dict(
                    sorted(decision_outcomes[(chunk_x, chunk_z)].items())
                )
            cells.append(cell)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(value)
            else:
                categories.add(str(value))
    result = {
        "layer": deepcopy(_LAYER_BY_ID[layer]),
        "material": material,
        "width": window["chunk_size_x"],
        "height": window["chunk_size_z"],
        "origin": {
            "min_chunk_x": window["min_chunk_x"],
            "min_chunk_z": window["min_chunk_z"],
        },
        "cells": cells,
        "numeric_range": (
            {"minimum": min(values), "maximum": max(values)} if values else None
        ),
        "categories": sorted(categories),
        "ordering": "row-major chunkZ then chunkX",
    }
    return dataset.make(
        operation="map",
        status="answered",
        result=result,
    )


def _block_bounds(dataset: StudioDataset) -> dict[str, int]:
    window = dataset.region.window
    return {
        "min_x": window["min_chunk_x"] * 16,
        "max_x": (window["min_chunk_x"] + window["chunk_size_x"]) * 16 - 1,
        "min_z": window["min_chunk_z"] * 16,
        "max_z": (window["min_chunk_z"] + window["chunk_size_z"]) * 16 - 1,
    }


def _inside_block_bounds(dataset: StudioDataset, x: int, z: int) -> bool:
    window = dataset.region.window
    return (
        window["min_chunk_x"] * 16
        <= x
        < (window["min_chunk_x"] + window["chunk_size_x"]) * 16
        and window["min_chunk_z"] * 16
        <= z
        < (window["min_chunk_z"] + window["chunk_size_z"]) * 16
    )


def _is_subsurface_air(dataset: StudioDataset, x: int, y: int, z: int) -> bool:
    return (
        _inside_block_bounds(dataset, x, z)
        and dataset.region.block_at(x, y, z)
        in dataset.profile["state_semantics"]["air_states"]
        and y < dataset.region.surface_at(x, z)
    )


def _nearest_subsurface_air(
    dataset: StudioDataset,
    *,
    position: tuple[int, int, int],
    radius: int,
) -> dict[str, Any] | None:
    """Return the deterministic nearest final subsurface-air coordinate.

    Euclidean distance is used for developer intuition.  Squared distance and
    coordinate ordering make ties stable and avoid floating-point comparisons.
    """

    require(0 <= radius <= MAX_CAVE_RADIUS, f"cave radius must be in 0..{MAX_CAVE_RADIUS}")
    x, y, z = position
    best: tuple[int, int, int, int] | None = None
    for candidate_y in range(max(0, y - radius), min(255, y + radius) + 1):
        dy = candidate_y - y
        for candidate_z in range(z - radius, z + radius + 1):
            dz = candidate_z - z
            for candidate_x in range(x - radius, x + radius + 1):
                dx = candidate_x - x
                distance_squared = dx * dx + dy * dy + dz * dz
                if distance_squared > radius * radius:
                    continue
                if not _inside_block_bounds(dataset, candidate_x, candidate_z):
                    continue
                candidate = (distance_squared, candidate_y, candidate_z, candidate_x)
                if best is not None and candidate >= best:
                    continue
                if _is_subsurface_air(
                    dataset, candidate_x, candidate_y, candidate_z
                ):
                    best = candidate
    if best is None:
        return None
    distance_squared, nearest_y, nearest_z, nearest_x = best
    return {
        "position": {"x": nearest_x, "y": nearest_y, "z": nearest_z},
        "distance_squared": distance_squared,
        "distance": round(distance_squared ** 0.5, 3),
    }


def _trace_is_absence_closed(trace: TraceIndex | None) -> bool:
    return bool(
        trace is not None
        and trace.trace["capture"]["state"] == "complete"
        and trace.trace["coverage"]["position_decisions_complete"]
        and not trace.trace["coverage"]["truncated"]
    )


def _trace_decision_public(
    dataset: StudioDataset,
    decision: Mapping[str, Any],
    *,
    final_state: str,
) -> dict[str, Any]:
    require(dataset.trace is not None, "trace decision requires a trace")
    deposit = dataset.trace.deposits[decision["deposit_instance_id"]]
    definition = dataset.definition_by_path[deposit["definition_path"]]
    row = deepcopy(dict(decision))
    row["definition_path"] = deposit["definition_path"]
    row["deposit"] = {
        "deposit_instance_id": deposit["deposit_instance_id"],
        "grid_x": deposit["grid_x"],
        "grid_z": deposit["grid_z"],
        "selection_ordinal": deposit["selection_ordinal"],
        "center": deepcopy(deposit["center"]),
        "bounds": deepcopy(deposit["bounds"]),
        "placement": deepcopy(deposit["placement"]),
    }
    row["definition"] = _definition_public(dataset, definition)
    if decision["outcome"] == "written":
        row["final_state_relation"] = (
            "survives-as-final-state"
            if decision["after_state"] == final_state
            else "not-final-state"
        )
    else:
        row["final_state_relation"] = "no-write"
    row["evidence_state"] = "observed-controlled"
    return row


def _grid_context(dataset: StudioDataset, x: int, z: int) -> dict[str, Any]:
    chunk_x = x // 16
    chunk_z = z // 16
    grid_size = dataset.profile["grid_semantics"]["ore_grid_chunks"]
    radius = dataset.profile["grid_semantics"]["consulted_grid_radius"]
    grid_x = chunk_x // grid_size
    grid_z = chunk_z // grid_size
    consulted = [
        {"grid_x": candidate_x, "grid_z": candidate_z}
        for candidate_z in range(grid_z - radius, grid_z + radius + 1)
        for candidate_x in range(grid_x - radius, grid_x + radius + 1)
    ]
    fluid_size = dataset.profile["grid_semantics"]["bedrock_fluid_grid_chunks"]
    return {
        "chunk": {"x": chunk_x, "z": chunk_z},
        "ore_grid": {
            "grid_x": grid_x,
            "grid_z": grid_z,
            "size_chunks": grid_size,
            "consulted_radius": radius,
            "consulted_grids": consulted,
            "evidence_state": "declared",
        },
        "bedrock_fluid_grid": {
            "grid_x": chunk_x // fluid_size,
            "grid_z": chunk_z // fluid_size,
            "size_chunks": fluid_size,
            "evidence_state": "declared",
        },
    }


def explain(
    dataset: StudioDataset,
    *,
    position: tuple[int, int, int],
    material: str | None = None,
    cave_radius: int = 8,
) -> dict[str, Any]:
    """Explain one exact final coordinate without inventing causal attribution."""

    x, y, z = position
    require(
        all(isinstance(value, int) and not isinstance(value, bool) for value in position),
        "position must contain integer X, Y, and Z",
    )
    require(0 <= y <= 255, "position Y must be in 0..255")
    require(_inside_block_bounds(dataset, x, z), "position is outside the captured window")
    require(
        isinstance(cave_radius, int) and not isinstance(cave_radius, bool),
        "cave radius must be an integer",
    )
    require(0 <= cave_radius <= MAX_CAVE_RADIUS, f"cave radius must be in 0..{MAX_CAVE_RADIUS}")
    require(material is None or bool(material.strip()), "material cannot be empty")
    if material is not None:
        material = material.strip()

    final_state = dataset.region.block_at(x, y, z)
    classification = classify_state(dataset, final_state)
    surface_height = dataset.region.surface_at(x, z)
    biome = dataset.region.biome_at(x, z)
    directions = (
        ("west", -1, 0, 0),
        ("east", 1, 0, 0),
        ("down", 0, -1, 0),
        ("up", 0, 1, 0),
        ("north", 0, 0, -1),
        ("south", 0, 0, 1),
    )
    neighbors: list[dict[str, Any]] = []
    for direction, dx, dy, dz in directions:
        neighbor = (x + dx, y + dy, z + dz)
        nx, ny, nz = neighbor
        inside = 0 <= ny <= 255 and _inside_block_bounds(dataset, nx, nz)
        if not inside:
            neighbors.append(
                {
                    "direction": direction,
                    "position": {"x": nx, "y": ny, "z": nz},
                    "inside_capture": False,
                    "state": None,
                    "subsurface_air": None,
                }
            )
            continue
        state = dataset.region.block_at(nx, ny, nz)
        neighbors.append(
            {
                "direction": direction,
                "position": {"x": nx, "y": ny, "z": nz},
                "inside_capture": True,
                "state": classify_state(dataset, state),
                "subsurface_air": _is_subsurface_air(dataset, nx, ny, nz),
            }
        )

    requested_material = material or classification.get("material")
    candidate_definitions = [
        _definition_public(dataset, definition, y=y)
        for definition in dataset.material_definitions.get(requested_material or "", [])
    ]
    candidate_definitions.sort(key=lambda row: row["definition_path"])

    exact_decisions: list[dict[str, Any]] = []
    intersecting_deposits: list[dict[str, Any]] = []
    if dataset.trace is not None:
        exact_decisions = [
            _trace_decision_public(dataset, row, final_state=final_state)
            for row in dataset.trace.decisions_by_position.get(position, [])
        ]
        for deposit in dataset.trace.containing(position):
            intersecting_deposits.append(
                {
                    "deposit_instance_id": deposit["deposit_instance_id"],
                    "definition_path": deposit["definition_path"],
                    "center": deepcopy(deposit["center"]),
                    "bounds": deepcopy(deposit["bounds"]),
                    "has_exact_position_decision": any(
                        row["deposit_instance_id"] == deposit["deposit_instance_id"]
                        for row in exact_decisions
                    ),
                    "evidence_state": "observed-controlled",
                }
            )

    relevant_decisions = (
        [
            row
            for row in exact_decisions
            if material in row["definition"]["materials"]
        ]
        if material is not None
        else exact_decisions
    )

    surviving_writes = [
        row
        for row in relevant_decisions
        if row["outcome"] == "written"
        and row["final_state_relation"] == "survives-as-final-state"
    ]
    absence_closed = _trace_is_absence_closed(dataset.trace)
    uncertainty: list[str] = []
    if len(surviving_writes) == 1:
        attribution_state = "observed-controlled"
        attribution = {
            "state": attribution_state,
            "deposit_instance_id": surviving_writes[0]["deposit_instance_id"],
            "definition_path": surviving_writes[0]["definition_path"],
            "reason": "One exact controlled write matches the captured final state.",
        }
        status = "answered"
    elif len(surviving_writes) > 1:
        attribution_state = "ambiguous"
        attribution = {
            "state": attribution_state,
            "deposit_instance_id": None,
            "definition_path": None,
            "reason": "Multiple exact controlled writes match the final state and the trace does not order them causally.",
        }
        uncertainty.append(
            "Multiple matching controlled writes prevent unique deposit attribution."
        )
        status = "ambiguous"
    elif relevant_decisions:
        attribution_state = "observed-no-surviving-write"
        attribution = {
            "state": attribution_state,
            "deposit_instance_id": None,
            "definition_path": None,
            "reason": "Exact controlled decisions exist, but none writes the captured final state.",
        }
        status = "answered" if absence_closed else "partial"
        if not absence_closed:
            uncertainty.append("The supplied trace does not close absent position decisions.")
    elif absence_closed:
        attribution_state = "no-decision-for-trace-selector"
        attribution = {
            "state": attribution_state,
            "deposit_instance_id": None,
            "definition_path": None,
            "reason": "Complete trace coverage contains no GTCEu decision at this coordinate for its declared selector.",
        }
        status = "answered"
    elif requested_material is not None or classification["kind"] == "ore":
        attribution_state = "candidate-only"
        attribution = {
            "state": attribution_state,
            "deposit_instance_id": None,
            "definition_path": None,
            "reason": "Definition relations are available, but no exact controlled write establishes the deposit instance.",
        }
        uncertainty.append(
            "A final block and material-compatible definitions do not establish which deposit was selected or why this position was written."
        )
        status = "partial"
    else:
        attribution_state = "not-requested"
        attribution = {
            "state": attribution_state,
            "deposit_instance_id": None,
            "definition_path": None,
            "reason": "The exact final-state question is answered; no causal ore attribution was requested or observed.",
        }
        status = "answered"

    tile = dataset.region.tile_for_chunk(x // 16, z // 16)
    navigation = dataset.navigation() + [
        {
            "kind": "tile",
            "label": f"Open exact Strata tile {tile.tile_id}",
            "target": str(tile.path),
        }
    ]
    if dataset.trace is not None:
        navigation.append(
            {
                "kind": "trace",
                "label": "Open controlled GTCEu subsurface trace",
                "target": str(dataset.trace.binding.path),
            }
        )

    immediate_subsurface_air = [
        row["direction"] for row in neighbors if row["subsurface_air"] is True
    ]
    result = {
        "position": {"x": x, "y": y, "z": z},
        "final_state": {
            **classification,
            "evidence_state": "observed-final",
        },
        "surface": {
            "height": surface_height,
            "biome": biome,
            "below_surface": y < surface_height,
            "evidence_state": "observed-final",
        },
        "neighbors": neighbors,
        "cave_context": {
            "is_final_subsurface_air": _is_subsurface_air(dataset, x, y, z),
            "immediate_subsurface_air_directions": immediate_subsurface_air,
            "exposed_to_final_subsurface_air": bool(immediate_subsurface_air),
            "search_radius": cave_radius,
            "nearest_final_subsurface_air": _nearest_subsurface_air(
                dataset, position=position, radius=cave_radius
            ),
            "evidence_state": "derived-presentation",
            "causal_generator": None,
        },
        "grid_context": _grid_context(dataset, x, z),
        "definition_query": {
            "material": requested_material,
            "candidate_count": len(candidate_definitions),
            "candidates": candidate_definitions,
            "evidence_state": "candidate-only" if requested_material else "unavailable",
        },
        "trace": {
            "available": dataset.trace is not None,
            "absence_closed_for_selector": absence_closed,
            "exact_position_decisions": exact_decisions,
            "relevant_position_decisions": relevant_decisions,
            "intersecting_deposits": intersecting_deposits,
        },
        "attribution": attribution,
    }
    limitations = [
        "Final subsurface air does not identify the cave or terrain generator that produced it."
    ]
    return dataset.make(
        operation="explain",
        status=status,
        result=result,
        uncertainty=uncertainty,
        limitations=limitations,
        navigation=navigation,
    )


def _section_cell(
    dataset: StudioDataset,
    *,
    x: int,
    y: int,
    z: int,
    focus_material: str | None,
) -> dict[str, Any]:
    state = dataset.region.block_at(x, y, z)
    classification = classify_state(dataset, state)
    subsurface_air = classification["kind"] == "air" and y < dataset.region.surface_at(x, z)
    if classification["kind"] == "air":
        category = "subsurface-air" if subsurface_air else "open-air"
    elif classification["kind"] == "ore" and focus_material is not None:
        category = (
            "ore-focus"
            if classification["material"] == focus_material
            else "ore-other"
        )
    else:
        category = classification["kind"]
    return {
        "block_state": state,
        "category": category,
        "material": classification["material"],
        "lithology": classification["lithology"],
        "subsurface_air": subsurface_air,
    }


def section(
    dataset: StudioDataset,
    *,
    x: int | None = None,
    z: int | None = None,
    min_y: int = 0,
    max_y: int = 255,
    min_axis: int | None = None,
    max_axis: int | None = None,
    material: str | None = None,
) -> dict[str, Any]:
    """Return an exact, bounded, run-length encoded vertical plane."""

    require((x is None) != (z is None), "section requires exactly one of X or Z")
    for name, value in (("min_y", min_y), ("max_y", max_y)):
        require(
            isinstance(value, int) and not isinstance(value, bool),
            f"{name} must be an integer",
        )
    require(0 <= min_y <= max_y <= 255, "section Y bounds must satisfy 0 <= min-y <= max-y <= 255")
    bounds = _block_bounds(dataset)
    if x is not None:
        require(isinstance(x, int) and not isinstance(x, bool), "section X must be an integer")
        require(bounds["min_x"] <= x <= bounds["max_x"], "section X is outside the captured window")
        orientation = "fixed-x"
        fixed_coordinate = x
        axis_name = "z"
        natural_min, natural_max = bounds["min_z"], bounds["max_z"]
    else:
        require(isinstance(z, int) and not isinstance(z, bool), "section Z must be an integer")
        require(bounds["min_z"] <= z <= bounds["max_z"], "section Z is outside the captured window")
        orientation = "fixed-z"
        fixed_coordinate = z
        axis_name = "x"
        natural_min, natural_max = bounds["min_x"], bounds["max_x"]
    axis_start = natural_min if min_axis is None else min_axis
    axis_end = natural_max if max_axis is None else max_axis
    require(
        isinstance(axis_start, int)
        and not isinstance(axis_start, bool)
        and isinstance(axis_end, int)
        and not isinstance(axis_end, bool),
        "section axis bounds must be integers",
    )
    require(
        natural_min <= axis_start <= axis_end <= natural_max,
        "section axis bounds are outside the captured window or inverted",
    )
    require(
        axis_end - axis_start + 1 <= MAX_SECTION_COLUMNS,
        f"section exceeds {MAX_SECTION_COLUMNS} columns",
    )
    require(material is None or bool(material.strip()), "material cannot be empty")
    if material is not None:
        material = material.strip()

    columns: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    material_counts: Counter[str] = Counter()
    lithology_counts: Counter[str] = Counter()
    for axis in range(axis_start, axis_end + 1):
        cell_x = fixed_coordinate if orientation == "fixed-x" else axis
        cell_z = axis if orientation == "fixed-x" else fixed_coordinate
        runs: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for cell_y in range(min_y, max_y + 1):
            cell = _section_cell(
                dataset,
                x=cell_x,
                y=cell_y,
                z=cell_z,
                focus_material=material,
            )
            category_counts[cell["category"]] += 1
            if cell["material"] is not None:
                material_counts[cell["material"]] += 1
            if cell["lithology"] is not None:
                lithology_counts[cell["lithology"]] += 1
            identity = (
                cell["block_state"],
                cell["category"],
                cell["material"],
                cell["lithology"],
                cell["subsurface_air"],
            )
            if current is not None and current.pop("_identity") == identity:
                current["max_y"] = cell_y
                current["_identity"] = identity
            else:
                if current is not None:
                    current.pop("_identity", None)
                    runs.append(current)
                current = {
                    "min_y": cell_y,
                    "max_y": cell_y,
                    **cell,
                    "_identity": identity,
                }
        require(current is not None, "section column unexpectedly has no cells")
        current.pop("_identity", None)
        runs.append(current)
        columns.append(
            {
                "axis": axis,
                "x": cell_x,
                "z": cell_z,
                "surface_height": dataset.region.surface_at(cell_x, cell_z),
                "biome": dataset.region.biome_at(cell_x, cell_z),
                "runs": runs,
            }
        )

    result = {
        "orientation": orientation,
        "fixed_coordinate": fixed_coordinate,
        "axis": axis_name,
        "axis_minimum": axis_start,
        "axis_maximum": axis_end,
        "minimum_y": min_y,
        "maximum_y": max_y,
        "column_count": axis_end - axis_start + 1,
        "vertical_count": max_y - min_y + 1,
        "cell_count": (axis_end - axis_start + 1) * (max_y - min_y + 1),
        "cell_encoding": "per-column inclusive-y run-length-v1",
        "material_focus": material,
        "columns": columns,
        "category_counts": _public_counter(category_counts, key_name="category"),
        "ore_materials": _public_counter(material_counts, key_name="material"),
        "lithology": _public_counter(lithology_counts, key_name="lithology"),
    }
    return dataset.make(
        operation="section",
        status="answered",
        result=result,
        limitations=[
            "The plane shows exact final cells; it does not attribute cave carving or ore placement without a matching controlled trace."
        ],
    )


def definitions(
    dataset: StudioDataset,
    *,
    material: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    require(material is None or bool(material.strip()), "material cannot be empty")
    require(query is None or bool(query.strip()), "definition query cannot be empty")
    material = material.strip() if material is not None else None
    query = query.strip() if query is not None else None
    candidates = dataset.definitions
    if material is not None:
        candidates = [
            row for row in candidates if material in (row.get("material_tokens") or [])
        ]
    if query is not None:
        needle = query.casefold()
        candidates = [
            row
            for row in candidates
            if needle
            in " ".join(
                [
                    str(row.get("relative_path", "")),
                    str(row.get("name", "")),
                    *(str(value) for value in row.get("material_tokens") or []),
                ]
            ).casefold()
        ]
    rows = [_definition_public(dataset, row) for row in candidates]
    selected_count = sum(
        row["trace_selection"]["deposit_instance_count"] for row in rows
    )
    status = "not-found" if not rows else ("answered" if dataset.impact is not None else "partial")
    uncertainty = []
    if dataset.impact is None and rows:
        uncertainty.append(
            "The V1 definition inventory lacks full priority, generator, filler, biome, and height controls."
        )
    return dataset.make(
        operation="definitions",
        status=status,
        result={
            "material": material,
            "query": query,
            "definition_count": len(rows),
            "definitions": rows,
            "selection_observed": selected_count > 0,
            "selected_deposit_instance_count": selected_count,
        },
        uncertainty=uncertainty,
        limitations=[
            "Definition compatibility and observed material counts do not prove that a definition was selected."
        ],
    )


def _fluid_definition_rows(dataset: StudioDataset) -> list[dict[str, Any]]:
    inventory_rows = {
        row["relative_path"]: row
        for row in dataset.inventory["definitions"]
        if row.get("kind") == "fluid"
    }
    controls = (
        {
            row["relative_path"]: row
            for row in dataset.impact["definition_controls"]["bedrock_fluid_definitions"]
        }
        if dataset.impact is not None
        else {}
    )
    rows: list[dict[str, Any]] = []
    for path in sorted(inventory_rows):
        base = inventory_rows[path]
        control = controls.get(path)
        merged = deepcopy(dict(control if control is not None else base))
        merged["relative_path"] = path
        merged["source_sha256"] = base["source_sha256"]
        merged["source_size_bytes"] = base["source_size_bytes"]
        clauses = merged.get("dimension_filter")
        if clauses is None:
            clauses = [
                f"dimension_id:{value}" for value in base.get("dimension_ids") or []
            ]
        merged["dimension_state"] = _dimension_match(
            clauses, dataset.region.manifest["dimensionId"], dataset.profile
        )
        merged["evidence_state"] = "declared"
        rows.append(merged)
    return rows


def _fluid_cell_identity(cell: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        cell.get("veinX"),
        cell.get("veinZ"),
        cell.get("queryChunkX"),
        cell.get("queryChunkZ"),
        cell.get("depositName"),
    )


def fluids(dataset: StudioDataset) -> dict[str, Any]:
    definition_rows = _fluid_definition_rows(dataset)
    definition_by_suffix: dict[str, dict[str, Any]] = {}
    definitions_by_basename: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in definition_rows:
        path = row["relative_path"]
        suffix = path.removeprefix("worldgen/fluid/")
        definition_by_suffix[suffix] = row
        definitions_by_basename[Path(suffix).name].append(row)
    cells: list[dict[str, Any]] = []
    fluid_yields: Counter[str] = Counter()
    for source in dataset.region.fluid_cells:
        row = deepcopy(dict(source))
        deposit_name = str(row.get("depositName", ""))
        matched = definition_by_suffix.get(deposit_name)
        match_state = "exact-path" if matched is not None else "unmatched"
        if matched is None:
            basename_matches = definitions_by_basename.get(Path(deposit_name).name, [])
            if len(basename_matches) == 1:
                matched = basename_matches[0]
                match_state = "unique-basename"
            elif len(basename_matches) > 1:
                match_state = "ambiguous-basename"
        row["definition"] = deepcopy(matched) if matched is not None else None
        row["definition_match_state"] = match_state
        row["evidence_state"] = "observed-final"
        if isinstance(row.get("fluidYield"), int) and isinstance(row.get("fluid"), str):
            fluid_yields[row["fluid"]] += row["fluidYield"]
        cells.append(row)
    cells.sort(key=_fluid_cell_identity)
    unresolved_matches = [
        row
        for row in cells
        if row["definition_match_state"]
        in {"ambiguous-basename", "unmatched"}
    ]
    status = (
        "not-found"
        if not cells
        else ("partial" if unresolved_matches else "answered")
    )
    uncertainty = (
        [
            f"{len(unresolved_matches)} virtual fluid cells could not be joined uniquely to an exact definition path."
        ]
        if unresolved_matches
        else []
    )
    return dataset.make(
        operation="fluids",
        status=status,
        result={
            "semantics": "virtual persistent GTCEu bedrock-fluid cells",
            "physical_blocks": False,
            "cell_count": len(cells),
            "yield_total": sum(fluid_yields.values()),
            "fluid_yields": _public_counter(fluid_yields, key_name="fluid"),
            "cells": cells,
            "definition_count": len(definition_rows),
            "definitions": definition_rows,
        },
        uncertainty=uncertainty,
        limitations=[
            "Bedrock-fluid cells are virtual chunk-grid assignments, not underground fluid blocks or cave pockets."
        ],
    )


def _dataset_aggregate(dataset: StudioDataset) -> dict[str, Counter[str]]:
    return {
        "materials": _aggregate_counter(
            metric.ore_counts for metric in dataset.region.chunk_metrics.values()
        ),
        "ore_hosts": _aggregate_counter(
            metric.ore_host_counts for metric in dataset.region.chunk_metrics.values()
        ),
        "lithology": _aggregate_counter(
            metric.host_counts for metric in dataset.region.chunk_metrics.values()
        ),
        "surface_indicators": _aggregate_counter(
            metric.surface_indicator_counts
            for metric in dataset.region.chunk_metrics.values()
        ),
    }


def _counter_delta(
    baseline: Counter[str], candidate: Counter[str], *, key_name: str
) -> list[dict[str, Any]]:
    return [
        {
            key_name: key,
            "baseline": baseline[key],
            "candidate": candidate[key],
            "delta": candidate[key] - baseline[key],
        }
        for key in sorted(set(baseline) | set(candidate))
        if baseline[key] != candidate[key]
    ]


def _namespaced_sources(dataset: StudioDataset, prefix: str) -> list[dict[str, Any]]:
    rows = deepcopy(dataset.sources())
    for row in rows:
        row["source_id"] = f"{prefix}:{row['source_id']}"
    return rows


def compare(
    baseline: StudioDataset,
    candidate: StudioDataset,
    *,
    material: str | None = None,
) -> dict[str, Any]:
    """Compare aligned declared definitions and exact final-state captures."""

    require(material is None or bool(material.strip()), "material cannot be empty")
    material = material.strip() if material is not None else None
    sources = _namespaced_sources(baseline, "baseline") + _namespaced_sources(
        candidate, "candidate"
    )
    limitations = sorted(set(baseline.limitations) | set(candidate.limitations))
    navigation = [
        {
            "kind": f"baseline-{row['kind']}",
            "label": f"Baseline: {row['label']}",
            "target": row["target"],
        }
        for row in baseline.navigation()
    ] + [
        {
            "kind": f"candidate-{row['kind']}",
            "label": f"Candidate: {row['label']}",
            "target": row["target"],
        }
        for row in candidate.navigation()
    ]
    profile_match = (
        baseline.profile_binding.sha256 == candidate.profile_binding.sha256
        and baseline.profile["profile_id"] == candidate.profile["profile_id"]
    )
    scope_match = baseline.scope == candidate.scope
    if not profile_match or not scope_match:
        reasons = []
        if not profile_match:
            reasons.append("Pack-profile identities differ.")
        if not scope_match:
            reasons.append("World seed, dimension, or chunk window differs.")
        return make_result(
            operation="compare",
            status="incomparable",
            profile=candidate.profile,
            profile_binding=candidate.profile_binding,
            scope=candidate.scope,
            sources=sources,
            result={
                "aligned": False,
                "material": material,
                "reasons": reasons,
                "baseline_scope": baseline.scope,
                "candidate_scope": candidate.scope,
            },
            uncertainty=reasons,
            limitations=limitations,
            navigation=navigation,
        )

    baseline_definitions = {
        row["relative_path"]: row for row in baseline.definitions
    }
    candidate_definitions = {
        row["relative_path"]: row for row in candidate.definitions
    }
    definition_paths = sorted(set(baseline_definitions) | set(candidate_definitions))
    definition_changes: list[dict[str, Any]] = []
    for path in definition_paths:
        before = baseline_definitions.get(path)
        after = candidate_definitions.get(path)
        if before is None:
            definition_changes.append({"definition_path": path, "change": "added"})
        elif after is None:
            definition_changes.append({"definition_path": path, "change": "removed"})
        else:
            before_identity = deepcopy(before)
            after_identity = deepcopy(after)
            if sha256_json(before_identity) != sha256_json(after_identity):
                definition_changes.append(
                    {
                        "definition_path": path,
                        "change": "changed",
                        "baseline_sha256": before.get("source_sha256"),
                        "candidate_sha256": after.get("source_sha256"),
                    }
                )

    baseline_aggregate = _dataset_aggregate(baseline)
    candidate_aggregate = _dataset_aggregate(candidate)
    material_deltas = _counter_delta(
        baseline_aggregate["materials"],
        candidate_aggregate["materials"],
        key_name="material",
    )
    if material is not None:
        material_deltas = [row for row in material_deltas if row["material"] == material]
    chunk_deltas: list[dict[str, Any]] = []
    for chunk in sorted(candidate.region.chunk_metrics, key=lambda row: (row[1], row[0])):
        before = baseline.region.chunk_metrics[chunk]
        after = candidate.region.chunk_metrics[chunk]
        before_ore = before.ore_counts[material] if material else sum(before.ore_counts.values())
        after_ore = after.ore_counts[material] if material else sum(after.ore_counts.values())
        row = {
            "chunk_x": chunk[0],
            "chunk_z": chunk[1],
            "ore_blocks": {
                "baseline": before_ore,
                "candidate": after_ore,
                "delta": after_ore - before_ore,
            },
            "subsurface_air": {
                "baseline": before.subsurface_air,
                "candidate": after.subsurface_air,
                "delta": after.subsurface_air - before.subsurface_air,
            },
            "exposed_ore_faces": {
                "baseline": (
                    before.exposed_ore_faces_by_material[material]
                    if material
                    else before.exposed_ore_faces
                ),
                "candidate": (
                    after.exposed_ore_faces_by_material[material]
                    if material
                    else after.exposed_ore_faces
                ),
                "delta": (
                    (
                        after.exposed_ore_faces_by_material[material]
                        if material
                        else after.exposed_ore_faces
                    )
                    - (
                        before.exposed_ore_faces_by_material[material]
                        if material
                        else before.exposed_ore_faces
                    )
                ),
            },
            "mean_height": {
                "baseline": before.mean_height,
                "candidate": after.mean_height,
                "delta": round(after.mean_height - before.mean_height, 6),
            },
            "biome": {
                "baseline": before.dominant_biome,
                "candidate": after.dominant_biome,
                "changed": before.dominant_biome != after.dominant_biome,
            },
            "fluid_yield": {
                "baseline": before.fluid_yield,
                "candidate": after.fluid_yield,
                "delta": after.fluid_yield - before.fluid_yield,
            },
            "surface_indicators": {
                "baseline": (
                    before.surface_indicator_counts[material]
                    if material
                    else sum(before.surface_indicator_counts.values())
                ),
                "candidate": (
                    after.surface_indicator_counts[material]
                    if material
                    else sum(after.surface_indicator_counts.values())
                ),
                "delta": (
                    (
                        after.surface_indicator_counts[material]
                        if material
                        else sum(after.surface_indicator_counts.values())
                    )
                    - (
                        before.surface_indicator_counts[material]
                        if material
                        else sum(before.surface_indicator_counts.values())
                    )
                ),
            },
        }
        numeric_changed = any(
            row[key]["delta"] != 0
            for key in (
                "ore_blocks",
                "subsurface_air",
                "exposed_ore_faces",
                "mean_height",
                "fluid_yield",
                "surface_indicators",
            )
        )
        if numeric_changed or row["biome"]["changed"]:
            chunk_deltas.append(row)

    baseline_fluids = {
        _fluid_cell_identity(row): row for row in baseline.region.fluid_cells
    }
    candidate_fluids = {
        _fluid_cell_identity(row): row for row in candidate.region.fluid_cells
    }
    fluid_changes: list[dict[str, Any]] = []
    for identity in sorted(set(baseline_fluids) | set(candidate_fluids), key=repr):
        before = baseline_fluids.get(identity)
        after = candidate_fluids.get(identity)
        if before != after:
            fluid_changes.append(
                {
                    "identity": list(identity),
                    "baseline": deepcopy(before),
                    "candidate": deepcopy(after),
                }
            )

    before_ore_total = (
        baseline_aggregate["materials"][material]
        if material
        else sum(baseline_aggregate["materials"].values())
    )
    after_ore_total = (
        candidate_aggregate["materials"][material]
        if material
        else sum(candidate_aggregate["materials"].values())
    )
    before_air = sum(row.subsurface_air for row in baseline.region.chunk_metrics.values())
    after_air = sum(row.subsurface_air for row in candidate.region.chunk_metrics.values())
    before_indicators = (
        baseline_aggregate["surface_indicators"][material]
        if material
        else sum(baseline_aggregate["surface_indicators"].values())
    )
    after_indicators = (
        candidate_aggregate["surface_indicators"][material]
        if material
        else sum(candidate_aggregate["surface_indicators"].values())
    )
    result = {
        "aligned": True,
        "material": material,
        "summary": {
            "ore_blocks": {
                "baseline": before_ore_total,
                "candidate": after_ore_total,
                "delta": after_ore_total - before_ore_total,
            },
            "subsurface_air": {
                "baseline": before_air,
                "candidate": after_air,
                "delta": after_air - before_air,
            },
            "surface_indicators": {
                "baseline": before_indicators,
                "candidate": after_indicators,
                "delta": after_indicators - before_indicators,
            },
            "changed_chunk_count": len(chunk_deltas),
            "definition_change_count": len(definition_changes),
            "fluid_change_count": len(fluid_changes),
        },
        "definitions": definition_changes,
        "materials": material_deltas,
        "ore_hosts": _counter_delta(
            baseline_aggregate["ore_hosts"],
            candidate_aggregate["ore_hosts"],
            key_name="lithology",
        ),
        "captured_lithology": _counter_delta(
            baseline_aggregate["lithology"],
            candidate_aggregate["lithology"],
            key_name="lithology",
        ),
        "surface_indicators": _counter_delta(
            baseline_aggregate["surface_indicators"],
            candidate_aggregate["surface_indicators"],
            key_name="material",
        ),
        "chunks": chunk_deltas,
        "fluid_cells": fluid_changes,
    }
    return make_result(
        operation="compare",
        status="answered",
        profile=candidate.profile,
        profile_binding=candidate.profile_binding,
        scope=candidate.scope,
        sources=sources,
        result=result,
        limitations=sorted(
            set(limitations)
            | {
                "Aligned final-state differences do not by themselves establish which generator or configuration change caused them."
            }
        ),
        navigation=navigation,
    )
