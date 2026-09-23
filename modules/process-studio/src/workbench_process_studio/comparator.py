"""Source-bound, bounded observed-effect projection and comparison V2."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import re
from typing import Any, Mapping, NoReturn, Sequence

from workbench_api.canonical import CANONICALIZER_ID, CanonicalJsonError, canonical_json_bytes, content_id, parse_canonical_json
from workbench_crucible_stage_snapshot import (
    StageSnapshotError,
    validate_stage_snapshot,
)

from .adapters import EffectAdapterError, EffectFamilyAdapter


PROJECTION_FORMAT = "workbench-process-studio-effect-snapshot-projection-v2"
COMPARISON_FORMAT = "workbench-process-studio-bounded-observed-effect-comparison-v2"
CHANGE_ENVELOPE_FORMAT = "workbench-process-studio-effect-change-envelope-v2"
SCHEMA_VERSION = 2

CLASSIFICATIONS = (
    "added",
    "removed",
    "modified",
    "unchanged",
    "ambiguous",
    "unresolved",
)
COVERAGE_STATES = ("complete", "partial", "not-observed", "unavailable", "failed")

HARD_MAX_INPUT_BYTES = 256 * 1024 * 1024
HARD_MAX_RECORDS_PER_SNAPSHOT = 500_000
HARD_MAX_COMPARISONS = 500_000
HARD_MAX_ENVELOPE_EXPECTATIONS = 100_000
HARD_MAX_JSON_DEPTH = 64
HARD_MAX_CONTAINER_ITEMS = 500_000
HARD_MAX_STRING_BYTES = 1 * 1024 * 1024
HARD_MAX_INTEGER = (1 << 63) - 1
HARD_MIN_INTEGER = -(1 << 63)

_PROJECTION_KIND = "workbench-process-studio-effect-snapshot-projection-v2"
_COMPARISON_KIND = "workbench-process-studio-bounded-observed-effect-comparison-v2"
_ROW_KIND = "workbench-process-studio-effect-comparison-v2"
_ENVELOPE_KIND = "workbench-process-studio-effect-change-envelope-v2"
_PROJECTION_PREFIX = f"{_PROJECTION_KIND}:sha256:"
_COMPARISON_PREFIX = f"{_COMPARISON_KIND}:sha256:"
_ROW_PREFIX = f"{_ROW_KIND}:sha256:"
_ENVELOPE_PREFIX = f"{_ENVELOPE_KIND}:sha256:"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVELOPE_ID = re.compile(rf"^{re.escape(_ENVELOPE_PREFIX)}[0-9a-f]{{64}}$")
_PROJECTION_ID = re.compile(rf"^{re.escape(_PROJECTION_PREFIX)}[0-9a-f]{{64}}$")

_STAGE_SNAPSHOT_AUTHORITY = {
    "owner": "Crucible",
    "claim": "observed registry and effect state at one lifecycle stage",
    "source_authority": "none",
    "playability_authority": "none",
}
_STAGE_SNAPSHOT_LIMITATIONS = [
    "The snapshot establishes only what the named Crucible capture observed at the bound stage.",
    "Atlas may derive conclusions from this snapshot but cannot rewrite it into Atlas-owned runtime truth.",
]
_PROJECTION_AUTHORITY = {
    "owner": "Process Studio",
    "claim": "closed projection of admitted snapshot records through bound family adapters",
    "runtime_authority": "none",
    "semantic_authority": "family-adapters",
    "causality_authority": "none",
    "construction_authority": "none",
    "action_authorization": "none",
}
_COMPARISON_AUTHORITY = {
    "owner": "Process Studio",
    "claim": "bounded structural comparison of source-replayable projections",
    "runtime_authority": "none",
    "semantic_authority": "family-adapters",
    "causality_authority": "none",
    "construction_authority": "none",
    "action_authorization": "none",
}
_PROJECTION_LIMITATIONS = [
    "The projection preserves source record identity but is not a new runtime observation.",
    "Correlation and fingerprints are opaque family-adapter outputs; Process Studio does not reinterpret them.",
    "Shape validation alone does not establish that a projection came from its named source snapshot.",
    "This fixture slice does not authenticate the input owner, provenance, or runtime custody.",
]
_COMPARISON_LIMITATIONS = [
    "This is a bounded observed-effect comparison, not a safety finding or approval.",
    "Modified means one stable adapter-owned correspondence with different adapter-owned fingerprints; it does not establish causality.",
    "No change envelope yields comparison-only state and never an envelope-match claim or authorization.",
    "Registry presence and registration effects do not prove execution, reachability, balance, playability, or support.",
    "This fixture slice does not authenticate input ownership, provenance, runtime custody, or envelope timing.",
]
_ADAPTER_FIELDS = (
    "family",
    "semantic_owner_id",
    "adapter_id",
    "semantic_version",
    "correlation_policy_id",
    "fingerprint_policy_id",
)


class BoundedEffectComparisonError(ValueError):
    """A comparison cannot preserve its declared evidence boundary."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code: str, path: str, message: str) -> NoReturn:
    raise BoundedEffectComparisonError(code, path, message)


@dataclass(frozen=True)
class ComparisonBounds:
    """Caller-selectable ceilings which cannot exceed fixed implementation maxima."""

    max_input_bytes: int = HARD_MAX_INPUT_BYTES
    max_records_per_snapshot: int = HARD_MAX_RECORDS_PER_SNAPSHOT
    max_comparisons: int = HARD_MAX_COMPARISONS
    max_envelope_expectations: int = HARD_MAX_ENVELOPE_EXPECTATIONS

    def __post_init__(self) -> None:
        maximums = {
            "max_input_bytes": HARD_MAX_INPUT_BYTES,
            "max_records_per_snapshot": HARD_MAX_RECORDS_PER_SNAPSHOT,
            "max_comparisons": HARD_MAX_COMPARISONS,
            "max_envelope_expectations": HARD_MAX_ENVELOPE_EXPECTATIONS,
        }
        for name, value in self.as_dict().items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            if value > maximums[name]:
                raise ValueError(f"{name} exceeds fixed maximum {maximums[name]}")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_comparisons": self.max_comparisons,
            "max_envelope_expectations": self.max_envelope_expectations,
            "max_input_bytes": self.max_input_bytes,
            "max_records_per_snapshot": self.max_records_per_snapshot,
        }


DEFAULT_BOUNDS = ComparisonBounds()


@dataclass(frozen=True)
class _SnapshotBinding:
    capture_id: str
    declared_change_envelope_id: str | None
    context_id: str
    pack_profile_id: str
    platform_profile_id: str
    physical_side: str
    stage: str
    coverage: tuple[dict[str, str], ...]


def _strict_json_domain(
    value: Any,
    *,
    path: str,
    depth: int = 0,
    ancestors: set[int] | None = None,
) -> None:
    if depth > HARD_MAX_JSON_DEPTH:
        _fail("json.depth", path, f"JSON depth exceeds {HARD_MAX_JSON_DEPTH}")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value < HARD_MIN_INTEGER or value > HARD_MAX_INTEGER:
            _fail("json.integer", path, "integer exceeds signed 64-bit range")
        return
    if type(value) is str:
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            _fail("json.string", path, f"string is not Unicode scalar text: {exc}")
        if len(encoded) > HARD_MAX_STRING_BYTES:
            _fail(
                "json.string",
                path,
                f"string exceeds {HARD_MAX_STRING_BYTES} UTF-8 bytes",
            )
        return
    active = set() if ancestors is None else ancestors
    if type(value) is list:
        if len(value) > HARD_MAX_CONTAINER_ITEMS:
            _fail("json.container", path, "array exceeds fixed item limit")
        identity = id(value)
        if identity in active:
            _fail("json.cycle", path, "cyclic JSON array is unsupported")
        active.add(identity)
        for index, item in enumerate(value):
            _strict_json_domain(
                item,
                path=f"{path}/{index}",
                depth=depth + 1,
                ancestors=active,
            )
        active.remove(identity)
        return
    if type(value) is dict:
        if len(value) > HARD_MAX_CONTAINER_ITEMS:
            _fail("json.container", path, "object exceeds fixed member limit")
        identity = id(value)
        if identity in active:
            _fail("json.cycle", path, "cyclic JSON object is unsupported")
        active.add(identity)
        for key, item in value.items():
            if type(key) is not str:
                _fail("json.key", path, "object keys must be strings")
            try:
                encoded_key = key.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                _fail("json.key", path, f"object key is not Unicode scalar text: {exc}")
            if len(encoded_key) > HARD_MAX_STRING_BYTES:
                _fail("json.string", path, "object key exceeds fixed UTF-8 byte limit")
            _strict_json_domain(
                item,
                path=f"{path}/{key}",
                depth=depth + 1,
                ancestors=active,
            )
        active.remove(identity)
        return
    if type(value) is float:
        _fail("json.float", path, "floating-point JSON numbers are unsupported")
    _fail("json.type", path, f"unsupported JSON value type: {type(value).__name__}")


def _canonical_bytes(value: Any, *, path: str = "/") -> bytes:
    _strict_json_domain(value, path=path)
    try:
        return canonical_json_bytes(value)
    except CanonicalJsonError as exc:
        _fail("json.invalid", path, f"value is outside {CANONICALIZER_ID}: {exc}")


def _plain_json(value: Any, *, path: str) -> Any:
    try:
        return parse_canonical_json(_canonical_bytes(value, path=path))
    except CanonicalJsonError as exc:
        _fail("json.invalid", path, f"value is outside {CANONICALIZER_ID}: {exc}")


def _content_id(kind: str, value: Mapping[str, Any], *, id_key: str) -> str:
    payload = dict(value)
    payload.pop(id_key, None)
    try:
        return content_id(kind, payload)
    except CanonicalJsonError as exc:
        _fail("json.invalid", "/", f"cannot derive {CANONICALIZER_ID} content ID: {exc}")


def _string(value: Any, *, path: str) -> str:
    if type(value) is not str or not value:
        _fail("value.string", path, "expected a non-empty string")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        _fail("json.string", path, f"string is not Unicode scalar text: {exc}")
    if len(encoded) > HARD_MAX_STRING_BYTES:
        _fail("json.string", path, "string exceeds fixed UTF-8 byte limit")
    return value


def _string_or_none(value: Any, *, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path=path)


def _envelope_id(value: Any, *, path: str) -> str:
    admitted = _string(value, path=path)
    if _ENVELOPE_ID.fullmatch(admitted) is None:
        _fail("envelope.id-syntax", path, "expected an exact lowercase V2 envelope ID")
    return admitted


def _envelope_id_or_none(value: Any, *, path: str) -> str | None:
    if value is None:
        return None
    return _envelope_id(value, path=path)


def _projection_id(value: Any, *, path: str) -> str:
    admitted = _string(value, path=path)
    if _PROJECTION_ID.fullmatch(admitted) is None:
        _fail("projection.id-syntax", path, "expected an exact lowercase V2 projection ID")
    return admitted


def _checked_bounds(value: Any, *, path: str) -> ComparisonBounds:
    keys = set(DEFAULT_BOUNDS.as_dict())
    if type(value) is not dict or set(value) != keys:
        _fail("bounds.invalid", path, "bounds have unexpected keys")
    try:
        return ComparisonBounds(**value)
    except (TypeError, ValueError) as exc:
        _fail("bounds.invalid", path, str(exc))


def _validate_coverage(value: Any, *, path: str) -> tuple[dict[str, str], ...]:
    if type(value) is not list:
        _fail("coverage.invalid", path, "effect_coverage must be an array")
    keys = {*_ADAPTER_FIELDS, "state"}
    rows: list[dict[str, str]] = []
    families: set[str] = set()
    for index, raw in enumerate(value):
        row_path = f"{path}/{index}"
        if type(raw) is not dict or set(raw) != keys:
            _fail("coverage.invalid", row_path, "coverage row is not closed")
        row = {key: _string(raw[key], path=f"{row_path}/{key}") for key in keys}
        if row["state"] not in COVERAGE_STATES:
            _fail("coverage.state", f"{row_path}/state", "unsupported coverage state")
        if row["family"] in families:
            _fail("coverage.duplicate-family", row_path, "coverage family is duplicate")
        families.add(row["family"])
        rows.append(row)
    if rows != sorted(rows, key=lambda row: row["family"]):
        _fail("coverage.order", path, "coverage rows must be ordered by family")
    return tuple(rows)


def _validated_stage_snapshot(
    value: Mapping[str, Any], *, label: str, bounds: ComparisonBounds
) -> tuple[dict[str, Any], _SnapshotBinding]:
    """Close known permissive Stage Snapshot V1 fields before adapter dispatch."""

    if isinstance(value, Mapping) and isinstance(value.get("effects"), list):
        for index, row in enumerate(value["effects"]):
            if isinstance(row, Mapping) and "operation" in row:
                _string(row["operation"], path=f"/{label}/effects/{index}/operation")
    plain = _plain_json(value, path=f"/{label}")
    if type(plain) is not dict:
        _fail("snapshot.invalid", f"/{label}", "snapshot must be an object")
    if len(_canonical_bytes(plain, path=f"/{label}")) > bounds.max_input_bytes:
        _fail("bounds.input-bytes", f"/{label}", "snapshot exceeds max_input_bytes")
    try:
        snapshot = validate_stage_snapshot(plain)
    except StageSnapshotError as exc:
        _fail("snapshot.invalid", f"/{label}", str(exc))
    if snapshot["authority"] != _STAGE_SNAPSHOT_AUTHORITY:
        _fail("snapshot.authority", f"/{label}/authority", "exact Crucible V1 authority differs")
    if snapshot["limitations"] != _STAGE_SNAPSHOT_LIMITATIONS:
        _fail("snapshot.limitations", f"/{label}/limitations", "exact Crucible V1 limitations differ")
    binding = snapshot["binding"]
    if type(binding) is not dict or set(binding) != {
        "pack_profile_id",
        "platform_profile_id",
        "stage",
        "capture",
    }:
        _fail("snapshot.binding", f"/{label}/binding", "binding is not closed")
    capture = binding["capture"]
    if type(capture) is not dict or set(capture) != {
        "capture_id",
        "comparison_context_id",
        "physical_side",
        "effect_coverage",
        "declared_change_envelope_id",
    }:
        _fail("snapshot.capture", f"/{label}/binding/capture", "capture extension is not closed")
    admitted = _SnapshotBinding(
        capture_id=_string(capture["capture_id"], path=f"/{label}/binding/capture/capture_id"),
        declared_change_envelope_id=_envelope_id_or_none(
            capture["declared_change_envelope_id"],
            path=f"/{label}/binding/capture/declared_change_envelope_id",
        ),
        context_id=_string(
            capture["comparison_context_id"],
            path=f"/{label}/binding/capture/comparison_context_id",
        ),
        pack_profile_id=_string(binding["pack_profile_id"], path=f"/{label}/binding/pack_profile_id"),
        platform_profile_id=_string(
            binding["platform_profile_id"], path=f"/{label}/binding/platform_profile_id"
        ),
        physical_side=_string(
            capture["physical_side"], path=f"/{label}/binding/capture/physical_side"
        ),
        stage=_string(binding["stage"], path=f"/{label}/binding/stage"),
        coverage=_validate_coverage(
            capture["effect_coverage"], path=f"/{label}/binding/capture/effect_coverage"
        ),
    )
    record_count = len(snapshot["registry"]) + len(snapshot["effects"])
    if record_count > bounds.max_records_per_snapshot:
        _fail("bounds.snapshot-records", f"/{label}", "snapshot record count exceeds bound")
    coverage = {row["family"]: row for row in admitted.coverage}
    seen_ids: set[str] = set()
    for collection in ("registry", "effects"):
        for index, row in enumerate(snapshot[collection]):
            row_path = f"/{label}/{collection}/{index}"
            source_id = _string(row["runtime_record_id"], path=f"{row_path}/runtime_record_id")
            if source_id in seen_ids:
                _fail("snapshot.duplicate-record", row_path, "source record ID is duplicate")
            seen_ids.add(source_id)
            state_key = "registry_state" if collection == "registry" else "effect_state"
            if type(row[state_key]) is not dict or not row[state_key]:
                _fail("snapshot.state", f"{row_path}/{state_key}", "state must be a non-empty object")
            if type(row["provenance"]) is not dict or row["provenance"].get("authority") != "Crucible":
                _fail(
                    "snapshot.provenance",
                    f"{row_path}/provenance",
                    "required declared V1 provenance field is missing",
                )
            if collection == "effects":
                _string(row["operation"], path=f"{row_path}/operation")
            family = row["semantic_descriptor"]["domain"]
            if family not in coverage:
                _fail("coverage.missing-family", row_path, f"family {family!r} has no coverage row")
            if coverage[family]["state"] in {"not-observed", "unavailable"}:
                _fail("coverage.contradiction", row_path, "coverage state contradicts observed records")
    expected_summary = {
        "registry_records": len(snapshot["registry"]),
        "effect_records": len(snapshot["effects"]),
        "effects_by_operation": dict(
            sorted(Counter(row["operation"] for row in snapshot["effects"]).items())
        ),
    }
    if type(snapshot["summary"]) is not dict or snapshot["summary"] != expected_summary:
        _fail("snapshot.summary", f"/{label}/summary", "summary is stale or malformed")
    return snapshot, admitted


def _bound_adapters(
    adapters: Sequence[EffectFamilyAdapter],
    coverage: tuple[dict[str, str], ...],
    *,
    observed_families: set[str],
) -> tuple[EffectFamilyAdapter, ...]:
    rows = tuple(adapters)
    coverage_by_family = {row["family"]: row for row in coverage}
    families: set[str] = set()
    adapter_ids: set[str] = set()
    for index, adapter in enumerate(rows):
        for field in _ADAPTER_FIELDS:
            _string(getattr(adapter, field, None), path=f"/adapters/{index}/{field}")
        if adapter.family in families:
            _fail("adapter.duplicate-family", f"/adapters/{index}", "adapter family is duplicate")
        if adapter.adapter_id in adapter_ids:
            _fail("adapter.duplicate-id", f"/adapters/{index}", "adapter ID is duplicate")
        families.add(adapter.family)
        adapter_ids.add(adapter.adapter_id)
        declared = coverage_by_family.get(adapter.family)
        if declared is not None and any(
            declared[field] != getattr(adapter, field) for field in _ADAPTER_FIELDS
        ):
            _fail("adapter.policy-mismatch", f"/adapters/{index}", "adapter metadata differs from coverage")
    missing = sorted(
        family
        for family, row in coverage_by_family.items()
        if family not in families
        and (
            family in observed_families
            or row["state"] not in {"unavailable", "failed", "not-observed"}
        )
    )
    if missing:
        _fail("adapter.unavailable", "/adapters", f"required adapters unavailable: {', '.join(missing)}")
    return tuple(sorted(rows, key=lambda adapter: (adapter.family, adapter.adapter_id)))


def _unresolved_projection(
    observation: Mapping[str, Any], coverage: Mapping[str, str], *, issue: str
) -> dict[str, Any]:
    descriptor = observation["semantic_descriptor"]
    return {
        "source_record_id": observation["runtime_record_id"],
        "family": descriptor["domain"],
        "semantic_owner_id": coverage["semantic_owner_id"],
        "adapter_id": coverage["adapter_id"],
        "semantic_version": coverage["semantic_version"],
        "correlation_policy_id": coverage["correlation_policy_id"],
        "fingerprint_policy_id": coverage["fingerprint_policy_id"],
        "record_kind": observation["record_kind"],
        "descriptor_kind": descriptor["kind"],
        "operation": observation.get("operation"),
        "correlation": None,
        "semantic_fingerprint": None,
        "projection_state": "unresolved",
        "issue": issue,
    }


def _project_observation(
    observation: Mapping[str, Any],
    *,
    adapter: EffectFamilyAdapter,
    coverage: Mapping[str, str],
) -> dict[str, Any]:
    try:
        if not adapter.accepts(observation):
            raise EffectAdapterError("adapter rejected its declared family observation")
        projected = adapter.project(observation)
        for field in _ADAPTER_FIELDS:
            if getattr(projected, field) != getattr(adapter, field):
                raise EffectAdapterError(f"adapter changed {field}")
        if projected.runtime_record_id != observation["runtime_record_id"]:
            raise EffectAdapterError("adapter changed source record ID")
        if projected.record_kind != observation["record_kind"]:
            raise EffectAdapterError("adapter changed record kind")
        if projected.descriptor_kind != observation["semantic_descriptor"]["kind"]:
            raise EffectAdapterError("adapter changed descriptor kind")
        if projected.operation != observation.get("operation"):
            raise EffectAdapterError("adapter changed operation")
        if type(projected.correlation) is not dict or not projected.correlation:
            return _unresolved_projection(observation, coverage, issue="correlation-unavailable")
        if type(projected.semantic_fingerprint) is not str or _SHA256.fullmatch(
            projected.semantic_fingerprint
        ) is None:
            raise EffectAdapterError("adapter fingerprint is not lowercase SHA-256")
        return {
            "source_record_id": projected.runtime_record_id,
            **{field: getattr(adapter, field) for field in _ADAPTER_FIELDS},
            "record_kind": projected.record_kind,
            "descriptor_kind": projected.descriptor_kind,
            "operation": projected.operation,
            "correlation": _plain_json(projected.correlation, path="/adapter/correlation"),
            "semantic_fingerprint": projected.semantic_fingerprint,
            "projection_state": "projected",
            "issue": None,
        }
    except Exception as exc:
        return _unresolved_projection(
            observation,
            coverage,
            issue=f"family-adapter-projection-failed:{type(exc).__name__}",
        )


def _record_selector(value: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    correlation = value.get("correlation")
    return (
        value["family"],
        value["record_kind"],
        value["descriptor_kind"],
        value.get("operation") or "",
        "" if correlation is None else _canonical_bytes(correlation).decode("utf-8"),
        value["source_record_id"],
    )


def project_effect_snapshot(
    snapshot: Mapping[str, Any],
    *,
    adapters: Sequence[EffectFamilyAdapter],
    bounds: ComparisonBounds = DEFAULT_BOUNDS,
    label: str = "snapshot",
) -> dict[str, Any]:
    """Project one raw snapshot through one explicit adapter set."""

    checked, binding = _validated_stage_snapshot(snapshot, label=label, bounds=bounds)
    observations = [*checked["registry"], *checked["effects"]]
    selected = _bound_adapters(
        adapters,
        binding.coverage,
        observed_families={row["semantic_descriptor"]["domain"] for row in observations},
    )
    adapter_by_family = {adapter.family: adapter for adapter in selected}
    coverage_by_family = {row["family"]: row for row in binding.coverage}
    records = [
        _project_observation(
            observation,
            adapter=adapter_by_family[observation["semantic_descriptor"]["domain"]],
            coverage=coverage_by_family[observation["semantic_descriptor"]["domain"]],
        )
        for observation in observations
    ]
    records.sort(key=_record_selector)
    adapter_rows = [
        {field: getattr(adapter, field) for field in _ADAPTER_FIELDS}
        for adapter in selected
        if adapter.family in coverage_by_family
    ]
    value: dict[str, Any] = {
        "format": PROJECTION_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "canonicalizer": CANONICALIZER_ID,
        "projection_id": "",
        "authority": dict(_PROJECTION_AUTHORITY),
        "binding": {
            "source_snapshot_id": checked["snapshot_id"],
            "source_capture_id": binding.capture_id,
            "declared_change_envelope_id": binding.declared_change_envelope_id,
            "context_id": binding.context_id,
            "pack_profile_id": binding.pack_profile_id,
            "platform_profile_id": binding.platform_profile_id,
            "physical_side": binding.physical_side,
            "stage": binding.stage,
            "coverage": [dict(row) for row in binding.coverage],
            "adapters": adapter_rows,
            "bounds": bounds.as_dict(),
        },
        "records": records,
        "summary": {
            "source_records": len(records),
            "projected": sum(row["projection_state"] == "projected" for row in records),
            "unresolved": sum(row["projection_state"] == "unresolved" for row in records),
        },
        "limitations": list(_PROJECTION_LIMITATIONS),
    }
    value["projection_id"] = _content_id(_PROJECTION_KIND, value, id_key="projection_id")
    return validate_effect_snapshot_projection_shape(value)


def validate_effect_snapshot_projection_shape(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate self-consistent projection shape, not source provenance."""

    plain = _plain_json(value, path="/projection")
    if type(plain) is not dict or set(plain) != {
        "format",
        "schema_version",
        "canonicalizer",
        "projection_id",
        "authority",
        "binding",
        "records",
        "summary",
        "limitations",
    }:
        _fail("projection.invalid", "/projection", "projection has unexpected keys")
    if plain["format"] != PROJECTION_FORMAT or plain["schema_version"] != SCHEMA_VERSION:
        _fail("projection.format", "/projection/format", "unsupported projection format")
    if plain["canonicalizer"] != CANONICALIZER_ID:
        _fail("projection.canonicalizer", "/projection/canonicalizer", "canonicalizer differs")
    _projection_id(plain["projection_id"], path="/projection/projection_id")
    if plain["projection_id"] != _content_id(_PROJECTION_KIND, plain, id_key="projection_id"):
        _fail("projection.identity", "/projection/projection_id", "projection identity differs")
    if plain["authority"] != _PROJECTION_AUTHORITY:
        _fail("projection.authority", "/projection/authority", "authority boundary differs")
    binding = plain["binding"]
    binding_keys = {
        "source_snapshot_id",
        "source_capture_id",
        "declared_change_envelope_id",
        "context_id",
        "pack_profile_id",
        "platform_profile_id",
        "physical_side",
        "stage",
        "coverage",
        "adapters",
        "bounds",
    }
    if type(binding) is not dict or set(binding) != binding_keys:
        _fail("projection.binding", "/projection/binding", "binding is malformed")
    for key in binding_keys - {"declared_change_envelope_id", "coverage", "adapters", "bounds"}:
        _string(binding[key], path=f"/projection/binding/{key}")
    _envelope_id_or_none(
        binding["declared_change_envelope_id"],
        path="/projection/binding/declared_change_envelope_id",
    )
    coverage_rows = _validate_coverage(binding["coverage"], path="/projection/binding/coverage")
    coverage = {row["family"]: row for row in coverage_rows}
    adapters = binding["adapters"]
    adapter_keys = set(_ADAPTER_FIELDS)
    if type(adapters) is not list:
        _fail("projection.adapters", "/projection/binding/adapters", "adapters must be an array")
    adapter_families: set[str] = set()
    for index, row in enumerate(adapters):
        path = f"/projection/binding/adapters/{index}"
        if type(row) is not dict or set(row) != adapter_keys:
            _fail("projection.adapter", path, "adapter binding is malformed")
        for key in _ADAPTER_FIELDS:
            _string(row[key], path=f"{path}/{key}")
        if row["family"] in adapter_families:
            _fail("projection.adapter", path, "adapter family is duplicate")
        adapter_families.add(row["family"])
        declared = coverage.get(row["family"])
        if declared is None or any(declared[key] != row[key] for key in _ADAPTER_FIELDS):
            _fail("projection.adapter", path, "adapter differs from coverage")
    if adapters != sorted(adapters, key=lambda row: (row["family"], row["adapter_id"])):
        _fail("projection.order", "/projection/binding/adapters", "adapters are not ordered")
    checked_bounds = _checked_bounds(binding["bounds"], path="/projection/binding/bounds")
    records = plain["records"]
    if type(records) is not list or len(records) > checked_bounds.max_records_per_snapshot:
        _fail("projection.records", "/projection/records", "record array exceeds bound")
    record_keys = {
        "source_record_id",
        *_ADAPTER_FIELDS,
        "record_kind",
        "descriptor_kind",
        "operation",
        "correlation",
        "semantic_fingerprint",
        "projection_state",
        "issue",
    }
    source_ids: set[str] = set()
    selectors: list[tuple[str, str, str, str, str, str]] = []
    record_families: set[str] = set()
    for index, row in enumerate(records):
        path = f"/projection/records/{index}"
        if type(row) is not dict or set(row) != record_keys:
            _fail("projection.record", path, "record has unexpected keys")
        source_id = _string(row["source_record_id"], path=f"{path}/source_record_id")
        if source_id in source_ids:
            _fail("projection.record", path, "source record ID is duplicate")
        source_ids.add(source_id)
        family = _string(row["family"], path=f"{path}/family")
        record_families.add(family)
        declared = coverage.get(family)
        if declared is None or family not in adapter_families:
            _fail("projection.record", path, "record family lacks coverage or adapter")
        for key in _ADAPTER_FIELDS[1:]:
            if row[key] != declared[key]:
                _fail("projection.record", f"{path}/{key}", "record metadata differs")
        if row["record_kind"] not in {"registry-observation", "effect-observation"}:
            _fail("projection.record", f"{path}/record_kind", "record kind is unsupported")
        _string(row["descriptor_kind"], path=f"{path}/descriptor_kind")
        _string_or_none(row["operation"], path=f"{path}/operation")
        if row["record_kind"] == "registry-observation" and row["operation"] is not None:
            _fail("projection.record", f"{path}/operation", "registry record has operation")
        if row["record_kind"] == "effect-observation" and row["operation"] is None:
            _fail("projection.record", f"{path}/operation", "effect record lacks operation")
        if row["projection_state"] == "projected":
            if row["issue"] is not None or type(row["correlation"]) is not dict or not row["correlation"]:
                _fail("projection.record", path, "projected record lacks correlation or carries issue")
            if type(row["semantic_fingerprint"]) is not str or _SHA256.fullmatch(
                row["semantic_fingerprint"]
            ) is None:
                _fail("projection.record", path, "projected fingerprint is invalid")
        elif row["projection_state"] == "unresolved":
            _string(row["issue"], path=f"{path}/issue")
            if row["correlation"] is not None or row["semantic_fingerprint"] is not None:
                _fail("projection.record", path, "unresolved record carries semantic output")
        else:
            _fail("projection.record", f"{path}/projection_state", "projection state is unsupported")
        selectors.append(_record_selector(row))
    if selectors != sorted(selectors):
        _fail("projection.order", "/projection/records", "records are not ordered")
    for family, row in coverage.items():
        if family not in adapter_families and (
            family in record_families
            or row["state"] not in {"unavailable", "failed", "not-observed"}
        ):
            _fail("projection.adapters", "/projection/binding/adapters", "required adapter missing")
    expected_summary = {
        "source_records": len(records),
        "projected": sum(row["projection_state"] == "projected" for row in records),
        "unresolved": sum(row["projection_state"] == "unresolved" for row in records),
    }
    if type(plain["summary"]) is not dict or plain["summary"] != expected_summary:
        _fail("projection.summary", "/projection/summary", "summary is stale")
    if plain["limitations"] != _PROJECTION_LIMITATIONS:
        _fail("projection.limitations", "/projection/limitations", "exact limitations differ")
    return plain


def validate_source_bound_projection(
    value: Mapping[str, Any],
    *,
    source_snapshot: Mapping[str, Any],
    adapters: Sequence[EffectFamilyAdapter],
    bounds: ComparisonBounds = DEFAULT_BOUNDS,
) -> dict[str, Any]:
    """Reproject a raw snapshot and require byte-semantic projection equality."""

    checked = validate_effect_snapshot_projection_shape(value)
    expected = project_effect_snapshot(
        source_snapshot,
        adapters=tuple(adapters),
        bounds=bounds,
        label="source_snapshot",
    )
    if checked != expected:
        _fail("projection.source-binding", "/projection", "projection differs from raw-source replay")
    return checked


def _pair_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    binding = value["binding"]
    return {
        "context_id": binding["context_id"],
        "pack_profile_id": binding["pack_profile_id"],
        "platform_profile_id": binding["platform_profile_id"],
        "physical_side": binding["physical_side"],
        "stage": binding["stage"],
        "coverage": binding["coverage"],
    }


def _require_aligned(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    if baseline["binding"]["source_snapshot_id"] == candidate["binding"]["source_snapshot_id"]:
        _fail("incomparable.identical-snapshot", "/binding", "snapshot identities are identical")
    if baseline["binding"]["source_capture_id"] == candidate["binding"]["source_capture_id"]:
        _fail("incomparable.identical-capture", "/binding", "capture identities are identical")
    before = _pair_binding(baseline)
    after = _pair_binding(candidate)
    for field in (
        "context_id",
        "pack_profile_id",
        "platform_profile_id",
        "physical_side",
        "stage",
    ):
        if before[field] != after[field]:
            _fail(
                f"incomparable.{field}",
                f"/binding/{field}",
                f"baseline {before[field]!r} differs from candidate {after[field]!r}",
            )
    if before["coverage"] != after["coverage"]:
        _fail("incomparable.coverage", "/binding/coverage", "coverage or semantic policies differ")
    if baseline["binding"]["bounds"] != candidate["binding"]["bounds"]:
        _fail("incomparable.bounds", "/binding/bounds", "projection bounds differ")
    return before


def _group_selector(value: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    correlation = value.get("correlation")
    return (
        value["family"],
        value["record_kind"],
        value["descriptor_kind"],
        value.get("operation") or "",
        "" if correlation is None else _canonical_bytes(correlation).decode("utf-8"),
    )


def _classification(
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
    *,
    coverage_state: str,
) -> tuple[str, str]:
    rows = [*baseline, *candidate]
    if coverage_state != "complete":
        return "unresolved", f"coverage-{coverage_state}"
    if any(row["projection_state"] == "unresolved" for row in rows):
        return "unresolved", "semantic-projection-unresolved"
    if rows[0]["correlation"] is None:
        return "unresolved", "semantic-projection-unresolved"
    if len(baseline) > 1 or len(candidate) > 1:
        return "ambiguous", "non-unique-correlation"
    if baseline and candidate:
        if baseline[0]["semantic_fingerprint"] == candidate[0]["semantic_fingerprint"]:
            return "unchanged", "semantic-fingerprint-equal"
        return "modified", "semantic-fingerprint-changed"
    if candidate:
        return "added", "candidate-only-complete-coverage"
    return "removed", "baseline-only-complete-coverage"


def _comparison_row(
    selector: tuple[str, str, str, str, str],
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
    *,
    coverage_state: str,
) -> dict[str, Any]:
    classification, reason = _classification(
        baseline, candidate, coverage_state=coverage_state
    )
    representative = [*baseline, *candidate][0]
    value: dict[str, Any] = {
        "comparison_id": "",
        "canonicalizer": CANONICALIZER_ID,
        "family": selector[0],
        "record_kind": selector[1],
        "descriptor_kind": selector[2],
        "operation": representative["operation"],
        "correlation": _plain_json(representative["correlation"], path="/comparison/correlation"),
        "classification": classification,
        "reason": reason,
        "baseline_source_record_ids": sorted(row["source_record_id"] for row in baseline),
        "candidate_source_record_ids": sorted(row["source_record_id"] for row in candidate),
        "baseline_semantic_fingerprints": sorted(
            {row["semantic_fingerprint"] for row in baseline if row["semantic_fingerprint"] is not None}
        ),
        "candidate_semantic_fingerprints": sorted(
            {row["semantic_fingerprint"] for row in candidate if row["semantic_fingerprint"] is not None}
        ),
    }
    value["comparison_id"] = _content_id(_ROW_KIND, value, id_key="comparison_id")
    return value


def _assert_reverse_diff(
    selectors: Sequence[tuple[str, str, str, str, str]],
    before: Mapping[tuple[str, str, str, str, str], Sequence[Mapping[str, Any]]],
    after: Mapping[tuple[str, str, str, str, str], Sequence[Mapping[str, Any]]],
    coverage: Mapping[str, str],
) -> None:
    reverse = {
        "added": "removed",
        "removed": "added",
        "modified": "modified",
        "unchanged": "unchanged",
        "ambiguous": "ambiguous",
        "unresolved": "unresolved",
    }
    for selector in selectors:
        forward, _ = _classification(
            before.get(selector, ()), after.get(selector, ()), coverage_state=coverage[selector[0]]
        )
        backward, _ = _classification(
            after.get(selector, ()), before.get(selector, ()), coverage_state=coverage[selector[0]]
        )
        if backward != reverse[forward]:
            _fail("comparison.reverse-invariant", "/comparisons", "reverse classification differs")


def _expectation_selector(value: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return _group_selector(value)


def _expected_classification(value: Mapping[str, Any], *, path: str) -> str:
    before = value["baseline_semantic_fingerprint"]
    after = value["candidate_semantic_fingerprint"]
    if before is None and after is None:
        _fail("envelope.expectation", path, "both expected fingerprints cannot be null")
    if before is None:
        return "added"
    if after is None:
        return "removed"
    return "unchanged" if before == after else "modified"


def _validate_expectation(value: Any, *, path: str) -> None:
    keys = {
        "family",
        "record_kind",
        "descriptor_kind",
        "operation",
        "correlation",
        "baseline_semantic_fingerprint",
        "candidate_semantic_fingerprint",
    }
    if type(value) is not dict or set(value) != keys:
        _fail("envelope.expectation", path, "expectation is not closed")
    for key in ("family", "record_kind", "descriptor_kind"):
        _string(value[key], path=f"{path}/{key}")
    if value["record_kind"] not in {"registry-observation", "effect-observation"}:
        _fail("envelope.expectation", f"{path}/record_kind", "record kind is unsupported")
    _string_or_none(value["operation"], path=f"{path}/operation")
    if value["record_kind"] == "registry-observation" and value["operation"] is not None:
        _fail("envelope.expectation", f"{path}/operation", "registry expectation has operation")
    if value["record_kind"] == "effect-observation" and value["operation"] is None:
        _fail("envelope.expectation", f"{path}/operation", "effect expectation lacks operation")
    if type(value["correlation"]) is not dict or not value["correlation"]:
        _fail("envelope.expectation", f"{path}/correlation", "stable correspondence is required")
    for key in ("baseline_semantic_fingerprint", "candidate_semantic_fingerprint"):
        fingerprint = value[key]
        if fingerprint is not None and (
            type(fingerprint) is not str or _SHA256.fullmatch(fingerprint) is None
        ):
            _fail("envelope.expectation", f"{path}/{key}", "fingerprint is not lowercase SHA-256")
    _expected_classification(value, path=path)


def build_change_envelope(
    *,
    owner_id: str,
    baseline_snapshot_id: str,
    baseline_capture_id: str,
    context_id: str,
    pack_profile_id: str,
    platform_profile_id: str,
    physical_side: str,
    stage: str,
    expectations: Sequence[Mapping[str, Any]],
    state: str = "closed",
    allow_unlisted_changes: bool = False,
    limitations: Sequence[str] = (),
    bounds: ComparisonBounds = DEFAULT_BOUNDS,
) -> dict[str, Any]:
    """Seal an owner-declared expected-effect envelope."""

    value: dict[str, Any] = {
        "format": CHANGE_ENVELOPE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "canonicalizer": CANONICALIZER_ID,
        "envelope_id": "",
        "state": state,
        "authority": {
            "owner_id": owner_id,
            "claim": "declared expected structural effects only",
            "causality_authority": "none",
            "action_authorization": "none",
        },
        "binding": {
            "baseline_snapshot_id": baseline_snapshot_id,
            "baseline_capture_id": baseline_capture_id,
            "context_id": context_id,
            "pack_profile_id": pack_profile_id,
            "platform_profile_id": platform_profile_id,
            "physical_side": physical_side,
            "stage": stage,
        },
        "expectations": [_plain_json(row, path="/expectations") for row in expectations],
        "allow_unlisted_changes": allow_unlisted_changes,
        "limitations": list(limitations),
    }
    value["expectations"] = sorted(value["expectations"], key=_expectation_selector)
    value["envelope_id"] = _content_id(_ENVELOPE_KIND, value, id_key="envelope_id")
    return validate_change_envelope_shape(value, bounds=bounds)


def validate_change_envelope_shape(
    value: Mapping[str, Any], *, bounds: ComparisonBounds = DEFAULT_BOUNDS
) -> dict[str, Any]:
    """Validate a declared envelope's shape and content identity."""

    plain = _plain_json(value, path="/change_envelope")
    if type(plain) is not dict or set(plain) != {
        "format",
        "schema_version",
        "canonicalizer",
        "envelope_id",
        "state",
        "authority",
        "binding",
        "expectations",
        "allow_unlisted_changes",
        "limitations",
    }:
        _fail("envelope.invalid", "/change_envelope", "envelope has unexpected keys")
    if plain["format"] != CHANGE_ENVELOPE_FORMAT or plain["schema_version"] != SCHEMA_VERSION:
        _fail("envelope.format", "/change_envelope/format", "unsupported envelope format")
    if plain["canonicalizer"] != CANONICALIZER_ID:
        _fail("envelope.canonicalizer", "/change_envelope/canonicalizer", "canonicalizer differs")
    _envelope_id(plain["envelope_id"], path="/change_envelope/envelope_id")
    if plain["envelope_id"] != _content_id(_ENVELOPE_KIND, plain, id_key="envelope_id"):
        _fail("envelope.identity", "/change_envelope/envelope_id", "envelope identity differs")
    if plain["state"] not in {"open", "closed"}:
        _fail("envelope.state", "/change_envelope/state", "envelope state is unsupported")
    authority = plain["authority"]
    if type(authority) is not dict or set(authority) != {
        "owner_id",
        "claim",
        "causality_authority",
        "action_authorization",
    }:
        _fail("envelope.authority", "/change_envelope/authority", "authority is malformed")
    _string(authority["owner_id"], path="/change_envelope/authority/owner_id")
    if authority != {
        "owner_id": authority["owner_id"],
        "claim": "declared expected structural effects only",
        "causality_authority": "none",
        "action_authorization": "none",
    }:
        _fail("envelope.authority", "/change_envelope/authority", "authority exceeds expected effects")
    binding = plain["binding"]
    binding_keys = {
        "baseline_snapshot_id",
        "baseline_capture_id",
        "context_id",
        "pack_profile_id",
        "platform_profile_id",
        "physical_side",
        "stage",
    }
    if type(binding) is not dict or set(binding) != binding_keys:
        _fail("envelope.binding", "/change_envelope/binding", "binding is malformed")
    for key in binding_keys:
        _string(binding[key], path=f"/change_envelope/binding/{key}")
    expectations = plain["expectations"]
    if type(expectations) is not list or len(expectations) > bounds.max_envelope_expectations:
        _fail("envelope.expectations", "/change_envelope/expectations", "expectations exceed bound")
    selectors: list[tuple[str, str, str, str, str]] = []
    for index, row in enumerate(expectations):
        _validate_expectation(row, path=f"/change_envelope/expectations/{index}")
        selectors.append(_expectation_selector(row))
    if selectors != sorted(selectors) or len(selectors) != len(set(selectors)):
        _fail("envelope.order", "/change_envelope/expectations", "expectations are not ordered and unique")
    if type(plain["allow_unlisted_changes"]) is not bool:
        _fail("envelope.invalid", "/change_envelope/allow_unlisted_changes", "expected boolean")
    if plain["state"] == "closed" and plain["allow_unlisted_changes"]:
        _fail("envelope.open-policy", "/change_envelope", "closed envelope cannot allow unlisted changes")
    if type(plain["limitations"]) is not list or any(
        type(item) is not str or not item for item in plain["limitations"]
    ):
        _fail("envelope.invalid", "/change_envelope/limitations", "limitations are malformed")
    return plain


def _envelope_binding(
    baseline: Mapping[str, Any], pair_binding: Mapping[str, Any]
) -> dict[str, str]:
    return {
        "baseline_snapshot_id": baseline["binding"]["source_snapshot_id"],
        "baseline_capture_id": baseline["binding"]["source_capture_id"],
        "context_id": pair_binding["context_id"],
        "pack_profile_id": pair_binding["pack_profile_id"],
        "platform_profile_id": pair_binding["platform_profile_id"],
        "physical_side": pair_binding["physical_side"],
        "stage": pair_binding["stage"],
    }


def _unsupported_entry(
    *,
    family: str,
    reason: str,
    comparison_id: str | None = None,
    expectation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "family": family,
        "reason": reason,
        "comparison_id": comparison_id,
        "expectation": None if expectation is None else _plain_json(expectation, path="/assessment"),
    }


def _unsupported_selector(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    expectation = value.get("expectation")
    return (
        value["family"],
        value["reason"],
        value.get("comparison_id") or "",
        "" if expectation is None else _canonical_bytes(expectation).decode("utf-8"),
    )


def _empty_assessment() -> dict[str, Any]:
    return {
        "state": "comparison-only",
        "expected_and_observed_comparison_ids": [],
        "unexpected_observed_comparison_ids": [],
        "expected_not_observed": [],
        "unsupported_or_unobservable": [],
        "expectation_mismatches": [],
    }


def _expectation_fingerprint_lists(value: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    before = value["baseline_semantic_fingerprint"]
    after = value["candidate_semantic_fingerprint"]
    return ([] if before is None else [before], [] if after is None else [after])


def _assess_envelope(
    envelope: Mapping[str, Any] | None,
    comparisons: Sequence[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    pair_binding: Mapping[str, Any],
    coverage: Mapping[str, str],
    bounds: ComparisonBounds,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    coverage_gaps = [
        _unsupported_entry(family=family, reason=f"coverage-{state}")
        for family, state in sorted(coverage.items())
        if state != "complete"
    ]
    observed_uncertain = [
        _unsupported_entry(
            family=row["family"], reason=row["reason"], comparison_id=row["comparison_id"]
        )
        for row in comparisons
        if row["classification"] in {"ambiguous", "unresolved"}
    ]
    baseline_declared = baseline["binding"]["declared_change_envelope_id"]
    candidate_declared = candidate["binding"]["declared_change_envelope_id"]
    if envelope is None:
        if baseline_declared is not None or candidate_declared is not None:
            _fail("incomparable.change-envelope-required", "/binding", "capture declares an omitted envelope")
        assessment = _empty_assessment()
        assessment["unsupported_or_unobservable"] = sorted(
            {
                _canonical_bytes(row).decode("utf-8"): row
                for row in [*coverage_gaps, *observed_uncertain]
            }.values(),
            key=_unsupported_selector,
        )
        return assessment, None
    if not coverage:
        _fail("incomparable.empty-coverage", "/binding/coverage", "empty coverage cannot assess an envelope")
    checked = validate_change_envelope_shape(envelope, bounds=bounds)
    if checked["state"] != "closed":
        _fail("incomparable.change-envelope-open", "/change_envelope/state", "open envelope cannot be assessed")
    if checked["binding"] != _envelope_binding(baseline, pair_binding):
        _fail("incomparable.change-envelope-binding", "/change_envelope/binding", "envelope is not baseline-bound")
    if baseline_declared is not None:
        _fail(
            "incomparable.baseline-envelope",
            "/baseline",
            "baseline envelope declaration must be null for this pair binding",
        )
    if candidate_declared != checked["envelope_id"]:
        _fail(
            "incomparable.candidate-envelope",
            "/candidate/binding/declared_change_envelope_id",
            "candidate declaration does not bind this exact envelope",
        )
    actual = {_group_selector(row): row for row in comparisons}
    expected = {_expectation_selector(row): row for row in checked["expectations"]}
    matched: list[str] = []
    missing: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = list(coverage_gaps)
    mismatches: list[dict[str, Any]] = []
    for selector, expectation in expected.items():
        row = actual.get(selector)
        if row is None:
            coverage_state = coverage.get(expectation["family"])
            if coverage_state != "complete":
                unsupported.append(
                    _unsupported_entry(
                        family=expectation["family"],
                        reason=f"coverage-{coverage_state or 'unavailable'}",
                        expectation=expectation,
                    )
                )
            else:
                missing.append(expectation)
            continue
        if row["classification"] in {"ambiguous", "unresolved"}:
            unsupported.append(
                _unsupported_entry(
                    family=row["family"],
                    reason=row["reason"],
                    comparison_id=row["comparison_id"],
                    expectation=expectation,
                )
            )
            continue
        expected_before, expected_after = _expectation_fingerprint_lists(expectation)
        expected_classification = _expected_classification(expectation, path="/expectation")
        if (
            row["classification"] == expected_classification
            and row["baseline_semantic_fingerprints"] == expected_before
            and row["candidate_semantic_fingerprints"] == expected_after
        ):
            matched.append(row["comparison_id"])
        else:
            mismatches.append(
                {
                    "comparison_id": row["comparison_id"],
                    "expected_classification": expected_classification,
                    "observed_classification": row["classification"],
                    "expected_baseline_semantic_fingerprint": expectation[
                        "baseline_semantic_fingerprint"
                    ],
                    "expected_candidate_semantic_fingerprint": expectation[
                        "candidate_semantic_fingerprint"
                    ],
                    "observed_baseline_semantic_fingerprints": row[
                        "baseline_semantic_fingerprints"
                    ],
                    "observed_candidate_semantic_fingerprints": row[
                        "candidate_semantic_fingerprints"
                    ],
                }
            )
    unexpected: list[str] = []
    for selector, row in actual.items():
        if selector in expected:
            continue
        if row["classification"] in {"ambiguous", "unresolved"}:
            unsupported.append(
                _unsupported_entry(
                    family=row["family"], reason=row["reason"], comparison_id=row["comparison_id"]
                )
            )
        if row["classification"] != "unchanged":
            unexpected.append(row["comparison_id"])
    if unsupported:
        state = "unresolved"
    elif missing or mismatches or unexpected:
        state = "mismatch"
    else:
        state = "matches-declared-envelope"
    return {
        "state": state,
        "expected_and_observed_comparison_ids": sorted(matched),
        "unexpected_observed_comparison_ids": sorted(set(unexpected)),
        "expected_not_observed": sorted(missing, key=_expectation_selector),
        "unsupported_or_unobservable": sorted(
            {
                _canonical_bytes(row).decode("utf-8"): row for row in unsupported
            }.values(),
            key=_unsupported_selector,
        ),
        "expectation_mismatches": sorted(mismatches, key=lambda row: row["comparison_id"]),
    }, checked


def _compare_projected_pair(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    change_envelope: Mapping[str, Any] | None,
    bounds: ComparisonBounds,
) -> dict[str, Any]:
    pair_binding = _require_aligned(baseline, candidate)
    if baseline["binding"]["bounds"] != bounds.as_dict():
        _fail("incomparable.requested-bounds", "/binding/bounds", "requested bounds differ")
    coverage = {row["family"]: row["state"] for row in pair_binding["coverage"]}
    before: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    after: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in baseline["records"]:
        before[_group_selector(row)].append(row)
    for row in candidate["records"]:
        after[_group_selector(row)].append(row)
    selectors = sorted(set(before) | set(after))
    if len(selectors) > bounds.max_comparisons:
        _fail("bounds.comparisons", "/comparisons", "comparison count exceeds bound")
    _assert_reverse_diff(selectors, before, after, coverage)
    comparisons = [
        _comparison_row(
            selector,
            sorted(before.get(selector, []), key=lambda row: row["source_record_id"]),
            sorted(after.get(selector, []), key=lambda row: row["source_record_id"]),
            coverage_state=coverage[selector[0]],
        )
        for selector in selectors
    ]
    assessment, checked_envelope = _assess_envelope(
        change_envelope,
        comparisons,
        baseline=baseline,
        candidate=candidate,
        pair_binding=pair_binding,
        coverage=coverage,
        bounds=bounds,
    )
    counts = Counter(row["classification"] for row in comparisons)
    envelope_id = None if checked_envelope is None else checked_envelope["envelope_id"]
    value: dict[str, Any] = {
        "format": COMPARISON_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "canonicalizer": CANONICALIZER_ID,
        "comparison_id": "",
        "authority": dict(_COMPARISON_AUTHORITY),
        "binding": {
            "baseline_snapshot_id": baseline["binding"]["source_snapshot_id"],
            "candidate_snapshot_id": candidate["binding"]["source_snapshot_id"],
            "baseline_projection_id": baseline["projection_id"],
            "candidate_projection_id": candidate["projection_id"],
            "baseline_capture_id": baseline["binding"]["source_capture_id"],
            "candidate_capture_id": candidate["binding"]["source_capture_id"],
            "baseline_declared_change_envelope_id": baseline["binding"][
                "declared_change_envelope_id"
            ],
            "candidate_declared_change_envelope_id": candidate["binding"][
                "declared_change_envelope_id"
            ],
            "context_id": pair_binding["context_id"],
            "pack_profile_id": pair_binding["pack_profile_id"],
            "platform_profile_id": pair_binding["platform_profile_id"],
            "physical_side": pair_binding["physical_side"],
            "stage": pair_binding["stage"],
            "coverage": pair_binding["coverage"],
            "change_envelope_id": envelope_id,
            "bounds": bounds.as_dict(),
        },
        "change_envelope": checked_envelope,
        "comparisons": comparisons,
        "envelope_assessment": assessment,
        "summary": {
            "state": assessment["state"],
            "comparisons": len(comparisons),
            "by_classification": {
                classification: counts[classification] for classification in CLASSIFICATIONS
            },
            "structural_changes": counts["added"] + counts["removed"] + counts["modified"],
            "uncertain": counts["ambiguous"] + counts["unresolved"],
            "reverse_diff_invariant": "satisfied",
        },
        "limitations": list(_COMPARISON_LIMITATIONS),
    }
    value["comparison_id"] = _content_id(_COMPARISON_KIND, value, id_key="comparison_id")
    return validate_observed_effect_comparison_shape(value)


def compare_bounded_effects(
    baseline_snapshot: Mapping[str, Any],
    candidate_snapshot: Mapping[str, Any],
    *,
    adapters: Sequence[EffectFamilyAdapter],
    change_envelope: Mapping[str, Any] | None = None,
    bounds: ComparisonBounds = DEFAULT_BOUNDS,
) -> dict[str, Any]:
    """Reproject both raw snapshots with one adapter set, then compare."""

    selected = tuple(adapters)
    baseline = project_effect_snapshot(
        baseline_snapshot, adapters=selected, bounds=bounds, label="baseline"
    )
    candidate = project_effect_snapshot(
        candidate_snapshot, adapters=selected, bounds=bounds, label="candidate"
    )
    return _compare_projected_pair(
        baseline,
        candidate,
        change_envelope=change_envelope,
        bounds=bounds,
    )


def _derived_row_semantics(
    row: Mapping[str, Any], *, coverage_state: str, path: str
) -> tuple[str, str]:
    before_ids = row["baseline_source_record_ids"]
    after_ids = row["candidate_source_record_ids"]
    before_fingerprints = row["baseline_semantic_fingerprints"]
    after_fingerprints = row["candidate_semantic_fingerprints"]
    correlation = row["correlation"]
    if len(before_fingerprints) > len(before_ids) or len(after_fingerprints) > len(after_ids):
        _fail(
            "result.row-semantics",
            path,
            "a side cannot carry more unique fingerprints than source records",
        )
    if correlation is not None and (
        (before_ids and not before_fingerprints)
        or (after_ids and not after_fingerprints)
    ):
        _fail(
            "result.row-semantics",
            path,
            "resolved source records require semantic fingerprints",
        )
    if not before_ids and before_fingerprints:
        _fail("result.row-semantics", path, "baseline fingerprints lack source records")
    if not after_ids and after_fingerprints:
        _fail("result.row-semantics", path, "candidate fingerprints lack source records")
    if correlation is None and (before_fingerprints or after_fingerprints):
        _fail(
            "result.row-semantics",
            path,
            "unresolved correspondence cannot carry semantic fingerprints",
        )
    if coverage_state != "complete":
        return "unresolved", f"coverage-{coverage_state}"
    if correlation is None:
        return "unresolved", "semantic-projection-unresolved"
    if len(before_ids) > 1 or len(after_ids) > 1:
        return "ambiguous", "non-unique-correlation"
    if before_ids and after_ids:
        if len(before_fingerprints) != 1 or len(after_fingerprints) != 1:
            _fail(
                "result.row-semantics",
                path,
                "unique correspondence requires one fingerprint on each side",
            )
        if before_fingerprints == after_fingerprints:
            return "unchanged", "semantic-fingerprint-equal"
        return "modified", "semantic-fingerprint-changed"
    if after_ids:
        if len(after_fingerprints) != 1:
            _fail("result.row-semantics", path, "added row requires one fingerprint")
        return "added", "candidate-only-complete-coverage"
    if len(before_fingerprints) != 1:
        _fail("result.row-semantics", path, "removed row requires one fingerprint")
    return "removed", "baseline-only-complete-coverage"


def _validate_comparison_row(
    row: Any, *, path: str, coverage_state: str
) -> None:
    keys = {
        "comparison_id",
        "canonicalizer",
        "family",
        "record_kind",
        "descriptor_kind",
        "operation",
        "correlation",
        "classification",
        "reason",
        "baseline_source_record_ids",
        "candidate_source_record_ids",
        "baseline_semantic_fingerprints",
        "candidate_semantic_fingerprints",
    }
    if type(row) is not dict or set(row) != keys:
        _fail("result.row", path, "comparison row is not closed")
    if row["canonicalizer"] != CANONICALIZER_ID:
        _fail("result.row", f"{path}/canonicalizer", "canonicalizer differs")
    if row["comparison_id"] != _content_id(_ROW_KIND, row, id_key="comparison_id"):
        _fail("result.row-identity", f"{path}/comparison_id", "row identity differs")
    for key in ("family", "record_kind", "descriptor_kind", "reason"):
        _string(row[key], path=f"{path}/{key}")
    if row["record_kind"] not in {"registry-observation", "effect-observation"}:
        _fail("result.row", f"{path}/record_kind", "record kind is unsupported")
    _string_or_none(row["operation"], path=f"{path}/operation")
    if row["record_kind"] == "registry-observation" and row["operation"] is not None:
        _fail("result.row", f"{path}/operation", "registry row has operation")
    if row["record_kind"] == "effect-observation" and row["operation"] is None:
        _fail("result.row", f"{path}/operation", "effect row lacks operation")
    if row["correlation"] is not None and (
        type(row["correlation"]) is not dict or not row["correlation"]
    ):
        _fail("result.row", f"{path}/correlation", "correlation is malformed")
    if row["classification"] not in CLASSIFICATIONS:
        _fail("result.row", f"{path}/classification", "classification is unsupported")
    for key in ("baseline_source_record_ids", "candidate_source_record_ids"):
        values = row[key]
        if (
            type(values) is not list
            or any(type(item) is not str or not item for item in values)
            or values != sorted(set(values))
        ):
            _fail("result.row", f"{path}/{key}", "source IDs are not ordered unique strings")
    if not row["baseline_source_record_ids"] and not row["candidate_source_record_ids"]:
        _fail("result.row", path, "comparison row cannot be empty, including unresolved rows")
    for key in ("baseline_semantic_fingerprints", "candidate_semantic_fingerprints"):
        values = row[key]
        if (
            type(values) is not list
            or any(type(item) is not str or _SHA256.fullmatch(item) is None for item in values)
            or values != sorted(set(values))
        ):
            _fail("result.row", f"{path}/{key}", "fingerprints are malformed")
    expected_classification, expected_reason = _derived_row_semantics(
        row, coverage_state=coverage_state, path=path
    )
    if (
        row["classification"] != expected_classification
        or row["reason"] != expected_reason
    ):
        _fail(
            "result.row-semantics",
            path,
            "classification or reason differs from coverage, cardinalities, and fingerprints",
        )


def validate_observed_effect_comparison_shape(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate sealed output shape; this is not raw-source validation."""

    plain = _plain_json(value, path="/comparison")
    if type(plain) is not dict or set(plain) != {
        "format",
        "schema_version",
        "canonicalizer",
        "comparison_id",
        "authority",
        "binding",
        "change_envelope",
        "comparisons",
        "envelope_assessment",
        "summary",
        "limitations",
    }:
        _fail("result.invalid", "/comparison", "comparison has unexpected keys")
    if plain["format"] != COMPARISON_FORMAT or plain["schema_version"] != SCHEMA_VERSION:
        _fail("result.format", "/comparison/format", "unsupported comparison format")
    if plain["canonicalizer"] != CANONICALIZER_ID:
        _fail("result.canonicalizer", "/comparison/canonicalizer", "canonicalizer differs")
    if plain["comparison_id"] != _content_id(_COMPARISON_KIND, plain, id_key="comparison_id"):
        _fail("result.identity", "/comparison/comparison_id", "comparison identity differs")
    if plain["authority"] != _COMPARISON_AUTHORITY:
        _fail("result.authority", "/comparison/authority", "authority boundary differs")
    binding = plain["binding"]
    binding_keys = {
        "baseline_snapshot_id",
        "candidate_snapshot_id",
        "baseline_projection_id",
        "candidate_projection_id",
        "baseline_capture_id",
        "candidate_capture_id",
        "baseline_declared_change_envelope_id",
        "candidate_declared_change_envelope_id",
        "context_id",
        "pack_profile_id",
        "platform_profile_id",
        "physical_side",
        "stage",
        "coverage",
        "change_envelope_id",
        "bounds",
    }
    if type(binding) is not dict or set(binding) != binding_keys:
        _fail("result.binding", "/comparison/binding", "binding is malformed")
    for key in binding_keys - {
        "baseline_declared_change_envelope_id",
        "candidate_declared_change_envelope_id",
        "coverage",
        "change_envelope_id",
        "bounds",
    }:
        _string(binding[key], path=f"/comparison/binding/{key}")
    _projection_id(
        binding["baseline_projection_id"],
        path="/comparison/binding/baseline_projection_id",
    )
    _projection_id(
        binding["candidate_projection_id"],
        path="/comparison/binding/candidate_projection_id",
    )
    for key in (
        "baseline_declared_change_envelope_id",
        "candidate_declared_change_envelope_id",
        "change_envelope_id",
    ):
        _envelope_id_or_none(binding[key], path=f"/comparison/binding/{key}")
    if binding["baseline_snapshot_id"] == binding["candidate_snapshot_id"]:
        _fail("result.binding", "/comparison/binding", "snapshot identities are identical")
    if binding["baseline_capture_id"] == binding["candidate_capture_id"]:
        _fail("result.binding", "/comparison/binding", "capture identities are identical")
    coverage_rows = _validate_coverage(binding["coverage"], path="/comparison/binding/coverage")
    coverage = {row["family"]: row["state"] for row in coverage_rows}
    checked_bounds = _checked_bounds(binding["bounds"], path="/comparison/binding/bounds")
    envelope = plain["change_envelope"]
    if envelope is None:
        if (
            binding["change_envelope_id"] is not None
            or binding["baseline_declared_change_envelope_id"] is not None
            or binding["candidate_declared_change_envelope_id"] is not None
        ):
            _fail("result.envelope", "/comparison", "null envelope binding is inconsistent")
    else:
        checked_envelope = validate_change_envelope_shape(envelope, bounds=checked_bounds)
        if checked_envelope["state"] != "closed":
            _fail("result.envelope", "/comparison/change_envelope/state", "embedded envelope is open")
        if not coverage:
            _fail("result.envelope", "/comparison/binding/coverage", "empty coverage cannot assess an envelope")
        if binding["change_envelope_id"] != checked_envelope["envelope_id"]:
            _fail("result.envelope", "/comparison/binding/change_envelope_id", "embedded ID differs")
        if binding["baseline_declared_change_envelope_id"] is not None:
            _fail(
                "result.envelope",
                "/comparison/binding",
                "baseline envelope declaration must be null for this pair binding",
            )
        if binding["candidate_declared_change_envelope_id"] != checked_envelope["envelope_id"]:
            _fail("result.envelope", "/comparison/binding", "candidate did not declare envelope")
        expected_binding = {
            "baseline_snapshot_id": binding["baseline_snapshot_id"],
            "baseline_capture_id": binding["baseline_capture_id"],
            "context_id": binding["context_id"],
            "pack_profile_id": binding["pack_profile_id"],
            "platform_profile_id": binding["platform_profile_id"],
            "physical_side": binding["physical_side"],
            "stage": binding["stage"],
        }
        if checked_envelope["binding"] != expected_binding:
            _fail("result.envelope", "/comparison/change_envelope/binding", "embedded binding differs")
    rows = plain["comparisons"]
    if type(rows) is not list or len(rows) > checked_bounds.max_comparisons:
        _fail("result.rows", "/comparison/comparisons", "comparison rows exceed bound")
    selectors: list[tuple[str, str, str, str, str]] = []
    row_ids: set[str] = set()
    for index, row in enumerate(rows):
        path = f"/comparison/comparisons/{index}"
        if type(row) is not dict or row.get("family") not in coverage:
            _fail("result.row", f"{path}/family", "family has no coverage")
        _validate_comparison_row(
            row,
            path=path,
            coverage_state=coverage[row["family"]],
        )
        if row["comparison_id"] in row_ids:
            _fail("result.row", path, "comparison identity is duplicate")
        row_ids.add(row["comparison_id"])
        selectors.append(_group_selector(row))
    if selectors != sorted(selectors) or len(selectors) != len(set(selectors)):
        _fail("result.order", "/comparison/comparisons", "selectors are not ordered and unique")
    assessment = plain["envelope_assessment"]
    assessment_keys = {
        "state",
        "expected_and_observed_comparison_ids",
        "unexpected_observed_comparison_ids",
        "expected_not_observed",
        "unsupported_or_unobservable",
        "expectation_mismatches",
    }
    if type(assessment) is not dict or set(assessment) != assessment_keys:
        _fail("result.assessment", "/comparison/envelope_assessment", "assessment is malformed")
    if assessment["state"] not in {
        "comparison-only",
        "matches-declared-envelope",
        "mismatch",
        "unresolved",
    }:
        _fail("result.assessment", "/comparison/envelope_assessment/state", "state is unsupported")
    rows_by_id = {row["comparison_id"]: row for row in rows}
    for key in ("expected_and_observed_comparison_ids", "unexpected_observed_comparison_ids"):
        values = assessment[key]
        if (
            type(values) is not list
            or values != sorted(set(values))
            or any(item not in rows_by_id for item in values)
        ):
            _fail("result.assessment", f"/comparison/envelope_assessment/{key}", "IDs are invalid")
    missing = assessment["expected_not_observed"]
    if type(missing) is not list:
        _fail("result.assessment", "/comparison/envelope_assessment/expected_not_observed", "expected array")
    missing_selectors: list[tuple[str, str, str, str, str]] = []
    for index, expectation in enumerate(missing):
        _validate_expectation(expectation, path=f"/comparison/envelope_assessment/expected_not_observed/{index}")
        missing_selectors.append(_expectation_selector(expectation))
    if missing_selectors != sorted(missing_selectors) or len(missing_selectors) != len(set(missing_selectors)):
        _fail("result.assessment", "/comparison/envelope_assessment/expected_not_observed", "missing expectations are not ordered")
    unsupported = assessment["unsupported_or_unobservable"]
    if type(unsupported) is not list:
        _fail("result.assessment", "/comparison/envelope_assessment/unsupported_or_unobservable", "expected array")
    unsupported_selectors: list[tuple[str, str, str, str]] = []
    for index, item in enumerate(unsupported):
        path = f"/comparison/envelope_assessment/unsupported_or_unobservable/{index}"
        if type(item) is not dict or set(item) != {"family", "reason", "comparison_id", "expectation"}:
            _fail("result.assessment", path, "unsupported entry is malformed")
        _string(item["family"], path=f"{path}/family")
        _string(item["reason"], path=f"{path}/reason")
        comparison_id = _string_or_none(item["comparison_id"], path=f"{path}/comparison_id")
        if comparison_id is not None and comparison_id not in row_ids:
            _fail("result.assessment", path, "unsupported comparison ID is absent")
        if item["expectation"] is not None:
            _validate_expectation(item["expectation"], path=f"{path}/expectation")
        unsupported_selectors.append(_unsupported_selector(item))
    if unsupported_selectors != sorted(unsupported_selectors) or len(unsupported_selectors) != len(set(unsupported_selectors)):
        _fail("result.assessment", "/comparison/envelope_assessment/unsupported_or_unobservable", "unsupported entries are not ordered")
    mismatches = assessment["expectation_mismatches"]
    mismatch_keys = {
        "comparison_id",
        "expected_classification",
        "observed_classification",
        "expected_baseline_semantic_fingerprint",
        "expected_candidate_semantic_fingerprint",
        "observed_baseline_semantic_fingerprints",
        "observed_candidate_semantic_fingerprints",
    }
    if type(mismatches) is not list:
        _fail("result.assessment", "/comparison/envelope_assessment/expectation_mismatches", "expected array")
    mismatch_ids: list[str] = []
    for index, mismatch in enumerate(mismatches):
        path = f"/comparison/envelope_assessment/expectation_mismatches/{index}"
        if type(mismatch) is not dict or set(mismatch) != mismatch_keys:
            _fail("result.assessment", path, "expectation mismatch is malformed")
        comparison_id = _string(mismatch["comparison_id"], path=f"{path}/comparison_id")
        row = rows_by_id.get(comparison_id)
        if row is None or mismatch["observed_classification"] != row["classification"]:
            _fail("result.assessment", path, "expectation mismatch does not reopen")
        if mismatch["expected_classification"] not in {"added", "removed", "modified", "unchanged"}:
            _fail("result.assessment", path, "expected classification is unsupported")
        for key in (
            "expected_baseline_semantic_fingerprint",
            "expected_candidate_semantic_fingerprint",
        ):
            expected_fingerprint = mismatch[key]
            if expected_fingerprint is not None and (
                type(expected_fingerprint) is not str
                or _SHA256.fullmatch(expected_fingerprint) is None
            ):
                _fail("result.assessment", f"{path}/{key}", "expected fingerprint is invalid")
        if mismatch["observed_baseline_semantic_fingerprints"] != row["baseline_semantic_fingerprints"]:
            _fail("result.assessment", path, "baseline fingerprints differ from row")
        if mismatch["observed_candidate_semantic_fingerprints"] != row["candidate_semantic_fingerprints"]:
            _fail("result.assessment", path, "candidate fingerprints differ from row")
        mismatch_ids.append(comparison_id)
    if mismatch_ids != sorted(set(mismatch_ids)):
        _fail("result.assessment", "/comparison/envelope_assessment/expectation_mismatches", "mismatches are not ordered")
    rebound_baseline = {
        "binding": {
            "source_snapshot_id": binding["baseline_snapshot_id"],
            "source_capture_id": binding["baseline_capture_id"],
            "declared_change_envelope_id": binding[
                "baseline_declared_change_envelope_id"
            ],
        }
    }
    rebound_candidate = {
        "binding": {
            "source_snapshot_id": binding["candidate_snapshot_id"],
            "source_capture_id": binding["candidate_capture_id"],
            "declared_change_envelope_id": binding[
                "candidate_declared_change_envelope_id"
            ],
        }
    }
    rebound_pair = {
        key: binding[key]
        for key in (
            "context_id",
            "pack_profile_id",
            "platform_profile_id",
            "physical_side",
            "stage",
            "coverage",
        )
    }
    expected_assessment, _ = _assess_envelope(
        envelope,
        rows,
        baseline=rebound_baseline,
        candidate=rebound_candidate,
        pair_binding=rebound_pair,
        coverage=coverage,
        bounds=checked_bounds,
    )
    if assessment != expected_assessment:
        _fail(
            "result.assessment-semantics",
            "/comparison/envelope_assessment",
            "assessment differs from the embedded envelope and comparison rows",
        )
    summary = plain["summary"]
    counts = Counter(row["classification"] for row in rows)
    expected_summary = {
        "state": assessment["state"],
        "comparisons": len(rows),
        "by_classification": {
            classification: counts[classification] for classification in CLASSIFICATIONS
        },
        "structural_changes": counts["added"] + counts["removed"] + counts["modified"],
        "uncertain": counts["ambiguous"] + counts["unresolved"],
        "reverse_diff_invariant": "satisfied",
    }
    if type(summary) is not dict or summary != expected_summary:
        _fail("result.summary", "/comparison/summary", "summary is stale")
    if plain["limitations"] != _COMPARISON_LIMITATIONS:
        _fail("result.limitations", "/comparison/limitations", "exact limitations differ")
    return plain


def validate_source_bound_comparison(
    value: Mapping[str, Any],
    *,
    baseline_snapshot: Mapping[str, Any],
    candidate_snapshot: Mapping[str, Any],
    adapters: Sequence[EffectFamilyAdapter],
    bounds: ComparisonBounds = DEFAULT_BOUNDS,
) -> dict[str, Any]:
    """Reproject both raw sources and rederive the entire embedded-envelope result."""

    checked = validate_observed_effect_comparison_shape(value)
    expected = compare_bounded_effects(
        baseline_snapshot,
        candidate_snapshot,
        adapters=tuple(adapters),
        change_envelope=checked["change_envelope"],
        bounds=bounds,
    )
    if checked != expected:
        _fail("result.source-binding", "/comparison", "result differs from raw-source replay")
    return checked


__all__ = [
    "BoundedEffectComparisonError",
    "CANONICALIZER_ID",
    "CHANGE_ENVELOPE_FORMAT",
    "CLASSIFICATIONS",
    "COMPARISON_FORMAT",
    "COVERAGE_STATES",
    "ComparisonBounds",
    "DEFAULT_BOUNDS",
    "HARD_MAX_COMPARISONS",
    "HARD_MAX_CONTAINER_ITEMS",
    "HARD_MAX_ENVELOPE_EXPECTATIONS",
    "HARD_MAX_INPUT_BYTES",
    "HARD_MAX_INTEGER",
    "HARD_MAX_JSON_DEPTH",
    "HARD_MAX_RECORDS_PER_SNAPSHOT",
    "HARD_MAX_STRING_BYTES",
    "PROJECTION_FORMAT",
    "build_change_envelope",
    "compare_bounded_effects",
    "project_effect_snapshot",
    "validate_change_envelope_shape",
    "validate_effect_snapshot_projection_shape",
    "validate_observed_effect_comparison_shape",
    "validate_source_bound_comparison",
    "validate_source_bound_projection",
]
