"""Exact Mixin target-application evidence and receipt V1."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence


RAW_DIRECT_APPLICATION_FORMAT = "workbench-cleanmix-direct-application-raw-v1"
DIRECT_APPLICATION_RECEIPT_FORMAT = "workbench-crucible-mixin-direct-application-receipt-v1"
DIRECT_APPLICATION_RECEIPT_PREFIX = "crucible-mixin-direct-application:sha256:"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
EXPECTED_APPLICATOR_CLASS = "org/spongepowered/asm/mixin/transformer/MixinApplicatorStandard"
EXPECTED_APPLICATOR_SHA256 = "b0bd92ba11df86ec1eb3027a665729877e87fe3ca4c3574995d18b0fd210cc46"

_SHA256 = frozenset("0123456789abcdef")
_RAW_KEYS = frozenset({"format", "capture_id", "sequence", "event", "payload"})
_RAW_EVENTS = frozenset({
    "capture_start", "transformer_installed", "transform_applied",
    "transform_rejected", "transform_failure", "application_started",
    "application_completed", "application_failure", "observer_failure",
    "capture_end",
})
_FILE_KEYS = frozenset({"label", "sha256", "size_bytes"})
_INPUT_NAMES = ("agent_artifact", "fixture_result", "launch_log", "raw_trace")

MANDATORY_LIMITATIONS = (
    "The receipt proves entry and normal return at the exact MixinApplicatorStandard.apply seam only for the named targets in one launch.",
    "A completed application event proves delegated target application returned; final definition bytes and application-level behavior remain separate evidence.",
    "Mixin and classloader identity strings are launch-local evidence locators, not cross-launch semantic identities.",
    "The observer neither requests classes nor changes target ordering, but its bytecode instrumentation is still identified and bounded to one exact input class hash.",
)


class DirectApplicationValidationError(ValueError):
    """Direct-application evidence is malformed or overclaims."""


class _DuplicateJsonKey(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DirectApplicationValidationError(message)


def canonical_direct_application_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DirectApplicationValidationError(
            f"cannot canonically encode direct-application evidence: {exc}"
        ) from exc


def _closed(value: Mapping[str, Any], keys: frozenset[str], context: str) -> None:
    missing = keys - set(value)
    unknown = set(value) - keys
    _require(not missing, f"{context} lacks fields: {sorted(missing)}")
    _require(not unknown, f"{context} has unknown fields: {sorted(unknown)}")


def _text(value: Any, context: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    _require(
        isinstance(value, str) and bool(value) and len(value) <= 8192
        and not any(character in value for character in "\r\n\x00"),
        f"{context} must be bounded nonempty single-line text",
    )
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _boolean(value: Any, context: str) -> bool:
    _require(type(value) is bool, f"{context} must be boolean")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _strings(value: Any, context: str, *, sorted_unique: bool = False) -> list[str]:
    _require(isinstance(value, list), f"{context} must be an array")
    result = [_text(item, f"{context}[{index}]") for index, item in enumerate(value)]
    if sorted_unique:
        _require(result == sorted(set(result)), f"{context} must be sorted and unique")
    return result


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _application_payload(
    payload: Mapping[str, Any], context: str, *, failure: bool
) -> tuple[int, str, list[str]]:
    keys = {"application_id", "target_class", "mixins", "thread_name"}
    if failure:
        keys |= {"exception_class", "message", "stack_top"}
    _closed(payload, frozenset(keys), context)
    application_id = _integer(payload["application_id"], f"{context}.application_id", minimum=1)
    target = _text(payload["target_class"], f"{context}.target_class")
    mixins = _strings(payload["mixins"], f"{context}.mixins")
    _require(bool(mixins), f"{context}.mixins must be nonempty")
    _text(payload["thread_name"], f"{context}.thread_name")
    if failure:
        _text(payload["exception_class"], f"{context}.exception_class")
        _text(payload["message"], f"{context}.message")
        _text(payload["stack_top"], f"{context}.stack_top", nullable=True)
    return application_id, target, mixins


def parse_raw_direct_application(encoded: bytes) -> list[dict[str, Any]]:
    """Strictly parse and health-check one direct-application NDJSON stream."""

    _require(isinstance(encoded, bytes) and bool(encoded), "raw direct-application trace is empty")
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise DirectApplicationValidationError(
            f"raw direct-application trace is not UTF-8: {exc}"
        ) from exc
    _require(text.endswith("\n"), "raw direct-application trace lacks a terminal newline")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        _require(bool(line), f"raw direct-application line {index + 1} is empty")
        try:
            value = json.loads(
                line, object_pairs_hook=_json_object,
                parse_constant=lambda item: (_ for _ in ()).throw(
                    ValueError(f"non-finite number {item}")
                ),
            )
        except (json.JSONDecodeError, _DuplicateJsonKey, ValueError) as exc:
            raise DirectApplicationValidationError(
                f"raw direct-application line {index + 1} is malformed: {exc}"
            ) from exc
        _require(isinstance(value, Mapping), f"raw direct-application line {index + 1} is not an object")
        _closed(value, _RAW_KEYS, f"raw direct-application line {index + 1}")
        _require(value["format"] == RAW_DIRECT_APPLICATION_FORMAT, "raw direct-application format is not V1")
        _text(value["capture_id"], f"raw direct-application line {index + 1}.capture_id")
        _require(value["sequence"] == index, "raw direct-application sequences are not contiguous from zero")
        _require(value["event"] in _RAW_EVENTS, "raw direct-application event is unsupported")
        _require(isinstance(value["payload"], Mapping), "raw direct-application payload must be an object")
        rows.append(deepcopy(dict(value)))

    _require(len(rows) >= 6, "raw direct-application trace is too short")
    _require(rows[0]["event"] == "capture_start", "raw direct-application trace does not start with capture_start")
    _require(rows[-1]["event"] == "capture_end", "raw direct-application trace does not end with capture_end")
    _require(len({row["capture_id"] for row in rows}) == 1, "raw direct-application trace crosses capture identities")
    _require(sum(row["event"] == "capture_start" for row in rows) == 1, "raw direct-application repeats capture_start")
    _require(sum(row["event"] == "capture_end" for row in rows) == 1, "raw direct-application repeats capture_end")

    start = rows[0]["payload"]
    _closed(start, frozenset({
        "agent_id", "java_version", "targets", "redefine_supported",
        "retransform_supported",
    }), "capture_start payload")
    _text(start["agent_id"], "capture_start.agent_id")
    _text(start["java_version"], "capture_start.java_version")
    targets = _strings(start["targets"], "capture_start.targets", sorted_unique=True)
    _require(bool(targets), "capture_start.targets must be nonempty")
    _boolean(start["redefine_supported"], "capture_start.redefine_supported")
    _boolean(start["retransform_supported"], "capture_start.retransform_supported")

    started: dict[int, tuple[str, list[str]]] = {}
    completed: dict[int, tuple[str, list[str]]] = {}
    installed = transformed = transform_failures = application_failures = observer_failures = 0
    install_sequence: int | None = None
    transform_sequence: int | None = None
    first_application_sequence: int | None = None
    for row in rows[1:-1]:
        event = row["event"]
        payload = row["payload"]
        context = f"{event} payload at sequence {row['sequence']}"
        if event == "transformer_installed":
            _closed(payload, frozenset({
                "target_class", "expected_input_sha256", "retransform_requested",
            }), context)
            _require(payload["target_class"] == EXPECTED_APPLICATOR_CLASS, "direct observer target class drifted")
            _require(_sha256(payload["expected_input_sha256"], f"{context}.expected_input_sha256") == EXPECTED_APPLICATOR_SHA256, "direct observer target hash drifted")
            _require(payload["retransform_requested"] is False, "direct observer must not request retransformation")
            installed += 1
            install_sequence = row["sequence"]
        elif event in {"transform_applied", "transform_rejected", "transform_failure"}:
            keys = {"target_class", "defining_loader_class", "defining_loader_identity", "target_code_source_uri", "input_sha256", "output_sha256", "reason"}
            if event == "transform_failure":
                keys |= {"exception_class", "message", "stack_top"}
            _closed(payload, frozenset(keys), context)
            _require(payload["target_class"] == EXPECTED_APPLICATOR_CLASS.replace("/", "."), "transform event target drifted")
            _text(payload["defining_loader_class"], f"{context}.defining_loader_class")
            _text(payload["defining_loader_identity"], f"{context}.defining_loader_identity")
            _text(payload["target_code_source_uri"], f"{context}.target_code_source_uri", nullable=True)
            _require(_sha256(payload["input_sha256"], f"{context}.input_sha256") == EXPECTED_APPLICATOR_SHA256, "transform event violates exact byte guard")
            if event == "transform_applied":
                _sha256(payload["output_sha256"], f"{context}.output_sha256")
                _require(payload["reason"] is None, f"{context}.reason must be null")
                transformed += 1
                transform_sequence = row["sequence"]
            else:
                _require(payload["output_sha256"] is None, f"{context}.output_sha256 must be null")
                _text(payload["reason"], f"{context}.reason")
                transform_failures += 1
        elif event == "application_started":
            if first_application_sequence is None:
                first_application_sequence = row["sequence"]
            application_id, target, mixins = _application_payload(payload, context, failure=False)
            _require(target in targets, f"{context} names an unrequested target")
            _require(application_id not in started, "application identifier starts twice")
            started[application_id] = (target, mixins)
        elif event == "application_completed":
            application_id, target, mixins = _application_payload(payload, context, failure=False)
            _require(started.get(application_id) == (target, mixins), "application completion lacks its exact start")
            _require(application_id not in completed, "application identifier completes twice")
            completed[application_id] = (target, mixins)
        elif event == "application_failure":
            application_failures += 1
            application_id, target, mixins = _application_payload(payload, context, failure=True)
            _require(started.get(application_id) == (target, mixins), "application failure lacks its exact start")
        elif event == "observer_failure":
            observer_failures += 1
            _closed(payload, frozenset({"operation", "exception_class", "message", "stack_top"}), context)
            _text(payload["operation"], f"{context}.operation")
            _text(payload["exception_class"], f"{context}.exception_class")
            _text(payload["message"], f"{context}.message")
            _text(payload["stack_top"], f"{context}.stack_top", nullable=True)

    footer = rows[-1]["payload"]
    _closed(footer, frozenset({
        "health", "requested_targets", "started_counts", "completed_counts",
        "missing_targets", "non_singleton_targets", "application_failure_count",
        "observer_failure_count", "write_failure",
    }), "capture_end payload")
    _require(footer["health"] in {"healthy", "failed"}, "capture_end health is unsupported")
    _require(_strings(footer["requested_targets"], "capture_end.requested_targets", sorted_unique=True) == targets, "capture_end target set drifted")

    def counts(value: Any, context: str) -> dict[str, int]:
        _require(isinstance(value, list), f"{context} must be an array")
        result: dict[str, int] = {}
        for index, item in enumerate(value):
            item_context = f"{context}[{index}]"
            _require(isinstance(item, Mapping), f"{item_context} must be an object")
            _closed(item, frozenset({"target_class", "count"}), item_context)
            target = _text(item["target_class"], f"{item_context}.target_class")
            _require(target in targets and target not in result, f"{item_context} target is unexpected or repeated")
            result[target] = _integer(item["count"], f"{item_context}.count")
        _require(list(result) == targets, f"{context} target order drifted")
        return result

    start_counts = counts(footer["started_counts"], "capture_end.started_counts")
    complete_counts = counts(footer["completed_counts"], "capture_end.completed_counts")
    observed_starts = {target: sum(item[0] == target for item in started.values()) for target in targets}
    observed_completions = {target: sum(item[0] == target for item in completed.values()) for target in targets}
    _require(start_counts == observed_starts, "capture_end started counts are stale")
    _require(complete_counts == observed_completions, "capture_end completed counts are stale")
    missing = _strings(footer["missing_targets"], "capture_end.missing_targets", sorted_unique=True)
    non_singleton = _strings(footer["non_singleton_targets"], "capture_end.non_singleton_targets", sorted_unique=True)
    _require(missing == [target for target in targets if complete_counts[target] == 0], "capture_end missing targets are stale")
    _require(non_singleton == [target for target in targets if start_counts[target] != 1 or complete_counts[target] != 1], "capture_end non-singleton targets are stale")
    _require(_integer(footer["application_failure_count"], "capture_end.application_failure_count") == application_failures, "capture_end application failure count is stale")
    _require(_integer(footer["observer_failure_count"], "capture_end.observer_failure_count") == observer_failures, "capture_end observer failure count is stale")
    write_failure = _boolean(footer["write_failure"], "capture_end.write_failure")
    _require(installed == 1, "raw direct-application trace must install its transformer exactly once")
    _require(
        install_sequence is not None and transform_sequence is not None
        and install_sequence < transform_sequence
        and first_application_sequence is not None
        and transform_sequence < first_application_sequence,
        "raw direct-application transformer install/transform/application order is invalid",
    )
    healthy = transformed == 1 and transform_failures == 0 and application_failures == 0 and observer_failures == 0 and not write_failure and not missing and not non_singleton
    _require((footer["health"] == "healthy") == healthy, "capture_end health is stale")
    _require(footer["health"] == "healthy", "direct-application producer is unhealthy")
    return rows


def _file(value: Any, context: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    _closed(value, _FILE_KEYS, context)
    return {
        "label": _text(value["label"], f"{context}.label"),
        "sha256": _sha256(value["sha256"], f"{context}.sha256"),
        "size_bytes": _integer(value["size_bytes"], f"{context}.size_bytes"),
    }


def build_direct_application_receipt(
    *, launch_id: str, profile_id: str, side: str,
    inputs: Mapping[str, Mapping[str, Any]],
    target_artifact: Mapping[str, Any],
    raw_events: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one content-addressed receipt from an admitted healthy stream."""

    _text(launch_id, "launch_id")
    _text(profile_id, "profile_id")
    _require(side in {"client", "dedicated_server", "integrated_server"}, "side is unsupported")
    _require(isinstance(inputs, Mapping) and set(inputs) == set(_INPUT_NAMES), "inputs are not the exact V1 input set")
    normalized_inputs = {name: _file(inputs[name], f"inputs.{name}") for name in _INPUT_NAMES}
    _require(isinstance(target_artifact, Mapping), "target_artifact must be an object")
    _closed(target_artifact, frozenset({"label", "sha256", "size_bytes", "code_source_uri"}), "target_artifact")
    artifact = {
        "label": _text(target_artifact["label"], "target_artifact.label"),
        "sha256": _sha256(target_artifact["sha256"], "target_artifact.sha256"),
        "size_bytes": _integer(target_artifact["size_bytes"], "target_artifact.size_bytes"),
        "code_source_uri": _text(target_artifact["code_source_uri"], "target_artifact.code_source_uri"),
    }
    rows = deepcopy(list(raw_events))
    encoded = b"".join(canonical_direct_application_json_bytes(row) + b"\n" for row in rows)
    parsed = parse_raw_direct_application(encoded)
    _require(parsed == rows, "raw events are not canonical admitted material")
    capture_id = rows[0]["capture_id"]
    transform = next(row["payload"] for row in rows if row["event"] == "transform_applied")
    _require(transform["target_code_source_uri"] == artifact["code_source_uri"], "target artifact URI does not match the transform event")
    applications = []
    for row in rows:
        if row["event"] == "application_completed":
            payload = row["payload"]
            applications.append({
                "sequence": row["sequence"],
                "application_id": payload["application_id"],
                "target_class": payload["target_class"],
                "mixins": payload["mixins"],
                "outcome": "completed",
            })
    normalized_limitations = sorted(set(MANDATORY_LIMITATIONS) | {
        _text(value, f"limitations[{index}]")
        for index, value in enumerate(limitations)
    })
    receipt = {
        "format": DIRECT_APPLICATION_RECEIPT_FORMAT,
        "schema_version": 1,
        "receipt_id": "",
        "canonicalization": CANONICALIZATION_ID,
        "session": {"capture_id": capture_id, "launch_id": launch_id, "profile_id": profile_id, "side": side},
        "inputs": normalized_inputs,
        "target_artifact": artifact,
        "observer": {
            "target_class": EXPECTED_APPLICATOR_CLASS.replace("/", "."),
            "input_sha256": transform["input_sha256"],
            "instrumented_output_sha256": transform["output_sha256"],
            "defining_loader_class": transform["defining_loader_class"],
        },
        "applications": applications,
        "state": "complete",
        "limitations": normalized_limitations,
    }
    material = deepcopy(receipt)
    material.pop("receipt_id")
    receipt["receipt_id"] = DIRECT_APPLICATION_RECEIPT_PREFIX + hashlib.sha256(
        canonical_direct_application_json_bytes(material)
    ).hexdigest()
    return parse_direct_application_receipt(receipt)


def parse_direct_application_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "direct-application receipt must be an object")
    _closed(value, frozenset({
        "format", "schema_version", "receipt_id", "canonicalization", "session",
        "inputs", "target_artifact", "observer", "applications", "state",
        "limitations",
    }), "direct-application receipt")
    _require(value["format"] == DIRECT_APPLICATION_RECEIPT_FORMAT, "direct-application receipt format drifted")
    _require(value["schema_version"] == 1, "direct-application receipt schema version drifted")
    _require(value["canonicalization"] == CANONICALIZATION_ID, "direct-application receipt canonicalization drifted")
    _require(value["state"] == "complete", "direct-application receipt is incomplete")

    session = value["session"]
    _require(isinstance(session, Mapping), "direct-application receipt session must be an object")
    _closed(session, frozenset({"capture_id", "launch_id", "profile_id", "side"}), "direct-application receipt session")
    _text(session["capture_id"], "direct-application receipt session.capture_id")
    _text(session["launch_id"], "direct-application receipt session.launch_id")
    _text(session["profile_id"], "direct-application receipt session.profile_id")
    _require(session["side"] in {"client", "dedicated_server", "integrated_server"}, "direct-application receipt session side is unsupported")

    inputs = value["inputs"]
    _require(isinstance(inputs, Mapping) and set(inputs) == set(_INPUT_NAMES), "direct-application receipt inputs are not the exact V1 set")
    for name in _INPUT_NAMES:
        _file(inputs[name], f"direct-application receipt inputs.{name}")

    artifact = value["target_artifact"]
    _require(isinstance(artifact, Mapping), "direct-application receipt target artifact must be an object")
    _closed(artifact, frozenset({"label", "sha256", "size_bytes", "code_source_uri"}), "direct-application receipt target artifact")
    _text(artifact["label"], "direct-application receipt target_artifact.label")
    _sha256(artifact["sha256"], "direct-application receipt target_artifact.sha256")
    _integer(artifact["size_bytes"], "direct-application receipt target_artifact.size_bytes")
    _text(artifact["code_source_uri"], "direct-application receipt target_artifact.code_source_uri")

    observer = value["observer"]
    _require(isinstance(observer, Mapping), "direct-application receipt observer must be an object")
    _closed(observer, frozenset({
        "target_class", "input_sha256", "instrumented_output_sha256",
        "defining_loader_class",
    }), "direct-application receipt observer")
    _require(observer["target_class"] == EXPECTED_APPLICATOR_CLASS.replace("/", "."), "direct-application receipt observer target drifted")
    _require(_sha256(observer["input_sha256"], "direct-application receipt observer.input_sha256") == EXPECTED_APPLICATOR_SHA256, "direct-application receipt observer input hash drifted")
    _sha256(observer["instrumented_output_sha256"], "direct-application receipt observer.instrumented_output_sha256")
    _text(observer["defining_loader_class"], "direct-application receipt observer.defining_loader_class")

    applications = value["applications"]
    _require(isinstance(applications, list) and bool(applications), "direct-application receipt has no applications")
    application_ids: set[int] = set()
    target_classes: set[str] = set()
    sequences: list[int] = []
    for index, application in enumerate(applications):
        context = f"direct-application receipt applications[{index}]"
        _require(isinstance(application, Mapping), f"{context} must be an object")
        _closed(application, frozenset({
            "sequence", "application_id", "target_class", "mixins", "outcome",
        }), context)
        sequence = _integer(application["sequence"], f"{context}.sequence", minimum=1)
        application_id = _integer(application["application_id"], f"{context}.application_id", minimum=1)
        target_class = _text(application["target_class"], f"{context}.target_class")
        mixins = _strings(application["mixins"], f"{context}.mixins")
        _require(bool(mixins), f"{context}.mixins must be nonempty")
        _require(application["outcome"] == "completed", f"{context}.outcome must be completed")
        _require(application_id not in application_ids, "direct-application receipt repeats an application ID")
        _require(target_class not in target_classes, "direct-application receipt repeats a target class")
        application_ids.add(application_id)
        target_classes.add(target_class)
        sequences.append(sequence)
    _require(sequences == sorted(set(sequences)), "direct-application receipt application sequences must be increasing and unique")

    limitations = _strings(value["limitations"], "direct-application receipt limitations")
    _require(limitations == sorted(set(limitations)), "direct-application receipt limitations must be sorted and unique")
    _require(set(MANDATORY_LIMITATIONS) <= set(limitations), "direct-application receipt omits mandatory limitations")
    material = deepcopy(dict(value))
    supplied = material.pop("receipt_id")
    _require(isinstance(supplied, str) and supplied.startswith(DIRECT_APPLICATION_RECEIPT_PREFIX), "direct-application receipt ID is invalid")
    expected_id = DIRECT_APPLICATION_RECEIPT_PREFIX + hashlib.sha256(
        canonical_direct_application_json_bytes(material)
    ).hexdigest()
    _require(supplied == expected_id, "direct-application receipt identity mismatch")
    return deepcopy(dict(value))


def write_direct_application_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    admitted = parse_direct_application_receipt(receipt)
    _require(path.is_absolute(), "direct-application receipt output must be absolute")
    _require(not path.exists() and not path.is_symlink(), "direct-application receipt output must be absent")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".direct-application-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical_direct_application_json_bytes(admitted) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
