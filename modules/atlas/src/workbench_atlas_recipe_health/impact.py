"""Bounded counterfactual recipe-impact projection over one observed graph.

The projection is deliberately narrower than reachability.  It asks which
finite recipe producers and consumers become structurally exposed when one
exact observed recipe is treated as unavailable.  Other acquisition domains,
inventory state, task execution, and player progression remain unknown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .view import GraphRecipeHealthView


IMPACT_FORMAT = "workbench-atlas-recipe-impact-report-v1"

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
_MACHINE_MAP_RELATIONS = frozenset(
    {"uses-recipe-map", "currently-selects-recipe-map"}
)
_QUEST_RESOURCE_RELATIONS = frozenset(
    {"observes-progression-item-variant", "requires-progression-fluid"}
)
_TASK_RESOURCE_RELATIONS = frozenset(
    {"has-progression-item-requirement", "has-progression-fluid-requirement"}
)


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": node["id"],
        "kind": node["kind"],
        "semantic_key": node["semantic_key"],
        "properties": node["properties"],
        "evidence": node["evidence"],
    }


def _edge_summary(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation": edge["relation"],
        "semantic_key": edge["semantic_key"],
        "properties": edge["properties"],
        "evidence": edge["evidence"],
    }


def _edge_node_summary(
    edge: dict[str, Any], node: dict[str, Any]
) -> dict[str, Any]:
    return {**_edge_summary(edge), "node": _node_summary(node)}


def _bound(value: object, label: str, *, minimum: int, maximum: int) -> int:
    from .view import RecipeHealthError

    if type(value) is not int or not minimum <= value <= maximum:
        raise RecipeHealthError(
            f"recipe-impact {label} is outside {minimum}..{maximum}"
        )
    return value


@dataclass
class _TraversalBudget:
    max_nodes: int
    selected_id: str
    seen: set[str] = field(default_factory=set)
    frontiers: list[dict[str, Any]] = field(default_factory=list)
    _frontier_keys: set[tuple[object, ...]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.seen.add(self.selected_id)

    def admit(
        self,
        node_id: str,
        *,
        from_node_id: str,
        relation: str,
        phase: str,
    ) -> bool:
        if node_id in self.seen:
            return True
        if len(self.seen) < self.max_nodes:
            self.seen.add(node_id)
            return True
        self.frontier(
            "node-bound",
            phase=phase,
            from_node_id=from_node_id,
            relation=relation,
            omitted_at_least=1,
        )
        return False

    def frontier(self, kind: str, **detail: Any) -> None:
        key = (
            kind,
            detail.get("phase"),
            detail.get("from_node_id"),
            detail.get("relation"),
            detail.get("depth"),
        )
        if key in self._frontier_keys:
            return
        self._frontier_keys.add(key)
        self.frontiers.append({"kind": kind, **detail})


class _ImpactBuilder:
    def __init__(
        self,
        view: "GraphRecipeHealthView",
        recipe: dict[str, Any],
        *,
        max_depth: int,
        max_nodes: int,
    ) -> None:
        self.view = view
        self.recipe = recipe
        self.max_depth = max_depth
        self.budget = _TraversalBudget(max_nodes, recipe["id"])
        self.unknowns: list[dict[str, Any]] = []
        self._unknown_keys: set[tuple[str, str | None]] = set()

    def unknown(self, code: str, message: str, subject_id: str | None = None) -> None:
        key = (code, subject_id)
        if key in self._unknown_keys:
            return
        self._unknown_keys.add(key)
        row: dict[str, Any] = {"code": code, "message": message}
        if subject_id is not None:
            row["subject_id"] = subject_id
        self.unknowns.append(row)

    def _limited_edges(
        self,
        node_id: str,
        relations: frozenset[str],
        *,
        direction: str,
        phase: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        ordered = sorted(relations)
        if direction == "from":
            endpoint = "s.id=?"
            order_by = "e.relation,t.id,e.edge_key"
        else:
            endpoint = "t.id=?"
            order_by = "e.relation,s.id,e.edge_key"
        rows = list(
            self.view.query.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
                "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
                "JOIN nodes t ON t.node_key=e.target_node WHERE "
                + endpoint
                + " AND e.relation IN ("
                + ",".join("?" for _ in ordered)
                + ") ORDER BY "
                + order_by
                + " LIMIT ?",
                [node_id, *ordered, self.budget.max_nodes + 1],
            )
        )
        truncated = len(rows) > self.budget.max_nodes
        edges = [self.view.query._edge_row(row) for row in rows[: self.budget.max_nodes]]
        if truncated:
            self.budget.frontier(
                "relation-bound",
                phase=phase,
                from_node_id=node_id,
                relation="|".join(ordered),
                omitted_at_least=1,
            )
        return edges, truncated

    def _has_edges(
        self,
        node_id: str,
        relations: frozenset[str],
        *,
        direction: str,
    ) -> bool:
        ordered = sorted(relations)
        endpoint = "s.id=?" if direction == "from" else "t.id=?"
        row = self.view.query.connection.execute(
            "SELECT 1 FROM edges e JOIN nodes s ON s.node_key=e.source_node "
            "JOIN nodes t ON t.node_key=e.target_node WHERE "
            + endpoint
            + " AND e.relation IN ("
            + ",".join("?" for _ in ordered)
            + ") LIMIT 1",
            [node_id, *ordered],
        ).fetchone()
        return row is not None

    def _related_node(
        self,
        edge: dict[str, Any],
        *,
        direction: str,
        from_node_id: str,
        phase: str,
    ) -> dict[str, Any] | None:
        node_id = edge["target"] if direction == "from" else edge["source"]
        if not self.budget.admit(
            node_id,
            from_node_id=from_node_id,
            relation=edge["relation"],
            phase=phase,
        ):
            return None
        return self.view._node(node_id)

    def _producer_rows(
        self, resource: dict[str, Any], *, phase: str
    ) -> tuple[list[dict[str, Any]], bool]:
        edges, truncated = self._limited_edges(
            resource["id"], _OUTPUT_RELATIONS, direction="to", phase=phase
        )
        result: list[dict[str, Any]] = []
        for edge in edges:
            producer = self._related_node(
                edge,
                direction="to",
                from_node_id=resource["id"],
                phase=phase,
            )
            if producer is None:
                truncated = True
            elif producer["kind"] == _RECIPE_KIND:
                # Foundation recipe nodes are the union of lookup and category
                # membership.  Only the lookup lens is an executable producer
                # candidate; category-only membership must not keep a route alive.
                lookup_active = producer["properties"].get("lookup_active")
                if lookup_active is True:
                    result.append({"recipe": producer, "edge": edge})
                elif lookup_active is not False:
                    truncated = True
                    self.unknown(
                        "producer-lookup-state-unavailable",
                        "A finite recipe registration has no exact boolean lookup-active state, so Atlas did not treat it as a viable producer.",
                        producer["id"],
                    )
        return result, truncated

    def _accepted_resources(
        self, selector: dict[str, Any], *, phase: str
    ) -> tuple[list[dict[str, Any]], bool]:
        if not self._selector_acceptance_complete(selector, phase=phase):
            return [], True
        edges, truncated = self._limited_edges(
            selector["id"], _INPUT_RELATIONS, direction="from", phase=phase
        )
        rows: list[dict[str, Any]] = []
        for edge in edges:
            resource = self._related_node(
                edge,
                direction="from",
                from_node_id=selector["id"],
                phase=phase,
            )
            if resource is None:
                truncated = True
            else:
                rows.append({"resource": resource, "edge": edge})
        return rows, truncated

    def _selector_acceptance_complete(self, selector: dict[str, Any], *, phase: str) -> bool:
        if selector["properties"].get("acceptance_complete", True) is True:
            return True
        self.unknown(
            "selector-acceptance-incomplete",
            "Captured input representatives do not establish this selector's complete accepted-resource set.",
            selector["id"],
        )
        self.budget.frontier(
            "relation-bound", phase=phase, from_node_id=selector["id"],
            relation="selector-acceptance-incomplete", omitted_at_least=1,
        )
        return False

    def _consumer_rows(
        self, resource: dict[str, Any], *, phase: str
    ) -> tuple[list[dict[str, Any]], bool]:
        accepted_edges, truncated = self._limited_edges(
            resource["id"], _INPUT_RELATIONS, direction="to", phase=phase
        )
        result: list[dict[str, Any]] = []
        for accepted_edge in accepted_edges:
            selector = self._related_node(
                accepted_edge,
                direction="to",
                from_node_id=resource["id"],
                phase=phase,
            )
            if selector is None:
                truncated = True
                continue
            selector_edges, selector_truncated = self._limited_edges(
                selector["id"], _SELECTOR_RELATIONS, direction="to", phase=phase
            )
            truncated = truncated or selector_truncated
            for selector_edge in selector_edges:
                recipe = self._related_node(
                    selector_edge,
                    direction="to",
                    from_node_id=selector["id"],
                    phase=phase,
                )
                if recipe is None:
                    truncated = True
                    continue
                if recipe["kind"] != _RECIPE_KIND:
                    continue
                lookup_active = recipe["properties"].get("lookup_active")
                if lookup_active is not True:
                    if lookup_active is not False:
                        truncated = True
                        self.unknown(
                            "consumer-lookup-state-unavailable",
                            "A finite recipe registration has no exact boolean lookup-active state, so Atlas did not classify it as an exposed consumer.",
                            recipe["id"],
                        )
                    continue
                result.append(
                    {
                        "recipe": recipe,
                        "selector": selector,
                        "acceptance": accepted_edge,
                        "selector_edge": selector_edge,
                    }
                )
        result.sort(key=lambda row: (row["recipe"]["id"], row["selector"]["id"]))
        return result, truncated

    def _recipe_outputs(
        self, recipe: dict[str, Any], *, phase: str
    ) -> tuple[list[dict[str, Any]], bool]:
        edges, truncated = self._limited_edges(
            recipe["id"], _OUTPUT_RELATIONS, direction="from", phase=phase
        )
        result: list[dict[str, Any]] = []
        for edge in edges:
            resource = self._related_node(
                edge,
                direction="from",
                from_node_id=recipe["id"],
                phase=phase,
            )
            if resource is None:
                truncated = True
            else:
                result.append({"resource": resource, "edge": edge})
        return result, truncated

    def _direct(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        inputs: list[dict[str, Any]] = []
        output_edges: list[dict[str, Any]] = []
        recipe_edges, recipe_edges_truncated = self._limited_edges(
            self.recipe["id"],
            _OUTPUT_RELATIONS | _SELECTOR_RELATIONS,
            direction="from",
            phase="direct-io",
        )
        if recipe_edges_truncated:
            self.unknown(
                "direct-io-truncated",
                "Some direct recipe selectors or outputs were outside the traversal bound.",
                self.recipe["id"],
            )
        for edge in recipe_edges:
            related = self._related_node(
                edge,
                direction="from",
                from_node_id=self.recipe["id"],
                phase="direct-io",
            )
            if related is None:
                self.unknown(
                    "direct-io-truncated",
                    "A direct recipe selector or output was outside the traversal bound.",
                    self.recipe["id"],
                )
                continue
            if edge["relation"] in _OUTPUT_RELATIONS:
                output_edges.append(_edge_node_summary(edge, related))
                continue
            if not self._selector_acceptance_complete(related, phase="direct-io"):
                continue
            accepted_edges, accepted_truncated = self._limited_edges(
                related["id"],
                _INPUT_RELATIONS,
                direction="from",
                phase="direct-io",
            )
            if accepted_truncated:
                self.unknown(
                    "direct-io-truncated",
                    "Some direct selector alternatives were outside the traversal bound.",
                    related["id"],
                )
            for accepted in accepted_edges:
                resource = self._related_node(
                    accepted,
                    direction="from",
                    from_node_id=related["id"],
                    phase="direct-io",
                )
                if resource is None:
                    self.unknown(
                        "direct-io-truncated",
                        "A direct selector alternative was outside the traversal bound.",
                        related["id"],
                    )
                    continue
                if len(inputs) >= self.budget.max_nodes:
                    self.budget.frontier(
                        "relation-bound",
                        phase="direct-io",
                        from_node_id=related["id"],
                        relation="direct-input-row-budget",
                        omitted_at_least=1,
                    )
                    self.unknown(
                        "direct-io-truncated",
                        "Some direct selector alternatives were outside the report-row bound.",
                        related["id"],
                    )
                    break
                row = _edge_node_summary(accepted, resource)
                row["selector"] = _node_summary(related)
                row["selector_edge"] = {
                    "relation": edge["relation"],
                    "properties": edge["properties"],
                    "evidence": edge["evidence"],
                }
                inputs.append(row)
        direct_outputs: list[dict[str, Any]] = []
        output_nodes: list[dict[str, Any]] = []
        for output in output_edges:
            resource_summary = output.get("node")
            if resource_summary is None:
                self.unknown(
                    "output-node-unresolved",
                    "An observed output edge has no resolvable target node.",
                    self.recipe["id"],
                )
                continue
            resource = self.view._node(resource_summary["selection_id"])
            if resource is None:
                continue
            if not self.budget.admit(
                resource["id"],
                from_node_id=self.recipe["id"],
                relation=output["relation"],
                phase="direct-output",
            ):
                continue
            output_nodes.append(resource)
            producer_rows, producer_truncated = self._producer_rows(
                resource, phase="direct-alternative-producers"
            )
            alternatives = [
                row
                for row in producer_rows
                if row["recipe"]["id"] != self.recipe["id"]
            ]
            consumer_rows, consumers_truncated = self._consumer_rows(
                resource, phase="direct-downstream-consumers"
            )
            direct_outputs.append(
                {
                    "output": output,
                    "producer_portfolio": {
                        "status": (
                            "producer-set-truncated"
                            if producer_truncated
                            else (
                                "observed-alternatives-present"
                                if alternatives
                                else (
                                    "sole-observed-finite-producer"
                                    if producer_rows
                                    else "no-observed-active-producer"
                                )
                            )
                        ),
                        "alternative_producers": [
                            {
                                "recipe": _node_summary(row["recipe"]),
                                "output_edge": _edge_summary(row["edge"]),
                            }
                            for row in alternatives
                        ],
                        "truncated": producer_truncated,
                        "viability": "not-assessed",
                    },
                    "downstream_consumers": [
                        {
                            "recipe": _node_summary(row["recipe"]),
                            "selector": _node_summary(row["selector"]),
                            "acceptance_edge": _edge_summary(row["acceptance"]),
                        }
                        for row in consumer_rows
                    ],
                    "downstream_consumers_truncated": consumers_truncated,
                }
            )
        return {"inputs": inputs, "outputs": direct_outputs}, output_nodes

    def _propagate(
        self, direct_outputs: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        unavailable_recipes = {self.recipe["id"]}
        at_risk_resources: dict[str, dict[str, Any]] = {}
        at_risk_recipes: dict[str, dict[str, Any]] = {}

        for resource in direct_outputs:
            producers, truncated = self._producer_rows(
                resource, phase="risk-seed-producers"
            )
            if truncated:
                self.unknown(
                    "producer-set-truncated",
                    "The complete observed producer set was not inspected, so this resource was not classified at risk.",
                    resource["id"],
                )
                continue
            producer_ids = {row["recipe"]["id"] for row in producers}
            if producer_ids and producer_ids <= unavailable_recipes:
                at_risk_resources[resource["id"]] = {
                    "resource": resource,
                    "depth": 0,
                    "all_observed_finite_producers": sorted(producer_ids),
                }

        frontier_resources = list(at_risk_resources)
        depth = 0
        while frontier_resources and depth < self.max_depth:
            depth += 1
            candidate_selectors: dict[str, dict[str, Any]] = {}
            for resource_id in sorted(frontier_resources):
                resource = at_risk_resources[resource_id]["resource"]
                consumers, truncated = self._consumer_rows(
                    resource, phase="risk-downstream-consumers"
                )
                if truncated:
                    self.unknown(
                        "consumer-set-truncated",
                        "Some downstream finite recipe consumers could not be classified within the activity evidence or traversal bound.",
                        resource_id,
                    )
                for row in consumers:
                    candidate_selectors[row["selector"]["id"]] = row

            newly_at_risk_recipes: dict[str, dict[str, Any]] = {}
            for selector_id in sorted(candidate_selectors):
                row = candidate_selectors[selector_id]
                accepted, truncated = self._accepted_resources(
                    row["selector"], phase="risk-selector-alternatives"
                )
                if truncated:
                    self.unknown(
                        "selector-alternatives-truncated",
                        "The selector's complete accepted-resource set was not inspected.",
                        selector_id,
                    )
                    continue
                accepted_ids = {item["resource"]["id"] for item in accepted}
                if not accepted_ids or not accepted_ids <= set(at_risk_resources):
                    continue
                recipe = row["recipe"]
                if recipe["id"] in unavailable_recipes:
                    continue
                blocked = {
                    "selector": _node_summary(row["selector"]),
                    "accepted_resources": [
                        _node_summary(item["resource"]) for item in accepted
                    ],
                }
                existing = newly_at_risk_recipes.setdefault(
                    recipe["id"],
                    {"recipe": recipe, "depth": depth, "blocked_selectors": []},
                )
                existing["blocked_selectors"].append(blocked)

            if not newly_at_risk_recipes:
                frontier_resources = []
                break
            for recipe_id, row in newly_at_risk_recipes.items():
                unavailable_recipes.add(recipe_id)
                at_risk_recipes[recipe_id] = row

            new_resources: list[str] = []
            candidates: dict[str, dict[str, Any]] = {}
            for recipe_id in sorted(newly_at_risk_recipes):
                outputs, truncated = self._recipe_outputs(
                    newly_at_risk_recipes[recipe_id]["recipe"],
                    phase="risk-cascading-outputs",
                )
                if truncated:
                    self.unknown(
                        "output-set-truncated",
                        "Some outputs of an exposed downstream recipe were outside the traversal bound.",
                        recipe_id,
                    )
                for output in outputs:
                    candidates[output["resource"]["id"]] = output["resource"]
            for resource_id in sorted(candidates):
                if resource_id in at_risk_resources:
                    continue
                resource = candidates[resource_id]
                producers, truncated = self._producer_rows(
                    resource, phase="risk-cascading-producers"
                )
                if truncated:
                    self.unknown(
                        "producer-set-truncated",
                        "The complete observed producer set was not inspected, so this resource was not classified at risk.",
                        resource_id,
                    )
                    continue
                producer_ids = {row["recipe"]["id"] for row in producers}
                if producer_ids and producer_ids <= unavailable_recipes:
                    at_risk_resources[resource_id] = {
                        "resource": resource,
                        "depth": depth,
                        "all_observed_finite_producers": sorted(producer_ids),
                    }
                    new_resources.append(resource_id)
            frontier_resources = new_resources

        if frontier_resources and any(
            self._has_edges(resource_id, _INPUT_RELATIONS, direction="to")
            for resource_id in frontier_resources
        ):
            self.budget.frontier(
                "depth-bound",
                phase="risk-propagation",
                depth=self.max_depth,
                subject_ids=sorted(frontier_resources),
                omitted_at_least=1,
            )

        return (
            {
                "status": (
                    "truncated" if self.budget.frontiers else "complete-within-model"
                ),
                "at_risk_resources": [
                    {
                        "resource": _node_summary(row["resource"]),
                        "depth": row["depth"],
                        "reason": "all-observed-finite-producers-are-unavailable-candidates",
                        "all_observed_finite_producers": row[
                            "all_observed_finite_producers"
                        ],
                    }
                    for _, row in sorted(at_risk_resources.items())
                ],
                "at_risk_recipes": [
                    {
                        "recipe": _node_summary(row["recipe"]),
                        "depth": row["depth"],
                        "reason": "at-least-one-selector-has-only-at-risk-observed-alternatives",
                        "blocked_selectors": row["blocked_selectors"],
                    }
                    for _, row in sorted(at_risk_recipes.items())
                ],
            },
            at_risk_resources,
        )

    def _alternative_cycle_signals(
        self, direct_outputs: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        signal_keys: set[tuple[str, ...]] = set()
        expanded_states: set[tuple[str, str, int]] = set()
        cycle_work = 0
        maximum_cycle_work = self.budget.max_nodes * 8
        maximum_signals = self.budget.max_nodes

        def cycle_frontier(relation: str, from_node_id: str | None = None) -> None:
            detail: dict[str, Any] = {
                "phase": "alternative-cycle-scan",
                "relation": relation,
                "omitted_at_least": 1,
            }
            if from_node_id is not None:
                detail["from_node_id"] = from_node_id
            self.budget.frontier("relation-bound", **detail)

        def spend(from_node_id: str) -> bool:
            nonlocal cycle_work
            if cycle_work >= maximum_cycle_work:
                cycle_frontier("cycle-work-budget", from_node_id)
                return False
            cycle_work += 1
            return True

        def emit(row: dict[str, Any], key: tuple[str, ...]) -> None:
            if key in signal_keys:
                return
            if len(signals) >= maximum_signals:
                cycle_frontier("cycle-signal-budget", row["root_resource_id"])
                return
            signal_keys.add(key)
            signals.append(row)

        def scan_recipe(
            root_resource_id: str,
            recipe: dict[str, Any],
            *,
            depth: int,
            path: tuple[str, ...],
        ) -> None:
            if depth > self.max_depth:
                self.budget.frontier(
                    "depth-bound",
                    phase="alternative-cycle-scan",
                    from_node_id=recipe["id"],
                    depth=self.max_depth,
                    omitted_at_least=1,
                )
                return
            state = (root_resource_id, recipe["id"], depth)
            if state in expanded_states:
                cycle_frontier("canonical-cycle-state")
                return
            if not spend(recipe["id"]):
                return
            expanded_states.add(state)
            selector_edges, truncated = self._limited_edges(
                recipe["id"], _SELECTOR_RELATIONS, direction="from", phase="alternative-cycle-scan"
            )
            if truncated:
                self.unknown(
                    "cycle-scan-truncated",
                    "The bounded alternative-producer cycle scan omitted selectors.",
                    recipe["id"],
                )
            for selector_edge in selector_edges:
                if not spend(recipe["id"]):
                    return
                selector = self._related_node(
                    selector_edge,
                    direction="from",
                    from_node_id=recipe["id"],
                    phase="alternative-cycle-scan",
                )
                if selector is None:
                    continue
                accepted, accepted_truncated = self._accepted_resources(
                    selector, phase="alternative-cycle-scan"
                )
                if accepted_truncated:
                    self.unknown(
                        "cycle-scan-truncated",
                        "The bounded alternative-producer cycle scan omitted selector alternatives.",
                        selector["id"],
                    )
                for item in accepted:
                    resource = item["resource"]
                    next_path = (*path, selector["id"], resource["id"])
                    if resource["id"] == root_resource_id:
                        key = next_path
                        emit(
                            {
                                "root_resource_id": root_resource_id,
                                "path": list(next_path),
                                "interpretation": "an observed alternative producer has an input-dependency path back to the resource it produces",
                                "viability_effect": "unknown",
                            },
                            key,
                        )
                        continue
                    if resource["id"] in path:
                        key = next_path
                        emit(
                            {
                                "root_resource_id": root_resource_id,
                                "path": list(next_path),
                                "interpretation": "the bounded alternative-producer dependency neighborhood contains a cycle",
                                "viability_effect": "unknown",
                            },
                            key,
                        )
                        continue
                    if not spend(resource["id"]):
                        return
                    producers, producer_truncated = self._producer_rows(
                        resource, phase="alternative-cycle-scan"
                    )
                    if producer_truncated:
                        self.unknown(
                            "cycle-scan-truncated",
                            "The bounded alternative-producer cycle scan omitted producers.",
                            resource["id"],
                        )
                    for producer in producers:
                        producer_id = producer["recipe"]["id"]
                        if producer_id == self.recipe["id"] or producer_id in next_path:
                            continue
                        scan_recipe(
                            root_resource_id,
                            producer["recipe"],
                            depth=depth + 1,
                            path=(*next_path, producer_id),
                        )

        for resource in direct_outputs:
            producers, truncated = self._producer_rows(
                resource, phase="alternative-cycle-scan"
            )
            if truncated:
                self.unknown(
                    "cycle-scan-truncated",
                    "The bounded alternative-producer cycle scan omitted direct producers.",
                    resource["id"],
                )
            for producer in producers:
                candidate = producer["recipe"]
                if candidate["id"] == self.recipe["id"]:
                    continue
                scan_recipe(
                    resource["id"],
                    candidate,
                    depth=1,
                    path=(resource["id"], candidate["id"]),
                )
        return signals

    def _quest_requirements_for_resource(
        self, resource: dict[str, Any], *, risk_status: str
    ) -> list[dict[str, Any]]:
        occurrence_edges, truncated = self._limited_edges(
            resource["id"],
            _QUEST_RESOURCE_RELATIONS,
            direction="to",
            phase="quest-resource-exposure",
        )
        if truncated:
            self.unknown(
                "quest-requirements-truncated",
                "Some exact quest resource occurrences were outside the traversal bound.",
                resource["id"],
            )
        rows: list[dict[str, Any]] = []
        for occurrence_edge in occurrence_edges:
            occurrence = self._related_node(
                occurrence_edge,
                direction="to",
                from_node_id=resource["id"],
                phase="quest-resource-exposure",
            )
            if occurrence is None:
                self.unknown(
                    "quest-requirements-truncated",
                    "A quest resource occurrence was outside the traversal bound.",
                    resource["id"],
                )
                continue
            ore_edges, ore_truncated = self._limited_edges(
                occurrence["id"],
                frozenset({"accepts-progression-ore-dictionary-key"}),
                direction="from",
                phase="quest-resource-exposure",
            )
            ore_keys: list[dict[str, Any]] = []
            if ore_truncated:
                self.unknown(
                    "quest-requirements-truncated",
                    "Some ore-dictionary alternatives were outside the traversal bound.",
                    occurrence["id"],
                )
            for ore_edge in ore_edges:
                ore_key = self._related_node(
                    ore_edge,
                    direction="from",
                    from_node_id=occurrence["id"],
                    phase="quest-resource-exposure",
                )
                if ore_key is None:
                    self.unknown(
                        "quest-requirements-truncated",
                        "An ore-dictionary alternative was outside the traversal bound.",
                        occurrence["id"],
                    )
                else:
                    ore_keys.append(
                        {
                            "ore_dictionary_key": _node_summary(ore_key),
                            "acceptance_edge": _edge_summary(ore_edge),
                        }
                    )
            task_edges, task_truncated = self._limited_edges(
                occurrence["id"],
                _TASK_RESOURCE_RELATIONS,
                direction="to",
                phase="quest-resource-exposure",
            )
            if task_truncated:
                self.unknown(
                    "quest-requirements-truncated",
                    "Some task owners were outside the traversal bound.",
                    occurrence["id"],
                )
            for task_edge in task_edges:
                task = self._related_node(
                    task_edge,
                    direction="to",
                    from_node_id=occurrence["id"],
                    phase="quest-resource-exposure",
                )
                if task is None:
                    self.unknown(
                        "quest-requirements-truncated",
                        "A quest task owner was outside the traversal bound.",
                        occurrence["id"],
                    )
                    continue
                quest_edges, quest_truncated = self._limited_edges(
                    task["id"],
                    frozenset({"owns-progression-task"}),
                    direction="to",
                    phase="quest-resource-exposure",
                )
                if quest_truncated:
                    self.unknown(
                        "quest-requirements-truncated",
                        "Some quest owners were outside the traversal bound.",
                        task["id"],
                    )
                for quest_edge in quest_edges:
                    quest = self._related_node(
                        quest_edge,
                        direction="to",
                        from_node_id=task["id"],
                        phase="quest-resource-exposure",
                    )
                    if quest is None:
                        self.unknown(
                            "quest-requirements-truncated",
                            "A quest definition owner was outside the traversal bound.",
                            task["id"],
                        )
                        continue
                    rows.append(
                        {
                            "quest": _node_summary(quest),
                            "task": _node_summary(task),
                            "requirement_occurrence": _node_summary(occurrence),
                            "resource": _node_summary(resource),
                            "resource_risk_status": risk_status,
                            "requirement_semantics": (
                                "ore-dictionary-selector-with-observed-item-representative"
                                if ore_keys
                                else "exact-observed-resource-reference"
                            ),
                            "accepted_ore_dictionary_keys": ore_keys,
                            "resource_edge": _edge_summary(occurrence_edge),
                            "task_edge": _edge_summary(task_edge),
                            "owner_edge": _edge_summary(quest_edge),
                        }
                    )
        rows.sort(
            key=lambda row: (
                row["quest"]["selection_id"],
                row["task"]["selection_id"],
                row["requirement_occurrence"]["selection_id"],
            )
        )
        return rows

    def _quest_dependents(
        self, direct_quests: Iterable[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        queue: list[tuple[dict[str, Any], int, tuple[str, ...]]] = []
        seen_depth: dict[str, int] = {}
        for quest in sorted(direct_quests, key=lambda row: row["id"]):
            queue.append((quest, 0, (quest["id"],)))
            seen_depth[quest["id"]] = 0
        dependents: dict[tuple[str, str], dict[str, Any]] = {}
        cycles: dict[tuple[str, ...], dict[str, Any]] = {}
        while queue:
            target, depth, path = queue.pop(0)
            if depth >= self.max_depth:
                if self._has_edges(
                    target["id"],
                    frozenset({"targets-progression-prerequisite"}),
                    direction="to",
                ):
                    self.budget.frontier(
                        "depth-bound",
                        phase="quest-prerequisite-dependents",
                        from_node_id=target["id"],
                        depth=self.max_depth,
                        omitted_at_least=1,
                    )
                continue
            prerequisite_edges, truncated = self._limited_edges(
                target["id"],
                frozenset({"targets-progression-prerequisite"}),
                direction="to",
                phase="quest-prerequisite-dependents",
            )
            if truncated:
                self.unknown(
                    "quest-dependents-truncated",
                    "Some prerequisite occurrences were outside the traversal bound.",
                    target["id"],
                )
            for target_edge in prerequisite_edges:
                occurrence = self._related_node(
                    target_edge,
                    direction="to",
                    from_node_id=target["id"],
                    phase="quest-prerequisite-dependents",
                )
                if occurrence is None:
                    continue
                owner_edges, owner_truncated = self._limited_edges(
                    occurrence["id"],
                    frozenset({"owns-progression-prerequisite"}),
                    direction="to",
                    phase="quest-prerequisite-dependents",
                )
                if owner_truncated:
                    self.unknown(
                        "quest-dependents-truncated",
                        "Some prerequisite owners were outside the traversal bound.",
                        occurrence["id"],
                    )
                for owner_edge in owner_edges:
                    dependent = self._related_node(
                        owner_edge,
                        direction="to",
                        from_node_id=occurrence["id"],
                        phase="quest-prerequisite-dependents",
                    )
                    if dependent is None:
                        continue
                    next_path = (*path, dependent["id"])
                    if dependent["id"] in path:
                        cycles[next_path] = {
                            "path": list(next_path),
                            "interpretation": "observed BetterQuesting prerequisite cycle",
                        }
                        continue
                    key = (dependent["id"], target["id"])
                    dependents[key] = {
                        "quest": _node_summary(dependent),
                        "depends_on": _node_summary(target),
                        "prerequisite_occurrence": _node_summary(occurrence),
                        "depth": depth + 1,
                        "target_edge": _edge_summary(target_edge),
                        "owner_edge": _edge_summary(owner_edge),
                    }
                    prior = seen_depth.get(dependent["id"])
                    if prior is None or depth + 1 < prior:
                        seen_depth[dependent["id"]] = depth + 1
                        queue.append((dependent, depth + 1, next_path))
        return (
            [dependents[key] for key in sorted(dependents)],
            [cycles[key] for key in sorted(cycles)],
        )

    def _progression(
        self,
        direct_outputs: Sequence[dict[str, Any]],
        at_risk_resources: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        properties = self.recipe["properties"]
        recipe_map_name = properties.get("recipe_map")
        recipe_map: dict[str, Any] | None = None
        map_edges, map_truncated = self._limited_edges(
            self.recipe["id"],
            frozenset({"contained-in-recipe-map"}),
            direction="from",
            phase="machine-tier-signals",
        )
        machines: list[dict[str, Any]] = []
        machine_truncated = map_truncated
        for map_edge in map_edges:
            candidate = self._related_node(
                map_edge,
                direction="from",
                from_node_id=self.recipe["id"],
                phase="machine-tier-signals",
            )
            if candidate is None:
                machine_truncated = True
                continue
            recipe_map = candidate
            machine_edges, truncated = self._limited_edges(
                candidate["id"],
                _MACHINE_MAP_RELATIONS,
                direction="to",
                phase="machine-tier-signals",
            )
            machine_truncated = machine_truncated or truncated
            for machine_edge in machine_edges:
                machine = self._related_node(
                    machine_edge,
                    direction="to",
                    from_node_id=candidate["id"],
                    phase="machine-tier-signals",
                )
                if machine is None:
                    machine_truncated = True
                else:
                    machines.append(
                        {
                            "machine": _node_summary(machine),
                            "map_edge": _edge_summary(machine_edge),
                        }
                    )
        machines.sort(key=lambda row: row["machine"]["selection_id"])
        tier_values = sorted(
            {
                tier
                for row in machines
                if type(tier := row["machine"]["properties"].get("tier")) is int
            }
        )

        affected: dict[str, tuple[dict[str, Any], str]] = {
            resource["id"]: (resource, "producer-portfolio-changed")
            for resource in direct_outputs
        }
        for resource_id, row in at_risk_resources.items():
            affected[resource_id] = (
                row["resource"],
                "observed-all-finite-producers-at-risk",
            )
        quest_requirements: list[dict[str, Any]] = []
        quest_nodes: dict[str, dict[str, Any]] = {}
        for resource_id in sorted(affected):
            resource, risk_status = affected[resource_id]
            rows = self._quest_requirements_for_resource(
                resource, risk_status=risk_status
            )
            quest_requirements.extend(rows)
            for row in rows:
                quest_id = row["quest"]["selection_id"]
                node = self.view._node(quest_id)
                if node is not None:
                    quest_nodes[quest_id] = node
        dependents, quest_cycles = self._quest_dependents(quest_nodes.values())
        quest_truncated = any(
            row["code"].startswith("quest-") for row in self.unknowns
        ) or any(
            str(row.get("phase", "")).startswith("quest-")
            for row in self.budget.frontiers
        )

        return {
            "energy_and_machine_signals": {
                "observed_recipe_properties": {
                    "eut": properties.get("eut"),
                    "duration": properties.get("duration"),
                    "recipe_map": recipe_map_name,
                    "hidden": properties.get("hidden"),
                    "lookup_active": properties.get("lookup_active"),
                },
                "recipe_map": None if recipe_map is None else _node_summary(recipe_map),
                "machines_observed_using_recipe_map": machines,
                "machine_numeric_tiers_observed": tier_values,
                "truncated": machine_truncated,
                "interpretation": "numeric EUT and machine tier properties are observed signals; no pack progression tier is inferred",
            },
            "quest_signals": {
                "status": (
                    "truncated"
                    if quest_truncated
                    else (
                        "observed-definition-references"
                        if quest_requirements
                        else (
                            "no-observed-reference"
                            if any(
                                self.view.relations.get(relation, 0)
                                for relation in _QUEST_RESOURCE_RELATIONS
                            )
                            else "evidence-unavailable"
                        )
                    )
                ),
                "direct_resource_requirements": quest_requirements,
                "structural_prerequisite_dependents": dependents,
                "prerequisite_cycles": quest_cycles,
                "interpretation": "these are definition references and prerequisite structure, not observed player blockage or task execution",
            },
        }

    def build(self) -> dict[str, Any]:
        direct, direct_output_nodes = self._direct()
        propagation, at_risk_resources = self._propagate(direct_output_nodes)
        cycles = self._alternative_cycle_signals(direct_output_nodes)
        progression = self._progression(direct_output_nodes, at_risk_resources)
        quest = progression["quest_signals"]
        report = {
            "format": IMPACT_FORMAT,
            "schema_version": 1,
            "context": self.view.describe(),
            "selection": _node_summary(self.recipe),
            "scenario": {
                "kind": "remove-exact-observed-recipe",
                "selected_recipe_assumed_unavailable": True,
                "change_interpretation": "a recipe change is assessed only as loss of the selected current recipe; replacement inputs, outputs, and runtime behavior require a new observed graph or a separately validated proposed-recipe model",
            },
            "analysis_model": {
                "kind": "bounded-observed-finite-recipe-dependency-exposure",
                "resource_at_risk_rule": "every observed finite recipe producer is an unavailable candidate",
                "recipe_at_risk_rule": "at least one exact input selector accepts only resources already classified at risk",
                "claim_boundary": "candidate dead paths inside observed finite recipe structure; not gameplay reachability",
            },
            "bounds": {
                "max_depth": self.max_depth,
                "max_nodes": self.budget.max_nodes,
                "visited_node_count": len(self.budget.seen),
            },
            "direct": direct,
            "propagation": {
                **propagation,
                "alternative_dependency_cycle_signals": cycles,
            },
            "progression_signals": progression,
            "frontiers": sorted(
                self.budget.frontiers,
                key=lambda row: (
                    row["kind"],
                    str(row.get("phase", "")),
                    str(row.get("from_node_id", "")),
                ),
            ),
            "unknowns": sorted(
                self.unknowns,
                key=lambda row: (row["code"], row.get("subject_id", "")),
            ),
            "evidence_gaps": [
                {
                    "code": "counterfactual-runtime-not-observed",
                    "message": "Atlas did not execute a runtime with the selected recipe removed or changed.",
                },
                {
                    "code": "non-recipe-acquisition-not-assessed",
                    "message": "World generation, loot, trade, inventory, commands, and other acquisition domains are not treated as alternative producers.",
                },
                {
                    "code": "dynamic-recipes-not-executed",
                    "message": "Procedural and contextual recipe rules were not invoked for this counterfactual.",
                },
                {
                    "code": "stoichiometry-and-chance-not-assessed",
                    "message": "Amounts, reusable inputs, probabilities, throughput, and inventory balance do not establish or refute availability here.",
                },
                {
                    "code": "progression-reachability-not-proven",
                    "message": "Quest definitions, finite recipes, and machine signals do not prove player reachability or a broken progression path.",
                },
                {
                    "code": "task-execution-not-invoked",
                    "message": "BetterQuesting task matching, completion, and reward execution were not invoked.",
                },
                {
                    "code": "pack-tier-policy-not-applied",
                    "message": "Numeric EUT and observed machine tier fields are reported without deriving a pack progression tier.",
                },
                *(
                    gap
                    for gap in self.view._evidence_gaps(role="recipe")
                    if gap["code"] == "graph-projection-limitations"
                ),
            ],
        }
        report["summary"] = {
            "selected_output_count": len(direct["outputs"]),
            "sole_observed_finite_producer_output_count": sum(
                row["producer_portfolio"]["status"]
                == "sole-observed-finite-producer"
                for row in direct["outputs"]
            ),
            "at_risk_resource_candidate_count": len(
                propagation["at_risk_resources"]
            ),
            "at_risk_recipe_candidate_count": len(propagation["at_risk_recipes"]),
            "quest_requirement_exposure_count": len(
                quest["direct_resource_requirements"]
            ),
            "structural_quest_dependent_count": len(
                quest["structural_prerequisite_dependents"]
            ),
            "alternative_dependency_cycle_signal_count": len(cycles),
            "truncated": bool(report["frontiers"]),
        }
        return report


def build_recipe_impact(
    view: "GraphRecipeHealthView",
    selection_id: str,
    *,
    max_depth: int = 4,
    max_nodes: int = 500,
) -> dict[str, Any]:
    """Explain bounded structural exposure if one exact observed recipe disappears."""

    from .view import RecipeHealthError, _text

    selection_id = _text(selection_id, "recipe-health selection ID")
    max_depth = _bound(max_depth, "maximum depth", minimum=1, maximum=12)
    max_nodes = _bound(max_nodes, "maximum nodes", minimum=10, maximum=2_000)
    recipe = view._node(selection_id)
    if recipe is None:
        raise RecipeHealthError("recipe-health selection does not exist in this graph")
    if recipe["kind"] != _RECIPE_KIND:
        raise RecipeHealthError(
            "recipe impact requires one exact observed gt-recipe selection"
        )
    return _ImpactBuilder(
        view, recipe, max_depth=max_depth, max_nodes=max_nodes
    ).build()
