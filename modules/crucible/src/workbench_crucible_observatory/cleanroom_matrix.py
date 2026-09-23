"""Admit and evaluate the exact Cleanroom worldgen fixture matrix.

This module is an evaluation boundary, not a fixture or evidence generator.
It accepts the deterministic result written by ``DedicatedServerFixtureDriver``
and already-normalized Crucible bundles.  Passing this evaluator does not by
itself prove where those inputs came from; custody and launch receipts remain
the responsibility of the runtime harness.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping

from .bundle import (
    CAPTURE_CONTRACT_ID,
    CaptureValidationError,
    _ValidatedBundlePublication,
    _ValidatedBundleWriteReceipt,
    _validated_bundle_from_write_receipt,
    canonical_json_bytes,
    canonical_json_sha256,
    validate_bundle,
)


FIXTURE_RESULT_SCHEMA = "workbench.worldgen-observatory.fixture-result.v1"
FIXTURE_ID = "dedicated_server_fixed_region_v1"
MATRIX_EVALUATION_SCHEMA = (
    "workbench.worldgen-observatory.cleanroom-matrix-evaluation.v1"
)
MATRIX_EVALUATION_PREFIX = "crucible-worldgen-cleanroom-matrix:sha256:"
PROJECTION_MATRIX_EVALUATION_SCHEMA = (
    "workbench.worldgen-observatory.cleanroom-projection-matrix-evaluation.v1"
)
PROJECTION_MATRIX_EVALUATION_PREFIX = (
    "crucible-worldgen-cleanroom-projection-matrix:sha256:"
)
CLEANROOM_BUNDLE_PROJECTION_SCHEMA = (
    "workbench.worldgen-observatory.cleanroom-bundle-projection.v1"
)
CLEANROOM_BUNDLE_PROJECTION_PREFIX = (
    "crucible-worldgen-cleanroom-bundle-projection:sha256:"
)
CLEANROOM_BUNDLE_PROJECTION_EVIDENCE_CLASS = "validated_bundle_projection"
FIXED_WORLD_SEED = -571123474424848392
FIXED_WORLD_SEED_SHA256 = hashlib.sha256(
    str(FIXED_WORLD_SEED).encode("utf-8")
).hexdigest()
FORWARD_ROUTE = ((64, 64), (65, 64), (64, 65), (65, 65))
REVERSE_ROUTE = tuple(reversed(FORWARD_ROUTE))
FIXED_CHUNK_SET = frozenset(FORWARD_ROUTE)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MOD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_JAVA_CLASS = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+"
)
_EXECUTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
_RESULT_KEYS = {
    "schema",
    "fixture",
    "completion_state",
    "save_state",
    "shutdown_state",
    "world_seed_sha256",
    "dimension",
    "route_order",
    "selector_sha256",
    "route_sha256",
    "selected_chunks",
    "runtime_mod_inventory",
}
_CHUNK_KEYS = {"chunk_x", "chunk_z", "semantic_state_sha256"}
_MOD_KEYS = {"mod_id", "source_sha256", "mod_class_name"}
_PROJECTION_KEYS = {
    "schema",
    "projection_id",
    "evidence_class",
    "execution_id",
    "source_bundle_sha256",
    "run",
    "summary",
    "publication",
    "checkpoint_rows",
    "checkpoint_summary_sha256",
    "rng_rows",
    "rng_summary_sha256",
}
_RUN_KEYS = {
    "run_id",
    "capture_mode",
    "capture_plan_sha256",
    "fixture_id",
    "fixture_sha256",
    "environment",
    "world",
}
_ENVIRONMENT_KEYS = {
    "minecraft_version",
    "platform_profile_id",
    "platform_profile_sha256",
    "pack_profile_id",
    "pack_profile_sha256",
    "snapshot_id",
    "physical_side",
    "runtime_java",
    "mapping_namespace",
    "transformed_runtime_sha256",
    "mod_set_sha256",
    "configuration_set_sha256",
}
_WORLD_KEYS = {
    "world_instance_id",
    "world_seed_sha256",
    "world_type",
    "generator_options_sha256",
    "dimension_ids",
}
_SUMMARY_KEYS = {
    "record_count",
    "last_ordinal",
    "span_enter_count",
    "span_return_count",
    "span_throw_count",
    "open_span_ids",
    "coverage_state",
    "dropped_record_count",
    "limitations",
}
_PUBLICATION_KEYS = {"state", "completion_seal", "crash_residue"}
_SEAL_KEYS = {
    "capture_id",
    "run_manifest_sha256",
    "records_sha256",
    "semantic_fingerprints_sha256",
    "record_count",
    "last_ordinal",
    "sealed_after_stop_record",
}
_RESIDUE_KEYS = {
    "residue_id",
    "reason",
    "last_complete_ordinal",
    "open_span_ids",
    "recoverable",
    "diagnostic_sha256",
}
_PROJECTED_ACTOR_KEYS = {
    "binding",
    "mod_id",
    "code_source_sha256",
    "class_name",
    "method_name",
    "method_descriptor",
    "mapping_namespace",
    "transformed_class_sha256",
}
_CHECKPOINT_ROW_KEYS = {
    "chunk_x",
    "chunk_z",
    "checkpoint_id",
    "stage_id",
    "canonicalization_id",
    "included_domains",
    "semantic_state_sha256",
    "actor",
}
_RNG_ROW_KEYS = {
    "chunk_x",
    "chunk_z",
    "detail",
    "stream_id",
    "algorithm_class",
    "operation",
    "call_ordinal",
    "parameters_sha256",
    "result_sha256",
    "rolling_digest",
    "actor",
}

REQUIRED_CASES = (
    "aa-1",
    "aa-2",
    "observer-off",
    "observer-on",
    "order-forward",
    "order-reverse",
    "restart-1",
    "restart-2",
    "crash-before-seal",
)


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token}")


def _reject_float(token: str) -> None:
    raise ValueError(f"fixture result does not permit a JSON number {token}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(canonical_json_bytes(value))


def _route_material(route: tuple[tuple[int, int], ...]) -> str:
    """Match DedicatedServerFixtureDriver.routeMaterial byte-for-byte."""

    return ";".join(f"{chunk_x},{chunk_z}" for chunk_x, chunk_z in route)


def _route_sha256(route: tuple[tuple[int, int], ...]) -> str:
    return _sha256_bytes(_route_material(route).encode("utf-8"))


SELECTOR_SHA256 = _route_sha256(FORWARD_ROUTE)
FORWARD_ROUTE_SHA256 = SELECTOR_SHA256
REVERSE_ROUTE_SHA256 = _route_sha256(REVERSE_ROUTE)


@dataclass(frozen=True, slots=True)
class FixtureChunkResult:
    chunk_x: int
    chunk_z: int
    semantic_state_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_x": self.chunk_x,
            "chunk_z": self.chunk_z,
            "semantic_state_sha256": self.semantic_state_sha256,
        }


@dataclass(frozen=True, slots=True)
class RuntimeModInventoryEntry:
    mod_id: str
    source_sha256: str
    mod_class_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "mod_id": self.mod_id,
            "source_sha256": self.source_sha256,
            "mod_class_name": self.mod_class_name,
        }


@dataclass(frozen=True, slots=True)
class FixtureResult:
    """One closed, admitted driver result artifact."""

    artifact_sha256: str
    canonical_document_sha256: str
    route_order: str
    route_sha256: str
    selected_chunks: tuple[FixtureChunkResult, ...]
    runtime_mod_inventory: tuple[RuntimeModInventoryEntry, ...]

    def semantic_rows(self) -> list[dict[str, Any]]:
        return [
            chunk.as_dict()
            for chunk in sorted(
                self.selected_chunks,
                key=lambda item: (item.chunk_x, item.chunk_z),
            )
        ]

    def semantic_map_sha256(self) -> str:
        return _sha256_json(self.semantic_rows())

    def inventory_rows(self) -> list[dict[str, str]]:
        return [entry.as_dict() for entry in self.runtime_mod_inventory]

    def inventory_sha256(self) -> str:
        return _sha256_json(self.inventory_rows())


def _exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    _require(
        actual == expected,
        f"{location} fields mismatch: missing={sorted(expected - actual)!r}, "
        f"unknown={sorted(actual - expected)!r}",
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _parse_fixture_value(value: Any, *, artifact_sha256: str) -> FixtureResult:
    _require(isinstance(value, dict), "fixture result must be a JSON object")
    _exact_keys(value, _RESULT_KEYS, "fixture result")
    _require(value["schema"] == FIXTURE_RESULT_SCHEMA, "fixture result schema mismatch")
    _require(value["fixture"] == FIXTURE_ID, "fixture result fixture mismatch")
    _require(
        value["completion_state"] == "complete",
        "fixture result is not complete",
    )
    _require(value["save_state"] == "flushed", "fixture result was not flushed")
    _require(
        value["shutdown_state"] == "requested",
        "fixture result lacks the shutdown request marker",
    )
    _require(
        value["world_seed_sha256"] == FIXED_WORLD_SEED_SHA256,
        "fixture result fixed-seed digest mismatch",
    )
    _require(
        type(value["dimension"]) is int and value["dimension"] == 0,
        "fixture result dimension must be integer zero",
    )

    route_order = value["route_order"]
    _require(
        isinstance(route_order, str) and route_order in {"forward", "reverse"},
        "fixture route order is invalid",
    )
    route = FORWARD_ROUTE if route_order == "forward" else REVERSE_ROUTE
    _require(
        value["selector_sha256"] == SELECTOR_SHA256,
        "fixture selector digest does not match the fixed Java selector material",
    )
    _require(
        value["route_sha256"] == _route_sha256(route),
        "fixture route digest does not match the declared Java route material",
    )

    chunks_value = value["selected_chunks"]
    _require(isinstance(chunks_value, list), "selected_chunks must be an array")
    _require(len(chunks_value) == len(FORWARD_ROUTE), "fixture must contain four chunks")
    chunks: list[FixtureChunkResult] = []
    coordinates: list[tuple[int, int]] = []
    for index, row in enumerate(chunks_value):
        _require(isinstance(row, dict), f"selected_chunks[{index}] must be an object")
        _exact_keys(row, _CHUNK_KEYS, f"selected_chunks[{index}]")
        chunk_x = row["chunk_x"]
        chunk_z = row["chunk_z"]
        _require(
            type(chunk_x) is int and type(chunk_z) is int,
            f"selected_chunks[{index}] coordinates must be integers",
        )
        semantic_sha256 = row["semantic_state_sha256"]
        _require(
            _is_sha256(semantic_sha256),
            f"selected_chunks[{index}] semantic digest must be lowercase SHA-256",
        )
        coordinate = (chunk_x, chunk_z)
        _require(coordinate not in coordinates, "selected chunk coordinate is duplicated")
        coordinates.append(coordinate)
        chunks.append(FixtureChunkResult(chunk_x, chunk_z, semantic_sha256))
    _require(
        tuple(coordinates) == route,
        "selected chunk sequence does not match the declared fixed route",
    )
    _require(
        frozenset(coordinates) == FIXED_CHUNK_SET,
        "selected chunks do not match the fixed 64-65 two-by-two set",
    )

    inventory_value = value["runtime_mod_inventory"]
    _require(
        isinstance(inventory_value, list) and bool(inventory_value),
        "runtime_mod_inventory must be a nonempty array",
    )
    inventory: list[RuntimeModInventoryEntry] = []
    mod_ids: set[str] = set()
    inventory_keys: list[tuple[str, str, str]] = []
    for index, row in enumerate(inventory_value):
        _require(
            isinstance(row, dict),
            f"runtime_mod_inventory[{index}] must be an object",
        )
        _exact_keys(row, _MOD_KEYS, f"runtime_mod_inventory[{index}]")
        mod_id = row["mod_id"]
        source_sha256 = row["source_sha256"]
        mod_class_name = row["mod_class_name"]
        _require(
            isinstance(mod_id, str)
            and _MOD_ID.fullmatch(mod_id) is not None
            and mod_id != "unavailable",
            f"runtime_mod_inventory[{index}] has an invalid mod_id",
        )
        _require(mod_id not in mod_ids, "runtime mod_id is duplicated")
        mod_ids.add(mod_id)
        _require(
            source_sha256 == "unavailable" or _is_sha256(source_sha256),
            f"runtime_mod_inventory[{index}] source must be lowercase SHA-256 "
            "or explicit unavailable",
        )
        _require(
            mod_class_name == "unavailable"
            or (
                isinstance(mod_class_name, str)
                and _JAVA_CLASS.fullmatch(mod_class_name) is not None
            ),
            f"runtime_mod_inventory[{index}] mod class must be a Java binary "
            "name or explicit unavailable",
        )
        entry = RuntimeModInventoryEntry(mod_id, source_sha256, mod_class_name)
        inventory.append(entry)
        inventory_keys.append((mod_id, mod_class_name, source_sha256))
    _require(
        inventory_keys == sorted(inventory_keys),
        "runtime_mod_inventory is not in the driver's canonical sort order",
    )
    _require(
        len(inventory_keys) == len(set(inventory_keys)),
        "runtime_mod_inventory contains a duplicate row",
    )

    canonical_sha256 = _sha256_json(value)
    return FixtureResult(
        artifact_sha256=artifact_sha256,
        canonical_document_sha256=canonical_sha256,
        route_order=route_order,
        route_sha256=value["route_sha256"],
        selected_chunks=tuple(chunks),
        runtime_mod_inventory=tuple(inventory),
    )


def parse_fixture_result(encoded: bytes | str) -> FixtureResult:
    """Parse one result while rejecting duplicate keys and non-finite numbers."""

    if isinstance(encoded, str):
        try:
            raw = encoded.encode("utf-8")
        except UnicodeError as exc:
            raise CaptureValidationError(
                f"fixture result is not valid UTF-8 text: {exc}"
            ) from exc
        text = encoded
    elif isinstance(encoded, bytes):
        raw = encoded
        try:
            text = encoded.decode("utf-8")
        except UnicodeError as exc:
            raise CaptureValidationError(
                f"fixture result is not valid UTF-8: {exc}"
            ) from exc
    else:
        raise CaptureValidationError("fixture result input must be bytes or text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse fixture result: {exc}") from exc
    return _parse_fixture_value(value, artifact_sha256=_sha256_bytes(raw))


def load_fixture_result(path: Path | str) -> FixtureResult:
    try:
        encoded = Path(path).read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot load fixture result {path}: {exc}") from exc
    return parse_fixture_result(encoded)


@dataclass(frozen=True, slots=True)
class CleanroomExecution:
    """Caller-supplied custody record for one dedicated-server process."""

    execution_id: str
    observer_enabled: bool
    route_order: str
    fixture_result: FixtureResult | None
    canonical_bundle: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class CleanroomBundleProjection:
    """Small, content-addressed receipt derived from one validated V1 bundle."""

    projection_id: str
    execution_id: str
    source_bundle_sha256: str
    run: Mapping[str, Any]
    summary: Mapping[str, Any]
    publication: Mapping[str, Any]
    checkpoint_rows: tuple[Mapping[str, Any], ...]
    checkpoint_summary_sha256: str | None
    rng_rows: tuple[Mapping[str, Any], ...]
    rng_summary_sha256: str | None

    @property
    def publication_state(self) -> str:
        return str(self.publication["state"])

    @property
    def publication_id(self) -> str:
        if self.publication_state == "completed":
            return str(self.publication["completion_seal"]["capture_id"])
        return str(self.publication["crash_residue"]["residue_id"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": CLEANROOM_BUNDLE_PROJECTION_SCHEMA,
            "projection_id": self.projection_id,
            "evidence_class": CLEANROOM_BUNDLE_PROJECTION_EVIDENCE_CLASS,
            "execution_id": self.execution_id,
            "source_bundle_sha256": self.source_bundle_sha256,
            "run": deepcopy(dict(self.run)),
            "summary": deepcopy(dict(self.summary)),
            "publication": deepcopy(dict(self.publication)),
            "checkpoint_rows": [deepcopy(dict(row)) for row in self.checkpoint_rows],
            "checkpoint_summary_sha256": self.checkpoint_summary_sha256,
            "rng_rows": [deepcopy(dict(row)) for row in self.rng_rows],
            "rng_summary_sha256": self.rng_summary_sha256,
        }


@dataclass(frozen=True, slots=True)
class CleanroomProjectionExecution:
    """Custody record that carries only a validated small bundle projection."""

    execution_id: str
    observer_enabled: bool
    route_order: str
    fixture_result: FixtureResult | None
    bundle_projection: CleanroomBundleProjection | None


@dataclass(frozen=True, slots=True)
class _BundleProjection:
    publication_state: str
    publication_id: str
    checkpoint_rows: tuple[dict[str, Any], ...]
    checkpoint_sha256: str | None
    rng_rows: tuple[dict[str, Any], ...]
    rng_sha256: str | None


def _actor_identity(actor: Mapping[str, Any], *, location: str) -> dict[str, str]:
    _require(
        actor.get("binding") in {"exact", "workbench"},
        f"{location} actor is not exactly bound",
    )
    return {
        field: str(actor[field])
        for field in (
            "binding",
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
            "transformed_class_sha256",
        )
    }


def _validate_bundle_manifest(bundle: Mapping[str, Any], *, location: str) -> None:
    run = bundle["run"]
    _require(
        run["capture_mode"] == "lossless-fixture",
        f"{location} is not a lossless fixture capture",
    )
    _require(
        run["environment"]["physical_side"] == "DEDICATED_SERVER",
        f"{location} was not captured on a dedicated server",
    )
    _require(
        run["world"]["world_seed_sha256"] == FIXED_WORLD_SEED_SHA256,
        f"{location} fixed-seed digest mismatch",
    )
    _require(
        run["world"]["dimension_ids"] == [0],
        f"{location} must be scoped to dimension zero",
    )
    _require(
        run["world"]["world_type"] == "wb_observe",
        f"{location} did not use the cooperative fixture world type",
    )


def _completed_bundle_projection(
    bundle: Mapping[str, Any], *, location: str, already_validated: bool = False
) -> _BundleProjection:
    if not already_validated:
        validate_bundle(bundle, require_completed=True)
    _validate_bundle_manifest(bundle, location=location)
    _require(
        bundle["summary"]["dropped_record_count"] == 0,
        f"{location} dropped records",
    )
    _require(
        bundle["summary"]["coverage_state"] == "complete",
        f"{location} coverage is not complete",
    )
    _require(
        bundle["summary"]["limitations"] == [],
        f"{location} has capture limitations",
    )
    seal = bundle["publication"]["completion_seal"]
    _require(isinstance(seal, Mapping), f"{location} is not sealed")

    checkpoint_rows: list[dict[str, Any]] = []
    checkpoint_coordinates: set[tuple[int, int]] = set()
    for index, fingerprint in enumerate(bundle["semantic_fingerprints"]):
        scope = fingerprint["scope"]
        coordinate = (scope["chunk"]["x"], scope["chunk"]["z"])
        _require(
            scope["dimension_id"] == 0 and coordinate in FIXED_CHUNK_SET,
            f"{location} checkpoint {index} is outside the fixed fixture region",
        )
        record = bundle["records"][fingerprint["checkpoint_ordinal"]]
        checkpoint_coordinates.add(coordinate)
        checkpoint_rows.append(
            {
                "chunk_x": coordinate[0],
                "chunk_z": coordinate[1],
                "checkpoint_id": fingerprint["checkpoint_id"],
                "stage_id": scope["stage_id"],
                "canonicalization_id": fingerprint["canonicalization_id"],
                "included_domains": list(fingerprint["included_domains"]),
                "semantic_state_sha256": fingerprint["semantic_state_sha256"],
                "actor": _actor_identity(
                    record["actor"], location=f"{location} checkpoint {index}"
                ),
            }
        )
    _require(
        checkpoint_coordinates == set(FIXED_CHUNK_SET),
        f"{location} checkpoints do not cover every selected chunk",
    )
    checkpoint_rows.sort(key=lambda row: canonical_json_bytes(row))

    rng_rows: list[dict[str, Any]] = []
    rng_coordinates: set[tuple[int, int]] = set()
    rng_keys: set[tuple[Any, ...]] = set()
    for record in bundle["records"]:
        if record["record_type"] != "rng_observation":
            continue
        scope = record["scope"]
        chunk = scope["chunk"]
        _require(
            scope["dimension_id"] == 0 and isinstance(chunk, Mapping),
            f"{location} has an unscoped cooperative RNG observation",
        )
        coordinate = (chunk["x"], chunk["z"])
        _require(
            coordinate in FIXED_CHUNK_SET,
            f"{location} has an RNG observation outside the fixture region",
        )
        payload = record["payload"]
        identity = (
            coordinate,
            payload["stream_id"],
            payload["detail"],
            payload["call_ordinal"],
        )
        _require(identity not in rng_keys, f"{location} has a duplicate RNG summary key")
        rng_keys.add(identity)
        rng_coordinates.add(coordinate)
        rng_rows.append(
            {
                "chunk_x": coordinate[0],
                "chunk_z": coordinate[1],
                "detail": payload["detail"],
                "stream_id": payload["stream_id"],
                "algorithm_class": payload["algorithm_class"],
                "operation": payload["operation"],
                "call_ordinal": payload["call_ordinal"],
                "parameters_sha256": payload["parameters_sha256"],
                "result_sha256": payload["result_sha256"],
                "rolling_digest": payload["rolling_digest"],
                "actor": _actor_identity(
                    record["actor"], location=f"{location} RNG observation"
                ),
            }
        )
    _require(bool(rng_rows), f"{location} lacks cooperative RNG observations")
    _require(
        rng_coordinates == set(FIXED_CHUNK_SET),
        f"{location} RNG observations do not cover every selected chunk",
    )
    rng_rows.sort(key=lambda row: canonical_json_bytes(row))
    return _BundleProjection(
        publication_state="completed",
        publication_id=str(seal["capture_id"]),
        checkpoint_rows=tuple(checkpoint_rows),
        checkpoint_sha256=_sha256_json(checkpoint_rows),
        rng_rows=tuple(rng_rows),
        rng_sha256=_sha256_json(rng_rows),
    )


def _crash_bundle_projection(
    bundle: Mapping[str, Any], *, location: str, already_validated: bool = False
) -> _BundleProjection:
    if not already_validated:
        validate_bundle(bundle)
    _validate_bundle_manifest(bundle, location=location)
    publication = bundle["publication"]
    _require(publication["state"] == "incomplete", f"{location} is not incomplete")
    _require(publication["completion_seal"] is None, f"{location} contains a seal")
    residue = publication["crash_residue"]
    _require(isinstance(residue, Mapping), f"{location} lacks crash residue")
    _require(
        residue["reason"] in {"process_crash", "forced_termination"},
        f"{location} does not record a crash or forced termination",
    )
    return _BundleProjection(
        publication_state="incomplete",
        publication_id=str(residue["residue_id"]),
        checkpoint_rows=(),
        checkpoint_sha256=None,
        rng_rows=(),
        rng_sha256=None,
    )


def _projection_material(
    *,
    execution_id: str,
    source_bundle_sha256: str,
    run: Mapping[str, Any],
    summary: Mapping[str, Any],
    publication: Mapping[str, Any],
    checkpoint_rows: list[dict[str, Any]],
    checkpoint_summary_sha256: str | None,
    rng_rows: list[dict[str, Any]],
    rng_summary_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema": CLEANROOM_BUNDLE_PROJECTION_SCHEMA,
        "evidence_class": CLEANROOM_BUNDLE_PROJECTION_EVIDENCE_CLASS,
        "execution_id": execution_id,
        "source_bundle_sha256": source_bundle_sha256,
        "run": deepcopy(dict(run)),
        "summary": deepcopy(dict(summary)),
        "publication": deepcopy(dict(publication)),
        "checkpoint_rows": deepcopy(checkpoint_rows),
        "checkpoint_summary_sha256": checkpoint_summary_sha256,
        "rng_rows": deepcopy(rng_rows),
        "rng_summary_sha256": rng_summary_sha256,
    }


def _projection_actor(value: Any, *, location: str) -> dict[str, str]:
    _require(isinstance(value, Mapping), f"{location} actor must be an object")
    _exact_keys(value, _PROJECTED_ACTOR_KEYS, f"{location} actor")
    binding = value["binding"]
    _require(binding in {"exact", "workbench"}, f"{location} actor is not exact")
    for field in _PROJECTED_ACTOR_KEYS - {
        "binding",
        "code_source_sha256",
        "transformed_class_sha256",
    }:
        _require(
            isinstance(value[field], str) and bool(value[field]),
            f"{location} actor {field} must be nonempty",
        )
    _require(
        _MOD_ID.fullmatch(value["mod_id"]) is not None,
        f"{location} actor mod_id is invalid",
    )
    _require(
        _JAVA_CLASS.fullmatch(value["class_name"]) is not None,
        f"{location} actor class_name is invalid",
    )
    for field in ("code_source_sha256", "transformed_class_sha256"):
        _require(_is_sha256(value[field]), f"{location} actor {field} is not SHA-256")
    return {field: str(value[field]) for field in _PROJECTED_ACTOR_KEYS}


def _validate_projection_run(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "projection run must be an object")
    _exact_keys(value, _RUN_KEYS, "projection run")
    material = deepcopy(dict(value))
    run_id = material.pop("run_id")
    _require(
        run_id == "crucible-worldgen-run:sha256:" + _sha256_json(material),
        "projection run ID digest mismatch",
    )
    _require(value["capture_mode"] == "lossless-fixture", "projection is not lossless")
    for field in ("capture_plan_sha256", "fixture_sha256"):
        _require(_is_sha256(value[field]), f"projection run {field} is not SHA-256")
    _require(
        isinstance(value["fixture_id"], str) and bool(value["fixture_id"]),
        "projection fixture ID is empty",
    )

    environment = value["environment"]
    _require(isinstance(environment, Mapping), "projection environment must be an object")
    _exact_keys(environment, _ENVIRONMENT_KEYS, "projection environment")
    _require(environment["minecraft_version"] == "1.12.2", "projection Minecraft mismatch")
    _require(
        environment["physical_side"] == "DEDICATED_SERVER",
        "projection was not captured on a dedicated server",
    )
    for field in (
        "platform_profile_sha256",
        "transformed_runtime_sha256",
        "mod_set_sha256",
        "configuration_set_sha256",
    ):
        _require(_is_sha256(environment[field]), f"projection environment {field} is invalid")
    for field in (
        "platform_profile_id",
        "snapshot_id",
        "runtime_java",
        "mapping_namespace",
    ):
        _require(
            isinstance(environment[field], str) and bool(environment[field]),
            f"projection environment {field} is empty",
        )
    pack_id = environment["pack_profile_id"]
    pack_digest = environment["pack_profile_sha256"]
    _require(
        (pack_id is None) == (pack_digest is None),
        "projection pack identity and digest must be present together",
    )
    if pack_id is not None:
        _require(isinstance(pack_id, str) and bool(pack_id), "projection pack ID is empty")
        _require(_is_sha256(pack_digest), "projection pack digest is invalid")

    world = value["world"]
    _require(isinstance(world, Mapping), "projection world must be an object")
    _exact_keys(world, _WORLD_KEYS, "projection world")
    _require(
        isinstance(world["world_instance_id"], str) and bool(world["world_instance_id"]),
        "projection world instance ID is empty",
    )
    _require(
        world["world_seed_sha256"] == FIXED_WORLD_SEED_SHA256,
        "projection fixed-seed digest mismatch",
    )
    _require(world["world_type"] == "wb_observe", "projection world type mismatch")
    _require(
        _is_sha256(world["generator_options_sha256"]),
        "projection generator options digest is invalid",
    )
    _require(world["dimension_ids"] == [0], "projection dimensions must equal [0]")
    return deepcopy(dict(value))


def _validate_projection_summary(value: Any) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "projection summary must be an object")
    _exact_keys(value, _SUMMARY_KEYS, "projection summary")
    for field in (
        "record_count",
        "last_ordinal",
        "span_enter_count",
        "span_return_count",
        "span_throw_count",
        "dropped_record_count",
    ):
        _require(
            type(value[field]) is int and value[field] >= 0,
            f"projection summary {field} must be a nonnegative integer",
        )
    _require(value["record_count"] > 0, "projection summary record count is zero")
    _require(
        value["last_ordinal"] == value["record_count"] - 1,
        "projection summary ordinal does not match its record count",
    )
    for field in ("open_span_ids", "limitations"):
        items = value[field]
        _require(
            isinstance(items, list)
            and all(isinstance(item, str) and bool(item) for item in items)
            and items == sorted(set(items)),
            f"projection summary {field} is not canonical",
        )
    _require(
        value["coverage_state"] in {"complete", "sampled", "truncated", "incomplete"},
        "projection summary coverage state is invalid",
    )
    _require(
        value["span_return_count"]
        + value["span_throw_count"]
        + len(value["open_span_ids"])
        == value["span_enter_count"],
        "projection summary span counts do not balance",
    )
    return deepcopy(dict(value))


def _validate_projection_publication(
    value: Any,
    *,
    run: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "projection publication must be an object")
    _exact_keys(value, _PUBLICATION_KEYS, "projection publication")
    state = value["state"]
    _require(state in {"completed", "incomplete"}, "projection publication state is invalid")
    if state == "completed":
        seal = value["completion_seal"]
        _require(isinstance(seal, Mapping), "completed projection lacks a seal")
        _exact_keys(seal, _SEAL_KEYS, "projection completion seal")
        _require(value["crash_residue"] is None, "completed projection contains residue")
        for field in (
            "run_manifest_sha256",
            "records_sha256",
            "semantic_fingerprints_sha256",
        ):
            _require(_is_sha256(seal[field]), f"projection seal {field} is invalid")
        _require(
            type(seal["record_count"]) is int
            and seal["record_count"] > 0
            and type(seal["last_ordinal"]) is int
            and seal["last_ordinal"] >= 0,
            "projection seal counts are invalid",
        )
        _require(
            seal["run_manifest_sha256"] == _sha256_json(run),
            "projection seal run digest mismatch",
        )
        _require(
            seal["record_count"] == summary["record_count"]
            and seal["last_ordinal"] == summary["last_ordinal"],
            "projection seal counts mismatch",
        )
        _require(seal["sealed_after_stop_record"] is True, "projection seal predates stop")
        capture_material = {
            "contract_id": CAPTURE_CONTRACT_ID,
            "run_id": run["run_id"],
            "run_manifest_sha256": seal["run_manifest_sha256"],
            "records_sha256": seal["records_sha256"],
            "semantic_fingerprints_sha256": seal["semantic_fingerprints_sha256"],
            "record_count": seal["record_count"],
            "last_ordinal": seal["last_ordinal"],
        }
        _require(
            seal["capture_id"]
            == "crucible-worldgen-capture:sha256:" + _sha256_json(capture_material),
            "projection capture ID digest mismatch",
        )
        _require(
            summary["coverage_state"] == "complete"
            and summary["dropped_record_count"] == 0
            and summary["limitations"] == [],
            "completed projection is not complete zero-drop evidence",
        )
    else:
        _require(value["completion_seal"] is None, "incomplete projection contains a seal")
        residue = value["crash_residue"]
        _require(isinstance(residue, Mapping), "incomplete projection lacks residue")
        _exact_keys(residue, _RESIDUE_KEYS, "projection crash residue")
        _require(
            residue["reason"] in {"process_crash", "forced_termination"},
            "projection residue is not a crash or forced termination",
        )
        _require(
            type(residue["last_complete_ordinal"]) is int
            and residue["last_complete_ordinal"] == summary["last_ordinal"],
            "projection residue ordinal mismatch",
        )
        _require(
            residue["open_span_ids"] == summary["open_span_ids"],
            "projection residue open spans mismatch",
        )
        _require(type(residue["recoverable"]) is bool, "projection recoverable flag is invalid")
        _require(
            residue["diagnostic_sha256"] is None
            or _is_sha256(residue["diagnostic_sha256"]),
            "projection diagnostic digest is invalid",
        )
        residue_material = deepcopy(dict(residue))
        residue_id = residue_material.pop("residue_id")
        _require(
            residue_id
            == "crucible-worldgen-residue:sha256:" + _sha256_json(residue_material),
            "projection residue ID digest mismatch",
        )
        _require(
            summary["coverage_state"] == "incomplete",
            "incomplete projection summary is not incomplete",
        )
    return deepcopy(dict(value))


def _validate_checkpoint_rows(value: Any) -> tuple[dict[str, Any], ...]:
    _require(isinstance(value, list), "projection checkpoint rows must be an array")
    rows: list[dict[str, Any]] = []
    coordinates: set[tuple[int, int]] = set()
    for index, raw in enumerate(value):
        _require(isinstance(raw, Mapping), f"checkpoint row {index} must be an object")
        _exact_keys(raw, _CHECKPOINT_ROW_KEYS, f"checkpoint row {index}")
        x, z = raw["chunk_x"], raw["chunk_z"]
        _require(type(x) is int and type(z) is int, f"checkpoint row {index} coordinates invalid")
        coordinate = (x, z)
        _require(coordinate in FIXED_CHUNK_SET, f"checkpoint row {index} is outside fixture")
        coordinates.add(coordinate)
        for field in ("checkpoint_id", "stage_id", "canonicalization_id"):
            _require(
                isinstance(raw[field], str) and bool(raw[field]),
                f"checkpoint row {index} {field} is empty",
            )
        domains = raw["included_domains"]
        _require(
            isinstance(domains, list)
            and bool(domains)
            and all(isinstance(item, str) and bool(item) for item in domains)
            and domains == sorted(set(domains)),
            f"checkpoint row {index} domains are not canonical",
        )
        _require(
            _is_sha256(raw["semantic_state_sha256"]),
            f"checkpoint row {index} semantic digest is invalid",
        )
        row = deepcopy(dict(raw))
        row["actor"] = _projection_actor(raw["actor"], location=f"checkpoint row {index}")
        rows.append(row)
    _require(coordinates == set(FIXED_CHUNK_SET), "projection checkpoints lack fixture coverage")
    _require(
        rows == sorted(rows, key=canonical_json_bytes),
        "projection checkpoint rows are not canonically ordered",
    )
    return tuple(rows)


def _validate_rng_rows(value: Any) -> tuple[dict[str, Any], ...]:
    _require(isinstance(value, list), "projection RNG rows must be an array")
    rows: list[dict[str, Any]] = []
    coordinates: set[tuple[int, int]] = set()
    identities: set[tuple[Any, ...]] = set()
    for index, raw in enumerate(value):
        _require(isinstance(raw, Mapping), f"RNG row {index} must be an object")
        _exact_keys(raw, _RNG_ROW_KEYS, f"RNG row {index}")
        x, z = raw["chunk_x"], raw["chunk_z"]
        _require(type(x) is int and type(z) is int, f"RNG row {index} coordinates invalid")
        coordinate = (x, z)
        _require(coordinate in FIXED_CHUNK_SET, f"RNG row {index} is outside fixture")
        coordinates.add(coordinate)
        for field in ("detail", "stream_id", "algorithm_class", "operation"):
            _require(
                isinstance(raw[field], str) and bool(raw[field]),
                f"RNG row {index} {field} is empty",
            )
        _require(
            raw["call_ordinal"] is None
            or (type(raw["call_ordinal"]) is int and raw["call_ordinal"] >= 0),
            f"RNG row {index} call ordinal is invalid",
        )
        for field in ("parameters_sha256", "result_sha256", "rolling_digest"):
            _require(_is_sha256(raw[field]), f"RNG row {index} {field} is invalid")
        identity = (coordinate, raw["stream_id"], raw["detail"], raw["call_ordinal"])
        _require(identity not in identities, f"RNG row {index} duplicates a summary key")
        identities.add(identity)
        row = deepcopy(dict(raw))
        row["actor"] = _projection_actor(raw["actor"], location=f"RNG row {index}")
        rows.append(row)
    _require(bool(rows), "projection lacks cooperative RNG observations")
    _require(coordinates == set(FIXED_CHUNK_SET), "projection RNG rows lack fixture coverage")
    _require(rows == sorted(rows, key=canonical_json_bytes), "projection RNG rows are not ordered")
    return tuple(rows)


def _parse_projection_value(value: Any) -> CleanroomBundleProjection:
    _require(isinstance(value, Mapping), "bundle projection must be a JSON object")
    _exact_keys(value, _PROJECTION_KEYS, "bundle projection")
    _require(value["schema"] == CLEANROOM_BUNDLE_PROJECTION_SCHEMA, "projection schema mismatch")
    _require(
        value["evidence_class"] == CLEANROOM_BUNDLE_PROJECTION_EVIDENCE_CLASS,
        "projection evidence class mismatch",
    )
    execution_id = value["execution_id"]
    _require(
        isinstance(execution_id, str) and _EXECUTION_ID.fullmatch(execution_id) is not None,
        "projection execution ID is invalid",
    )
    _require(_is_sha256(value["source_bundle_sha256"]), "projection source digest is invalid")
    run = _validate_projection_run(value["run"])
    summary = _validate_projection_summary(value["summary"])
    publication = _validate_projection_publication(
        value["publication"], run=run, summary=summary
    )
    state = publication["state"]
    if state == "completed":
        checkpoints = _validate_checkpoint_rows(value["checkpoint_rows"])
        rng_rows = _validate_rng_rows(value["rng_rows"])
        _require(
            value["checkpoint_summary_sha256"]
            == _sha256_json([dict(row) for row in checkpoints]),
            "projection checkpoint summary digest mismatch",
        )
        _require(
            value["rng_summary_sha256"] == _sha256_json([dict(row) for row in rng_rows]),
            "projection RNG summary digest mismatch",
        )
    else:
        _require(value["checkpoint_rows"] == [], "incomplete projection contains checkpoints")
        _require(value["rng_rows"] == [], "incomplete projection contains RNG rows")
        _require(
            value["checkpoint_summary_sha256"] is None
            and value["rng_summary_sha256"] is None,
            "incomplete projection contains summary digests",
        )
        checkpoints = ()
        rng_rows = ()
    material = deepcopy(dict(value))
    projection_id = material.pop("projection_id")
    _require(
        projection_id
        == CLEANROOM_BUNDLE_PROJECTION_PREFIX + _sha256_json(material),
        "bundle projection content identity mismatch",
    )
    return CleanroomBundleProjection(
        projection_id=projection_id,
        execution_id=execution_id,
        source_bundle_sha256=value["source_bundle_sha256"],
        run=run,
        summary=summary,
        publication=publication,
        checkpoint_rows=checkpoints,
        checkpoint_summary_sha256=value["checkpoint_summary_sha256"],
        rng_rows=rng_rows,
        rng_summary_sha256=value["rng_summary_sha256"],
    )


def project_cleanroom_bundle(
    bundle: Mapping[str, Any], *, execution_id: str
) -> CleanroomBundleProjection:
    """Validate one full bundle and retain only its matrix-relevant receipt."""

    validate_bundle(bundle)
    return _project_already_validated_cleanroom_bundle(
        bundle,
        execution_id=execution_id,
        source_bundle_sha256=canonical_json_sha256(bundle),
    )


def _project_already_validated_cleanroom_bundle(
    bundle: Mapping[str, Any],
    *,
    execution_id: str,
    source_bundle_sha256: str,
) -> CleanroomBundleProjection:
    """Project a bundle retained behind the worker's validation capability."""

    _require(
        isinstance(execution_id, str) and _EXECUTION_ID.fullmatch(execution_id) is not None,
        "projection execution ID is invalid",
    )
    _require(
        _is_sha256(source_bundle_sha256),
        "projection source bundle digest is not SHA-256",
    )
    state = bundle["publication"]["state"]
    if state == "completed":
        view = _completed_bundle_projection(
            bundle,
            location=f"execution {execution_id}",
            already_validated=True,
        )
    else:
        view = _crash_bundle_projection(
            bundle,
            location=f"execution {execution_id}",
            already_validated=True,
        )
    checkpoint_rows = [deepcopy(row) for row in view.checkpoint_rows]
    rng_rows = [deepcopy(row) for row in view.rng_rows]
    material = _projection_material(
        execution_id=execution_id,
        source_bundle_sha256=source_bundle_sha256,
        run=bundle["run"],
        summary=bundle["summary"],
        publication=bundle["publication"],
        checkpoint_rows=checkpoint_rows,
        checkpoint_summary_sha256=view.checkpoint_sha256,
        rng_rows=rng_rows,
        rng_summary_sha256=view.rng_sha256,
    )
    document = {
        "projection_id": CLEANROOM_BUNDLE_PROJECTION_PREFIX + _sha256_json(material),
        **material,
    }
    return _parse_projection_value(document)


def _project_validated_cleanroom_bundle_publication(
    publication: _ValidatedBundlePublication,
    write_receipt: _ValidatedBundleWriteReceipt,
    *,
    execution_id: str,
) -> CleanroomBundleProjection:
    """Project the exact in-memory bundle cited by an atomic-write receipt."""

    bundle = _validated_bundle_from_write_receipt(publication, write_receipt)
    return _project_already_validated_cleanroom_bundle(
        bundle,
        execution_id=execution_id,
        source_bundle_sha256=write_receipt.canonical_json_sha256,
    )


def validate_cleanroom_bundle_projection(
    projection: CleanroomBundleProjection,
) -> CleanroomBundleProjection:
    _require(
        isinstance(projection, CleanroomBundleProjection),
        "bundle projection has not passed strict admission",
    )
    return _parse_projection_value(projection.as_dict())


def parse_cleanroom_bundle_projection(encoded: bytes | str) -> CleanroomBundleProjection:
    if isinstance(encoded, str):
        try:
            raw = encoded.encode("utf-8")
        except UnicodeError as exc:
            raise CaptureValidationError(
                f"bundle projection is not valid UTF-8 text: {exc}"
            ) from exc
    elif isinstance(encoded, bytes):
        raw = encoded
    else:
        raise CaptureValidationError("bundle projection input must be bytes or text")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeError, _DuplicateJsonKey, json.JSONDecodeError, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse bundle projection: {exc}") from exc
    return _parse_projection_value(value)


def load_cleanroom_bundle_projection(path: Path | str) -> CleanroomBundleProjection:
    try:
        encoded = Path(path).read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot load bundle projection {path}: {exc}") from exc
    return parse_cleanroom_bundle_projection(encoded)


def write_cleanroom_bundle_projection(
    path: Path | str,
    bundle: Mapping[str, Any],
    *,
    execution_id: str,
) -> CleanroomBundleProjection:
    """Validate/project one bundle and atomically publish its small receipt."""

    projection = project_cleanroom_bundle(bundle, execution_id=execution_id)
    _write_cleanroom_projection_document(path, projection)
    return projection


def _write_validated_cleanroom_bundle_projection(
    path: Path | str,
    publication: _ValidatedBundlePublication,
    write_receipt: _ValidatedBundleWriteReceipt,
    *,
    execution_id: str,
) -> CleanroomBundleProjection:
    """Project and write one worker-owned bundle without reopening validation."""

    projection = _project_validated_cleanroom_bundle_publication(
        publication,
        write_receipt,
        execution_id=execution_id,
    )
    _write_cleanroom_projection_document(path, projection)
    return projection


def _write_cleanroom_projection_document(
    path: Path | str,
    projection: CleanroomBundleProjection,
) -> None:
    """Atomically write one already parsed small projection document."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(projection.as_dict()))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


_CASE_REQUIREMENTS = {
    "aa-1": (True, "forward", False),
    "aa-2": (True, "forward", False),
    "observer-off": (False, "forward", False),
    "observer-on": (True, "forward", False),
    "order-forward": (True, "forward", False),
    "order-reverse": (True, "reverse", False),
    "restart-1": (True, "forward", False),
    "restart-2": (True, "forward", False),
    "crash-before-seal": (True, "forward", True),
}


def _comparison(
    comparison_id: str,
    left_case: str,
    right_case: str,
    aliases: Mapping[str, str],
    results: Mapping[str, FixtureResult],
    projections: Mapping[str, _BundleProjection | None],
    *,
    compare_bundle: bool,
) -> dict[str, Any]:
    left_execution = aliases[left_case]
    right_execution = aliases[right_case]
    left_result = results[left_execution]
    right_result = results[right_execution]
    semantic_equal = left_result.semantic_rows() == right_result.semantic_rows()
    row: dict[str, Any] = {
        "comparison_id": comparison_id,
        "left_case": left_case,
        "right_case": right_case,
        "left_execution_id": left_execution,
        "right_execution_id": right_execution,
        "semantic_equal": semantic_equal,
        "differing_chunks": [
            {"chunk_x": coordinate[0], "chunk_z": coordinate[1]}
            for coordinate in sorted(FIXED_CHUNK_SET)
            if {
                (item.chunk_x, item.chunk_z): item.semantic_state_sha256
                for item in left_result.selected_chunks
            }[coordinate]
            != {
                (item.chunk_x, item.chunk_z): item.semantic_state_sha256
                for item in right_result.selected_chunks
            }[coordinate]
        ],
    }
    if compare_bundle:
        left_projection = projections[left_execution]
        right_projection = projections[right_execution]
        _require(
            left_projection is not None and right_projection is not None,
            f"{comparison_id} lacks a completed bundle projection",
        )
        checkpoint_equal = (
            left_projection.checkpoint_rows == right_projection.checkpoint_rows
        )
        rng_equal = left_projection.rng_rows == right_projection.rng_rows
        row.update(
            {
                "checkpoint_equal": checkpoint_equal,
                "rng_equal": rng_equal,
                "passed": semantic_equal and checkpoint_equal and rng_equal,
            }
        )
    else:
        row.update(
            {
                "checkpoint_equal": None,
                "rng_equal": None,
                "passed": semantic_equal,
            }
        )
    return row


def _bundle_projection_resolver(
    execution: Any, *, crash: bool, location: str
) -> _BundleProjection | None:
    _require(isinstance(execution, CleanroomExecution), f"{location} is not a CleanroomExecution")
    if crash:
        _require(
            execution.canonical_bundle is not None,
            "crash-before-seal lacks canonical residue",
        )
        return _crash_bundle_projection(execution.canonical_bundle, location=location)
    if execution.observer_enabled:
        _require(
            execution.canonical_bundle is not None,
            f"observer-on {location} lacks a canonical bundle",
        )
        return _completed_bundle_projection(execution.canonical_bundle, location=location)
    _require(
        execution.canonical_bundle is None,
        f"observer-off {location} unexpectedly has a capture bundle",
    )
    return None


def _small_projection_resolver(
    execution: Any, *, crash: bool, location: str
) -> _BundleProjection | None:
    _require(
        isinstance(execution, CleanroomProjectionExecution),
        f"{location} is not a CleanroomProjectionExecution",
    )
    projection = execution.bundle_projection
    if crash or execution.observer_enabled:
        _require(projection is not None, f"{location} lacks a bundle projection")
        admitted = validate_cleanroom_bundle_projection(projection)
        _require(
            admitted.execution_id == execution.execution_id,
            f"{location} projection execution identity mismatch",
        )
        expected_state = "incomplete" if crash else "completed"
        _require(
            admitted.publication_state == expected_state,
            f"{location} projection publication state mismatch",
        )
        return _BundleProjection(
            publication_state=admitted.publication_state,
            publication_id=admitted.publication_id,
            checkpoint_rows=tuple(deepcopy(dict(row)) for row in admitted.checkpoint_rows),
            checkpoint_sha256=admitted.checkpoint_summary_sha256,
            rng_rows=tuple(deepcopy(dict(row)) for row in admitted.rng_rows),
            rng_sha256=admitted.rng_summary_sha256,
        )
    _require(
        projection is None,
        f"observer-off {location} unexpectedly has a bundle projection",
    )
    return None


def _evaluate_cleanroom_matrix(
    executions: Mapping[str, Any],
    case_aliases: Mapping[str, str],
    *,
    execution_type: type,
    projection_resolver: Callable[..., _BundleProjection | None],
) -> dict[str, Any]:
    _require(
        isinstance(executions, Mapping) and isinstance(case_aliases, Mapping),
        "matrix executions and case aliases must be mappings",
    )
    _require(
        set(case_aliases) == set(REQUIRED_CASES),
        "matrix case aliases do not match the required case set",
    )
    _require(bool(executions), "matrix contains no executions")
    _require(
        all(
            isinstance(execution_id, str)
            and _EXECUTION_ID.fullmatch(execution_id) is not None
            for execution_id in executions
        ),
        "matrix contains an invalid execution identity",
    )
    _require(
        all(isinstance(execution_id, str) for execution_id in case_aliases.values()),
        "matrix case aliases must contain execution identity strings",
    )
    referenced = set(case_aliases.values())
    _require(
        referenced == set(executions),
        "matrix executions must be exactly the executions referenced by cases",
    )
    _require(
        case_aliases["aa-1"] != case_aliases["aa-2"],
        "A/A comparison requires two distinct executions",
    )
    _require(
        case_aliases["restart-1"] != case_aliases["restart-2"],
        "restart comparison requires two distinct executions",
    )

    aliases_by_execution: dict[str, list[str]] = {
        execution_id: [] for execution_id in executions
    }
    for case_id in REQUIRED_CASES:
        execution_id = case_aliases[case_id]
        _require(
            isinstance(execution_id, str) and execution_id in executions,
            f"matrix case {case_id} references an unknown execution",
        )
        aliases_by_execution[execution_id].append(case_id)

    results: dict[str, FixtureResult] = {}
    projections: dict[str, _BundleProjection | None] = {}
    execution_rows: list[dict[str, Any]] = []
    for execution_id in sorted(executions):
        execution = executions[execution_id]
        _require(
            isinstance(execution, execution_type),
            f"execution {execution_id} is not a {execution_type.__name__}",
        )
        _require(
            execution.execution_id == execution_id
            and _EXECUTION_ID.fullmatch(execution_id) is not None,
            f"execution {execution_id!r} has an invalid custody identity",
        )
        _require(
            type(execution.observer_enabled) is bool,
            f"execution {execution_id} observer state must be boolean",
        )
        _require(
            isinstance(execution.route_order, str)
            and execution.route_order in {"forward", "reverse"},
            f"execution {execution_id} route order is invalid",
        )
        case_ids = sorted(aliases_by_execution[execution_id])
        expected_conditions = {
            _CASE_REQUIREMENTS[case_id] for case_id in case_ids
        }
        _require(
            len(expected_conditions) == 1,
            f"execution {execution_id} aliases cases with incompatible conditions",
        )
        expected_observer, expected_route, crash = next(iter(expected_conditions))
        _require(
            execution.observer_enabled == expected_observer,
            f"execution {execution_id} observer state does not match its cases",
        )
        _require(
            execution.route_order == expected_route,
            f"execution {execution_id} route order does not match its cases",
        )

        if crash:
            _require(
                execution.fixture_result is None,
                "crash-before-seal must not contain a completed fixture result",
            )
            projection = projection_resolver(
                execution,
                crash=True,
                location=f"execution {execution_id}",
            )
            _require(projection is not None, "crash-before-seal lacks projection residue")
            projections[execution_id] = projection
            execution_rows.append(
                {
                    "execution_id": execution_id,
                    "case_aliases": case_ids,
                    "observer_enabled": True,
                    "route_order": execution.route_order,
                    "fixture_result_sha256": None,
                    "semantic_map_sha256": None,
                    "runtime_mod_inventory_sha256": None,
                    "publication_state": projection.publication_state,
                    "publication_id": projection.publication_id,
                    "checkpoint_summary_sha256": None,
                    "rng_summary_sha256": None,
                }
            )
            continue

        result = execution.fixture_result
        _require(
            isinstance(result, FixtureResult),
            f"execution {execution_id} lacks an admitted fixture result",
        )
        _require(
            result.route_order == execution.route_order,
            f"execution {execution_id} result route order mismatch",
        )
        results[execution_id] = result
        projection = projection_resolver(
            execution,
            crash=False,
            location=f"execution {execution_id}",
        )
        projections[execution_id] = projection
        execution_rows.append(
            {
                "execution_id": execution_id,
                "case_aliases": case_ids,
                "observer_enabled": execution.observer_enabled,
                "route_order": execution.route_order,
                "fixture_result_sha256": result.artifact_sha256,
                "semantic_map_sha256": result.semantic_map_sha256(),
                "runtime_mod_inventory_sha256": result.inventory_sha256(),
                "publication_state": (
                    "not_captured" if projection is None else projection.publication_state
                ),
                "publication_id": (
                    None if projection is None else projection.publication_id
                ),
                "checkpoint_summary_sha256": (
                    None if projection is None else projection.checkpoint_sha256
                ),
                "rng_summary_sha256": (
                    None if projection is None else projection.rng_sha256
                ),
            }
        )

    normal_results = list(results.values())
    semantic_consistent = len(
        {result.semantic_map_sha256() for result in normal_results}
    ) == 1
    inventory_consistent = len(
        {result.inventory_sha256() for result in normal_results}
    ) == 1
    completed_projections = [
        projection
        for projection in projections.values()
        if projection is not None and projection.publication_state == "completed"
    ]
    checkpoint_consistent = len(
        {projection.checkpoint_sha256 for projection in completed_projections}
    ) == 1
    rng_consistent = len(
        {projection.rng_sha256 for projection in completed_projections}
    ) == 1

    comparisons = [
        _comparison(
            "aa-determinism",
            "aa-1",
            "aa-2",
            case_aliases,
            results,
            projections,
            compare_bundle=True,
        ),
        _comparison(
            "observer-neutrality",
            "observer-off",
            "observer-on",
            case_aliases,
            results,
            projections,
            compare_bundle=False,
        ),
        _comparison(
            "route-order-invariance",
            "order-forward",
            "order-reverse",
            case_aliases,
            results,
            projections,
            compare_bundle=True,
        ),
        _comparison(
            "restart-stability",
            "restart-1",
            "restart-2",
            case_aliases,
            results,
            projections,
            compare_bundle=True,
        ),
    ]
    crash_projection = projections[case_aliases["crash-before-seal"]]
    crash_passed = (
        crash_projection is not None
        and crash_projection.publication_state == "incomplete"
    )
    global_checks = {
        "semantic_maps_coordinatewise_consistent": semantic_consistent,
        "runtime_mod_inventory_consistent": inventory_consistent,
        "cooperative_checkpoints_consistent": checkpoint_consistent,
        "cooperative_rng_summaries_consistent": rng_consistent,
        "crash_is_incomplete_and_unsealed": crash_passed,
    }
    passed = all(global_checks.values()) and all(
        comparison["passed"] for comparison in comparisons
    )
    material = {
        "schema": MATRIX_EVALUATION_SCHEMA,
        "evidence_class": "evaluation_only",
        "fixed_world_seed_sha256": FIXED_WORLD_SEED_SHA256,
        "selector_sha256": SELECTOR_SHA256,
        "required_cases": list(REQUIRED_CASES),
        "case_aliases": [
            {"case_id": case_id, "execution_id": case_aliases[case_id]}
            for case_id in REQUIRED_CASES
        ],
        "executions": execution_rows,
        "comparisons": comparisons,
        "global_checks": global_checks,
        "matrix_state": "passed" if passed else "failed",
    }
    return {
        "evaluation_id": MATRIX_EVALUATION_PREFIX + _sha256_json(material),
        **material,
    }


def evaluate_cleanroom_matrix(
    executions: Mapping[str, CleanroomExecution],
    case_aliases: Mapping[str, str],
) -> dict[str, Any]:
    """Evaluate exact-runtime fixture results and full canonical bundles.

    This original API is retained for bounded captures. Large retained cases
    should be projected one process at a time and passed to
    :func:`evaluate_cleanroom_projection_matrix` instead.
    """

    return _evaluate_cleanroom_matrix(
        executions,
        case_aliases,
        execution_type=CleanroomExecution,
        projection_resolver=_bundle_projection_resolver,
    )


def evaluate_cleanroom_projection_matrix(
    executions: Mapping[str, CleanroomProjectionExecution],
    case_aliases: Mapping[str, str],
) -> dict[str, Any]:
    """Evaluate admitted small projections without loading their source bundles."""

    evaluation = _evaluate_cleanroom_matrix(
        executions,
        case_aliases,
        execution_type=CleanroomProjectionExecution,
        projection_resolver=_small_projection_resolver,
    )
    projections_by_execution: dict[str, CleanroomBundleProjection | None] = {}
    for execution_id, execution in executions.items():
        projection = execution.bundle_projection
        projections_by_execution[execution_id] = (
            None
            if projection is None
            else validate_cleanroom_bundle_projection(projection)
        )
    execution_rows = deepcopy(evaluation["executions"])
    for row in execution_rows:
        projection = projections_by_execution[row["execution_id"]]
        row["bundle_projection_id"] = (
            None if projection is None else projection.projection_id
        )
        row["source_bundle_sha256"] = (
            None if projection is None else projection.source_bundle_sha256
        )
    material = {
        key: deepcopy(value)
        for key, value in evaluation.items()
        if key not in {"evaluation_id", "schema", "executions"}
    }
    material.update(
        {
            "schema": PROJECTION_MATRIX_EVALUATION_SCHEMA,
            "matrix_input_mode": "validated_bundle_projections",
            "executions": execution_rows,
        }
    )
    # Preserve the stable field order used by the original evaluation material;
    # content identity itself is based on sorted canonical JSON.
    return {
        "evaluation_id": PROJECTION_MATRIX_EVALUATION_PREFIX + _sha256_json(material),
        **material,
    }
