"""Inventory exact GTCEu 2.8.10 worldgen definitions and observed resource states."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping
from zipfile import BadZipFile, ZipFile


GTCEU_WORLDGEN_FORMAT = "workbench-crucible-gtceu-worldgen-inventory-v1"
GTCEU_WORLDGEN_PREFIX = "crucible-gtceu-worldgen:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
SUPPORTED_MOD_VERSION = "2.8.10-beta"
SUPPORTED_MC_VERSION = "1.12.2"

REQUIRED_API_CLASSES = (
    "gregtech/api/worldgen/config/WorldGenRegistry.class",
    "gregtech/api/worldgen/config/OreDepositDefinition.class",
    "gregtech/api/worldgen/config/BedrockFluidDepositDefinition.class",
    "gregtech/api/worldgen/generator/WorldGeneratorImpl.class",
    "gregtech/api/worldgen/generator/CachedGridEntry.class",
    "gregtech/api/worldgen/bedrockFluids/BedrockFluidVeinHandler.class",
)

PUBLIC_SEAMS = (
    {
        "owner": "gregtech.api.worldgen.config.WorldGenRegistry",
        "member": "initializeRegistry()",
        "role": "registers the Forge IWorldGenerator, ore-generation listener, built-in component factories, and initial definitions",
    },
    {
        "owner": "gregtech.api.worldgen.config.WorldGenRegistry",
        "member": "reinitializeRegisteredVeins()",
        "role": "reloads JSON definitions, clears ore caches, and rebuilds registered ore and fluid definitions",
    },
    {
        "owner": "gregtech.api.worldgen.config.WorldGenRegistry",
        "member": "addVeinDefinitions(OreDepositDefinition)",
        "role": "registers an addon ore definition before initialization or a later reinitialization",
    },
    {
        "owner": "gregtech.api.worldgen.config.WorldGenRegistry",
        "member": "addVeinDefinitions(BedrockFluidDepositDefinition)",
        "role": "registers an addon bedrock-fluid definition",
    },
    {
        "owner": "gregtech.api.worldgen.config.WorldGenRegistry",
        "member": "removeVeinDefinitions(IWorldgenDefinition)",
        "role": "marks a registered ore or fluid definition for removal on reinitialization",
    },
    {
        "owner": "gregtech.api.worldgen.config.WorldGenRegistry",
        "member": "registerShapeGenerator/registerBlockFiller/registerVeinPopulator",
        "role": "adds custom JSON component factories before definitions using them are initialized",
    },
    {
        "owner": "gregtech.api.worldgen.bedrockFluids.BedrockFluidVeinHandler",
        "member": "getFluidVeinWorldEntry(World,int,int)",
        "role": "observes the deterministic cached 8x8-chunk bedrock-fluid cell selected for a world position",
    },
)

ORE_STATE_RE = re.compile(r"^gregtech:ore_(.+)_\d+(?:\[|$)")
VARIANT_RE = re.compile(r"\bvariant=([^,\]]+)")


class GtceuWorldgenValidationError(ValueError):
    """Raised when an exact GTCEu input cannot support a closed inventory."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GtceuWorldgenValidationError(message)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GtceuWorldgenValidationError(
            f"cannot canonically encode GTCEu inventory material: {exc}"
        ) from exc


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


def _json_file(path: Path, context: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(_stable_bytes(path).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GtceuWorldgenValidationError(f"cannot parse {context}: {exc}") from exc
    _require(isinstance(payload, Mapping), f"{context} must be a JSON object")
    return payload


def _number(value: Any, context: str) -> int | float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{context} must be numeric",
    )
    return value


def _integer(value: Any, context: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{context} must be an integer",
    )
    return value


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be text")
    return value


def _component_type(payload: Mapping[str, Any], key: str, context: str) -> str | None:
    component = payload.get(key)
    if component is None:
        return None
    _require(isinstance(component, Mapping), f"{context}.{key} must be an object")
    return _text(component.get("type"), f"{context}.{key}.type")


def _walk_values(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _material_tokens(payload: Mapping[str, Any]) -> list[str]:
    tokens: set[str] = set()
    for value in _walk_values(payload):
        if isinstance(value, str) and value.startswith("ore:") and len(value) > 4:
            tokens.add(value[4:])
    for value in _walk_values(payload):
        if not isinstance(value, Mapping):
            continue
        material = value.get("material")
        if isinstance(material, str) and material:
            tokens.add(material)
        block = value.get("block")
        variant = value.get("variant")
        if isinstance(block, str) and isinstance(variant, str) and variant:
            tokens.add(variant)
    return sorted(tokens)


def _dimension_ids(value: Any) -> list[int]:
    dimensions: set[int] = set()
    for item in _walk_values(value):
        if not isinstance(item, str) or not item.startswith("dimension_id:"):
            continue
        suffix = item.partition(":")[2]
        if suffix.lstrip("-").isdigit():
            dimensions.add(int(suffix))
    return sorted(dimensions)


def _normalize_ore(relative_path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    context = relative_path
    weight = _integer(payload.get("weight"), f"{context}.weight")
    density = _number(payload.get("density"), f"{context}.density")
    min_height = _integer(payload.get("min_height"), f"{context}.min_height")
    max_height = _integer(payload.get("max_height"), f"{context}.max_height")
    _require(min_height <= max_height, f"{context} has inverted height limits")
    generator_type = _component_type(payload, "generator", context)
    filler_type = _component_type(payload, "filler", context)
    _require(generator_type is not None, f"{context} lacks generator")
    _require(filler_type is not None, f"{context} lacks filler")
    populator_type = _component_type(payload, "vein_populator", context)
    return {
        "kind": "ore",
        "relative_path": relative_path,
        "weight": weight,
        "enabled_by_base_weight": weight > 0,
        "density": density,
        "min_height": min_height,
        "max_height": max_height,
        "count_as_vein": payload.get("count_as_vein", True),
        "generator_type": generator_type,
        "filler_type": filler_type,
        "populator_type": populator_type,
        "dimension_ids": _dimension_ids(payload.get("dimension_filter")),
        "has_dimension_filter": "dimension_filter" in payload,
        "has_biome_modifier": "biome_modifier" in payload,
        "material_tokens": _material_tokens(payload),
    }


def _normalize_fluid(relative_path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    context = relative_path
    weight = _integer(payload.get("weight"), f"{context}.weight")
    yields = payload.get("yield")
    depletion = payload.get("depletion")
    _require(isinstance(yields, Mapping), f"{context}.yield must be an object")
    _require(isinstance(depletion, Mapping), f"{context}.depletion must be an object")
    minimum_yield = _integer(yields.get("min"), f"{context}.yield.min")
    maximum_yield = _integer(yields.get("max"), f"{context}.yield.max")
    _require(minimum_yield <= maximum_yield, f"{context} has inverted yields")
    depletion_chance = _integer(
        depletion.get("chance"), f"{context}.depletion.chance"
    )
    return {
        "kind": "fluid",
        "relative_path": relative_path,
        "weight": weight,
        "enabled_by_base_weight": weight > 0,
        "fluid": _text(payload.get("fluid"), f"{context}.fluid"),
        "minimum_yield": minimum_yield,
        "maximum_yield": maximum_yield,
        "depletion_amount": _integer(
            depletion.get("amount"), f"{context}.depletion.amount"
        ),
        "depletion_chance_configured": depletion_chance,
        "depletion_chance_effective": max(0, min(100, depletion_chance)),
        "depleted_yield": _integer(
            depletion.get("depleted_yield", 0),
            f"{context}.depletion.depleted_yield",
        ),
        "dimension_ids": _dimension_ids(payload.get("dimension_filter")),
        "has_dimension_filter": "dimension_filter" in payload,
        "has_biome_modifier": "biome_modifier" in payload,
        "material_tokens": [],
    }


def normalize_definition(
    kind: str, relative_path: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if kind == "ore":
        return _normalize_ore(relative_path, payload)
    if kind == "fluid":
        return _normalize_fluid(relative_path, payload)
    raise GtceuWorldgenValidationError(f"unsupported definition kind: {kind}")


def _jar_binding(jar_path: Path) -> dict[str, Any]:
    binding = _binding(jar_path)
    try:
        with ZipFile(jar_path) as archive:
            names = set(archive.namelist())
            missing = sorted(set(REQUIRED_API_CLASSES) - names)
            _require(not missing, f"GTCEu jar lacks required worldgen APIs: {missing}")
            try:
                mod_metadata = json.loads(archive.read("mcmod.info").decode("utf-8"))
            except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
                raise GtceuWorldgenValidationError(
                    f"cannot read GTCEu mcmod.info: {exc}"
                ) from exc
            _require(isinstance(mod_metadata, list), "GTCEu mcmod.info must be a list")
            gregtech = next(
                (
                    row
                    for row in mod_metadata
                    if isinstance(row, Mapping) and row.get("modid") == "gregtech"
                ),
                None,
            )
            _require(gregtech is not None, "jar mcmod.info lacks gregtech metadata")
            version = _text(gregtech.get("version"), "gregtech version")
            mc_version = _text(gregtech.get("mcversion"), "gregtech mcversion")
            _require(
                version == SUPPORTED_MOD_VERSION,
                f"expected GTCEu {SUPPORTED_MOD_VERSION}, got {version}",
            )
            _require(
                mc_version == SUPPORTED_MC_VERSION,
                f"expected Minecraft {SUPPORTED_MC_VERSION}, got {mc_version}",
            )
            api_classes = []
            for name in REQUIRED_API_CLASSES:
                data = archive.read(name)
                api_classes.append(
                    {
                        "entry": name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                )
    except BadZipFile as exc:
        raise GtceuWorldgenValidationError(f"invalid GTCEu jar: {exc}") from exc
    return {
        **binding,
        "mod_id": "gregtech",
        "mod_version": version,
        "minecraft_version": mc_version,
        "required_api_classes": api_classes,
    }


def _definition_files(config_root: Path) -> list[tuple[str, str, Path]]:
    rows: list[tuple[str, str, Path]] = []
    for kind, folder in (
        ("fluid", config_root / "worldgen/fluid"),
        ("ore", config_root / "worldgen/vein"),
    ):
        _require(folder.is_dir(), f"missing GTCEu {kind} definition directory: {folder}")
        for path in sorted(folder.rglob("*.json")):
            relative = path.relative_to(config_root).as_posix()
            rows.append((kind, relative, path))
    _require(rows, "GTCEu worldgen configuration has no definitions")
    return sorted(rows, key=lambda row: row[1])


def _config_inventory(config_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    definitions: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for kind, relative, path in _definition_files(config_root):
        payload = _json_file(path, relative)
        binding = _binding(path, relative)
        bindings.append(binding)
        definitions.append(
            {
                **normalize_definition(kind, relative, payload),
                "source_sha256": binding["sha256"],
                "source_size_bytes": binding["size_bytes"],
            }
        )

    for relative in ("dimensions.json", "worldgen_extracted.json"):
        path = config_root / relative
        if path.is_file():
            _json_file(path, relative)
            bindings.append(_binding(path, relative))
    bindings.sort(key=lambda row: row["relative_path"])
    tree_material = "".join(
        f"{row['relative_path']}\0{row['sha256']}\0{row['size_bytes']}\n"
        for row in bindings
    ).encode("utf-8")
    return (
        {
            "tree_sha256": hashlib.sha256(tree_material).hexdigest(),
            "file_count": len(bindings),
            "files": bindings,
        },
        definitions,
    )


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counter.items(), key=lambda row: (-row[1], row[0]))
    ]


def _summary(definitions: list[dict[str, Any]]) -> dict[str, Any]:
    ore = [row for row in definitions if row["kind"] == "ore"]
    fluid = [row for row in definitions if row["kind"] == "fluid"]
    materials = sorted({token for row in ore for token in row["material_tokens"]})
    dimensions = Counter(
        str(dimension)
        for row in definitions
        for dimension in row["dimension_ids"]
    )
    heights = [value for row in ore for value in (row["min_height"], row["max_height"])]
    densities = [float(row["density"]) for row in ore]
    return {
        "definition_count": len(definitions),
        "ore_definition_count": len(ore),
        "fluid_definition_count": len(fluid),
        "positive_weight_definition_count": sum(
            1 for row in definitions if row["enabled_by_base_weight"]
        ),
        "nonpositive_weight_definition_count": sum(
            1 for row in definitions if not row["enabled_by_base_weight"]
        ),
        "positive_ore_weight_total": sum(max(0, row["weight"]) for row in ore),
        "positive_fluid_weight_total": sum(max(0, row["weight"]) for row in fluid),
        "ore_height_extent": {
            "minimum": min(heights) if heights else None,
            "maximum": max(heights) if heights else None,
        },
        "ore_density": {
            "minimum": min(densities) if densities else None,
            "maximum": max(densities) if densities else None,
            "mean": sum(densities) / len(densities) if densities else None,
        },
        "generator_types": _counter_rows(Counter(row["generator_type"] for row in ore)),
        "filler_types": _counter_rows(Counter(row["filler_type"] for row in ore)),
        "populator_types": _counter_rows(
            Counter(row["populator_type"] or "none" for row in ore)
        ),
        "explicit_dimension_ids": _counter_rows(dimensions),
        "material_token_count": len(materials),
        "material_tokens": materials,
    }


def _observed_counts(package: Mapping[str, Any]) -> Counter[str]:
    rows = (package.get("resourceStats") or {}).get("blockStateCounts") or []
    if not rows:
        rows = package.get("blockCounts") or []
    counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        block_state = row.get("blockState")
        count = row.get("count")
        if isinstance(block_state, str) and isinstance(count, int) and count > 0:
            if ORE_STATE_RE.match(block_state) or block_state.startswith(
                "gregtech:meta_block_surface_rock_"
            ):
                counts[block_state] += count
    return counts


def _observed_material(block_state: str) -> str | None:
    match = ORE_STATE_RE.match(block_state)
    if match:
        return match.group(1)
    if block_state.startswith("gregtech:meta_block_surface_rock_"):
        variant = VARIANT_RE.search(block_state)
        if variant:
            return variant.group(1).split("__", 1)[-1]
    return None


def _observation(
    package_path: Path, definitions: list[dict[str, Any]]
) -> dict[str, Any]:
    package = _json_file(package_path, "Strataview observation package")
    _require(
        package.get("schema") == "strata.strataview.package.v1",
        "GTCEu observation requires a Strataview package V1",
    )
    material_candidates: dict[str, list[str]] = {}
    for definition in definitions:
        for material in definition["material_tokens"]:
            material_candidates.setdefault(material, []).append(
                definition["relative_path"]
            )
    rows = []
    for block_state, count in _observed_counts(package).most_common():
        material = _observed_material(block_state)
        rows.append(
            {
                "block_state": block_state,
                "count": count,
                "material_token": material,
                "candidate_definition_paths": sorted(
                    material_candidates.get(material or "", [])
                ),
            }
        )
    return {
        "artifact": _binding(package_path),
        "schema": package.get("schema"),
        "chunk_window": package.get("chunkWindow"),
        "relation": "material-token-candidate-not-causal-attribution",
        "observed_gtceu_state_count": len(rows),
        "observed_gtceu_block_count": sum(row["count"] for row in rows),
        "states": rows,
    }


def build_gtceu_worldgen_inventory(
    *,
    jar_path: Path,
    config_root: Path,
    strataview_path: Path | None = None,
) -> dict[str, Any]:
    jar = jar_path.expanduser().resolve(strict=True)
    config = config_root.expanduser().resolve(strict=True)
    _require(config.is_dir(), f"GTCEu config root is not a directory: {config}")
    config_binding, definitions = _config_inventory(config)
    report: dict[str, Any] = {
        "format": GTCEU_WORLDGEN_FORMAT,
        "schema_version": 1,
        "inventory_id": "",
        "canonicalization_id": CANONICALIZATION_ID,
        "adapter_profile": {
            "id": "gtceu-1.12.2-2.8.10-worldgen-v1",
            "grid_size_chunks": {"ore_x": 3, "ore_z": 3, "bedrock_fluid": 8},
            "public_seams": list(PUBLIC_SEAMS),
        },
        "artifact": _jar_binding(jar),
        "configuration": config_binding,
        "definitions": definitions,
        "summary": _summary(definitions),
        "observation": _observation(strataview_path, definitions)
        if strataview_path
        else None,
        "boundaries": {
            "configuration_was_modified": False,
            "definitions_are_exactly_bound": True,
            "gregtech_version_specific": True,
            "observed_state_proves_deposit_cause": False,
            "recurrent_complex_integration_added": False,
            "strata_geometry_is_authority": False,
        },
    }
    identity = deepcopy(report)
    identity["inventory_id"] = ""
    report["inventory_id"] = GTCEU_WORLDGEN_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    return parse_gtceu_worldgen_inventory(report)


def parse_gtceu_worldgen_inventory(value: Mapping[str, Any]) -> dict[str, Any]:
    report = deepcopy(dict(value))
    _require(report.get("format") == GTCEU_WORLDGEN_FORMAT, "inventory format drift")
    _require(report.get("schema_version") == 1, "inventory schema version drift")
    _require(
        report.get("canonicalization_id") == CANONICALIZATION_ID,
        "inventory canonicalization drift",
    )
    inventory_id = report.get("inventory_id")
    _require(
        isinstance(inventory_id, str) and inventory_id.startswith(GTCEU_WORLDGEN_PREFIX),
        "invalid GTCEu inventory ID",
    )
    identity = deepcopy(report)
    identity["inventory_id"] = ""
    expected = GTCEU_WORLDGEN_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    _require(inventory_id == expected, "GTCEu inventory ID drift")
    boundaries = report.get("boundaries")
    _require(
        boundaries
        == {
            "configuration_was_modified": False,
            "definitions_are_exactly_bound": True,
            "gregtech_version_specific": True,
            "observed_state_proves_deposit_cause": False,
            "recurrent_complex_integration_added": False,
            "strata_geometry_is_authority": False,
        },
        "GTCEu inventory boundaries drift",
    )
    return report


def write_gtceu_worldgen_inventory(path: Path, report: Mapping[str, Any]) -> None:
    validated = parse_gtceu_worldgen_inventory(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
