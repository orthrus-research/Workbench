"""Check-provider admission and saved-candidate evidence records, without Shell."""

from hashlib import sha256
import json
from workbench_api.checks import validate_observation
from workbench_api.profile_extensions import (
    require_profile_extension,
    profile_extension_identity,
)


GROUP = "workbench.developer_checks"
FORMAT = "workbench-saved-check-result-v4"


def content_id(kind, value):
    return (
        kind
        + ":sha256:"
        + sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )


def provider(profile):
    value = require_profile_extension(GROUP, profile)
    if not all(
        callable(getattr(value, name, None))
        for name in (
            "descriptor",
            "binding",
            "validate_image",
            "overlays",
            "interpret",
            "observe",
            "provenance",
            "recipe_catalog",
            "recipe_expectation",
            "assertion_observation",
            "attachment",
            "validate_attachment_image",
        )
    ):
        raise ValueError("profile lacks the complete developer-check contract")
    descriptor = value.descriptor()
    if set(descriptor) != {
        "id",
        "label",
        "platform",
        "variant",
        "side",
        "source_roots",
        "excluded_roots",
        "log_paths",
        "optional_log_globs",
        "limitations",
    }:
        raise ValueError("developer-check descriptor fields are invalid")
    identity = profile_extension_identity(GROUP, profile)
    identity["platform"] = profile_extension_identity(
        "workbench.check_platforms", descriptor["platform"]
    )
    return value, identity


def seal_result(body):
    from .check_comparison import group_findings, validate_provenance
    from .check_assertions import validate_assertion
    if body.get("format") != FORMAT or body.get("authority") != {
        "source_mutated": False,
        "construction_authorized": False,
        "qualification_granted": False,
    }:
        raise ValueError("saved check cannot authorize construction or qualification")
    if body.get("interpretation") is not None:
        validate_interpretation(body["interpretation"])
    validate_provenance(body.get("provenance"))
    assertion = validate_assertion(body.get("assertions"), body["execution"], body["interpretation"], body["error"])
    if assertion is not None and assertion["expectation"]["candidate_id"] != body["candidate_id"]:
        raise ValueError("assertion belongs to another saved candidate")
    if body.get("diagnostics") != group_findings(body.get("interpretation")) or body["provenance"]["image"]["id"] != body.get("image_id"):
        raise ValueError("saved-check summaries or image provenance disagree")
    expected = {
        "format",
        "state",
        "workspace_uri",
        "selection_id",
        "attempt_id",
        "request_id",
        "candidate_id",
        "candidate",
        "image_id",
        "provider",
        "execution",
        "cleanup",
        "error",
        "interpretation",
        "evidence",
        "attempt_uri",
        "limitations",
        "authority",
        "provenance",
        "diagnostics",
        "assertions",
    }
    if set(body) != expected or body["state"] != outcome(
        body["execution"], body["interpretation"], body["error"]
    ):
        raise ValueError(
            "saved check fields or outcome do not match the retained observations"
        )
    if body["cleanup"]["state"] not in {"not-needed", "blocked", "trashed"}:
        raise ValueError("unknown check cleanup state")
    return {**body, "id": content_id("saved-check-result", body)}


def validate_result(value):
    body = {key: item for key, item in value.items() if key != "id"}
    if seal_result(body) != value:
        raise ValueError("saved-check result identity changed")
    return value


def outcome(execution, interpretation, error):
    if error:
        return "failed"
    reason = execution.get("stop_reason")
    if reason in {"cancelled", "timed-out"}:
        return reason
    if execution.get("state") != "closed":
        return "inconclusive"
    console = execution.get("console", {})
    if console.get("outcome_failure_events", 0):
        return "failed"
    expected_stop = (
        reason in {"checkpoint-reached", "failure-observed"}
        and console.get("cancellation") is not None
    )
    if expected_stop and console.get("process_exit_code") not in {
        0,
        -2,
        -15,
        -9,
        130,
        143,
        137,
    }:
        return "failed"
    if console.get("effective_exit_code") != 0 and not expected_stop:
        return "failed"
    if interpretation is None:
        return "inconclusive"
    if interpretation.get("outcome") == "failed":
        return "failed"
    if reason == "failure-observed":
        # A terminal observation must still be supported by the final evidence.
        return "inconclusive"
    return (
        "completed" if interpretation.get("outcome") == "completed" else "inconclusive"
    )


def validate_interpretation(value):
    if not isinstance(value, dict) or set(value) != {
        "outcome", "observation", "checks", "findings", "findings_count",
        "blocking_findings_count", "unclassified_findings_count", "truncated", "complete_logs",
        "runtime_observations",
    }:
        raise ValueError("invalid saved-check interpretation")
    validate_observation(value["observation"])
    runtime = value["runtime_observations"]
    if not isinstance(runtime, dict) or set(runtime) != {"graphics", "complete"} or type(runtime["complete"]) is not bool or not isinstance(runtime["graphics"], list) or len(runtime["graphics"]) > 8 or any(not isinstance(row, str) or not row for row in runtime["graphics"]):
        raise ValueError("invalid bounded runtime observations")
    for name in ("findings_count", "blocking_findings_count", "unclassified_findings_count"):
        if type(value[name]) is not int or value[name] < 0:
            raise ValueError("invalid saved-check finding counts")
    if (
        not isinstance(value["findings"], list)
        or len(value["findings"]) != min(value["findings_count"], 1000)
        or type(value["complete_logs"]) is not bool
        or type(value["truncated"]) is not bool
        or value["truncated"] != (value["findings_count"] > 1000)
        or value["blocking_findings_count"] + value["unclassified_findings_count"] > value["findings_count"]
    ):
        raise ValueError("invalid saved-check finding bounds")
    for row in value["findings"]:
        if (
            not isinstance(row, dict)
            or row.get("category") not in {"compiler", "runtime", "environment", "informational", "unclassified"}
            or row.get("severity") not in {"error", "warning", "information"}
            or type(row.get("blocking")) is not bool
            or row["blocking"] != (row["category"] in {"compiler", "runtime", "environment"})
        ):
            raise ValueError("invalid saved-check finding classification")
        validate_observation({**value["observation"], "state": "failure", "evidence": row.get("evidence")})
    for count, predicate in (
        ("blocking_findings_count", lambda row: row["blocking"]),
        ("unclassified_findings_count", lambda row: row["category"] == "unclassified"),
    ):
        retained = sum(predicate(row) for row in value["findings"])
        if value[count] < retained or (not value["truncated"] and value[count] != retained):
            raise ValueError("saved-check finding counts disagree")
    expected = (
        "failed" if value["blocking_findings_count"]
        else "completed" if value["complete_logs"] and not value["truncated"]
        and not value["unclassified_findings_count"] and value["observation"]["state"] == "checkpoint"
        else "inconclusive"
    )
    if value["outcome"] != expected:
        raise ValueError("saved-check outcome disagrees with its classified evidence")
    return value
