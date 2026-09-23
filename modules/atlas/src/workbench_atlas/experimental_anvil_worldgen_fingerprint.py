"""Canonical, read-only fingerprints for stopped legacy Anvil worlds.

The structural Anvil observer remains the prerequisite authority.  This
experimental layer deliberately ignores runtime-only chunk fields such as
timestamps, inhabited time, entities, and scheduled ticks, then fingerprints
the saved block, biome, heightmap, structure, lighting, and population state
that can be compared across fixed-seed generation runs.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
from typing import Any

from .experimental_anvil_region_observation import (
    AtlasAnvilRegionObservationError,
    MAX_CHUNKS,
    MAX_DECOMPRESSED_CHUNK_BYTES,
    MAX_NBT_COLLECTION_ENTRIES,
    MAX_NBT_DEPTH,
    MAX_NBT_TAGS,
    SECTOR_BYTES,
    _canonical_bytes,
    _decompress_chunk,
    _NBTError as _AnvilNBTError,
    _read_immutable_region,
    observe_anvil_region_world,
)


FORMAT = "atlas-experimental-anvil-worldgen-fingerprint-v1"
COMPARISON_FORMAT = "atlas-experimental-anvil-worldgen-comparison-v1"
SCHEMA_VERSION = 1
MAX_LEVEL_DAT_BYTES = 8 * 1024 * 1024
MAX_LEVEL_DAT_DECOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_COMPARISON_DIFFERENCES = 65_536


class AtlasAnvilWorldgenFingerprintError(ValueError):
    """Raised when worldgen evidence cannot be fingerprinted safely."""


class _NBTError(ValueError):
    """Bounded NBT decoding failure."""


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _identity(value: Any) -> str:
    return "sha256:" + _digest(_canonical_bytes(value))


def _read_stable_file(path: Path, maximum_bytes: int) -> bytes:
    if path.is_symlink():
        raise AtlasAnvilWorldgenFingerprintError(
            f"worldgen evidence cannot be a symbolic link: {path.name}"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise AtlasAnvilWorldgenFingerprintError(
            f"cannot open worldgen evidence: {path}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AtlasAnvilWorldgenFingerprintError(
                f"worldgen evidence is not a regular file: {path}"
            )
        if before.st_size > maximum_bytes:
            raise AtlasAnvilWorldgenFingerprintError(
                f"worldgen evidence exceeds the {maximum_bytes}-byte limit"
            )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise AtlasAnvilWorldgenFingerprintError(
                    f"worldgen evidence ended during its read: {path}"
                )
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise AtlasAnvilWorldgenFingerprintError(
            f"cannot read worldgen evidence: {path}"
        ) from exc
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise AtlasAnvilWorldgenFingerprintError(
            f"worldgen evidence changed while it was read: {path}"
        )
    return b"".join(chunks)


class _BoundedNBTReader:
    _FIXED_SIZES = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.tags = 0
        self.collection_entries = 0

    def _take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.payload):
            raise _NBTError("NBT payload ends before a declared value")
        start = self.offset
        self.offset += count
        return self.payload[start:self.offset]

    def _u8(self) -> int:
        return self._take(1)[0]

    def _i8(self) -> int:
        return int.from_bytes(self._take(1), "big", signed=True)

    def _u16(self) -> int:
        return int.from_bytes(self._take(2), "big", signed=False)

    def _i16(self) -> int:
        return int.from_bytes(self._take(2), "big", signed=True)

    def _i32(self) -> int:
        return int.from_bytes(self._take(4), "big", signed=True)

    def _i64(self) -> int:
        return int.from_bytes(self._take(8), "big", signed=True)

    def _name_bytes(self) -> bytes:
        return self._take(self._u16())

    def _name(self) -> str:
        try:
            return self._name_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _NBTError("NBT tag name is not valid UTF-8") from exc

    def _count_tag(self) -> None:
        self.tags += 1
        if self.tags > MAX_NBT_TAGS:
            raise _NBTError("NBT payload exceeds the tag-count limit")

    def _count_collection(self, count: int) -> None:
        if count < 0:
            raise _NBTError("NBT collection has a negative length")
        self.collection_entries += count
        if self.collection_entries > MAX_NBT_COLLECTION_ENTRIES:
            raise _NBTError("NBT payload exceeds the collection-entry limit")

    def _byte_array(self) -> bytes:
        count = self._i32()
        self._count_collection(count)
        return self._take(count)

    def _int_array_bytes(self) -> bytes:
        count = self._i32()
        self._count_collection(count)
        return self._take(count * 4)

    def _skip_payload(self, tag_type: int, depth: int) -> None:
        if depth > MAX_NBT_DEPTH:
            raise _NBTError("NBT payload exceeds the nesting-depth limit")
        fixed = self._FIXED_SIZES.get(tag_type)
        if fixed is not None:
            self._take(fixed)
            return
        if tag_type == 7:
            self._byte_array()
            return
        if tag_type == 8:
            self._take(self._u16())
            return
        if tag_type == 9:
            element_type = self._u8()
            count = self._i32()
            self._count_collection(count)
            if count and element_type == 0:
                raise _NBTError("non-empty NBT list uses TAG_End elements")
            if element_type > 12:
                raise _NBTError("NBT list uses an unsupported tag type")
            for _index in range(count):
                self._count_tag()
                self._skip_payload(element_type, depth + 1)
            return
        if tag_type == 10:
            self._skip_compound(depth)
            return
        if tag_type == 11:
            self._int_array_bytes()
            return
        if tag_type == 12:
            count = self._i32()
            self._count_collection(count)
            self._take(count * 8)
            return
        raise _NBTError(f"NBT payload uses unsupported tag type {tag_type}")

    def _skip_compound(self, depth: int) -> None:
        if depth > MAX_NBT_DEPTH:
            raise _NBTError("NBT payload exceeds the nesting-depth limit")
        while True:
            tag_type = self._u8()
            if tag_type == 0:
                return
            if tag_type > 12:
                raise _NBTError(
                    f"NBT payload uses unsupported tag type {tag_type}"
                )
            self._count_tag()
            self._name_bytes()
            self._skip_payload(tag_type, depth + 1)

    def _raw_payload(self, tag_type: int, depth: int) -> bytes:
        start = self.offset
        self._skip_payload(tag_type, depth)
        return self.payload[start:self.offset]


class _ChunkFingerprintReader(_BoundedNBTReader):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.coordinates: dict[str, int] = {}
        self.population: dict[str, bool] = {}
        self.biomes: bytes | None = None
        self.heightmap: bytes | None = None
        self.structures: bytes | None = None
        self.sections: list[dict[str, Any]] = []
        self._seen_level = False
        self._sections_seen = False

    def _section(self, depth: int) -> None:
        section: dict[str, Any] = {}
        while True:
            tag_type = self._u8()
            if tag_type == 0:
                break
            if tag_type > 12:
                raise _NBTError("section uses an unsupported tag type")
            self._count_tag()
            name = self._name()
            if name == "Y" and tag_type == 1:
                if "y" in section:
                    raise _NBTError("section contains duplicate Y")
                section["y"] = self._i8()
            elif name in {
                "Blocks",
                "Data",
                "Add",
                "BlockLight",
                "SkyLight",
            } and tag_type == 7:
                if name in section:
                    raise _NBTError(f"section contains duplicate {name}")
                section[name] = self._byte_array()
            else:
                self._skip_payload(tag_type, depth + 1)
        if "y" not in section:
            raise _NBTError("section does not contain Y")
        self.sections.append(section)

    def _section_list(self, depth: int) -> None:
        element_type = self._u8()
        count = self._i32()
        self._count_collection(count)
        if count and element_type != 10:
            raise _NBTError("Level.Sections is not a compound list")
        if count == 0 and element_type not in {0, 10}:
            raise _NBTError("empty Level.Sections uses an unexpected type")
        for _index in range(count):
            self._count_tag()
            self._section(depth + 1)

    def _level(self, depth: int) -> None:
        while True:
            tag_type = self._u8()
            if tag_type == 0:
                return
            if tag_type > 12:
                raise _NBTError("Level uses an unsupported tag type")
            self._count_tag()
            name = self._name()
            if name in {"xPos", "zPos"} and tag_type == 3:
                if name in self.coordinates:
                    raise _NBTError(f"Level contains duplicate {name}")
                self.coordinates[name] = self._i32()
            elif name in {"TerrainPopulated", "LightPopulated"} and tag_type == 1:
                if name in self.population:
                    raise _NBTError(f"Level contains duplicate {name}")
                self.population[name] = self._i8() != 0
            elif name == "Biomes" and tag_type == 7:
                if self.biomes is not None:
                    raise _NBTError("Level contains duplicate Biomes")
                self.biomes = self._byte_array()
            elif name == "HeightMap" and tag_type == 11:
                if self.heightmap is not None:
                    raise _NBTError("Level contains duplicate HeightMap")
                self.heightmap = self._int_array_bytes()
            elif name == "Sections" and tag_type == 9:
                if self._sections_seen:
                    raise _NBTError("Level contains duplicate Sections")
                self._sections_seen = True
                self._section_list(depth + 1)
            elif name == "Structures" and tag_type == 10:
                if self.structures is not None:
                    raise _NBTError("Level contains duplicate Structures")
                self.structures = self._raw_payload(tag_type, depth + 1)
            else:
                self._skip_payload(tag_type, depth + 1)

    def result(self) -> dict[str, Any]:
        if self._u8() != 10:
            raise _NBTError("chunk NBT root is not a compound")
        self._name_bytes()
        while True:
            tag_type = self._u8()
            if tag_type == 0:
                break
            if tag_type > 12:
                raise _NBTError("chunk root uses an unsupported tag type")
            self._count_tag()
            name = self._name()
            if name == "Level":
                if self._seen_level:
                    raise _NBTError("chunk root contains duplicate Level")
                self._seen_level = True
                if tag_type != 10:
                    raise _NBTError("chunk Level is not a compound")
                self._level(2)
            else:
                self._skip_payload(tag_type, 2)
        if self.offset != len(self.payload):
            raise _NBTError("chunk NBT has trailing bytes")
        if not self._seen_level:
            raise _NBTError("chunk root does not contain Level")
        if set(self.coordinates) != {"xPos", "zPos"}:
            raise _NBTError("chunk Level lacks coordinates")

        section_records: list[dict[str, Any]] = []
        seen_y: set[int] = set()
        for section in sorted(self.sections, key=lambda item: item["y"]):
            y = section["y"]
            if y in seen_y:
                raise _NBTError("chunk contains duplicate section Y")
            seen_y.add(y)
            block_projection = {
                key: {
                    "present": key in section,
                    "sha256": _digest(section.get(key, b"")),
                    "size": len(section.get(key, b"")),
                }
                for key in ("Blocks", "Data", "Add")
            }
            light_projection = {
                key: {
                    "present": key in section,
                    "sha256": _digest(section.get(key, b"")),
                    "size": len(section.get(key, b"")),
                }
                for key in ("BlockLight", "SkyLight")
            }
            section_records.append({
                "y": y,
                "block_content_sha256": _digest(
                    _canonical_bytes(block_projection)
                ),
                "lighting_sha256": _digest(
                    _canonical_bytes(light_projection)
                ),
            })

        block_content_sha256 = _digest(_canonical_bytes([
            {"y": item["y"], "sha256": item["block_content_sha256"]}
            for item in section_records
        ]))
        lighting_sha256 = _digest(_canonical_bytes([
            {"y": item["y"], "sha256": item["lighting_sha256"]}
            for item in section_records
        ]))
        biome_sha256 = _digest(self.biomes or b"")
        heightmap_sha256 = _digest(self.heightmap or b"")
        structure_sha256 = _digest(self.structures or b"")
        observed_lengths = {
            "biomes": 0 if self.biomes is None else len(self.biomes),
            "heightmap_entries": (
                0 if self.heightmap is None else len(self.heightmap) // 4
            ),
        }
        worldgen_projection = {
            "block_content_sha256": block_content_sha256,
            "biome_sha256": biome_sha256,
            "biomes_present": self.biomes is not None,
            "heightmap_sha256": heightmap_sha256,
            "heightmap_present": self.heightmap is not None,
            "structure_sha256": structure_sha256,
            "structures_present": self.structures is not None,
            "observed_lengths": observed_lengths,
        }
        return {
            "x": self.coordinates["xPos"],
            "z": self.coordinates["zPos"],
            "terrain_populated": self.population.get("TerrainPopulated"),
            "light_populated": self.population.get("LightPopulated"),
            "section_count": len(section_records),
            "sections": section_records,
            **worldgen_projection,
            "lighting_sha256": lighting_sha256,
            "worldgen_sha256": _digest(_canonical_bytes(worldgen_projection)),
            "observed_lengths": observed_lengths,
        }


class _ValueNBTReader(_BoundedNBTReader):
    """Bounded full decoder used only for the small, capped level.dat."""

    def _value(self, tag_type: int, depth: int) -> Any:
        if depth > MAX_NBT_DEPTH:
            raise _NBTError("NBT payload exceeds the nesting-depth limit")
        if tag_type == 1:
            return self._i8()
        if tag_type == 2:
            return self._i16()
        if tag_type == 3:
            return self._i32()
        if tag_type == 4:
            return self._i64()
        if tag_type == 5:
            return struct.unpack(">f", self._take(4))[0]
        if tag_type == 6:
            return struct.unpack(">d", self._take(8))[0]
        if tag_type == 7:
            return self._byte_array()
        if tag_type == 8:
            try:
                return self._take(self._u16()).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _NBTError("NBT string is not valid UTF-8") from exc
        if tag_type == 9:
            element_type = self._u8()
            count = self._i32()
            self._count_collection(count)
            if count and element_type == 0:
                raise _NBTError("non-empty NBT list uses TAG_End elements")
            if element_type > 12:
                raise _NBTError("NBT list uses an unsupported tag type")
            values = []
            for _index in range(count):
                self._count_tag()
                values.append(self._value(element_type, depth + 1))
            return values
        if tag_type == 10:
            values: dict[str, Any] = {}
            while True:
                element_type = self._u8()
                if element_type == 0:
                    return values
                if element_type > 12:
                    raise _NBTError("NBT compound uses an unsupported tag type")
                self._count_tag()
                name = self._name()
                if name in values:
                    raise _NBTError(f"NBT compound contains duplicate {name}")
                values[name] = self._value(element_type, depth + 1)
        if tag_type == 11:
            raw = self._int_array_bytes()
            return [
                int.from_bytes(raw[index:index + 4], "big", signed=True)
                for index in range(0, len(raw), 4)
            ]
        if tag_type == 12:
            count = self._i32()
            self._count_collection(count)
            return [self._i64() for _index in range(count)]
        raise _NBTError(f"NBT payload uses unsupported tag type {tag_type}")

    def root(self) -> dict[str, Any]:
        if self._u8() != 10:
            raise _NBTError("level.dat root is not a compound")
        self._name()
        result = self._value(10, 1)
        if self.offset != len(self.payload):
            raise _NBTError("level.dat has trailing bytes")
        if not isinstance(result, dict):
            raise _NBTError("level.dat root did not decode as a compound")
        return result


def _decompress_level_dat(raw: bytes) -> bytes:
    try:
        import zlib

        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        result = decoder.decompress(raw, MAX_LEVEL_DAT_DECOMPRESSED_BYTES + 1)
        if len(result) > MAX_LEVEL_DAT_DECOMPRESSED_BYTES or decoder.unconsumed_tail:
            raise _NBTError("level.dat exceeds the decompressed-byte limit")
        result += decoder.flush(MAX_LEVEL_DAT_DECOMPRESSED_BYTES + 1 - len(result))
    except zlib.error as exc:
        raise _NBTError("level.dat gzip stream is invalid") from exc
    if len(result) > MAX_LEVEL_DAT_DECOMPRESSED_BYTES:
        raise _NBTError("level.dat exceeds the decompressed-byte limit")
    if not decoder.eof or decoder.unused_data:
        raise _NBTError("level.dat gzip framing is invalid")
    return result


def _world_metadata(world: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    level_path = world / "level.dat"
    raw = _read_stable_file(level_path, MAX_LEVEL_DAT_BYTES)
    try:
        decoded = _ValueNBTReader(_decompress_level_dat(raw)).root()
    except _NBTError as exc:
        raise AtlasAnvilWorldgenFingerprintError(
            f"level.dat cannot provide trusted generation metadata: {exc}"
        ) from exc
    data = decoded.get("Data")
    if not isinstance(data, dict):
        raise AtlasAnvilWorldgenFingerprintError(
            "level.dat root does not contain a Data compound"
        )
    selected = {
        key: data.get(key)
        for key in (
            "LevelName",
            "RandomSeed",
            "generatorName",
            "generatorVersion",
            "generatorOptions",
            "GameType",
            "allowCommands",
            "SpawnX",
            "SpawnY",
            "SpawnZ",
        )
    }
    metadata = {
        "level_name": selected["LevelName"],
        "random_seed": selected["RandomSeed"],
        "generator_name": selected["generatorName"],
        "generator_version": selected["generatorVersion"],
        "generator_options": selected["generatorOptions"],
        "game_type": selected["GameType"],
        "allow_commands": selected["allowCommands"],
        "spawn": {
            "x": selected["SpawnX"],
            "y": selected["SpawnY"],
            "z": selected["SpawnZ"],
        },
    }
    metadata["generation_metadata_sha256"] = _digest(
        _canonical_bytes({
            key: metadata[key]
            for key in (
                "random_seed",
                "generator_name",
                "generator_version",
                "generator_options",
                "spawn",
            )
        })
    )
    evidence = {
        "evidence_id": "sha256:" + _digest(raw),
        "kind": "minecraft-level-dat",
        "path": "level.dat",
        "sha256": _digest(raw),
        "size": len(raw),
    }
    return metadata, evidence


def _dimension(relative_region_path: str) -> str:
    parent = PurePosixPath(relative_region_path).parent
    dimension_root = parent.parent
    return "overworld" if dimension_root.as_posix() == "." else dimension_root.as_posix()


def _fingerprint_chunk(
    raw: bytes,
    chunk: dict[str, Any],
) -> dict[str, Any]:
    start = chunk["sector_offset"] * SECTOR_BYTES
    payload_end = start + 4 + chunk["declared_length"]
    compression = raw[start + 4]
    try:
        decompressed = _decompress_chunk(compression, raw[start + 5:payload_end])
        result = _ChunkFingerprintReader(decompressed).result()
    except (AtlasAnvilRegionObservationError, _AnvilNBTError, _NBTError) as exc:
        raise AtlasAnvilWorldgenFingerprintError(
            f"validated chunk cannot be fingerprinted: {exc}"
        ) from exc
    expected = chunk.get("expected_coordinates")
    if not isinstance(expected, dict) or (
        result["x"], result["z"]
    ) != (expected.get("x"), expected.get("z")):
        raise AtlasAnvilWorldgenFingerprintError(
            "chunk coordinates changed after structural validation"
        )
    result["raw_nbt_sha256"] = _digest(decompressed)
    result["raw_nbt_size"] = len(decompressed)
    return result


def observe_anvil_worldgen_fingerprint(world_root: Path | str) -> dict[str, Any]:
    """Fingerprint stable generation-relevant fields in one stopped world."""

    selected = Path(world_root).expanduser()
    if selected.is_symlink():
        raise AtlasAnvilWorldgenFingerprintError(
            f"world root cannot be a symbolic link: {world_root}"
        )
    world = selected.resolve()
    if not world.is_dir():
        raise AtlasAnvilWorldgenFingerprintError(
            f"world root is not a directory: {world_root}"
        )
    try:
        structural = observe_anvil_region_world(world)
    except AtlasAnvilRegionObservationError as exc:
        raise AtlasAnvilWorldgenFingerprintError(str(exc)) from exc
    if structural.get("state") != "no-findings-observed":
        raise AtlasAnvilWorldgenFingerprintError(
            "worldgen fingerprint requires a finding-free structural observation"
        )
    metadata, level_evidence = _world_metadata(world)
    evidence_by_path = {
        item["path"]: item
        for item in structural["evidence"]
    }
    chunks: list[dict[str, Any]] = []
    dimensions: dict[str, int] = {}
    for region in structural["facts"]["regions"]:
        relative = region["path"]
        path = world.joinpath(*PurePosixPath(relative).parts)
        raw = _read_immutable_region(path)
        if _digest(raw) != evidence_by_path[relative]["sha256"]:
            raise AtlasAnvilWorldgenFingerprintError(
                f"Anvil region changed after structural validation: {relative}"
            )
        dimension = _dimension(relative)
        for structural_chunk in region["chunks"]:
            if structural_chunk.get("state") != "validated":
                raise AtlasAnvilWorldgenFingerprintError(
                    "structural observation contains an unvalidated chunk"
                )
            chunk = _fingerprint_chunk(raw, structural_chunk)
            chunk["dimension"] = dimension
            chunks.append(chunk)
            dimensions[dimension] = dimensions.get(dimension, 0) + 1
            if len(chunks) > MAX_CHUNKS:
                raise AtlasAnvilWorldgenFingerprintError(
                    "world exceeds the worldgen fingerprint chunk limit"
                )
    chunks.sort(key=lambda item: (item["dimension"], item["x"], item["z"]))
    summary = {
        "chunk_count": len(chunks),
        "dimensions": {key: dimensions[key] for key in sorted(dimensions)},
        "terrain_populated": sum(
            item["terrain_populated"] is True for item in chunks
        ),
        "terrain_unpopulated": sum(
            item["terrain_populated"] is False for item in chunks
        ),
        "terrain_state_missing": sum(
            item["terrain_populated"] is None for item in chunks
        ),
        "light_populated": sum(
            item["light_populated"] is True for item in chunks
        ),
        "light_unpopulated": sum(
            item["light_populated"] is False for item in chunks
        ),
        "light_state_missing": sum(
            item["light_populated"] is None for item in chunks
        ),
    }
    observation: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "fingerprint_id": "",
        "authority": {
            "classification": "atlas-derived-observation",
            "normative": False,
            "atlas_publication": False,
            "sentinel_policy_finding": False,
        },
        "maturity": "experimental",
        "operation_class": "read-only",
        "state": "fingerprinted" if chunks else "insufficient-evidence",
        "source": {"world_root_uri": world.as_uri()},
        "limits": {
            "maximum_chunks": MAX_CHUNKS,
            "maximum_decompressed_chunk_bytes": MAX_DECOMPRESSED_CHUNK_BYTES,
            "maximum_nbt_depth": MAX_NBT_DEPTH,
            "maximum_nbt_tags_per_payload": MAX_NBT_TAGS,
            "maximum_nbt_collection_entries_per_payload": (
                MAX_NBT_COLLECTION_ENTRIES
            ),
            "maximum_level_dat_bytes": MAX_LEVEL_DAT_BYTES,
            "maximum_level_dat_decompressed_bytes": (
                MAX_LEVEL_DAT_DECOMPRESSED_BYTES
            ),
        },
        "prerequisite": {
            "structural_observation_id": structural["observation_id"],
            "state": structural["state"],
        },
        "evidence": [level_evidence, *structural["evidence"]],
        "facts": {
            "metadata": metadata,
            "summary": summary,
            "chunks": chunks,
        },
        "limitations": [
            (
                "The fingerprint excludes entities, tile entities, scheduled "
                "ticks, inhabited time, and save timestamps."
            ),
            (
                "Saved block sections include both base terrain and completed "
                "population effects; V1 cannot reconstruct their execution order."
            ),
            (
                "Atlas detects per-file mutation but Workbench Shell must bind "
                "the observation to a stopped client lifecycle."
            ),
        ],
    }
    identity = dict(observation)
    identity.pop("fingerprint_id")
    observation["fingerprint_id"] = _identity(identity)
    return observation


def _validate_fingerprint(value: Any, label: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("format") != FORMAT
        or value.get("schema_version") != SCHEMA_VERSION
        or not isinstance(value.get("fingerprint_id"), str)
    ):
        raise AtlasAnvilWorldgenFingerprintError(
            f"{label} is not a worldgen fingerprint V1"
        )
    identity = dict(value)
    recorded = identity.pop("fingerprint_id")
    if recorded != _identity(identity):
        raise AtlasAnvilWorldgenFingerprintError(
            f"{label} fingerprint identity does not match its contents"
        )
    chunks = value.get("facts", {}).get("chunks")
    if not isinstance(chunks, list) or len(chunks) > MAX_CHUNKS:
        raise AtlasAnvilWorldgenFingerprintError(
            f"{label} fingerprint has an invalid chunk collection"
        )
    return value


def compare_anvil_worldgen_fingerprints(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Compare two identity-verified V1 worldgen fingerprints."""

    first = _validate_fingerprint(left, "left")
    second = _validate_fingerprint(right, "right")
    def index_chunks(value: dict[str, Any], label: str) -> dict[tuple[Any, Any, Any], dict[str, Any]]:
        indexed: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        for item in value["facts"]["chunks"]:
            if not isinstance(item, dict):
                raise AtlasAnvilWorldgenFingerprintError(
                    f"{label} fingerprint contains a non-object chunk"
                )
            key = (item.get("dimension"), item.get("x"), item.get("z"))
            if key in indexed:
                raise AtlasAnvilWorldgenFingerprintError(
                    f"{label} fingerprint contains duplicate chunk coordinates"
                )
            indexed[key] = item
        return indexed

    first_chunks = index_chunks(first, "left")
    second_chunks = index_chunks(second, "right")
    first_keys = set(first_chunks)
    second_keys = set(second_chunks)
    shared = sorted(first_keys & second_keys)
    components = (
        "worldgen_sha256",
        "block_content_sha256",
        "biome_sha256",
        "heightmap_sha256",
        "structure_sha256",
        "lighting_sha256",
        "terrain_populated",
        "light_populated",
    )
    equal_counts = {component: 0 for component in components}
    differences: list[dict[str, Any]] = []
    for key in shared:
        first_chunk = first_chunks[key]
        second_chunk = second_chunks[key]
        changed = []
        for component in components:
            if first_chunk.get(component) == second_chunk.get(component):
                equal_counts[component] += 1
            else:
                changed.append(component)
        if changed:
            if len(differences) >= MAX_COMPARISON_DIFFERENCES:
                raise AtlasAnvilWorldgenFingerprintError(
                    "comparison exceeds the difference-record limit"
                )
            differences.append({
                "dimension": key[0],
                "x": key[1],
                "z": key[2],
                "changed": changed,
            })
    first_metadata = first["facts"]["metadata"]
    second_metadata = second["facts"]["metadata"]
    metadata_fields = (
        "random_seed",
        "generator_name",
        "generator_version",
        "generator_options",
        "spawn",
    )
    metadata_equal = {
        field: first_metadata.get(field) == second_metadata.get(field)
        for field in metadata_fields
    }
    summary = {
        "left_chunk_count": len(first_keys),
        "right_chunk_count": len(second_keys),
        "shared_chunk_count": len(shared),
        "left_only_chunk_count": len(first_keys - second_keys),
        "right_only_chunk_count": len(second_keys - first_keys),
        "different_shared_chunk_count": len(differences),
        "equal_shared_chunks": {
            component: equal_counts[component]
            for component in components
        },
        "metadata_equal": metadata_equal,
    }
    comparison: dict[str, Any] = {
        "format": COMPARISON_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "comparison_id": "",
        "authority": {
            "classification": "atlas-derived-observation",
            "normative": False,
            "atlas_publication": False,
            "sentinel_policy_finding": False,
        },
        "maturity": "experimental",
        "operation_class": "read-only",
        "state": "compared" if shared else "insufficient-overlap",
        "inputs": {
            "left_fingerprint_id": first["fingerprint_id"],
            "right_fingerprint_id": second["fingerprint_id"],
        },
        "facts": {
            "summary": summary,
            "differences": differences,
        },
        "limitations": [
            (
                "Only chunks present in both inputs are compared; traversal "
                "differences are reported as left-only and right-only counts."
            ),
            (
                "A differing saved block fingerprint localizes output drift but "
                "does not identify which generator or population event caused it."
            ),
        ],
    }
    identity = dict(comparison)
    identity.pop("comparison_id")
    comparison["comparison_id"] = _identity(identity)
    return comparison


__all__ = [
    "AtlasAnvilWorldgenFingerprintError",
    "compare_anvil_worldgen_fingerprints",
    "observe_anvil_worldgen_fingerprint",
]
