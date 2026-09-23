"""Focused tests for canonical Atlas Anvil worldgen fingerprints."""

from __future__ import annotations

import gzip
from pathlib import Path
import sys
import tempfile
import unittest
import zlib


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_atlas.experimental_anvil_worldgen_fingerprint import (  # noqa: E402
    AtlasAnvilWorldgenFingerprintError,
    compare_anvil_worldgen_fingerprints,
    observe_anvil_worldgen_fingerprint,
)
from workbench_atlas.experimental_anvil_block_delta import (  # noqa: E402
    observe_anvil_block_delta,
)


SECTOR_BYTES = 4096


def _named(tag_type: int, name: bytes, payload: bytes) -> bytes:
    return bytes([tag_type]) + len(name).to_bytes(2, "big") + name + payload


def _string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(2, "big") + encoded


def _byte_array(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big", signed=True) + payload


def _chunk_nbt(
    x: int,
    z: int,
    *,
    block: int = 1,
    last_update: int = 0,
    terrain_populated: bool = True,
) -> bytes:
    section = (
        _named(1, b"Y", b"\x00")
        + _named(7, b"Blocks", _byte_array(bytes([block]) * 4096))
        + _named(7, b"Data", _byte_array(b"\x00" * 2048))
        + _named(7, b"BlockLight", _byte_array(b"\x00" * 2048))
        + _named(7, b"SkyLight", _byte_array(b"\xff" * 2048))
        + b"\x00"
    )
    sections = b"\x0a" + (1).to_bytes(4, "big", signed=True) + section
    heightmap = (256).to_bytes(4, "big", signed=True) + b"".join(
        (64).to_bytes(4, "big", signed=True) for _index in range(256)
    )
    level = (
        _named(3, b"xPos", x.to_bytes(4, "big", signed=True))
        + _named(3, b"zPos", z.to_bytes(4, "big", signed=True))
        + _named(
            1,
            b"TerrainPopulated",
            b"\x01" if terrain_populated else b"\x00",
        )
        + _named(1, b"LightPopulated", b"\x01")
        + _named(4, b"LastUpdate", last_update.to_bytes(8, "big", signed=True))
        + _named(7, b"Biomes", _byte_array(bytes(range(256))))
        + _named(11, b"HeightMap", heightmap)
        + _named(9, b"Sections", sections)
        + _named(10, b"Structures", b"\x00")
        + b"\x00"
    )
    return b"\x0a\x00\x00" + _named(10, b"Level", level) + b"\x00"


def _level_dat(
    seed: int = 42,
    generator: str = "RTG",
    block_registry: dict[str, int] | None = None,
) -> bytes:
    data = (
        _named(8, b"LevelName", _string("fixture"))
        + _named(4, b"RandomSeed", seed.to_bytes(8, "big", signed=True))
        + _named(8, b"generatorName", _string(generator))
        + _named(3, b"generatorVersion", (0).to_bytes(4, "big", signed=True))
        + _named(8, b"generatorOptions", _string("{\"seaLevel\":63}"))
        + _named(3, b"GameType", (1).to_bytes(4, "big", signed=True))
        + _named(1, b"allowCommands", b"\x01")
        + _named(3, b"SpawnX", (8).to_bytes(4, "big", signed=True))
        + _named(3, b"SpawnY", (64).to_bytes(4, "big", signed=True))
        + _named(3, b"SpawnZ", (8).to_bytes(4, "big", signed=True))
        + b"\x00"
    )
    root = _named(10, b"Data", data)
    if block_registry is not None:
        entries = b"".join(
            _named(8, b"K", _string(name))
            + _named(3, b"V", numeric_id.to_bytes(4, "big", signed=True))
            + b"\x00"
            for name, numeric_id in sorted(block_registry.items())
        )
        ids = (
            b"\x0a"
            + len(block_registry).to_bytes(4, "big", signed=True)
            + entries
        )
        minecraft_blocks = _named(9, b"ids", ids) + b"\x00"
        registries = _named(
            10,
            b"minecraft:blocks",
            minecraft_blocks,
        ) + b"\x00"
        fml = _named(10, b"Registries", registries) + b"\x00"
        root += _named(10, b"FML", fml)
    return gzip.compress(b"\x0a\x00\x00" + root + b"\x00", mtime=0)


def _region(chunk_nbt: bytes) -> bytes:
    compressed = zlib.compress(chunk_nbt)
    body = b"\x02" + compressed
    sector_count = (4 + len(body) + SECTOR_BYTES - 1) // SECTOR_BYTES
    result = bytearray((2 + sector_count) * SECTOR_BYTES)
    result[0:4] = ((2 << 8) | sector_count).to_bytes(4, "big")
    start = 2 * SECTOR_BYTES
    result[start:start + 4] = len(body).to_bytes(4, "big")
    result[start + 4:start + 4 + len(body)] = body
    return bytes(result)


def _world(
    root: Path,
    name: str,
    *,
    block: int = 1,
    last_update: int = 0,
    terrain_populated: bool = True,
    seed: int = 42,
    block_registry: dict[str, int] | None = None,
) -> Path:
    world = root / name
    region = world / "region"
    region.mkdir(parents=True)
    (world / "level.dat").write_bytes(
        _level_dat(seed=seed, block_registry=block_registry)
    )
    (region / "r.0.0.mca").write_bytes(
        _region(
            _chunk_nbt(
                0,
                0,
                block=block,
                last_update=last_update,
                terrain_populated=terrain_populated,
            )
        )
    )
    return world


class ExperimentalAnvilWorldgenFingerprintTest(unittest.TestCase):
    def test_fingerprint_is_deterministic_and_binds_generation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = _world(Path(temporary), "world")

            first = observe_anvil_worldgen_fingerprint(world)
            second = observe_anvil_worldgen_fingerprint(world)

            self.assertEqual(first["fingerprint_id"], second["fingerprint_id"])
            self.assertEqual(first["facts"]["metadata"]["random_seed"], 42)
            self.assertEqual(first["facts"]["metadata"]["generator_name"], "RTG")
            self.assertEqual(first["facts"]["summary"]["chunk_count"], 1)
            chunk = first["facts"]["chunks"][0]
            self.assertTrue(chunk["terrain_populated"])
            self.assertEqual(chunk["observed_lengths"]["biomes"], 256)

    def test_runtime_only_chunk_fields_do_not_change_worldgen_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = observe_anvil_worldgen_fingerprint(
                _world(root, "first", last_update=1)
            )
            second = observe_anvil_worldgen_fingerprint(
                _world(root, "second", last_update=999)
            )

            left = first["facts"]["chunks"][0]
            right = second["facts"]["chunks"][0]
            self.assertNotEqual(left["raw_nbt_sha256"], right["raw_nbt_sha256"])
            self.assertEqual(left["worldgen_sha256"], right["worldgen_sha256"])

    def test_comparison_localizes_block_and_population_differences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = observe_anvil_worldgen_fingerprint(_world(root, "first"))
            second = observe_anvil_worldgen_fingerprint(
                _world(root, "second", block=2, terrain_populated=False)
            )

            comparison = compare_anvil_worldgen_fingerprints(first, second)

            summary = comparison["facts"]["summary"]
            self.assertEqual(summary["shared_chunk_count"], 1)
            self.assertEqual(summary["different_shared_chunk_count"], 1)
            self.assertEqual(
                comparison["facts"]["differences"][0]["changed"],
                [
                    "worldgen_sha256",
                    "block_content_sha256",
                    "terrain_populated",
                ],
            )

    def test_comparison_reports_seed_mismatch_without_hiding_chunk_equality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = observe_anvil_worldgen_fingerprint(_world(root, "first"))
            second = observe_anvil_worldgen_fingerprint(
                _world(root, "second", seed=43)
            )

            summary = compare_anvil_worldgen_fingerprints(
                first,
                second,
            )["facts"]["summary"]

            self.assertFalse(summary["metadata_equal"]["random_seed"])
            self.assertEqual(summary["equal_shared_chunks"]["worldgen_sha256"], 1)

    def test_tampered_fingerprint_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            value = observe_anvil_worldgen_fingerprint(
                _world(Path(temporary), "world")
            )
            value["facts"]["metadata"]["random_seed"] = 99

            with self.assertRaisesRegex(
                AtlasAnvilWorldgenFingerprintError,
                "identity",
            ):
                compare_anvil_worldgen_fingerprints(value, value)

    def test_block_delta_attributes_exact_legacy_state_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = _world(root, "left", block=1)
            right = _world(root, "right", block=2)

            result = observe_anvil_block_delta(left, right)

            summary = result["facts"]["summary"]
            self.assertEqual(result["state"], "attributed")
            self.assertEqual(
                result["format"],
                "atlas-experimental-anvil-block-delta-v2",
            )
            self.assertEqual(
                summary["changed_block_state_position_count"],
                4096,
            )
            self.assertEqual(summary["changed_positions_below_y_80"], 4096)
            self.assertEqual(summary["minimum_changed_y"], 0)
            self.assertEqual(summary["maximum_changed_y"], 15)
            transition = result["facts"]["block_state_transitions"][0]
            self.assertEqual(transition["count"], 4096)
            self.assertEqual(transition["left"]["name"], "legacy-block:1")
            self.assertEqual(transition["right"]["name"], "legacy-block:2")

    def test_semantic_block_delta_excludes_numeric_registry_remaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = _world(
                root,
                "left",
                block=1,
                block_registry={"fixture:same_block": 1},
            )
            right = _world(
                root,
                "right",
                block=2,
                block_registry={"fixture:same_block": 2},
            )

            semantic = observe_anvil_block_delta(left, right)
            semantic_summary = semantic["facts"]["summary"]
            self.assertEqual(
                semantic_summary["changed_block_state_position_count"],
                0,
            )
            self.assertEqual(
                semantic_summary["numeric_id_remap_only_position_count"],
                4096,
            )

    def test_block_delta_reports_no_change_for_equal_worlds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = _world(Path(temporary), "world")

            result = observe_anvil_block_delta(world, world)

            self.assertEqual(result["state"], "no-block-delta-observed")
            self.assertEqual(
                result["facts"]["summary"][
                    "changed_block_state_position_count"
                ],
                0,
            )


if __name__ == "__main__":
    unittest.main()
