"""Admit one exact Cleanroom fixture JAR snapshot under Core custody.

The snapshot is linked to a zero-exit supervised attempt. It does not prove
that no detached child remains, establish arbitrary build provenance, or
complete an environment reconstruction.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Mapping

from workbench_api.durable_resources import DurableResourceError
from workbench_api.profile_extensions import (
    ProfileExtensionError, profile_extension_identity, require_profile_extension,
)

from .durable_files import _directory as pinned_directory, _visible_parent
from .durable_records import private_record_lock
from .environment_fixture_execution import reopen_fixture_execution
from .environment_fixture_execution_policy import reopen_fixture_execution_policy
from .environment_fixture_gradle_import import _qualified_filesystem
from .environment_fixture_import import _read_source, reopen_fixture_import
from .environment_fixture_projection import reopen_fixture_projection
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import ReconstructionError, _canonical, _resource_host, _seal, validate_share
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported
from .output_routing import _private_directory
from .storage.registered import DurableResourceError as CatalogResourceError


PLAN_FORMAT = "workbench-environment-fixture-artifact-plan-v1"
ATTEMPT_FORMAT = "workbench-environment-fixture-artifact-attempt-v1"
RESULT_FORMAT = "workbench-environment-fixture-artifact-admission-v1"
_ATTEMPT_NAME = "environment-fixture-artifact-attempt.json"
_RESULT_NAME = "environment-fixture-artifact-admission.json"
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_SCOPE = (
    "Exact immutable JAR bytes observed at the profile-selected output path "
    "after a zero-exit supervised fixture attempt passed owner content checks. "
    "Detached descendant absence, independent build provenance and complete "
    "environment reconstruction remain unproven."
)
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")


def _kwargs(
    *, workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    execution_result_resource_id: str, environment: Mapping[str, str] | None,
) -> dict[str, Any]:
    return {
        "workspace": workspace, "fixture_result_resource_id": fixture_result_resource_id,
        "expected_review_id": expected_review_id,
        "projection_result_resource_id": projection_result_resource_id,
        "binding_result_resource_id": binding_result_resource_id,
        "preflight_result_resource_id": preflight_result_resource_id,
        "execution_result_resource_id": execution_result_resource_id,
        "environment": environment,
    }


def _context(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any],
    args: Mapping[str, Any],
) -> dict[str, Any]:
    portable = validate_share(dict(share))
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    values = dict(os.environ if args["environment"] is None else args["environment"])
    local = resolve_environment(
        suite_root, workspace=args["workspace"], environment=values,
    )
    execution_args = {
        key: args[key] for key in (
            "workspace", "fixture_result_resource_id", "expected_review_id",
            "projection_result_resource_id", "binding_result_resource_id",
            "preflight_result_resource_id", "environment",
        )
    }
    execution = reopen_fixture_execution(
        suite_root, portable, reviewed,
        result_resource_id=args["execution_result_resource_id"], **execution_args,
    )
    if execution["capture"]["exit_code"] != 0:
        raise ReconstructionError("fixture artifact needs a zero-exit supervised attempt")
    imported = reopen_fixture_import(
        suite_root, portable, reviewed, workspace=local.workspace,
        result_resource_id=args["fixture_result_resource_id"], environment=values,
    )
    review = reopen_fixture_execution_policy(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=args["fixture_result_resource_id"],
        expected_review_id=args["expected_review_id"], environment=values,
    )
    projection = reopen_fixture_projection(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=args["fixture_result_resource_id"],
        expected_review_id=args["expected_review_id"],
        result_resource_id=args["projection_result_resource_id"], environment=values,
    )
    fixture_root = Path(projection["project"]).relative_to(Path(projection["projection_root"]))
    lock_row, = (
        row for row in reviewed["profile_fixture"]["sources"]
        if row["kind"] == "fixture-owner-lock"
    )
    if lock_row["relative_path"] != (fixture_root / "fixture-lock-v1.json").as_posix():
        raise ReconstructionError("fixture artifact lock has another projected path")
    retained_root = Path(imported["tree_path"])
    try:
        lock = json.loads(_read_source(
            retained_root / lock_row["relative_path"], limit=2 * 1024 * 1024,
        ).decode("utf-8"))
        properties = _read_source(
            retained_root / fixture_root / "gradle.properties", limit=2 * 1024 * 1024,
        )
        identity = profile_extension_identity("workbench.workspace_home_fixtures", "cleanroom")
        if identity != reviewed["profile_fixture"]["owner_code"]:
            raise ReconstructionError("fixture artifact owner code differs from the candidate")
        owner = require_profile_extension("workbench.workspace_home_fixtures", "cleanroom")
        spec = owner.portable_artifact_spec(
            fixture_lock=lock, execution_policy=review["policy"],
            gradle_properties=properties,
        )
    except (ProfileExtensionError, OSError, ValueError, UnicodeError,
            AttributeError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"fixture artifact owner cannot derive its identity: {exc}") from exc
    if (type(spec) is not dict or type(spec.get("relative_path")) is not str
            or type(spec.get("filename")) is not str):
        raise ReconstructionError("fixture artifact owner returned an invalid spec")
    relative = PurePosixPath(spec["relative_path"])
    generated = review["policy"]["paths"]["generated_root_relative_to_projection_digest"]
    if (spec.get("format") != "workbench-cleanroom-fixture-artifact-spec-v1"
            or spec.get("maximum_bytes") != _MAX_ARTIFACT_BYTES
            or relative.as_posix() != spec["relative_path"]
            or relative.is_absolute() or ".." in relative.parts
            or relative.parent.as_posix() != f"{generated}/libs"
            or relative.name != spec["filename"]):
        raise ReconstructionError("fixture artifact owner returned another output path or byte bound")
    target = Path(projection["projection_root"]).joinpath(*relative.parts)
    if not _tree_host_supported() or not _qualified_filesystem(target):
        raise ReconstructionError("fixture artifact needs a qualified Linux/WSL private filesystem")
    return {
        "share": portable, "candidate": reviewed, "local": local, "values": values,
        "service": _resource_host(Path(suite_root), local.workspace, values),
        "execution": execution, "projection": projection, "owner": owner,
        "spec": spec, "target": target,
    }


def _pinned_artifact(path: Path) -> bytes:
    """Pin all parent components and one ordinary leaf through exact reading."""

    parent = descriptor = -1
    try:
        parent = pinned_directory(path.parent, create=False)
        parent_id = (os.fstat(parent).st_dev, os.fstat(parent).st_ino)
        visible = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(visible.st_mode) or visible.st_nlink != 1
                or not 0 < visible.st_size <= _MAX_ARTIFACT_BYTES):
            raise ReconstructionError("fixture artifact is not one bounded ordinary file")
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if any(getattr(opened, field) != getattr(visible, field) for field in _STAT_FIELDS):
            raise ReconstructionError("fixture artifact changed while opening")
        blocks: list[bytes] = []
        remaining = _MAX_ARTIFACT_BYTES + 1
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                break
            blocks.append(block)
            remaining -= len(block)
        raw = b"".join(blocks)
        after = os.fstat(descriptor)
        final = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (len(raw) != opened.st_size
                or any(getattr(opened, field) != getattr(after, field)
                       or getattr(after, field) != getattr(final, field)
                       for field in _STAT_FIELDS)):
            raise ReconstructionError("fixture artifact changed during source read")
        _visible_parent(path.parent, parent_id)
        return raw
    except (OSError, DurableResourceError) as exc:
        raise ReconstructionError(f"fixture artifact cannot be pinned: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _rows(context: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    service = context["service"]
    execution_id = context["execution"]["resource"]["resource_id"]
    try:
        resources = service.catalog.inventory(workspace=service.workspace)["resources"]
    except (CatalogResourceError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture artifact resources cannot be inventoried: {exc}") from exc
    def selected(row: dict[str, Any], name: str) -> bool:
        return (row["owner_id"] == "workbench-core" and row["role"] == "evidence"
                and Path(row["path"]).name.endswith("-" + name)
                and execution_id in row["references"])
    attempts = [row for row in resources if selected(row, _ATTEMPT_NAME)]
    results = [row for row in resources if selected(row, _RESULT_NAME)]
    if len(attempts) > 1 or len(results) > 1:
        raise ReconstructionError("fixture artifact has ambiguous Core attempts or results")
    return attempts, results


def _outline(context: Mapping[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": context["share"]["share_id"],
        "candidate_id": context["candidate"]["candidate_id"],
        "workspace": str(context["local"].workspace),
        "environment_resolution_id": context["local"].record["resolution_id"],
        "execution_result_resource_id": context["execution"]["resource"]["resource_id"],
        "execution_capture_id": context["execution"]["capture"]["capture_id"],
        "fixture_tree_id": context["projection"]["fixture_tree_id"],
        "projection_id": context["projection"]["projection_id"],
        "artifact_path": str(context["target"]),
        "artifact": dict(observed),
        "unresolved_inputs": list(context["share"]["lock"]["unresolved_inputs"]),
    }


def _observed(context: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    try:
        inspected = context["owner"].inspect_portable_artifact(raw, spec=context["spec"])
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise ReconstructionError(f"Cleanroom owner refused fixture JAR content: {exc}") from exc
    if (type(inspected) is not dict or inspected.get("spec") != context["spec"]
            or inspected.get("sha256") != "sha256:" + sha256(raw).hexdigest()
            or inspected.get("size") != len(raw)
            or inspected.get("state") != "owner-content-validated"):
        raise ReconstructionError("fixture artifact owner returned another byte identity")
    return inspected


def plan_fixture_artifact_admission(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    execution_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review an existing output without copying it or changing an attempt."""

    args = _kwargs(
        workspace=workspace, fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        execution_result_resource_id=execution_result_resource_id,
        environment=environment,
    )
    context = _context(Path(suite_root), share, candidate, args)
    attempts, results = _rows(context)
    blockers: list[str] = []
    if attempts or results:
        blockers.append("fixture artifact has a prior Core admission attempt")
    target = context["target"]
    if attempts or results:
        # An existing prepared attempt owns the review.  The mutable output may
        # have changed or vanished since then; it cannot authorize a retry.
        observed = {"spec": context["spec"]}
    elif not target.exists() and not target.is_symlink():
        blockers.append("fixture artifact is absent at the profile-selected output path")
        observed = {"spec": context["spec"]}
    else:
        raw = _pinned_artifact(target)
        observed = _observed(context, raw)
    return _seal({**_outline(context, observed), "blockers": blockers,
                  "state": "blocked" if blockers else "ready", "scope": _SCOPE},
                 "workbench-environment-fixture-artifact-plan", "plan_id")


def _resource_row(service: Any, resource_id: str) -> dict[str, Any]:
    try:
        rows = service.catalog.inventory(workspace=service.workspace)["resources"]
        selected, = (row for row in rows if row["resource_id"] == resource_id)
        if selected["status"] in {"published-uncommitted", "committed-needs-reconcile"}:
            service.catalog.reconcile(resource_id)
            rows = service.catalog.inventory(workspace=service.workspace)["resources"]
            selected, = (row for row in rows if row["resource_id"] == resource_id)
    except (CatalogResourceError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture artifact resource cannot reopen: {exc}") from exc
    if selected["status"] != "committed":
        raise ReconstructionError("fixture artifact resource is incomplete")
    return selected


def _record(service: Any, resource_id: str, name: str) -> dict[str, Any]:
    row = _resource_row(service, resource_id)
    try:
        ref = service.describe(resource_id)
        raw = service.read_bytes(resource_id)
        value = json.loads(raw.decode("utf-8"))
    except (CatalogResourceError, OSError, ValueError, TypeError, UnicodeError) as exc:
        raise ReconstructionError(f"fixture artifact record cannot reopen: {exc}") from exc
    if (ref.owner_id != "workbench-core" or ref.role != "evidence"
            or not Path(row["path"]).name.endswith("-" + name)
            or type(value) is not dict or raw != _canonical(value) + b"\n"):
        raise ReconstructionError("fixture artifact record has another identity")
    return value


def _admitted(context: Mapping[str, Any], attempt_id: str, result_id: str) -> dict[str, Any]:
    service = context["service"]
    execution_id = context["execution"]["resource"]["resource_id"]
    attempt = _record(service, attempt_id, _ATTEMPT_NAME)
    result = _record(service, result_id, _RESULT_NAME)
    attempt_row = _resource_row(service, attempt_id)
    result_row = _resource_row(service, result_id)
    attempt_ref = service.describe(attempt_id)
    result_ref = service.describe(result_id)
    if (attempt.get("format") != ATTEMPT_FORMAT
            or attempt.get("schema_version") != 1
            or attempt.get("state") != "prepared"
            or attempt_ref.domain_id != context["share"]["share_id"]
            or attempt_row["references"] != [execution_id]
            or result.get("format") != RESULT_FORMAT
            or result.get("schema_version") != 1
            or result.get("attempt_resource_id") != attempt_id
            or result.get("execution_result_resource_id") != execution_id
            or result.get("artifact_path") != str(context["target"])):
        raise ReconstructionError("fixture artifact admission has another attempt or execution binding")
    artifact_id = result.get("artifact_resource_id")
    if type(artifact_id) is not str:
        raise ReconstructionError("fixture artifact admission has no immutable snapshot")
    artifact_row = _resource_row(service, artifact_id)
    try:
        artifact_ref = service.describe(artifact_id)
        raw = service.read_bytes(artifact_id)
    except (CatalogResourceError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture artifact snapshot cannot reopen: {exc}") from exc
    if (artifact_ref.owner_id != "workbench-core" or artifact_ref.role != "artifacts"
            or artifact_ref.domain_id != context["share"]["share_id"]
            or artifact_ref.path.name != artifact_row["path"].rsplit("/", 1)[-1]
            or not artifact_ref.path.name.endswith("-" + context["spec"]["filename"])
            or set(artifact_row["references"]) != {attempt_id, execution_id}
            or len(artifact_row["references"]) != 2
            or set(result_row["references"]) != {attempt_id, artifact_id, execution_id}
            or len(result_row["references"]) != 3):
        raise ReconstructionError("fixture artifact snapshot has another Core custody chain")
    observed = _observed(context, raw)
    expected_plan = _seal({**_outline(context, observed), "blockers": [],
                           "state": "ready", "scope": _SCOPE},
                          "workbench-environment-fixture-artifact-plan", "plan_id")
    if attempt.get("plan") != expected_plan:
        raise ReconstructionError("fixture artifact prepared plan differs from retained bytes")
    expected = {
        "format": RESULT_FORMAT, "schema_version": 1,
        "plan_id": expected_plan["plan_id"],
        "share_id": context["share"]["share_id"],
        "candidate_id": context["candidate"]["candidate_id"],
        "workspace": str(context["local"].workspace),
        "execution_result_resource_id": execution_id,
        "execution_capture_id": context["execution"]["capture"]["capture_id"],
        "attempt_resource_id": attempt_id,
        "artifact_resource_id": artifact_id,
        "artifact_path": str(context["target"]),
        "artifact": observed,
        "unresolved_inputs": list(context["share"]["lock"]["unresolved_inputs"]),
        "remaining_prerequisites": ["combined-environment-reconstruction"],
        "state": "owner-artifact-snapshot-admitted", "scope": _SCOPE,
    }
    if (result != expected or result_ref.domain_id != context["share"]["share_id"]):
        raise ReconstructionError("fixture artifact admission result differs from current exact bytes")
    return {**result, "resource": {
        "resource_id": result_id, "store_id": result_ref.store_id,
        "path": str(result_ref.path),
        "sha256": result_ref.sha256,
    }}


def admit_fixture_artifact(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    execution_result_resource_id: str, expected_plan_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain one prepared attempt, immutable JAR snapshot and linked result."""

    args = _kwargs(
        workspace=workspace, fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        execution_result_resource_id=execution_result_resource_id,
        environment=environment,
    )
    context = _context(Path(suite_root), share, candidate, args)
    local = context["local"]
    _private_directory(local.locations["evidence"])
    lock = local.locations["evidence"] / (
        ".fixture-artifact-" + sha256(execution_result_resource_id.encode()).hexdigest() + ".lock"
    )
    with private_record_lock(lock, wait=True):
        plan = plan_fixture_artifact_admission(suite_root, share, candidate, **args)
        if plan["plan_id"] != expected_plan_id or plan["state"] != "ready":
            raise ReconstructionError("fixture artifact changed or is blocked after review")
        service = context["service"]
        execution_id = context["execution"]["resource"]["resource_id"]
        prepared = {"format": ATTEMPT_FORMAT, "schema_version": 1,
                    "state": "prepared", "plan": plan}
        attempt_ref = service.publish_bytes(
            "evidence", _ATTEMPT_NAME, _canonical(prepared) + b"\n",
            domain_id=plan["share_id"], references=(execution_id,),
        )
        raw = _pinned_artifact(context["target"])
        observed = _observed(context, raw)
        if observed != plan["artifact"]:
            raise ReconstructionError("fixture artifact changed after its prepared attempt")
        artifact_ref = service.publish_bytes(
            "artifacts", context["spec"]["filename"], raw,
            domain_id=plan["share_id"], references=(attempt_ref.resource_id, execution_id),
        )
        retained = service.read_bytes(artifact_ref.resource_id)
        if retained != raw or _observed(context, retained) != observed:
            raise ReconstructionError("fixture artifact changed after Core snapshot publication")
        if _pinned_artifact(context["target"]) != raw:
            raise ReconstructionError("fixture artifact source changed during Core admission")
        result = {
            "format": RESULT_FORMAT, "schema_version": 1,
            "plan_id": plan["plan_id"],
            "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
            "workspace": plan["workspace"],
            "execution_result_resource_id": execution_id,
            "execution_capture_id": plan["execution_capture_id"],
            "attempt_resource_id": attempt_ref.resource_id,
            "artifact_resource_id": artifact_ref.resource_id,
            "artifact_path": plan["artifact_path"], "artifact": observed,
            "unresolved_inputs": plan["unresolved_inputs"],
            "remaining_prerequisites": ["combined-environment-reconstruction"],
            "state": "owner-artifact-snapshot-admitted", "scope": _SCOPE,
        }
        result_ref = service.publish_bytes(
            "evidence", _RESULT_NAME, _canonical(result) + b"\n",
            domain_id=plan["share_id"],
            references=(attempt_ref.resource_id, artifact_ref.resource_id, execution_id),
        )
        return _admitted(context, attempt_ref.resource_id, result_ref.resource_id)


def inspect_fixture_artifact_admission(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    execution_result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Classify preparation, interruption or complete immutable admission."""

    context = _context(Path(suite_root), share, candidate, _kwargs(
        workspace=workspace, fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        execution_result_resource_id=execution_result_resource_id,
        environment=environment,
    ))
    attempts, results = _rows(context)
    if not attempts and not results:
        return {"state": "not-started", "artifact_path": str(context["target"])}
    if not attempts or not results:
        return {"state": "unknown-after-preparation", "artifact_path": str(context["target"]),
                "attempt_resource_ids": [row["resource_id"] for row in attempts],
                "result_resource_ids": [row["resource_id"] for row in results]}
    return _admitted(context, attempts[0]["resource_id"], results[0]["resource_id"])


def reopen_fixture_artifact_admission(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, projection_result_resource_id: str,
    binding_result_resource_id: str, preflight_result_resource_id: str,
    execution_result_resource_id: str, admission_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    result = inspect_fixture_artifact_admission(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id,
        projection_result_resource_id=projection_result_resource_id,
        binding_result_resource_id=binding_result_resource_id,
        preflight_result_resource_id=preflight_result_resource_id,
        execution_result_resource_id=execution_result_resource_id,
        environment=environment,
    )
    if (result["state"] != "owner-artifact-snapshot-admitted"
            or result["resource"]["resource_id"] != admission_resource_id):
        raise ReconstructionError("fixture artifact has no matching Core admission")
    return result


__all__ = [
    "plan_fixture_artifact_admission", "admit_fixture_artifact",
    "inspect_fixture_artifact_admission", "reopen_fixture_artifact_admission",
]
