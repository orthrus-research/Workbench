"""Focused tests for the experimental Atlas Anvil region observer."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_atlas import (  # noqa: E402
    experimental_anvil_region_observation as anvil,
)
from workbench_atlas.experimental_anvil_region_observation import (  # noqa: E402
    FORMAT,
    observe_anvil_region_world,
)


SECTOR_BYTES = 4096


def _named(tag_type: int, name: bytes, payload: bytes) -> bytes:
    return bytes([tag_type]) + len(name).to_bytes(2, "big") + name + payload


def _chunk_nbt(x: int, z: int) -> bytes:
    level = (
        _named(3, b"xPos", x.to_bytes(4, "big", signed=True))
        + _named(3, b"zPos", z.to_bytes(4, "big", signed=True))
        + b"\x00"
    )
    return b"\x0a\x00\x00" + _named(10, b"Level", level) + b"\x00"


def _chunk_nbt_with_nested_level_decoy(x: int, z: int) -> bytes:
    decoy_level = (
        _named(3, b"xPos", (999).to_bytes(4, "big", signed=True))
        + _named(3, b"zPos", (-999).to_bytes(4, "big", signed=True))
        + b"\x00"
    )
    unrelated = _named(10, b"Level", decoy_level) + b"\x00"
    real_level = (
        _named(3, b"xPos", x.to_bytes(4, "big", signed=True))
        + _named(3, b"zPos", z.to_bytes(4, "big", signed=True))
        + b"\x00"
    )
    return (
        b"\x0a\x00\x00"
        + _named(10, b"Unrelated", unrelated)
        + _named(10, b"Level", real_level)
        + b"\x00"
    )


def _region(
    chunks: dict[int, tuple[int, int, bytes]],
    *,
    sectors: int | None = None,
) -> bytes:
    """Build slots as ``slot: (offset, compression, compressed bytes)``."""

    header = bytearray(8192)
    required = 2
    encoded: list[tuple[int, bytes]] = []
    for slot, (offset, compression, payload) in sorted(chunks.items()):
        body = bytes([compression]) + payload
        sector_count = (4 + len(body) + SECTOR_BYTES - 1) // SECTOR_BYTES
        location = (offset << 8) | sector_count
        header[slot * 4:slot * 4 + 4] = location.to_bytes(4, "big")
        encoded.append((offset, len(body).to_bytes(4, "big") + body))
        required = max(required, offset + sector_count)
    result = bytearray((sectors if sectors is not None else required) * SECTOR_BYTES)
    result[:8192] = header
    for offset, payload in encoded:
        start = offset * SECTOR_BYTES
        result[start:start + len(payload)] = payload
    return bytes(result)


def _categories(observation: dict[str, object]) -> set[str]:
    return {item["category"] for item in observation["findings"]}


class ExperimentalAnvilRegionObservationTest(unittest.TestCase):
    def test_decompression_limit_is_enforced_before_unbounded_output(self) -> None:
        compressed = zlib.compress(b"x" * 65)

        with patch.object(anvil, "MAX_DECOMPRESSED_CHUNK_BYTES", 64):
            with self.assertRaisesRegex(anvil._NBTError, "byte limit"):
                anvil._decompress_chunk(2, compressed)

    def test_nbt_collection_entry_limit_is_enforced(self) -> None:
        oversized_array = (2).to_bytes(4, "big", signed=True) + b"ab"
        payload = (
            b"\x0a\x00\x00"
            + _named(7, b"Array", oversized_array)
            + _named(
                10,
                b"Level",
                _named(3, b"xPos", (0).to_bytes(4, "big", signed=True))
                + _named(3, b"zPos", (0).to_bytes(4, "big", signed=True))
                + b"\x00",
            )
            + b"\x00"
        )

        with patch.object(anvil, "MAX_NBT_COLLECTION_ENTRIES", 1):
            with self.assertRaisesRegex(anvil._NBTError, "collection-entry limit"):
                anvil._NBTReader(payload).coordinates()

    def test_valid_region_is_deterministic_and_validates_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            compressed = zlib.compress(_chunk_nbt(0, 0))
            (region_root / "r.0.0.mca").write_bytes(
                _region({0: (2, 2, compressed)})
            )

            first = observe_anvil_region_world(world)
            second = observe_anvil_region_world(world)

            self.assertEqual(first["format"], FORMAT)
            self.assertEqual(first["state"], "no-findings-observed")
            self.assertEqual(first["observation_id"], second["observation_id"])
            self.assertEqual(first["facts"]["summary"]["validated_chunk_count"], 1)
            chunk = first["facts"]["regions"][0]["chunks"][0]
            self.assertEqual(chunk["state"], "validated")
            self.assertEqual(chunk["level_coordinates"], {"x": 0, "z": 0})

    def test_nested_level_decoy_does_not_shadow_root_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            compressed = zlib.compress(_chunk_nbt_with_nested_level_decoy(0, 0))
            (region_root / "r.0.0.mca").write_bytes(
                _region({0: (2, 2, compressed)})
            )

            result = observe_anvil_region_world(world)

            self.assertEqual(result["state"], "no-findings-observed")
            chunk = result["facts"]["regions"][0]["chunks"][0]
            self.assertEqual(chunk["state"], "validated")
            self.assertEqual(chunk["level_coordinates"], {"x": 0, "z": 0})

    def test_out_of_bounds_allocation_is_a_structural_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            raw = bytearray(3 * SECTOR_BYTES)
            raw[0:4] = ((3 << 8) | 1).to_bytes(4, "big")
            (region_root / "r.0.0.mca").write_bytes(raw)

            result = observe_anvil_region_world(world)

            self.assertIn("out-of-bounds-sector-allocation", _categories(result))
            self.assertEqual(
                result["facts"]["regions"][0]["chunks"][0]["state"],
                "structural-error",
            )

    def test_payload_length_must_fit_its_allocated_sectors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            raw = bytearray(3 * SECTOR_BYTES)
            raw[0:4] = ((2 << 8) | 1).to_bytes(4, "big")
            raw[2 * SECTOR_BYTES:2 * SECTOR_BYTES + 4] = (
                SECTOR_BYTES
            ).to_bytes(4, "big")
            (region_root / "r.0.0.mca").write_bytes(raw)

            result = observe_anvil_region_world(world)

            self.assertIn("invalid-chunk-payload-length", _categories(result))
            self.assertEqual(
                result["facts"]["regions"][0]["chunks"][0]["state"],
                "structural-error",
            )

    def test_invalid_compression_stream_is_retained_as_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            (region_root / "r.0.0.mca").write_bytes(
                _region({0: (2, 2, b"not-a-zlib-stream")})
            )

            result = observe_anvil_region_world(world)

            self.assertIn("chunk-decompression-failed", _categories(result))
            self.assertEqual(
                result["facts"]["regions"][0]["chunks"][0]["state"],
                "decompression-error",
            )

    def test_overlapping_allocations_invalidate_both_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            compressed = zlib.compress(_chunk_nbt(0, 0))
            raw = bytearray(_region({0: (2, 2, compressed)}))
            raw[4:8] = raw[0:4]
            (region_root / "r.0.0.mca").write_bytes(raw)

            result = observe_anvil_region_world(world)

            self.assertIn("overlapping-sector-allocation", _categories(result))
            states = [
                chunk["state"]
                for chunk in result["facts"]["regions"][0]["chunks"]
            ]
            self.assertEqual(states, ["structural-error", "structural-error"])

    def test_level_coordinates_must_match_region_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            region_root = world / "region"
            region_root.mkdir(parents=True)
            compressed = zlib.compress(_chunk_nbt(99, -4))
            (region_root / "r.0.0.mca").write_bytes(
                _region({0: (2, 2, compressed)})
            )

            result = observe_anvil_region_world(world)

            self.assertIn("chunk-coordinate-mismatch", _categories(result))
            finding = next(
                item
                for item in result["findings"]
                if item["category"] == "chunk-coordinate-mismatch"
            )
            self.assertEqual(finding["facts"]["observed"], {"x": 99, "z": -4})
            self.assertEqual(finding["facts"]["expected"], {"x": 0, "z": 0})


if __name__ == "__main__":
    unittest.main()
