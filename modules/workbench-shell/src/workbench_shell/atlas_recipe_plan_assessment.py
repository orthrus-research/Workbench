"""Translate one owner-validated recipe-change plan into Atlas input."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, cast

from workbench_atlas_recipe_health import assess_proposed_recipe, open_recipe_health

from .developer_feature import DeveloperFeatureError, resolve_feature_record
from workbench_blueprints.profile_construction import recipe_change_authority


def _validated_recipe_change_plan(
    suite_root: Path | str,
    state_root: Path | str,
    plan_reference: Path | str,
) -> dict[str, Any]:
    """Reopen one retained plan and require exact current owner inputs."""

    authority = recipe_change_authority("supersymmetry")
    raw = resolve_feature_record(state_root, "plans", plan_reference)
    plan = cast(dict[str, Any], authority.validate_recipe_change_plan(raw))
    verification = authority.verify_recipe_change_plan(
        Path(suite_root).expanduser().resolve(), plan
    )
    if (
        not isinstance(verification, Mapping)
        or verification.get("state") != "ready"
        or verification.get("plan_id") != plan["id"]
    ):
        reason = (
            verification.get("reason")
            if isinstance(verification, Mapping)
            else None
        )
        raise DeveloperFeatureError(
            "the retained recipe-change plan is stale for the current source"
            + (f": {reason}" if isinstance(reason, str) and reason else "")
        )
    return plan


def _proposal_from_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Translate only the generic recipe fields Atlas is allowed to consume."""

    request = cast(Mapping[str, Any], plan["request"])
    map_binding = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], plan["authority_bindings"])["recipe_map"],
    )
    return {
        "proposal_id": plan["id"],
        "source_format": plan["format"],
        "source_kind": plan["kind"],
        "mutation": request["mutation"],
        "recipe_map": map_binding["registry_name"],
        "duration": request["duration"],
        "voltage_tier": request["voltage_tier"],
        "item_inputs": request["item_inputs"],
        "fluid_inputs": request["fluid_inputs"],
        "item_outputs": request["item_outputs"],
        "fluid_outputs": request["fluid_outputs"],
    }


def recipe_change_proposal(
    suite_root: Path | str,
    state_root: Path | str,
    plan_reference: Path | str,
) -> dict[str, Any]:
    """Return the bounded projection of one current owner plan."""

    return _proposal_from_plan(
        _validated_recipe_change_plan(suite_root, state_root, plan_reference)
    )


def _expected_graph_scope(plan: Mapping[str, Any]) -> dict[str, str]:
    """Project the plan owner's pack/platform authority into graph scope."""

    context = cast(Mapping[str, Any], plan["profile_context"])
    return {
        "pack_profile_id": cast(str, context["pack_profile_id"]),
        "platform_profile_id": cast(str, context["platform_profile_id"]),
        "platform_candidate": "cleanroom-" + cast(str, context["cleanroom_version"]),
    }


def _require_matching_graph_scope(
    description: Mapping[str, Any], expected: Mapping[str, str]
) -> None:
    scope = description.get("scope")
    if type(scope) is not dict or any(scope.get(key) != value for key, value in expected.items()):
        raise DeveloperFeatureError(
            "the Atlas graph pack/platform scope does not match the validated recipe-change plan"
        )


def assess_recipe_change_plan(
    suite_root: Path | str,
    graph_root: Path | str,
    state_root: Path | str,
    plan_reference: Path | str,
    *,
    max_depth: int = 4,
    max_nodes: int = 500,
) -> dict[str, Any]:
    """Assess one retained ADD plan against one explicit verified graph."""

    plan = _validated_recipe_change_plan(suite_root, state_root, plan_reference)
    proposal = _proposal_from_plan(plan)
    expected_scope = _expected_graph_scope(plan)
    with open_recipe_health(Path(graph_root)) as view:
        _require_matching_graph_scope(view.describe(), expected_scope)
        return assess_proposed_recipe(
            view,
            proposal,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )


__all__ = ["assess_recipe_change_plan", "recipe_change_proposal"]
