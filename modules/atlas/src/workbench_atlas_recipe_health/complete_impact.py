"""Complete finite dependency exposure, with fixed-point loss and iterative SCCs.

This engine is deliberately separate from the bounded V1 implementation. Its
structural index belongs to one already verified view; no external cache is
trusted. A completed traversal is not a statement of complete runtime evidence.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
import sys
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .impact import (
    _INPUT_RELATIONS, _OUTPUT_RELATIONS, _SELECTOR_RELATIONS,
    _MACHINE_MAP_RELATIONS, _QUEST_RESOURCE_RELATIONS, _TASK_RESOURCE_RELATIONS,
    _node_summary, _edge_summary,
)

FORMAT = "workbench-atlas-recipe-impact-report-v2"
_REPRESENTATIVE_RELATIONS = frozenset({
    "observes-gt-item-input-representative", "observes-gt-fluid-input-representative",
})


class _Poll:
    def __init__(self, callback: Callable[[], None] | None) -> None:
        self.callback = callback or (lambda: None)
        self.work = 0
        self.callback()

    def __call__(self, *, force: bool = False) -> None:
        self.work += 1
        if force or self.work % 256 == 0:
            self.callback()


@dataclass(frozen=True, slots=True)
class _Node:
    key: int
    identity: str
    kind: str
    semantic: str
    properties: str
    evidence: str


@dataclass(frozen=True, slots=True)
class _Edge:
    key: int
    relation: str
    source: int
    target: int
    semantic: str
    properties: str
    evidence: str


@dataclass(frozen=True, slots=True)
class _Arc:
    source: int
    target: int
    edge: int
    direction: str


def _frozen_sets(values: Mapping[int, set[int]]) -> Mapping[int, frozenset[int]]:
    return MappingProxyType({key: frozenset(rows) for key, rows in values.items()})


@dataclass(frozen=True)
class _Index:
    graph_set_id: str
    nodes: Mapping[int, _Node]
    by_id: Mapping[str, int]
    edges: Mapping[int, _Edge]
    outgoing: Mapping[int, tuple[int, ...]]
    incoming: Mapping[int, tuple[int, ...]]
    active_recipes: frozenset[int]
    unknown_recipes: frozenset[int]
    incomplete_selectors: frozenset[int]
    producer_recipes: Mapping[int, frozenset[int]]
    unknown_producers: Mapping[int, frozenset[int]]
    recipe_outputs: Mapping[int, frozenset[int]]
    selector_resources: Mapping[int, frozenset[int]]
    resource_selectors: Mapping[int, frozenset[int]]
    selector_owners: Mapping[int, frozenset[int]]

    @classmethod
    def load(cls, view: Any, poll: _Poll) -> _Index:
        nodes, by_id, edges = {}, {}, {}
        active, unknown, incomplete, selectors = set(), set(), set(), set()
        connection = view.query.connection
        for row in connection.execute("SELECT node_key,id,kind,semantic_key,properties_json,evidence_json FROM nodes ORDER BY node_key"):
            poll()
            node = _Node(row[0], row[1], sys.intern(row[2]), row[3], row[4], row[5])
            nodes[node.key] = node
            by_id[node.identity] = node.key
            if node.kind == "gt-recipe":
                state = json.loads(node.properties).get("lookup_active")
                if state is True:
                    active.add(node.key)
                elif state is not False:
                    unknown.add(node.key)
            elif node.kind == "gt-recipe-input-selector":
                selectors.add(node.key)
                if json.loads(node.properties).get("acceptance_complete", True) is not True:
                    incomplete.add(node.key)
        outgoing, incoming = defaultdict(list), defaultdict(list)
        producers, unknown_producers, outputs = defaultdict(set), defaultdict(set), defaultdict(set)
        accepted, owners = defaultdict(set), defaultdict(set)
        for row in connection.execute("SELECT edge_key,relation,source_node,target_node,semantic_key,properties_json,evidence_json FROM edges ORDER BY edge_key"):
            poll()
            edge = _Edge(row[0], sys.intern(row[1]), row[2], row[3], row[4], row[5], row[6])
            edges[edge.key] = edge
            outgoing[edge.source].append(edge.key)
            incoming[edge.target].append(edge.key)
            if edge.relation in _OUTPUT_RELATIONS and nodes[edge.source].kind == "gt-recipe":
                outputs[edge.source].add(edge.target)
                if edge.source in active:
                    producers[edge.target].add(edge.source)
                elif edge.source in unknown:
                    unknown_producers[edge.target].add(edge.source)
            elif edge.relation in _INPUT_RELATIONS and edge.source in selectors:
                accepted[edge.source].add(edge.target)
            elif edge.relation in _SELECTOR_RELATIONS and nodes[edge.source].kind == "gt-recipe" and edge.target in selectors:
                owners[edge.target].add(edge.source)
        # Missing alternatives are evidence gaps, never empty-set proof of loss.
        incomplete.update(key for key in selectors if not accepted.get(key))
        accepted = {key: rows for key, rows in accepted.items() if key not in incomplete}
        consumers = defaultdict(set)
        for selector, resources in accepted.items():
            poll()
            for resource in resources:
                consumers[resource].add(selector)
        def order(key: int) -> tuple[str, str, str, str]:
            edge = edges[key]
            return edge.relation, nodes[edge.source].identity, nodes[edge.target].identity, edge.semantic
        immutable_out = {}
        immutable_in = {}
        for source, rows in outgoing.items():
            poll()
            immutable_out[source] = tuple(sorted(rows, key=order))
        for target, rows in incoming.items():
            poll()
            immutable_in[target] = tuple(sorted(rows, key=order))
        poll(force=True)
        return cls(view.manifest["graph_set_id"], MappingProxyType(nodes), MappingProxyType(by_id),
                   MappingProxyType(edges), MappingProxyType(immutable_out), MappingProxyType(immutable_in),
                   frozenset(active), frozenset(unknown), frozenset(incomplete),
                   _frozen_sets(producers), _frozen_sets(unknown_producers), _frozen_sets(outputs),
                   _frozen_sets(accepted), _frozen_sets(consumers), _frozen_sets(owners))


class _CompleteImpact:
    def __init__(self, view: Any, index: _Index, selected: int, poll: _Poll) -> None:
        self.view, self.index, self.selected, self.poll = view, index, selected, poll
        self.visited: set[int] = {selected}
        self.traversed: set[int] = set()
        self.nodes: dict[int, dict[str, Any]] = {}
        self.edges: dict[int, dict[str, Any]] = {}
        self.incomplete_seen: set[int] = set()
        self.unknown_seen: set[int] = set()
        self.unknowns: dict[tuple[str, int], dict[str, Any]] = {}
        self.events = 0

    def key_order(self, key: int) -> str:
        return self.index.nodes[key].identity

    def node(self, key: int) -> dict[str, Any]:
        self.poll()
        self.visited.add(key)
        if key not in self.nodes:
            row = self.index.nodes[key]
            self.nodes[key] = self.view.query._node_row((row.identity, row.kind, row.semantic, row.properties, row.evidence))
        return self.nodes[key]

    def summary(self, key: int) -> dict[str, Any]:
        return _node_summary(self.node(key))

    def edge(self, key: int) -> dict[str, Any]:
        self.poll()
        self.traversed.add(key)
        if key not in self.edges:
            row = self.index.edges[key]
            self.edges[key] = self.view.query._edge_row((row.relation, self.key_order(row.source), self.key_order(row.target), row.semantic, row.properties, row.evidence))
        return self.edges[key]

    def edge_node(self, edge: _Edge, target: int) -> dict[str, Any]:
        return {**_edge_summary(self.edge(edge.key)), "node": self.summary(target)}

    def related(self, key: int, relations: frozenset[str], *, reverse: bool = False):
        self.poll()
        self.visited.add(key)
        table = self.index.incoming if reverse else self.index.outgoing
        for edge_key in table.get(key, ()):
            self.poll()
            edge = self.index.edges[edge_key]
            if edge.relation in relations:
                self.traversed.add(edge_key)
                self.visited.add(edge.source if reverse else edge.target)
                yield edge

    def unknown(self, code: str, message: str, key: int) -> None:
        self.unknowns[(code, key)] = {"code": code, "message": message, "subject_id": self.key_order(key)}

    def selector_complete(self, key: int) -> bool:
        if key not in self.index.incomplete_selectors:
            return True
        self.incomplete_seen.add(key)
        self.unknown("selector-acceptance-incomplete", "Captured input evidence does not establish this selector's complete nonempty accepted-resource set.", key)
        return False

    def active(self, key: int) -> bool:
        if key in self.index.unknown_recipes:
            self.unknown_seen.add(key)
            self.unknown("recipe-lookup-state-unavailable", "The captured recipe has no exact boolean lookup activity; absence and viability cannot be inferred.", key)
        return key in self.index.active_recipes

    def producers(self, resource: int) -> tuple[list[_Edge], bool]:
        result = []
        complete = True
        for edge in self.related(resource, _OUTPUT_RELATIONS, reverse=True):
            if self.index.nodes[edge.source].kind != "gt-recipe":
                continue
            if self.active(edge.source):
                result.append(edge)
            elif edge.source in self.index.unknown_recipes:
                complete = False
        return result, complete

    def consumers(self, resource: int) -> tuple[list[dict[str, Any]], bool]:
        result = []
        # Any unavailable matching set could include this resource. Matching
        # closure is a graph evidence limitation, not a traversal limit.
        complete = not self.index.incomplete_selectors
        for edge in self.related(resource, _INPUT_RELATIONS | _REPRESENTATIVE_RELATIONS, reverse=True):
            selector = edge.source
            if not self.selector_complete(selector) or edge.relation not in _INPUT_RELATIONS:
                complete = False
                continue
            for owner in self.related(selector, _SELECTOR_RELATIONS, reverse=True):
                recipe = owner.source
                if self.index.nodes[recipe].kind != "gt-recipe":
                    continue
                if not self.active(recipe):
                    complete = complete and recipe not in self.index.unknown_recipes
                    continue
                result.append({"recipe": self.summary(recipe), "selector": self.summary(selector),
                               "acceptance_edge": _edge_summary(self.edge(edge.key))})
        result.sort(key=lambda row: (row["recipe"]["selection_id"], row["selector"]["selection_id"], row["acceptance_edge"]["semantic_key"]))
        return result, complete

    def direct(self) -> tuple[dict[str, Any], list[int]]:
        inputs, outputs, resources = [], [], set()
        self.active(self.selected)
        for edge in self.related(self.selected, _SELECTOR_RELATIONS | _OUTPUT_RELATIONS):
            if edge.relation in _SELECTOR_RELATIONS:
                selector = edge.target
                if not self.selector_complete(selector):
                    continue
                for accepted in self.related(selector, _INPUT_RELATIONS):
                    inputs.append({**self.edge_node(accepted, accepted.target), "selector": self.summary(selector),
                                   "selector_edge": {"relation": edge.relation, "properties": self.edge(edge.key)["properties"], "evidence": self.edge(edge.key)["evidence"]}})
                continue
            resources.add(edge.target)
            producers, complete = self.producers(edge.target)
            alternatives = [candidate for candidate in producers if candidate.source != self.selected]
            consumers, consumers_complete = self.consumers(edge.target)
            status = (
                "producer-evidence-incomplete" if not complete else
                "no-observed-active-producer" if not producers else
                "observed-alternatives-present" if alternatives else
                "sole-observed-finite-producer"
            )
            outputs.append({"output": self.edge_node(edge, edge.target),
                            "producer_portfolio": {
                                "status": status,
                                "alternative_producers": [{"recipe": self.summary(candidate.source), "output_edge": _edge_summary(self.edge(candidate.key))} for candidate in alternatives],
                                "truncated": False, "evidence_complete": complete, "viability": "not-assessed"},
                            "downstream_consumers": consumers, "downstream_consumers_truncated": False,
                            "downstream_consumers_evidence_complete": consumers_complete})
        return {"inputs": inputs, "outputs": outputs}, sorted(resources, key=self.key_order)

    def propagate(self) -> tuple[dict[str, Any], dict[int, int]]:
        index = self.index
        remaining_producers = {key: len(rows) for key, rows in index.producer_recipes.items()}
        remaining_alternatives = {key: len(rows) for key, rows in index.selector_resources.items()}
        producer_round, selector_round = defaultdict(int), defaultdict(int)
        unavailable = {self.selected: 0}
        resources: dict[int, int] = {}
        queue = deque([("recipe", self.selected, 0)])
        while queue:
            self.poll()
            kind, key, depth = queue.popleft()
            self.events += 1
            self.visited.add(key)
            if kind == "recipe":
                for output in self.related(key, _OUTPUT_RELATIONS):
                    self.visited.add(output.target)
                for resource in sorted(index.recipe_outputs.get(key, ()), key=self.key_order):
                    if key not in index.producer_recipes.get(resource, ()):
                        continue
                    remaining_producers[resource] -= 1
                    producer_round[resource] = max(producer_round[resource], depth)
                    if remaining_producers[resource] != 0 or resource in resources:
                        continue
                    if index.unknown_producers.get(resource):
                        for unknown in index.unknown_producers[resource]:
                            self.active(unknown)
                        continue
                    resources[resource] = producer_round[resource]
                    queue.append(("resource", resource, resources[resource]))
            else:
                for edge in self.related(key, _INPUT_RELATIONS | _REPRESENTATIVE_RELATIONS, reverse=True):
                    if edge.source in index.incomplete_selectors:
                        self.selector_complete(edge.source)
                for selector in sorted(index.resource_selectors.get(key, ()), key=self.key_order):
                    self.poll()
                    self.visited.add(selector)
                    remaining_alternatives[selector] -= 1
                    selector_round[selector] = max(selector_round[selector], depth)
                    if remaining_alternatives[selector] != 0:
                        continue
                    for edge in self.related(selector, _SELECTOR_RELATIONS, reverse=True):
                        self.visited.add(edge.source)
                    for recipe in sorted(index.selector_owners.get(selector, ()), key=self.key_order):
                        if recipe in unavailable or not self.active(recipe):
                            continue
                        unavailable[recipe] = selector_round[selector] + 1
                        queue.append(("recipe", recipe, unavailable[recipe]))
        resource_rows, recipe_rows = [], []
        for key in sorted(resources, key=self.key_order):
            self.poll()
            resource_rows.append({"resource": self.summary(key), "depth": resources[key],
                                  "reason": "all-observed-finite-producers-are-unavailable-candidates",
                                  "all_observed_finite_producers": sorted(self.key_order(recipe) for recipe in index.producer_recipes[key])})
        for key in sorted(set(unavailable) - {self.selected}, key=self.key_order):
            self.poll()
            blocked = []
            for edge in self.related(key, _SELECTOR_RELATIONS):
                selector = edge.target
                accepted = index.selector_resources.get(selector, frozenset())
                if self.selector_complete(selector) and accepted and accepted <= resources.keys():
                    blocked.append({"selector": self.summary(selector),
                                    "accepted_resources": [self.summary(resource) for resource in sorted(accepted, key=self.key_order)]})
            recipe_rows.append({"recipe": self.summary(key), "depth": unavailable[key],
                                "reason": "at-least-one-selector-has-only-at-risk-observed-alternatives",
                                "blocked_selectors": blocked})
        return {"status": "evidence-incomplete" if index.incomplete_selectors or index.unknown_recipes else "complete-within-model",
                "at_risk_resources": resource_rows, "at_risk_recipes": recipe_rows}, resources

    def dependency_graph(self, roots: list[int]) -> dict[int, tuple[_Arc, ...]]:
        adjacency: dict[int, tuple[_Arc, ...]] = {}
        queued = set(roots)
        queue = deque(roots)
        while queue:
            self.poll()
            key = queue.popleft()
            self.visited.add(key)
            kind = self.index.nodes[key].kind
            arcs = []
            if kind == "gt-recipe":
                for edge in self.related(key, _SELECTOR_RELATIONS):
                    arcs.append(_Arc(key, edge.target, edge.key, "forward"))
            elif kind == "gt-recipe-input-selector":
                if self.selector_complete(key):
                    for edge in self.related(key, _INPUT_RELATIONS):
                        arcs.append(_Arc(key, edge.target, edge.key, "forward"))
            else:
                producers, _ = self.producers(key)
                for edge in producers:
                    if edge.source != self.selected:
                        arcs.append(_Arc(key, edge.source, edge.key, "reverse"))
            adjacency[key] = tuple(sorted(arcs, key=lambda arc: (self.key_order(arc.target), arc.direction, self.index.edges[arc.edge].semantic)))
            for arc in arcs:
                if arc.target not in queued:
                    queued.add(arc.target)
                    queue.append(arc.target)
        return adjacency

    def components(self, adjacency: Mapping[int, tuple[_Arc, ...]], roots: list[int], *, quest: bool = False) -> list[dict[str, Any]]:
        """Iterative Tarjan: one discovery and completion per vertex, no paths."""
        discovery, low = {}, {}
        active, active_set = [], set()
        groups: list[tuple[int, ...]] = []
        sequence = 0
        for start in sorted(adjacency, key=self.key_order):
            self.poll()
            if start in discovery:
                continue
            discovery[start] = low[start] = sequence
            sequence += 1
            active.append(start)
            active_set.add(start)
            frames = [(start, iter(adjacency[start]))]
            while frames:
                self.poll()
                key, iterator = frames[-1]
                arc = next(iterator, None)
                if arc is not None:
                    child = arc.target
                    if child not in discovery:
                        discovery[child] = low[child] = sequence
                        sequence += 1
                        active.append(child)
                        active_set.add(child)
                        frames.append((child, iter(adjacency[child])))
                    elif child in active_set:
                        low[key] = min(low[key], discovery[child])
                    continue
                frames.pop()
                if frames:
                    parent = frames[-1][0]
                    low[parent] = min(low[parent], low[key])
                if low[key] == discovery[key]:
                    members = []
                    while True:
                        member = active.pop()
                        active_set.remove(member)
                        members.append(member)
                        if member == key:
                            break
                    groups.append(tuple(sorted(members, key=self.key_order)))
        groups.sort(key=lambda members: self.key_order(members[0]))
        membership = {node: component for component, members in enumerate(groups) for node in members}
        successors = defaultdict(set)
        indegree = [0] * len(groups)
        for source, arcs in adjacency.items():
            self.poll()
            for arc in arcs:
                left, right = membership[source], membership[arc.target]
                if left != right and right not in successors[left]:
                    successors[left].add(right)
                    indegree[right] += 1
        root_masks = [0] * len(groups)
        for position, root in enumerate(roots):
            root_masks[membership[root]] |= 1 << position
        ready = deque(index for index, degree in enumerate(indegree) if degree == 0)
        while ready:
            self.poll()
            component = ready.popleft()
            for target in sorted(successors[component]):
                root_masks[target] |= root_masks[component]
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        result = []
        for component, members in enumerate(groups):
            self.poll()
            if len(members) == 1 and not any(arc.target == members[0] for arc in adjacency[members[0]]):
                continue
            member_set = frozenset(members)
            first = members[0]
            initial = next(arc for arc in adjacency[first] if arc.target in member_set)
            previous: dict[int, _Arc] = {}
            if initial.target != first:
                queue = deque([initial.target])
                seen = {initial.target}
                while queue and first not in seen:
                    self.poll()
                    key = queue.popleft()
                    for arc in adjacency[key]:
                        if arc.target in member_set and arc.target not in seen:
                            seen.add(arc.target)
                            previous[arc.target] = arc
                            queue.append(arc.target)
                tail = []
                key = first
                while key != initial.target:
                    arc = previous[key]
                    tail.append(arc)
                    key = arc.source
                witness = [initial, *reversed(tail)]
            else:
                witness = [initial]
            identities = [self.key_order(key) for key in members]
            component_id = "workbench-atlas-dependency-component-v2:sha256:" + hashlib.sha256(json.dumps(identities, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            result.append({
                "component_id": component_id, "member_node_ids": identities,
                "root_resource_ids": sorted(self.key_order(root) for position, root in enumerate(roots) if root_masks[component] & (1 << position)),
                "witness": {"node_ids": [self.key_order(witness[0].source), *(self.key_order(arc.target) for arc in witness)],
                            "arcs": [{"source_id": self.key_order(arc.source), "target_id": self.key_order(arc.target),
                                      "edge_id": self.edge(arc.edge)["id"], "relation": self.index.edges[arc.edge].relation,
                                      "direction": arc.direction} for arc in witness]},
                "interpretation": "observed BetterQuesting prerequisite dependency component" if quest else "mutually reachable nodes in observed alternative-producer input dependencies; one witness is shown, not every cycle",
                "viability_effect": "unknown",
            })
        return result

    def quest_requirements(self, resource: int, risk_status: str) -> list[dict[str, Any]]:
        rows = []
        for reference in self.related(resource, _QUEST_RESOURCE_RELATIONS, reverse=True):
            occurrence = reference.source
            ores = [{"ore_dictionary_key": self.summary(edge.target), "acceptance_edge": _edge_summary(self.edge(edge.key))}
                    for edge in self.related(occurrence, frozenset({"accepts-progression-ore-dictionary-key"}))]
            for task_edge in self.related(occurrence, _TASK_RESOURCE_RELATIONS, reverse=True):
                task = task_edge.source
                for owner in self.related(task, frozenset({"owns-progression-task"}), reverse=True):
                    rows.append({"quest": self.summary(owner.source), "task": self.summary(task),
                                 "requirement_occurrence": self.summary(occurrence), "resource": self.summary(resource),
                                 "resource_risk_status": risk_status,
                                 "requirement_semantics": "ore-dictionary-selector-with-observed-item-representative" if ores else "exact-observed-resource-reference",
                                 "accepted_ore_dictionary_keys": ores, "resource_edge": _edge_summary(self.edge(reference.key)),
                                 "task_edge": _edge_summary(self.edge(task_edge.key)), "owner_edge": _edge_summary(self.edge(owner.key))})
        return sorted(rows, key=lambda row: (row["quest"]["selection_id"], row["task"]["selection_id"], row["requirement_occurrence"]["selection_id"]))

    def quest_dependents(self, roots: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        depths = {root: 0 for root in roots}
        queue = deque(roots)
        rows, adjacency = {}, {}
        while queue:
            self.poll()
            quest = queue.popleft()
            quest_arcs = []
            for target in self.related(quest, frozenset({"targets-progression-prerequisite"}), reverse=True):
                occurrence = target.source
                quest_arcs.append(_Arc(quest, occurrence, target.key, "reverse"))
                occurrence_arcs = []
                for owner in self.related(occurrence, frozenset({"owns-progression-prerequisite"}), reverse=True):
                    dependent = owner.source
                    occurrence_arcs.append(_Arc(occurrence, dependent, owner.key, "reverse"))
                    rows[(dependent, quest, occurrence)] = {"quest": self.summary(dependent), "depends_on": self.summary(quest),
                                                          "prerequisite_occurrence": self.summary(occurrence), "depth": depths[quest] + 1,
                                                          "target_edge": _edge_summary(self.edge(target.key)), "owner_edge": _edge_summary(self.edge(owner.key))}
                    if dependent not in depths:
                        depths[dependent] = depths[quest] + 1
                        queue.append(dependent)
                adjacency[occurrence] = tuple(sorted(occurrence_arcs, key=lambda arc: (self.key_order(arc.target), arc.edge)))
            adjacency[quest] = tuple(sorted(quest_arcs, key=lambda arc: (self.key_order(arc.target), arc.edge)))
        # A cyclic edge may return to a seed, or a longer route may converge on
        # a quest already reached by a shorter route. Depth belongs to the
        # dependent quest's BFS distance, not to this individual edge walk.
        for row in rows.values():
            self.poll()
            row["depth"] = depths[self.index.by_id[row["quest"]["selection_id"]]]
        result = sorted(rows.values(), key=lambda row: (row["quest"]["selection_id"], row["depends_on"]["selection_id"], row["prerequisite_occurrence"]["selection_id"]))
        return result, self.components(adjacency, [], quest=True)

    def progression(self, outputs: list[int], at_risk: Mapping[int, int]) -> dict[str, Any]:
        props = self.node(self.selected)["properties"]
        recipe_map = None
        machines = []
        for binding in self.related(self.selected, frozenset({"contained-in-recipe-map"})):
            recipe_map = self.summary(binding.target)
            for machine in self.related(binding.target, _MACHINE_MAP_RELATIONS, reverse=True):
                machines.append({"machine": self.summary(machine.source), "map_edge": _edge_summary(self.edge(machine.key))})
        machines.sort(key=lambda row: row["machine"]["selection_id"])
        affected = {key: "producer-portfolio-changed" for key in outputs}
        affected.update({key: "observed-all-finite-producers-at-risk" for key in at_risk})
        requirements = []
        quests = set()
        for resource in sorted(affected, key=self.key_order):
            self.poll()
            rows = self.quest_requirements(resource, affected[resource])
            requirements.extend(rows)
            quests.update(self.index.by_id[row["quest"]["selection_id"]] for row in rows)
        dependents, components = self.quest_dependents(sorted(quests, key=self.key_order))
        return {
            "energy_and_machine_signals": {
                "observed_recipe_properties": {key: props.get(key) for key in ("eut", "duration", "recipe_map", "hidden", "lookup_active")},
                "recipe_map": recipe_map, "machines_observed_using_recipe_map": machines,
                "machine_numeric_tiers_observed": sorted({row["machine"]["properties"]["tier"] for row in machines if type(row["machine"]["properties"].get("tier")) is int}),
                "truncated": False, "evidence_complete": recipe_map is not None,
                "interpretation": "numeric EUT and machine tier properties are observed signals; no pack progression tier is inferred",
            },
            "quest_signals": {
                "status": "observed-definition-references" if requirements else ("no-observed-reference" if any(self.view.relations.get(relation, 0) for relation in _QUEST_RESOURCE_RELATIONS) else "evidence-unavailable"),
                "direct_resource_requirements": requirements, "structural_prerequisite_dependents": dependents,
                "prerequisite_cycle_components": components,
                "interpretation": "these are definition references and prerequisite structure, not observed player blockage or task execution",
            },
        }

    def build(self) -> dict[str, Any]:
        direct, outputs = self.direct()
        propagation, at_risk = self.propagate()
        adjacency = self.dependency_graph(outputs)
        components = self.components(adjacency, outputs)
        propagation["alternative_dependency_components"] = components
        progression = self.progression(outputs, at_risk)
        quest = progression["quest_signals"]
        report = {
            "format": FORMAT, "schema_version": 2,
            "context": self.view.describe(), "selection": self.summary(self.selected),
            "scenario": {"kind": "remove-exact-observed-recipe", "selected_recipe_assumed_unavailable": True,
                         "change_interpretation": "a recipe change is assessed only as loss of the selected current recipe; replacement inputs, outputs, and runtime behavior require a new observed graph or a separately validated proposed-recipe model"},
            "analysis_model": {"kind": "complete-observed-finite-recipe-dependency-exposure",
                               "resource_at_risk_rule": "every observed finite recipe producer is an unavailable candidate",
                               "recipe_at_risk_rule": "at least one exact input selector accepts only resources already classified at risk",
                               "claim_boundary": "candidate dead paths inside observed finite recipe structure; not gameplay reachability"},
            "exploration": {"mode": "complete-finite", "status": "complete",
                            "graph_node_count": len(self.index.nodes), "graph_edge_count": len(self.index.edges),
                            "visited_node_count": len(self.visited), "traversed_edge_count": len(self.traversed),
                            "worklist_event_count": self.events, "cycle_algorithm": "iterative-scc"},
            "evidence_completeness": {
                "status": "incomplete" if self.index.incomplete_selectors or self.index.unknown_recipes else "complete-within-declared-model",
                "graph_incomplete_selector_count": len(self.index.incomplete_selectors),
                "graph_unknown_lookup_recipe_count": len(self.index.unknown_recipes),
                "encountered_incomplete_selector_ids": sorted(self.key_order(key) for key in self.incomplete_seen),
                "encountered_unknown_lookup_recipe_ids": sorted(self.key_order(key) for key in self.unknown_seen),
            },
            "direct": direct, "propagation": propagation, "progression_signals": progression,
            "frontiers": [], "unknowns": sorted(self.unknowns.values(), key=lambda row: (row["code"], row["subject_id"])),
            "evidence_gaps": self.evidence_gaps(),
            "summary": {
                "selected_output_count": len(direct["outputs"]),
                "sole_observed_finite_producer_output_count": sum(row["producer_portfolio"]["status"] == "sole-observed-finite-producer" for row in direct["outputs"]),
                "at_risk_resource_candidate_count": len(propagation["at_risk_resources"]),
                "at_risk_recipe_candidate_count": len(propagation["at_risk_recipes"]),
                "quest_requirement_exposure_count": len(quest["direct_resource_requirements"]),
                "structural_quest_dependent_count": len(quest["structural_prerequisite_dependents"]),
                "alternative_dependency_component_count": len(components),
                "quest_prerequisite_component_count": len(quest["prerequisite_cycle_components"]),
                "truncated": False,
            },
        }
        self.poll(force=True)
        return report

    def evidence_gaps(self) -> list[dict[str, str]]:
        return [
            {"code": "counterfactual-runtime-not-observed", "message": "Atlas did not execute a runtime with the selected recipe removed or changed."},
            {"code": "non-recipe-acquisition-not-assessed", "message": "World generation, loot, trade, inventory, commands, and other acquisition domains are not treated as alternative producers."},
            {"code": "dynamic-recipes-not-executed", "message": "Procedural and contextual recipe rules were not invoked for this counterfactual."},
            {"code": "stoichiometry-and-chance-not-assessed", "message": "Amounts, reusable inputs, probabilities, throughput, and inventory balance do not establish or refute availability here."},
            {"code": "progression-reachability-not-proven", "message": "Quest definitions, finite recipes, and machine signals do not prove player reachability or a broken progression path."},
            {"code": "task-execution-not-invoked", "message": "BetterQuesting task matching, completion, and reward execution were not invoked."},
            {"code": "pack-tier-policy-not-applied", "message": "Numeric EUT and observed machine tier fields are reported without deriving a pack progression tier."},
            *(gap for gap in self.view._evidence_gaps(role="recipe") if gap["code"] == "graph-projection-limitations"),
        ]


def derive_complete_recipe_impact(view: Any, selection_id: str, *, check_cancelled: Callable[[], None] | None = None) -> dict[str, Any]:
    """Exhaust admitted finite dependencies; cancellation never returns a report."""
    from .view import GraphRecipeHealthView, RecipeHealthError, _text

    if not isinstance(view, GraphRecipeHealthView):
        raise RecipeHealthError("complete recipe impact requires a verified categorical graph, not a source-only view")
    if check_cancelled is not None and not callable(check_cancelled):
        raise RecipeHealthError("complete recipe impact cancellation callback must be callable")
    selection_id = _text(selection_id, "recipe-health selection ID")
    poll = _Poll(check_cancelled)
    index = getattr(view, "_complete_impact_index", None)
    if index is None:
        index = _Index.load(view, poll)
        # Publish the cache only after a fully constructed, uncancelled index.
        view._complete_impact_index = index
    elif not isinstance(index, _Index) or index.graph_set_id != view.manifest["graph_set_id"]:
        raise RecipeHealthError("complete recipe impact structural index belongs to another graph")
    selected = index.by_id.get(selection_id)
    if selected is None:
        raise RecipeHealthError("recipe-health selection does not exist in this graph")
    if index.nodes[selected].kind != "gt-recipe":
        raise RecipeHealthError("complete recipe impact requires one exact observed gt-recipe selection")
    return _CompleteImpact(view, index, selected, poll).build()
