"""Bounded validation and coordinate access for exact Strata V1/V2 regions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .model import (
    FileBinding,
    SubsurfaceStudioError,
    load_json_file,
    normalize_window,
    require,
    window_contains_chunk,
)


MANIFEST_SCHEMA_V1 = "strata.strataview.region-manifest.v1"
MANIFEST_SCHEMA = "strata.strataview.region-manifest.v2"
TILE_SCHEMA_V1 = "strata.strataview.tile.v1"
TILE_SCHEMA = "strata.strataview.tile.v2"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_TILE_BYTES = 64 * 1024 * 1024
MAX_TILE_SET_BYTES = 512 * 1024 * 1024
MAX_TILES = 256
MAX_PALETTE = 100_000
MAX_DENSE_SECTIONS = 16_384
MAX_RESOURCE_VOXELS = 8_000_000

_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "generatedAt",
        "sourcePackage",
        "packageSchema",
        "tileSizeChunks",
        "source",
        "dimensionId",
        "providerName",
        "worldSeed",
        "terrainType",
        "chunkGeneratorClass",
        "chunkWindow",
        "blockPalette",
        "voxelMap",
        "heightmaps",
        "biomeMap",
        "fluidCells",
        "blockCounts",
        "denseStats",
        "overviewStats",
        "tiles",
        "surfacePreview",
    }
)
_MANIFEST_KEYS_V1 = _MANIFEST_KEYS - {"surfacePreview"}
_TILE_KEYS = frozenset(
    {
        "schema",
        "generatedAt",
        "tileId",
        "parentSchema",
        "sourcePackage",
        "tileWindow",
        "blockPaletteRef",
        "voxelPaletteRef",
        "heightmaps",
        "biomeMap",
        "denseSections",
        "terrainMeshes",
        "voxelMap",
        "fluidCells",
        "tileSummary",
    }
)


def _integer(
    value: Any,
    context: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{context} must be an integer",
    )
    if minimum is not None:
        require(value >= minimum, f"{context} must be at least {minimum}")
    if maximum is not None:
        require(value <= maximum, f"{context} must be at most {maximum}")
    return value


def _text(value: Any, context: str) -> str:
    require(isinstance(value, str) and bool(value), f"{context} must be nonempty text")
    return value


def _exact_keys(value: Any, expected: set[str] | frozenset[str], context: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    require(
        actual == set(expected),
        f"{context} fields mismatch: missing={sorted(set(expected) - actual)!r}, "
        f"unknown={sorted(actual - set(expected))!r}",
    )
    return value


def _counter_rows(value: Any, context: str) -> Counter[str]:
    require(isinstance(value, list), f"{context} must be an array")
    result: Counter[str] = Counter()
    for index, item in enumerate(value):
        require(isinstance(item, Mapping), f"{context}[{index}] must be an object")
        state = item.get("blockState")
        count = item.get("count")
        _text(state, f"{context}[{index}].blockState")
        _integer(count, f"{context}[{index}].count", minimum=1)
        require(state not in result, f"{context} repeats block state {state}")
        result[state] = count
    return result


def _state_properties(block_state: str) -> dict[str, str]:
    if "[" not in block_state or not block_state.endswith("]"):
        return {}
    raw = block_state.split("[", 1)[1][:-1]
    result: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key and value:
            result[key] = value
    return result


def material_from_state(block_state: str, ore_prefix: str) -> str | None:
    if not block_state.startswith(ore_prefix):
        return None
    token = block_state[len(ore_prefix) :].split("[", 1)[0]
    stem, separator, suffix = token.rpartition("_")
    if separator and suffix.isdigit() and stem:
        return stem
    return token or None


def host_from_state(
    block_state: str, host_properties: list[str], known_hosts: set[str]
) -> str | None:
    properties = _state_properties(block_state)
    for key in host_properties:
        value = properties.get(key)
        if value in known_hosts:
            return value
    return None


@dataclass
class ChunkMetrics:
    ore_counts: Counter[str] = field(default_factory=Counter)
    ore_state_counts: Counter[str] = field(default_factory=Counter)
    host_counts: Counter[str] = field(default_factory=Counter)
    ore_host_counts: Counter[str] = field(default_factory=Counter)
    surface_indicator_counts: Counter[str] = field(default_factory=Counter)
    exposed_ore_faces: int = 0
    exposed_ore_faces_by_material: Counter[str] = field(default_factory=Counter)
    subsurface_air: int = 0
    below_surface_non_air: int = 0
    non_air_blocks: int = 0
    min_height: int = 0
    max_height: int = 0
    mean_height: float = 0.0
    dominant_biome: str = "unknown"
    fluid_yield: int = 0
    fluid_cells: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "ore_blocks": sum(self.ore_counts.values()),
            "ore_material_count": len(self.ore_counts),
            "ore_materials": [
                {"material": key, "count": count}
                for key, count in sorted(
                    self.ore_counts.items(), key=lambda row: (-row[1], row[0])
                )
            ],
            "dominant_lithology": (
                sorted(self.host_counts.items(), key=lambda row: (-row[1], row[0]))[0][0]
                if self.host_counts
                else None
            ),
            "lithology_counts": [
                {"lithology": key, "count": count}
                for key, count in sorted(
                    self.host_counts.items(), key=lambda row: (-row[1], row[0])
                )
            ],
            "ore_host_counts": [
                {"lithology": key, "count": count}
                for key, count in sorted(
                    self.ore_host_counts.items(), key=lambda row: (-row[1], row[0])
                )
            ],
            "surface_indicators": [
                {"material": key, "count": count}
                for key, count in sorted(
                    self.surface_indicator_counts.items(),
                    key=lambda row: (-row[1], row[0]),
                )
            ],
            "exposed_ore_faces": self.exposed_ore_faces,
            "exposed_ore_faces_by_material": [
                {"material": key, "count": count}
                for key, count in sorted(
                    self.exposed_ore_faces_by_material.items(),
                    key=lambda row: (-row[1], row[0]),
                )
            ],
            "subsurface_air": self.subsurface_air,
            "non_air_blocks": self.non_air_blocks,
            "height": {
                "minimum": self.min_height,
                "maximum": self.max_height,
                "mean": self.mean_height,
            },
            "dominant_biome": self.dominant_biome,
            "fluid_yield": self.fluid_yield,
            "fluid_cells": self.fluid_cells,
        }


@dataclass(frozen=True)
class TileBinding:
    tile_id: str
    path: Path
    binding: FileBinding
    window: dict[str, int]


@dataclass
class _CachedTile:
    value: dict[str, Any]
    sections: dict[tuple[int, int, int], list[int]]


@dataclass
class StrataRegion:
    manifest: dict[str, Any]
    manifest_binding: FileBinding
    window: dict[str, int]
    block_palette: list[str]
    voxel_palette: list[str]
    tiles: list[TileBinding]
    tile_by_chunk: dict[tuple[int, int], TileBinding]
    chunk_metrics: dict[tuple[int, int], ChunkMetrics]
    block_counts: Counter[str]
    resource_counts: Counter[str]
    fluid_cells: list[dict[str, Any]]
    heights: dict[tuple[int, int], list[int]]
    biomes: dict[tuple[int, int], list[str]]
    tile_set_sha256: str
    tile_set_size_bytes: int
    _cache: dict[str, _CachedTile] = field(default_factory=dict, repr=False)

    @property
    def scope(self) -> dict[str, Any]:
        return {
            "world_seed": self.manifest["worldSeed"],
            "dimension_id": self.manifest["dimensionId"],
            "chunk_window": dict(self.window),
        }

    def tile_for_chunk(self, chunk_x: int, chunk_z: int) -> TileBinding:
        try:
            return self.tile_by_chunk[(chunk_x, chunk_z)]
        except KeyError as exc:
            raise SubsurfaceStudioError(
                f"chunk {chunk_x},{chunk_z} is not covered by a Strata tile"
            ) from exc

    def _cached_tile(self, tile: TileBinding) -> _CachedTile:
        existing = self._cache.get(tile.tile_id)
        if existing is not None:
            return existing
        value, binding = load_json_file(
            tile.path,
            maximum_bytes=MAX_TILE_BYTES,
            context=f"Strata tile {tile.tile_id}",
        )
        require(
            binding.sha256 == tile.binding.sha256
            and binding.size_bytes == tile.binding.size_bytes,
            f"Strata tile changed after validation: {tile.path}",
        )
        sections = {
            (row["chunkX"], row["chunkZ"], row["ySection"]): row["indices"]
            for row in value["denseSections"]
        }
        cached = _CachedTile(value=value, sections=sections)
        self._cache[tile.tile_id] = cached
        return cached

    def block_at(self, x: int, y: int, z: int) -> str:
        require(0 <= y <= 255, "block Y must be in 0..255")
        chunk_x = x // 16
        chunk_z = z // 16
        require(
            window_contains_chunk(self.window, chunk_x, chunk_z),
            f"block {x},{y},{z} is outside the captured window",
        )
        tile = self.tile_for_chunk(chunk_x, chunk_z)
        sections = self._cached_tile(tile).sections
        indices = sections.get((chunk_x, chunk_z, y // 16))
        if indices is None:
            return "minecraft:air"
        local_x = x % 16
        local_y = y % 16
        local_z = z % 16
        palette_index = indices[local_y * 256 + local_z * 16 + local_x]
        return self.block_palette[palette_index]

    def dense_section_indices(
        self, chunk_x: int, chunk_z: int, y_section: int
    ) -> list[int] | None:
        """Return one validated dense section's palette indices, or implicit air.

        The returned list is owned by the validated in-memory region and must be
        treated as read-only.  This bounded primitive lets comparison clients
        traverse exact state without repeating tile/path validation or issuing
        4,096 coordinate lookups per section.
        """

        require(0 <= y_section <= 15, "section Y must be in 0..15")
        require(
            window_contains_chunk(self.window, chunk_x, chunk_z),
            f"chunk {chunk_x},{chunk_z} is outside the captured window",
        )
        tile = self.tile_for_chunk(chunk_x, chunk_z)
        return self._cached_tile(tile).sections.get((chunk_x, chunk_z, y_section))

    def surface_at(self, x: int, z: int) -> int:
        chunk = (x // 16, z // 16)
        values = self.heights.get(chunk)
        require(values is not None, f"height coverage is unavailable at {x},{z}")
        return values[(z % 16) * 16 + (x % 16)]

    def biome_at(self, x: int, z: int) -> str:
        chunk = (x // 16, z // 16)
        values = self.biomes.get(chunk)
        require(values is not None, f"biome coverage is unavailable at {x},{z}")
        return values[(z % 16) * 16 + (x % 16)]


def _safe_tile_path(root: Path, relative: str, context: str) -> Path:
    _text(relative, context)
    pure = PurePosixPath(relative)
    require(not pure.is_absolute(), f"{context} must be relative")
    require(
        all(part not in {"", ".", ".."} for part in pure.parts),
        f"{context} contains an unsafe path",
    )
    requested = root.joinpath(*pure.parts)
    require(not requested.is_symlink(), f"{context} cannot be a symlink: {requested}")
    resolved = requested.resolve(strict=True)
    require(resolved == root or root in resolved.parents, f"{context} escapes the manifest root")
    return resolved


def _validate_palette(
    value: Any, context: str, *, allow_empty: bool = False
) -> list[str]:
    require(
        isinstance(value, list) and (allow_empty or bool(value)),
        f"{context} must be {'an array' if allow_empty else 'a nonempty array'}",
    )
    require(len(value) <= MAX_PALETTE, f"{context} exceeds {MAX_PALETTE} states")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_text(item, f"{context}[{index}]"))
    require(len(result) == len(set(result)), f"{context} contains duplicate states")
    return result


def _validate_height_surface(
    manifest: Mapping[str, Any], window: Mapping[str, int]
) -> dict[tuple[int, int], list[int]]:
    preview = manifest.get("surfacePreview")
    require(isinstance(preview, Mapping), "Strata manifest lacks surfacePreview")
    require(
        preview.get("mode") == "derived-landform-and-exact-surface-v1",
        "Strata manifest has an unsupported surface preview mode",
    )
    exact = preview.get("exactHeightmaps")
    require(isinstance(exact, Mapping), "surfacePreview lacks exactHeightmaps")
    chunks = exact.get("chunks")
    require(isinstance(chunks, list), "exactHeightmaps.chunks must be an array")
    expected = window["chunk_size_x"] * window["chunk_size_z"]
    require(len(chunks) == expected, "exact height coverage differs from chunk window")
    result: dict[tuple[int, int], list[int]] = {}
    for index, item in enumerate(chunks):
        require(isinstance(item, Mapping), f"exactHeightmaps.chunks[{index}] must be an object")
        chunk_x = _integer(item.get("chunkX"), f"exactHeightmaps.chunks[{index}].chunkX")
        chunk_z = _integer(item.get("chunkZ"), f"exactHeightmaps.chunks[{index}].chunkZ")
        require(
            window_contains_chunk(window, chunk_x, chunk_z),
            f"exact height chunk is outside scope: {chunk_x},{chunk_z}",
        )
        key = (chunk_x, chunk_z)
        require(key not in result, f"duplicate exact height chunk: {chunk_x},{chunk_z}")
        values = item.get("values")
        require(
            isinstance(values, list) and len(values) == 256,
            f"exact height chunk {chunk_x},{chunk_z} must have 256 values",
        )
        checked = [
            _integer(value, f"height {chunk_x},{chunk_z}", minimum=0, maximum=255)
            for value in values
        ]
        require(item.get("minY") == min(checked), f"height minimum drift at {chunk_x},{chunk_z}")
        require(item.get("maxY") == max(checked), f"height maximum drift at {chunk_x},{chunk_z}")
        result[key] = checked
    return result


def _biome_name(palette: list[str], biome_ids: list[int], indexes: list[int], index: int) -> str:
    if 0 <= indexes[index] < len(palette):
        value = palette[indexes[index]]
        prefix, separator, suffix = value.partition(":")
        if separator and prefix.lstrip("-").isdigit() and suffix:
            return suffix
        return value
    biome_id = biome_ids[index]
    prefix = f"{biome_id}:"
    for value in palette:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return f"biome:{biome_id}"


def _validate_biomes(
    manifest: Mapping[str, Any], window: Mapping[str, int]
) -> dict[tuple[int, int], list[str]]:
    preview = manifest["surfacePreview"]
    biome_map = preview.get("biomeMap")
    require(isinstance(biome_map, Mapping), "surfacePreview lacks biomeMap")
    chunks = biome_map.get("chunks")
    require(isinstance(chunks, list), "biomeMap.chunks must be an array")
    expected = window["chunk_size_x"] * window["chunk_size_z"]
    require(len(chunks) == expected, "biome coverage differs from chunk window")
    result: dict[tuple[int, int], list[str]] = {}
    for index, item in enumerate(chunks):
        require(isinstance(item, Mapping), f"biomeMap.chunks[{index}] must be an object")
        chunk_x = _integer(item.get("chunkX"), f"biomeMap.chunks[{index}].chunkX")
        chunk_z = _integer(item.get("chunkZ"), f"biomeMap.chunks[{index}].chunkZ")
        key = (chunk_x, chunk_z)
        require(window_contains_chunk(window, *key), f"biome chunk is outside scope: {key}")
        require(key not in result, f"duplicate biome chunk: {key}")
        palette = item.get("biomePalette")
        ids = item.get("biomeIds")
        indexes = item.get("biomes")
        require(
            isinstance(palette, list)
            and all(isinstance(value, str) and value for value in palette),
            f"biome palette is invalid at {key}",
        )
        require(
            isinstance(ids, list)
            and isinstance(indexes, list)
            and len(ids) == len(indexes) == 256,
            f"biome arrays must contain 256 values at {key}",
        )
        checked_ids = [_integer(value, f"biome ID at {key}") for value in ids]
        checked_indexes = [_integer(value, f"biome index at {key}", minimum=0) for value in indexes]
        result[key] = [
            _biome_name(palette, checked_ids, checked_indexes, offset)
            for offset in range(256)
        ]
    return result


def _tile_descriptor_window(value: Mapping[str, Any]) -> dict[str, int]:
    window = normalize_window(value, camel_case=True)
    require(window["halo_chunks"] == 0, "Strata region tiles must have zero halo")
    return window


def _validate_tile(
    value: Mapping[str, Any],
    *,
    descriptor: Mapping[str, Any],
    tile_window: Mapping[str, int],
    block_palette: list[str],
    voxel_palette: list[str],
    heights: Mapping[tuple[int, int], list[int]],
    air_states: set[str],
    ore_prefix: str,
    surface_rock_prefix: str,
    host_properties: list[str],
    known_hosts: set[str],
    chunk_metrics: dict[tuple[int, int], ChunkMetrics],
    manifest_schema: str,
) -> tuple[Counter[str], Counter[str], list[dict[str, Any]], int, int, int]:
    tile_id = descriptor["tileId"]
    _exact_keys(value, _TILE_KEYS, f"Strata tile {tile_id}")
    expected_tile_schema = (
        TILE_SCHEMA if manifest_schema == MANIFEST_SCHEMA else TILE_SCHEMA_V1
    )
    require(value["schema"] == expected_tile_schema, f"{tile_id} schema drift")
    require(value["parentSchema"] == manifest_schema, f"{tile_id} parent schema drift")
    require(value["tileId"] == tile_id, f"{tile_id} identity drift")
    require(
        _tile_descriptor_window(value["tileWindow"]) == dict(tile_window),
        f"{tile_id} window drift",
    )
    require(value["blockPaletteRef"] == "manifest.blockPalette", f"{tile_id} block palette reference drift")
    require(value["voxelPaletteRef"] == "manifest.voxelMap.palette", f"{tile_id} voxel palette reference drift")

    dense = value["denseSections"]
    require(isinstance(dense, list), f"{tile_id}.denseSections must be an array")
    require(len(dense) <= MAX_DENSE_SECTIONS, f"{tile_id} has too many dense sections")
    section_index: dict[tuple[int, int, int], list[int]] = {}
    tile_block_counts: Counter[str] = Counter()
    tile_non_air = 0
    for index, section in enumerate(dense):
        require(isinstance(section, Mapping), f"{tile_id}.denseSections[{index}] must be an object")
        chunk_x = _integer(section.get("chunkX"), f"{tile_id}.denseSections[{index}].chunkX")
        chunk_z = _integer(section.get("chunkZ"), f"{tile_id}.denseSections[{index}].chunkZ")
        y_section = _integer(
            section.get("ySection"),
            f"{tile_id}.denseSections[{index}].ySection",
            minimum=0,
            maximum=15,
        )
        require(
            window_contains_chunk(tile_window, chunk_x, chunk_z),
            f"{tile_id} contains a dense section outside its window",
        )
        key = (chunk_x, chunk_z, y_section)
        require(key not in section_index, f"{tile_id} repeats dense section {key}")
        indices = section.get("indices")
        require(
            isinstance(indices, list) and len(indices) == 4096,
            f"{tile_id} dense section {key} must contain 4096 indices",
        )
        checked: list[int] = []
        non_air = 0
        metric = chunk_metrics[(chunk_x, chunk_z)]
        surface = heights[(chunk_x, chunk_z)]
        for offset, palette_index in enumerate(indices):
            palette_index = _integer(
                palette_index,
                f"{tile_id} dense section palette index",
                minimum=0,
                maximum=len(block_palette) - 1,
            )
            checked.append(palette_index)
            state = block_palette[palette_index]
            tile_block_counts[state] += 1
            local_y = offset // 256
            remainder = offset % 256
            local_z = remainder // 16
            local_x = remainder % 16
            world_y = y_section * 16 + local_y
            if state not in air_states:
                non_air += 1
                tile_non_air += 1
                metric.non_air_blocks += 1
                if world_y < surface[local_z * 16 + local_x]:
                    metric.below_surface_non_air += 1
                host = host_from_state(state, host_properties, known_hosts)
                if host is not None and not state.startswith(ore_prefix):
                    metric.host_counts[host] += 1
        require(
            section.get("nonAirCount") == non_air,
            f"{tile_id} dense section {key} non-air count drift",
        )
        section_index[key] = checked

    voxel_map = value["voxelMap"]
    require(isinstance(voxel_map, Mapping), f"{tile_id}.voxelMap must be an object")
    require(
        voxel_map.get("voxelEncoding") == "localX,y,localZ,paletteIndex",
        f"{tile_id} voxel encoding drift",
    )
    voxel_chunks = voxel_map.get("chunks")
    require(isinstance(voxel_chunks, list), f"{tile_id}.voxelMap.chunks must be an array")
    tile_resource_counts: Counter[str] = Counter()
    voxel_total = 0
    voxel_positions: set[tuple[int, int, int]] = set()
    for chunk_index, chunk in enumerate(voxel_chunks):
        require(isinstance(chunk, Mapping), f"{tile_id}.voxelMap.chunks[{chunk_index}] must be an object")
        chunk_x = _integer(chunk.get("chunkX"), f"{tile_id}.voxel chunk X")
        chunk_z = _integer(chunk.get("chunkZ"), f"{tile_id}.voxel chunk Z")
        require(
            window_contains_chunk(tile_window, chunk_x, chunk_z),
            f"{tile_id} contains resource voxels outside its window",
        )
        voxels = chunk.get("voxels")
        require(isinstance(voxels, list), f"{tile_id} voxel list must be an array")
        require(chunk.get("voxelCount") == len(voxels), f"{tile_id} voxel count drift")
        metric = chunk_metrics[(chunk_x, chunk_z)]
        for voxel_index, voxel in enumerate(voxels):
            require(
                isinstance(voxel, list) and len(voxel) == 4,
                f"{tile_id} voxel {voxel_index} must contain four integers",
            )
            local_x = _integer(voxel[0], f"{tile_id} voxel localX", minimum=0, maximum=15)
            y = _integer(voxel[1], f"{tile_id} voxel Y", minimum=0, maximum=255)
            local_z = _integer(voxel[2], f"{tile_id} voxel localZ", minimum=0, maximum=15)
            palette_index = _integer(
                voxel[3],
                f"{tile_id} voxel palette index",
                minimum=0,
                maximum=len(voxel_palette) - 1,
            )
            world_position = (chunk_x * 16 + local_x, y, chunk_z * 16 + local_z)
            require(world_position not in voxel_positions, f"{tile_id} repeats resource voxel {world_position}")
            voxel_positions.add(world_position)
            state = voxel_palette[palette_index]
            section = section_index.get((chunk_x, chunk_z, y // 16))
            require(section is not None, f"{tile_id} resource voxel lacks its dense section")
            dense_state = block_palette[
                section[(y % 16) * 256 + local_z * 16 + local_x]
            ]
            require(
                dense_state == state,
                f"{tile_id} resource voxel disagrees with dense state at {world_position}",
            )
            tile_resource_counts[state] += 1
            material = material_from_state(state, ore_prefix)
            if material is not None:
                metric.ore_counts[material] += 1
                metric.ore_state_counts[state] += 1
                host = host_from_state(state, host_properties, known_hosts)
                if host is not None:
                    metric.ore_host_counts[host] += 1
            elif state.startswith(surface_rock_prefix):
                variant = _state_properties(state).get("variant")
                if variant:
                    if "__" in variant:
                        variant = variant.split("__", 1)[1]
                    metric.surface_indicator_counts[variant] += 1
            voxel_total += 1
    require(voxel_total <= MAX_RESOURCE_VOXELS, f"{tile_id} has too many resource voxels")
    require(voxel_map.get("voxelCount") == voxel_total, f"{tile_id} total voxel count drift")

    faces_total = 0
    exposed_ore_faces = 0
    meshes = value["terrainMeshes"]
    require(isinstance(meshes, list), f"{tile_id}.terrainMeshes must be an array")
    for mesh_index, mesh in enumerate(meshes):
        require(isinstance(mesh, Mapping), f"{tile_id}.terrainMeshes[{mesh_index}] must be an object")
        state = _text(mesh.get("blockState"), f"{tile_id}.terrainMeshes[{mesh_index}].blockState")
        faces = mesh.get("faces")
        require(isinstance(faces, list) and len(faces) % 6 == 0, f"{tile_id} terrain face encoding drift")
        count = len(faces) // 6
        require(mesh.get("faceCount") == count, f"{tile_id} terrain face count drift")
        faces_total += count
        if state.startswith(ore_prefix):
            exposed_ore_faces += count
            material = material_from_state(state, ore_prefix)
            for offset in range(0, len(faces), 6):
                world_x = _integer(faces[offset], f"{tile_id} terrain face X")
                world_z = _integer(faces[offset + 2], f"{tile_id} terrain face Z")
                chunk = (world_x // 16, world_z // 16)
                if window_contains_chunk(tile_window, *chunk):
                    chunk_metrics[chunk].exposed_ore_faces += 1
                    if material is not None:
                        chunk_metrics[chunk].exposed_ore_faces_by_material[
                            material
                        ] += 1

    summary = value["tileSummary"]
    require(isinstance(summary, Mapping), f"{tile_id}.tileSummary must be an object")
    require(summary.get("denseSections") == len(dense), f"{tile_id} dense section summary drift")
    require(summary.get("nonAirBlocks") == tile_non_air, f"{tile_id} non-air summary drift")
    require(summary.get("resourceVoxels") == voxel_total, f"{tile_id} resource summary drift")
    require(summary.get("terrainMeshes") == len(meshes), f"{tile_id} terrain mesh summary drift")
    require(summary.get("terrainFaces") == faces_total, f"{tile_id} terrain face summary drift")
    require(
        _counter_rows(summary.get("blockStateCounts"), f"{tile_id}.tileSummary.blockStateCounts")
        == Counter({key: value for key, value in tile_block_counts.items() if key not in air_states}),
        f"{tile_id} block-state summary drift",
    )
    require(
        _counter_rows(
            summary.get("resourceBlockStateCounts"),
            f"{tile_id}.tileSummary.resourceBlockStateCounts",
        )
        == tile_resource_counts,
        f"{tile_id} resource-state summary drift",
    )
    fluids = value["fluidCells"]
    require(isinstance(fluids, list), f"{tile_id}.fluidCells must be an array")
    require(summary.get("fluidCells") == len(fluids), f"{tile_id} fluid-cell summary drift")
    return (
        Counter({key: value for key, value in tile_block_counts.items() if key not in air_states}),
        tile_resource_counts,
        [dict(row) for row in fluids],
        len(dense),
        tile_non_air,
        faces_total,
    )


def _fluid_identity(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("veinX"),
        value.get("veinZ"),
        value.get("queryChunkX"),
        value.get("queryChunkZ"),
        value.get("fluid"),
        value.get("depositName"),
    )


def load_strata_region(
    manifest_path: Path | str, *, profile: Mapping[str, Any]
) -> StrataRegion:
    manifest, manifest_binding = load_json_file(
        manifest_path,
        maximum_bytes=MAX_MANIFEST_BYTES,
        context="Strata region manifest",
    )
    manifest_schema = manifest.get("schema")
    require(
        manifest_schema in {MANIFEST_SCHEMA_V1, MANIFEST_SCHEMA},
        "Strata manifest schema drift",
    )
    _exact_keys(
        manifest,
        _MANIFEST_KEYS if manifest_schema == MANIFEST_SCHEMA else _MANIFEST_KEYS_V1,
        "Strata region manifest",
    )
    _text(manifest["sourcePackage"], "Strata manifest sourcePackage")
    _text(manifest["providerName"], "Strata manifest providerName")
    _text(manifest["chunkGeneratorClass"], "Strata manifest chunkGeneratorClass")
    _integer(manifest["dimensionId"], "Strata manifest dimensionId")
    require(
        (
            isinstance(manifest["worldSeed"], int)
            and not isinstance(manifest["worldSeed"], bool)
        )
        or (isinstance(manifest["worldSeed"], str) and bool(manifest["worldSeed"])),
        "Strata manifest worldSeed is invalid",
    )
    window = normalize_window(manifest["chunkWindow"], camel_case=True)
    block_palette = _validate_palette(manifest["blockPalette"], "Strata blockPalette")
    voxel_map = manifest["voxelMap"]
    require(isinstance(voxel_map, Mapping), "Strata manifest voxelMap must be an object")
    voxel_palette = _validate_palette(
        voxel_map.get("palette"), "Strata voxel palette", allow_empty=True
    )
    state_semantics = profile["state_semantics"]
    air_states = set(state_semantics["air_states"])
    require(
        any(state in block_palette for state in air_states),
        "Strata block palette lacks a profile-declared air state",
    )
    ore_prefix = state_semantics["ore_prefix"]
    surface_rock_prefix = state_semantics["surface_rock_prefix"]
    host_properties = list(state_semantics["host_properties"])
    known_hosts = set(state_semantics["known_host_variants"])

    descriptors = manifest["tiles"]
    require(isinstance(descriptors, list) and descriptors, "Strata manifest has no tiles")
    require(len(descriptors) <= MAX_TILES, f"Strata manifest exceeds {MAX_TILES} tiles")
    root = manifest_binding.path.parent
    prepared: list[
        tuple[Mapping[str, Any], Path, dict[str, int], int, dict[str, Any], FileBinding]
    ] = []
    tile_ids: set[str] = set()
    covered_chunks: set[tuple[int, int]] = set()
    total_size = 0
    for index, descriptor in enumerate(descriptors):
        _exact_keys(descriptor, {"tileId", "path", "tileWindow", "summary"}, f"tiles[{index}]")
        tile_id = _text(descriptor["tileId"], f"tiles[{index}].tileId")
        require(tile_id not in tile_ids, f"duplicate Strata tile ID: {tile_id}")
        tile_ids.add(tile_id)
        tile_window = _tile_descriptor_window(descriptor["tileWindow"])
        tile_path = _safe_tile_path(root, descriptor["path"], f"tiles[{index}].path")
        size = tile_path.stat().st_size
        require(0 < size <= MAX_TILE_BYTES, f"Strata tile is outside size bounds: {tile_path}")
        total_size += size
        require(total_size <= MAX_TILE_SET_BYTES, "Strata tile set exceeds aggregate byte bound")
        for chunk_z in range(
            tile_window["min_chunk_z"],
            tile_window["min_chunk_z"] + tile_window["chunk_size_z"],
        ):
            for chunk_x in range(
                tile_window["min_chunk_x"],
                tile_window["min_chunk_x"] + tile_window["chunk_size_x"],
            ):
                require(
                    window_contains_chunk(window, chunk_x, chunk_z),
                    f"Strata tile {tile_id} covers a chunk outside the manifest",
                )
                chunk = (chunk_x, chunk_z)
                require(chunk not in covered_chunks, f"Strata tiles overlap at chunk {chunk}")
                covered_chunks.add(chunk)
        tile, tile_binding = load_json_file(
            tile_path,
            maximum_bytes=MAX_TILE_BYTES,
            context=f"Strata tile {tile_id}",
        )
        prepared.append(
            (descriptor, tile_path, tile_window, size, tile, tile_binding)
        )
    expected_chunks = {
        (chunk_x, chunk_z)
        for chunk_z in range(
            window["min_chunk_z"], window["min_chunk_z"] + window["chunk_size_z"]
        )
        for chunk_x in range(
            window["min_chunk_x"], window["min_chunk_x"] + window["chunk_size_x"]
        )
    }
    require(covered_chunks == expected_chunks, "Strata tiles do not exactly cover the manifest window")

    if manifest_schema == MANIFEST_SCHEMA:
        heights = _validate_height_surface(manifest, window)
        biomes = _validate_biomes(manifest, window)
    else:
        height_chunks: list[Any] = []
        biome_chunks: list[Any] = []
        for descriptor, _, _, _, tile, _ in prepared:
            heightmaps = tile.get("heightmaps")
            biome_map = tile.get("biomeMap")
            require(
                isinstance(heightmaps, Mapping)
                and heightmaps.get("mode") == "chunk-surface-height"
                and isinstance(heightmaps.get("chunks"), list),
                f"Strata V1 tile {descriptor['tileId']} lacks exact height chunks",
            )
            require(
                isinstance(biome_map, Mapping)
                and biome_map.get("mode") == "chunk-biomes"
                and isinstance(biome_map.get("chunks"), list),
                f"Strata V1 tile {descriptor['tileId']} lacks exact biome chunks",
            )
            height_chunks.extend(heightmaps["chunks"])
            biome_chunks.extend(biome_map["chunks"])
        surface_projection = {
            "surfacePreview": {
                "mode": "derived-landform-and-exact-surface-v1",
                "exactHeightmaps": {"chunks": height_chunks},
                "biomeMap": {"chunks": biome_chunks},
            }
        }
        heights = _validate_height_surface(surface_projection, window)
        biomes = _validate_biomes(surface_projection, window)

    chunk_metrics = {chunk: ChunkMetrics() for chunk in sorted(expected_chunks)}
    for chunk, values in heights.items():
        metric = chunk_metrics[chunk]
        metric.min_height = min(values)
        metric.max_height = max(values)
        metric.mean_height = sum(values) / len(values)
        biome_counts = Counter(biomes[chunk])
        metric.dominant_biome = sorted(
            biome_counts.items(), key=lambda row: (-row[1], row[0])
        )[0][0]

    tile_bindings: list[TileBinding] = []
    aggregate_blocks: Counter[str] = Counter()
    aggregate_resources: Counter[str] = Counter()
    aggregate_fluids: dict[tuple[Any, ...], dict[str, Any]] = {}
    dense_sections = 0
    non_air_blocks = 0
    exposed_faces = 0
    tile_tree_material = bytearray()
    for descriptor, tile_path, tile_window, _, tile, binding in prepared:
        counts, resources, fluids, dense_count, non_air, faces = _validate_tile(
            tile,
            descriptor=descriptor,
            tile_window=tile_window,
            block_palette=block_palette,
            voxel_palette=voxel_palette,
            heights=heights,
            air_states=air_states,
            ore_prefix=ore_prefix,
            surface_rock_prefix=surface_rock_prefix,
            host_properties=host_properties,
            known_hosts=known_hosts,
            chunk_metrics=chunk_metrics,
            manifest_schema=manifest_schema,
        )
        aggregate_blocks.update(counts)
        aggregate_resources.update(resources)
        dense_sections += dense_count
        non_air_blocks += non_air
        exposed_faces += faces
        for fluid in fluids:
            identity = _fluid_identity(fluid)
            previous = aggregate_fluids.setdefault(identity, fluid)
            require(previous == fluid, f"conflicting duplicate virtual fluid cell: {identity}")
        tile_bindings.append(
            TileBinding(
                tile_id=descriptor["tileId"],
                path=binding.path,
                binding=binding,
                window=dict(tile_window),
            )
        )
        relative = descriptor["path"]
        tile_tree_material.extend(
            f"{relative}\0{binding.sha256}\0{binding.size_bytes}\n".encode("utf-8")
        )

    manifest_counts = _counter_rows(manifest["blockCounts"], "Strata manifest blockCounts")
    require(
        (
            aggregate_resources == manifest_counts
            if manifest_schema == MANIFEST_SCHEMA
            else aggregate_blocks == manifest_counts
        ),
        "Strata manifest resource block counts drift",
    )
    overview = manifest["overviewStats"]
    require(isinstance(overview, Mapping), "Strata overviewStats must be an object")
    require(
        _counter_rows(overview.get("blockStateCounts"), "Strata overview blockStateCounts")
        == aggregate_blocks,
        "Strata overview block counts drift",
    )
    require(
        _counter_rows(
            overview.get("resourceBlockStateCounts"),
            "Strata overview resourceBlockStateCounts",
        )
        == aggregate_resources,
        "Strata overview resource counts drift",
    )
    dense_stats = manifest["denseStats"]
    require(isinstance(dense_stats, Mapping), "Strata denseStats must be an object")
    require(dense_stats.get("denseSections") == dense_sections, "Strata dense section total drift")
    require(dense_stats.get("nonAirBlocks") == non_air_blocks, "Strata non-air total drift")
    require(dense_stats.get("exposedFaces") == exposed_faces, "Strata exposed-face total drift")
    require(
        voxel_map.get("voxelCount") == sum(aggregate_resources.values()),
        "Strata manifest resource voxel total drift",
    )
    manifest_fluids = manifest["fluidCells"]
    require(isinstance(manifest_fluids, list), "Strata manifest fluidCells must be an array")
    expected_fluid_map: dict[tuple[Any, ...], dict[str, Any]] = {}
    for fluid in manifest_fluids:
        require(isinstance(fluid, Mapping), "Strata fluid cell must be an object")
        identity = _fluid_identity(fluid)
        require(identity not in expected_fluid_map, f"duplicate manifest fluid cell: {identity}")
        expected_fluid_map[identity] = dict(fluid)
    require(expected_fluid_map == aggregate_fluids, "Strata virtual fluid-cell coverage drift")
    for fluid in aggregate_fluids.values():
        query_chunk = (fluid.get("queryChunkX"), fluid.get("queryChunkZ"))
        if query_chunk in chunk_metrics:
            yield_value = fluid.get("fluidYield")
            if isinstance(yield_value, int) and not isinstance(yield_value, bool):
                chunk_metrics[query_chunk].fluid_yield += yield_value
            chunk_metrics[query_chunk].fluid_cells += 1

    for chunk, metric in chunk_metrics.items():
        below_surface_volume = sum(heights[chunk])
        require(
            metric.below_surface_non_air <= below_surface_volume,
            f"Strata non-air state exceeds below-surface volume at chunk {chunk}",
        )
        metric.subsurface_air = below_surface_volume - metric.below_surface_non_air

    tile_by_chunk: dict[tuple[int, int], TileBinding] = {}
    for tile in tile_bindings:
        for chunk_z in range(
            tile.window["min_chunk_z"],
            tile.window["min_chunk_z"] + tile.window["chunk_size_z"],
        ):
            for chunk_x in range(
                tile.window["min_chunk_x"],
                tile.window["min_chunk_x"] + tile.window["chunk_size_x"],
            ):
                tile_by_chunk[(chunk_x, chunk_z)] = tile

    return StrataRegion(
        manifest=manifest,
        manifest_binding=manifest_binding,
        window=window,
        block_palette=block_palette,
        voxel_palette=voxel_palette,
        tiles=tile_bindings,
        tile_by_chunk=tile_by_chunk,
        chunk_metrics=chunk_metrics,
        block_counts=aggregate_blocks,
        resource_counts=aggregate_resources,
        fluid_cells=[aggregate_fluids[key] for key in sorted(aggregate_fluids, key=repr)],
        heights=heights,
        biomes=biomes,
        tile_set_sha256=__import__("hashlib").sha256(tile_tree_material).hexdigest(),
        tile_set_size_bytes=sum(tile.binding.size_bytes for tile in tile_bindings),
    )
