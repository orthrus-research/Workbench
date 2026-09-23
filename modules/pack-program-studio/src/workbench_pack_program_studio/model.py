"""Shared identities and semantic validation for Pack Program Studio V1."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from typing import Any, Mapping


REPORT_FORMAT = "workbench-groovy-pack-program-report-v1"
REPORT_SCHEMA_VERSION = 1
PROGRAM_FORMAT = "workbench-groovy-static-program-v1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class PackProgramError(RuntimeError):
    """An input cannot support a truthful Pack Program Studio result."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def content_id(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def report_identity(report: Mapping[str, Any]) -> str:
    payload = dict(report)
    payload.pop("report_id", None)
    return content_id("workbench-groovy-pack-program-report:sha256:", payload)


def validate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate cross-field invariants not expressible in the JSON schema."""

    if not isinstance(report, Mapping):
        raise PackProgramError("Groovy program report must be an object")
    required = {
        "format",
        "schema_version",
        "report_id",
        "operation_class",
        "authority",
        "profile",
        "request",
        "candidate",
        "baseline",
        "comparison",
        "change_assessment",
        "runtime_evidence",
        "summary",
        "limitations",
        "horizons",
    }
    if set(report) != required:
        missing = sorted(required - set(report))
        extra = sorted(set(report) - required)
        raise PackProgramError(
            f"Groovy program report keys differ; missing={missing}, extra={extra}"
        )
    if report["format"] != REPORT_FORMAT or report["schema_version"] != 1:
        raise PackProgramError("unsupported Groovy program report format")
    if report["operation_class"] != "read-only":
        raise PackProgramError("Groovy program analysis must remain read-only")
    if report["report_id"] != report_identity(report):
        raise PackProgramError("Groovy program report identity does not match content")

    candidate = _validate_program(report["candidate"], "candidate")
    baseline_value = report["baseline"]
    baseline = (
        None
        if baseline_value is None
        else _validate_program(baseline_value, "baseline")
    )
    comparison = report["comparison"]
    if not isinstance(comparison, Mapping):
        raise PackProgramError("Groovy program comparison must be an object")
    if baseline is None:
        if comparison.get("state") != "not-requested":
            raise PackProgramError("comparison without a baseline must be not-requested")
    else:
        if comparison.get("candidate_program_id") != candidate["program_id"]:
            raise PackProgramError("comparison candidate program binding is stale")
        if comparison.get("baseline_program_id") != baseline["program_id"]:
            raise PackProgramError("comparison baseline program binding is stale")

    summary = report["summary"]
    if not isinstance(summary, Mapping):
        raise PackProgramError("Groovy program summary must be an object")
    if summary.get("candidate_program_id") != candidate["program_id"]:
        raise PackProgramError("summary candidate program binding is stale")
    if summary.get("files") != candidate["summary"]["files"]:
        raise PackProgramError("summary file count does not match candidate")
    if summary.get("effects") != candidate["summary"]["effects"]:
        raise PackProgramError("summary effect count does not match candidate")
    if summary.get("collision_candidates") != len(candidate["collisions"]):
        raise PackProgramError("summary collision count does not match candidate")
    return dict(report)


def _validate_program(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PackProgramError(f"{label} Groovy program must be an object")
    required = {
        "format",
        "program_id",
        "binding",
        "run_config",
        "files",
        "stages",
        "symbols",
        "dependencies",
        "effects",
        "collisions",
        "summary",
        "coverage",
        "limitations",
    }
    if set(value) != required:
        raise PackProgramError(f"{label} Groovy program has unexpected keys")
    if value["format"] != PROGRAM_FORMAT:
        raise PackProgramError(f"{label} Groovy program format is unsupported")
    files = value["files"]
    effects = value["effects"]
    collisions = value["collisions"]
    if not isinstance(files, list) or not isinstance(effects, list) or not isinstance(collisions, list):
        raise PackProgramError(f"{label} Groovy program lists are malformed")
    file_paths = [row.get("path") for row in files if isinstance(row, Mapping)]
    if len(file_paths) != len(files) or len(file_paths) != len(set(file_paths)):
        raise PackProgramError(f"{label} Groovy file identities are missing or duplicate")
    effect_ids = [row.get("effect_id") for row in effects if isinstance(row, Mapping)]
    if len(effect_ids) != len(effects) or len(effect_ids) != len(set(effect_ids)):
        raise PackProgramError(f"{label} Groovy effect identities are missing or duplicate")
    effect_id_set = set(effect_ids)
    for collision in collisions:
        if not isinstance(collision, Mapping):
            raise PackProgramError(f"{label} collision candidate is malformed")
        occurrence_ids = collision.get("effect_ids")
        if not isinstance(occurrence_ids, list) or not occurrence_ids:
            raise PackProgramError(f"{label} collision candidate has no effects")
        if not set(occurrence_ids) <= effect_id_set:
            raise PackProgramError(f"{label} collision references an unknown effect")
    summary = value["summary"]
    if not isinstance(summary, Mapping):
        raise PackProgramError(f"{label} Groovy summary is malformed")
    if summary.get("files") != len(files) or summary.get("effects") != len(effects):
        raise PackProgramError(f"{label} Groovy summary counts are stale")
    expected_by_rule = dict(sorted(Counter(row["rule_id"] for row in effects).items()))
    if summary.get("effects_by_rule") != expected_by_rule:
        raise PackProgramError(f"{label} Groovy rule counts are stale")
    binding = value["binding"]
    if not isinstance(binding, Mapping):
        raise PackProgramError(f"{label} binding is malformed")
    for field in ("source_sha256", "run_config_sha256"):
        if not isinstance(binding.get(field), str) or not _DIGEST_RE.fullmatch(binding[field]):
            raise PackProgramError(f"{label} binding {field} is malformed")
    identity_payload = {
        "profile_id": binding.get("profile_id"),
        "platform_profile_id": binding.get("platform_profile_id"),
        "source_sha256": binding.get("source_sha256"),
        "run_config_sha256": binding.get("run_config_sha256"),
        "side": binding.get("side"),
        "packmode": binding.get("packmode"),
        "debug": binding.get("debug"),
        "files": [
            {"path": row["path"], "sha256": row["sha256"], "stage": row["stage"]}
            for row in files
        ],
    }
    expected_id = content_id("workbench-groovy-static-program:sha256:", identity_payload)
    if value["program_id"] != expected_id:
        raise PackProgramError(f"{label} Groovy program identity does not match inputs")
    return value


__all__ = [
    "PROGRAM_FORMAT",
    "REPORT_FORMAT",
    "REPORT_SCHEMA_VERSION",
    "PackProgramError",
    "canonical_bytes",
    "content_id",
    "report_identity",
    "sha256_bytes",
    "validate_report",
]
