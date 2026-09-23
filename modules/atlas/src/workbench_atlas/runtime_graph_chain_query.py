#!/usr/bin/env python3

"""Bounded, evidence-preserving process-chain queries over a runtime graph.

The traversal deliberately returns a route DAG.  A recipe is a producer
hyperedge: its distinct input slots are jointly required, while the candidates
inside one slot remain alternatives.  The implementation never enumerates the
Cartesian product of those alternatives and never chooses a "best" route.
"""

from __future__ import annotations

import copy
import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from workbench_atlas.runtime_graph_domain_query import (
    GraphScope,
    ProfileScope,
    RuntimeGraphDomainQuery,
    ScopedTarget,
    alternative_semantics,
)
from workbench_atlas.runtime_graph_query import (
    NodeSelector,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
)

PROCEDURAL_OWNER_KINDS = frozenset({"recipe_rule", "process_rule"})
PRESENTATION_OWNER_KINDS = frozenset({"recipe_wrapper", "recipe_example"})
INPUT_PREDICATES = ("consumes", "may_consume", "requires")
OUTPUT_PREDICATES = ("produces", "may_produce")
TRUNCATION_REASONS = (
    "max-depth",
    "max-routes",
    "max-alternatives-per-slot",
    "max-visited-nodes",
)


@dataclass(frozen=True)
class ChainOptions:
    """Hard bounds and inclusion policy for one process-chain traversal."""

    max_depth: int = 8
    max_routes: int = 50
    max_alternatives_per_slot: int = 25
    include_chanced_outputs: bool = True
    include_procedural_rules: bool = True
    max_visited_nodes: int = 10000

    def __post_init__(self) -> None:
        self._bounded_integer("max_depth", self.max_depth, minimum=0)
        self._bounded_integer("max_routes", self.max_routes, minimum=1)
        self._bounded_integer(
            "max_alternatives_per_slot",
            self.max_alternatives_per_slot,
            minimum=1,
        )
        self._bounded_integer(
            "max_visited_nodes",
            self.max_visited_nodes,
            minimum=1,
        )
        for name, value in (
            ("include_chanced_outputs", self.include_chanced_outputs),
            ("include_procedural_rules", self.include_procedural_rules),
        ):
            if type(value) is not bool:
                raise RuntimeGraphQueryError(
                    f"chain option {name} must be boolean"
                )

    @staticmethod
    def _bounded_integer(name: str, value: object, *, minimum: int) -> None:
        if type(value) is not int or value < minimum:
            raise RuntimeGraphQueryError(
                f"chain option {name} must be an integer greater than or "
                f"equal to {minimum}"
            )

    def limits(self) -> dict[str, int]:
        """Return the reader-facing structural limits using contract keys."""

        return {
            "max_depth": self.max_depth,
            "max_routes": self.max_routes,
            "max_alternatives_per_slot": self.max_alternatives_per_slot,
            "max_visited_nodes": self.max_visited_nodes,
        }


def _scope_dict(scope: object) -> dict[str, str]:
    profile = getattr(scope, "profile", None)
    physical_side = getattr(scope, "physical_side", None)
    if not isinstance(profile, str) or not isinstance(physical_side, str):
        raise RuntimeGraphQueryError("domain query returned an invalid graph scope")
    return {"profile": profile, "physical_side": physical_side}


def _selector_dict(selector: NodeSelector) -> dict[str, str]:
    result = {
        "key_kind": selector.key_kind,
        "key_value": selector.key_value,
    }
    if selector.kind is not None:
        result["kind"] = selector.kind
    if selector.profile is not None:
        result["profile"] = selector.profile
    if selector.physical_side is not None:
        result["physical_side"] = selector.physical_side
    return result


def _record_id(record: object) -> str:
    if not isinstance(record, dict) or not isinstance(record.get("id"), str):
        raise RuntimeGraphQueryError("domain query returned a record without an ID")
    return str(record["id"])


def _record_kind(record: object) -> str:
    if not isinstance(record, dict) or not isinstance(record.get("kind"), str):
        raise RuntimeGraphQueryError(
            "resolved chain target record must have a kind"
        )
    return str(record["kind"])


def _scoped_target_key(target: ScopedTarget) -> tuple[str, str, str, str]:
    scope = _scope_dict(target.scope)
    return (
        scope["profile"],
        scope["physical_side"],
        _record_kind(target.node),
        _record_id(target.node),
    )


def _resolved_target_selector(target: ScopedTarget) -> NodeSelector:
    scope = _scope_dict(target.scope)
    return NodeSelector.by_id(
        _record_id(target.node),
        kind=_record_kind(target.node),
        profile=scope["profile"],
        physical_side=scope["physical_side"],
    )


def _normalize_resolved_targets(
    reader: RuntimeGraphReader,
    targets: Iterable[ScopedTarget],
    *,
    require_nonempty: bool = True,
) -> tuple[ScopedTarget, ...]:
    """Validate and deterministically deduplicate exact traversal roots."""

    try:
        candidates = tuple(targets)
    except TypeError as exc:
        raise RuntimeGraphQueryError(
            "resolved process-chain targets must be an iterable of "
            "ScopedTarget values"
        ) from exc
    if require_nonempty and not candidates:
        raise RuntimeGraphQueryError(
            "resolved process-chain traversal requires at least one target"
        )

    by_key: dict[tuple[str, str, str, str], ScopedTarget] = {}
    for index, target in enumerate(candidates):
        if not isinstance(target, ScopedTarget):
            raise RuntimeGraphQueryError(
                "resolved process-chain target must be a ScopedTarget: "
                f"index {index}"
            )
        node = target.node
        if not isinstance(node, dict) or node.get("record_type") != "node":
            raise RuntimeGraphQueryError(
                "resolved process-chain target must contain a node record: "
                f"index {index}"
            )
        node_scope = node.get("scope")
        scope = _scope_dict(target.scope)
        if (
            not isinstance(node_scope, dict)
            or node_scope.get("profile") != scope["profile"]
            or node_scope.get("physical_side") != scope["physical_side"]
        ):
            raise RuntimeGraphQueryError(
                "resolved process-chain target scope differs from its node "
                f"record: index {index}"
            )
        selector = _resolved_target_selector(target)
        matches = reader.resolve_exact(selector)
        if len(matches) != 1:
            raise RuntimeGraphQueryError(
                "resolved process-chain target is not an exact node in the "
                f"open graph: {_record_id(node)}"
            )
        if matches[0] != node:
            raise RuntimeGraphQueryError(
                "resolved process-chain target record differs from the open "
                f"graph: {_record_id(node)}"
            )
        key = _scoped_target_key(target)
        previous = by_key.get(key)
        if previous is not None and previous.node != node:
            raise RuntimeGraphQueryError(
                "resolved process-chain targets disagree on one exact node: "
                + _record_id(node)
            )
        by_key[key] = target
    return tuple(by_key[key] for key in sorted(by_key))


def _cyclic_components(
    adjacency: dict[str, list[str]],
) -> list[tuple[str, ...]]:
    """Return strongly connected components that contain a cycle."""

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[tuple[str, ...]] = []

    def strongconnect(vertex: str) -> None:
        nonlocal index
        indices[vertex] = index
        lowlinks[vertex] = index
        index += 1
        stack.append(vertex)
        on_stack.add(vertex)
        for neighbor in adjacency[vertex]:
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlinks[vertex] = min(
                    lowlinks[vertex],
                    lowlinks[neighbor],
                )
            elif neighbor in on_stack:
                lowlinks[vertex] = min(
                    lowlinks[vertex],
                    indices[neighbor],
                )
        if lowlinks[vertex] == indices[vertex]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == vertex:
                    break
            normalized = tuple(sorted(component))
            if len(normalized) > 1 or vertex in adjacency[vertex]:
                components.append(normalized)

    for vertex in sorted(adjacency):
        if vertex not in indices:
            strongconnect(vertex)

    return components


def _record_attributes(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    attributes = record.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _integer_attribute(record: object, key: str) -> int:
    value = _record_attributes(record).get(key)
    if type(value) is int:
        return value
    return 2**63 - 1


def _alternative_order(row: dict[str, Any]) -> tuple[int, str, str, str]:
    relationship = row.get("relationship")
    node = row.get("node")
    return (
        _integer_attribute(relationship, "alternative_ordinal"),
        str(node.get("kind", "")) if isinstance(node, dict) else "",
        _record_id(node),
        _record_id(relationship),
    )


def _slot_order(row: dict[str, Any]) -> tuple[int, str, str, str]:
    relationship = row.get("relationship")
    slot = row.get("slot")
    slot_ordinal = _integer_attribute(slot, "ordinal")
    if slot_ordinal == 2**63 - 1:
        slot_ordinal = _integer_attribute(relationship, "ordinal")
    return (
        slot_ordinal,
        str(relationship.get("predicate", ""))
        if isinstance(relationship, dict)
        else "",
        _record_id(slot),
        _record_id(relationship),
    )


def _occurrence_order(row: dict[str, Any]) -> tuple[str, ...]:
    scope = row.get("scope")
    owner = row.get("owner")
    relationship = row.get("owner_relationship")
    slot = row.get("slot")
    matched = row.get("matched_alternatives", ())
    matched_ids = tuple(
        _record_id(value["relationship"])
        for value in matched
        if isinstance(value, dict) and "relationship" in value
    )
    return (
        str(scope.get("profile", "")) if isinstance(scope, dict) else "",
        str(scope.get("physical_side", "")) if isinstance(scope, dict) else "",
        str(owner.get("kind", "")) if isinstance(owner, dict) else "",
        _record_id(owner),
        _record_id(relationship),
        _record_id(slot),
        *matched_ids,
    )


def _chance_projection(*records: object) -> dict[str, dict[str, Any]]:
    """Retain chance-bearing attributes without interpreting their units."""

    result: dict[str, dict[str, Any]] = {}
    labels = ("owner_relationship", "slot", "alternative_relationship")
    for label, record in zip(labels, records):
        chance = {
            key: value
            for key, value in _record_attributes(record).items()
            if "chance" in key.lower() or "probab" in key.lower()
        }
        if chance:
            result[label] = chance
    return result


def _has_chance(*records: object) -> bool:
    return any(
        "chance" in key.lower() or "probab" in key.lower()
        for record in records
        for key in _record_attributes(record)
    )


def _json_tree(value: Any) -> Any:
    """Convert query tuples into canonical-JSON-compatible list trees."""

    if isinstance(value, dict):
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_tree(child) for child in value]
    return value


def _normalize_graph_scope(
    value: GraphScope | ProfileScope | Iterable[ProfileScope] | None,
) -> GraphScope | None:
    if value is None or isinstance(value, GraphScope):
        return value
    if isinstance(value, ProfileScope):
        return GraphScope.of(value)
    try:
        scopes = tuple(value)
    except TypeError as exc:
        raise RuntimeGraphQueryError(
            "process-chain graph scope must contain ProfileScope values"
        ) from exc
    if not scopes or any(not isinstance(scope, ProfileScope) for scope in scopes):
        raise RuntimeGraphQueryError(
            "process-chain graph scope must contain ProfileScope values"
        )
    return GraphScope.of(*scopes)


class RuntimeGraphChainQuery:
    """Compose exact domain lookup into a deterministic bounded route DAG."""

    def __init__(self, reader: RuntimeGraphReader):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "process-chain query requires a RuntimeGraphReader"
            )
        self.reader = reader
        self.domain = RuntimeGraphDomainQuery(reader)

    def chain(
        self,
        selector: NodeSelector,
        options: ChainOptions = ChainOptions(),
        graph_scope: (
            GraphScope | ProfileScope | Iterable[ProfileScope] | None
        ) = None,
    ) -> dict[str, Any]:
        """Build a bounded process-chain route DAG for an exact target."""

        if not isinstance(selector, NodeSelector):
            raise RuntimeGraphQueryError(
                "process-chain query requires a NodeSelector"
            )
        if not isinstance(options, ChainOptions):
            raise RuntimeGraphQueryError(
                "process-chain query requires ChainOptions"
            )
        resolution = self.domain.resolve_targets(
            selector,
            _normalize_graph_scope(graph_scope),
        )
        state = _TraversalState(self.reader, self.domain, options)
        return _json_tree(
            state.run(
                resolution.targets,
                target_result={
                    "selector": _selector_dict(selector),
                    "resolved": [
                        {
                            "scope": _scope_dict(target.scope),
                            "node": target.node,
                        }
                        for target in resolution.targets
                    ],
                    "gaps": [
                        _scope_dict(gap) for gap in resolution.gaps
                    ],
                },
                profile_gaps=resolution.gaps,
                unresolved_identity=not resolution.targets,
            )
        )

    def chain_resolved_targets(
        self,
        targets: Iterable[ScopedTarget],
        options: ChainOptions = ChainOptions(),
        additional_root_provider: (
            Callable[[dict[str, Any]], Iterable[ScopedTarget]] | None
        ) = None,
    ) -> dict[str, Any]:
        """Build one shared route DAG from exact, already-resolved roots.

        Every root is validated against this query's open graph, sorted by
        exact scoped identity, and inserted before traversal begins. Duplicate
        roots, descendants, route limits, and visited-node limits therefore
        share one deterministic traversal state.
        """

        if not isinstance(options, ChainOptions):
            raise RuntimeGraphQueryError(
                "resolved process-chain query requires ChainOptions"
            )
        if (
            additional_root_provider is not None
            and not callable(additional_root_provider)
        ):
            raise RuntimeGraphQueryError(
                "resolved process-chain additional root provider must be "
                "callable"
            )
        resolved_targets = _normalize_resolved_targets(self.reader, targets)
        if options.max_visited_nodes < len(resolved_targets):
            raise RuntimeGraphQueryError(
                "resolved process-chain max_visited_nodes must cover every "
                f"explicit root: {options.max_visited_nodes} < "
                f"{len(resolved_targets)}"
            )
        state = _TraversalState(
            self.reader,
            self.domain,
            options,
            additional_root_provider=additional_root_provider,
        )
        return _json_tree(
            state.run(
                resolved_targets,
                target_result={
                    "selectors": [
                        _selector_dict(_resolved_target_selector(target))
                        for target in resolved_targets
                    ],
                    "resolved": [
                        {
                            "scope": _scope_dict(target.scope),
                            "node": target.node,
                        }
                        for target in resolved_targets
                    ],
                    "gaps": [],
                },
            )
        )


class _TraversalState:
    def __init__(
        self,
        reader: RuntimeGraphReader,
        domain: RuntimeGraphDomainQuery,
        options: ChainOptions,
        additional_root_provider: (
            Callable[[dict[str, Any]], Iterable[ScopedTarget]] | None
        ) = None,
    ):
        self.reader = reader
        self.domain = domain
        self.options = options
        self.additional_root_provider = additional_root_provider
        self.root_queue: deque[tuple[str, int]] = deque()
        self.queue: deque[tuple[str, int]] = deque()
        self.root_ids: list[str] = []
        self.expanded_ids: set[str] = set()
        self.targets: dict[str, ScopedTarget] = {}
        self.subproblems: dict[str, dict[str, Any]] = {}
        self.routes: list[dict[str, Any]] = []
        self.dependencies: list[dict[str, str]] = []
        self.unresolved: list[dict[str, Any]] = []
        self.unresolved_keys: set[tuple[str, ...]] = set()
        self.truncation_reasons: set[str] = set()
        self.visit_count = 0
        self.active_expansion: dict[str, Any] | None = None

    def _claim_visit(self) -> bool:
        if self.visit_count >= self.options.max_visited_nodes:
            self.truncation_reasons.add("max-visited-nodes")
            return False
        self.visit_count += 1
        return True

    @staticmethod
    def _target_key(target: ScopedTarget) -> str:
        return "\x1f".join(_scoped_target_key(target))

    @classmethod
    def _subproblem_id(cls, target: ScopedTarget) -> str:
        key = cls._target_key(target)
        return "subproblem:" + hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def _route_id(occurrence: dict[str, Any]) -> str:
        identity = "\x1f".join(
            (
                _record_id(occurrence["owner"]),
                _record_id(occurrence["owner_relationship"]),
                _record_id(occurrence["slot"]),
                *(
                    _record_id(row["relationship"])
                    for row in occurrence.get("matched_alternatives", ())
                ),
            )
        )
        return "route:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _new_subproblem(
        self,
        target: ScopedTarget,
        depth: int,
        *,
        root: bool = False,
    ) -> str | None:
        identifier = self._subproblem_id(target)
        if identifier in self.subproblems:
            existing = self.subproblems[identifier]
            if depth < int(existing["depth"]):
                existing["depth"] = depth
            if root and identifier not in self.root_ids:
                self.root_ids.append(identifier)
                if identifier not in self.expanded_ids:
                    self.root_queue.append((identifier, 0))
            return identifier
        if not self._claim_visit():
            self._add_unresolved(
                "max-visited-nodes",
                target=target,
                root=root,
            )
            return None
        self.targets[identifier] = target
        self.subproblems[identifier] = {
            "id": identifier,
            "scope": _scope_dict(target.scope),
            "target": target.node,
            "depth": depth,
            "route_ids": [],
            "status": "pending",
        }
        if root:
            self.root_ids.append(identifier)
            self.root_queue.append((identifier, depth))
        else:
            self.queue.append((identifier, depth))
        return identifier

    def _add_unresolved(
        self,
        reason: str,
        *,
        subproblem_id: str | None = None,
        target: ScopedTarget | None = None,
        occurrence: dict[str, Any] | None = None,
        slot: dict[str, Any] | None = None,
        alternative: dict[str, Any] | None = None,
        root: bool = False,
        cycle_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        owner_id = (
            _record_id(occurrence["owner"]) if occurrence is not None else ""
        )
        relationship_id = (
            _record_id(occurrence["owner_relationship"])
            if occurrence is not None
            else ""
        )
        slot_id = _record_id(slot["slot"]) if slot is not None else ""
        alternative_id = (
            _record_id(alternative["relationship"])
            if alternative is not None
            else ""
        )
        target_id = _record_id(target.node) if target is not None else ""
        key = (
            reason,
            subproblem_id or "",
            target_id,
            owner_id,
            relationship_id,
            slot_id,
            alternative_id,
            cycle_id or "",
        )
        if key in self.unresolved_keys:
            return
        self.unresolved_keys.add(key)
        row: dict[str, Any] = {"reason": reason}
        if subproblem_id is not None:
            row["subproblem_id"] = subproblem_id
        if target is not None:
            row["scope"] = _scope_dict(target.scope)
            row["target"] = target.node
        if occurrence is not None:
            row["candidate_producer"] = occurrence["owner"]
            row["production_relationship"] = occurrence["owner_relationship"]
            mechanics = occurrence.get("mechanics")
            if isinstance(mechanics, dict):
                row["mechanics"] = mechanics
        if slot is not None:
            row["slot"] = slot["slot"]
        if alternative is not None:
            row["alternative"] = alternative
        if root:
            row["root"] = True
        if cycle_id is not None:
            row["cycle_id"] = cycle_id
        if details:
            row["details"] = details
        self.unresolved.append(row)

    def _ineligible_reason(self, occurrence: dict[str, Any]) -> str:
        owner = occurrence["owner"]
        if owner.get("kind") in PRESENTATION_OWNER_KINDS:
            return "presentation-only-evidence"
        mechanics = occurrence.get("mechanics")
        if isinstance(mechanics, dict):
            classification = str(mechanics.get("classification", ""))
            if "presentation" in classification:
                return "presentation-only-evidence"
            if mechanics.get("consultation") and not mechanics.get("execution"):
                return "missing-execution-evidence"
        return "missing-execution-evidence"

    @staticmethod
    def _mechanically_eligible(occurrence: dict[str, Any]) -> bool:
        mechanics = occurrence.get("mechanics")
        if not isinstance(mechanics, dict) or not mechanics.get("eligible"):
            return False
        if occurrence["owner"].get("kind") == "recipe":
            # Lookup membership and map consultation are useful evidence, but
            # neither proves that any machine executes the row.
            return bool(mechanics.get("execution"))
        return True

    def _queue_alternative(
        self,
        *,
        source_subproblem_id: str,
        route_id: str,
        slot: dict[str, Any],
        alternative: dict[str, Any],
        scope: object,
        depth: int,
        expand: bool,
    ) -> dict[str, Any]:
        node = alternative["node"]
        classification = alternative_semantics(alternative["relationship"])
        result: dict[str, Any] = {
            "relationship": alternative["relationship"],
            "target": node,
            "alternative_semantics": classification,
        }
        chance = _chance_projection(alternative["relationship"])
        if chance:
            result["chance"] = chance
        target = ScopedTarget(scope=scope, node=node)
        if not self._claim_visit():
            result["expansion"] = {
                "status": "truncated",
                "reason": "max-visited-nodes",
            }
            self._add_unresolved(
                "max-visited-nodes",
                subproblem_id=source_subproblem_id,
                target=target,
                slot=slot,
                alternative=alternative,
            )
            return result
        if not expand or classification != "exact":
            reason = (
                "procedural-or-symbolic-boundary"
                if not expand or classification == "symbolic"
                else "non-finite-match-domain"
            )
            result["expansion"] = {
                "status": "boundary",
                "reason": reason,
            }
            self._add_unresolved(
                reason,
                subproblem_id=source_subproblem_id,
                target=target,
                slot=slot,
                alternative=alternative,
            )
            return result
        child_id = self._new_subproblem(target, depth + 1)
        if child_id is None:
            result["expansion"] = {
                "status": "truncated",
                "reason": "max-visited-nodes",
            }
            self._add_unresolved(
                "max-visited-nodes",
                subproblem_id=source_subproblem_id,
                target=target,
                slot=slot,
                alternative=alternative,
            )
            return result
        result["subproblem_id"] = child_id
        result["expansion"] = {"status": "linked"}
        self.dependencies.append(
            {
                "from": source_subproblem_id,
                "to": child_id,
                "route_id": route_id,
                "slot_id": _record_id(slot["slot"]),
                "alternative_relationship_id": _record_id(
                    alternative["relationship"]
                ),
            }
        )
        return result

    def _project_slot(
        self,
        *,
        source_subproblem_id: str,
        route_id: str,
        row: dict[str, Any],
        scope: object,
        depth: int,
        expand: bool,
    ) -> dict[str, Any]:
        alternatives = sorted(row["alternatives"], key=_alternative_order)
        classifications = {
            alternative_semantics(alternative["relationship"])
            for alternative in alternatives
        }
        if {
            "authoritative-match-domain",
            "non-exhaustive-representative",
        } & classifications:
            alternative_mode = "MATCH_DOMAIN"
            exhaustive = False
        elif "symbolic" in classifications:
            alternative_mode = "SYMBOLIC"
            exhaustive = False
        else:
            alternative_mode = "OR"
            exhaustive = True
        retained = alternatives[: self.options.max_alternatives_per_slot]
        omitted = alternatives[self.options.max_alternatives_per_slot :]
        if omitted:
            self.truncation_reasons.add("max-alternatives-per-slot")
            alternative = omitted[0]
            self._add_unresolved(
                "max-alternatives-per-slot",
                subproblem_id=source_subproblem_id,
                target=ScopedTarget(scope=scope, node=alternative["node"]),
                slot=row,
                alternative=alternative,
                details={"omitted_alternatives": len(omitted)},
            )
        predicate = str(row["relationship"].get("predicate", ""))
        semantics = {
            "jointly_required": True,
            "reusable": predicate == "requires",
            "consumed": predicate != "requires",
            "conditional": predicate in {"may_consume"},
            "guaranteed": predicate not in {"may_consume"},
        }
        return {
            "relationship": row["relationship"],
            "slot": row["slot"],
            "semantics": semantics,
            "alternatives": {
                "mode": alternative_mode,
                "exhaustive": exhaustive,
                "items": [
                    self._queue_alternative(
                        source_subproblem_id=source_subproblem_id,
                        route_id=route_id,
                        slot=row,
                        alternative=alternative,
                        scope=scope,
                        depth=depth,
                        expand=expand,
                    )
                    for alternative in retained
                ],
                "total": len(alternatives),
                "returned": len(retained),
                "truncated": bool(omitted),
            },
        }

    def _project_output_alternatives(
        self,
        *,
        subproblem_id: str,
        target_scope: object,
        slot: dict[str, Any],
        alternatives: Iterable[dict[str, Any]],
        required_relationship_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        ordered = sorted(alternatives, key=_alternative_order)
        prioritized = [
            alternative
            for alternative in ordered
            if _record_id(alternative["relationship"])
            in required_relationship_ids
        ] + [
            alternative
            for alternative in ordered
            if _record_id(alternative["relationship"])
            not in required_relationship_ids
        ]
        within_slot_bound = sorted(
            prioritized[: self.options.max_alternatives_per_slot],
            key=_alternative_order,
        )
        retained_ids = {
            _record_id(alternative["relationship"])
            for alternative in within_slot_bound
        }
        omitted_for_slot_bound = [
            alternative
            for alternative in ordered
            if _record_id(alternative["relationship"]) not in retained_ids
        ]
        if omitted_for_slot_bound:
            self.truncation_reasons.add("max-alternatives-per-slot")
            alternative = omitted_for_slot_bound[0]
            self._add_unresolved(
                "max-alternatives-per-slot",
                subproblem_id=subproblem_id,
                target=ScopedTarget(
                    scope=target_scope,
                    node=alternative["node"],
                ),
                slot=slot,
                alternative=alternative,
                details={
                    "omitted_alternatives": len(omitted_for_slot_bound)
                },
            )
        retained: list[dict[str, Any]] = []
        for alternative in within_slot_bound:
            relationship_id = _record_id(alternative["relationship"])
            if (
                relationship_id not in required_relationship_ids
                and not self._claim_visit()
            ):
                self._add_unresolved(
                    "max-visited-nodes",
                    subproblem_id=subproblem_id,
                    target=ScopedTarget(
                        scope=target_scope,
                        node=alternative["node"],
                    ),
                    slot=slot,
                    alternative=alternative,
                )
                continue
            retained.append(
                {
                    **alternative,
                    "alternative_semantics": alternative_semantics(
                        alternative["relationship"]
                    ),
                }
            )
        classifications = {
            alternative_semantics(alternative["relationship"])
            for alternative in ordered
        }
        if {
            "authoritative-match-domain",
            "non-exhaustive-representative",
        } & classifications:
            mode = "MATCH_DOMAIN"
            exhaustive = False
        elif "symbolic" in classifications:
            mode = "SYMBOLIC"
            exhaustive = False
        else:
            mode = "OR"
            exhaustive = True
        return {
            "mode": mode,
            "exhaustive": exhaustive,
            "items": retained,
            "total": len(ordered),
            "returned": len(retained),
            "truncated": len(retained) != len(ordered),
        }

    def _procedural_boundaries(
        self,
        owner: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        rows = self.reader.connection.execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.object "
            "WHERE edge.subject = ? "
            "AND edge.predicate IN ('delegates_to', 'governed_by_rule') "
            "AND node.kind IN ('recipe_rule', 'process_rule') "
            "ORDER BY edge.predicate, node.kind, node.id, edge.id",
            (_record_id(owner),),
        ).fetchall()
        from workbench_atlas.runtime_graph_query import decode_record

        return tuple(
            {
                "relationship": decode_record(encoded_edge, "edge"),
                "rule": decode_record(encoded_node, "node"),
            }
            for encoded_edge, encoded_node in rows
        )

    def _route(
        self,
        subproblem_id: str,
        target: ScopedTarget,
        occurrence: dict[str, Any],
        depth: int,
    ) -> dict[str, Any]:
        owner = occurrence["owner"]
        owner_kind = str(owner.get("kind", ""))
        route_id = self._route_id(occurrence)
        is_procedural = owner_kind in PROCEDURAL_OWNER_KINDS
        boundaries = list(self._procedural_boundaries(owner))
        if is_procedural:
            boundaries.insert(
                0,
                {
                    "rule": owner,
                    "status": "procedural-or-symbolic-boundary",
                },
            )
            if not occurrence["mechanics"].get("execution"):
                boundaries.append(
                    {
                        "rule": owner,
                        "status": "procedural-no-machine-binding",
                    }
                )
        elif (
            owner_kind == "worldgen_deposit"
            and not occurrence["mechanics"].get("execution")
        ):
            boundaries.append(
                {
                    "producer": owner,
                    "status": "worldgen-acquisition-no-machine-required",
                }
            )

        raw_inputs = sorted(
            self.reader.recipe_slots(_record_id(owner), INPUT_PREDICATES),
            key=_slot_order,
        )
        consumed_rows = [
            row
            for row in raw_inputs
            if row["relationship"].get("predicate") != "requires"
        ]
        reusable_rows = [
            row
            for row in raw_inputs
            if row["relationship"].get("predicate") == "requires"
        ]
        expand = not is_procedural
        inputs = [
            self._project_slot(
                source_subproblem_id=subproblem_id,
                route_id=route_id,
                row=row,
                scope=target.scope,
                depth=depth,
                expand=expand,
            )
            for row in consumed_rows
        ]
        reusable = [
            self._project_slot(
                source_subproblem_id=subproblem_id,
                route_id=route_id,
                row=row,
                scope=target.scope,
                depth=depth,
                expand=expand,
            )
            for row in reusable_rows
        ]

        output_rows = sorted(
            self.reader.recipe_slots(_record_id(owner), OUTPUT_PREDICATES),
            key=_slot_order,
        )
        production_slot_id = _record_id(occurrence["slot"])
        byproducts: list[dict[str, Any]] = []
        for row in output_rows:
            if _record_id(row["slot"]) == production_slot_id:
                continue
            output_records = (
                row["relationship"],
                row["slot"],
                *(
                    alternative["relationship"]
                    for alternative in row["alternatives"]
                ),
            )
            output_chance = _chance_projection(*output_records)
            if (
                not self.options.include_chanced_outputs
                and _has_chance(*output_records)
            ):
                continue
            byproduct: dict[str, Any] = {
                "relationship": row["relationship"],
                "slot": row["slot"],
                "semantics": {
                    "conditional": row["relationship"].get("predicate")
                    == "may_produce",
                    "guaranteed": row["relationship"].get("predicate")
                    == "produces",
                },
                "alternatives": self._project_output_alternatives(
                    subproblem_id=subproblem_id,
                    target_scope=target.scope,
                    slot=row,
                    alternatives=row["alternatives"],
                ),
            }
            if output_chance:
                byproduct["chance"] = output_chance
            byproducts.append(byproduct)
        matched = tuple(
            sorted(
                occurrence.get("matched_alternatives", ()),
                key=_alternative_order,
            )
        )
        chance = _chance_projection(
            occurrence["owner_relationship"],
            occurrence["slot"],
            matched[0]["relationship"] if matched else {},
        )
        production: dict[str, Any] = {
            "relationship": occurrence["owner_relationship"],
            "slot": occurrence["slot"],
            "semantics": occurrence.get(
                "semantics",
                {
                    "direction": "output",
                    "conditional": occurrence["owner_relationship"].get(
                        "predicate"
                    )
                    == "may_produce",
                    "guaranteed": occurrence["owner_relationship"].get(
                        "predicate"
                    )
                    == "produces",
                },
            ),
            "alternatives": self._project_output_alternatives(
                subproblem_id=subproblem_id,
                target_scope=target.scope,
                slot={
                    "relationship": occurrence["owner_relationship"],
                    "slot": occurrence["slot"],
                },
                alternatives=occurrence.get("alternatives", ()),
                required_relationship_ids=frozenset(
                    _record_id(row["relationship"]) for row in matched
                ),
            ),
        }
        retained_production_ids = {
            _record_id(row["relationship"])
            for row in production["alternatives"]["items"]
        }
        production["matched_alternatives"] = [
            row
            for row in matched
            if _record_id(row["relationship"]) in retained_production_ids
        ]
        if chance:
            production["chance"] = chance
        return {
            "id": route_id,
            "subproblem_id": subproblem_id,
            "scope": _scope_dict(target.scope),
            "producer": owner,
            "classification": occurrence["mechanics"].get(
                "classification",
                "runtime-mechanics",
            ),
            "mechanics": occurrence["mechanics"],
            "production": production,
            "ingredient_slots": {"mode": "AND", "slots": inputs},
            "reusable_requirements": {"mode": "AND", "slots": reusable},
            "byproducts": byproducts,
            "boundaries": boundaries,
        }

    def _expand(self, subproblem_id: str, depth: int) -> None:
        subproblem = self.subproblems[subproblem_id]
        target = self.targets[subproblem_id]
        occurrences = tuple(
            sorted(
                self.domain.producer_occurrences(target),
                key=_occurrence_order,
            )
        )
        if depth >= self.options.max_depth:
            expandable = False
            for occurrence in occurrences:
                if not self._mechanically_eligible(occurrence):
                    self._add_unresolved(
                        self._ineligible_reason(occurrence),
                        subproblem_id=subproblem_id,
                        target=target,
                        occurrence=occurrence,
                    )
                    continue
                if (
                    occurrence["owner"].get("kind")
                    in PROCEDURAL_OWNER_KINDS
                    and not self.options.include_procedural_rules
                ):
                    self._add_unresolved(
                        "procedural-rules-disabled",
                        subproblem_id=subproblem_id,
                        target=target,
                        occurrence=occurrence,
                    )
                    continue
                occurrence_records = (
                    occurrence["owner_relationship"],
                    occurrence["slot"],
                    *(
                        alternative["relationship"]
                        for alternative in occurrence.get("alternatives", ())
                    ),
                )
                if (
                    not self.options.include_chanced_outputs
                    and _has_chance(*occurrence_records)
                ):
                    self._add_unresolved(
                        "chanced-outputs-disabled",
                        subproblem_id=subproblem_id,
                        target=target,
                        occurrence=occurrence,
                    )
                    continue
                expandable = True
            if expandable:
                subproblem["status"] = "truncated"
                self.truncation_reasons.add("max-depth")
                self._add_unresolved(
                    "max-depth",
                    subproblem_id=subproblem_id,
                    target=target,
                )
            else:
                subproblem["status"] = "unresolved"
                if not occurrences:
                    self._add_unresolved(
                        "no-mechanical-producer",
                        subproblem_id=subproblem_id,
                        target=target,
                    )
            return

        eligible_count = 0
        for occurrence_index, occurrence in enumerate(occurrences):
            if not self._claim_visit():
                self._add_unresolved(
                    "max-visited-nodes",
                    subproblem_id=subproblem_id,
                    target=target,
                    details={
                        "remaining_candidate_occurrences": (
                            len(occurrences) - occurrence_index
                        )
                    },
                )
                break
            if not self._mechanically_eligible(occurrence):
                self._add_unresolved(
                    self._ineligible_reason(occurrence),
                    subproblem_id=subproblem_id,
                    target=target,
                    occurrence=occurrence,
                )
                continue
            occurrence_records = (
                occurrence["owner_relationship"],
                occurrence["slot"],
                *(
                    alternative["relationship"]
                    for alternative in occurrence.get("alternatives", ())
                ),
            )
            if (
                not self.options.include_chanced_outputs
                and _has_chance(*occurrence_records)
            ):
                self._add_unresolved(
                    "chanced-outputs-disabled",
                    subproblem_id=subproblem_id,
                    target=target,
                    occurrence=occurrence,
                )
                continue
            if (
                occurrence["owner"].get("kind") in PROCEDURAL_OWNER_KINDS
                and not self.options.include_procedural_rules
            ):
                self._add_unresolved(
                    "procedural-rules-disabled",
                    subproblem_id=subproblem_id,
                    target=target,
                    occurrence=occurrence,
                )
                continue
            eligible_count += 1
            if len(self.routes) >= self.options.max_routes:
                self.truncation_reasons.add("max-routes")
                self._add_unresolved(
                    "max-routes",
                    subproblem_id=subproblem_id,
                    target=target,
                    details={
                        "remaining_candidate_occurrences": (
                            len(occurrences) - occurrence_index
                        )
                    },
                )
                break
            route_id = self._route_id(occurrence)
            if self.additional_root_provider is not None:
                additional_targets = _normalize_resolved_targets(
                    self.reader,
                    self.additional_root_provider(
                        {
                            "route_id": route_id,
                            "subproblem_id": subproblem_id,
                            "scope": _scope_dict(target.scope),
                            "target": target.node,
                            "producer": occurrence["owner"],
                            "mechanics": occurrence["mechanics"],
                        }
                    ),
                    require_nonempty=False,
                )
                for additional_target in additional_targets:
                    self._new_subproblem(
                        additional_target,
                        0,
                        root=True,
                    )
            route = self._route(subproblem_id, target, occurrence, depth)
            self.routes.append(route)
            subproblem["route_ids"].append(route["id"])

        if subproblem["route_ids"]:
            subproblem["status"] = "expanded"
        elif eligible_count:
            subproblem["status"] = "truncated"
        else:
            subproblem["status"] = "unresolved"
            if not occurrences:
                self._add_unresolved(
                    "no-mechanical-producer",
                    subproblem_id=subproblem_id,
                    target=target,
                )

    def _begin_incremental_expansion(
        self,
        subproblem_id: str,
        depth: int,
    ) -> None:
        if self.active_expansion is not None:
            raise RuntimeGraphQueryError(
                "process-chain incremental expansion is already active"
            )
        target = self.targets[subproblem_id]
        self.active_expansion = {
            "subproblem_id": subproblem_id,
            "depth": depth,
            "occurrences": list(
                sorted(
                    self.domain.producer_occurrences(target),
                    key=_occurrence_order,
                )
            ),
            "next_occurrence": 0,
            "eligible_count": 0,
            "expandable_at_depth_limit": False,
        }

    def _finish_incremental_expansion(self) -> None:
        active = self.active_expansion
        if active is None:
            return
        subproblem_id = str(active["subproblem_id"])
        subproblem = self.subproblems[subproblem_id]
        target = self.targets[subproblem_id]
        occurrences = active["occurrences"]
        depth = int(active["depth"])
        eligible_count = int(active["eligible_count"])
        if depth >= self.options.max_depth:
            if active["expandable_at_depth_limit"]:
                subproblem["status"] = "truncated"
                self.truncation_reasons.add("max-depth")
                self._add_unresolved(
                    "max-depth",
                    subproblem_id=subproblem_id,
                    target=target,
                )
            else:
                subproblem["status"] = "unresolved"
                if not occurrences:
                    self._add_unresolved(
                        "no-mechanical-producer",
                        subproblem_id=subproblem_id,
                        target=target,
                    )
        elif subproblem["route_ids"]:
            subproblem["status"] = "expanded"
        elif eligible_count:
            subproblem["status"] = "truncated"
        else:
            subproblem["status"] = "unresolved"
            if not occurrences:
                self._add_unresolved(
                    "no-mechanical-producer",
                    subproblem_id=subproblem_id,
                    target=target,
                )
        self.active_expansion = None

    def _advance_incremental_expansion(self) -> bool:
        """Process one producer occurrence and return whether work was spent."""

        active = self.active_expansion
        if active is None:
            return False
        index = int(active["next_occurrence"])
        occurrences = active["occurrences"]
        if index >= len(occurrences):
            self._finish_incremental_expansion()
            return False
        subproblem_id = str(active["subproblem_id"])
        depth = int(active["depth"])
        subproblem = self.subproblems[subproblem_id]
        target = self.targets[subproblem_id]
        occurrence = occurrences[index]
        active["next_occurrence"] = index + 1

        if depth >= self.options.max_depth:
            if not self._mechanically_eligible(occurrence):
                self._add_unresolved(
                    self._ineligible_reason(occurrence),
                    subproblem_id=subproblem_id,
                    target=target,
                    occurrence=occurrence,
                )
                return True
            if (
                occurrence["owner"].get("kind") in PROCEDURAL_OWNER_KINDS
                and not self.options.include_procedural_rules
            ):
                self._add_unresolved(
                    "procedural-rules-disabled",
                    subproblem_id=subproblem_id,
                    target=target,
                    occurrence=occurrence,
                )
                return True
            occurrence_records = (
                occurrence["owner_relationship"],
                occurrence["slot"],
                *(
                    alternative["relationship"]
                    for alternative in occurrence.get("alternatives", ())
                ),
            )
            if (
                not self.options.include_chanced_outputs
                and _has_chance(*occurrence_records)
            ):
                self._add_unresolved(
                    "chanced-outputs-disabled",
                    subproblem_id=subproblem_id,
                    target=target,
                    occurrence=occurrence,
                )
                return True
            active["expandable_at_depth_limit"] = True
            return True

        if not self._claim_visit():
            self._add_unresolved(
                "max-visited-nodes",
                subproblem_id=subproblem_id,
                target=target,
                details={
                    "remaining_candidate_occurrences": (
                        len(occurrences) - index
                    )
                },
            )
            active["next_occurrence"] = len(occurrences)
            return True
        if not self._mechanically_eligible(occurrence):
            self._add_unresolved(
                self._ineligible_reason(occurrence),
                subproblem_id=subproblem_id,
                target=target,
                occurrence=occurrence,
            )
            return True
        occurrence_records = (
            occurrence["owner_relationship"],
            occurrence["slot"],
            *(
                alternative["relationship"]
                for alternative in occurrence.get("alternatives", ())
            ),
        )
        if (
            not self.options.include_chanced_outputs
            and _has_chance(*occurrence_records)
        ):
            self._add_unresolved(
                "chanced-outputs-disabled",
                subproblem_id=subproblem_id,
                target=target,
                occurrence=occurrence,
            )
            return True
        if (
            occurrence["owner"].get("kind") in PROCEDURAL_OWNER_KINDS
            and not self.options.include_procedural_rules
        ):
            self._add_unresolved(
                "procedural-rules-disabled",
                subproblem_id=subproblem_id,
                target=target,
                occurrence=occurrence,
            )
            return True
        active["eligible_count"] = int(active["eligible_count"]) + 1
        if len(self.routes) >= self.options.max_routes:
            self.truncation_reasons.add("max-routes")
            self._add_unresolved(
                "max-routes",
                subproblem_id=subproblem_id,
                target=target,
                details={
                    "remaining_candidate_occurrences": (
                        len(occurrences) - index
                    )
                },
            )
            active["next_occurrence"] = len(occurrences)
            return True
        route_id = self._route_id(occurrence)
        if self.additional_root_provider is not None:
            additional_targets = _normalize_resolved_targets(
                self.reader,
                self.additional_root_provider(
                    {
                        "route_id": route_id,
                        "subproblem_id": subproblem_id,
                        "scope": _scope_dict(target.scope),
                        "target": target.node,
                        "producer": occurrence["owner"],
                        "mechanics": occurrence["mechanics"],
                    }
                ),
                require_nonempty=False,
            )
            for additional_target in additional_targets:
                self._new_subproblem(additional_target, 0, root=True)
        route = self._route(
            subproblem_id,
            target,
            occurrence,
            depth,
        )
        self.routes.append(route)
        subproblem["route_ids"].append(route["id"])
        return True

    def _cycles(
        self,
        *,
        add_unresolved: bool = True,
    ) -> list[dict[str, Any]]:
        adjacency: dict[str, list[str]] = {
            identifier: [] for identifier in self.subproblems
        }
        for edge in self.dependencies:
            if edge["from"] in adjacency and edge["to"] in adjacency:
                adjacency[edge["from"]].append(edge["to"])
        for identifier in adjacency:
            adjacency[identifier] = sorted(set(adjacency[identifier]))

        result: list[dict[str, Any]] = []
        for members in sorted(_cyclic_components(adjacency)):
            member_set = set(members)
            cycle_edges = sorted(
                (
                    edge
                    for edge in self.dependencies
                    if edge["from"] in member_set and edge["to"] in member_set
                ),
                key=lambda edge: (
                    edge["from"],
                    edge["to"],
                    edge["route_id"],
                    edge["slot_id"],
                    edge["alternative_relationship_id"],
                ),
            )
            identity = "\x1f".join(members)
            cycle_id = (
                "cycle:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            )
            row = {
                "id": cycle_id,
                "subproblem_ids": list(members),
                "dependencies": cycle_edges,
            }
            result.append(row)
            if add_unresolved:
                for member in members:
                    self._add_unresolved(
                        "cycle",
                        subproblem_id=member,
                        target=self.targets[member],
                        cycle_id=cycle_id,
                    )
        return result

    def run(
        self,
        targets: Iterable[ScopedTarget],
        *,
        target_result: dict[str, Any],
        profile_gaps: Iterable[ProfileScope] = (),
        unresolved_identity: bool = False,
    ) -> dict[str, Any]:
        """Traverse one prevalidated target set with one shared budget."""

        resolved_targets = sorted(
            targets,
            key=lambda target: self._target_key(target),
        )
        for target in resolved_targets:
            self._new_subproblem(target, 0, root=True)

        while self.root_queue or self.queue:
            source = self.root_queue if self.root_queue else self.queue
            identifier, depth = source.popleft()
            if identifier in self.expanded_ids:
                continue
            self.expanded_ids.add(identifier)
            self._expand(identifier, depth)

        cycles = self._cycles()
        ordered_reasons = [
            reason
            for reason in TRUNCATION_REASONS
            if reason in self.truncation_reasons
        ]
        profile_scopes = {
            (target.scope.profile, target.scope.physical_side)
            for target in resolved_targets
        }
        gaps = tuple(profile_gaps)
        for gap in gaps:
            if not isinstance(gap, ProfileScope):
                raise RuntimeGraphQueryError(
                    "process-chain profile gap must be a ProfileScope"
                )
            profile_scopes.add((gap.profile, gap.physical_side))
        if unresolved_identity:
            self._add_unresolved("unresolved-identity")
        return {
            "target": target_result,
            "profile_scope": [
                {"profile": profile, "physical_side": side}
                for profile, side in sorted(profile_scopes)
            ],
            "roots": self.root_ids,
            "subproblems": sorted(
                self.subproblems.values(),
                key=lambda row: (
                    int(row["depth"]),
                    str(row["scope"]["profile"]),
                    str(row["scope"]["physical_side"]),
                    _record_id(row["target"]),
                    str(row["id"]),
                ),
            ),
            "routes": self.routes,
            "cycles": cycles,
            "unresolved_leaves": sorted(
                self.unresolved,
                key=lambda row: (
                    str(row.get("reason", "")),
                    str(row.get("subproblem_id", "")),
                    _record_id(row["target"])
                    if isinstance(row.get("target"), dict)
                    else "",
                    _record_id(row["candidate_producer"])
                    if isinstance(row.get("candidate_producer"), dict)
                    else "",
                    str(row.get("cycle_id", "")),
                ),
            ),
            "truncation": {
                "truncated": bool(ordered_reasons),
                "reasons": ordered_reasons,
                "limits": self.options.limits(),
            },
        }


class RuntimeGraphChainContinuation:
    """Serializable producer-occurrence continuation over route traversal.

    The V1 structural limits remain immutable. ``step`` controls only how many
    producer occurrences this segment may admit or dispose, allowing the exact
    active occurrence offset and queues to be resumed without re-expanding
    completed work.
    """

    STATE_FORMAT = "susy-route-traversal-state-v1"
    ALGORITHM_ID = "ATLAS-ROUTE-CONTINUATION"
    ALGORITHM_VERSION = 1
    FAIRNESS_POLICY_ID = "ATLAS-ROUTE-BREADTH-FIRST-V1"

    def __init__(
        self,
        reader: RuntimeGraphReader,
        targets: Iterable[ScopedTarget],
        options: ChainOptions = ChainOptions(),
        *,
        target_result: dict[str, Any] | None = None,
        profile_gaps: Iterable[ProfileScope] = (),
    ):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "route continuation requires a RuntimeGraphReader"
            )
        if not isinstance(options, ChainOptions):
            raise RuntimeGraphQueryError(
                "route continuation requires ChainOptions"
            )
        resolved = _normalize_resolved_targets(reader, targets)
        if options.max_visited_nodes < len(resolved):
            raise RuntimeGraphQueryError(
                "route continuation max_visited_nodes must cover every root"
            )
        gaps = tuple(profile_gaps)
        if any(not isinstance(gap, ProfileScope) for gap in gaps):
            raise RuntimeGraphQueryError(
                "route continuation gaps must be ProfileScope values"
            )
        self.reader = reader
        self.options = options
        self.root_targets = resolved
        self.profile_gaps = gaps
        self.target_result = (
            {
                "selectors": [
                    _selector_dict(_resolved_target_selector(target))
                    for target in resolved
                ],
                "resolved": [
                    {
                        "scope": _scope_dict(target.scope),
                        "node": target.node,
                    }
                    for target in resolved
                ],
                "gaps": [_scope_dict(gap) for gap in gaps],
            }
            if target_result is None
            else _json_tree(target_result)
        )
        self.state = _TraversalState(
            reader,
            RuntimeGraphDomainQuery(reader),
            options,
        )
        for target in resolved:
            self.state._new_subproblem(target, 0, root=True)
        self.cumulative_work_items = 0
        self.last_page_ranges: list[dict[str, Any]] = []

    @staticmethod
    def _options_document(options: ChainOptions) -> dict[str, Any]:
        return {
            "include_chanced_outputs": options.include_chanced_outputs,
            "include_procedural_rules": options.include_procedural_rules,
            **options.limits(),
        }

    @property
    def complete(self) -> bool:
        return (
            self.state.active_expansion is None
            and not self.state.root_queue
            and not self.state.queue
        )

    def _start_next_expansion(self) -> bool:
        while self.state.root_queue or self.state.queue:
            source = (
                self.state.root_queue
                if self.state.root_queue
                else self.state.queue
            )
            identifier, depth = source.popleft()
            if identifier in self.state.expanded_ids:
                continue
            self.state.expanded_ids.add(identifier)
            self.state._begin_incremental_expansion(identifier, depth)
            return True
        return False

    def step(self, max_work_items: int) -> dict[str, Any]:
        if type(max_work_items) is not int or max_work_items < 1:
            raise RuntimeGraphQueryError(
                "route continuation work allocation must be a positive integer"
            )
        consumed = 0
        ranges: list[dict[str, Any]] = []
        while consumed < max_work_items:
            if self.state.active_expansion is None:
                if not self._start_next_expansion():
                    break
            active = self.state.active_expansion
            if active is None:
                continue
            before = int(active["next_occurrence"])
            subject_id = str(active["subproblem_id"])
            spent = self.state._advance_incremental_expansion()
            if not spent:
                continue
            consumed += 1
            ranges.append(
                {
                    "stream": "producer-occurrences",
                    "subject_id": subject_id,
                    "start": before,
                    "end": before + 1,
                }
            )
        self.cumulative_work_items += consumed
        self.last_page_ranges = ranges
        return {
            "allocated_work_items": max_work_items,
            "consumed_work_items": consumed,
            "cumulative_work_items": self.cumulative_work_items,
            "complete": self.complete,
            "page_ranges": copy.deepcopy(ranges),
            "frontier": self.frontier(),
            "state": self.export_state(),
            "result": self.result(),
        }

    def frontier(self) -> dict[str, Any]:
        active = self.state.active_expansion
        active_row: dict[str, Any] | None = None
        if active is not None:
            active_row = {
                "subproblem_id": active["subproblem_id"],
                "depth": active["depth"],
                "next_occurrence": active["next_occurrence"],
                "occurrence_count": len(active["occurrences"]),
            }
        return {
            "phase": "complete" if self.complete else "producer-occurrences",
            "active": active_row,
            "root_queue": [
                {"subproblem_id": identifier, "depth": depth}
                for identifier, depth in self.state.root_queue
            ],
            "descendant_queue": [
                {"subproblem_id": identifier, "depth": depth}
                for identifier, depth in self.state.queue
            ],
            "visit_count": self.state.visit_count,
            "route_count": len(self.state.routes),
        }

    def _cycle_unresolved(
        self,
        cycles: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for cycle in cycles:
            for identifier in cycle["subproblem_ids"]:
                rows.append(
                    {
                        "reason": "cycle",
                        "subproblem_id": identifier,
                        "scope": _scope_dict(
                            self.state.targets[identifier].scope
                        ),
                        "target": self.state.targets[identifier].node,
                        "cycle_id": cycle["id"],
                    }
                )
        return rows

    def result(self) -> dict[str, Any]:
        cycles = self.state._cycles(add_unresolved=False)
        unresolved_by_key: dict[bytes, dict[str, Any]] = {}
        for row in [
            *self.state.unresolved,
            *self._cycle_unresolved(cycles),
        ]:
            unresolved_by_key[canonical_json_bytes(row)] = row
        ordered_reasons = [
            reason
            for reason in TRUNCATION_REASONS
            if reason in self.state.truncation_reasons
        ]
        profile_scopes = {
            (target.scope.profile, target.scope.physical_side)
            for target in self.root_targets
        }
        profile_scopes.update(
            (gap.profile, gap.physical_side) for gap in self.profile_gaps
        )
        result = _json_tree(
            {
                "target": self.target_result,
                "profile_scope": [
                    {"profile": profile, "physical_side": side}
                    for profile, side in sorted(profile_scopes)
                ],
                "roots": list(self.state.root_ids),
                "subproblems": sorted(
                    self.state.subproblems.values(),
                    key=lambda row: (
                        int(row["depth"]),
                        str(row["scope"]["profile"]),
                        str(row["scope"]["physical_side"]),
                        _record_id(row["target"]),
                        str(row["id"]),
                    ),
                ),
                "routes": self.state.routes,
                "cycles": cycles,
                "unresolved_leaves": sorted(
                    unresolved_by_key.values(),
                    key=lambda row: (
                        str(row.get("reason", "")),
                        str(row.get("subproblem_id", "")),
                        _record_id(row["target"])
                        if isinstance(row.get("target"), dict)
                        else "",
                        _record_id(row["candidate_producer"])
                        if isinstance(row.get("candidate_producer"), dict)
                        else "",
                        str(row.get("cycle_id", "")),
                    ),
                ),
                "truncation": {
                    "truncated": bool(ordered_reasons),
                    "reasons": ordered_reasons,
                    "limits": self.options.limits(),
                },
            }
        )
        if self.complete:
            validate_process_chain_result(result)
        return result

    def export_state(self) -> dict[str, Any]:
        targets = {
            identifier: {
                "scope": _scope_dict(target.scope),
                "node": target.node,
            }
            for identifier, target in sorted(self.state.targets.items())
        }
        return _json_tree(
            {
                "format": self.STATE_FORMAT,
                "algorithm_version": self.ALGORITHM_VERSION,
                "options": self._options_document(self.options),
                "target_result": self.target_result,
                "profile_gaps": [
                    _scope_dict(gap) for gap in self.profile_gaps
                ],
                "root_target_ids": [
                    self.state._subproblem_id(target)
                    for target in self.root_targets
                ],
                "root_queue": list(self.state.root_queue),
                "queue": list(self.state.queue),
                "root_ids": list(self.state.root_ids),
                "expanded_ids": sorted(self.state.expanded_ids),
                "targets": targets,
                "subproblems": self.state.subproblems,
                "routes": self.state.routes,
                "dependencies": self.state.dependencies,
                "unresolved": self.state.unresolved,
                "unresolved_keys": [
                    list(key) for key in sorted(self.state.unresolved_keys)
                ],
                "truncation_reasons": sorted(
                    self.state.truncation_reasons
                ),
                "visit_count": self.state.visit_count,
                "active_expansion": self.state.active_expansion,
                "cumulative_work_items": self.cumulative_work_items,
            }
        )

    @classmethod
    def from_state(
        cls,
        reader: RuntimeGraphReader,
        document: dict[str, Any],
    ) -> "RuntimeGraphChainContinuation":
        """Decode and validate portable state without replaying traversal."""

        if not isinstance(document, dict) or document.get("format") != cls.STATE_FORMAT:
            raise RuntimeGraphQueryError(
                "route continuation state format is unsupported"
            )
        if document.get("algorithm_version") != cls.ALGORITHM_VERSION:
            raise RuntimeGraphQueryError(
                "route continuation algorithm version differs"
            )
        options_row = document.get("options")
        if not isinstance(options_row, dict):
            raise RuntimeGraphQueryError(
                "route continuation options are missing"
            )
        options = ChainOptions(**options_row)
        targets_row = document.get("targets")
        if not isinstance(targets_row, dict) or not targets_row:
            raise RuntimeGraphQueryError(
                "route continuation targets are missing"
            )
        targets: dict[str, ScopedTarget] = {}
        for identifier, row in targets_row.items():
            if (
                not isinstance(identifier, str)
                or not isinstance(row, dict)
                or not isinstance(row.get("scope"), dict)
                or not isinstance(row.get("node"), dict)
            ):
                raise RuntimeGraphQueryError(
                    "route continuation target state is invalid"
                )
            scope = ProfileScope(
                str(row["scope"].get("profile", "")),
                str(row["scope"].get("physical_side", "")),
            )
            target = ScopedTarget(scope, row["node"])
            if _TraversalState._subproblem_id(target) != identifier:
                raise RuntimeGraphQueryError(
                    "route continuation target identity differs"
                )
            matches = reader.resolve_exact(_resolved_target_selector(target))
            if len(matches) != 1 or matches[0] != target.node:
                raise RuntimeGraphQueryError(
                    "route continuation target differs from the open graph"
                )
            targets[identifier] = target
        root_ids = document.get("root_target_ids")
        if (
            not isinstance(root_ids, list)
            or not root_ids
            or any(identifier not in targets for identifier in root_ids)
        ):
            raise RuntimeGraphQueryError(
                "route continuation root target identities are invalid"
            )
        instance = cls.__new__(cls)
        instance.reader = reader
        instance.options = options
        instance.root_targets = tuple(targets[identifier] for identifier in root_ids)
        instance.profile_gaps = tuple(
            ProfileScope(
                str(row.get("profile", "")),
                str(row.get("physical_side", "")),
            )
            for row in document.get("profile_gaps", [])
            if isinstance(row, dict)
        )
        instance.target_result = copy.deepcopy(document.get("target_result"))
        state = _TraversalState(
            reader,
            RuntimeGraphDomainQuery(reader),
            options,
        )
        state.root_queue = deque(
            (str(row[0]), int(row[1]))
            for row in document.get("root_queue", [])
        )
        state.queue = deque(
            (str(row[0]), int(row[1]))
            for row in document.get("queue", [])
        )
        state.root_ids = [str(value) for value in document.get("root_ids", [])]
        state.expanded_ids = {
            str(value) for value in document.get("expanded_ids", [])
        }
        state.targets = targets
        state.subproblems = copy.deepcopy(document.get("subproblems", {}))
        state.routes = copy.deepcopy(document.get("routes", []))
        state.dependencies = copy.deepcopy(document.get("dependencies", []))
        state.unresolved = copy.deepcopy(document.get("unresolved", []))
        state.unresolved_keys = {
            tuple(str(part) for part in row)
            for row in document.get("unresolved_keys", [])
        }
        state.truncation_reasons = {
            str(value)
            for value in document.get("truncation_reasons", [])
        }
        state.visit_count = int(document.get("visit_count", -1))
        state.active_expansion = copy.deepcopy(
            document.get("active_expansion")
        )
        if (
            state.visit_count < 0
            or not isinstance(state.subproblems, dict)
            or not isinstance(state.routes, list)
            or not isinstance(state.dependencies, list)
            or not isinstance(state.unresolved, list)
        ):
            raise RuntimeGraphQueryError(
                "route continuation traversal state is invalid"
            )
        known_ids = set(targets)
        referenced_ids = set(state.root_ids)
        referenced_ids.update(identifier for identifier, _ in state.root_queue)
        referenced_ids.update(identifier for identifier, _ in state.queue)
        referenced_ids.update(state.expanded_ids)
        if (
            not referenced_ids.issubset(known_ids)
            or set(state.subproblems) != known_ids
        ):
            raise RuntimeGraphQueryError(
                "route continuation frontier references unknown targets"
            )
        if state.active_expansion is not None:
            active_id = state.active_expansion.get("subproblem_id")
            next_occurrence = state.active_expansion.get("next_occurrence")
            occurrences = state.active_expansion.get("occurrences")
            if (
                active_id not in known_ids
                or type(next_occurrence) is not int
                or next_occurrence < 0
                or not isinstance(occurrences, list)
                or next_occurrence > len(occurrences)
            ):
                raise RuntimeGraphQueryError(
                    "route continuation active occurrence is invalid"
                )
        instance.state = state
        cumulative = document.get("cumulative_work_items")
        if type(cumulative) is not int or cumulative < 0:
            raise RuntimeGraphQueryError(
                "route continuation cumulative work is invalid"
            )
        instance.cumulative_work_items = cumulative
        instance.last_page_ranges = []
        return instance


def _route_cycles_from_continuation_state(
    subproblems: dict[str, Any],
    dependencies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    adjacency: dict[str, list[str]] = {
        identifier: [] for identifier in subproblems
    }
    for edge in dependencies:
        if not isinstance(edge, dict):
            raise RuntimeGraphQueryError(
                "route continuation dependency is not an object"
            )
        if edge.get("from") in adjacency and edge.get("to") in adjacency:
            adjacency[edge["from"]].append(edge["to"])
    for identifier in adjacency:
        adjacency[identifier] = sorted(set(adjacency[identifier]))

    cycles: list[dict[str, Any]] = []
    for members in sorted(_cyclic_components(adjacency)):
        member_set = set(members)
        cycle_edges = sorted(
            (
                edge
                for edge in dependencies
                if edge["from"] in member_set
                and edge["to"] in member_set
            ),
            key=lambda edge: (
                edge["from"],
                edge["to"],
                edge["route_id"],
                edge["slot_id"],
                edge["alternative_relationship_id"],
            ),
        )
        identity = "\x1f".join(members)
        cycles.append(
            {
                "id": (
                    "cycle:"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                ),
                "subproblem_ids": list(members),
                "dependencies": cycle_edges,
            }
        )
    return cycles


def validate_route_continuation_projection(
    state: object,
    result: object,
    frontier: object,
    *,
    cumulative_work_items: int,
    status: str,
) -> None:
    """Recompute a route result and frontier from portable state alone."""

    if not isinstance(state, dict) or state.get(
        "format"
    ) != RuntimeGraphChainContinuation.STATE_FORMAT:
        raise RuntimeGraphQueryError(
            "route continuation projection state is unsupported"
        )
    if (
        type(cumulative_work_items) is not int
        or cumulative_work_items < 0
        or state.get("cumulative_work_items") != cumulative_work_items
    ):
        raise RuntimeGraphQueryError(
            "route continuation state cumulative work differs"
        )
    try:
        options_row = state["options"]
        if not isinstance(options_row, dict):
            raise TypeError("options")
        options = ChainOptions(**options_row)
        targets = state["targets"]
        subproblems = state["subproblems"]
        dependencies = state["dependencies"]
        unresolved = state["unresolved"]
        routes = state["routes"]
        root_queue = state["root_queue"]
        descendant_queue = state["queue"]
        active = state["active_expansion"]
        if (
            not isinstance(targets, dict)
            or not isinstance(subproblems, dict)
            or not isinstance(dependencies, list)
            or not isinstance(unresolved, list)
            or not isinstance(routes, list)
            or not isinstance(root_queue, list)
            or not isinstance(descendant_queue, list)
        ):
            raise TypeError("route state collection")
        cycles = _route_cycles_from_continuation_state(
            subproblems,
            dependencies,
        )
        unresolved_by_key: dict[bytes, dict[str, Any]] = {}
        cycle_unresolved = [
            {
                "reason": "cycle",
                "subproblem_id": identifier,
                "scope": targets[identifier]["scope"],
                "target": targets[identifier]["node"],
                "cycle_id": cycle["id"],
            }
            for cycle in cycles
            for identifier in cycle["subproblem_ids"]
        ]
        for row in [*unresolved, *cycle_unresolved]:
            unresolved_by_key[canonical_json_bytes(row)] = row
        root_target_ids = state["root_target_ids"]
        if not isinstance(root_target_ids, list) or not root_target_ids:
            raise TypeError("root target ids")
        profile_scopes = {
            (
                targets[identifier]["scope"]["profile"],
                targets[identifier]["scope"]["physical_side"],
            )
            for identifier in root_target_ids
        }
        profile_gaps = state["profile_gaps"]
        if not isinstance(profile_gaps, list):
            raise TypeError("profile gaps")
        profile_scopes.update(
            (gap["profile"], gap["physical_side"])
            for gap in profile_gaps
        )
        ordered_reasons = [
            reason
            for reason in TRUNCATION_REASONS
            if reason in set(state["truncation_reasons"])
        ]
        expected_result = _json_tree(
            {
                "target": state["target_result"],
                "profile_scope": [
                    {"profile": profile, "physical_side": side}
                    for profile, side in sorted(profile_scopes)
                ],
                "roots": state["root_ids"],
                "subproblems": sorted(
                    subproblems.values(),
                    key=lambda row: (
                        int(row["depth"]),
                        str(row["scope"]["profile"]),
                        str(row["scope"]["physical_side"]),
                        _record_id(row["target"]),
                        str(row["id"]),
                    ),
                ),
                "routes": routes,
                "cycles": cycles,
                "unresolved_leaves": sorted(
                    unresolved_by_key.values(),
                    key=lambda row: (
                        str(row.get("reason", "")),
                        str(row.get("subproblem_id", "")),
                        _record_id(row["target"])
                        if isinstance(row.get("target"), dict)
                        else "",
                        _record_id(row["candidate_producer"])
                        if isinstance(
                            row.get("candidate_producer"),
                            dict,
                        )
                        else "",
                        str(row.get("cycle_id", "")),
                    ),
                ),
                "truncation": {
                    "truncated": bool(ordered_reasons),
                    "reasons": ordered_reasons,
                    "limits": options.limits(),
                },
            }
        )
        active_row = None
        if active is not None:
            if not isinstance(active, dict):
                raise TypeError("active expansion")
            active_row = {
                "subproblem_id": active["subproblem_id"],
                "depth": active["depth"],
                "next_occurrence": active["next_occurrence"],
                "occurrence_count": len(active["occurrences"]),
            }
        complete = (
            active is None
            and not root_queue
            and not descendant_queue
        )
        expected_frontier = {
            "phase": (
                "complete" if complete else "producer-occurrences"
            ),
            "active": active_row,
            "root_queue": [
                {"subproblem_id": identifier, "depth": depth}
                for identifier, depth in root_queue
            ],
            "descendant_queue": [
                {"subproblem_id": identifier, "depth": depth}
                for identifier, depth in descendant_queue
            ],
            "visit_count": state["visit_count"],
            "route_count": len(routes),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeGraphQueryError(
            "route continuation state cannot project its result/frontier"
        ) from exc
    if result != expected_result:
        raise RuntimeGraphQueryError(
            "route continuation result differs from its decoded state"
        )
    if frontier != expected_frontier:
        raise RuntimeGraphQueryError(
            "route continuation frontier differs from its decoded state"
        )
    if status == "complete" and not complete:
        raise RuntimeGraphQueryError(
            "route continuation complete status has resumable state"
        )
    if complete:
        validate_process_chain_result(expected_result)


def validate_process_chain_result(result: object) -> None:
    """Independently recompute exact identities and retained route structure."""

    if not isinstance(result, dict):
        raise RuntimeGraphQueryError(
            "process-chain result must be an object"
        )
    required = {
        "target",
        "profile_scope",
        "roots",
        "subproblems",
        "routes",
        "cycles",
        "unresolved_leaves",
        "truncation",
    }
    if set(result) != required:
        raise RuntimeGraphQueryError(
            "process-chain result fields differ from its contract"
        )
    for name in (
        "profile_scope",
        "roots",
        "subproblems",
        "routes",
        "cycles",
        "unresolved_leaves",
    ):
        if not isinstance(result[name], list):
            raise RuntimeGraphQueryError(
                f"process-chain {name} must be an array"
            )
    subproblems = result["subproblems"]
    if any(not isinstance(row, dict) for row in subproblems):
        raise RuntimeGraphQueryError(
            "process-chain subproblems must be objects"
        )
    subproblem_by_id: dict[str, dict[str, Any]] = {}
    for row in subproblems:
        identifier = row.get("id")
        scope_row = row.get("scope")
        target = row.get("target")
        if (
            not isinstance(identifier, str)
            or not isinstance(scope_row, dict)
            or not isinstance(target, dict)
        ):
            raise RuntimeGraphQueryError(
                "process-chain subproblem identity is invalid"
            )
        scoped = ScopedTarget(
            ProfileScope(
                str(scope_row.get("profile", "")),
                str(scope_row.get("physical_side", "")),
            ),
            target,
        )
        if identifier != _TraversalState._subproblem_id(scoped):
            raise RuntimeGraphQueryError(
                "process-chain subproblem identity differs"
            )
        if identifier in subproblem_by_id:
            raise RuntimeGraphQueryError(
                "process-chain subproblem identity is duplicated"
            )
        if row.get("status") == "pending":
            raise RuntimeGraphQueryError(
                "complete process-chain result retains a pending subproblem"
            )
        route_ids = row.get("route_ids")
        if not isinstance(route_ids, list) or len(set(route_ids)) != len(
            route_ids
        ):
            raise RuntimeGraphQueryError(
                "process-chain subproblem route ids are invalid"
            )
        subproblem_by_id[identifier] = row
    if any(
        not isinstance(identifier, str)
        or identifier not in subproblem_by_id
        for identifier in result["roots"]
    ) or len(set(result["roots"])) != len(result["roots"]):
        raise RuntimeGraphQueryError(
            "process-chain roots do not name unique subproblems"
        )

    route_by_id: dict[str, dict[str, Any]] = {}
    expected_by_subproblem: dict[str, list[str]] = {
        identifier: [] for identifier in subproblem_by_id
    }
    linked_subproblem_ids: set[str] = set()
    for route in result["routes"]:
        if not isinstance(route, dict):
            raise RuntimeGraphQueryError(
                "process-chain routes must be objects"
            )
        identifier = route.get("id")
        subproblem_id = route.get("subproblem_id")
        producer = route.get("producer")
        production = route.get("production")
        if (
            not isinstance(identifier, str)
            or subproblem_id not in subproblem_by_id
            or not isinstance(producer, dict)
            or not isinstance(production, dict)
            or not isinstance(production.get("relationship"), dict)
            or not isinstance(production.get("slot"), dict)
            or not isinstance(
                production.get("matched_alternatives"),
                list,
            )
        ):
            raise RuntimeGraphQueryError(
                "process-chain route identity is invalid"
            )
        occurrence = {
            "owner": producer,
            "owner_relationship": production["relationship"],
            "slot": production["slot"],
            "matched_alternatives": production[
                "matched_alternatives"
            ],
        }
        if identifier != _TraversalState._route_id(occurrence):
            raise RuntimeGraphQueryError(
                "process-chain route identity differs"
            )
        if identifier in route_by_id:
            raise RuntimeGraphQueryError(
                "process-chain route identity is duplicated"
            )
        if (
            production["relationship"].get("subject")
            != producer.get("id")
            or production["relationship"].get("object")
            != production["slot"].get("id")
        ):
            raise RuntimeGraphQueryError(
                "process-chain production endpoints differ"
            )
        for section_name in (
            "ingredient_slots",
            "reusable_requirements",
        ):
            section = route.get(section_name)
            slots = section.get("slots") if isinstance(section, dict) else None
            if not isinstance(slots, list):
                raise RuntimeGraphQueryError(
                    f"process-chain route {section_name} is invalid"
                )
            for slot in slots:
                alternatives = (
                    slot.get("alternatives")
                    if isinstance(slot, dict)
                    else None
                )
                items = (
                    alternatives.get("items")
                    if isinstance(alternatives, dict)
                    else None
                )
                if not isinstance(items, list):
                    raise RuntimeGraphQueryError(
                        "process-chain route alternative page is invalid"
                    )
                for item in items:
                    child_id = (
                        item.get("subproblem_id")
                        if isinstance(item, dict)
                        else None
                    )
                    expansion = (
                        item.get("expansion")
                        if isinstance(item, dict)
                        else None
                    )
                    if (
                        isinstance(expansion, dict)
                        and expansion.get("status") == "linked"
                    ):
                        if child_id not in subproblem_by_id:
                            raise RuntimeGraphQueryError(
                                "process-chain route links an unknown subproblem"
                            )
                        linked_subproblem_ids.add(str(child_id))
        route_by_id[identifier] = route
        expected_by_subproblem[str(subproblem_id)].append(identifier)
    for identifier, subproblem in subproblem_by_id.items():
        if subproblem["route_ids"] != expected_by_subproblem[identifier]:
            raise RuntimeGraphQueryError(
                "process-chain subproblem route membership differs"
            )
    if set(subproblem_by_id) - set(result["roots"]) - linked_subproblem_ids:
        raise RuntimeGraphQueryError(
            "process-chain contains unreachable subproblems"
        )

    cycle_ids: set[str] = set()
    for cycle in result["cycles"]:
        if (
            not isinstance(cycle, dict)
            or not isinstance(cycle.get("id"), str)
            or not isinstance(cycle.get("subproblem_ids"), list)
            or any(
                identifier not in subproblem_by_id
                for identifier in cycle.get("subproblem_ids", [])
            )
        ):
            raise RuntimeGraphQueryError(
                "process-chain cycle is invalid"
            )
        expected_id = "cycle:" + hashlib.sha256(
            "\x1f".join(cycle["subproblem_ids"]).encode("utf-8")
        ).hexdigest()
        if (
            cycle["id"] != expected_id
            or cycle["subproblem_ids"]
            != sorted(set(cycle["subproblem_ids"]))
            or cycle["id"] in cycle_ids
        ):
            raise RuntimeGraphQueryError(
                "process-chain cycle identity differs"
            )
        cycle_ids.add(cycle["id"])
    unresolved_cycle_ids = {
        row.get("cycle_id")
        for row in result["unresolved_leaves"]
        if isinstance(row, dict) and row.get("reason") == "cycle"
    }
    if unresolved_cycle_ids != cycle_ids:
        raise RuntimeGraphQueryError(
            "process-chain cycle unresolved closure differs"
        )

    truncation = result["truncation"]
    if (
        not isinstance(truncation, dict)
        or set(truncation) != {"truncated", "reasons", "limits"}
        or not isinstance(truncation["reasons"], list)
        or truncation["reasons"]
        != [
            reason
            for reason in TRUNCATION_REASONS
            if reason in set(truncation["reasons"])
        ]
        or truncation["truncated"] != bool(truncation["reasons"])
        or not isinstance(truncation["limits"], dict)
        or set(truncation["limits"])
        != {
            "max_depth",
            "max_routes",
            "max_alternatives_per_slot",
            "max_visited_nodes",
        }
    ):
        raise RuntimeGraphQueryError(
            "process-chain truncation differs from its contract"
        )
    unresolved_reasons = {
        row.get("reason")
        for row in result["unresolved_leaves"]
        if isinstance(row, dict)
    }
    if not set(truncation["reasons"]).issubset(unresolved_reasons):
        raise RuntimeGraphQueryError(
            "process-chain truncation lacks unresolved closure"
        )


def canonical_json_bytes(value: Any) -> bytes:
    """Local canonical key helper without importing the Atlas envelope."""

    import json

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_process_chain(
    reader: RuntimeGraphReader,
    selector: NodeSelector,
    options: ChainOptions = ChainOptions(),
    graph_scope: (
        GraphScope | ProfileScope | Iterable[ProfileScope] | None
    ) = None,
) -> dict[str, Any]:
    """Convenience wrapper for :class:`RuntimeGraphChainQuery`."""

    return RuntimeGraphChainQuery(reader).chain(
        selector,
        options=options,
        graph_scope=graph_scope,
    )


def build_process_chain_from_targets(
    reader: RuntimeGraphReader,
    targets: Iterable[ScopedTarget],
    options: ChainOptions = ChainOptions(),
    additional_root_provider: (
        Callable[[dict[str, Any]], Iterable[ScopedTarget]] | None
    ) = None,
) -> dict[str, Any]:
    """Build one bounded route DAG from exact pre-resolved targets."""

    return RuntimeGraphChainQuery(reader).chain_resolved_targets(
        targets,
        options=options,
        additional_root_provider=additional_root_provider,
    )
