"""Observe one projected client through exit and analyze its final evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname

from workbench_core.artifact_store import sha256_file
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .runtime_anvil_observe import (
    RuntimeAnvilObserveError,
    iter_runtime_anvil_worlds,
)
from .runtime_diagnose import (
    RuntimeDiagnosisError,
    diagnose_project_runtime,
)
from .runtime_launch import (
    RuntimeLaunchError,
    _capture_file,
    _latest_crash,
    _probe_launcher,
    _probe_launcher_root,
    launch_project_runtime,
)
from .runtime_worldgen_audit import (
    RuntimeWorldgenAuditError,
    audit_project_worldgen,
)
from workbench_api.state_paths import default_suite_state_root


INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
WINDOWS_IMAGE_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
MAX_LAUNCH_TIMEOUT = 86_400.0
MAX_ATTACH_TIMEOUT = 600.0
MAX_SESSION_TIMEOUT = 86_400.0


class RuntimeObserveError(ValueError):
    """Raised when a full client session cannot be observed safely."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _local_uri(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise RuntimeObserveError(f"{label} must be a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeObserveError(f"{label} must be a local file URI")
    return Path(url2pathname(parsed.path)).resolve()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeObserveError(f"observation evidence already exists: {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        temporary.rename(path)
    except OSError as exc:
        raise RuntimeObserveError(
            f"cannot retain runtime observation evidence: {path}"
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _windows_process_pids(
    instance_id: str,
    image_names: Sequence[str],
) -> frozenset[int]:
    """Return command-bound native Windows PIDs for selected images."""

    if INSTANCE_ID_RE.fullmatch(instance_id) is None:
        raise RuntimeObserveError("projected instance ID is unsafe for probing")
    if (
        not image_names
        or any(
            WINDOWS_IMAGE_RE.fullmatch(image_name) is None
            for image_name in image_names
        )
    ):
        raise RuntimeObserveError("Windows process image is unsafe for probing")
    escaped = instance_id.replace("'", "''")
    process_filter = " OR ".join(
        f"Name = '{image_name}'" for image_name in image_names
    )
    script = (
        f"$needle = '{escaped}'; "
        "$items = Get-CimInstance Win32_Process -Filter "
        f'"{process_filter}"; '
        "$items | Where-Object { $_.CommandLine -and "
        "$_.CommandLine.IndexOf($needle, "
        "[System.StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
        "ForEach-Object { [string]$_.ProcessId }"
    )
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeObserveError(
            "cannot query Workbench-owned Windows runtime processes"
        ) from exc
    if completed.returncode:
        raise RuntimeObserveError(
            "Workbench-owned Windows runtime process query failed"
        )
    pids: set[int] = set()
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.isdigit():
            raise RuntimeObserveError(
                "Workbench-owned Windows runtime process query was malformed"
            )
        pids.add(int(line))
    return frozenset(pids)


def _windows_instance_pids(instance_id: str) -> frozenset[int]:
    """Return exact projected Java PIDs after command-line filtering."""

    return _windows_process_pids(instance_id, ("javaw.exe", "java.exe"))


def _windows_runtime_pids(
    instance_id: str,
    launcher_image_name: str,
) -> frozenset[int]:
    """Return Prism/MultiMC and Java PIDs bound to one Workbench instance."""

    return _windows_process_pids(
        instance_id,
        (launcher_image_name, "javaw.exe", "java.exe"),
    )


def preflight_close_observer_launcher(
    launcher_executable: Path | str,
    launcher_root: Path | str,
    launcher: str,
) -> dict[str, Any]:
    """Prove launcher, data-root, host, and process inventory readiness."""

    try:
        executable, identity, host = _probe_launcher(
            launcher_executable,
            launcher,
        )
    except RuntimeLaunchError as exc:
        raise RuntimeObserveError(str(exc)) from exc
    if host.get("os") != "windows":
        raise RuntimeObserveError(
            "V1 close observation requires a Windows launcher host"
        )
    root = Path(launcher_root).expanduser().resolve()
    try:
        _probe_launcher_root(root, launcher)
    except RuntimeLaunchError as exc:
        raise RuntimeObserveError(str(exc)) from exc
    launcher_image_name = executable.name
    if WINDOWS_IMAGE_RE.fullmatch(launcher_image_name) is None:
        raise RuntimeObserveError("runtime launcher image is unsafe for probing")
    # Exercise the exact CIM authority before staging or materialization.  The
    # reserved readiness token's result is intentionally ignored.
    _windows_runtime_pids(
        "workbench-runtime-readiness-probe",
        launcher_image_name,
    )
    return {
        "executable_uri": executable.as_uri(),
        "host": dict(host),
        "launcher": dict(identity),
        "launcher_root_uri": root.as_uri(),
    }


def require_runtime_processes_closed(
    runtime_result: Mapping[str, Any],
    *,
    _owned_process_probe: (
        Callable[[str, str], frozenset[int]] | None
    ) = None,
) -> None:
    """Fail unless one returned observation proves and rechecks exact closure.

    This is deliberately a publication gate, not a cleanup authority.  It
    never terminates a process and it does not alter identity-bearing runtime
    receipts.
    """

    session = runtime_result.get("receipt")
    final = runtime_result.get("launch_receipt")
    session_launch = session.get("launch") if isinstance(session, dict) else None
    lifecycle = (
        session_launch.get("process_observation")
        if isinstance(session_launch, dict)
        else None
    )
    checks = (
        session.get("analysis_lifecycle_checks")
        if isinstance(session, dict)
        else None
    )
    failures = (
        session.get("analysis_failures") if isinstance(session, dict) else None
    )
    final_launcher = final.get("launcher") if isinstance(final, dict) else None
    final_observation = (
        final.get("observation") if isinstance(final, dict) else None
    )
    final_policy = (
        final.get("launch_policy") if isinstance(final, dict) else None
    )
    instance_id = (
        session_launch.get("instance_id")
        if isinstance(session_launch, dict)
        else None
    )
    observed_pids = (
        lifecycle.get("observed_pids") if isinstance(lifecycle, dict) else None
    )
    if (
        runtime_result.get("outcome") not in {"completed", "analysis-incomplete"}
        or not isinstance(session, dict)
        or session.get("outcome") != runtime_result.get("outcome")
        or not isinstance(final, dict)
        or final.get("outcome") != "checkpoint-reached"
        or not isinstance(lifecycle, dict)
        or lifecycle.get("state") != "exited"
        or not isinstance(observed_pids, list)
        or not observed_pids
        or any(type(pid) is not int or pid <= 0 for pid in observed_pids)
        or len(observed_pids) != len(set(observed_pids))
        or not isinstance(lifecycle.get("attached_at"), str)
        or not lifecycle["attached_at"]
        or not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(check, dict)
            or check.get("state") != "absent"
            or check.get("observed_pids") != []
            for check in checks
        )
        or not isinstance(failures, list)
        or any(
            not isinstance(failure, dict)
            or failure.get("kind") == "live-root-lifecycle-invalid"
            for failure in failures
        )
        or not isinstance(final_launcher, dict)
        or final_launcher.get("instance_id") != instance_id
        or not isinstance(instance_id, str)
        or INSTANCE_ID_RE.fullmatch(instance_id) is None
        or not isinstance(final_observation, dict)
        or final_observation.get("session_exit") != lifecycle
        or not isinstance(final_policy, dict)
        or final_policy.get("observation_boundary")
        != "projected-client-process-exit"
    ):
        raise RuntimeObserveError(
            "runtime observation does not prove a closed Workbench process set"
        )
    host = final_launcher.get("host")
    if not isinstance(host, dict) or host.get("os") != "windows":
        raise RuntimeObserveError(
            "runtime closure requires its exact Windows launcher host"
        )
    executable = _local_uri(
        final_launcher.get("executable_uri"),
        "runtime launcher executable",
    )
    launcher_image_name = executable.name
    if WINDOWS_IMAGE_RE.fullmatch(launcher_image_name) is None:
        raise RuntimeObserveError("runtime launcher image is unsafe for probing")
    process_probe = (
        _windows_runtime_pids
        if _owned_process_probe is None
        else _owned_process_probe
    )
    # Two current empty samples close the race left by a single inventory read.
    for _sample in range(2):
        current = process_probe(instance_id, launcher_image_name)
        if not isinstance(current, frozenset) or any(
            type(pid) is not int or pid <= 0 for pid in current
        ):
            raise RuntimeObserveError(
                "Workbench-owned runtime process inventory is malformed"
            )
        if current:
            raise RuntimeObserveError(
                "a Workbench-owned launcher or Java process remains live"
            )


def _validate_observation_bounds(
    *,
    launch_timeout: float,
    attach_timeout: float,
    session_timeout: float,
    poll_interval: float,
) -> None:
    bounds = (
        ("launch timeout", launch_timeout, MAX_LAUNCH_TIMEOUT),
        ("attach timeout", attach_timeout, MAX_ATTACH_TIMEOUT),
        ("session timeout", session_timeout, MAX_SESSION_TIMEOUT),
        ("poll interval", poll_interval, 30.0),
    )
    for label, value, maximum in bounds:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value > maximum
        ):
            raise RuntimeObserveError(
                f"{label} must be finite and between 0 and {maximum} seconds"
            )


def _observe_process_exit(
    instance_id: str,
    *,
    attach_timeout: float,
    session_timeout: float,
    process_probe: Callable[[str], frozenset[int]],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    poll_interval: float,
) -> dict[str, Any]:
    _validate_observation_bounds(
        launch_timeout=1.0,
        attach_timeout=attach_timeout,
        session_timeout=session_timeout,
        poll_interval=poll_interval,
    )
    started = clock()
    attach_deadline = started + attach_timeout
    session_deadline = started + session_timeout
    observed_pids: set[int] = set()
    attached_at: str | None = None
    empty_samples = 0
    samples = 0
    failures = 0
    while True:
        now = clock()
        if now >= session_deadline:
            return {
                "state": "session-timeout",
                "method": "windows-cim-instance-id",
                "samples": samples,
                "observed_pids": sorted(observed_pids),
                "attached_at": attached_at,
                "observed_at": _timestamp(),
            }
        try:
            current = process_probe(instance_id)
        except RuntimeObserveError:
            failures += 1
            if failures >= 3:
                return {
                    "state": "probe-failed",
                    "method": "windows-cim-instance-id",
                    "samples": samples,
                    "observed_pids": sorted(observed_pids),
                    "attached_at": attached_at,
                    "observed_at": _timestamp(),
                }
            sleep(poll_interval)
            continue
        failures = 0
        samples += 1
        if current:
            if attached_at is None:
                attached_at = _timestamp()
            observed_pids.update(current)
            empty_samples = 0
        elif observed_pids:
            empty_samples += 1
            if empty_samples >= 2:
                return {
                    "state": "exited",
                    "method": "windows-cim-instance-id",
                    "samples": samples,
                    "observed_pids": sorted(observed_pids),
                    "attached_at": attached_at,
                    "observed_at": _timestamp(),
                }
        elif now >= attach_deadline:
            return {
                "state": "attach-timeout",
                "method": "windows-cim-instance-id",
                "samples": samples,
                "observed_pids": [],
                "attached_at": None,
                "observed_at": _timestamp(),
            }
        sleep(poll_interval)


def _check_process_absent(
    instance_id: str,
    *,
    phase: str,
    process_probe: Callable[[str], frozenset[int]],
) -> dict[str, Any]:
    try:
        current = process_probe(instance_id)
    except RuntimeObserveError as exc:
        return {
            "phase": phase,
            "state": "probe-failed",
            "method": "windows-cim-instance-id",
            "observed_pids": [],
            "observed_at": _timestamp(),
            "reason": str(exc)[:1000],
        }
    return {
        "phase": phase,
        "state": "absent" if not current else "process-reappeared",
        "method": "windows-cim-instance-id",
        "observed_pids": sorted(current),
        "observed_at": _timestamp(),
    }


def _analysis_failure(kind: str, reason: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "reason": reason[:1000],
        "observed_at": _timestamp(),
    }


def _capture_final_evidence(
    runtime_root: Path,
    destination: Path,
    *,
    redact_values: Sequence[str],
) -> list[dict[str, Any]]:
    if destination.exists() or destination.is_symlink():
        raise RuntimeObserveError(
            f"final runtime evidence already exists: {destination}"
        )
    declared = (
        ("minecraft-latest-log", "logs/latest.log", "minecraft-latest.log"),
        ("minecraft-debug-log", "logs/debug.log", "minecraft-debug.log"),
        ("minecraft-groovy-log", "logs/groovy.log", "minecraft-groovy.log"),
        ("minecraft-cleanmix-log", "logs/cleanmix.log", "minecraft-cleanmix.log"),
    )
    sources = [
        (
            label,
            _safe_runtime_descendant(runtime_root, relative),
            filename,
        )
        for label, relative, filename in declared
    ]
    crash_root = _safe_runtime_descendant(runtime_root, "crash-reports")
    crash = _latest_crash(crash_root)
    destination.mkdir()
    captures: list[dict[str, Any]] = []
    for label, source, filename in sources:
        captures.append(_capture_file(
            source,
            destination / filename,
            label,
            redact_values=redact_values,
        ))
    if crash is not None:
        captures.append(_capture_file(
            crash,
            destination / "minecraft-crash-report.txt",
            "minecraft-crash-report",
            redact_values=redact_values,
        ))
    return captures


def _safe_runtime_descendant(runtime_root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeObserveError("runtime evidence path is unsafe")
    root = runtime_root.resolve()
    current = root
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeObserveError(
                f"runtime evidence path contains a symbolic link: {current}"
            )
    try:
        resolved = current.resolve(strict=False)
    except OSError as exc:
        raise RuntimeObserveError(
            f"runtime evidence path cannot be resolved: {current}"
        ) from exc
    if not resolved.is_relative_to(root):
        raise RuntimeObserveError(
            f"runtime evidence path escapes the projected instance: {current}"
        )
    return current


def _retained_identity(path: Path) -> dict[str, Any]:
    digest, size = sha256_file(path)
    return {"uri": path.resolve().as_uri(), "sha256": digest, "size": size}


def observe_project_runtime(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launcher: str = "prism",
    state_root: Path | str | None = None,
    launcher_executable: Path | str,
    launcher_root: Path | str,
    launcher_profile: str | None = None,
    launcher_java: Path | str | None = None,
    launcher_java_state: Path | str | None = None,
    packwiz_executable: Path | str | None = None,
    seed_roots: Sequence[Path | str] = (),
    memory_mib: int = 8192,
    offline_name: str = "Workbench",
    compatibility_patches: Sequence[Path | str] = (),
    timeout_seconds: float = 600.0,
    attach_timeout: float = 120.0,
    session_timeout: float = 21_600.0,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
    _process_probe: Callable[[str], frozenset[int]] | None = None,
    _clock: Callable[[], float] = time.monotonic,
    _sleep: Callable[[float], None] = time.sleep,
    _poll_interval: float = 1.0,
) -> dict[str, Any]:
    """Launch a client, wait for its process to exit, and analyze final logs."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).resolve()
    if configuration is not None and config_path is not None:
        raise RuntimeObserveError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise RuntimeObserveError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    _validate_observation_bounds(
        launch_timeout=timeout_seconds,
        attach_timeout=attach_timeout,
        session_timeout=session_timeout,
        poll_interval=_poll_interval,
    )
    preflight_host: Mapping[str, str] | None = None
    if _process_probe is None:
        readiness = preflight_close_observer_launcher(
            launcher_executable,
            launcher_root,
            launcher,
        )
        preflight_host = readiness["host"]
    started_at = _timestamp()
    try:
        launch_result = launch_project_runtime(
            suite,
            workspace,
            launcher=launcher,
            state_root=state,
            launcher_executable=launcher_executable,
            launcher_root=launcher_root,
            launcher_profile=launcher_profile,
            launcher_java=launcher_java,
            launcher_java_state=launcher_java_state,
            packwiz_executable=packwiz_executable,
            seed_roots=seed_roots,
            memory_mib=memory_mib,
            offline_name=offline_name,
            compatibility_patches=compatibility_patches,
            timeout_seconds=timeout_seconds,
            configuration=active_configuration,
        )
    except RuntimeLaunchError as exc:
        raise RuntimeObserveError(str(exc)) from exc
    parent = launch_result.get("receipt")
    if not isinstance(parent, dict):
        raise RuntimeObserveError("runtime launch result has no receipt")
    launcher_record = parent.get("launcher")
    target = parent.get("target")
    if not isinstance(launcher_record, dict) or not isinstance(target, dict):
        raise RuntimeObserveError("runtime launch receipt is incomplete")
    instance_id = launcher_record.get("instance_id")
    if not isinstance(instance_id, str) or INSTANCE_ID_RE.fullmatch(instance_id) is None:
        raise RuntimeObserveError("runtime launch receipt has an invalid instance ID")
    projection = _local_uri(
        launcher_record.get("projection_uri"),
        "launcher projection",
    )
    run_root = _local_uri(target.get("run_root_uri"), "launch evidence root")
    parent_receipt_path = _local_uri(
        target.get("receipt_uri"),
        "parent launch receipt",
    )
    expected_evidence_root = (state / "evidence/runtime").resolve()
    if (
        projection.is_symlink()
        or not projection.is_dir()
        or run_root.is_symlink()
        or not run_root.is_dir()
        or not run_root.is_relative_to(expected_evidence_root)
        or parent_receipt_path.is_symlink()
        or not parent_receipt_path.is_file()
        or not parent_receipt_path.is_relative_to(run_root)
    ):
        raise RuntimeObserveError("runtime launch targets are unsafe or unavailable")
    runtime_root = projection / ".minecraft"
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise RuntimeObserveError("projected Minecraft root is unsafe or unavailable")
    host = launcher_record.get("host")
    bound_process_probe: Callable[[str], frozenset[int]] | None = None
    if launch_result.get("outcome") == "checkpoint-reached":
        if _process_probe is None:
            if (
                not isinstance(host, dict)
                or host.get("os") != "windows"
                or preflight_host is None
                or dict(host) != dict(preflight_host)
            ):
                raise RuntimeObserveError(
                    "launcher host changed after close-observer preflight"
                )
            process_probe = _windows_instance_pids
        else:
            process_probe = _process_probe
        bound_process_probe = process_probe
        lifecycle = _observe_process_exit(
            instance_id,
            attach_timeout=attach_timeout,
            session_timeout=session_timeout,
            process_probe=process_probe,
            clock=_clock,
            sleep=_sleep,
            poll_interval=_poll_interval,
        )
    else:
        lifecycle = {
            "state": "launch-ended-before-attach",
            "method": "launch-outcome",
            "samples": 0,
            "observed_pids": [],
            "attached_at": None,
            "observed_at": _timestamp(),
        }
    exited = lifecycle["state"] == "exited"
    evidence_phase = "final" if exited else "incomplete-snapshot"
    final_root = run_root / evidence_phase
    captures = _capture_final_evidence(
        runtime_root,
        final_root,
        redact_values=(() if launcher_profile is None else (launcher_profile,)),
    )
    if (
        launch_result.get("outcome") == "checkpoint-reached"
        and not any(
            item.get("label") == "minecraft-latest-log"
            and item.get("state") == "captured"
            for item in captures
        )
    ):
        raise RuntimeObserveError("Minecraft latest log was not retained")
    final_receipt_path = run_root / "runtime-launch-v3.json"
    parent_identity = _retained_identity(parent_receipt_path)
    final_receipt = deepcopy(parent)
    final_receipt["format"] = "workbench-runtime-launch-receipt-v3"
    final_receipt["schema_version"] = 3
    final_receipt["parent_launch_receipt"] = {
        "launch_id": parent.get("launch_id"),
        **parent_identity,
    }
    final_receipt["observed_at"] = lifecycle["observed_at"]
    final_receipt["evidence"] = captures
    final_receipt["observation"] = {
        **(
            parent.get("observation")
            if isinstance(parent.get("observation"), dict)
            else {}
        ),
        "session_exit": lifecycle,
    }
    final_receipt["launch_policy"] = {
        **(
            parent.get("launch_policy")
            if isinstance(parent.get("launch_policy"), dict)
            else {}
        ),
        "observation_boundary": (
            "projected-client-process-exit"
            if exited
            else "incomplete-process-observation"
        ),
        "attach_timeout_seconds": attach_timeout,
        "session_timeout_seconds": session_timeout,
    }
    final_receipt["limitations"] = [
        *(
            parent.get("limitations")
            if isinstance(parent.get("limitations"), list)
            else []
        ),
        (
            "The close observer retains only instance-local Minecraft evidence; "
            "launcher-global logs and Java command lines are excluded."
        ),
        *(
            []
            if exited
            else [
                (
                    "The exact client exit was not observed. Captures are a "
                    "bounded, potentially live snapshot and are not quiescent "
                    "final evidence."
                )
            ]
        ),
    ]
    final_receipt["target"] = {
        **target,
        "receipt_uri": final_receipt_path.as_uri(),
        "parent_receipt_uri": parent_receipt_path.as_uri(),
    }
    final_identity = {
        "parent_launch_id": parent.get("launch_id"),
        "session_exit": lifecycle,
        "evidence": [
            {
                key: item.get(key)
                for key in ("label", "state", "sha256", "size")
            }
            for item in captures
        ],
    }
    final_receipt["launch_id"] = "sha256:" + sha256(
        _canonical_bytes(final_identity)
    ).hexdigest()
    _write_json(final_receipt_path, final_receipt)

    analysis_failures: list[dict[str, Any]] = []
    lifecycle_checks: list[dict[str, Any]] = []
    diagnosis: dict[str, Any] | None = None
    try:
        diagnosis = diagnose_project_runtime(
            suite,
            workspace,
            launch_receipt=final_receipt_path,
            configuration=active_configuration,
        )
    except RuntimeDiagnosisError as exc:
        failure = _analysis_failure("runtime-diagnosis-failed", str(exc))
        analysis_failures.append(failure)
        diagnosis_record: dict[str, Any] = {
            "state": "failed",
            **failure,
        }
    else:
        diagnosis_path = run_root / "runtime-diagnosis-v2.json"
        _write_json(diagnosis_path, diagnosis)
        diagnosis_record = {
            "diagnosis_id": diagnosis["diagnosis_id"],
            "state": diagnosis["state"],
            **_retained_identity(diagnosis_path),
        }

    def live_root_absent(phase: str) -> bool:
        if bound_process_probe is None:
            check = {
                "phase": phase,
                "state": "probe-unavailable",
                "method": "none",
                "observed_pids": [],
                "observed_at": _timestamp(),
            }
        else:
            check = _check_process_absent(
                instance_id,
                phase=phase,
                process_probe=bound_process_probe,
            )
        lifecycle_checks.append(check)
        if check["state"] == "absent":
            return True
        reason = f"{phase}: {check['state']}"
        if check["observed_pids"]:
            reason += " (projected client PID observed)"
        analysis_failures.append(_analysis_failure(
            "live-root-lifecycle-invalid",
            reason,
        ))
        return False

    worldgen_audit: dict[str, Any] | None = None
    if not exited:
        worldgen_record: dict[str, Any] = {
            "state": "not-run",
            "reason": "exact projected-client process exit was not observed",
        }
        live_root_safe = False
    else:
        live_root_safe = live_root_absent("before-worldgen-audit")
        if not live_root_safe:
            worldgen_record = {
                "state": "not-run",
                "reason": "projected client absence could not be reconfirmed",
            }
        else:
            worldgen_error: RuntimeWorldgenAuditError | None = None
            try:
                worldgen_candidate = audit_project_worldgen(
                    suite,
                    workspace,
                    runtime_root=runtime_root,
                    configuration=active_configuration,
                )
            except RuntimeWorldgenAuditError as exc:
                worldgen_candidate = None
                worldgen_error = exc
            live_root_safe = live_root_absent("after-worldgen-audit")
            if worldgen_error is not None:
                failure = _analysis_failure(
                    "runtime-worldgen-audit-failed",
                    str(worldgen_error),
                )
                analysis_failures.append(failure)
                worldgen_record = {"state": "failed", **failure}
            elif not live_root_safe:
                worldgen_record = {
                    "state": "discarded",
                    "reason": (
                        "projected client absence was not preserved through "
                        "the worldgen audit"
                    ),
                }
            else:
                worldgen_audit = worldgen_candidate
                audit_path = run_root / "runtime-worldgen-audit-v1.json"
                _write_json(audit_path, worldgen_audit)
                worldgen_record = {
                    "audit_id": worldgen_audit["audit_id"],
                    "state": worldgen_audit["state"],
                    **_retained_identity(audit_path),
                }

    anvil_records: list[dict[str, Any]] = []
    if exited and live_root_safe:
        live_root_safe = live_root_absent("before-anvil-observation")
    if exited and live_root_safe:
        try:
            for index, item in enumerate(iter_runtime_anvil_worlds(
                suite,
                runtime_root,
            )):
                live_root_safe = live_root_absent(
                    f"after-anvil-world-{index:03d}"
                )
                if not live_root_safe:
                    anvil_records.append({
                        "world_name": item.get("world_name"),
                        "world_uri": item.get("world_uri"),
                        "state": "discarded",
                        "reason": (
                            "projected client absence was not preserved "
                            "through this Anvil observation"
                        ),
                    })
                    break
                item_error = item.get("error")
                if isinstance(item_error, dict):
                    failure = _analysis_failure(
                        str(item_error.get("kind", "anvil-observation-failed")),
                        str(item_error.get("reason", "unknown Atlas failure")),
                    )
                    analysis_failures.append(failure)
                    anvil_records.append({
                        "world_name": item["world_name"],
                        "world_uri": item["world_uri"],
                        "state": "failed",
                        **failure,
                    })
                    continue
                observation = item["observation"]
                observation_path = (
                    run_root / f"atlas-anvil-observation-{index:03d}-v1.json"
                )
                _write_json(observation_path, observation)
                facts = observation.get("facts")
                summary = (
                    facts.get("summary") if isinstance(facts, dict) else None
                )
                anvil_records.append({
                    "world_name": item["world_name"],
                    "world_uri": item["world_uri"],
                    "observation_id": observation["observation_id"],
                    "state": observation.get("state"),
                    "summary": (
                        dict(summary) if isinstance(summary, dict) else {}
                    ),
                    **_retained_identity(observation_path),
                })
        except RuntimeAnvilObserveError as exc:
            failure = _analysis_failure(
                "anvil-observation-set-failed",
                str(exc),
            )
            analysis_failures.append(failure)
            anvil_records.append({"state": "failed", **failure})
        if live_root_safe:
            live_root_safe = live_root_absent("after-anvil-observation-set")
            if not live_root_safe:
                anvil_records.append({
                    "state": "lifecycle-invalidated",
                    "reason": (
                        "projected client absence was not preserved through "
                        "the Anvil observation set"
                    ),
                })
    elif exited:
        anvil_records.append({
            "state": "not-run",
            "reason": "projected client absence could not be reconfirmed",
        })

    if launch_result.get("outcome") != "checkpoint-reached":
        outcome = "launch-failed"
    elif lifecycle["state"] == "probe-failed":
        outcome = "probe-failed"
    elif lifecycle["state"] != "exited":
        outcome = "timed-out"
    elif analysis_failures:
        outcome = "analysis-incomplete"
    else:
        outcome = "completed"
    session_path = run_root / "runtime-observation-v1.json"
    observed_at = _timestamp()
    session: dict[str, Any] = {
        "format": "workbench-runtime-observation-session-v1",
        "schema_version": 1,
        "operation_class": "local-mutation",
        "state": "complete" if outcome == "completed" else "incomplete",
        "outcome": outcome,
        "started_at": started_at,
        "observed_at": observed_at,
        "launch": {
            "parent_launch_id": parent.get("launch_id"),
            "final_launch_id": final_receipt["launch_id"],
            "parent_receipt": parent_identity,
            "final_receipt": _retained_identity(final_receipt_path),
            "instance_id": instance_id,
            "projection_uri": projection.as_uri(),
            "process_observation": lifecycle,
        },
        "diagnosis": diagnosis_record,
        "worldgen_audit": worldgen_record,
        "anvil_observations": anvil_records,
        "analysis_lifecycle_checks": lifecycle_checks,
        "analysis_failures": analysis_failures,
        "evidence": captures,
        "target": {
            "run_root_uri": run_root.as_uri(),
            "receipt_uri": session_path.as_uri(),
        },
        "limitations": [
            (
                "The session does not bind a reported visual symptom to exact "
                "chunk coordinates or validate terrain continuity semantics."
            )
            if exited
            else (
                "The session is incomplete; its snapshot may have been "
                "captured while the projected client was still running."
            ),
            *(
                [
                    "One or more post-close analyzers did not retain a "
                    "complete trusted result; see analysis_failures."
                ]
                if analysis_failures
                else []
            ),
        ],
    }
    session["session_id"] = "sha256:" + sha256(
        _canonical_bytes(session)
    ).hexdigest()
    _write_json(session_path, session)
    return {
        "format": "workbench-runtime-observation-result-v1",
        "schema_version": 1,
        "outcome": outcome,
        "receipt": session,
        "launch_receipt": final_receipt,
        "diagnosis": diagnosis,
        "worldgen_audit": worldgen_audit,
        "anvil_observations": anvil_records,
    }
