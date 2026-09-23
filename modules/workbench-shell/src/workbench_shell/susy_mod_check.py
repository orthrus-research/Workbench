"""Compare one retained SUSY mod candidate with its exact Packwiz baseline.

The check runs two independent dedicated-server smokes through the existing
server launcher, verifies their full retained logs, compares like-for-like
inputs, and reports only an observed startup-health delta.
"""

from __future__ import annotations

from urllib.request import url2pathname

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from .susy_mod_server import (
    LOG_ENTRY_RE,
    SERVER_EXPERIMENTS,
    SERVER_RECEIPT_FORMAT,
    SERVER_RESULT_FORMAT,
    SusyModServerError,
    _canonical,
    _safe_run_root,
    _validate_retained_build,
    launch_susy_mod_server,
)


CHECK_RESULT_FORMAT = "workbench-susy-mod-server-comparison-result-v1"
CHECK_RECEIPT_FORMAT = "workbench-susy-mod-server-comparison-receipt-v1"
CHECK_ID_PREFIX = "workbench-susy-mod-server-check:"
ISSUE_LEVELS = frozenset({"WARN", "ERROR", "FATAL"})
REGRESSION_LEVELS = frozenset({"ERROR", "FATAL"})
_OBJECT_ID_RE = re.compile(r"(?<=@)[0-9a-fA-F]{6,16}\b")
_HEX_TOKEN_RE = re.compile(r"(?i)\b0x[0-9a-f]+\b")
_WORKER_THREAD_RE = re.compile(r"(?i)\b(pool|worker|thread)-\d+\b")
_FML_BIOME_GUESS_RE = re.compile(
    r"^(?P<prefix>No types have been added to Biome [^,\r\n]+, "
    r"types have been assigned on a best-effort guess: )"
    r"\[(?P<types>[A-Z_]+(?:, [A-Z_]+)*)\]$"
)


class SusyModCheckError(RuntimeError):
    """A paired SUSY server change check cannot be started safely."""


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise SusyModCheckError(
            f"cannot retain SUSY server comparison: {path}"
        ) from exc


def _file_uri(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise SusyModCheckError(f"{label} URI is missing")
    parsed = urlparse(value)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise SusyModCheckError(f"{label} must use a local file URI")
    path = Path(url2pathname(parsed.path))
    try:
        info = path.lstat()
    except OSError as exc:
        raise SusyModCheckError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SusyModCheckError(f"{label} must be a regular file: {path}")
    return path.resolve()


def _without_receipt_id(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in receipt.items()
        if key != "receipt_id"
    }


def _verify_leg(
    result: Mapping[str, Any], *, run_id: str, run_root: Path, role: str
) -> tuple[dict[str, Any], str]:
    if (
        not isinstance(result, Mapping)
        or result.get("format") != SERVER_RESULT_FORMAT
        or result.get("schema_version") != 2
    ):
        raise SusyModCheckError(f"{role} server result has the wrong identity")
    receipt_value = result.get("receipt")
    if not isinstance(receipt_value, Mapping):
        raise SusyModCheckError(f"{role} server attempt lacks a retained receipt")
    receipt = deepcopy(dict(receipt_value))
    if (
        receipt.get("format") != SERVER_RECEIPT_FORMAT
        or receipt.get("schema_version") != 2
        or receipt.get("run_id") != run_id
    ):
        raise SusyModCheckError(f"{role} server receipt has the wrong identity")
    expected_id = "workbench-susy-mod-server-launch:" + sha256(
        _canonical(_without_receipt_id(receipt))
    ).hexdigest()
    if receipt.get("receipt_id") != expected_id:
        raise SusyModCheckError(f"{role} server receipt identity has drifted")
    claims = receipt.get("claims")
    candidate = receipt.get("candidate")
    if role == "baseline" and (
        receipt.get("runtime_subject") != "pack-baseline"
        or not isinstance(candidate, Mapping)
        or candidate.get("role") != "pack-baseline-control"
        or not isinstance(claims, Mapping)
        or claims.get("exact_runtime_subject_loaded") is not True
    ):
        raise SusyModCheckError(
            "baseline attempt did not prove the exact Packwiz baseline"
        )
    if role == "candidate" and (
        receipt.get("runtime_subject") == "pack-baseline"
        or not isinstance(claims, Mapping)
        or claims.get("exact_candidate_loaded") is not True
        or claims.get("exact_runtime_subject_loaded") is not True
    ):
        raise SusyModCheckError("candidate attempt did not prove the exact candidate")

    projection = receipt.get("projection")
    evidence = receipt.get("evidence")
    latest = evidence.get("latest_log") if isinstance(evidence, Mapping) else None
    if not isinstance(projection, Mapping) or not isinstance(latest, Mapping):
        raise SusyModCheckError(f"{role} server receipt lacks its full runtime log")
    root_uri = projection.get("root_uri")
    if not isinstance(root_uri, str):
        raise SusyModCheckError(f"{role} server projection identity is missing")
    parsed_root = urlparse(root_uri)
    if parsed_root.scheme != "file" or parsed_root.netloc not in {"", "localhost"}:
        raise SusyModCheckError(f"{role} server projection must use a local file URI")
    projection_path = Path(url2pathname(parsed_root.path)).resolve()
    if not projection_path.is_relative_to(run_root):
        raise SusyModCheckError(
            f"{role} server projection escapes its retained run"
        )
    log_path = _file_uri(latest.get("uri"), f"{role} dedicated-server log")
    if log_path != (projection_path / ".minecraft/logs/latest.log").resolve():
        raise SusyModCheckError(f"{role} runtime log is not bound to its projection")
    if not log_path.is_relative_to(run_root):
        raise SusyModCheckError(f"{role} runtime log escapes its retained run")
    try:
        raw = log_path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SusyModCheckError(f"{role} runtime log is unreadable") from exc
    if (
        latest.get("sha256") != sha256(raw).hexdigest()
        or latest.get("size") != len(raw)
    ):
        raise SusyModCheckError(f"{role} runtime log identity has drifted")
    return receipt, text


def _safe_leg(receipt: Mapping[str, Any]) -> bool:
    cleanup = receipt.get("cleanup")
    immutable = receipt.get("immutable_inputs")
    return bool(
        isinstance(cleanup, Mapping)
        and not cleanup.get("owned_processes_running")
        and cleanup.get("errors") == []
        and isinstance(immutable, Mapping)
        and immutable
        and all(value is True for value in immutable.values())
    )


def _normalized_message(message: str, receipt: Mapping[str, Any]) -> str:
    normalized = message.replace("\\", "/")
    projection = receipt.get("projection")
    root_uri = projection.get("root_uri") if isinstance(projection, Mapping) else None
    if isinstance(root_uri, str):
        parsed = urlparse(root_uri)
        if parsed.scheme == "file":
            path = unquote(parsed.path).replace("\\", "/")
            for token in sorted({path, root_uri}, key=len, reverse=True):
                if token:
                    normalized = normalized.replace(token, "<projection>")
    attempt_id = receipt.get("attempt_id")
    if isinstance(attempt_id, str) and attempt_id:
        normalized = normalized.replace(attempt_id, "<attempt>")
    process = receipt.get("process")
    pid = process.get("pid") if isinstance(process, Mapping) else None
    if isinstance(pid, int) and not isinstance(pid, bool):
        normalized = re.sub(rf"\b{pid}\b", "<pid>", normalized)
    normalized = _OBJECT_ID_RE.sub("<object-id>", normalized)
    normalized = _HEX_TOKEN_RE.sub("<hex-address>", normalized)
    normalized = _WORKER_THREAD_RE.sub(
        lambda match: match.group(1).lower() + "-<n>", normalized
    )
    biome_guess = _FML_BIOME_GUESS_RE.fullmatch(normalized)
    if biome_guess is not None:
        types = sorted(biome_guess.group("types").split(", "))
        normalized = biome_guess.group("prefix") + "[" + ", ".join(types) + "]"
    return normalized


IssueKey = tuple[str, str, str, str]


def _issue_counter(
    text: str, receipt: Mapping[str, Any]
) -> tuple[Counter[IssueKey], dict[IssueKey, int]]:
    counter: Counter[IssueKey] = Counter()
    first_lines: dict[IssueKey, int] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = LOG_ENTRY_RE.fullmatch(line)
        if match is None or match.group("level") not in ISSUE_LEVELS:
            continue
        key = (
            match.group("level"),
            match.group("thread"),
            match.group("logger"),
            _normalized_message(match.group("message"), receipt),
        )
        counter[key] += 1
        first_lines.setdefault(key, line_number)
    return counter, first_lines


def _signature(key: IssueKey) -> str:
    return "|".join(key)


def _rows(
    counter: Counter[IssueKey],
    first_lines: Mapping[IssueKey, int],
    *,
    sample_limit: int,
) -> list[dict[str, Any]]:
    rows = [
        {
            "signature": _signature(key),
            "level": key[0],
            "thread": key[1],
            "logger": key[2],
            "message": key[3],
            "count": count,
            "first_line": first_lines.get(key),
        }
        for key, count in counter.items()
    ]
    rows.sort(key=lambda row: str(row["signature"]))
    return rows[:sample_limit]


def _multiset_sha(counter: Counter[IssueKey]) -> str:
    rows = [
        {"signature": _signature(key), "count": count}
        for key, count in sorted(counter.items(), key=lambda item: _signature(item[0]))
    ]
    return "sha256:" + sha256(_canonical(rows)).hexdigest()


def _diagnostic_comparison(
    baseline_text: str,
    baseline_receipt: Mapping[str, Any],
    candidate_text: str,
    candidate_receipt: Mapping[str, Any],
    *,
    sample_limit: int,
) -> dict[str, Any]:
    baseline, baseline_lines = _issue_counter(baseline_text, baseline_receipt)
    candidate, candidate_lines = _issue_counter(candidate_text, candidate_receipt)
    added = candidate - baseline
    removed = baseline - candidate
    unchanged = baseline & candidate
    return {
        "normalization": {
            "format": "workbench-minecraft-log-entry-normalization-v1",
            "levels": sorted(ISSUE_LEVELS),
            "rules": [
                "strip-log-timestamp",
                "replace-exact-leg-projection-attempt-and-process",
                "replace-java-object-identity-and-generic-worker-suffix",
                "preserve-resource-ids-versions-dimensions-hashes-counts-and-numbers",
            ],
        },
        "baseline": {
            "issue_count": sum(baseline.values()),
            "distinct_signatures": len(baseline),
            "multiset_sha256": _multiset_sha(baseline),
        },
        "candidate": {
            "issue_count": sum(candidate.values()),
            "distinct_signatures": len(candidate),
            "multiset_sha256": _multiset_sha(candidate),
        },
        "diff": {
            "added_count": sum(added.values()),
            "removed_count": sum(removed.values()),
            "unchanged_count": sum(unchanged.values()),
            "added_regression_count": sum(
                count for key, count in added.items() if key[0] in REGRESSION_LEVELS
            ),
            "removed_regression_count": sum(
                count for key, count in removed.items() if key[0] in REGRESSION_LEVELS
            ),
            "added": _rows(added, candidate_lines, sample_limit=sample_limit),
            "removed": _rows(removed, baseline_lines, sample_limit=sample_limit),
            "unchanged": _rows(unchanged, candidate_lines, sample_limit=sample_limit),
            "samples_truncated": any(
                len(counter) > sample_limit
                for counter in (added, removed, unchanged)
            ),
        },
    }


def _experiment_identity(receipt: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    projection = receipt.get("projection")
    rows = (
        projection.get("compatibility_experiments")
        if isinstance(projection, Mapping)
        else None
    )
    if not isinstance(rows, list):
        return None
    identities: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        authority = row.get("authority")
        manifest = row.get("manifest")
        implementation = row.get("implementation")
        identities.append(
            {
                "experiment_id": row.get("experiment_id") or row.get("patch_id"),
                "operation": row.get("operation"),
                "scope": row.get("scope"),
                "spec_sha256": row.get("spec_sha256"),
                "target_path": row.get("target_path"),
                "entry": row.get("entry"),
                "applicability": deepcopy(row.get("applicability")),
                "authority_sha256": (
                    authority.get("sha256")
                    if isinstance(authority, Mapping)
                    else None
                ),
                "manifest_sha256": (
                    manifest.get("sha256")
                    if isinstance(manifest, Mapping)
                    else None
                ),
                "implementation_sha256": (
                    implementation.get("sha256")
                    if isinstance(implementation, Mapping)
                    else None
                ),
            }
        )
    return sorted(identities, key=lambda row: json.dumps(row, sort_keys=True))


def _same(left: Any, right: Any) -> bool:
    return _canonical(left) == _canonical(right)


def _comparisons(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, bool]:
    baseline_projection = baseline.get("projection")
    candidate_projection = candidate.get("projection")
    values = {
        "template_identity_equal": _same(
            baseline.get("template"), candidate.get("template")
        ),
        "java_identity_equal": _same(baseline.get("java"), candidate.get("java")),
        "server_properties_equal": _same(
            (
                baseline_projection.get("server_properties")
                if isinstance(baseline_projection, Mapping)
                else None
            ),
            (
                candidate_projection.get("server_properties")
                if isinstance(candidate_projection, Mapping)
                else None
            ),
        ),
        "compatibility_experiments_equal": _same(
            _experiment_identity(baseline), _experiment_identity(candidate)
        ),
        "runner_identity_equal": _same(
            baseline.get("runner"), candidate.get("runner")
        ),
    }
    values["candidate_cleanup_safe"] = _safe_leg(candidate)
    values["comparable"] = all(values.values())
    return values


def _verdict(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
    comparisons: Mapping[str, bool],
    diagnostics: Mapping[str, Any] | None,
) -> str:
    if candidate is None or not comparisons.get("comparable") or diagnostics is None:
        return "incomparable"
    baseline_outcome = baseline.get("outcome")
    candidate_outcome = candidate.get("outcome")
    diff = diagnostics.get("diff")
    if not isinstance(diff, Mapping):
        return "incomparable"
    candidate_crash = bool(
        isinstance(candidate.get("evidence"), Mapping)
        and candidate["evidence"].get("crash_report")
    )
    if baseline_outcome == "passed" and (
        candidate_outcome != "passed"
        or candidate_crash
        or int(diff.get("added_regression_count", 0)) > 0
    ):
        return "candidate-regression"
    if baseline_outcome != "passed" and candidate_outcome == "passed":
        return "candidate-improvement"
    if baseline_outcome == "passed" and candidate_outcome == "passed":
        if int(diff.get("removed_regression_count", 0)) > 0:
            return "candidate-improvement"
        return "no-observed-regression"
    # A matched failure can be useful diagnostic evidence, but a failed
    # required smoke never becomes a passing check merely because both sides
    # broke in the same way.
    return "incomparable"


def _comparison_target(run_root: Path, comparison_id: str) -> Path:
    runtime = run_root / "runtime"
    if runtime.is_symlink() or (runtime.exists() and not runtime.is_dir()):
        raise SusyModCheckError("retained runtime evidence root is unsafe")
    runtime.mkdir(exist_ok=True)
    comparisons = runtime / "server-comparisons"
    if comparisons.is_symlink() or (
        comparisons.exists() and not comparisons.is_dir()
    ):
        raise SusyModCheckError("server comparison evidence root is unsafe")
    comparisons.mkdir(exist_ok=True)
    if not comparisons.resolve().is_relative_to(run_root):
        raise SusyModCheckError("server comparison evidence root escapes its run")
    target = comparisons / comparison_id
    if target.exists() or target.is_symlink():
        raise SusyModCheckError("server comparison evidence target already exists")
    target.mkdir()
    return target


def _shared_applicable_diagnosed_experiment(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any] | None
) -> str | None:
    """Return one identical, applicable experiment diagnosed by both legs."""

    if candidate is None:
        return None
    observed: list[Mapping[str, Any]] = []
    for leg in (baseline, candidate):
        diagnosis = leg.get("diagnosis")
        available = (
            diagnosis.get("available_experiment")
            if isinstance(diagnosis, Mapping)
            else None
        )
        if not isinstance(available, Mapping):
            return None
        experiment_id = available.get("id")
        if (
            not isinstance(experiment_id, str)
            or experiment_id not in SERVER_EXPERIMENTS
            or not isinstance(available.get("applicability"), Mapping)
        ):
            return None
        observed.append(available)
    return (
        str(observed[0]["id"])
        if _canonical(observed[0]) == _canonical(observed[1])
        else None
    )


def _runner_identity() -> dict[str, Any]:
    path = Path(__file__)
    try:
        info = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise SusyModCheckError("comparison runner source is unreadable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SusyModCheckError("comparison runner source must be a regular file")
    return {
        "source_uri": path.resolve().as_uri(),
        "sha256": sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _retained_input_identity(suite: Path, run_id: str) -> dict[str, Any]:
    try:
        (
            _run_root,
            result,
            _artifact_path,
            artifact,
            selected,
            pack,
        ) = _validate_retained_build(suite, run_id)
    except SusyModServerError:
        raise
    return {
        "result_id": result.get("result_id"),
        "candidate_sha256": artifact.get("sha256"),
        "candidate_size": artifact.get("size"),
        "selected": selected,
        "pack": {key: value for key, value in pack.items() if key != "entries"},
    }


def check_susy_mod_server(
    suite_root: Path | str,
    run_id: str,
    *,
    server_template: Path | str | None = None,
    server_java: Path | str | None = None,
    accept_minecraft_eula: bool = False,
    compatibility_experiments: Sequence[str] = (),
    memory_mib: int = 8192,
    timeout_seconds: float = 600.0,
    shutdown_timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 0.25,
    sample_limit: int = 12,
) -> dict[str, Any]:
    """Run and compare exact Packwiz-baseline and retained-candidate smokes."""

    if (
        isinstance(sample_limit, bool)
        or not isinstance(sample_limit, int)
        or not 1 <= sample_limit <= 100
    ):
        raise SusyModCheckError("comparison sample limit must be between 1 and 100")
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value <= 0
        for value in (
            timeout_seconds,
            shutdown_timeout_seconds,
            poll_interval_seconds,
        )
    ):
        raise SusyModCheckError("comparison timeouts must be positive")
    suite = Path(suite_root).resolve()
    try:
        run_root = _safe_run_root(suite, run_id)
        retained_before = _retained_input_identity(suite, run_id)
    except SusyModServerError as exc:
        detail = str(exc).replace(
            "launch-server requires", "check requires"
        )
        raise SusyModCheckError(detail) from exc

    managed_materialization: dict[str, Any] | None = None
    template_mode = "override"
    if server_template is None:
        from .susy_server_materialize import (
            MATERIALIZATION_RESULT_FORMAT_V2,
            SusyServerMaterializationError,
            materialize_susy_server,
            susy_server_materialization_version,
        )

        try:
            materialization_result = materialize_susy_server(
                suite,
                run_id,
                server_java=server_java,
                accept_minecraft_eula=accept_minecraft_eula,
            )
        except SusyServerMaterializationError as exc:
            raise SusyModCheckError(str(exc)) from exc
        receipt_value = materialization_result.get("receipt")
        receipt_version = (
            susy_server_materialization_version(receipt_value)
            if isinstance(receipt_value, Mapping)
            else None
        )
        if (
            receipt_version != 2
            or materialization_result.get("format")
            != MATERIALIZATION_RESULT_FORMAT_V2
            or materialization_result.get("schema_version") != 2
        ):
            raise SusyModCheckError(
                "managed SUSY server materialization result is invalid"
            )
        target_value = (
            receipt_value.get("target")
            if isinstance(receipt_value, Mapping)
            else None
        )
        template_uri = (
            target_value.get("template_uri")
            if isinstance(target_value, Mapping)
            else None
        )
        if not isinstance(receipt_value, Mapping) or not isinstance(
            template_uri, str
        ):
            raise SusyModCheckError(
                "managed SUSY server materialization lacks its template"
            )
        parsed_template = urlparse(template_uri)
        if (
            parsed_template.scheme != "file"
            or parsed_template.netloc not in {"", "localhost"}
        ):
            raise SusyModCheckError(
                "managed SUSY server template must use a local file URI"
            )
        server_template = Path(url2pathname(parsed_template.path))
        managed_materialization = deepcopy(dict(receipt_value))
        template_mode = "managed"
    elif accept_minecraft_eula:
        raise SusyModCheckError(
            "--accept-minecraft-eula applies only to managed server materialization"
        )

    arguments = {
        "server_template": server_template,
        "server_java": server_java,
        "accept_minecraft_eula": False,
        "compatibility_experiments": tuple(compatibility_experiments),
        "memory_mib": memory_mib,
        "timeout_seconds": timeout_seconds,
        "shutdown_timeout_seconds": shutdown_timeout_seconds,
        "poll_interval_seconds": poll_interval_seconds,
        "_managed_materialization": managed_materialization,
    }
    started = datetime.now(timezone.utc)
    try:
        baseline_result = launch_susy_mod_server(
            suite_root, run_id, **arguments, _subject="pack-baseline"
        )
        baseline, baseline_text = _verify_leg(
            baseline_result, run_id=run_id, run_root=run_root, role="baseline"
        )
    except (SusyModServerError, SusyModCheckError) as exc:
        raise SusyModCheckError(str(exc)) from exc

    inputs_unchanged_between_legs = False
    try:
        inputs_unchanged_between_legs = _same(
            retained_before, _retained_input_identity(suite, run_id)
        )
    except SusyModServerError:
        inputs_unchanged_between_legs = False
    safe_after_baseline = (
        _safe_leg(baseline) and inputs_unchanged_between_legs
    )
    candidate: dict[str, Any] | None = None
    candidate_text: str | None = None
    candidate_preflight_error: str | None = None
    if safe_after_baseline:
        try:
            candidate_result = launch_susy_mod_server(
                suite_root, run_id, **arguments, _subject="candidate"
            )
            candidate, candidate_text = _verify_leg(
                candidate_result,
                run_id=run_id,
                run_root=run_root,
                role="candidate",
            )
        except (SusyModServerError, SusyModCheckError) as exc:
            candidate_preflight_error = str(exc)

    comparisons: dict[str, bool] = {
        "safe_to_continue_after_baseline": safe_after_baseline,
        "inputs_unchanged_between_legs": inputs_unchanged_between_legs,
        "template_identity_equal": False,
        "java_identity_equal": False,
        "server_properties_equal": False,
        "compatibility_experiments_equal": False,
        "runner_identity_equal": False,
        "candidate_cleanup_safe": False,
        "comparable": False,
    }
    diagnostic_comparison: dict[str, Any] | None = None
    if candidate is not None and candidate_text is not None:
        comparisons.update(_comparisons(baseline, candidate))
        comparisons["safe_to_continue_after_baseline"] = safe_after_baseline
        comparisons["inputs_unchanged_between_legs"] = (
            inputs_unchanged_between_legs
        )
        diagnostic_comparison = _diagnostic_comparison(
            baseline_text,
            baseline,
            candidate_text,
            candidate,
            sample_limit=sample_limit,
        )
    verdict = _verdict(baseline, candidate, comparisons, diagnostic_comparison)
    ended = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    comparison_id = f"workbench-susy-server-check-{stamp}"
    receipt: dict[str, Any] = {
        "format": CHECK_RECEIPT_FORMAT,
        "schema_version": 1,
        "comparison_id": comparison_id,
        "run_id": run_id,
        "side": "dedicated-server",
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": round((ended - started).total_seconds(), 3),
        "verdict": verdict,
        "runner": _runner_identity(),
        "inputs": {
            "server_template_mode": template_mode,
            "server_template_uri": Path(server_template)
            .expanduser()
            .resolve()
            .as_uri(),
            "server_materialization_id": (
                None
                if managed_materialization is None
                else managed_materialization.get("materialization_id")
            ),
            "server_java_uri": (
                None
                if server_java is None
                else Path(server_java).expanduser().resolve().as_uri()
            ),
            "compatibility_experiments": list(compatibility_experiments),
            "memory_mib": memory_mib,
            "timeout_seconds": timeout_seconds,
            "shutdown_timeout_seconds": shutdown_timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
        },
        "legs": {"baseline": baseline, "candidate": candidate},
        "candidate_preflight_error": candidate_preflight_error,
        "comparisons": comparisons,
        "diagnostics": (
            {
                "baseline": baseline.get("diagnostics"),
                "candidate": None,
                "diff": None,
            }
            if diagnostic_comparison is None
            else diagnostic_comparison
        ),
        "limitations": [
            "This compares two matched dedicated-server startup/load/stop observations; it does not prove gameplay correctness.",
            "No-observed-regression means no new retained ERROR/FATAL signature or lifecycle failure was observed in this pair.",
            "This does not establish client/server parity or stable support for the pack, platform, or constituent mod.",
        ],
    }
    receipt["receipt_id"] = CHECK_ID_PREFIX + sha256(_canonical(receipt)).hexdigest()
    target = _comparison_target(run_root, comparison_id)
    receipt_path = target / "susy-mod-server-comparison-v1.json"
    _write_json(receipt_path, receipt)

    retry = [
        "workbench",
        "dev",
        "check",
        "--run",
        run_id,
        "--side",
        "server",
    ]
    if managed_materialization is None:
        retry.extend(
            [
                "--server-template",
                str(Path(server_template).expanduser().resolve()),
            ]
        )
    else:
        retry.append("--accept-minecraft-eula")
    if server_java is not None:
        retry.extend(
            ["--server-java", str(Path(server_java).expanduser().resolve())]
        )
    retry.extend(
        [
            "--memory",
            str(memory_mib),
            "--launch-timeout",
            str(timeout_seconds),
            "--shutdown-timeout",
            str(shutdown_timeout_seconds),
        ]
    )
    selected_experiments = [str(value) for value in compatibility_experiments]
    for experiment in selected_experiments:
        retry.extend(["--runtime-experiment", str(experiment)])
    diagnosed_experiment = _shared_applicable_diagnosed_experiment(
        baseline, candidate
    )
    if (
        diagnosed_experiment is not None
        and diagnosed_experiment not in selected_experiments
    ):
        retry.extend(["--runtime-experiment", diagnosed_experiment])
        next_action_id = "retry-server-change-check-with-diagnosed-experiment"
    else:
        next_action_id = "run-server-change-check-again"
    return {
        "format": CHECK_RESULT_FORMAT,
        "schema_version": 1,
        "outcome": (
            "passed"
            if verdict in {"no-observed-regression", "candidate-improvement"}
            else "failed"
        ),
        "verdict": verdict,
        "receipt": deepcopy(receipt),
        "receipt_uri": receipt_path.as_uri(),
        "next_actions": [
            {
                "id": next_action_id,
                "available": True,
                "argv": retry,
            }
        ],
    }


def render_susy_mod_check(
    result: Mapping[str, Any], *, json_output: bool = False
) -> str:
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise SusyModCheckError("SUSY server comparison lacks its receipt")
    legs = receipt.get("legs")
    diagnostics = receipt.get("diagnostics")
    diff = diagnostics.get("diff") if isinstance(diagnostics, Mapping) else None
    baseline = legs.get("baseline") if isinstance(legs, Mapping) else None
    candidate = legs.get("candidate") if isinstance(legs, Mapping) else None
    lines = [
        f"SUSY server comparison verdict: {result.get('verdict')}",
        f"Run: {receipt.get('run_id')}",
    ]
    inputs = receipt.get("inputs")
    experiments = (
        inputs.get("compatibility_experiments")
        if isinstance(inputs, Mapping)
        else None
    )
    if isinstance(experiments, list):
        lines.append(
            "Selected runtime experiments: "
            + (", ".join(str(value) for value in experiments) or "none")
        )
    for label, leg in (("Baseline", baseline), ("Candidate", candidate)):
        lines.append(
            f"{label} load/ready/stop smoke: "
            + (str(leg.get("outcome")) if isinstance(leg, Mapping) else "not run")
        )
        leg_diagnostics = (
            leg.get("diagnostics") if isinstance(leg, Mapping) else None
        )
        entry_counts = (
            leg_diagnostics.get("entry_counts")
            if isinstance(leg_diagnostics, Mapping)
            else None
        )
        if isinstance(leg_diagnostics, Mapping) and isinstance(entry_counts, Mapping):
            lines.append(
                f"{label} pack health: {leg_diagnostics.get('state')}"
                + f" · {entry_counts.get('fatal', 0)} FATAL"
                + f" · {entry_counts.get('error', 0)} ERROR"
                + f" · {entry_counts.get('warn', 0)} WARN"
            )
    if isinstance(diff, Mapping):
        lines.append(
            "Candidate diagnostic delta: "
            + f"{diff.get('added_regression_count', 0)} new ERROR/FATAL"
            + f" · +{diff.get('added_count', 0)} total issues"
            + f" · -{diff.get('removed_count', 0)} resolved"
            + f" · {diff.get('unchanged_count', 0)} persisted"
        )
        added = diff.get("added")
        if isinstance(added, list):
            for row in added[:5]:
                if isinstance(row, Mapping):
                    lines.append(
                        "New: "
                        + f"{row.get('level')} [{row.get('logger')}] "
                        + str(row.get("message"))
                        + f" ×{row.get('count')}"
                    )
    if receipt.get("candidate_preflight_error"):
        lines.append(
            "Candidate preflight: " + str(receipt["candidate_preflight_error"])
        )
    lines.append(f"Receipt: {receipt.get('receipt_id')}")
    if result.get("receipt_uri"):
        lines.append(f"Evidence: {result.get('receipt_uri')}")
    actions = result.get("next_actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], Mapping):
        lines.append(
            "Next: " + json.dumps(actions[0].get("argv"), ensure_ascii=False)
        )
    return "\n".join(lines) + "\n"
