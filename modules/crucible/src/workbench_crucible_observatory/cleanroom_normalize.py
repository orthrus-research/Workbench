"""Normalize admitted Cleanroom raw rows into the generic Crucible contract.

Raw admission proves transport shape and candidate-plan binding.  This module
is the separate publication boundary: it removes candidate-only fields, binds
actors only when independently supplied runtime evidence corroborates them,
and leaves a crash prefix as incomplete residue rather than a capture.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping

from .bundle import (
    FINGERPRINT_PREFIX,
    OBSERVATION_EXCLUSIONS,
    BundleBuilder,
    CaptureValidationError,
    _ValidatedBundlePublication,
    _release_validated_bundle_publication,
    canonical_json_bytes,
    exact_actor,
    unbound_actor,
)
from .cleanroom_raw import RawAdmission


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_QUALIFIED_ID_RE = re.compile(r"^[a-z][a-z0-9._-]*:[A-Za-z0-9._:-]+$")
_PLAN_PREFIX = "cleanroom-worldgen-observatory-probe-plan:sha256:"

# These are generic contract identities, not Cleanroom raw hook names.  The
# supplied candidate probe plan is the only authority allowed to connect one
# of its raw hooks to one of these roles.
_CANONICAL_ROLES = {
    "block_write.chunk_primer": (
        "worldgen-hook:chunk-primer-set-block-state",
        "net.minecraft.world.chunk.ChunkPrimer",
        "setBlockState",
        "(IIILnet/minecraft/block/state/IBlockState;)V",
    ),
    "block_write.world_api": (
        "worldgen-hook:world-set-block-state",
        "net.minecraft.world.World",
        "setBlockState",
        "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;I)Z",
    ),
    "block_write.chunk_storage": (
        "worldgen-hook:chunk-set-block-state",
        "net.minecraft.world.chunk.Chunk",
        "setBlockState",
        "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)"
        "Lnet/minecraft/block/state/IBlockState;",
    ),
    "event_dispatch.bus_post": (
        "worldgen-hook:event-bus-post",
        "net.minecraftforge.fml.common.eventhandler.EventBus",
        "post",
        "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
    ),
    "event_dispatch.per_listener_callsite": (
        "worldgen-hook:event-listener-invoke",
        "net.minecraftforge.fml.common.eventhandler.EventBus",
        "post",
        "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
    ),
}

_RAW_ACTOR_KEYS = {
    "binding",
    "mod_id",
    "class_name",
    "method_name",
    "method_descriptor",
    "mapping_namespace",
    "code_source_sha256",
    "transformed_class_sha256",
}
_INVENTORY_KEYS = {
    "mod_id",
    "code_source_sha256",
    "class_name",
    "method_name",
    "method_descriptor",
    "mapping_namespace",
}
_ROW_KEYS = {
    "format",
    "record_type",
    "sequence",
    "capture_id",
    "scope",
    "causality",
    "actor",
    "order",
    "outcome",
    "coverage",
    "payload",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _closed(value: Any, keys: set[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    _require(set(value) == keys, f"{context} fields do not match its contract")
    return value


def _string(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _integer(value: Any, context: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{context} must be an integer",
    )
    return value


def _boolean(value: Any, context: str) -> bool:
    _require(isinstance(value, bool), f"{context} must be a boolean")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _content_id(prefix: str, value: Any) -> str:
    return prefix + _digest(value)


def _qualified(prefix: str, value: Any) -> str:
    raw = _string(value, prefix + " identity")
    if _QUALIFIED_ID_RE.fullmatch(raw):
        return raw
    if re.fullmatch(r"[A-Za-z0-9._:-]+", raw):
        candidate = prefix + ":" + raw
        if _QUALIFIED_ID_RE.fullmatch(candidate):
            return candidate
    return prefix + ":sha256:" + _digest(raw)


class StrictActorBinder:
    """Bind raw actor leads only against an exact inventory and final class dump.

    A raw ``transformed_class_sha256`` is an intermediate plugin-stage lead. It
    is syntax-checked above but is not a claim about LaunchWrapper's final
    transformed bytes, which may legitimately differ after later transformers.
    """

    def __init__(
        self,
        actor_inventory: Iterable[Mapping[str, Any]],
        class_dump_sha256: Mapping[str, str],
    ) -> None:
        _require(
            isinstance(class_dump_sha256, Mapping),
            "transformed class dump inventory must be an object",
        )
        self._class_dumps: dict[str, str] = {}
        for class_name, digest in class_dump_sha256.items():
            name = _string(class_name, "class dump class name")
            _require(name not in self._class_dumps, "duplicate class dump identity")
            self._class_dumps[name] = _sha256(digest, f"class dump {name}")

        self._inventory: dict[tuple[str, ...], Mapping[str, Any]] = {}
        self._inventory_by_lead: dict[
            tuple[str, ...], list[Mapping[str, Any]]
        ] = defaultdict(list)
        for index, raw in enumerate(actor_inventory):
            item = _closed(raw, _INVENTORY_KEYS, f"actor inventory row {index}")
            normalized = {
                field: _string(item[field], f"actor inventory row {index} {field}")
                for field in _INVENTORY_KEYS
            }
            normalized["code_source_sha256"] = _sha256(
                normalized["code_source_sha256"],
                f"actor inventory row {index} code source",
            )
            key = self._key(normalized)
            _require(key not in self._inventory, "duplicate exact actor inventory row")
            self._inventory[key] = normalized
            self._inventory_by_lead[self._lead_key(normalized)].append(normalized)

    @staticmethod
    def _key(actor: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(
            str(actor[field])
            for field in (
                "mod_id",
                "code_source_sha256",
                "class_name",
                "method_name",
                "method_descriptor",
                "mapping_namespace",
            )
        )

    @staticmethod
    def _lead_key(actor: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(
            str(actor[field])
            for field in (
                "code_source_sha256",
                "class_name",
                "method_name",
                "method_descriptor",
                "mapping_namespace",
            )
        )

    def bind(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        actor = _closed(raw, _RAW_ACTOR_KEYS, "raw actor")
        _string(actor["binding"], "raw actor binding lead")
        for field in (
            "mod_id",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
            "code_source_sha256",
            "transformed_class_sha256",
        ):
            _require(
                actor[field] is None or isinstance(actor[field], str),
                f"raw actor {field} must be a string or null",
            )
        if actor["code_source_sha256"] is not None:
            _sha256(actor["code_source_sha256"], "raw actor code source")
        if actor["transformed_class_sha256"] is not None:
            _sha256(actor["transformed_class_sha256"], "raw actor transformed class")

        exact_fields = all(
            isinstance(actor[field], str) and bool(actor[field])
            for field in _INVENTORY_KEYS - {"mod_id"}
        )
        if exact_fields:
            matches = self._inventory_by_lead.get(self._lead_key(actor), [])
            if actor["mod_id"] is not None:
                matches = [
                    inventory
                    for inventory in matches
                    if inventory["mod_id"] == actor["mod_id"]
                ]
            inventory = matches[0] if len(matches) == 1 else None
            class_digest = self._class_dumps.get(str(actor["class_name"]))
            if inventory is not None and class_digest is not None:
                return exact_actor(
                    mod_id=str(inventory["mod_id"]),
                    code_source_sha256=str(inventory["code_source_sha256"]),
                    class_name=str(inventory["class_name"]),
                    method_name=str(inventory["method_name"]),
                    method_descriptor=str(inventory["method_descriptor"]),
                    mapping_namespace=str(inventory["mapping_namespace"]),
                    transformed_class_sha256=class_digest,
                    workbench=(
                        actor["binding"] == "workbench"
                        and str(inventory["mod_id"]).startswith("workbench")
                    ),
                )

        candidates: set[str] = set()
        if isinstance(actor["mod_id"], str) and actor["mod_id"]:
            candidates.add(actor["mod_id"])
        for inventory in self._inventory.values():
            source_match = (
                actor["code_source_sha256"] is not None
                and inventory["code_source_sha256"] == actor["code_source_sha256"]
            )
            class_match = (
                actor["class_name"] is not None
                and inventory["class_name"] == actor["class_name"]
            )
            if source_match or class_match:
                candidates.add(str(inventory["mod_id"]))
        return unbound_actor(candidate_mod_ids=candidates)

    def final_class_digest(self, class_name: str) -> str:
        """Return independently dumped definition bytes for one target class."""

        name = _string(class_name, "final transformed class name")
        digest = self._class_dumps.get(name)
        _require(
            digest is not None,
            f"final transformed class dump is missing for {name}",
        )
        return digest


def _probe_plan_hooks(
    probe_plan: Mapping[str, Any], admission: RawAdmission
) -> dict[str, dict[str, Any]]:
    _require(isinstance(probe_plan, Mapping), "probe plan must be an object")
    plan_id = probe_plan.get("plan_id")
    material = deepcopy(dict(probe_plan))
    material.pop("plan_id", None)
    _require(
        plan_id == _PLAN_PREFIX + _digest(material),
        "probe plan content identity mismatch",
    )
    _require(plan_id == admission.probe_plan_id, "raw admission probe plan mismatch")
    values = probe_plan.get("hooks")
    _require(isinstance(values, list) and bool(values), "probe plan has no hooks")
    hooks: dict[str, dict[str, Any]] = {}
    roles: set[str] = set()
    for index, value in enumerate(values):
        _require(isinstance(value, Mapping), f"probe plan hook {index} is not an object")
        raw_hook_id = _string(value.get("raw_hook_id"), f"probe plan hook {index} ID")
        _require(raw_hook_id not in hooks, f"probe plan duplicates {raw_hook_id}")
        role = value.get("canonical_role")
        _require(role is None or isinstance(role, str), f"invalid role for {raw_hook_id}")
        if role is not None:
            _require(role in _CANONICAL_ROLES, f"unknown canonical probe role {role}")
            _require(role not in roles, f"duplicate canonical probe role {role}")
            roles.add(role)
        target = tuple(
            _string(value.get(field), f"probe plan {raw_hook_id} {field}")
            for field in ("target_class", "target_method", "target_descriptor")
        )
        cardinality = _integer(
            value.get("expected_cardinality"),
            f"probe plan {raw_hook_id} cardinality",
        )
        _require(cardinality is not None and cardinality > 0, "invalid hook cardinality")
        if role is not None:
            _require(
                target == _CANONICAL_ROLES[role][1:],
                f"canonical probe role target mismatch for {role}",
            )
        hooks[raw_hook_id] = {
            "raw_hook_id": raw_hook_id,
            "canonical_role": role,
            "canonical_hook_id": (
                raw_hook_id if role is None else _CANONICAL_ROLES[role][0]
            ),
            "target_class": target[0],
            "target_method": target[1],
            "target_descriptor": target[2],
            "expected_cardinality": cardinality,
        }
    _require(
        roles == set(_CANONICAL_ROLES),
        "probe plan does not bind every canonical query-closing role",
    )
    return hooks


def _scope(row: Mapping[str, Any]) -> tuple[int | None, tuple[int, int] | None]:
    raw = _closed(
        row["scope"],
        {"dimension_id", "chunk_x", "chunk_z"},
        f"raw row {row['sequence']} scope",
    )
    dimension = _integer(raw["dimension_id"], "raw dimension", nullable=True)
    chunk_x = _integer(raw["chunk_x"], "raw chunk x", nullable=True)
    chunk_z = _integer(raw["chunk_z"], "raw chunk z", nullable=True)
    _require(
        (chunk_x is None) == (chunk_z is None),
        "raw chunk coordinates must be present together",
    )
    _require(chunk_x is None or dimension is not None, "raw chunk lacks a dimension")
    return dimension, None if chunk_x is None else (chunk_x, int(chunk_z))


@lru_cache(maxsize=None)
def _canonical_thread_key(name: str, identifier: int) -> str:
    return f"cleanroom-thread:{identifier}:{_digest(name)[:16]}"


def _thread_key(row: Mapping[str, Any]) -> str:
    order = _closed(
        row["order"],
        {"thread_name", "thread_id", "thread_sequence", "lamport", "monotonic_ns"},
        f"raw row {row['sequence']} order",
    )
    name = _string(order["thread_name"], "raw thread name")
    identifier = _integer(order["thread_id"], "raw thread ID")
    _require(identifier is not None and identifier >= 0, "raw thread ID is negative")
    return _canonical_thread_key(name, identifier)


def _canonical_outcome(
    row: Mapping[str, Any],
    *,
    event_payload: Mapping[str, Any] | None = None,
    diagnostic: bool = False,
) -> dict[str, Any]:
    raw = _closed(
        row["outcome"],
        {"state", "exception_class", "exception_message_sha256"},
        f"raw row {row['sequence']} outcome",
    )
    state = _string(raw["state"], "raw outcome state")
    _require(
        state in {"entered", "returned", "threw", "canceled", "observed", "incomplete"},
        "raw outcome state is not canonicalizable",
    )
    exception_class = raw["exception_class"]
    exception_digest = raw["exception_message_sha256"]
    if state == "threw":
        _string(exception_class, "raw exception class")
        _sha256(exception_digest, "raw exception message")
    elif not diagnostic:
        _require(
            exception_class is None and exception_digest is None,
            "raw non-throw outcome carries an exception",
        )
    elif exception_class is not None or exception_digest is not None:
        _string(exception_class, "raw diagnostic exception class")
        _sha256(exception_digest, "raw diagnostic exception message")
    cancelled = state == "canceled"
    event_result: str | None = None
    if event_payload is not None:
        cancelled = _boolean(event_payload["cancelled_after"], "event cancellation")
        result = event_payload.get("result_after")
        _require(result is None or isinstance(result, str), "event result must be string or null")
        event_result = result
    return {
        "state": state,
        "reason_code": None,
        "exception_class": exception_class,
        "exception_message_sha256": exception_digest,
        "cancelled": cancelled,
        "event_result": event_result,
    }


class _Normalizer:
    def __init__(
        self,
        admission: RawAdmission,
        *,
        run: Mapping[str, Any],
        selection_id: str,
        probe_plan: Mapping[str, Any],
        binder: StrictActorBinder,
        workbench_identity: Mapping[str, Any],
        monotonic_ns: Callable[[], int] | None,
        checkpoint_domains: Iterable[str],
    ) -> None:
        _require(isinstance(admission, RawAdmission), "raw rows were not admitted")
        self.admission = admission
        self.rows = admission.rows
        _require(bool(self.rows), "raw admission has no rows")
        self.hooks = _probe_plan_hooks(probe_plan, admission)
        self.binder = binder
        self.workbench_actor = binder.bind(workbench_identity)
        _require(
            self.workbench_actor["binding"] == "workbench",
            "normalizer Workbench identity lacks exact inventory and class-dump proof",
        )
        self.builder = BundleBuilder(
            run,
            selection_id=selection_id,
            actor=unbound_actor(),
            monotonic_ns=monotonic_ns,
            # Raw admission already retains a second immutable representation.
            # This one-shot normalizer can transfer its record lists directly
            # into the published bundle instead of doubling their object graph.
            copy_on_publish=False,
        )
        _require(
            admission.requested_mode == run.get("capture_mode"),
            "raw and canonical capture modes differ",
        )
        self.selection_id = selection_id
        domains = sorted(set(checkpoint_domains))
        _require(domains and all(isinstance(item, str) and item for item in domains),
                 "checkpoint domains must be nonempty strings")
        self.checkpoint_domains = domains
        self.raw_stacks: dict[str, list[str]] = defaultdict(list)
        self.raw_entries: dict[str, dict[str, Any]] = {}
        self.span_map: dict[str, str] = {}
        self.seen_raw_spans: set[str] = set()
        self.trace_map: dict[str, str] = {}
        self.root_map: dict[str, str] = {}
        self.expected_thread_sequence: dict[str, int] = defaultdict(int)
        self.last_thread_monotonic: dict[str, int] = {}
        self.capture_nonce = admission.capture_nonce
        self.saw_error = False
        self.driver_complete = False
        self.pending_probe_health_preamble: dict[str, Any] | None = None

    def _causal_ids(self, row: Mapping[str, Any]) -> tuple[str, str]:
        raw = _closed(
            row["causality"],
            {"trace_id", "root_trigger_id", "span_id", "parent_span_id"},
            f"raw row {row['sequence']} causality",
        )
        stack = self.raw_stacks[_thread_key(row)]
        if stack:
            entry = self.raw_entries[stack[-1]]
            _require(raw["span_id"] == stack[-1], "raw record detached from active span")
            _require(
                raw["parent_span_id"] == entry["parent_raw_span_id"],
                "raw record active parent mismatch",
            )
            _require(
                raw["trace_id"] == entry["raw_trace_id"]
                and raw["root_trigger_id"] == entry["raw_root_id"],
                "raw record changed its active trace",
            )
        else:
            _require(
                raw["span_id"] is None and raw["parent_span_id"] is None,
                "raw record cites an inactive span",
            )
        raw_trace = raw["trace_id"] or "detached"
        raw_root = raw["root_trigger_id"] or "detached"
        trace_key = str(raw_trace)
        trace = self.trace_map.get(trace_key)
        if trace is None:
            trace = "crucible-trace:sha256:" + _digest(
                {"capture_nonce": self.capture_nonce, "raw_trace": raw_trace}
            )
            self.trace_map[trace_key] = trace
        root_key = str(raw_root)
        root = self.root_map.get(root_key)
        if root is None:
            root = "crucible-trigger:sha256:" + _digest(
                {"capture_nonce": self.capture_nonce, "raw_root": raw_root}
            )
            self.root_map[root_key] = root
        return trace, root

    def _record(
        self,
        row: Mapping[str, Any],
        record_type: str,
        payload: Mapping[str, Any],
        *,
        actor: Mapping[str, Any] | None = None,
        outcome: Mapping[str, Any] | None = None,
        thread_key: str | None = None,
        trace_id: str | None = None,
        root_trigger_id: str | None = None,
    ) -> int:
        dimension, chunk = _scope(row)
        if trace_id is None or root_trigger_id is None:
            trace_id, root_trigger_id = self._causal_ids(row)
        return self.builder.record(
            record_type,
            payload,
            dimension_id=dimension,
            chunk=chunk,
            thread_key=thread_key or _thread_key(row),
            trace_id=trace_id,
            root_trigger_id=root_trigger_id,
            actor=actor or self.binder.bind(row["actor"]),
            outcome=outcome or _canonical_outcome(row),
        )

    def _enter_span(self, row: Mapping[str, Any]) -> None:
        raw_causality = row["causality"]
        raw_span = _string(raw_causality["span_id"], "raw span ID")
        _require(raw_span not in self.seen_raw_spans, "raw span ID is duplicated")
        thread = _thread_key(row)
        stack = self.raw_stacks[thread]
        expected_parent = stack[-1] if stack else None
        _require(
            raw_causality["parent_span_id"] == expected_parent,
            "raw span parent does not match nesting",
        )
        raw_trace = _string(raw_causality["trace_id"], "raw span trace")
        raw_root = _string(raw_causality["root_trigger_id"], "raw span root")
        if stack:
            parent = self.raw_entries[stack[-1]]
            _require(
                raw_trace == parent["raw_trace_id"] and raw_root == parent["raw_root_id"],
                "raw nested span changed trace or root",
            )
        canonical_span = "crucible-span:sha256:" + _digest(
            {"capture_nonce": self.capture_nonce, "raw_span_id": raw_span}
        )
        canonical_parent = None if expected_parent is None else self.span_map[expected_parent]
        trace = self.trace_map.get(raw_trace)
        if trace is None:
            trace = "crucible-trace:sha256:" + _digest(
                {"capture_nonce": self.capture_nonce, "raw_trace": raw_trace}
            )
            self.trace_map[raw_trace] = trace
        root = self.root_map.get(raw_root)
        if root is None:
            root = "crucible-trigger:sha256:" + _digest(
                {"capture_nonce": self.capture_nonce, "raw_root": raw_root}
            )
            self.root_map[raw_root] = root
        payload = row["payload"]
        span_kind = _string(payload.get("span_kind"), "raw span kind")
        operation = _string(payload.get("operation_id"), "raw span operation")
        planned = self.hooks.get(operation)
        operation_id = operation if planned is None else planned["canonical_hook_id"]
        _require(_QUALIFIED_ID_RE.fullmatch(operation_id) is not None,
                 "raw span operation is not a qualified ID")
        arguments_digest = _sha256(payload.get("arguments_sha256"), "span arguments")
        actor = self.binder.bind(row["actor"])
        dimension, chunk = _scope(row)
        self.builder.record(
            "span_enter",
            {
                "span_kind": span_kind,
                "operation_id": operation_id,
                "arguments_sha256": arguments_digest,
            },
            dimension_id=dimension,
            chunk=chunk,
            thread_key=thread,
            trace_id=trace,
            root_trigger_id=root,
            span_id=canonical_span,
            parent_span_id=canonical_parent,
            actor=actor,
            outcome={
                "state": "entered",
                "reason_code": None,
                "exception_class": None,
                "exception_message_sha256": None,
                "cancelled": False,
                "event_result": None,
            },
        )
        # BundleBuilder exposes higher-level span methods for unhashed source
        # values.  Raw transport already carries trusted digests, so the
        # normalizer maintains the same append-only stack after recording the
        # exact digest instead of hashing a digest a second time.
        self.builder._span_stacks[thread].append(canonical_span)
        stack.append(raw_span)
        self.seen_raw_spans.add(raw_span)
        self.span_map[raw_span] = canonical_span
        self.raw_entries[raw_span] = {
            "thread": thread,
            "scope": (dimension, chunk),
            "span_kind": span_kind,
            "actor": actor,
            "raw_trace_id": raw_trace,
            "raw_root_id": raw_root,
            "parent_raw_span_id": expected_parent,
            "canonical_parent": canonical_parent,
            "canonical_trace": trace,
            "canonical_root": root,
        }

    def _terminal_span(self, row: Mapping[str, Any]) -> None:
        raw_span = _string(row["causality"]["span_id"], "raw terminal span ID")
        thread = _thread_key(row)
        stack = self.raw_stacks[thread]
        _require(stack and stack[-1] == raw_span, "raw terminal is not the active span")
        _require(raw_span in self.raw_entries, "raw terminal has no entry")
        entry = self.raw_entries[raw_span]
        _require(_scope(row) == entry["scope"], "raw span terminal changed scope")
        _require(
            row["causality"]["parent_span_id"] == entry["parent_raw_span_id"],
            "raw span terminal changed parent",
        )
        _require(
            row["causality"]["trace_id"] == entry["raw_trace_id"]
            and row["causality"]["root_trigger_id"] == entry["raw_root_id"],
            "raw span terminal changed trace",
        )
        actor = self.binder.bind(row["actor"])
        _require(actor == entry["actor"], "raw span terminal changed actor evidence")
        payload = row["payload"]
        span_kind = _string(payload.get("span_kind"), "raw terminal span kind")
        _require(span_kind == entry["span_kind"], "raw span terminal changed kind")
        dimension, chunk = entry["scope"]
        canonical_span = self.span_map[raw_span]
        if row["record_type"] == "span_return":
            canonical_payload = {
                "span_kind": span_kind,
                "result_sha256": _sha256(payload.get("result_sha256"), "span result"),
            }
            outcome = {
                "state": "returned",
                "reason_code": None,
                "exception_class": None,
                "exception_message_sha256": None,
                "cancelled": False,
                "event_result": None,
            }
        else:
            canonical_payload = {
                "span_kind": span_kind,
                "throwable_state_sha256": _sha256(
                    payload.get("throwable_state_sha256"), "span throwable state"
                ),
            }
            outcome = _canonical_outcome(row)
            _require(outcome["state"] == "threw", "raw span throw outcome is not threw")
        self.builder.record(
            row["record_type"],
            canonical_payload,
            dimension_id=dimension,
            chunk=chunk,
            thread_key=thread,
            trace_id=entry["canonical_trace"],
            root_trigger_id=entry["canonical_root"],
            span_id=canonical_span,
            parent_span_id=entry["canonical_parent"],
            actor=actor,
            outcome=outcome,
        )
        self.builder._span_stacks[thread].pop()
        stack.pop()
        # Closed spans can no longer be referenced by a valid raw record. Keep
        # only their compact identity in ``seen_raw_spans`` so duplicate IDs
        # still fail closed without retaining every entry receipt for the run.
        del self.raw_entries[raw_span]
        del self.span_map[raw_span]

    def _publish_probe_health(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        raw_hook = _string(payload.get("hook_id"), "raw probe health hook")
        _require(raw_hook in self.hooks, f"raw health names unplanned hook {raw_hook}")
        planned = self.hooks[raw_hook]
        observed_target = tuple(
            payload.get(field)
            for field in ("target_class", "target_method", "target_descriptor")
        )
        expected_target = tuple(
            planned[field]
            for field in ("target_class", "target_method", "target_descriptor")
        )
        _require(observed_target == expected_target, "raw probe target tuple mismatch")
        expected = _integer(payload.get("expected_injection_count"), "probe expected count")
        observed = _integer(payload.get("observed_injection_count"), "probe observed count")
        _require(expected == planned["expected_cardinality"], "probe cardinality mismatch")
        self._record(
            row,
            "probe_health",
            {
                "hook_id": planned["canonical_hook_id"],
                "health_state": _string(payload.get("health_state"), "probe health state"),
                "target_class": planned["target_class"],
                "target_method": planned["target_method"],
                "target_descriptor": planned["target_descriptor"],
                "original_class_sha256": _sha256(
                    payload.get("original_class_sha256"), "probe original class"
                ),
                # Admission syntax-checks the raw Mixin postApply digest.  The
                # canonical receipt publishes Foundation's independent final
                # byte[] handed to defineClass instead.
                "transformed_class_sha256": self.binder.final_class_digest(
                    planned["target_class"]
                ),
                "expected_injection_count": expected,
                "observed_injection_count": observed,
            },
            actor=self.workbench_actor,
            outcome=_canonical_outcome(row),
            thread_key="cleanroom-probe-health",
            trace_id="crucible-trace:probe-health",
            root_trigger_id="crucible-trigger:probe-health",
        )

    def _queue_probe_health_preamble(self, row: Mapping[str, Any]) -> None:
        """Retain one health row that announces the immediately following span.

        Cleanroom's first-reach probe is emitted inside the instrumented method,
        immediately before that method's span-enter receipt.  Its raw causal
        tuple therefore names the forthcoming span.  This is the sole allowed
        forward reference: it must describe a new root/child of the current
        stack and the next raw row must consume the exact tuple.
        """

        _require(
            self.pending_probe_health_preamble is None,
            "raw probe-health span preamble is already pending",
        )
        causality = row["causality"]
        raw_span = _string(causality["span_id"], "raw probe-health preamble span ID")
        _require(
            raw_span not in self.seen_raw_spans,
            "raw probe-health preamble does not name a new span",
        )
        thread = _thread_key(row)
        stack = self.raw_stacks[thread]
        expected_parent = stack[-1] if stack else None
        _require(
            causality["parent_span_id"] == expected_parent,
            "raw probe-health preamble parent does not match the current span",
        )
        raw_trace = _string(
            causality["trace_id"], "raw probe-health preamble trace"
        )
        raw_root = _string(
            causality["root_trigger_id"], "raw probe-health preamble root"
        )
        if stack:
            parent = self.raw_entries[stack[-1]]
            _require(
                raw_trace == parent["raw_trace_id"]
                and raw_root == parent["raw_root_id"],
                "raw probe-health preamble changed the current trace or root",
            )
        self.pending_probe_health_preamble = {
            "row": row,
            "thread": thread,
            "trace_id": raw_trace,
            "root_trigger_id": raw_root,
            "span_id": raw_span,
            "parent_span_id": expected_parent,
        }

    def _probe_health(self, row: Mapping[str, Any]) -> None:
        # Probe health is deliberately moved to an out-of-band canonical
        # thread, but its raw occurrence must still have valid nesting.  A
        # non-null span other than the current top is the narrowly admitted
        # first-reach preamble handled above; null and in-span health retain
        # the ordinary causal validation path.
        causality = row["causality"]
        stack = self.raw_stacks[_thread_key(row)]
        if causality["span_id"] is None or (
            stack and causality["span_id"] == stack[-1]
        ):
            self._causal_ids(row)
            self._publish_probe_health(row)
            return
        self._queue_probe_health_preamble(row)

    def _consume_probe_health_preamble(self, row: Mapping[str, Any]) -> None:
        pending = self.pending_probe_health_preamble
        _require(pending is not None, "probe-health preamble is not pending")
        _require(
            row["record_type"] == "span_enter",
            "raw probe-health span preamble is not immediately followed by span_enter",
        )
        _require(
            _thread_key(row) == pending["thread"],
            "raw probe-health span preamble is not followed on the same thread",
        )
        causality = row["causality"]
        expected = tuple(
            pending[field]
            for field in ("trace_id", "root_trigger_id", "span_id", "parent_span_id")
        )
        observed = tuple(
            causality[field]
            for field in ("trace_id", "root_trigger_id", "span_id", "parent_span_id")
        )
        _require(
            observed == expected,
            "raw probe-health span preamble does not match the following span_enter",
        )
        self._publish_probe_health(pending["row"])
        self.pending_probe_health_preamble = None

    def _event(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        boundary = _string(payload.get("boundary"), "event boundary")
        before_digest = _sha256(payload.get("state_before_sha256"), "event state before")
        after_digest = _sha256(payload.get("state_after_sha256"), "event state after")
        before_cancelled = _boolean(payload.get("cancelled_before"), "event cancellation before")
        after_cancelled = _boolean(payload.get("cancelled_after"), "event cancellation after")
        listener_ordinal = _integer(
            payload.get("listener_ordinal"), "listener ordinal", nullable=True
        )
        if boundary == "post_return":
            # The raw post receipt compares the whole dispatch.  Canonical
            # listener continuity already records that transition, so the post
            # terminal is a final-state boundary rather than a duplicate delta.
            before_digest = after_digest
            before_cancelled = after_cancelled
        self._record(
            row,
            "event_dispatch",
            {
                "event_class": _string(payload.get("event_class"), "event class"),
                "bus_id": _qualified("forge-bus", payload.get("bus_id")),
                "boundary": boundary,
                "listener_ordinal": listener_ordinal,
                "state_before_sha256": before_digest,
                "state_after_sha256": after_digest,
                "cancelled_before": before_cancelled,
                "cancelled_after": after_cancelled,
            },
            outcome=_canonical_outcome(row, event_payload=payload),
        )

    def _block_write(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        generation = (
            _integer(payload.get("generation_chunk_x"), "write generation x"),
            _integer(payload.get("generation_chunk_z"), "write generation z"),
        )
        target = (
            _integer(payload.get("target_chunk_x"), "write target x"),
            _integer(payload.get("target_chunk_z"), "write target z"),
        )
        position = [
            _integer(payload.get("position_x"), "write position x"),
            _integer(payload.get("position_y"), "write position y"),
            _integer(payload.get("position_z"), "write position z"),
        ]
        self._record(
            row,
            "block_write",
            {
                "write_chain_id": _qualified("worldgen-write", payload.get("write_chain_id")),
                "channel": _string(payload.get("channel"), "write channel"),
                "position": position,
                "generation_chunk": {"x": generation[0], "z": generation[1]},
                "target_chunk": {"x": target[0], "z": target[1]},
                "before_state_sha256": _sha256(
                    payload.get("before_state_sha256"), "write state before"
                ),
                "after_state_sha256": _sha256(
                    payload.get("after_state_sha256"), "write state after"
                ),
                "flags": _integer(payload.get("flags"), "write flags", nullable=True),
                "terminal": _boolean(payload.get("terminal"), "write terminal"),
            },
        )

    def _chunk_access(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        current_x = _integer(
            payload.get("current_generation_chunk_x"), "current generation x", nullable=True
        )
        current_z = _integer(
            payload.get("current_generation_chunk_z"), "current generation z", nullable=True
        )
        _require((current_x is None) == (current_z is None),
                 "current generation chunk is partial")
        self._record(
            row,
            "chunk_access",
            {
                "access_kind": _string(payload.get("access_kind"), "chunk access kind"),
                "api_id": _qualified("cleanroom-worldgen", payload.get("api_id")),
                "current_generation_chunk": (
                    None if current_x is None else {"x": current_x, "z": current_z}
                ),
                "requested_chunk": {
                    "x": _integer(payload.get("requested_chunk_x"), "requested chunk x"),
                    "z": _integer(payload.get("requested_chunk_z"), "requested chunk z"),
                },
                "loaded_before": _boolean(payload.get("loaded_before"), "loaded before"),
                "result": _string(payload.get("result"), "chunk access result"),
            },
        )

    def _decision(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        raw_decision = _string(payload.get("decision"), "cooperative decision")
        decision = raw_decision if raw_decision in {
            "accepted", "rejected", "selected", "skipped"
        } else "selected"
        self._record(
            row,
            "decision",
            {
                "rule_id": _qualified("worldgen-rule", payload.get("rule_id")),
                "input_sha256": _sha256(payload.get("input_state_sha256"), "decision input"),
                "decision": decision,
                "output_sha256": _sha256(payload.get("output_state_sha256"), "decision output"),
            },
        )

    def _rng(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        self._record(
            row,
            "rng_observation",
            {
                "detail": "summary",
                "stream_id": _qualified("worldgen-rng", payload.get("stream_id")),
                "algorithm_class": _string(payload.get("algorithm"), "RNG algorithm"),
                "operation": _string(payload.get("operation"), "RNG operation"),
                "call_ordinal": None,
                "parameters_sha256": _sha256(payload.get("parameters_sha256"), "RNG parameters"),
                "result_sha256": _sha256(payload.get("result_sha256"), "RNG result"),
                "rolling_digest": _sha256(
                    payload.get("rolling_digest_sha256"), "RNG rolling digest"
                ),
            },
        )

    def _checkpoint(self, row: Mapping[str, Any]) -> None:
        payload = row["payload"]
        dimension, chunk = _scope(row)
        _require(dimension is not None and chunk is not None, "checkpoint lacks exact scope")
        checkpoint_id = _qualified("worldgen-checkpoint", payload.get("checkpoint_id"))
        stage_id = _qualified("worldgen-stage", payload.get("stage_id"))
        canonicalizer = _qualified(
            "worldgen-canonicalizer", payload.get("canonicalization_id")
        )
        semantic_digest = _sha256(
            payload.get("semantic_state_sha256"), "checkpoint semantic state"
        )
        ordinal = self._record(
            row,
            "checkpoint",
            {
                "checkpoint_id": checkpoint_id,
                "stage_id": stage_id,
                "canonicalization_id": canonicalizer,
                "semantic_state_sha256": semantic_digest,
            },
        )
        comparison_scope = {
            "selection_id": self.selection_id,
            "dimension_id": dimension,
            "chunk": {"x": chunk[0], "z": chunk[1]},
            "stage_id": stage_id,
        }
        fingerprint_without_id = {
            "checkpoint_ordinal": ordinal,
            "checkpoint_id": checkpoint_id,
            "canonicalization_id": canonicalizer,
            "scope": {
                "comparison_scope_sha256": _digest(comparison_scope),
                "dimension_id": dimension,
                "chunk": {"x": chunk[0], "z": chunk[1]},
                "stage_id": stage_id,
            },
            "included_domains": self.checkpoint_domains,
            "excluded_observation_fields": sorted(OBSERVATION_EXCLUSIONS),
            "semantic_state_sha256": semantic_digest,
        }
        self.builder.semantic_fingerprints.append(
            {
                "fingerprint_id": _content_id(
                    FINGERPRINT_PREFIX, fingerprint_without_id
                ),
                **fingerprint_without_id,
            }
        )

    def _diagnostic(self, row: Mapping[str, Any], code: str, severity: str) -> None:
        self._record(
            row,
            "diagnostic",
            {
                "code": code,
                "severity": severity,
                "supporting_ordinals": [],
                "message_arguments_sha256": _digest(dict(row["payload"])),
            },
            outcome=_canonical_outcome(row, diagnostic=True),
        )
        if severity == "error":
            self.saw_error = True

    def _validate_row_envelope(self, row: Mapping[str, Any], index: int) -> None:
        _closed(row, _ROW_KEYS, f"raw row {index}")
        _require(
            _integer(row["sequence"], "raw global sequence") == index,
            "raw file order is not contiguous",
        )
        _require(
            _string(row["format"], "raw format")
            == "workbench-cleanroom-worldgen-observatory-raw-v1",
            "raw transport format changed",
        )
        _string(row["record_type"], "raw record type")
        _require(
            _string(row["capture_id"], "raw capture nonce") == self.capture_nonce,
            "raw capture nonce changed",
        )
        _scope(row)
        _closed(
            row["causality"],
            {"trace_id", "root_trigger_id", "span_id", "parent_span_id"},
            f"raw row {index} causality",
        )
        _closed(row["actor"], _RAW_ACTOR_KEYS, f"raw row {index} actor")
        _closed(
            row["outcome"],
            {"state", "exception_class", "exception_message_sha256"},
            f"raw row {index} outcome",
        )
        _closed(
            row["coverage"],
            {"mode", "detail_state", "dropped_record_count"},
            f"raw row {index} coverage",
        )
        _require(row["coverage"]["mode"] == self.admission.requested_mode,
                 "raw coverage mode changed")
        _require(row["coverage"]["detail_state"] == "complete",
                 "raw detail is incomplete")
        _require(_integer(
            row["coverage"]["dropped_record_count"], "raw dropped count"
        ) == 0,
                 "raw capture dropped records")
        thread = _thread_key(row)
        order = row["order"]
        _require(
            _integer(order["thread_sequence"], "raw thread sequence")
            == self.expected_thread_sequence[thread],
            "raw per-thread order is not contiguous",
        )
        self.expected_thread_sequence[thread] += 1
        monotonic = _integer(order["monotonic_ns"], "raw monotonic time")
        _require(monotonic is not None and monotonic >= 0, "raw monotonic time is negative")
        _require(
            monotonic >= self.last_thread_monotonic.get(thread, -1),
            "raw monotonic time moved backward",
        )
        self.last_thread_monotonic[thread] = monotonic

    def normalize(self) -> dict[str, Any]:
        """Return the ordinary public mapping after one strict validation."""

        return _release_validated_bundle_publication(
            self._normalize_validated_publication()
        )

    def _normalize_validated_publication(self) -> _ValidatedBundlePublication:
        """Retain validation custody for the one-shot exact-case worker."""

        self.builder.start()
        controls: list[Mapping[str, Any]] = []
        driver_completions: list[Mapping[str, Any]] = []
        for index, row in enumerate(self.rows):
            self._validate_row_envelope(row, index)
            if self.pending_probe_health_preamble is not None:
                self._consume_probe_health_preamble(row)
            record_type = row["record_type"]
            if record_type == "capture_control":
                controls.append(row)
                code = (
                    "CRW100_RAW_CAPTURE_STARTED"
                    if row["payload"].get("control") == "start"
                    else "CRW101_RAW_CAPTURE_STOPPED"
                )
                self._diagnostic(row, code, "info")
            elif record_type == "fixture_driver":
                action = row["payload"].get("action")
                if action == "complete":
                    driver_completions.append(row)
                    self.driver_complete = True
                    self._diagnostic(row, "CRW103_FIXTURE_ROUTE_COMPLETED", "info")
                elif action == "route_started":
                    self._diagnostic(row, "CRW102_FIXTURE_ROUTE_STARTED", "info")
                else:
                    self._diagnostic(row, "CRW104_FIXTURE_ROUTE_FAILED", "error")
            elif record_type == "probe_health":
                self._probe_health(row)
                if row["payload"].get("health_state") == "failed":
                    self.saw_error = True
            elif record_type == "span_enter":
                self._enter_span(row)
            elif record_type in {"span_return", "span_throw"}:
                self._terminal_span(row)
            elif record_type == "event_dispatch":
                self._event(row)
            elif record_type == "block_write":
                self._block_write(row)
            elif record_type == "chunk_access":
                self._chunk_access(row)
            elif record_type == "decision":
                self._decision(row)
            elif record_type == "rng_observation":
                self._rng(row)
            elif record_type == "checkpoint":
                self._checkpoint(row)
            elif record_type == "cooperative_stage":
                self._diagnostic(row, "CRW110_COOPERATIVE_STAGE", "info")
            elif record_type == "diagnostic":
                severity = row["payload"].get("severity", "error")
                raw_code = _string(
                    row["payload"].get("code"), "raw diagnostic code"
                )
                self._diagnostic(
                    row,
                    (
                        raw_code
                        if re.fullmatch(r"CRW[0-9]{3}_[A-Z0-9_]+", raw_code)
                        else "CRW199_RAW_DIAGNOSTIC"
                    ),
                    _string(severity, "raw diagnostic severity"),
                )
            else:
                raise CaptureValidationError(f"raw record type {record_type!r} has no mapping")

        _require(
            self.pending_probe_health_preamble is None,
            "raw probe-health span preamble reaches the end of capture",
        )
        _require(controls and controls[0] is self.rows[0], "raw start receipt is missing")
        _require(controls[0]["payload"].get("control") == "start", "raw first control is not start")
        stop = controls[1] if len(controls) == 2 else None
        _require(len(controls) <= 2, "raw capture has too many controls")
        summary_state = self.admission.control_summary.get("completion_state")
        clean_stop = (
            stop is not None
            and stop is self.rows[-1]
            and stop["payload"].get("control") == "stop"
            and stop["payload"].get("completion_state") == "complete"
            and stop["payload"].get("open_span_count") == 0
            and stop["payload"].get("open_write_count") == 0
            and stop["outcome"].get("state") == "returned"
            and summary_state == "complete"
        )
        clean_driver = (
            len(driver_completions) == 1
            and driver_completions[0]["payload"].get("completion_marker")
            == "dedicated_server_fixture_complete_v1"
            and driver_completions[0]["payload"].get("save_state") == "flushed"
            and driver_completions[0]["outcome"].get("state") == "returned"
        )
        if clean_stop and clean_driver and not self.saw_error:
            _require(not any(self.raw_stacks.values()), "completed raw capture has open spans")
            _require(self.builder.semantic_fingerprints, "completed raw capture has no checkpoint")
            return self.builder._complete_validated_publication()

        if stop is None:
            reason = "process_crash"
        elif self.saw_error:
            reason = "probe_failure"
        else:
            reason = "fixture_abort"
        return self.builder._incomplete_validated_publication(
            reason=reason,
            recoverable=stop is None,
            diagnostic={
                "raw_control_completion": summary_state,
                "clean_stop": clean_stop,
                "clean_driver": clean_driver,
                "open_raw_spans": sorted(
                    span for stack in self.raw_stacks.values() for span in stack
                ),
            },
        )


def normalize_cleanroom_raw(
    admission: RawAdmission,
    *,
    run: Mapping[str, Any],
    selection_id: str,
    probe_plan: Mapping[str, Any],
    actor_inventory: Iterable[Mapping[str, Any]],
    class_dump_sha256: Mapping[str, str],
    workbench_identity: Mapping[str, Any],
    monotonic_ns: Callable[[], int] | None = None,
    checkpoint_domains: Iterable[str] = ("block_states",),
) -> dict[str, Any]:
    """Normalize one immutable raw admission and publish one canonical bundle.

    `actor_inventory` contains flat exact mod/source/class/method/descriptor
    receipts. `class_dump_sha256` contains hashes of LaunchWrapper's final
    transformed class dumps.  Neither raw self-description nor either input by
    itself is enough to produce an exact actor.
    """

    return _new_cleanroom_normalizer(
        admission,
        run=run,
        selection_id=selection_id,
        probe_plan=probe_plan,
        actor_inventory=actor_inventory,
        class_dump_sha256=class_dump_sha256,
        workbench_identity=workbench_identity,
        monotonic_ns=monotonic_ns,
        checkpoint_domains=checkpoint_domains,
    ).normalize()


def _normalize_cleanroom_raw_publication(
    admission: RawAdmission,
    *,
    run: Mapping[str, Any],
    selection_id: str,
    probe_plan: Mapping[str, Any],
    actor_inventory: Iterable[Mapping[str, Any]],
    class_dump_sha256: Mapping[str, str],
    workbench_identity: Mapping[str, Any],
    monotonic_ns: Callable[[], int] | None = None,
    checkpoint_domains: Iterable[str] = ("block_states",),
) -> _ValidatedBundlePublication:
    """Normalize one worker-owned admission without releasing its mutable bundle."""

    return _new_cleanroom_normalizer(
        admission,
        run=run,
        selection_id=selection_id,
        probe_plan=probe_plan,
        actor_inventory=actor_inventory,
        class_dump_sha256=class_dump_sha256,
        workbench_identity=workbench_identity,
        monotonic_ns=monotonic_ns,
        checkpoint_domains=checkpoint_domains,
    )._normalize_validated_publication()


def _new_cleanroom_normalizer(
    admission: RawAdmission,
    *,
    run: Mapping[str, Any],
    selection_id: str,
    probe_plan: Mapping[str, Any],
    actor_inventory: Iterable[Mapping[str, Any]],
    class_dump_sha256: Mapping[str, str],
    workbench_identity: Mapping[str, Any],
    monotonic_ns: Callable[[], int] | None,
    checkpoint_domains: Iterable[str],
) -> _Normalizer:
    binder = StrictActorBinder(actor_inventory, class_dump_sha256)
    return _Normalizer(
        admission,
        run=run,
        selection_id=selection_id,
        probe_plan=probe_plan,
        binder=binder,
        workbench_identity=workbench_identity,
        monotonic_ns=monotonic_ns,
        checkpoint_domains=checkpoint_domains,
    )


__all__ = ["StrictActorBinder", "normalize_cleanroom_raw"]
