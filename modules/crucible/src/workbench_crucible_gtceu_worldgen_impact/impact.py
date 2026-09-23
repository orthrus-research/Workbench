"""Build a closed account of GTCEu 2.8.10 world-generation effects.

The V1 inventory binds and normalizes definitions.  This additive inventory
composes that result with global controls, the complete relevant class surface,
effect lifecycles, cache/reload hazards, and optional runtime integration
candidates.  Byte-token matches are navigation evidence, never behavioral
attribution.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping
from zipfile import BadZipFile, ZipFile

from workbench_crucible_gtceu_worldgen import (
    build_gtceu_worldgen_inventory,
    parse_gtceu_worldgen_inventory,
)
from workbench_crucible_gtceu_worldgen.inventory import canonical_json_bytes


GTCEU_WORLDGEN_IMPACT_FORMAT = (
    "workbench-crucible-gtceu-worldgen-impact-inventory-v2"
)
GTCEU_WORLDGEN_IMPACT_PREFIX = "crucible-gtceu-worldgen-impact:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
SUPPORTED_MOD_VERSION = "2.8.10-beta"
SUPPORTED_SOURCE_TAG = "v2.8.10"
SUPPORTED_SOURCE_COMMIT = "9fe140febe8747bbe2f06dfd570421331ec06f4b"

WORLDGEN_CONTROL_TYPES = {
    "addLoot": "boolean",
    "additionalVeinsInSection": "integer",
    "allUniqueStoneTypes": "boolean",
    "disableRubberTreeGeneration": "boolean",
    "disableVanillaOres": "boolean",
    "generateVeinsInCenterOfChunk": "boolean",
    "increaseDungeonLoot": "boolean",
    "minVeinsInSection": "integer",
    "rubberTreeRateIncrease": "number",
}

IMPACT_CLASS_PREFIXES = (
    "gregtech/api/worldgen/",
    "gregtech/common/command/worldgen/",
    "gregtech/common/worldgen/",
    "gregtech/loaders/dungeon/",
)
IMPACT_EXACT_CLASSES = frozenset(
    {
        "gregtech/api/capability/SimpleCapabilityManager.class",
        "gregtech/api/capability/impl/FluidDrillLogic.class",
        "gregtech/api/unification/ore/StoneType.class",
        "gregtech/common/ConfigHolder$WorldGenOptions.class",
        "gregtech/common/EventHandlers.class",
        "gregtech/common/blocks/wood/BlockRubberSapling.class",
        "gregtech/common/items/behaviors/TricorderBehavior.class",
        "gregtech/common/terminal/app/prospector/widget/WidgetProspectingMap.class",
        "gregtech/core/CoreModule.class",
        "gregtech/integration/jei/JustEnoughItemsModule.class",
    }
)

INTEGRATION_TOKENS = {
    "gtceu_worldgen_package": (b"gregtech/api/worldgen/", b"gregtech.api.worldgen."),
    "worldgen_registry": (
        b"gregtech/api/worldgen/config/WorldGenRegistry",
        b"gregtech.api.worldgen.config.WorldGenRegistry",
    ),
    "ore_generator": (
        b"gregtech/api/worldgen/generator/WorldGeneratorImpl",
        b"gregtech.api.worldgen.generator.WorldGeneratorImpl",
    ),
    "ore_grid": (
        b"gregtech/api/worldgen/generator/CachedGridEntry",
        b"gregtech.api.worldgen.generator.CachedGridEntry",
    ),
    "bedrock_fluid_map": (
        b"gregtech/api/worldgen/bedrockFluids/BedrockFluidVeinHandler",
        b"gregtech.api.worldgen.bedrockFluids.BedrockFluidVeinHandler",
    ),
    "worldgen_controls": (
        b"gregtech/common/ConfigHolder$WorldGenOptions",
        b"gregtech.common.ConfigHolder$WorldGenOptions",
    ),
    "gtceu_shared_rng": (
        b"gregtech/api/GTValues;RNG:Ljava/util/Random;",
        b"gregtech.api.GTValues.RNG",
    ),
    "host_stone_registry": (
        b"gregtech/api/unification/ore/StoneType",
        b"gregtech.api.unification.ore.StoneType",
    ),
    "surface_rock": (
        b"gregtech/common/blocks/BlockSurfaceRock",
        b"gregtech.common.blocks.BlockSurfaceRock",
    ),
    "rubber_tree": (
        b"gregtech/common/worldgen/WorldGenRubberTree",
        b"gregtech.common.worldgen.WorldGenRubberTree",
    ),
}

DIRECT_WORLDGEN_SCRIPT_TOKENS = (
    "gregtech.api.worldgen",
    "WorldGenRegistry",
    "OreDepositDefinition",
    "BedrockFluidDepositDefinition",
    "BedrockFluidVeinHandler",
    "CachedGridEntry",
    "WorldGeneratorImpl",
)

VANILLA_ORE_EVENT_TYPES = (
    "COAL",
    "DIAMOND",
    "EMERALD",
    "GOLD",
    "IRON",
    "LAPIS",
    "QUARTZ",
    "REDSTONE",
)

LOOT_ROLL_ADDITIONS = (
    ("minecraft:chests/spawn_bonus_chest", 2, 4),
    ("minecraft:chests/simple_dungeon", 1, 3),
    ("minecraft:chests/desert_pyramid", 2, 4),
    ("minecraft:chests/jungle_temple", 4, 8),
    ("minecraft:chests/jungle_temple_dispenser", 0, 2),
    ("minecraft:chests/abandoned_mineshaft", 1, 3),
    ("minecraft:chests/village_blacksmith", 2, 6),
    ("minecraft:chests/stronghold_crossing", 2, 4),
    ("minecraft:chests/stronghold_corridor", 2, 4),
    ("minecraft:chests/stronghold_library", 4, 8),
)

_TOP_LEVEL_KEYS = {
    "format",
    "schema_version",
    "inventory_id",
    "canonicalization_id",
    "adapter_profile",
    "definition_inventory",
    "impact_class_surface",
    "runtime_controls",
    "definition_controls",
    "api_surface",
    "effect_catalog",
    "reload_and_persistence",
    "runtime_integration_scan",
    "pack_context",
    "source_audit",
    "summary",
    "boundaries",
}

_BOUNDARIES = {
    "behavioral_completeness_claimed": False,
    "bytecode_token_match_proves_behavior": False,
    "configuration_was_modified": False,
    "fresh_world_runtime_probe_completed": False,
    "gregtech_version_specific": True,
    "recurrent_complex_integration_added": False,
    "reload_hazards_are_source_derived_not_runtime_proven": True,
    "structure_loot_target_reachability_proven": False,
}


class GtceuWorldgenImpactValidationError(ValueError):
    """Raised when exact inputs cannot support a closed impact inventory."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GtceuWorldgenImpactValidationError(message)


def _stable_bytes(path: Path) -> bytes:
    resolved = path.resolve(strict=True)
    before = resolved.stat()
    _require(stat.S_ISREG(before.st_mode), f"not a regular file: {resolved}")
    data = resolved.read_bytes()
    after = resolved.stat()
    _require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        f"file changed while inventorying it: {resolved}",
    )
    return data


def _binding(path: Path, relative_path: str | None = None) -> dict[str, Any]:
    data = _stable_bytes(path)
    return {
        "relative_path": relative_path or path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _json_object(path: Path, context: str) -> Mapping[str, Any]:
    try:
        value = json.loads(_stable_bytes(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GtceuWorldgenImpactValidationError(
            f"cannot parse {context}: {exc}"
        ) from exc
    _require(isinstance(value, Mapping), f"{context} must be a JSON object")
    return value


def _tree_binding(files: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(files, key=lambda row: row["relative_path"])
    material = "".join(
        f"{row['relative_path']}\0{row['sha256']}\0{row['size_bytes']}\n"
        for row in ordered
    ).encode("utf-8")
    return {
        "tree_sha256": hashlib.sha256(material).hexdigest(),
        "file_count": len(ordered),
        "files": ordered,
    }


def _parse_scalar(kind: str, raw: str, context: str) -> Any:
    value = raw.strip()
    try:
        if kind == "boolean":
            _require(value.lower() in {"true", "false"}, f"{context} is not boolean")
            return value.lower() == "true"
        if kind == "integer":
            return int(value)
        if kind == "number":
            return float(value)
    except ValueError as exc:
        raise GtceuWorldgenImpactValidationError(
            f"cannot parse {context}: {value!r}"
        ) from exc
    raise GtceuWorldgenImpactValidationError(f"unsupported scalar kind: {kind}")


def _parse_forge_controls(config_path: Path) -> dict[str, Any]:
    text = _stable_bytes(config_path).decode("utf-8")
    found: dict[str, list[str]] = defaultdict(list)
    assignment = re.compile(r"^\s*[BID]:([^=]+)=(.*?)\s*$")
    for line in text.splitlines():
        match = assignment.match(line)
        if match:
            found[match.group(1).strip().strip('"')].append(match.group(2))
    values: dict[str, Any] = {}
    for name, kind in WORLDGEN_CONTROL_TYPES.items():
        rows = found.get(name, [])
        _require(len(rows) == 1, f"expected exactly one GTCEu control {name}, got {len(rows)}")
        values[name] = _parse_scalar(kind, rows[0], f"GTCEu control {name}")
    _require(values["minVeinsInSection"] >= 0, "minimum vein count is negative")
    _require(values["additionalVeinsInSection"] >= 0, "additional vein count is negative")
    return {
        "artifact": _binding(config_path, "config/gregtech/gregtech.cfg"),
        "values": values,
        "derived": {
            "counted_ore_veins_per_3x3_grid": {
                "minimum": values["minVeinsInSection"],
                "maximum_inclusive": values["minVeinsInSection"]
                + values["additionalVeinsInSection"],
                "count_as_vein_false_consumes_quota": False,
            },
            "ore_centers_for_counted_veins_are_chunk_centered": values[
                "generateVeinsInCenterOfChunk"
            ],
            "rubber_tree_world_generation_enabled": not values[
                "disableRubberTreeGeneration"
            ],
            "vanilla_ore_event_denial_enabled": values["disableVanillaOres"],
            "denied_vanilla_ore_event_types": list(VANILLA_ORE_EVENT_TYPES)
            if values["disableVanillaOres"]
            else [],
            "gtceu_loot_entries_enabled": values["addLoot"],
            "gtceu_extra_loot_rolls_enabled": values["increaseDungeonLoot"],
        },
    }


def _definition_payloads(config_root: Path) -> list[tuple[str, str, Mapping[str, Any]]]:
    rows: list[tuple[str, str, Mapping[str, Any]]] = []
    for kind, folder in (
        ("fluid", config_root / "worldgen/fluid"),
        ("ore", config_root / "worldgen/vein"),
    ):
        _require(folder.is_dir(), f"missing GTCEu {kind} directory: {folder}")
        for path in sorted(folder.rglob("*.json")):
            relative = path.relative_to(config_root).as_posix()
            rows.append((kind, relative, _json_object(path, relative)))
    _require(rows, "GTCEu configuration contains no worldgen definitions")
    return sorted(rows, key=lambda row: row[1])


def _declaration_kind(value: str) -> str:
    for prefix in ("block:", "fluid:", "ore:", "ore_dict:"):
        if value.startswith(prefix):
            return prefix[:-1]
    return "unknown_string"


def _walk_filler(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield _declaration_kind(value)
    elif isinstance(value, Mapping):
        if "block" in value:
            yield "exact_block_state"
        declared = value.get("type")
        if isinstance(declared, str):
            yield declared
        for child in value.values():
            yield from _walk_filler(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_filler(child)


def _walk_predicates(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value in {"any", "stone_type"}:
            yield value
            return
        for prefix in ("stone_type:", "block:", "ore_dict:"):
            if value.startswith(prefix):
                yield prefix[:-1]
                return
        yield "unknown_string"
    elif isinstance(value, Mapping):
        yield "exact_block_state"
    elif isinstance(value, list):
        for child in value:
            yield from _walk_predicates(child)


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))
    ]


def _definition_controls(config_root: Path) -> dict[str, Any]:
    ore_rows: list[dict[str, Any]] = []
    fluid_rows: list[dict[str, Any]] = []
    generator_paths: dict[str, list[str]] = defaultdict(list)
    filler_paths: dict[str, list[str]] = defaultdict(list)
    populator_paths: dict[str, list[str]] = defaultdict(list)
    biome_types: Counter[str] = Counter()
    dimension_clauses: Counter[str] = Counter()
    predicate_types: Counter[str] = Counter()
    filler_declarations: Counter[str] = Counter()
    nested_filler_paths: dict[str, set[str]] = defaultdict(set)

    for kind, relative, payload in _definition_payloads(config_root):
        dimension_filter = payload.get("dimension_filter", ["is_surface_world"])
        if isinstance(dimension_filter, list):
            dimension_clauses.update(
                str(value) for value in dimension_filter if isinstance(value, str)
            )
        biome_modifier = payload.get("biome_modifier")
        if isinstance(biome_modifier, Mapping):
            modifier_type = biome_modifier.get("type")
            if isinstance(modifier_type, str):
                biome_types[modifier_type] += 1

        if kind == "fluid":
            yields = payload.get("yield")
            depletion = payload.get("depletion")
            _require(isinstance(yields, Mapping), f"{relative}.yield must be an object")
            _require(
                isinstance(depletion, Mapping),
                f"{relative}.depletion must be an object",
            )
            fluid_rows.append(
                {
                    "relative_path": relative,
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "fluid": payload.get("fluid"),
                    "weight": payload.get("weight"),
                    "yield_minimum": yields.get("min"),
                    "yield_maximum_exclusive": yields.get("max"),
                    "depletion_amount": depletion.get("amount"),
                    "depletion_chance_configured": depletion.get("chance"),
                    "depletion_chance_effective": max(
                        0, min(100, int(depletion.get("chance", 0)))
                    ),
                    "depleted_yield": depletion.get("depleted_yield", 0),
                    "dimension_filter": deepcopy(dimension_filter),
                    "biome_modifier": deepcopy(biome_modifier),
                }
            )
            continue

        generator = payload.get("generator")
        filler = payload.get("filler")
        populator = payload.get("vein_populator")
        _require(isinstance(generator, Mapping), f"{relative}.generator must be an object")
        _require(isinstance(filler, Mapping), f"{relative}.filler must be an object")
        generator_type = generator.get("type")
        filler_type = filler.get("type")
        _require(isinstance(generator_type, str), f"{relative}.generator.type missing")
        _require(isinstance(filler_type, str), f"{relative}.filler.type missing")
        populator_type = "none"
        if populator is not None:
            _require(
                isinstance(populator, Mapping),
                f"{relative}.vein_populator must be an object",
            )
            _require(
                isinstance(populator.get("type"), str),
                f"{relative}.vein_populator.type missing",
            )
            populator_type = str(populator["type"])
        generator_paths[generator_type].append(relative)
        filler_paths[filler_type].append(relative)
        populator_paths[populator_type].append(relative)
        for declaration in _walk_filler(filler):
            filler_declarations[declaration] += 1
            if declaration in {"weight_random", "state_match"}:
                nested_filler_paths[declaration].add(relative)
        predicate = payload.get("generation_predicate", "stone_type")
        predicate_types.update(_walk_predicates(predicate))
        ore_rows.append(
            {
                "relative_path": relative,
                "name": payload.get("name"),
                "description": payload.get("description"),
                "weight": payload.get("weight"),
                "priority": payload.get("priority", 0),
                "density": payload.get("density"),
                "min_height": payload.get("min_height"),
                "max_height": payload.get("max_height"),
                "count_as_vein": payload.get("count_as_vein", True),
                "dimension_filter": deepcopy(dimension_filter),
                "biome_modifier": deepcopy(biome_modifier),
                "generation_predicate": deepcopy(predicate),
                "generator": deepcopy(dict(generator)),
                "filler_type": filler_type,
                "vein_populator": deepcopy(populator),
            }
        )

    return {
        "ore_definitions": ore_rows,
        "bedrock_fluid_definitions": fluid_rows,
        "component_usage": {
            "shape_generators": [
                {"type": key, "definition_count": len(paths), "definition_paths": sorted(paths)}
                for key, paths in sorted(generator_paths.items())
            ],
            "block_fillers": [
                {"type": key, "definition_count": len(paths), "definition_paths": sorted(paths)}
                for key, paths in sorted(filler_paths.items())
            ],
            "vein_populators": [
                {"type": key, "definition_count": len(paths), "definition_paths": sorted(paths)}
                for key, paths in sorted(populator_paths.items())
            ],
            "nested_filler_declarations": _counter_rows(filler_declarations),
            "nested_filler_definition_paths": {
                key: sorted(paths) for key, paths in sorted(nested_filler_paths.items())
            },
            "generation_predicates": _counter_rows(predicate_types),
            "dimension_filter_clauses": _counter_rows(dimension_clauses),
            "biome_modifier_types": _counter_rows(biome_types),
        },
    }


def _impact_class_surface(jar_path: Path) -> dict[str, Any]:
    try:
        with ZipFile(jar_path) as archive:
            entries = sorted(
                name
                for name in archive.namelist()
                if name.endswith(".class")
                and (
                    any(name.startswith(prefix) for prefix in IMPACT_CLASS_PREFIXES)
                    or name in IMPACT_EXACT_CLASSES
                )
            )
            missing = sorted(IMPACT_EXACT_CLASSES - set(entries))
            _require(not missing, f"GTCEu jar lacks impact classes: {missing}")
            bindings = []
            for name in entries:
                data = archive.read(name)
                bindings.append(
                    {
                        "relative_path": name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                )
    except BadZipFile as exc:
        raise GtceuWorldgenImpactValidationError(f"invalid GTCEu jar: {exc}") from exc
    return _tree_binding(bindings)


def _paths_for_populator(controls: Mapping[str, Any], kind: str) -> list[str]:
    for row in controls["component_usage"]["vein_populators"]:
        if row["type"] == kind:
            return list(row["definition_paths"])
    return []


def _component_count(
    controls: Mapping[str, Any], collection: str, component_type: str
) -> int:
    for row in controls["component_usage"][collection]:
        if row["type"] == component_type:
            return int(row["definition_count"])
    return 0


def _api_surface(controls: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the exact public seams without implying lifecycle safety."""

    def component(
        identifier: str,
        implementation: str,
        collection: str,
    ) -> dict[str, Any]:
        return {
            "identifier": identifier,
            "implementation": implementation,
            "active_definition_count": _component_count(
                controls, collection, identifier
            ),
        }

    return {
        "forge_entrypoints": [
            {
                "method": "WorldGeneratorImpl.generate(Random,int,int,World,IChunkGenerator,IChunkProvider)",
                "registration": "GameRegistry.registerWorldGenerator(WorldGeneratorImpl.INSTANCE, 1)",
                "role": "physical ore population followed by rubber-tree attempts",
            },
            {
                "method": "WorldGeneratorImpl.onOreGenerate(OreGenEvent.GenerateMinable)",
                "registration": "MinecraftForge.ORE_GEN_BUS at HIGH priority",
                "role": "optional denial of eight vanilla ore event types",
            },
            {
                "method": "ChestGenHooks.onWorldLoad(LootTableLoadEvent)",
                "registration": "Forge event bus",
                "role": "GTCEu entries and optional roll additions for ten vanilla chest tables",
            },
        ],
        "registered_component_factories": {
            "shape_generators": [
                component("ellipsoid", "EllipsoidGenerator", "shape_generators"),
                component("sphere", "SphereGenerator", "shape_generators"),
                component("plate", "PlateGenerator", "shape_generators"),
                component("single", "SingleBlockGenerator", "shape_generators"),
                component("layered", "LayeredGenerator", "shape_generators"),
            ],
            "block_fillers": [
                component("simple", "SimpleBlockFiller", "block_fillers"),
                component("layered", "LayeredBlockFiller", "block_fillers"),
                component("ignore_bedrock", "BlacklistedBlockFiller", "block_fillers"),
            ],
            "vein_populators": [
                component("surface_rock", "SurfaceRockPopulator", "vein_populators"),
                component("fluid_spring", "FluidSpringPopulator", "vein_populators"),
                component("surface_block", "SurfaceBlockPopulator", "vein_populators"),
            ],
        },
        "registry_methods": [
            {
                "method": "WorldGenRegistry.addVeinDefinitions(OreDepositDefinition)",
                "role": "stage an addon ore definition",
                "safe_phase": "before initializeRegistry; later use requires an explicit rebuild policy",
            },
            {
                "method": "WorldGenRegistry.addVeinDefinitions(BedrockFluidDepositDefinition)",
                "role": "stage an addon bedrock-fluid definition",
                "safe_phase": "before initializeRegistry; later use requires an explicit rebuild policy",
            },
            {
                "method": "WorldGenRegistry.removeVeinDefinitions(IWorldgenDefinition)",
                "role": "stage removal of a registered definition and its extracted file",
                "safe_phase": "controlled configuration materialization; not a chunk callback",
            },
            {
                "method": "WorldGenRegistry.registerShapeGenerator(String,Supplier<ShapeGenerator>)",
                "role": "register a custom JSON shape type",
                "safe_phase": "before any dependent definition is parsed",
            },
            {
                "method": "WorldGenRegistry.registerBlockFiller(String,Supplier<BlockFiller>)",
                "role": "register a custom JSON filler type",
                "safe_phase": "before any dependent definition is parsed",
            },
            {
                "method": "WorldGenRegistry.registerVeinPopulator(String,Supplier<IVeinPopulator>)",
                "role": "register a custom JSON post-populator type",
                "safe_phase": "before any dependent definition is parsed",
            },
            {
                "method": "WorldGenRegistry.reinitializeRegisteredVeins()",
                "role": "re-extract/read definitions and rebuild public lists",
                "safe_phase": "not a safe general hot reload in this version; see reload_and_persistence",
            },
        ],
        "bedrock_fluid_methods": [
            {
                "method": "BedrockFluidVeinHandler.getFluidVeinWorldEntry(World,int,int)",
                "role": "lazy cell selection and complete cell observation",
                "mutates_state": True,
            },
            {
                "method": "BedrockFluidVeinHandler.getFluidInChunk/getFluidYield/getDepletedFluidYield/getOperationsRemaining",
                "role": "consumer-facing cell observations",
                "mutates_state": True,
            },
            {
                "method": "BedrockFluidVeinHandler.depleteVein(World,int,int,int,boolean)",
                "role": "decrease persistent operations according to definition policy",
                "mutates_state": True,
            },
            {
                "method": "BedrockFluidVeinHandler.recalculateChances(boolean)",
                "role": "clear total-weight caches and optionally publish a client packet",
                "mutates_state": True,
            },
        ],
        "spatial_and_state_constants": {
            "ore_grid_chunks": [3, 3],
            "ore_neighbor_grids_consulted_per_chunk": 9,
            "ore_grid_cache_maximum_entries_per_world": 300,
            "ore_grid_cache_expire_after_access_minutes": 5,
            "ore_block_write_flags": 16,
            "bedrock_fluid_cell_chunks": [8, 8],
            "bedrock_fluid_initial_operations": 100000,
        },
        "groovyscript_boundary": {
            "dedicated_gtceu_worldgen_dsl_found_in_tagged_source": False,
            "recommended_role": "publish a validated immutable declarative plan before world construction",
            "forbidden_hot_path_role": "invoke Groovy dynamically for each chunk, block, shape sample or fluid-cell query",
        },
    }


def _effect_catalog(
    controls: Mapping[str, Any],
    runtime_controls: Mapping[str, Any],
    runtime_mod_names: set[str],
) -> list[dict[str, Any]]:
    values = runtime_controls["values"]
    ore_count = len(controls["ore_definitions"])
    fluid_count = len(controls["bedrock_fluid_definitions"])
    surface_rock = _paths_for_populator(controls, "surface_rock")
    fluid_spring = _paths_for_populator(controls, "fluid_spring")
    surface_block = _paths_for_populator(controls, "surface_block")
    galacticraft_present = any("galacticraft" in name.lower() for name in runtime_mod_names)

    def effect(
        effect_id: str,
        classification: str,
        status: str,
        writes_blocks: bool,
        timing: str,
        spatial_model: str,
        controls_text: str,
        observation: str,
        modification: str,
        source_classes: list[str],
        active_basis: str,
    ) -> dict[str, Any]:
        return {
            "effect_id": effect_id,
            "classification": classification,
            "status": status,
            "active_basis": active_basis,
            "writes_blocks": writes_blocks,
            "timing": timing,
            "spatial_model": spatial_model,
            "control_surface": controls_text,
            "observation_surface": observation,
            "recommended_modification_seam": modification,
            "source_classes": source_classes,
        }

    rows = [
        effect(
            "physical_ore_veins",
            "terrain_mutation",
            "active" if ore_count else "inactive",
            True,
            "Forge IWorldGenerator population",
            "deterministic 3x3-chunk grids; current chunk consults nine neighboring grid entries",
            "ore JSON plus global vein-count and centering controls",
            "exact ore block states, deposit centers, selected definition, placement counts and timings",
            "checked JSON overlay first; custom factories before definition parsing for new algorithms",
            [
                "gregtech/api/worldgen/generator/WorldGeneratorImpl.class",
                "gregtech/api/worldgen/generator/CachedGridEntry.class",
                "gregtech/api/worldgen/config/OreDepositDefinition.class",
            ],
            f"{ore_count} configured ore definitions",
        ),
        effect(
            "surface_rock_indicators",
            "terrain_decoration",
            "active" if surface_rock else "inactive",
            True,
            "after a vein writes at least one block in the chunk",
            "one or two random chunk attempts plus the successful grid center; skips flat worlds",
            "vein_populator.type=surface_rock and material",
            "exact surface-rock blocks correlated with candidate deposits",
            "replace/remove the populator in a checked definition overlay",
            ["gregtech/api/worldgen/populator/SurfaceRockPopulator.class"],
            f"{len(surface_rock)} definitions use surface_rock",
        ),
        effect(
            "fluid_spring_populator",
            "terrain_mutation",
            "active" if fluid_spring else "available_not_selected",
            True,
            "ore buffer population",
            "vertical fluid pipe from a vein fluid block toward terrain height",
            "vein_populator.type=fluid_spring",
            "exact placed fluid blocks and associated deposit buffer writes",
            "definition overlay or custom registered populator",
            ["gregtech/api/worldgen/populator/FluidSpringPopulator.class"],
            f"{len(fluid_spring)} definitions use fluid_spring",
        ),
        effect(
            "surface_block_populator",
            "terrain_decoration",
            "active" if surface_block else "available_not_selected",
            True,
            "after successful ore placement",
            "random chunk attempts plus the successful grid center; skips flat worlds",
            "vein_populator.type=surface_block",
            "exact configured surface blocks",
            "definition overlay or custom registered populator",
            ["gregtech/api/worldgen/populator/SurfaceBlockPopulator.class"],
            f"{len(surface_block)} definitions use surface_block",
        ),
        effect(
            "vanilla_ore_event_denial",
            "event_policy",
            "active" if values["disableVanillaOres"] else "inactive",
            False,
            "Forge ORE_GEN_BUS at HIGH priority",
            "each GenerateMinable event of eight named vanilla types",
            "disableVanillaOres",
            "event result traces plus final absence/presence in fresh chunks",
            "global config or an intentionally ordered Forge event policy",
            ["gregtech/api/worldgen/generator/WorldGeneratorImpl.class"],
            str(values["disableVanillaOres"]).lower(),
        ),
        effect(
            "rubber_tree_worldgen",
            "vegetation",
            "active" if not values["disableRubberTreeGeneration"] else "inactive",
            True,
            "same Forge IWorldGenerator call, after ore population",
            "chunk-corner biome samples; SWAMP, FOREST and JUNGLE dictionary tags",
            "disableRubberTreeGeneration and rubberTreeRateIncrease",
            "tree attempts, SaplingGrowTreeEvent result, exact logs/leaves",
            "global config for enable/rate; replace generator only through an explicit mod hook",
            [
                "gregtech/api/worldgen/generator/WorldGeneratorImpl.class",
                "gregtech/common/worldgen/WorldGenRubberTree.class",
            ],
            f"enabled={not values['disableRubberTreeGeneration']}, scale={values['rubberTreeRateIncrease']}",
        ),
        effect(
            "bedrock_fluid_cells",
            "virtual_resource_map",
            "active" if fluid_count else "inactive",
            False,
            "lazy query/use with WorldSavedData persistence",
            "deterministic 8x8-chunk cells; no underground fluid blocks are placed",
            "fluid JSON: weight, biome/dimension filters, yield and depletion",
            "cell definition, yield, operations remaining, save-data version and consumers",
            "checked fluid-definition overlay; restart/fresh save until reload hazards are patched",
            [
                "gregtech/api/worldgen/bedrockFluids/BedrockFluidVeinHandler.class",
                "gregtech/api/worldgen/bedrockFluids/BedrockFluidVeinSaveData.class",
                "gregtech/api/worldgen/config/BedrockFluidDepositDefinition.class",
            ],
            f"{fluid_count} configured bedrock-fluid definitions",
        ),
        effect(
            "chunk_height_capability",
            "persistence_support",
            "active",
            False,
            "chunk capability attach/read/write during ore grid construction",
            "stores the chosen grid terrain height and top-solid-or-liquid height",
            "not directly configurable",
            "chunk capability NBT H/BH and grid height decisions",
            "instrument or patch version-specific capability code; do not treat it as a public config seam",
            ["gregtech/api/worldgen/generator/GTWorldGenCapability.class"],
            "registered by the exact runtime",
        ),
        effect(
            "structure_loot_mutation",
            "structure_content",
            "active_target_reachability_unresolved"
            if values["addLoot"] or values["increaseDungeonLoot"]
            else "inactive",
            False,
            "LootTableLoadEvent",
            "ten vanilla chest loot tables; changes contents/rolls, not structure geometry",
            "addLoot and increaseDungeonLoot",
            "loaded table names, main-pool mutations, and actual generated container loot",
            "global config or separate loot-table policy; keep independent from structure placement",
            [
                "gregtech/loaders/dungeon/DungeonLootLoader.class",
                "gregtech/loaders/dungeon/ChestGenHooks.class",
            ],
            f"addLoot={values['addLoot']}, increaseDungeonLoot={values['increaseDungeonLoot']}",
        ),
        effect(
            "definition_extraction_and_reload",
            "configuration_lifecycle",
            "active",
            False,
            "initialization and /gregtech worldgen reload",
            "config/gregtech definition tree and extraction-version locks",
            "JSON files, dimensions.json and worldgen_extracted.json",
            "loaded counts, parse failures, extracted-file writes and cache identities",
            "Workbench materializes a fresh overlay; deployment/restart remains explicit",
            [
                "gregtech/api/worldgen/config/WorldGenRegistry.class",
                "gregtech/common/command/worldgen/CommandWorldgenReload.class",
            ],
            "registry initialization is unconditional",
        ),
        effect(
            "galacticraft_generator_allowlist",
            "conditional_compatibility",
            "active" if galacticraft_present else "inactive_missing_mod",
            False,
            "GTCEu initialization reflection when Galacticraft Core is loaded",
            "adds the GTCEu IWorldGenerator to Galacticraft's other-mod generator allowlist",
            "mod presence only",
            "successful reflection or fatal log; generator calls in Galacticraft dimensions",
            "version-specific compatibility adapter only if Galacticraft enters the target pack",
            ["gregtech/api/worldgen/config/WorldGenRegistry.class"],
            f"galacticraft_present={galacticraft_present}",
        ),
        effect(
            "dimension_names_for_resource_ui",
            "presentation_only",
            "active",
            False,
            "definition registry initialization and JEI rendering",
            "dimension ID to label map; creates no dimensions and changes no terrain",
            "dimensions.json",
            "JEI resource-deposit dimension labels",
            "edit labels separately from generator/dimension registration",
            ["gregtech/api/worldgen/config/WorldGenRegistry.class"],
            "dimensions.json is present in the bound V1 inventory",
        ),
    ]
    return rows


def _reload_and_persistence(controls: Mapping[str, Any]) -> dict[str, Any]:
    weight_random_paths = controls["component_usage"][
        "nested_filler_definition_paths"
    ].get("weight_random", [])
    return {
        "command": {
            "name": "/gregtech worldgen reload",
            "direct_call": "WorldGenRegistry.INSTANCE.reinitializeRegisteredVeins()",
            "calls_bedrock_fluid_recalculate_chances": False,
            "safe_as_general_hot_reload": False,
        },
        "state_matrix": [
            {
                "state": "WorldGenRegistry.registeredVeinDefinitions",
                "reload_action": "cleared and rebuilt",
                "consequence": "new uncached ore grids can see new definitions",
            },
            {
                "state": "WorldGenRegistry.oreVeinCache",
                "reload_action": "cleared",
                "consequence": "dimension/biome weighted ore lists are rebuilt on next use",
            },
            {
                "state": "CachedGridEntry.gridEntryCache",
                "reload_action": "not cleared by registry reload",
                "consequence": "existing grid entries can retain old selections until eviction; cache expires five minutes after access and caps at 300 per world",
            },
            {
                "state": "WorldGenRegistry.registeredBedrockVeinDefinitions",
                "reload_action": "cleared and rebuilt",
                "consequence": "the public registry list changes",
            },
            {
                "state": "BedrockFluidVeinHandler.veinList",
                "reload_action": "not cleared before rebuilt definitions call addFluidDeposit",
                "consequence": "source control flow permits old and new definition objects to coexist after reload",
            },
            {
                "state": "BedrockFluidVeinHandler.totalWeightMap",
                "reload_action": "not cleared by the reload command",
                "consequence": "previous dimension/biome totals can remain cached",
            },
            {
                "state": "BedrockFluidVeinHandler.veinCache",
                "reload_action": "not cleared by the reload command",
                "consequence": "already queried 8x8 cells retain their selected definition, yield and operations",
            },
            {
                "state": "gregtech.bedrockFluidVeinData",
                "reload_action": "persistent WorldSavedData remains",
                "consequence": "cell identity, yield and depletion survive restart by deposit name",
            },
        ],
        "source_derived_hazards": [
            {
                "hazard_id": "fluid_reload_incomplete_invalidation",
                "severity": "high",
                "runtime_probe_status": "not_run",
                "finding": "the command rebuilds fluid definitions without clearing veinList, totalWeightMap or veinCache",
                "workbench_policy": "use a restart and fresh disposable world/save for fluid-map experiments until an exact patch and probe exist",
            },
            {
                "hazard_id": "ore_grid_reload_lag",
                "severity": "medium",
                "runtime_probe_status": "not_run",
                "finding": "registry biome caches clear, but CachedGridEntry objects do not",
                "workbench_policy": "measure only fresh, previously unqueried grid cells after reload; prefer restart for controlled comparisons",
            },
            {
                "hazard_id": "capability_bottom_height_deserialization",
                "severity": "high",
                "runtime_probe_status": "not_run",
                "finding": "GTWorldGenCapability.readFromNBT assigns maxBottomHeight=maxHeight in both BH branches and never reads BH",
                "workbench_policy": "instrument chunk capability read/write and compare fresh generation with reload-after-save before relying on persisted height limits",
            },
            {
                "hazard_id": "definition_file_null_breaks_remaining_scan",
                "severity": "medium",
                "runtime_probe_status": "not_run",
                "finding": "a null JSON extraction breaks, rather than continues, each definition-file loop",
                "workbench_policy": "validate every materialized definition before deployment and retain loaded-definition counts",
            },
            {
                "hazard_id": "weighted_filler_shared_rng",
                "severity": "medium" if weight_random_paths else "inactive",
                "runtime_probe_status": "not_run",
                "finding": "weight_random filler selection uses GTUtility's shared RNG rather than the per-grid Random",
                "active_definition_paths": list(weight_random_paths),
                "workbench_policy": "include order/repeat probes when a selected definition uses weight_random",
            },
        ],
    }


def _scan_tokens(data: bytes) -> list[str]:
    return sorted(
        token_id
        for token_id, spellings in INTEGRATION_TOKENS.items()
        if any(spelling in data for spelling in spellings)
    )


def _runtime_mod_bindings(mods_root: Path) -> tuple[list[dict[str, Any]], set[str]]:
    bindings = []
    names: set[str] = set()
    for path in sorted(mods_root.glob("*.jar"), key=lambda value: value.name.lower()):
        names.add(path.name)
        bindings.append(_binding(path, f"mods/{path.name}"))
    _require(bindings, f"runtime has no mod jars: {mods_root}")
    return bindings, names


def _scan_runtime_integrations(runtime_root: Path, subject_jar: Path) -> tuple[dict[str, Any], set[str]]:
    mods_root = runtime_root / "mods"
    _require(mods_root.is_dir(), f"runtime lacks mods directory: {mods_root}")
    bindings, names = _runtime_mod_bindings(mods_root)
    candidates = []
    subject = subject_jar.resolve(strict=True)
    allowed_suffixes = (".class", ".json", ".cfg", ".properties", ".mf", ".groovy")
    for path in sorted(mods_root.glob("*.jar"), key=lambda value: value.name.lower()):
        if path.resolve(strict=True) == subject:
            continue
        matched_entries = []
        mixin_configs: list[str] = []
        shipped_definitions: list[str] = []
        try:
            with ZipFile(path) as archive:
                infos = sorted(archive.infolist(), key=lambda info: info.filename)
                for info in infos:
                    lower = info.filename.lower()
                    basename = lower.rsplit("/", 1)[-1]
                    if basename.startswith("mixins") and lower.endswith(".json"):
                        mixin_configs.append(info.filename)
                    if re.search(r"^assets/[^/]+/worldgen/(?:vein|fluid)/.+\.json$", lower):
                        shipped_definitions.append(info.filename)
                    if info.is_dir() or not lower.endswith(allowed_suffixes):
                        continue
                    _require(
                        info.file_size <= 32 * 1024 * 1024,
                        f"runtime scan entry exceeds 32 MiB: {path.name}!/{info.filename}",
                    )
                    data = archive.read(info)
                    tokens = _scan_tokens(data)
                    if tokens:
                        matched_entries.append(
                            {
                                "entry": info.filename,
                                "matched_tokens": tokens,
                            }
                        )
        except BadZipFile as exc:
            raise GtceuWorldgenImpactValidationError(
                f"invalid runtime mod jar {path.name}: {exc}"
            ) from exc
        if matched_entries or shipped_definitions:
            candidates.append(
                {
                    "artifact": _binding(path, f"mods/{path.name}"),
                    "matched_entries": matched_entries,
                    "mixin_configuration_entries": sorted(mixin_configs),
                    "shipped_worldgen_definition_entries": sorted(shipped_definitions),
                    "classification": "integration_candidate_requires_semantic_review",
                }
            )
    return (
        {
            "runtime_mod_tree": _tree_binding(bindings),
            "candidate_count": len(candidates),
            "candidates": candidates,
            "method": "bounded class/resource constant-token scan",
            "limitations": [
                "a token match does not prove a call, mutation, mixin application or runtime reachability",
                "reflection, encryption, generated bytecode and semantically indirect integrations can evade this scan",
                "candidate behavior requires exact bytecode/source review and runtime observation",
            ],
        },
        names,
    )


def _scan_groovy(runtime_root: Path) -> dict[str, Any] | None:
    root = runtime_root / "groovy"
    if not root.is_dir():
        return None
    bindings = []
    matches = []
    for path in sorted(root.rglob("*.groovy")):
        relative = path.relative_to(runtime_root).as_posix()
        data = _stable_bytes(path)
        binding = {
            "relative_path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        bindings.append(binding)
        text = data.decode("utf-8", errors="replace")
        tokens = sorted(token for token in DIRECT_WORLDGEN_SCRIPT_TOKENS if token in text)
        if tokens:
            matches.append({"relative_path": relative, "matched_tokens": tokens})
    tree = _tree_binding(bindings)
    return {
        "tree_sha256": tree["tree_sha256"],
        "file_count": tree["file_count"],
        "direct_worldgen_api_match_count": len(matches),
        "matches": matches,
        "method": "literal direct-API token scan",
    }


def _optional_cfg_context(runtime_root: Path, name: str, keys: tuple[str, ...]) -> dict[str, Any] | None:
    path = runtime_root / "config" / name
    if not path.is_file():
        return None
    text = _stable_bytes(path).decode("utf-8", errors="replace")
    assignments: dict[str, Any] = {}
    assignment = re.compile(r"^\s*[BID]:([^=]+)=(.*?)\s*$")
    for line in text.splitlines():
        match = assignment.match(line)
        if not match:
            continue
        key = match.group(1).strip().strip('"')
        if key not in keys:
            continue
        raw = match.group(2).strip()
        assignments[key] = (
            raw.lower() == "true"
            if raw.lower() in {"true", "false"}
            else int(raw)
            if re.fullmatch(r"-?\d+", raw)
            else raw
        )
    return {
        "artifact": _binding(path, f"config/{name}"),
        "selected_values": dict(sorted(assignments.items())),
    }


def _debug_log_context(runtime_root: Path) -> dict[str, Any] | None:
    path = runtime_root / "logs/debug.log"
    if not path.is_file():
        return None
    text = _stable_bytes(path).decode("utf-8", errors="replace")
    mixin_pattern = re.compile(
        r"Mixing ([A-Za-z0-9_$]+) from ([^ ]+) into "
        r"(gregtech\.(?:api\.worldgen|api\.GTValues)[A-Za-z0-9_.$]*)"
    )
    mixins = sorted(
        {
            (match.group(1), match.group(2), match.group(3))
            for match in mixin_pattern.finditer(text)
        }
    )
    load_pattern = re.compile(
        r"GregTech\]: ((?:Initializing|Reloading|Loaded|Registering dungeon loot)[^\r\n]*)"
    )
    messages = sorted({match.group(1) for match in load_pattern.finditer(text)})
    return {
        "artifact": _binding(path, "logs/debug.log"),
        "gtceu_worldgen_mixin_applications": [
            {"mixin_class": mixin, "configuration": config, "target_class": target}
            for mixin, config, target in mixins
        ],
        "gtceu_worldgen_load_messages": messages,
        "scope": "one exact retained launch log; not a feature-behavior probe",
    }


def _pack_context(runtime_root: Path | None) -> dict[str, Any] | None:
    if runtime_root is None:
        return None
    return {
        "groovyscript_direct_worldgen_api_scan": _scan_groovy(runtime_root),
        "no_worldgen5you": _optional_cfg_context(
            runtime_root,
            "noworldgen5you.cfg",
            (
                "disable_dungeon",
                "disable_mineshaft",
                "disable_scattered_feature",
                "disable_stronghold",
                "disable_village",
                "disable_dessert_pyramid",
                "disable_jungle_temple",
            ),
        ),
        "visualores": _optional_cfg_context(
            runtime_root,
            "visualores.cfg",
            (
                "cullEmptyChunks",
                "cullEmptyChunksRetrogen",
                "doRetrogen",
                "forceRetrogenV1",
                "oreBlockProspectRange",
                "surfaceRockProspectRange",
            ),
        ),
        "sussypatches": _optional_cfg_context(
            runtime_root,
            "sussypatches.cfg",
            (
                "Make surface populators populate the whole chunk",
                "Use XoShiRo256++ Random",
                "Improve ore vein info page in JEI",
            ),
        ),
        "retained_debug_log": _debug_log_context(runtime_root),
    }


def _source_audit() -> dict[str, Any]:
    return {
        "upstream_tag": SUPPORTED_SOURCE_TAG,
        "upstream_commit": SUPPORTED_SOURCE_COMMIT,
        "upstream_url": "https://github.com/GregTechCEu/GregTech/tree/v2.8.10",
        "method": "exact tagged-source control-flow audit cross-checked against bound runtime class entries",
        "audited_effect_packages": [
            "gregtech.api.worldgen",
            "gregtech.common.worldgen",
            "gregtech.loaders.dungeon",
            "gregtech.common.command.worldgen",
            "gregtech.core.CoreModule",
        ],
        "direct_generator_search_result": {
            "i_world_generator_implementations": [
                "gregtech.api.worldgen.generator.WorldGeneratorImpl"
            ],
            "ore_gen_bus_listeners": [
                "gregtech.api.worldgen.generator.WorldGeneratorImpl.onOreGenerate"
            ],
            "loot_table_listeners": [
                "gregtech.loaders.dungeon.ChestGenHooks.onWorldLoad"
            ],
            "separate_bedrock_fluid_block_generator_found": False,
        },
    }


def build_gtceu_worldgen_impact_inventory(
    *,
    jar_path: Path,
    config_root: Path,
    runtime_root: Path | None = None,
    strataview_path: Path | None = None,
) -> dict[str, Any]:
    jar = jar_path.expanduser().resolve(strict=True)
    config = config_root.expanduser().resolve(strict=True)
    _require(config.is_dir(), f"GTCEu config root is not a directory: {config}")
    runtime = runtime_root.expanduser().resolve(strict=True) if runtime_root else None
    if runtime is not None:
        _require(runtime.is_dir(), f"runtime root is not a directory: {runtime}")
    definition_inventory = build_gtceu_worldgen_inventory(
        jar_path=jar,
        config_root=config,
        strataview_path=strataview_path,
    )
    runtime_controls = _parse_forge_controls(config / "gregtech.cfg")
    definition_controls = _definition_controls(config)
    if runtime is not None:
        integration_scan, runtime_mod_names = _scan_runtime_integrations(runtime, jar)
    else:
        integration_scan = None
        runtime_mod_names = {jar.name}
    effect_catalog = _effect_catalog(
        definition_controls,
        runtime_controls,
        runtime_mod_names,
    )
    status_counts = Counter(row["status"] for row in effect_catalog)
    report: dict[str, Any] = {
        "format": GTCEU_WORLDGEN_IMPACT_FORMAT,
        "schema_version": 2,
        "inventory_id": "",
        "canonicalization_id": CANONICALIZATION_ID,
        "adapter_profile": {
            "id": "gtceu-1.12.2-2.8.10-worldgen-impact-v2",
            "minecraft_version": "1.12.2",
            "gregtech_version": SUPPORTED_MOD_VERSION,
            "source_tag": SUPPORTED_SOURCE_TAG,
            "source_commit": SUPPORTED_SOURCE_COMMIT,
        },
        "definition_inventory": definition_inventory,
        "impact_class_surface": _impact_class_surface(jar),
        "runtime_controls": runtime_controls,
        "definition_controls": definition_controls,
        "api_surface": _api_surface(definition_controls),
        "effect_catalog": effect_catalog,
        "reload_and_persistence": _reload_and_persistence(definition_controls),
        "runtime_integration_scan": integration_scan,
        "pack_context": _pack_context(runtime),
        "source_audit": _source_audit(),
        "summary": {
            "effect_count": len(effect_catalog),
            "effect_statuses": _counter_rows(status_counts),
            "ore_definition_count": len(definition_controls["ore_definitions"]),
            "bedrock_fluid_definition_count": len(
                definition_controls["bedrock_fluid_definitions"]
            ),
            "active_weight_random_definition_count": len(
                definition_controls["component_usage"][
                    "nested_filler_definition_paths"
                ].get("weight_random", [])
            ),
            "runtime_integration_candidate_count": integration_scan[
                "candidate_count"
            ]
            if integration_scan
            else None,
            "source_derived_hazard_count": len(
                _reload_and_persistence(definition_controls)[
                    "source_derived_hazards"
                ]
            ),
        },
        "boundaries": dict(_BOUNDARIES),
    }
    identity = deepcopy(report)
    identity["inventory_id"] = ""
    report["inventory_id"] = GTCEU_WORLDGEN_IMPACT_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    return parse_gtceu_worldgen_impact_inventory(report)


def parse_gtceu_worldgen_impact_inventory(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "impact inventory must be an object")
    report = deepcopy(dict(value))
    actual = set(report)
    _require(
        actual == _TOP_LEVEL_KEYS,
        f"impact inventory fields mismatch: missing={sorted(_TOP_LEVEL_KEYS - actual)!r}, unknown={sorted(actual - _TOP_LEVEL_KEYS)!r}",
    )
    _require(
        report.get("format") == GTCEU_WORLDGEN_IMPACT_FORMAT,
        "impact inventory format drift",
    )
    _require(report.get("schema_version") == 2, "impact schema version drift")
    _require(
        report.get("canonicalization_id") == CANONICALIZATION_ID,
        "impact canonicalization drift",
    )
    _require(report.get("boundaries") == _BOUNDARIES, "impact boundaries drift")
    parse_gtceu_worldgen_inventory(report["definition_inventory"])
    effects = report.get("effect_catalog")
    _require(isinstance(effects, list) and effects, "impact effect catalog is empty")
    effect_ids = [row.get("effect_id") for row in effects if isinstance(row, Mapping)]
    _require(len(effect_ids) == len(effects), "impact effect row is not an object")
    _require(len(set(effect_ids)) == len(effect_ids), "impact effect IDs are not unique")
    _require(
        report["summary"].get("effect_count") == len(effects),
        "impact effect count drift",
    )
    inventory_id = report.get("inventory_id")
    _require(
        isinstance(inventory_id, str)
        and inventory_id.startswith(GTCEU_WORLDGEN_IMPACT_PREFIX),
        "invalid GTCEu impact inventory ID",
    )
    identity = deepcopy(report)
    identity["inventory_id"] = ""
    expected = GTCEU_WORLDGEN_IMPACT_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    _require(inventory_id == expected, "GTCEu impact inventory ID drift")
    return report


def write_gtceu_worldgen_impact_inventory(
    path: Path, report: Mapping[str, Any]
) -> None:
    validated = parse_gtceu_worldgen_impact_inventory(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
