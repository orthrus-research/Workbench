"""Run registered Python validation suites with bounded concurrency."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from orchestration import (
    OrchestrationFailure,
    ProcessOutcome,
    SchedulingItem,
    SuiteReport,
    ValidationRunPaths,
    create_run_paths,
    fingerprint_paths,
    load_suite_report,
    load_test_inventory,
    new_run_id,
    require_successful_process,
    select_runnable,
)
from suite_measurement import environment_provenance, inventory_digest
from suite_catalog import (
    SUITES_BY_NAME,
    PythonTestSuite,
    suites_for_tier,
)


ROOT = Path(__file__).resolve().parents[1]


class SuiteExecutionFailure(RuntimeError):
    """Registered Python suites could not produce an accepted run."""


def _validation_temporary_storage() -> Path:
    """Return a declared suite-temp root that is outside the Git checkout."""

    candidate = (
        Path("/tmp").resolve()
        if os.name == "posix"
        else Path(tempfile.gettempdir()).resolve()
    )
    repository = ROOT.resolve()
    try:
        candidate.relative_to(repository)
    except ValueError:
        return candidate / "workbench-validation"
    raise SuiteExecutionFailure(
        "system temporary storage resolves inside the repository; set TMPDIR, "
        "TMP, or TEMP to an external directory before running validation"
    )


@dataclass(frozen=True)
class _SuiteRequest:
    suite: PythonTestSuite
    scheduling: SchedulingItem
    report_path: Path
    log_path: Path
    temporary_root: Path
    command: tuple[str, ...]


@dataclass(frozen=True)
class _SuiteProcessResult:
    request: _SuiteRequest
    outcome: ProcessOutcome
    started_at: str | None = None
    completed_at: str | None = None
    admitted_test_ids: tuple[str, ...] | None = None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: object) -> None:
    _atomic_write_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _timing_estimate(suite: PythonTestSuite, source_fingerprint: str, environment: dict) -> float:
    path = ROOT / ".workbench/validation/test-timings" / f"{suite.name}.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            return 0.0
        if report.get("environment") != environment:
            return 0.0
        # Retained v2, mixed-source and changed inventories are historical
        # diagnostics. They cannot supply the current scheduler's cost model.
        load_suite_report(path, expected_suite=suite.name,
                          expected_run_id=report["run_id"],
                          expected_source_fingerprint=source_fingerprint,
                          expected_authority=suite.authority,
                          expected_test_ids=report.get("collected_ids", ()))
        seconds = report["process_wall_seconds"]
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(float(seconds))
            or float(seconds) < 0
        ):
            return 0.0
        if report.get("suite") != suite.name:
            return 0.0
        return float(seconds)
    except (OSError, KeyError, TypeError, ValueError, OrchestrationFailure):
        return 0.0


def _suite_requests(
    suites: tuple[PythonTestSuite, ...],
    *,
    paths: ValidationRunPaths,
    source_fingerprint: str,
) -> list[_SuiteRequest]:
    environment = environment_provenance(ROOT)
    estimates = {suite.name: _timing_estimate(suite, source_fingerprint, environment) for suite in suites}
    ranked = sorted(
        enumerate(suites),
        # Put exclusive work at the start of a phase; a long-running parallel
        # suite can no longer strand workers at a mid-queue exclusive barrier.
        key=lambda row: (not row[1].exclusive, -estimates[row[1].name], row[0]),
    )
    requests: list[_SuiteRequest] = []
    for schedule_order, (_, suite) in enumerate(ranked):
        report_path = paths.report_for(suite.name)
        log_path = paths.log_for(suite.name)
        temporary_root = (
            paths.root / "repository-tmp" / suite.name
            if suite.repository_temp
            else paths.temporary_for(suite.name)
        )
        command = (
            sys.executable,
            "validation/run_python_suite.py",
            suite.name,
            "--report",
            str(report_path),
            "--temporary-root",
            str(temporary_root),
            "--run-id",
            paths.run_id,
            "--source-fingerprint",
            source_fingerprint,
            "--admission-file",
            str(report_path.with_suffix(".admitted.json")),
        )
        requests.append(
            _SuiteRequest(
                suite=suite,
                scheduling=SchedulingItem(
                    name=suite.name,
                    order=schedule_order,
                    estimated_seconds=estimates[suite.name],
                    exclusive=suite.exclusive,
                    resource_locks=suite.resource_locks,
                ),
                report_path=report_path,
                log_path=log_path,
                temporary_root=temporary_root,
                command=command,
            )
        )
    return requests


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        if process.poll() is not None:
            return
        # ``Popen.terminate`` reaches only the immediate Windows process.  The
        # suite may have launched Java, Gradle, or another nested worker, so
        # terminate the complete descendant tree identified by the suite PID.
        # ``/F`` avoids returning while console descendants remain alive.
        try:
            subprocess.run(
                [
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Retain a direct-child fallback for hosts where taskkill itself
            # cannot be started.  The run still cannot be admitted unless the
            # child reaches a terminal status below.
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    # The leader may have exited while a descendant ignored SIGTERM. Group
    # cleanup must not depend on the leader's liveness or wait return value.
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_suite_process(
    request: _SuiteRequest,
    *,
    process_registry: dict[str, subprocess.Popen[bytes]],
    process_lock: threading.Lock,
    interruption_event: threading.Event,
) -> _SuiteProcessResult:
    started = time.perf_counter()
    started_at = _utc_timestamp()
    request.log_path.parent.mkdir(parents=True, exist_ok=True)
    with request.log_path.open("wb") as log:
        arguments: dict[str, Any] = {
            "cwd": ROOT,
            "stdout": log,
            "stderr": subprocess.STDOUT,
        }
        isolated = request.temporary_root / "environment"
        environment = os.environ.copy()
        for variable, directory in (("WORKBENCH_CONFIG_HOME", "config"),
                                    ("XDG_CONFIG_HOME", "config"),
                                    ("XDG_CACHE_HOME", "cache"),
                                    ("XDG_STATE_HOME", "state")):
            path = isolated / directory
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
            environment[variable] = str(path)
        arguments["env"] = environment
        if os.name == "posix":
            arguments["start_new_session"] = True
        elif os.name == "nt":
            arguments["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        with process_lock:
            # Hold the registry lock across the interruption check and spawn.
            # This closes the race where Ctrl-C observes an empty registry and
            # a worker launches a suite immediately afterwards.
            if interruption_event.is_set():
                raise SuiteExecutionFailure(
                    f"suite {request.suite.name} was cancelled before launch"
                )
            process = subprocess.Popen(list(request.command), **arguments)
            process_registry[request.suite.name] = process
        timed_out = False
        admitted_ids = None
        try:
            try:
                deadline = time.monotonic() + request.suite.timeout_seconds
                while True:
                    inventory_path = request.report_path.with_suffix(".inventory.json")
                    if admitted_ids is None and inventory_path.exists():
                        command = list(request.command)
                        run_id = command[command.index("--run-id") + 1]
                        source_fingerprint = command[command.index("--source-fingerprint") + 1]
                        admitted_ids = load_test_inventory(
                            inventory_path, expected_suite=request.suite.name,
                            expected_run_id=run_id, expected_source_fingerprint=source_fingerprint,
                            expected_authority=request.suite.authority,
                        )
                        _atomic_write_json(request.report_path.with_suffix(".admitted.json"), {
                            "format": "workbench-python-test-admission-v1", "run_id": run_id,
                            "source_fingerprint": source_fingerprint, "test_ids": sorted(admitted_ids),
                            "inventory_digest": inventory_digest(admitted_ids),
                        })
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(request.command, request.suite.timeout_seconds)
                    try:
                        returncode = process.wait(timeout=min(0.02, remaining) if admitted_ids is None else remaining)
                        break
                    except subprocess.TimeoutExpired:
                        if admitted_ids is not None:
                            raise
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process(process)
                returncode = process.returncode
            except BaseException:
                _terminate_process(process)
                raise
        finally:
            with process_lock:
                process_registry.pop(request.suite.name, None)
    return _SuiteProcessResult(
        request=request,
        outcome=ProcessOutcome(
            suite=request.suite.name,
            returncode=returncode,
            elapsed_seconds=time.perf_counter() - started,
            timeout_seconds=request.suite.timeout_seconds,
            timed_out=timed_out,
        ),
        started_at=started_at,
        completed_at=_utc_timestamp(),
        admitted_test_ids=admitted_ids,
    )


def _log_excerpt(path: Path, *, limit: int = 12000) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return f"could not read suite log: {exc}"
    return raw[-limit:].decode("utf-8", errors="replace").strip()


def _publish_timing_report(document: dict, suite_name: str) -> None:
    target = ROOT / ".workbench/validation/test-timings" / f"{suite_name}.json"
    _atomic_write_json(target, document)


def _cleanup_run_temporary(paths: ValidationRunPaths) -> list[str]:
    """Remove run-owned scratch trees while retaining reports and logs."""

    failures: list[str] = []
    targets = (
        (paths.temporary, "isolated suite temporary storage"),
        (paths.root / "repository-tmp", "repository-scoped suite temporary storage"),
    )
    for path, label in targets:
        if not path.exists() and not path.is_symlink():
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            failures.append(f"could not remove {label}: {exc}")
    return failures


def _write_run_manifest(
    paths: ValidationRunPaths,
    *,
    state: str,
    tier: str,
    jobs: int,
    source_fingerprint: str,
    selected_suites: tuple[PythonTestSuite, ...],
    reports: dict[str, SuiteReport],
    failures: list[str],
    started_at: str,
    details: dict | None = None,
) -> None:
    _atomic_write_json(
        paths.root / "run.json",
        {
            "format": "workbench-validation-run-v1",
            "run_id": paths.run_id,
            "state": state,
            "tier": tier,
            "jobs": jobs,
            "source_fingerprint": source_fingerprint,
            "started_at": started_at,
            "updated_at": _utc_timestamp(),
            "selected_suites": [suite.name for suite in selected_suites],
            "completed_suites": [
                {
                    "name": suite.name,
                    "tests": reports[suite.name].executed_tests,
                    "seconds": reports[suite.name].seconds,
                    "report": paths.report_for(suite.name)
                    .relative_to(paths.root)
                    .as_posix(),
                    "log": paths.log_for(suite.name)
                    .relative_to(paths.root)
                    .as_posix(),
                }
                for suite in selected_suites
                if suite.name in reports
            ],
            "failures": failures,
            **(details or {}),
        },
    )


def _terminalize_abnormal_run(
    paths: ValidationRunPaths,
    *,
    error: BaseException,
    tier: str,
    jobs: int,
    source_fingerprint: str,
    selected_suites: tuple[PythonTestSuite, ...],
    reports: dict[str, SuiteReport],
    failures: list[str],
    started_at: str,
    details: dict | None = None,
) -> None:
    """Best-effort terminalization that preserves the original exception."""

    if isinstance(error, Exception):
        description = f"validation aborted by {type(error).__name__}"
    else:
        description = f"validation interrupted by {type(error).__name__}"
    if details:
        for stage in details["suite_states"].values():
            if stage["state"] in {"pending", "running"}:
                stage["state"] = "interrupted" if stage["state"] == "running" else "not-run"
                stage["reason"] = description
                stage["completed_at"] = _utc_timestamp()
    failures.append(description + (f": {error}" if str(error) else ""))
    failures.extend(_cleanup_run_temporary(paths))
    try:
        _write_run_manifest(
            paths,
            # V1 already uses ``failed`` as its terminal non-admission state.
            # Preserve that identity-bearing vocabulary and retain the exact
            # interruption or exception provenance in ``failures``.
            state="failed",
            tier=tier,
            jobs=jobs,
            source_fingerprint=source_fingerprint,
            selected_suites=selected_suites,
            reports=reports,
            failures=failures,
            started_at=started_at,
            details=details,
        )
    except Exception as terminalization_error:
        error.add_note(
            "validation could not write its failed terminal manifest: "
            f"{terminalization_error}"
        )


def _finish_run(
    paths: ValidationRunPaths,
    *,
    tier: str,
    jobs: int,
    source_fingerprint: str,
    selected_suites: tuple[PythonTestSuite, ...],
    reports: dict[str, SuiteReport],
    failures: list[str],
    started_at: str,
    repository_files: Callable[[], list[Path]],
    details: dict | None = None,
) -> ValidationRunPaths:
    """Verify, clean, and terminalize a normally drained validation run."""

    if details:
        for stage in details["suite_states"].values():
            if stage["state"] == "pending":
                stage.update(state="not-run", reason="An earlier suite failed; new work was not admitted.")

    try:
        final_source_fingerprint = fingerprint_paths(ROOT, repository_files())
    except OrchestrationFailure as exc:
        failures.append(str(exc))
    except Exception as exc:
        failures.append(
            "could not verify final validation source fingerprint: "
            f"{type(exc).__name__}: {exc}"
        )
    else:
        if final_source_fingerprint != source_fingerprint:
            failures.append(
                "validation source fingerprint drifted during suite execution: "
                f"expected {source_fingerprint}, "
                f"observed {final_source_fingerprint}"
            )

    if not failures and set(reports) != {
        suite.name for suite in selected_suites
    }:
        missing = sorted(
            {suite.name for suite in selected_suites} - set(reports)
        )
        failures.append(
            "Python suite validation ended without complete reports: "
            + ", ".join(missing)
        )
    failures.extend(_cleanup_run_temporary(paths))
    if not failures:
        try:
            for suite in selected_suites:
                _publish_timing_report(dict(reports[suite.name].document), suite.name)
        except OSError as exc:
            failures.append(f"could not publish diagnostic timing reports: {exc}")
    if failures:
        _write_run_manifest(
            paths,
            state="failed",
            tier=tier,
            jobs=jobs,
            source_fingerprint=source_fingerprint,
            selected_suites=selected_suites,
            reports=reports,
            failures=failures,
            started_at=started_at,
            details=details,
        )
        raise SuiteExecutionFailure(
            "Python suite validation failed; run artifacts retained at "
            f"{paths.root.relative_to(ROOT)}:\n- "
            + "\n- ".join(failures)
        )
    _write_run_manifest(
        paths,
        state="passed",
        tier=tier,
        jobs=jobs,
        source_fingerprint=source_fingerprint,
        selected_suites=selected_suites,
        reports=reports,
        failures=failures,
        started_at=started_at,
        details=details,
    )

    print("Python suite summary:")
    for suite in selected_suites:
        report = reports[suite.name]
        print(
            f"  {report.executed_tests:4} tests  "
            f"{report.seconds:9.3f}s  {suite.name}"
        )
    print(
        f"  {sum(report.executed_tests for report in reports.values()):4} tests  "
        f"{sum(report.seconds for report in reports.values()):9.3f}s  summed suite time (parallel intervals overlap)",
        flush=True,
    )
    return paths


def run_python_suites(
    *,
    tier: str,
    selected: tuple[str, ...],
    jobs: int,
    source_fingerprint: str,
    repository_files: Callable[[], list[Path]],
    run_id: str | None = None,
    selection_plan: dict | None = None,
) -> ValidationRunPaths:
    """Run and validate registered suites, retaining one run-scoped record."""

    suites = (
        tuple(SUITES_BY_NAME[name] for name in selected)
        if selected
        else suites_for_tier(tier)
    )
    effective_jobs = min(jobs, len(suites))
    run_paths = create_run_paths(
        ROOT / ".workbench/validation/runs",
        run_id or new_run_id(),
        temporary_storage_root=_validation_temporary_storage(),
    )
    requests = _suite_requests(
        suites,
        paths=run_paths,
        source_fingerprint=source_fingerprint,
    )
    request_by_name = {request.suite.name: request for request in requests}
    pending = list(requests)
    active: dict[Future[_SuiteProcessResult], _SuiteRequest] = {}
    reports: dict[str, SuiteReport] = {}
    failures: list[str] = []
    process_registry: dict[str, subprocess.Popen[bytes]] = {}
    process_lock = threading.Lock()
    interruption_event = threading.Event()
    started_at = _utc_timestamp()
    started_clock = time.perf_counter()
    details = {
        "selection_kind": "focused" if selected else tier,
        "selection": selection_plan,
        "environment": environment_provenance(ROOT),
        "timing_policy": "Current-source v3 process wall estimates; exclusive phase first.",
        "suite_states": {
            request.suite.name: {
                "state": "pending", "requested_at": started_at,
                "storage": "repository-filesystem" if request.suite.repository_temp else "system-temporary",
                "temporary_root": str(request.temporary_root),
                "resource_locks": list(request.suite.resource_locks),
                "exclusive": request.suite.exclusive,
                "estimated_process_seconds": request.scheduling.estimated_seconds,
            } for request in requests
        },
    }
    try:
        details["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        details["commit"] = None
    _write_run_manifest(
        run_paths,
        state="running",
        tier=tier,
        jobs=effective_jobs,
        source_fingerprint=source_fingerprint,
        selected_suites=suites,
        reports=reports,
        failures=failures,
        started_at=started_at,
        details=details,
    )
    print(
        f"Python validation run {run_paths.run_id}: {len(suites)} suites, "
        f"{effective_jobs} worker{'s' if effective_jobs != 1 else ''}; "
        f"artifacts: {run_paths.root.relative_to(ROOT)}",
        flush=True,
    )

    executor: ThreadPoolExecutor | None = None
    try:
        executor = ThreadPoolExecutor(
            max_workers=effective_jobs,
            thread_name_prefix="validation-suite",
        )
        while pending or active:
            selected_items = ()
            if not failures:
                selected_items = select_runnable(
                    [request.scheduling for request in pending],
                    [request.scheduling for request in active.values()],
                    jobs=effective_jobs,
                )
                for item in selected_items:
                    request = request_by_name[item.name]
                    pending.remove(request)
                    details["suite_states"][request.suite.name].update(
                        state="running", launched_at=_utc_timestamp(),
                        queue_seconds=round(time.perf_counter() - started_clock, 6),
                    )
                    print(
                        f"[{request.suite.name}] started; timeout "
                        f"{request.suite.timeout_seconds}s",
                        flush=True,
                    )
                    future = executor.submit(
                        _run_suite_process,
                        request,
                        process_registry=process_registry,
                        process_lock=process_lock,
                        interruption_event=interruption_event,
                    )
                    active[future] = request
            if selected_items:
                _write_run_manifest(
                    run_paths, state="running", tier=tier, jobs=effective_jobs,
                    source_fingerprint=source_fingerprint, selected_suites=suites,
                    reports=reports, failures=failures, started_at=started_at, details=details,
                )
            if not active:
                if pending and not failures:
                    failures.append(
                        "validation scheduler made no forward progress"
                    )
                break

            completed, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
            for future in sorted(
                completed,
                key=lambda value: active[value].scheduling.order,
            ):
                request = active.pop(future)
                try:
                    process_result = future.result()
                    stage = details["suite_states"][request.suite.name]
                    stage.update(
                        state="failed", process_wall_seconds=process_result.outcome.elapsed_seconds,
                        returncode=process_result.outcome.returncode,
                        timed_out=process_result.outcome.timed_out,
                        process_started_at=process_result.started_at,
                        completed_at=process_result.completed_at or _utc_timestamp(),
                    )
                    require_successful_process(process_result.outcome)
                    test_ids = process_result.admitted_test_ids
                    if test_ids is None:
                        raise OrchestrationFailure("suite completed without scheduler inventory admission")
                    report = load_suite_report(
                        request.report_path,
                        expected_suite=request.suite.name,
                        expected_run_id=run_paths.run_id,
                        expected_source_fingerprint=source_fingerprint,
                        expected_authority=request.suite.authority,
                        expected_test_ids=test_ids,
                    )
                    # Preserve the measured process cost in the diagnostic cache.
                    raw = dict(report.document)
                    raw["process_wall_seconds"] = process_result.outcome.elapsed_seconds
                    _atomic_write_json(request.report_path, raw)
                    report = replace(report, document=raw)
                    stage.update(state="passed", outcomes={
                        value: sum(row.outcome == value for row in report.tests)
                        for value in sorted({row.outcome for row in report.tests})
                    })
                    reports[request.suite.name] = report
                    print(
                        f"[{request.suite.name}] passed "
                        f"{report.executed_tests} tests in {report.seconds:.3f}s; "
                        f"wall {process_result.outcome.elapsed_seconds:.3f}s",
                        flush=True,
                    )
                except Exception as exc:
                    details["suite_states"][request.suite.name].update(
                        state="failed", completed_at=_utc_timestamp(), reason=str(exc),
                    )
                    detail = _log_excerpt(request.log_path)
                    failures.append(
                        f"{request.suite.name}: {exc}"
                        + (f"\n{detail}" if detail else "")
                    )
            _write_run_manifest(
                run_paths, state="running", tier=tier, jobs=effective_jobs,
                source_fingerprint=source_fingerprint, selected_suites=suites,
                reports=reports, failures=failures, started_at=started_at, details=details,
            )
        # A failed suite stops admission of new work, but already-running suites
        # are drained so their own descendant cleanup and timing writes finish.
    except BaseException as interrupted:
        interruption_event.set()
        with process_lock:
            running_processes = tuple(process_registry.values())
        for process in running_processes:
            _terminate_process(process)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
            executor = None
        _terminalize_abnormal_run(
            run_paths,
            error=interrupted,
            tier=tier,
            jobs=effective_jobs,
            source_fingerprint=source_fingerprint,
            selected_suites=suites,
            reports=reports,
            failures=failures,
            started_at=started_at,
            details=details,
        )
        raise
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    try:
        return _finish_run(
            run_paths,
            tier=tier,
            jobs=effective_jobs,
            source_fingerprint=source_fingerprint,
            selected_suites=suites,
            reports=reports,
            failures=failures,
            started_at=started_at,
            details=details,
            repository_files=repository_files,
        )
    except SuiteExecutionFailure:
        raise
    except BaseException as interrupted:
        _terminalize_abnormal_run(
            run_paths,
            error=interrupted,
            tier=tier,
            jobs=effective_jobs,
            source_fingerprint=source_fingerprint,
            selected_suites=suites,
            reports=reports,
            failures=failures,
            started_at=started_at,
            details=details,
        )
        raise
