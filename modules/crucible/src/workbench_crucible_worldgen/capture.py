"""Streaming same-run Observatory, World Studio, and Strata V2 adapter."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from workbench_crucible_strata_observation.observation import (
    parse_strata_observation_receipt,
)
from workbench_api.canonical import CANONICALIZER_ID, canonical_json_bytes, content_id

from .identity import WorldgenExecutionEnvelope, load_execution_envelope


RAW_FORMAT = "workbench-cleanroom-worldgen-observatory-raw-v1"
CAUSAL_FORMAT = "workbench-world-studio-causal-trace-v2"
CAUSAL_PREFIX = "WORLDGEN_PROTOTYPE_CAUSAL_V2"
RAW_AUDIT_FORMAT = "workbench-worldgen-population-capture-audit-v2"
CAUSAL_RECEIPT_FORMAT = "workbench-worldgen-causal-trace-receipt-v2"
JOIN_RECEIPT_FORMAT = "workbench-worldgen-same-run-join-receipt-v2"
STABILITY_FORMAT = "workbench-worldgen-stability-receipt-v2"
_MAX_LINE_BYTES = 16 * 1024 * 1024
_MAX_CAUSAL_RECORDS = 4096
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorldgenCaptureError(ValueError):
    """The same-run capture is incomplete, malformed, or identity-incompatible."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorldgenCaptureError(message)


def _sha_file(path: Path) -> tuple[str, int]:
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode), f"not a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    after = path.lstat()
    _require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        f"file changed while it was read: {path}",
    )
    return digest.hexdigest(), size


def _json_bytes(path: Path, label: str, maximum: int = 64 * 1024 * 1024) -> dict[str, Any]:
    digest, size = _sha_file(path)
    _require(size <= maximum, f"{label} exceeds {maximum} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorldgenCaptureError(f"cannot parse {label}: {exc}") from exc
    _require(type(value) is dict, f"{label} must be a JSON object")
    return value


def _seal(kind: str, body: Mapping[str, Any]) -> tuple[str, bytes, dict[str, Any]]:
    record_id = content_id(kind, body)
    value = {**body, "id": record_id}
    return record_id, canonical_json_bytes(value), value


def _window(envelope: WorldgenExecutionEnvelope) -> tuple[int, int, int, int]:
    value = envelope.to_dict()["run_plan"]
    region = value["region"]
    return region[0], region[1], region[2], region[3]


def _selected_chunks(envelope: WorldgenExecutionEnvelope) -> set[tuple[int, int]]:
    min_x, min_z, width, height = _window(envelope)
    return {
        (x, z)
        for x in range(min_x, min_x + width)
        for z in range(min_z, min_z + height)
    }


@dataclass(frozen=True, slots=True)
class PopulationCaptureAudit:
    audit_id: str
    envelope_id: str
    raw_sha256: str
    raw_size_bytes: int
    record_count: int
    record_type_counts: Mapping[str, int]
    hook_health: Mapping[str, Mapping[str, Any]]
    population_root_count: int
    event_listener_count: int
    terminal_write_count: int
    terminal_writes: tuple[Mapping[str, Any], ...]
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class CausalTraceReceipt:
    receipt_id: str
    envelope_id: str
    launch_log_sha256: str
    launch_log_size_bytes: int
    records: tuple[Mapping[str, Any], ...]
    gates: Mapping[tuple[int, int], Mapping[str, Any]]
    features: Mapping[tuple[int, int], Mapping[str, Any]]
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class SameRunJoinReceipt:
    receipt_id: str
    envelope_id: str
    run_plan_id: str
    context_ref_id: str
    input_binding_id: str
    capture_audit_id: str
    causal_receipt_id: str
    strata_receipt_id: str
    raw_sha256: str
    launch_log_sha256: str
    scan_sha256: str
    sites: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]
    value: Mapping[str, Any]
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return dict(self.value)


@dataclass(frozen=True, slots=True)
class StabilityReceipt:
    receipt_id: str
    run_plan_id: str
    outcome: str
    sites: tuple[Mapping[str, Any], ...]
    value: Mapping[str, Any]
    canonical_bytes: bytes

    def to_dict(self) -> dict[str, Any]:
        return dict(self.value)


def _load_json_schema(path: Path):
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError as exc:
        raise WorldgenCaptureError("jsonschema is required for worldgen V2 capture") from exc
    schema = _json_bytes(path, "JSON schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise WorldgenCaptureError(f"invalid JSON schema {path}: {exc}") from exc
    return Draft202012Validator(schema), schema


def audit_population_capture(
    raw_path: Path,
    *,
    envelope: WorldgenExecutionEnvelope,
    raw_schema_path: Path,
    probe_plan_path: Path,
    requested_mode: str = "trace",
) -> PopulationCaptureAudit:
    """Stream-validate one control-free V1 prefix under a sealed V2 envelope."""

    envelope = load_execution_envelope(envelope.canonical_bytes)
    validator, schema = _load_json_schema(raw_schema_path)
    schema_digest, _ = _sha_file(raw_schema_path)
    plan = _json_bytes(probe_plan_path, "probe plan")
    transport = plan.get("raw_transport")
    _require(type(transport) is dict, "probe plan lacks raw transport")
    _require(transport.get("schema_sha256") == schema_digest, "probe plan/schema digest differs")
    _require(schema.get("properties", {}).get("format", {}).get("const") == RAW_FORMAT, "raw schema format differs")
    planned_hooks = {
        item["raw_hook_id"]: item
        for item in plan.get("hooks", [])
        if type(item) is dict and type(item.get("raw_hook_id")) is str
    }
    required_hooks = {
        "cleanroom-worldgen:chunk.populate_neighbors",
        "cleanroom-worldgen:chunk.populate_owned",
        "cleanroom-worldgen:chunk.generator_populate_call",
        "cleanroom-worldgen:event_bus.post",
        "cleanroom-worldgen:event_bus.listener_invoke",
        "cleanroom-worldgen:write.world_api",
        "cleanroom-worldgen:write.chunk_storage",
    }
    _require(required_hooks <= set(planned_hooks), "probe plan lacks W01 hooks")

    expected_chunks = _selected_chunks(envelope)
    counts: Counter[str] = Counter()
    health: dict[str, dict[str, Any]] = {}
    span_stacks: dict[tuple[str, int], list[str]] = defaultdict(list)
    entered: set[str] = set()
    closed: set[str] = set()
    terminal_writes: list[dict[str, Any]] = []
    population_roots = 0
    event_listeners = 0
    expected_sequence = 0
    thread_sequences: dict[tuple[str, int], int] = {}
    prior_lamport = -1
    digest = hashlib.sha256()
    size = 0
    before = raw_path.lstat()
    _require(stat.S_ISREG(before.st_mode), f"raw capture is not regular: {raw_path}")
    try:
        stream = raw_path.open("rb")
    except OSError as exc:
        raise WorldgenCaptureError(f"cannot open raw capture: {exc}") from exc
    with stream:
        for line_number, encoded in enumerate(stream, 1):
            size += len(encoded)
            digest.update(encoded)
            _require(len(encoded) <= _MAX_LINE_BYTES, f"raw line {line_number} is oversized")
            _require(encoded.endswith(b"\n") and encoded != b"\n", f"raw line {line_number} is not LF framed")
            try:
                row = json.loads(encoded[:-1].decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise WorldgenCaptureError(f"cannot parse raw line {line_number}: {exc}") from exc
            errors = tuple(validator.iter_errors(row))
            _require(not errors, f"raw line {line_number} fails V1 schema: {errors[0].message if errors else ''}")
            _require(row["sequence"] == expected_sequence, f"raw sequence gap at line {line_number}")
            expected_sequence += 1
            _require(row["capture_id"] == envelope.envelope_id, f"raw line {line_number} cites another envelope")
            coverage = row["coverage"]
            _require(
                coverage == {
                    "detail_state": "complete",
                    "dropped_record_count": 0,
                    "mode": requested_mode,
                },
                f"raw line {line_number} reports incomplete coverage",
            )
            order = row["order"]
            thread = (order["thread_name"], order["thread_id"])
            expected_thread = thread_sequences.get(thread, 0)
            _require(order["thread_sequence"] == expected_thread, f"raw thread sequence gap at line {line_number}")
            thread_sequences[thread] = expected_thread + 1
            _require(order["lamport"] > prior_lamport, f"raw Lamport order regressed at line {line_number}")
            prior_lamport = order["lamport"]
            record_type = row["record_type"]
            counts[record_type] += 1
            causality = row["causality"]
            if record_type == "probe_health":
                payload = row["payload"]
                hook_id = payload["hook_id"]
                _require(hook_id in planned_hooks, f"unplanned hook health {hook_id}")
                planned = planned_hooks[hook_id]
                _require(
                    payload["health_state"] in {"applied_not_reached", "reached"}
                    and payload["expected_injection_count"] == planned["expected_cardinality"]
                    and payload["observed_injection_count"] == planned["expected_cardinality"]
                    and payload["original_class_sha256"] != "0" * 64
                    and payload["transformed_class_sha256"] != "0" * 64,
                    f"hook health is not exact for {hook_id}",
                )
                health[hook_id] = dict(payload)
            elif record_type == "span_enter":
                span_id = causality["span_id"]
                _require(span_id not in entered, f"duplicate span enter {span_id}")
                parent = causality["parent_span_id"]
                stack = span_stacks[thread]
                _require(parent == (stack[-1] if stack else None), f"span parent mismatch {span_id}")
                if parent is None:
                    operation = row["payload"]["operation_id"]
                    _require(
                        operation in {
                            "cleanroom-worldgen:chunk.populate_neighbors",
                            "cleanroom-worldgen:chunk.populate_owned",
                        },
                        f"unselected root operation {operation}",
                    )
                    scope = row["scope"]
                    _require((scope["chunk_x"], scope["chunk_z"]) in expected_chunks, "population root lies outside selector")
                    population_roots += 1
                entered.add(span_id)
                stack.append(span_id)
            elif record_type in {"span_return", "span_throw"}:
                span_id = causality["span_id"]
                stack = span_stacks[thread]
                _require(stack and stack[-1] == span_id, f"span close is unbalanced for {span_id}")
                stack.pop()
                _require(span_id in entered and span_id not in closed, f"span close is duplicated for {span_id}")
                closed.add(span_id)
                _require(record_type != "span_throw", f"captured span threw: {span_id}")
            elif record_type == "block_write":
                _require(row["outcome"]["state"] != "threw", "captured block write threw")
                if row["payload"]["terminal"]:
                    terminal_writes.append(row)
            elif record_type == "event_dispatch":
                payload = row["payload"]
                if payload["boundary"] == "listener_return":
                    event_listeners += 1
            elif record_type == "diagnostic":
                _require(row["payload"].get("severity") != "error", "capture contains an error diagnostic")
    after = raw_path.lstat()
    _require(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        "raw capture changed while audited",
    )
    _require(expected_sequence > 0, "raw capture is empty")
    _require(all(not stack for stack in span_stacks.values()), "raw capture has open spans")
    _require(entered == closed, "raw capture has incomplete spans")
    _require(
        required_hooks <= {
            hook_id
            for hook_id, payload in health.items()
            if payload["health_state"] == "reached"
        },
        "required hook health did not finish reached",
    )
    _require(population_roots >= len(expected_chunks), "population selector did not cover every requested chunk")
    _require(event_listeners > 0, "capture contains no completed listener invocation")
    _require(bool(terminal_writes), "capture contains no terminal block writes")
    raw_digest = digest.hexdigest()
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "capture_mode": requested_mode,
        "dropped_record_count": 0,
        "event_listener_count": event_listeners,
        "execution_envelope_id": envelope.envelope_id,
        "format": RAW_AUDIT_FORMAT,
        "hook_health": [health[key] for key in sorted(health)],
        "kind": "worldgen-population-capture-audit",
        "open_span_count": 0,
        "open_write_count": 0,
        "population_root_count": population_roots,
        "raw_sha256": raw_digest,
        "raw_size_bytes": size,
        "record_count": expected_sequence,
        "record_type_counts": dict(sorted(counts.items())),
        "schema_version": 2,
        "selector": {
            "maximum_chunk_exclusive": {
                "x": _window(envelope)[0] + _window(envelope)[2],
                "z": _window(envelope)[1] + _window(envelope)[3],
            },
            "minimum_chunk_inclusive": {
                "x": _window(envelope)[0], "z": _window(envelope)[1]
            },
        },
        "terminal_write_count": len(terminal_writes),
    }
    audit_id, canonical, _ = _seal("worldgen-population-capture-audit", body)
    return PopulationCaptureAudit(
        audit_id,
        envelope.envelope_id,
        raw_digest,
        size,
        expected_sequence,
        dict(sorted(counts.items())),
        {key: health[key] for key in sorted(health)},
        population_roots,
        event_listeners,
        len(terminal_writes),
        tuple(terminal_writes),
        canonical,
    )


class _JavaRandom:
    _MULTIPLIER = 0x5DEECE66D
    _ADDEND = 0xB
    _MASK = (1 << 48) - 1

    def __init__(self, seed: int):
        self.seed = (seed ^ self._MULTIPLIER) & self._MASK

    def _next(self, bits: int) -> int:
        self.seed = (self.seed * self._MULTIPLIER + self._ADDEND) & self._MASK
        return self.seed >> (48 - bits)

    def next_int(self, bound: int) -> int:
        if bound & (bound - 1) == 0:
            return (bound * self._next(31)) >> 31
        while True:
            bits = self._next(31)
            value = bits % bound
            signed = (bits - value + (bound - 1)) & 0xFFFFFFFF
            if signed < 0x80000000:
                return value

    def next_boolean(self) -> bool:
        return self._next(1) != 0


def _signed_long(value: int) -> int:
    value &= (1 << 64) - 1
    return value - (1 << 64) if value >= (1 << 63) else value


def _expected_stage_seed(seed: int, chunk_x: int, chunk_z: int) -> int:
    return _signed_long(
        seed
        ^ 0x6A09E667F3BCC909
        ^ _signed_long(chunk_x * 341873128712)
        ^ _signed_long(chunk_z * 132897987541)
    )


def extract_causal_trace(
    launch_log_path: Path,
    *,
    envelope: WorldgenExecutionEnvelope,
    causal_schema_path: Path,
) -> CausalTraceReceipt:
    """Extract and independently replay the selected feature's RNG decisions."""

    envelope = load_execution_envelope(envelope.canonical_bytes)
    validator, _ = _load_json_schema(causal_schema_path)
    selected = _selected_chunks(envelope)
    digest, size = _sha_file(launch_log_path)
    records: list[dict[str, Any]] = []
    with launch_log_path.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            if CAUSAL_PREFIX not in line:
                continue
            raw = line.split(CAUSAL_PREFIX, 1)[1].strip()
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise WorldgenCaptureError(f"causal trace line {line_number} is invalid: {exc}") from exc
            errors = tuple(validator.iter_errors(row))
            _require(not errors, f"causal trace line {line_number} fails schema: {errors[0].message if errors else ''}")
            _require(row["execution_envelope_id"] == envelope.envelope_id, "causal trace cites another envelope")
            _require((row["chunk_x"], row["chunk_z"]) in selected, "causal trace lies outside selector")
            records.append(row)
            _require(len(records) <= _MAX_CAUSAL_RECORDS, "causal trace record bound exceeded")
    _require(records, "launch log contains no causal V2 records")
    _require(
        [row["occurrence_ordinal"] for row in records] == list(range(len(records))),
        "causal occurrence ordinals are not contiguous",
    )
    run_plan = envelope.to_dict()["run_plan"]
    gates: dict[tuple[int, int], dict[str, Any]] = {}
    features: dict[tuple[int, int], dict[str, Any]] = {}
    for row in records:
        key = (row["chunk_x"], row["chunk_z"])
        _require(row["seed"] == run_plan["seed"] and row["dimension"] == run_plan["dimension"], "causal trace seed/dimension differs")
        if row["record_type"] == "gate-decision":
            _require(key not in gates, f"duplicate CUSTOM gate for chunk {key}")
            gates[key] = row
            continue
        _require(key not in features, f"duplicate feature decision for chunk {key}")
        _require(row["stage_seed"] == _expected_stage_seed(row["seed"], *key), f"stage seed differs for chunk {key}")
        random = _JavaRandom(row["stage_seed"])
        eligibility = random.next_int(10)
        _require(row["eligibility_draw"] == eligibility, f"eligibility draw differs for chunk {key}")
        if eligibility != 0:
            _require(
                row["eligibility_accepted"] is False
                and row["x_draw"] is None
                and row["z_draw"] is None
                and row["optional_top_draw"] is None
                and row["base_position"] is None
                and row["writes"] == []
                and row["outcome"] == "rejected-by-eligibility-draw",
                f"rejected feature closure differs for chunk {key}",
            )
        else:
            x_draw = random.next_int(8)
            z_draw = random.next_int(8)
            optional = random.next_boolean()
            _require(
                row["eligibility_accepted"] is True
                and row["x_draw"] == x_draw
                and row["z_draw"] == z_draw
                and row["optional_top_draw"] is optional
                and row["outcome"] == "placed",
                f"accepted feature draws differ for chunk {key}",
            )
            base = row["base_position"]
            _require(
                base["x"] == key[0] * 16 + 4 + x_draw
                and base["z"] == key[1] * 16 + 4 + z_draw,
                f"feature position differs for chunk {key}",
            )
            writes = {item["role"]: item for item in row["writes"]}
            _require(set(writes) == {"mandatory-base", "mandatory-east", "optional-top"}, f"feature write roles differ for chunk {key}")
            _require(writes["mandatory-base"]["position"] == base, "base write position differs")
            _require(writes["mandatory-east"]["position"] == {"x": base["x"] + 1, "y": base["y"], "z": base["z"]}, "east write position differs")
            _require(writes["optional-top"]["position"] == {"x": base["x"], "y": base["y"] + 1, "z": base["z"]}, "top write position differs")
            for role, registry in (
                ("mandatory-base", "minecraft:mossy_cobblestone"),
                ("mandatory-east", "minecraft:cobblestone"),
            ):
                write = writes[role]
                _require(
                    write["invoked"] is True
                    and write["result"] is True
                    and write["outcome"] == "written"
                    and write["requested_state"] == {"registry_name": registry, "metadata": 0}
                    and write["after_state"] == write["requested_state"],
                    f"mandatory {role} write did not complete",
                )
            top = writes["optional-top"]
            _require(
                top["invoked"] is optional
                and (
                    (optional and top["result"] is True and top["outcome"] == "written")
                    or (
                        not optional
                        and top["result"] is False
                        and top["outcome"] == "rejected-by-optional-draw"
                        and top["after_state"] == top["before_state"]
                    )
                ),
                f"optional write closure differs for chunk {key}",
            )
        features[key] = row
    _require(set(gates) == selected, "CUSTOM gate coverage differs from selected chunks")
    _require(all(row["decision"] == "allowed" for row in gates.values()), "CUSTOM gate rejected a selected proving chunk")
    _require(set(features) == selected, "feature decision coverage differs from selected chunks")
    _require(any(row["outcome"] == "placed" for row in features.values()), "selector contains no placed feature")
    _require(any(row["outcome"] != "placed" for row in features.values()), "selector contains no rejected feature")
    _require(any(row.get("optional_top_draw") is False for row in features.values()), "selector contains no closed optional-top rejection")
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "execution_envelope_id": envelope.envelope_id,
        "feature_count": len(features),
        "format": CAUSAL_RECEIPT_FORMAT,
        "gate_count": len(gates),
        "kind": "worldgen-causal-trace-receipt",
        "launch_log_sha256": digest,
        "launch_log_size_bytes": size,
        "placed_count": sum(row["outcome"] == "placed" for row in features.values()),
        "record_count": len(records),
        "rejected_count": sum(row["outcome"] != "placed" for row in features.values()),
        "schema_version": 2,
    }
    receipt_id, canonical, _ = _seal("worldgen-causal-trace-receipt", body)
    return CausalTraceReceipt(
        receipt_id,
        envelope.envelope_id,
        digest,
        size,
        tuple(records),
        {key: gates[key] for key in sorted(gates)},
        {key: features[key] for key in sorted(features)},
        canonical,
    )


def _state_digest(state: Mapping[str, Any]) -> str:
    digest = hashlib.sha256()
    for value in (state["registry_name"], state["metadata"]):
        encoded = str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _strata_state(scan: Mapping[str, Any], position: Mapping[str, int]) -> dict[str, Any]:
    x, y, z = position["x"], position["y"], position["z"]
    chunk_x, chunk_z = x // 16, z // 16
    local_x, local_z = x & 15, z & 15
    chunks = scan.get("denseBlockMap", {}).get("chunks")
    _require(type(chunks) is list, "Strata scan lacks dense chunks")
    chunk = next(
        (row for row in chunks if row.get("chunkX") == chunk_x and row.get("chunkZ") == chunk_z),
        None,
    )
    _require(chunk is not None, f"Strata scan lacks chunk {(chunk_x, chunk_z)}")
    section_y = y >> 4
    section = next((row for row in chunk["sections"] if row["ySection"] == section_y), None)
    if section is None:
        _require(section_y in chunk["omittedAirSections"], "Strata section is neither stored nor declared air")
        palette_state = "minecraft:air"
        index = None
    else:
        index = (y & 15) * 256 + local_z * 16 + local_x
        palette_state = section["palette"][section["indices"][index]]
    return {
        "locator": {
            "chunk_x": chunk_x,
            "chunk_z": chunk_z,
            "local_x": local_x,
            "local_y": y & 15,
            "local_z": local_z,
            "section_y": section_y,
            "section_index": index,
        },
        "palette_state": palette_state,
        "registry_name": palette_state.split("[", 1)[0],
    }


def join_same_run(
    *,
    envelope: WorldgenExecutionEnvelope,
    audit: PopulationCaptureAudit,
    causal: CausalTraceReceipt,
    strata_receipt_path: Path,
    strata_scan_path: Path,
) -> SameRunJoinReceipt:
    """Join causal writes to exact final positions under one prelaunch envelope."""

    envelope = load_execution_envelope(envelope.canonical_bytes)
    _require(audit.envelope_id == causal.envelope_id == envelope.envelope_id, "producer envelopes differ")
    receipt = parse_strata_observation_receipt(
        _json_bytes(strata_receipt_path, "Strata receipt")
    )
    scan = _json_bytes(strata_scan_path, "Strata dense scan", 1024 * 1024 * 1024)
    scan_digest, _ = _sha_file(strata_scan_path)
    run_plan = envelope.to_dict()["run_plan"]
    capture = receipt["capture"]
    window = capture["chunk_window"]
    min_x, min_z, width, height = _window(envelope)
    _require(
        capture["world_seed"] == run_plan["seed"]
        and capture["dimension_id"] == run_plan["dimension"]
        and capture["terrain_type"] == run_plan["world_type"]
        and capture["chunk_generator_class"] == run_plan["generator_id"],
        "Strata capture identity differs from the execution envelope",
    )
    _require(
        window["minChunkX"] == min_x
        and window["minChunkZ"] == min_z
        and window["chunkSizeX"] == width
        and window["chunkSizeZ"] == height,
        "Strata chunk window differs from the execution envelope",
    )
    _require(receipt["artifacts"]["launch_log"]["sha256"] == causal.launch_log_sha256, "Strata and causal receipts bind different launch logs")
    _require(receipt["artifacts"]["scan"]["sha256"] == scan_digest, "Strata receipt does not bind supplied scan")

    terminal_by_position: dict[tuple[int, int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for row in audit.terminal_writes:
        payload = row["payload"]
        terminal_by_position[
            (payload["position_x"], payload["position_y"], payload["position_z"])
        ].append(row)
    sites: list[dict[str, Any]] = []
    for key, feature in sorted(causal.features.items()):
        if feature["outcome"] != "placed":
            sites.append(
                {
                    "chunk": {"x": key[0], "z": key[1]},
                    "decision": "rejected-by-eligibility-draw",
                    "eligibility_draw": feature["eligibility_draw"],
                    "site_state": "known-absent",
                    "writes": [],
                }
            )
            continue
        joined_writes: list[dict[str, Any]] = []
        for write in feature["writes"]:
            position = write["position"]
            final = _strata_state(scan, position)
            if write["invoked"]:
                expected_digest = _state_digest(write["requested_state"])
                candidates = [
                    row
                    for row in terminal_by_position[
                        (position["x"], position["y"], position["z"])
                    ]
                    if row["payload"]["requested_state_sha256"] == expected_digest
                    and row["payload"]["generation_chunk_x"] == key[0]
                    and row["payload"]["generation_chunk_z"] == key[1]
                ]
                _require(candidates, f"no terminal raw write joins {write['role']} at {position}")
                raw_write = max(candidates, key=lambda row: row["sequence"])
                _require(
                    final["registry_name"] == write["after_state"]["registry_name"],
                    f"Strata final state differs for {write['role']} at {position}",
                )
                raw_locator = {
                    "sequence": raw_write["sequence"],
                    "write_chain_id": raw_write["payload"]["write_chain_id"],
                    "channel": raw_write["payload"]["channel"],
                    "after_state_sha256": raw_write["payload"]["after_state_sha256"],
                }
                state = "matched-final-state"
            else:
                raw_locator = None
                state = "known-absent-by-closed-decision"
            joined_writes.append(
                {
                    "causal": write,
                    "final_state": final,
                    "join_state": state,
                    "raw_terminal_write": raw_locator,
                }
            )
        sites.append(
            {
                "base_position": feature["base_position"],
                "chunk": {"x": key[0], "z": key[1]},
                "decision": "placed",
                "eligibility_draw": feature["eligibility_draw"],
                "optional_top_draw": feature["optional_top_draw"],
                "site_state": "realized",
                "writes": joined_writes,
            }
        )
    mandatory = [
        write
        for site in sites if site["decision"] == "placed"
        for write in site["writes"]
        if write["causal"]["role"] in {"mandatory-base", "mandatory-east"}
    ]
    _require(len(mandatory) >= 2 and all(row["join_state"] == "matched-final-state" for row in mandatory), "fewer than two mandatory writes join final state")
    _require(
        any(
            write["join_state"] == "known-absent-by-closed-decision"
            for site in sites if site["decision"] == "placed"
            for write in site["writes"]
        ),
        "no optional top absence is closed by a decision",
    )
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "capture_audit_id": audit.audit_id,
        "causal_receipt_id": causal.receipt_id,
        "context_ref_id": envelope.context_ref_id,
        "execution_envelope_id": envelope.envelope_id,
        "format": JOIN_RECEIPT_FORMAT,
        "input_binding_id": envelope.input_binding_id,
        "kind": "worldgen-same-run-join-receipt",
        "launch_log_sha256": causal.launch_log_sha256,
        "limitations": [
            "bounded-selected-chunk-window",
            "experimental-profile-no-stable-support",
            "no-whole-world-determinism-claim",
        ],
        "raw_sha256": audit.raw_sha256,
        "run_plan_id": envelope.run_plan_id,
        "scan_sha256": scan_digest,
        "schema_version": 2,
        "sites": sites,
        "strata_receipt_id": receipt["receipt_id"],
        "world_epoch_id": envelope.world_epoch_id,
    }
    receipt_id, canonical, value = _seal("worldgen-same-run-join-receipt", body)
    return SameRunJoinReceipt(
        receipt_id,
        envelope.envelope_id,
        envelope.run_plan_id,
        envelope.context_ref_id,
        envelope.input_binding_id,
        audit.audit_id,
        causal.receipt_id,
        receipt["receipt_id"],
        audit.raw_sha256,
        causal.launch_log_sha256,
        scan_digest,
        tuple(sites),
        tuple(body["limitations"]),
        value,
        canonical,
    )


def load_same_run_join_receipt(raw: bytes) -> SameRunJoinReceipt:
    """Load one canonical join receipt without reopening its large producers."""

    from workbench_api.canonical import parse_canonical_json

    try:
        value = parse_canonical_json(raw)
    except Exception as exc:
        raise WorldgenCaptureError("same-run join receipt is not canonical JSON") from exc
    required = {
        "canonicalizer",
        "capture_audit_id",
        "causal_receipt_id",
        "context_ref_id",
        "execution_envelope_id",
        "format",
        "id",
        "input_binding_id",
        "kind",
        "launch_log_sha256",
        "limitations",
        "raw_sha256",
        "run_plan_id",
        "scan_sha256",
        "schema_version",
        "sites",
        "strata_receipt_id",
        "world_epoch_id",
    }
    _require(type(value) is dict and set(value) == required, "same-run join receipt shape differs")
    _require(
        value["canonicalizer"] == CANONICALIZER_ID
        and value["format"] == JOIN_RECEIPT_FORMAT
        and value["kind"] == "worldgen-same-run-join-receipt"
        and value["schema_version"] == 2,
        "same-run join receipt version differs",
    )
    body = dict(value)
    receipt_id = body.pop("id")
    _require(
        receipt_id == content_id("worldgen-same-run-join-receipt", body),
        "same-run join receipt identity differs",
    )
    for field in (
        "capture_audit_id",
        "causal_receipt_id",
        "context_ref_id",
        "execution_envelope_id",
        "input_binding_id",
        "run_plan_id",
        "strata_receipt_id",
        "world_epoch_id",
    ):
        _require(type(value[field]) is str and bool(value[field]), f"same-run join {field} is invalid")
    for field in ("launch_log_sha256", "raw_sha256", "scan_sha256"):
        _require(type(value[field]) is str and _SHA256.fullmatch(value[field]) is not None, f"same-run join {field} is invalid")
    limitations = value["limitations"]
    _require(
        type(limitations) is list
        and bool(limitations)
        and all(type(item) is str and bool(item) for item in limitations),
        "same-run join limitations are invalid",
    )
    sites = value["sites"]
    _require(type(sites) is list and bool(sites) and all(type(site) is dict for site in sites), "same-run join sites are invalid")
    site_keys: list[tuple[int, int]] = []
    for site in sites:
        chunk = site.get("chunk")
        _require(
            type(chunk) is dict
            and set(chunk) == {"x", "z"}
            and type(chunk["x"]) is int
            and type(chunk["z"]) is int,
            "same-run join site chunk is invalid",
        )
        site_keys.append((chunk["x"], chunk["z"]))
    _require(len(site_keys) == len(set(site_keys)), "same-run join sites repeat a chunk")
    return SameRunJoinReceipt(
        receipt_id,
        value["execution_envelope_id"],
        value["run_plan_id"],
        value["context_ref_id"],
        value["input_binding_id"],
        value["capture_audit_id"],
        value["causal_receipt_id"],
        value["strata_receipt_id"],
        value["raw_sha256"],
        value["launch_log_sha256"],
        value["scan_sha256"],
        tuple(sites),
        tuple(limitations),
        value,
        raw,
    )


def _site_semantics(site: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "chunk": site["chunk"],
        "decision": site["decision"],
        "eligibility_draw": site["eligibility_draw"],
        "site_state": site["site_state"],
    }
    if site["decision"] == "placed":
        result.update(
            {
                "base_position": site["base_position"],
                "optional_top_draw": site["optional_top_draw"],
                "writes": [
                    {
                        "after_state": row["causal"]["after_state"],
                        "final_state": row["final_state"]["palette_state"],
                        "invoked": row["causal"]["invoked"],
                        "join_state": row["join_state"],
                        "position": row["causal"]["position"],
                        "role": row["causal"]["role"],
                    }
                    for row in site["writes"]
                ],
            }
        )
    return result


def compare_fresh_controls(
    first: SameRunJoinReceipt,
    second: SameRunJoinReceipt,
) -> StabilityReceipt:
    """Compare exact site semantics without collapsing the two world identities."""

    _require(first.run_plan_id == second.run_plan_id, "A/A controls use different run plans")
    _require(first.envelope_id != second.envelope_id, "A/A controls reuse one execution envelope")
    first_sites = {canonical_json_bytes(site["chunk"]): site for site in first.sites}
    second_sites = {canonical_json_bytes(site["chunk"]): site for site in second.sites}
    _require(set(first_sites) == set(second_sites) and first_sites, "A/A site cohorts differ")
    comparisons: list[dict[str, Any]] = []
    unstable = False
    for key in sorted(first_sites):
        left = _site_semantics(first_sites[key])
        right = _site_semantics(second_sites[key])
        stable = canonical_json_bytes(left) == canonical_json_bytes(right)
        unstable |= not stable
        comparisons.append(
            {
                "chunk": left["chunk"],
                "classification": "closed-deterministic",
                "comparison": "stable" if stable else "unstable",
                "first_semantic_sha256": hashlib.sha256(canonical_json_bytes(left)).hexdigest(),
                "second_semantic_sha256": hashlib.sha256(canonical_json_bytes(right)).hexdigest(),
                "isolation_scope": "world-studio.populate.custom.chunk",
                "observed_axes": ["fresh-jvm", "fresh-runtime", "fresh-world"],
                "downstream_boundary": "strata.same-epoch.final-state",
                "frontier": None if stable else "first-semantic-difference-requires-inspection",
            }
        )
    outcome = "unstable" if unstable else "stable-within-envelope"
    body = {
        "canonicalizer": CANONICALIZER_ID,
        "control_execution_envelope_ids": sorted(
            [first.envelope_id, second.envelope_id], key=lambda item: item.encode("utf-8")
        ),
        "control_join_receipt_ids": sorted(
            [first.receipt_id, second.receipt_id], key=lambda item: item.encode("utf-8")
        ),
        "format": STABILITY_FORMAT,
        "kind": "worldgen-stability-receipt",
        "outcome": outcome,
        "run_plan_id": first.run_plan_id,
        "schema_version": 2,
        "sites": comparisons,
    }
    receipt_id, canonical, value = _seal("worldgen-stability-receipt", body)
    return StabilityReceipt(
        receipt_id, first.run_plan_id, outcome, tuple(comparisons), value, canonical
    )


def load_stability_receipt(raw: bytes) -> StabilityReceipt:
    """Load and independently validate one canonical A/A stability receipt."""

    from workbench_api.canonical import parse_canonical_json

    try:
        value = parse_canonical_json(raw)
    except Exception as exc:
        raise WorldgenCaptureError("stability receipt is not canonical JSON") from exc
    required = {
        "canonicalizer",
        "control_execution_envelope_ids",
        "control_join_receipt_ids",
        "format",
        "id",
        "kind",
        "outcome",
        "run_plan_id",
        "schema_version",
        "sites",
    }
    _require(type(value) is dict and set(value) == required, "stability receipt shape differs")
    _require(
        value["canonicalizer"] == CANONICALIZER_ID
        and value["format"] == STABILITY_FORMAT
        and value["kind"] == "worldgen-stability-receipt"
        and value["schema_version"] == 2,
        "stability receipt version differs",
    )
    body = dict(value)
    receipt_id = body.pop("id")
    _require(
        receipt_id == content_id("worldgen-stability-receipt", body),
        "stability receipt identity differs",
    )
    envelopes = value["control_execution_envelope_ids"]
    joins = value["control_join_receipt_ids"]
    _require(
        type(envelopes) is list
        and len(envelopes) == 2
        and envelopes == sorted(envelopes, key=lambda item: item.encode("utf-8"))
        and len(set(envelopes)) == 2,
        "stability controls are not two ordered distinct envelopes",
    )
    _require(
        type(joins) is list
        and len(joins) == 2
        and joins == sorted(joins, key=lambda item: item.encode("utf-8"))
        and len(set(joins)) == 2,
        "stability joins are not two ordered distinct receipts",
    )
    _require(
        value["outcome"] in {"stable-within-envelope", "unstable"},
        "stability outcome is invalid",
    )
    sites = value["sites"]
    _require(type(sites) is list and bool(sites), "stability receipt has no sites")
    keys: list[tuple[int, int]] = []
    observed_unstable = False
    for site in sites:
        _require(
            type(site) is dict
            and set(site)
            == {
                "chunk",
                "classification",
                "comparison",
                "downstream_boundary",
                "first_semantic_sha256",
                "frontier",
                "isolation_scope",
                "observed_axes",
                "second_semantic_sha256",
            },
            "stability site shape differs",
        )
        chunk = site["chunk"]
        _require(
            type(chunk) is dict
            and set(chunk) == {"x", "z"}
            and type(chunk["x"]) is int
            and type(chunk["z"]) is int,
            "stability site chunk is invalid",
        )
        keys.append((chunk["x"], chunk["z"]))
        _require(
            site["classification"] == "closed-deterministic"
            and site["comparison"] in {"stable", "unstable"}
            and site["isolation_scope"] == "world-studio.populate.custom.chunk"
            and site["downstream_boundary"] == "strata.same-epoch.final-state",
            "stability site semantics differ",
        )
        for field in ("first_semantic_sha256", "second_semantic_sha256"):
            _require(
                type(site[field]) is str and _SHA256.fullmatch(site[field]) is not None,
                f"stability site {field} is invalid",
            )
        is_unstable = site["comparison"] == "unstable"
        observed_unstable |= is_unstable
        _require(
            (site["frontier"] is None) == (not is_unstable),
            "stability frontier does not match its comparison",
        )
    _require(len(keys) == len(set(keys)), "stability receipt repeats a site")
    _require(
        (value["outcome"] == "unstable") == observed_unstable,
        "stability aggregate outcome differs from its sites",
    )
    return StabilityReceipt(
        receipt_id,
        value["run_plan_id"],
        value["outcome"],
        tuple(sites),
        value,
        raw,
    )


__all__ = [
    "CAUSAL_FORMAT",
    "CAUSAL_PREFIX",
    "CausalTraceReceipt",
    "PopulationCaptureAudit",
    "SameRunJoinReceipt",
    "StabilityReceipt",
    "WorldgenCaptureError",
    "audit_population_capture",
    "compare_fresh_controls",
    "extract_causal_trace",
    "join_same_run",
    "load_same_run_join_receipt",
    "load_stability_receipt",
]
