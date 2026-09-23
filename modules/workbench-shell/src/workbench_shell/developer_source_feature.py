"""Transactional developer flows for bounded Supersymmetry source edits.

Pack-owned renderers decide what may be constructed.  This module turns their
exact replacement bytes into reviewable, retained plans and delegates every
mutation, rollback, and crash recovery to the generic Blueprints transaction.
"""

from __future__ import annotations

from workbench_api.resources import repository_root as _repository_resource_root

import base64
from hashlib import sha256
from workbench_blueprints.profile_construction import quest_change_authority
from pathlib import Path
import re
from typing import Any, Callable, Mapping, NoReturn, cast

from .bootstrap import inspect_project
from .developer_feature import (
    DeveloperFeatureError,
    _canonical_bytes,
    _content_id,
    _decoded,
    _require_context,
    _review,
    _read_workspace_file,
    _safe_relative,
    _seal,
    _transaction_operation,
    _unified_diff,
    _workspace_path_from_uri,
    _workspace_from_uri,
)


PLAN_FORMAT = "workbench-developer-source-feature-plan-v1"
RECEIPT_FORMAT = "workbench-developer-source-feature-receipt-v1"
ROLLBACK_FORMAT = "workbench-developer-source-feature-rollback-v1"
RECOVERY_FORMAT = "workbench-developer-source-feature-recovery-v1"
PLAN_KIND = "workbench-developer-source-feature-plan"
RECEIPT_KIND = "workbench-developer-source-feature-receipt"
ROLLBACK_KIND = "workbench-developer-source-feature-rollback"
RECOVERY_KIND = "workbench-developer-source-feature-recovery"
QUEST_FAMILY = "quest-for-process"
QUEST_OPTIONS_FORMAT = "workbench-supersymmetry-quest-for-process-options-v1"
AUTHORITY_BOUNDARY = {
    "application_scope": "reversible-local-experiment",
    "atlas_runtime_verified": False,
    "profile_tested_support": False,
    "release_or_publication_authorized": False,
    "transaction_owner": "blueprints",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
QUEST_LANGUAGE_PATH = (
    "config/betterquesting/resources/supersymmetry/lang/en_us.lang"
)


def _fail(message: str) -> NoReturn:
    raise DeveloperFeatureError(message)


def _transaction_authority() -> Any:
    try:
        from workbench_blueprints import application_transaction
    except ImportError as exc:  # pragma: no cover - packaging regression
        raise DeveloperFeatureError(
            f"Blueprints transaction authority is unavailable: {exc}"
        ) from exc
    return application_transaction


def _source_dependency(
    workspace: Path,
    path: str,
    role: str,
) -> dict[str, Any]:
    relative = _safe_relative(path, "source dependency path")
    raw = _read_workspace_file(
        workspace,
        relative,
        f"source dependency {relative}",
    )
    return {
        "base64": base64.b64encode(raw).decode("ascii"),
        "path": relative.as_posix(),
        "role": role,
        "sha256": sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _decoded_source_dependency(value: Mapping[str, Any]) -> bytes:
    if type(value) is not dict or set(value) != {
        "base64",
        "path",
        "role",
        "sha256",
        "size",
    }:
        _fail("source dependency fields changed")
    try:
        raw = base64.b64decode(value.get("base64"), validate=True)
    except (TypeError, ValueError) as exc:
        raise DeveloperFeatureError("source dependency bytes are invalid") from exc
    if (
        not 1 <= len(raw) <= 4 * 1024 * 1024
        or type(value.get("size")) is not int
        or type(value.get("size")) is bool
        or value.get("size") != len(raw)
        or value.get("sha256") != sha256(raw).hexdigest()
    ):
        _fail("source dependency byte identity changed")
    return raw


def _quest_request(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "add_prerequisite_id",
        "description",
        "quest_id",
        "requirement_type",
        "title",
    }
    if type(value) is not dict or set(value) != expected:
        _fail("quest-for-process request fields changed")
    request = dict(value)
    if (
        type(request.get("quest_id")) is not int
        or type(request.get("quest_id")) is bool
        or not 0 <= request["quest_id"] <= 2_147_483_647
    ):
        _fail("quest-for-process quest identity changed")
    prerequisite = request.get("add_prerequisite_id")
    if prerequisite is not None and (
        type(prerequisite) is not int
        or type(prerequisite) is bool
        or not 0 <= prerequisite <= 2_147_483_647
    ):
        _fail("quest-for-process prerequisite identity changed")
    requirement = request.get("requirement_type")
    if (prerequisite is None and requirement is not None) or (
        prerequisite is not None
        and requirement not in {"NORMAL", "IMPLICIT", "HIDDEN"}
    ):
        _fail("quest-for-process prerequisite type changed")
    for field in ("title", "description"):
        text = request.get(field)
        if text is not None and (
            type(text) is not str
            or not text
            or any(character in text for character in "\r\n")
        ):
            _fail(f"quest-for-process {field} changed")
    if prerequisite is None and request["title"] is None and request["description"] is None:
        _fail("quest-for-process request is empty")
    return request


def _source_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "localization",
        "prerequisite_graph",
        "quest_path",
        "quest_source_sha256",
        "source_checks",
    }:
        _fail("quest-for-process source evidence changed")
    evidence = dict(value)
    graph = evidence.get("prerequisite_graph")
    localization = evidence.get("localization")
    checks = evidence.get("source_checks")
    if (
        type(graph) is not dict
        or set(graph) != {"file_count", "sha256"}
        or type(graph.get("file_count")) is not int
        or type(graph.get("file_count")) is bool
        or not 1 <= graph["file_count"] <= 4096
        or type(graph.get("sha256")) is not str
        or _DIGEST.fullmatch(graph["sha256"]) is None
        or type(evidence.get("quest_source_sha256")) is not str
        or _DIGEST.fullmatch(evidence["quest_source_sha256"]) is None
        or type(evidence.get("quest_path")) is not str
        or not evidence["quest_path"].startswith(
            "config/betterquesting/DefaultQuests/Quests/"
        )
        or not evidence["quest_path"].endswith(".json")
    ):
        _fail("quest-for-process graph binding changed")
    if (
        type(localization) is not dict
        or set(localization)
        != {
            "description_before",
            "description_key",
            "title_before",
            "title_key",
        }
        or any(type(item) is not str for item in localization.values())
        or type(checks) is not dict
        or set(checks)
        != {
            "localization_keys_unique",
            "new_edge_creates_cycle",
            "prerequisite_exists",
            "quest_exists",
        }
        or checks
        != {
            "localization_keys_unique": True,
            "new_edge_creates_cycle": False,
            "prerequisite_exists": True,
            "quest_exists": True,
        }
    ):
        _fail("quest-for-process source checks changed")
    return evidence


def quest_for_process_options(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    query: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Open the profile-owned, read-only quest selection surface."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    _require_context(inspect_project(suite, workspace))
    owner = quest_change_authority("supersymmetry")
    discover = getattr(owner, "discover_quest_for_process_options", None)
    if not callable(discover):
        _fail("Supersymmetry quest Blueprint lacks owner discovery")
    try:
        result = discover(workspace, query=query, limit=limit)
    except (OSError, ValueError) as exc:
        raise DeveloperFeatureError(
            f"cannot discover quest-for-process owners: {exc}"
        ) from exc
    expected_fields = {
        "authority_boundary",
        "format",
        "limit",
        "matched_count",
        "options",
        "query",
        "returned_count",
        "schema_version",
        "source_binding",
        "state",
        "total_count",
        "truncated",
        "workspace_uri",
    }
    if (
        type(result) is not dict
        or set(result) != expected_fields
        or result.get("format") != QUEST_OPTIONS_FORMAT
        or type(result.get("schema_version")) is not int
        or result.get("schema_version") != 1
        or result.get("state") != "available"
        or result.get("workspace_uri") != workspace.as_uri()
        or result.get("query") != query
        or result.get("limit") != limit
    ):
        _fail("Supersymmetry quest owner discovery identity changed")
    boundary = result.get("authority_boundary")
    if boundary != {
        "construction_authority": False,
        "mutation_authorized": False,
        "profile_owner": "supersymmetry-quest-for-process",
        "runtime_verified": False,
    }:
        _fail("Supersymmetry quest owner discovery authority changed")
    source_binding = result.get("source_binding")
    total = result.get("total_count")
    matched = result.get("matched_count")
    returned = result.get("returned_count")
    options = result.get("options")
    if (
        type(limit) is not int
        or type(limit) is bool
        or not 1 <= limit <= 200
        or type(total) is not int
        or type(total) is bool
        or not 1 <= total <= 4096
        or type(matched) is not int
        or type(matched) is bool
        or not 0 <= matched <= total
        or type(returned) is not int
        or type(returned) is bool
        or returned != min(limit, matched)
        or type(options) is not list
        or len(options) != returned
        or result.get("truncated") is not (returned < matched)
        or type(source_binding) is not dict
        or set(source_binding) != {"file_count", "sha256"}
        or source_binding.get("file_count") != total
        or type(source_binding.get("sha256")) is not str
        or _DIGEST.fullmatch(source_binding["sha256"]) is None
    ):
        _fail("Supersymmetry quest owner discovery counts changed")
    observed_ids: set[int] = set()
    for option in options:
        if type(option) is not dict or set(option) != {
            "current_prerequisites",
            "localization_state",
            "localized_title",
            "path",
            "quest_id",
            "title_key",
        }:
            _fail("Supersymmetry quest owner option fields changed")
        quest_id = option.get("quest_id")
        localized_title = option.get("localized_title")
        if (
            type(quest_id) is not int
            or type(quest_id) is bool
            or not 0 <= quest_id <= 2_147_483_647
            or quest_id in observed_ids
            or option.get("localization_state")
            not in {"resolved", "missing", "ambiguous"}
            or (
                localized_title is not None
                and (type(localized_title) is not str or not localized_title)
            )
            or (option["localization_state"] == "resolved")
            is not (localized_title is not None)
            or type(option.get("title_key")) is not str
            or not option["title_key"]
            or type(option.get("path")) is not str
            or _safe_relative(option["path"], "quest owner option path").stem
            != str(quest_id)
        ):
            _fail("Supersymmetry quest owner option identity changed")
        prerequisites = option.get("current_prerequisites")
        if type(prerequisites) is not list:
            _fail("Supersymmetry quest owner prerequisites changed")
        prerequisite_ids: set[int] = set()
        for prerequisite in prerequisites:
            if (
                type(prerequisite) is not dict
                or set(prerequisite) != {"quest_id", "requirement_type"}
                or type(prerequisite.get("quest_id")) is not int
                or type(prerequisite.get("quest_id")) is bool
                or not 0 <= prerequisite["quest_id"] <= 2_147_483_647
                or prerequisite["quest_id"] in prerequisite_ids
                or prerequisite.get("requirement_type")
                not in {"NORMAL", "IMPLICIT", "HIDDEN"}
            ):
                _fail("Supersymmetry quest owner prerequisites changed")
            prerequisite_ids.add(prerequisite["quest_id"])
        observed_ids.add(quest_id)
    if [row["quest_id"] for row in options] != sorted(observed_ids):
        _fail("Supersymmetry quest owner option order changed")
    return cast(dict[str, Any], result)


def build_quest_for_process_plan(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    quest_id: int,
    add_prerequisite_id: int | None = None,
    requirement_type: str = "IMPLICIT",
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Build one exact existing-quest edit plan without mutating the checkout."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    _require_context(inspect_project(suite, workspace))
    owner = quest_change_authority("supersymmetry")
    try:
        rendered = owner.render_quest_for_process_update(
            workspace,
            quest_id=quest_id,
            add_prerequisite_id=add_prerequisite_id,
            requirement_type=requirement_type,
            title=title,
            description=description,
        )
    except (OSError, ValueError) as exc:
        raise DeveloperFeatureError(
            f"cannot construct quest-for-process: {exc}"
        ) from exc
    if (
        type(rendered) is not dict
        or rendered.get("format")
        != "workbench-supersymmetry-quest-for-process-render-v1"
        or rendered.get("schema_version") != 1
        or rendered.get("state") != "source-ready-runtime-unverified"
    ):
        _fail("Supersymmetry quest Blueprint result changed")
    raw_operations = rendered.get("operations")
    if type(raw_operations) is not list or not 1 <= len(raw_operations) <= 2:
        _fail("quest-for-process must update one quest and optional localization")
    roles = [row.get("role") for row in raw_operations if type(row) is dict]
    if roles not in (["quest-definition"], ["quest-localization"], ["quest-definition", "quest-localization"]):
        _fail("quest-for-process owner roles changed")
    operations = [
        _transaction_operation(
            workspace,
            operation,
            ordinal=ordinal,
            role=operation["role"],
        )
        for ordinal, operation in enumerate(raw_operations)
    ]
    evidence = dict(rendered.get("evidence", {}))
    operation_paths = {row["path"] for row in operations}
    semantic_sources = (
        (evidence.get("quest_path"), "quest-definition-source"),
        (QUEST_LANGUAGE_PATH, "quest-localization-source"),
    )
    source_dependencies = [
        _source_dependency(workspace, path, role)
        for path, role in semantic_sources
        if type(path) is str and path not in operation_paths
    ]
    body = {
        "action": "apply-experimental-reversible-edit",
        "authority_boundary": dict(AUTHORITY_BOUNDARY),
        "family": QUEST_FAMILY,
        "format": PLAN_FORMAT,
        "kind": PLAN_KIND,
        "limitations": list(rendered.get("limitations", [])),
        "operations": operations,
        "request": dict(rendered.get("request", {})),
        "review": _review(operations),
        "schema_version": 1,
        "source_dependencies": source_dependencies,
        "source_evidence": evidence,
        "state": "experimental-ready-runtime-unverified",
        "workspace_uri": workspace.as_uri(),
    }
    return validate_source_feature_plan(
        _seal(PLAN_KIND, body),
        suite_root=suite,
    )


def validate_source_feature_plan(
    value: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("source feature plan must be one ordinary object")
    plan = dict(value)
    body = dict(plan)
    supplied = body.pop("id", None)
    if (
        set(plan)
        != {
            "action",
            "authority_boundary",
            "family",
            "format",
            "id",
            "kind",
            "limitations",
            "operations",
            "request",
            "review",
            "schema_version",
            "source_dependencies",
            "source_evidence",
            "state",
            "workspace_uri",
        }
        or plan.get("format") != PLAN_FORMAT
        or plan.get("kind") != PLAN_KIND
        or type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != 1
        or plan.get("family") != QUEST_FAMILY
        or plan.get("action") != "apply-experimental-reversible-edit"
        or plan.get("state") != "experimental-ready-runtime-unverified"
        or _canonical_bytes(plan.get("authority_boundary"))
        != _canonical_bytes(AUTHORITY_BOUNDARY)
        or supplied != _content_id(PLAN_KIND, body)
    ):
        _fail("source feature plan identity or authority boundary changed")
    _workspace_path_from_uri(plan.get("workspace_uri"))
    request = _quest_request(plan.get("request"))
    evidence = _source_evidence(plan.get("source_evidence"))
    expected_limitations = [
        "Source structure does not prove BetterQuesting runtime load or player detection.",
        "Pack-default quest bytes do not rewrite quest state already retained in a world.",
        "New-edge checks do not certify pre-existing quest graph health; inspect it through Atlas.",
        "Objective reachability requires a compatible Atlas runtime graph.",
    ]
    if plan.get("limitations") != expected_limitations:
        _fail("quest-for-process limitations changed")
    operations = plan.get("operations")
    if type(operations) is not list or not 1 <= len(operations) <= 2:
        _fail("source feature operation count changed")
    if [row.get("ordinal") for row in operations if type(row) is dict] != list(
        range(len(operations))
    ):
        _fail("source feature operation order changed")
    roles = [row.get("role") for row in operations if type(row) is dict]
    expected_roles: list[str] = []
    if request["add_prerequisite_id"] is not None:
        expected_roles.append("quest-definition")
    localization = evidence["localization"]
    if (
        request["title"] is not None
        and request["title"] != localization["title_before"]
    ) or (
        request["description"] is not None
        and request["description"] != localization["description_before"]
    ):
        expected_roles.append("quest-localization")
    if roles != expected_roles:
        _fail("source feature operation roles changed")
    raw_operations: list[dict[str, Any]] = []
    before_bytes: dict[str, bytes] = {}
    for row in operations:
        if type(row) is not dict or set(row) != {
            "after_base64",
            "after_sha256",
            "after_size",
            "before_base64",
            "before_sha256",
            "before_size",
            "diff",
            "operation",
            "ordinal",
            "outcome",
            "path",
            "role",
        }:
            _fail("source feature operation fields changed")
        before = _decoded(row, "before")
        after = _decoded(row, "after")
        relative = _safe_relative(row.get("path"), "source feature operation path")
        expected_diff = _unified_diff(before, after, relative.as_posix())
        if (
            row.get("operation") != "update"
            or row.get("outcome") != "approved-update"
            or before == after
            or row.get("diff") != expected_diff
        ):
            _fail("source feature operation identity changed")
        if relative.as_posix() in before_bytes:
            _fail("source feature operation targets are duplicated")
        before_bytes[relative.as_posix()] = before
        raw_operations.append(
            {
                "before_sha256": row["before_sha256"],
                "before_size": row["before_size"],
                "content": after,
                "content_sha256": row["after_sha256"],
                "content_size": row["after_size"],
                "operation": row["operation"],
                "path": relative.as_posix(),
                "role": row["role"],
            }
        )
    if roles[0] == "quest-definition" and operations[0]["path"] != evidence["quest_path"]:
        _fail("quest source evidence and operation differ")
    if "quest-localization" in roles:
        localization_operation = operations[roles.index("quest-localization")]
        if localization_operation["path"] != QUEST_LANGUAGE_PATH:
            _fail("quest localization owner changed")
    expected_semantic_sources = [
        (evidence["quest_path"], "quest-definition-source"),
        (QUEST_LANGUAGE_PATH, "quest-localization-source"),
    ]
    dependencies = plan.get("source_dependencies")
    if type(dependencies) is not list or len(dependencies) > 2:
        _fail("source feature dependency count changed")
    expected_dependencies = [
        (path, role)
        for path, role in expected_semantic_sources
        if path not in before_bytes
    ]
    if [
        (row.get("path"), row.get("role"))
        for row in dependencies
        if type(row) is dict
    ] != expected_dependencies:
        _fail("source feature dependency ownership changed")
    for dependency in dependencies:
        relative = _safe_relative(
            dependency.get("path"), "source dependency path"
        )
        if relative.as_posix() in before_bytes:
            _fail("source feature source snapshots are duplicated")
        before_bytes[relative.as_posix()] = _decoded_source_dependency(dependency)

    owner = quest_change_authority("supersymmetry")
    validator = getattr(owner, "validate_quest_for_process_render", None)
    if not callable(validator):
        _fail("Supersymmetry quest Blueprint lacks its semantic validator")
    profile_render = {
        "evidence": evidence,
        "format": "workbench-supersymmetry-quest-for-process-render-v1",
        "limitations": plan["limitations"],
        "operations": raw_operations,
        "request": request,
        "schema_version": 1,
        "state": "source-ready-runtime-unverified",
    }
    try:
        validator(
            profile_render,
            before_bytes=before_bytes,
        )
    except (OSError, ValueError) as exc:
        raise DeveloperFeatureError(
            f"source feature plan violates its profile Blueprint: {exc}"
        ) from exc
    if plan.get("review") != _review(cast(list[dict[str, Any]], operations)):
        _fail("source feature review differs from its exact operations")
    return cast(dict[str, Any], plan)


def verify_source_feature_plan(
    suite_root: Path | str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    value = validate_source_feature_plan(plan, suite_root=suite_root)
    request = value["request"]
    try:
        fresh = build_quest_for_process_plan(
            suite_root,
            _workspace_from_uri(value["workspace_uri"]),
            quest_id=request["quest_id"],
            add_prerequisite_id=request["add_prerequisite_id"],
            requirement_type=request["requirement_type"] or "IMPLICIT",
            title=request["title"],
            description=request["description"],
        )
    except (OSError, ValueError) as exc:
        return {
            "format": "workbench-developer-source-feature-verification-v1",
            "schema_version": 1,
            "plan_id": value["id"],
            "state": "stale",
            "reason": str(exc),
        }
    matches = fresh["id"] == value["id"]
    return {
        "format": "workbench-developer-source-feature-verification-v1",
        "schema_version": 1,
        "plan_id": value["id"],
        "state": "ready" if matches else "stale",
        "reason": None if matches else "plan regenerated differently",
    }


def validate_source_feature_receipt(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    reviewed = validate_source_feature_plan(plan, suite_root=suite_root)
    if type(value) is not dict:
        _fail("source feature receipt must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        receipt.get("format") != RECEIPT_FORMAT
        or receipt.get("kind") != RECEIPT_KIND
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("authority_boundary") != AUTHORITY_BOUNDARY
        or receipt.get("state") not in {"applied", "rejected"}
        or supplied != _content_id(RECEIPT_KIND, body)
    ):
        _fail("source feature receipt identity changed")
    transaction = _transaction_authority()
    if receipt["state"] == "applied":
        try:
            transaction.validate_applied_application_receipt(
                receipt,
                reviewed,
                receipt_format=RECEIPT_FORMAT,
                receipt_kind=RECEIPT_KIND,
                receipt_content_kind=RECEIPT_KIND,
                success_mutation_state="applied-experimental-local-edit",
            )
        except ValueError as exc:
            raise DeveloperFeatureError("source feature success receipt is invalid") from exc
        return receipt
    diagnostic = receipt.get("diagnostic_code")
    simple = {
        "BLUEPRINTS_M2_STALE_PLAN",
        "BLUEPRINTS_M2_TRANSACTION_LOCKED",
    }
    partial = {
        "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
        "BLUEPRINTS_M2_ROLLBACK_REQUIRES_REVIEW",
    }
    base_fields = {
        "authority_boundary",
        "diagnostic_code",
        "format",
        "id",
        "kind",
        "mutation_state",
        "plan_id",
        "rollback",
        "schema_version",
        "state",
    }
    if diagnostic in simple:
        if (
            set(receipt) != base_fields
            or receipt.get("mutation_state") != "not-started"
            or receipt.get("rollback") != "not-needed"
        ):
            _fail("source feature preflight rejection fields changed")
    elif diagnostic in partial:
        expected_history = sorted(
            (
                {
                    "sha256": operation[f"{prefix}_sha256"],
                    "size": operation[f"{prefix}_size"],
                }
                for operation in reviewed["operations"]
                for prefix in ("before", "after")
            ),
            key=lambda row: (row["sha256"], row["size"]),
        )
        succeeded = diagnostic == "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
        if (
            set(receipt) != base_fields | {"failure_kind", "history_objects"}
            or type(receipt.get("failure_kind")) is not str
            or receipt.get("history_objects") != expected_history
            or receipt.get("mutation_state")
            != ("restored" if succeeded else "indeterminate")
            or receipt.get("rollback")
            != ("succeeded" if succeeded else "blocked-by-later-edit")
        ):
            _fail("source feature failure receipt fields changed")
    else:
        _fail("source feature rejection has an unknown diagnostic")
    return receipt


def apply_source_feature_plan(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    consent_plan_id: str,
    transaction_lock: Path | str,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    value = validate_source_feature_plan(plan, suite_root=suite_root)
    if consent_plan_id != value["id"]:
        _fail("apply requires consent to the exact source feature plan ID")
    verification = verify_source_feature_plan(suite_root, value)
    if verification["state"] != "ready":
        _fail(f"source feature plan is stale: {verification['reason']}")
    workspace = _workspace_from_uri(value["workspace_uri"])
    transaction = _transaction_authority()

    def source_preflight(root: Path, candidate: Mapping[str, Any]) -> bool:
        try:
            return (
                root == workspace
                and candidate.get("id") == value["id"]
                and verify_source_feature_plan(suite_root, candidate)["state"] == "ready"
            )
        except (OSError, ValueError):
            return False

    def commit(candidate: Mapping[str, Any]) -> None:
        if commit_receipt is not None:
            commit_receipt(
                validate_source_feature_receipt(
                    candidate,
                    value,
                    suite_root=suite_root,
                )
            )

    try:
        result = transaction.apply_application_transaction(
            workspace,
            value,
            state_root,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="applied-experimental-local-edit",
            source_preflight=source_preflight,
            transaction_lock=transaction_lock,
            commit_receipt=commit if commit_receipt is not None else None,
        )
    except ValueError as exc:
        raise DeveloperFeatureError(f"source feature transaction failed: {exc}") from exc
    return validate_source_feature_receipt(
        result,
        value,
        suite_root=suite_root,
    )


def validate_source_feature_rollback(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    reviewed = validate_source_feature_plan(plan, suite_root=suite_root)
    if type(value) is not dict:
        _fail("source feature rollback must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        set(receipt)
        != {
            "diagnostic_code",
            "format",
            "id",
            "kind",
            "plan_id",
            "schema_version",
            "state",
            "workspace_mutated",
        }
        or receipt.get("format") != ROLLBACK_FORMAT
        or receipt.get("kind") != ROLLBACK_KIND
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state") not in {"restored", "rejected"}
        or supplied != _content_id(ROLLBACK_KIND, body)
    ):
        _fail("source feature rollback identity changed")
    if receipt["state"] == "restored":
        if receipt.get("diagnostic_code") is not None or receipt.get("workspace_mutated") is not True:
            _fail("source feature rollback success fields changed")
    elif (
        receipt.get("diagnostic_code")
        not in {
            "BLUEPRINTS_M2_LATER_EDIT_PRESERVED",
            "BLUEPRINTS_M2_TRANSACTION_LOCKED",
        }
        or receipt.get("workspace_mutated") is not False
    ):
        _fail("source feature rollback rejection fields changed")
    return receipt


def rollback_source_feature(
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    suite_root: Path | str | None = None,
    application_receipt: Mapping[str, Any],
    transaction_lock: Path | str,
) -> dict[str, Any]:
    reviewed = validate_source_feature_plan(plan, suite_root=suite_root)
    applied = validate_source_feature_receipt(
        application_receipt,
        reviewed,
        suite_root=suite_root,
    )
    if applied["state"] != "applied":
        _fail("rollback requires a successful source feature receipt")
    try:
        result = _transaction_authority().rollback_application_transaction(
            _workspace_from_uri(reviewed["workspace_uri"]),
            reviewed,
            state_root,
            applied=applied,
            rollback_format=ROLLBACK_FORMAT,
            rollback_kind=ROLLBACK_KIND,
            rollback_content_kind=ROLLBACK_KIND,
            transaction_lock=transaction_lock,
        )
    except ValueError as exc:
        raise DeveloperFeatureError(f"source feature rollback failed: {exc}") from exc
    return validate_source_feature_rollback(
        result,
        reviewed,
        suite_root=suite_root,
    )


def validate_source_feature_recovery(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    suite_root: Path | str | None = None,
) -> dict[str, Any]:
    reviewed = validate_source_feature_plan(plan, suite_root=suite_root)
    if type(value) is not dict:
        _fail("source feature recovery must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        set(receipt)
        != {
            "application_receipt",
            "attempted_ordinals",
            "diagnostic_code",
            "format",
            "id",
            "kind",
            "plan_id",
            "schema_version",
            "state",
            "workspace_mutated",
        }
        or receipt.get("format") != RECOVERY_FORMAT
        or receipt.get("kind") != RECOVERY_KIND
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state") not in {"applied", "restored", "review-required"}
        or supplied != _content_id(RECOVERY_KIND, body)
    ):
        _fail("source feature recovery identity changed")
    attempted = receipt.get("attempted_ordinals")
    if (
        type(attempted) is not list
        or any(type(row) is not int or type(row) is bool for row in attempted)
        or attempted != list(range(len(attempted)))
        or len(attempted) > len(reviewed["operations"])
        or type(receipt.get("workspace_mutated")) is not bool
    ):
        _fail("source feature recovery attempt ownership changed")
    if receipt["state"] == "applied":
        if (
            attempted != list(range(len(reviewed["operations"])))
            or receipt.get("diagnostic_code") is not None
            or receipt.get("workspace_mutated") is not False
            or type(receipt.get("application_receipt")) is not dict
        ):
            _fail("source feature recovery finalization fields changed")
        applied = validate_source_feature_receipt(
            receipt["application_receipt"],
            reviewed,
            suite_root=suite_root,
        )
        if applied["state"] != "applied":
            _fail("source feature recovery requires an applied receipt")
    elif receipt["state"] == "restored":
        if (
            receipt.get("diagnostic_code")
            not in {
                "BLUEPRINTS_M2_INTERRUPTED_BEFORE_MUTATION",
                "BLUEPRINTS_M2_INTERRUPTED_TRANSACTION_RESTORED",
            }
            or receipt.get("application_receipt") is not None
        ):
            _fail("source feature recovery restoration fields changed")
    elif (
        receipt.get("diagnostic_code") != "BLUEPRINTS_M2_RECOVERY_REQUIRES_REVIEW"
        or receipt.get("workspace_mutated") is not False
        or receipt.get("application_receipt") is not None
    ):
        _fail("source feature recovery review fields changed")
    return receipt


def recover_source_feature(
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    suite_root: Path | str | None = None,
    transaction_lock: Path | str,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    reviewed = validate_source_feature_plan(plan, suite_root=suite_root)

    def commit(candidate: Mapping[str, Any]) -> None:
        if commit_receipt is not None:
            commit_receipt(
                validate_source_feature_receipt(
                    candidate,
                    reviewed,
                    suite_root=suite_root,
                )
            )

    try:
        result = _transaction_authority().recover_application_transaction(
            _workspace_from_uri(reviewed["workspace_uri"]),
            reviewed,
            state_root,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="applied-experimental-local-edit",
            transaction_lock=transaction_lock,
            commit_receipt=commit if commit_receipt is not None else None,
        )
    except ValueError as exc:
        raise DeveloperFeatureError(f"source feature recovery failed: {exc}") from exc
    application_receipt = result.get("application_receipt")
    if application_receipt is not None:
        application_receipt = validate_source_feature_receipt(
            application_receipt,
            reviewed,
            suite_root=suite_root,
        )
    body = {
        "application_receipt": application_receipt,
        "attempted_ordinals": result.get("attempted_ordinals"),
        "diagnostic_code": result.get("diagnostic_code"),
        "format": RECOVERY_FORMAT,
        "kind": RECOVERY_KIND,
        "plan_id": reviewed["id"],
        "schema_version": 1,
        "state": result.get("outcome"),
        "workspace_mutated": result.get("workspace_mutated"),
    }
    return validate_source_feature_recovery(
        _seal(RECOVERY_KIND, body),
        reviewed,
        suite_root=suite_root,
    )


__all__ = [
    "AUTHORITY_BOUNDARY",
    "PLAN_KIND",
    "QUEST_FAMILY",
    "QUEST_OPTIONS_FORMAT",
    "RECEIPT_KIND",
    "RECOVERY_KIND",
    "ROLLBACK_KIND",
    "apply_source_feature_plan",
    "build_quest_for_process_plan",
    "quest_for_process_options",
    "recover_source_feature",
    "rollback_source_feature",
    "validate_source_feature_plan",
    "validate_source_feature_receipt",
    "verify_source_feature_plan",
]
