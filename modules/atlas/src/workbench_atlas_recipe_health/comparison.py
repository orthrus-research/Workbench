"""Fail-closed, bounded comparison of two observed runtime recipe graphs.

Recipe semantic hashes identify immutable observed signatures.  A signature
that is absent before and present after is therefore an addition, while the
inverse is a removal.  Atlas intentionally does not pair one removal with one
addition as a "changed recipe": the current capture protocol publishes no
stable recipe-occurrence identity across launches.

The comparison is narrower than gameplay reachability.  It reports exact
finite-recipe membership and bounded graph-structure exposure.  Other ways to
obtain resources, inventory state, dynamic recipe execution, and player/task
execution remain outside the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover - avoids a runtime import cycle
    from .view import GraphRecipeHealthView


RUNTIME_COMPARISON_FORMAT = "workbench-atlas-runtime-recipe-comparison-v1"

_RECIPE_KIND = "gt-recipe"
_INPUT_RELATIONS = frozenset(
    {
        "accepts-gt-item-alternative",
        "accepts-ore-dictionary-class",
        "accepts-gt-fluid-input",
    }
)
_OUTPUT_RELATIONS = frozenset({"produces-gt-item", "produces-gt-fluid"})
_SELECTOR_RELATIONS = frozenset(
    {"has-item-input-selector", "has-fluid-input-selector"}
)
_QUEST_RESOURCE_RELATIONS = frozenset(
    {"observes-progression-item-variant", "requires-progression-fluid"}
)
_TASK_RESOURCE_RELATIONS = frozenset(
    {"has-progression-item-requirement", "has-progression-fluid-requirement"}
)
_HEX = frozenset("0123456789abcdef")


def _canonical_text(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _bound(value: object, label: str, *, minimum: int, maximum: int) -> int:
    from .view import RecipeHealthError

    if type(value) is not int or not minimum <= value <= maximum:
        raise RecipeHealthError(
            f"runtime recipe comparison {label} is outside {minimum}..{maximum}"
        )
    return value


def _node_summary(node: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": node["id"],
        "kind": node["kind"],
        "semantic_key": node["semantic_key"],
        "properties": node["properties"],
        "evidence": node["evidence"],
    }


def _edge_summary(edge: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "relation": edge["relation"],
        "semantic_key": edge["semantic_key"],
        "properties": edge["properties"],
        "evidence": edge["evidence"],
    }


def _collect_named_values(value: object, name: str) -> list[object]:
    result: list[object] = []
    if type(value) is dict:
        for key, child in value.items():
            if key == name:
                result.append(child)
            result.extend(_collect_named_values(child, name))
    elif type(value) is list:
        for child in value:
            result.extend(_collect_named_values(child, name))
    return result


def _unique_canonical(values: Iterable[object]) -> list[object]:
    by_value = {_canonical_text(value): value for value in values}
    return [by_value[key] for key in sorted(by_value)]


def _recipe_category_bindings(evidence_binding: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(value: object) -> None:
        if type(value) is dict:
            categories = value.get("category_results")
            if type(categories) is dict and type(categories.get("gt-recipes")) is dict:
                rows.append(dict(categories["gt-recipes"]))
            for child in value.values():
                visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)

    visit(evidence_binding)
    return [dict(value) for value in _unique_canonical(rows)]


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _capture_protocol(view: "GraphRecipeHealthView") -> dict[str, Any]:
    binding = view.manifest["evidence_binding"]
    adapter_profiles = _unique_canonical(
        _collect_named_values(binding, "adapter_profile_sha256")
    )
    input_manifests = _unique_canonical(
        _collect_named_values(binding, "input_manifest_sha256")
    )
    recipe_bindings = _recipe_category_bindings(binding)
    adapter_profile_state = (
        "exactly-one"
        if len(adapter_profiles) == 1 and _is_sha256(adapter_profiles[0])
        else "missing"
        if not adapter_profiles
        else "invalid"
        if len(adapter_profiles) == 1
        else "ambiguous"
    )
    return {
        "adapter_profile_state": adapter_profile_state,
        "adapter_profile_sha256": (
            adapter_profiles[0] if adapter_profile_state == "exactly-one" else None
        ),
        "observed_adapter_profile_values": adapter_profiles,
        "input_manifest_values": input_manifests,
        "recipe_category_binding_state": (
            "exactly-one"
            if len(recipe_bindings) == 1
            else "missing"
            if not recipe_bindings
            else "ambiguous"
        ),
        "recipe_category_binding": (
            recipe_bindings[0] if len(recipe_bindings) == 1 else None
        ),
        "observed_recipe_category_bindings": recipe_bindings,
    }


def _comparison_context(view: "GraphRecipeHealthView") -> dict[str, Any]:
    context = view.describe()
    return {**context, "capture_protocol": _capture_protocol(view)}


def _compatibility(
    before: "GraphRecipeHealthView", after: "GraphRecipeHealthView"
) -> dict[str, Any]:
    before_protocol = _capture_protocol(before)
    after_protocol = _capture_protocol(after)
    blocking: list[dict[str, Any]] = []

    if before.manifest["scope"] != after.manifest["scope"]:
        blocking.append(
            {
                "code": "graph-scope-mismatch",
                "before": before.manifest["scope"],
                "after": after.manifest["scope"],
                "message": "The graph scopes differ, so absence cannot be interpreted as a runtime delta.",
            }
        )
    for side, protocol in (("before", before_protocol), ("after", after_protocol)):
        if protocol["adapter_profile_state"] != "exactly-one":
            blocking.append(
                {
                    "code": "adapter-protocol-unavailable",
                    "side": side,
                    "observed_values": protocol["observed_adapter_profile_values"],
                    "message": "One exact adapter profile is required for recipe-signature comparison.",
                }
            )
        if protocol["recipe_category_binding_state"] != "exactly-one":
            blocking.append(
                {
                    "code": "recipe-capture-binding-unavailable",
                    "side": side,
                    "observed_bindings": protocol[
                        "observed_recipe_category_bindings"
                    ],
                    "message": "One exact gt-recipes capture binding is required.",
                }
            )
        else:
            recipe_binding = protocol["recipe_category_binding"]
            assert type(recipe_binding) is dict
            side_view = before if side == "before" else after
            valid_binding = (
                recipe_binding.get("category_id") == "transformation-recipe"
                and recipe_binding.get("checkpoint_id")
                == "post-start-end-tick"
                and type(recipe_binding.get("record_count")) is int
                and recipe_binding["record_count"]
                == side_view.kinds.get(_RECIPE_KIND, 0)
                and _is_sha256(recipe_binding.get("records_sha256"))
                and _is_sha256(recipe_binding.get("result_sha256"))
            )
            if not valid_binding:
                blocking.append(
                    {
                        "code": "recipe-capture-binding-invalid",
                        "side": side,
                        "observed_binding": recipe_binding,
                        "message": "The gt-recipes binding does not match the supported complete post-start capture.",
                    }
                )
    if (
        before_protocol["adapter_profile_state"] == "exactly-one"
        and after_protocol["adapter_profile_state"] == "exactly-one"
        and before_protocol["adapter_profile_sha256"]
        != after_protocol["adapter_profile_sha256"]
    ):
        blocking.append(
            {
                "code": "adapter-protocol-mismatch",
                "before": before_protocol["adapter_profile_sha256"],
                "after": after_protocol["adapter_profile_sha256"],
                "message": "Recipe semantic hashes were produced by different adapter profiles.",
            }
        )
    if (
        before_protocol["recipe_category_binding_state"] == "exactly-one"
        and after_protocol["recipe_category_binding_state"] == "exactly-one"
    ):
        before_binding = before_protocol["recipe_category_binding"]
        after_binding = after_protocol["recipe_category_binding"]
        assert type(before_binding) is dict and type(after_binding) is dict
        for field in ("category_id", "checkpoint_id"):
            if before_binding.get(field) != after_binding.get(field):
                blocking.append(
                    {
                        "code": "recipe-capture-semantics-mismatch",
                        "field": field,
                        "before": before_binding.get(field),
                        "after": after_binding.get(field),
                        "message": "The gt-recipes capture semantics differ.",
                    }
                )
    if before.kinds.get(_RECIPE_KIND, 0) == 0 or after.kinds.get(_RECIPE_KIND, 0) == 0:
        blocking.append(
            {
                "code": "finite-recipe-evidence-unavailable",
                "message": "Both graphs must publish finite GT recipe nodes.",
            }
        )
    return {
        "state": "incomparable" if blocking else "compatible",
        "blocking_differences": blocking,
        "before_capture_protocol": before_protocol,
        "after_capture_protocol": after_protocol,
        "interpretation": (
            "no semantic delta is computed when scope or capture protocol compatibility is unproven"
        ),
    }


@dataclass
class _TraversalBudget:
    maximum: int
    seen: set[tuple[str, str]] = field(default_factory=set)
    frontiers: list[dict[str, Any]] = field(default_factory=list)
    _frontier_keys: set[tuple[object, ...]] = field(default_factory=set)

    def admit(
        self,
        side: str,
        node_id: str,
        *,
        phase: str,
        from_node_id: str | None = None,
        relation: str | None = None,
    ) -> bool:
        key = (side, node_id)
        if key in self.seen:
            return True
        if len(self.seen) < self.maximum:
            self.seen.add(key)
            return True
        self.frontier(
            "node-bound",
            phase=phase,
            side=side,
            from_node_id=from_node_id,
            relation=relation,
            omitted_at_least=1,
        )
        return False

    def frontier(self, kind: str, **detail: Any) -> None:
        key = (
            kind,
            detail.get("phase"),
            detail.get("side"),
            detail.get("from_node_id"),
            detail.get("relation"),
            detail.get("depth"),
        )
        if key in self._frontier_keys:
            return
        self._frontier_keys.add(key)
        self.frontiers.append({"kind": kind, **detail})


def _empty_signature_comparison(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "before_count": None,
        "after_count": None,
        "unchanged_exact_signature_count": None,
        "removed_exact_signature_count": None,
        "added_exact_signature_count": None,
        "removed_exact_signatures": [],
        "added_exact_signatures": [],
        "same_signature_observation_deltas": [],
        "details_truncated": False,
        "change_correspondence": {
            "status": "not-assessed",
            "changed_recipe_pairs": [],
            "interpretation": "No cross-capture recipe pairing was attempted.",
        },
    }


def _base_report(
    before: "GraphRecipeHealthView",
    after: "GraphRecipeHealthView",
    compatibility: dict[str, Any],
    *,
    max_recipes: int,
    max_recipe_deltas: int,
    max_resources: int,
    max_depth: int,
    max_nodes: int,
) -> dict[str, Any]:
    return {
        "format": RUNTIME_COMPARISON_FORMAT,
        "schema_version": 1,
        "before": _comparison_context(before),
        "after": _comparison_context(after),
        "compatibility": compatibility,
        "analysis_model": {
            "kind": "bounded-observed-runtime-recipe-graph-delta",
            "exact_signature_identity": "recipe-map + semantic-sha256 + duplicate-ordinal",
            "resource_exposure_rule": "a resource is newly exposed only when its complete observed finite producer set changes from nonempty to empty",
            "recipe_exposure_rule": "a candidate recipe is structurally exposed when one exact selector accepts only exposed resources",
            "claim_boundary": "observed finite-recipe structure and definition references; not player reachability",
        },
        "bounds": {
            "max_recipes_per_graph": max_recipes,
            "max_recipe_delta_details": max_recipe_deltas,
            "max_resource_deltas": max_resources,
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "visited_node_count": 0,
        },
        "recipe_signatures": _empty_signature_comparison("not-compared"),
        "resource_flow_deltas": {
            "status": "not-compared",
            "deltas": [],
            "newly_without_observed_finite_producers": [],
        },
        "propagation": {
            "status": "not-compared",
            "at_risk_resources": [],
            "at_risk_recipes": [],
        },
        "cycle_signals": {
            "status": "not-compared",
            "introduced_with_added_recipes": [],
            "introduced_with_activated_same_signatures": [],
            "removed_with_removed_recipes": [],
            "removed_with_deactivated_same_signatures": [],
            "remaining_producer_dependency_cycle_signals": [],
        },
        "progression_signals": {
            "status": "not-compared",
            "direct_requirement_link_deltas": [],
            "exposed_requirements_after": [],
            "structural_prerequisite_dependents_after": [],
            "prerequisite_cycles_after": [],
            "interpretation": "definition references and prerequisite structure are not player or task execution",
        },
        "frontiers": [],
        "unknowns": [],
        "evidence_gaps": [
            {
                "code": "comparison-attribution-not-established",
                "message": "The graph pair observes differences but does not by itself prove which source edit caused them.",
            },
            {
                "code": "non-recipe-acquisition-not-assessed",
                "message": "World generation, loot, trade, inventory, commands, and other acquisition domains are not finite recipe producers.",
            },
            {
                "code": "dynamic-recipes-not-executed",
                "message": "Procedural and contextual recipe rules were not invoked during comparison.",
            },
            {
                "code": "player-reachability-not-assessed",
                "message": "Recipe edges and quest definitions do not establish that a player can or cannot reach a state.",
            },
            {
                "code": "task-execution-not-invoked",
                "message": "BetterQuesting matching, completion, and reward execution were not invoked.",
            },
            *(
                {**gap, "side": side}
                for side, view in (("before", before), ("after", after))
                for gap in view._evidence_gaps(role="recipe")
                if gap["code"] == "graph-projection-limitations"
            ),
        ],
        "summary": {
            "comparison_state": "incomparable",
            "removed_exact_signature_count": None,
            "added_exact_signature_count": None,
            "newly_exposed_resource_candidate_count": None,
            "at_risk_recipe_candidate_count": None,
            "quest_requirement_exposure_count": None,
            "introduced_cycle_signal_count": None,
            "remaining_producer_dependency_cycle_signal_count": None,
            "changed_recipe_pair_count": 0,
            "truncated": False,
        },
    }


def _signature(node: Mapping[str, Any]) -> tuple[str, str, int] | None:
    properties = node["properties"]
    recipe_map = properties.get("recipe_map")
    semantic_sha256 = properties.get("semantic_sha256")
    duplicate_ordinal = properties.get("duplicate_ordinal")
    if (
        type(recipe_map) is not str
        or not recipe_map
        or type(semantic_sha256) is not str
        or len(semantic_sha256) != 64
        or any(character not in _HEX for character in semantic_sha256)
        or type(duplicate_ordinal) is not int
        or duplicate_ordinal < 0
    ):
        return None
    if node["semantic_key"] != (
        f"{recipe_map}|{semantic_sha256}|{duplicate_ordinal}"
    ):
        return None
    return recipe_map, semantic_sha256, duplicate_ordinal


def _load_recipes(
    view: "GraphRecipeHealthView", *, maximum: int
) -> tuple[dict[tuple[str, str, int], dict[str, Any]] | None, str | None]:
    declared = view.kinds.get(_RECIPE_KIND, 0)
    if declared > maximum:
        return None, "recipe-scan-bound"
    rows = view.query.connection.execute(
        "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
        "WHERE kind=? ORDER BY semantic_key,id LIMIT ?",
        (_RECIPE_KIND, maximum + 1),
    )
    recipes: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        if len(recipes) >= maximum:
            return None, "recipe-scan-bound"
        node = view.query._node_row(row)
        signature = _signature(node)
        if signature is None:
            return None, "invalid-runtime-recipe-signature"
        if signature in recipes:
            return None, "duplicate-runtime-recipe-signature"
        recipes[signature] = node
    if len(recipes) != declared:
        return None, "recipe-count-mismatch"
    return recipes, None


def _changed_fields(
    before: Mapping[str, Any], after: Mapping[str, Any], ignored: set[str]
) -> dict[str, dict[str, Any]]:
    return {
        field: {"before": before.get(field), "after": after.get(field)}
        for field in sorted(set(before) | set(after))
        if field not in ignored and before.get(field) != after.get(field)
    }


def _placeholders(values: Sequence[str]) -> str:
    return ",".join("?" for _ in values)


def _limited_rows(
    view: "GraphRecipeHealthView",
    sql: str,
    parameters: Sequence[object],
    *,
    maximum: int,
) -> tuple[list[Sequence[Any]], bool]:
    rows = list(view.query.connection.execute(sql, [*parameters, maximum + 1]))
    return rows[:maximum], len(rows) > maximum


def _direct_resources(
    view: "GraphRecipeHealthView",
    side: str,
    recipe_id: str,
    budget: _TraversalBudget,
    *,
    phase: str,
) -> tuple[set[str], set[str], bool]:
    if not budget.admit(side, recipe_id, phase=phase):
        return set(), set(), True
    remaining = max(1, budget.maximum - len(budget.seen))
    output_relations = sorted(_OUTPUT_RELATIONS)
    output_rows, output_truncated = _limited_rows(
        view,
        "SELECT t.id FROM edges e JOIN nodes s ON s.node_key=e.source_node "
        "JOIN nodes t ON t.node_key=e.target_node WHERE s.id=? "
        f"AND e.relation IN ({_placeholders(output_relations)}) "
        "ORDER BY t.id,e.edge_key LIMIT ?",
        [recipe_id, *output_relations],
        maximum=remaining,
    )
    outputs: set[str] = set()
    truncated = output_truncated
    for row in output_rows:
        node_id = str(row[0])
        if budget.admit(
            side,
            node_id,
            phase=phase,
            from_node_id=recipe_id,
            relation="recipe-output",
        ):
            outputs.add(node_id)
        else:
            truncated = True

    selectors, input_truncated = _selector_resources(
        view, side, recipe_id, budget, phase=phase
    )
    inputs = {resource_id for selector in selectors
              for resource_id in selector["accepted_resource_ids"]}
    truncated = truncated or input_truncated
    if truncated:
        budget.frontier(
            "relation-bound",
            phase=phase,
            side=side,
            from_node_id=recipe_id,
            relation="direct-recipe-io",
            omitted_at_least=1,
        )
    return inputs, outputs, truncated


def _portfolio(
    view: "GraphRecipeHealthView",
    side: str,
    resource_id: str,
    budget: _TraversalBudget,
    *,
    phase: str,
) -> dict[str, Any]:
    node = view._node(resource_id)
    if node is None:
        return {
            "present": False,
            "resource": None,
            "producer_recipe_ids": [],
            "related_producer_recipe_ids": [],
            "producer_availability_unknown_recipe_ids": [],
            "consumer_recipe_ids": [],
            "related_consumer_recipe_ids": [],
            "consumer_availability_unknown_recipe_ids": [],
            "availability_complete": True,
            "truncated": False,
        }
    if not budget.admit(side, resource_id, phase=phase):
        return {
            "present": True,
            "resource": _node_summary(node),
            "producer_recipe_ids": [],
            "related_producer_recipe_ids": [],
            "producer_availability_unknown_recipe_ids": [],
            "consumer_recipe_ids": [],
            "related_consumer_recipe_ids": [],
            "consumer_availability_unknown_recipe_ids": [],
            "availability_complete": False,
            "truncated": True,
        }
    maximum = max(1, budget.maximum - len(budget.seen))
    outputs = sorted(_OUTPUT_RELATIONS)
    producer_rows, producer_truncated = _limited_rows(
        view,
        "SELECT recipe.id,recipe.properties_json FROM edges e "
        "JOIN nodes recipe ON recipe.node_key=e.source_node "
        "JOIN nodes resource ON resource.node_key=e.target_node "
        "WHERE resource.id=? AND recipe.kind=? "
        f"AND e.relation IN ({_placeholders(outputs)}) "
        "ORDER BY recipe.id,e.edge_key LIMIT ?",
        [resource_id, _RECIPE_KIND, *outputs],
        maximum=maximum,
    )
    related_producers: set[str] = set()
    producers: set[str] = set()
    producer_unknown: set[str] = set()
    truncated = producer_truncated
    for row in producer_rows:
        recipe_id = str(row[0])
        related_producers.add(recipe_id)
        if budget.admit(
            side,
            recipe_id,
            phase=phase,
            from_node_id=resource_id,
            relation="producer",
        ):
            properties = json.loads(row[1])
            if properties.get("lookup_active") is True:
                producers.add(recipe_id)
            elif properties.get("lookup_active") is not False:
                producer_unknown.add(recipe_id)
        else:
            truncated = True

    maximum = max(1, budget.maximum - len(budget.seen))
    inputs = sorted(_INPUT_RELATIONS)
    selectors = sorted(_SELECTOR_RELATIONS)
    consumer_rows, consumer_truncated = _limited_rows(
        view,
        "SELECT recipe.id,selector.id,recipe.properties_json FROM edges accepts "
        "JOIN nodes resource ON resource.node_key=accepts.target_node "
        "JOIN nodes selector ON selector.node_key=accepts.source_node "
        "JOIN edges owns ON owns.target_node=selector.node_key "
        "JOIN nodes recipe ON recipe.node_key=owns.source_node "
        "WHERE resource.id=? AND recipe.kind=? "
        f"AND accepts.relation IN ({_placeholders(inputs)}) "
        f"AND owns.relation IN ({_placeholders(selectors)}) "
        "ORDER BY recipe.id,selector.id,accepts.edge_key LIMIT ?",
        [resource_id, _RECIPE_KIND, *inputs, *selectors],
        maximum=maximum,
    )
    related_consumers: set[str] = set()
    consumers: set[str] = set()
    consumer_unknown: set[str] = set()
    truncated = truncated or consumer_truncated
    for row in consumer_rows:
        recipe_id, selector_id = str(row[0]), str(row[1])
        related_consumers.add(recipe_id)
        if not budget.admit(
            side,
            selector_id,
            phase=phase,
            from_node_id=resource_id,
            relation="consumer-selector",
        ):
            truncated = True
            continue
        if budget.admit(
            side,
            recipe_id,
            phase=phase,
            from_node_id=selector_id,
            relation="consumer-recipe",
        ):
            properties = json.loads(row[2])
            if properties.get("lookup_active") is True:
                consumers.add(recipe_id)
            elif properties.get("lookup_active") is not False:
                consumer_unknown.add(recipe_id)
        else:
            truncated = True
    if truncated:
        budget.frontier(
            "relation-bound",
            phase=phase,
            side=side,
            from_node_id=resource_id,
            relation="producer-consumer-portfolio",
            omitted_at_least=1,
        )
    return {
        "present": True,
        "resource": _node_summary(node),
        "producer_recipe_ids": sorted(producers),
        "related_producer_recipe_ids": sorted(related_producers),
        "producer_availability_unknown_recipe_ids": sorted(producer_unknown),
        "consumer_recipe_ids": sorted(consumers),
        "related_consumer_recipe_ids": sorted(related_consumers),
        "consumer_availability_unknown_recipe_ids": sorted(consumer_unknown),
        "availability_complete": not truncated
        and not producer_unknown
        and not consumer_unknown,
        "truncated": truncated,
    }


def _selector_resources(
    view: "GraphRecipeHealthView",
    side: str,
    recipe_id: str,
    budget: _TraversalBudget,
    *,
    phase: str,
) -> tuple[list[dict[str, Any]], bool]:
    selector_relations = sorted(_SELECTOR_RELATIONS)
    input_relations = sorted(_INPUT_RELATIONS)
    maximum = max(1, budget.maximum - len(budget.seen))
    rows, truncated = _limited_rows(
        view,
        "SELECT selector.id,resource.id,selector.properties_json FROM edges owns "
        "JOIN nodes recipe ON recipe.node_key=owns.source_node "
        "JOIN nodes selector ON selector.node_key=owns.target_node "
        "LEFT JOIN edges accepts ON accepts.source_node=selector.node_key "
        f"AND accepts.relation IN ({_placeholders(input_relations)}) "
        "LEFT JOIN nodes resource ON resource.node_key=accepts.target_node "
        "WHERE recipe.id=? "
        f"AND owns.relation IN ({_placeholders(selector_relations)}) "
        "ORDER BY selector.id,resource.id LIMIT ?",
        [*input_relations, recipe_id, *selector_relations],
        maximum=maximum,
    )
    by_selector: dict[str, set[str]] = {}
    for row in rows:
        selector_id = str(row[0])
        if not budget.admit(
            side,
            selector_id,
            phase=phase,
            from_node_id=recipe_id,
            relation="selector",
        ):
            truncated = True
            continue
        if json.loads(str(row[2])).get("acceptance_complete", True) is not True:
            truncated = True
            budget.frontier(
                "relation-bound", phase=phase, side=side,
                from_node_id=selector_id, relation="selector-acceptance-incomplete",
                omitted_at_least=1,
            )
            continue
        if row[1] is None:
            continue
        resource_id = str(row[1])
        if not budget.admit(
            side,
            resource_id,
            phase=phase,
            from_node_id=selector_id,
            relation="accepted-resource",
        ):
            truncated = True
            continue
        by_selector.setdefault(selector_id, set()).add(resource_id)
    if truncated:
        budget.frontier(
            "relation-bound",
            phase=phase,
            side=side,
            from_node_id=recipe_id,
            relation="selector-alternatives",
            omitted_at_least=1,
        )
    return [
        {"selector_id": selector, "accepted_resource_ids": sorted(resources)}
        for selector, resources in sorted(by_selector.items())
    ], truncated


def _propagate_after(
    view: "GraphRecipeHealthView",
    seed_resource_ids: Sequence[str],
    budget: _TraversalBudget,
    *,
    max_depth: int,
) -> dict[str, Any]:
    at_risk_resources: dict[str, int] = {}
    missing_seeds: list[str] = []
    for resource_id in sorted(set(seed_resource_ids)):
        if view._node(resource_id) is None:
            missing_seeds.append(resource_id)
        else:
            at_risk_resources[resource_id] = 0
    at_risk_recipes: dict[str, dict[str, Any]] = {}
    frontier = sorted(at_risk_resources)
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        candidates: set[str] = set()
        for resource_id in frontier:
            portfolio = _portfolio(
                view,
                "after",
                resource_id,
                budget,
                phase="candidate-propagation-consumers",
            )
            if portfolio["availability_complete"]:
                candidates.update(portfolio["consumer_recipe_ids"])
        newly_risky: list[str] = []
        for recipe_id in sorted(candidates):
            if recipe_id in at_risk_recipes:
                continue
            selectors, truncated = _selector_resources(
                view,
                "after",
                recipe_id,
                budget,
                phase="candidate-propagation-selectors",
            )
            if truncated:
                continue
            blocked = [
                row
                for row in selectors
                if row["accepted_resource_ids"]
                and set(row["accepted_resource_ids"]) <= set(at_risk_resources)
            ]
            if blocked:
                node = view._node(recipe_id)
                if node is not None:
                    at_risk_recipes[recipe_id] = {
                        "recipe": _node_summary(node),
                        "depth": depth,
                        "blocked_selectors": blocked,
                    }
                    newly_risky.append(recipe_id)
        next_resources: list[str] = []
        for recipe_id in newly_risky:
            _, outputs, truncated = _direct_resources(
                view,
                "after",
                recipe_id,
                budget,
                phase="candidate-propagation-outputs",
            )
            if truncated:
                continue
            for resource_id in sorted(outputs):
                if resource_id in at_risk_resources:
                    continue
                portfolio = _portfolio(
                    view,
                    "after",
                    resource_id,
                    budget,
                    phase="candidate-propagation-producers",
                )
                producers = set(portfolio["producer_recipe_ids"])
                if (
                    portfolio["availability_complete"]
                    and producers
                    and producers <= set(at_risk_recipes)
                ):
                    at_risk_resources[resource_id] = depth
                    next_resources.append(resource_id)
        frontier = sorted(set(next_resources))
    if frontier:
        budget.frontier(
            "depth-bound",
            phase="candidate-propagation",
            side="after",
            depth=max_depth,
            omitted_at_least=1,
        )
    return {
        "status": "truncated" if budget.frontiers else "complete-within-model",
        "seed_resources_absent_after": missing_seeds,
        "at_risk_resources": [
            {
                "resource": _node_summary(view._node(resource_id)),
                "depth": depth_value,
                "reason": (
                    "observed-finite-producer-set-became-empty"
                    if depth_value == 0
                    else "all-observed-finite-producers-are-structurally-exposed-candidates"
                ),
            }
            for resource_id, depth_value in sorted(at_risk_resources.items())
            if view._node(resource_id) is not None
        ],
        "at_risk_recipes": [at_risk_recipes[key] for key in sorted(at_risk_recipes)],
    }


def _cycle_signals_for_recipes(
    view: "GraphRecipeHealthView",
    side: str,
    recipe_ids: Sequence[str],
    budget: _TraversalBudget,
    *,
    max_depth: int,
    phase: str,
) -> list[dict[str, Any]]:
    signals: dict[tuple[str, ...], dict[str, Any]] = {}
    for recipe_id in sorted(set(recipe_ids)):
        root_inputs, root_outputs, truncated = _direct_resources(
            view, side, recipe_id, budget, phase=phase
        )
        if truncated or not root_inputs or not root_outputs:
            continue
        queue: list[tuple[str, int, tuple[str, ...]]] = [
            (resource_id, 0, (recipe_id, resource_id))
            for resource_id in sorted(root_outputs)
        ]
        expanded: set[tuple[str, int]] = set()
        while queue:
            resource_id, depth, path = queue.pop(0)
            if resource_id in root_inputs:
                cycle = (*path, recipe_id)
                signals[cycle] = {
                    "recipe_id": recipe_id,
                    "path": list(cycle),
                    "interpretation": "observed finite-recipe dependency cycle involving the changed membership occurrence",
                }
                continue
            state = (resource_id, depth)
            if state in expanded:
                continue
            expanded.add(state)
            portfolio = _portfolio(
                view, side, resource_id, budget, phase=phase
            )
            if portfolio["truncated"]:
                continue
            if depth >= max_depth:
                # A terminal resource has no omitted expansion. Merely reaching
                # the depth value does not make an otherwise complete scan partial.
                if any(
                    consumer_id not in path
                    for consumer_id in portfolio["consumer_recipe_ids"]
                ):
                    budget.frontier(
                        "depth-bound",
                        phase=phase,
                        side=side,
                        from_node_id=resource_id,
                        depth=max_depth,
                        omitted_at_least=1,
                    )
                continue
            for consumer_id in portfolio["consumer_recipe_ids"]:
                if consumer_id in path:
                    continue
                _, outputs, output_truncated = _direct_resources(
                    view, side, consumer_id, budget, phase=phase
                )
                if output_truncated:
                    continue
                for output_id in sorted(outputs):
                    queue.append(
                        (output_id, depth + 1, (*path, consumer_id, output_id))
                    )
    return [signals[key] for key in sorted(signals)]


def _quest_requirements(
    view: "GraphRecipeHealthView",
    side: str,
    resource_id: str,
    budget: _TraversalBudget,
    *,
    phase: str,
) -> tuple[list[dict[str, Any]], bool]:
    quest_relations = sorted(_QUEST_RESOURCE_RELATIONS)
    task_relations = sorted(_TASK_RESOURCE_RELATIONS)
    maximum = max(1, budget.maximum - len(budget.seen))
    rows, truncated = _limited_rows(
        view,
        "SELECT quest.id,task.id,occurrence.id,resource_edge.relation,"
        "resource_edge.semantic_key,resource_edge.properties_json,resource_edge.evidence_json "
        "FROM nodes resource "
        "JOIN edges resource_edge ON resource_edge.target_node=resource.node_key "
        "JOIN nodes occurrence ON occurrence.node_key=resource_edge.source_node "
        "JOIN edges task_edge ON task_edge.target_node=occurrence.node_key "
        "JOIN nodes task ON task.node_key=task_edge.source_node "
        "JOIN edges owner_edge ON owner_edge.target_node=task.node_key "
        "JOIN nodes quest ON quest.node_key=owner_edge.source_node "
        "WHERE resource.id=? "
        f"AND resource_edge.relation IN ({_placeholders(quest_relations)}) "
        f"AND task_edge.relation IN ({_placeholders(task_relations)}) "
        "AND owner_edge.relation='owns-progression-task' "
        "ORDER BY quest.id,task.id,occurrence.id LIMIT ?",
        [resource_id, *quest_relations, *task_relations],
        maximum=maximum,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        quest_id, task_id, occurrence_id = str(row[0]), str(row[1]), str(row[2])
        admitted = all(
            budget.admit(
                side,
                node_id,
                phase=phase,
                from_node_id=resource_id,
                relation="quest-resource-reference",
            )
            for node_id in (occurrence_id, task_id, quest_id)
        )
        if not admitted:
            truncated = True
            continue
        quest = view._node(quest_id)
        task = view._node(task_id)
        occurrence = view._node(occurrence_id)
        if quest is None or task is None or occurrence is None:
            truncated = True
            continue
        result.append(
            {
                "identity": {
                    "quest_id": quest_id,
                    "task_id": task_id,
                    "requirement_occurrence_id": occurrence_id,
                    "resource_id": resource_id,
                },
                "quest": _node_summary(quest),
                "task": _node_summary(task),
                "requirement_occurrence": _node_summary(occurrence),
                "resource_edge": {
                    "relation": row[3],
                    "semantic_key": row[4],
                    "properties": json.loads(row[5]),
                    "evidence": json.loads(row[6]),
                },
            }
        )
    if truncated:
        budget.frontier(
            "relation-bound",
            phase=phase,
            side=side,
            from_node_id=resource_id,
            relation="quest-resource-reference",
            omitted_at_least=1,
        )
    return result, truncated


def _quest_dependents(
    view: "GraphRecipeHealthView",
    quest_ids: Iterable[str],
    budget: _TraversalBudget,
    *,
    max_depth: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queue = [(quest_id, 0, (quest_id,)) for quest_id in sorted(set(quest_ids))]
    dependents: dict[tuple[str, str], dict[str, Any]] = {}
    cycles: dict[tuple[str, ...], dict[str, Any]] = {}
    while queue:
        target_id, depth, path = queue.pop(0)
        if depth >= max_depth:
            budget.frontier(
                "depth-bound",
                phase="quest-prerequisite-dependents",
                side="after",
                from_node_id=target_id,
                depth=max_depth,
                omitted_at_least=1,
            )
            continue
        maximum = max(1, budget.maximum - len(budget.seen))
        rows, truncated = _limited_rows(
            view,
            "SELECT dependent.id,occurrence.id FROM nodes target "
            "JOIN edges target_edge ON target_edge.target_node=target.node_key "
            "JOIN nodes occurrence ON occurrence.node_key=target_edge.source_node "
            "JOIN edges owner_edge ON owner_edge.target_node=occurrence.node_key "
            "JOIN nodes dependent ON dependent.node_key=owner_edge.source_node "
            "WHERE target.id=? "
            "AND target_edge.relation='targets-progression-prerequisite' "
            "AND owner_edge.relation='owns-progression-prerequisite' "
            "ORDER BY dependent.id,occurrence.id LIMIT ?",
            [target_id],
            maximum=maximum,
        )
        if truncated:
            budget.frontier(
                "relation-bound",
                phase="quest-prerequisite-dependents",
                side="after",
                from_node_id=target_id,
                relation="quest-prerequisite",
                omitted_at_least=1,
            )
        for row in rows:
            dependent_id, occurrence_id = str(row[0]), str(row[1])
            if not budget.admit(
                "after",
                occurrence_id,
                phase="quest-prerequisite-dependents",
                from_node_id=target_id,
                relation="prerequisite-occurrence",
            ) or not budget.admit(
                "after",
                dependent_id,
                phase="quest-prerequisite-dependents",
                from_node_id=occurrence_id,
                relation="dependent-quest",
            ):
                continue
            next_path = (*path, dependent_id)
            if dependent_id in path:
                cycles[next_path] = {
                    "path": list(next_path),
                    "interpretation": "observed BetterQuesting prerequisite cycle",
                }
                continue
            dependent = view._node(dependent_id)
            occurrence = view._node(occurrence_id)
            if dependent is None or occurrence is None:
                continue
            key = (dependent_id, target_id)
            dependents[key] = {
                "quest": _node_summary(dependent),
                "depends_on_quest_id": target_id,
                "prerequisite_occurrence": _node_summary(occurrence),
                "depth": depth + 1,
            }
            queue.append((dependent_id, depth + 1, next_path))
    return (
        [dependents[key] for key in sorted(dependents)],
        [cycles[key] for key in sorted(cycles)],
    )


def compare_runtime_recipe_graphs(
    before: "GraphRecipeHealthView",
    after: "GraphRecipeHealthView",
    *,
    max_recipes: int = 100_000,
    max_recipe_deltas: int = 500,
    max_resources: int = 500,
    max_depth: int = 4,
    max_nodes: int = 2_000,
) -> dict[str, Any]:
    """Compare two verified V2 graph views without inferring recipe pairing."""

    from .view import GraphRecipeHealthView, RecipeHealthError

    if not isinstance(before, GraphRecipeHealthView) or not isinstance(
        after, GraphRecipeHealthView
    ):
        raise RecipeHealthError(
            "runtime recipe comparison requires two observed categorical graph views"
        )
    max_recipes = _bound(
        max_recipes, "maximum recipes per graph", minimum=1, maximum=250_000
    )
    max_recipe_deltas = _bound(
        max_recipe_deltas,
        "maximum recipe delta details",
        minimum=1,
        maximum=10_000,
    )
    max_resources = _bound(
        max_resources, "maximum resource deltas", minimum=1, maximum=10_000
    )
    max_depth = _bound(max_depth, "maximum depth", minimum=1, maximum=12)
    max_nodes = _bound(max_nodes, "maximum nodes", minimum=10, maximum=20_000)

    compatibility = _compatibility(before, after)
    report = _base_report(
        before,
        after,
        compatibility,
        max_recipes=max_recipes,
        max_recipe_deltas=max_recipe_deltas,
        max_resources=max_resources,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    if compatibility["state"] != "compatible":
        report["unknowns"] = [
            {
                "code": row["code"],
                "message": row["message"],
                **({"side": row["side"]} if "side" in row else {}),
            }
            for row in compatibility["blocking_differences"]
        ]
        return report

    before_recipes, before_error = _load_recipes(before, maximum=max_recipes)
    after_recipes, after_error = _load_recipes(after, maximum=max_recipes)
    if before_error is not None or after_error is not None:
        for side, code in (("before", before_error), ("after", after_error)):
            if code is None:
                continue
            if code == "recipe-scan-bound":
                report["frontiers"].append(
                    {
                        "kind": "recipe-scan-bound",
                        "phase": "exact-signature-membership",
                        "side": side,
                        "omitted_at_least": 1,
                    }
                )
            else:
                report["unknowns"].append(
                    {
                        "code": code,
                        "side": side,
                        "message": "The graph does not expose a valid exact runtime recipe signature set.",
                    }
                )
        report["summary"]["comparison_state"] = "not-compared"
        report["summary"]["truncated"] = bool(report["frontiers"])
        return report
    assert before_recipes is not None and after_recipes is not None

    before_keys = set(before_recipes)
    after_keys = set(after_recipes)
    unchanged = before_keys & after_keys
    removed = before_keys - after_keys
    added = after_keys - before_keys
    merged_deltas = sorted(
        [("removed", key) for key in removed] + [("added", key) for key in added],
        key=lambda row: (row[1], row[0]),
    )
    emitted = merged_deltas[:max_recipe_deltas]
    details_truncated = len(merged_deltas) > len(emitted)
    emitted_removed = [key for kind, key in emitted if kind == "removed"]
    emitted_added = [key for kind, key in emitted if kind == "added"]
    if details_truncated:
        report["frontiers"].append(
            {
                "kind": "recipe-delta-detail-bound",
                "phase": "exact-signature-membership",
                "omitted_at_least": len(merged_deltas) - len(emitted),
            }
        )

    observation_deltas: list[dict[str, Any]] = []
    observation_delta_keys: list[tuple[str, str, int]] = []
    observation_delta_count = 0
    for key in sorted(unchanged):
        left = before_recipes[key]
        right = after_recipes[key]
        fields = _changed_fields(
            left["properties"],
            right["properties"],
            {"recipe_map", "semantic_sha256", "duplicate_ordinal"},
        )
        if left["evidence"] != right["evidence"]:
            fields["evidence"] = {
                "before": left["evidence"],
                "after": right["evidence"],
            }
            fields = dict(sorted(fields.items()))
        if fields:
            observation_delta_count += 1
            if len(observation_deltas) < max_recipe_deltas:
                observation_delta_keys.append(key)
                observation_deltas.append(
                    {
                        "signature": {
                            "recipe_map": key[0],
                            "semantic_sha256": key[1],
                            "duplicate_ordinal": key[2],
                        },
                        "selection_id": left["id"],
                        "changed_fields": fields,
                        "interpretation": "same exact recipe signature with changed membership or projection metadata",
                    }
                )
    if observation_delta_count > len(observation_deltas):
        report["frontiers"].append(
            {
                "kind": "same-signature-observation-detail-bound",
                "phase": "exact-signature-membership",
                "omitted_at_least": observation_delta_count
                - len(observation_deltas),
            }
        )
    report["recipe_signatures"] = {
        "status": "compared",
        "before_count": len(before_recipes),
        "after_count": len(after_recipes),
        "unchanged_exact_signature_count": len(unchanged),
        "removed_exact_signature_count": len(removed),
        "added_exact_signature_count": len(added),
        "removed_exact_signatures": [
            _node_summary(before_recipes[key]) for key in emitted_removed
        ],
        "added_exact_signatures": [
            _node_summary(after_recipes[key]) for key in emitted_added
        ],
        "same_signature_observation_delta_count": observation_delta_count,
        "same_signature_observation_deltas": observation_deltas,
        "details_truncated": details_truncated,
        "change_correspondence": {
            "status": (
                "unavailable-no-cross-capture-occurrence-identity"
                if removed and added
                else "not-needed"
            ),
            "changed_recipe_pairs": [],
            "unpaired_removed_exact_signature_count": len(removed),
            "unpaired_added_exact_signature_count": len(added),
            "interpretation": "Exact signatures are immutable. The capture protocol has no stable cross-launch recipe occurrence key, so Atlas does not infer changed pairs from additions and removals.",
        },
    }
    if removed and added:
        report["unknowns"].append(
            {
                "code": "changed-recipe-correspondence-unavailable",
                "message": "Removed and added exact signatures were not paired as changed recipes because no cross-capture occurrence identity is retained.",
            }
        )

    budget = _TraversalBudget(max_nodes)
    touched_resources: set[str] = set()
    removed_recipe_ids: list[str] = []
    added_recipe_ids: list[str] = []
    activated_recipe_ids: list[str] = []
    deactivated_recipe_ids: list[str] = []
    for key in emitted_removed:
        recipe_id = before_recipes[key]["id"]
        lookup_active = before_recipes[key]["properties"].get("lookup_active")
        if lookup_active is True:
            removed_recipe_ids.append(recipe_id)
        elif lookup_active is not False:
            report["unknowns"].append(
                {
                    "code": "recipe-lookup-activity-unavailable",
                    "side": "before",
                    "subject_id": recipe_id,
                    "message": "Non-boolean lookup activity prevents finite recipe availability classification.",
                }
            )
        inputs, outputs, _ = _direct_resources(
            before,
            "before",
            recipe_id,
            budget,
            phase="removed-recipe-io",
        )
        touched_resources.update(inputs | outputs)
    for key in emitted_added:
        recipe_id = after_recipes[key]["id"]
        lookup_active = after_recipes[key]["properties"].get("lookup_active")
        if lookup_active is True:
            added_recipe_ids.append(recipe_id)
        elif lookup_active is not False:
            report["unknowns"].append(
                {
                    "code": "recipe-lookup-activity-unavailable",
                    "side": "after",
                    "subject_id": recipe_id,
                    "message": "Non-boolean lookup activity prevents finite recipe availability classification.",
                }
            )
        inputs, outputs, _ = _direct_resources(
            after,
            "after",
            recipe_id,
            budget,
            phase="added-recipe-io",
        )
        touched_resources.update(inputs | outputs)
    for key in observation_delta_keys:
        before_recipe = before_recipes[key]
        after_recipe = after_recipes[key]
        before_inputs, before_outputs, _ = _direct_resources(
            before,
            "before",
            before_recipe["id"],
            budget,
            phase="same-signature-observation-io",
        )
        after_inputs, after_outputs, _ = _direct_resources(
            after,
            "after",
            after_recipe["id"],
            budget,
            phase="same-signature-observation-io",
        )
        touched_resources.update(
            before_inputs | before_outputs | after_inputs | after_outputs
        )
        before_active = before_recipe["properties"].get("lookup_active")
        after_active = after_recipe["properties"].get("lookup_active")
        if before_active is False and after_active is True:
            activated_recipe_ids.append(after_recipe["id"])
        elif before_active is True and after_active is False:
            deactivated_recipe_ids.append(before_recipe["id"])
    ordered_resources = sorted(touched_resources)
    if len(ordered_resources) > max_resources:
        budget.frontier(
            "resource-delta-bound",
            phase="resource-flow-delta",
            omitted_at_least=len(ordered_resources) - max_resources,
        )
        ordered_resources = ordered_resources[:max_resources]

    resource_deltas: list[dict[str, Any]] = []
    new_empty: list[str] = []
    remaining_producer_resources: dict[str, set[str]] = {}
    for resource_id in ordered_resources:
        left = _portfolio(
            before,
            "before",
            resource_id,
            budget,
            phase="resource-flow-delta",
        )
        right = _portfolio(
            after,
            "after",
            resource_id,
            budget,
            phase="resource-flow-delta",
        )
        for side, portfolio in (("before", left), ("after", right)):
            unknown_activity = sorted(
                set(portfolio["producer_availability_unknown_recipe_ids"])
                | set(portfolio["consumer_availability_unknown_recipe_ids"])
            )
            if unknown_activity:
                report["unknowns"].append(
                    {
                        "code": "recipe-lookup-activity-unavailable",
                        "side": side,
                        "subject_id": resource_id,
                        "recipe_ids": unknown_activity,
                        "message": "Non-boolean lookup activity prevents finite producer or consumer availability classification.",
                    }
                )
        left_producers = set(left["producer_recipe_ids"])
        right_producers = set(right["producer_recipe_ids"])
        left_related_producers = set(left["related_producer_recipe_ids"])
        right_related_producers = set(right["related_producer_recipe_ids"])
        left_consumers = set(left["consumer_recipe_ids"])
        right_consumers = set(right["consumer_recipe_ids"])
        left_related_consumers = set(left["related_consumer_recipe_ids"])
        right_related_consumers = set(right["related_consumer_recipe_ids"])
        if (
            left["present"] == right["present"]
            and left_producers == right_producers
            and left_related_producers == right_related_producers
            and left_consumers == right_consumers
            and left_related_consumers == right_related_consumers
        ):
            continue
        became_empty = (
            left["availability_complete"]
            and right["availability_complete"]
            and bool(left_producers)
            and not right_producers
        )
        if became_empty:
            new_empty.append(resource_id)
        if (
            left["availability_complete"]
            and right["availability_complete"]
            and left_producers - right_producers
        ):
            for producer_id in right_producers:
                remaining_producer_resources.setdefault(producer_id, set()).add(
                    resource_id
                )
        resource_deltas.append(
            {
                "resource_id": resource_id,
                "before": left,
                "after": right,
                "removed_producer_recipe_ids": sorted(left_producers - right_producers),
                "added_producer_recipe_ids": sorted(right_producers - left_producers),
                "removed_related_producer_recipe_ids": sorted(
                    left_related_producers - right_related_producers
                ),
                "added_related_producer_recipe_ids": sorted(
                    right_related_producers - left_related_producers
                ),
                "removed_consumer_recipe_ids": sorted(left_consumers - right_consumers),
                "added_consumer_recipe_ids": sorted(right_consumers - left_consumers),
                "removed_related_consumer_recipe_ids": sorted(
                    left_related_consumers - right_related_consumers
                ),
                "added_related_consumer_recipe_ids": sorted(
                    right_related_consumers - left_related_consumers
                ),
                "newly_without_observed_finite_producers": became_empty,
                "interpretation": "finite recipe producer and consumer membership only",
            }
        )
    report["resource_flow_deltas"] = {
        "status": "truncated" if budget.frontiers else "complete-within-bounds",
        "deltas": resource_deltas,
        "newly_without_observed_finite_producers": sorted(new_empty),
    }

    propagation = _propagate_after(
        after, new_empty, budget, max_depth=max_depth
    )
    report["propagation"] = propagation
    introduced_with_added = _cycle_signals_for_recipes(
        after,
        "after",
        added_recipe_ids,
        budget,
        max_depth=max_depth,
        phase="introduced-cycle-scan",
    )
    introduced_with_activated = _cycle_signals_for_recipes(
        after,
        "after",
        activated_recipe_ids,
        budget,
        max_depth=max_depth,
        phase="activated-cycle-scan",
    )
    removed_with_removed = _cycle_signals_for_recipes(
        before,
        "before",
        removed_recipe_ids,
        budget,
        max_depth=max_depth,
        phase="removed-cycle-scan",
    )
    removed_with_deactivated = _cycle_signals_for_recipes(
        before,
        "before",
        deactivated_recipe_ids,
        budget,
        max_depth=max_depth,
        phase="deactivated-cycle-scan",
    )
    remaining_producer_cycles = [
        {
            **signal,
            "affected_resource_ids": sorted(
                remaining_producer_resources[signal["recipe_id"]]
            ),
            "viability_effect": "unknown",
            "interpretation": "observed dependency cycle involving a remaining finite producer for a resource whose active producer set lost members; this does not establish independent supply or unavailability",
        }
        for signal in _cycle_signals_for_recipes(
            after,
            "after",
            sorted(remaining_producer_resources),
            budget,
            max_depth=max_depth,
            phase="remaining-producer-cycle-scan",
        )
    ]
    report["cycle_signals"] = {
        "status": "truncated" if budget.frontiers else "complete-within-bounds",
        "introduced_with_added_recipes": introduced_with_added,
        "introduced_with_activated_same_signatures": introduced_with_activated,
        "removed_with_removed_recipes": removed_with_removed,
        "removed_with_deactivated_same_signatures": removed_with_deactivated,
        "remaining_producer_dependency_cycle_signals": remaining_producer_cycles,
        "interpretation": "bounded dependency-cycle signals involving changed exact recipe membership or remaining producers for resources that lost active producers",
    }

    direct_link_deltas: list[dict[str, Any]] = []
    exposed_after: list[dict[str, Any]] = []
    exposed_quest_ids: set[str] = set()
    quest_truncated = False
    for resource_id in ordered_resources:
        before_rows, before_truncated = _quest_requirements(
            before,
            "before",
            resource_id,
            budget,
            phase="quest-resource-delta",
        )
        after_rows, after_truncated = _quest_requirements(
            after,
            "after",
            resource_id,
            budget,
            phase="quest-resource-delta",
        )
        quest_truncated = quest_truncated or before_truncated or after_truncated
        before_by_id = {
            _canonical_text(row["identity"]): row for row in before_rows
        }
        after_by_id = {_canonical_text(row["identity"]): row for row in after_rows}
        removed_links = [
            before_by_id[key]
            for key in sorted(set(before_by_id) - set(after_by_id))
        ]
        added_links = [
            after_by_id[key]
            for key in sorted(set(after_by_id) - set(before_by_id))
        ]
        if removed_links or added_links:
            direct_link_deltas.append(
                {
                    "resource_id": resource_id,
                    "removed_requirement_links": removed_links,
                    "added_requirement_links": added_links,
                }
            )
        if resource_id in new_empty:
            for row in after_rows:
                exposed_after.append(
                    {
                        **row,
                        "resource_exposure": "observed-finite-producer-set-became-empty",
                    }
                )
                exposed_quest_ids.add(row["identity"]["quest_id"])
    dependents, quest_cycles = _quest_dependents(
        after, exposed_quest_ids, budget, max_depth=max_depth
    )
    quest_relations_available = any(
        after.relations.get(relation, 0) for relation in _QUEST_RESOURCE_RELATIONS
    )
    report["progression_signals"] = {
        "status": (
            "truncated"
            if quest_truncated
            else "observed-definition-references"
            if quest_relations_available
            else "evidence-unavailable"
        ),
        "direct_requirement_link_deltas": direct_link_deltas,
        "exposed_requirements_after": exposed_after,
        "structural_prerequisite_dependents_after": dependents,
        "prerequisite_cycles_after": quest_cycles,
        "interpretation": "definition references and prerequisite structure are not player or task execution",
    }
    if not quest_relations_available:
        report["evidence_gaps"].append(
            {
                "code": "quest-definition-evidence-unavailable",
                "message": "The after graph publishes no supported exact quest resource relation.",
            }
        )

    report["frontiers"] = sorted(
        [*report["frontiers"], *budget.frontiers],
        key=lambda row: (
            row["kind"],
            str(row.get("phase", "")),
            str(row.get("side", "")),
            str(row.get("from_node_id", "")),
        ),
    )
    report["bounds"]["visited_node_count"] = len(budget.seen)
    report["summary"] = {
        "comparison_state": (
            "truncated" if report["frontiers"] else "complete-within-model"
        ),
        "removed_exact_signature_count": len(removed),
        "added_exact_signature_count": len(added),
        "newly_exposed_resource_candidate_count": len(new_empty),
        "at_risk_recipe_candidate_count": len(propagation["at_risk_recipes"]),
        "quest_requirement_exposure_count": len(exposed_after),
        "introduced_cycle_signal_count": len(
            report["cycle_signals"]["introduced_with_added_recipes"]
        )
        + len(
            report["cycle_signals"][
                "introduced_with_activated_same_signatures"
            ]
        ),
        "remaining_producer_dependency_cycle_signal_count": len(
            remaining_producer_cycles
        ),
        "changed_recipe_pair_count": 0,
        "truncated": bool(report["frontiers"]),
    }
    return report


__all__ = ["RUNTIME_COMPARISON_FORMAT", "compare_runtime_recipe_graphs"]
