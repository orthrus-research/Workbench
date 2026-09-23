"""Build, seal, and fail-closed validate Worldgen Observatory bundles.

The primitive intentionally uses only the Python standard library. Runtime
probes may emit append-only records in another process; this module owns the
generic publication boundary that decides whether those records form a
completed Crucible capture or only incomplete residue.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping


CAPTURE_CONTRACT_ID = "WORKBENCH-CRUCIBLE-WORLDGEN-OBSERVATORY-CAPTURE-V1"
BUNDLE_FORMAT = "workbench-crucible-worldgen-observatory-bundle-v1"
RECORD_FORMAT = "workbench-crucible-worldgen-observatory-record-v1"
RUN_PREFIX = "crucible-worldgen-run:sha256:"
FINGERPRINT_PREFIX = "crucible-worldgen-fingerprint:sha256:"
CAPTURE_PREFIX = "crucible-worldgen-capture:sha256:"
RESIDUE_PREFIX = "crucible-worldgen-residue:sha256:"

OBSERVATION_EXCLUSIONS = (
    "wall_clock",
    "monotonic_ns",
    "thread_key",
    "thread_sequence",
    "lamport",
    "timing_metrics",
    "runtime_object_identity",
)

RECORD_TYPES = {
    "capture_control",
    "probe_health",
    "span_enter",
    "span_return",
    "span_throw",
    "event_dispatch",
    "decision",
    "rng_observation",
    "block_write",
    "chunk_access",
    "checkpoint",
    "dropped_detail",
    "diagnostic",
}

_TOP_LEVEL_KEYS = {
    "schema_version",
    "format",
    "contract_id",
    "run",
    "records",
    "semantic_fingerprints",
    "summary",
    "publication",
}

_SCHEMA_VALIDATOR: Any | None = None
_MISSING = object()
_CANONICAL_JSON_BUFFER_CHAR_LIMIT = 1024 * 1024


class CaptureValidationError(ValueError):
    """Raised when capture bytes cannot satisfy their declared contract."""


_VALIDATED_PUBLICATION_CAPABILITY = object()


class _ValidatedBundlePublication:
    """Internal capability for one bundle validated in this process.

    The worker path retains this wrapper instead of exposing its mutable bundle
    mapping between validation, publication, and projection.  Public APIs keep
    returning ordinary mappings and therefore keep validating them at every
    external admission boundary.
    """

    __slots__ = (
        "_bundle",
        "_capability",
        "_identity",
        "_publication_state",
        "_publication_id",
        "_run_id",
        "_record_count",
    )

    def __init__(self, bundle: dict[str, Any], *, capability: object) -> None:
        if capability is not _VALIDATED_PUBLICATION_CAPABILITY:
            raise CaptureValidationError(
                "validated bundle publication capability is not authorized"
            )
        self._bundle = bundle
        self._capability = capability
        self._identity = object()
        publication = bundle["publication"]
        self._publication_state = str(publication["state"])
        if self._publication_state == "completed":
            self._publication_id = str(publication["completion_seal"]["capture_id"])
        else:
            self._publication_id = str(publication["crash_residue"]["residue_id"])
        self._run_id = str(bundle["run"]["run_id"])
        self._record_count = int(bundle["summary"]["record_count"])


@dataclass(frozen=True, slots=True)
class _ValidatedBundleWriteReceipt:
    canonical_json_sha256: str
    file_sha256: str
    byte_count: int
    run_id: str
    publication_state: str
    publication_id: str
    record_count: int
    _publication_identity: object


def _schema_validator() -> Any:
    """Load the closed Draft 2020-12 schemas or fail closed.

    Workbench repository validation already carries Python validation
    dependencies. Capture admission must never silently become weaker when
    the schema validator is absent.
    """

    global _SCHEMA_VALIDATOR
    if _SCHEMA_VALIDATOR is not None:
        return _SCHEMA_VALIDATOR
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ModuleNotFoundError as exc:
        raise CaptureValidationError(
            "jsonschema is required for closed capture admission"
        ) from exc
    module_root = _module_resource_root(__file__, 'crucible')
    record_schema_path = (
        module_root / "schemas" / "worldgen-observatory-record-v1.schema.json"
    )
    bundle_schema_path = (
        module_root / "schemas" / "worldgen-observatory-bundle-v1.schema.json"
    )
    try:
        record_schema = json.loads(record_schema_path.read_text(encoding="utf-8"))
        bundle_schema = json.loads(bundle_schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureValidationError(f"cannot load capture schemas: {exc}") from exc
    registry = Registry().with_resources(
        [
            (record_schema["$id"], Resource.from_contents(record_schema)),
            (bundle_schema["$id"], Resource.from_contents(bundle_schema)),
        ]
    )
    _SCHEMA_VALIDATOR = Draft202012Validator(bundle_schema, registry=registry)
    return _SCHEMA_VALIDATOR


def _validate_closed_schema(bundle: Mapping[str, Any]) -> None:
    errors = sorted(
        _schema_validator().iter_errors(bundle),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(item) for item in first.absolute_path) or "bundle"
    raise CaptureValidationError(
        f"closed capture schema violation at {location}: {first.message}"
    )


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CaptureValidationError("canonical JSON cannot contain non-finite numbers")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CaptureValidationError("canonical JSON object keys must be strings")
            _reject_non_finite(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def _iter_canonical_json_bytes(value: Any) -> Iterable[bytes]:
    """Yield the contract's canonical JSON without one monolithic allocation."""

    _reject_non_finite(value)
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        buffered: list[str] = []
        buffered_characters = 0
        for chunk in encoder.iterencode(value):
            buffered.append(chunk)
            buffered_characters += len(chunk)
            if buffered_characters >= _CANONICAL_JSON_BUFFER_CHAR_LIMIT:
                yield "".join(buffered).encode("utf-8")
                buffered.clear()
                buffered_characters = 0
        if buffered:
            yield "".join(buffered).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CaptureValidationError(f"value is not canonical JSON: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    """Return the contract's deterministic UTF-8 JSON representation."""

    return b"".join(_iter_canonical_json_bytes(value))


def canonical_json_sha256(value: Any) -> str:
    """Hash canonical JSON incrementally without materializing its bytes."""

    digest = hashlib.sha256()
    for chunk in _iter_canonical_json_bytes(value):
        digest.update(chunk)
    return digest.hexdigest()


def _sha256(value: Any) -> str:
    return canonical_json_sha256(value)


def _content_id(prefix: str, value: Any) -> str:
    return prefix + _sha256(value)


def _without(mapping: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = deepcopy(dict(mapping))
    value.pop(field, None)
    return value


def new_run(
    *,
    capture_mode: str,
    capture_plan_sha256: str,
    fixture_id: str,
    fixture_sha256: str,
    environment: Mapping[str, Any],
    world: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a content-addressed exact run manifest."""

    run_without_id = {
        "capture_mode": capture_mode,
        "capture_plan_sha256": capture_plan_sha256,
        "fixture_id": fixture_id,
        "fixture_sha256": fixture_sha256,
        "environment": deepcopy(dict(environment)),
        "world": deepcopy(dict(world)),
    }
    return {
        "run_id": _content_id(RUN_PREFIX, run_without_id),
        **run_without_id,
    }


def unbound_actor(*, candidate_mod_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Return an actor that makes no unsupported attribution claim."""

    candidates = sorted(set(candidate_mod_ids))
    return {
        "binding": "ambiguous" if candidates else "unbound",
        "mod_id": None,
        "code_source_sha256": None,
        "class_name": None,
        "method_name": None,
        "method_descriptor": None,
        "mapping_namespace": None,
        "transformed_class_sha256": None,
        "candidate_mod_ids": candidates,
    }


def exact_actor(
    *,
    mod_id: str,
    code_source_sha256: str,
    class_name: str,
    method_name: str,
    method_descriptor: str,
    mapping_namespace: str,
    transformed_class_sha256: str,
    workbench: bool = False,
) -> dict[str, Any]:
    """Return a fully bound runtime actor."""

    return {
        "binding": "workbench" if workbench else "exact",
        "mod_id": mod_id,
        "code_source_sha256": code_source_sha256,
        "class_name": class_name,
        "method_name": method_name,
        "method_descriptor": method_descriptor,
        "mapping_namespace": mapping_namespace,
        "transformed_class_sha256": transformed_class_sha256,
        "candidate_mod_ids": [],
    }


def _default_outcome(state: str = "observed") -> dict[str, Any]:
    return {
        "state": state,
        "reason_code": None,
        "exception_class": None,
        "exception_message_sha256": None,
        "cancelled": False,
        "event_result": None,
    }


def _incomplete_limitation(reason: str, open_spans: Iterable[str]) -> str:
    if reason == "process_crash" and any(open_spans):
        return "Process terminated before span closure and capture stop."
    return f"Capture incomplete: {reason}."


class BundleBuilder:
    """In-memory append-only builder for bounded fixture captures.

    The Java probe is expected to stream the same record envelopes for an
    actual game process. This builder is also useful for deterministic
    contract fixtures and post-processing raw probe output.
    """

    def __init__(
        self,
        run: Mapping[str, Any],
        *,
        selection_id: str,
        actor: Mapping[str, Any] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
        copy_on_publish: bool = True,
    ) -> None:
        self.run = deepcopy(dict(run))
        self.selection_id = selection_id
        self.default_actor = deepcopy(
            dict(actor) if actor is not None else unbound_actor()
        )
        self.records: list[dict[str, Any]] = []
        self.semantic_fingerprints: list[dict[str, Any]] = []
        self._thread_sequences: dict[str, int] = defaultdict(int)
        self._span_stacks: dict[str, list[str]] = defaultdict(list)
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._copy_on_publish = copy_on_publish
        self._started = False
        self._published = False
        self._limitations: set[str] = set()

    @property
    def run_id(self) -> str:
        return str(self.run["run_id"])

    def _scope(
        self,
        dimension_id: int | None,
        chunk: tuple[int, int] | None,
    ) -> dict[str, Any]:
        environment = self.run["environment"]
        return {
            "run_id": self.run_id,
            "platform_profile_id": environment["platform_profile_id"],
            "platform_profile_sha256": environment["platform_profile_sha256"],
            "pack_profile_id": environment["pack_profile_id"],
            "pack_profile_sha256": environment["pack_profile_sha256"],
            "snapshot_id": environment["snapshot_id"],
            "physical_side": environment["physical_side"],
            "world_instance_id": self.run["world"]["world_instance_id"],
            "dimension_id": dimension_id,
            "chunk": None if chunk is None else {"x": chunk[0], "z": chunk[1]},
        }

    def _coverage(self) -> dict[str, Any]:
        return {
            "mode": self.run["capture_mode"],
            "selection_id": self.selection_id,
            "detail_state": "complete",
            "dropped_record_count": 0,
            "limitations": sorted(self._limitations),
        }

    def start(self) -> int:
        if self._started:
            raise CaptureValidationError("capture has already started")
        self._started = True
        return self.record(
            "capture_control",
            {
                "action": "start",
                "capture_plan_sha256": self.run["capture_plan_sha256"],
                "prior_selection_id": None,
                "next_selection_id": self.selection_id,
            },
            trace_id="crucible-trace:capture-control",
            root_trigger_id="crucible-trigger:fixture",
        )

    def record(
        self,
        record_type: str,
        payload: Mapping[str, Any],
        *,
        dimension_id: int | None = None,
        chunk: tuple[int, int] | None = None,
        thread_key: str = "server",
        trace_id: str = "crucible-trace:fixture",
        root_trigger_id: str = "crucible-trigger:fixture",
        span_id: str | None = None,
        parent_span_id: str | None = None,
        links: Iterable[Mapping[str, Any]] = (),
        actor: Mapping[str, Any] | None = None,
        outcome: Mapping[str, Any] | None = None,
        happens_after: Iterable[int] = (),
        tick: int | None = None,
    ) -> int:
        if not self._started and record_type != "capture_control":
            raise CaptureValidationError("capture must start before records are appended")
        if self._published:
            raise CaptureValidationError("published capture is immutable")
        if record_type not in RECORD_TYPES:
            raise CaptureValidationError(f"unknown record type: {record_type}")

        active_stack = self._span_stacks[thread_key]
        active_span = active_stack[-1] if active_stack else None
        resolved_span = active_span if span_id is None else span_id
        if parent_span_id is None:
            if resolved_span == active_span and active_span is not None:
                parent_span_id = (
                    active_stack[-2] if len(active_stack) > 1 else None
                )
            elif resolved_span != active_span:
                parent_span_id = active_span

        ordinal = len(self.records)
        sequence = self._thread_sequences[thread_key]
        self._thread_sequences[thread_key] += 1
        record = {
            "schema_version": 1,
            "format": RECORD_FORMAT,
            "contract_id": CAPTURE_CONTRACT_ID,
            "record_type": record_type,
            "ordinal": ordinal,
            "scope": self._scope(dimension_id, chunk),
            "causality": {
                "trace_id": trace_id,
                "root_trigger_id": root_trigger_id,
                "span_id": resolved_span,
                "parent_span_id": parent_span_id,
                "links": [deepcopy(dict(item)) for item in links],
            },
            "actor": deepcopy(dict(actor) if actor is not None else self.default_actor),
            "order": {
                "thread_key": thread_key,
                "thread_sequence": sequence,
                "lamport": ordinal,
                "tick": tick,
                "monotonic_ns": int(self._monotonic_ns()),
                "happens_after": sorted(set(happens_after)),
            },
            "outcome": deepcopy(
                dict(outcome) if outcome is not None else _default_outcome()
            ),
            "coverage": self._coverage(),
            "payload": deepcopy(dict(payload)),
        }
        self.records.append(record)
        return ordinal

    def enter_span(
        self,
        *,
        span_kind: str,
        operation_id: str,
        arguments: Any,
        dimension_id: int | None = None,
        chunk: tuple[int, int] | None = None,
        thread_key: str = "server",
        trace_id: str = "crucible-trace:fixture",
        root_trigger_id: str = "crucible-trigger:fixture",
        actor: Mapping[str, Any] | None = None,
    ) -> str:
        local = {
            "run_id": self.run_id,
            "thread_key": thread_key,
            "thread_sequence": self._thread_sequences[thread_key],
            "operation_id": operation_id,
        }
        span_id = "crucible-span:" + _sha256(local)
        stack = self._span_stacks[thread_key]
        parent = stack[-1] if stack else None
        self.record(
            "span_enter",
            {
                "span_kind": span_kind,
                "operation_id": operation_id,
                "arguments_sha256": _sha256(arguments),
            },
            dimension_id=dimension_id,
            chunk=chunk,
            thread_key=thread_key,
            trace_id=trace_id,
            root_trigger_id=root_trigger_id,
            span_id=span_id,
            parent_span_id=parent,
            actor=actor,
            outcome=_default_outcome("entered"),
        )
        stack.append(span_id)
        return span_id

    def return_span(
        self,
        span_id: str,
        *,
        span_kind: str,
        result: Any,
        dimension_id: int | None = None,
        chunk: tuple[int, int] | None = None,
        thread_key: str = "server",
        actor: Mapping[str, Any] | None = None,
    ) -> int:
        stack = self._span_stacks[thread_key]
        if not stack or stack[-1] != span_id:
            raise CaptureValidationError("span return does not match the active span")
        entry = next(
            record
            for record in reversed(self.records)
            if record["record_type"] == "span_enter"
            and record["causality"]["span_id"] == span_id
        )
        ordinal = self.record(
            "span_return",
            {"span_kind": span_kind, "result_sha256": _sha256(result)},
            dimension_id=dimension_id,
            chunk=chunk,
            thread_key=thread_key,
            trace_id=entry["causality"]["trace_id"],
            root_trigger_id=entry["causality"]["root_trigger_id"],
            span_id=span_id,
            parent_span_id=stack[-2] if len(stack) > 1 else None,
            actor=actor,
            outcome=_default_outcome("returned"),
        )
        stack.pop()
        return ordinal

    def throw_span(
        self,
        span_id: str,
        *,
        span_kind: str,
        throwable_class: str,
        throwable_message: str,
        dimension_id: int | None = None,
        chunk: tuple[int, int] | None = None,
        thread_key: str = "server",
        actor: Mapping[str, Any] | None = None,
    ) -> int:
        stack = self._span_stacks[thread_key]
        if not stack or stack[-1] != span_id:
            raise CaptureValidationError("span throw does not match the active span")
        entry = next(
            record
            for record in reversed(self.records)
            if record["record_type"] == "span_enter"
            and record["causality"]["span_id"] == span_id
        )
        outcome = _default_outcome("threw")
        outcome["exception_class"] = throwable_class
        outcome["exception_message_sha256"] = _sha256(throwable_message)
        ordinal = self.record(
            "span_throw",
            {
                "span_kind": span_kind,
                "throwable_state_sha256": _sha256(
                    {"class": throwable_class, "message": throwable_message}
                ),
            },
            dimension_id=dimension_id,
            chunk=chunk,
            thread_key=thread_key,
            trace_id=entry["causality"]["trace_id"],
            root_trigger_id=entry["causality"]["root_trigger_id"],
            span_id=span_id,
            parent_span_id=stack[-2] if len(stack) > 1 else None,
            actor=actor,
            outcome=outcome,
        )
        stack.pop()
        return ordinal

    def checkpoint(
        self,
        *,
        checkpoint_id: str,
        stage_id: str,
        canonicalization_id: str,
        semantic_state: Any = _MISSING,
        semantic_state_sha256: str | None = None,
        comparison_scope: Any,
        dimension_id: int,
        chunk: tuple[int, int],
        included_domains: Iterable[str],
        actor: Mapping[str, Any] | None = None,
    ) -> int:
        if (semantic_state is _MISSING) == (semantic_state_sha256 is None):
            raise CaptureValidationError(
                "checkpoint requires exactly one semantic state or trusted digest"
            )
        if semantic_state_sha256 is None:
            semantic_digest = _sha256(semantic_state)
        else:
            semantic_digest = semantic_state_sha256
            if (
                len(semantic_digest) != 64
                or any(character not in "0123456789abcdef" for character in semantic_digest)
            ):
                raise CaptureValidationError(
                    "checkpoint semantic state digest must be lowercase SHA-256"
                )
        ordinal = self.record(
            "checkpoint",
            {
                "checkpoint_id": checkpoint_id,
                "stage_id": stage_id,
                "canonicalization_id": canonicalization_id,
                "semantic_state_sha256": semantic_digest,
            },
            dimension_id=dimension_id,
            chunk=chunk,
            actor=actor,
        )
        fingerprint_without_id = {
            "checkpoint_ordinal": ordinal,
            "checkpoint_id": checkpoint_id,
            "canonicalization_id": canonicalization_id,
            "scope": {
                "comparison_scope_sha256": _sha256(comparison_scope),
                "dimension_id": dimension_id,
                "chunk": {"x": chunk[0], "z": chunk[1]},
                "stage_id": stage_id,
            },
            "included_domains": sorted(set(included_domains)),
            "excluded_observation_fields": sorted(OBSERVATION_EXCLUSIONS),
            "semantic_state_sha256": semantic_digest,
        }
        self.semantic_fingerprints.append(
            {
                "fingerprint_id": _content_id(
                    FINGERPRINT_PREFIX, fingerprint_without_id
                ),
                **fingerprint_without_id,
            }
        )
        return ordinal

    def add_limitation(self, limitation: str) -> None:
        if not limitation:
            raise CaptureValidationError("capture limitation must be non-empty")
        if self._started:
            raise CaptureValidationError(
                "capture limitations must be declared before the start record"
            )
        self._limitations.add(limitation)

    def _summary(
        self,
        *,
        incomplete: bool,
        incomplete_reason: str | None = None,
    ) -> dict[str, Any]:
        types = [record["record_type"] for record in self.records]
        open_spans = sorted(
            span for stack in self._span_stacks.values() for span in stack
        )
        dropped = max(
            [record["coverage"]["dropped_record_count"] for record in self.records]
            or [0]
        )
        detail_states = {
            record["coverage"]["detail_state"] for record in self.records
        }
        if incomplete:
            coverage_state = "incomplete"
        elif dropped or "truncated" in detail_states:
            coverage_state = "truncated"
        elif detail_states != {"complete"}:
            coverage_state = "sampled"
        else:
            coverage_state = "complete"
        limitations = {
            limitation
            for record in self.records
            for limitation in record["coverage"]["limitations"]
        }
        if incomplete:
            limitations.add(
                _incomplete_limitation(
                    incomplete_reason or "unknown",
                    open_spans,
                )
            )
        return {
            "record_count": len(self.records),
            "last_ordinal": len(self.records) - 1,
            "span_enter_count": types.count("span_enter"),
            "span_return_count": types.count("span_return"),
            "span_throw_count": types.count("span_throw"),
            "open_span_ids": open_spans,
            "coverage_state": coverage_state,
            "dropped_record_count": dropped,
            "limitations": sorted(limitations),
        }

    @staticmethod
    def _capture_material(
        *,
        run_id: str,
        run_manifest_sha256: str,
        records_sha256: str,
        semantic_fingerprints_sha256: str,
        record_count: int,
        last_ordinal: int,
    ) -> dict[str, Any]:
        return {
            "contract_id": CAPTURE_CONTRACT_ID,
            "run_id": run_id,
            "run_manifest_sha256": run_manifest_sha256,
            "records_sha256": records_sha256,
            "semantic_fingerprints_sha256": semantic_fingerprints_sha256,
            "record_count": record_count,
            "last_ordinal": last_ordinal,
        }

    def complete(self) -> dict[str, Any]:
        """Publish and return one strictly validated completed bundle."""

        return _release_validated_bundle_publication(
            self._complete_validated_publication()
        )

    def _complete_validated_publication(self) -> _ValidatedBundlePublication:
        """Publish a completed bundle as an internal validation capability."""

        if self._published:
            raise CaptureValidationError("capture has already been published")
        if any(self._span_stacks.values()):
            raise CaptureValidationError("completed capture has open spans")
        if not self.semantic_fingerprints:
            raise CaptureValidationError("completed capture needs a semantic fingerprint")
        self.record(
            "capture_control",
            {
                "action": "stop",
                "capture_plan_sha256": self.run["capture_plan_sha256"],
                "prior_selection_id": self.selection_id,
                "next_selection_id": self.selection_id,
            },
            trace_id="crucible-trace:capture-control",
            root_trigger_id="crucible-trigger:fixture",
        )
        summary = self._summary(incomplete=False)
        run_digest = _sha256(self.run)
        records_digest = _sha256(self.records)
        fingerprints_digest = _sha256(self.semantic_fingerprints)
        material = self._capture_material(
            run_id=self.run_id,
            run_manifest_sha256=run_digest,
            records_sha256=records_digest,
            semantic_fingerprints_sha256=fingerprints_digest,
            record_count=summary["record_count"],
            last_ordinal=summary["last_ordinal"],
        )
        seal = {
            "capture_id": _content_id(CAPTURE_PREFIX, material),
            "run_manifest_sha256": run_digest,
            "records_sha256": records_digest,
            "semantic_fingerprints_sha256": fingerprints_digest,
            "record_count": summary["record_count"],
            "last_ordinal": summary["last_ordinal"],
            "sealed_after_stop_record": True,
        }
        bundle = {
            "schema_version": 1,
            "format": BUNDLE_FORMAT,
            "contract_id": CAPTURE_CONTRACT_ID,
            "run": deepcopy(self.run),
            "records": (
                deepcopy(self.records) if self._copy_on_publish else self.records
            ),
            "semantic_fingerprints": (
                deepcopy(self.semantic_fingerprints)
                if self._copy_on_publish
                else self.semantic_fingerprints
            ),
            "summary": summary,
            "publication": {
                "state": "completed",
                "completion_seal": seal,
                "crash_residue": None,
            },
        }
        publication = _new_validated_bundle_publication(
            bundle,
            require_completed=True,
        )
        self._published = True
        return publication

    def incomplete(
        self,
        *,
        reason: str,
        recoverable: bool,
        diagnostic: Any | None = None,
    ) -> dict[str, Any]:
        """Publish and return one strictly validated incomplete bundle."""

        return _release_validated_bundle_publication(
            self._incomplete_validated_publication(
                reason=reason,
                recoverable=recoverable,
                diagnostic=diagnostic,
            )
        )

    def _incomplete_validated_publication(
        self,
        *,
        reason: str,
        recoverable: bool,
        diagnostic: Any | None = None,
    ) -> _ValidatedBundlePublication:
        """Publish incomplete residue as an internal validation capability."""

        if self._published:
            raise CaptureValidationError("capture has already been published")
        summary = self._summary(
            incomplete=True,
            incomplete_reason=reason,
        )
        residue_without_id = {
            "reason": reason,
            "last_complete_ordinal": summary["last_ordinal"],
            "open_span_ids": summary["open_span_ids"],
            "recoverable": recoverable,
            "diagnostic_sha256": None if diagnostic is None else _sha256(diagnostic),
        }
        residue = {
            "residue_id": _content_id(RESIDUE_PREFIX, residue_without_id),
            **residue_without_id,
        }
        bundle = {
            "schema_version": 1,
            "format": BUNDLE_FORMAT,
            "contract_id": CAPTURE_CONTRACT_ID,
            "run": deepcopy(self.run),
            "records": (
                deepcopy(self.records) if self._copy_on_publish else self.records
            ),
            "semantic_fingerprints": (
                deepcopy(self.semantic_fingerprints)
                if self._copy_on_publish
                else self.semantic_fingerprints
            ),
            "summary": summary,
            "publication": {
                "state": "incomplete",
                "completion_seal": None,
                "crash_residue": residue,
            },
        }
        publication = _new_validated_bundle_publication(bundle)
        self._published = True
        return publication


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _validate_actor(actor: Mapping[str, Any], ordinal: int) -> None:
    binding = actor.get("binding")
    _require(binding in {"exact", "ambiguous", "unbound", "workbench"},
             f"record {ordinal} has invalid actor binding")
    if binding in {"exact", "workbench"}:
        for field in (
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
            "transformed_class_sha256",
        ):
            _require(bool(actor.get(field)),
                     f"record {ordinal} exact actor lacks {field}")
        _require(not actor.get("candidate_mod_ids"),
                 f"record {ordinal} exact actor has candidates")
    elif binding in {"unbound", "ambiguous"}:
        for field in (
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
            "transformed_class_sha256",
        ):
            _require(actor.get(field) is None,
                     f"record {ordinal} unresolved actor claims {field}")
        if binding == "unbound":
            _require(not actor.get("candidate_mod_ids"),
                     f"record {ordinal} unbound actor has candidates")
        else:
            _require(bool(actor.get("candidate_mod_ids")),
                     f"record {ordinal} ambiguous actor lacks candidates")


def _validate_run(run: Mapping[str, Any]) -> None:
    expected = _content_id(RUN_PREFIX, _without(run, "run_id"))
    _require(run.get("run_id") == expected, "run ID digest mismatch")
    environment = run["environment"]
    _require(
        (environment["pack_profile_id"] is None)
        == (environment["pack_profile_sha256"] is None),
        "pack profile identity and digest must be present together",
    )
    dimensions = run["world"]["dimension_ids"]
    _require(dimensions == sorted(set(dimensions)),
             "run dimensions must use canonical sorted order")


def _validate_fingerprints(
    fingerprints: list[dict[str, Any]], records: list[dict[str, Any]]
) -> None:
    fingerprint_ids: set[str] = set()
    fingerprint_scopes: set[tuple[Any, ...]] = set()
    for fingerprint in fingerprints:
        expected = _content_id(
            FINGERPRINT_PREFIX,
            _without(fingerprint, "fingerprint_id"),
        )
        _require(
            fingerprint.get("fingerprint_id") == expected,
            "semantic fingerprint ID digest mismatch",
        )
        ordinal = fingerprint.get("checkpoint_ordinal")
        _require(isinstance(ordinal, int) and 0 <= ordinal < len(records),
                 "fingerprint references an invalid checkpoint ordinal")
        record = records[ordinal]
        _require(record.get("record_type") == "checkpoint",
                 "fingerprint does not reference a checkpoint record")
        payload = record.get("payload", {})
        _require(payload.get("checkpoint_id") == fingerprint.get("checkpoint_id"),
                 "fingerprint checkpoint identity mismatch")
        _require(
            payload.get("canonicalization_id") == fingerprint.get("canonicalization_id"),
            "fingerprint canonicalization mismatch",
        )
        _require(
            payload.get("semantic_state_sha256")
            == fingerprint.get("semantic_state_sha256"),
            "fingerprint semantic digest mismatch",
        )
        scope = fingerprint.get("scope", {})
        record_scope = record.get("scope", {})
        _require(scope.get("dimension_id") == record_scope.get("dimension_id"),
                 "fingerprint dimension mismatch")
        _require(scope.get("chunk") == record_scope.get("chunk"),
                 "fingerprint chunk mismatch")
        _require(scope.get("stage_id") == payload.get("stage_id"),
                 "fingerprint stage mismatch")
        _require(
            set(fingerprint.get("excluded_observation_fields", ()))
            == set(OBSERVATION_EXCLUSIONS),
            "fingerprint observation exclusions are incomplete",
        )
        _require(
            fingerprint.get("excluded_observation_fields")
            == sorted(OBSERVATION_EXCLUSIONS),
            "fingerprint exclusions are not canonically ordered",
        )
        included_domains = fingerprint.get("included_domains", [])
        _require(included_domains == sorted(set(included_domains)),
                 "fingerprint domains are not canonically ordered")
        fingerprint_id = fingerprint["fingerprint_id"]
        _require(fingerprint_id not in fingerprint_ids,
                 "semantic fingerprint ID is duplicated")
        fingerprint_ids.add(fingerprint_id)
        scope_key = (
            scope.get("comparison_scope_sha256"),
            scope.get("dimension_id"),
            scope.get("chunk", {}).get("x"),
            scope.get("chunk", {}).get("z"),
            scope.get("stage_id"),
        )
        _require(scope_key not in fingerprint_scopes,
                 "semantic fingerprint scope is duplicated")
        fingerprint_scopes.add(scope_key)


def validate_bundle(
    bundle: Mapping[str, Any], *, require_completed: bool = False
) -> Mapping[str, Any]:
    """Validate integrity, span balance, and publication semantics.

    The validated mapping is returned unchanged for convenient composition.
    Any uncertainty that would permit invalid evidence to appear completed is
    an error rather than a warning.
    """

    _validate_closed_schema(bundle)
    _require(set(bundle) == _TOP_LEVEL_KEYS, "bundle top-level keys mismatch")
    _require(bundle.get("schema_version") == 1, "bundle schema version mismatch")
    _require(bundle.get("format") == BUNDLE_FORMAT, "bundle format mismatch")
    _require(bundle.get("contract_id") == CAPTURE_CONTRACT_ID,
             "bundle capture contract mismatch")

    run = bundle.get("run")
    records = bundle.get("records")
    fingerprints = bundle.get("semantic_fingerprints")
    summary = bundle.get("summary")
    publication = bundle.get("publication")
    _require(isinstance(run, Mapping), "bundle run must be an object")
    _require(isinstance(records, list) and records, "bundle records must be non-empty")
    _require(isinstance(fingerprints, list), "bundle fingerprints must be an array")
    _require(isinstance(summary, Mapping), "bundle summary must be an object")
    _require(isinstance(publication, Mapping), "bundle publication must be an object")
    _validate_run(run)

    thread_expected: dict[str, int] = defaultdict(int)
    thread_last_monotonic: dict[str, int] = {}
    thread_stacks: dict[str, list[str]] = defaultdict(list)
    entered: set[str] = set()
    span_entries: dict[str, Mapping[str, Any]] = {}
    span_terminals: dict[str, Mapping[str, Any]] = {}
    event_dispatch_terminals: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    terminated: set[str] = set()
    span_counts: dict[str, int] = defaultdict(int)
    maximum_drop = 0
    dropped_detail_total = 0
    active_selection: str | None = None
    write_chains: dict[str, dict[str, Any]] = {}
    start_count = 0
    stop_count = 0
    runtime_scoped_types = {
        "decision",
        "rng_observation",
        "block_write",
        "chunk_access",
        "checkpoint",
    }
    for ordinal, record in enumerate(records):
        _require(isinstance(record, Mapping), f"record {ordinal} must be an object")
        _require(record.get("ordinal") == ordinal, "record ordinals are not contiguous")
        _require(record.get("schema_version") == 1, f"record {ordinal} schema mismatch")
        _require(record.get("format") == RECORD_FORMAT, f"record {ordinal} format mismatch")
        _require(record.get("contract_id") == CAPTURE_CONTRACT_ID,
                 f"record {ordinal} contract mismatch")
        record_type = record.get("record_type")
        _require(record_type in RECORD_TYPES, f"record {ordinal} type is not admitted")
        scope = record.get("scope", {})
        _require(scope.get("run_id") == run["run_id"],
                 f"record {ordinal} run scope mismatch")
        environment = run["environment"]
        for field in (
            "platform_profile_id",
            "platform_profile_sha256",
            "pack_profile_id",
            "pack_profile_sha256",
            "snapshot_id",
            "physical_side",
        ):
            _require(scope.get(field) == environment.get(field),
                     f"record {ordinal} {field} scope mismatch")
        _require(
            scope.get("world_instance_id")
            in {None, run["world"]["world_instance_id"]},
            f"record {ordinal} world scope mismatch",
        )
        if scope.get("dimension_id") is not None:
            _require(scope.get("world_instance_id") == run["world"]["world_instance_id"],
                     f"record {ordinal} dimension lacks its world scope")
            _require(scope["dimension_id"] in run["world"]["dimension_ids"],
                     f"record {ordinal} dimension is outside the run manifest")
        if scope.get("chunk") is not None:
            _require(scope.get("dimension_id") is not None,
                     f"record {ordinal} chunk lacks its dimension scope")

        order = record.get("order", {})
        thread_key = order.get("thread_key")
        _require(isinstance(thread_key, str) and thread_key,
                 f"record {ordinal} lacks a thread key")
        _require(order.get("thread_sequence") == thread_expected[thread_key],
                 f"record {ordinal} thread sequence is not contiguous")
        thread_expected[thread_key] += 1
        lamport = order.get("lamport")
        _require(isinstance(lamport, int) and lamport >= 0,
                 f"record {ordinal} invalid Lamport value")
        monotonic_ns = order.get("monotonic_ns")
        _require(isinstance(monotonic_ns, int) and monotonic_ns >= 0,
                 f"record {ordinal} invalid monotonic time")
        prior_monotonic = thread_last_monotonic.get(thread_key, -1)
        _require(monotonic_ns >= prior_monotonic,
                 f"record {ordinal} monotonic time moved backward")
        thread_last_monotonic[thread_key] = monotonic_ns
        for dependency in order.get("happens_after", ()):
            _require(isinstance(dependency, int) and dependency < ordinal,
                     f"record {ordinal} has a forward happens-after edge")

        _validate_actor(record.get("actor", {}), ordinal)
        outcome = record["outcome"]
        if outcome["state"] == "threw":
            _require(
                bool(outcome["exception_class"])
                and bool(outcome["exception_message_sha256"]),
                f"record {ordinal} threw without exception identity",
            )
        elif record_type != "diagnostic":
            _require(
                outcome["exception_class"] is None
                and outcome["exception_message_sha256"] is None,
                f"record {ordinal} claims an exception without a throw outcome",
            )
        if record_type == "span_throw":
            _require(
                outcome["state"] == "threw",
                f"record {ordinal} span throw lacks a throw outcome",
            )
        if record_type == "event_dispatch":
            boundary = record["payload"]["boundary"]
            if boundary == "listener_throw":
                _require(
                    outcome["state"] == "threw",
                    f"record {ordinal} listener throw lacks a throw outcome",
                )
            elif boundary in {"post_enter", "listener_enter", "listener_return"}:
                _require(
                    outcome["state"] != "threw",
                    f"record {ordinal} non-throw event boundary claims a throw",
                )
        if record_type == "probe_health":
            _require(
                record["actor"]["binding"] == "workbench",
                f"record {ordinal} probe health is not emitted by Workbench",
            )
        coverage = record.get("coverage", {})
        _require(coverage.get("mode") == run.get("capture_mode"),
                 f"record {ordinal} capture mode mismatch")
        dropped = coverage.get("dropped_record_count")
        _require(isinstance(dropped, int) and dropped >= maximum_drop,
                 f"record {ordinal} dropped count decreased")
        maximum_drop = dropped

        if record_type == "capture_control":
            payload = record["payload"]
            _require(payload["capture_plan_sha256"] == run["capture_plan_sha256"],
                     f"record {ordinal} capture plan mismatch")
            action = payload["action"]
            if action == "start":
                start_count += 1
                _require(ordinal == 0 and start_count == 1,
                         "capture start must be the first and only start record")
                _require(payload["prior_selection_id"] is None,
                         "capture start has a prior selection")
                active_selection = payload["next_selection_id"]
            elif action == "selector_change":
                _require(active_selection is not None,
                         "selector changed before capture start")
                _require(payload["prior_selection_id"] == active_selection,
                         "selector change prior selection mismatch")
                active_selection = payload["next_selection_id"]
            elif action == "stop":
                stop_count += 1
                _require(active_selection is not None,
                         "capture stopped before start")
                _require(payload["prior_selection_id"] == active_selection,
                         "capture stop prior selection mismatch")
                _require(payload["next_selection_id"] == active_selection,
                         "capture stop changed selection")
            else:
                _require(active_selection is not None,
                         "capture flushed before start")
                _require(payload["prior_selection_id"] == active_selection,
                         "capture flush prior selection mismatch")
                _require(payload["next_selection_id"] == active_selection,
                         "capture flush changed selection")
        _require(active_selection is not None,
                 f"record {ordinal} occurred before capture start")
        _require(coverage.get("selection_id") == active_selection,
                 f"record {ordinal} selection scope mismatch")

        if record_type in runtime_scoped_types:
            _require(
                scope.get("world_instance_id") is not None
                and scope.get("dimension_id") is not None
                and scope.get("chunk") is not None,
                f"record {ordinal} lacks exact runtime scope",
            )
            _require(scope["dimension_id"] in run["world"]["dimension_ids"],
                     f"record {ordinal} dimension is outside the run manifest")
        if record_type == "dropped_detail":
            dropped_detail_total += record["payload"]["dropped_count"]
        if record_type == "block_write":
            payload = record["payload"]
            position = payload["position"]
            expected_target = {"x": position[0] >> 4, "z": position[2] >> 4}
            _require(
                payload["target_chunk"] == expected_target,
                f"record {ordinal} block target chunk disagrees with its position",
            )
            _require(
                payload["generation_chunk"] == scope["chunk"],
                f"record {ordinal} generation chunk disagrees with record scope",
            )
            chain_id = payload["write_chain_id"]
            chain_shape = {
                "dimension_id": scope["dimension_id"],
                "position": position,
                "generation_chunk": payload["generation_chunk"],
                "target_chunk": payload["target_chunk"],
            }
            chain = write_chains.setdefault(
                chain_id,
                {"shape": deepcopy(chain_shape), "terminal_count": 0},
            )
            _require(
                chain["shape"] == chain_shape,
                f"record {ordinal} reuses a write chain across block scopes",
            )
            if payload["terminal"]:
                chain["terminal_count"] += 1
                _require(
                    chain["terminal_count"] == 1,
                    f"record {ordinal} duplicates a write-chain terminal",
                )

        causality = record.get("causality", {})
        span_id = causality.get("span_id")
        if (
            record_type == "event_dispatch"
            and record["payload"]["boundary"]
            in {"listener_return", "listener_throw"}
            and isinstance(span_id, str)
        ):
            event_dispatch_terminals[span_id].append(record)
        stack = thread_stacks[thread_key]
        if record_type == "span_enter":
            _require(isinstance(span_id, str) and span_id not in entered,
                     f"record {ordinal} has a duplicate or missing span ID")
            _require(causality.get("parent_span_id") == (stack[-1] if stack else None),
                     f"record {ordinal} span parent does not match nesting")
            if stack:
                parent_entry = span_entries[stack[-1]]
                _require(
                    causality.get("trace_id")
                    == parent_entry["causality"]["trace_id"]
                    and causality.get("root_trigger_id")
                    == parent_entry["causality"]["root_trigger_id"],
                    f"record {ordinal} nested span changed trace or root",
                )
            stack.append(span_id)
            entered.add(span_id)
            span_entries[span_id] = record
            span_counts["span_enter"] += 1
        elif record_type in {"span_return", "span_throw"}:
            _require(stack and stack[-1] == span_id,
                     f"record {ordinal} terminates a non-active span")
            _require(span_id in entered and span_id not in terminated,
                     f"record {ordinal} has an invalid span terminal")
            entry = span_entries[span_id]
            _require(
                causality.get("parent_span_id")
                == entry["causality"]["parent_span_id"],
                f"record {ordinal} span terminal parent mismatch",
            )
            _require(
                causality.get("trace_id") == entry["causality"]["trace_id"]
                and causality.get("root_trigger_id")
                == entry["causality"]["root_trigger_id"],
                f"record {ordinal} span terminal trace mismatch",
            )
            _require(
                record["payload"]["span_kind"]
                == entry["payload"]["span_kind"],
                f"record {ordinal} span terminal kind mismatch",
            )
            _require(
                record["scope"] == entry["scope"],
                f"record {ordinal} span terminal scope mismatch",
            )
            stack.pop()
            terminated.add(span_id)
            span_terminals[span_id] = record
            span_counts[record_type] += 1
        elif span_id is not None:
            _require(span_id in span_entries and stack and stack[-1] == span_id,
                     f"record {ordinal} cites a nonexistent or inactive span")
            entry = span_entries[span_id]
            _require(
                causality.get("parent_span_id")
                == entry["causality"]["parent_span_id"],
                f"record {ordinal} active span parent mismatch",
            )
            _require(
                causality.get("trace_id") == entry["causality"]["trace_id"]
                and causality.get("root_trigger_id")
                == entry["causality"]["root_trigger_id"],
                f"record {ordinal} active span trace mismatch",
            )
        else:
            _require(causality.get("parent_span_id") is None,
                     f"record {ordinal} has a parent without an active span")
            _require(
                not stack,
                f"record {ordinal} detached from its active span",
            )

    open_spans = sorted(span for stack in thread_stacks.values() for span in stack)
    for span_id, entry in span_entries.items():
        if entry["payload"]["span_kind"] != "event_listener":
            continue
        terminal = span_terminals.get(span_id)
        if terminal is None:
            continue
        dispatch_terminals = event_dispatch_terminals.get(span_id, [])
        _require(
            len(dispatch_terminals) == 1,
            f"event-listener span {span_id} lacks one dispatch terminal",
        )
        dispatch = dispatch_terminals[0]
        expected_record_type = (
            "span_throw"
            if dispatch["payload"]["boundary"] == "listener_throw"
            else "span_return"
        )
        _require(
            terminal["record_type"] == expected_record_type,
            f"event-listener span {span_id} contradicts its dispatch terminal",
        )
        _require(
            dispatch["actor"] == entry["actor"] == terminal["actor"],
            f"event-listener span {span_id} changes actor identity",
        )
        if expected_record_type == "span_throw":
            _require(
                dispatch["outcome"]["exception_class"]
                == terminal["outcome"]["exception_class"]
                and dispatch["outcome"]["exception_message_sha256"]
                == terminal["outcome"]["exception_message_sha256"],
                f"event-listener span {span_id} changes exception identity",
            )
    _validate_fingerprints(fingerprints, records)

    _require(summary.get("record_count") == len(records), "summary record count mismatch")
    _require(summary.get("last_ordinal") == len(records) - 1,
             "summary last ordinal mismatch")
    for field in ("span_enter", "span_return", "span_throw"):
        _require(summary.get(field + "_count") == span_counts[field],
                 f"summary {field} count mismatch")
    _require(summary.get("open_span_ids") == open_spans,
             "summary open spans mismatch")
    _require(summary.get("dropped_record_count") == maximum_drop,
             "summary dropped count mismatch")
    _require(dropped_detail_total == maximum_drop,
             "dropped-detail records do not reconcile with the summary")

    state = publication.get("state")
    _require(state in {"completed", "incomplete"}, "publication state is invalid")
    _require(start_count == 1, "capture must contain exactly one start record")
    _require(stop_count <= 1, "capture contains more than one stop record")
    detail_states = {
        record["coverage"]["detail_state"] for record in records
    }
    if state == "incomplete":
        derived_coverage_state = "incomplete"
    elif maximum_drop or "truncated" in detail_states:
        derived_coverage_state = "truncated"
    elif detail_states != {"complete"}:
        derived_coverage_state = "sampled"
    else:
        derived_coverage_state = "complete"
    _require(
        summary.get("coverage_state") == derived_coverage_state,
        "summary coverage state is not derivable from retained records",
    )
    derived_limitations = {
        limitation
        for record in records
        for limitation in record["coverage"]["limitations"]
    }
    if state == "incomplete":
        residue_for_limit = publication.get("crash_residue")
        reason_for_limit = (
            residue_for_limit.get("reason", "unknown")
            if isinstance(residue_for_limit, Mapping)
            else "unknown"
        )
        derived_limitations.add(
            _incomplete_limitation(reason_for_limit, open_spans)
        )
    _require(
        summary.get("limitations") == sorted(derived_limitations),
        "summary limitations are not derivable from retained evidence",
    )
    if require_completed:
        _require(state == "completed", "capture is incomplete")

    if state == "completed":
        _require(stop_count == 1, "completed capture must contain one stop record")
        _require(not open_spans, "completed capture has open spans")
        _require(bool(fingerprints), "completed capture lacks fingerprints")
        _require(summary.get("coverage_state") != "incomplete",
                 "completed capture has incomplete coverage")
        _require(publication.get("crash_residue") is None,
                 "completed capture contains crash residue")
        _require(records[-1].get("record_type") == "capture_control"
                 and records[-1].get("payload", {}).get("action") == "stop",
                 "completed capture does not end with a stop record")
        _require(
            all(chain["terminal_count"] == 1 for chain in write_chains.values()),
            "completed capture has an unterminated write chain",
        )
        if run.get("capture_mode") == "lossless-fixture":
            _require(maximum_drop == 0, "lossless capture dropped records")
            _require(not any(r.get("record_type") == "dropped_detail" for r in records),
                     "lossless capture contains dropped-detail records")
            _require(all(r.get("coverage", {}).get("detail_state") == "complete"
                         for r in records),
                     "lossless capture has non-complete detail")

        seal = publication.get("completion_seal")
        _require(isinstance(seal, Mapping), "completed capture lacks a seal")
        run_digest = _sha256(run)
        records_digest = _sha256(records)
        fingerprints_digest = _sha256(fingerprints)
        _require(seal.get("run_manifest_sha256") == run_digest,
                 "seal run digest mismatch")
        _require(seal.get("records_sha256") == records_digest,
                 "seal records digest mismatch")
        _require(seal.get("semantic_fingerprints_sha256") == fingerprints_digest,
                 "seal fingerprint digest mismatch")
        _require(seal.get("record_count") == len(records), "seal record count mismatch")
        _require(seal.get("last_ordinal") == len(records) - 1,
                 "seal last ordinal mismatch")
        _require(seal.get("sealed_after_stop_record") is True,
                 "seal was not created after the stop record")
        material = BundleBuilder._capture_material(
            run_id=run["run_id"],
            run_manifest_sha256=run_digest,
            records_sha256=records_digest,
            semantic_fingerprints_sha256=fingerprints_digest,
            record_count=len(records),
            last_ordinal=len(records) - 1,
        )
        _require(seal.get("capture_id") == _content_id(CAPTURE_PREFIX, material),
                 "capture ID digest mismatch")
    else:
        _require(publication.get("completion_seal") is None,
                 "incomplete capture contains a completion seal")
        _require(summary.get("coverage_state") == "incomplete",
                 "incomplete capture summary claims complete coverage")
        residue = publication.get("crash_residue")
        _require(isinstance(residue, Mapping), "incomplete capture lacks crash residue")
        expected = _content_id(RESIDUE_PREFIX, _without(residue, "residue_id"))
        _require(residue.get("residue_id") == expected, "crash residue ID mismatch")
        _require(residue.get("last_complete_ordinal") == len(records) - 1,
                 "crash residue last ordinal mismatch")
        _require(residue.get("open_span_ids") == open_spans,
                 "crash residue open spans mismatch")

    return bundle


def _new_validated_bundle_publication(
    bundle: dict[str, Any],
    *,
    require_completed: bool = False,
) -> _ValidatedBundlePublication:
    """Validate one owned bundle and retain the result as an internal capability."""

    _require(isinstance(bundle, dict), "owned bundle publication must be a dictionary")
    validate_bundle(bundle, require_completed=require_completed)
    return _ValidatedBundlePublication(
        bundle,
        capability=_VALIDATED_PUBLICATION_CAPABILITY,
    )


def _validated_bundle_for_internal_use(
    publication: _ValidatedBundlePublication,
) -> dict[str, Any]:
    """Resolve a bundle only from this module's unforgeable-in-normal-use capability."""

    _require(
        isinstance(publication, _ValidatedBundlePublication)
        and publication._capability is _VALIDATED_PUBLICATION_CAPABILITY,
        "validated bundle publication capability is required",
    )
    return publication._bundle


def _release_validated_bundle_publication(
    publication: _ValidatedBundlePublication,
) -> dict[str, Any]:
    """Return the mapping after its one strict validation for a public caller."""

    return _validated_bundle_for_internal_use(publication)


def _validated_bundle_publication_state(
    publication: _ValidatedBundlePublication,
) -> str:
    """Return only the validated state without releasing the mutable mapping."""

    _validated_bundle_for_internal_use(publication)
    return publication._publication_state


def _validated_bundle_from_write_receipt(
    publication: _ValidatedBundlePublication,
    receipt: _ValidatedBundleWriteReceipt,
) -> dict[str, Any]:
    """Resolve the exact publication cited by one internal atomic-write receipt."""

    bundle = _validated_bundle_for_internal_use(publication)
    _require(
        isinstance(receipt, _ValidatedBundleWriteReceipt)
        and receipt._publication_identity is publication._identity,
        "bundle write receipt does not cite the validated publication",
    )
    return bundle


def _write_validated_bundle(
    path: Path | str,
    publication: _ValidatedBundlePublication,
) -> _ValidatedBundleWriteReceipt:
    """Atomically write an internally validated bundle and digest that exact stream."""

    bundle = _validated_bundle_for_internal_use(publication)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    canonical_digest = hashlib.sha256()
    file_digest = hashlib.sha256()
    byte_count = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for chunk in _iter_canonical_json_bytes(bundle):
                canonical_digest.update(chunk)
                file_digest.update(chunk)
                byte_count += len(chunk)
                handle.write(chunk)
            file_digest.update(b"\n")
            byte_count += 1
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return _ValidatedBundleWriteReceipt(
        canonical_json_sha256=canonical_digest.hexdigest(),
        file_sha256=file_digest.hexdigest(),
        byte_count=byte_count,
        run_id=publication._run_id,
        publication_state=publication._publication_state,
        publication_id=publication._publication_id,
        record_count=publication._record_count,
        _publication_identity=publication._identity,
    )


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CaptureValidationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_bundle(path: Path | str, *, require_completed: bool = False) -> dict[str, Any]:
    """Load one strict JSON bundle and validate it before use."""

    try:
        text = Path(path).read_text(encoding="utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                CaptureValidationError(f"non-finite JSON number: {token}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureValidationError(f"cannot load capture bundle: {exc}") from exc
    _require(isinstance(value, dict), "capture bundle must be a JSON object")
    validate_bundle(value, require_completed=require_completed)
    return value


def write_bundle(path: Path | str, bundle: Mapping[str, Any]) -> None:
    """Atomically publish one already validated bundle."""

    validate_bundle(bundle)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for chunk in _iter_canonical_json_bytes(bundle):
                handle.write(chunk)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
