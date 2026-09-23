"""Bounded block-state attribution for two stopped legacy Anvil worlds.

Worldgen fingerprints localize changed chunks without retaining game payloads.
This experimental follow-on reopens the same identity-bound region evidence and
counts exact legacy block-state transitions so a fixed-seed experiment can
distinguish terrain drift from ore, decoration, and other population effects.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from .experimental_anvil_worldgen_fingerprint import (
    AtlasAnvilWorldgenFingerprintError,
    MAX_LEVEL_DAT_BYTES,
    _ChunkFingerprintReader,
    _NBTError,
    _ValueNBTReader,
    _canonical_bytes,
    _decompress_chunk,
    _decompress_level_dat,
    _digest,
    _identity,
    _read_immutable_region,
    _read_stable_file,
    compare_anvil_worldgen_fingerprints,
    observe_anvil_worldgen_fingerprint,
)
from .experimental_anvil_region_observation import (
    AtlasAnvilRegionObservationError,
    SECTOR_BYTES,
    _NBTError as _AnvilNBTError,
)


FORMAT = "atlas-experimental-anvil-block-delta-v2"
SCHEMA_VERSION = 2
MAX_ANALYZED_SECTION_PAIRS = 65_536
MAX_CHANGED_BLOCK_POSITIONS = 100_000_000
MAX_UNIQUE_TRANSITIONS = 65_536
MAX_RETAINED_ATTRIBUTIONS = 512


class AtlasAnvilBlockDeltaError(ValueError):
    """Raised when stopped-world block attribution cannot be trusted."""


def _level_payload(world: Path, fingerprint: dict[str, Any]) -> dict[str, Any]:
    raw = _read_stable_file(world / "level.dat", MAX_LEVEL_DAT_BYTES)
    expected = {
        item.get("path"): item.get("sha256")
        for item in fingerprint.get("evidence", [])
        if isinstance(item, dict)
    }.get("level.dat")
    if expected != _digest(raw):
        raise AtlasAnvilBlockDeltaError(
            "level.dat changed after worldgen fingerprinting"
        )
    try:
        return _ValueNBTReader(_decompress_level_dat(raw)).root()
    except _NBTError as exc:
        raise AtlasAnvilBlockDeltaError(
            f"level.dat block registry cannot be decoded: {exc}"
        ) from exc


def _block_registry(
    world: Path,
    fingerprint: dict[str, Any],
) -> dict[int, str]:
    root = _level_payload(world, fingerprint)
    fml = root.get("FML")
    if fml is None:
        return {0: "minecraft:air"}
    if not isinstance(fml, dict):
        raise AtlasAnvilBlockDeltaError("level.dat FML registry is not a compound")
    registries = fml.get("Registries")
    if not isinstance(registries, dict):
        raise AtlasAnvilBlockDeltaError(
            "level.dat FML.Registries is not a compound"
        )
    block_registry = registries.get("minecraft:blocks")
    if not isinstance(block_registry, dict):
        raise AtlasAnvilBlockDeltaError(
            "level.dat block registry is not a compound"
        )
    ids = block_registry.get("ids")
    if ids is None:
        return {0: "minecraft:air"}
    if not isinstance(ids, list) or len(ids) > 4096:
        raise AtlasAnvilBlockDeltaError(
            "level.dat contains an invalid legacy block registry"
        )
    result = {0: "minecraft:air"}
    for item in ids:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("K"), str)
            or not isinstance(item.get("V"), int)
            or isinstance(item.get("V"), bool)
            or not 0 <= item["V"] <= 4095
        ):
            raise AtlasAnvilBlockDeltaError(
                "level.dat contains an invalid legacy block-registry entry"
            )
        numeric_id = item["V"]
        name = item["K"]
        previous = result.get(numeric_id)
        if previous is not None and previous != name:
            raise AtlasAnvilBlockDeltaError(
                "level.dat maps one legacy block ID to multiple names"
            )
        result[numeric_id] = name
    return result


class _WorldSections:
    def __init__(
        self,
        world: Path,
        fingerprint: dict[str, Any],
    ) -> None:
        self.world = world
        self.evidence = {
            item.get("path"): item.get("sha256")
            for item in fingerprint.get("evidence", [])
            if isinstance(item, dict)
        }
        self.regions: dict[str, bytes] = {}

    @staticmethod
    def _relative_region(dimension: str, x: int, z: int) -> str:
        region = f"region/r.{x // 32}.{z // 32}.mca"
        if dimension == "overworld":
            return region
        candidate = PurePosixPath(dimension)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.as_posix() != dimension
        ):
            raise AtlasAnvilBlockDeltaError(
                "fingerprint contains an unsafe dimension path"
            )
        return f"{dimension}/{region}"

    def _region(self, relative: str) -> bytes:
        cached = self.regions.get(relative)
        if cached is not None:
            return cached
        expected = self.evidence.get(relative)
        if not isinstance(expected, str):
            raise AtlasAnvilBlockDeltaError(
                f"fingerprint does not bind region evidence: {relative}"
            )
        path = self.world.joinpath(*PurePosixPath(relative).parts)
        try:
            raw = _read_immutable_region(path)
        except AtlasAnvilRegionObservationError as exc:
            raise AtlasAnvilBlockDeltaError(str(exc)) from exc
        if _digest(raw) != expected:
            raise AtlasAnvilBlockDeltaError(
                f"Anvil region changed after worldgen fingerprinting: {relative}"
            )
        self.regions[relative] = raw
        return raw

    def sections(
        self,
        dimension: str,
        x: int,
        z: int,
    ) -> dict[int, dict[str, Any]]:
        relative = self._relative_region(dimension, x, z)
        raw = self._region(relative)
        slot = (x & 31) + ((z & 31) * 32)
        location = raw[slot * 4:slot * 4 + 4]
        sector_offset = int.from_bytes(location[:3], "big")
        if sector_offset == 0:
            raise AtlasAnvilBlockDeltaError(
                f"fingerprinted chunk is absent from {relative}: {x},{z}"
            )
        start = sector_offset * SECTOR_BYTES
        declared_length = int.from_bytes(raw[start:start + 4], "big")
        payload_end = start + 4 + declared_length
        try:
            decompressed = _decompress_chunk(
                raw[start + 4],
                raw[start + 5:payload_end],
            )
            reader = _ChunkFingerprintReader(decompressed)
            result = reader.result()
        except (
            AtlasAnvilRegionObservationError,
            AtlasAnvilWorldgenFingerprintError,
            _AnvilNBTError,
            _NBTError,
        ) as exc:
            raise AtlasAnvilBlockDeltaError(
                f"fingerprinted chunk cannot be reopened: {exc}"
            ) from exc
        if (result["x"], result["z"]) != (x, z):
            raise AtlasAnvilBlockDeltaError(
                "chunk coordinates changed after worldgen fingerprinting"
            )
        return {item["y"]: item for item in reader.sections}


def _section_arrays(
    section: dict[str, Any] | None,
) -> tuple[bytes, bytes, bytes]:
    if section is None:
        return b"", b"", b""
    blocks = section.get("Blocks", b"")
    data = section.get("Data", b"")
    add = section.get("Add", b"")
    if len(blocks) not in {0, 4096}:
        raise AtlasAnvilBlockDeltaError(
            "chunk section Blocks array does not contain 4096 entries"
        )
    if len(data) not in {0, 2048}:
        raise AtlasAnvilBlockDeltaError(
            "chunk section Data array does not contain 2048 entries"
        )
    if len(add) not in {0, 2048}:
        raise AtlasAnvilBlockDeltaError(
            "chunk section Add array does not contain 2048 entries"
        )
    if not blocks and (data or add):
        raise AtlasAnvilBlockDeltaError(
            "chunk section has metadata without a Blocks array"
        )
    return blocks, data, add


def _nibble(payload: bytes, index: int) -> int:
    value = payload[index >> 1]
    return (value >> (4 if index & 1 else 0)) & 0x0F


def _state(
    arrays: tuple[bytes, bytes, bytes],
    index: int,
) -> tuple[int, int]:
    blocks, data, add = arrays
    if not blocks:
        return 0, 0
    numeric_id = blocks[index]
    if add:
        numeric_id |= _nibble(add, index) << 8
    metadata = _nibble(data, index) if data else 0
    return numeric_id, metadata


def _block_name(registry: dict[int, str], numeric_id: int) -> str:
    return registry.get(numeric_id, f"legacy-block:{numeric_id}")


def _ranked(counter: Counter[Any]) -> list[tuple[Any, int]]:
    return sorted(
        counter.items(),
        key=lambda item: (-item[1], _canonical_bytes(item[0])),
    )


def _observe_anvil_block_delta(
    left_world_root: Path | str,
    right_world_root: Path | str,
) -> dict[str, Any]:
    """Attribute saved block-state changes between two stopped worlds."""

    left_selected = Path(left_world_root).expanduser()
    right_selected = Path(right_world_root).expanduser()
    try:
        left = observe_anvil_worldgen_fingerprint(left_selected)
        right = observe_anvil_worldgen_fingerprint(right_selected)
        comparison = compare_anvil_worldgen_fingerprints(left, right)
    except AtlasAnvilWorldgenFingerprintError as exc:
        raise AtlasAnvilBlockDeltaError(str(exc)) from exc
    left_world = left_selected.resolve()
    right_world = right_selected.resolve()

    left_registry = _block_registry(left_world, left)
    right_registry = _block_registry(right_world, right)
    left_sections = _WorldSections(left_world, left)
    right_sections = _WorldSections(right_world, right)
    raw_candidates = [
        item
        for item in comparison["facts"]["differences"]
        if "block_content_sha256" in item["changed"]
    ]
    registries_equal = left_registry == right_registry
    if not registries_equal:
        left_keys = {
            (item["dimension"], item["x"], item["z"])
            for item in left["facts"]["chunks"]
        }
        right_keys = {
            (item["dimension"], item["x"], item["z"])
            for item in right["facts"]["chunks"]
        }
        candidates = [
            {"dimension": dimension, "x": x, "z": z}
            for dimension, x, z in sorted(left_keys & right_keys)
        ]
    else:
        candidates = raw_candidates

    transitions: Counter[tuple[int, int, str, int, int, str]] = Counter()
    blocks: Counter[str] = Counter()
    namespaces: Counter[str] = Counter()
    y_bands: Counter[int] = Counter()
    exact_changed_chunks = 0
    changed_positions = 0
    raw_numeric_state_different_positions = 0
    numeric_id_remap_only_positions = 0
    metadata_only_positions = 0
    analyzed_section_pairs = 0
    minimum_y: int | None = None
    maximum_y: int | None = None

    for chunk in candidates:
        dimension = chunk["dimension"]
        x = chunk["x"]
        z = chunk["z"]
        first = left_sections.sections(dimension, x, z)
        second = right_sections.sections(dimension, x, z)
        chunk_changed = False
        for section_y in sorted(set(first) | set(second)):
            left_arrays = _section_arrays(first.get(section_y))
            right_arrays = _section_arrays(second.get(section_y))
            if left_arrays == right_arrays and registries_equal:
                continue
            analyzed_section_pairs += 1
            if analyzed_section_pairs > MAX_ANALYZED_SECTION_PAIRS:
                raise AtlasAnvilBlockDeltaError(
                    "block attribution exceeds the analyzed-section limit"
                )
            for index in range(4096):
                left_state = _state(left_arrays, index)
                right_state = _state(right_arrays, index)
                raw_states_equal = left_state == right_state
                if not raw_states_equal:
                    raw_numeric_state_different_positions += 1
                left_name = _block_name(left_registry, left_state[0])
                right_name = _block_name(right_registry, right_state[0])
                semantic_states_equal = (
                    left_name,
                    left_state[1],
                ) == (
                    right_name,
                    right_state[1],
                )
                if semantic_states_equal:
                    if not raw_states_equal:
                        numeric_id_remap_only_positions += 1
                    continue
                chunk_changed = True
                changed_positions += 1
                if changed_positions > MAX_CHANGED_BLOCK_POSITIONS:
                    raise AtlasAnvilBlockDeltaError(
                        "block attribution exceeds the changed-position limit"
                    )
                if left_name == right_name:
                    metadata_only_positions += 1
                y = section_y * 16 + (index >> 8)
                minimum_y = y if minimum_y is None else min(minimum_y, y)
                maximum_y = y if maximum_y is None else max(maximum_y, y)
                y_bands[(y // 16) * 16] += 1
                transition = (
                    left_state[0],
                    left_state[1],
                    left_name,
                    right_state[0],
                    right_state[1],
                    right_name,
                )
                if transition not in transitions and (
                    len(transitions) >= MAX_UNIQUE_TRANSITIONS
                ):
                    raise AtlasAnvilBlockDeltaError(
                        "block attribution exceeds the transition-variant limit"
                    )
                transitions[transition] += 1
                blocks[left_name] += 1
                blocks[right_name] += 1
                involved = {
                    left_name.split(":", 1)[0],
                    right_name.split(":", 1)[0],
                }
                for namespace in involved:
                    namespaces[namespace] += 1
        if chunk_changed:
            exact_changed_chunks += 1

    retained_transitions = []
    for transition, count in _ranked(transitions)[:MAX_RETAINED_ATTRIBUTIONS]:
        left_id, left_meta, left_name, right_id, right_meta, right_name = transition
        retained_transitions.append({
            "count": count,
            "left": {
                "name": left_name,
                "numeric_id": left_id,
                "metadata": left_meta,
            },
            "right": {
                "name": right_name,
                "numeric_id": right_id,
                "metadata": right_meta,
            },
        })

    metadata = comparison["facts"]["summary"]["metadata_equal"]
    summary = {
        "shared_chunk_count": comparison["facts"]["summary"][
            "shared_chunk_count"
        ],
        "candidate_block_different_chunk_count": len(candidates),
        "exact_block_different_chunk_count": exact_changed_chunks,
        "analyzed_section_pair_count": analyzed_section_pairs,
        "changed_block_state_position_count": changed_positions,
        "metadata_only_position_count": metadata_only_positions,
        "changed_positions_below_y_80": sum(
            count for band, count in y_bands.items() if band < 80
        ),
        "minimum_changed_y": minimum_y,
        "maximum_changed_y": maximum_y,
        "unique_transition_count": len(transitions),
        "retained_transition_count": len(retained_transitions),
        "generation_metadata_equal": metadata,
        "block_registry_equal": registries_equal,
    }
    summary["candidate_semantic_scan_chunk_count"] = summary.pop(
        "candidate_block_different_chunk_count"
    )
    summary.update({
        "raw_block_different_chunk_count": len(raw_candidates),
        "raw_numeric_state_different_position_count": (
            raw_numeric_state_different_positions
        ),
        "numeric_id_remap_only_position_count": (
            numeric_id_remap_only_positions
        ),
    })
    observation: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "observation_id": "",
        "authority": {
            "classification": "atlas-derived-observation",
            "normative": False,
            "atlas_publication": False,
            "sentinel_policy_finding": False,
        },
        "maturity": "experimental",
        "operation_class": "read-only",
        "state": (
            "attributed" if changed_positions else "no-block-delta-observed"
        ),
        "inputs": {
            "left_world_root_uri": left_world.as_uri(),
            "right_world_root_uri": right_world.as_uri(),
            "left_fingerprint_id": left["fingerprint_id"],
            "right_fingerprint_id": right["fingerprint_id"],
            "comparison_id": comparison["comparison_id"],
        },
        "limits": {
            "maximum_analyzed_section_pairs": MAX_ANALYZED_SECTION_PAIRS,
            "maximum_changed_block_positions": MAX_CHANGED_BLOCK_POSITIONS,
            "maximum_unique_transitions": MAX_UNIQUE_TRANSITIONS,
            "maximum_retained_attributions_per_collection": (
                MAX_RETAINED_ATTRIBUTIONS
            ),
        },
        "facts": {
            "summary": summary,
            "changed_positions_by_y_band": [
                {"minimum_y": band, "maximum_y": band + 15, "count": count}
                for band, count in sorted(y_bands.items())
            ],
            "changed_position_namespace_involvement": [
                {"namespace": namespace, "count": count}
                for namespace, count in _ranked(namespaces)[
                    :MAX_RETAINED_ATTRIBUTIONS
                ]
            ],
            "changed_block_endpoint_occurrences": [
                {"block": block, "count": count}
                for block, count in _ranked(blocks)[
                    :MAX_RETAINED_ATTRIBUTIONS
                ]
            ],
            "block_state_transitions": retained_transitions,
        },
        "limitations": [
            (
                "A changed-position namespace count includes a namespace once "
                "when either transition endpoint belongs to it; it does not "
                "prove which generator placed the block."
            ),
            (
                "Block endpoint counts include both sides of every changed "
                "position and therefore can sum to twice the position count."
            ),
            (
                "The observation sees final saved states and cannot reconstruct event "
                "ordering or distinguish base generation from later population."
            ),
            (
                "Only the most frequent bounded attribution records are "
                "retained; summary counts cover every analyzed changed position."
            ),
        ],
    }
    observation["limitations"].append(
        "Resource-location and metadata states are compared across each "
        "world's saved Forge registry; raw numeric-ID remaps are reported "
        "separately and are not counted as semantic block changes."
    )
    identity = dict(observation)
    identity.pop("observation_id")
    observation["observation_id"] = _identity(identity)
    return observation


def observe_anvil_block_delta(
    left_world_root: Path | str,
    right_world_root: Path | str,
) -> dict[str, Any]:
    """Attribute semantic saved block-state changes between stopped worlds."""

    return _observe_anvil_block_delta(left_world_root, right_world_root)


__all__ = [
    "AtlasAnvilBlockDeltaError",
    "observe_anvil_block_delta",
]
