"""Small Shell-owned projections for common developer-feature workflows.

The owner option and plan records are intentionally left unchanged.  These
views exist for interactive filtering and automation that does not need the
owner records' exact byte payloads.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, NoReturn, cast
from urllib.parse import urlparse

from .developer_feature import DeveloperFeatureError
from .developer_feature_transaction_view import validate_feature_transaction_view


OPTION_FAMILIES = ("material-fluid-recipe", "recipe-change")
FAMILIES = (*OPTION_FAMILIES, "quest-for-process")
OPTIONS_FORMAT = "workbench-developer-feature-options-projection-v1"
OPTIONS_KIND = "workbench-developer-feature-options-projection"
COMPACT_PLAN_FORMAT = "workbench-developer-feature-compact-plan-result-v1"
COMPACT_PLAN_KIND = "workbench-developer-feature-compact-plan-result"
MAXIMUM_OPTION_LIMIT = 200
_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_EXPECTED_OPTION_FORMATS = {
    "material-fluid-recipe": (
        "workbench-developer-material-fluid-recipe-options-v1"
    ),
    "recipe-change": "workbench-supersymmetry-recipe-change-options-v1",
}
_EXPECTED_PLAN_FORMATS = {
    "material-fluid-recipe": "workbench-developer-material-fluid-recipe-plan-v1",
    "recipe-change": "workbench-supersymmetry-recipe-change-plan-v1",
    "quest-for-process": "workbench-developer-source-feature-plan-v1",
}
_EXPECTED_PLAN_STATES = {
    "material-fluid-recipe": "experimental-ready",
    "recipe-change": "experimental-ready",
    "quest-for-process": "experimental-ready-runtime-unverified",
}
_OPTIONS_BOUNDARY = {
    "construction_authority": "source-owner-options",
    "owner_record_mutated": False,
    "profile_support_claimed": False,
    "release_or_publication_authorized": False,
}
_COMPACT_PLAN_BOUNDARY = {
    "complete_owner_plan_omitted": True,
    "owner_plan_remains_construction_authority": True,
    "retained_plan_remains_action_authority": True,
    "release_or_publication_authorized": False,
}


def _fail(message: str) -> NoReturn:
    raise DeveloperFeatureError(message)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _seal(kind: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(body),
        "id": f"{kind}:sha256:{sha256(_canonical_bytes(body)).hexdigest()}",
    }


def _safe_relative(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        _fail(f"{label} is not a safe relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail(f"{label} is not a safe relative path")
    return value


def _file_uri(value: Any, label: str) -> str:
    if type(value) is not str:
        _fail(f"{label} must be a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or not parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} must be a local file URI")
    return value


def _option_query(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > 512:
        _fail("feature option query must be 1 to 512 non-whitespace characters")
    return value


def _option_limit(value: Any) -> int:
    if (
        type(value) is not int
        or type(value) is bool
        or not 1 <= value <= MAXIMUM_OPTION_LIMIT
    ):
        _fail(f"feature option limit must be from 1 to {MAXIMUM_OPTION_LIMIT}")
    return value


def _source_options(
    family: str,
    value: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    if (
        type(value) is not dict
        or value.get("format") != _EXPECTED_OPTION_FORMATS[family]
        or value.get("schema_version") != 1
        or type(value.get("schema_version")) is bool
        or value.get("state") != "experimental"
    ):
        _fail("source owner option identity changed")
    recipe_maps = value.get("recipe_maps")
    scripts = value.get("scripts")
    if type(recipe_maps) is not list or type(scripts) is not list:
        _fail("source owner option collections changed")
    checked_maps: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    for row in recipe_maps:
        if (
            type(row) is not dict
            or set(row) != {"alias", "registry_name"}
            or type(row.get("alias")) is not str
            or not row["alias"]
            or type(row.get("registry_name")) is not str
            or not row["registry_name"]
            or (row["alias"], row["registry_name"]) in identities
        ):
            _fail("source owner recipe-map options changed")
        identities.add((row["alias"], row["registry_name"]))
        checked_maps.append(dict(row))
    checked_scripts: list[str] = []
    for script in scripts:
        checked = _safe_relative(script, "source owner recipe script")
        if checked in checked_scripts:
            _fail("source owner recipe-script options changed")
        checked_scripts.append(checked)
    return checked_maps, checked_scripts


def project_recipe_options(
    family: str,
    owner_options: Mapping[str, Any],
    *,
    query: str | None,
    limit: int,
) -> dict[str, Any]:
    """Filter recipe owner V1 options into an exact, bounded Shell view."""

    if family not in OPTION_FAMILIES:
        _fail("feature option projection family is unsupported")
    query = _option_query(query)
    limit = _option_limit(limit)
    recipe_maps, scripts = _source_options(family, owner_options)
    needle = None if query is None else query.casefold()
    matched_maps = [
        row
        for row in recipe_maps
        if needle is None
        or needle in row["alias"].casefold()
        or needle in row["registry_name"].casefold()
    ]
    matched_scripts = [
        row for row in scripts if needle is None or needle in row.casefold()
    ]
    selected_maps = matched_maps[:limit]
    selected_scripts = matched_scripts[:limit]
    counts = {
        "recipe_maps": {
            "matched": len(matched_maps),
            "returned": len(selected_maps),
            "total": len(recipe_maps),
        },
        "scripts": {
            "matched": len(matched_scripts),
            "returned": len(selected_scripts),
            "total": len(scripts),
        },
    }
    body = {
        "authority_boundary": dict(_OPTIONS_BOUNDARY),
        "counts": counts,
        "family": family,
        "format": OPTIONS_FORMAT,
        "kind": OPTIONS_KIND,
        "limit": limit,
        "query": query,
        "recipe_maps": selected_maps,
        "schema_version": 1,
        "scripts": selected_scripts,
        "source_options": {
            "format": owner_options["format"],
            "schema_version": owner_options["schema_version"],
            "state": owner_options["state"],
        },
        "state": "available",
        "truncated": any(
            row["returned"] < row["matched"] for row in counts.values()
        ),
    }
    return validate_recipe_options_projection(_seal(OPTIONS_KIND, body))


def _validate_count(value: Any, *, returned: int, limit: int) -> None:
    if (
        type(value) is not dict
        or set(value) != {"matched", "returned", "total"}
        or any(type(value.get(field)) is not int for field in value)
        or any(type(value.get(field)) is bool for field in value)
        or not 0 <= value["returned"] <= value["matched"] <= value["total"]
        or value["returned"] != returned
        or value["returned"] != min(limit, value["matched"])
    ):
        _fail("feature option projection counts changed")


def validate_recipe_options_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exact Shell-owned recipe option projection."""

    if type(value) is not dict:
        _fail("feature option projection must be one ordinary object")
    result = dict(value)
    body = dict(result)
    supplied = body.pop("id", None)
    fields = {
        "authority_boundary",
        "counts",
        "family",
        "format",
        "id",
        "kind",
        "limit",
        "query",
        "recipe_maps",
        "schema_version",
        "scripts",
        "source_options",
        "state",
        "truncated",
    }
    family = result.get("family")
    if (
        set(result) != fields
        or result.get("format") != OPTIONS_FORMAT
        or result.get("kind") != OPTIONS_KIND
        or result.get("schema_version") != 1
        or type(result.get("schema_version")) is bool
        or family not in OPTION_FAMILIES
        or result.get("state") != "available"
        or result.get("authority_boundary") != _OPTIONS_BOUNDARY
        or type(supplied) is not str
        or supplied != _seal(OPTIONS_KIND, body)["id"]
    ):
        _fail("feature option projection identity changed")
    query = _option_query(result.get("query"))
    limit = _option_limit(result.get("limit"))
    source = result.get("source_options")
    if (
        type(source) is not dict
        or set(source) != {"format", "schema_version", "state"}
        or source.get("format") != _EXPECTED_OPTION_FORMATS[cast(str, family)]
        or source.get("schema_version") != 1
        or type(source.get("schema_version")) is bool
        or source.get("state") != "experimental"
    ):
        _fail("feature option projection source identity changed")
    projected_owner = {
        "format": source["format"],
        "schema_version": source["schema_version"],
        "state": source["state"],
        "recipe_maps": result.get("recipe_maps"),
        "scripts": result.get("scripts"),
    }
    recipe_maps, scripts = _source_options(cast(str, family), projected_owner)
    needle = None if query is None else query.casefold()
    if any(
        needle is not None
        and needle not in row["alias"].casefold()
        and needle not in row["registry_name"].casefold()
        for row in recipe_maps
    ) or any(
        needle is not None and needle not in row.casefold() for row in scripts
    ):
        _fail("feature option projection includes a non-matching option")
    counts = result.get("counts")
    if type(counts) is not dict or set(counts) != {"recipe_maps", "scripts"}:
        _fail("feature option projection count collections changed")
    _validate_count(counts["recipe_maps"], returned=len(recipe_maps), limit=limit)
    _validate_count(counts["scripts"], returned=len(scripts), limit=limit)
    truncated = any(
        row["returned"] < row["matched"] for row in counts.values()
    )
    if type(result.get("truncated")) is not bool or result["truncated"] is not truncated:
        _fail("feature option projection truncation changed")
    return result


def _review_summary(owner_plan: Mapping[str, Any]) -> dict[str, Any]:
    review = owner_plan.get("review")
    operations = owner_plan.get("operations")
    if type(review) is not dict or type(operations) is not list:
        _fail("owner plan lacks its reviewed operation summary")
    summary = {
        "additions": review.get("additions"),
        "changed_files": review.get("changed_files"),
        "deletions": review.get("deletions"),
        "operation_count": len(operations),
    }
    return _validate_review(summary)


def _validate_review(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "additions",
        "changed_files",
        "deletions",
        "operation_count",
    }:
        _fail("compact plan review fields changed")
    changed = value.get("changed_files")
    if (
        type(value.get("additions")) is not int
        or type(value.get("additions")) is bool
        or value["additions"] < 0
        or type(value.get("deletions")) is not int
        or type(value.get("deletions")) is bool
        or value["deletions"] < 0
        or type(value.get("operation_count")) is not int
        or type(value.get("operation_count")) is bool
        or not 1 <= value["operation_count"] <= 64
        or type(changed) is not list
        or len(changed) != value["operation_count"]
        or len(set(changed)) != len(changed)
    ):
        _fail("compact plan review values changed")
    for path in changed:
        _safe_relative(path, "compact plan changed file")
    return dict(value)


def build_compact_plan_result(
    *,
    family: str,
    owner_plan: Mapping[str, Any] | None,
    retained_plan_uri: str | None,
    transaction_view: Mapping[str, Any] | None,
    example_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact plan result after ordinary owner-plan retention."""

    if family not in FAMILIES:
        _fail("compact plan family is unsupported")
    example: dict[str, Any] | None = None
    diagnostic: dict[str, str] | None = None
    if example_result is not None:
        example_row = example_result.get("example")
        applicability = example_result.get("applicability")
        if type(example_row) is not dict or type(applicability) is not dict:
            _fail("packaged example result lacks applicability")
        current = applicability.get("current")
        historical = applicability.get("historical")
        if type(current) is not dict or type(historical) is not dict:
            _fail("packaged example result applicability changed")
        example = {
            "current_applicability_state": current.get("state"),
            "example_key": example_row.get("example_key"),
            "historical_applicability_state": historical.get("state"),
        }
        if owner_plan is None:
            reason = current.get("reason")
            if type(reason) is not str or not reason:
                _fail("not-applicable packaged example lacks its owner reason")
            diagnostic = {
                "code": "example-not-applicable",
                "reason": reason[:4096],
            }
    if owner_plan is None:
        if retained_plan_uri is not None or transaction_view is not None or example is None:
            _fail("compact plan absence is inconsistent")
        plan = None
        review = None
        transaction = None
        state = "not-applicable"
    else:
        if (
            owner_plan.get("format") != _EXPECTED_PLAN_FORMATS[family]
            or owner_plan.get("state") != _EXPECTED_PLAN_STATES[family]
            or type(owner_plan.get("id")) is not str
            or _CONTENT_ID.fullmatch(owner_plan["id"]) is None
            or retained_plan_uri is None
            or transaction_view is None
        ):
            _fail("compact plan owner identity changed")
        _file_uri(retained_plan_uri, "retained compact plan")
        transaction_view = validate_feature_transaction_view(transaction_view)
        if (
            transaction_view.get("family") != family
            or transaction_view.get("plan_id") != owner_plan["id"]
            or transaction_view.get("workspace_uri") != owner_plan.get("workspace_uri")
        ):
            _fail("compact plan transaction binding changed")
        plan = {
            "format": owner_plan["format"],
            "id": owner_plan["id"],
            "state": owner_plan["state"],
            "workspace_uri": _file_uri(
                owner_plan.get("workspace_uri"), "compact plan workspace"
            ),
        }
        review = _review_summary(owner_plan)
        if review["operation_count"] != len(transaction_view["operations"]):
            _fail("compact plan operation count changed")
        transaction = {
            "current_effective_state": transaction_view["current_effective_state"],
            "plan_freshness_state": transaction_view["plan_freshness"]["state"],
            "workspace_match_state": transaction_view["workspace_match"]["state"],
        }
        state = "ready"
    body = {
        "authority_boundary": dict(_COMPACT_PLAN_BOUNDARY),
        "diagnostic": diagnostic,
        "example": example,
        "family": family,
        "format": COMPACT_PLAN_FORMAT,
        "kind": COMPACT_PLAN_KIND,
        "limitations": [
            (
                "This projection omits exact operation bytes and unified diffs; "
                "the retained owner plan remains authoritative."
            ),
            (
                "Apply still requires consent to the exact retained plan ID and "
                "owner revalidation under the transaction lock."
            ),
        ],
        "plan": plan,
        "retained_plan_uri": retained_plan_uri,
        "review": review,
        "schema_version": 1,
        "state": state,
        "transaction": transaction,
    }
    return validate_compact_plan_result(_seal(COMPACT_PLAN_KIND, body))


def validate_compact_plan_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact, non-byte-bearing plan result projection."""

    if type(value) is not dict:
        _fail("compact plan result must be one ordinary object")
    result = dict(value)
    body = dict(result)
    supplied = body.pop("id", None)
    fields = {
        "authority_boundary",
        "diagnostic",
        "example",
        "family",
        "format",
        "id",
        "kind",
        "limitations",
        "plan",
        "retained_plan_uri",
        "review",
        "schema_version",
        "state",
        "transaction",
    }
    family = result.get("family")
    if (
        set(result) != fields
        or result.get("format") != COMPACT_PLAN_FORMAT
        or result.get("kind") != COMPACT_PLAN_KIND
        or result.get("schema_version") != 1
        or type(result.get("schema_version")) is bool
        or family not in FAMILIES
        or result.get("authority_boundary") != _COMPACT_PLAN_BOUNDARY
        or type(supplied) is not str
        or supplied != _seal(COMPACT_PLAN_KIND, body)["id"]
        or type(result.get("limitations")) is not list
        or not all(type(row) is str and row for row in result["limitations"])
    ):
        _fail("compact plan result identity changed")
    example = result.get("example")
    if example is not None and (
        type(example) is not dict
        or set(example)
        != {
            "current_applicability_state",
            "example_key",
            "historical_applicability_state",
        }
        or type(example.get("example_key")) is not str
        or not example["example_key"]
        or example.get("current_applicability_state")
        not in {"owner-plan-validated", "owner-plan-rejected"}
        or example.get("historical_applicability_state")
        not in {"not-declared", "provenance-only"}
    ):
        _fail("compact plan example summary changed")
    if result.get("state") == "not-applicable":
        diagnostic = result.get("diagnostic")
        if (
            example is None
            or example["current_applicability_state"] != "owner-plan-rejected"
            or result.get("plan") is not None
            or result.get("retained_plan_uri") is not None
            or result.get("review") is not None
            or result.get("transaction") is not None
            or type(diagnostic) is not dict
            or set(diagnostic) != {"code", "reason"}
            or diagnostic.get("code") != "example-not-applicable"
            or type(diagnostic.get("reason")) is not str
            or not diagnostic["reason"]
            or len(diagnostic["reason"]) > 4096
        ):
            _fail("compact not-applicable plan result changed")
        return result
    if result.get("state") != "ready" or result.get("diagnostic") is not None:
        _fail("compact plan result state changed")
    plan = result.get("plan")
    if (
        type(plan) is not dict
        or set(plan) != {"format", "id", "state", "workspace_uri"}
        or plan.get("format") != _EXPECTED_PLAN_FORMATS[cast(str, family)]
        or plan.get("state") != _EXPECTED_PLAN_STATES[cast(str, family)]
        or type(plan.get("id")) is not str
        or _CONTENT_ID.fullmatch(plan["id"]) is None
    ):
        _fail("compact plan owner summary changed")
    _file_uri(plan.get("workspace_uri"), "compact plan workspace")
    _file_uri(result.get("retained_plan_uri"), "retained compact plan")
    _validate_review(result.get("review"))
    transaction = result.get("transaction")
    if (
        type(transaction) is not dict
        or set(transaction)
        != {
            "current_effective_state",
            "plan_freshness_state",
            "workspace_match_state",
        }
        or transaction.get("current_effective_state")
        not in {
            "applied",
            "drifted",
            "interrupted",
            "matches-after-without-receipt",
            "mixed",
            "planned",
            "restored",
            "unavailable",
        }
        or transaction.get("plan_freshness_state") not in {"ready", "stale"}
        or transaction.get("workspace_match_state")
        not in {"matches-before", "matches-after", "mixed", "drifted", "unavailable"}
    ):
        _fail("compact plan transaction summary changed")
    if example is not None and example["current_applicability_state"] != "owner-plan-validated":
        _fail("compact ready example applicability changed")
    return result


__all__ = [
    "COMPACT_PLAN_FORMAT",
    "OPTIONS_FORMAT",
    "build_compact_plan_result",
    "project_recipe_options",
    "validate_compact_plan_result",
    "validate_recipe_options_projection",
]
