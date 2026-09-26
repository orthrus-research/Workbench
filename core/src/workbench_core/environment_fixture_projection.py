"""Project retained Cleanroom fixture source through Core reusable custody.

The imported owner tree stays immutable. This separate private projection may
later hold generated Gradle state under an active Core read lease. No fixture
command is run by this route.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from workbench_api.profile_extensions import (
    ProfileExtensionError, profile_extension_identity, require_profile_extension,
)
from workbench_api.reusable_projections import ReusableProjectionError, ReusableProjectionReference

from .environment_fixture_execution_policy import reopen_fixture_execution_policy
from .environment_fixture_gradle_import import _qualified_filesystem
from .environment_fixture_import import _read_source, reopen_fixture_import
from .environment_input_candidates import validate_input_candidate
from .environment_reconstruction import (
    ReconstructionError, SHARE_FORMAT_V3, _canonical, _resource_host, _seal,
    validate_share,
)
from .environment_resolution import resolve_environment
from .environment_wheel_import import _tree_host_supported
from .reusable_projections import CoreReusableProjections
from .storage.registered import DurableResourceError


PLAN_FORMAT = "workbench-environment-fixture-projection-plan-v1"
RESULT_FORMAT = "workbench-environment-fixture-projection-result-v1"
_PROJECT = Path("profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop")
_LOCK = "fixture-lock-v1.json"
_SCOPE = (
    "Exact retained fixture source projection only; Java and Gradle execution, "
    "artifact admission and reconstruction remain separate."
)


def _source_rows(receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected = []
    for row in receipt["files"]:
        if row["kind"] not in {"fixture-source", "fixture-owner-lock"}:
            continue
        try:
            relative = PurePosixPath(row["relative_path"]).relative_to(
                PurePosixPath(_PROJECT.as_posix()),
            )
        except ValueError as exc:
            raise ReconstructionError("retained fixture source is outside its project") from exc
        if str(relative) == ".":
            raise ReconstructionError("retained fixture source is a directory")
        selected.append({"path": relative.as_posix(), "sha256": row["sha256"],
                         "size": row["size"]})
    if (not selected or len({row["path"] for row in selected}) != len(selected)
            or [row["path"] for row in selected if row["path"] == _LOCK] != [_LOCK]):
        raise ReconstructionError("retained fixture projection has no exact owner lock")
    return sorted(selected, key=lambda row: row["path"].encode("utf-8"))


def _rules(candidate: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, list[str]]:
    fixture = candidate["profile_fixture"]
    try:
        identity = profile_extension_identity("workbench.workspace_home_fixtures", "cleanroom")
        if identity != fixture["owner_code"]:
            raise ReconstructionError("installed Cleanroom owner code differs from the candidate")
        owner = require_profile_extension("workbench.workspace_home_fixtures", "cleanroom")
        rules = owner.portable_projection_rules(policy=policy)
    except (ProfileExtensionError, OSError, ValueError, RuntimeError,
            AttributeError, KeyError, TypeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"Cleanroom projection rules are unavailable: {exc}") from exc
    if (type(rules) is not dict or set(rules) != {
            "generated_parts", "generated_suffixes", "generated_roots",
        } or any(type(rules[key]) is not list or any(type(value) is not str
                   for value in rules[key]) for key in rules)):
        raise ReconstructionError("Cleanroom owner returned invalid projection rules")
    if rules["generated_roots"] != [
        policy["paths"]["generated_root_relative_to_projection_digest"],
    ]:
        raise ReconstructionError("Cleanroom owner returned another generated root")
    return rules


def _project_source(project: Path, rows: list[dict[str, Any]], fixture: Mapping[str, Any]) -> None:
    """Owner lock identity check; CoreReusableProjections scans all members."""

    lock = next(row for row in rows if row["path"] == _LOCK)
    raw = _read_source(project / _LOCK, limit=2 * 1024 * 1024)
    if len(raw) != lock["size"] or "sha256:" + sha256(raw).hexdigest() != lock["sha256"]:
        raise ReconstructionError("projected fixture owner lock differs from retained input")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ReconstructionError("projected fixture owner lock is invalid") from exc
    if (type(value) is not dict or value.get("declaration_id") != fixture["declaration_id"]
            or value.get("declared_values", {}).get("tree_digest") != fixture["tree_digest"]):
        raise ReconstructionError("projected fixture owner lock has another identity")


def _host(local: Any) -> CoreReusableProjections:
    return CoreReusableProjections(
        workspace=local.workspace, configuration_home=local.configuration_home,
        owner_id="workbench-core",
    )


def plan_fixture_projection(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Review one exact source-only projection without publishing it."""

    portable = validate_share(dict(share))
    if portable["format"] != SHARE_FORMAT_V3:
        raise ReconstructionError("fixture projection requires a V3 share")
    reviewed = validate_input_candidate(portable, deepcopy(dict(candidate)))
    values = dict(os.environ if environment is None else environment)
    local = resolve_environment(suite_root, workspace=workspace, environment=values)
    review = reopen_fixture_execution_policy(
        suite_root, portable, reviewed, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, environment=values,
    )
    imported = reopen_fixture_import(
        suite_root, portable, reviewed, workspace=local.workspace,
        result_resource_id=fixture_result_resource_id, environment=values,
    )
    fixture = reviewed["profile_fixture"]
    rules = _rules(reviewed, review["policy"])
    path_template = review["policy"]["paths"]["project_relative_to_state"]
    target_project = local.state_root / path_template.replace(
        "{fixture_digest_hex}", fixture["tree_digest"].removeprefix("sha256:"),
    )
    target = local.state_root / "source-projections/cleanroom" / fixture[
        "tree_digest"
    ].removeprefix("sha256:")
    if target_project != target / _PROJECT:
        raise ReconstructionError("fixture projection path differs from retained policy")
    blockers = []
    if not _tree_host_supported() or not _qualified_filesystem(target):
        blockers.append("exact Linux/WSL projection is unavailable on this filesystem")
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        blockers.append("fixture projection target has a foreign occupant")
    return _seal({
        "format": PLAN_FORMAT, "schema_version": 1,
        "share_id": portable["share_id"], "candidate_id": reviewed["candidate_id"],
        "workspace": str(local.workspace),
        "environment_resolution_id": local.record["resolution_id"],
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_tree_id": imported["tree_id"],
        "fixture_tree_content_sha256": imported["tree_content_sha256"],
        "fixture_policy_review_id": expected_review_id,
        "declaration_id": fixture["declaration_id"],
        "fixture_tree_digest": fixture["tree_digest"],
        "source_root": str(Path(imported["tree_path"]) / _PROJECT),
        "target": str(target), "project": str(target_project),
        "source_files": _source_rows(imported), "rules": rules,
        "unresolved_inputs": list(portable["lock"]["unresolved_inputs"]),
        "blockers": blockers, "state": "blocked" if blockers else "ready",
    }, "workbench-environment-fixture-projection-plan", "plan_id")


def _projection_binding(
    host: CoreReusableProjections, projection_id: str, plan: Mapping[str, Any],
) -> ReusableProjectionReference:
    try:
        record = host._record(projection_id)
        if (record["path"] != plan["target"] or record["source_digest"] != plan["fixture_tree_digest"]
                or record["project_relative"] != _PROJECT.as_posix()
                or record["source_files"] != plan["source_files"]
                or record["generated_parts"] != plan["rules"]["generated_parts"]
                or record["generated_suffixes"] != plan["rules"]["generated_suffixes"]
                or record["generated_roots"] != plan["rules"]["generated_roots"]):
            raise ReconstructionError("fixture projection record has another source binding")
        with host.open(
            projection_id,
            validate=lambda project: _project_source(
                project, plan["source_files"],
                {"declaration_id": plan["declaration_id"],
                 "tree_digest": plan["fixture_tree_digest"]},
            ),
        ) as opened:
            if opened.path != Path(plan["target"]) or opened.project != Path(plan["project"]):
                raise ReconstructionError("fixture projection reopened at another path")
            return opened
    except (ReusableProjectionError, OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ReconstructionError):
            raise
        raise ReconstructionError(f"fixture projection cannot reopen: {exc}") from exc


def apply_fixture_projection(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, expected_plan_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish or recover a private source projection and retain Core evidence."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_projection(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, environment=values,
    )
    if type(expected_plan_id) is not str or plan["plan_id"] != expected_plan_id:
        raise ReconstructionError("fixture projection changed after review")
    if plan["state"] != "ready":
        raise ReconstructionError("fixture projection is blocked: " + "; ".join(plan["blockers"]))
    local = resolve_environment(suite_root, workspace=Path(plan["workspace"]), environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    if service.policy_id != plan["environment_resolution_id"]:
        raise ReconstructionError("fixture projection resolution changed after review")
    prepared = {
        "format": "workbench-environment-fixture-projection-attempt-v1",
        "schema_version": 1, "state": "prepared", "plan_id": plan["plan_id"],
        "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
        "target": plan["target"],
    }
    attempt = service.publish_bytes(
        "evidence", "environment-fixture-projection-attempt.json",
        _canonical(prepared) + b"\n", domain_id=plan["share_id"],
        references=(fixture_result_resource_id,),
    )
    host = _host(local)
    try:
        reference = host.ensure(
            "cleanroom", Path(plan["target"]),
            source_root=Path(plan["source_root"]),
            source_digest=plan["fixture_tree_digest"], project_relative=_PROJECT,
            source_files=tuple(plan["source_files"]),
            generated_parts=tuple(plan["rules"]["generated_parts"]),
            generated_suffixes=tuple(plan["rules"]["generated_suffixes"]),
            generated_roots=tuple(Path(value) for value in plan["rules"]["generated_roots"]),
            validate=lambda project: _project_source(
                project, plan["source_files"],
                {"declaration_id": plan["declaration_id"],
                 "tree_digest": plan["fixture_tree_digest"]},
            ),
        )
    except (ReusableProjectionError, OSError, ValueError) as exc:
        raise ReconstructionError(f"fixture projection cannot be published: {exc}") from exc
    # Retained input and projected source are independently checked after copy.
    if plan_fixture_projection(
        suite_root, share, candidate, workspace=local.workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, environment=values,
    ) != plan:
        raise ReconstructionError("retained fixture input changed during projection")
    _projection_binding(host, reference.projection_id, plan)
    result = {
        "format": RESULT_FORMAT, "schema_version": 1,
        "plan_id": plan["plan_id"], "share_id": plan["share_id"],
        "candidate_id": plan["candidate_id"], "workspace": plan["workspace"],
        "environment_resolution_id": plan["environment_resolution_id"],
        "fixture_result_resource_id": fixture_result_resource_id,
        "fixture_tree_id": plan["fixture_tree_id"],
        "fixture_tree_content_sha256": plan["fixture_tree_content_sha256"],
        "fixture_policy_review_id": expected_review_id,
        "projection_id": reference.projection_id,
        "projection_root": str(reference.path), "project": str(reference.project),
        "fixture_tree_digest": plan["fixture_tree_digest"],
        "attempt_resource_id": attempt.resource_id,
        "unresolved_inputs": plan["unresolved_inputs"],
        "state": "source-projected-unexecuted", "scope": _SCOPE,
    }
    payload = _canonical(result) + b"\n"
    completed = service.publish_bytes(
        "evidence", "environment-fixture-projection.json", payload,
        domain_id=plan["share_id"], references=(attempt.resource_id,),
    )
    if service.read_bytes(completed.resource_id) != payload:
        raise ReconstructionError("fixture projection result did not reopen exactly")
    return {**result, "resource": {
        "resource_id": completed.resource_id, "store_id": completed.store_id,
        "path": str(completed.path), "sha256": completed.sha256,
    }}


def reopen_fixture_projection(
    suite_root: Path, share: Mapping[str, Any], candidate: Mapping[str, Any], *,
    workspace: Path | str, fixture_result_resource_id: str,
    expected_review_id: str, result_resource_id: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Reopen exact projected source and prepared/result evidence after restart."""

    values = dict(os.environ if environment is None else environment)
    plan = plan_fixture_projection(
        suite_root, share, candidate, workspace=workspace,
        fixture_result_resource_id=fixture_result_resource_id,
        expected_review_id=expected_review_id, environment=values,
    )
    if plan["state"] != "ready":
        raise ReconstructionError("fixture projection no longer has a qualified host")
    local = resolve_environment(suite_root, workspace=Path(plan["workspace"]), environment=values)
    service = _resource_host(Path(suite_root), local.workspace, values)
    try:
        reference = service.describe(result_resource_id)
        payload = service.read_bytes(result_resource_id)
        result = json.loads(payload.decode("utf-8"))
        attempt_ref = service.describe(result["attempt_resource_id"])
        attempt_raw = service.read_bytes(result["attempt_resource_id"])
        attempt = json.loads(attempt_raw.decode("utf-8"))
    except (DurableResourceError, OSError, ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise ReconstructionError(f"fixture projection evidence cannot reopen: {exc}") from exc
    prepared = {
        "format": "workbench-environment-fixture-projection-attempt-v1",
        "schema_version": 1, "state": "prepared", "plan_id": plan["plan_id"],
        "share_id": plan["share_id"], "candidate_id": plan["candidate_id"],
        "target": plan["target"],
    }
    if (reference.owner_id != "workbench-core" or reference.role != "evidence"
            or reference.domain_id != plan["share_id"]
            or attempt_ref.owner_id != "workbench-core" or attempt_ref.role != "evidence"
            or attempt_ref.domain_id != plan["share_id"]
            or type(result) is not dict or payload != _canonical(result) + b"\n"
            or attempt != prepared or attempt_raw != _canonical(prepared) + b"\n"
            or result != {
                "format": RESULT_FORMAT, "schema_version": 1,
                "plan_id": plan["plan_id"], "share_id": plan["share_id"],
                "candidate_id": plan["candidate_id"], "workspace": plan["workspace"],
                "environment_resolution_id": plan["environment_resolution_id"],
                "fixture_result_resource_id": fixture_result_resource_id,
                "fixture_tree_id": plan["fixture_tree_id"],
                "fixture_tree_content_sha256": plan["fixture_tree_content_sha256"],
                "fixture_policy_review_id": expected_review_id,
                "projection_id": result.get("projection_id"),
                "projection_root": plan["target"], "project": plan["project"],
                "fixture_tree_digest": plan["fixture_tree_digest"],
                "attempt_resource_id": result.get("attempt_resource_id"),
                "unresolved_inputs": plan["unresolved_inputs"],
                "state": "source-projected-unexecuted", "scope": _SCOPE,
            }):
        raise ReconstructionError("fixture projection result has another identity or scope")
    _projection_binding(_host(local), result["projection_id"], plan)
    return result


__all__ = ["plan_fixture_projection", "apply_fixture_projection", "reopen_fixture_projection"]
