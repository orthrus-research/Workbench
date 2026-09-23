"""Bounded runtime-evidence correlation for Groovy program reports."""

from __future__ import annotations

from collections import Counter
import re
from pathlib import Path
from typing import Any, Mapping

from .model import PackProgramError, sha256_bytes
from .profile import safe_regular_bytes, strict_json_file


MAX_LOG_BYTES = 128 * 1024 * 1024
_VERSION_RE = re.compile(r"^GroovyScript version:\s*(?P<version>\S+)", re.MULTILINE)
_LOADER_RE = re.compile(r"Running scripts in loader ['\"](?P<stage>[^'\"]+)['\"]")
_SCRIPT_RE = re.compile(r" - (?P<action>running script|loading class) (?P<name>[^\r\n]+)")
_TIMING_RE = re.compile(
    r"Groovy scripts took (?P<compile>[0-9]+)ms to compile and "
    r"(?P<run>[0-9]+)ms to run in (?P<stage>[A-Za-z0-9_-]+)\."
)
_LEVEL_RE = re.compile(r"\[[^\]]+\]\s+\[[^\]]+/(?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\]")


def runtime_evidence(
    *,
    groovy_log: Path | None,
    runtime_diagnosis: Path | None,
) -> dict[str, Any]:
    groovy = None if groovy_log is None else _groovy_log(groovy_log)
    diagnosis = None if runtime_diagnosis is None else _runtime_diagnosis(runtime_diagnosis)
    reasons: list[str] = []
    if groovy is None and diagnosis is None:
        state = "not-supplied"
        reasons.append("No exact runtime log or diagnosis was supplied.")
    else:
        state = "observations-present"
        if groovy is not None and groovy["diagnostics"]["fatal_or_error"]:
            state = "attention"
            reasons.append("The Groovy log contains ERROR or FATAL rows.")
        if diagnosis is not None:
            if diagnosis["primary_failure"] is not None:
                state = "attention"
                reasons.append("The runtime diagnosis records a primary failure.")
            if diagnosis["exception_observations"]:
                state = "attention"
                reasons.append(
                    "The broader runtime records exception observations even if the Groovy log is clean."
                )
        if state == "observations-present":
            reasons.append(
                "Runtime observations are available, but they are not bound to the candidate source bytes."
            )
    return {
        "state": state,
        "groovy_log": groovy,
        "runtime_diagnosis": diagnosis,
        "acceptance": {
            "state": (
                "attention"
                if state == "attention"
                else "unbound-observation"
                if state == "observations-present"
                else "not-evaluated"
            ),
            "reasons": reasons,
            "candidate_source_bound": False,
            "effective_registry_diff": "unavailable",
            "reload_idempotence": "unavailable",
        },
        "limitations": [
            "A log path does not identify the source hashes, mod graph, side, cache, or exact loader inputs that produced it.",
            "Runtime exceptions are correlated as neighboring observations and are not attributed to Groovy without causal evidence.",
        ],
    }


def _groovy_log(path: Path) -> dict[str, Any]:
    raw = safe_regular_bytes(path, maximum=MAX_LOG_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise PackProgramError(f"Groovy runtime log is not UTF-8: {path}: {exc}") from exc
    version = _VERSION_RE.search(text)
    current_stage: str | None = None
    executions: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        loader = _LOADER_RE.search(line)
        if loader:
            current_stage = loader.group("stage")
        script = _SCRIPT_RE.search(line)
        if script:
            executions.append(
                {
                    "stage": current_stage,
                    "action": script.group("action"),
                    "name": script.group("name").strip(),
                    "line": line_number,
                }
            )
    timings = [
        {
            "stage": match.group("stage"),
            "compile_ms": int(match.group("compile")),
            "run_ms": int(match.group("run")),
        }
        for match in _TIMING_RE.finditer(text)
    ]
    levels = Counter(match.group("level") for match in _LEVEL_RE.finditer(text))
    return {
        "evidence_state": "verbatim-runtime-observation",
        "path": str(path.expanduser().resolve()),
        "sha256": sha256_bytes(raw),
        "size": len(raw),
        "groovyscript_version": None if version is None else version.group("version"),
        "executions": executions,
        "timings": timings,
        "totals": {
            "compile_ms": sum(row["compile_ms"] for row in timings),
            "run_ms": sum(row["run_ms"] for row in timings),
        },
        "diagnostics": {
            "levels": dict(sorted(levels.items())),
            "fatal_or_error": levels["ERROR"] + levels["FATAL"],
        },
        "candidate_source_binding": "unresolved",
    }


def _runtime_diagnosis(path: Path) -> dict[str, Any]:
    value, raw = strict_json_file(path, maximum=MAX_LOG_BYTES)
    if not isinstance(value, dict):
        raise PackProgramError("runtime diagnosis must be a JSON object")
    observations = value.get("log_observations", [])
    if not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations):
        raise PackProgramError("runtime diagnosis log observations are malformed")
    exception_rows = []
    for row in observations:
        kind = row.get("type")
        count = row.get("count")
        if not isinstance(kind, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PackProgramError("runtime diagnosis observation identity/count is malformed")
        if "Exception" in kind or "Error" in kind:
            exception_rows.append(
                {
                    "type": kind,
                    "count": count,
                    "classification": row.get("classification"),
                    "evidence_label": row.get("evidence_label"),
                    "sample_messages": [
                        str(message)[:1024]
                        for message in row.get("sample_messages", [])[:10]
                        if isinstance(message, str)
                    ],
                }
            )
    checkpoint = value.get("checkpoint")
    return {
        "evidence_state": "integration-observation",
        "path": str(path.expanduser().resolve()),
        "sha256": sha256_bytes(raw),
        "size": len(raw),
        "format": value.get("format"),
        "diagnosis_id": value.get("diagnosis_id"),
        "checkpoint": checkpoint if isinstance(checkpoint, Mapping) else None,
        "primary_failure": value.get("primary_failure"),
        "exception_observations": exception_rows,
        "exception_count": sum(row["count"] for row in exception_rows),
        "authority": value.get("authority"),
        "candidate_source_binding": "unresolved",
    }


__all__ = ["runtime_evidence"]
