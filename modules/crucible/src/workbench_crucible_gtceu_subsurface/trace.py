"""Validate bounded controlled GTCEu ore selection and placement evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


GTCEU_SUBSURFACE_TRACE_FORMAT = "workbench-crucible-gtceu-subsurface-trace-v1"
GTCEU_SUBSURFACE_TRACE_PREFIX = "crucible-gtceu-subsurface:sha256:"
GTCEU_SUBSURFACE_TRACE_PROFILE = "gtceu-1.12.2-2.8.10-subsurface-trace-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
MAX_DEPOSITS = 10_000
MAX_DECISIONS = 250_000
MAX_CHUNKS = 1_024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INVENTORY_ID_RE = re.compile(r"^crucible-gtceu-worldgen:sha256:[0-9a-f]{64}$")
_IMPACT_ID_RE = re.compile(
    r"^crucible-gtceu-worldgen-impact:sha256:[0-9a-f]{64}$"
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "format",
        "schema_version",
        "trace_id",
        "canonicalization_id",
        "adapter_profile",
        "capture",
        "coverage",
        "deposits",
        "decisions",
        "boundaries",
    }
)
_BOUNDARIES = {
    "atlas_interpretation_performed": False,
    "bedrock_fluid_cells_included": False,
    "final_state_included": False,
    "gregtech_version_specific": True,
    "strata_remains_final_state_source": True,
}
_OUTCOMES = frozenset(
    {"density-rejected", "host-rejected", "write-failed", "written"}
)
_OUTCOME_TO_COUNT = {
    "density-rejected": "density_rejected_count",
    "host-rejected": "host_rejected_count",
    "write-failed": "other_rejected_count",
    "written": "successful_write_count",
}


class GtceuSubsurfaceTraceValidationError(ValueError):
    """Raised when a GTCEu subsurface trace is not self-consistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GtceuSubsurfaceTraceValidationError(message)


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
        raise GtceuSubsurfaceTraceValidationError(
            f"cannot canonically encode GTCEu subsurface trace: {exc}"
        ) from exc


def _exact_keys(value: Any, expected: set[str] | frozenset[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == set(expected),
        f"{context} fields mismatch: missing={sorted(set(expected) - actual)!r}, "
        f"unknown={sorted(actual - set(expected))!r}",
    )
    return value


def _text(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be text")
    return value


def _integer(value: Any, context: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{context} must be an integer",
    )
    if minimum is not None:
        _require(value >= minimum, f"{context} must be at least {minimum}")
    if maximum is not None:
        _require(value <= maximum, f"{context} must be at most {maximum}")
    return value


def _boolean(value: Any, context: str) -> bool:
    _require(isinstance(value, bool), f"{context} must be boolean")
    return value


def _position(value: Any, context: str) -> tuple[int, int, int]:
    row = _exact_keys(value, {"x", "y", "z"}, context)
    return (
        _integer(row["x"], f"{context}.x"),
        _integer(row["y"], f"{context}.y", minimum=0, maximum=255),
        _integer(row["z"], f"{context}.z"),
    )


def _window(value: Any) -> dict[str, int]:
    row = _exact_keys(
        value,
        {"min_chunk_x", "min_chunk_z", "chunk_size_x", "chunk_size_z", "halo_chunks"},
        "capture.chunk_window",
    )
    window = {
        "min_chunk_x": _integer(row["min_chunk_x"], "capture.chunk_window.min_chunk_x"),
        "min_chunk_z": _integer(row["min_chunk_z"], "capture.chunk_window.min_chunk_z"),
        "chunk_size_x": _integer(row["chunk_size_x"], "capture.chunk_window.chunk_size_x", minimum=1),
        "chunk_size_z": _integer(row["chunk_size_z"], "capture.chunk_window.chunk_size_z", minimum=1),
        "halo_chunks": _integer(row["halo_chunks"], "capture.chunk_window.halo_chunks", minimum=0),
    }
    _require(
        window["chunk_size_x"] * window["chunk_size_z"] <= MAX_CHUNKS,
        f"capture exceeds {MAX_CHUNKS} chunks",
    )
    return window


def _inside_window(position: tuple[int, int, int], window: Mapping[str, int]) -> bool:
    chunk_x = position[0] // 16
    chunk_z = position[2] // 16
    halo = window["halo_chunks"]
    return (
        window["min_chunk_x"] - halo
        <= chunk_x
        < window["min_chunk_x"] + window["chunk_size_x"] + halo
        and window["min_chunk_z"] - halo
        <= chunk_z
        < window["min_chunk_z"] + window["chunk_size_z"] + halo
    )


def _validate_deposit(value: Any, index: int) -> tuple[str, dict[str, Any]]:
    context = f"deposits[{index}]"
    row = _exact_keys(
        value,
        {
            "deposit_instance_id",
            "definition_path",
            "grid_x",
            "grid_z",
            "selection_ordinal",
            "effective_weight",
            "priority",
            "count_as_vein",
            "center",
            "bounds",
            "rng",
            "cache",
            "placement",
        },
        context,
    )
    deposit_id = _text(row["deposit_instance_id"], f"{context}.deposit_instance_id")
    definition_path = _text(row["definition_path"], f"{context}.definition_path")
    _require(
        definition_path.startswith("worldgen/vein/") and definition_path.endswith(".json"),
        f"{context}.definition_path is not a GTCEu ore definition",
    )
    center = _position(row["center"], f"{context}.center")
    bounds_row = _exact_keys(
        row["bounds"],
        {"min_x", "min_y", "min_z", "max_x", "max_y", "max_z"},
        f"{context}.bounds",
    )
    bounds = {
        key: _integer(
            bounds_row[key],
            f"{context}.bounds.{key}",
            minimum=0 if key in {"min_y", "max_y"} else None,
            maximum=255 if key in {"min_y", "max_y"} else None,
        )
        for key in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")
    }
    _require(
        bounds["min_x"] <= bounds["max_x"]
        and bounds["min_y"] <= bounds["max_y"]
        and bounds["min_z"] <= bounds["max_z"],
        f"{context}.bounds are inverted",
    )
    _require(
        bounds["min_x"] <= center[0] <= bounds["max_x"]
        and bounds["min_y"] <= center[1] <= bounds["max_y"]
        and bounds["min_z"] <= center[2] <= bounds["max_z"],
        f"{context}.center is outside deposit bounds",
    )
    rng = _exact_keys(
        row["rng"], {"algorithm", "seed_material_sha256", "lane"}, f"{context}.rng"
    )
    _text(rng["algorithm"], f"{context}.rng.algorithm")
    digest = _text(rng["seed_material_sha256"], f"{context}.rng.seed_material_sha256")
    _require(bool(_SHA256_RE.fullmatch(digest)), f"{context}.rng seed digest is invalid")
    _text(rng["lane"], f"{context}.rng.lane")
    cache = _exact_keys(row["cache"], {"hit", "epoch"}, f"{context}.cache")
    _boolean(cache["hit"], f"{context}.cache.hit")
    _require(
        (isinstance(cache["epoch"], str) and bool(cache["epoch"]))
        or (isinstance(cache["epoch"], int) and not isinstance(cache["epoch"], bool)),
        f"{context}.cache.epoch must be nonempty text or an integer",
    )
    placement = _exact_keys(
        row["placement"],
        {
            "candidate_count",
            "density_rejected_count",
            "host_rejected_count",
            "other_rejected_count",
            "successful_write_count",
        },
        f"{context}.placement",
    )
    counts = {
        key: _integer(placement[key], f"{context}.placement.{key}", minimum=0)
        for key in placement
    }
    _require(
        counts["candidate_count"]
        == counts["density_rejected_count"]
        + counts["host_rejected_count"]
        + counts["other_rejected_count"]
        + counts["successful_write_count"],
        f"{context}.placement outcomes do not sum to candidate_count",
    )
    _integer(row["grid_x"], f"{context}.grid_x")
    _integer(row["grid_z"], f"{context}.grid_z")
    _integer(row["selection_ordinal"], f"{context}.selection_ordinal", minimum=0)
    _integer(row["effective_weight"], f"{context}.effective_weight")
    _integer(row["priority"], f"{context}.priority")
    _boolean(row["count_as_vein"], f"{context}.count_as_vein")
    return deposit_id, {"center": center, "bounds": bounds, "placement": counts}


def parse_gtceu_subsurface_trace(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach one content-addressed controlled trace."""

    _require(isinstance(value, Mapping), "GTCEu subsurface trace must be an object")
    report = deepcopy(dict(value))
    _exact_keys(report, _TOP_LEVEL_KEYS, "trace")
    _require(report["format"] == GTCEU_SUBSURFACE_TRACE_FORMAT, "trace format drift")
    _require(report["schema_version"] == 1, "trace schema version drift")
    _require(
        report["canonicalization_id"] == CANONICALIZATION_ID,
        "trace canonicalization drift",
    )
    _require(report["boundaries"] == _BOUNDARIES, "trace boundaries drift")

    adapter = _exact_keys(
        report["adapter_profile"],
        {"id", "inventory_id", "impact_inventory_id"},
        "adapter_profile",
    )
    _require(adapter["id"] == GTCEU_SUBSURFACE_TRACE_PROFILE, "trace adapter drift")
    _require(
        isinstance(adapter["inventory_id"], str)
        and bool(_INVENTORY_ID_RE.fullmatch(adapter["inventory_id"])),
        "trace inventory ID is invalid",
    )
    _require(
        isinstance(adapter["impact_inventory_id"], str)
        and bool(_IMPACT_ID_RE.fullmatch(adapter["impact_inventory_id"])),
        "trace impact inventory ID is invalid",
    )

    capture = _exact_keys(
        report["capture"],
        {
            "run_id",
            "state",
            "runtime_artifact_set_sha256",
            "world_seed",
            "dimension_id",
            "chunk_window",
        },
        "capture",
    )
    _text(capture["run_id"], "capture.run_id")
    _require(capture["state"] in {"complete", "partial", "incomplete"}, "capture.state is invalid")
    artifact_digest = _text(
        capture["runtime_artifact_set_sha256"], "capture.runtime_artifact_set_sha256"
    )
    _require(bool(_SHA256_RE.fullmatch(artifact_digest)), "runtime artifact digest is invalid")
    _require(
        (isinstance(capture["world_seed"], int) and not isinstance(capture["world_seed"], bool))
        or (isinstance(capture["world_seed"], str) and bool(capture["world_seed"])),
        "capture.world_seed must be an integer or nonempty text",
    )
    _integer(capture["dimension_id"], "capture.dimension_id")
    window = _window(capture["chunk_window"])

    coverage = _exact_keys(
        report["coverage"],
        {"selected_definitions_complete", "position_decisions_complete", "truncated", "selector"},
        "coverage",
    )
    selected_complete = _boolean(
        coverage["selected_definitions_complete"], "coverage.selected_definitions_complete"
    )
    positions_complete = _boolean(
        coverage["position_decisions_complete"], "coverage.position_decisions_complete"
    )
    truncated = _boolean(coverage["truncated"], "coverage.truncated")
    _text(coverage["selector"], "coverage.selector")
    if capture["state"] != "complete":
        _require(
            not selected_complete and not positions_complete,
            "non-complete capture cannot claim complete selection or position coverage",
        )
    _require(
        not (truncated and positions_complete),
        "truncated trace cannot claim complete position decisions",
    )

    deposits = report["deposits"]
    decisions = report["decisions"]
    _require(isinstance(deposits, list), "deposits must be an array")
    _require(isinstance(decisions, list), "decisions must be an array")
    _require(len(deposits) <= MAX_DEPOSITS, f"trace exceeds {MAX_DEPOSITS} deposits")
    _require(len(decisions) <= MAX_DECISIONS, f"trace exceeds {MAX_DECISIONS} decisions")

    deposit_index: dict[str, dict[str, Any]] = {}
    for index, deposit in enumerate(deposits):
        deposit_id, normalized = _validate_deposit(deposit, index)
        _require(deposit_id not in deposit_index, f"duplicate deposit ID: {deposit_id}")
        _require(
            _inside_window(normalized["center"], window),
            f"deposit center is outside capture scope: {deposit_id}",
        )
        deposit_index[deposit_id] = normalized

    decision_ids: set[str] = set()
    deposit_positions: set[tuple[str, int, int, int]] = set()
    observed_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for index, decision in enumerate(decisions):
        context = f"decisions[{index}]"
        row = _exact_keys(
            decision,
            {
                "decision_id",
                "deposit_instance_id",
                "position",
                "outcome",
                "reason",
                "before_state",
                "after_state",
                "write_chain_id",
            },
            context,
        )
        decision_id = _text(row["decision_id"], f"{context}.decision_id")
        _require(decision_id not in decision_ids, f"duplicate decision ID: {decision_id}")
        decision_ids.add(decision_id)
        deposit_id = _text(row["deposit_instance_id"], f"{context}.deposit_instance_id")
        _require(deposit_id in deposit_index, f"decision references unknown deposit: {deposit_id}")
        position = _position(row["position"], f"{context}.position")
        position_key = (deposit_id, *position)
        _require(
            position_key not in deposit_positions,
            f"duplicate deposit-position decision: {deposit_id}@{position}",
        )
        deposit_positions.add(position_key)
        _require(_inside_window(position, window), f"decision is outside capture scope: {decision_id}")
        bounds = deposit_index[deposit_id]["bounds"]
        _require(
            bounds["min_x"] <= position[0] <= bounds["max_x"]
            and bounds["min_y"] <= position[1] <= bounds["max_y"]
            and bounds["min_z"] <= position[2] <= bounds["max_z"],
            f"decision is outside deposit bounds: {decision_id}",
        )
        outcome = row["outcome"]
        _require(outcome in _OUTCOMES, f"{context}.outcome is invalid")
        _text(row["reason"], f"{context}.reason")
        for key in ("before_state", "after_state", "write_chain_id"):
            _require(
                row[key] is None or (isinstance(row[key], str) and bool(row[key])),
                f"{context}.{key} must be null or nonempty text",
            )
        if outcome == "written":
            _require(row["after_state"] is not None, f"written decision lacks after_state: {decision_id}")
        else:
            _require(row["after_state"] is None, f"rejected decision cannot claim after_state: {decision_id}")
        observed_counts[deposit_id][_OUTCOME_TO_COUNT[outcome]] += 1

    for deposit_id, normalized in deposit_index.items():
        declared = normalized["placement"]
        observed = observed_counts[deposit_id]
        for count_key in _OUTCOME_TO_COUNT.values():
            if positions_complete:
                _require(
                    observed[count_key] == declared[count_key],
                    f"complete decision count disagrees for {deposit_id}.{count_key}",
                )
            else:
                _require(
                    observed[count_key] <= declared[count_key],
                    f"partial decision count exceeds summary for {deposit_id}.{count_key}",
                )

    trace_id = report["trace_id"]
    _require(
        isinstance(trace_id, str) and trace_id.startswith(GTCEU_SUBSURFACE_TRACE_PREFIX),
        "trace ID is invalid",
    )
    identity = deepcopy(report)
    identity["trace_id"] = ""
    expected = GTCEU_SUBSURFACE_TRACE_PREFIX + hashlib.sha256(
        canonical_json_bytes(identity)
    ).hexdigest()
    _require(trace_id == expected, "GTCEu subsurface trace ID drift")
    return report


def build_gtceu_subsurface_trace(
    *,
    adapter_profile: Mapping[str, Any],
    capture: Mapping[str, Any],
    coverage: Mapping[str, Any],
    deposits: list[Mapping[str, Any]],
    decisions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build and validate one receipt from already collected exact observations."""

    report: dict[str, Any] = {
        "format": GTCEU_SUBSURFACE_TRACE_FORMAT,
        "schema_version": 1,
        "trace_id": "",
        "canonicalization_id": CANONICALIZATION_ID,
        "adapter_profile": deepcopy(dict(adapter_profile)),
        "capture": deepcopy(dict(capture)),
        "coverage": deepcopy(dict(coverage)),
        "deposits": [deepcopy(dict(row)) for row in deposits],
        "decisions": [deepcopy(dict(row)) for row in decisions],
        "boundaries": dict(_BOUNDARIES),
    }
    report["trace_id"] = GTCEU_SUBSURFACE_TRACE_PREFIX + hashlib.sha256(
        canonical_json_bytes(report)
    ).hexdigest()
    return parse_gtceu_subsurface_trace(report)


def write_gtceu_subsurface_trace(path: Path, report: Mapping[str, Any]) -> None:
    """Atomically create, but never replace, one validated trace."""

    validated = parse_gtceu_subsurface_trace(report)
    requested = path.expanduser()
    _require(not requested.is_symlink(), f"trace output cannot be a symlink: {requested}")
    destination = requested.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(validated, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError as exc:
            raise GtceuSubsurfaceTraceValidationError(
                f"trace output already exists; choose a fresh path: {destination}"
            ) from exc
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
