"""Bounded read-only navigation of content-bound source declarations.

Resource keys are not declarations. Material, fluid, item, ore and quest
identities remain separate, connected only by the interpreter's typed edges.
Nothing in this view establishes runtime registration or progression.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
from hashlib import sha256

from workbench_api.source_declarations import (
    key_identity,
    validate_navigation_declarations,
)


class SourceNavigationError(ValueError):
    pass


def _bound(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise SourceNavigationError(f"{name} must be between {low} and {high}")
    return value


class SourceNavigation:
    def __init__(self, declarations):
        feed = validate_navigation_declarations(deepcopy(declarations))
        self._feed = feed
        self._nodes = {}
        self._edges = []
        self._adjacency = defaultdict(list)
        providers = defaultdict(list)
        for row in feed["declarations"]:
            nav = row["attributes"]["navigation"]
            node = self._id(row["source_declaration_id"])
            self._nodes[node] = {
                "selection_id": node,
                "kind": nav["kind"],
                "label": nav["label"],
                "semantic_key": row["semantic_descriptor"],
                "source_declaration_id": row["source_declaration_id"],
                "location": nav["location"],
                "issues": nav["issues"],
                "declaration": row,
            }
            for key in nav["provides"]:
                providers[key_identity(key)].append(node)
        for node, record in tuple(self._nodes.items()):
            nav = record["declaration"]["attributes"]["navigation"]
            for key in nav["provides"]:
                self._edge(node, self._resource(key), "defines", "declared", {})
            for ref in nav["references"]:
                target = ref["target"]
                state = "unresolved"
                if target is not None:
                    count = len(providers[key_identity(target)])
                    state = (
                        "ambiguous"
                        if count > 1
                        else "declared"
                        if count == 1
                        else "dangling"
                        if target["kind"] in {"quest", "quest-line"}
                        else "external-definition-unknown"
                    )
                self._edge(
                    node,
                    None if target is None else self._resource(target),
                    ref["relation"],
                    state,
                    ref["details"],
                )
        for record in self._nodes.values():
            if record["kind"] == "resource":
                matches = providers[key_identity(record["semantic_key"])]
                record["definitions"] = matches
                if len(matches) > 1:
                    record["issues"].append("ambiguous-definitions")
        self._states = dict(
            sorted(Counter(edge["state"] for edge in self._edges).items())
        )
        self._cycles = self._prerequisite_cycles(providers)

    def _id(self, key):
        return (
            "source-selection:sha256:"
            + sha256(
                (self._feed["declaration_set_id"] + "\0" + key).encode()
            ).hexdigest()
        )

    def _resource(self, key):
        node = self._id("resource:" + key_identity(key))
        self._nodes.setdefault(
            node,
            {
                "selection_id": node,
                "kind": "resource",
                "label": key_identity(key),
                "semantic_key": key,
                "location": None,
                "issues": [],
                "source_declaration_id": None,
            },
        )
        return node

    def _edge(self, source, target, relation, state, details):
        index = len(self._edges)
        self._edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "state": state,
                "details": details,
            }
        )
        self._adjacency[source].append(index)
        if target is not None:
            self._adjacency[target].append(index)

    def _prerequisite_cycles(self, providers):
        # Iterative DFS: deep prerequisite chains cannot exhaust Python's stack.
        adjacency = defaultdict(list)
        for record in self._nodes.values():
            if record["kind"] != "quest":
                continue
            for ref in record["declaration"]["attributes"]["navigation"]["references"]:
                if ref["relation"] == "requires-quest" and ref["target"] is not None:
                    targets = providers[key_identity(ref["target"])]
                    if len(targets) == 1:
                        adjacency[record["selection_id"]].append(targets[0])
        done, active, cycles = set(), set(), []
        for start in sorted(adjacency):
            if start in done:
                continue
            stack = [(start, iter(adjacency[start]))]
            active.add(start)
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    stack.pop()
                    active.remove(node)
                    done.add(node)
                elif child in active:
                    cycles.append({"source": node, "target": child})
                    if len(cycles) == 50:
                        return {"back_edges": cycles, "truncated": True}
                elif child not in done:
                    active.add(child)
                    stack.append((child, iter(adjacency[child])))
        return {"back_edges": cycles, "truncated": False}

    def describe(self):
        return deepcopy(
            {
                "format": "workbench-source-navigation-v1",
                "declaration_set_id": self._feed["declaration_set_id"],
                "binding": self._feed["binding"],
                "authority": self._feed["authority"],
                "counts": {
                    "declarations": len(self._feed["declarations"]),
                    "nodes": len(self._nodes),
                    "relationships": len(self._edges),
                },
                "relationship_states": self._states,
                "prerequisite_cycles": self._cycles,
                "limitations": self._feed["limitations"],
            }
        )

    def declaration_summaries(self):
        """Detached declaration locations/keys for other source-only read models."""
        return [deepcopy(self._summary(node)) for node in self._nodes.values()
                if node["kind"] != "resource"]

    def _selected(self, selection):
        if not isinstance(selection, str) or selection not in self._nodes:
            raise SourceNavigationError(
                "source selection is stale or belongs to another input snapshot; search again"
            )
        return self._nodes[selection]

    @staticmethod
    def _summary(node):
        return {key: value for key, value in node.items() if key != "declaration"}

    def search(self, text, *, kind=None, limit=50):
        _bound(limit, 1, 200, "limit")
        if not isinstance(text, str) or len(text) > 512:
            raise SourceNavigationError("search text must be bounded text")
        if kind not in {
            None,
            "recipe",
            "material",
            "quest",
            "quest-line",
            "resource",
            "unsupported",
        }:
            raise SourceNavigationError("unsupported source kind")
        needle = text.casefold()
        matches = []
        for node in self._nodes.values():
            if (kind is None or node["kind"] == kind) and needle in (
                node["label"] + " " + key_identity(node["semantic_key"])
            ).casefold():
                matches.append(self._summary(node))
                if len(matches) > limit:
                    break
        return deepcopy(
            {
                "context": self.describe(),
                "results": matches[:limit],
                "truncated": len(matches) > limit,
            }
        )

    def inspect(self, selection):
        node = self._selected(selection)
        return deepcopy(
            {
                "context": self.describe(),
                "selection": node,
                "relationships": [
                    self._edges[index] for index in self._adjacency[selection]
                ][:1000],
                "truncated": len(self._adjacency[selection]) > 1000,
            }
        )

    def location(self, selection):
        node = self._selected(selection)
        if node["location"] is None:
            raise SourceNavigationError(
                "a referenced resource has no declaration location; inspect its definitions or references"
            )
        return deepcopy(
            {
                "context": self.describe(),
                "selection_id": selection,
                "location": node["location"],
            }
        )

    def related(self, selection, *, max_depth=2, max_nodes=100, max_edges=500):
        self._selected(selection)
        _bound(max_depth, 0, 8, "max_depth")
        _bound(max_nodes, 1, 200, "max_nodes")
        _bound(max_edges, 1, 1000, "max_edges")
        queue = deque([(selection, 0)])
        nodes, edges, seen_edges, frontier = {selection}, [], set(), []
        examined = 0
        while queue:
            node, depth = queue.popleft()
            if depth == max_depth:
                if self._adjacency[node]:
                    frontier.append({"selection_id": node, "reason": "depth-bound"})
                continue
            for index in self._adjacency[node]:
                examined += 1
                if examined > 4000 or len(edges) == max_edges:
                    frontier.append({"selection_id": node, "reason": "edge-bound"})
                    queue.clear()
                    break
                if index in seen_edges:
                    continue
                seen_edges.add(index)
                edge = self._edges[index]
                other = edge["target"] if edge["source"] == node else edge["source"]
                if other is not None and other not in nodes:
                    if len(nodes) == max_nodes:
                        frontier.append({"selection_id": node, "reason": "node-bound"})
                        continue
                    nodes.add(other)
                    queue.append((other, depth + 1))
                edges.append(edge)
        return deepcopy(
            {
                "context": self.describe(),
                "selection_id": selection,
                "nodes": [self._summary(self._nodes[node]) for node in sorted(nodes)],
                "relationships": edges,
                "frontier": frontier[:200],
                "truncated": bool(frontier),
                "traversal": "both-directions; declarations and typed resources remain distinct",
            }
        )
