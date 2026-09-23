"""Owner-neutral startup observations exchanged by check profiles and Core."""

import re


OBSERVATION_FORMAT = "workbench-check-observation-v1"


def observation(state, stage, summary, evidence=()):
    return validate_observation({
        "format": OBSERVATION_FORMAT,
        "state": state,
        "stage": stage,
        "summary": summary,
        "evidence": list(evidence),
    })


def validate_observation(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "state", "stage", "summary", "evidence"}
        or value["format"] != OBSERVATION_FORMAT
        or not isinstance(value["state"], str)
        or value["state"] not in {"running", "checkpoint", "failure"}
        or not isinstance(value["stage"], str)
        or re.fullmatch(r"[a-z][a-z0-9-]{0,79}", value["stage"]) is None
        or not isinstance(value["summary"], str)
        or not 1 <= len(value["summary"]) <= 2000
        or any(c in value["summary"] for c in "\0\r\n")
        or not isinstance(value["evidence"], list)
        or len(value["evidence"]) > 32
    ):
        raise ValueError("invalid check observation")
    for reference in value["evidence"]:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"log", "line"}
            or not isinstance(reference["log"], str)
            or re.fullmatch(r"(?:[a-zA-Z0-9._-]+/)+[a-zA-Z0-9._-]+", reference["log"]) is None
            or any(part in {".", ".."} for part in reference["log"].split("/"))
            or type(reference["line"]) is not int
            or not 1 <= reference["line"] <= 16 * 1024**2
        ):
            raise ValueError("invalid check observation evidence")
    if value["state"] != "running" and not value["evidence"]:
        raise ValueError("terminal check observations require evidence")
    return value
