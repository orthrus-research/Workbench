#!/usr/bin/env python3

"""Fairly bounded forward-consumption DAG queries over exact runtime nodes.

The upstream process-chain query asks which operations can produce a target.
This module answers the complementary question for an already-resolved set of
mechanical co-outputs: which exact operations consume them, and where can the
observed outputs go?

Roots are deduplicated only by exact scoped runtime identity.  Traversal is
breadth-first and fair within each depth: occurrence rank zero is considered
for every target before rank one.  Reusable ``requires`` occurrences are
counted but never admitted as recycling operations.
"""

from __future__ import annotations

from collections import defaultdict, deque
import copy
from dataclasses import dataclass
import hashlib
from typing import Any, Iterable

from workbench_atlas.runtime_graph_domain_query import (
    CONSUMING_PREDICATES,
    GraphScope,
    PRESENTATION_OWNER_KINDS,
    PRODUCER_PREDICATES,
    ProfileScope,
    REUSABLE_CONSUMER_PREDICATES,
    RuntimeGraphDomainQuery,
    ScopedTarget,
    TargetMatch,
    alternative_semantics,
)
from workbench_atlas.runtime_graph_query import (
    MAX_QUERY_PAGE_SIZE,
    NodeSelector,
    PageRequest,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
    decode_record,
)


PROCEDURAL_OWNER_KINDS = frozenset({"recipe_rule", "process_rule"})
INPUT_PREDICATES = ("consumes", "may_consume", "requires")
RECYCLING_TRUNCATION_REASONS = (
    "max-depth",
    "max-operations",
    "max-consumers-per-target",
    "max-output-alternatives-per-slot",
    "max-visited-targets",
)
RECYCLING_CLOSURE_CLASSIFICATIONS = frozenset(
    {
        "returns-final-target",
        "rejoins-retained-route",
        "forward-cycle",
        "open-terminal",
        "open-procedural-boundary",
        "open-truncated",
    }
)


@dataclass(frozen=True)
class RecyclingOptions:
    """Hard bounds for one shared forward-consumption traversal."""

    max_depth: int = 4
    max_operations: int = 250
    max_consumers_per_target: int = 25
    max_output_alternatives_per_slot: int = 25
    max_visited_targets: int = 10_000

    def __post_init__(self) -> None:
        self._bounded_integer("max_depth", self.max_depth, minimum=0)
        self._bounded_integer(
            "max_operations",
            self.max_operations,
            minimum=1,
        )
        self._bounded_integer(
            "max_consumers_per_target",
            self.max_consumers_per_target,
            minimum=1,
            maximum=MAX_QUERY_PAGE_SIZE,
        )
        self._bounded_integer(
            "max_output_alternatives_per_slot",
            self.max_output_alternatives_per_slot,
            minimum=1,
        )
        self._bounded_integer(
            "max_visited_targets",
            self.max_visited_targets,
            minimum=1,
        )

    @staticmethod
    def _bounded_integer(
        name: str,
        value: object,
        *,
        minimum: int,
        maximum: int | None = None,
    ) -> None:
        if (
            type(value) is not int
            or value < minimum
            or (maximum is not None and value > maximum)
        ):
            suffix = (
                f" through {maximum}"
                if maximum is not None
                else f" or greater"
            )
            raise RuntimeGraphQueryError(
                f"recycling option {name} must be an integer from "
                f"{minimum}{suffix}"
            )

    def limits(self) -> dict[str, int]:
        return {
            "max_depth": self.max_depth,
            "max_operations": self.max_operations,
            "max_consumers_per_target": self.max_consumers_per_target,
            "max_output_alternatives_per_slot": (
                self.max_output_alternatives_per_slot
            ),
            "max_visited_targets": self.max_visited_targets,
        }


@dataclass(frozen=True)
class RecyclingRootSeed:
    """One co-output occurrence before exact-target deduplication."""

    target: ScopedTarget
    origin: dict[str, Any]


@dataclass(frozen=True)
class RecyclingRouteTarget:
    """One retained upstream route target eligible for route re-entry."""

    subproblem_id: str
    target: ScopedTarget


def _json_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_tree(child) for child in value]
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeGraphQueryError(f"{label} must be a non-empty string")
    return value


def _record_id(record: object, label: str = "runtime record") -> str:
    if not isinstance(record, dict):
        raise RuntimeGraphQueryError(f"{label} must be an object")
    return _required_text(record.get("id"), f"{label} id")


def _record_kind(record: object, label: str = "runtime node") -> str:
    if not isinstance(record, dict) or record.get("record_type") != "node":
        raise RuntimeGraphQueryError(f"{label} must be a runtime node")
    return _required_text(record.get("kind"), f"{label} kind")


def _scope_dict(scope: ProfileScope) -> dict[str, str]:
    if not isinstance(scope, ProfileScope):
        raise RuntimeGraphQueryError(
            "recycling target scope must be a ProfileScope"
        )
    return scope.to_dict()


def _record_scope(record: dict[str, Any], label: str) -> ProfileScope:
    value = record.get("scope")
    if not isinstance(value, dict):
        raise RuntimeGraphQueryError(f"{label} has no runtime scope")
    return ProfileScope(
        _required_text(value.get("profile"), f"{label} scope profile"),
        _required_text(
            value.get("physical_side"),
            f"{label} scope physical side",
        ),
    )


def _target_key(target: ScopedTarget) -> tuple[str, str, str, str]:
    if not isinstance(target, ScopedTarget):
        raise RuntimeGraphQueryError(
            "recycling target must be a ScopedTarget"
        )
    return (
        target.scope.profile,
        target.scope.physical_side,
        _record_kind(target.node),
        _record_id(target.node, "recycling target"),
    )


def _target_key_text(target: ScopedTarget) -> str:
    return "\x1f".join(_target_key(target))


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _subproblem_id(target: ScopedTarget) -> str:
    return _stable_id("recycling-subproblem:", _target_key_text(target))


def _root_id(target: ScopedTarget) -> str:
    return _stable_id("recycling-root:", _target_key_text(target))


def _origin_key(origin: dict[str, Any]) -> tuple[str, ...]:
    return (
        _required_text(origin.get("route_id"), "recycling origin route id"),
        _required_text(
            origin.get("subproblem_id"),
            "recycling origin subproblem id",
        ),
        _record_id(origin.get("producer"), "recycling origin producer"),
        _record_id(
            origin.get("byproduct_relationship"),
            "recycling origin byproduct relationship",
        ),
        _record_id(origin.get("slot"), "recycling origin slot"),
        _record_id(
            origin.get("alternative_relationship"),
            "recycling origin alternative relationship",
        ),
    )


def _validate_seed(seed: RecyclingRootSeed) -> tuple[str, str, str, str]:
    if not isinstance(seed, RecyclingRootSeed):
        raise RuntimeGraphQueryError(
            "recycling roots must contain RecyclingRootSeed values"
        )
    key = _target_key(seed.target)
    if _record_scope(seed.target.node, "recycling target") != seed.target.scope:
        raise RuntimeGraphQueryError(
            "recycling target scope differs from its node record: "
            + _record_id(seed.target.node)
        )
    origin = seed.origin
    if not isinstance(origin, dict):
        raise RuntimeGraphQueryError("recycling root origin must be an object")
    _origin_key(origin)
    producer = origin.get("producer")
    relationship = origin.get("byproduct_relationship")
    slot = origin.get("slot")
    alternative_relationship = origin.get("alternative_relationship")
    local_semantics = origin.get("local_semantics")
    if (
        not isinstance(producer, dict)
        or producer.get("record_type") != "node"
        or not isinstance(relationship, dict)
        or relationship.get("record_type") != "edge"
        or relationship.get("predicate") not in PRODUCER_PREDICATES
        or not isinstance(slot, dict)
        or slot.get("record_type") != "node"
        or not isinstance(alternative_relationship, dict)
        or alternative_relationship.get("record_type") != "edge"
        or alternative_relationship.get("predicate") != "accepts_alternative"
        or not isinstance(local_semantics, dict)
    ):
        raise RuntimeGraphQueryError(
            "recycling root origin omits exact co-output records"
        )
    output_semantics = local_semantics.get("output")
    expected_conditional = relationship.get("predicate") == "may_produce"
    if (
        not isinstance(output_semantics, dict)
        or output_semantics.get("conditional") is not expected_conditional
        or output_semantics.get("guaranteed") is not (
            not expected_conditional
        )
        or local_semantics.get("amount_source")
        != "exact-slot-and-relationship-records"
        or not isinstance(local_semantics.get("chance"), dict)
    ):
        raise RuntimeGraphQueryError(
            "recycling root local semantics differ from its co-output"
        )
    producer_id = _record_id(producer)
    slot_id = _record_id(slot)
    target_id = _record_id(seed.target.node)
    if (
        relationship.get("subject") != producer_id
        or relationship.get("object") != slot_id
        or alternative_relationship.get("subject") != slot_id
        or alternative_relationship.get("object") != target_id
    ):
        raise RuntimeGraphQueryError(
            "recycling root origin relationship endpoints differ"
        )
    for label, record in (
        ("producer", producer),
        ("byproduct relationship", relationship),
        ("slot", slot),
        ("alternative relationship", alternative_relationship),
    ):
        if _record_scope(record, f"recycling origin {label}") != seed.target.scope:
            raise RuntimeGraphQueryError(
                f"recycling origin {label} escaped its exact target scope"
            )
    return key


def canonical_recycling_roots(
    seeds: Iterable[RecyclingRootSeed],
) -> list[dict[str, Any]]:
    """Validate, exactly deduplicate, and canonically order co-output roots."""

    try:
        candidates = tuple(seeds)
    except TypeError as exc:
        raise RuntimeGraphQueryError(
            "recycling roots must be an iterable of RecyclingRootSeed values"
        ) from exc

    grouped: dict[
        tuple[str, str, str, str],
        tuple[ScopedTarget, dict[tuple[str, ...], dict[str, Any]]],
    ] = {}
    for seed in candidates:
        key = _validate_seed(seed)
        current = grouped.get(key)
        if current is None:
            current = (seed.target, {})
            grouped[key] = current
        elif current[0].node != seed.target.node:
            raise RuntimeGraphQueryError(
                "recycling roots disagree on one exact target record: "
                + _record_id(seed.target.node)
            )
        origin_key = _origin_key(seed.origin)
        previous = current[1].get(origin_key)
        if previous is not None and previous != seed.origin:
            raise RuntimeGraphQueryError(
                "recycling roots disagree on one exact origin: "
                + "/".join(origin_key)
            )
        current[1][origin_key] = seed.origin

    roots: list[dict[str, Any]] = []
    for key in sorted(grouped):
        target, origins = grouped[key]
        roots.append(
            {
                "id": _root_id(target),
                "subproblem_id": _subproblem_id(target),
                "scope": _scope_dict(target.scope),
                "target": target.node,
                "origins": [
                    origins[origin_key]
                    for origin_key in sorted(origins)
                ],
            }
        )
    return _json_tree(roots)


def _record_attributes(record: object) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    value = record.get("attributes")
    return value if isinstance(value, dict) else {}


def _integer_attribute(record: object, key: str) -> int:
    value = _record_attributes(record).get(key)
    return value if type(value) is int else 2**63 - 1


def _slot_order(row: dict[str, Any]) -> tuple[int, str, str, str]:
    relationship = row.get("relationship")
    slot = row.get("slot")
    ordinal = _integer_attribute(slot, "ordinal")
    if ordinal == 2**63 - 1:
        ordinal = _integer_attribute(relationship, "ordinal")
    return (
        ordinal,
        str(relationship.get("predicate", ""))
        if isinstance(relationship, dict)
        else "",
        _record_id(slot),
        _record_id(relationship),
    )


def _alternative_order(row: dict[str, Any]) -> tuple[int, str, str, str]:
    relationship = row.get("relationship")
    node = row.get("node")
    return (
        _integer_attribute(relationship, "alternative_ordinal"),
        str(node.get("kind", "")) if isinstance(node, dict) else "",
        _record_id(node),
        _record_id(relationship),
    )


def _chance_projection(*records: object) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    labels = (
        "owner_relationship",
        "slot",
        "alternative_relationship",
    )
    for label, record in zip(labels, records):
        chance = {
            key: value
            for key, value in _record_attributes(record).items()
            if "chance" in key.lower() or "probab" in key.lower()
        }
        if chance:
            result[label] = chance
    return result


def _alternative_page_semantics(
    alternatives: Iterable[dict[str, Any]],
) -> tuple[str, bool]:
    classifications = {
        alternative_semantics(alternative["relationship"])
        for alternative in alternatives
    }
    if {
        "authoritative-match-domain",
        "non-exhaustive-representative",
    } & classifications:
        return "MATCH_DOMAIN", False
    if "symbolic" in classifications:
        return "SYMBOLIC", False
    return "OR", True


def _input_slot_projection(row: dict[str, Any]) -> dict[str, Any]:
    predicate = str(row["relationship"].get("predicate", ""))
    alternatives = sorted(row["alternatives"], key=_alternative_order)
    mode, exhaustive = _alternative_page_semantics(alternatives)
    result: dict[str, Any] = {
        "relationship": row["relationship"],
        "slot": row["slot"],
        "semantics": {
            "jointly_required": True,
            "reusable": predicate == "requires",
            "consumed": predicate != "requires",
            "conditional": predicate == "may_consume",
            "guaranteed": predicate != "may_consume",
        },
        "alternatives": {
            "mode": mode,
            "exhaustive": exhaustive,
            "items": [
                {
                    **alternative,
                    "alternative_semantics": alternative_semantics(
                        alternative["relationship"]
                    ),
                }
                for alternative in alternatives
            ],
            "total": len(alternatives),
            "returned": len(alternatives),
            "truncated": False,
        },
    }
    chance = _chance_projection(row["relationship"], row["slot"])
    if chance:
        result["chance"] = chance
    return result


def _mechanically_eligible(occurrence: dict[str, Any]) -> bool:
    mechanics = occurrence.get("mechanics")
    if not isinstance(mechanics, dict) or not mechanics.get("eligible"):
        return False
    if occurrence["owner"].get("kind") == "recipe":
        return bool(mechanics.get("execution"))
    return True


def _ineligible_reason(occurrence: dict[str, Any]) -> str:
    owner = occurrence["owner"]
    if owner.get("kind") in PRESENTATION_OWNER_KINDS:
        return "presentation-only-evidence"
    mechanics = occurrence.get("mechanics")
    if isinstance(mechanics, dict):
        classification = str(mechanics.get("classification", ""))
        if "presentation" in classification:
            return "presentation-only-evidence"
    return "missing-execution-evidence"


class RuntimeGraphRecyclingQuery:
    """Build one exact, deterministic forward-consumption DAG."""

    def __init__(self, reader: RuntimeGraphReader):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "recycling query requires a RuntimeGraphReader"
            )
        self.reader = reader
        self.domain = RuntimeGraphDomainQuery(reader)

    def paths(
        self,
        roots: Iterable[RecyclingRootSeed],
        *,
        final_targets: Iterable[ScopedTarget] = (),
        route_targets: Iterable[RecyclingRouteTarget] = (),
        options: RecyclingOptions = RecyclingOptions(),
    ) -> dict[str, Any]:
        if not isinstance(options, RecyclingOptions):
            raise RuntimeGraphQueryError(
                "recycling query requires RecyclingOptions"
            )
        root_seeds = tuple(roots)
        root_documents = canonical_recycling_roots(root_seeds)
        normalized_roots = self._validate_exact_roots(
            root_seeds,
            root_documents,
        )
        normalized_final_targets = self._normalize_targets(
            final_targets,
            "final",
        )
        normalized_route_targets = self._normalize_route_targets(
            route_targets,
        )
        if options.max_visited_targets < len(root_documents):
            raise RuntimeGraphQueryError(
                "recycling max_visited_targets must cover every exact root: "
                f"{options.max_visited_targets} < {len(root_documents)}"
            )
        state = _RecyclingTraversalState(
            self.reader,
            self.domain,
            options,
            normalized_final_targets,
            normalized_route_targets,
        )
        return _json_tree(state.run(normalized_roots, root_documents))

    def _validate_target(
        self,
        target: ScopedTarget,
        label: str,
    ) -> ScopedTarget:
        _target_key(target)
        if _record_scope(target.node, label) != target.scope:
            raise RuntimeGraphQueryError(
                f"{label} scope differs from its node record"
            )
        matches = self.reader.resolve_exact(
            NodeSelector.by_id(
                _record_id(target.node, label),
                kind=_record_kind(target.node, label),
                profile=target.scope.profile,
                physical_side=target.scope.physical_side,
            )
        )
        if len(matches) != 1 or matches[0] != target.node:
            raise RuntimeGraphQueryError(
                f"{label} is not an exact node in the open graph: "
                + _record_id(target.node)
            )
        return target

    def _validate_exact_roots(
        self,
        seeds: tuple[RecyclingRootSeed, ...],
        documents: list[dict[str, Any]],
    ) -> tuple[ScopedTarget, ...]:
        by_key: dict[tuple[str, str, str, str], ScopedTarget] = {}
        for seed in seeds:
            target = self._validate_target(seed.target, "recycling root")
            by_key[_target_key(target)] = target
        if len(by_key) != len(documents):
            raise RuntimeGraphQueryError(
                "canonical recycling root count differs from exact targets"
            )
        return tuple(by_key[key] for key in sorted(by_key))

    def _normalize_targets(
        self,
        targets: Iterable[ScopedTarget],
        label: str,
    ) -> tuple[ScopedTarget, ...]:
        try:
            candidates = tuple(targets)
        except TypeError as exc:
            raise RuntimeGraphQueryError(
                f"recycling {label} targets must be iterable"
            ) from exc
        by_key: dict[tuple[str, str, str, str], ScopedTarget] = {}
        for target in candidates:
            normalized = self._validate_target(
                target,
                f"recycling {label} target",
            )
            key = _target_key(normalized)
            previous = by_key.get(key)
            if previous is not None and previous.node != normalized.node:
                raise RuntimeGraphQueryError(
                    f"recycling {label} targets disagree on one exact node"
                )
            by_key[key] = normalized
        return tuple(by_key[key] for key in sorted(by_key))

    def _normalize_route_targets(
        self,
        targets: Iterable[RecyclingRouteTarget],
    ) -> tuple[RecyclingRouteTarget, ...]:
        try:
            candidates = tuple(targets)
        except TypeError as exc:
            raise RuntimeGraphQueryError(
                "recycling route targets must be iterable"
            ) from exc
        by_key: dict[
            tuple[str, tuple[str, str, str, str]],
            RecyclingRouteTarget,
        ] = {}
        by_subproblem_id: dict[str, tuple[str, str, str, str]] = {}
        for candidate in candidates:
            if not isinstance(candidate, RecyclingRouteTarget):
                raise RuntimeGraphQueryError(
                    "recycling route targets must contain "
                    "RecyclingRouteTarget values"
                )
            subproblem_id = _required_text(
                candidate.subproblem_id,
                "recycling route subproblem id",
            )
            target = self._validate_target(
                candidate.target,
                "recycling route target",
            )
            target_key = _target_key(target)
            previous_target_key = by_subproblem_id.get(subproblem_id)
            if (
                previous_target_key is not None
                and previous_target_key != target_key
            ):
                raise RuntimeGraphQueryError(
                    "one recycling route subproblem names multiple exact "
                    "targets"
                )
            by_subproblem_id[subproblem_id] = target_key
            key = (subproblem_id, target_key)
            previous = by_key.get(key)
            if previous is not None and previous.target.node != target.node:
                raise RuntimeGraphQueryError(
                    "recycling route targets disagree on one retained "
                    "subproblem"
                )
            by_key[key] = RecyclingRouteTarget(subproblem_id, target)
        return tuple(by_key[key] for key in sorted(by_key))


class _RecyclingTraversalState:
    def __init__(
        self,
        reader: RuntimeGraphReader,
        domain: RuntimeGraphDomainQuery,
        options: RecyclingOptions,
        final_targets: tuple[ScopedTarget, ...],
        route_targets: tuple[RecyclingRouteTarget, ...],
    ):
        self.reader = reader
        self.domain = domain
        self.options = options
        self.final_targets = final_targets
        self.route_targets = route_targets
        self.targets: dict[str, ScopedTarget] = {}
        self.subproblems: dict[str, dict[str, Any]] = {}
        self.target_ids: dict[tuple[str, str, str, str], str] = {}
        self.frontiers: dict[int, set[str]] = defaultdict(set)
        self.operations: dict[str, dict[str, Any]] = {}
        self.continuations: dict[str, dict[str, Any]] = {}
        self.closures: dict[str, dict[str, Any]] = {}
        self.cycles: dict[str, dict[str, Any]] = {}
        self.terminal_leaves: dict[str, dict[str, Any]] = {}
        self.unresolved: dict[str, dict[str, Any]] = {}
        self.truncation_reasons: set[str] = set()
        self.destination_matches: dict[
            tuple[str, str, str],
            list[dict[str, Any]],
        ] = defaultdict(list)
        self.subproblem_matches: dict[
            tuple[str, str, str],
            list[tuple[str, TargetMatch]],
        ] = defaultdict(list)
        self.operation_admission_count = 0
        self.consuming_candidate_count = 0
        self.reusable_candidate_count = 0
        self.active_depth: dict[str, Any] | None = None
        self._prepare_destination_matches()

    @staticmethod
    def _match_key(
        scope: ProfileScope,
        node: dict[str, Any],
    ) -> tuple[str, str, str]:
        return (
            scope.profile,
            scope.physical_side,
            _record_id(node),
        )

    def _match_document(self, match: TargetMatch) -> dict[str, Any]:
        return {
            "role": match.match_role,
            "path": list(match.path),
        }

    def _closure_matches(
        self,
        target: ScopedTarget,
    ) -> tuple[TargetMatch, ...]:
        closure = self.domain.target_match_closure(target, "producer")
        if closure.truncated:
            raise RuntimeGraphQueryError(
                "recycling destination identity closure is truncated: "
                + _record_id(target.node)
            )
        return closure.matches

    def _prepare_destination_matches(self) -> None:
        for target in self.final_targets:
            for match in self._closure_matches(target):
                self.destination_matches[
                    self._match_key(target.scope, match.node)
                ].append(
                    {
                        "classification": "returns-final-target",
                        "scope": _scope_dict(target.scope),
                        "target": target.node,
                        "match": self._match_document(match),
                    }
                )
        for route_target in self.route_targets:
            target = route_target.target
            for match in self._closure_matches(target):
                self.destination_matches[
                    self._match_key(target.scope, match.node)
                ].append(
                    {
                        "classification": "rejoins-retained-route",
                        "route_subproblem_id": route_target.subproblem_id,
                        "scope": _scope_dict(target.scope),
                        "target": target.node,
                        "match": self._match_document(match),
                    }
                )
        for key in self.destination_matches:
            self.destination_matches[key].sort(
                key=lambda row: (
                    0
                    if row["classification"] == "returns-final-target"
                    else 1,
                    str(row.get("route_subproblem_id", "")),
                    _record_id(row["target"]),
                    tuple(
                        _record_id(edge)
                        for edge in row["match"]["path"]
                    ),
                )
            )

    def _register_subproblem_matches(
        self,
        identifier: str,
        target: ScopedTarget,
    ) -> None:
        for match in self._closure_matches(target):
            key = self._match_key(target.scope, match.node)
            self.subproblem_matches[key].append((identifier, match))
            self.subproblem_matches[key].sort(
                key=lambda row: (
                    _target_key(self.targets[row[0]]),
                    row[0],
                    tuple(_record_id(edge) for edge in row[1].path),
                )
            )

    def _new_subproblem(
        self,
        target: ScopedTarget,
        depth: int,
    ) -> str | None:
        key = _target_key(target)
        existing = self.target_ids.get(key)
        if existing is not None:
            if depth < int(self.subproblems[existing]["depth"]):
                self.subproblems[existing]["depth"] = depth
                self.frontiers[depth].add(existing)
            return existing
        if len(self.subproblems) >= self.options.max_visited_targets:
            self.truncation_reasons.add("max-visited-targets")
            return None
        identifier = _subproblem_id(target)
        if identifier in self.subproblems:
            raise RuntimeGraphQueryError(
                "recycling subproblem hash collision: " + identifier
            )
        self.target_ids[key] = identifier
        self.targets[identifier] = target
        self.subproblems[identifier] = {
            "id": identifier,
            "scope": _scope_dict(target.scope),
            "target": target.node,
            "depth": depth,
            "root_ids": [],
            "consumer_page": {
                "predicates": list(CONSUMING_PREDICATES),
                "limit": self.options.max_consumers_per_target,
                "offset": 0,
                "returned": 0,
                "total": 0,
                "truncated": False,
                "candidate_ids": [],
            },
            "reusable_observation": {
                "predicates": list(REUSABLE_CONSUMER_PREDICATES),
                "total": 0,
                "policy": "counted-not-traversed",
            },
            "candidate_dispositions": [],
            "operation_ids": [],
            "status": "pending",
        }
        self.frontiers[depth].add(identifier)
        self._register_subproblem_matches(identifier, target)
        return identifier

    def _selector(self, target: ScopedTarget) -> NodeSelector:
        return NodeSelector.by_id(
            _record_id(target.node),
            kind=_record_kind(target.node),
            profile=target.scope.profile,
            physical_side=target.scope.physical_side,
        )

    def _occurrence_page(
        self,
        target: ScopedTarget,
        predicates: tuple[str, ...],
        limit: int,
    ) -> tuple[tuple[dict[str, Any], ...], int, bool]:
        result = self.domain.consumers(
            self._selector(target),
            PageRequest(limit=limit, offset=0),
            GraphScope.of(target.scope),
            predicates=predicates,
        )
        resolved = result.resolution.targets
        if len(resolved) != 1 or resolved[0].node != target.node:
            raise RuntimeGraphQueryError(
                "recycling consumer query changed its exact target"
            )
        return result.page.items, result.page.total, result.page.truncated

    @staticmethod
    def _candidate_id(
        subproblem_id: str,
        occurrence: dict[str, Any],
    ) -> str:
        return _stable_id(
            "recycling-candidate:",
            subproblem_id,
            _record_id(occurrence["owner"]),
            _record_id(occurrence["owner_relationship"]),
            _record_id(occurrence["slot"]),
            *(
                _record_id(row["relationship"])
                for row in occurrence.get("matched_alternatives", ())
            ),
        )

    def _prepare_subproblem(
        self,
        identifier: str,
    ) -> tuple[dict[str, Any], ...]:
        subproblem = self.subproblems[identifier]
        target = self.targets[identifier]
        consuming, consuming_total, consuming_truncated = (
            self._occurrence_page(
                target,
                CONSUMING_PREDICATES,
                self.options.max_consumers_per_target,
            )
        )
        _, reusable_total, _ = self._occurrence_page(
            target,
            REUSABLE_CONSUMER_PREDICATES,
            1,
        )
        candidate_ids = [
            self._candidate_id(identifier, occurrence)
            for occurrence in consuming
        ]
        subproblem["consumer_page"] = {
            "predicates": list(CONSUMING_PREDICATES),
            "limit": self.options.max_consumers_per_target,
            "offset": 0,
            "returned": len(consuming),
            "total": consuming_total,
            "truncated": consuming_truncated,
            "candidate_ids": candidate_ids,
        }
        subproblem["reusable_observation"] = {
            "predicates": list(REUSABLE_CONSUMER_PREDICATES),
            "total": reusable_total,
            "policy": "counted-not-traversed",
        }
        self.consuming_candidate_count += consuming_total
        self.reusable_candidate_count += reusable_total
        if consuming_truncated:
            self.truncation_reasons.add("max-consumers-per-target")
            self._add_unresolved(
                "max-consumers-per-target",
                subproblem_id=identifier,
                scope=subproblem["scope"],
                target=target.node,
                details={
                    "returned": len(consuming),
                    "total": consuming_total,
                },
            )
            self._add_open_subproblem_closure(
                identifier,
                "open-truncated",
                "max-consumers-per-target",
            )
        return consuming

    def _add_unresolved(
        self,
        reason: str,
        *,
        subproblem_id: str | None = None,
        scope: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
        candidate_id: str | None = None,
        occurrence: dict[str, Any] | None = None,
        operation_id: str | None = None,
        slot: dict[str, Any] | None = None,
        alternative: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        identifier = _stable_id(
            "recycling-unresolved:",
            reason,
            subproblem_id or "",
            candidate_id or "",
            operation_id or "",
            _record_id(slot["slot"]) if slot is not None else "",
            _record_id(alternative["relationship"])
            if alternative is not None
            else "",
        )
        row: dict[str, Any] = {"id": identifier, "reason": reason}
        if subproblem_id is not None:
            row["subproblem_id"] = subproblem_id
        if scope is not None:
            row["scope"] = scope
        if target is not None:
            row["target"] = target
        if candidate_id is not None:
            row["candidate_id"] = candidate_id
        if occurrence is not None:
            row["candidate_consumer"] = occurrence
        if operation_id is not None:
            row["operation_id"] = operation_id
        if slot is not None:
            row["output_slot"] = slot
        if alternative is not None:
            row["alternative"] = alternative
        if details:
            row["details"] = details
        previous = self.unresolved.get(identifier)
        if previous is not None and previous != row:
            raise RuntimeGraphQueryError(
                "recycling unresolved identity projects inconsistently: "
                + identifier
            )
        self.unresolved[identifier] = row
        return identifier

    def _add_closure(self, row: dict[str, Any]) -> str:
        classification = row.get("classification")
        if classification not in RECYCLING_CLOSURE_CLASSIFICATIONS:
            raise RuntimeGraphQueryError(
                "recycling closure has unsupported classification"
            )
        identity_parts = (
            classification,
            row.get("subproblem_id", ""),
            row.get("operation_id", ""),
            row.get("output_slot_id", ""),
            row.get("alternative_relationship_id", ""),
            row.get("destination_subproblem_id", ""),
            row.get("route_subproblem_id", ""),
            row.get("reason", ""),
        )
        identifier = _stable_id("recycling-closure:", *identity_parts)
        projected = {"id": identifier, **row}
        previous = self.closures.get(identifier)
        if previous is not None and previous != projected:
            raise RuntimeGraphQueryError(
                "recycling closure identity projects inconsistently: "
                + identifier
            )
        self.closures[identifier] = projected
        if classification.startswith("open-"):
            leaf = {
                "id": _stable_id("recycling-terminal:", identifier),
                "closure_id": identifier,
                "classification": classification,
                "subproblem_id": row.get("subproblem_id"),
                "reason": row.get("reason"),
            }
            if isinstance(row.get("scope"), dict):
                leaf["scope"] = row["scope"]
            if isinstance(row.get("target"), dict):
                leaf["target"] = row["target"]
            self.terminal_leaves[leaf["id"]] = leaf
        return identifier

    def _add_open_subproblem_closure(
        self,
        subproblem_id: str,
        classification: str,
        reason: str,
    ) -> str:
        subproblem = self.subproblems[subproblem_id]
        return self._add_closure(
            {
                "classification": classification,
                "subproblem_id": subproblem_id,
                "scope": subproblem["scope"],
                "target": subproblem["target"],
                "reason": reason,
            }
        )

    def _procedural_boundaries(
        self,
        owner: dict[str, Any],
    ) -> list[dict[str, Any]]:
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
        return [
            {
                "relationship": decode_record(encoded_edge, "edge"),
                "rule": decode_record(encoded_node, "node"),
            }
            for encoded_edge, encoded_node in rows
        ]

    def _consumption_projection(
        self,
        occurrence: dict[str, Any],
    ) -> dict[str, Any]:
        predicate = str(
            occurrence["owner_relationship"].get("predicate", "")
        )
        alternatives = sorted(
            occurrence.get("alternatives", ()),
            key=_alternative_order,
        )
        matched = sorted(
            occurrence.get("matched_alternatives", ()),
            key=_alternative_order,
        )
        mode, exhaustive = _alternative_page_semantics(alternatives)
        result: dict[str, Any] = {
            "target": occurrence["target"],
            "relationship": occurrence["owner_relationship"],
            "slot": occurrence["slot"],
            "semantics": {
                "direction": "input",
                "conditional": predicate == "may_consume",
                "guaranteed": predicate == "consumes",
                "consumed": True,
                "reusable": False,
            },
            "alternatives": {
                "mode": mode,
                "exhaustive": exhaustive,
                "items": [
                    {
                        **alternative,
                        "alternative_semantics": alternative_semantics(
                            alternative["relationship"]
                        ),
                    }
                    for alternative in alternatives
                ],
                "total": len(alternatives),
                "returned": len(alternatives),
                "truncated": False,
            },
            "matched_alternatives": matched,
            "match_closure": occurrence.get(
                "match_closure",
                {"truncated": False, "reasons": []},
            ),
        }
        chance = _chance_projection(
            occurrence["owner_relationship"],
            occurrence["slot"],
            matched[0]["relationship"] if matched else {},
        )
        if chance:
            result["chance"] = chance
        return result

    def _operation_id(
        self,
        subproblem_id: str,
        occurrence: dict[str, Any],
    ) -> str:
        return _stable_id(
            "recycling-operation:",
            subproblem_id,
            _record_id(occurrence["owner"]),
            _record_id(occurrence["owner_relationship"]),
            _record_id(occurrence["slot"]),
            *(
                _record_id(row["relationship"])
                for row in occurrence.get("matched_alternatives", ())
            ),
        )

    def _is_ancestor(
        self,
        candidate_id: str,
        current_id: str,
    ) -> bool:
        if candidate_id == current_id:
            return True
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in self.continuations.values():
            adjacency[edge["from_subproblem_id"]].append(
                edge["to_subproblem_id"]
            )
        queue = deque([candidate_id])
        visited = {candidate_id}
        while queue:
            identifier = queue.popleft()
            for neighbor in adjacency.get(identifier, ()):
                if neighbor == current_id:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False

    def _destination_rows(
        self,
        scope: ProfileScope,
        node: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rows = self.destination_matches.get(
            self._match_key(scope, node),
            (),
        )
        final = [
            row
            for row in rows
            if row["classification"] == "returns-final-target"
        ]
        return final if final else list(rows)

    def _ancestor_rows(
        self,
        current_subproblem_id: str,
        scope: ProfileScope,
        node: dict[str, Any],
    ) -> list[tuple[str, TargetMatch]]:
        return [
            (identifier, match)
            for identifier, match in self.subproblem_matches.get(
                self._match_key(scope, node),
                (),
            )
            if self._is_ancestor(identifier, current_subproblem_id)
        ]

    def _add_closed_output(
        self,
        *,
        classification: str,
        subproblem_id: str,
        operation_id: str,
        slot: dict[str, Any],
        alternative: dict[str, Any],
        destination: dict[str, Any],
    ) -> str:
        row: dict[str, Any] = {
            "classification": classification,
            "subproblem_id": subproblem_id,
            "operation_id": operation_id,
            "output_slot_id": _record_id(slot["slot"]),
            "alternative_relationship_id": _record_id(
                alternative["relationship"]
            ),
            "scope": self.subproblems[subproblem_id]["scope"],
            "target": alternative["node"],
            "output_slot": slot,
            "alternative": alternative,
            "destination_scope": destination["scope"],
            "destination_target": destination["target"],
            "match": destination["match"],
        }
        if "route_subproblem_id" in destination:
            row["route_subproblem_id"] = destination[
                "route_subproblem_id"
            ]
        return self._add_closure(row)

    def _add_cycle(
        self,
        *,
        subproblem_id: str,
        operation_id: str,
        slot: dict[str, Any],
        alternative: dict[str, Any],
        ancestor_id: str,
        match: TargetMatch,
    ) -> None:
        destination = self.subproblems[ancestor_id]
        closure_id = self._add_closure(
            {
                "classification": "forward-cycle",
                "subproblem_id": subproblem_id,
                "operation_id": operation_id,
                "output_slot_id": _record_id(slot["slot"]),
                "alternative_relationship_id": _record_id(
                    alternative["relationship"]
                ),
                "destination_subproblem_id": ancestor_id,
                "scope": self.subproblems[subproblem_id]["scope"],
                "target": alternative["node"],
                "output_slot": slot,
                "alternative": alternative,
                "destination_scope": destination["scope"],
                "destination_target": destination["target"],
                "match": self._match_document(match),
            }
        )
        cycle_id = _stable_id(
            "recycling-cycle:",
            closure_id,
            ancestor_id,
            subproblem_id,
        )
        self.cycles[cycle_id] = {
            "id": cycle_id,
            "closure_id": closure_id,
            "from_subproblem_id": subproblem_id,
            "to_ancestor_subproblem_id": ancestor_id,
            "operation_id": operation_id,
        }

    def _add_continuation(
        self,
        *,
        subproblem_id: str,
        operation_id: str,
        slot: dict[str, Any],
        alternative: dict[str, Any],
        scope: ProfileScope,
        depth: int,
    ) -> None:
        target = ScopedTarget(scope, alternative["node"])
        child_id = self._new_subproblem(target, depth + 1)
        if child_id is None:
            self._add_closure(
                {
                    "classification": "open-truncated",
                    "subproblem_id": subproblem_id,
                    "operation_id": operation_id,
                    "output_slot_id": _record_id(slot["slot"]),
                    "alternative_relationship_id": _record_id(
                        alternative["relationship"]
                    ),
                    "scope": self.subproblems[subproblem_id]["scope"],
                    "target": alternative["node"],
                    "output_slot": slot,
                    "alternative": alternative,
                    "reason": "max-visited-targets",
                }
            )
            self._add_unresolved(
                "max-visited-targets",
                subproblem_id=subproblem_id,
                scope=self.subproblems[subproblem_id]["scope"],
                target=alternative["node"],
                operation_id=operation_id,
                slot=slot,
                alternative=alternative,
            )
            return
        edge_id = _stable_id(
            "recycling-continuation:",
            subproblem_id,
            operation_id,
            _record_id(slot["slot"]),
            _record_id(alternative["relationship"]),
            child_id,
        )
        edge = {
            "id": edge_id,
            "from_subproblem_id": subproblem_id,
            "operation_id": operation_id,
            "output_slot_id": _record_id(slot["slot"]),
            "alternative_relationship_id": _record_id(
                alternative["relationship"]
            ),
            "to_subproblem_id": child_id,
            "scope": self.subproblems[subproblem_id]["scope"],
            "output_slot": slot,
            "alternative": alternative,
            "match": {"role": "exact", "path": []},
        }
        previous = self.continuations.get(edge_id)
        if previous is not None and previous != edge:
            raise RuntimeGraphQueryError(
                "recycling continuation identity projects inconsistently: "
                + edge_id
            )
        self.continuations[edge_id] = edge

    def _project_output_slot(
        self,
        *,
        subproblem_id: str,
        operation_id: str,
        scope: ProfileScope,
        depth: int,
        row: dict[str, Any],
        procedural: bool,
    ) -> dict[str, Any]:
        alternatives = sorted(row["alternatives"], key=_alternative_order)
        retained = alternatives[
            : self.options.max_output_alternatives_per_slot
        ]
        omitted = alternatives[
            self.options.max_output_alternatives_per_slot :
        ]
        mode, exhaustive = _alternative_page_semantics(alternatives)
        predicate = str(row["relationship"].get("predicate", ""))
        projected_items = [
            {
                **alternative,
                "alternative_semantics": alternative_semantics(
                    alternative["relationship"]
                ),
            }
            for alternative in retained
        ]
        projected: dict[str, Any] = {
            "relationship": row["relationship"],
            "slot": row["slot"],
            "semantics": {
                "conditional": predicate == "may_produce",
                "guaranteed": predicate == "produces",
            },
            "alternatives": {
                "mode": mode,
                "exhaustive": exhaustive,
                "items": projected_items,
                "total": len(alternatives),
                "returned": len(projected_items),
                "truncated": bool(omitted),
            },
        }
        chance = _chance_projection(row["relationship"], row["slot"])
        if chance:
            projected["chance"] = chance
        if omitted:
            self.truncation_reasons.add(
                "max-output-alternatives-per-slot"
            )
            first = {
                **omitted[0],
                "alternative_semantics": alternative_semantics(
                    omitted[0]["relationship"]
                ),
            }
            self._add_closure(
                {
                    "classification": "open-truncated",
                    "subproblem_id": subproblem_id,
                    "operation_id": operation_id,
                    "output_slot_id": _record_id(row["slot"]),
                    "alternative_relationship_id": _record_id(
                        first["relationship"]
                    ),
                    "scope": self.subproblems[subproblem_id]["scope"],
                    "target": first["node"],
                    "output_slot": projected,
                    "alternative": first,
                    "reason": "max-output-alternatives-per-slot",
                    "details": {"omitted_alternatives": len(omitted)},
                }
            )
            self._add_unresolved(
                "max-output-alternatives-per-slot",
                subproblem_id=subproblem_id,
                scope=self.subproblems[subproblem_id]["scope"],
                target=first["node"],
                operation_id=operation_id,
                slot=projected,
                alternative=first,
                details={"omitted_alternatives": len(omitted)},
            )

        for alternative in projected_items:
            classification = alternative["alternative_semantics"]
            if procedural or classification != "exact":
                self._add_closure(
                    {
                        "classification": "open-procedural-boundary",
                        "subproblem_id": subproblem_id,
                        "operation_id": operation_id,
                        "output_slot_id": _record_id(row["slot"]),
                        "alternative_relationship_id": _record_id(
                            alternative["relationship"]
                        ),
                        "scope": self.subproblems[subproblem_id]["scope"],
                        "target": alternative["node"],
                        "output_slot": projected,
                        "alternative": alternative,
                        "reason": (
                            "procedural-consumer"
                            if procedural
                            else "non-exact-output-alternative"
                        ),
                    }
                )
                continue

            destinations = self._destination_rows(scope, alternative["node"])
            if destinations:
                for destination in destinations:
                    self._add_closed_output(
                        classification=destination["classification"],
                        subproblem_id=subproblem_id,
                        operation_id=operation_id,
                        slot=projected,
                        alternative=alternative,
                        destination=destination,
                    )
                continue

            ancestors = self._ancestor_rows(
                subproblem_id,
                scope,
                alternative["node"],
            )
            if ancestors:
                for ancestor_id, match in ancestors:
                    self._add_cycle(
                        subproblem_id=subproblem_id,
                        operation_id=operation_id,
                        slot=projected,
                        alternative=alternative,
                        ancestor_id=ancestor_id,
                        match=match,
                    )
                continue

            self._add_continuation(
                subproblem_id=subproblem_id,
                operation_id=operation_id,
                slot=projected,
                alternative=alternative,
                scope=scope,
                depth=depth,
            )
        return projected

    def _project_operation(
        self,
        *,
        subproblem_id: str,
        occurrence: dict[str, Any],
        candidate_id: str,
        candidate_rank: int,
        depth: int,
    ) -> dict[str, Any]:
        operation_id = self._operation_id(subproblem_id, occurrence)
        if operation_id in self.operations:
            raise RuntimeGraphQueryError(
                "recycling operation occurrence is duplicated: "
                + operation_id
            )
        owner = occurrence["owner"]
        owner_id = _record_id(owner)
        input_rows = sorted(
            self.reader.recipe_slots(owner_id, INPUT_PREDICATES),
            key=_slot_order,
        )
        matched_relationship_id = _record_id(
            occurrence["owner_relationship"]
        )
        other_consumed = [
            _input_slot_projection(row)
            for row in input_rows
            if row["relationship"].get("predicate")
            in CONSUMING_PREDICATES
            and _record_id(row["relationship"]) != matched_relationship_id
        ]
        reusable = [
            _input_slot_projection(row)
            for row in input_rows
            if row["relationship"].get("predicate") == "requires"
        ]
        procedural = owner.get("kind") in PROCEDURAL_OWNER_KINDS
        boundaries = self._procedural_boundaries(owner)
        if procedural:
            boundaries.insert(
                0,
                {
                    "rule": owner,
                    "status": "procedural-or-symbolic-boundary",
                },
            )
        output_rows = sorted(
            self.reader.recipe_slots(owner_id, PRODUCER_PREDICATES),
            key=_slot_order,
        )
        outputs = [
            self._project_output_slot(
                subproblem_id=subproblem_id,
                operation_id=operation_id,
                scope=self.targets[subproblem_id].scope,
                depth=depth,
                row=row,
                procedural=procedural,
            )
            for row in output_rows
        ]
        if not outputs:
            self._add_closure(
                {
                    "classification": (
                        "open-procedural-boundary"
                        if procedural
                        else "open-terminal"
                    ),
                    "subproblem_id": subproblem_id,
                    "operation_id": operation_id,
                    "scope": self.subproblems[subproblem_id]["scope"],
                    "target": self.subproblems[subproblem_id]["target"],
                    "reason": (
                        "procedural-consumer-has-no-observed-output"
                        if procedural
                        else "consumer-has-no-observed-output"
                    ),
                }
            )
        return {
            "id": operation_id,
            "subproblem_id": subproblem_id,
            "candidate_id": candidate_id,
            "candidate_rank": candidate_rank,
            "admission_index": self.operation_admission_count,
            "scope": self.subproblems[subproblem_id]["scope"],
            "consumed_target": self.subproblems[subproblem_id]["target"],
            "owner": owner,
            "consumption": self._consumption_projection(occurrence),
            "mechanics": occurrence["mechanics"],
            "other_consumed_inputs": {
                "mode": "AND",
                "slots": other_consumed,
            },
            "reusable_requirements": {
                "mode": "AND",
                "slots": reusable,
            },
            "outputs": outputs,
            "boundaries": boundaries,
        }

    def _truncate_candidate(
        self,
        subproblem_id: str,
        candidate_id: str,
        candidate_rank: int,
        reason: str,
    ) -> None:
        subproblem = self.subproblems[subproblem_id]
        unresolved_id = self._add_unresolved(
            reason,
            subproblem_id=subproblem_id,
            scope=subproblem["scope"],
            target=subproblem["target"],
            candidate_id=candidate_id,
        )
        subproblem["candidate_dispositions"].append(
            {
                "candidate_id": candidate_id,
                "rank": candidate_rank,
                "status": "truncated",
                "reason": reason,
                "unresolved_id": unresolved_id,
            }
        )

    def _expand_depth(self, depth: int, identifiers: list[str]) -> None:
        pages = {
            identifier: self._prepare_subproblem(identifier)
            for identifier in identifiers
        }
        if depth >= self.options.max_depth:
            for identifier in identifiers:
                if pages[identifier]:
                    self.truncation_reasons.add("max-depth")
                    for rank, occurrence in enumerate(pages[identifier]):
                        self._truncate_candidate(
                            identifier,
                            self._candidate_id(identifier, occurrence),
                            rank,
                            "max-depth",
                        )
                    self.subproblems[identifier]["status"] = "truncated"
                    self._add_open_subproblem_closure(
                        identifier,
                        "open-truncated",
                        "max-depth",
                    )
                elif (
                    self.subproblems[identifier]["consumer_page"]["total"]
                    == 0
                ):
                    self.subproblems[identifier]["status"] = "terminal"
                    self._add_open_subproblem_closure(
                        identifier,
                        "open-terminal",
                        "no-consuming-use-observed-in-complete-page",
                    )
            return

        max_rank = max((len(page) for page in pages.values()), default=0)
        for rank in range(max_rank):
            for identifier in identifiers:
                page = pages[identifier]
                if rank >= len(page):
                    continue
                occurrence = page[rank]
                candidate_id = self._candidate_id(identifier, occurrence)
                subproblem = self.subproblems[identifier]
                if not _mechanically_eligible(occurrence):
                    unresolved_id = self._add_unresolved(
                        _ineligible_reason(occurrence),
                        subproblem_id=identifier,
                        scope=subproblem["scope"],
                        target=subproblem["target"],
                        candidate_id=candidate_id,
                        occurrence=occurrence,
                    )
                    subproblem["candidate_dispositions"].append(
                        {
                            "candidate_id": candidate_id,
                            "rank": rank,
                            "status": "ineligible",
                            "unresolved_id": unresolved_id,
                        }
                    )
                    continue
                if (
                    self.operation_admission_count
                    >= self.options.max_operations
                ):
                    self.truncation_reasons.add("max-operations")
                    self._truncate_candidate(
                        identifier,
                        candidate_id,
                        rank,
                        "max-operations",
                    )
                    continue
                operation = self._project_operation(
                    subproblem_id=identifier,
                    occurrence=occurrence,
                    candidate_id=candidate_id,
                    candidate_rank=rank,
                    depth=depth,
                )
                self.operations[operation["id"]] = operation
                subproblem["operation_ids"].append(operation["id"])
                subproblem["candidate_dispositions"].append(
                    {
                        "candidate_id": candidate_id,
                        "rank": rank,
                        "status": "retained",
                        "operation_id": operation["id"],
                    }
                )
                self.operation_admission_count += 1

        for identifier in identifiers:
            subproblem = self.subproblems[identifier]
            if subproblem["operation_ids"]:
                subproblem["status"] = (
                    "expanded"
                    if not any(
                        row["status"] == "truncated"
                        for row in subproblem["candidate_dispositions"]
                    )
                    and not subproblem["consumer_page"]["truncated"]
                    else "expanded-truncated"
                )
            elif subproblem["consumer_page"]["total"] == 0:
                subproblem["status"] = "terminal"
                self._add_open_subproblem_closure(
                    identifier,
                    "open-terminal",
                    "no-consuming-use-observed-in-complete-page",
                )
            elif any(
                row["status"] == "truncated"
                for row in subproblem["candidate_dispositions"]
            ):
                subproblem["status"] = "truncated"
                self._add_open_subproblem_closure(
                    identifier,
                    "open-truncated",
                    "max-operations",
                )
            else:
                subproblem["status"] = "unresolved"

    def _begin_incremental_depth(
        self,
        depth: int,
        identifiers: list[str],
    ) -> None:
        if self.active_depth is not None:
            raise RuntimeGraphQueryError(
                "recycling incremental depth is already active"
            )
        pages = {
            identifier: list(self._prepare_subproblem(identifier))
            for identifier in identifiers
        }
        self.active_depth = {
            "depth": depth,
            "identifiers": identifiers,
            "pages": pages,
            "rank": 0,
            "identifier_index": 0,
            "max_rank": max(
                (len(page) for page in pages.values()),
                default=0,
            ),
        }

    def _finish_incremental_depth(self) -> None:
        active = self.active_depth
        if active is None:
            return
        depth = int(active["depth"])
        identifiers = active["identifiers"]
        pages = active["pages"]
        for identifier in identifiers:
            subproblem = self.subproblems[identifier]
            if depth >= self.options.max_depth:
                if pages[identifier]:
                    subproblem["status"] = "truncated"
                    self._add_open_subproblem_closure(
                        identifier,
                        "open-truncated",
                        "max-depth",
                    )
                elif subproblem["consumer_page"]["total"] == 0:
                    subproblem["status"] = "terminal"
                    self._add_open_subproblem_closure(
                        identifier,
                        "open-terminal",
                        "no-consuming-use-observed-in-complete-page",
                    )
                continue
            if subproblem["operation_ids"]:
                subproblem["status"] = (
                    "expanded"
                    if not any(
                        row["status"] == "truncated"
                        for row in subproblem["candidate_dispositions"]
                    )
                    and not subproblem["consumer_page"]["truncated"]
                    else "expanded-truncated"
                )
            elif subproblem["consumer_page"]["total"] == 0:
                subproblem["status"] = "terminal"
                self._add_open_subproblem_closure(
                    identifier,
                    "open-terminal",
                    "no-consuming-use-observed-in-complete-page",
                )
            elif any(
                row["status"] == "truncated"
                for row in subproblem["candidate_dispositions"]
            ):
                subproblem["status"] = "truncated"
                self._add_open_subproblem_closure(
                    identifier,
                    "open-truncated",
                    "max-operations",
                )
            else:
                subproblem["status"] = "unresolved"
        self.active_depth = None

    def _advance_incremental_depth(self) -> bool:
        """Process one candidate in exact depth/rank/target fairness order."""

        active = self.active_depth
        if active is None:
            return False
        identifiers = active["identifiers"]
        pages = active["pages"]
        max_rank = int(active["max_rank"])
        while int(active["rank"]) < max_rank:
            rank = int(active["rank"])
            while int(active["identifier_index"]) < len(identifiers):
                index = int(active["identifier_index"])
                active["identifier_index"] = index + 1
                identifier = identifiers[index]
                page = pages[identifier]
                if rank >= len(page):
                    continue
                occurrence = page[rank]
                candidate_id = self._candidate_id(identifier, occurrence)
                subproblem = self.subproblems[identifier]
                if int(active["depth"]) >= self.options.max_depth:
                    self.truncation_reasons.add("max-depth")
                    self._truncate_candidate(
                        identifier,
                        candidate_id,
                        rank,
                        "max-depth",
                    )
                    return True
                if not _mechanically_eligible(occurrence):
                    unresolved_id = self._add_unresolved(
                        _ineligible_reason(occurrence),
                        subproblem_id=identifier,
                        scope=subproblem["scope"],
                        target=subproblem["target"],
                        candidate_id=candidate_id,
                        occurrence=occurrence,
                    )
                    subproblem["candidate_dispositions"].append(
                        {
                            "candidate_id": candidate_id,
                            "rank": rank,
                            "status": "ineligible",
                            "unresolved_id": unresolved_id,
                        }
                    )
                    return True
                if (
                    self.operation_admission_count
                    >= self.options.max_operations
                ):
                    self.truncation_reasons.add("max-operations")
                    self._truncate_candidate(
                        identifier,
                        candidate_id,
                        rank,
                        "max-operations",
                    )
                    return True
                operation = self._project_operation(
                    subproblem_id=identifier,
                    occurrence=occurrence,
                    candidate_id=candidate_id,
                    candidate_rank=rank,
                    depth=int(active["depth"]),
                )
                self.operations[operation["id"]] = operation
                subproblem["operation_ids"].append(operation["id"])
                subproblem["candidate_dispositions"].append(
                    {
                        "candidate_id": candidate_id,
                        "rank": rank,
                        "status": "retained",
                        "operation_id": operation["id"],
                    }
                )
                self.operation_admission_count += 1
                return True
            active["rank"] = rank + 1
            active["identifier_index"] = 0
        self._finish_incremental_depth()
        return False

    def _recompute_reachability(
        self,
        roots: list[dict[str, Any]],
    ) -> None:
        root_sets: dict[str, set[str]] = {
            identifier: set() for identifier in self.subproblems
        }
        depth: dict[str, int] = {
            identifier: 2**63 - 1 for identifier in self.subproblems
        }
        for root in roots:
            identifier = root["subproblem_id"]
            root_sets[identifier].add(root["id"])
            depth[identifier] = 0
        ordered_edges = sorted(
            self.continuations.values(),
            key=lambda row: row["id"],
        )
        changed = True
        while changed:
            changed = False
            for edge in ordered_edges:
                source = edge["from_subproblem_id"]
                target = edge["to_subproblem_id"]
                merged = root_sets[target] | root_sets[source]
                if merged != root_sets[target]:
                    root_sets[target] = merged
                    changed = True
                candidate_depth = depth[source] + 1
                if candidate_depth < depth[target]:
                    depth[target] = candidate_depth
                    changed = True
        for identifier, subproblem in self.subproblems.items():
            subproblem["root_ids"] = sorted(root_sets[identifier])
            if depth[identifier] != 2**63 - 1:
                subproblem["depth"] = depth[identifier]

    def _assert_continuation_dag(self) -> None:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in self.continuations.values():
            adjacency[edge["from_subproblem_id"]].append(
                edge["to_subproblem_id"]
            )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise RuntimeGraphQueryError(
                    "recycling continuation graph contains an inline cycle"
                )
            if identifier in visited:
                return
            visiting.add(identifier)
            for child in sorted(adjacency.get(identifier, ())):
                visit(child)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in sorted(self.subproblems):
            visit(identifier)

    def run(
        self,
        root_targets: tuple[ScopedTarget, ...],
        root_documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        roots_by_key = {
            _target_key(target): target for target in root_targets
        }
        for root in root_documents:
            key = (
                root["scope"]["profile"],
                root["scope"]["physical_side"],
                root["target"]["kind"],
                root["target"]["id"],
            )
            target = roots_by_key[key]
            identifier = self._new_subproblem(target, 0)
            if identifier != root["subproblem_id"]:
                raise RuntimeGraphQueryError(
                    "recycling root/subproblem identity differs"
                )

        depth = 0
        expanded: set[str] = set()
        while True:
            identifiers = sorted(
                (
                    identifier
                    for identifier in self.frontiers.get(depth, set())
                    if identifier not in expanded
                ),
                key=lambda identifier: (
                    _target_key(self.targets[identifier]),
                    identifier,
                ),
            )
            if not identifiers:
                pending_depths = sorted(
                    candidate_depth
                    for candidate_depth, values in self.frontiers.items()
                    if candidate_depth > depth
                    and any(value not in expanded for value in values)
                )
                if not pending_depths:
                    break
                depth = pending_depths[0]
                continue
            expanded.update(identifiers)
            self._expand_depth(depth, identifiers)
            depth += 1

        self._assert_continuation_dag()
        self._recompute_reachability(root_documents)
        reasons = [
            reason
            for reason in RECYCLING_TRUNCATION_REASONS
            if reason in self.truncation_reasons
        ]
        result = {
            "roots": root_documents,
            "subproblems": sorted(
                self.subproblems.values(),
                key=lambda row: (
                    int(row["depth"]),
                    str(row["scope"]["profile"]),
                    str(row["scope"]["physical_side"]),
                    _record_kind(row["target"]),
                    _record_id(row["target"]),
                    str(row["id"]),
                ),
            ),
            "operations": sorted(
                self.operations.values(),
                key=lambda row: (
                    int(row["admission_index"]),
                    str(row["id"]),
                ),
            ),
            "continuation_edges": sorted(
                self.continuations.values(),
                key=lambda row: (
                    row["from_subproblem_id"],
                    row["to_subproblem_id"],
                    row["operation_id"],
                    row["output_slot_id"],
                    row["alternative_relationship_id"],
                    row["id"],
                ),
            ),
            "closures": sorted(
                self.closures.values(),
                key=lambda row: (
                    row["classification"],
                    str(row.get("subproblem_id", "")),
                    str(row.get("operation_id", "")),
                    str(row.get("output_slot_id", "")),
                    str(row.get("alternative_relationship_id", "")),
                    str(row["id"]),
                ),
            ),
            "cycles": sorted(
                self.cycles.values(),
                key=lambda row: (
                    row["from_subproblem_id"],
                    row["to_ancestor_subproblem_id"],
                    row["id"],
                ),
            ),
            "terminal_leaves": sorted(
                self.terminal_leaves.values(),
                key=lambda row: (
                    row["classification"],
                    str(row.get("subproblem_id", "")),
                    row["id"],
                ),
            ),
            "unresolved": sorted(
                self.unresolved.values(),
                key=lambda row: (
                    row["reason"],
                    str(row.get("subproblem_id", "")),
                    str(row.get("candidate_id", "")),
                    row["id"],
                ),
            ),
            "summary": {
                "root_count": len(root_documents),
                "consuming_candidate_count": (
                    self.consuming_candidate_count
                ),
                "reusable_only_candidate_count": (
                    self.reusable_candidate_count
                ),
                "retained_operation_count": len(self.operations),
                "visited_target_count": len(self.subproblems),
                "closure_count": len(self.closures),
                "terminal_count": len(self.terminal_leaves),
            },
            "truncation": {
                "truncated": bool(reasons),
                "reasons": reasons,
                "limits": self.options.limits(),
            },
        }
        validate_recycling_result(result, root_documents)
        return result


class RuntimeGraphRecyclingContinuation:
    """Serializable fair candidate continuation for recycling traversal."""

    STATE_FORMAT = "susy-recycling-traversal-state-v1"
    ALGORITHM_ID = "ATLAS-RECYCLING-CONTINUATION"
    ALGORITHM_VERSION = 1
    FAIRNESS_POLICY_ID = "ATLAS-RECYCLING-DEPTH-RANK-V1"

    def __init__(
        self,
        reader: RuntimeGraphReader,
        roots: Iterable[RecyclingRootSeed],
        *,
        final_targets: Iterable[ScopedTarget] = (),
        route_targets: Iterable[RecyclingRouteTarget] = (),
        options: RecyclingOptions = RecyclingOptions(),
    ):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "recycling continuation requires a RuntimeGraphReader"
            )
        if not isinstance(options, RecyclingOptions):
            raise RuntimeGraphQueryError(
                "recycling continuation requires RecyclingOptions"
            )
        query = RuntimeGraphRecyclingQuery(reader)
        root_seeds = tuple(roots)
        root_documents = canonical_recycling_roots(root_seeds)
        root_targets = query._validate_exact_roots(
            root_seeds,
            root_documents,
        )
        normalized_final = query._normalize_targets(
            final_targets,
            "final",
        )
        normalized_route = query._normalize_route_targets(route_targets)
        if options.max_visited_targets < len(root_documents):
            raise RuntimeGraphQueryError(
                "recycling max_visited_targets must cover every exact root"
            )
        self.reader = reader
        self.options = options
        self.root_targets = root_targets
        self.root_documents = root_documents
        self.final_targets = normalized_final
        self.route_targets = normalized_route
        self.state = _RecyclingTraversalState(
            reader,
            query.domain,
            options,
            normalized_final,
            normalized_route,
        )
        roots_by_key = {
            _target_key(target): target for target in root_targets
        }
        for root in root_documents:
            key = (
                root["scope"]["profile"],
                root["scope"]["physical_side"],
                root["target"]["kind"],
                root["target"]["id"],
            )
            identifier = self.state._new_subproblem(
                roots_by_key[key],
                0,
            )
            if identifier != root["subproblem_id"]:
                raise RuntimeGraphQueryError(
                    "recycling continuation root identity differs"
                )
        self.current_depth = 0
        self.expanded: set[str] = set()
        self.cumulative_work_items = 0
        self.last_page_ranges: list[dict[str, Any]] = []

    @property
    def complete(self) -> bool:
        if self.state.active_depth is not None:
            return False
        return not any(
            identifier not in self.expanded
            for identifiers in self.state.frontiers.values()
            for identifier in identifiers
        )

    def _start_next_depth(self) -> bool:
        while True:
            identifiers = sorted(
                (
                    identifier
                    for identifier in self.state.frontiers.get(
                        self.current_depth,
                        set(),
                    )
                    if identifier not in self.expanded
                ),
                key=lambda identifier: (
                    _target_key(self.state.targets[identifier]),
                    identifier,
                ),
            )
            if identifiers:
                self.expanded.update(identifiers)
                self.state._begin_incremental_depth(
                    self.current_depth,
                    identifiers,
                )
                return True
            pending_depths = sorted(
                depth
                for depth, values in self.state.frontiers.items()
                if depth > self.current_depth
                and any(value not in self.expanded for value in values)
            )
            if not pending_depths:
                return False
            self.current_depth = pending_depths[0]

    def step(self, max_work_items: int) -> dict[str, Any]:
        if type(max_work_items) is not int or max_work_items < 1:
            raise RuntimeGraphQueryError(
                "recycling continuation work allocation must be positive"
            )
        consumed = 0
        ranges: list[dict[str, Any]] = []
        while consumed < max_work_items:
            if self.state.active_depth is None:
                if not self._start_next_depth():
                    break
            active = self.state.active_depth
            if active is None:
                continue
            rank = int(active["rank"])
            identifier_index = int(active["identifier_index"])
            identifiers = active["identifiers"]
            pages = active["pages"]
            expected_subject: str | None = None
            expected_rank = rank
            expected_index = identifier_index
            while expected_rank < int(active["max_rank"]):
                for candidate_index in range(
                    expected_index,
                    len(identifiers),
                ):
                    candidate = identifiers[candidate_index]
                    if expected_rank < len(pages[candidate]):
                        expected_subject = candidate
                        break
                if expected_subject is not None:
                    break
                expected_rank += 1
                expected_index = 0
            spent = self.state._advance_incremental_depth()
            if not spent:
                if self.state.active_depth is None:
                    self.current_depth += 1
                continue
            consumed += 1
            if expected_subject is None:
                raise RuntimeGraphQueryError(
                    "recycling fairness cursor lost its candidate"
                )
            ranges.append(
                {
                    "stream": "consumer-candidates",
                    "subject_id": expected_subject,
                    "start": expected_rank,
                    "end": expected_rank + 1,
                }
            )
        self.cumulative_work_items += consumed
        self.last_page_ranges = ranges
        result = self.result()
        state = self.export_state()
        return {
            "allocated_work_items": max_work_items,
            "consumed_work_items": consumed,
            "cumulative_work_items": self.cumulative_work_items,
            "complete": self.complete,
            "page_ranges": copy.deepcopy(ranges),
            "frontier": self.frontier(),
            "state": state,
            "result": result,
        }

    def frontier(self) -> dict[str, Any]:
        active = self.state.active_depth
        active_row: dict[str, Any] | None = None
        if active is not None:
            active_row = {
                "depth": active["depth"],
                "rank": active["rank"],
                "identifier_index": active["identifier_index"],
                "identifiers": active["identifiers"],
                "page_sizes": {
                    identifier: len(active["pages"][identifier])
                    for identifier in active["identifiers"]
                },
            }
        pending = {
            str(depth): sorted(
                identifier
                for identifier in identifiers
                if identifier not in self.expanded
            )
            for depth, identifiers in sorted(self.state.frontiers.items())
            if any(
                identifier not in self.expanded
                for identifier in identifiers
            )
        }
        return {
            "phase": "complete" if self.complete else "depth-rank",
            "current_depth": self.current_depth,
            "active": active_row,
            "pending_by_depth": pending,
            "operation_admission_count": (
                self.state.operation_admission_count
            ),
            "visited_target_count": len(self.state.subproblems),
        }

    def result(self) -> dict[str, Any]:
        self.state._assert_continuation_dag()
        self.state._recompute_reachability(self.root_documents)
        reasons = [
            reason
            for reason in RECYCLING_TRUNCATION_REASONS
            if reason in self.state.truncation_reasons
        ]
        result = {
            "roots": self.root_documents,
            "subproblems": sorted(
                self.state.subproblems.values(),
                key=lambda row: (
                    int(row["depth"]),
                    str(row["scope"]["profile"]),
                    str(row["scope"]["physical_side"]),
                    _record_kind(row["target"]),
                    _record_id(row["target"]),
                    str(row["id"]),
                ),
            ),
            "operations": sorted(
                self.state.operations.values(),
                key=lambda row: (
                    int(row["admission_index"]),
                    str(row["id"]),
                ),
            ),
            "continuation_edges": sorted(
                self.state.continuations.values(),
                key=lambda row: (
                    row["from_subproblem_id"],
                    row["to_subproblem_id"],
                    row["operation_id"],
                    row["output_slot_id"],
                    row["alternative_relationship_id"],
                    row["id"],
                ),
            ),
            "closures": sorted(
                self.state.closures.values(),
                key=lambda row: (
                    row["classification"],
                    str(row.get("subproblem_id", "")),
                    str(row.get("operation_id", "")),
                    str(row.get("output_slot_id", "")),
                    str(row.get("alternative_relationship_id", "")),
                    str(row["id"]),
                ),
            ),
            "cycles": sorted(
                self.state.cycles.values(),
                key=lambda row: (
                    row["from_subproblem_id"],
                    row["to_ancestor_subproblem_id"],
                    row["id"],
                ),
            ),
            "terminal_leaves": sorted(
                self.state.terminal_leaves.values(),
                key=lambda row: (
                    row["classification"],
                    str(row.get("subproblem_id", "")),
                    row["id"],
                ),
            ),
            "unresolved": sorted(
                self.state.unresolved.values(),
                key=lambda row: (
                    row["reason"],
                    str(row.get("subproblem_id", "")),
                    str(row.get("candidate_id", "")),
                    row["id"],
                ),
            ),
            "summary": {
                "root_count": len(self.root_documents),
                "consuming_candidate_count": (
                    self.state.consuming_candidate_count
                ),
                "reusable_only_candidate_count": (
                    self.state.reusable_candidate_count
                ),
                "retained_operation_count": len(self.state.operations),
                "visited_target_count": len(self.state.subproblems),
                "closure_count": len(self.state.closures),
                "terminal_count": len(self.state.terminal_leaves),
            },
            "truncation": {
                "truncated": bool(reasons),
                "reasons": reasons,
                "limits": self.options.limits(),
            },
        }
        if self.complete:
            validate_recycling_result(result, self.root_documents)
        return _json_tree(result)

    @staticmethod
    def _target_document(target: ScopedTarget) -> dict[str, Any]:
        return {
            "scope": _scope_dict(target.scope),
            "node": target.node,
        }

    def export_state(self) -> dict[str, Any]:
        return _json_tree(
            {
                "format": self.STATE_FORMAT,
                "algorithm_version": self.ALGORITHM_VERSION,
                "options": self.options.limits(),
                "root_documents": self.root_documents,
                "root_target_ids": [
                    _subproblem_id(target) for target in self.root_targets
                ],
                "final_targets": [
                    self._target_document(target)
                    for target in self.final_targets
                ],
                "route_targets": [
                    {
                        "subproblem_id": target.subproblem_id,
                        **self._target_document(target.target),
                    }
                    for target in self.route_targets
                ],
                "targets": {
                    identifier: self._target_document(target)
                    for identifier, target in sorted(
                        self.state.targets.items()
                    )
                },
                "subproblems": self.state.subproblems,
                "frontiers": {
                    str(depth): sorted(identifiers)
                    for depth, identifiers in sorted(
                        self.state.frontiers.items()
                    )
                },
                "operations": self.state.operations,
                "continuations": self.state.continuations,
                "closures": self.state.closures,
                "cycles": self.state.cycles,
                "terminal_leaves": self.state.terminal_leaves,
                "unresolved": self.state.unresolved,
                "truncation_reasons": sorted(
                    self.state.truncation_reasons
                ),
                "operation_admission_count": (
                    self.state.operation_admission_count
                ),
                "consuming_candidate_count": (
                    self.state.consuming_candidate_count
                ),
                "reusable_candidate_count": (
                    self.state.reusable_candidate_count
                ),
                "active_depth": self.state.active_depth,
                "current_depth": self.current_depth,
                "expanded": sorted(self.expanded),
                "cumulative_work_items": self.cumulative_work_items,
            }
        )

    @classmethod
    def from_state(
        cls,
        reader: RuntimeGraphReader,
        document: dict[str, Any],
    ) -> "RuntimeGraphRecyclingContinuation":
        """Decode exact recycling frontier state without replaying candidates."""

        if not isinstance(document, dict) or document.get("format") != cls.STATE_FORMAT:
            raise RuntimeGraphQueryError(
                "recycling continuation state format is unsupported"
            )
        if document.get("algorithm_version") != cls.ALGORITHM_VERSION:
            raise RuntimeGraphQueryError(
                "recycling continuation algorithm version differs"
            )
        options_row = document.get("options")
        if not isinstance(options_row, dict):
            raise RuntimeGraphQueryError(
                "recycling continuation options are missing"
            )
        options = RecyclingOptions(**options_row)
        query = RuntimeGraphRecyclingQuery(reader)

        def decode_target(
            row: object,
            label: str,
        ) -> ScopedTarget:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("scope"), dict)
                or not isinstance(row.get("node"), dict)
            ):
                raise RuntimeGraphQueryError(
                    f"recycling continuation {label} is invalid"
                )
            target = ScopedTarget(
                ProfileScope(
                    _required_text(
                        row["scope"].get("profile"),
                        f"recycling continuation {label} profile",
                    ),
                    _required_text(
                        row["scope"].get("physical_side"),
                        f"recycling continuation {label} physical side",
                    ),
                ),
                row["node"],
            )
            return query._validate_target(
                target,
                f"recycling continuation {label}",
            )

        final_targets = tuple(
            decode_target(row, "final target")
            for row in document.get("final_targets", [])
        )
        route_targets = tuple(
            RecyclingRouteTarget(
                _required_text(
                    row.get("subproblem_id")
                    if isinstance(row, dict)
                    else None,
                    "recycling continuation route subproblem id",
                ),
                decode_target(row, "route target"),
            )
            for row in document.get("route_targets", [])
        )
        targets_row = document.get("targets")
        if not isinstance(targets_row, dict) or not targets_row:
            raise RuntimeGraphQueryError(
                "recycling continuation targets are missing"
            )
        targets: dict[str, ScopedTarget] = {}
        for identifier, row in targets_row.items():
            target = decode_target(row, "target")
            if identifier != _subproblem_id(target):
                raise RuntimeGraphQueryError(
                    "recycling continuation target identity differs"
                )
            targets[identifier] = target
        root_ids = document.get("root_target_ids")
        if (
            not isinstance(root_ids, list)
            or not root_ids
            or any(identifier not in targets for identifier in root_ids)
        ):
            raise RuntimeGraphQueryError(
                "recycling continuation root identities are invalid"
            )
        root_documents = document.get("root_documents")
        if (
            not isinstance(root_documents, list)
            or [row.get("subproblem_id") for row in root_documents]
            != root_ids
        ):
            raise RuntimeGraphQueryError(
                "recycling continuation root documents differ"
            )
        instance = cls.__new__(cls)
        instance.reader = reader
        instance.options = options
        instance.root_targets = tuple(
            targets[identifier] for identifier in root_ids
        )
        instance.root_documents = copy.deepcopy(root_documents)
        instance.final_targets = final_targets
        instance.route_targets = route_targets
        state = _RecyclingTraversalState(
            reader,
            query.domain,
            options,
            final_targets,
            route_targets,
        )
        state.targets = targets
        state.target_ids = {
            _target_key(target): identifier
            for identifier, target in targets.items()
        }
        state.subproblems = copy.deepcopy(
            document.get("subproblems", {})
        )
        if set(state.subproblems) != set(targets):
            raise RuntimeGraphQueryError(
                "recycling continuation subproblems differ from targets"
            )
        frontiers = document.get("frontiers")
        if not isinstance(frontiers, dict):
            raise RuntimeGraphQueryError(
                "recycling continuation frontiers are missing"
            )
        state.frontiers = defaultdict(
            set,
            {
                int(depth): {str(identifier) for identifier in identifiers}
                for depth, identifiers in frontiers.items()
            },
        )
        if any(
            identifier not in targets
            for identifiers in state.frontiers.values()
            for identifier in identifiers
        ):
            raise RuntimeGraphQueryError(
                "recycling continuation frontier references unknown targets"
            )
        for name in (
            "operations",
            "continuations",
            "closures",
            "cycles",
            "terminal_leaves",
            "unresolved",
        ):
            value = document.get(name)
            if not isinstance(value, dict):
                raise RuntimeGraphQueryError(
                    f"recycling continuation {name} state is invalid"
                )
            setattr(state, name, copy.deepcopy(value))
        state.truncation_reasons = {
            str(value)
            for value in document.get("truncation_reasons", [])
        }
        for name in (
            "operation_admission_count",
            "consuming_candidate_count",
            "reusable_candidate_count",
        ):
            value = document.get(name)
            if type(value) is not int or value < 0:
                raise RuntimeGraphQueryError(
                    f"recycling continuation {name} is invalid"
                )
            setattr(state, name, value)
        state.active_depth = copy.deepcopy(document.get("active_depth"))
        if state.active_depth is not None:
            identifiers = state.active_depth.get("identifiers")
            pages = state.active_depth.get("pages")
            rank = state.active_depth.get("rank")
            identifier_index = state.active_depth.get("identifier_index")
            if (
                not isinstance(identifiers, list)
                or any(identifier not in targets for identifier in identifiers)
                or not isinstance(pages, dict)
                or set(pages) != set(identifiers)
                or type(rank) is not int
                or rank < 0
                or type(identifier_index) is not int
                or identifier_index < 0
                or identifier_index > len(identifiers)
            ):
                raise RuntimeGraphQueryError(
                    "recycling continuation fairness cursor is invalid"
                )
        state.subproblem_matches = defaultdict(list)
        for identifier in sorted(targets):
            state._register_subproblem_matches(
                identifier,
                targets[identifier],
            )
        instance.state = state
        instance.current_depth = document.get("current_depth")
        if type(instance.current_depth) is not int or instance.current_depth < 0:
            raise RuntimeGraphQueryError(
                "recycling continuation current depth is invalid"
            )
        instance.expanded = {
            str(value) for value in document.get("expanded", [])
        }
        if not instance.expanded.issubset(targets):
            raise RuntimeGraphQueryError(
                "recycling continuation expanded set is invalid"
            )
        cumulative = document.get("cumulative_work_items")
        if type(cumulative) is not int or cumulative < 0:
            raise RuntimeGraphQueryError(
                "recycling continuation cumulative work is invalid"
            )
        instance.cumulative_work_items = cumulative
        instance.last_page_ranges = []
        return instance


def validate_recycling_continuation_projection(
    state: object,
    result: object,
    frontier: object,
    *,
    cumulative_work_items: int,
    status: str,
) -> None:
    """Recompute a recycling result and frontier from portable state alone."""

    if not isinstance(state, dict) or state.get(
        "format"
    ) != RuntimeGraphRecyclingContinuation.STATE_FORMAT:
        raise RuntimeGraphQueryError(
            "recycling continuation projection state is unsupported"
        )
    if (
        type(cumulative_work_items) is not int
        or cumulative_work_items < 0
        or state.get("cumulative_work_items") != cumulative_work_items
    ):
        raise RuntimeGraphQueryError(
            "recycling continuation state cumulative work differs"
        )
    try:
        options_row = state["options"]
        if not isinstance(options_row, dict):
            raise TypeError("options")
        options = RecyclingOptions(**options_row)
        root_documents = state["root_documents"]
        subproblems = state["subproblems"]
        operations = state["operations"]
        continuations = state["continuations"]
        closures = state["closures"]
        cycles = state["cycles"]
        terminal_leaves = state["terminal_leaves"]
        unresolved = state["unresolved"]
        frontiers = state["frontiers"]
        expanded = set(state["expanded"])
        active = state["active_depth"]
        if (
            not isinstance(root_documents, list)
            or not isinstance(subproblems, dict)
            or not isinstance(operations, dict)
            or not isinstance(continuations, dict)
            or not isinstance(closures, dict)
            or not isinstance(cycles, dict)
            or not isinstance(terminal_leaves, dict)
            or not isinstance(unresolved, dict)
            or not isinstance(frontiers, dict)
        ):
            raise TypeError("recycling state collection")
        reasons = [
            reason
            for reason in RECYCLING_TRUNCATION_REASONS
            if reason in set(state["truncation_reasons"])
        ]
        expected_result = _json_tree(
            {
                "roots": root_documents,
                "subproblems": sorted(
                    subproblems.values(),
                    key=lambda row: (
                        int(row["depth"]),
                        str(row["scope"]["profile"]),
                        str(row["scope"]["physical_side"]),
                        _record_kind(row["target"]),
                        _record_id(row["target"]),
                        str(row["id"]),
                    ),
                ),
                "operations": sorted(
                    operations.values(),
                    key=lambda row: (
                        int(row["admission_index"]),
                        str(row["id"]),
                    ),
                ),
                "continuation_edges": sorted(
                    continuations.values(),
                    key=lambda row: (
                        row["from_subproblem_id"],
                        row["to_subproblem_id"],
                        row["operation_id"],
                        row["output_slot_id"],
                        row["alternative_relationship_id"],
                        row["id"],
                    ),
                ),
                "closures": sorted(
                    closures.values(),
                    key=lambda row: (
                        row["classification"],
                        str(row.get("subproblem_id", "")),
                        str(row.get("operation_id", "")),
                        str(row.get("output_slot_id", "")),
                        str(
                            row.get(
                                "alternative_relationship_id",
                                "",
                            )
                        ),
                        str(row["id"]),
                    ),
                ),
                "cycles": sorted(
                    cycles.values(),
                    key=lambda row: (
                        row["from_subproblem_id"],
                        row["to_ancestor_subproblem_id"],
                        row["id"],
                    ),
                ),
                "terminal_leaves": sorted(
                    terminal_leaves.values(),
                    key=lambda row: (
                        row["classification"],
                        str(row.get("subproblem_id", "")),
                        row["id"],
                    ),
                ),
                "unresolved": sorted(
                    unresolved.values(),
                    key=lambda row: (
                        row["reason"],
                        str(row.get("subproblem_id", "")),
                        str(row.get("candidate_id", "")),
                        row["id"],
                    ),
                ),
                "summary": {
                    "root_count": len(root_documents),
                    "consuming_candidate_count": state[
                        "consuming_candidate_count"
                    ],
                    "reusable_only_candidate_count": state[
                        "reusable_candidate_count"
                    ],
                    "retained_operation_count": len(operations),
                    "visited_target_count": len(subproblems),
                    "closure_count": len(closures),
                    "terminal_count": len(terminal_leaves),
                },
                "truncation": {
                    "truncated": bool(reasons),
                    "reasons": reasons,
                    "limits": options.limits(),
                },
            }
        )
        active_row = None
        if active is not None:
            if not isinstance(active, dict):
                raise TypeError("active depth")
            active_row = {
                "depth": active["depth"],
                "rank": active["rank"],
                "identifier_index": active["identifier_index"],
                "identifiers": active["identifiers"],
                "page_sizes": {
                    identifier: len(active["pages"][identifier])
                    for identifier in active["identifiers"]
                },
            }
        pending = {
            str(depth): sorted(
                identifier
                for identifier in identifiers
                if identifier not in expanded
            )
            for depth, identifiers in sorted(
                (
                    (int(depth), identifiers)
                    for depth, identifiers in frontiers.items()
                ),
                key=lambda row: row[0],
            )
            if any(
                identifier not in expanded
                for identifier in identifiers
            )
        }
        complete = active is None and not pending
        expected_frontier = {
            "phase": "complete" if complete else "depth-rank",
            "current_depth": state["current_depth"],
            "active": active_row,
            "pending_by_depth": pending,
            "operation_admission_count": state[
                "operation_admission_count"
            ],
            "visited_target_count": len(subproblems),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeGraphQueryError(
            "recycling continuation state cannot project its result/frontier"
        ) from exc
    if result != expected_result:
        raise RuntimeGraphQueryError(
            "recycling continuation result differs from its decoded state"
        )
    if frontier != expected_frontier:
        raise RuntimeGraphQueryError(
            "recycling continuation frontier differs from its decoded state"
        )
    if status == "complete" and not complete:
        raise RuntimeGraphQueryError(
            "recycling continuation complete status has resumable state"
        )
    if complete:
        validate_recycling_result(expected_result, root_documents)


def validate_recycling_result(
    result: dict[str, Any],
    expected_roots: list[dict[str, Any]],
) -> None:
    """Recompute retained-DAG invariants without consulting the query source."""

    if not isinstance(result, dict):
        raise RuntimeGraphQueryError("recycling result must be an object")
    required = {
        "roots",
        "subproblems",
        "operations",
        "continuation_edges",
        "closures",
        "cycles",
        "terminal_leaves",
        "unresolved",
        "summary",
        "truncation",
    }
    if set(result) != required:
        raise RuntimeGraphQueryError(
            "recycling result fields differ from its structural contract"
        )
    if result["roots"] != expected_roots:
        raise RuntimeGraphQueryError(
            "recycling roots differ from exact deduplicated co-output roots"
        )
    sequence_names = (
        "subproblems",
        "operations",
        "continuation_edges",
        "closures",
        "cycles",
        "terminal_leaves",
        "unresolved",
    )
    for name in sequence_names:
        if not isinstance(result[name], list) or any(
            not isinstance(row, dict) for row in result[name]
        ):
            raise RuntimeGraphQueryError(
                f"recycling {name} must be an array of objects"
            )
        identifiers = [row.get("id") for row in result[name]]
        if any(not isinstance(value, str) or not value for value in identifiers):
            raise RuntimeGraphQueryError(
                f"recycling {name} contains a row without an id"
            )
        if len(set(identifiers)) != len(identifiers):
            raise RuntimeGraphQueryError(
                f"recycling {name} contains duplicate ids"
            )
    canonical_orders = {
        "subproblems": lambda row: (
            int(row["depth"]),
            str(row["scope"]["profile"]),
            str(row["scope"]["physical_side"]),
            _record_kind(row["target"]),
            _record_id(row["target"]),
            str(row["id"]),
        ),
        "operations": lambda row: (
            int(row["admission_index"]),
            str(row["id"]),
        ),
        "continuation_edges": lambda row: (
            row["from_subproblem_id"],
            row["to_subproblem_id"],
            row["operation_id"],
            row["output_slot_id"],
            row["alternative_relationship_id"],
            row["id"],
        ),
        "closures": lambda row: (
            row["classification"],
            str(row.get("subproblem_id", "")),
            str(row.get("operation_id", "")),
            str(row.get("output_slot_id", "")),
            str(row.get("alternative_relationship_id", "")),
            str(row["id"]),
        ),
        "cycles": lambda row: (
            row["from_subproblem_id"],
            row["to_ancestor_subproblem_id"],
            row["id"],
        ),
        "terminal_leaves": lambda row: (
            row["classification"],
            str(row.get("subproblem_id", "")),
            row["id"],
        ),
        "unresolved": lambda row: (
            row["reason"],
            str(row.get("subproblem_id", "")),
            str(row.get("candidate_id", "")),
            row["id"],
        ),
    }
    for name, order in canonical_orders.items():
        if result[name] != sorted(result[name], key=order):
            raise RuntimeGraphQueryError(
                f"recycling {name} is not in canonical order"
            )

    subproblems = {row["id"]: row for row in result["subproblems"]}
    operations = {row["id"]: row for row in result["operations"]}
    closures = {row["id"]: row for row in result["closures"]}
    unresolved = {row["id"]: row for row in result["unresolved"]}
    root_ids = {row["id"] for row in expected_roots}
    if any(root["subproblem_id"] not in subproblems for root in expected_roots):
        raise RuntimeGraphQueryError(
            "recycling root references an unknown subproblem"
        )

    exact_target_keys: set[tuple[str, str, str, str]] = set()
    page_candidate_ids: set[str] = set()
    consuming_total = 0
    reusable_total = 0
    accounted_unresolved_ids: set[str] = set()
    for subproblem in result["subproblems"]:
        scope = subproblem.get("scope")
        target = subproblem.get("target")
        if not isinstance(scope, dict) or not isinstance(target, dict):
            raise RuntimeGraphQueryError(
                "recycling subproblem omits scope or target"
            )
        key = (
            _required_text(scope.get("profile"), "subproblem profile"),
            _required_text(
                scope.get("physical_side"),
                "subproblem physical side",
            ),
            _record_kind(target, "subproblem target"),
            _record_id(target, "subproblem target"),
        )
        if key in exact_target_keys:
            raise RuntimeGraphQueryError(
                "recycling subproblems duplicate one exact scoped target"
            )
        exact_target_keys.add(key)
        if subproblem["id"] != _stable_id(
            "recycling-subproblem:",
            "\x1f".join(key),
        ):
            raise RuntimeGraphQueryError(
                "recycling subproblem id differs from exact target identity"
            )
        page = subproblem.get("consumer_page")
        reusable = subproblem.get("reusable_observation")
        if (
            not isinstance(page, dict)
            or page.get("predicates") != list(CONSUMING_PREDICATES)
            or page.get("offset") != 0
            or type(page.get("limit")) is not int
            or type(page.get("returned")) is not int
            or type(page.get("total")) is not int
            or type(page.get("truncated")) is not bool
            or not isinstance(page.get("candidate_ids"), list)
            or page["returned"] != len(page["candidate_ids"])
            or page["total"] < page["returned"]
            or page["truncated"] is not (
                page["returned"] != page["total"]
            )
        ):
            raise RuntimeGraphQueryError(
                "recycling consumer page is inconsistent"
            )
        if len(set(page["candidate_ids"])) != len(page["candidate_ids"]):
            raise RuntimeGraphQueryError(
                "recycling consumer page candidate ids are duplicated"
            )
        page_candidate_ids.update(page["candidate_ids"])
        consuming_total += page["total"]
        if (
            not isinstance(reusable, dict)
            or reusable.get("predicates")
            != list(REUSABLE_CONSUMER_PREDICATES)
            or type(reusable.get("total")) is not int
            or reusable["total"] < 0
            or reusable.get("policy") != "counted-not-traversed"
        ):
            raise RuntimeGraphQueryError(
                "recycling reusable observation is inconsistent"
            )
        reusable_total += reusable["total"]
        operation_ids = subproblem.get("operation_ids")
        if (
            not isinstance(operation_ids, list)
            or len(set(operation_ids)) != len(operation_ids)
            or any(identifier not in operations for identifier in operation_ids)
        ):
            raise RuntimeGraphQueryError(
                "recycling subproblem operation ids are inconsistent"
            )
        dispositions = subproblem.get("candidate_dispositions")
        if (
            not isinstance(dispositions, list)
            or [row.get("candidate_id") for row in dispositions]
            != page["candidate_ids"]
        ):
            raise RuntimeGraphQueryError(
                "recycling candidate dispositions differ from its page"
            )
        for rank, disposition in enumerate(dispositions):
            if disposition.get("rank") != rank:
                raise RuntimeGraphQueryError(
                    "recycling candidate disposition rank differs"
                )
            status = disposition.get("status")
            if status not in {"retained", "ineligible", "truncated"}:
                raise RuntimeGraphQueryError(
                    "recycling candidate disposition status is invalid"
                )
            if status == "retained":
                operation_id = disposition.get("operation_id")
                if (
                    operation_id not in operations
                    or operation_id not in operation_ids
                ):
                    raise RuntimeGraphQueryError(
                        "retained recycling candidate has no operation"
                    )
            else:
                unresolved_id = disposition.get("unresolved_id")
                unresolved_row = unresolved.get(unresolved_id)
                if (
                    unresolved_row is None
                    or unresolved_row.get("subproblem_id")
                    != subproblem["id"]
                    or unresolved_row.get("candidate_id")
                    != disposition["candidate_id"]
                    or (
                        status == "truncated"
                        and unresolved_row.get("reason")
                        != disposition.get("reason")
                    )
                    or (
                        status == "ineligible"
                        and unresolved_row.get("reason")
                        not in {
                            "presentation-only-evidence",
                            "missing-execution-evidence",
                        }
                    )
                ):
                    raise RuntimeGraphQueryError(
                        "recycling candidate disposition differs from its "
                        "unresolved row"
                    )
                accounted_unresolved_ids.add(unresolved_id)
        retained_operation_ids = [
            disposition["operation_id"]
            for disposition in dispositions
            if disposition["status"] == "retained"
        ]
        if operation_ids != retained_operation_ids:
            raise RuntimeGraphQueryError(
                "recycling subproblem operation ids differ from retained "
                "candidate dispositions"
            )
        if page["truncated"]:
            page_unresolved = [
                row
                for row in result["unresolved"]
                if row.get("reason") == "max-consumers-per-target"
                and row.get("subproblem_id") == subproblem["id"]
                and row.get("candidate_id") is None
            ]
            if len(page_unresolved) != 1:
                raise RuntimeGraphQueryError(
                    "truncated recycling consumer page has no exact "
                    "unresolved row"
                )
            accounted_unresolved_ids.add(page_unresolved[0]["id"])

    admission_indexes: list[int] = []
    for operation in result["operations"]:
        source_id = operation.get("subproblem_id")
        candidate_id = operation.get("candidate_id")
        if source_id not in subproblems or candidate_id not in page_candidate_ids:
            raise RuntimeGraphQueryError(
                "recycling operation source candidate is unknown"
            )
        source = subproblems[source_id]
        if (
            operation.get("scope") != source["scope"]
            or operation.get("consumed_target") != source["target"]
        ):
            raise RuntimeGraphQueryError(
                "recycling operation differs from its consumed subproblem"
            )
        consumption = operation.get("consumption")
        relationship = (
            consumption.get("relationship")
            if isinstance(consumption, dict)
            else None
        )
        if (
            not isinstance(relationship, dict)
            or relationship.get("predicate") not in CONSUMING_PREDICATES
            or relationship.get("subject")
            != _record_id(operation.get("owner"), "recycling operation owner")
            or relationship.get("object")
            != _record_id(consumption.get("slot"), "consumption slot")
            or consumption.get("target") != source["target"]
        ):
            raise RuntimeGraphQueryError(
                "recycling operation is not an exact consuming occurrence"
            )
        retained_dispositions = [
            disposition
            for disposition in source["candidate_dispositions"]
            if disposition.get("status") == "retained"
            and disposition.get("operation_id") == operation["id"]
        ]
        if (
            len(retained_dispositions) != 1
            or retained_dispositions[0].get("candidate_id")
            != candidate_id
            or retained_dispositions[0].get("rank")
            != operation.get("candidate_rank")
        ):
            raise RuntimeGraphQueryError(
                "recycling operation differs from its retained candidate "
                "disposition"
            )
        matched_alternatives = consumption.get("matched_alternatives")
        if not isinstance(matched_alternatives, list):
            raise RuntimeGraphQueryError(
                "recycling operation omits exact matched alternatives"
            )
        identity_parts = (
            source_id,
            _record_id(operation["owner"], "recycling operation owner"),
            _record_id(
                relationship,
                "recycling operation consumption relationship",
            ),
            _record_id(
                consumption.get("slot"),
                "recycling operation consumption slot",
            ),
            *(
                _record_id(
                    alternative.get("relationship"),
                    "recycling operation matched alternative relationship",
                )
                for alternative in matched_alternatives
            ),
        )
        if (
            candidate_id
            != _stable_id("recycling-candidate:", *identity_parts)
            or operation["id"]
            != _stable_id("recycling-operation:", *identity_parts)
        ):
            raise RuntimeGraphQueryError(
                "recycling operation identity differs from its exact "
                "consuming occurrence"
            )
        for slot in operation.get("reusable_requirements", {}).get(
            "slots",
            (),
        ):
            if slot.get("relationship", {}).get("predicate") != "requires":
                raise RuntimeGraphQueryError(
                    "recycling reusable requirement is not requires"
                )
        for slot in operation.get("other_consumed_inputs", {}).get(
            "slots",
            (),
        ):
            if (
                slot.get("relationship", {}).get("predicate")
                not in CONSUMING_PREDICATES
            ):
                raise RuntimeGraphQueryError(
                    "recycling other input is not consumed"
                )
        for output in operation.get("outputs", ()):
            if (
                output.get("relationship", {}).get("predicate")
                not in PRODUCER_PREDICATES
            ):
                raise RuntimeGraphQueryError(
                    "recycling operation output is not produced"
                )
        admission_index = operation.get("admission_index")
        if type(admission_index) is not int:
            raise RuntimeGraphQueryError(
                "recycling operation has no admission index"
            )
        admission_indexes.append(admission_index)
    if sorted(admission_indexes) != list(range(len(operations))):
        raise RuntimeGraphQueryError(
            "recycling operation admission indexes are not contiguous"
        )
    fair_retained_ids: list[str] = []
    depths = sorted(
        {
            int(subproblem.get("depth", -1))
            for subproblem in result["subproblems"]
        }
    )
    for depth in depths:
        same_depth = sorted(
            (
                subproblem
                for subproblem in result["subproblems"]
                if subproblem.get("depth") == depth
            ),
            key=lambda row: (
                str(row["scope"]["profile"]),
                str(row["scope"]["physical_side"]),
                _record_kind(row["target"]),
                _record_id(row["target"]),
                row["id"],
            ),
        )
        max_rank = max(
            (
                len(subproblem["candidate_dispositions"])
                for subproblem in same_depth
            ),
            default=0,
        )
        for rank in range(max_rank):
            for subproblem in same_depth:
                dispositions = subproblem["candidate_dispositions"]
                if rank >= len(dispositions):
                    continue
                disposition = dispositions[rank]
                if disposition["status"] == "retained":
                    fair_retained_ids.append(disposition["operation_id"])
    observed_admission_ids = [
        row["id"]
        for row in sorted(
            result["operations"],
            key=lambda row: row["admission_index"],
        )
    ]
    if observed_admission_ids != fair_retained_ids:
        raise RuntimeGraphQueryError(
            "recycling operation admission is not fair within depth"
        )

    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in result["continuation_edges"]:
        source_id = edge.get("from_subproblem_id")
        target_id = edge.get("to_subproblem_id")
        operation_id = edge.get("operation_id")
        if (
            source_id not in subproblems
            or target_id not in subproblems
            or operation_id not in operations
            or operations[operation_id]["subproblem_id"] != source_id
        ):
            raise RuntimeGraphQueryError(
                "recycling continuation references an unknown endpoint"
            )
        alternative = edge.get("alternative")
        if (
            not isinstance(alternative, dict)
            or alternative.get("node") != subproblems[target_id]["target"]
            or edge.get("alternative_relationship_id")
            != _record_id(
                alternative.get("relationship"),
                "continuation alternative relationship",
            )
        ):
            raise RuntimeGraphQueryError(
                "recycling continuation target differs from its output"
            )
        operation = operations[operation_id]
        matched_outputs = [
            output
            for output in operation.get("outputs", ())
            if _record_id(output.get("slot"), "operation output slot")
            == edge.get("output_slot_id")
        ]
        if len(matched_outputs) != 1:
            raise RuntimeGraphQueryError(
                "recycling continuation has no exact operation output slot"
            )
        matched_alternatives = [
            item
            for item in matched_outputs[0]
            .get("alternatives", {})
            .get("items", ())
            if _record_id(
                item.get("relationship"),
                "operation output alternative relationship",
            )
            == edge.get("alternative_relationship_id")
        ]
        if (
            len(matched_alternatives) != 1
            or matched_alternatives[0] != alternative
        ):
            raise RuntimeGraphQueryError(
                "recycling continuation is not backed by an exact output "
                "alternative"
            )
        adjacency[source_id].append(target_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise RuntimeGraphQueryError(
                "recycling continuation graph is not a DAG"
            )
        if identifier in visited:
            return
        visiting.add(identifier)
        for child in adjacency.get(identifier, ()):
            visit(child)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in sorted(subproblems):
        visit(identifier)

    expected_root_sets: dict[str, set[str]] = {
        identifier: set() for identifier in subproblems
    }
    expected_depths: dict[str, int] = {
        identifier: 2**63 - 1 for identifier in subproblems
    }
    for root in expected_roots:
        expected_root_sets[root["subproblem_id"]].add(root["id"])
        expected_depths[root["subproblem_id"]] = 0
    changed = True
    while changed:
        changed = False
        for edge in sorted(
            result["continuation_edges"],
            key=lambda row: row["id"],
        ):
            source_id = edge["from_subproblem_id"]
            target_id = edge["to_subproblem_id"]
            merged = (
                expected_root_sets[target_id]
                | expected_root_sets[source_id]
            )
            if merged != expected_root_sets[target_id]:
                expected_root_sets[target_id] = merged
                changed = True
            candidate_depth = expected_depths[source_id] + 1
            if candidate_depth < expected_depths[target_id]:
                expected_depths[target_id] = candidate_depth
                changed = True
    for identifier, subproblem in subproblems.items():
        if (
            subproblem.get("root_ids")
            != sorted(expected_root_sets[identifier])
            or subproblem.get("depth") != expected_depths[identifier]
        ):
            raise RuntimeGraphQueryError(
                "recycling subproblem reachability differs from its DAG"
            )

    for closure in result["closures"]:
        classification = closure.get("classification")
        if classification not in (
            RECYCLING_CLOSURE_CLASSIFICATIONS
        ):
            raise RuntimeGraphQueryError(
                "recycling closure classification is invalid"
            )
        if closure.get("subproblem_id") not in subproblems:
            raise RuntimeGraphQueryError(
                "recycling closure references an unknown subproblem"
            )
        operation_id = closure.get("operation_id")
        if operation_id is not None:
            source_id = closure["subproblem_id"]
            if (
                operation_id not in operations
                or operations[operation_id]["subproblem_id"] != source_id
            ):
                raise RuntimeGraphQueryError(
                    "recycling closure references an unknown operation"
                )
            output_slot_id = closure.get("output_slot_id")
            alternative_id = closure.get(
                "alternative_relationship_id"
            )
            if output_slot_id is not None or alternative_id is not None:
                output_matches = [
                    output
                    for output in operations[operation_id].get("outputs", ())
                    if _record_id(
                        output.get("slot"),
                        "closure operation output slot",
                    )
                    == output_slot_id
                ]
                alternative_matches = [
                    item
                    for output in output_matches
                    for item in output.get("alternatives", {}).get(
                        "items",
                        (),
                    )
                    if _record_id(
                        item.get("relationship"),
                        "closure output alternative relationship",
                    )
                    == alternative_id
                ]
                omitted_marker = (
                    closure.get("classification") == "open-truncated"
                    and closure.get("reason")
                    == "max-output-alternatives-per-slot"
                )
                if (
                    len(output_matches) != 1
                    or (
                        not omitted_marker
                        and len(alternative_matches) != 1
                    )
                    or (
                        omitted_marker
                        and (
                            alternative_matches
                            or output_matches[0]
                            .get("alternatives", {})
                            .get("truncated")
                            is not True
                        )
                    )
                ):
                    raise RuntimeGraphQueryError(
                        "recycling closure is not backed by an operation "
                        "output alternative"
                    )
                if (
                    not omitted_marker
                    and closure.get("alternative") is not None
                    and closure["alternative"] != alternative_matches[0]
                ):
                    raise RuntimeGraphQueryError(
                        "recycling closure alternative differs from its "
                        "operation output"
                    )
        if classification in {
            "returns-final-target",
            "rejoins-retained-route",
            "forward-cycle",
        }:
            destination_scope = closure.get("destination_scope")
            destination_target = closure.get("destination_target")
            target = closure.get("target")
            match = closure.get("match")
            path = match.get("path") if isinstance(match, dict) else None
            if (
                not isinstance(destination_scope, dict)
                or not isinstance(destination_target, dict)
                or not isinstance(target, dict)
                or not isinstance(match, dict)
                or not isinstance(match.get("role"), str)
                or not match["role"]
                or not isinstance(path, list)
                or any(
                    not isinstance(edge, dict)
                    or edge.get("record_type") != "edge"
                    for edge in path
                )
            ):
                raise RuntimeGraphQueryError(
                    "closed recycling path omits its exact match proof"
                )
            current_id = _record_id(
                destination_target,
                "recycling closure destination",
            )
            for edge in path:
                if edge.get("subject") == current_id:
                    current_id = edge.get("object")
                elif edge.get("object") == current_id:
                    current_id = edge.get("subject")
                else:
                    raise RuntimeGraphQueryError(
                        "recycling closure match path is disconnected"
                    )
            if current_id != _record_id(
                target,
                "recycling closure output target",
            ):
                raise RuntimeGraphQueryError(
                    "recycling closure match path does not reach its output"
                )
            if classification == "forward-cycle":
                destination_id = closure.get(
                    "destination_subproblem_id"
                )
                if (
                    destination_id not in subproblems
                    or subproblems[destination_id]["scope"]
                    != destination_scope
                    or subproblems[destination_id]["target"]
                    != destination_target
                ):
                    raise RuntimeGraphQueryError(
                        "forward-cycle destination differs from its "
                        "ancestor subproblem"
                    )
        if closure.get("reason") in {
            "max-output-alternatives-per-slot",
            "max-visited-targets",
        }:
            matching_unresolved = [
                row
                for row in result["unresolved"]
                if row.get("reason") == closure.get("reason")
                and row.get("subproblem_id")
                == closure.get("subproblem_id")
                and row.get("operation_id")
                == closure.get("operation_id")
                and (
                    row.get("alternative", {})
                    .get("relationship", {})
                    .get("id")
                    == closure.get("alternative_relationship_id")
                )
            ]
            if len(matching_unresolved) != 1:
                raise RuntimeGraphQueryError(
                    "truncated recycling closure has no exact unresolved row"
                )
            accounted_unresolved_ids.add(matching_unresolved[0]["id"])
    for cycle in result["cycles"]:
        if (
            cycle.get("closure_id") not in closures
            or closures[cycle["closure_id"]].get("classification")
            != "forward-cycle"
            or cycle.get("from_subproblem_id") not in subproblems
            or cycle.get("to_ancestor_subproblem_id") not in subproblems
        ):
            raise RuntimeGraphQueryError(
                "recycling cycle differs from its forward-cycle closure"
            )
        queue = deque([cycle["to_ancestor_subproblem_id"]])
        seen = set(queue)
        reaches_source = False
        while queue:
            identifier = queue.popleft()
            if identifier == cycle["from_subproblem_id"]:
                reaches_source = True
                break
            for child in adjacency.get(identifier, ()):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        if not reaches_source:
            raise RuntimeGraphQueryError(
                "recycling cycle target is not an ancestor"
            )
    forward_cycle_closure_ids = {
        closure["id"]
        for closure in result["closures"]
        if closure["classification"] == "forward-cycle"
    }
    if {
        cycle["closure_id"] for cycle in result["cycles"]
    } != forward_cycle_closure_ids:
        raise RuntimeGraphQueryError(
            "recycling cycles differ from forward-cycle closures"
        )
    for leaf in result["terminal_leaves"]:
        closure_id = leaf.get("closure_id")
        if (
            closure_id not in closures
            or not str(closures[closure_id]["classification"]).startswith(
                "open-"
            )
            or leaf.get("classification")
            != closures[closure_id]["classification"]
        ):
            raise RuntimeGraphQueryError(
                "recycling terminal leaf differs from its open closure"
            )
    open_closure_ids = {
        closure["id"]
        for closure in result["closures"]
        if str(closure["classification"]).startswith("open-")
    }
    if {
        leaf["closure_id"] for leaf in result["terminal_leaves"]
    } != open_closure_ids:
        raise RuntimeGraphQueryError(
            "recycling terminal leaves differ from open closures"
        )
    if accounted_unresolved_ids != set(unresolved):
        raise RuntimeGraphQueryError(
            "recycling unresolved rows differ from retained state"
        )

    summary = result.get("summary")
    expected_summary = {
        "root_count": len(expected_roots),
        "consuming_candidate_count": consuming_total,
        "reusable_only_candidate_count": reusable_total,
        "retained_operation_count": len(operations),
        "visited_target_count": len(subproblems),
        "closure_count": len(closures),
        "terminal_count": len(result["terminal_leaves"]),
    }
    if summary != expected_summary:
        raise RuntimeGraphQueryError(
            "recycling summary differs from its retained DAG"
        )
    truncation = result.get("truncation")
    if (
        not isinstance(truncation, dict)
        or type(truncation.get("truncated")) is not bool
        or not isinstance(truncation.get("reasons"), list)
        or any(
            reason not in RECYCLING_TRUNCATION_REASONS
            for reason in truncation.get("reasons", ())
        )
        or truncation["reasons"]
        != [
            reason
            for reason in RECYCLING_TRUNCATION_REASONS
            if reason in set(truncation["reasons"])
        ]
        or truncation["truncated"] is not bool(truncation["reasons"])
        or not isinstance(truncation.get("limits"), dict)
    ):
        raise RuntimeGraphQueryError(
            "recycling truncation is inconsistent"
        )
    limits = truncation["limits"]
    expected_limit_keys = {
        "max_depth",
        "max_operations",
        "max_consumers_per_target",
        "max_output_alternatives_per_slot",
        "max_visited_targets",
    }
    if (
        set(limits) != expected_limit_keys
        or any(type(value) is not int for value in limits.values())
        or limits["max_depth"] < 0
        or any(
            limits[name] < 1
            for name in expected_limit_keys - {"max_depth"}
        )
        or limits["max_consumers_per_target"] > MAX_QUERY_PAGE_SIZE
        or len(operations) > limits["max_operations"]
        or len(subproblems) > limits["max_visited_targets"]
        or len(expected_roots) > limits["max_visited_targets"]
    ):
        raise RuntimeGraphQueryError(
            "recycling retained state exceeds its declared global limits"
        )
    for subproblem in result["subproblems"]:
        if (
            subproblem["consumer_page"]["limit"]
            != limits["max_consumers_per_target"]
            or (
                subproblem["operation_ids"]
                and subproblem["depth"] >= limits["max_depth"]
            )
        ):
            raise RuntimeGraphQueryError(
                "recycling subproblem exceeds its declared traversal limits"
            )
    if any(
        output.get("alternatives", {}).get("returned", 0)
        > limits["max_output_alternatives_per_slot"]
        for operation in result["operations"]
        for output in operation.get("outputs", ())
    ):
        raise RuntimeGraphQueryError(
            "recycling output page exceeds its declared alternative limit"
        )
    if any(
        not set(subproblem.get("root_ids", ())).issubset(root_ids)
        for subproblem in result["subproblems"]
    ):
        raise RuntimeGraphQueryError(
            "recycling subproblem contains an unknown root id"
        )
    derived_reasons: set[str] = set()
    if any(
        subproblem["consumer_page"]["truncated"]
        for subproblem in result["subproblems"]
    ):
        derived_reasons.add("max-consumers-per-target")
    for subproblem in result["subproblems"]:
        for disposition in subproblem["candidate_dispositions"]:
            if (
                disposition["status"] == "truncated"
                and disposition.get("reason")
                in {"max-depth", "max-operations"}
            ):
                derived_reasons.add(disposition["reason"])
    if any(
        output.get("alternatives", {}).get("truncated") is True
        for operation in result["operations"]
        for output in operation.get("outputs", ())
    ):
        derived_reasons.add("max-output-alternatives-per-slot")
    if any(
        closure.get("reason") == "max-visited-targets"
        for closure in result["closures"]
    ):
        derived_reasons.add("max-visited-targets")
    expected_reasons = [
        reason
        for reason in RECYCLING_TRUNCATION_REASONS
        if reason in derived_reasons
    ]
    if truncation["reasons"] != expected_reasons:
        raise RuntimeGraphQueryError(
            "recycling truncation reasons differ from retained state"
        )


def build_recycling_paths(
    reader: RuntimeGraphReader,
    roots: Iterable[RecyclingRootSeed],
    *,
    final_targets: Iterable[ScopedTarget] = (),
    route_targets: Iterable[RecyclingRouteTarget] = (),
    options: RecyclingOptions = RecyclingOptions(),
) -> dict[str, Any]:
    """Convenience wrapper for :class:`RuntimeGraphRecyclingQuery`."""

    return RuntimeGraphRecyclingQuery(reader).paths(
        roots,
        final_targets=final_targets,
        route_targets=route_targets,
        options=options,
    )
