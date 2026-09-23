"""Pure, evidence-preserving saved-check summaries and directional comparison.

Comparison never changes either run's outcome and never infers edit causation.
"""
from collections import Counter
import re

from .developer_checks import content_id

FORMAT = "workbench-check-comparison-v2"
STATUSES = {"newly-observed", "persistent", "occurrence-count-changed", "no-longer-observed", "not-comparable"}


def group_findings(interpretation):
    groups = {}
    for index, finding in enumerate((interpretation or {}).get("findings", [])):
        metadata = finding.get("diagnostic")
        if (not isinstance(metadata, dict) or set(metadata) != {"key", "family", "subject", "origin", "guidance"}
                or re.fullmatch(r"diagnostic:sha256:[0-9a-f]{64}", metadata.get("key", "")) is None
                or not all(isinstance(metadata[key], str) and 0 < len(metadata[key]) <= 8000 for key in ("family", "subject", "guidance"))
                or not isinstance(metadata["origin"], dict)
                or set(metadata["origin"]) != {"kind", "name", "basis"}
                or metadata["origin"]["kind"] not in {"unknown", "pack-source", "workbench", "host-environment", "dependency"}
                or not all(isinstance(value, str) and 0 < len(value) <= 8000 for value in metadata["origin"].values())):
            raise ValueError("invalid diagnostic identity or ownership explanation")
        key = metadata["key"]
        group = groups.setdefault(key, {**metadata, "category": finding["category"], "severity": finding["severity"], "blocking": finding["blocking"], "message": finding["message"], "finding_indices": [], "evidence": []})
        if any(group[field] != finding[field] for field in ("category", "severity", "blocking")) or any(group[field] != metadata[field] for field in metadata):
            raise ValueError("diagnostic identity has conflicting meaning")
        group["finding_indices"].append(index)
        for ref in finding["evidence"]:
            if ref not in group["evidence"]:
                group["evidence"].append(ref)
    return [{**groups[key], "occurrence_count": len(groups[key]["finding_indices"])} for key in sorted(groups)]


def validate_provenance(value):
    if not isinstance(value, dict) or set(value) != {"format", "source_labels", "runtime", "image", "host", "check", "timeout_seconds"} or value["format"] != "workbench-check-provenance-v1":
        raise ValueError("invalid check provenance")
    if any(not isinstance(value[key], dict) or not value[key] for key in ("source_labels", "runtime", "image", "host", "check")):
        raise ValueError("incomplete check provenance")
    if value["source_labels"].get("release_verified") is not False or type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 3600:
        raise ValueError("invalid provenance release claim or timeout")
    if value["image"].get("id") is None or not value["image"].get("binding") or not value["host"].get("environment_sha256"):
        raise ValueError("incomplete runtime or host identity")
    return value


def _complete(result):
    value = result.get("interpretation")
    return bool(value and value["complete_logs"] and not value["truncated"] and value["runtime_observations"]["complete"]
                and result["execution"]["state"] == "closed"
                and result["execution"].get("stop_reason") not in {"cancelled", "timed-out"}
                and result["error"] is None)


def compare_results(reference, candidate):
    from .developer_checks import validate_result
    from .check_assertions import compare_assertions
    for value in (reference, candidate):
        validate_result(value)
    if reference["id"] == candidate["id"]:
        raise ValueError("select two distinct retained runs")
    reasons = []
    for key in ("workspace_uri", "selection_id", "provider", "image_id"):
        if reference[key] != candidate[key]:
            reasons.append(key + " differs")
    for key in ("runtime", "image", "host", "check", "timeout_seconds"):
        if reference["provenance"][key] != candidate["provenance"][key]:
            reasons.append("provenance." + key + " differs")
    for label, result in (("reference", reference), ("candidate", candidate)):
        if not _complete(result):
            reasons.append(label + " lacks complete, closed evidence")
    left_observations = (reference.get("interpretation") or {}).get("runtime_observations")
    right_observations = (candidate.get("interpretation") or {}).get("runtime_observations")
    if left_observations != right_observations:
        reasons.append("observed runtime environment differs or is missing on one side")
    before = {row["key"]: row for row in reference["diagnostics"]}
    after = {row["key"]: row for row in candidate["diagnostics"]}
    reference_checkpoint = (reference.get("interpretation") or {}).get("observation", {}).get("state") == "checkpoint"
    candidate_checkpoint = (candidate.get("interpretation") or {}).get("observation", {}).get("state") == "checkpoint"
    rows = []
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key), after.get(key)
        reason = None
        if reasons:
            status, reason = "not-comparable", "Run-level comparability requirements were not met."
        elif left and right:
            if left["occurrence_count"] == right["occurrence_count"]:
                status = "persistent"
            elif reference_checkpoint and candidate_checkpoint:
                status = "occurrence-count-changed"
            else:
                status, reason = "not-comparable", "Different counts with unequal/incomplete checkpoint coverage do not prove a frequency change."
        elif right and reference_checkpoint:
            status = "newly-observed"
        elif left and candidate_checkpoint:
            status = "no-longer-observed"
        else:
            status, reason = "not-comparable", "The run lacking this finding did not reach the full checkpoint. Absence is not resolution."
        rows.append({"key": key, "status": status, "reason": reason, "reference": left, "candidate": right})
    counts = Counter(row["status"] for row in rows)
    body = {
        "format": FORMAT, "workspace_uri": candidate["workspace_uri"], "selection_id": candidate["selection_id"],
        "state": "not-comparable" if reasons else "partial" if counts["not-comparable"] or not (reference_checkpoint and candidate_checkpoint) else "compared",
        "reference": {**{key: reference[key] for key in ("id", "attempt_id", "state", "candidate_id", "provenance")}, "source": reference["candidate"]},
        "candidate": {**{key: candidate[key] for key in ("id", "attempt_id", "state", "candidate_id", "provenance")}, "source": candidate["candidate"]},
        "reasons": reasons, "groups": rows, "counts": {status: counts[status] for status in sorted(STATUSES)},
        "assertions": compare_assertions(reference, candidate, reasons),
        "limitations": ["Observed diagnostic differences, not proof that an edit caused them.", "Persistent errors and unknowns retain their original severity and outcome.", "Missing findings are not proof of a fix or recipe correctness.", "Host and graphics observations are bounded, not full hardware or network equivalence."],
        "authority": {"runtime_launched": False, "source_mutated": False, "qualification_granted": False, "outcomes_promoted": False},
    }
    return {**body, "id": content_id("check-comparison", body)}


def validate_comparison(value):
    body = {key: row for key, row in value.items() if key != "id"}
    if (value.get("format") != FORMAT or value.get("id") != content_id("check-comparison", body)
            or value.get("state") not in {"compared", "partial", "not-comparable"}
            or value.get("authority") != {"runtime_launched": False, "source_mutated": False, "qualification_granted": False, "outcomes_promoted": False}
            or any(row.get("status") not in STATUSES for row in value.get("groups", []))):
        raise ValueError("invalid retained check comparison")
    return value
