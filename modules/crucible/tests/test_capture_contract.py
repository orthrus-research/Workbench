#!/usr/bin/env python3

"""Focused, dependency-free tests for the Worldgen Observatory capture contract."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
import unittest


MODULE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    MODULE_ROOT / "contracts" / "worldgen-observatory-capture-v1.md"
)
RECORD_SCHEMA_PATH = (
    MODULE_ROOT / "schemas" / "worldgen-observatory-record-v1.schema.json"
)
BUNDLE_SCHEMA_PATH = (
    MODULE_ROOT / "schemas" / "worldgen-observatory-bundle-v1.schema.json"
)

CONTRACT_ID = "WORKBENCH-CRUCIBLE-WORLDGEN-OBSERVATORY-CAPTURE-V1"
RECORD_FORMAT = "workbench-crucible-worldgen-observatory-record-v1"
BUNDLE_FORMAT = "workbench-crucible-worldgen-observatory-bundle-v1"
REQUIRED_FINGERPRINT_EXCLUSIONS = {
    "wall_clock",
    "monotonic_ns",
    "thread_key",
    "thread_sequence",
    "lamport",
    "timing_metrics",
    "runtime_object_identity",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AssertionError(f"JSON root is not an object: {path}")
    return value


RECORD_SCHEMA = _load_json(RECORD_SCHEMA_PATH)
BUNDLE_SCHEMA = _load_json(BUNDLE_SCHEMA_PATH)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _content_id(prefix: str, value: dict[str, Any], field: str) -> str:
    projected = copy.deepcopy(value)
    projected.pop(field, None)
    return prefix + _sha256(projected)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _shape_errors(
    value: Any,
    schema: dict[str, Any],
    path: str,
) -> set[str]:
    if not isinstance(value, dict):
        return {f"shape:{path}:object"}
    required = set(schema.get("required", []))
    properties = set(schema.get("properties", {}))
    errors = {
        f"shape:{path}:missing:{field}"
        for field in required - set(value)
    }
    if schema.get("additionalProperties") is False:
        errors.update(
            f"shape:{path}:extra:{field}"
            for field in set(value) - properties
        )
    return errors


def _payload_schema_by_record_type() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for condition in RECORD_SCHEMA["allOf"]:
        record_type = condition["if"]["properties"]["record_type"]["const"]
        reference = condition["then"]["properties"]["payload"]["$ref"]
        result[record_type] = RECORD_SCHEMA["$defs"][reference.rsplit("/", 1)[1]]
    return result


PAYLOAD_SCHEMAS = _payload_schema_by_record_type()


def _make_run(mode: str = "lossless-fixture") -> dict[str, Any]:
    run = {
        "run_id": "",
        "capture_mode": mode,
        "capture_plan_sha256": _digest(f"capture-plan:{mode}"),
        "fixture_id": "fixture:worldgen-contract",
        "fixture_sha256": _digest("fixture"),
        "environment": {
            "minecraft_version": "1.12.2",
            "platform_profile_id": "cleanroom-test-exact",
            "platform_profile_sha256": _digest("platform-profile"),
            "pack_profile_id": None,
            "pack_profile_sha256": None,
            "snapshot_id": "snapshot:test-worldgen-observatory",
            "physical_side": "DEDICATED_SERVER",
            "runtime_java": "25-test",
            "mapping_namespace": "stable-test-mappings",
            "transformed_runtime_sha256": _digest("transformed-runtime"),
            "mod_set_sha256": _digest("mod-set"),
            "configuration_set_sha256": _digest("configuration-set"),
        },
        "world": {
            "world_instance_id": "world:fixture-0",
            "world_seed_sha256": _digest("world-seed"),
            "world_type": "WORKBENCH_TEST",
            "generator_options_sha256": _digest("generator-options"),
            "dimension_ids": [0],
        },
    }
    run["run_id"] = _content_id(
        "crucible-worldgen-run:sha256:", run, "run_id"
    )
    return run


def _actor(binding: str = "exact") -> dict[str, Any]:
    if binding in {"exact", "workbench"}:
        return {
            "binding": binding,
            "mod_id": "workbench_observer" if binding == "workbench" else "testgen",
            "code_source_sha256": _digest(f"code:{binding}"),
            "class_name": "dev.workbench.fixture.TestGenerator",
            "method_name": "generateChunk",
            "method_descriptor": "(II)Lnet/minecraft/world/chunk/Chunk;",
            "mapping_namespace": "stable-test-mappings",
            "transformed_class_sha256": _digest(f"class:{binding}"),
            "candidate_mod_ids": [],
        }
    if binding == "ambiguous":
        return {
            "binding": binding,
            "mod_id": None,
            "code_source_sha256": None,
            "class_name": None,
            "method_name": None,
            "method_descriptor": None,
            "mapping_namespace": None,
            "transformed_class_sha256": None,
            "candidate_mod_ids": ["candidate_a", "candidate_b"],
        }
    return {
        "binding": "unbound",
        "mod_id": None,
        "code_source_sha256": None,
        "class_name": None,
        "method_name": None,
        "method_descriptor": None,
        "mapping_namespace": None,
        "transformed_class_sha256": None,
        "candidate_mod_ids": [],
    }


def _scope(run: dict[str, Any], *, in_world: bool) -> dict[str, Any]:
    environment = run["environment"]
    return {
        "run_id": run["run_id"],
        "platform_profile_id": environment["platform_profile_id"],
        "platform_profile_sha256": environment["platform_profile_sha256"],
        "pack_profile_id": environment["pack_profile_id"],
        "pack_profile_sha256": environment["pack_profile_sha256"],
        "snapshot_id": environment["snapshot_id"],
        "physical_side": environment["physical_side"],
        "world_instance_id": run["world"]["world_instance_id"] if in_world else None,
        "dimension_id": 0 if in_world else None,
        "chunk": {"x": 0, "z": 0} if in_world else None,
    }


def _outcome(
    state: str,
    *,
    exception: bool = False,
) -> dict[str, Any]:
    return {
        "state": state,
        "reason_code": "FIXTURE_THROW" if exception else None,
        "exception_class": "java.lang.IllegalStateException" if exception else None,
        "exception_message_sha256": _digest("fixture throw") if exception else None,
        "cancelled": state == "canceled",
        "event_result": None,
    }


def _record(
    run: dict[str, Any],
    ordinal: int,
    record_type: str,
    payload: dict[str, Any],
    *,
    state: str = "observed",
    span_id: str | None = None,
    parent_span_id: str | None = None,
    in_world: bool = True,
    binding: str = "exact",
    exception: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "format": RECORD_FORMAT,
        "contract_id": CONTRACT_ID,
        "record_type": record_type,
        "ordinal": ordinal,
        "scope": _scope(run, in_world=in_world),
        "causality": {
            "trace_id": "trace:chunk-0-0",
            "root_trigger_id": "trigger:fixture-route",
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "links": [],
        },
        "actor": _actor(binding),
        "order": {
            "thread_key": "server-thread",
            "thread_sequence": ordinal,
            "lamport": ordinal,
            "tick": 1,
            "monotonic_ns": 1_000_000 + ordinal,
            "happens_after": [] if ordinal == 0 else [ordinal - 1],
        },
        "outcome": _outcome(state, exception=exception),
        "coverage": {
            "mode": run["capture_mode"],
            "selection_id": "selection:all-fixture-chunks",
            "detail_state": "complete",
            "dropped_record_count": 0,
            "limitations": [],
        },
        "payload": payload,
    }


def _fingerprint(
    checkpoint: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    payload = checkpoint["payload"]
    value = {
        "fingerprint_id": "",
        "checkpoint_ordinal": checkpoint["ordinal"],
        "checkpoint_id": payload["checkpoint_id"],
        "canonicalization_id": payload["canonicalization_id"],
        "scope": {
            "comparison_scope_sha256": _sha256(
                {
                    "environment": run["environment"],
                    "world": run["world"],
                    "dimension_id": checkpoint["scope"]["dimension_id"],
                    "chunk": checkpoint["scope"]["chunk"],
                    "stage_id": payload["stage_id"],
                }
            ),
            "dimension_id": checkpoint["scope"]["dimension_id"],
            "chunk": copy.deepcopy(checkpoint["scope"]["chunk"]),
            "stage_id": payload["stage_id"],
        },
        "included_domains": [
            "biomes",
            "block_states",
            "chunk_flags",
            "heightmaps",
            "structures",
        ],
        "excluded_observation_fields": sorted(REQUIRED_FINGERPRINT_EXCLUSIONS),
        "semantic_state_sha256": payload["semantic_state_sha256"],
    }
    value["fingerprint_id"] = _content_id(
        "crucible-worldgen-fingerprint:sha256:",
        value,
        "fingerprint_id",
    )
    return value


def _completion_seal(bundle: dict[str, Any]) -> dict[str, Any]:
    seal = {
        "capture_id": "",
        "run_manifest_sha256": _sha256(bundle["run"]),
        "records_sha256": _sha256(bundle["records"]),
        "semantic_fingerprints_sha256": _sha256(
            bundle["semantic_fingerprints"]
        ),
        "record_count": len(bundle["records"]),
        "last_ordinal": bundle["records"][-1]["ordinal"],
        "sealed_after_stop_record": True,
    }
    identity = {
        "contract_id": CONTRACT_ID,
        "run_id": bundle["run"]["run_id"],
        "run_manifest_sha256": seal["run_manifest_sha256"],
        "records_sha256": seal["records_sha256"],
        "semantic_fingerprints_sha256": seal[
            "semantic_fingerprints_sha256"
        ],
        "record_count": seal["record_count"],
        "last_ordinal": seal["last_ordinal"],
    }
    seal["capture_id"] = (
        "crucible-worldgen-capture:sha256:" + _sha256(identity)
    )
    return seal


def _crash_residue(
    records: list[dict[str, Any]],
    open_span_ids: list[str],
) -> dict[str, Any]:
    residue = {
        "residue_id": "",
        "reason": "process_crash",
        "last_complete_ordinal": records[-1]["ordinal"],
        "open_span_ids": sorted(open_span_ids),
        "recoverable": True,
        "diagnostic_sha256": None,
    }
    residue["residue_id"] = _content_id(
        "crucible-worldgen-residue:sha256:", residue, "residue_id"
    )
    return residue


def _complete_bundle(*, terminal: str = "return") -> dict[str, Any]:
    run = _make_run()
    span_id = "span:generate-chunk-0-0"
    checkpoint_payload = {
        "checkpoint_id": "checkpoint:after-base-terrain-0-0",
        "stage_id": "stage:base-terrain",
        "canonicalization_id": "canonicalizer:semantic-chunk-v1",
        "semantic_state_sha256": _digest("semantic-state"),
    }
    records = [
        _record(
            run,
            0,
            "capture_control",
            {
                "action": "start",
                "capture_plan_sha256": run["capture_plan_sha256"],
                "prior_selection_id": None,
                "next_selection_id": "selection:all-fixture-chunks",
            },
            state="returned",
            in_world=False,
            binding="workbench",
        ),
        _record(
            run,
            1,
            "probe_health",
            {
                "hook_id": "hook:test-generate-chunk",
                "health_state": "reached",
                "target_class": "dev.workbench.fixture.TestGenerator",
                "target_method": "generateChunk",
                "target_descriptor": "(II)Lnet/minecraft/world/chunk/Chunk;",
                "original_class_sha256": _digest("original-class"),
                "transformed_class_sha256": _digest("class:workbench"),
                "expected_injection_count": 1,
                "observed_injection_count": 1,
            },
            in_world=False,
            binding="workbench",
        ),
        _record(
            run,
            2,
            "span_enter",
            {
                "span_kind": "generator_call",
                "operation_id": "operation:generate-chunk",
                "arguments_sha256": _digest("chunk-arguments"),
            },
            state="entered",
            span_id=span_id,
        ),
        _record(
            run,
            3,
            "decision",
            {
                "rule_id": "rule:biome-selection",
                "input_sha256": _digest("decision-input"),
                "decision": "selected",
                "output_sha256": _digest("decision-output"),
            },
            span_id=span_id,
        ),
        _record(
            run,
            4,
            "rng_observation",
            {
                "detail": "call",
                "stream_id": "rng:base-terrain",
                "algorithm_class": "java.util.Random",
                "operation": "nextInt",
                "call_ordinal": 0,
                "parameters_sha256": _digest("bound:16"),
                "result_sha256": _digest("result:7"),
                "rolling_digest": _digest("rng-rolling"),
            },
            span_id=span_id,
        ),
        _record(
            run,
            5,
            "block_write",
            {
                "write_chain_id": "write:base-terrain-0",
                "channel": "chunk_primer",
                "position": [0, 64, 0],
                "generation_chunk": {"x": 0, "z": 0},
                "target_chunk": {"x": 0, "z": 0},
                "before_state_sha256": _digest("minecraft:air"),
                "after_state_sha256": _digest("minecraft:stone"),
                "flags": None,
                "terminal": True,
            },
            span_id=span_id,
        ),
        _record(
            run,
            6,
            "checkpoint",
            checkpoint_payload,
            span_id=span_id,
        ),
    ]
    if terminal == "return":
        records.append(
            _record(
                run,
                7,
                "span_return",
                {
                    "span_kind": "generator_call",
                    "result_sha256": _digest("generated-chunk"),
                },
                state="returned",
                span_id=span_id,
            )
        )
        return_count = 1
        throw_count = 0
    elif terminal == "throw":
        records.append(
            _record(
                run,
                7,
                "span_throw",
                {
                    "span_kind": "generator_call",
                    "throwable_state_sha256": _digest("throwable-state"),
                },
                state="threw",
                span_id=span_id,
                exception=True,
            )
        )
        return_count = 0
        throw_count = 1
    else:
        raise AssertionError(f"unsupported terminal kind: {terminal}")
    records.append(
        _record(
            run,
            8,
            "capture_control",
            {
                "action": "stop",
                "capture_plan_sha256": run["capture_plan_sha256"],
                "prior_selection_id": "selection:all-fixture-chunks",
                "next_selection_id": "selection:all-fixture-chunks",
            },
            state="returned",
            in_world=False,
            binding="workbench",
        )
    )
    fingerprint = _fingerprint(records[6], run)
    bundle = {
        "schema_version": 1,
        "format": BUNDLE_FORMAT,
        "contract_id": CONTRACT_ID,
        "run": run,
        "records": records,
        "semantic_fingerprints": [fingerprint],
        "summary": {
            "record_count": len(records),
            "last_ordinal": records[-1]["ordinal"],
            "span_enter_count": 1,
            "span_return_count": return_count,
            "span_throw_count": throw_count,
            "open_span_ids": [],
            "coverage_state": "complete",
            "dropped_record_count": 0,
            "limitations": [],
        },
        "publication": {
            "state": "completed",
            "completion_seal": None,
            "crash_residue": None,
        },
    }
    bundle["publication"]["completion_seal"] = _completion_seal(bundle)
    return bundle


def _incomplete_crash_bundle() -> dict[str, Any]:
    run = _make_run("trace")
    span_id = "span:generate-chunk-crashed"
    records = [
        _record(
            run,
            0,
            "capture_control",
            {
                "action": "start",
                "capture_plan_sha256": run["capture_plan_sha256"],
                "prior_selection_id": None,
                "next_selection_id": "selection:all-fixture-chunks",
            },
            state="returned",
            in_world=False,
            binding="workbench",
        ),
        _record(
            run,
            1,
            "span_enter",
            {
                "span_kind": "generator_call",
                "operation_id": "operation:generate-chunk",
                "arguments_sha256": _digest("crash-arguments"),
            },
            state="entered",
            span_id=span_id,
        ),
        _record(
            run,
            2,
            "decision",
            {
                "rule_id": "rule:crash-boundary",
                "input_sha256": _digest("crash-decision-input"),
                "decision": "accepted",
                "output_sha256": _digest("crash-decision-output"),
            },
            span_id=span_id,
        ),
    ]
    open_spans = [span_id]
    return {
        "schema_version": 1,
        "format": BUNDLE_FORMAT,
        "contract_id": CONTRACT_ID,
        "run": run,
        "records": records,
        "semantic_fingerprints": [],
        "summary": {
            "record_count": len(records),
            "last_ordinal": records[-1]["ordinal"],
            "span_enter_count": 1,
            "span_return_count": 0,
            "span_throw_count": 0,
            "open_span_ids": open_spans,
            "coverage_state": "incomplete",
            "dropped_record_count": 0,
            "limitations": [
                "Process terminated before span closure and capture stop."
            ],
        },
        "publication": {
            "state": "incomplete",
            "completion_seal": None,
            "crash_residue": _crash_residue(records, open_spans),
        },
    }


def _capture_identity(bundle: dict[str, Any]) -> str:
    seal = bundle["publication"]["completion_seal"]
    identity = {
        "contract_id": CONTRACT_ID,
        "run_id": bundle["run"]["run_id"],
        "run_manifest_sha256": seal["run_manifest_sha256"],
        "records_sha256": seal["records_sha256"],
        "semantic_fingerprints_sha256": seal[
            "semantic_fingerprints_sha256"
        ],
        "record_count": seal["record_count"],
        "last_ordinal": seal["last_ordinal"],
    }
    return "crucible-worldgen-capture:sha256:" + _sha256(identity)


def _bundle_errors(bundle: dict[str, Any]) -> set[str]:
    errors = _shape_errors(bundle, BUNDLE_SCHEMA, "bundle")
    if errors:
        return errors
    if bundle.get("schema_version") != 1:
        errors.add("bundle-schema-version")
    if bundle.get("format") != BUNDLE_FORMAT:
        errors.add("bundle-format")
    if bundle.get("contract_id") != CONTRACT_ID:
        errors.add("bundle-contract-id")

    run = bundle["run"]
    errors.update(_shape_errors(run, BUNDLE_SCHEMA["$defs"]["run"], "run"))
    if errors:
        return errors
    errors.update(
        _shape_errors(
            run["environment"],
            BUNDLE_SCHEMA["$defs"]["environment"],
            "run.environment",
        )
    )
    errors.update(
        _shape_errors(
            run["world"],
            BUNDLE_SCHEMA["$defs"]["world"],
            "run.world",
        )
    )
    if run["run_id"] != _content_id(
        "crucible-worldgen-run:sha256:", run, "run_id"
    ):
        errors.add("run-id")
    if run["environment"]["minecraft_version"] != "1.12.2":
        errors.add("minecraft-version")
    pack_id = run["environment"]["pack_profile_id"]
    pack_sha = run["environment"]["pack_profile_sha256"]
    if (pack_id is None) != (pack_sha is None):
        errors.add("pack-profile-pair")
    dimensions = run["world"]["dimension_ids"]
    if dimensions != sorted(set(dimensions)) or not dimensions:
        errors.add("dimension-order")

    records = bundle["records"]
    if not isinstance(records, list) or not records:
        errors.add("records-empty")
        return errors
    ordinals = [record.get("ordinal") for record in records]
    if ordinals != list(range(len(records))):
        errors.add("record-append-order")

    record_required = set(RECORD_SCHEMA["required"])
    record_properties = set(RECORD_SCHEMA["properties"])
    thread_next: dict[str, int] = {}
    thread_last_time: dict[str, int] = {}
    thread_stacks: dict[str, list[str]] = {}
    span_entries: dict[str, dict[str, Any]] = {}
    span_terminals: dict[str, dict[str, Any]] = {}
    dropped_detail_total = 0
    coverage_drops: list[int] = []

    expected_scope = {
        "run_id": run["run_id"],
        "platform_profile_id": run["environment"]["platform_profile_id"],
        "platform_profile_sha256": run["environment"][
            "platform_profile_sha256"
        ],
        "pack_profile_id": run["environment"]["pack_profile_id"],
        "pack_profile_sha256": run["environment"]["pack_profile_sha256"],
        "snapshot_id": run["environment"]["snapshot_id"],
        "physical_side": run["environment"]["physical_side"],
    }
    runtime_scoped_types = {
        "decision",
        "rng_observation",
        "block_write",
        "chunk_access",
        "checkpoint",
    }
    state_by_terminal = {
        "span_enter": "entered",
        "span_return": "returned",
        "span_throw": "threw",
        "dropped_detail": "incomplete",
    }

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.add(f"record:{index}:object")
            continue
        missing = record_required - set(record)
        extra = set(record) - record_properties
        errors.update(f"record:{index}:missing:{field}" for field in missing)
        errors.update(f"record:{index}:extra:{field}" for field in extra)
        if missing:
            continue
        if record["schema_version"] != 1:
            errors.add(f"record:{index}:schema-version")
        if record["format"] != RECORD_FORMAT:
            errors.add(f"record:{index}:format")
        if record["contract_id"] != CONTRACT_ID:
            errors.add(f"record:{index}:contract-id")
        record_type = record["record_type"]
        payload_schema = PAYLOAD_SCHEMAS.get(record_type)
        if payload_schema is None:
            errors.add(f"record:{index}:type")
        else:
            errors.update(
                _shape_errors(record["payload"], payload_schema, f"record:{index}:payload")
            )
        for field, value in expected_scope.items():
            if record["scope"].get(field) != value:
                errors.add(f"record:{index}:scope:{field}")
        if record["coverage"].get("mode") != run["capture_mode"]:
            errors.add(f"record:{index}:coverage-mode")
        coverage_drops.append(record["coverage"].get("dropped_record_count", -1))

        actor = record["actor"]
        errors.update(
            _shape_errors(actor, RECORD_SCHEMA["$defs"]["actor"], f"record:{index}:actor")
        )
        exact_fields = (
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
            "transformed_class_sha256",
        )
        if actor.get("binding") in {"exact", "workbench"}:
            if any(actor.get(field) is None for field in exact_fields):
                errors.add(f"record:{index}:actor-exact-binding")
            if actor.get("candidate_mod_ids"):
                errors.add(f"record:{index}:actor-exact-candidates")
        elif actor.get("binding") == "unbound":
            if any(actor.get(field) is not None for field in exact_fields):
                errors.add(f"record:{index}:actor-unbound-claim")
            if actor.get("candidate_mod_ids"):
                errors.add(f"record:{index}:actor-unbound-candidates")
        elif actor.get("binding") == "ambiguous":
            if not actor.get("candidate_mod_ids"):
                errors.add(f"record:{index}:actor-ambiguous-empty")
        else:
            errors.add(f"record:{index}:actor-binding")

        expected_state = state_by_terminal.get(record_type)
        if expected_state is not None and record["outcome"].get("state") != expected_state:
            errors.add(f"record:{index}:outcome-state")
        if record_type == "span_throw":
            if record["outcome"].get("exception_class") is None:
                errors.add(f"record:{index}:throw-exception")
        elif record["outcome"].get("state") != "threw" and any(
            record["outcome"].get(field) is not None
            for field in ("exception_class", "exception_message_sha256")
        ):
            errors.add(f"record:{index}:unexpected-exception")

        order = record["order"]
        thread_key = order.get("thread_key")
        expected_sequence = thread_next.get(thread_key, 0)
        if order.get("thread_sequence") != expected_sequence:
            errors.add(f"record:{index}:thread-sequence")
        thread_next[thread_key] = expected_sequence + 1
        prior_time = thread_last_time.get(thread_key, -1)
        if order.get("monotonic_ns", -1) < prior_time:
            errors.add(f"record:{index}:monotonic-order")
        thread_last_time[thread_key] = order.get("monotonic_ns", -1)
        if any(
            not isinstance(prior, int) or prior < 0 or prior >= index
            for prior in order.get("happens_after", [])
        ):
            errors.add(f"record:{index}:happens-after")

        causality = record["causality"]
        span_id = causality.get("span_id")
        parent_span_id = causality.get("parent_span_id")
        stack = thread_stacks.setdefault(thread_key, [])
        if record_type == "span_enter":
            if span_id is None or span_id in span_entries:
                errors.add(f"record:{index}:span-enter-id")
            else:
                if stack and parent_span_id != stack[-1]:
                    errors.add(f"record:{index}:span-parent")
                if not stack and parent_span_id is not None and not causality.get("links"):
                    errors.add(f"record:{index}:async-parent-link")
                span_entries[span_id] = record
                stack.append(span_id)
        elif record_type in {"span_return", "span_throw"}:
            if span_id not in span_entries:
                errors.add(f"record:{index}:terminal-without-enter")
            if span_id in span_terminals:
                errors.add(f"record:{index}:duplicate-terminal")
            if not stack or stack[-1] != span_id:
                errors.add(f"record:{index}:span-nesting")
            else:
                stack.pop()
            if span_id in span_entries:
                entry_kind = span_entries[span_id]["payload"].get("span_kind")
                if record["payload"].get("span_kind") != entry_kind:
                    errors.add(f"record:{index}:span-kind")
            if span_id is not None:
                span_terminals[span_id] = record
        elif span_id is not None and (not stack or stack[-1] != span_id):
            errors.add(f"record:{index}:inactive-span")

        if record_type in runtime_scoped_types:
            if (
                record["scope"].get("world_instance_id") is None
                or record["scope"].get("dimension_id") is None
                or record["scope"].get("chunk") is None
            ):
                errors.add(f"record:{index}:runtime-scope")
        if record_type == "dropped_detail":
            dropped_detail_total += record["payload"].get("dropped_count", 0)

    open_spans = sorted(set(span_entries) - set(span_terminals))
    summary = bundle["summary"]
    errors.update(
        _shape_errors(summary, BUNDLE_SCHEMA["$defs"]["summary"], "summary")
    )
    if summary.get("record_count") != len(records):
        errors.add("summary-record-count")
    if summary.get("last_ordinal") != records[-1]["ordinal"]:
        errors.add("summary-last-ordinal")
    expected_counts = {
        "span_enter_count": sum(row["record_type"] == "span_enter" for row in records),
        "span_return_count": sum(row["record_type"] == "span_return" for row in records),
        "span_throw_count": sum(row["record_type"] == "span_throw" for row in records),
    }
    for field, value in expected_counts.items():
        if summary.get(field) != value:
            errors.add(f"summary-{field}")
    if summary.get("open_span_ids") != open_spans:
        errors.add("summary-open-spans")
    if coverage_drops and any(
        right < left for left, right in zip(coverage_drops, coverage_drops[1:])
    ):
        errors.add("coverage-drop-regression")
    final_drop_count = coverage_drops[-1] if coverage_drops else 0
    if summary.get("dropped_record_count") != final_drop_count:
        errors.add("summary-drop-count")
    if dropped_detail_total != summary.get("dropped_record_count"):
        errors.add("dropped-detail-total")
    if run["capture_mode"] == "lossless-fixture":
        if summary.get("dropped_record_count") != 0:
            errors.add("lossless-dropped")
        if any(row["record_type"] == "dropped_detail" for row in records):
            errors.add("lossless-drop-record")
        if any(
            row["coverage"].get("detail_state") != "complete" for row in records
        ):
            errors.add("lossless-detail-state")

    fingerprint_ids: set[str] = set()
    for index, fingerprint in enumerate(bundle["semantic_fingerprints"]):
        errors.update(
            _shape_errors(
                fingerprint,
                BUNDLE_SCHEMA["$defs"]["semanticFingerprint"],
                f"fingerprint:{index}",
            )
        )
        if fingerprint.get("fingerprint_id") != _content_id(
            "crucible-worldgen-fingerprint:sha256:",
            fingerprint,
            "fingerprint_id",
        ):
            errors.add(f"fingerprint:{index}:id")
        if fingerprint.get("fingerprint_id") in fingerprint_ids:
            errors.add(f"fingerprint:{index}:duplicate")
        fingerprint_ids.add(fingerprint.get("fingerprint_id"))
        included = fingerprint.get("included_domains", [])
        if included != sorted(set(included)) or not included:
            errors.add(f"fingerprint:{index}:included-domains")
        excluded = fingerprint.get("excluded_observation_fields", [])
        if excluded != sorted(set(excluded)) or set(excluded) != REQUIRED_FINGERPRINT_EXCLUSIONS:
            errors.add(f"fingerprint:{index}:exclusions")
        checkpoint_ordinal = fingerprint.get("checkpoint_ordinal")
        if not isinstance(checkpoint_ordinal, int) or not (
            0 <= checkpoint_ordinal < len(records)
        ):
            errors.add(f"fingerprint:{index}:checkpoint-ordinal")
            continue
        checkpoint = records[checkpoint_ordinal]
        if checkpoint.get("record_type") != "checkpoint":
            errors.add(f"fingerprint:{index}:checkpoint-type")
            continue
        payload = checkpoint["payload"]
        if fingerprint.get("checkpoint_id") != payload.get("checkpoint_id"):
            errors.add(f"fingerprint:{index}:checkpoint-id")
        if fingerprint.get("canonicalization_id") != payload.get("canonicalization_id"):
            errors.add(f"fingerprint:{index}:canonicalization")
        if fingerprint.get("semantic_state_sha256") != payload.get(
            "semantic_state_sha256"
        ):
            errors.add(f"fingerprint:{index}:semantic-state")
        fingerprint_scope = fingerprint.get("scope", {})
        if fingerprint_scope.get("dimension_id") != checkpoint["scope"].get(
            "dimension_id"
        ):
            errors.add(f"fingerprint:{index}:dimension")
        if fingerprint_scope.get("chunk") != checkpoint["scope"].get("chunk"):
            errors.add(f"fingerprint:{index}:chunk")
        if fingerprint_scope.get("stage_id") != payload.get("stage_id"):
            errors.add(f"fingerprint:{index}:stage")

    publication = bundle["publication"]
    errors.update(
        _shape_errors(
            publication,
            BUNDLE_SCHEMA["$defs"]["publication"],
            "publication",
        )
    )
    if publication.get("state") == "completed":
        seal = publication.get("completion_seal")
        if not isinstance(seal, dict):
            errors.add("completed-seal")
        else:
            errors.update(
                _shape_errors(
                    seal,
                    BUNDLE_SCHEMA["$defs"]["completionSeal"],
                    "completion-seal",
                )
            )
            expected_components = {
                "run_manifest_sha256": _sha256(run),
                "records_sha256": _sha256(records),
                "semantic_fingerprints_sha256": _sha256(
                    bundle["semantic_fingerprints"]
                ),
                "record_count": len(records),
                "last_ordinal": records[-1]["ordinal"],
                "sealed_after_stop_record": True,
            }
            for field, value in expected_components.items():
                if seal.get(field) != value:
                    errors.add(f"seal-{field}")
            if seal.get("capture_id") != _capture_identity(bundle):
                errors.add("capture-id")
        if publication.get("crash_residue") is not None:
            errors.add("completed-residue")
        if open_spans:
            errors.add("completed-open-spans")
        if not bundle["semantic_fingerprints"]:
            errors.add("completed-fingerprints")
        last = records[-1]
        if (
            last.get("record_type") != "capture_control"
            or last.get("payload", {}).get("action") != "stop"
            or last.get("outcome", {}).get("state") != "returned"
        ):
            errors.add("completed-stop-record")
        if summary.get("coverage_state") == "incomplete":
            errors.add("completed-summary-state")
    elif publication.get("state") == "incomplete":
        if publication.get("completion_seal") is not None:
            errors.add("incomplete-seal")
        residue = publication.get("crash_residue")
        if not isinstance(residue, dict):
            errors.add("incomplete-residue")
        else:
            errors.update(
                _shape_errors(
                    residue,
                    BUNDLE_SCHEMA["$defs"]["crashResidue"],
                    "crash-residue",
                )
            )
            if residue.get("residue_id") != _content_id(
                "crucible-worldgen-residue:sha256:", residue, "residue_id"
            ):
                errors.add("residue-id")
            if residue.get("last_complete_ordinal") != records[-1]["ordinal"]:
                errors.add("residue-last-ordinal")
            if residue.get("open_span_ids") != open_spans:
                errors.add("residue-open-spans")
        if summary.get("coverage_state") != "incomplete":
            errors.add("incomplete-summary-state")
        if not summary.get("limitations"):
            errors.add("incomplete-limitations")
    else:
        errors.add("publication-state")
    return errors


class WorldgenObservatoryCaptureContractTests(unittest.TestCase):
    def assert_valid(self, bundle: dict[str, Any]) -> None:
        self.assertEqual(_bundle_errors(bundle), set())

    def test_contract_and_schemas_are_closed_and_self_contained(self) -> None:
        contract = CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn(f"Contract ID: `{CONTRACT_ID}`", contract)
        self.assertEqual(RECORD_SCHEMA["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(BUNDLE_SCHEMA["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(RECORD_SCHEMA["additionalProperties"])
        self.assertFalse(BUNDLE_SCHEMA["additionalProperties"])
        record_reference = BUNDLE_SCHEMA["properties"]["records"]["items"]["$ref"]
        self.assertEqual(record_reference, RECORD_SCHEMA["$id"])
        admitted_types = set(RECORD_SCHEMA["properties"]["record_type"]["enum"])
        self.assertEqual(set(PAYLOAD_SCHEMAS), admitted_types)

    def test_complete_lossless_return_bundle_is_valid(self) -> None:
        self.assert_valid(_complete_bundle())

    def test_complete_lossless_throw_bundle_balances_span(self) -> None:
        bundle = _complete_bundle(terminal="throw")
        self.assert_valid(bundle)
        self.assertEqual(bundle["summary"]["span_throw_count"], 1)
        self.assertEqual(bundle["summary"]["open_span_ids"], [])

    def test_crash_residue_accepts_exact_reported_open_span(self) -> None:
        bundle = _incomplete_crash_bundle()
        self.assert_valid(bundle)
        self.assertIsNone(bundle["publication"]["completion_seal"])
        self.assertEqual(
            bundle["publication"]["crash_residue"]["open_span_ids"],
            bundle["summary"]["open_span_ids"],
        )

    def test_completed_bundle_rejects_open_or_unentered_spans(self) -> None:
        open_bundle = _complete_bundle()
        del open_bundle["records"][7]
        for ordinal, record in enumerate(open_bundle["records"]):
            record["ordinal"] = ordinal
            record["order"]["thread_sequence"] = ordinal
            record["order"]["lamport"] = ordinal
            record["order"]["happens_after"] = [] if ordinal == 0 else [ordinal - 1]
        self.assertIn("completed-open-spans", _bundle_errors(open_bundle))

        terminal_bundle = _complete_bundle()
        terminal_bundle["records"][2]["record_type"] = "span_return"
        terminal_bundle["records"][2]["outcome"] = _outcome("returned")
        terminal_bundle["records"][2]["payload"] = {
            "span_kind": "generator_call",
            "result_sha256": _digest("no-enter"),
        }
        self.assertTrue(
            {
                "record:2:terminal-without-enter",
                "completed-open-spans",
            }
            & _bundle_errors(terminal_bundle)
        )

    def test_lossless_fixture_rejects_drop_or_truncation(self) -> None:
        bundle = _complete_bundle()
        bundle["records"][4]["coverage"]["detail_state"] = "truncated"
        self.assertIn("lossless-detail-state", _bundle_errors(bundle))

        bundle = _complete_bundle()
        bundle["records"][-1]["coverage"]["dropped_record_count"] = 1
        bundle["summary"]["dropped_record_count"] = 1
        self.assertIn("lossless-dropped", _bundle_errors(bundle))

    def test_append_order_and_forward_happens_after_fail_closed(self) -> None:
        bundle = _complete_bundle()
        bundle["records"][4]["ordinal"] = 7
        self.assertIn("record-append-order", _bundle_errors(bundle))

        bundle = _complete_bundle()
        bundle["records"][4]["order"]["happens_after"] = [5]
        self.assertIn("record:4:happens-after", _bundle_errors(bundle))

    def test_fingerprint_digest_scope_and_exclusions_fail_closed(self) -> None:
        bundle = _complete_bundle()
        bundle["semantic_fingerprints"][0]["excluded_observation_fields"].remove(
            "runtime_object_identity"
        )
        self.assertIn("fingerprint:0:exclusions", _bundle_errors(bundle))

        bundle = _complete_bundle()
        bundle["semantic_fingerprints"][0]["semantic_state_sha256"] = _digest(
            "mutated-state"
        )
        errors = _bundle_errors(bundle)
        self.assertIn("fingerprint:0:id", errors)
        self.assertIn("fingerprint:0:semantic-state", errors)

    def test_completion_seal_and_crash_residue_are_mutually_exclusive(self) -> None:
        completed = _complete_bundle()
        completed["publication"]["crash_residue"] = _crash_residue(
            completed["records"], []
        )
        self.assertIn("completed-residue", _bundle_errors(completed))

        incomplete = _incomplete_crash_bundle()
        temporary = _complete_bundle()
        incomplete["publication"]["completion_seal"] = temporary[
            "publication"
        ]["completion_seal"]
        self.assertIn("incomplete-seal", _bundle_errors(incomplete))

    def test_sealed_component_mutation_invalidates_capture(self) -> None:
        bundle = _complete_bundle()
        bundle["records"][3]["payload"]["decision"] = "rejected"
        errors = _bundle_errors(bundle)
        self.assertIn("seal-records_sha256", errors)

        bundle["publication"]["completion_seal"]["records_sha256"] = _sha256(
            bundle["records"]
        )
        errors = _bundle_errors(bundle)
        self.assertIn("capture-id", errors)


if __name__ == "__main__":
    unittest.main()
