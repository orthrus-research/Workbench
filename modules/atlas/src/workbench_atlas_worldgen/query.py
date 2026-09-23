"""Initial Atlas interpretations over sealed Worldgen Observatory evidence."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from workbench_crucible_observatory import (
    CaptureValidationError,
    canonical_json_bytes,
    load_bundle,
    validate_bundle,
)


QUERY_CONTRACT_ID = "WORKBENCH-ATLAS-WORLDGEN-OBSERVATORY-QUERY-V1"
_CAPTURE_ID_RE = re.compile(
    r"^crucible-worldgen-capture:sha256:[0-9a-f]{64}$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ANSWER_KEYS = {
    "contract_id", "query", "status", "capture_ids", "scope",
    "evidence_ordinals", "limitations", "result",
}
_ACTOR_KEYS = {
    "binding", "mod_id", "code_source_sha256", "class_name", "method_name",
    "method_descriptor", "mapping_namespace", "transformed_class_sha256",
    "candidate_mod_ids",
}

_WRITE_PROBES = {
    (
        "worldgen-hook:chunk-primer-set-block-state",
        "net.minecraft.world.chunk.ChunkPrimer",
        "setBlockState",
        "(IIILnet/minecraft/block/state/IBlockState;)V",
    ),
    (
        "worldgen-hook:world-set-block-state",
        "net.minecraft.world.World",
        "setBlockState",
        "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;I)Z",
    ),
    (
        "worldgen-hook:chunk-set-block-state",
        "net.minecraft.world.chunk.Chunk",
        "setBlockState",
        "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)Lnet/minecraft/block/state/IBlockState;",
    ),
}
_EVENT_PROBES = {
    (
        "worldgen-hook:event-bus-post",
        "net.minecraftforge.fml.common.eventhandler.EventBus",
        "post",
        "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
    ),
    (
        "worldgen-hook:event-listener-invoke",
        "net.minecraftforge.fml.common.eventhandler.EventBus",
        "post",
        "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
    ),
}


class AdmittedWorldgenBundle:
    """One Crucible bundle whose integrity has already been validated.

    The wrapper is intentionally small and does not copy or reinterpret the
    evidence.  It exists so a bounded Atlas query session can load and
    validate a large canonical bundle once instead of repeating the complete
    Crucible admission pass for every interpretation.
    """

    __slots__ = ("_bundle",)
    _TOKEN = object()

    def __init__(self, bundle: dict[str, Any], *, _token: object) -> None:
        if _token is not self._TOKEN:
            raise TypeError("use admit_worldgen_bundle or load_admitted_worldgen_bundle")
        self._bundle = bundle

    @classmethod
    def _validated(cls, bundle: dict[str, Any]) -> "AdmittedWorldgenBundle":
        return cls(bundle, _token=cls._TOKEN)


def admit_worldgen_bundle(bundle: Mapping[str, Any]) -> AdmittedWorldgenBundle:
    """Validate one in-memory Crucible bundle for repeated Atlas queries."""

    validate_bundle(bundle)
    if not isinstance(bundle, dict):
        # validate_bundle accepts Mapping, while query capture ownership needs
        # one stable object whose identity cannot change beneath the wrapper.
        bundle = deepcopy(dict(bundle))
        validate_bundle(bundle)
    return AdmittedWorldgenBundle._validated(bundle)


def load_admitted_worldgen_bundle(path: Path | str) -> AdmittedWorldgenBundle:
    """Strict-load and validate one canonical bundle exactly once."""

    return AdmittedWorldgenBundle._validated(load_bundle(path))


def _query_bundle(
    value: Mapping[str, Any] | AdmittedWorldgenBundle,
) -> tuple[dict[str, Any] | Mapping[str, Any], bool]:
    if isinstance(value, AdmittedWorldgenBundle):
        return value._bundle, True
    return value, False


def _capture_identity(bundle: Mapping[str, Any]) -> str:
    publication = bundle.get("publication", {})
    seal = publication.get("completion_seal")
    if (
        isinstance(seal, Mapping)
        and isinstance(seal.get("capture_id"), str)
        and _CAPTURE_ID_RE.fullmatch(seal["capture_id"])
    ):
        return seal["capture_id"]
    run = bundle.get("run", {})
    return str(run.get("run_id", "crucible-worldgen-run:sha256:" + "0" * 64))


def _invalid_identity(bundle: Mapping[str, Any]) -> str:
    try:
        digest = hashlib.sha256(canonical_json_bytes(bundle)).hexdigest()
    except (CaptureValidationError, TypeError, ValueError):
        digest = hashlib.sha256(repr(bundle).encode("utf-8")).hexdigest()
    return "crucible-worldgen-invalid:sha256:" + digest


def _answer(
    *,
    query: str,
    status: str,
    bundles: Iterable[Mapping[str, Any]],
    scope: Mapping[str, Any],
    evidence_ordinals: Iterable[int] = (),
    limitations: Iterable[str] = (),
    result: Mapping[str, Any] | None = None,
    capture_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    return {
        "contract_id": QUERY_CONTRACT_ID,
        "query": query,
        "status": status,
        "capture_ids": (
            list(capture_ids)
            if capture_ids is not None
            else [_capture_identity(bundle) for bundle in bundles]
        ),
        "scope": deepcopy(dict(scope)),
        "evidence_ordinals": sorted(set(evidence_ordinals)),
        "limitations": sorted(set(limitations)),
        "result": None if result is None else deepcopy(dict(result)),
    }


def _closed_answer_mapping(
    value: Any,
    keys: set[str],
    context: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CaptureValidationError(f"{context} must be an object")
    actual = set(value)
    if actual != keys:
        raise CaptureValidationError(
            f"{context} fields mismatch: missing={sorted(keys - actual)!r}, "
            f"unknown={sorted(actual - keys)!r}"
        )
    return value


def _answer_text(value: Any, context: str) -> str:
    if not (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 8192
        and not any(character in value for character in "\r\n\x00")
    ):
        raise CaptureValidationError(
            f"{context} must be bounded nonempty single-line text"
        )
    return value


def _answer_ordinal(value: Any, context: str) -> int:
    if type(value) is not int or value < 0:
        raise CaptureValidationError(f"{context} must be a nonnegative integer")
    return value


def _validate_answer_actor(value: Any, context: str) -> Mapping[str, Any]:
    actor = _closed_answer_mapping(value, _ACTOR_KEYS, context)
    binding = actor["binding"]
    if binding not in {"exact", "ambiguous", "unbound", "workbench"}:
        raise CaptureValidationError(f"{context} binding is invalid")
    candidates = actor["candidate_mod_ids"]
    if not (
        isinstance(candidates, list)
        and all(
            isinstance(item, str)
            and bool(item)
            and len(item) <= 8192
            and not any(character in item for character in "\r\n\x00")
            for item in candidates
        )
        and candidates == sorted(set(candidates))
    ):
        raise CaptureValidationError(f"{context} candidate mod IDs are invalid")
    exact_fields = (
        "mod_id", "code_source_sha256", "class_name", "method_name",
        "method_descriptor", "mapping_namespace", "transformed_class_sha256",
    )
    if binding in {"exact", "workbench"}:
        for field in exact_fields:
            _answer_text(actor[field], f"{context}.{field}")
        for field in ("code_source_sha256", "transformed_class_sha256"):
            if _SHA256_RE.fullmatch(actor[field]) is None:
                raise CaptureValidationError(f"{context}.{field} is not SHA-256")
        if candidates:
            raise CaptureValidationError(f"{context} exact actor has candidates")
    else:
        if any(actor[field] is not None for field in exact_fields):
            raise CaptureValidationError(f"{context} unresolved actor claims exact identity")
        if binding == "unbound" and candidates:
            raise CaptureValidationError(f"{context} unbound actor has candidates")
        if binding == "ambiguous" and not candidates:
            raise CaptureValidationError(f"{context} ambiguous actor lacks candidates")
    return actor


def _validate_answer_causal_path(value: Any, context: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise CaptureValidationError(f"{context} must be an array")
    result: list[Mapping[str, Any]] = []
    prior_ordinal = -1
    span_ids: set[str] = set()
    for index, raw in enumerate(value):
        step_context = f"{context}[{index}]"
        step = _closed_answer_mapping(
            raw,
            {"span_id", "span_kind", "operation_id", "enter_ordinal", "actor"},
            step_context,
        )
        span_id = _answer_text(step["span_id"], f"{step_context}.span_id")
        if span_id in span_ids:
            raise CaptureValidationError(f"{context} repeats span {span_id!r}")
        span_ids.add(span_id)
        _answer_text(step["span_kind"], f"{step_context}.span_kind")
        _answer_text(step["operation_id"], f"{step_context}.operation_id")
        ordinal = _answer_ordinal(step["enter_ordinal"], f"{step_context}.enter_ordinal")
        if ordinal <= prior_ordinal:
            raise CaptureValidationError(f"{context} is not in causal enter order")
        prior_ordinal = ordinal
        _validate_answer_actor(step["actor"], f"{step_context}.actor")
        result.append(step)
    return result


def validate_who_wrote_block_answer(value: Any) -> dict[str, Any]:
    """Validate one evidence-closed ``who-wrote-block`` answer projection."""

    answer = _closed_answer_mapping(value, _ANSWER_KEYS, "Atlas block-write answer")
    if answer["contract_id"] != QUERY_CONTRACT_ID:
        raise CaptureValidationError("Atlas block-write answer contract mismatch")
    if answer["query"] != "who-wrote-block" or answer["status"] != "answered":
        raise CaptureValidationError("Atlas block-write answer is not answered")
    capture_ids = answer["capture_ids"]
    if not (
        isinstance(capture_ids, list)
        and len(capture_ids) == 1
        and isinstance(capture_ids[0], str)
        and _CAPTURE_ID_RE.fullmatch(capture_ids[0]) is not None
    ):
        raise CaptureValidationError("Atlas block-write answer capture identity is invalid")
    scope = _closed_answer_mapping(
        answer["scope"], {"dimension_id", "position"}, "Atlas block-write scope"
    )
    if type(scope["dimension_id"]) is not int:
        raise CaptureValidationError("Atlas block-write dimension must be an integer")
    if not (
        isinstance(scope["position"], list)
        and len(scope["position"]) == 3
        and all(type(item) is int for item in scope["position"])
    ):
        raise CaptureValidationError("Atlas block-write position is invalid")
    evidence = answer["evidence_ordinals"]
    if not (
        isinstance(evidence, list)
        and all(type(item) is int and item >= 0 for item in evidence)
        and evidence == sorted(set(evidence))
    ):
        raise CaptureValidationError("Atlas block-write evidence ordinals are not canonical")
    evidence_set = set(evidence)
    limitations = answer["limitations"]
    if not (
        isinstance(limitations, list)
        and all(isinstance(item, str) and bool(item) for item in limitations)
        and limitations == sorted(set(limitations))
    ):
        raise CaptureValidationError("Atlas block-write limitations are not canonical")
    if limitations:
        raise CaptureValidationError("answered Atlas block-write result has limitations")

    result = _closed_answer_mapping(
        answer["result"], {"write_chains", "final_writer"}, "Atlas block-write result"
    )
    chains = result["write_chains"]
    if not isinstance(chains, list) or not chains:
        raise CaptureValidationError("Atlas block-write answer lacks write chains")
    admitted_chains: dict[str, dict[str, Any]] = {}
    prior_first_ordinal = -1
    successful_terminals: list[tuple[int, str, Mapping[str, Any]]] = []
    derived_evidence: set[int] = set()
    channel_rank = {"world_api": 0, "chunk_primer": 1, "direct_storage": 2, "chunk_storage": 3}
    for chain_index, raw_chain in enumerate(chains):
        context = f"Atlas block-write chain {chain_index}"
        chain = _closed_answer_mapping(
            raw_chain,
            {
                "write_chain_id", "generation_chunk", "target_chunk", "channels",
                "initiating_ordinal", "initiating_actor", "terminal_ordinal",
                "terminal_actor",
            },
            context,
        )
        chain_id = _answer_text(chain["write_chain_id"], f"{context}.write_chain_id")
        if chain_id in admitted_chains:
            raise CaptureValidationError(f"Atlas block-write answer repeats chain {chain_id!r}")
        for field in ("generation_chunk", "target_chunk"):
            coordinates = _closed_answer_mapping(
                chain[field], {"x", "z"}, f"{context}.{field}"
            )
            if not all(type(coordinates[axis]) is int for axis in ("x", "z")):
                raise CaptureValidationError(f"{context}.{field} is invalid")
        if chain["target_chunk"] != {
            "x": scope["position"][0] >> 4,
            "z": scope["position"][2] >> 4,
        }:
            raise CaptureValidationError(f"{context} target chunk disagrees with query scope")
        channels = chain["channels"]
        if not isinstance(channels, list) or not channels:
            raise CaptureValidationError(f"{context} lacks channel observations")
        if not all(isinstance(row, Mapping) for row in channels):
            raise CaptureValidationError(f"{context} contains a non-object channel")
        ordinals: list[int] = []
        terminals: list[Mapping[str, Any]] = []
        initiating_candidates: list[Mapping[str, Any]] = []
        initiating_rank = min(channel_rank.get(row.get("channel"), 99) for row in channels)
        if initiating_rank == 99:
            raise CaptureValidationError(f"{context} has no admitted channel")
        for channel_index, raw_channel in enumerate(channels):
            channel_context = f"{context}.channels[{channel_index}]"
            channel = _closed_answer_mapping(
                raw_channel,
                {
                    "ordinal", "channel", "before_state_sha256", "after_state_sha256",
                    "flags", "terminal", "outcome", "actor", "causal_path",
                },
                channel_context,
            )
            ordinal = _answer_ordinal(channel["ordinal"], f"{channel_context}.ordinal")
            ordinals.append(ordinal)
            derived_evidence.add(ordinal)
            if ordinal not in evidence_set:
                raise CaptureValidationError(f"{channel_context} ordinal is absent from evidence")
            if channel["channel"] not in channel_rank:
                raise CaptureValidationError(f"{channel_context} channel is invalid")
            for field in ("before_state_sha256", "after_state_sha256"):
                if not isinstance(channel[field], str) or _SHA256_RE.fullmatch(channel[field]) is None:
                    raise CaptureValidationError(f"{channel_context}.{field} is invalid")
            if channel["flags"] is not None and type(channel["flags"]) is not int:
                raise CaptureValidationError(f"{channel_context}.flags is invalid")
            if type(channel["terminal"]) is not bool:
                raise CaptureValidationError(f"{channel_context}.terminal is invalid")
            if channel["outcome"] not in {
                "entered", "returned", "threw", "canceled", "observed", "incomplete",
            }:
                raise CaptureValidationError(f"{channel_context}.outcome is invalid")
            _validate_answer_actor(channel["actor"], f"{channel_context}.actor")
            path = _validate_answer_causal_path(channel["causal_path"], f"{channel_context}.causal_path")
            if any(step["enter_ordinal"] not in evidence_set for step in path):
                raise CaptureValidationError(f"{channel_context} causal path is absent from evidence")
            derived_evidence.update(step["enter_ordinal"] for step in path)
            if channel["terminal"]:
                terminals.append(channel)
            if channel_rank[channel["channel"]] == initiating_rank:
                initiating_candidates.append(channel)
        if ordinals != sorted(set(ordinals)):
            raise CaptureValidationError(f"{context} channels are not in unique observed order")
        if ordinals[0] <= prior_first_ordinal:
            raise CaptureValidationError("Atlas block-write chains are not in observed order")
        prior_first_ordinal = ordinals[0]
        if len(terminals) != 1:
            raise CaptureValidationError(f"{context} must contain exactly one terminal")
        terminal = terminals[0]
        initiating = max(initiating_candidates, key=lambda row: row["ordinal"])
        initiating_ordinal = _answer_ordinal(chain["initiating_ordinal"], f"{context}.initiating_ordinal")
        terminal_ordinal = _answer_ordinal(chain["terminal_ordinal"], f"{context}.terminal_ordinal")
        _validate_answer_actor(chain["initiating_actor"], f"{context}.initiating_actor")
        _validate_answer_actor(chain["terminal_actor"], f"{context}.terminal_actor")
        if (
            initiating_ordinal != initiating["ordinal"]
            or chain["initiating_actor"] != initiating["actor"]
            or terminal_ordinal != terminal["ordinal"]
            or chain["terminal_actor"] != terminal["actor"]
        ):
            raise CaptureValidationError(f"{context} role projections are stale")
        if terminal["outcome"] in {"returned", "observed"} and terminal["before_state_sha256"] != terminal["after_state_sha256"]:
            successful_terminals.append((terminal_ordinal, chain_id, initiating))
        admitted_chains[chain_id] = dict(chain)

    if evidence_set != derived_evidence:
        raise CaptureValidationError("Atlas block-write evidence ordinals are not exactly derived")

    if not successful_terminals:
        raise CaptureValidationError("answered Atlas block-write result has no successful terminal mutation")
    final = _closed_answer_mapping(
        result["final_writer"],
        {
            "write_chain_id", "ordinal", "actor", "initiating_ordinal",
            "initiating_actor", "terminal_ordinal", "terminal_actor",
            "after_state_sha256", "causal_path",
        },
        "Atlas final writer",
    )
    final_chain_id = _answer_text(final["write_chain_id"], "Atlas final writer chain ID")
    expected_terminal_ordinal, expected_chain_id, expected_initiating = max(
        successful_terminals,
        key=lambda item: item[0],
    )
    if final_chain_id != expected_chain_id:
        raise CaptureValidationError("Atlas final writer is not the latest successful terminal")
    final_chain = admitted_chains[final_chain_id]
    if (
        final["ordinal"] != expected_initiating["ordinal"]
        or final["initiating_ordinal"] != expected_initiating["ordinal"]
        or final["actor"] != expected_initiating["actor"]
        or final["initiating_actor"] != expected_initiating["actor"]
        or final["terminal_ordinal"] != expected_terminal_ordinal
        or final["terminal_actor"] != final_chain["terminal_actor"]
    ):
        raise CaptureValidationError("Atlas final-writer role projection is stale")
    _validate_answer_actor(final["actor"], "Atlas final writer actor")
    _validate_answer_actor(final["initiating_actor"], "Atlas final initiating actor")
    _validate_answer_actor(final["terminal_actor"], "Atlas final terminal actor")
    if not isinstance(final["after_state_sha256"], str) or _SHA256_RE.fullmatch(final["after_state_sha256"]) is None:
        raise CaptureValidationError("Atlas final writer state digest is invalid")
    final_path = _validate_answer_causal_path(final["causal_path"], "Atlas final writer causal path")
    if any(step["enter_ordinal"] not in evidence_set for step in final_path):
        raise CaptureValidationError("Atlas final writer lacks an evidence-bound causal path")
    terminal_channel = next(
        channel for channel in final_chain["channels"] if channel["terminal"]
    )
    if (
        final["after_state_sha256"] != terminal_channel["after_state_sha256"]
        or final["causal_path"] != terminal_channel["causal_path"]
    ):
        raise CaptureValidationError("Atlas final-writer terminal projection is stale")
    return deepcopy(dict(answer))


def _admission_failure(
    bundle: Mapping[str, Any],
    query: str,
    scope: Mapping[str, Any],
    *,
    already_validated: bool = False,
) -> dict[str, Any] | None:
    if not already_validated:
        try:
            validate_bundle(bundle)
        except (CaptureValidationError, KeyError, TypeError, ValueError) as exc:
            return _answer(
                query=query,
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                limitations=[str(exc)],
                capture_ids=[_invalid_identity(bundle)],
            )
    if bundle["publication"]["state"] != "completed":
        residue = bundle["publication"].get("crash_residue") or {}
        reason = residue.get("reason", "unknown")
        return _answer(
            query=query,
            status="incomplete-evidence",
            bundles=[bundle],
            scope=scope,
            limitations=[f"capture publication is incomplete: {reason}"],
        )
    summary = bundle["summary"]
    if (
        bundle["run"]["capture_mode"] != "lossless-fixture"
        or summary["coverage_state"] != "complete"
        or summary["dropped_record_count"] != 0
        or summary["limitations"]
    ):
        limitations = list(summary["limitations"])
        limitations.append(
            "query requires a complete zero-drop lossless fixture capture"
        )
        return _answer(
            query=query,
            status="unavailable",
            bundles=[bundle],
            scope=scope,
            limitations=limitations,
        )
    return None


def _reached_probes(
    bundle: Mapping[str, Any], required: set[tuple[str, str, str, str]]
) -> tuple[bool, list[str]]:
    latest: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for record in bundle["records"]:
        if record["record_type"] != "probe_health":
            continue
        payload = record["payload"]
        latest[
            (
                payload["hook_id"],
                payload["target_class"],
                payload["target_method"],
                payload["target_descriptor"],
            )
        ] = payload
    missing: list[str] = []
    for target in sorted(required):
        payload = latest.get(target)
        if (
            payload is None
            or payload["hook_id"] != target[0]
            or payload["health_state"] != "reached"
            or payload["expected_injection_count"] != 1
            or payload["observed_injection_count"]
            != payload["expected_injection_count"]
            or payload["original_class_sha256"] == "0" * 64
            or payload["transformed_class_sha256"] == "0" * 64
            or payload["original_class_sha256"]
            == payload["transformed_class_sha256"]
        ):
            missing.append(f"{target[0]}={target[1]}#{target[2]}{target[3]}")
    return not missing, missing


def _span_path(
    span_id: str | None, spans: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = []
    visited: set[str] = set()
    current = span_id
    while current is not None and current not in visited:
        visited.add(current)
        record = spans.get(current)
        if record is None:
            break
        path.append(
            {
                "span_id": current,
                "span_kind": record["payload"]["span_kind"],
                "operation_id": record["payload"]["operation_id"],
                "enter_ordinal": record["ordinal"],
                "actor": deepcopy(record["actor"]),
            }
        )
        current = record["causality"]["parent_span_id"]
    path.reverse()
    return path


def who_wrote_block(
    bundle: Mapping[str, Any] | AdmittedWorldgenBundle,
    *,
    dimension_id: int,
    x: int,
    y: int,
    z: int,
) -> dict[str, Any]:
    """Return the observed logical write history for one exact block."""

    bundle, already_validated = _query_bundle(bundle)
    scope = {"dimension_id": dimension_id, "position": [x, y, z]}
    failure = _admission_failure(
        bundle,
        "who-wrote-block",
        scope,
        already_validated=already_validated,
    )
    if failure is not None:
        return failure
    probes_closed, missing_probes = _reached_probes(bundle, _WRITE_PROBES)
    if not probes_closed:
        return _answer(
            query="who-wrote-block",
            status="unavailable",
            bundles=[bundle],
            scope=scope,
            limitations=[
                "required write probe is not exactly reached: " + target
                for target in missing_probes
            ],
        )

    records = bundle["records"]
    matching: list[dict[str, Any]] = []
    matching_paths: dict[int, list[dict[str, Any]]] = {}
    active_spans: dict[str, dict[str, Any]] = {}
    for record in records:
        record_type = record["record_type"]
        span_id = record["causality"]["span_id"]
        if record_type == "span_enter" and span_id is not None:
            active_spans[span_id] = record
        elif (
            record_type == "block_write"
            and record["scope"]["dimension_id"] == dimension_id
            and record["payload"]["position"] == [x, y, z]
        ):
            matching.append(record)
            matching_paths[record["ordinal"]] = _span_path(
                span_id,
                active_spans,
            )
        if record_type in {"span_return", "span_throw"} and span_id is not None:
            active_spans.pop(span_id, None)
    if not matching:
        return _answer(
            query="who-wrote-block",
            status="unavailable",
            bundles=[bundle],
            scope=scope,
            limitations=[
                "capture_plan_sha256 does not provide a machine-readable selector "
                "closure from which write absence can be inferred"
            ],
        )

    by_chain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in matching:
        by_chain[record["payload"]["write_chain_id"]].append(record)

    chains: list[dict[str, Any]] = []
    final_writer: dict[str, Any] | None = None
    ambiguous = False
    evidence: list[int] = []
    successful_terminals: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for chain_id, chain_records in sorted(
        by_chain.items(), key=lambda item: min(row["ordinal"] for row in item[1])
    ):
        chain_records.sort(key=lambda row: row["ordinal"])
        terminals = [row for row in chain_records if row["payload"]["terminal"]]
        if len(terminals) != 1:
            return _answer(
                query="who-wrote-block",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[row["ordinal"] for row in chain_records],
                limitations=[
                    f"write chain {chain_id} has {len(terminals)} terminal observations"
                ],
            )
        terminal = terminals[0]
        chain_shape = {
            (
                tuple(row["payload"]["position"]),
                tuple(sorted(row["payload"]["generation_chunk"].items())),
                tuple(sorted(row["payload"]["target_chunk"].items())),
            )
            for row in chain_records
        }
        if len(chain_shape) != 1:
            return _answer(
                query="who-wrote-block",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[row["ordinal"] for row in chain_records],
                limitations=[
                    f"write chain {chain_id} crosses positions or chunk scopes"
                ],
            )
        channel_rank = {
            "world_api": 0,
            "chunk_primer": 1,
            "direct_storage": 2,
            "chunk_storage": 3,
        }
        initiating_rank = min(
            channel_rank[row["payload"]["channel"]] for row in chain_records
        )
        initiating_candidates = [
            row
            for row in chain_records
            if channel_rank[row["payload"]["channel"]] == initiating_rank
        ]
        initiating_record = max(
            initiating_candidates,
            key=lambda row: row["ordinal"],
        )
        channels = []
        for record in chain_records:
            evidence.append(record["ordinal"])
            evidence.extend(
                step["enter_ordinal"]
                for step in matching_paths[record["ordinal"]]
            )
            channels.append(
                {
                    "ordinal": record["ordinal"],
                    "channel": record["payload"]["channel"],
                    "before_state_sha256": record["payload"]["before_state_sha256"],
                    "after_state_sha256": record["payload"]["after_state_sha256"],
                    "flags": record["payload"]["flags"],
                    "terminal": record["payload"]["terminal"],
                    "outcome": record["outcome"]["state"],
                    "actor": deepcopy(record["actor"]),
                    "causal_path": deepcopy(matching_paths[record["ordinal"]]),
                }
            )
        chain = {
            "write_chain_id": chain_id,
            "generation_chunk": deepcopy(chain_records[0]["payload"]["generation_chunk"]),
            "target_chunk": deepcopy(chain_records[0]["payload"]["target_chunk"]),
            "channels": channels,
            "initiating_ordinal": initiating_record["ordinal"],
            "initiating_actor": deepcopy(initiating_record["actor"]),
            "terminal_ordinal": terminal["ordinal"],
            "terminal_actor": deepcopy(terminal["actor"]),
        }
        chains.append(chain)
        if terminal["outcome"]["state"] in {
            "returned",
            "observed",
        } and terminal["payload"]["before_state_sha256"] != terminal["payload"]["after_state_sha256"]:
            if (
                initiating_record["actor"]["binding"] != "exact"
                or terminal["actor"]["binding"] != "exact"
            ):
                ambiguous = True
            successful_terminals.append((terminal, initiating_record))

    if successful_terminals:
        terminal, initiating_record = max(
            successful_terminals,
            key=lambda pair: pair[0]["ordinal"],
        )
        final_writer = {
            "write_chain_id": terminal["payload"]["write_chain_id"],
            "ordinal": initiating_record["ordinal"],
            "actor": deepcopy(initiating_record["actor"]),
            "initiating_ordinal": initiating_record["ordinal"],
            "initiating_actor": deepcopy(initiating_record["actor"]),
            "terminal_ordinal": terminal["ordinal"],
            "terminal_actor": deepcopy(terminal["actor"]),
            "after_state_sha256": terminal["payload"]["after_state_sha256"],
            "causal_path": deepcopy(matching_paths[terminal["ordinal"]]),
        }

    answer = _answer(
        query="who-wrote-block",
        status="ambiguous" if ambiguous else "answered",
        bundles=[bundle],
        scope=scope,
        evidence_ordinals=evidence,
        limitations=(
            ["at least one initiating or terminal writer lacks exact actor binding"]
            if ambiguous
            else []
        ),
        result={"write_chains": chains, "final_writer": final_writer},
    )
    return (
        validate_who_wrote_block_answer(answer)
        if answer["status"] == "answered"
        else answer
    )


def _event_scope_records(
    records: Iterable[dict[str, Any]],
    event_span_id: str,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Collect one post and its listeners without indexing every run span."""

    active_spans: dict[str, dict[str, Any]] = {}
    active_posts: set[str] = set()
    requested_span: dict[str, Any] | None = None
    scoped: list[dict[str, Any]] = []
    directly_bound: list[dict[str, Any]] = []
    listener_spans: dict[str, dict[str, Any]] = {}
    listener_terminals: dict[str, dict[str, Any]] = {}

    def owning_post(span_id: str | None) -> str | None:
        visited: set[str] = set()
        current = span_id
        while current is not None and current not in visited:
            if current in active_posts:
                return current
            visited.add(current)
            entry = active_spans.get(current)
            current = (
                None
                if entry is None
                else entry["causality"]["parent_span_id"]
            )
        return None

    for record in records:
        record_type = record["record_type"]
        span_id = record["causality"]["span_id"]
        if record_type == "span_enter" and span_id is not None:
            active_spans[span_id] = record
            if span_id == event_span_id:
                requested_span = record

        if record_type == "event_dispatch":
            boundary = record["payload"]["boundary"]
            if boundary == "post_enter" and span_id is not None:
                active_posts.add(span_id)
            if span_id == event_span_id:
                directly_bound.append(record)
            if owning_post(span_id) == event_span_id:
                scoped.append(record)
                if boundary.startswith("listener_") and span_id is not None:
                    entry = active_spans.get(span_id)
                    if entry is not None:
                        listener_spans[span_id] = entry

        if record_type in {"span_return", "span_throw"} and span_id is not None:
            if span_id == event_span_id or span_id in listener_spans:
                listener_terminals[span_id] = record
            active_posts.discard(span_id)
            active_spans.pop(span_id, None)

    return (
        requested_span,
        scoped,
        directly_bound,
        listener_spans,
        listener_terminals,
    )


def which_handler_changed_event(
    bundle: Mapping[str, Any] | AdmittedWorldgenBundle,
    *,
    event_span_id: str,
) -> dict[str, Any]:
    """Return per-listener state transitions for one exact event post."""

    bundle, already_validated = _query_bundle(bundle)
    scope = {"event_span_id": event_span_id}
    failure = _admission_failure(
        bundle,
        "which-handler-changed-event",
        scope,
        already_validated=already_validated,
    )
    if failure is not None:
        return failure
    probes_closed, missing_probes = _reached_probes(bundle, _EVENT_PROBES)
    if not probes_closed:
        return _answer(
            query="which-handler-changed-event",
            status="unavailable",
            bundles=[bundle],
            scope=scope,
            limitations=[
                "required dispatch probe is not exactly reached: " + target
                for target in missing_probes
            ],
        )

    all_records = bundle["records"]
    (
        requested_span,
        records,
        directly_bound,
        spans,
        span_terminals,
    ) = _event_scope_records(all_records, event_span_id)
    if requested_span is None:
        return _answer(
            query="which-handler-changed-event",
            status="unavailable",
            bundles=[bundle],
            scope=scope,
            limitations=["the requested event span was not observed"],
        )

    if not any(
        record["payload"]["boundary"] == "post_enter"
        and record["causality"]["span_id"] == event_span_id
        for record in directly_bound
    ):
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence" if directly_bound else "unavailable",
            bundles=[bundle],
            scope=scope,
            evidence_ordinals=[record["ordinal"] for record in directly_bound],
            limitations=["the requested span has no post-enter boundary"],
        )

    records.sort(key=lambda row: row["ordinal"])
    if not records:
        return _answer(
            query="which-handler-changed-event",
            status="unavailable",
            bundles=[bundle],
            scope=scope,
            limitations=["the event span has no dispatch-boundary evidence"],
        )

    event_identities = {
        (record["payload"]["event_class"], record["payload"]["bus_id"])
        for record in records
    }
    if len(event_identities) != 1:
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            evidence_ordinals=[record["ordinal"] for record in records],
            limitations=["one event span contains multiple event or bus identities"],
        )

    post_enters = [
        record for record in records
        if record["payload"]["boundary"] == "post_enter"
    ]
    post_returns = [
        record for record in records
        if record["payload"]["boundary"] == "post_return"
    ]
    if len(post_enters) != 1 or len(post_returns) != 1:
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            evidence_ordinals=[record["ordinal"] for record in records],
            limitations=["dispatch does not have exactly one post enter and return"],
        )
    post_actor = post_enters[0]["actor"]
    post_span_terminal = span_terminals.get(event_span_id)
    actor_is_bound = (
        post_actor["binding"] == "workbench"
        or (
            post_actor["binding"] == "exact"
            and post_actor["class_name"]
            == "net.minecraftforge.fml.common.eventhandler.EventBus"
            and post_actor["method_name"] == "post"
            and post_actor["method_descriptor"]
            == "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z"
        )
    )
    if (
        not actor_is_bound
        or post_span_terminal is None
        or post_span_terminal["record_type"] != "span_return"
        or any(
            record["causality"]["span_id"] != event_span_id
            or record["actor"] != post_actor
            for record in (post_enters[0], post_returns[0])
        )
        or requested_span["actor"] != post_actor
        or post_span_terminal["actor"] != post_actor
    ):
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            evidence_ordinals=[
                post_enters[0]["ordinal"],
                post_returns[0]["ordinal"],
            ],
            limitations=[
                "post boundaries lack one exact Workbench observer or Forge EventBus span"
            ],
        )
    if post_enters[0]["ordinal"] != records[0]["ordinal"] or post_returns[0]["ordinal"] != records[-1]["ordinal"]:
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            evidence_ordinals=[record["ordinal"] for record in records],
            limitations=["dispatch boundaries are not properly ordered"],
        )

    by_listener: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        boundary = record["payload"]["boundary"]
        listener_ordinal = record["payload"]["listener_ordinal"]
        if boundary.startswith("listener_"):
            if listener_ordinal is None:
                return _answer(
                    query="which-handler-changed-event",
                    status="invalid-evidence",
                    bundles=[bundle],
                    scope=scope,
                    evidence_ordinals=[record["ordinal"]],
                    limitations=["listener boundary lacks its listener ordinal"],
                )
            by_listener[listener_ordinal].append(record)
        elif listener_ordinal is not None:
            return _answer(
                query="which-handler-changed-event",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[record["ordinal"]],
                limitations=["post boundary incorrectly carries a listener ordinal"],
            )
    if sorted(by_listener) != list(range(len(by_listener))):
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            limitations=["listener ordinals are not contiguous from zero"],
        )

    handlers: list[dict[str, Any]] = []
    current_state = (
        post_enters[0]["payload"]["state_after_sha256"],
        post_enters[0]["payload"]["cancelled_after"],
    )
    if (
        post_enters[0]["payload"]["state_before_sha256"],
        post_enters[0]["payload"]["cancelled_before"],
    ) != current_state:
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            limitations=["event changed inside the post-enter boundary"],
        )

    for listener_ordinal in sorted(by_listener):
        listener_records = sorted(
            by_listener[listener_ordinal], key=lambda row: row["ordinal"]
        )
        enters = [
            row for row in listener_records
            if row["payload"]["boundary"] == "listener_enter"
        ]
        terminals = [
            row for row in listener_records
            if row["payload"]["boundary"] in {"listener_return", "listener_throw"}
        ]
        if len(enters) != 1 or len(terminals) != 1 or enters[0]["ordinal"] >= terminals[0]["ordinal"]:
            return _answer(
                query="which-handler-changed-event",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[row["ordinal"] for row in listener_records],
                limitations=[
                    f"listener {listener_ordinal} lacks one ordered enter/terminal pair"
                ],
            )
        record = terminals[0]
        listener_span_id = enters[0]["causality"]["span_id"]
        listener_span = spans.get(listener_span_id)
        listener_span_terminal = span_terminals.get(listener_span_id)
        expected_span_terminal = (
            "span_throw"
            if record["payload"]["boundary"] == "listener_throw"
            else "span_return"
        )
        if (
            listener_span_id is None
            or listener_span_id == event_span_id
            or record["causality"]["span_id"] != listener_span_id
            or listener_span is None
            or listener_span["payload"]["span_kind"] != "event_listener"
            or listener_span_terminal is None
            or listener_span_terminal["record_type"] != expected_span_terminal
            or enters[0]["actor"] != record["actor"]
            or listener_span["actor"] != record["actor"]
            or listener_span_terminal["actor"] != record["actor"]
        ):
            return _answer(
                query="which-handler-changed-event",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[enters[0]["ordinal"], record["ordinal"]],
                limitations=[
                    f"listener {listener_ordinal} actor or listener-span identity changed"
                ],
            )
        enter_payload = enters[0]["payload"]
        if (
            enter_payload["state_before_sha256"],
            enter_payload["cancelled_before"],
        ) != current_state or (
            enter_payload["state_after_sha256"],
            enter_payload["cancelled_after"],
        ) != current_state:
            return _answer(
                query="which-handler-changed-event",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[enters[0]["ordinal"]],
                limitations=[f"listener {listener_ordinal} enter state is discontinuous"],
            )
        payload = record["payload"]
        if (
            payload["state_before_sha256"],
            payload["cancelled_before"],
        ) != current_state:
            return _answer(
                query="which-handler-changed-event",
                status="invalid-evidence",
                bundles=[bundle],
                scope=scope,
                evidence_ordinals=[record["ordinal"]],
                limitations=[f"listener {listener_ordinal} terminal state is discontinuous"],
            )
        changed = (
            payload["state_before_sha256"] != payload["state_after_sha256"]
            or payload["cancelled_before"] != payload["cancelled_after"]
        )
        current_state = (
            payload["state_after_sha256"],
            payload["cancelled_after"],
        )
        handlers.append(
            {
                "ordinal": record["ordinal"],
                "listener_ordinal": payload["listener_ordinal"],
                "event_class": payload["event_class"],
                "bus_id": payload["bus_id"],
                "boundary": payload["boundary"],
                "changed": changed,
                "state_before_sha256": payload["state_before_sha256"],
                "state_after_sha256": payload["state_after_sha256"],
                "cancelled_before": payload["cancelled_before"],
                "cancelled_after": payload["cancelled_after"],
                "actor": deepcopy(record["actor"]),
                "outcome": record["outcome"]["state"],
            }
        )
    post_return = post_returns[0]["payload"]
    if (
        post_return["state_before_sha256"],
        post_return["cancelled_before"],
    ) != current_state or (
        post_return["state_after_sha256"],
        post_return["cancelled_after"],
    ) != current_state:
        return _answer(
            query="which-handler-changed-event",
            status="invalid-evidence",
            bundles=[bundle],
            scope=scope,
            evidence_ordinals=[post_returns[0]["ordinal"]],
            limitations=["post-return state is discontinuous or changed outside a listener"],
        )
    changed_handlers = [row for row in handlers if row["changed"]]
    ambiguous = any(
        row["actor"]["binding"] != "exact"
        for row in changed_handlers
    )
    return _answer(
        query="which-handler-changed-event",
        status="ambiguous" if ambiguous else "answered",
        bundles=[bundle],
        scope=scope,
        evidence_ordinals=[record["ordinal"] for record in records],
        limitations=(
            ["at least one changing listener lacks exact actor binding"]
            if ambiguous
            else []
        ),
        result={
            "handlers": handlers,
            "changed_handler_count": len(changed_handlers),
        },
    )


_DOMAIN_ORDER = {
    "rng_observation": 10,
    "decision": 20,
    "event_dispatch": 30,
    "block_write": 40,
    "checkpoint": 90,
}


@dataclass(slots=True)
class _SemanticItem:
    comparison_key: tuple[Any, ...]
    stage_id: str
    record_type: str
    ordinal: int
    record: Mapping[str, Any]


def _semantic_key(record: Mapping[str, Any], stage: str) -> tuple[Any, ...]:
    scope = record["scope"]
    chunk = scope["chunk"] or {"x": 0, "z": 0}
    payload = record["payload"]
    record_type = record["record_type"]
    specific: tuple[Any, ...]
    if record_type == "rng_observation":
        specific = (
            payload["stream_id"],
            -1 if payload["call_ordinal"] is None else payload["call_ordinal"],
            payload["detail"],
            payload["operation"],
        )
    elif record_type == "decision":
        specific = (payload["rule_id"],)
    elif record_type == "event_dispatch":
        specific = (
            payload["bus_id"],
            payload["event_class"],
            -1 if payload["listener_ordinal"] is None else payload["listener_ordinal"],
            payload["boundary"],
        )
    elif record_type == "block_write":
        specific = (
            *payload["position"],
            payload["write_chain_id"],
            payload["channel"],
        )
    else:
        specific = (payload["checkpoint_id"],)
    return (
        scope["dimension_id"],
        chunk["z"],
        chunk["x"],
        stage,
        _DOMAIN_ORDER[record_type],
        *specific,
    )


def _semantic_value(record: Mapping[str, Any]) -> dict[str, Any]:
    # The exact transformed-class digest is a custody receipt, not a semantic
    # comparison field.  Cleanroom/Mixin may stamp a random MixinMerged
    # session ID into otherwise equivalent final bytes.  Writer and event
    # answers retain that exact per-run receipt; divergence compares the
    # source/method actor identity without promoting byte-level noise into a
    # world-generation difference.
    return {
        "record_type": record["record_type"],
        "payload": deepcopy(record["payload"]),
        "outcome": {
            "state": record["outcome"]["state"],
            "cancelled": record["outcome"]["cancelled"],
            "event_result": record["outcome"]["event_result"],
        },
        "actor": {
            "binding": record["actor"]["binding"],
            "mod_id": record["actor"]["mod_id"],
            "code_source_sha256": record["actor"]["code_source_sha256"],
            "class_name": record["actor"]["class_name"],
            "method_name": record["actor"]["method_name"],
            "method_descriptor": record["actor"]["method_descriptor"],
        },
    }


def _iter_semantic_base_items(
    bundle: Mapping[str, Any],
    allowed_scopes: set[tuple[int, int, int, str]],
    allowed_checkpoint_ordinals: set[int],
) -> Iterable[_SemanticItem]:
    records = bundle["records"]
    active_stages: dict[str, str] = {}
    for record in records:
        record_type = record["record_type"]
        span_id = record["causality"]["span_id"]
        if record_type == "span_enter" and span_id is not None:
            parent_span_id = record["causality"]["parent_span_id"]
            stage = active_stages.get(
                parent_span_id,
                "worldgen-stage:unscoped",
            )
            if record["payload"]["span_kind"] == "generation_phase":
                stage = record["payload"]["operation_id"]
            active_stages[span_id] = stage

        if record_type not in _DOMAIN_ORDER:
            if record_type in {"span_return", "span_throw"} and span_id is not None:
                active_stages.pop(span_id, None)
            continue
        if (
            record_type == "checkpoint"
            and record["ordinal"] not in allowed_checkpoint_ordinals
        ):
            continue
        stage = (
            str(record["payload"]["stage_id"])
            if record_type == "checkpoint"
            else active_stages.get(span_id, "worldgen-stage:unscoped")
        )
        scope = record["scope"]
        chunk = scope["chunk"]
        if chunk is None or (
            scope["dimension_id"],
            chunk["x"],
            chunk["z"],
            stage,
        ) not in allowed_scopes:
            continue
        yield _SemanticItem(
            comparison_key=_semantic_key(record, stage),
            stage_id=stage,
            record_type=record_type,
            ordinal=record["ordinal"],
            record=record,
        )


def _semantic_items(
    bundle: Mapping[str, Any],
    allowed_scopes: set[tuple[int, int, int, str]],
    allowed_checkpoint_ordinals: set[int],
) -> list[_SemanticItem]:
    items = list(
        _iter_semantic_base_items(
            bundle,
            allowed_scopes,
            allowed_checkpoint_ordinals,
        )
    )

    # Raw occurrence order defines the local ordinal for records with an
    # otherwise identical semantic key.  Sorting once by base key and raw
    # ordinal avoids a second full grouped-record graph and its deep copies.
    items.sort(key=lambda item: (item.comparison_key, item.ordinal))
    previous: tuple[Any, ...] | None = None
    occurrence = 0
    for item in items:
        base_key = item.comparison_key
        if base_key == previous:
            occurrence += 1
        else:
            previous = base_key
            occurrence = 0
        item.comparison_key = (*base_key, occurrence)
    return items


def _semantic_items_equal(left: _SemanticItem, right: _SemanticItem) -> bool:
    left_record = left.record
    right_record = right.record
    return (
        left.record_type == right.record_type
        and left_record["payload"] == right_record["payload"]
        and {
            key: left_record["outcome"][key]
            for key in ("state", "cancelled", "event_result")
        }
        == {
            key: right_record["outcome"][key]
            for key in ("state", "cancelled", "event_result")
        }
        and {
            key: left_record["actor"][key]
            for key in (
                "binding",
                "mod_id",
                "code_source_sha256",
                "class_name",
                "method_name",
                "method_descriptor",
            )
        }
        == {
            key: right_record["actor"][key]
            for key in (
                "binding",
                "mod_id",
                "code_source_sha256",
                "class_name",
                "method_name",
                "method_descriptor",
            )
        }
    )


def _public_semantic_item(
    item: _SemanticItem,
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "comparison_key": list(item.comparison_key),
        "stage_id": item.stage_id,
        "record_type": item.record_type,
        "ordinal": item.ordinal,
        "value": _semantic_value(item.record),
        "capture_id": _capture_identity(bundle),
    }


def first_divergence(
    left: Mapping[str, Any] | AdmittedWorldgenBundle,
    right: Mapping[str, Any] | AdmittedWorldgenBundle,
    *,
    comparison_scope_sha256: str,
) -> dict[str, Any]:
    """Locate the first stable semantic occurrence that differs between runs."""

    left, left_validated = _query_bundle(left)
    right, right_validated = _query_bundle(right)
    scope = {"comparison_scope_sha256": comparison_scope_sha256}
    left_failure = _admission_failure(
        left,
        "first-divergence",
        scope,
        already_validated=left_validated,
    )
    if left_failure is not None:
        return left_failure
    right_failure = _admission_failure(
        right,
        "first-divergence",
        scope,
        already_validated=right_validated,
    )
    if right_failure is not None:
        right_failure["capture_ids"] = [
            _capture_identity(left),
            right_failure["capture_ids"][-1],
        ]
        return right_failure

    left_fingerprints = [
        item for item in left["semantic_fingerprints"]
        if item["scope"]["comparison_scope_sha256"] == comparison_scope_sha256
    ]
    right_fingerprints = [
        item for item in right["semantic_fingerprints"]
        if item["scope"]["comparison_scope_sha256"] == comparison_scope_sha256
    ]
    if not left_fingerprints or not right_fingerprints:
        return _answer(
            query="first-divergence",
            status="unavailable",
            bundles=[left, right],
            scope=scope,
            limitations=["one or both captures lack the exact comparison scope"],
        )

    def fingerprint_cohort(
        fingerprints: list[Mapping[str, Any]],
    ) -> dict[tuple[int, int, int, str], tuple[Any, ...]]:
        cohort: dict[tuple[int, int, int, str], tuple[Any, ...]] = {}
        for fingerprint in fingerprints:
            fingerprint_scope = fingerprint["scope"]
            key = (
                fingerprint_scope["dimension_id"],
                fingerprint_scope["chunk"]["x"],
                fingerprint_scope["chunk"]["z"],
                fingerprint_scope["stage_id"],
            )
            cohort[key] = (
                fingerprint["canonicalization_id"],
                tuple(fingerprint["included_domains"]),
                tuple(fingerprint["excluded_observation_fields"]),
            )
        return cohort

    left_cohort = fingerprint_cohort(left_fingerprints)
    right_cohort = fingerprint_cohort(right_fingerprints)
    if left_cohort != right_cohort:
        return _answer(
            query="first-divergence",
            status="unavailable",
            bundles=[left, right],
            scope=scope,
            limitations=[
                "comparison cohorts differ in chunks, stages, domains, or canonicalizer"
            ],
        )

    allowed_scopes = set(left_cohort)
    left_items = _semantic_items(
        left,
        allowed_scopes,
        {item["checkpoint_ordinal"] for item in left_fingerprints},
    )
    right_items = _semantic_items(
        right,
        allowed_scopes,
        {item["checkpoint_ordinal"] for item in right_fingerprints},
    )
    last_equal: dict[str, Any] | None = None
    left_index = 0
    right_index = 0
    comparison_item_count = 0
    while left_index < len(left_items) or right_index < len(right_items):
        left_item = (
            None if left_index == len(left_items) else left_items[left_index]
        )
        right_item = (
            None if right_index == len(right_items) else right_items[right_index]
        )
        if left_item is None:
            difference = "missing-left"
        elif right_item is None:
            difference = "missing-right"
        elif left_item.comparison_key < right_item.comparison_key:
            difference = "missing-right"
            right_item = None
        elif right_item.comparison_key < left_item.comparison_key:
            difference = "missing-left"
            left_item = None
        elif not _semantic_items_equal(left_item, right_item):
            difference = "changed-value"
        else:
            last_equal = {
                "comparison_key": list(left_item.comparison_key),
                "record_type": left_item.record_type,
                "left_ordinal": left_item.ordinal,
                "right_ordinal": right_item.ordinal,
            }
            left_index += 1
            right_index += 1
            comparison_item_count += 1
            continue

        differing_item = left_item or right_item
        assert differing_item is not None
        return _answer(
            query="first-divergence",
            status="diverged",
            bundles=[left, right],
            scope=scope,
            evidence_ordinals=[],
            result={
                "difference": difference,
                "last_equal": last_equal,
                "first_difference": {
                    "comparison_key": list(differing_item.comparison_key),
                    "stage_id": differing_item.stage_id,
                    "record_type": differing_item.record_type,
                    "left": (
                        None
                        if left_item is None
                        else _public_semantic_item(left_item, left)
                    ),
                    "right": (
                        None
                        if right_item is None
                        else _public_semantic_item(right_item, right)
                    ),
                },
            },
        )

    return _answer(
        query="first-divergence",
        status="equal",
        bundles=[left, right],
        scope=scope,
        limitations=["equality is bounded to the declared comparison scope and domains"],
        result={
            "comparison_item_count": comparison_item_count,
            "last_equal": last_equal,
            "first_difference": None,
        },
    )
