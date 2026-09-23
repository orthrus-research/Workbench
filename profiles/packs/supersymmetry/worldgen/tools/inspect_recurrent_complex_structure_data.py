#!/usr/bin/env python3
"""Inspect exact Recurrent Complex structure data for one bounded fixture region.

This profile-owned tool interprets RC's saved-data shape. Crucible consumes only
its small content-addressed observation and does not learn RC packages, private
registries, or structure formats.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from typing import Any


FORMAT = "workbench-supersymmetry-recurrent-complex-structure-observation-v1"
ID_PREFIX = "supersymmetry-recurrent-complex-structure-observation:sha256:"
MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COLLECTION_LENGTH = 10_000_000
MAX_DEPTH = 128


class ObservationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ObservationError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _file_bytes(path: Path, *, maximum: int, context: str) -> tuple[bytes, str]:
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode), f"{context} must be a regular file")
    _require(not stat.S_ISLNK(before.st_mode), f"{context} must not be a symlink")
    _require(0 < before.st_size <= maximum, f"{context} size is outside bounds")
    encoded = path.read_bytes()
    after = path.lstat()
    _require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        f"{context} changed while read",
    )
    return encoded, hashlib.sha256(encoded).hexdigest()


class _NbtReader:
    def __init__(self, encoded: bytes) -> None:
        self.encoded = encoded
        self.offset = 0

    def _take(self, size: int) -> bytes:
        _require(size >= 0, "negative NBT length")
        end = self.offset + size
        _require(end <= len(self.encoded), "truncated NBT payload")
        value = self.encoded[self.offset:end]
        self.offset = end
        return value

    def _number(self, template: str, size: int) -> int | float:
        return struct.unpack(template, self._take(size))[0]

    def _length(self) -> int:
        value = int(self._number(">i", 4))
        _require(0 <= value <= MAX_COLLECTION_LENGTH, "NBT collection length is outside bounds")
        return value

    def _string(self) -> str:
        size = int(self._number(">H", 2))
        try:
            return self._take(size).decode("utf-8")
        except UnicodeError as exc:
            raise ObservationError(f"invalid NBT UTF-8 string: {exc}") from exc

    def _payload(self, tag: int, depth: int) -> Any:
        _require(depth <= MAX_DEPTH, "NBT nesting exceeds bound")
        if tag == 1:
            return int(self._number(">b", 1))
        if tag == 2:
            return int(self._number(">h", 2))
        if tag == 3:
            return int(self._number(">i", 4))
        if tag == 4:
            return int(self._number(">q", 8))
        if tag == 5:
            return float(self._number(">f", 4))
        if tag == 6:
            return float(self._number(">d", 8))
        if tag == 7:
            return self._take(self._length())
        if tag == 8:
            return self._string()
        if tag == 9:
            element_tag = self._take(1)[0]
            length = self._length()
            _require(element_tag != 0 or length == 0, "nonempty NBT list has end-tag elements")
            return [self._payload(element_tag, depth + 1) for _ in range(length)]
        if tag == 10:
            result: dict[str, Any] = {}
            while True:
                element_tag = self._take(1)[0]
                if element_tag == 0:
                    return result
                name = self._string()
                _require(name not in result, f"duplicate NBT compound key {name!r}")
                result[name] = self._payload(element_tag, depth + 1)
        if tag == 11:
            return [int(self._number(">i", 4)) for _ in range(self._length())]
        if tag == 12:
            return [int(self._number(">q", 8)) for _ in range(self._length())]
        raise ObservationError(f"unsupported NBT tag {tag}")

    def read_root(self) -> Any:
        tag = self._take(1)[0]
        _require(tag == 10, "NBT root must be a compound")
        self._string()
        value = self._payload(tag, 0)
        _require(self.offset == len(self.encoded), "trailing bytes after NBT root")
        return value


def _load_nbt(compressed: bytes) -> dict[str, Any]:
    try:
        decoded = gzip.decompress(compressed)
    except (OSError, EOFError) as exc:
        raise ObservationError(f"cannot decompress RC structure data: {exc}") from exc
    _require(0 < len(decoded) <= MAX_DECOMPRESSED_BYTES, "decompressed NBT size is outside bounds")
    value = _NbtReader(decoded).read_root()
    _require(isinstance(value, dict), "NBT root is not a compound")
    return value


def _integer(value: Any, context: str) -> int:
    _require(type(value) is int, f"{context} must be an integer")
    return value


def _overlaps_selection(box: list[int], anchor_x: int, anchor_z: int, size: int) -> bool:
    min_chunk_x = box[0] // 16
    min_chunk_z = box[2] // 16
    max_chunk_x = box[3] // 16
    max_chunk_z = box[5] // 16
    return not (
        max_chunk_x < anchor_x
        or min_chunk_x >= anchor_x + size
        or max_chunk_z < anchor_z
        or min_chunk_z >= anchor_z + size
    )


def inspect(
    artifact: Path,
    data_file: Path,
    *,
    anchor_x: int,
    anchor_z: int,
    route_size: int,
    required_count: int,
) -> dict[str, Any]:
    _require(2 <= route_size <= 32, "route size must be between 2 and 32")
    _require(required_count >= 0, "required count must be nonnegative")
    artifact_bytes, artifact_sha256 = _file_bytes(
        artifact, maximum=1024 * 1024 * 1024, context="RC artifact"
    )
    data_bytes, data_sha256 = _file_bytes(
        data_file, maximum=MAX_COMPRESSED_BYTES, context="RC structure data"
    )
    root = _load_nbt(data_bytes)
    data = root.get("data")
    _require(isinstance(data, dict), "RC structure data lacks its data compound")
    entries = data.get("entries")
    checked = data.get("checkedChunks")
    checked_final = data.get("checkedChunksFinal")
    _require(isinstance(entries, list), "RC structure entries must be a list")
    _require(isinstance(checked, list), "RC checkedChunks must be a list")
    _require(isinstance(checked_final, list), "RC checkedChunksFinal must be a list")

    selected: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(entries):
        _require(isinstance(raw_entry, dict), f"RC entry {index} must be a compound")
        structure_id = raw_entry.get("structureID")
        generation_id = raw_entry.get("generationInfoID")
        box = raw_entry.get("boundingBox")
        _require(isinstance(structure_id, str) and bool(structure_id), f"RC entry {index} structure ID is invalid")
        _require(isinstance(generation_id, str) and bool(generation_id), f"RC entry {index} generation ID is invalid")
        _require(
            isinstance(box, list) and len(box) == 6 and all(type(item) is int for item in box),
            f"RC entry {index} bounding box is invalid",
        )
        if generation_id.startswith("decoration_") or not _overlaps_selection(
            box, anchor_x, anchor_z, route_size
        ):
            continue
        selected.append(
            {
                "structure_id": structure_id,
                "generation_info_id": generation_id,
                "bounding_box": box,
                "covered_chunk_bounds": [box[0] // 16, box[2] // 16, box[3] // 16, box[5] // 16],
                "seed": _integer(raw_entry.get("seed"), f"RC entry {index} seed"),
                "first_time": bool(_integer(raw_entry.get("firstTime"), f"RC entry {index} firstTime")),
                "blocking": bool(_integer(raw_entry.get("blocking"), f"RC entry {index} blocking")),
                "prevent_complementation": bool(
                    _integer(raw_entry.get("preventComplementation"), f"RC entry {index} preventComplementation")
                ),
            }
        )
    selected.sort(
        key=lambda row: (
            row["structure_id"],
            row["generation_info_id"],
            row["bounding_box"],
            row["seed"],
        )
    )
    _require(
        len(selected) == required_count,
        f"expected {required_count} ordinary overlapping RC entries, found {len(selected)}",
    )
    receipt: dict[str, Any] = {
        "format": FORMAT,
        "observation_id": None,
        "artifact_sha256": artifact_sha256,
        "artifact_size_bytes": len(artifact_bytes),
        "data_file_sha256": data_sha256,
        "data_file_size_bytes": len(data_bytes),
        "selection": {
            "anchor_chunk_x": anchor_x,
            "anchor_chunk_z": anchor_z,
            "route_size": route_size,
        },
        "entry_count": len(entries),
        "checked_chunk_count": len(checked),
        "checked_final_chunk_count": len(checked_final),
        "ordinary_overlapping_entries": selected,
    }
    identity = hashlib.sha256(_canonical_bytes({**receipt, "observation_id": None})).hexdigest()
    receipt["observation_id"] = ID_PREFIX + identity
    return receipt


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--anchor-chunk-x", type=int, required=True)
    parser.add_argument("--anchor-chunk-z", type=int, required=True)
    parser.add_argument("--route-size", type=int, required=True)
    parser.add_argument("--require-count", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    receipt = inspect(
        arguments.artifact,
        arguments.data,
        anchor_x=arguments.anchor_chunk_x,
        anchor_z=arguments.anchor_chunk_z,
        route_size=arguments.route_size,
        required_count=arguments.require_count,
    )
    _write(arguments.output, receipt)
    print(receipt["observation_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
