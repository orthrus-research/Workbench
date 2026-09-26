"""Supervise one retained Cleanroom fixture command under Core custody.

An observed process/group exit is only process evidence. The projection, cache,
capture, and attempt stay protected because a detached descendant or later
artifact mutation is not excluded by process-group supervision.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from threading import Event
from typing import Any, Mapping

from workbench_api.processes import ProcessError
from workbench_api.reusable_projections import ReusableProjectionError
from workbench_api.working_allocations import WorkingAllocationError

from . import process_capture, tool_process
from .durable_records import private_record_lock, publish_immutable_bytes, read_private_single_link_bytes
from .environment_fixture_command import plan_fixture_command
from .environment_fixture_gradle_import import _qualified_filesystem
from .environment_fixture_java_preflight import reopen_fixture_java_preflight
from .environment_fixture_projection import _host as _projection_host, _project_source
from .environment_reconstruction import ReconstructionError, _canonical, _resource_host, _seal
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported
from .host_filesystem import private_path
from .output_routing import _private_directory
from .reusable_projections import _generated, _generated_roots, _scan, _source_rows, _suffixes
from .storage.registered import DurableResourceError
from .working_allocations import CoreWorkingAllocations


PLAN_FORMAT = "workbench-environment-fixture-execution-plan-v1"
ATTEMPT_FORMAT = "workbench-environment-fixture-execution-attempt-v1"
MARKER_FORMAT = "workbench-environment-fixture-execution-marker-v1"
FINISHED_FORMAT = "workbench-environment-fixture-execution-finished-v1"
RESULT_FORMAT = "workbench-environment-fixture-execution-result-v1"
_ATTEMPT_FILE = "workbench-fixture-execution-attempt.json"
_FINISHED_FILE = "workbench-fixture-execution-finished.json"
_CAPTURE = "capture"
_RECORD_LIMIT = 256 * 1024
_PROJECTION_ID = re.compile(r"workbench-reusable-projection-v1:([0-9a-f]{64})\Z")
_SCOPE = (
    "Observed supervised process/group exit and bounded capture only; detached "
    "descendant absence, artifact admission and clean-root rebuild are unproven."
)
_REMAINING = ["fixture-artifact-admission", "combined-environment-reconstruction"]


def _arguments(
    *, workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    environment: Mapping[str, str] | None,
) -> dict[str, Any]:
    return {
        "workspace": workspace, "fixture_result_resource_id": fixture_result_resource_id,
        "expected_review_id": expected_review_id,
        "projection_result_resource_id": projection_result_resource_id,
        "binding_result_resource_id": binding_result_resource_id,
        "preflight_result_resource_id": preflight_result_resource_id,
        "environment": environment,
    }


def _current(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    kwargs: Mapping[str, Any],
) -> tuple[dict[str, Any], Any]:
    command = plan_fixture_command(suite_root, share, candidate, **kwargs)
    local = resolve_environment(
        suite_root, workspace=Path(command["workspace"]), environment=kwargs["environment"],
    )
    if command["environment_resolution_id"] != local.record["resolution_id"]:
        raise ReconstructionError("fixture execution resolution changed")
    return command, local


def _paths(command: Mapping[str, Any], local: Any) -> dict[str, Path]:
    match = _PROJECTION_ID.fullmatch(command["projection_id"])
    if match is None:
        raise ReconstructionError("fixture execution has no exact projection identity")
    selected = command["command"]
    target = local.locations["evidence"] / "environment-fixture-attempts" / match.group(1)
    cache = Path(selected["argv"][5])
    gradle_home = Path(selected["environment_overrides"]["GRADLE_USER_HOME"])
    if (selected["argv"][4] != "--project-cache-dir"
            or cache != local.state_root / "gradle-project-cache/generic-mod-daily-loop"
            or gradle_home != local.state_root / "gradle-home/generic-mod-daily-loop"
            or not target.is_absolute()):
        raise ReconstructionError("fixture execution paths differ from retained policy")
    return {"attempt": target, "project_cache": cache, "gradle_home": gradle_home,
            "home": target / "home", "tmp": target / "tmp"}


def _process_environment(command: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, str]:
    overrides = command["command"]["environment_overrides"]
    if set(overrides) != {"JAVA_HOME", "PATH_PREPEND", "GRADLE_USER_HOME", "TZ"}:
        raise ReconstructionError("fixture command has another environment policy")
    java_bin = str(Path(overrides["JAVA_HOME"]) / "bin")
    if (overrides["PATH_PREPEND"] != java_bin
            or overrides["GRADLE_USER_HOME"] != str(paths["gradle_home"])
            or overrides["TZ"] != "UTC"):
        raise ReconstructionError("fixture command environment changed")
    return {
        "HOME": str(paths["home"]), "TMPDIR": str(paths["tmp"]),
        "JAVA_HOME": overrides["JAVA_HOME"], "GRADLE_USER_HOME": overrides["GRADLE_USER_HOME"],
        "PATH": java_bin + ":/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
    }


def _allocation_host(local: Any) -> CoreWorkingAllocations:
    return CoreWorkingAllocations(
        workspace=local.workspace, configuration_home=local.configuration_home,
        locations=local.locations, owner_id="workbench-core",
        policy_id=local.record["resolution_id"],
        location_sources={role: row["source"] for role, row in local.record["locations"].items()},
    )


def _allocation_rows(host: CoreWorkingAllocations, target: Path) -> list[Any]:
    try:
        return [row for row in host.inventory()
                if row.reference.path == target and row.reference.family == "environment.fixture"]
    except (WorkingAllocationError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture allocation inventory cannot reopen: {exc}") from exc


def _base(command: Mapping[str, Any], local: Any) -> dict[str, Any]:
    paths = _paths(command, local)
    return {
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": command["share_id"], "candidate_id": command["candidate_id"],
        "workspace": command["workspace"],
        "environment_resolution_id": command["environment_resolution_id"],
        "command_plan_id": command["plan_id"],
        "fixture_result_resource_id": command["fixture_result_resource_id"],
        "fixture_policy_review_id": command["fixture_policy_review_id"],
        "projection_result_resource_id": command["projection_result_resource_id"],
        "projection_id": command["projection_id"],
        "binding_result_resource_id": command["binding_result_resource_id"],
        "preflight_result_resource_id": command["preflight_result_resource_id"],
        "gradle_tree_id": command["gradle_tree_id"], "owner_code": command["owner_code"],
        "command": command["command"], "process_environment": _process_environment(command, paths),
        "paths": {name: str(path) for name, path in paths.items()},
        "unresolved_inputs": command["unresolved_inputs"],
        "remaining_prerequisites": list(_REMAINING),
    }


def _blockers(paths: Mapping[str, Path]) -> list[str]:
    if not _tree_host_supported() or any(not _qualified_filesystem(path) for path in paths.values()):
        return ["fixture execution needs a qualified Linux/WSL private filesystem"]
    if any(ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)()
           for path in paths.values() for ancestor in (path, *path.parents)):
        return ["fixture execution path traverses a redirect"]
    blockers = []
    for name in ("attempt", "project_cache", "gradle_home"):
        path = paths[name]
        if path.exists() or path.is_symlink():
            blockers.append(f"{name} already exists; review its retained custody before another execution")
    return blockers


def plan_fixture_execution(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review one isolated attempt without creating cache or starting Gradle."""
    kwargs = _arguments(
        workspace=workspace, fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        environment=environment,
    )
    command, local = _current(suite_root, share, candidate, kwargs)
    body = _base(command, local)
    blockers = _blockers({name: Path(path) for name, path in body["paths"].items()})
    if _allocation_rows(_allocation_host(local), Path(body["paths"]["attempt"])):
        blockers.append("fixture execution has a prior Core allocation reservation")
    return _seal({**body, "blockers": blockers, "state": "blocked" if blockers else "ready"},
                 "workbench-environment-fixture-execution-plan", "plan_id")


def _write_record(path: Path, body: Mapping[str, Any]) -> None:
    publish_immutable_bytes(path, _canonical(body) + b"\n", byte_limit=_RECORD_LIMIT)


def _read_record(path: Path, record_format: str, prefix: str, id_field: str) -> dict[str, Any]:
    try:
        raw = read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"fixture execution marker cannot reopen: {exc}") from exc
    if (type(value) is not dict or raw != _canonical(value) + b"\n"
            or value.get("format") != record_format or value.get("schema_version") != 1
            or value != _seal({key: item for key, item in value.items() if key != id_field},
                              prefix, id_field)):
        raise ReconstructionError("fixture execution marker has another identity")
    return value


def _attempt(service: Any, resource_id: str, base: Mapping[str, Any]) -> dict[str, Any]:
    try:
        reference = service.describe(resource_id)
        raw = service.read_bytes(resource_id)
        value = json.loads(raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"fixture execution attempt cannot reopen: {exc}") from exc
    if (reference.owner_id != "workbench-core" or reference.role != "evidence"
            or reference.domain_id != base["share_id"] or raw != _canonical(value) + b"\n"
            or value != {"format": ATTEMPT_FORMAT, "schema_version": 1, "state": "prepared",
                         "reviewed": base}):
        raise ReconstructionError("fixture execution attempt has another binding")
    return value


def _allocation(host: CoreWorkingAllocations, target: Path) -> Any:
    try:
        rows = _allocation_rows(host, target)
        if len(rows) != 1 or rows[0].status != "incomplete":
            raise ReconstructionError("fixture execution has no unique retained Core allocation")
        return host.open(rows[0].reference.allocation_id)
    except (WorkingAllocationError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture execution allocation cannot reopen: {exc}") from exc


def _marker(base: Mapping[str, Any], attempt_id: str, allocation_id: str) -> dict[str, Any]:
    return _seal({
        "format": MARKER_FORMAT, "schema_version": 1,
        "share_id": base["share_id"], "candidate_id": base["candidate_id"],
        "command_plan_id": base["command_plan_id"],
        "attempt_resource_id": attempt_id, "allocation_id": allocation_id,
        "target": base["paths"]["attempt"],
    }, "workbench-environment-fixture-execution-marker", "marker_id")


def _fixture_source_check(host: Any, reference: Any, fixture: Mapping[str, Any]) -> None:
    record = host._record(reference.projection_id)
    project = Path(reference.project)
    source_rows = _source_rows(tuple(record["source_files"]))
    def validate(value: Path) -> None:
        _project_source(value, record["source_files"], fixture)
    root_info, parent_info = _scan(
        Path(reference.path), project, source_rows,
        _generated(tuple(record["generated_parts"])),
        _suffixes(tuple(record["generated_suffixes"])), validate,
        _generated_roots(tuple(Path(value) for value in record["generated_roots"])),
    )
    if ((root_info.st_dev, root_info.st_ino) != (record["device"], record["inode"])
            or (parent_info.st_dev, parent_info.st_ino) != (record["parent_device"], record["parent_inode"])):
        raise ReconstructionError("fixture projection changed during execution")


def _cache_directories(local: Any, paths: Mapping[str, Path]) -> None:
    for name, family in (("project_cache", "cleanroom.fixture-project-cache"),
                         ("gradle_home", "cleanroom.fixture-gradle-home")):
        path = paths[name]
        if path.exists() or path.is_symlink():
            raise ReconstructionError(f"fixture {name} has a prior or foreign occupant")
        _private_directory(path)
        if not private_path(path, directory=True):
            raise ReconstructionError("fixture cache cannot enforce private custody")
        _projection_host(local).catalog.register_record_store(
            family=family, owner_id="workbench-core", workspace=local.workspace, root=path,
        )


def _capture_evidence(target: Path, binding: str, capture_id: str) -> dict[str, Any]:
    directory = target / _CAPTURE
    try:
        files = process_capture.retained_files(
            directory, binding=binding, expected_id=capture_id, verify=True,
        )
        record = process_capture.load(directory, binding=binding, expected_id=capture_id)
    except (ProcessError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture capture cannot reopen: {exc}") from exc
    if record["state"] != "complete":
        raise ReconstructionError("fixture process has no complete supervisor capture")
    return {"capture_id": capture_id, "capture_directory": str(directory),
            "capture_files": sorted(files), "exit_code": record["exit_code"]}


def _retained_directories(paths: Mapping[str, Path]) -> None:
    for name in ("project_cache", "gradle_home", "home", "tmp"):
        path = paths[name]
        if any(ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)()
               for ancestor in (path, *path.parents)) or not private_path(path, directory=True):
            raise ReconstructionError(f"fixture {name} lost private custody")


def _attempt_members(target: Path, *, finished: bool) -> None:
    expected = {".workbench-allocation.json", _ATTEMPT_FILE, "home", "tmp", _CAPTURE}
    if finished:
        expected.add(_FINISHED_FILE)
    if {path.name for path in target.iterdir()} != expected:
        raise ReconstructionError("fixture attempt contains missing or foreign root members")


def apply_fixture_execution(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    expected_plan_id: str, environment: Mapping[str, str] | None = None,
    cancelled: Event | None = None,
) -> dict[str, Any]:
    """Prepare, allocate and launch once; any interrupted attempt blocks retry."""
    kwargs = _arguments(
        workspace=workspace, fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        environment=environment,
    )
    plan = plan_fixture_execution(suite_root, share, candidate, **kwargs)
    if plan["plan_id"] != expected_plan_id or plan["state"] != "ready":
        raise ReconstructionError("fixture execution changed or is blocked after review")
    command, local = _current(suite_root, share, candidate, kwargs)
    base = _base(command, local)
    paths = {name: Path(path) for name, path in base["paths"].items()}
    service = _resource_host(Path(suite_root), local.workspace, kwargs["environment"])
    if service.policy_id != base["environment_resolution_id"]:
        raise ReconstructionError("fixture execution resource policy changed")
    prepared = {"format": ATTEMPT_FORMAT, "schema_version": 1,
                "state": "prepared", "reviewed": base}
    attempt_ref = service.publish_bytes(
        "evidence", "environment-fixture-execution-attempt.json",
        _canonical(prepared) + b"\n", domain_id=base["share_id"],
        references=(base["fixture_result_resource_id"],
                    base["projection_result_resource_id"],
                    base["binding_result_resource_id"],
                    base["preflight_result_resource_id"]),
    )
    _attempt(service, attempt_ref.resource_id, base)
    host = _allocation_host(local)
    # Serializes the policy's fixed mutable cache paths across projections.
    _private_directory(local.state_root)
    with private_record_lock(local.state_root / ".workbench-fixture-execution.lock", wait=True):
        if _blockers(paths) or _allocation_rows(host, paths["attempt"]):
            raise ReconstructionError("fixture execution destination changed after preparation")
        try:
            allocation = host.allocate(
                "environment.fixture", paths["attempt"].name,
                requested_path=paths["attempt"],
            )
        except (WorkingAllocationError, OSError, ValueError) as exc:
            raise ReconstructionError(f"fixture execution allocation failed: {exc}") from exc
        marker = _marker(base, attempt_ref.resource_id, allocation.allocation_id)
        _write_record(allocation.path / _ATTEMPT_FILE, marker)
        with host.execution(allocation):
            # Full rederivation precedes the lease; reentering it inside the
            # lease would deadlock on the same projection record lock.
            if plan_fixture_command(suite_root, share, candidate, **kwargs) != command:
                raise ReconstructionError("fixture command changed before execution")
            projection_host = _projection_host(local)
            record = projection_host._record(base["projection_id"])
            fixture = deepcopy(dict(candidate))["profile_fixture"]
            def validate(project: Path) -> None:
                _project_source(project, record["source_files"], fixture)
            try:
                with projection_host.open(base["projection_id"], validate=validate) as opened:
                    preflight = reopen_fixture_java_preflight(
                        suite_root, share, candidate, workspace=local.workspace,
                        binding_result_resource_id=base["binding_result_resource_id"],
                        result_resource_id=base["preflight_result_resource_id"],
                        environment=kwargs["environment"],
                    )
                    if (preflight["gradle_tree_id"] != base["gradle_tree_id"]
                            or preflight["owner_code"] != base["owner_code"]):
                        raise ReconstructionError("fixture Java/Gradle input changed before launch")
                    _fixture_source_check(projection_host, opened, fixture)
                    _cache_directories(local, paths)
                    for name in ("home", "tmp"):
                        _private_directory(paths[name])
                        if not private_path(paths[name], directory=True):
                            raise ReconstructionError("fixture process home is not owner-private")
                    _retained_directories(paths)
                    binding = marker["marker_id"]
                    captured = tool_process.capture(
                        command["command"]["argv"], directory=allocation.path / _CAPTURE,
                        binding=binding, cwd=opened.project, stdin=b"",
                        environment=base["process_environment"],
                        cancelled=Event() if cancelled is None else cancelled,
                        timeout_seconds=command["command"]["capture"]["timeout_seconds"],
                        output_limit=command["command"]["capture"]["maximum_bytes_per_stream"],
                    )
                    _fixture_source_check(projection_host, opened, fixture)
                    if reopen_fixture_java_preflight(
                        suite_root, share, candidate, workspace=local.workspace,
                        binding_result_resource_id=base["binding_result_resource_id"],
                        result_resource_id=base["preflight_result_resource_id"],
                        environment=kwargs["environment"],
                    ) != preflight:
                        raise ReconstructionError("fixture Java/Gradle input changed during execution")
                    capture = _capture_evidence(allocation.path, binding, captured.capture_id)
                    if capture["exit_code"] != captured.exit_code:
                        raise ReconstructionError("fixture process exit differs from retained capture")
                    _retained_directories(paths)
                    _attempt_members(allocation.path, finished=False)
            except (ReusableProjectionError, ProcessError, WorkingAllocationError,
                    DurableResourceError, OSError, ValueError) as exc:
                if isinstance(exc, ReconstructionError):
                    raise
                raise ReconstructionError(f"fixture execution is retained with unknown outcome: {exc}") from exc
            result = {
                "format": RESULT_FORMAT, "schema_version": 1,
                "share_id": base["share_id"], "candidate_id": base["candidate_id"],
                "command_plan_id": base["command_plan_id"],
                "attempt_resource_id": attempt_ref.resource_id,
                "allocation_id": allocation.allocation_id,
                "target": str(allocation.path), "marker_id": marker["marker_id"],
                "capture": capture,
                "unresolved_inputs": base["unresolved_inputs"],
                "remaining_prerequisites": base["remaining_prerequisites"],
                "state": "observed-process-group-exit-unadmitted", "scope": _SCOPE,
            }
            payload = _canonical(result) + b"\n"
            completed = service.publish_bytes(
                "evidence", "environment-fixture-execution.json", payload,
                domain_id=base["share_id"], references=(attempt_ref.resource_id,),
            )
            if service.read_bytes(completed.resource_id) != payload:
                raise ReconstructionError("fixture execution result did not reopen exactly")
            finished = _seal({
                "format": FINISHED_FORMAT, "schema_version": 1,
                "marker_id": marker["marker_id"], "result_resource_id": completed.resource_id,
                "capture_id": capture["capture_id"],
            }, "workbench-environment-fixture-execution-finished", "finished_id")
            _write_record(allocation.path / _FINISHED_FILE, finished)
            return {**result, "resource": {"resource_id": completed.resource_id,
                                           "store_id": completed.store_id,
                                           "path": str(completed.path), "sha256": completed.sha256}}


def inspect_fixture_execution(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Classify one stable attempt after restart without launching a child."""
    kwargs = _arguments(
        workspace=workspace, fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        environment=environment,
    )
    command, local = _current(suite_root, share, candidate, kwargs)
    base = _base(command, local)
    paths = {name: Path(path) for name, path in base["paths"].items()}
    if not _tree_host_supported() or any(not _qualified_filesystem(path) for path in paths.values()):
        raise ReconstructionError("fixture execution host is no longer qualified")
    target = paths["attempt"]
    host = _allocation_host(local)
    if not target.exists() and not target.is_symlink():
        rows = _allocation_rows(host, target)
        if rows:
            return {"state": "unknown-after-preparation", "target": str(target),
                    "allocation_ids": [row.reference.allocation_id for row in rows],
                    "command_plan_id": base["command_plan_id"]}
        if paths["project_cache"].exists() or paths["gradle_home"].exists():
            raise ReconstructionError("fixture execution cache exists without its Core attempt")
        return {"state": "not-started", "target": str(target),
                "command_plan_id": base["command_plan_id"]}
    allocation = _allocation(host, target)
    service = _resource_host(Path(suite_root), local.workspace, kwargs["environment"])
    if service.policy_id != base["environment_resolution_id"]:
        raise ReconstructionError("fixture execution resource policy changed")
    if not private_path(target, directory=True):
        raise ReconstructionError("fixture execution attempt lost private custody")
    marker_path = target / _ATTEMPT_FILE
    if not marker_path.exists() and not marker_path.is_symlink():
        return {"state": "unknown-after-preparation", "target": str(target),
                "allocation_id": allocation.allocation_id,
                "command_plan_id": base["command_plan_id"]}
    marker = _read_record(marker_path, MARKER_FORMAT,
                          "workbench-environment-fixture-execution-marker", "marker_id")
    _attempt(service, marker.get("attempt_resource_id"), base)
    if marker != _marker(base, marker["attempt_resource_id"], allocation.allocation_id):
        raise ReconstructionError("fixture execution marker differs from prepared attempt")
    finished_path = target / _FINISHED_FILE
    if not finished_path.exists() and not finished_path.is_symlink():
        return {"state": "unknown-after-preparation", "target": str(target),
                "allocation_id": allocation.allocation_id,
                "attempt_resource_id": marker["attempt_resource_id"],
                "command_plan_id": base["command_plan_id"]}
    finished = _read_record(finished_path, FINISHED_FORMAT,
                            "workbench-environment-fixture-execution-finished", "finished_id")
    if finished.get("marker_id") != marker["marker_id"] or type(finished.get("result_resource_id")) is not str:
        raise ReconstructionError("fixture execution completion marker has another binding")
    try:
        reference = service.describe(finished["result_resource_id"])
        raw = service.read_bytes(finished["result_resource_id"])
        result = json.loads(raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"fixture execution result cannot reopen: {exc}") from exc
    capture = _capture_evidence(target, marker["marker_id"], finished["capture_id"])
    _retained_directories(paths)
    _attempt_members(target, finished=True)
    expected = {
        "format": RESULT_FORMAT, "schema_version": 1,
        "share_id": base["share_id"], "candidate_id": base["candidate_id"],
        "command_plan_id": base["command_plan_id"],
        "attempt_resource_id": marker["attempt_resource_id"],
        "allocation_id": allocation.allocation_id,
        "target": str(target), "marker_id": marker["marker_id"],
        "capture": capture, "unresolved_inputs": base["unresolved_inputs"],
        "remaining_prerequisites": base["remaining_prerequisites"],
        "state": "observed-process-group-exit-unadmitted", "scope": _SCOPE,
    }
    if (reference.owner_id != "workbench-core" or reference.role != "evidence"
            or reference.domain_id != base["share_id"]
            or result != expected or raw != _canonical(result) + b"\n"):
        raise ReconstructionError("fixture execution result has another identity or scope")
    return {**result, "resource": {"resource_id": reference.resource_id,
                                   "store_id": reference.store_id,
                                   "path": str(reference.path), "sha256": reference.sha256}}


def reopen_fixture_execution(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    result_resource_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    result = inspect_fixture_execution(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        environment=environment,
    )
    if result["state"] != "observed-process-group-exit-unadmitted" or result["resource"]["resource_id"] != result_resource_id:
        raise ReconstructionError("fixture execution has no matching completed capture")
    return result


__all__ = ["plan_fixture_execution", "apply_fixture_execution",
           "inspect_fixture_execution", "reopen_fixture_execution"]
