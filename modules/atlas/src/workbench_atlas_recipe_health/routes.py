"""Captured-occurrence backend for Atlas's AND/OR prerequisite route model.

This adapter deliberately does not manufacture a COMMON_FINAL_STATE runtime
graph or execution-proven machine relationships. Its authority is exactly the
verified categorical graph supplied by the caller.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable

from .view import GraphRecipeHealthView, RecipeHealthError

RECIPE_ROUTES_FORMAT = "workbench-atlas-captured-recipe-routes-v1"
_RESOURCES = frozenset({"item-variant", "forge-fluid"})
_OUTPUTS = frozenset({"produces-gt-item", "produces-gt-fluid"})
_SELECTORS = frozenset({"has-item-input-selector", "has-fluid-input-selector"})
_ACCEPTS = frozenset({"accepts-gt-item-alternative", "accepts-gt-fluid-input"})
_OBSERVES = frozenset({"observes-gt-item-input-representative", "observes-gt-fluid-input-representative"})
_BINDINGS = frozenset({"uses-recipe-map", "currently-selects-recipe-map"})


@dataclass(frozen=True)
class RecipeRouteOptions:
    """Optional user limits; None means explore the complete finite closure."""

    max_depth: int | None = None
    max_resources: int | None = None
    max_recipes: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_depth", "max_resources", "max_recipes"):
            value = getattr(self, name)
            minimum = 0 if name == "max_depth" else 1
            if value is not None and (type(value) is not int or value < minimum):
                raise RecipeHealthError(f"{name} must be an integer >= {minimum}, or None")


def _identity(kind: str, *parts: str) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return f"workbench-atlas-{kind}-v1:sha256:" + sha256(raw).hexdigest()


def _order(edge: dict[str, Any]) -> tuple:
    props = edge["properties"]
    ordinal = props.get("ordinal", props.get("domain_ordinal", props.get("representative_ordinal")))
    return (str(props.get("output_family", edge["relation"])),
            ordinal if type(ordinal) is int else -1, edge["id"])


class _Routes:
    def __init__(self, view, options, check_cancelled):
        self.view, self.options = view, options
        self.poll = check_cancelled or (lambda: None)
        self.graph_id = view.manifest["graph_set_id"]
        self.nodes, self.recipe_rows = {}, {}
        self.map_bindings = {}
        self.resources, self.routes = {}, []
        self.queue = deque()
        self.unresolved, self.frontiers = [], []
        self.frontier_resources = set()
        self.adjacency = defaultdict(list)
        self.arc_keys = set()
        self.edge_ids = set()
        self.dependencies = []

    def node(self, identifier):
        self.poll()
        if identifier not in self.nodes:
            node = self.view._node(identifier)
            if node is None:
                raise RecipeHealthError("recipe route relationship refers to an absent node")
            self.nodes[identifier] = node
        return self.nodes[identifier]

    def edges(self, identifier, relations, *, reverse=False):
        self.poll()
        column = "target_node" if reverse else "source_node"
        sql = (
            "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
            "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
            "JOIN nodes t ON t.node_key=e.target_node "
            f"WHERE e.{column}=(SELECT node_key FROM nodes WHERE id=?) "
            "AND e.relation IN (" + ",".join("?" for _ in relations) + ")"
        )
        result = []
        for row in self.view.query.connection.execute(sql, [identifier, *sorted(relations)]):
            self.poll()
            edge = self.view.query._edge_row(row)
            self.edge_ids.add(edge["id"])
            result.append(edge)
        return sorted(result, key=_order)

    def issue(self, code, **details):
        self.unresolved.append({"code": code, **details})

    def frontier(self, code, **details):
        self.frontiers.append({"code": code, **details})
        if "resource_id" in details:
            self.frontier_resources.add(details["resource_id"])

    def bindings(self, recipe_map_id):
        self.poll()
        if recipe_map_id not in self.map_bindings:
            self.map_bindings[recipe_map_id] = [
                {"relationship": edge, "machine": self.node(edge["source"])}
                for edge in self.edges(recipe_map_id, _BINDINGS, reverse=True)
            ]
        return self.map_bindings[recipe_map_id]

    def resource(self, identifier, depth, **origin):
        node = self.node(identifier)
        if node["kind"] not in _RESOURCES:
            raise RecipeHealthError("recipe routes require exact item-variant or fluid resource nodes")
        if identifier in self.resources:
            return self.resources[identifier]["id"]
        if self.options.max_resources is not None and len(self.resources) >= self.options.max_resources:
            self.frontier("max-resources", resource_id=identifier, **origin)
            return None
        subproblem_id = _identity("recipe-route-subproblem", self.graph_id, identifier)
        self.resources[identifier] = {"id": subproblem_id, "target": node, "depth": depth,
                                      "route_ids": [], "status": "pending"}
        self.queue.append(identifier)
        self.adjacency[identifier]
        return subproblem_id

    def arc(self, edge, *, reverse=False):
        source, target = edge["source"], edge["target"]
        if reverse:
            source, target = target, source
        row = {"source_id": source, "target_id": target, "edge_id": edge["id"],
               "relation": edge["relation"], "direction": "reverse" if reverse else "forward"}
        key = (edge["id"], reverse)
        if key not in self.arc_keys:
            self.adjacency[source].append(row)
            self.arc_keys.add(key)
        self.adjacency[target]

    def recipe(self, identifier):
        if identifier in self.recipe_rows:
            return self.recipe_rows[identifier]
        self.poll()
        node = self.node(identifier)
        slots, reusable, maps = [], [], []
        outputs = [{"relationship": edge, "target": self.node(edge["target"])}
                   for edge in self.edges(identifier, _OUTPUTS)]
        for edge in self.edges(identifier, frozenset({"contained-in-recipe-map"})):
            recipe_map = self.node(edge["target"])
            bindings = self.bindings(recipe_map["id"])
            maps.append({"relationship": edge, "recipe_map": recipe_map, "bindings": bindings})
            if not bindings:
                self.issue("machine-map-binding-unavailable", recipe_id=identifier, recipe_map_id=recipe_map["id"])
        if not maps:
            self.issue("recipe-map-binding-unavailable", recipe_id=identifier)
        selector_edges = self.edges(identifier, _SELECTORS)
        counts = node["properties"].get("captured_input_counts")
        if counts is None:
            self.issue("recipe-input-inventory-unavailable", recipe_id=identifier,
                       observed_selector_count=len(selector_edges))
        else:
            if (type(counts) is not dict or set(counts) != {"item", "fluid"}
                    or any(type(value) is not int or value < 0 for value in counts.values())):
                raise RecipeHealthError("captured recipe input counts must contain nonnegative item/fluid integers")
            actual = {family: sum(edge["relation"] == f"has-{family}-input-selector" for edge in selector_edges)
                      for family in ("item", "fluid")}
            if counts != actual:
                raise RecipeHealthError("captured recipe input counts differ from selector occurrences")
        for owner_edge in selector_edges:
            selector = self.node(owner_edge["target"])
            if selector["kind"] != "gt-recipe-input-selector":
                raise RecipeHealthError("recipe input relation must target an input selector")
            props = selector["properties"]
            accepted, observed = [], []
            for edge in self.edges(selector["id"], _ACCEPTS | _OBSERVES):
                target = self.node(edge["target"])
                if target["kind"] not in _RESOURCES:
                    raise RecipeHealthError("recipe selector must refer to an exact captured resource")
                entry = {"relationship": edge, "target": target}
                (accepted if edge["relation"] in _ACCEPTS else observed).append(entry)
            complete = props.get("acceptance_complete") is True
            if observed and complete:
                raise RecipeHealthError("complete selector contains observation-only alternatives")
            if not complete:
                self.issue("selector-acceptance-incomplete", recipe_id=identifier, selector_id=selector["id"],
                           acceptance_gaps=props.get("acceptance_gaps", []))
            elif not accepted:
                self.issue("no-accepted-resource-in-captured-domain", recipe_id=identifier, selector_id=selector["id"])
            if props.get("matching_limits"):
                self.issue("selector-runtime-matching-limits", recipe_id=identifier, selector_id=selector["id"],
                           matching_limits=props["matching_limits"])
            non_consumable = props.get("non_consumable")
            if type(non_consumable) is not bool:
                self.issue("selector-consumption-unknown", recipe_id=identifier, selector_id=selector["id"])
            amount = props.get("amount")
            if type(amount) is not int or amount < 1:
                self.issue("selector-quantity-unavailable", recipe_id=identifier, selector_id=selector["id"])
            row = {"relationship": owner_edge, "slot": selector,
                   "semantics": {"jointly_required": True, "reusable": non_consumable is True,
                                 "consumed": non_consumable is False, "quantity": amount,
                                 "consumption_known": type(non_consumable) is bool},
                   "alternatives": {"mode": "OR", "exhaustive": complete,
                                    "scope": "captured-resource-domain", "items": accepted},
                   "observed_representatives": observed}
            (reusable if non_consumable is True else slots).append(row)
        result = {"producer": node, "classification": "observed-lookup-active-recipe",
                  "mechanics": {"execution_status": "unassessed", "recipe_map_bindings": maps},
                  "ingredient_slots": {"mode": "AND", "slots": slots},
                  "reusable_requirements": {"mode": "AND", "slots": reusable},
                  "outputs": outputs}
        self.recipe_rows[identifier] = result
        return result

    def expand(self, identifier):
        self.poll()
        subproblem = self.resources[identifier]
        candidates = defaultdict(list)
        for edge in self.edges(identifier, _OUTPUTS, reverse=True):
            recipe = self.node(edge["source"])
            if recipe["kind"] != "gt-recipe":
                raise RecipeHealthError("recipe output relation must originate at a GT recipe")
            candidates[recipe["id"]].append(edge)
        for recipe_id, matched in sorted(candidates.items()):
            self.poll()
            recipe = self.node(recipe_id)
            active = recipe["properties"].get("lookup_active")
            if active is not True:
                self.issue("lookup-inactive-producer" if active is False else "producer-lookup-state-unknown",
                           resource_id=identifier, candidate_producer=recipe, output_relationships=matched)
                continue
            if self.options.max_depth is not None and subproblem["depth"] >= self.options.max_depth:
                self.frontier("max-depth", resource_id=identifier, recipe_id=recipe_id)
                continue
            if (recipe_id not in self.recipe_rows and self.options.max_recipes is not None
                    and len(self.recipe_rows) >= self.options.max_recipes):
                self.frontier("max-recipes", resource_id=identifier, recipe_id=recipe_id)
                continue
            base = self.recipe(recipe_id)
            route_id = _identity("captured-recipe-route", self.graph_id, identifier, recipe_id)
            route = {**base, "id": route_id, "subproblem_id": subproblem["id"],
                     "scope": self.view.manifest["scope"],
                     "production": {"matched_outputs": [row for row in base["outputs"]
                                      if row["target"]["id"] == identifier], "all_outputs": base["outputs"]}}
            for key in ("ingredient_slots", "reusable_requirements"):
                route_slots = []
                for row in base[key]["slots"]:
                    self.poll()
                    alternatives = []
                    for alternative in row["alternatives"]["items"]:
                        child = self.resource(alternative["target"]["id"], subproblem["depth"] + 1,
                                              recipe_id=recipe_id, selector_id=row["slot"]["id"])
                        value = {**alternative, "subproblem_id": child,
                                 "expansion": {"status": "linked" if child else "truncated"}}
                        alternatives.append(value)
                        if child:
                            self.dependencies.append({"from_subproblem_id": child, "to_route_id": route_id,
                                "to_subproblem_id": subproblem["id"], "slot_id": row["slot"]["id"],
                                "alternative_relationship_id": alternative["relationship"]["id"],
                                "requirement_kind": "reusable-requirement" if key == "reusable_requirements" else "consumed-input"})
                            self.arc(row["relationship"])
                            self.arc(alternative["relationship"])
                    route_slots.append({**row, "alternatives": {**row["alternatives"], "items": alternatives}})
                route[key] = {"mode": "AND", "slots": route_slots}
            for edge in matched:
                self.arc(edge, reverse=True)
            self.routes.append(route)
            subproblem["route_ids"].append(route_id)
        if not candidates:
            self.issue("no-observed-producer", resource_id=identifier)
        subproblem["status"] = "expanded" if subproblem["route_ids"] else (
            "truncated" if identifier in self.frontier_resources else "unresolved")

    def cycles(self):
        """Iterative SCCs plus one exact edge witness; no path enumeration."""
        adjacency = {node: sorted(rows, key=lambda row: (row["target_id"], row["edge_id"]))
                     for node, rows in self.adjacency.items()}
        seen, finished, reverse = set(), [], defaultdict(set)
        for source, rows in adjacency.items():
            self.poll()
            for row in rows:
                reverse[row["target_id"]].add(source)
        for start in sorted(adjacency):
            if start in seen:
                continue
            seen.add(start)
            stack = [(start, iter(adjacency[start]))]
            while stack:
                self.poll()
                node, children = stack[-1]
                edge = next(children, None)
                if edge is None:
                    finished.append(node)
                    stack.pop()
                elif edge["target_id"] not in seen:
                    seen.add(edge["target_id"])
                    stack.append((edge["target_id"], iter(adjacency[edge["target_id"]])))
        seen, result = set(), []
        for start in reversed(finished):
            if start in seen:
                continue
            members, stack = set(), [start]
            seen.add(start)
            while stack:
                self.poll()
                node = stack.pop()
                members.add(node)
                for parent in sorted(reverse[node]):
                    if parent not in seen:
                        seen.add(parent)
                        stack.append(parent)
            first = min(members)
            if len(members) == 1 and not any(row["target_id"] == first for row in adjacency[first]):
                continue
            initial = next(row for row in adjacency[first] if row["target_id"] in members)
            previous, queue = {initial["target_id"]: None}, deque([initial["target_id"]])
            while first not in previous:
                self.poll()
                for row in adjacency[queue.popleft()]:
                    target = row["target_id"]
                    if target in members and target not in previous:
                        previous[target] = row
                        queue.append(target)
            tail, current = [], first
            while current != initial["target_id"]:
                row = previous[current]
                tail.append(row)
                current = row["source_id"]
            witness = [initial, *reversed(tail)]
            ordered = sorted(members)
            result.append({"component_id": _identity("recipe-route-component", self.graph_id, *ordered),
                           "member_node_ids": ordered,
                           "witness": {"node_ids": [first, *(row["target_id"] for row in witness)], "arcs": witness},
                           "seed_supply": "unknown", "viability_effect": "unknown",
                           "interpretation": "structural alternative dependency cycle; not an unavoidable prerequisite or bootstrap proof"})
        return sorted(result, key=lambda row: row["member_node_ids"])


def derive_recipe_routes(view: GraphRecipeHealthView, resource_selection_id: str, *,
                         check_cancelled: Callable[[], None] | None = None,
                         options: RecipeRouteOptions | None = None) -> dict[str, Any]:
    """Explore producer prerequisites in one verified finite captured graph.

    Output quantities describe one observed batch. No demand multiplication,
    recipe choice, probability calculation, inventory or quest ordering is inferred.
    The caller owns the view lifetime and optional Core cancellation callback.
    """
    if not isinstance(view, GraphRecipeHealthView):
        raise RecipeHealthError("recipe routes require an observed categorical graph view")
    if view.manifest.get("format") != "workbench-atlas-categorical-graph-bundle-v2":
        raise RecipeHealthError("recipe routes require a V2 recipe graph; initialization observations are not promoted")
    if type(resource_selection_id) is not str or not resource_selection_id:
        raise RecipeHealthError("recipe routes require an exact resource selection ID")
    options = RecipeRouteOptions() if options is None else options
    if not isinstance(options, RecipeRouteOptions):
        raise RecipeHealthError("recipe route options must be RecipeRouteOptions")
    state = _Routes(view, options, check_cancelled)
    state.poll()
    root = state.resource(resource_selection_id, 0)
    while state.queue:
        state.expand(state.queue.popleft())
    cycles = state.cycles()
    limits = {key: getattr(options, key) for key in ("max_depth", "max_resources", "max_recipes")}
    assumptions = {"player_inventory": "unknown", "seed_supply": "unknown", "existing_infrastructure": "unknown",
                   "machine_execution": "unassessed", "quest_ordering": "not-inferred", "route_preference": "none",
                   "quantity_model": "observed-batch-values-without-demand-balancing",
                   "chance_model": "ordered-observations-without-probability-evaluation",
                   "craftability": "unknown"}
    operations = []
    for route in state.routes:
        state.poll()
        groups = [{"kind": "observed-machine-map-binding", "mode": "OR", "execution_status": "unassessed",
                   "items": route["mechanics"]["recipe_map_bindings"]}]
        for key, kind in (("ingredient_slots", "consumed-input"), ("reusable_requirements", "reusable-requirement")):
            groups.extend({"kind": kind, "mode": "OR", "requirement": slot} for slot in route[key]["slots"])
        operations.append({"route_id": route["id"], "subproblem_id": route["subproblem_id"],
                           "producer": route["producer"], "requirements": {"mode": "AND", "groups": groups}})
    gaps = [{"code": "execution-and-acquisition-unassessed", "message": "Machine formation, energy, environment, external acquisition and initial supply are not established."},
            {"code": "registration-causation-unassessed", "message": "Captured records and their evidence do not attribute registration to source statements."},
            {"code": "quest-ordering-not-inferred", "message": "Recipe requirements do not establish quest dependencies or author intent."}]
    for partition in view.manifest.get("partitions", []):
        if partition.get("limitations"):
            gaps.append({"code": "graph-projection-limitations", "partition_id": partition.get("id"),
                         "limitations": partition["limitations"]})
    report = {"format": RECIPE_ROUTES_FORMAT, "schema_version": 1, "context": view.describe(),
              "selection": state.nodes[resource_selection_id], "assumptions": assumptions,
              "exploration": {"mode": "bounded" if any(value is not None for value in limits.values()) else "complete-finite",
                              "status": "truncated" if state.frontiers else "complete", "limits": limits,
                              "visited_resource_count": len(state.resources), "expanded_recipe_count": len(state.recipe_rows),
                              "examined_edge_count": len(state.edge_ids)},
              "roots": [root], "subproblems": list(state.resources.values()),
              "routes": {"mode": "OR", "group_by": "subproblem_id", "items": state.routes},
              "prerequisites": {"assumptions": assumptions, "operations": operations, "dependency_edges": state.dependencies},
              "cycles": cycles, "unresolved": state.unresolved, "frontiers": state.frontiers, "evidence_gaps": gaps,
              "summary": {"route_count": len(state.routes), "recipe_count": len(state.recipe_rows),
                          "resource_count": len(state.resources), "cycle_component_count": len(cycles),
                          "unresolved_count": len(state.unresolved), "frontier_count": len(state.frontiers),
                          "truncated": bool(state.frontiers), "craftability": "unknown"}}
    state.poll()
    return report
