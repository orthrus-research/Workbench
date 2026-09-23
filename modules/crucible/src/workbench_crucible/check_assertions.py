"""Owner-neutral expectations and assertions, independent of recipe semantics.

Profiles translate domain observations into named facts. Crucible applies exact
equality only after execution and evidence gates; it never promotes startup.
"""
import json
import re

from .developer_checks import content_id

STATES = {"matched", "mismatched", "inconclusive", "unsupported"}


def seal(kind, body):
    return {**body, "id": content_id(kind, body)}


def validate_expectation(value):
    if value is None:
        return None
    body = {key: row for key, row in value.items() if key != "id"}
    if (set(body) != {"format", "candidate_id", "source", "subject", "mode", "support", "reasons", "expected"}
            or value.get("format") != "workbench-check-expectation-v1"
            or seal("check-expectation", body) != value
            or re.fullmatch(r"candidate:sha256:[0-9a-f]{64}", value["candidate_id"]) is None
            or value["mode"] not in {"present", "absent"}
            or value["support"] not in {"supported", "unsupported"}
            or not isinstance(value["subject"], dict) or not isinstance(value["source"], dict)
            or not isinstance(value["reasons"], list) or not isinstance(value["expected"], dict)
            or not 1 <= len(value["expected"]) <= 16
            or len(json.dumps(value, allow_nan=False)) > 65536):
        raise ValueError("invalid bounded check expectation")
    return value


def evaluate(expectation, observation, execution, interpretation, error):
    if expectation is None:
        if observation is not None:
            raise ValueError("observation has no confirmed expectation")
        return None
    validate_expectation(expectation)
    if (not isinstance(observation, dict) or set(observation) != {"state", "facts", "evidence", "details", "reasons"}
            or observation["state"] not in {"complete", "incomplete", "unsupported", "ambiguous"}
            or not isinstance(observation["facts"], dict) or not isinstance(observation["details"], dict)
            or not isinstance(observation["evidence"], list) or len(observation["evidence"]) > 32
            or not isinstance(observation["reasons"], list)
            or len(json.dumps(observation, allow_nan=False)) > 12 * 1024 * 1024):
        raise ValueError("invalid assertion observation")
    facts = observation["facts"]
    if "lifecycle" in observation["details"]:
        from .check_lifecycle import validate_lifecycle
        validate_lifecycle(observation["details"]["lifecycle"], observation["details"].get("capture", {}).get("lifecycle"))
    if "decisions" in observation["details"]:
        from .check_decisions import validate_decisions
        validate_decisions(observation["details"]["decisions"], observation["details"].get("capture", {}).get("decisions"))
    if "explanation" in observation["details"]:
        from .check_explanations import validate_explanation
        validate_explanation(observation["details"]["explanation"], expectation, observation["evidence"])
    expected = expectation["expected"]
    if observation["state"] == "complete" and (set(facts) != set(expected) or not observation["evidence"]):
        raise ValueError("complete assertion observation lacks its exact named facts")
    eligible = (execution.get("state") == "closed" and execution.get("stop_reason") == "checkpoint-reached"
                and error is None and interpretation is not None and interpretation["complete_logs"]
                and not interpretation["truncated"] and interpretation["runtime_observations"]["complete"]
                and interpretation["observation"]["state"] == "checkpoint"
                and interpretation["blocking_findings_count"] == 0)
    reasons = [*expectation["reasons"], *observation["reasons"]]
    if not eligible:
        reasons.append("Complete checkpoint evidence and verified process closure are required for recipe assertions.")
    comparable = eligible and observation["state"] == "complete" and expectation["support"] == "supported"
    checks = [{"name": name, "expected": value, "observed": facts.get(name),
               "state": ("matched" if type(facts[name]) is type(value) and facts[name] == value else "mismatched")
               if comparable else "inconclusive"} for name, value in sorted(expected.items())]
    state = ("unsupported" if expectation["support"] == "unsupported" or observation["state"] == "unsupported"
             else "inconclusive" if not comparable
             else "mismatched" if any(row["state"] == "mismatched" for row in checks) else "matched")
    return seal("check-assertion", {
        "format": "workbench-check-assertion-v1", "expectation": expectation, "observation": observation,
        "state": state, "checks": checks, "reasons": reasons,
        "authority": {"source_mutated": False, "outcomes_promoted": False, "qualification_granted": False},
        "limitations": ["A matched assertion does not promote the startup outcome.",
                        "Registration and bounded lookup observations do not prove edit causation, gameplay or reachability."],
    })


def validate_assertion(value, execution, interpretation, error):
    if value is not None and evaluate(value["expectation"], value["observation"], execution, interpretation, error) != value:
        raise ValueError("retained assertion disagrees with its observations")
    return value


def compare_assertions(reference, candidate, reasons):
    left, right = reference.get("assertions"), candidate.get("assertions")
    if left is None and right is None:
        return None
    failures = list(reasons)
    if left is None or right is None:
        failures.append("One run has no recipe capture; historical evidence cannot be upgraded.")
    else:
        for row in (left, right):
            if row["state"] not in {"matched", "mismatched"}:
                failures.append("One assertion lacks complete supported observations.")
        if left["expectation"]["subject"].get("selector") != right["expectation"]["subject"].get("selector"):
            failures.append("Recipe input selectors differ; no automatic modification pairing.")
        if left["observation"]["details"].get("query_resolution") != right["observation"]["details"].get("query_resolution"):
            failures.append("Runtime ingredient resolutions differ.")
    before = None if left is None else left["observation"]["details"].get("observed")
    after = None if right is None else right["observation"]["details"].get("observed")
    if not isinstance(before, dict) or not isinstance(after, dict):
        failures.append("One run lacks its bounded observed recipe inventory and lookups.")
    return {"state": "not-comparable" if failures else "unchanged" if before == after else "changed",
            "reference_state": None if left is None else left["state"], "candidate_state": None if right is None else right["state"],
            "reference": before, "candidate": after, "reasons": failures,
            "meaning": "Same explicit input selector, not proof of source causation or individual duplicate identity."}
