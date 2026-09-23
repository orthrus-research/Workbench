"""Strict admission for Cleanroom case-worker publication receipts."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .bundle import CaptureValidationError, canonical_json_sha256


CLEANROOM_CASE_WORKER_RECEIPT_SCHEMA = (
    "workbench.crucible.cleanroom-case-worker-receipt.v1"
)
CLEANROOM_CASE_WORKER_RECEIPT_PREFIX = (
    "crucible-cleanroom-case-worker-receipt:sha256:"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_KEYS = {
    "receipt_id", "schema", "spec_id", "spec_file_sha256",
    "raw_ndjson_sha256", "raw_schema_id", "raw_schema_sha256",
    "probe_plan_id", "probe_plan_file_sha256", "candidate_lock_file_sha256",
    "fixture_artifact_file_sha256", "installed_mod_set_manifest_file_sha256",
    "installed_mod_set_manifest_canonical_sha256",
    "configuration_set_manifest_file_sha256",
    "configuration_set_manifest_canonical_sha256", "fixture_result_file_sha256",
    "fixture_result_canonical_sha256", "fixture_route_order",
    "runtime_mod_inventory_sha256", "capture_nonce", "raw_row_count",
    "raw_completion_state", "class_dump_inventory_sha256",
    "foundation_class_dump_manifest_format", "foundation_class_dump_class_count",
    "foundation_class_dump_total_size_bytes",
    "foundation_class_dump_manifest_sha256", "actor_inventory_sha256", "run_id",
    "publication_state", "publication_id", "bundle_canonical_sha256",
    "bundle_file_sha256", "bundle_record_count", "projection_id",
    "projection_canonical_sha256", "projection_file_sha256",
}
_REQUIRED_SHA256_FIELDS = {
    "spec_file_sha256", "raw_ndjson_sha256", "raw_schema_sha256",
    "probe_plan_file_sha256", "candidate_lock_file_sha256",
    "fixture_artifact_file_sha256", "installed_mod_set_manifest_file_sha256",
    "installed_mod_set_manifest_canonical_sha256",
    "configuration_set_manifest_file_sha256",
    "configuration_set_manifest_canonical_sha256", "class_dump_inventory_sha256",
    "foundation_class_dump_manifest_sha256", "actor_inventory_sha256",
    "bundle_canonical_sha256", "bundle_file_sha256", "projection_canonical_sha256",
    "projection_file_sha256",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _closed(value: Any, keys: set[str], context: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    _require(
        actual == keys,
        f"{context} fields mismatch: missing={sorted(keys - actual)!r}, "
        f"unknown={sorted(actual - keys)!r}",
    )
    return value


def _text(value: Any, context: str) -> str:
    _require(
        isinstance(value, str)
        and bool(value)
        and len(value) <= 8192
        and not any(character in value for character in "\r\n\x00"),
        f"{context} must be bounded nonempty single-line text",
    )
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    _require(
        type(value) is int and value >= minimum,
        f"{context} must be an integer >= {minimum}",
    )
    return value


def _content_identity(value: Any, prefix: str, context: str) -> str:
    identity = _text(value, context)
    _require(identity.startswith(prefix), f"{context} prefix is invalid")
    _sha256(identity[len(prefix):], f"{context} digest")
    return identity


def parse_cleanroom_case_worker_receipt(value: Any) -> dict[str, Any]:
    """Validate a decoded V1 worker receipt and rederive its state semantics."""

    receipt = _closed(value, _RECEIPT_KEYS, "Cleanroom case-worker receipt")
    _require(
        receipt["schema"] == CLEANROOM_CASE_WORKER_RECEIPT_SCHEMA,
        "Cleanroom case-worker receipt schema mismatch",
    )
    material = deepcopy(dict(receipt))
    receipt_id = material.pop("receipt_id")
    _require(
        isinstance(receipt_id, str)
        and receipt_id
        == CLEANROOM_CASE_WORKER_RECEIPT_PREFIX + canonical_json_sha256(material),
        "Cleanroom case-worker receipt content identity mismatch",
    )
    for field in _REQUIRED_SHA256_FIELDS:
        _sha256(receipt[field], f"Cleanroom case-worker receipt {field}")
    for field in (
        "raw_schema_id", "probe_plan_id", "capture_nonce",
    ):
        _text(receipt[field], f"Cleanroom case-worker receipt {field}")
    _content_identity(
        receipt["spec_id"],
        "crucible-cleanroom-case-worker-spec:sha256:",
        "Cleanroom case-worker receipt spec_id",
    )
    _content_identity(
        receipt["run_id"],
        "crucible-worldgen-run:sha256:",
        "Cleanroom case-worker receipt run_id",
    )
    _content_identity(
        receipt["projection_id"],
        "crucible-worldgen-cleanroom-bundle-projection:sha256:",
        "Cleanroom case-worker receipt projection_id",
    )
    for field in (
        "raw_row_count", "foundation_class_dump_class_count",
        "foundation_class_dump_total_size_bytes", "bundle_record_count",
    ):
        _integer(receipt[field], f"Cleanroom case-worker receipt {field}", minimum=1)
    _require(
        receipt["foundation_class_dump_manifest_format"]
        == "workbench-foundation-class-dump-manifest-v1",
        "Cleanroom case-worker receipt Foundation format mismatch",
    )

    state = receipt["publication_state"]
    _require(state in {"completed", "incomplete"}, "worker publication state is invalid")
    expected_raw_state = "complete" if state == "completed" else "incomplete"
    _require(
        receipt["raw_completion_state"] == expected_raw_state,
        "worker raw completion state contradicts publication state",
    )
    optional_completed_fields = (
        "fixture_result_file_sha256", "fixture_result_canonical_sha256",
        "runtime_mod_inventory_sha256",
    )
    if state == "completed":
        _content_identity(
            receipt["publication_id"],
            "crucible-worldgen-capture:sha256:",
            "completed worker receipt publication_id",
        )
        for field in optional_completed_fields:
            _sha256(receipt[field], f"completed worker receipt {field}")
        _require(
            receipt["fixture_route_order"] in {"forward", "reverse"},
            "completed worker receipt route order is invalid",
        )
    else:
        _content_identity(
            receipt["publication_id"],
            "crucible-worldgen-residue:sha256:",
            "incomplete worker receipt publication_id",
        )
        _require(
            all(receipt[field] is None for field in optional_completed_fields)
            and receipt["fixture_route_order"] is None,
            "incomplete worker receipt retains completed fixture-result fields",
        )
    return deepcopy(dict(receipt))


__all__ = [
    "CLEANROOM_CASE_WORKER_RECEIPT_PREFIX",
    "CLEANROOM_CASE_WORKER_RECEIPT_SCHEMA",
    "parse_cleanroom_case_worker_receipt",
]
