"""Deterministic synthetic fixtures for the Worldgen Observatory contract.

This module exercises capture and comparison semantics without launching
Minecraft, Cleanroom, Forge, or a mod.  Its output is synthetic contract
evidence only.  It must never be presented as runtime evidence that a probe
was installed, that a hook was reached, or that a real decorator was
attributed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from .bundle import (
    BundleBuilder,
    canonical_json_bytes,
    exact_actor,
    new_run,
    validate_bundle,
    write_bundle,
)


MATRIX_FORMAT = "workbench-crucible-worldgen-observatory-synthetic-matrix-v1"
MATRIX_PREFIX = "crucible-worldgen-synthetic-matrix:sha256:"
EVIDENCE_CLASS = "synthetic_contract_evidence"
SYNTHETIC_DISCLAIMER = (
    "Synthetic contract evidence only; no Minecraft, Cleanroom, Forge, or mod "
    "runtime was executed."
)
DEFAULT_OUTPUT_ROOT = Path(
    ".workbench/evidence/worldgen-observatory/synthetic-contract-matrix-v1"
)

FIXED_SEED = 0x57A6_0B5E_12
REGION = ((0, 0), (1, 0), (0, 1), (1, 1))
FORWARD_ORDER = REGION
REVERSE_ORDER = tuple(reversed(REGION))
CANONICALIZATION_ID = "synthetic-canonicalizer:block-state-map-v1"
COMPARISON_SCOPE = {
    "dimension_id": 0,
    "region_chunks": [{"x": x, "z": z} for x, z in REGION],
    "semantic_domain": "block_states",
    "fixture_kind": EVIDENCE_CLASS,
}


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _digest_bytes(canonical_json_bytes(value))


def _actor(
    mod_id: str,
    class_name: str,
    method_name: str,
    descriptor: str,
    *,
    workbench: bool = False,
) -> dict[str, Any]:
    identity = {
        "evidence_class": EVIDENCE_CLASS,
        "mod_id": mod_id,
        "class_name": class_name,
        "method_name": method_name,
        "descriptor": descriptor,
    }
    return exact_actor(
        mod_id=mod_id,
        code_source_sha256=_digest({"synthetic_code_source": identity}),
        class_name=class_name,
        method_name=method_name,
        method_descriptor=descriptor,
        mapping_namespace="synthetic-contract-v1",
        transformed_class_sha256=_digest({"synthetic_transformed_class": identity}),
        workbench=workbench,
    )


HARNESS_ACTOR = _actor(
    "workbench_synthetic_harness",
    "dev.workbench.synthetic.ContractHarness",
    "run",
    "()V",
    workbench=True,
)
GENERATOR_ACTOR = _actor(
    "workbench_synthetic_generator",
    "dev.workbench.synthetic.MinimalGenerator",
    "generateChunk",
    "(II)LSyntheticChunk;",
)
STANDARD_EVENT_ACTOR = _actor(
    "synthetic_standard_event_handler",
    "example.synthetic.StandardDecorateHandler",
    "onDecorate",
    "(LSyntheticDecorateEvent;)V",
)
EXTERNAL_DECORATOR_ACTOR = _actor(
    "unrelated_synthetic_decorator",
    "unrelated.synthetic.ExternalDecorator",
    "generate",
    "(LSyntheticWorld;Ljava/util/Random;II)V",
)


@dataclass(frozen=True)
class SyntheticCaseSpec:
    """One exact modeled execution condition in the synthetic matrix."""

    case_id: str
    observer_enabled: bool
    chunk_order: tuple[tuple[int, int], ...]
    execution_identity: str
    restart_identity: str
    crash_before_seal: bool = False


@dataclass
class SyntheticCaseResult:
    """The bundle and semantic result from one synthetic case."""

    spec: SyntheticCaseSpec
    bundle: dict[str, Any]
    semantic_state: dict[str, str]
    semantic_output_sha256: str

    @property
    def fingerprint_ids(self) -> list[str]:
        return [
            str(item["fingerprint_id"])
            for item in self.bundle["semantic_fingerprints"]
        ]


@dataclass
class SyntheticMatrixResult:
    """A fully evaluated matrix and its unpublished in-memory bundles."""

    cases: dict[str, SyntheticCaseResult]
    summary: dict[str, Any]


CASE_SPECS = (
    SyntheticCaseSpec(
        "aa-left", True, FORWARD_ORDER, "aa-left", "synthetic-process-aa-left"
    ),
    SyntheticCaseSpec(
        "aa-right", True, FORWARD_ORDER, "aa-right", "synthetic-process-aa-right"
    ),
    SyntheticCaseSpec(
        "observer-off",
        False,
        FORWARD_ORDER,
        "observer-off",
        "synthetic-process-observer-off",
    ),
    SyntheticCaseSpec(
        "observer-on",
        True,
        FORWARD_ORDER,
        "observer-on",
        "synthetic-process-observer-on",
    ),
    SyntheticCaseSpec(
        "order-forward",
        True,
        FORWARD_ORDER,
        "order-forward",
        "synthetic-process-order-forward",
    ),
    SyntheticCaseSpec(
        "order-reverse",
        True,
        REVERSE_ORDER,
        "order-reverse",
        "synthetic-process-order-reverse",
    ),
    SyntheticCaseSpec(
        "restart-before",
        True,
        FORWARD_ORDER,
        "restart-before",
        "synthetic-process-before-restart",
    ),
    SyntheticCaseSpec(
        "restart-after",
        True,
        FORWARD_ORDER,
        "restart-after",
        "synthetic-process-after-restart",
    ),
    SyntheticCaseSpec(
        "crash-before-seal",
        True,
        FORWARD_ORDER,
        "crash-before-seal",
        "synthetic-process-crash",
        crash_before_seal=True,
    ),
)


def _run_manifest(spec: SyntheticCaseSpec) -> dict[str, Any]:
    observer_state = "enabled" if spec.observer_enabled else "disabled"
    fixture_definition = {
        "evidence_class": EVIDENCE_CLASS,
        "fixed_seed": FIXED_SEED,
        "region": REGION,
        "stages": ["base", "standard-event", "external-decorator"],
        "contract_harness_version": 1,
    }
    return new_run(
        capture_mode="lossless-fixture",
        capture_plan_sha256=_digest(
            {
                "fixture": fixture_definition,
                "selection": "all-synthetic-fixture-records",
                "observer_state": observer_state,
            }
        ),
        fixture_id="synthetic-fixture:worldgen-observatory-matrix-v1",
        fixture_sha256=_digest(fixture_definition),
        environment={
            "minecraft_version": "1.12.2",
            "platform_profile_id": "workbench-platform:synthetic-contract-harness-v1",
            "platform_profile_sha256": _digest(
                {"profile": "synthetic-contract-harness-v1"}
            ),
            "pack_profile_id": None,
            "pack_profile_sha256": None,
            "snapshot_id": "snapshot:synthetic-worldgen-contract-v1",
            "physical_side": "DEDICATED_SERVER",
            "runtime_java": "synthetic-python-contract-harness",
            "mapping_namespace": "synthetic-contract-v1",
            "transformed_runtime_sha256": _digest(
                {"runtime": "synthetic-no-cleanroom-runtime"}
            ),
            "mod_set_sha256": _digest(
                {
                    "synthetic_mods": [
                        "workbench_synthetic_generator",
                        "synthetic_standard_event_handler",
                        "unrelated_synthetic_decorator",
                    ],
                    "modeled_observer": observer_state,
                }
            ),
            "configuration_set_sha256": _digest(
                {"modeled_observer": observer_state, "fixture": fixture_definition}
            ),
        },
        world={
            "world_instance_id": f"synthetic-world:{spec.execution_identity}",
            "world_seed_sha256": _digest({"fixed_seed": FIXED_SEED}),
            "world_type": "SYNTHETIC_CONTRACT_WORLD",
            "generator_options_sha256": _digest(
                {"region": REGION, "stable_rng_lanes": True}
            ),
            "dimension_ids": [0],
        },
    )


def _lane_call(
    *,
    chunk: tuple[int, int],
    stage: str,
    bound: int,
    call_ordinal: int = 0,
) -> tuple[int, dict[str, Any]]:
    """Derive one order-independent pseudo-random result and its record payload."""

    lane = {
        "algorithm": "sha256-derived-synthetic-lane-v1",
        "fixed_seed": FIXED_SEED,
        "dimension_id": 0,
        "chunk": {"x": chunk[0], "z": chunk[1]},
        "stage": stage,
    }
    parameters = {"bound": bound}
    call_material = {
        "lane": lane,
        "call_ordinal": call_ordinal,
        "operation": "nextInt",
        "parameters": parameters,
    }
    raw = hashlib.sha256(canonical_json_bytes(call_material)).digest()
    value = int.from_bytes(raw[:8], "big") % bound
    stream_id = f"synthetic-rng:chunk.{chunk[0]}.{chunk[1]}.{stage}"
    result = {"value": value}
    return value, {
        "detail": "call",
        "stream_id": stream_id,
        "algorithm_class": "workbench.synthetic.Sha256LaneV1",
        "operation": "nextInt",
        "call_ordinal": call_ordinal,
        "parameters_sha256": _digest(parameters),
        "result_sha256": _digest(result),
        "rolling_digest": _digest(
            {"stream_id": stream_id, "calls": [{**call_material, "result": result}]}
        ),
    }


def _position_key(position: Mapping[str, int]) -> str:
    return f"{position['x']},{position['y']},{position['z']}"


def _chunk_payload(chunk: tuple[int, int]) -> dict[str, int]:
    return {"x": chunk[0], "z": chunk[1]}


def _block_write_payload(
    *,
    write_chain_id: str,
    channel: str,
    position: Mapping[str, int],
    chunk: tuple[int, int],
    before: str,
    after: str,
    flags: int | None,
    terminal: bool,
) -> dict[str, Any]:
    return {
        "write_chain_id": write_chain_id,
        "channel": channel,
        "position": [position["x"], position["y"], position["z"]],
        "generation_chunk": _chunk_payload(chunk),
        "target_chunk": _chunk_payload(chunk),
        "before_state_sha256": _digest({"block_state": before}),
        "after_state_sha256": _digest({"block_state": after}),
        "flags": flags,
        "terminal": terminal,
    }


def _record_probe_health(builder: BundleBuilder, observer_enabled: bool) -> None:
    state = "reached" if observer_enabled else "not_installed"
    count = 1 if observer_enabled else 0
    builder.record(
        "probe_health",
        {
            "hook_id": "synthetic-hook:modeled-chunk-generation",
            "health_state": state,
            "target_class": "synthetic.contract.ModelChunkGenerator",
            "target_method": "generateChunk",
            "target_descriptor": "(II)LSyntheticChunk;",
            "original_class_sha256": _digest(
                {"synthetic_class": "ModelChunkGenerator", "state": "original"}
            ),
            "transformed_class_sha256": _digest(
                {"synthetic_class": "ModelChunkGenerator", "state": "modeled"}
            ),
            "expected_injection_count": count,
            "observed_injection_count": count,
        },
        actor=HARNESS_ACTOR,
        trace_id="synthetic-trace:probe-health",
        root_trigger_id="synthetic-trigger:contract-harness",
    )


def _record_rng(
    builder: BundleBuilder,
    *,
    chunk: tuple[int, int],
    stage: str,
    bound: int,
    actor: Mapping[str, Any],
) -> int:
    value, payload = _lane_call(chunk=chunk, stage=stage, bound=bound)
    builder.record(
        "rng_observation",
        payload,
        dimension_id=0,
        chunk=chunk,
        actor=actor,
    )
    return value


def _base_stage(
    builder: BundleBuilder,
    semantic_state: dict[str, str],
    chunk: tuple[int, int],
    *,
    crash_before_seal: bool,
) -> bool:
    phase = builder.enter_span(
        span_kind="generation_phase",
        operation_id="synthetic-phase:base",
        arguments={"chunk": _chunk_payload(chunk)},
        dimension_id=0,
        chunk=chunk,
        actor=GENERATOR_ACTOR,
    )
    height_offset = _record_rng(
        builder,
        chunk=chunk,
        stage="base",
        bound=8,
        actor=GENERATOR_ACTOR,
    )
    position = {
        "x": chunk[0] * 16 + 4,
        "y": 48 + height_offset,
        "z": chunk[1] * 16 + 4,
    }
    selected_state = "minecraft:stone" if height_offset % 2 == 0 else "minecraft:dirt"
    builder.record(
        "decision",
        {
            "rule_id": "synthetic-rule:base-block",
            "input_sha256": _digest(
                {"chunk": _chunk_payload(chunk), "height_offset": height_offset}
            ),
            "decision": "selected",
            "output_sha256": _digest({"block_state": selected_state}),
        },
        dimension_id=0,
        chunk=chunk,
        actor=GENERATOR_ACTOR,
    )
    key = _position_key(position)
    before = semantic_state.get(key, "minecraft:air")
    builder.record(
        "block_write",
        _block_write_payload(
            write_chain_id=f"synthetic-write:primer.{chunk[0]}.{chunk[1]}",
            channel="chunk_primer",
            position=position,
            chunk=chunk,
            before=before,
            after=selected_state,
            flags=None,
            terminal=True,
        ),
        dimension_id=0,
        chunk=chunk,
        actor=GENERATOR_ACTOR,
    )
    semantic_state[key] = selected_state

    if crash_before_seal:
        # A modeled abrupt process loss intentionally leaves both the base
        # phase and its parent chunk-request span open.
        return False

    builder.checkpoint(
        checkpoint_id=f"synthetic-checkpoint:chunk.{chunk[0]}.{chunk[1]}.base",
        stage_id="synthetic-stage:base",
        canonicalization_id=CANONICALIZATION_ID,
        semantic_state=dict(sorted(semantic_state.items())),
        comparison_scope=COMPARISON_SCOPE,
        dimension_id=0,
        chunk=chunk,
        included_domains=["block_states"],
        actor=GENERATOR_ACTOR,
    )
    builder.return_span(
        phase,
        span_kind="generation_phase",
        result={"stage": "base", "completed": True},
        dimension_id=0,
        chunk=chunk,
        actor=GENERATOR_ACTOR,
    )
    return True


def _standard_event_mutation(builder: BundleBuilder, chunk: tuple[int, int]) -> None:
    event_before = {
        "event": "DecorateBiomeEvent.Decorate",
        "type": "TREE",
        "result": "DEFAULT",
        "cancelled": False,
    }
    event_after = {**event_before, "result": "ALLOW"}
    before_digest = _digest(event_before)
    after_digest = _digest(event_after)
    common = {
        "event_class": "net.minecraftforge.event.terraingen.DecorateBiomeEvent$Decorate",
        "bus_id": "synthetic-bus:terrain-gen",
        "cancelled_before": False,
        "cancelled_after": False,
    }
    builder.record(
        "event_dispatch",
        {
            **common,
            "boundary": "post_enter",
            "listener_ordinal": None,
            "state_before_sha256": before_digest,
            "state_after_sha256": before_digest,
        },
        dimension_id=0,
        chunk=chunk,
        actor=HARNESS_ACTOR,
    )
    listener = builder.enter_span(
        span_kind="event_listener",
        operation_id="synthetic-listener:standard-decorate-handler",
        arguments=event_before,
        dimension_id=0,
        chunk=chunk,
        actor=STANDARD_EVENT_ACTOR,
    )
    builder.record(
        "event_dispatch",
        {
            **common,
            "boundary": "listener_enter",
            "listener_ordinal": 0,
            "state_before_sha256": before_digest,
            "state_after_sha256": before_digest,
        },
        dimension_id=0,
        chunk=chunk,
        actor=STANDARD_EVENT_ACTOR,
    )
    builder.record(
        "decision",
        {
            "rule_id": "synthetic-rule:standard-event-result",
            "input_sha256": before_digest,
            "decision": "accepted",
            "output_sha256": after_digest,
        },
        dimension_id=0,
        chunk=chunk,
        actor=STANDARD_EVENT_ACTOR,
    )
    builder.record(
        "event_dispatch",
        {
            **common,
            "boundary": "listener_return",
            "listener_ordinal": 0,
            "state_before_sha256": before_digest,
            "state_after_sha256": after_digest,
        },
        dimension_id=0,
        chunk=chunk,
        actor=STANDARD_EVENT_ACTOR,
    )
    builder.return_span(
        listener,
        span_kind="event_listener",
        result=event_after,
        dimension_id=0,
        chunk=chunk,
        actor=STANDARD_EVENT_ACTOR,
    )
    builder.record(
        "event_dispatch",
        {
            **common,
            "boundary": "post_return",
            "listener_ordinal": None,
            "state_before_sha256": before_digest,
            "state_after_sha256": after_digest,
        },
        dimension_id=0,
        chunk=chunk,
        actor=HARNESS_ACTOR,
    )


def _external_decorator(
    builder: BundleBuilder,
    semantic_state: dict[str, str],
    chunk: tuple[int, int],
) -> None:
    decorator = builder.enter_span(
        span_kind="generator_call",
        operation_id="synthetic-generator:unrelated-decorator",
        arguments={"chunk": _chunk_payload(chunk)},
        dimension_id=0,
        chunk=chunk,
        actor=EXTERNAL_DECORATOR_ACTOR,
    )
    variant = _record_rng(
        builder,
        chunk=chunk,
        stage="external-decorator",
        bound=2,
        actor=EXTERNAL_DECORATOR_ACTOR,
    )
    selected_state = (
        "minecraft:red_flower[variant=poppy]"
        if variant == 0
        else "minecraft:yellow_flower[variant=dandelion]"
    )
    builder.record(
        "decision",
        {
            "rule_id": "synthetic-rule:unrelated-decorator-block",
            "input_sha256": _digest(
                {"chunk": _chunk_payload(chunk), "variant": variant}
            ),
            "decision": "selected",
            "output_sha256": _digest({"block_state": selected_state}),
        },
        dimension_id=0,
        chunk=chunk,
        actor=EXTERNAL_DECORATOR_ACTOR,
    )
    position = {
        "x": chunk[0] * 16 + 8,
        "y": 65,
        "z": chunk[1] * 16 + 8,
    }
    key = _position_key(position)
    before = semantic_state.get(key, "minecraft:air")
    write_chain_id = f"synthetic-write:unrelated-decorator.{chunk[0]}.{chunk[1]}"
    builder.record(
        "block_write",
        _block_write_payload(
            write_chain_id=write_chain_id,
            channel="world_api",
            position=position,
            chunk=chunk,
            before=before,
            after=selected_state,
            flags=2,
            terminal=False,
        ),
        dimension_id=0,
        chunk=chunk,
        actor=EXTERNAL_DECORATOR_ACTOR,
    )
    builder.record(
        "block_write",
        _block_write_payload(
            write_chain_id=write_chain_id,
            channel="chunk_storage",
            position=position,
            chunk=chunk,
            before=before,
            after=selected_state,
            flags=None,
            terminal=True,
        ),
        dimension_id=0,
        chunk=chunk,
        actor=EXTERNAL_DECORATOR_ACTOR,
    )
    semantic_state[key] = selected_state
    builder.return_span(
        decorator,
        span_kind="generator_call",
        result={"placed": True, "position": position},
        dimension_id=0,
        chunk=chunk,
        actor=EXTERNAL_DECORATOR_ACTOR,
    )


def _decorate_stage(
    builder: BundleBuilder,
    semantic_state: dict[str, str],
    chunk: tuple[int, int],
) -> None:
    phase = builder.enter_span(
        span_kind="generation_phase",
        operation_id="synthetic-phase:decorate",
        arguments={"chunk": _chunk_payload(chunk)},
        dimension_id=0,
        chunk=chunk,
        actor=GENERATOR_ACTOR,
    )
    _standard_event_mutation(builder, chunk)
    _external_decorator(builder, semantic_state, chunk)
    builder.checkpoint(
        checkpoint_id=f"synthetic-checkpoint:chunk.{chunk[0]}.{chunk[1]}.decorated",
        stage_id="synthetic-stage:decorated",
        canonicalization_id=CANONICALIZATION_ID,
        semantic_state=dict(sorted(semantic_state.items())),
        comparison_scope=COMPARISON_SCOPE,
        dimension_id=0,
        chunk=chunk,
        included_domains=["block_states"],
        actor=GENERATOR_ACTOR,
    )
    builder.return_span(
        phase,
        span_kind="generation_phase",
        result={"stage": "decorate", "completed": True},
        dimension_id=0,
        chunk=chunk,
        actor=GENERATOR_ACTOR,
    )


def _generate_chunk(
    builder: BundleBuilder,
    semantic_state: dict[str, str],
    chunk: tuple[int, int],
    *,
    crash_before_seal: bool,
) -> bool:
    builder.record(
        "chunk_access",
        {
            "access_kind": "generate",
            "api_id": "synthetic-api:chunk-provider.generate",
            "current_generation_chunk": None,
            "requested_chunk": _chunk_payload(chunk),
            "loaded_before": False,
            "result": "generated",
        },
        dimension_id=0,
        chunk=chunk,
        actor=HARNESS_ACTOR,
    )
    request = builder.enter_span(
        span_kind="chunk_request",
        operation_id="synthetic-operation:chunk-request",
        arguments={"chunk": _chunk_payload(chunk)},
        dimension_id=0,
        chunk=chunk,
        actor=HARNESS_ACTOR,
    )
    if not _base_stage(
        builder,
        semantic_state,
        chunk,
        crash_before_seal=crash_before_seal,
    ):
        return False
    _decorate_stage(builder, semantic_state, chunk)
    builder.return_span(
        request,
        span_kind="chunk_request",
        result={"generated": True, "populated": True},
        dimension_id=0,
        chunk=chunk,
        actor=HARNESS_ACTOR,
    )
    return True


def run_synthetic_case(spec: SyntheticCaseSpec) -> SyntheticCaseResult:
    """Execute one deterministic modeled case using :class:`BundleBuilder`."""

    monotonic_values = itertools.count(start=10_000, step=10)
    builder = BundleBuilder(
        _run_manifest(spec),
        selection_id="synthetic-selection:lossless-region-v1",
        actor=HARNESS_ACTOR,
        monotonic_ns=lambda: next(monotonic_values),
    )
    builder.add_limitation(SYNTHETIC_DISCLAIMER)
    builder.start()
    _record_probe_health(builder, spec.observer_enabled)
    semantic_state: dict[str, str] = {}

    for index, chunk in enumerate(spec.chunk_order):
        completed = _generate_chunk(
            builder,
            semantic_state,
            chunk,
            crash_before_seal=spec.crash_before_seal and index == 0,
        )
        if not completed:
            bundle = builder.incomplete(
                reason="process_crash",
                recoverable=True,
                diagnostic={
                    "evidence_class": EVIDENCE_CLASS,
                    "case_id": spec.case_id,
                    "modeled_exit": "abrupt-before-seal",
                },
            )
            return SyntheticCaseResult(
                spec=spec,
                bundle=bundle,
                semantic_state=dict(sorted(semantic_state.items())),
                semantic_output_sha256=_digest(dict(sorted(semantic_state.items()))),
            )

    builder.checkpoint(
        checkpoint_id="synthetic-checkpoint:region.final",
        stage_id="synthetic-stage:region-final",
        canonicalization_id=CANONICALIZATION_ID,
        semantic_state=dict(sorted(semantic_state.items())),
        comparison_scope=COMPARISON_SCOPE,
        dimension_id=0,
        chunk=(0, 0),
        included_domains=["block_states"],
        actor=GENERATOR_ACTOR,
    )
    bundle = builder.complete()
    return SyntheticCaseResult(
        spec=spec,
        bundle=bundle,
        semantic_state=dict(sorted(semantic_state.items())),
        semantic_output_sha256=_digest(dict(sorted(semantic_state.items()))),
    )


def _completed_capture_is_healthy(case: SyntheticCaseResult) -> bool:
    bundle = case.bundle
    summary = bundle["summary"]
    publication = bundle["publication"]
    try:
        validate_bundle(bundle, require_completed=True)
    except ValueError:
        return False
    return bool(
        publication["state"] == "completed"
        and publication["completion_seal"] is not None
        and publication["crash_residue"] is None
        and summary["dropped_record_count"] == 0
        and summary["open_span_ids"] == []
        and summary["span_enter_count"]
        == summary["span_return_count"] + summary["span_throw_count"]
    )


def _external_write_chains(case: SyntheticCaseResult) -> dict[str, list[dict[str, Any]]]:
    chains: dict[str, list[dict[str, Any]]] = {}
    for record in case.bundle["records"]:
        if (
            record["record_type"] == "block_write"
            and record["actor"]["mod_id"] == "unrelated_synthetic_decorator"
        ):
            chains.setdefault(record["payload"]["write_chain_id"], []).append(record)
    return chains


def _external_writes_are_exact(case: SyntheticCaseResult) -> bool:
    chains = _external_write_chains(case)
    if len(chains) != len(REGION):
        return False
    for records in chains.values():
        if [record["payload"]["channel"] for record in records] != [
            "world_api",
            "chunk_storage",
        ]:
            return False
        if [record["payload"]["terminal"] for record in records] != [False, True]:
            return False
        if not all(
            record["actor"]["binding"] == "exact"
            and record["actor"]["mod_id"] == "unrelated_synthetic_decorator"
            for record in records
        ):
            return False
    return True


def _event_mutation_count(case: SyntheticCaseResult) -> int:
    return sum(
        1
        for record in case.bundle["records"]
        if record["record_type"] == "event_dispatch"
        and record["payload"]["boundary"] == "listener_return"
        and record["payload"]["state_before_sha256"]
        != record["payload"]["state_after_sha256"]
        and record["actor"]["mod_id"] == "synthetic_standard_event_handler"
    )


def _all_records_labeled_synthetic(case: SyntheticCaseResult) -> bool:
    return bool(
        SYNTHETIC_DISCLAIMER in case.bundle["summary"]["limitations"]
        and all(
            SYNTHETIC_DISCLAIMER in record["coverage"]["limitations"]
            for record in case.bundle["records"]
        )
        and case.bundle["run"]["environment"]["platform_profile_id"]
        == "workbench-platform:synthetic-contract-harness-v1"
    )


def _comparison(
    comparison_id: str,
    left_case: str,
    right_case: str,
    assertions: Mapping[str, bool],
) -> dict[str, Any]:
    return {
        "comparison_id": comparison_id,
        "evidence_class": EVIDENCE_CLASS,
        "disclaimer": SYNTHETIC_DISCLAIMER,
        "left_case": left_case,
        "right_case": right_case,
        "assertions": dict(assertions),
        "outcome": "pass" if all(assertions.values()) else "fail",
    }


def _case_summary(case: SyntheticCaseResult) -> dict[str, Any]:
    bundle = case.bundle
    publication = bundle["publication"]
    chains = _external_write_chains(case)
    bundle_bytes = canonical_json_bytes(bundle) + b"\n"
    return {
        "evidence_class": EVIDENCE_CLASS,
        "disclaimer": SYNTHETIC_DISCLAIMER,
        "bundle_path": f"bundles/{case.spec.case_id}.bundle.json",
        "bundle_file_sha256": _digest_bytes(bundle_bytes),
        "publication_state": publication["state"],
        "run_id": bundle["run"]["run_id"],
        "capture_id": (
            None
            if publication["completion_seal"] is None
            else publication["completion_seal"]["capture_id"]
        ),
        "residue_id": (
            None
            if publication["crash_residue"] is None
            else publication["crash_residue"]["residue_id"]
        ),
        "observer_enabled": case.spec.observer_enabled,
        "chunk_order": [
            {"x": chunk[0], "z": chunk[1]} for chunk in case.spec.chunk_order
        ],
        "execution_identity": case.spec.execution_identity,
        "restart_identity": case.spec.restart_identity,
        "semantic_output_sha256": case.semantic_output_sha256,
        "phase_fingerprint_ids": case.fingerprint_ids,
        "phase_semantic_state_sha256": [
            item["semantic_state_sha256"]
            for item in bundle["semantic_fingerprints"]
        ],
        "event_mutation_count": _event_mutation_count(case),
        "external_decorator_write_chain_ids": sorted(chains),
        "record_count": bundle["summary"]["record_count"],
        "dropped_record_count": bundle["summary"]["dropped_record_count"],
        "open_span_ids": bundle["summary"]["open_span_ids"],
    }


def build_synthetic_matrix() -> SyntheticMatrixResult:
    """Build and fail-closed evaluate the complete synthetic fixture matrix."""

    cases = {spec.case_id: run_synthetic_case(spec) for spec in CASE_SPECS}
    complete_cases = [
        case for case in cases.values() if not case.spec.crash_before_seal
    ]
    aa_assertions = {
        "phase_fingerprints_identical": (
            cases["aa-left"].fingerprint_ids == cases["aa-right"].fingerprint_ids
        ),
        "independent_run_ids": (
            cases["aa-left"].bundle["run"]["run_id"]
            != cases["aa-right"].bundle["run"]["run_id"]
        ),
    }
    observer_assertions = {
        "semantic_output_identical": (
            cases["observer-off"].semantic_output_sha256
            == cases["observer-on"].semantic_output_sha256
        ),
        "modeled_observer_identity_differs": (
            cases["observer-off"].bundle["run"]["environment"]["mod_set_sha256"]
            != cases["observer-on"].bundle["run"]["environment"]["mod_set_sha256"]
        ),
    }
    order_assertions = {
        "final_semantic_output_identical": (
            cases["order-forward"].semantic_output_sha256
            == cases["order-reverse"].semantic_output_sha256
        ),
        "chunk_schedule_differs": (
            cases["order-forward"].spec.chunk_order
            != cases["order-reverse"].spec.chunk_order
        ),
    }
    restart_before = cases["restart-before"]
    restart_after = cases["restart-after"]
    restart_assertions = {
        "phase_fingerprints_identical": (
            restart_before.fingerprint_ids == restart_after.fingerprint_ids
        ),
        "run_identities_distinct": (
            restart_before.bundle["run"]["run_id"]
            != restart_after.bundle["run"]["run_id"]
        ),
        "capture_identities_distinct": (
            restart_before.bundle["publication"]["completion_seal"]["capture_id"]
            != restart_after.bundle["publication"]["completion_seal"]["capture_id"]
        ),
    }
    crash = cases["crash-before-seal"].bundle
    crash_is_residue = bool(
        crash["publication"]["state"] == "incomplete"
        and crash["publication"]["completion_seal"] is None
        and crash["publication"]["crash_residue"] is not None
        and crash["summary"]["open_span_ids"]
    )
    comparisons = [
        _comparison("synthetic-comparison:aa", "aa-left", "aa-right", aa_assertions),
        _comparison(
            "synthetic-comparison:observer-off-on",
            "observer-off",
            "observer-on",
            observer_assertions,
        ),
        _comparison(
            "synthetic-comparison:chunk-order",
            "order-forward",
            "order-reverse",
            order_assertions,
        ),
        _comparison(
            "synthetic-comparison:server-restart",
            "restart-before",
            "restart-after",
            restart_assertions,
        ),
    ]
    required_gates = {
        "aa_phase_fingerprints_identical": aa_assertions[
            "phase_fingerprints_identical"
        ],
        "observer_off_on_semantic_output_identical": observer_assertions[
            "semantic_output_identical"
        ],
        "reverse_order_final_semantic_output_identical": order_assertions[
            "final_semantic_output_identical"
        ],
        "restart_phase_fingerprints_identical": restart_assertions[
            "phase_fingerprints_identical"
        ],
        "restart_run_identities_distinct": restart_assertions[
            "run_identities_distinct"
        ],
        "all_complete_captures_zero_drop_balanced_sealed": all(
            _completed_capture_is_healthy(case) for case in complete_cases
        ),
        "external_decorator_writes_exactly_attributed": all(
            _external_writes_are_exact(case) for case in complete_cases
        ),
        "standard_event_mutation_captured": all(
            _event_mutation_count(case) == len(REGION) for case in complete_cases
        ),
        "crash_has_residue_and_no_seal": crash_is_residue,
        "all_results_labeled_synthetic_contract_evidence": all(
            _all_records_labeled_synthetic(case) for case in cases.values()
        ),
    }
    if not all(required_gates.values()):
        failed = sorted(key for key, value in required_gates.items() if not value)
        raise AssertionError("synthetic contract matrix gate failure: " + ", ".join(failed))

    summary_without_id = {
        "schema_version": 1,
        "format": MATRIX_FORMAT,
        "evidence_class": EVIDENCE_CLASS,
        "disclaimer": SYNTHETIC_DISCLAIMER,
        "fixed_seed_sha256": _digest({"fixed_seed": FIXED_SEED}),
        "region": [{"x": x, "z": z} for x, z in REGION],
        "cases": {
            case_id: _case_summary(case)
            for case_id, case in sorted(cases.items())
        },
        "comparisons": comparisons,
        "required_gates": required_gates,
        "overall_outcome": "pass",
    }
    summary = {
        "matrix_id": MATRIX_PREFIX + _digest(summary_without_id),
        **summary_without_id,
    }
    return SyntheticMatrixResult(cases=cases, summary=summary)


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _expected_publication_files(matrix: SyntheticMatrixResult) -> dict[Path, bytes]:
    expected = {
        Path(case_summary["bundle_path"]): (
            canonical_json_bytes(matrix.cases[case_id].bundle) + b"\n"
        )
        for case_id, case_summary in matrix.summary["cases"].items()
    }
    expected[Path("matrix-summary.json")] = canonical_json_bytes(matrix.summary) + b"\n"
    return expected


def _publication_matches(target: Path, expected: Mapping[Path, bytes]) -> bool:
    actual_paths = {
        path.relative_to(target)
        for path in target.rglob("*")
        if path.is_file()
    }
    if actual_paths != set(expected):
        return False
    return all((target / relative).read_bytes() == value for relative, value in expected.items())


def publish_synthetic_matrix(
    matrix: SyntheticMatrixResult,
    output_root: Path | str = DEFAULT_OUTPUT_ROOT,
) -> Path:
    """Atomically publish all bundles and their summary as one directory.

    A deterministic matrix ID names the destination.  Rerunning an identical
    matrix is idempotent; a conflicting pre-existing directory is never
    overwritten.
    """

    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    matrix_digest = matrix.summary["matrix_id"].rsplit(":", 1)[1]
    target = root / f"matrix-{matrix_digest}"
    expected = _expected_publication_files(matrix)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=root))
    try:
        for relative, value in expected.items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative.suffixes[-2:] == [".bundle", ".json"]:
                case_id = relative.name.removesuffix(".bundle.json")
                write_bundle(destination, matrix.cases[case_id].bundle)
                if destination.read_bytes() != value:
                    raise AssertionError("atomic bundle bytes differ from matrix summary")
            else:
                _atomic_write_bytes(destination, value)

        if target.exists():
            if not _publication_matches(target, expected):
                raise FileExistsError(
                    f"synthetic matrix target exists with different content: {target}"
                )
            shutil.rmtree(staging)
            return target

        try:
            os.replace(staging, target)
        except OSError:
            if not target.exists() or not _publication_matches(target, expected):
                raise
            shutil.rmtree(staging)
        return target
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def published_summary(path: Path | str) -> dict[str, Any]:
    """Load a published matrix summary using strict duplicate-key handling."""

    def strict_pairs(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    summary_path = Path(path) / "matrix-summary.json"
    value = json.loads(summary_path.read_text(encoding="utf-8"), object_pairs_hook=strict_pairs)
    if not isinstance(value, dict) or value.get("format") != MATRIX_FORMAT:
        raise ValueError("not a synthetic Worldgen Observatory matrix summary")
    if value.get("evidence_class") != EVIDENCE_CLASS:
        raise ValueError("matrix summary lacks its synthetic evidence classification")
    return value
