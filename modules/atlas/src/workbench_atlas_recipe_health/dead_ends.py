"""Finite-capture producer/use audit, without inventing acquisition or viability.

The report contains compact, graph-bound references rather than copying recipe
evidence into every producer/consumer result. Open those exact IDs in the same
graph to inspect the original records. A structural link is not proof that a
recipe executes, that its inputs are obtainable, or that its output is useful.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from .view import GraphRecipeHealthView, RecipeHealthError

DEAD_END_AUDIT_FORMAT = "workbench-atlas-recipe-dead-ends-v1"
_RESOURCES = frozenset({"item-variant", "forge-fluid"})
_OUTPUTS = frozenset({"produces-gt-item", "produces-gt-fluid"})
_OWNERS = frozenset({"has-item-input-selector", "has-fluid-input-selector"})
_ACCEPTS = frozenset({"accepts-gt-item-alternative", "accepts-gt-fluid-input"})
_OBSERVES = frozenset({"observes-gt-item-input-representative", "observes-gt-fluid-input-representative"})
_MAPS = frozenset({"contained-in-recipe-map"})
_KINDS = _RESOURCES | {"gt-recipe", "gt-recipe-input-selector", "gt-recipe-map"}


def _lookup(value):
    return "active" if value is True else "inactive" if value is False else "unknown"


def _consumption(value):
    return "reusable" if value is True else "consumed" if value is False else "unknown"


def _output_kind(props):
    return "chanced" if props.get("chanced") is True else "guaranteed" if props.get("chanced") is False else "unknown"


def _positive_quantity(value):
    return type(value) is int and value > 0


def _reference(node):
    props = node["properties"]
    observed = props.get("observed_item_names")
    names = sorted({entry["name"] for entry in (observed if isinstance(observed, list) else [])
                    if isinstance(entry, dict) and isinstance(entry.get("name"), str)})
    return {"selection_id": node["id"], "kind": node["kind"],
            "semantic_key": node["semantic_key"], "observed_names": names}


def _edge_reference(edge):
    return {"edge_id": edge["id"], "relation": edge["relation"],
            "source_id": edge["source"], "target_id": edge["target"]}


def _identity(graph_id, members):
    payload = json.dumps([graph_id, *members], ensure_ascii=False, separators=(",", ":"))
    return "workbench-atlas-dead-end-cycle-v1:sha256:" + sha256(payload.encode()).hexdigest()


class _Audit:
    def __init__(self, view, check_cancelled):
        self.view = view
        self.poll = check_cancelled or (lambda: None)
        self.nodes = {}
        self.recipes = {}
        self.resources = {}
        self.owners = defaultdict(list)
        self.accepts = defaultdict(list)
        self.observations = defaultdict(list)
        self.outputs = defaultdict(list)
        self.maps = defaultdict(list)
        self.adjacency = defaultdict(list)
        self.relations = Counter()

    def node(self, identifier, kinds):
        value = self.nodes.get(identifier)
        if value is None or value["kind"] not in kinds:
            raise RecipeHealthError("dead-end audit relationship has an invalid node kind or missing endpoint")
        return value

    def read(self):
        connection = self.view.query.connection
        # Two bulk index reads, independent of the number of recipes. Evidence
        # stays in the graph: only identifiers and relevant values are projected.
        placeholders = ",".join("?" for _ in _KINDS)
        for identifier, kind, key, raw in connection.execute(
                f"SELECT id,kind,semantic_key,properties_json FROM nodes WHERE kind IN ({placeholders})",
                sorted(_KINDS)):
            self.poll()
            props = json.loads(raw)
            retained = ({"lookup_active", "duration", "eut", "category", "captured_input_counts"} if kind == "gt-recipe" else
                        {"ordinal", "amount", "non_consumable", "acceptance_complete", "acceptance_domain",
                         "acceptance_model", "matching_limits"} if kind == "gt-recipe-input-selector" else
                        {"observed_item_names"} if kind in _RESOURCES else set())
            node = {"id": identifier, "kind": kind, "semantic_key": key,
                    "properties": {name: value for name, value in props.items() if name in retained}}
            self.nodes[identifier] = node
            if kind in _RESOURCES:
                self.resources[identifier] = {**_reference(node), "producers": [], "uses": [], "observation_only_uses": []}
            elif kind == "gt-recipe":
                props = node["properties"]
                self.recipes[identifier] = {
                    **_reference(node), "lookup_state": _lookup(props.get("lookup_active")),
                    "recipe_values": {key: props[key] for key in ("duration", "eut", "category") if key in props},
                    "recipe_maps": [], "inputs": [], "outputs": [], "issues": [],
                    "findings": [], "cycle_component_ids": [],
                }
        relations = _OUTPUTS | _OWNERS | _ACCEPTS | _OBSERVES | _MAPS
        placeholders = ",".join("?" for _ in relations)
        sql = ("SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,'[]' "
               "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
               "JOIN nodes t ON t.node_key=e.target_node "
               f"WHERE e.relation IN ({placeholders})")
        for raw in connection.execute(sql, sorted(relations)):
            self.poll()
            edge = self.view.query._edge_row(raw)
            if edge["relation"] not in _OUTPUTS:
                edge["properties"] = {}
            relation, source, target = edge["relation"], edge["source"], edge["target"]
            self.relations[relation] += 1
            if relation in _OUTPUTS:
                self.node(source, {"gt-recipe"})
                kind = "item-variant" if relation == "produces-gt-item" else "forge-fluid"
                self.node(target, {kind})
                self.outputs[source].append(edge)
                self.resources[target]["producers"].append({
                    "recipe_id": source, "output_edge_id": edge["id"],
                    "lookup_state": self.recipes[source]["lookup_state"],
                    "output_kind": _output_kind(edge["properties"]),
                    "positive_quantity": _positive_quantity(edge["properties"].get("amount")),
                })
            elif relation in _OWNERS:
                self.node(source, {"gt-recipe"})
                self.node(target, {"gt-recipe-input-selector"})
                self.owners[source].append(edge)
            elif relation in _ACCEPTS | _OBSERVES:
                self.node(source, {"gt-recipe-input-selector"})
                kind = "forge-fluid" if "fluid" in relation else "item-variant"
                self.node(target, {kind})
                (self.accepts if relation in _ACCEPTS else self.observations)[source].append(edge)
            else:
                self.node(source, {"gt-recipe"})
                self.node(target, {"gt-recipe-map"})
                self.maps[source].append(edge)
        # A selector's accepted alternatives describe a use only through a real
        # recipe owner. Observation-only representatives never enter this index.
        for recipe_id, owners in self.owners.items():
            for owner in owners:
                self.poll()
                selector_id = owner["target"]
                props = self.nodes[selector_id]["properties"]
                consumption = _consumption(props.get("non_consumable"))
                for accepted in self.accepts[selector_id]:
                    self.poll()
                    self.resources[accepted["target"]]["uses"].append({
                        "recipe_id": recipe_id, "selector_id": selector_id,
                        "owner_edge_id": owner["id"], "acceptance_edge_id": accepted["id"],
                        "lookup_state": self.recipes[recipe_id]["lookup_state"],
                        "consumption": consumption, "positive_quantity": _positive_quantity(props.get("amount")),
                    })
                for observed in self.observations[selector_id]:
                    self.poll()
                    self.resources[observed["target"]]["observation_only_uses"].append({
                        "recipe_id": recipe_id, "selector_id": selector_id,
                        "owner_edge_id": owner["id"], "observation_edge_id": observed["id"],
                        "lookup_state": self.recipes[recipe_id]["lookup_state"],
                    })
        for resource in self.resources.values():
            self.poll()
            resource["producers"].sort(key=lambda row: (row["recipe_id"], row["output_edge_id"]))
            resource["uses"].sort(key=lambda row: (row["recipe_id"], row["selector_id"], row["acceptance_edge_id"]))
            resource["observation_only_uses"].sort(key=lambda row: (row["recipe_id"], row["selector_id"], row["observation_edge_id"]))
            resource["producer_counts"] = dict(sorted(Counter(row["lookup_state"] for row in resource["producers"]).items()))
            resource["use_counts"] = dict(sorted(Counter(row["lookup_state"] for row in resource["uses"]).items()))
            resource["positive_quantity_producer_counts"] = dict(sorted(Counter(row["lookup_state"] for row in resource["producers"] if row["positive_quantity"]).items()))
            resource["positive_quantity_use_counts"] = dict(sorted(Counter(row["lookup_state"] for row in resource["uses"] if row["positive_quantity"]).items()))
            resource["observation_only_use_counts"] = dict(sorted(Counter(row["lookup_state"] for row in resource["observation_only_uses"]).items()))

    def arc(self, edge, *, reverse=False):
        row = _edge_reference(edge)
        row["direction"] = "reverse" if reverse else "forward"
        if reverse:
            row["source_id"], row["target_id"] = row["target_id"], row["source_id"]
        self.adjacency[row["source_id"]].append(row)
        self.adjacency[row["target_id"]]

    def inputs(self, recipe_id):
        row = self.recipes[recipe_id]
        props = self.nodes[recipe_id]["properties"]
        owners = sorted(self.owners[recipe_id], key=lambda edge: edge["id"])
        counts = props.get("captured_input_counts")
        if counts is not None:
            if (type(counts) is not dict or set(counts) != {"item", "fluid"}
                    or any(type(value) is not int or value < 0 for value in counts.values())):
                raise RecipeHealthError("captured recipe input counts must contain nonnegative item/fluid integers")
            actual = {family: sum(edge["relation"] == f"has-{family}-input-selector" for edge in owners)
                      for family in ("item", "fluid")}
            if counts != actual:
                raise RecipeHealthError("captured recipe input counts differ from selector occurrences")
        else:
            row["issues"].append({"code": "recipe-input-inventory-unavailable"})
        row["input_inventory"] = {"status": "observed" if counts is not None else "unknown", "captured_counts": counts}
        for owner in owners:
            self.poll()
            selector_id = owner["target"]
            props = self.nodes[selector_id]["properties"]
            accepted = sorted(self.accepts[selector_id], key=lambda edge: edge["id"])
            observations = sorted(self.observations[selector_id], key=lambda edge: edge["id"])
            fluid = owner["relation"] == "has-fluid-input-selector"
            accepted_relation = "accepts-gt-fluid-input" if fluid else "accepts-gt-item-alternative"
            observed_relation = "observes-gt-fluid-input-representative" if fluid else "observes-gt-item-input-representative"
            if any(edge["relation"] != accepted_relation for edge in accepted) or any(edge["relation"] != observed_relation for edge in observations):
                raise RecipeHealthError("recipe input selector family differs from its resource relationships")
            complete = props.get("acceptance_complete") is True
            if complete and observations:
                raise RecipeHealthError("complete selector contains observation-only alternatives")
            local_issues = []
            if not complete:
                local_issues.append("selector-acceptance-incomplete")
            if props.get("matching_limits"):
                local_issues.append("selector-runtime-matching-limits")
            if _consumption(props.get("non_consumable")) == "unknown":
                local_issues.append("selector-consumption-unknown")
            amount = props.get("amount")
            if type(amount) is not int or amount < 1:
                local_issues.append("selector-quantity-unavailable")
            active, unknown = False, False
            for edge in accepted:
                self.poll()
                resource = self.resources[edge["target"]]
                counts_by_state = resource["producer_counts"]
                positive = resource["positive_quantity_producer_counts"]
                active |= bool(positive.get("active"))
                unknown |= bool(counts_by_state.get("unknown")) or counts_by_state.get("active", 0) > positive.get("active", 0)
            status = ("unknown" if not _positive_quantity(amount) else "observed-active-producer" if active else
                      "unknown" if unknown or not complete or props.get("matching_limits") or not _positive_quantity(amount) else
                      "no-observed-active-producer")
            if unknown and not active:
                local_issues.append("producer-activity-or-output-quantity-unknown")
            if complete and not accepted:
                local_issues.append("no-accepted-resource-in-captured-domain")
            row["inputs"].append({
                "selector_id": selector_id, "owner_edge_id": owner["id"],
                "family": "fluid" if owner["relation"] == "has-fluid-input-selector" else "item",
                "ordinal": props.get("ordinal"), "amount": amount,
                "consumption": _consumption(props.get("non_consumable")),
                "acceptance_complete": complete, "acceptance_scope": "captured-resource-domain",
                "acceptance_domain": props.get("acceptance_domain"),
                "acceptance_model": props.get("acceptance_model"),
                "matching_limits": props.get("matching_limits", []),
                "alternatives": [{"resource_id": edge["target"], "acceptance_edge_id": edge["id"]} for edge in accepted],
                "observed_representatives": [{"resource_id": edge["target"], "observation_edge_id": edge["id"]} for edge in observations],
                "supply_status": status, "issues": sorted(local_issues),
            })
            if row["lookup_state"] == "active" and _positive_quantity(amount):
                self.arc(owner)
                for edge in accepted:
                    self.arc(edge)

    def assess(self):
        for recipe_id, row in sorted(self.recipes.items()):
            self.poll()
            self.inputs(recipe_id)
            row["recipe_maps"] = [{"selection_id": edge["target"], "edge_id": edge["id"],
                                   "semantic_key": self.nodes[edge["target"]]["semantic_key"]}
                                  for edge in sorted(self.maps[recipe_id], key=lambda edge: edge["id"])]
            if not row["recipe_maps"]:
                row["issues"].append({"code": "recipe-map-unavailable"})
            for edge in sorted(self.outputs[recipe_id], key=lambda edge: edge["id"]):
                self.poll()
                resource = self.resources[edge["target"]]
                uses = resource["use_counts"]
                positive = resource["positive_quantity_use_counts"]
                observations = resource["observation_only_use_counts"]
                status = ("observed-active-use" if positive.get("active") else "unknown" if uses.get("unknown")
                          or uses.get("active") or observations.get("active") or observations.get("unknown")
                          else "no-observed-active-use")
                props = edge["properties"]
                if not _positive_quantity(props.get("amount")):
                    row["issues"].append({"code": "output-quantity-unavailable", "output_edge_id": edge["id"]})
                    status = "unknown"
                row["outputs"].append({
                    "resource_id": edge["target"], "output_edge_id": edge["id"],
                    "output_kind": _output_kind(props), "use_status": status,
                    "values": {key: props[key] for key in ("amount", "ordinal", "output_family", "chanced",
                                                          "chance", "chance_boost", "logic_class") if key in props},
                })
                if row["lookup_state"] == "active" and _positive_quantity(props.get("amount")):
                    self.arc(edge, reverse=True)
            missing = [slot["selector_id"] for slot in row["inputs"] if slot["supply_status"] == "no-observed-active-producer"]
            unknown = [slot["selector_id"] for slot in row["inputs"] if slot["supply_status"] == "unknown"]
            stranded = [output["output_edge_id"] for output in row["outputs"] if output["use_status"] == "no-observed-active-use"]
            unknown_outputs = [output["output_edge_id"] for output in row["outputs"] if output["use_status"] == "unknown"]
            upstream = ("missing-producer-candidate" if missing else "unknown" if unknown or row["input_inventory"]["status"] == "unknown"
                        else "observed-local-links" if row["inputs"] else "observed-inputless")
            downstream = ("unknown" if not row["outputs"] else
                          "no-output-use-candidate" if len(stranded) == len(row["outputs"]) else
                          "partial-output-use-gap" if stranded else
                          "unknown" if unknown_outputs else "observed-local-links")
            if not row["outputs"]:
                row["issues"].append({"code": "recipe-output-inventory-unavailable-or-empty"})
            if row["lookup_state"] != "active":
                upstream = downstream = "unassessed"
            else:
                if missing:
                    row["findings"].append("missing-producer-candidate")
                if stranded:
                    row["findings"].append("stranded-output-candidate")
                if downstream == "no-output-use-candidate":
                    row["findings"].append("no-output-use-candidate")
                    if missing:
                        row["findings"].append("both-sides-candidate")
            row["upstream"] = {"status": upstream, "missing_selector_ids": sorted(missing), "unknown_selector_ids": sorted(unknown)}
            row["downstream"] = {"status": downstream, "stranded_output_edge_ids": sorted(stranded), "unknown_output_edge_ids": sorted(unknown_outputs)}

    def cycles(self):
        # Iterative Kosaraju on recipe -> selector -> accepted resource -> active
        # producer. It is linear in captured edges, not producer/consumer pairs,
        # and never multiplies OR alternatives into paths.
        adjacency = {key: sorted(value, key=lambda edge: (edge["target_id"], edge["edge_id"]))
                     for key, value in self.adjacency.items()}
        reverse = defaultdict(list)
        for source, edges in adjacency.items():
            self.poll()
            for edge in edges:
                reverse[edge["target_id"]].append(source)
        seen, finished = set(), []
        for start in sorted(adjacency):
            self.poll()
            if start in seen:
                continue
            seen.add(start)
            stack = [(start, iter(adjacency[start]))]
            while stack:
                self.poll()
                current, iterator = stack[-1]
                edge = next(iterator, None)
                if edge is None:
                    finished.append(current)
                    stack.pop()
                elif edge["target_id"] not in seen:
                    target = edge["target_id"]
                    seen.add(target)
                    stack.append((target, iter(adjacency[target])))
        seen.clear()
        components, membership = [], {}
        for start in reversed(finished):
            self.poll()
            if start in seen:
                continue
            members, pending = [], [start]
            seen.add(start)
            while pending:
                self.poll()
                current = pending.pop()
                members.append(current)
                for parent in reverse[current]:
                    if parent not in seen:
                        seen.add(parent)
                        pending.append(parent)
            if len(members) < 2 and not any(edge["target_id"] == start for edge in adjacency[start]):
                continue
            members.sort()
            component = {"component_id": _identity(self.view.manifest["graph_set_id"], members),
                         "member_node_ids": members, "recipe_ids": [key for key in members if key in self.recipes],
                         "incoming_dependency_edges": [], "outgoing_dependency_edges": [],
                         "seed_supply": "unknown", "viability_effect": "unknown",
                         "interpretation": "structural alternative dependency cycle; not proof of an unavoidable loop or viable bootstrap"}
            components.append(component)
            for key in members:
                membership[key] = component
            self.witness(component, adjacency)
        for source, edges in adjacency.items():
            self.poll()
            origin = membership.get(source)
            for edge in edges:
                destination = membership.get(edge["target_id"])
                if origin is not destination:
                    if origin is not None:
                        origin["outgoing_dependency_edges"].append(edge)
                    if destination is not None:
                        destination["incoming_dependency_edges"].append(edge)
        for component in components:
            self.poll()
            for key in ("incoming_dependency_edges", "outgoing_dependency_edges"):
                component[key].sort(key=lambda edge: (edge["source_id"], edge["target_id"], edge["edge_id"]))
            for recipe_id in component["recipe_ids"]:
                row = self.recipes[recipe_id]
                row["cycle_component_ids"].append(component["component_id"])
                row["findings"].append("structural-cycle")
        return sorted(components, key=lambda component: component["member_node_ids"])

    def witness(self, component, adjacency):
        members = set(component["member_node_ids"])
        start = min(members)
        initial = next(edge for edge in adjacency[start] if edge["target_id"] in members)
        previous, pending = {initial["target_id"]: None}, deque([initial["target_id"]])
        while start not in previous:
            self.poll()
            current = pending.popleft()
            for edge in adjacency[current]:
                target = edge["target_id"]
                if target in members and target not in previous:
                    previous[target] = edge
                    pending.append(target)
        tail, current = [], start
        while current != initial["target_id"]:
            self.poll()
            edge = previous[current]
            tail.append(edge)
            current = edge["source_id"]
        edges = [initial, *reversed(tail)]
        component["witness"] = {"node_ids": [start, *(edge["target_id"] for edge in edges)], "arcs": edges}


def audit_recipe_dead_ends(view: GraphRecipeHealthView, *,
                          check_cancelled: Callable[[], None] | None = None) -> dict[str, Any]:
    """Audit all GT recipe occurrences in a verified categorical V2 capture.

    No limit, runtime launch, inferred seed, acquisition policy, preferred recipe,
    or transitive viability claim is introduced. Cancellation propagates without
    returning a successful partial report; the caller owns the view lifetime.
    """
    if not isinstance(view, GraphRecipeHealthView):
        raise RecipeHealthError("dead-end audit requires an observed categorical graph view")
    if view.manifest.get("format") != "workbench-atlas-categorical-graph-bundle-v2":
        raise RecipeHealthError("dead-end audit requires a V2 recipe graph; initialization observations are not promoted")
    policy = {"id": "workbench-atlas-captured-active-links-v1",
              "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    state = _Audit(view, check_cancelled)
    state.poll()
    state.read()
    state.assess()
    cycles = state.cycles()
    recipes = [state.recipes[key] for key in sorted(state.recipes)]
    for row in recipes:
        state.poll()
        row["findings"].sort()
    findings = Counter(finding for row in recipes for finding in row["findings"])
    lookup = Counter(row["lookup_state"] for row in recipes)
    selectors = [node for node in state.nodes.values() if node["kind"] == "gt-recipe-input-selector"]
    limitations = [{"partition_id": partition.get("id"), "limitations": partition["limitations"]}
                   for partition in view.manifest.get("partitions", []) if partition.get("limitations")]
    result = {
        "format": DEAD_END_AUDIT_FORMAT, "schema_version": 1, "context": view.describe(), "policy": policy,
        "coverage": {
            "scan": "complete-finite", "truncated": False, "recipe_kinds": ["gt-recipe"],
            "status": "captured-gt-recipes-only" if recipes else "no-supported-recipes",
            "recipe_count": len(recipes), "examined_relationship_count": sum(state.relations.values()),
            "captured_node_kind_counts": dict(sorted(view.kinds.items())),
            "scanned_node_kind_counts": dict(sorted(Counter(node["kind"] for node in state.nodes.values()).items())),
            "input_selector_count": len(selectors),
            "incomplete_selector_count": sum(node["properties"].get("acceptance_complete") is not True for node in selectors),
            "matching_limited_selector_count": sum(bool(node["properties"].get("matching_limits")) for node in selectors),
            "unknown_input_inventory_recipe_count": sum(row["input_inventory"]["status"] == "unknown" for row in recipes),
            "relationships": dict(sorted(state.relations.items())),
            "matching_scope": "captured-resource-domain", "other_recipe_families": "unassessed",
            "external_acquisition": "unsupported", "terminal_use": "unsupported",
            "machine_execution": "unassessed", "transitive_supply": "unassessed",
            "quantity_feasibility": "unassessed", "chance_feasibility": "unassessed",
            "partition_limitations": limitations,
            "interpretation": "Missing links are candidates within this capture, not proof of impossible acquisition or useless outputs. Incomplete or unsupported selectors may conceal additional uses; no accepted use is not proof of no possible use.",
        },
        "recipes": recipes, "resources": [state.resources[key] for key in sorted(state.resources)], "cycles": cycles,
        "summary": {
            "recipe_count": len(recipes), "active_recipe_count": lookup["active"],
            "inactive_recipe_count": lookup["inactive"], "unknown_activity_recipe_count": lookup["unknown"],
            "resource_count": len(state.resources), "cycle_component_count": len(cycles),
            "cycle_count": len(cycles), "truncated": False,
            "missing_producer_candidate_count": findings["missing-producer-candidate"],
            "no_output_use_candidate_count": findings["no-output-use-candidate"],
            "both_sides_candidate_count": findings["both-sides-candidate"],
            "stranded_output_candidate_count": findings["stranded-output-candidate"],
            "unresolved_recipe_count": sum(bool(row["issues"]) or row["lookup_state"] != "active"
                                           or any(slot["issues"] for slot in row["inputs"])
                                           or row["upstream"]["status"] == "unknown"
                                           or row["downstream"]["status"] == "unknown"
                                           or bool(row["upstream"]["unknown_selector_ids"])
                                           or bool(row["downstream"]["unknown_output_edge_ids"])
                                           for row in recipes),
            "recipes_with_findings": sum(bool(row["findings"]) for row in recipes),
            "finding_counts": dict(sorted(findings.items())),
            "upstream_status_counts": dict(sorted(Counter(row["upstream"]["status"] for row in recipes).items())),
            "downstream_status_counts": dict(sorted(Counter(row["downstream"]["status"] for row in recipes).items())),
        },
    }
    state.poll()
    return result
