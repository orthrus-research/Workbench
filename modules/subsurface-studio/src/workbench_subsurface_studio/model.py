"""Identity, profile, input, and result primitives for Subsurface Studio V1."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping


RESULT_FORMAT = "workbench-gtceu-subsurface-studio-result-v1"
RESULT_PREFIX = "workbench-subsurface-result:sha256:"
PROFILE_FORMAT = "workbench-subsurface-pack-profile-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_PROFILE_BYTES = 1024 * 1024
MAX_CHUNKS = 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATIONS = frozenset(
    {"summary", "layers", "map", "explain", "section", "definitions", "fluids", "compare"}
)
_STATUSES = frozenset({"answered", "ambiguous", "partial", "not-found", "incomparable"})
_SOURCE_STATES = frozenset(
    {"declared", "observed-final", "observed-controlled", "unavailable"}
)
_PROFILE_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "profile_id",
        "pack_profile",
        "adapter",
        "defaults",
        "dimension_semantics",
        "state_semantics",
        "grid_semantics",
        "boundaries",
    }
)
_PROFILE_BOUNDARIES = {
    "atlas_remains_causal_authority": True,
    "bedrock_fluids_are_virtual_cells": True,
    "profile_is_pack_specific": True,
    "strata_remains_final_state_authority": True,
}
_RESULT_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "result_id",
        "read_only",
        "operation",
        "status",
        "profile",
        "scope",
        "sources",
        "result",
        "uncertainty",
        "limitations",
        "navigation",
    }
)


class SubsurfaceStudioError(ValueError):
    """Raised when a Studio input or result cannot be trusted."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SubsurfaceStudioError(message)


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
        raise SubsurfaceStudioError(f"cannot canonically encode Studio data: {exc}") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class FileBinding:
    path: Path
    size_bytes: int
    sha256: str

    def source(
        self,
        *,
        source_id: str,
        kind: str,
        authority: str,
        state: str,
        limitations: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        require(state in _SOURCE_STATES, f"invalid source state: {state}")
        return {
            "source_id": source_id,
            "kind": kind,
            "authority": authority,
            "state": state,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "path": str(self.path),
            "limitations": sorted(set(limitations)),
        }


def unavailable_source(
    *, source_id: str, kind: str, authority: str, limitations: list[str]
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "kind": kind,
        "authority": authority,
        "state": "unavailable",
        "sha256": None,
        "size_bytes": None,
        "path": None,
        "limitations": sorted(set(limitations)),
    }


def read_regular_file(
    path: Path | str, *, maximum_bytes: int, context: str
) -> tuple[bytes, FileBinding]:
    requested = Path(path).expanduser()
    require(not requested.is_symlink(), f"{context} cannot be a symlink: {requested}")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise SubsurfaceStudioError(f"cannot resolve {context}: {requested}: {exc}") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise SubsurfaceStudioError(f"cannot open {context}: {resolved}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode), f"{context} is not a regular file: {resolved}")
        require(before.st_size > 0, f"{context} is empty: {resolved}")
        require(
            before.st_size <= maximum_bytes,
            f"{context} exceeds {maximum_bytes} bytes: {resolved}",
        )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            require(bool(block), f"{context} ended before its declared size: {resolved}")
            chunks.append(block)
            remaining -= len(block)
        require(not os.read(descriptor, 1), f"{context} grew while being read: {resolved}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda row: (
        row.st_dev,
        row.st_ino,
        row.st_mode,
        row.st_size,
        row.st_mtime_ns,
        row.st_ctime_ns,
    )
    require(identity(before) == identity(after), f"{context} changed while being read: {resolved}")
    # NTFS pathname and handle ctime can describe different timestamps. Reopen
    # the pathname to compare the same metadata view, including replacement ID.
    try:
        current_descriptor = os.open(resolved, flags | getattr(os, "O_BINARY", 0))
        try:
            current = os.fstat(current_descriptor)
        finally:
            os.close(current_descriptor)
    except OSError as exc:
        raise SubsurfaceStudioError(f"cannot recheck {context}: {resolved}: {exc}") from exc
    require(identity(before) == identity(current), f"{context} was replaced while being read: {resolved}")
    data = b"".join(chunks)
    return data, FileBinding(resolved, len(data), hashlib.sha256(data).hexdigest())


def load_json_file(
    path: Path | str, *, maximum_bytes: int = MAX_JSON_BYTES, context: str
) -> tuple[dict[str, Any], FileBinding]:
    data, binding = read_regular_file(path, maximum_bytes=maximum_bytes, context=context)
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            require(key not in value, f"{context} repeats JSON key {key!r}")
            value[key] = item
        return value

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SubsurfaceStudioError(f"cannot parse {context}: {exc}") from exc
    require(isinstance(value, dict), f"{context} must contain a JSON object")
    return value, binding


def _exact_keys(
    value: Any, expected: set[str] | frozenset[str], context: str
) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    require(
        actual == set(expected),
        f"{context} fields mismatch: missing={sorted(set(expected) - actual)!r}, "
        f"unknown={sorted(actual - set(expected))!r}",
    )
    return value


def _text(value: Any, context: str) -> str:
    require(isinstance(value, str) and bool(value), f"{context} must be nonempty text")
    return value


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


def normalize_window(
    value: Mapping[str, Any], *, camel_case: bool = False
) -> dict[str, int]:
    if camel_case:
        keys = {
            "min_chunk_x": "minChunkX",
            "min_chunk_z": "minChunkZ",
            "chunk_size_x": "chunkSizeX",
            "chunk_size_z": "chunkSizeZ",
            "halo_chunks": "haloChunks",
        }
    else:
        keys = {
            key: key
            for key in (
                "min_chunk_x",
                "min_chunk_z",
                "chunk_size_x",
                "chunk_size_z",
                "halo_chunks",
            )
        }
    require(isinstance(value, Mapping), "chunk window must be an object")
    require(set(value) == set(keys.values()), "chunk window fields mismatch")
    window = {
        target: _integer(
            value[source],
            f"chunk_window.{source}",
            minimum=(
                1
                if target in {"chunk_size_x", "chunk_size_z"}
                else (0 if target == "halo_chunks" else None)
            ),
        )
        for target, source in keys.items()
    }
    require(
        window["chunk_size_x"] * window["chunk_size_z"] <= MAX_CHUNKS,
        f"chunk window exceeds {MAX_CHUNKS} chunks",
    )
    return window


def window_contains_chunk(
    window: Mapping[str, int],
    chunk_x: int,
    chunk_z: int,
    *,
    include_halo: bool = False,
) -> bool:
    halo = window["halo_chunks"] if include_halo else 0
    return (
        window["min_chunk_x"] - halo
        <= chunk_x
        < window["min_chunk_x"] + window["chunk_size_x"] + halo
        and window["min_chunk_z"] - halo
        <= chunk_z
        < window["min_chunk_z"] + window["chunk_size_z"] + halo
    )


def parse_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, _PROFILE_KEYS, "subsurface profile")
    profile = deepcopy(dict(value))
    require(profile["format"] == PROFILE_FORMAT, "subsurface profile format drift")
    require(profile["schema_version"] == 1, "subsurface profile schema drift")
    _text(profile["profile_id"], "profile_id")
    _text(profile["pack_profile"], "pack_profile")
    adapter = _exact_keys(
        profile["adapter"],
        {"inventory_profile_id", "impact_profile_id", "trace_profile_id"},
        "profile.adapter",
    )
    for key in adapter:
        _text(adapter[key], f"profile.adapter.{key}")
    defaults = _exact_keys(
        profile["defaults"], {"inventory", "impact", "manifest"}, "profile.defaults"
    )
    for key in defaults:
        _text(defaults[key], f"profile.defaults.{key}")
    dimensions = profile["dimension_semantics"]
    require(isinstance(dimensions, list) and dimensions, "profile requires dimension semantics")
    dimension_ids: set[int] = set()
    aliases: set[str] = set()
    for index, dimension in enumerate(dimensions):
        row = _exact_keys(
            dimension,
            {"dimension_id", "aliases", "surface_world"},
            f"dimension_semantics[{index}]",
        )
        dimension_id = _integer(
            row["dimension_id"], f"dimension_semantics[{index}].dimension_id"
        )
        require(dimension_id not in dimension_ids, f"duplicate dimension semantic: {dimension_id}")
        dimension_ids.add(dimension_id)
        require(
            isinstance(row["surface_world"], bool),
            "dimension surface_world must be boolean",
        )
        require(isinstance(row["aliases"], list), "dimension aliases must be an array")
        for alias in row["aliases"]:
            name = _text(alias, f"dimension_semantics[{index}].aliases")
            require(name not in aliases, f"duplicate dimension alias: {name}")
            aliases.add(name)
    states = _exact_keys(
        profile["state_semantics"],
        {
            "ore_prefix",
            "surface_rock_prefix",
            "air_states",
            "fluid_prefixes",
            "host_properties",
            "known_host_variants",
        },
        "profile.state_semantics",
    )
    for key in ("ore_prefix", "surface_rock_prefix"):
        _text(states[key], f"profile.state_semantics.{key}")
    for key in (
        "air_states",
        "fluid_prefixes",
        "host_properties",
        "known_host_variants",
    ):
        values = states[key]
        require(
            isinstance(values, list) and len(values) == len(set(values)),
            f"{key} must be a unique array",
        )
        for item in values:
            _text(item, f"profile.state_semantics.{key}")
    grid = _exact_keys(
        profile["grid_semantics"],
        {"ore_grid_chunks", "consulted_grid_radius", "bedrock_fluid_grid_chunks"},
        "profile.grid_semantics",
    )
    _integer(
        grid["ore_grid_chunks"],
        "profile.grid_semantics.ore_grid_chunks",
        minimum=1,
    )
    _integer(
        grid["consulted_grid_radius"],
        "profile.grid_semantics.consulted_grid_radius",
        minimum=0,
    )
    _integer(
        grid["bedrock_fluid_grid_chunks"],
        "profile.grid_semantics.bedrock_fluid_grid_chunks",
        minimum=1,
    )
    require(
        profile["boundaries"] == _PROFILE_BOUNDARIES,
        "subsurface profile boundaries drift",
    )
    return profile


def load_profile(path: Path | str) -> tuple[dict[str, Any], FileBinding]:
    value, binding = load_json_file(
        path, maximum_bytes=MAX_PROFILE_BYTES, context="subsurface pack profile"
    )
    return parse_profile(value), binding


def make_result(
    *,
    operation: str,
    status: str,
    profile: Mapping[str, Any],
    profile_binding: FileBinding,
    scope: Mapping[str, Any],
    sources: list[Mapping[str, Any]],
    result: Mapping[str, Any],
    uncertainty: list[str] | tuple[str, ...] = (),
    limitations: list[str] | tuple[str, ...] = (),
    navigation: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "schema_version": 1,
        "result_id": "",
        "read_only": True,
        "operation": operation,
        "status": status,
        "profile": {
            "profile_id": profile["profile_id"],
            "pack_profile": profile["pack_profile"],
            "sha256": profile_binding.sha256,
        },
        "scope": deepcopy(dict(scope)),
        "sources": [deepcopy(dict(row)) for row in sources],
        "result": deepcopy(dict(result)),
        "uncertainty": sorted(set(uncertainty)),
        "limitations": sorted(set(limitations)),
        "navigation": [deepcopy(dict(row)) for row in navigation],
    }
    report["result_id"] = RESULT_PREFIX + sha256_json(report)
    return validate_result(report)


def validate_result(value: Mapping[str, Any]) -> dict[str, Any]:
    require(isinstance(value, Mapping), "Studio result must be an object")
    report = deepcopy(dict(value))
    _exact_keys(report, _RESULT_KEYS, "Studio result")
    require(report["format"] == RESULT_FORMAT, "Studio result format drift")
    require(report["schema_version"] == 1, "Studio result schema drift")
    require(report["read_only"] is True, "Studio result must be read-only")
    operation = report["operation"]
    status = report["status"]
    require(operation in _OPERATIONS, f"invalid Studio operation: {operation}")
    require(status in _STATUSES, f"invalid Studio status: {status}")
    profile = _exact_keys(
        report["profile"], {"profile_id", "pack_profile", "sha256"}, "result.profile"
    )
    _text(profile["profile_id"], "result.profile.profile_id")
    _text(profile["pack_profile"], "result.profile.pack_profile")
    require(
        isinstance(profile["sha256"], str)
        and bool(_SHA256_RE.fullmatch(profile["sha256"])),
        "result profile digest is invalid",
    )
    scope = _exact_keys(
        report["scope"],
        {"world_seed", "dimension_id", "chunk_window"},
        "result.scope",
    )
    require(
        (
            isinstance(scope["world_seed"], int)
            and not isinstance(scope["world_seed"], bool)
        )
        or (isinstance(scope["world_seed"], str) and bool(scope["world_seed"])),
        "result world_seed is invalid",
    )
    _integer(scope["dimension_id"], "result.scope.dimension_id")
    window = normalize_window(scope["chunk_window"])
    sources = report["sources"]
    require(
        isinstance(sources, list) and len(sources) >= 3,
        "Studio result requires at least three sources",
    )
    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        row = _exact_keys(
            source,
            {
                "source_id",
                "kind",
                "authority",
                "state",
                "sha256",
                "size_bytes",
                "path",
                "limitations",
            },
            f"sources[{index}]",
        )
        source_id = _text(row["source_id"], f"sources[{index}].source_id")
        require(source_id not in source_ids, f"duplicate Studio source ID: {source_id}")
        source_ids.add(source_id)
        _text(row["kind"], f"sources[{index}].kind")
        _text(row["authority"], f"sources[{index}].authority")
        require(row["state"] in _SOURCE_STATES, f"invalid source state: {row['state']}")
        if row["state"] == "unavailable":
            require(
                row["sha256"] is None
                and row["size_bytes"] is None
                and row["path"] is None,
                "unavailable source cannot claim bytes",
            )
        else:
            require(
                isinstance(row["sha256"], str)
                and bool(_SHA256_RE.fullmatch(row["sha256"])),
                "source digest is invalid",
            )
            _integer(
                row["size_bytes"], f"sources[{index}].size_bytes", minimum=1
            )
            _text(row["path"], f"sources[{index}].path")
        require(
            isinstance(row["limitations"], list)
            and row["limitations"] == sorted(set(row["limitations"])),
            "source limitations must be sorted and unique",
        )
    require(isinstance(report["result"], Mapping), "Studio result payload must be an object")
    for key in ("uncertainty", "limitations"):
        rows = report[key]
        require(
            isinstance(rows, list) and rows == sorted(set(rows)),
            f"{key} must be sorted and unique",
        )
        for row in rows:
            _text(row, key)
    navigation = report["navigation"]
    require(isinstance(navigation, list), "navigation must be an array")
    for index, item in enumerate(navigation):
        row = _exact_keys(item, {"kind", "label", "target"}, f"navigation[{index}]")
        for key in row:
            _text(row[key], f"navigation[{index}].{key}")
    if status in {"ambiguous", "partial", "incomparable"}:
        require(
            bool(report["uncertainty"] or report["limitations"]),
            f"{status} result must explain its boundary",
        )
    if operation == "map":
        payload = report["result"]
        require(
            payload.get("width") == window["chunk_size_x"],
            "map width differs from scope",
        )
        require(
            payload.get("height") == window["chunk_size_z"],
            "map height differs from scope",
        )
        cells = payload.get("cells")
        require(
            isinstance(cells, list)
            and len(cells) == payload["width"] * payload["height"],
            "map cell coverage mismatch",
        )
    if operation == "explain":
        position = report["result"].get("position")
        require(isinstance(position, Mapping), "explain result lacks position")
        x = _integer(position.get("x"), "explain.position.x")
        _integer(position.get("y"), "explain.position.y", minimum=0, maximum=255)
        z = _integer(position.get("z"), "explain.position.z")
        require(
            window_contains_chunk(window, x // 16, z // 16),
            "explain position is outside scope",
        )
    if operation == "section":
        payload = report["result"]
        _integer(
            payload.get("column_count"),
            "section.column_count",
            minimum=1,
            maximum=256,
        )
        _integer(
            payload.get("vertical_count"),
            "section.vertical_count",
            minimum=1,
            maximum=256,
        )
    result_id = report["result_id"]
    require(
        isinstance(result_id, str) and result_id.startswith(RESULT_PREFIX),
        "Studio result ID is invalid",
    )
    identity = deepcopy(report)
    identity["result_id"] = ""
    expected = RESULT_PREFIX + sha256_json(identity)
    require(result_id == expected, "Studio result ID drift")
    return report
