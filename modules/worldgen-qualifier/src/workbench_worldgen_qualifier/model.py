"""Identity, profile, and retained-report rules for Worldgen Qualifier V1."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from workbench_worldgen_cockpit.model import (
    FileBinding,
    load_json_file,
    sha256_file,
    write_json_atomic,
)


PROFILE_FORMAT = "workbench-worldgen-qualification-profile-v1"
QUALIFICATION_FORMAT = "workbench-worldgen-qualification-report-v1"
RISK_SCAN_FORMAT = "workbench-worldgen-static-risk-report-v1"
PLAN_FORMAT = "workbench-worldgen-qualification-plan-v1"
SESSION_FORMAT = "workbench-worldgen-qualification-session-v1"
QUALIFICATION_PREFIX = "workbench-worldgen-qualification:sha256:"
RISK_PREFIX = "workbench-worldgen-static-risk:sha256:"
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,83}$")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class QualifierError(ValueError):
    """A qualification input is unsafe, malformed, incomplete, or contradictory."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise QualifierError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise QualifierError(f"cannot canonically encode qualifier data: {exc}") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def content_id(prefix: str, value: Mapping[str, Any], field: str) -> str:
    payload = deepcopy(dict(value))
    payload.pop(field, None)
    return prefix + sha256_json(payload)


def _exact(value: Any, expected: set[str], context: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{context} must be an object")
    actual = set(value)
    require(
        actual == expected,
        f"{context} fields mismatch: missing={sorted(expected - actual)!r}, "
        f"unknown={sorted(actual - expected)!r}",
    )
    return value


def _relative_file(root: Path, raw: Any, context: str) -> Path:
    require(isinstance(raw, str) and bool(raw), f"{context} must be nonempty text")
    relative = Path(raw)
    require(not relative.is_absolute() and ".." not in relative.parts, f"{context} must stay inside Workbench")
    current = root.resolve()
    for part in relative.parts:
        current /= part
        require(not current.is_symlink(), f"{context} cannot traverse a symlink: {current}")
    resolved = current.resolve(strict=True)
    require(resolved.is_file(), f"{context} is not a regular file: {resolved}")
    require(root.resolve() in resolved.parents, f"{context} escapes Workbench")
    return resolved


def _positive_int(value: Any, context: str) -> int:
    require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"{context} must be a positive integer")
    return value


def load_profile(path: Path | str, *, root: Path) -> tuple[dict[str, Any], FileBinding]:
    try:
        value, binding = load_json_file(path, context="Worldgen qualification profile", maximum_bytes=1024 * 1024)
    except (OSError, ValueError) as exc:
        raise QualifierError(str(exc)) from exc
    _exact(
        value,
        {
            "format", "schema_version", "profile_id", "pack_profile",
            "cockpit_profile", "capabilities", "suites", "intents", "domains",
            "risk_policy", "limits", "boundaries",
        },
        "Worldgen qualification profile",
    )
    require(value["format"] == PROFILE_FORMAT and value["schema_version"] == 1, "unsupported qualification profile version")
    require(isinstance(value["profile_id"], str) and SAFE_ID_RE.fullmatch(value["profile_id"]) is not None, "qualification profile_id is invalid")
    require(isinstance(value["pack_profile"], str) and SAFE_ID_RE.fullmatch(value["pack_profile"]) is not None, "qualification pack_profile is invalid")

    capabilities = value["capabilities"]
    require(isinstance(capabilities, Mapping) and capabilities, "qualification capabilities must be a nonempty object")
    for capability, state in capabilities.items():
        require(SAFE_ID_RE.fullmatch(str(capability)) is not None, f"invalid capability id: {capability!r}")
        require(state in {"supported", "partial", "static-only", "unsupported"}, f"invalid capability state for {capability}")

    suites = value["suites"]
    require(isinstance(suites, Mapping) and suites, "qualification suites must be a nonempty object")
    for suite_id, suite in suites.items():
        require(SAFE_ID_RE.fullmatch(str(suite_id)) is not None, f"invalid qualification suite id: {suite_id!r}")
        _exact(suite, {"mode", "seeds", "regions", "orders", "heaps", "pair_repetitions", "required_capabilities"}, f"qualification suite {suite_id}")
        require(suite["mode"] in {"fast", "debug", "performance"}, f"suite {suite_id} mode is invalid")
        require(isinstance(suite["seeds"], list) and suite["seeds"] and all(isinstance(item, int) and not isinstance(item, bool) for item in suite["seeds"]), f"suite {suite_id} seeds are invalid")
        require(len(suite["seeds"]) == len(set(suite["seeds"])), f"suite {suite_id} seeds must be unique")
        require(isinstance(suite["regions"], list) and suite["regions"] and all(isinstance(item, str) and item for item in suite["regions"]), f"suite {suite_id} regions are invalid")
        require(len(suite["regions"]) == len(set(suite["regions"])), f"suite {suite_id} regions must be unique")
        require(isinstance(suite["orders"], list) and suite["orders"] and set(suite["orders"]) <= {"baseline-first", "candidate-first"}, f"suite {suite_id} orders are invalid")
        require(len(suite["orders"]) == len(set(suite["orders"])), f"suite {suite_id} orders must be unique")
        require(isinstance(suite["heaps"], list) and suite["heaps"] and all(re.fullmatch(r"[1-9][0-9]*[KMGkmg]", str(item)) for item in suite["heaps"]), f"suite {suite_id} heaps are invalid")
        require(len(suite["heaps"]) == len(set(suite["heaps"])), f"suite {suite_id} heaps must be unique")
        _positive_int(suite["pair_repetitions"], f"suite {suite_id} pair_repetitions")
        required = suite["required_capabilities"]
        require(isinstance(required, list) and len(required) == len(set(required)) and set(required) <= set(capabilities), f"suite {suite_id} required capabilities are invalid")

    domains = value["domains"]
    require(isinstance(domains, Mapping) and domains, "qualification domains must be a nonempty object")
    allowed_signals = {
        "semantic", "changed_block_positions", "height_changed_columns",
        "biome_changed_columns", "cave-space", "lithology", "ore",
        "physical-fluid", "other-block",
    }
    for domain_id, domain in domains.items():
        require(SAFE_ID_RE.fullmatch(str(domain_id)) is not None, f"invalid qualification domain id: {domain_id!r}")
        _exact(domain, {"label", "signals"}, f"qualification domain {domain_id}")
        require(isinstance(domain["label"], str) and domain["label"], f"domain {domain_id} label is invalid")
        require(isinstance(domain["signals"], list) and domain["signals"] and set(domain["signals"]) <= allowed_signals, f"domain {domain_id} signals are invalid")
        require(len(domain["signals"]) == len(set(domain["signals"])), f"domain {domain_id} signals must be unique")

    intents = value["intents"]
    require(isinstance(intents, Mapping) and intents, "qualification intents must be a nonempty object")
    for intent_id, intent in intents.items():
        require(SAFE_ID_RE.fullmatch(str(intent_id)) is not None, f"invalid qualification intent id: {intent_id!r}")
        _exact(intent, {"required_domains", "required_evidence", "required_capabilities", "minimum_seeds", "risk_gate", "assurance"}, f"qualification intent {intent_id}")
        require(isinstance(intent["required_domains"], list) and intent["required_domains"] and set(intent["required_domains"]) <= set(domains), f"intent {intent_id} domains are invalid")
        require(len(intent["required_domains"]) == len(set(intent["required_domains"])), f"intent {intent_id} domains must be unique")
        allowed_evidence = {"semantic", "final_state", "statistical", "subsurface", "causal", "performance", "observer_overhead"}
        require(isinstance(intent["required_evidence"], list) and set(intent["required_evidence"]) <= allowed_evidence, f"intent {intent_id} evidence is invalid")
        require(len(intent["required_evidence"]) == len(set(intent["required_evidence"])), f"intent {intent_id} evidence must be unique")
        require(isinstance(intent["required_capabilities"], list) and set(intent["required_capabilities"]) <= set(capabilities), f"intent {intent_id} capabilities are invalid")
        require(len(intent["required_capabilities"]) == len(set(intent["required_capabilities"])), f"intent {intent_id} capabilities must be unique")
        _positive_int(intent["minimum_seeds"], f"intent {intent_id} minimum_seeds")
        require(intent["risk_gate"] in {"report", "review", "reject"}, f"intent {intent_id} risk_gate is invalid")
        require(intent["assurance"] in {"empirical", "stage-exact"}, f"intent {intent_id} assurance is invalid")

    risk = _exact(value["risk_policy"], {"scan_runtime_mods", "dispositions"}, "qualification risk_policy")
    require(isinstance(risk["scan_runtime_mods"], bool), "risk_policy.scan_runtime_mods must be boolean")
    require(isinstance(risk["dispositions"], list), "risk_policy.dispositions must be a list")
    disposition_keys: set[tuple[str, str, str]] = set()
    for row in risk["dispositions"]:
        _exact(row, {"jar_sha256", "class_name", "rule_id", "disposition", "rationale"}, "risk disposition")
        require(SHA256_RE.fullmatch(str(row["jar_sha256"])) is not None, "risk disposition jar SHA-256 is invalid")
        require(isinstance(row["class_name"], str) and row["class_name"], "risk disposition class_name is invalid")
        require(isinstance(row["rule_id"], str) and row["rule_id"], "risk disposition rule_id is invalid")
        require(row["disposition"] in {"accepted", "patched", "rejected"}, "risk disposition state is invalid")
        require(isinstance(row["rationale"], str) and row["rationale"], "risk disposition rationale is required")
        key = (row["jar_sha256"], row["class_name"], row["rule_id"])
        require(key not in disposition_keys, f"duplicate risk disposition: {key!r}")
        disposition_keys.add(key)

    limits = _exact(value["limits"], {"max_cells", "max_cross_comparisons", "max_jars", "max_classes", "max_class_bytes", "max_uncompressed_bytes", "max_findings"}, "qualification limits")
    for key, number in limits.items():
        _positive_int(number, f"qualification limit {key}")
    require(limits["max_cells"] <= 64 and limits["max_jars"] <= 256 and limits["max_classes"] <= 250000, "qualification safety limits exceed V1 hard bounds")

    boundaries = _exact(value["boundaries"], {"atlas_remains_causal_authority", "strata_remains_final_state_authority", "crucible_remains_experiment_authority", "static_risk_is_not_causal_proof", "unknown_evidence_fails_closed", "profile_is_pack_specific"}, "qualification boundaries")
    require(all(item is True for item in boundaries.values()), "qualification authority and fail-closed boundaries must remain enabled")

    result = deepcopy(value)
    result["_path"] = str(binding.path)
    result["_cockpit_profile"] = str(_relative_file(root, value["cockpit_profile"], "cockpit_profile"))
    return result, binding


def resolve_profile_path(root: Path, name: str) -> Path:
    require(SAFE_ID_RE.fullmatch(name) is not None, f"invalid pack profile name: {name!r}")
    return root / "profiles" / "packs" / name / "worldgen" / "worldgen-qualification-profile-v1.json"


def validate_qualification(report: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "format", "schema_version", "report_id", "created_at", "status",
        "assurance", "profile", "qualification", "matrix", "gates", "risk_scan",
        "decision", "sources", "navigation", "limitations", "reproduction_command",
    }
    _exact(report, required, "Worldgen qualification report")
    require(report["format"] == QUALIFICATION_FORMAT and report["schema_version"] == 1, "unsupported qualification report version")
    require(report["status"] in {"accepted-exact", "accepted-empirical", "accepted-bounded", "accepted-patched", "inconclusive", "rejected-unstable-critical", "failed"}, "qualification status is invalid")
    require(report["assurance"] in {"exact-in-declared-matrix", "empirical-in-declared-matrix", "bounded-equivalence", "patched-exact-artifact", "none"}, "qualification assurance is invalid")
    require(isinstance(report["limitations"], list) and len(report["limitations"]) == len(set(report["limitations"])), "qualification limitations must be unique")
    require(report["report_id"] == content_id(QUALIFICATION_PREFIX, report, "report_id"), "qualification report identity drift")
    return deepcopy(dict(report))


def load_qualification(path: Path | str) -> tuple[dict[str, Any], FileBinding]:
    try:
        value, binding = load_json_file(path, context="Worldgen qualification report")
    except (OSError, ValueError) as exc:
        raise QualifierError(str(exc)) from exc
    return validate_qualification(value), binding


__all__ = [
    "LABEL_RE", "PLAN_FORMAT", "PROFILE_FORMAT", "QUALIFICATION_FORMAT",
    "QUALIFICATION_PREFIX", "RISK_PREFIX", "RISK_SCAN_FORMAT", "SESSION_FORMAT",
    "QualifierError", "canonical_json_bytes", "content_id", "load_profile",
    "load_qualification", "require", "resolve_profile_path", "sha256_file",
    "sha256_json", "utc_now", "validate_qualification", "write_json_atomic",
]
