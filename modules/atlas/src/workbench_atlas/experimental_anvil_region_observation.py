"""Experimental, read-only normalization of Minecraft Anvil region files.

The observer intentionally accepts only quiescent world evidence.  It never
writes the world and detects a file that changes while it is being read, but a
future Shell binding must establish that Minecraft is stopped before calling
it.  Limits are deliberately explicit so a corrupt region or NBT payload
cannot turn an audit into unbounded allocation or recursion.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any
import zlib


FORMAT = "atlas-experimental-anvil-region-observation-v1"
SCHEMA_VERSION = 1
SECTOR_BYTES = 4096
HEADER_BYTES = 2 * SECTOR_BYTES
SLOTS_PER_REGION = 1024

# Structural/resource limits are also emitted in every observation.
MAX_REGION_FILES = 512
MAX_REGION_BYTES = 256 * 1024 * 1024
MAX_TOTAL_REGION_BYTES = 1024 * 1024 * 1024
MAX_CHUNKS = 65_536
MAX_FINDINGS = 10_000
MAX_DECOMPRESSED_CHUNK_BYTES = 16 * 1024 * 1024
MAX_NBT_DEPTH = 64
MAX_NBT_TAGS = 100_000
MAX_NBT_COLLECTION_ENTRIES = 1_000_000
MAX_DISCOVERY_DIRECTORIES = 100_000

REGION_NAME_RE = re.compile(r"^r\.(-?\d+)\.(-?\d+)\.mca$")


class AtlasAnvilRegionObservationError(ValueError):
    """Raised when world evidence cannot be inspected within safe bounds."""


class _NBTError(ValueError):
    """Bounded NBT decoding failure with a stable diagnostic reason."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _limits() -> dict[str, int]:
    return {
        "maximum_region_files": MAX_REGION_FILES,
        "maximum_region_bytes": MAX_REGION_BYTES,
        "maximum_total_region_bytes": MAX_TOTAL_REGION_BYTES,
        "maximum_chunks": MAX_CHUNKS,
        "maximum_findings": MAX_FINDINGS,
        "maximum_decompressed_chunk_bytes": MAX_DECOMPRESSED_CHUNK_BYTES,
        "maximum_nbt_depth": MAX_NBT_DEPTH,
        "maximum_nbt_tags_per_chunk": MAX_NBT_TAGS,
        "maximum_nbt_collection_entries_per_chunk": (
            MAX_NBT_COLLECTION_ENTRIES
        ),
        "maximum_discovery_directories": MAX_DISCOVERY_DIRECTORIES,
    }


def _discover_region_files(world: Path) -> list[Path]:
    """Find region files without following directory symbolic links."""

    found: list[Path] = []
    directory_count = 0

    def walk_error(error: OSError) -> None:
        raise AtlasAnvilRegionObservationError(
            f"cannot enumerate Anvil world evidence: {error.filename}"
        ) from error

    for base_text, directory_names, file_names in os.walk(
        world,
        topdown=True,
        onerror=walk_error,
        followlinks=False,
    ):
        base = Path(base_text)
        directory_count += 1
        if directory_count > MAX_DISCOVERY_DIRECTORIES:
            raise AtlasAnvilRegionObservationError(
                "world exceeds the Anvil discovery directory limit"
            )
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            candidate = base / name
            if candidate.is_symlink():
                raise AtlasAnvilRegionObservationError(
                    f"world tree contains a symbolic-link directory: "
                    f"{candidate.relative_to(world).as_posix()}"
                )
        if base.name != "region" and base != world:
            continue
        # A caller may select a region directory directly. Otherwise only
        # conventional directories named ``region`` contribute evidence.
        if base != world or world.name == "region":
            for name in file_names:
                if not name.casefold().endswith(".mca"):
                    continue
                candidate = base / name
                if candidate.is_symlink() or not candidate.is_file():
                    raise AtlasAnvilRegionObservationError(
                        "Anvil evidence is not a regular file: "
                        f"{candidate.relative_to(world).as_posix()}"
                    )
                found.append(candidate)
                if len(found) > MAX_REGION_FILES:
                    raise AtlasAnvilRegionObservationError(
                        "world exceeds the Anvil region-file limit"
                    )
    return sorted(found, key=lambda item: item.relative_to(world).as_posix())


def _read_immutable_region(path: Path) -> bytes:
    """Read one regular file once and reject concurrent mutation."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise AtlasAnvilRegionObservationError(
            f"cannot open Anvil region evidence: {path}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AtlasAnvilRegionObservationError(
                f"Anvil evidence is not a regular file: {path}"
            )
        if before.st_size > MAX_REGION_BYTES:
            raise AtlasAnvilRegionObservationError(
                f"Anvil region exceeds the {MAX_REGION_BYTES}-byte limit"
            )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                raise AtlasAnvilRegionObservationError(
                    f"Anvil region ended during its immutable read: {path}"
                )
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise AtlasAnvilRegionObservationError(
            f"cannot read Anvil region evidence: {path}"
        ) from exc
    finally:
        os.close(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after:
        raise AtlasAnvilRegionObservationError(
            f"Anvil region changed while it was being read: {path}"
        )
    return b"".join(chunks)


class _NBTReader:
    """Small bounded NBT reader that extracts only Level chunk coordinates."""

    _FIXED_SIZES = {
        1: 1,  # byte
        2: 2,  # short
        3: 4,  # int
        4: 8,  # long
        5: 4,  # float
        6: 8,  # double
    }

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.tags = 0
        self.collection_entries = 0
        self.level_seen = False
        self.coordinate_values: dict[bytes, int] = {}
        self.coordinate_types: dict[bytes, int] = {}

    def _take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.payload):
            raise _NBTError("NBT payload ends before a declared value")
        start = self.offset
        self.offset += count
        return self.payload[start:self.offset]

    def _u8(self) -> int:
        return self._take(1)[0]

    def _u16(self) -> int:
        return int.from_bytes(self._take(2), "big", signed=False)

    def _i32(self) -> int:
        return int.from_bytes(self._take(4), "big", signed=True)

    def _name(self) -> bytes:
        return self._take(self._u16())

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

    def _skip_payload(self, tag_type: int, depth: int) -> None:
        if depth > MAX_NBT_DEPTH:
            raise _NBTError("NBT payload exceeds the nesting-depth limit")
        fixed = self._FIXED_SIZES.get(tag_type)
        if fixed is not None:
            self._take(fixed)
            return
        if tag_type == 7:  # byte array
            count = self._i32()
            self._count_collection(count)
            self._take(count)
            return
        if tag_type == 8:  # string
            self._take(self._u16())
            return
        if tag_type == 9:  # list
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
        if tag_type == 10:  # compound
            # ``depth`` already describes this child payload. Do not count a
            # compound twice merely because its framing is handled here.
            self._compound(depth, context="other")
            return
        if tag_type in (11, 12):  # int array / long array
            count = self._i32()
            self._count_collection(count)
            self._take(count * (4 if tag_type == 11 else 8))
            return
        raise _NBTError(f"NBT payload uses unsupported tag type {tag_type}")

    def _compound(self, depth: int, *, context: str) -> None:
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
            name = self._name()
            # Only a direct child of the root compound is the legacy chunk
            # Level container. A nested, unrelated tag named Level is data to
            # skip, not a second coordinate authority.
            if context == "root" and name == b"Level":
                if self.level_seen:
                    raise _NBTError("NBT root contains duplicate Level tags")
                self.level_seen = True
                if tag_type != 10:
                    self.coordinate_types[b"Level"] = tag_type
                    self._skip_payload(tag_type, depth + 1)
                else:
                    self._compound(depth + 1, context="level")
                continue
            if context == "level" and name in (b"xPos", b"zPos"):
                if name in self.coordinate_types:
                    raise _NBTError(
                        "NBT Level contains a duplicate chunk coordinate"
                    )
                self.coordinate_types[name] = tag_type
                if tag_type == 3:
                    self.coordinate_values[name] = self._i32()
                else:
                    self._skip_payload(tag_type, depth + 1)
                continue
            self._skip_payload(tag_type, depth + 1)

    def coordinates(self) -> tuple[int, int]:
        root_type = self._u8()
        if root_type != 10:
            raise _NBTError("NBT root is not a compound tag")
        self._name()
        self._compound(1, context="root")
        if self.offset != len(self.payload):
            raise _NBTError("NBT payload has trailing bytes after its root")
        if not self.level_seen:
            raise _NBTError("NBT root does not contain Level")
        if self.coordinate_types.get(b"Level") is not None:
            raise _NBTError("NBT Level tag is not a compound")
        for name in (b"xPos", b"zPos"):
            if name not in self.coordinate_types:
                raise _NBTError(
                    f"NBT Level does not contain {name.decode('ascii')}"
                )
            if self.coordinate_types[name] != 3:
                raise _NBTError(
                    f"NBT Level {name.decode('ascii')} is not TAG_Int"
                )
        return self.coordinate_values[b"xPos"], self.coordinate_values[b"zPos"]


def _decompress_chunk(compression: int, payload: bytes) -> bytes:
    if compression == 3:
        if len(payload) > MAX_DECOMPRESSED_CHUNK_BYTES:
            raise _NBTError("uncompressed chunk exceeds the byte limit")
        return payload
    if compression not in (1, 2):
        raise _NBTError(f"unsupported Anvil compression type {compression}")
    window_bits = 16 + zlib.MAX_WBITS if compression == 1 else zlib.MAX_WBITS
    try:
        decoder = zlib.decompressobj(window_bits)
        result = decoder.decompress(
            payload,
            MAX_DECOMPRESSED_CHUNK_BYTES + 1,
        )
        if len(result) > MAX_DECOMPRESSED_CHUNK_BYTES or decoder.unconsumed_tail:
            raise _NBTError("decompressed chunk exceeds the byte limit")
        result += decoder.flush(
            MAX_DECOMPRESSED_CHUNK_BYTES + 1 - len(result)
        )
    except zlib.error as exc:
        raise _NBTError("chunk compression stream is invalid") from exc
    if len(result) > MAX_DECOMPRESSED_CHUNK_BYTES:
        raise _NBTError("decompressed chunk exceeds the byte limit")
    if not decoder.eof:
        raise _NBTError("chunk compression stream is truncated")
    if decoder.unused_data:
        raise _NBTError("chunk compression stream has trailing data")
    return result


def _finding(
    findings: list[dict[str, Any]],
    *,
    category: str,
    relative_path: str,
    summary: str,
    evidence_id: str,
    slot: int | None = None,
    facts: dict[str, Any] | None = None,
) -> None:
    if len(findings) >= MAX_FINDINGS:
        raise AtlasAnvilRegionObservationError(
            "Anvil observation exceeds the finding-count limit"
        )
    subject = relative_path if slot is None else f"{relative_path}#slot-{slot}"
    findings.append({
        "finding_id": f"workbench:anvil:{category}:{subject}",
        "category": category,
        "severity": "blocking",
        "confidence": "confirmed",
        "summary": summary,
        "facts": {} if facts is None else facts,
        "evidence_ids": [evidence_id],
    })


def _observe_region(
    raw: bytes,
    *,
    relative_path: str,
    evidence_id: str,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    name_match = REGION_NAME_RE.fullmatch(Path(relative_path).name)
    region_x = int(name_match.group(1)) if name_match else None
    region_z = int(name_match.group(2)) if name_match else None
    region: dict[str, Any] = {
        "path": relative_path,
        "region_x": region_x,
        "region_z": region_z,
        "size": len(raw),
        "sector_count": len(raw) // SECTOR_BYTES,
        "chunks": [],
    }
    if name_match is None:
        _finding(
            findings,
            category="invalid-region-filename",
            relative_path=relative_path,
            summary="The .mca filename does not encode Anvil region coordinates.",
            evidence_id=evidence_id,
        )
    if len(raw) < HEADER_BYTES:
        _finding(
            findings,
            category="truncated-region-header",
            relative_path=relative_path,
            summary="The region file is shorter than its 8192-byte header.",
            evidence_id=evidence_id,
            facts={"observed_size": len(raw), "required_size": HEADER_BYTES},
        )
        return region
    if len(raw) % SECTOR_BYTES:
        _finding(
            findings,
            category="partial-region-sector",
            relative_path=relative_path,
            summary="The region file ends in a partial 4096-byte sector.",
            evidence_id=evidence_id,
            facts={"trailing_bytes": len(raw) % SECTOR_BYTES},
        )

    allocations: dict[int, tuple[int, int]] = {}
    invalid_slots: set[int] = set()
    for slot in range(SLOTS_PER_REGION):
        location = int.from_bytes(raw[slot * 4:slot * 4 + 4], "big")
        sector_offset = location >> 8
        sector_count = location & 0xFF
        if sector_offset == 0 and sector_count == 0:
            continue
        chunk = {
            "slot": slot,
            "local_x": slot % 32,
            "local_z": slot // 32,
            "sector_offset": sector_offset,
            "sector_count": sector_count,
            "state": "pending",
        }
        region["chunks"].append(chunk)
        allocations[slot] = (sector_offset, sector_count)
        if sector_offset == 0 or sector_count == 0:
            invalid_slots.add(slot)
            _finding(
                findings,
                category="incomplete-sector-allocation",
                relative_path=relative_path,
                slot=slot,
                summary="The chunk location has only one nonzero allocation field.",
                evidence_id=evidence_id,
                facts={
                    "sector_offset": sector_offset,
                    "sector_count": sector_count,
                },
            )
        elif sector_offset < 2:
            invalid_slots.add(slot)
            _finding(
                findings,
                category="header-sector-allocation",
                relative_path=relative_path,
                slot=slot,
                summary="The chunk allocation points into the region header.",
                evidence_id=evidence_id,
                facts={
                    "sector_offset": sector_offset,
                    "sector_count": sector_count,
                },
            )
        elif (sector_offset + sector_count) * SECTOR_BYTES > len(raw):
            invalid_slots.add(slot)
            _finding(
                findings,
                category="out-of-bounds-sector-allocation",
                relative_path=relative_path,
                slot=slot,
                summary="The chunk allocation extends beyond the region file.",
                evidence_id=evidence_id,
                facts={
                    "sector_offset": sector_offset,
                    "sector_count": sector_count,
                    "region_size": len(raw),
                },
            )

    sector_owners: dict[int, int] = {}
    overlapping_pairs: set[tuple[int, int]] = set()
    for slot in sorted(allocations):
        if slot in invalid_slots:
            continue
        sector_offset, sector_count = allocations[slot]
        for sector in range(sector_offset, sector_offset + sector_count):
            prior = sector_owners.setdefault(sector, slot)
            if prior != slot:
                overlapping_pairs.add((min(prior, slot), max(prior, slot)))
    for first, second in overlapping_pairs:
        invalid_slots.update((first, second))
    if overlapping_pairs:
        _finding(
            findings,
            category="overlapping-sector-allocation",
            relative_path=relative_path,
            summary="Two or more chunk slots claim shared data sectors.",
            evidence_id=evidence_id,
            facts={
                "slot_pairs": [
                    [first, second]
                    for first, second in sorted(overlapping_pairs)
                ],
            },
        )

    chunk_by_slot = {item["slot"]: item for item in region["chunks"]}
    for slot in sorted(allocations):
        chunk = chunk_by_slot[slot]
        if slot in invalid_slots:
            chunk["state"] = "structural-error"
            continue
        sector_offset, sector_count = allocations[slot]
        start = sector_offset * SECTOR_BYTES
        capacity = sector_count * SECTOR_BYTES
        declared_length = int.from_bytes(raw[start:start + 4], "big")
        chunk["declared_length"] = declared_length
        if declared_length < 1 or declared_length > capacity - 4:
            chunk["state"] = "structural-error"
            _finding(
                findings,
                category="invalid-chunk-payload-length",
                relative_path=relative_path,
                slot=slot,
                summary="The chunk payload length is outside its allocation.",
                evidence_id=evidence_id,
                facts={
                    "declared_length": declared_length,
                    "allocation_capacity": capacity - 4,
                },
            )
            continue
        payload_end = start + 4 + declared_length
        if payload_end > len(raw):
            chunk["state"] = "structural-error"
            _finding(
                findings,
                category="truncated-chunk-payload",
                relative_path=relative_path,
                slot=slot,
                summary="The declared chunk payload extends beyond available bytes.",
                evidence_id=evidence_id,
            )
            continue
        compression = raw[start + 4]
        chunk["compression"] = compression
        try:
            decompressed = _decompress_chunk(
                compression,
                raw[start + 5:payload_end],
            )
        except _NBTError as exc:
            chunk["state"] = "decompression-error"
            _finding(
                findings,
                category="chunk-decompression-failed",
                relative_path=relative_path,
                slot=slot,
                summary="The chunk payload cannot be safely decompressed.",
                evidence_id=evidence_id,
                facts={"reason": str(exc), "compression": compression},
            )
            continue
        chunk["decompressed_size"] = len(decompressed)
        try:
            actual_x, actual_z = _NBTReader(decompressed).coordinates()
        except _NBTError as exc:
            chunk["state"] = "nbt-error"
            _finding(
                findings,
                category="invalid-chunk-nbt",
                relative_path=relative_path,
                slot=slot,
                summary="The chunk NBT cannot provide trusted Level coordinates.",
                evidence_id=evidence_id,
                facts={"reason": str(exc)},
            )
            continue
        chunk["level_coordinates"] = {"x": actual_x, "z": actual_z}
        if region_x is None or region_z is None:
            chunk["state"] = "coordinate-unverifiable"
            continue
        expected_x = region_x * 32 + slot % 32
        expected_z = region_z * 32 + slot // 32
        chunk["expected_coordinates"] = {"x": expected_x, "z": expected_z}
        if (actual_x, actual_z) != (expected_x, expected_z):
            chunk["state"] = "coordinate-mismatch"
            _finding(
                findings,
                category="chunk-coordinate-mismatch",
                relative_path=relative_path,
                slot=slot,
                summary=(
                    "Level.xPos/zPos do not match the region filename and slot."
                ),
                evidence_id=evidence_id,
                facts={
                    "observed": {"x": actual_x, "z": actual_z},
                    "expected": {"x": expected_x, "z": expected_z},
                },
            )
        else:
            chunk["state"] = "validated"
    return region


def observe_anvil_region_world(world_root: Path | str) -> dict[str, Any]:
    """Return a deterministic observation of regular ``.mca`` world evidence.

    The caller is responsible for stopping every process that can write the
    world. Atlas additionally rejects files that change during capture.
    """

    selected = Path(world_root).expanduser()
    if selected.is_symlink():
        raise AtlasAnvilRegionObservationError(
            f"world root cannot be a symbolic link: {world_root}"
        )
    world = selected.resolve()
    if not world.is_dir():
        raise AtlasAnvilRegionObservationError(
            f"world root is not a directory: {world_root}"
        )
    paths = _discover_region_files(world)
    evidence: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    total_bytes = 0
    total_chunks = 0
    for path in paths:
        raw = _read_immutable_region(path)
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_REGION_BYTES:
            raise AtlasAnvilRegionObservationError(
                "world exceeds the aggregate Anvil byte limit"
            )
        relative = path.relative_to(world).as_posix()
        digest = sha256(raw).hexdigest()
        evidence_id = "sha256:" + digest
        evidence.append({
            "evidence_id": evidence_id,
            "kind": "minecraft-anvil-region",
            "path": relative,
            "sha256": digest,
            "size": len(raw),
        })
        region = _observe_region(
            raw,
            relative_path=relative,
            evidence_id=evidence_id,
            findings=findings,
        )
        regions.append(region)
        total_chunks += len(region["chunks"])
        if total_chunks > MAX_CHUNKS:
            raise AtlasAnvilRegionObservationError(
                "world exceeds the Anvil allocated-chunk limit"
            )

    states: dict[str, int] = {}
    for region in regions:
        for chunk in region["chunks"]:
            state = chunk["state"]
            states[state] = states.get(state, 0) + 1
    summary = {
        "region_file_count": len(regions),
        "region_bytes": total_bytes,
        "allocated_chunk_count": total_chunks,
        "validated_chunk_count": states.get("validated", 0),
        "coordinate_mismatch_count": states.get("coordinate-mismatch", 0),
        "invalid_chunk_count": sum(
            count
            for state_name, count in states.items()
            if state_name not in {"validated", "coordinate-unverifiable"}
        ),
        "finding_count": len(findings),
        "chunk_states": {key: states[key] for key in sorted(states)},
    }
    if findings:
        state = "findings-observed"
    elif not evidence:
        state = "insufficient-evidence"
    else:
        state = "no-findings-observed"
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
        "state": state,
        "source": {"world_root_uri": world.as_uri()},
        "limits": _limits(),
        "evidence": evidence,
        "facts": {"summary": summary, "regions": regions},
        "findings": findings,
        "limitations": [
            (
                "Atlas detects mutation during each file read but cannot prove "
                "that Minecraft was stopped before or after the observation."
            ),
            (
                "Experimental V1 validates Anvil container structure and only "
                "the Level.xPos/zPos NBT fields; it does not validate block, "
                "entity, lighting, heightmap, or terrain continuity semantics."
            ),
            (
                "Compression types 1 (gzip), 2 (zlib), and 3 (uncompressed) "
                "are supported; external-stream and later container extensions "
                "are reported as unsupported chunk compression."
            ),
        ],
    }
    identity = dict(observation)
    identity.pop("observation_id")
    observation["observation_id"] = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    return observation


__all__ = [
    "AtlasAnvilRegionObservationError",
    "observe_anvil_region_world",
]
