"""Core-owned isolated wheel installation with prepared and restart evidence.

Only a previously retained wheelhouse is used. Completion here means the
isolated pip operation and pip check ended; installed profile admission and the
portable share's unresolved package marker remain separate work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from threading import Event
from typing import Any, Mapping
import venv

from workbench_api.processes import ProcessError
from workbench_api.working_allocations import WorkingAllocationError

from .durable_records import (
    private_record_lock, publish_immutable_bytes,
    read_private_single_link_bytes,
)
from .environment_package_closure import _digest_file
from .environment_package_import import reopen_package_import
from .environment_package_install_plan import plan_package_install_preflight
from .environment_reconstruction import ReconstructionError, _canonical, _resource_host, _seal
from .environment_resolution import resolve_environment
from .host_filesystem import private_path
from . import process_capture, tool_process
from .storage.registered import DurableResourceError
from .working_allocations import CoreWorkingAllocations


ATTEMPT_FORMAT = "workbench-environment-package-install-attempt-v1"
MARKER_FORMAT = "workbench-environment-package-install-marker-v1"
FINISHED_FORMAT = "workbench-environment-package-install-finished-v1"
RESULT_FORMAT = "workbench-environment-package-install-result-v1"
_ATTEMPT_NAME = "environment-package-install-attempt.json"
_RESULT_NAME = "environment-package-install.json"
_MARKER_NAME = "workbench-package-install-attempt.json"
_FINISHED_NAME = "workbench-package-install-finished.json"
_RECORD_LIMIT = 256 * 1024
_OUTPUT_LIMIT = 8 * 1024 * 1024
_DESTINATION_BLOCKER = "stable isolated install destination already exists; review recovery before reuse"
_SCOPE = "Isolated pip completed; installed module/profile admission and optional package closure remain unresolved."


def _preflight(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str, environment: Mapping[str, str],
) -> dict[str, Any]:
    return plan_package_install_preflight(
        suite_root, share, candidate, closure_plan,
        workspace=workspace, package_result_resource_id=package_result_resource_id,
        environment=environment,
    )


def _stable_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items()
            if key not in {"plan_id", "blockers", "state"}}


def _requires_existing_only(current: Mapping[str, Any], reviewed: Mapping[str, Any]) -> None:
    if (_stable_fields(current) != _stable_fields(reviewed)
            or current["state"] != "blocked"
            or current["blockers"] != [_DESTINATION_BLOCKER]):
        raise ReconstructionError("isolated install inputs changed after review")


def _record(path: Path, body: Mapping[str, Any]) -> None:
    publish_immutable_bytes(
        path, _canonical(body) + b"\n", byte_limit=_RECORD_LIMIT,
    )


def _read_record(path: Path, *, prefix: str, id_field: str,
                 record_format: str) -> dict[str, Any]:
    try:
        raw = read_private_single_link_bytes(path, byte_limit=_RECORD_LIMIT)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"isolated install marker cannot be reopened: {exc}") from exc
    if (type(value) is not dict or raw != _canonical(value) + b"\n"
            or value.get("format") != record_format or value.get("schema_version") != 1
            or value != _seal({key: item for key, item in value.items() if key != id_field},
                              prefix, id_field)):
        raise ReconstructionError("isolated install marker has another identity")
    return value


def _attempt(
    service: Any, resource_id: str, reviewed: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        ref = service.describe(resource_id)
        raw = service.read_bytes(resource_id)
        value = json.loads(raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"isolated install prepared attempt cannot be reopened: {exc}") from exc
    plan = value.get("reviewed_preflight") if type(value) is dict else None
    if (ref.owner_id != "workbench-core" or ref.role != "evidence"
            or ref.domain_id != reviewed["share_id"]
            or type(value) is not dict or raw != _canonical(value) + b"\n"
            or value.get("format") != ATTEMPT_FORMAT or value.get("schema_version") != 1
            or value.get("state") != "prepared"
            or value.get("share_id") != reviewed["share_id"]
            or value.get("candidate_id") != reviewed["candidate_id"]
            or value.get("closure_plan_id") != reviewed["closure_plan_id"]
            or value.get("package_result_resource_id") != reviewed["package_result_resource_id"]
            or value.get("destination") != reviewed["destination"]
            or type(plan) is not dict or plan.get("state") != "reviewed"
            or plan.get("blockers") != []
            or plan != _seal({key: item for key, item in plan.items() if key != "plan_id"},
                             "workbench-environment-package-install-preflight", "plan_id")
            or _stable_fields(plan) != _stable_fields(reviewed)
            or set(value) != {
                "format", "schema_version", "state", "share_id", "candidate_id",
                "closure_plan_id", "package_result_resource_id", "destination",
                "reviewed_preflight",
            }):
        raise ReconstructionError("isolated install prepared attempt has another review")
    return value


def _marker(plan: Mapping[str, Any], attempt_resource_id: str,
            allocation_id: str) -> dict[str, Any]:
    return _seal({
        "format": MARKER_FORMAT, "schema_version": 1,
        "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
        "closure_plan_id": plan["closure_plan_id"],
        "package_result_resource_id": plan["package_result_resource_id"],
        "preflight_plan_id": plan["plan_id"],
        "attempt_resource_id": attempt_resource_id,
        "allocation_id": allocation_id,
        "destination": plan["destination"],
    }, "workbench-environment-package-install-marker", "marker_id")


def _allocation_host(local: Any) -> CoreWorkingAllocations:
    return CoreWorkingAllocations(
        workspace=local.workspace, configuration_home=local.configuration_home,
        locations=local.locations, owner_id="workbench-core",
        policy_id=local.record["resolution_id"],
        location_sources={role: row["source"] for role, row in local.record["locations"].items()},
    )


def _allocation_description(host: CoreWorkingAllocations, target: Path) -> Any:
    try:
        matches = [row for row in host.inventory()
                   if row.reference.path == target
                   and row.reference.family == "environment.packages"]
        if len(matches) != 1:
            raise ReconstructionError("isolated install destination has no unique Core allocation")
        return matches[0]
    except (WorkingAllocationError, OSError, ValueError) as exc:
        raise ReconstructionError(f"isolated install allocation cannot be inventoried: {exc}") from exc


def _current_allocation(host: CoreWorkingAllocations, target: Path) -> Any:
    description = _allocation_description(host, target)
    try:
        allocation = host.open(description.reference.allocation_id)
        if allocation.path != target:
            raise ReconstructionError("isolated install allocation changed path")
        return allocation
    except (WorkingAllocationError, OSError, ValueError) as exc:
        raise ReconstructionError(f"isolated install allocation cannot be reopened: {exc}") from exc


def _read_marker(target: Path, service: Any, reviewed: Mapping[str, Any],
                 allocation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not private_path(target, directory=True):
        raise ReconstructionError("isolated install destination is not owner-private")
    marker = _read_record(
        target / _MARKER_NAME,
        prefix="workbench-environment-package-install-marker", id_field="marker_id",
        record_format=MARKER_FORMAT,
    )
    if (marker.get("share_id") != reviewed["share_id"]
            or marker.get("candidate_id") != reviewed["candidate_id"]
            or marker.get("closure_plan_id") != reviewed["closure_plan_id"]
            or marker.get("package_result_resource_id") != reviewed["package_result_resource_id"]
            or marker.get("destination") != reviewed["destination"]
            or marker.get("allocation_id") != allocation_id
            or type(marker.get("attempt_resource_id")) is not str):
        raise ReconstructionError("isolated install destination has another Core marker")
    attempt = _attempt(service, marker["attempt_resource_id"], reviewed)
    if marker != _marker(
        attempt["reviewed_preflight"], marker["attempt_resource_id"], allocation_id,
    ):
        raise ReconstructionError("isolated install marker differs from its prepared attempt")
    return marker, attempt


def _python_path(plan: Mapping[str, Any]) -> Path:
    return Path(plan["destination"]) / "bin/python"


def _verify_python(plan: Mapping[str, Any]) -> dict[str, Any]:
    python = _python_path(plan)
    expected = plan["interpreter"]["base"]
    try:
        info = python.lstat()
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o100:
            raise ReconstructionError("isolated environment has no ordinary executable Python")
        observed = _digest_file(python, expected["size"])
    except OSError as exc:
        raise ReconstructionError(f"isolated environment Python cannot be reviewed: {exc}") from exc
    if observed != expected["sha256"].removeprefix("sha256:"):
        raise ReconstructionError("isolated environment Python differs from the reviewed base interpreter")
    return {"path": str(python), "sha256": "sha256:" + observed, "size": info.st_size}


def _process_environment() -> dict[str, str]:
    values = {key: value for key, value in os.environ.items()
              if not key.startswith(("PYTHON", "PIP_"))
              and key not in {"VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}}
    values["PIP_CONFIG_FILE"] = os.devnull
    return values


def _capture(plan: Mapping[str, Any], attempt_resource_id: str,
             stage: str, command: list[str], *, timeout: int) -> dict[str, Any]:
    destination = Path(plan["destination"])
    directory = destination / f"workbench-package-{stage}-capture"
    binding = f"{attempt_resource_id}:{stage}"
    try:
        result = tool_process.capture(
            command, directory=directory, binding=binding,
            cwd=destination, stdin=b"", environment=_process_environment(),
            cancelled=Event(), timeout_seconds=timeout, output_limit=_OUTPUT_LIMIT,
        )
    except (ProcessError, OSError, ValueError) as exc:
        raise ReconstructionError(f"isolated package {stage} process did not close: {exc}") from exc
    if result.exit_code != 0:
        raise ReconstructionError(f"isolated package {stage} exited with code {result.exit_code}")
    return {"capture_id": result.capture_id, "binding": binding,
            "exit_code": result.exit_code}


def _install(plan: Mapping[str, Any], closure_plan: Mapping[str, Any],
             wheelhouse: Path, attempt_resource_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    destination = Path(plan["destination"])
    venv.EnvBuilder(with_pip=False, clear=False, symlinks=False).create(destination)
    python = _verify_python(plan)
    pip_rows = [row for row in closure_plan["wheels"] if row["name"] == "pip"]
    if len(pip_rows) != 1:
        raise ReconstructionError("retained closure has no singular pip bootstrap")
    bootstrap = wheelhouse / "wheels" / pip_rows[0]["filename"]
    command = [
        python["path"], "-I", "-c",
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')",
        str(bootstrap), "--isolated", "install", "--no-index",
        "--only-binary=:all:", "--require-hashes", "--no-cache-dir",
        "--find-links", str(wheelhouse / "wheels"),
        "-r", str(wheelhouse / "requirements.lock"),
    ]
    installed = _capture(plan, attempt_resource_id, "install", command, timeout=1200)
    checked = _capture(
        plan, attempt_resource_id, "check",
        [python["path"], "-I", "-m", "pip", "--isolated", "check"],
        timeout=120,
    )
    return _verify_python(plan), {"install": installed, "check": checked}


def _capture_evidence(plan: Mapping[str, Any], attempt_resource_id: str,
                      captures: Mapping[str, Any]) -> tuple[Path, ...]:
    if type(captures) is not dict or set(captures) != {"install", "check"}:
        raise ReconstructionError("isolated install lacks its two bounded process captures")
    files: list[Path] = []
    for stage in ("install", "check"):
        item = captures[stage]
        binding = f"{attempt_resource_id}:{stage}"
        directory = Path(plan["destination"]) / f"workbench-package-{stage}-capture"
        if (type(item) is not dict or set(item) != {"capture_id", "binding", "exit_code"}
                or item["binding"] != binding or item["exit_code"] != 0
                or type(item["capture_id"]) is not str):
            raise ReconstructionError("isolated install capture has another owner or outcome")
        try:
            record = process_capture.load(
                directory, binding=binding, expected_id=item["capture_id"],
            )
            if record["exit_code"] != 0:
                raise ReconstructionError("isolated install capture records a failed process")
            retained = process_capture.retained_files(
                directory, binding=binding, expected_id=item["capture_id"], verify=True,
            )
        except (ProcessError, OSError, ValueError) as exc:
            raise ReconstructionError(f"isolated install process capture changed: {exc}") from exc
        files.extend(retained.values())
    return tuple(files)


def _finished(plan: Mapping[str, Any], marker: Mapping[str, Any],
              python: Mapping[str, Any], captures: Mapping[str, Any]) -> dict[str, Any]:
    return _seal({
        "format": FINISHED_FORMAT, "schema_version": 1,
        "state": "pip-complete-unadmitted",
        "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
        "closure_plan_id": plan["closure_plan_id"],
        "package_result_resource_id": plan["package_result_resource_id"],
        "preflight_plan_id": plan["plan_id"],
        "attempt_resource_id": marker["attempt_resource_id"],
        "allocation_id": marker["allocation_id"],
        "destination": plan["destination"], "python": dict(python),
        "captures": dict(captures),
        "package_tree_id": plan["package_tree_id"],
        "package_tree_content_sha256": plan["package_tree_content_sha256"],
        "target_inventory_sha256": plan["targets"]["target_inventory_sha256"],
        "unresolved_inputs": plan["unresolved_inputs"],
    }, "workbench-environment-package-install-finished", "finished_id")


def _read_finished(target: Path, plan: Mapping[str, Any], marker: Mapping[str, Any]) -> dict[str, Any] | None:
    path = target / _FINISHED_NAME
    if not path.exists() and not path.is_symlink():
        return None
    value = _read_record(
        path, prefix="workbench-environment-package-install-finished",
        id_field="finished_id", record_format=FINISHED_FORMAT,
    )
    python = _verify_python(plan)
    _capture_evidence(plan, marker["attempt_resource_id"], value.get("captures"))
    if value != _finished(plan, marker, python, value["captures"]):
        raise ReconstructionError("isolated install completion differs from retained evidence")
    return value


def _selected_evidence(plan: Mapping[str, Any], marker: Mapping[str, Any],
                       finished: Mapping[str, Any]) -> tuple[Path, ...]:
    target = Path(plan["destination"])
    return (
        target / _MARKER_NAME, target / _FINISHED_NAME,
        target / "pyvenv.cfg", _python_path(plan),
        *_capture_evidence(plan, marker["attempt_resource_id"], finished["captures"]),
    )


def _result(plan: Mapping[str, Any], marker: Mapping[str, Any], finished: Mapping[str, Any],
            *, outcome: str) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT, "schema_version": 1,
        "state": "pip-complete-unadmitted", "outcome": outcome,
        "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
        "closure_plan_id": plan["closure_plan_id"],
        "package_result_resource_id": plan["package_result_resource_id"],
        "preflight_plan_id": plan["plan_id"],
        "attempt_resource_id": marker["attempt_resource_id"],
        "allocation_id": marker["allocation_id"],
        "finished_id": finished["finished_id"],
        "captures": finished["captures"],
        "destination": plan["destination"], "python": finished["python"],
        "package_tree_id": plan["package_tree_id"],
        "package_tree_content_sha256": plan["package_tree_content_sha256"],
        "target_inventory_sha256": plan["targets"]["target_inventory_sha256"],
        "workspace": plan["workspace"],
        "environment_resolution_id": plan["environment_resolution_id"],
        "unresolved_inputs": plan["unresolved_inputs"], "scope": _SCOPE,
    }


def _publish_result(service: Any, plan: Mapping[str, Any], marker: Mapping[str, Any],
                    finished: Mapping[str, Any], *, outcome: str) -> dict[str, Any]:
    result = _result(plan, marker, finished, outcome=outcome)
    payload = _canonical(result) + b"\n"
    ref = service.publish_bytes(
        "evidence", _RESULT_NAME, payload, domain_id=plan["share_id"],
        references=(marker["attempt_resource_id"], plan["package_result_resource_id"]),
    )
    if service.read_bytes(ref.resource_id) != payload:
        raise ReconstructionError("isolated install result did not reopen exactly")
    return {**result, "resource": {
        "resource_id": ref.resource_id, "store_id": ref.store_id,
        "path": str(ref.path), "sha256": ref.sha256,
    }}


def apply_package_install(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str, expected_plan_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Install once at the reviewed stable path with a prior Core attempt."""

    values = dict(os.environ if environment is None else environment)
    plan = _preflight(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        package_result_resource_id=package_result_resource_id, environment=values,
    )
    if type(expected_plan_id) is not str or expected_plan_id != plan["plan_id"]:
        raise ReconstructionError("isolated install changed after review")
    if plan["state"] != "reviewed":
        raise ReconstructionError("isolated install is blocked: " + "; ".join(plan["blockers"]))
    lock = Path(plan["install_root"]) / f".environment-install-{Path(plan['destination']).name}.lock"
    with private_record_lock(lock, wait=True):
        current = _preflight(
            suite_root, share, candidate, closure_plan, workspace=workspace,
            package_result_resource_id=package_result_resource_id, environment=values,
        )
        if current != plan:
            raise ReconstructionError("isolated install inputs changed before preparation")
        local = resolve_environment(suite_root, workspace=workspace, environment=values)
        service = _resource_host(Path(suite_root), local.workspace, values)
        if service.policy_id != plan["environment_resolution_id"]:
            raise ReconstructionError("isolated install Core resolution changed")
        prepared = {
            "format": ATTEMPT_FORMAT, "schema_version": 1, "state": "prepared",
            "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
            "closure_plan_id": plan["closure_plan_id"],
            "package_result_resource_id": package_result_resource_id,
            "destination": plan["destination"], "reviewed_preflight": plan,
        }
        attempt_ref = service.publish_bytes(
            "evidence", _ATTEMPT_NAME, _canonical(prepared) + b"\n",
            domain_id=plan["share_id"], references=(package_result_resource_id,),
        )
        if service.read_bytes(attempt_ref.resource_id) != _canonical(prepared) + b"\n":
            raise ReconstructionError("isolated install prepared attempt did not reopen")
        target = Path(plan["destination"])
        allocation_host = _allocation_host(local)
        try:
            allocation = allocation_host.allocate(
                "environment.packages", target.name, requested_path=target,
            )
        except WorkingAllocationError as exc:
            raise ReconstructionError(f"isolated install Core allocation failed: {exc}") from exc
        _record(target / _MARKER_NAME, _marker(plan, attempt_ref.resource_id, allocation.allocation_id))
        allocation_host.open(allocation.allocation_id)
        marker, _ = _read_marker(target, service, plan, allocation.allocation_id)
        retained = reopen_package_import(
            suite_root, share, candidate, closure_plan, workspace=workspace,
            result_resource_id=package_result_resource_id, environment=values,
        )
        try:
            with allocation_host.execution(allocation):
                python, captures = _install(
                    plan, closure_plan, Path(retained["tree_path"]), attempt_ref.resource_id,
                )
                after = _preflight(
                    suite_root, share, candidate, closure_plan, workspace=workspace,
                    package_result_resource_id=package_result_resource_id, environment=values,
                )
                _requires_existing_only(after, plan)
                allocation_host.open(allocation.allocation_id)
                renewed_marker, _ = _read_marker(
                    target, service, plan, allocation.allocation_id,
                )
                if renewed_marker != marker:
                    raise ReconstructionError("isolated install attempt marker changed during pip")
                completion = _finished(plan, marker, python, captures)
                _record(target / _FINISHED_NAME, completion)
                if _read_finished(target, plan, marker) != completion:
                    raise ReconstructionError("isolated install completion did not reopen")
                allocation_host.finish(
                    allocation, outcome="complete",
                    evidence=_selected_evidence(plan, marker, completion),
                    validate=lambda _: _read_finished(target, plan, marker),
                )
        except WorkingAllocationError as exc:
            raise ReconstructionError(f"isolated install allocation could not close: {exc}") from exc
        return _publish_result(service, plan, marker, completion, outcome="installed")


def _existing_result(service: Any, plan: Mapping[str, Any], attempt_id: str) -> str | None:
    try:
        rows = service.catalog.inventory(workspace=Path(plan["workspace"]))["resources"]
    except (DurableResourceError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"isolated install results cannot be inventoried: {exc}") from exc
    matches = [row for row in rows
               if row["owner_id"] == "workbench-core" and row["role"] == "evidence"
               and Path(row["path"]).name.endswith("-" + _RESULT_NAME)
               and attempt_id in row["references"]]
    if len(matches) > 1:
        raise ReconstructionError("isolated install has ambiguous completed results")
    if not matches:
        return None
    row = matches[0]
    if row["status"] in {"published-uncommitted", "committed-needs-reconcile"}:
        try:
            service.catalog.reconcile(row["resource_id"])
        except DurableResourceError as exc:
            raise ReconstructionError(f"isolated install result needs reviewed catalog recovery: {exc}") from exc
    elif row["status"] != "committed":
        raise ReconstructionError("isolated install result has an incomplete Core publication")
    return row["resource_id"]


def reconcile_package_install(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recover a completed install result; preserve incomplete installs."""

    values = dict(os.environ if environment is None else environment)
    current = _preflight(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        package_result_resource_id=package_result_resource_id, environment=values,
    )
    if current["blockers"] != [_DESTINATION_BLOCKER]:
        raise ReconstructionError("isolated install has no uniquely reviewable occupied destination")
    lock = Path(current["install_root"]) / f".environment-install-{Path(current['destination']).name}.lock"
    with private_record_lock(lock, wait=True):
        current = _preflight(
            suite_root, share, candidate, closure_plan, workspace=workspace,
            package_result_resource_id=package_result_resource_id, environment=values,
        )
        if current["blockers"] != [_DESTINATION_BLOCKER]:
            raise ReconstructionError("isolated install recovery inputs changed")
        local = resolve_environment(suite_root, workspace=workspace, environment=values)
        service = _resource_host(Path(suite_root), local.workspace, values)
        target = Path(current["destination"])
        allocation_host = _allocation_host(local)
        description = _allocation_description(allocation_host, target)
        marker_path = target / _MARKER_NAME
        if not marker_path.exists() and not marker_path.is_symlink():
            if description.status != "incomplete" or not private_path(target, directory=True):
                raise ReconstructionError("isolated install allocation changed before its domain marker")
            return {
                "state": "incomplete-review-required", "destination": str(target),
                "allocation_id": description.reference.allocation_id,
                "unresolved_inputs": current["unresolved_inputs"],
                "scope": "Core allocation is retained but its domain attempt marker was interrupted.",
            }
        allocation = _current_allocation(allocation_host, target)
        marker, attempt = _read_marker(target, service, current, allocation.allocation_id)
        reviewed = attempt["reviewed_preflight"]
        _requires_existing_only(current, reviewed)
        completed = _read_finished(target, reviewed, marker)
        if completed is None:
            return {
                "state": "incomplete-review-required",
                "destination": str(target),
                "allocation_id": allocation.allocation_id,
                "attempt_resource_id": marker["attempt_resource_id"],
                "unresolved_inputs": reviewed["unresolved_inputs"],
                "scope": "Prepared Core attempt is retained; incomplete environment is preserved for explicit review.",
            }
        retained = reopen_package_import(
            suite_root, share, candidate, closure_plan, workspace=workspace,
            result_resource_id=package_result_resource_id, environment=values,
        )
        if retained["tree_id"] != reviewed["package_tree_id"]:
            raise ReconstructionError("isolated install package tree changed after completion")
        try:
            status = allocation_host.describe(allocation.allocation_id).status
            if status == "incomplete":
                with allocation_host.execution(allocation):
                    allocation_host.finish(
                        allocation, outcome="complete",
                        evidence=_selected_evidence(reviewed, marker, completed),
                        validate=lambda _: _read_finished(target, reviewed, marker),
                    )
            elif status != "complete":
                raise ReconstructionError(f"isolated install allocation requires review: {status}")
            allocation_host.verify(allocation.allocation_id)
        except WorkingAllocationError as exc:
            raise ReconstructionError(f"isolated install allocation cannot be reconciled: {exc}") from exc
        existing = _existing_result(service, reviewed, marker["attempt_resource_id"])
        if existing is not None:
            result = reopen_package_install(
                suite_root, share, candidate, closure_plan,
                workspace=workspace, package_result_resource_id=package_result_resource_id,
                result_resource_id=existing, environment=values,
            )
            ref = service.describe(existing)
            return {**result, "resource": {
                "resource_id": ref.resource_id, "store_id": ref.store_id,
                "path": str(ref.path), "sha256": ref.sha256,
            }}
        return _publish_result(service, reviewed, marker, completed, outcome="reconciled")


def reopen_package_install(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    closure_plan: Mapping[str, Any], *, workspace: Path | str,
    package_result_resource_id: str, result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read the historical pip-complete receipt and current basic custody."""

    values = dict(os.environ if environment is None else environment)
    current = _preflight(
        suite_root, share, candidate, closure_plan, workspace=workspace,
        package_result_resource_id=package_result_resource_id, environment=values,
    )
    if current["blockers"] != [_DESTINATION_BLOCKER]:
        raise ReconstructionError("isolated install result has no reviewable destination")
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    target = Path(current["destination"])
    allocation_host = _allocation_host(local)
    allocation = _current_allocation(allocation_host, target)
    marker, attempt = _read_marker(target, service, current, allocation.allocation_id)
    reviewed = attempt["reviewed_preflight"]
    _requires_existing_only(current, reviewed)
    completed = _read_finished(target, reviewed, marker)
    if completed is None:
        raise ReconstructionError("isolated install has no completed pip evidence")
    try:
        allocation_host.verify(allocation.allocation_id)
    except WorkingAllocationError as exc:
        raise ReconstructionError(f"isolated install allocation has no exact terminal evidence: {exc}") from exc
    try:
        ref = service.describe(result_resource_id)
        raw = service.read_bytes(result_resource_id)
        result = json.loads(raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, TypeError) as exc:
        raise ReconstructionError(f"isolated install result cannot be reopened: {exc}") from exc
    if (ref.owner_id != "workbench-core" or ref.role != "evidence"
            or ref.domain_id != reviewed["share_id"]
            or type(result) is not dict or raw != _canonical(result) + b"\n"
            or result.get("outcome") not in {"installed", "reconciled"}
            or result != _result(reviewed, marker, completed, outcome=result["outcome"])):
        raise ReconstructionError("isolated install result differs from its prepared completion")
    return result


__all__ = ["apply_package_install", "reconcile_package_install", "reopen_package_install"]
