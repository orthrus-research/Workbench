"""Strict final-proof composition for the exact-runtime-v2 vertical slice.

This module does not interpret world-generation evidence.  It admits a closed
set of already-produced, caller-pinned small artifacts, verifies their native
content identities and cross-artifact custody links, and publishes one
content-addressed composition receipt.  Large raw captures and canonical
bundles are deliberately outside this final assembly step.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping, Sequence

from .bundle import (
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
)
from .cleanroom_hook_health import (
    EXCLUDED_INCOMPLETE_CASE_ROLE,
    HOOK_HEALTH_EVALUATION_PREFIX,
    HOOK_HEALTH_EVALUATION_SCHEMA,
    REQUIRED_CASE_ROLE,
)
from .cleanroom_matrix import (
    PROJECTION_MATRIX_EVALUATION_PREFIX,
    PROJECTION_MATRIX_EVALUATION_SCHEMA,
    CleanroomBundleProjection,
    parse_cleanroom_bundle_projection,
)
from .semantic_bytecode import (
    SEMANTIC_BYTECODE_COMPARISON_FORMAT,
    SEMANTIC_BYTECODE_POLICY_ID,
    semantic_bytecode_policy_material,
)


PROOF_SPEC_FORMAT = "workbench-worldgen-observatory-exact-runtime-proof-spec-v1"
PROOF_SPEC_PREFIX = "crucible-worldgen-observatory-proof-spec:sha256:"
PROOF_SCHEMA = "workbench.worldgen-observatory.exact-runtime-proof.v1"
PROOF_PREFIX = "crucible-worldgen-observatory-proof:sha256:"

SESSION_AUDIT_SCHEMA = "workbench.crucible.exact-runtime-session-audit.v1"
SESSION_AUDIT_PREFIX = "crucible-exact-runtime-session-audit:sha256:"
WORKER_RECEIPT_SCHEMA = "workbench.crucible.cleanroom-case-worker-receipt.v1"
WORKER_RECEIPT_PREFIX = "crucible-cleanroom-case-worker-receipt:sha256:"
ATLAS_SUITE_FORMAT = "workbench-atlas-worldgen-exact-query-suite-v1"
EXPECTED_ATLAS_WRITER_MOD_ID = "workbench_synthetic_worldgen"
EXPECTED_ATLAS_HANDLER_MOD_ID = "workbench_synthetic_worldgen"
EXPECTED_ATLAS_DIVERGENCE_STATUS = "equal"
EXPECTED_ATLAS_DIMENSION_ID = 0
EXPECTED_ATLAS_BLOCK_POSITION = (1028, 80, 1034)
EXPECTED_ATLAS_EVENT_CLASS = (
    "net.minecraftforge.event.terraingen.OreGenEvent$GenerateMinable"
)
EXPECTED_ATLAS_EVENT_BUS_ID = "forge-bus:ore_gen_bus"
EXPECTED_ATLAS_EVENT_CHUNK = (64, 64)
EXPECTED_ATLAS_COMPARISON_CHECKPOINT = (
    "worldgen-checkpoint:forge.world_generators.final"
)
EXPECTED_ATLAS_COMPARISON_CHUNK = (64, 64)

REQUIRED_CLAIM_LIMITATIONS = tuple(
    sorted(
        (
            "Atlas equality is bounded to one selected checkpoint at chunk (64,64) and its declared domains; the matrix separately compares six checkpoint and six observation-RNG rows.",
            "Completed exact-runtime cases contain no span_throw occurrence; throwable preservation is established by source and synthetic tests.",
            "Crash exit 137 is retained only as launch-log text and is not process-signal provenance.",
            "Integrated-client behavior was not exercised; the retained runtime evidence is dedicated-server scoped.",
            "Named RNG lanes are observation-only digests and do not trace, consume, or replace runtime java.util.Random calls.",
            "Populate and final checkpoints cover chunk (64,64); generation checkpoints cover the four selected chunks.",
            "Semantic bytecode equality is comparison-only; exact Foundation hashes remain custody.",
            "The catalog is bounded to 40 hooks and 21 terrain events; eight source entries affecting twelve rows remain unresolved, and only 14 hooks are runtime-probed.",
            "The lossless fixture is proof-mode telemetry: captures are roughly 1.2 GB, canonical bundles roughly 2.1 GB, and normalization peaks near 8.8 GB RSS; production needs selectors, budgets, summaries, and incremental admission.",
            "The synthetic decorator proves standard Forge lifecycle participation from an independent mod container and package, but it shares the fixture JAR and is neither a separate-artifact nor Recurrent Complex runtime test.",
            "World.setBlockState has an unavailable outer before-state sentinel; the terminal Chunk.setBlockState before-state is retained.",
        )
    )
)

NORMAL_CASES = ("aa-1", "aa-2", "order-reverse")
OBSERVED_CASES = (*NORMAL_CASES, "crash-before-seal")
SESSION_CASES = (*NORMAL_CASES, "observer-off", "crash-before-seal")

EXPECTED_ARTIFACT_ROLES = (
    *(f"session:{case_id}" for case_id in SESSION_CASES),
    *(f"worker:{case_id}" for case_id in OBSERVED_CASES),
    *(f"projection:{case_id}" for case_id in OBSERVED_CASES),
    "evaluation:matrix",
    "evaluation:hook-health",
    "evaluation:semantic-bytecode",
    "evaluation:atlas",
)

_EXECUTION_BY_CASE = {
    "aa-1": "exec-aa1",
    "aa-2": "exec-aa2",
    "order-reverse": "exec-reverse",
    "observer-off": "exec-observer-off",
    "crash-before-seal": "exec-crash",
}
_OUTCOME_BY_CASE = {
    "aa-1": "completed",
    "aa-2": "completed",
    "order-reverse": "completed",
    "observer-off": "completed",
    "crash-before-seal": "crash",
}
_OBSERVER_BY_CASE = {
    "aa-1": True,
    "aa-2": True,
    "order-reverse": True,
    "observer-off": False,
    "crash-before-seal": True,
}
_ROUTE_BY_CASE = {
    "aa-1": "forward",
    "aa-2": "forward",
    "order-reverse": "reverse",
    "observer-off": "forward",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_BYTES = 64 * 1024 * 1024
_HASH_BLOCK_BYTES = 1024 * 1024

_SPEC_KEYS = {"format", "spec_id", "artifacts", "claim_limitations", "output"}
_ARTIFACT_KEYS = {"role", "path", "sha256"}
_OUTPUT_KEYS = {"path"}
_SESSION_KEYS = {
    "audit_id",
    "schema",
    "case_id",
    "outcome",
    "observer_enabled",
    "candidate_lock",
    "fixture_artifact",
    "installed_mod_set",
    "configuration_set",
    "launch_log",
    "raw_capture",
    "fixture_result",
    "foundation_class_dump",
}
_SESSION_RAW_KEYS = {
    "file_sha256",
    "size_bytes",
    "capture_id",
    "row_count",
    "terminal_sequence",
    "terminal_state",
    "stop_control_count",
    "fixture_completion_marker_count",
}
_SESSION_RESULT_KEYS = {
    "file_sha256",
    "canonical_document_sha256",
    "completion_state",
    "save_state",
    "shutdown_state",
    "route_order",
    "route_sha256",
    "semantic_result_sha256",
    "runtime_inventory_sha256",
    "runtime_inventory_count",
}
_FOUNDATION_KEYS = {
    "format",
    "class_count",
    "total_size_bytes",
    "manifest_sha256",
}
_WORKER_KEYS = {
    "receipt_id",
    "schema",
    "spec_id",
    "spec_file_sha256",
    "raw_ndjson_sha256",
    "raw_schema_id",
    "raw_schema_sha256",
    "probe_plan_id",
    "probe_plan_file_sha256",
    "candidate_lock_file_sha256",
    "fixture_artifact_file_sha256",
    "installed_mod_set_manifest_file_sha256",
    "installed_mod_set_manifest_canonical_sha256",
    "configuration_set_manifest_file_sha256",
    "configuration_set_manifest_canonical_sha256",
    "fixture_result_file_sha256",
    "fixture_result_canonical_sha256",
    "fixture_route_order",
    "runtime_mod_inventory_sha256",
    "capture_nonce",
    "raw_row_count",
    "raw_completion_state",
    "class_dump_inventory_sha256",
    "foundation_class_dump_manifest_format",
    "foundation_class_dump_class_count",
    "foundation_class_dump_total_size_bytes",
    "foundation_class_dump_manifest_sha256",
    "actor_inventory_sha256",
    "run_id",
    "publication_state",
    "publication_id",
    "bundle_canonical_sha256",
    "bundle_file_sha256",
    "bundle_record_count",
    "projection_id",
    "projection_canonical_sha256",
    "projection_file_sha256",
}
_MATRIX_KEYS = {
    "evaluation_id",
    "schema",
    "evidence_class",
    "fixed_world_seed_sha256",
    "selector_sha256",
    "required_cases",
    "case_aliases",
    "executions",
    "comparisons",
    "global_checks",
    "matrix_state",
    "matrix_input_mode",
}
_MATRIX_GLOBAL_CHECKS = {
    "semantic_maps_coordinatewise_consistent",
    "runtime_mod_inventory_consistent",
    "cooperative_checkpoints_consistent",
    "cooperative_rng_summaries_consistent",
    "crash_is_incomplete_and_unsealed",
}
_HOOK_HEALTH_KEYS = {
    "evaluation_id",
    "schema",
    "status",
    "claim_scope",
    "validation_scope",
    "candidate",
    "raw_contract",
    "required_case_count",
    "excluded_incomplete_case_count",
    "cases",
}
_SEMANTIC_KEYS = {
    "format",
    "policy_id",
    "case_count",
    "cases",
    "exact_foundation_manifests_all_distinct",
    "semantic_dumps_all_equal",
    "comparison_sha256",
}
_SEMANTIC_CASE_KEYS = {
    "label",
    "manifest_sha256",
    "exact_foundation_manifest_sha256",
    "semantic_dump_sha256",
    "class_count",
    "total_size_bytes",
    "normalized_class_count",
    "normalized_utf8_constants",
    "normalized_annotation_references",
}
_ATLAS_KEYS = {
    "format",
    "query_contract_id",
    "authority",
    "inputs",
    "expectations",
    "answers",
    "crash_rejections",
    "gates",
    "passed",
}
_ATLAS_AUTHORITY_KEYS = {
    "evidence_owner",
    "interpretation_owner",
    "sorting_spool",
}
_ATLAS_INPUT_KEYS = {
    "primary_capture_id",
    "comparison_capture_id",
    "crash_capture_id",
    "dimension_id",
    "block_position",
    "event_span_id",
    "event_selector",
    "comparison_scope_sha256",
    "comparison_checkpoint_id",
    "comparison_chunk",
}
_ATLAS_EVENT_SELECTOR_KEYS = {
    "mode",
    "event_class",
    "bus_id",
    "chunk",
    "occurrence",
}
_ATLAS_EXPECTATION_KEYS = {
    "divergence_status",
    "writer_mod_id",
    "handler_mod_id",
    "crash_status",
}
_ATLAS_QUERY_ANSWER_KEYS = {
    "contract_id",
    "query",
    "status",
    "capture_ids",
    "scope",
    "evidence_ordinals",
    "limitations",
    "result",
}
_ATLAS_ANSWER_KEYS = {
    "who_wrote_block",
    "which_handler_changed_event",
    "first_divergence",
}
_ATLAS_GATE_KEYS = {
    "writer_answered",
    "event_answered",
    "divergence_closed",
    "writer_identity_matches",
    "handler_identity_matches",
    "all_queries_reject_crash",
}


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _FileReceipt:
    path: Path
    size: int
    sha256: str
    identity: tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class ProofArtifact:
    role: str
    path: Path
    expected_sha256: str


@dataclass(frozen=True, slots=True)
class ExactRuntimeProofSpec:
    spec_id: str
    spec_file_sha256: str
    spec_receipt: _FileReceipt
    artifacts: tuple[ProofArtifact, ...]
    claim_limitations: tuple[str, ...]
    output: Path


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _exact_keys(value: Any, expected: set[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == expected,
        f"{context} fields mismatch: missing={sorted(expected - actual)!r}, "
        f"unknown={sorted(actual - expected)!r}",
    )
    return value


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
    raise ValueError(f"floating-point number {token} is not admitted")


def _parse_json(encoded: bytes, *, context: str) -> Any:
    try:
        return json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
        raise CaptureValidationError(f"cannot parse {context}: {exc}") from exc


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _nonempty(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum, f"{context} must be an integer >= {minimum}")
    return value


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _absolute(value: Any, context: str) -> Path:
    _require(isinstance(value, str) and bool(value), f"{context} must be a path string")
    path = Path(value)
    _require(path.is_absolute(), f"{context} must be absolute")
    return path


def _hash_regular(path: Path, *, context: str) -> _FileReceipt:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(before.st_mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISREG(before.st_mode), f"{context} is not a regular file: {path}")
    _require(0 < before.st_size <= _MAX_JSON_BYTES, f"{context} is outside size bounds: {path}")
    expected_identity = _identity(before)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                block = stream.read(_HASH_BLOCK_BYTES)
                if not block:
                    break
                digest.update(block)
        after = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot hash {context} {path}: {exc}") from exc
    _require(_identity(after) == expected_identity, f"{context} changed while hashing: {path}")
    return _FileReceipt(path, before.st_size, digest.hexdigest(), expected_identity)


def _require_unchanged(receipt: _FileReceipt, context: str) -> None:
    try:
        current = receipt.path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot re-inspect {context} {receipt.path}: {exc}") from exc
    _require(_identity(current) == receipt.identity, f"{context} changed during proof assembly")


def _load_json(path: Path, *, context: str, expected_sha256: str | None = None) -> tuple[Any, _FileReceipt]:
    receipt = _hash_regular(path, context=context)
    if expected_sha256 is not None:
        _require(
            receipt.sha256 == _sha256(expected_sha256, f"expected digest for {context}"),
            f"{context} file SHA-256 mismatch",
        )
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot read {context} {path}: {exc}") from exc
    _require_unchanged(receipt, context)
    return _parse_json(encoded, context=context), receipt


def _content_identity(value: Mapping[str, Any], *, field: str, prefix: str, context: str) -> str:
    material = dict(value)
    content_id = material.pop(field, None)
    _require(
        content_id == prefix + canonical_json_sha256(material),
        f"{context} content identity mismatch",
    )
    return str(content_id)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        metadata = path.lstat()
        _require(not stat.S_ISLNK(metadata.st_mode), f"proof output must not be a symlink: {path}")
        _require(stat.S_ISREG(metadata.st_mode), f"proof output is not a regular file: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def load_exact_runtime_proof_spec(source: str | os.PathLike[str]) -> ExactRuntimeProofSpec:
    """Load a closed, self-addressed proof spec without admitting its artifacts."""

    source_path = Path(source)
    _require(source_path.is_absolute(), "proof spec path must be absolute")
    value, receipt = _load_json(source_path, context="proof spec")
    spec = _exact_keys(value, _SPEC_KEYS, "proof spec")
    _require(spec["format"] == PROOF_SPEC_FORMAT, "proof spec format mismatch")
    spec_id = _content_identity(
        spec,
        field="spec_id",
        prefix=PROOF_SPEC_PREFIX,
        context="proof spec",
    )

    raw_artifacts = spec["artifacts"]
    _require(isinstance(raw_artifacts, list), "proof spec artifacts must be an array")
    artifacts: list[ProofArtifact] = []
    roles: set[str] = set()
    paths: set[Path] = set()
    for index, raw in enumerate(raw_artifacts):
        item = _exact_keys(raw, _ARTIFACT_KEYS, f"proof artifact {index}")
        role = _nonempty(item["role"], f"proof artifact {index} role")
        _require(role in EXPECTED_ARTIFACT_ROLES, f"unknown proof artifact role: {role}")
        _require(role not in roles, f"duplicate proof artifact role: {role}")
        roles.add(role)
        path = _absolute(item["path"], f"proof artifact {role} path")
        _require(path not in paths, f"duplicate proof artifact path: {path}")
        paths.add(path)
        artifacts.append(
            ProofArtifact(
                role=role,
                path=path,
                expected_sha256=_sha256(item["sha256"], f"proof artifact {role}"),
            )
        )
    _require(
        roles == set(EXPECTED_ARTIFACT_ROLES),
        "proof artifact roles mismatch: "
        f"missing={sorted(set(EXPECTED_ARTIFACT_ROLES) - roles)!r}",
    )
    _require(
        [artifact.role for artifact in artifacts] == list(EXPECTED_ARTIFACT_ROLES),
        "proof artifacts must use the canonical role order",
    )

    limitations = spec["claim_limitations"]
    _require(
        isinstance(limitations, list)
        and bool(limitations)
        and all(isinstance(item, str) and bool(item) for item in limitations),
        "proof claim limitations must be a nonempty string array",
    )
    _require(
        limitations == sorted(set(limitations)),
        "proof claim limitations must be sorted and unique",
    )
    _require(
        tuple(limitations) == REQUIRED_CLAIM_LIMITATIONS,
        "proof claim limitations do not match the exact-runtime-v2 required set",
    )
    output_value = _exact_keys(spec["output"], _OUTPUT_KEYS, "proof output")
    output = _absolute(output_value["path"], "proof output path")
    _require(output != source_path and output not in paths, "proof output must not replace an input")
    return ExactRuntimeProofSpec(
        spec_id=spec_id,
        spec_file_sha256=receipt.sha256,
        spec_receipt=receipt,
        artifacts=tuple(artifacts),
        claim_limitations=tuple(limitations),
        output=output,
    )


def _admit_session(case_id: str, value: Any) -> dict[str, Any]:
    session = _exact_keys(value, _SESSION_KEYS, f"session audit {case_id}")
    _require(session["schema"] == SESSION_AUDIT_SCHEMA, f"session audit schema mismatch for {case_id}")
    audit_id = _content_identity(
        session,
        field="audit_id",
        prefix=SESSION_AUDIT_PREFIX,
        context=f"session audit {case_id}",
    )
    _require(session["case_id"] == case_id, f"session audit role mismatch for {case_id}")
    _require(session["outcome"] == _OUTCOME_BY_CASE[case_id], f"session outcome mismatch for {case_id}")
    _require(
        type(session["observer_enabled"]) is bool
        and session["observer_enabled"] == _OBSERVER_BY_CASE[case_id],
        f"session observer flag mismatch for {case_id}",
    )
    foundation = _exact_keys(
        session["foundation_class_dump"], _FOUNDATION_KEYS, f"Foundation custody for {case_id}"
    )
    _require(
        foundation["format"] == "workbench-foundation-class-dump-manifest-v1",
        f"Foundation custody format mismatch for {case_id}",
    )
    _integer(foundation["class_count"], f"Foundation class count for {case_id}", minimum=1)
    _integer(foundation["total_size_bytes"], f"Foundation byte count for {case_id}", minimum=1)
    _sha256(foundation["manifest_sha256"], f"Foundation manifest for {case_id}")

    raw = session["raw_capture"]
    result = session["fixture_result"]
    if case_id == "observer-off":
        _require(raw is None, "observer-off session must have no raw capture")
    else:
        raw = _exact_keys(raw, _SESSION_RAW_KEYS, f"session raw capture for {case_id}")
        _sha256(raw["file_sha256"], f"session raw digest for {case_id}")
        _integer(raw["size_bytes"], f"session raw size for {case_id}", minimum=1)
        _integer(raw["row_count"], f"session raw row count for {case_id}", minimum=1)
        _integer(raw["terminal_sequence"], f"session raw terminal sequence for {case_id}")
        _require(
            raw["terminal_sequence"] == raw["row_count"] - 1,
            f"session raw terminal sequence mismatch for {case_id}",
        )
        _nonempty(raw["capture_id"], f"session raw capture ID for {case_id}")
        if case_id == "crash-before-seal":
            _require(
                raw["terminal_state"] == "incomplete_without_stop"
                and raw["stop_control_count"] == 0
                and raw["fixture_completion_marker_count"] == 0,
                "crash session raw terminal state is not incomplete and unsealed",
            )
        else:
            _require(
                raw["terminal_state"] == "complete_and_stopped"
                and raw["stop_control_count"] == 1
                and raw["fixture_completion_marker_count"] == 1,
                f"completed session raw terminal state mismatch for {case_id}",
            )
    if case_id == "crash-before-seal":
        _require(result is None, "crash session must not contain a fixture result")
    else:
        result = _exact_keys(result, _SESSION_RESULT_KEYS, f"session fixture result for {case_id}")
        _sha256(result["file_sha256"], f"session fixture result for {case_id}")
        _sha256(result["canonical_document_sha256"], f"session result document for {case_id}")
        _require(
            result["completion_state"] == "complete"
            and result["save_state"] == "flushed"
            and result["shutdown_state"] == "requested",
            f"session fixture result is not durably complete for {case_id}",
        )
        _require(result["route_order"] == _ROUTE_BY_CASE[case_id], f"session route mismatch for {case_id}")
    return {"audit_id": audit_id, "value": dict(session)}


def _admit_worker(case_id: str, value: Any) -> dict[str, Any]:
    worker = _exact_keys(value, _WORKER_KEYS, f"worker receipt {case_id}")
    _require(worker["schema"] == WORKER_RECEIPT_SCHEMA, f"worker schema mismatch for {case_id}")
    receipt_id = _content_identity(
        worker,
        field="receipt_id",
        prefix=WORKER_RECEIPT_PREFIX,
        context=f"worker receipt {case_id}",
    )
    expected_state = "incomplete" if case_id == "crash-before-seal" else "completed"
    expected_raw_state = "incomplete" if case_id == "crash-before-seal" else "complete"
    _require(worker["publication_state"] == expected_state, f"worker publication state mismatch for {case_id}")
    _require(worker["raw_completion_state"] == expected_raw_state, f"worker raw terminal state mismatch for {case_id}")
    for field in (
        "spec_file_sha256",
        "raw_ndjson_sha256",
        "raw_schema_sha256",
        "probe_plan_file_sha256",
        "candidate_lock_file_sha256",
        "fixture_artifact_file_sha256",
        "installed_mod_set_manifest_file_sha256",
        "installed_mod_set_manifest_canonical_sha256",
        "configuration_set_manifest_file_sha256",
        "configuration_set_manifest_canonical_sha256",
        "class_dump_inventory_sha256",
        "foundation_class_dump_manifest_sha256",
        "actor_inventory_sha256",
        "bundle_canonical_sha256",
        "bundle_file_sha256",
        "projection_canonical_sha256",
        "projection_file_sha256",
    ):
        _sha256(worker[field], f"worker {field} for {case_id}")
    for field in ("raw_row_count", "foundation_class_dump_class_count", "foundation_class_dump_total_size_bytes", "bundle_record_count"):
        _integer(worker[field], f"worker {field} for {case_id}", minimum=1)
    for field in ("spec_id", "probe_plan_id", "capture_nonce", "run_id", "publication_id", "projection_id"):
        _nonempty(worker[field], f"worker {field} for {case_id}")
    if case_id == "crash-before-seal":
        _require(
            worker["fixture_result_file_sha256"] is None
            and worker["fixture_result_canonical_sha256"] is None
            and worker["fixture_route_order"] is None
            and worker["runtime_mod_inventory_sha256"] is None,
            "crash worker receipt unexpectedly binds a completed result",
        )
    else:
        for field in ("fixture_result_file_sha256", "fixture_result_canonical_sha256", "runtime_mod_inventory_sha256"):
            _sha256(worker[field], f"worker {field} for {case_id}")
        _require(worker["fixture_route_order"] == _ROUTE_BY_CASE[case_id], f"worker route mismatch for {case_id}")
    return {"receipt_id": receipt_id, "value": dict(worker)}


def _admit_projection(case_id: str, value: Any) -> CleanroomBundleProjection:
    _require(isinstance(value, Mapping), f"projection {case_id} must be an object")
    projection = parse_cleanroom_bundle_projection(canonical_json_bytes(value))
    _require(
        projection.execution_id == _EXECUTION_BY_CASE[case_id],
        f"projection execution role mismatch for {case_id}",
    )
    summary = projection.summary
    expected_state = "incomplete" if case_id == "crash-before-seal" else "completed"
    _require(projection.publication_state == expected_state, f"projection state mismatch for {case_id}")
    _require(summary["dropped_record_count"] == 0, f"projection records drops for {case_id}")
    _require(
        summary["span_enter_count"]
        == summary["span_return_count"] + summary["span_throw_count"] + len(summary["open_span_ids"]),
        f"projection spans do not balance for {case_id}",
    )
    if case_id == "crash-before-seal":
        _require(
            projection.publication["completion_seal"] is None
            and projection.publication["crash_residue"] is not None
            and summary["coverage_state"] == "incomplete",
            "crash projection is not incomplete and unsealed",
        )
    else:
        _require(
            projection.publication["completion_seal"] is not None
            and projection.publication["crash_residue"] is None
            and summary["coverage_state"] == "complete"
            and summary["open_span_ids"] == [],
            f"normal projection is not complete, balanced, and closed for {case_id}",
        )
    return projection


def _bind_case(
    case_id: str,
    *,
    session: Mapping[str, Any],
    session_file: _FileReceipt,
    worker: Mapping[str, Any] | None,
    worker_file: _FileReceipt | None,
    projection: CleanroomBundleProjection | None,
    projection_file: _FileReceipt | None,
) -> dict[str, Any]:
    session_value = session["value"]
    result = session_value["fixture_result"]
    raw = session_value["raw_capture"]
    row: dict[str, Any] = {
        "case_id": case_id,
        "outcome": session_value["outcome"],
        "observer_enabled": session_value["observer_enabled"],
        "session_audit": {
            "audit_id": session["audit_id"],
            "file_sha256": session_file.sha256,
        },
        "worker_receipt": None,
        "projection": None,
    }
    if worker is None:
        _require(case_id == "observer-off", f"missing worker for {case_id}")
        return row
    _require(worker_file is not None and projection is not None and projection_file is not None, f"incomplete proof inputs for {case_id}")
    worker_value = worker["value"]
    _require(raw is not None, f"observed case lacks raw custody: {case_id}")
    _require(worker_value["raw_ndjson_sha256"] == raw["file_sha256"], f"raw custody mismatch for {case_id}")
    _require(
        worker_value["raw_row_count"] == raw["row_count"]
        and worker_value["capture_nonce"] == raw["capture_id"],
        f"raw admission custody mismatch for {case_id}",
    )
    if result is None:
        _require(worker_value["fixture_result_file_sha256"] is None, f"result custody mismatch for {case_id}")
    else:
        _require(
            worker_value["fixture_result_file_sha256"] == result["file_sha256"]
            and worker_value["fixture_result_canonical_sha256"] == result["canonical_document_sha256"],
            f"fixture-result custody mismatch for {case_id}",
        )
        _require(
            worker_value["runtime_mod_inventory_sha256"]
            == result["runtime_inventory_sha256"],
            f"runtime-inventory custody mismatch for {case_id}",
        )
    _require(
        worker_value["foundation_class_dump_manifest_format"]
        == session_value["foundation_class_dump"]["format"]
        and worker_value["foundation_class_dump_class_count"]
        == session_value["foundation_class_dump"]["class_count"]
        and worker_value["foundation_class_dump_total_size_bytes"]
        == session_value["foundation_class_dump"]["total_size_bytes"]
        and worker_value["foundation_class_dump_manifest_sha256"]
        == session_value["foundation_class_dump"]["manifest_sha256"],
        f"Foundation custody mismatch for {case_id}",
    )
    _require(
        worker_value["candidate_lock_file_sha256"] == session_value["candidate_lock"]["file_sha256"]
        and worker_value["fixture_artifact_file_sha256"] == session_value["fixture_artifact"]["file_sha256"]
        and worker_value["installed_mod_set_manifest_file_sha256"] == session_value["installed_mod_set"]["file_sha256"]
        and worker_value["configuration_set_manifest_file_sha256"] == session_value["configuration_set"]["file_sha256"],
        f"worker/session input custody mismatch for {case_id}",
    )
    _require(worker_value["projection_id"] == projection.projection_id, f"worker/projection identity mismatch for {case_id}")
    _require(
        worker_value["projection_canonical_sha256"] == canonical_json_sha256(projection.as_dict())
        and worker_value["projection_file_sha256"] == projection_file.sha256,
        f"worker/projection digest mismatch for {case_id}",
    )
    _require(
        worker_value["bundle_canonical_sha256"] == projection.source_bundle_sha256
        and worker_value["bundle_record_count"] == projection.summary["record_count"]
        and worker_value["run_id"] == projection.run["run_id"]
        and worker_value["publication_state"] == projection.publication_state
        and worker_value["publication_id"] == projection.publication_id,
        f"worker/projection publication custody mismatch for {case_id}",
    )
    row["worker_receipt"] = {
        "receipt_id": worker["receipt_id"],
        "file_sha256": worker_file.sha256,
        "raw_ndjson_sha256": worker_value["raw_ndjson_sha256"],
        "bundle_canonical_sha256": worker_value["bundle_canonical_sha256"],
        "publication_state": worker_value["publication_state"],
        "publication_id": worker_value["publication_id"],
    }
    row["projection"] = {
        "projection_id": projection.projection_id,
        "file_sha256": projection_file.sha256,
        "canonical_sha256": canonical_json_sha256(projection.as_dict()),
        "source_bundle_sha256": projection.source_bundle_sha256,
        "publication_state": projection.publication_state,
        "publication_id": projection.publication_id,
    }
    return row


def _admit_matrix(value: Any, projections: Mapping[str, CleanroomBundleProjection], sessions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    matrix = _exact_keys(value, _MATRIX_KEYS, "matrix evaluation")
    _require(matrix["schema"] == PROJECTION_MATRIX_EVALUATION_SCHEMA, "matrix schema mismatch")
    evaluation_id = _content_identity(
        matrix,
        field="evaluation_id",
        prefix=PROJECTION_MATRIX_EVALUATION_PREFIX,
        context="matrix evaluation",
    )
    _require(matrix["matrix_state"] == "passed", "matrix evaluation did not pass")
    _require(matrix["matrix_input_mode"] == "validated_bundle_projections", "matrix did not use admitted projections")
    checks = _exact_keys(matrix["global_checks"], _MATRIX_GLOBAL_CHECKS, "matrix global checks")
    _require(all(value is True for value in checks.values()), "matrix global checks did not all pass")
    comparisons = matrix["comparisons"]
    _require(isinstance(comparisons, list) and len(comparisons) == 4, "matrix must contain four comparisons")
    _require(
        {item.get("comparison_id") for item in comparisons if isinstance(item, Mapping)}
        == {"aa-determinism", "observer-neutrality", "route-order-invariance", "restart-stability"}
        and all(item.get("passed") is True for item in comparisons if isinstance(item, Mapping)),
        "matrix comparisons are incomplete or failed",
    )
    by_comparison = {item["comparison_id"]: item for item in comparisons}
    for comparison_id, item in by_comparison.items():
        _require(
            item.get("semantic_equal") is True
            and item.get("differing_chunks") == [],
            f"matrix semantic comparison failed for {comparison_id}",
        )
        if comparison_id == "observer-neutrality":
            _require(
                item.get("checkpoint_equal") is None
                and item.get("rng_equal") is None,
                "observer-neutrality claims unavailable bundle comparisons",
            )
        else:
            _require(
                item.get("checkpoint_equal") is True
                and item.get("rng_equal") is True,
                f"matrix checkpoint or RNG comparison failed for {comparison_id}",
            )
    expected_aliases = {
        "aa-1": "exec-aa1",
        "aa-2": "exec-aa2",
        "observer-off": "exec-observer-off",
        "observer-on": "exec-aa1",
        "order-forward": "exec-aa1",
        "order-reverse": "exec-reverse",
        "restart-1": "exec-aa1",
        "restart-2": "exec-aa2",
        "crash-before-seal": "exec-crash",
    }
    aliases = matrix["case_aliases"]
    _require(
        isinstance(aliases, list)
        and aliases == [
            {"case_id": case_id, "execution_id": execution_id}
            for case_id, execution_id in expected_aliases.items()
        ],
        "matrix case aliases do not match exact-runtime-v2",
    )
    execution_rows = matrix["executions"]
    _require(isinstance(execution_rows, list) and len(execution_rows) == 5, "matrix execution count mismatch")
    by_id = {
        row.get("execution_id"): row
        for row in execution_rows
        if isinstance(row, Mapping) and isinstance(row.get("execution_id"), str)
    }
    _require(set(by_id) == set(_EXECUTION_BY_CASE.values()), "matrix execution identities mismatch")
    for case_id, execution_id in _EXECUTION_BY_CASE.items():
        row = by_id[execution_id]
        session_result = sessions[case_id]["value"]["fixture_result"]
        if case_id == "observer-off":
            _require(
                row.get("bundle_projection_id") is None
                and row.get("source_bundle_sha256") is None
                and row.get("publication_state") == "not_captured",
                "matrix observer-off execution unexpectedly cites a capture",
            )
        else:
            projection = projections[case_id]
            _require(
                row.get("bundle_projection_id") == projection.projection_id
                and row.get("source_bundle_sha256") == projection.source_bundle_sha256
                and row.get("publication_state") == projection.publication_state
                and row.get("publication_id") == projection.publication_id,
                f"matrix projection custody mismatch for {case_id}",
            )
        if session_result is None:
            _require(row.get("fixture_result_sha256") is None, f"matrix result mismatch for {case_id}")
        else:
            _require(
                row.get("fixture_result_sha256") == session_result["file_sha256"],
                f"matrix result custody mismatch for {case_id}",
            )
    return {"evaluation_id": evaluation_id, "value": dict(matrix)}


def _admit_hook_health(value: Any, sessions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    health = _exact_keys(value, _HOOK_HEALTH_KEYS, "hook-health evaluation")
    _require(health["schema"] == HOOK_HEALTH_EVALUATION_SCHEMA, "hook-health schema mismatch")
    evaluation_id = _content_identity(
        health,
        field="evaluation_id",
        prefix=HOOK_HEALTH_EVALUATION_PREFIX,
        context="hook-health evaluation",
    )
    _require(health["status"] == "passed", "hook-health evaluation did not pass")
    contract = health["raw_contract"]
    _require(isinstance(contract, Mapping) and contract.get("declared_hook_count") == 14, "hook-health plan does not declare exactly 14 hooks")
    cases = health["cases"]
    _require(isinstance(cases, list) and len(cases) == 4, "hook-health case count mismatch")
    by_case = {
        row.get("case_id"): row
        for row in cases
        if isinstance(row, Mapping) and isinstance(row.get("case_id"), str)
    }
    _require(set(by_case) == set(OBSERVED_CASES), "hook-health cases mismatch")
    _require(health["required_case_count"] == 3 and health["excluded_incomplete_case_count"] == 1, "hook-health case-role counts mismatch")
    required_hook_sets: list[set[str]] = []
    for case_id in OBSERVED_CASES:
        row = by_case[case_id]
        expected_role = EXCLUDED_INCOMPLETE_CASE_ROLE if case_id == "crash-before-seal" else REQUIRED_CASE_ROLE
        expected_verdict = "excluded_incomplete_case" if case_id == "crash-before-seal" else "all_declared_hooks_reached"
        _require(row.get("case_role") == expected_role and row.get("verdict") == expected_verdict, f"hook-health role or verdict mismatch for {case_id}")
        custody = row.get("session_custody")
        raw = row.get("raw_capture")
        _require(
            isinstance(custody, Mapping)
            and custody.get("audit_id") == sessions[case_id]["audit_id"],
            f"hook-health session custody mismatch for {case_id}",
        )
        _require(
            isinstance(raw, Mapping)
            and raw.get("file_sha256") == sessions[case_id]["value"]["raw_capture"]["file_sha256"],
            f"hook-health raw custody mismatch for {case_id}",
        )
        hook_health = row.get("hook_health")
        _require(isinstance(hook_health, Mapping), f"hook-health details missing for {case_id}")
        if case_id != "crash-before-seal":
            hooks = hook_health.get("hooks")
            hook_ids = [
                hook.get("hook_id")
                for hook in hooks
                if isinstance(hook, Mapping)
            ] if isinstance(hooks, list) else []
            _require(
                hook_health.get("declared_hook_count") == 14
                and hook_health.get("observed_hook_count") == 14
                and hook_health.get("missing_hook_ids") == []
                and hook_health.get("latest_not_reached_hook_ids") == []
                and isinstance(hooks, list)
                and len(hooks) == 14
                and len(hook_ids) == 14
                and all(isinstance(hook_id, str) and bool(hook_id) for hook_id in hook_ids)
                and len(set(hook_ids)) == 14
                and all(isinstance(hook, Mapping) and hook.get("latest_state") == "reached" for hook in hooks),
                f"not all 14 hooks reached in {case_id}",
            )
            required_hook_sets.append(set(hook_ids))
    _require(
        len(required_hook_sets) == 3
        and all(hooks == required_hook_sets[0] for hooks in required_hook_sets[1:]),
        "required hook-health cases do not cover the same 14 hooks",
    )
    return {"evaluation_id": evaluation_id, "value": dict(health)}


def _admit_semantic(value: Any, sessions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    comparison = _exact_keys(value, _SEMANTIC_KEYS, "semantic-bytecode evaluation")
    _require(comparison["format"] == SEMANTIC_BYTECODE_COMPARISON_FORMAT, "semantic-bytecode comparison format mismatch")
    _require(comparison["policy_id"] == SEMANTIC_BYTECODE_POLICY_ID, "semantic-bytecode policy mismatch")
    material = dict(comparison)
    comparison_sha256 = material.pop("comparison_sha256")
    _require(
        comparison_sha256 == canonical_json_sha256(material),
        "semantic-bytecode comparison content identity mismatch",
    )
    _require(
        comparison["semantic_dumps_all_equal"] is True
        and comparison["exact_foundation_manifests_all_distinct"] is True,
        "semantic-bytecode exact-vs-semantic equality gates did not pass",
    )
    rows = comparison["cases"]
    _require(isinstance(rows, list) and comparison["case_count"] == 3 and len(rows) == 3, "semantic-bytecode case count mismatch")
    admitted_rows = [
        _exact_keys(row, _SEMANTIC_CASE_KEYS, f"semantic-bytecode case {index}")
        for index, row in enumerate(rows)
    ]
    by_label = {
        row.get("label"): row
        for row in admitted_rows
        if isinstance(row.get("label"), str)
    }
    _require(set(by_label) == set(NORMAL_CASES), "semantic-bytecode case labels mismatch")
    for case_id in NORMAL_CASES:
        row = by_label[case_id]
        _require(
            row.get("exact_foundation_manifest_sha256")
            == sessions[case_id]["value"]["foundation_class_dump"]["manifest_sha256"],
            f"semantic-bytecode exact custody mismatch for {case_id}",
        )
        _sha256(row.get("manifest_sha256"), f"semantic manifest for {case_id}")
        _sha256(row.get("semantic_dump_sha256"), f"semantic dump for {case_id}")
        _integer(row.get("normalized_class_count"), f"normalized classes for {case_id}", minimum=1)
        _integer(row.get("normalized_utf8_constants"), f"normalized constants for {case_id}", minimum=1)
        _integer(row.get("normalized_annotation_references"), f"normalized references for {case_id}", minimum=1)
        _require(
            row.get("class_count")
            == sessions[case_id]["value"]["foundation_class_dump"]["class_count"]
            and row.get("total_size_bytes")
            == sessions[case_id]["value"]["foundation_class_dump"]["total_size_bytes"],
            f"semantic-bytecode Foundation size custody mismatch for {case_id}",
        )
    _require(
        len({row["exact_foundation_manifest_sha256"] for row in admitted_rows}) == 3
        and len({row["semantic_dump_sha256"] for row in admitted_rows}) == 1,
        "semantic-bytecode case rows contradict their equality gates",
    )
    return {"comparison_sha256": comparison_sha256, "policy_id": comparison["policy_id"], "value": dict(comparison)}


def _writer_mod_id(answer: Mapping[str, Any]) -> Any:
    result = answer.get("result")
    final_writer = result.get("final_writer") if isinstance(result, Mapping) else None
    actor = final_writer.get("initiating_actor") if isinstance(final_writer, Mapping) else None
    return actor.get("mod_id") if isinstance(actor, Mapping) else None


def _changed_handler_mod_ids(answer: Mapping[str, Any]) -> set[Any]:
    result = answer.get("result")
    handlers = result.get("handlers") if isinstance(result, Mapping) else None
    if not isinstance(handlers, list):
        return set()
    result_ids: set[Any] = set()
    for row in handlers:
        actor = row.get("actor") if isinstance(row, Mapping) and row.get("changed") is True else None
        if isinstance(actor, Mapping):
            result_ids.add(actor.get("mod_id"))
    return result_ids


def _admit_atlas(value: Any, projections: Mapping[str, CleanroomBundleProjection]) -> dict[str, Any]:
    report = _exact_keys(value, _ATLAS_KEYS, "Atlas exact query suite")
    _require(report["format"] == ATLAS_SUITE_FORMAT, "Atlas exact query suite format mismatch")
    _require(
        report["query_contract_id"] == "WORKBENCH-ATLAS-WORLDGEN-OBSERVATORY-QUERY-V1",
        "Atlas query contract mismatch",
    )
    authority = _exact_keys(report["authority"], _ATLAS_AUTHORITY_KEYS, "Atlas authority")
    _require(
        authority
        == {
            "evidence_owner": "Crucible",
            "interpretation_owner": "Atlas",
            "sorting_spool": "ephemeral-derived-non-authoritative",
        },
        "Atlas authority boundary mismatch",
    )
    _require(report["passed"] is True, "Atlas exact query suite did not pass")
    gates = _exact_keys(report["gates"], _ATLAS_GATE_KEYS, "Atlas gates")
    _require(all(value is True for value in gates.values()), "Atlas exact query gates did not all pass")
    answers = _exact_keys(report["answers"], _ATLAS_ANSWER_KEYS, "Atlas answers")
    rejections = _exact_keys(report["crash_rejections"], _ATLAS_ANSWER_KEYS, "Atlas crash rejections")
    expected_query_names = {
        "who_wrote_block": "who-wrote-block",
        "which_handler_changed_event": "which-handler-changed-event",
        "first_divergence": "first-divergence",
    }
    for label, answer in (*answers.items(), *rejections.items()):
        admitted = _exact_keys(answer, _ATLAS_QUERY_ANSWER_KEYS, f"Atlas query answer {label}")
        _require(
            admitted["contract_id"] == report["query_contract_id"],
            f"Atlas query answer contract mismatch for {label}",
        )
        _require(
            admitted["query"] == expected_query_names[label],
            f"Atlas query answer role mismatch for {label}",
        )
        _require(
            isinstance(admitted["capture_ids"], list)
            and isinstance(admitted["scope"], Mapping)
            and isinstance(admitted["evidence_ordinals"], list)
            and isinstance(admitted["limitations"], list),
            f"Atlas query answer structure mismatch for {label}",
        )
    _require(answers["who_wrote_block"].get("status") == "answered", "Atlas writer answer is not successful")
    _require(answers["which_handler_changed_event"].get("status") == "answered", "Atlas handler answer is not successful")
    divergence_status = answers["first_divergence"].get("status")
    _require(divergence_status in {"equal", "diverged"}, "Atlas divergence answer is not closed")
    _require(
        all(isinstance(answer, Mapping) and answer.get("status") == "incomplete-evidence" for answer in rejections.values()),
        "Atlas crash rejection answers are not all incomplete-evidence",
    )
    expectations = _exact_keys(report["expectations"], _ATLAS_EXPECTATION_KEYS, "Atlas expectations")
    writer = _nonempty(expectations.get("writer_mod_id"), "Atlas expected writer mod ID")
    handler = _nonempty(expectations.get("handler_mod_id"), "Atlas expected handler mod ID")
    _require(
        writer == EXPECTED_ATLAS_WRITER_MOD_ID
        and handler == EXPECTED_ATLAS_HANDLER_MOD_ID,
        "Atlas exact synthetic writer or handler expectation mismatch",
    )
    _require(
        divergence_status == EXPECTED_ATLAS_DIVERGENCE_STATUS
        and expectations.get("divergence_status")
        == EXPECTED_ATLAS_DIVERGENCE_STATUS,
        "Atlas exact divergence expectation mismatch",
    )
    _require(expectations.get("crash_status") == "incomplete-evidence", "Atlas crash expectation mismatch")
    _require(_writer_mod_id(answers["who_wrote_block"]) == writer, "Atlas writer identity answer mismatch")
    _require(handler in _changed_handler_mod_ids(answers["which_handler_changed_event"]), "Atlas handler identity answer mismatch")
    inputs = _exact_keys(report["inputs"], _ATLAS_INPUT_KEYS, "Atlas inputs")
    _require(
        inputs.get("primary_capture_id") == projections["aa-1"].publication_id
        and inputs.get("comparison_capture_id") == projections["aa-2"].publication_id
        and inputs.get("crash_capture_id")
        == projections["crash-before-seal"].run["run_id"],
        "Atlas capture custody does not match admitted projections",
    )
    _require(
        type(inputs.get("dimension_id")) is int
        and inputs["dimension_id"] == EXPECTED_ATLAS_DIMENSION_ID
        and inputs.get("block_position") == list(EXPECTED_ATLAS_BLOCK_POSITION),
        "Atlas exact block selector mismatch",
    )
    _nonempty(inputs.get("event_span_id"), "Atlas selected event span")
    event_selector = _exact_keys(
        inputs.get("event_selector"),
        _ATLAS_EVENT_SELECTOR_KEYS,
        "Atlas event selector",
    )
    _require(
        event_selector
        == {
            "mode": "occurrence",
            "event_class": EXPECTED_ATLAS_EVENT_CLASS,
            "bus_id": EXPECTED_ATLAS_EVENT_BUS_ID,
            "chunk": {
                "x": EXPECTED_ATLAS_EVENT_CHUNK[0],
                "z": EXPECTED_ATLAS_EVENT_CHUNK[1],
            },
            "occurrence": 0,
        },
        "Atlas exact event selector mismatch",
    )
    primary_id = inputs["primary_capture_id"]
    comparison_id = inputs["comparison_capture_id"]
    crash_id = inputs["crash_capture_id"]
    _require(
        answers["who_wrote_block"]["capture_ids"] == [primary_id]
        and answers["which_handler_changed_event"]["capture_ids"] == [primary_id]
        and answers["first_divergence"]["capture_ids"] == [primary_id, comparison_id],
        "Atlas successful answers do not cite the expected captures",
    )
    _require(
        all(answer["capture_ids"] == [crash_id] for answer in rejections.values()),
        "Atlas crash rejections do not cite the crash capture",
    )
    _sha256(inputs.get("comparison_scope_sha256"), "Atlas comparison scope")
    _require(
        inputs.get("comparison_checkpoint_id")
        == EXPECTED_ATLAS_COMPARISON_CHECKPOINT,
        "Atlas exact comparison checkpoint mismatch",
    )
    chunk = inputs.get("comparison_chunk")
    _require(
        chunk == list(EXPECTED_ATLAS_COMPARISON_CHUNK),
        "Atlas exact comparison chunk mismatch",
    )
    return {"canonical_sha256": canonical_json_sha256(report), "value": dict(report)}


def assemble_exact_runtime_proof(source: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate the closed input graph and atomically publish the final proof."""

    spec = load_exact_runtime_proof_spec(source)
    loaded: dict[str, Any] = {}
    receipts: dict[str, _FileReceipt] = {}
    for artifact in spec.artifacts:
        value, receipt = _load_json(
            artifact.path,
            context=artifact.role,
            expected_sha256=artifact.expected_sha256,
        )
        loaded[artifact.role] = value
        receipts[artifact.role] = receipt

    sessions = {
        case_id: _admit_session(case_id, loaded[f"session:{case_id}"])
        for case_id in SESSION_CASES
    }
    workers = {
        case_id: _admit_worker(case_id, loaded[f"worker:{case_id}"])
        for case_id in OBSERVED_CASES
    }
    projections = {
        case_id: _admit_projection(case_id, loaded[f"projection:{case_id}"])
        for case_id in OBSERVED_CASES
    }
    case_rows = []
    for case_id in SESSION_CASES:
        observed = case_id in OBSERVED_CASES
        case_rows.append(
            _bind_case(
                case_id,
                session=sessions[case_id],
                session_file=receipts[f"session:{case_id}"],
                worker=workers.get(case_id),
                worker_file=receipts.get(f"worker:{case_id}") if observed else None,
                projection=projections.get(case_id),
                projection_file=receipts.get(f"projection:{case_id}") if observed else None,
            )
        )

    matrix = _admit_matrix(loaded["evaluation:matrix"], projections, sessions)
    hook_health = _admit_hook_health(loaded["evaluation:hook-health"], sessions)
    semantic = _admit_semantic(loaded["evaluation:semantic-bytecode"], sessions)
    atlas = _admit_atlas(loaded["evaluation:atlas"], projections)

    for context, receipt in receipts.items():
        _require_unchanged(receipt, context)
    _require_unchanged(spec.spec_receipt, "proof spec")

    material: dict[str, Any] = {
        "schema": PROOF_SCHEMA,
        "proof_state": "passed",
        "authority": {
            "evidence_owner": "Crucible",
            "interpretation_owner": "Atlas",
            "composition": "validation-and-binding-only",
            "creates_new_interpretation": False,
        },
        "spec": {
            "spec_id": spec.spec_id,
            "file_sha256": spec.spec_file_sha256,
        },
        "cases": case_rows,
        "evaluations": {
            "matrix": {
                "evaluation_id": matrix["evaluation_id"],
                "file_sha256": receipts["evaluation:matrix"].sha256,
                "canonical_sha256": canonical_json_sha256(matrix["value"]),
                "state": "passed",
            },
            "hook_health": {
                "evaluation_id": hook_health["evaluation_id"],
                "file_sha256": receipts["evaluation:hook-health"].sha256,
                "canonical_sha256": canonical_json_sha256(hook_health["value"]),
                "state": "passed",
                "declared_hook_count": 14,
            },
            "semantic_bytecode": {
                "comparison_sha256": semantic["comparison_sha256"],
                "policy_id": semantic["policy_id"],
                "file_sha256": receipts["evaluation:semantic-bytecode"].sha256,
                "canonical_sha256": canonical_json_sha256(semantic["value"]),
                "state": "equal_under_declared_comparison_policy",
                "policy_scope": semantic_bytecode_policy_material(),
            },
            "atlas": {
                "embedded_content_id": None,
                "format": ATLAS_SUITE_FORMAT,
                "file_sha256": receipts["evaluation:atlas"].sha256,
                "canonical_sha256": atlas["canonical_sha256"],
                "state": "passed",
            },
        },
        "validated_checks": {
            "five_session_audits_bound": True,
            "normal_workers_completed": True,
            "crash_worker_incomplete": True,
            "normal_projections_zero_drop_balanced_closed": True,
            "crash_projection_incomplete_unsealed": True,
            "matrix_passed": True,
            "all_14_hook_health_passed": True,
            "semantic_bytecode_equal_under_pinned_policy": True,
            "atlas_answers_passed_and_crash_rejected": True,
        },
        "claim_limitations": list(spec.claim_limitations),
    }
    proof = {"proof_id": PROOF_PREFIX + canonical_json_sha256(material), **material}
    _atomic_json(spec.output, proof)
    return proof


def proof_spec_material(
    *,
    artifacts: Sequence[Mapping[str, str]],
    claim_limitations: Sequence[str],
    output: str,
) -> dict[str, Any]:
    """Build the self-addressed JSON value used by a caller-owned proof spec."""

    material: dict[str, Any] = {
        "format": PROOF_SPEC_FORMAT,
        "artifacts": [dict(item) for item in artifacts],
        "claim_limitations": list(claim_limitations),
        "output": {"path": output},
    }
    return {"spec_id": PROOF_SPEC_PREFIX + canonical_json_sha256(material), **material}


__all__ = [
    "ATLAS_SUITE_FORMAT",
    "EXPECTED_ARTIFACT_ROLES",
    "REQUIRED_CLAIM_LIMITATIONS",
    "ExactRuntimeProofSpec",
    "PROOF_PREFIX",
    "PROOF_SCHEMA",
    "PROOF_SPEC_FORMAT",
    "PROOF_SPEC_PREFIX",
    "ProofArtifact",
    "assemble_exact_runtime_proof",
    "load_exact_runtime_proof_spec",
    "proof_spec_material",
]
