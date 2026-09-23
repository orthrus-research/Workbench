#!/usr/bin/env python3

"""Bidirectional, mechanics-aware queries over a normalized runtime graph.

The low-level :mod:`runtime_graph_query` reader deliberately exposes exact
records.  This module adds the domain joins needed by producer, consumer, and
process-chain queries without flattening ingredient slots or confusing
presentation/consultation edges with execution evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import sys
from typing import Any, Iterable, Sequence

from workbench_atlas.runtime_graph_query import (
    NodeSelector,
    PageRequest,
    QueryPage,
    RuntimeGraphQueryError,
    RuntimeGraphReader,
    decode_record,
)


PRODUCER_PREDICATES = ("produces", "may_produce")
CONSUMING_PREDICATES = ("consumes", "may_consume")
REUSABLE_CONSUMER_PREDICATES = ("requires",)
CONSUMER_PREDICATES = (
    *CONSUMING_PREDICATES,
    *REUSABLE_CONSUMER_PREDICATES,
)
PRESENTATION_OWNER_KINDS = {"recipe_wrapper", "recipe_example"}
ALTERNATIVE_KINDS = {
    "ingredient",
    "block",
    "item_variant",
    "fluid",
    "fluid_variant",
    "ore_dictionary_key",
}
MATERIAL_IDENTITY_ROLES = {
    "effective_material_lookup",
    "effective_fluid_unifier_lookup",
    "material_fluid_storage_form",
}
MAX_MATCH_CLOSURE_NODES = 10_000
DIRECT_CONDITION_PREDICATES = (
    "has_constraint",
    "uses_energy",
    "produces_energy",
    "configured_by",
    "defined_by_resource",
    "spawns_in",
)
SLOT_CONDITION_PREDICATES = ("requires", "affects")


def _normalize_occurrence_predicates(
    direction: str,
    predicates: Iterable[str] | None,
) -> tuple[str, ...]:
    """Validate and canonically order one occurrence-predicate subset."""

    if direction == "producer":
        allowed = PRODUCER_PREDICATES
    elif direction == "consumer":
        allowed = CONSUMER_PREDICATES
    else:
        raise RuntimeGraphQueryError(
            f"unsupported runtime graph occurrence direction: {direction}"
        )
    if predicates is None:
        return allowed
    if isinstance(predicates, (str, bytes)):
        raise RuntimeGraphQueryError(
            "occurrence predicate filter must be an iterable of predicate names"
        )
    try:
        requested = tuple(predicates)
    except TypeError as exc:
        raise RuntimeGraphQueryError(
            "occurrence predicate filter must be an iterable of predicate names"
        ) from exc
    if not requested:
        raise RuntimeGraphQueryError(
            "occurrence predicate filter must contain at least one predicate"
        )
    if any(
        not isinstance(predicate, str) or not predicate
        for predicate in requested
    ):
        raise RuntimeGraphQueryError(
            "occurrence predicate filter values must be non-empty strings"
        )
    if len(set(requested)) != len(requested):
        raise RuntimeGraphQueryError(
            "occurrence predicate filter contains duplicate predicates"
        )
    unsupported = sorted(set(requested) - set(allowed))
    if unsupported:
        raise RuntimeGraphQueryError(
            f"{direction} occurrence predicate filter contains unsupported "
            "predicates: "
            + ", ".join(unsupported)
        )
    selected = set(requested)
    return tuple(predicate for predicate in allowed if predicate in selected)


def _json_tree(value: Any) -> Any:
    """Project immutable query containers to canonical-JSON container types."""

    if isinstance(value, dict):
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_tree(child) for child in value]
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeGraphQueryError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True, order=True)
class ProfileScope:
    """One normalized graph profile and physical-side pair."""

    profile: str
    physical_side: str

    def __post_init__(self) -> None:
        _required_text(self.profile, "profile scope profile")
        _required_text(self.physical_side, "profile scope physical side")

    def to_dict(self) -> dict[str, str]:
        return {
            "profile": self.profile,
            "physical_side": self.physical_side,
        }


@dataclass(frozen=True)
class GraphScope:
    """An explicit, deterministically ordered set of profile scopes."""

    scopes: tuple[ProfileScope, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scopes, tuple) or not self.scopes:
            raise RuntimeGraphQueryError(
                "graph scope must contain at least one profile scope"
            )
        if any(not isinstance(scope, ProfileScope) for scope in self.scopes):
            raise RuntimeGraphQueryError(
                "graph scope entries must be ProfileScope instances"
            )
        normalized = tuple(sorted(set(self.scopes)))
        if normalized != self.scopes:
            raise RuntimeGraphQueryError(
                "graph scopes must be unique and sorted by profile and physical side"
            )

    @classmethod
    def of(cls, *scopes: ProfileScope) -> GraphScope:
        return cls(tuple(sorted(set(scopes))))


@dataclass(frozen=True)
class ScopedTarget:
    """One exact target resolved inside one profile scope."""

    scope: ProfileScope
    node: dict[str, Any]


@dataclass(frozen=True)
class ScopedTargetResolution:
    """Exact targets plus requested profile scopes in which the key is absent."""

    selector: NodeSelector
    targets: tuple[ScopedTarget, ...]
    gaps: tuple[ProfileScope, ...]


@dataclass(frozen=True)
class TargetMatch:
    """One slot-matchable node reached by an explicit identity path."""

    node: dict[str, Any]
    path: tuple[dict[str, Any], ...]
    match_role: str


@dataclass(frozen=True)
class TargetMatchClosure:
    """A bounded target-to-slot-alternative identity closure."""

    target: ScopedTarget
    matches: tuple[TargetMatch, ...]
    truncated: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DomainQueryResult:
    """A scoped exact resolution and one deterministic occurrence page."""

    resolution: ScopedTargetResolution
    page: QueryPage

    def to_dict(self) -> dict[str, Any]:
        selector = self.resolution.selector
        return _json_tree({
            "target": {
                "selector": {
                    "key_kind": selector.key_kind,
                    "key_value": selector.key_value,
                    "kind": selector.kind,
                    "profile": selector.profile,
                    "physical_side": selector.physical_side,
                },
                "resolved": [target.node for target in self.resolution.targets],
                "profile_gaps": [
                    scope.to_dict() for scope in self.resolution.gaps
                ],
            },
            "page": {
                "items": list(self.page.items),
                "limit": self.page.limit,
                "offset": self.page.offset,
                "returned": self.page.returned,
                "total": self.page.total,
                "truncated": self.page.truncated,
            },
        })


@dataclass(frozen=True)
class _OccurrenceRef:
    target: ScopedTarget
    closure: TargetMatchClosure
    owner_relationship_id: str
    order: tuple[Any, ...]


def _attributes(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("attributes")
    return value if isinstance(value, dict) else {}


def alternative_semantics(relationship: dict[str, Any]) -> str:
    """Classify whether an ``accepts_alternative`` row is exhaustive evidence."""

    attributes = _attributes(relationship)
    if (
        attributes.get("alternative_kind") == "authoritative_match_domain"
        or attributes.get("match_domain") is True
    ):
        return "authoritative-match-domain"
    if (
        attributes.get("alternative_kind") == "public_representative"
        or attributes.get("representative_only") is True
    ):
        return "non-exhaustive-representative"
    if attributes.get("symbolic") is True:
        return "symbolic"
    return "exact"


def _ordinal(
    record: dict[str, Any],
    key: str,
) -> int:
    value = _attributes(record).get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return sys.maxsize


def _slot_ordinal(
    slot: dict[str, Any],
    relationship: dict[str, Any],
) -> int:
    """Prefer the slot's declared ordinal, falling back to its owner edge."""

    value = _ordinal(slot, "ordinal")
    return value if value != sys.maxsize else _ordinal(relationship, "ordinal")


def _node_scope(node: dict[str, Any]) -> ProfileScope:
    scope = node.get("scope")
    if not isinstance(scope, dict):
        raise RuntimeGraphQueryError("query database contains a node without scope")
    return ProfileScope(
        _required_text(scope.get("profile"), "node scope profile"),
        _required_text(
            scope.get("physical_side"),
            "node scope physical side",
        ),
    )


def _match_sort_key(match: TargetMatch) -> tuple[str, str, tuple[str, ...]]:
    return (
        str(match.node.get("kind", "")),
        str(match.node.get("id", "")),
        tuple(str(edge.get("id", "")) for edge in match.path),
    )


def _occurrence_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(item["scope"]["profile"]),
        str(item["scope"]["physical_side"]),
        str(item["owner"].get("kind", "")),
        str(item["owner"].get("id", "")),
        _slot_ordinal(
            item["slot"],
            item["owner_relationship"],
        ),
        str(item["owner_relationship"].get("predicate", "")),
        str(item["slot"].get("id", "")),
        str(item["owner_relationship"].get("id", "")),
    )


class RuntimeGraphDomainQuery:
    """Reusable producer/consumer joins over an open runtime graph reader."""

    def __init__(self, reader: RuntimeGraphReader):
        if not isinstance(reader, RuntimeGraphReader):
            raise RuntimeGraphQueryError(
                "domain query requires an open RuntimeGraphReader"
            )
        reader.connection
        self.reader = reader

    def _execute(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> Any:
        try:
            return self.reader.connection.execute(statement, parameters)
        except sqlite3.Error as exc:
            raise RuntimeGraphQueryError(
                f"cannot query runtime graph database: {exc}"
            ) from exc

    def resolve_targets(
        self,
        selector: NodeSelector,
        graph_scope: GraphScope | None = None,
    ) -> ScopedTargetResolution:
        """Resolve at most one exact target per profile scope.

        A semantic key legitimately appears in several normalized profiles.
        Cross-profile occurrences are returned independently; ambiguity is
        fail-closed only when more than one node matches inside the same scope.
        """

        if not isinstance(selector, NodeSelector):
            raise RuntimeGraphQueryError(
                "domain target query requires a NodeSelector"
            )
        if graph_scope is not None and not isinstance(graph_scope, GraphScope):
            raise RuntimeGraphQueryError(
                "domain target graph scope must be a GraphScope"
            )

        if graph_scope is None:
            matches = self.reader.resolve_exact(selector)
            grouped: dict[ProfileScope, list[dict[str, Any]]] = {}
            for node in matches:
                grouped.setdefault(_node_scope(node), []).append(node)
            gaps: tuple[ProfileScope, ...] = ()
            requested_scopes = tuple(sorted(grouped))
        else:
            grouped = {}
            missing: list[ProfileScope] = []
            requested_scopes = graph_scope.scopes
            for scope in requested_scopes:
                if selector.profile is not None and selector.profile != scope.profile:
                    raise RuntimeGraphQueryError(
                        "node selector profile conflicts with requested graph scope"
                    )
                if (
                    selector.physical_side is not None
                    and selector.physical_side != scope.physical_side
                ):
                    raise RuntimeGraphQueryError(
                        "node selector physical side conflicts with requested graph scope"
                    )
                scoped_selector = NodeSelector(
                    selector.key_kind,
                    selector.key_value,
                    kind=selector.kind,
                    profile=scope.profile,
                    physical_side=scope.physical_side,
                )
                scoped_matches = list(self.reader.resolve_exact(scoped_selector))
                if not scoped_matches:
                    missing.append(scope)
                else:
                    grouped[scope] = scoped_matches
            gaps = tuple(missing)

        targets: list[ScopedTarget] = []
        for scope in requested_scopes:
            scoped_matches = grouped.get(scope, [])
            if len(scoped_matches) > 1:
                raise RuntimeGraphQueryError(
                    "exact target selector is ambiguous within profile scope "
                    f"{scope.profile}/{scope.physical_side}: "
                    f"{selector.key_kind}={selector.key_value} matched "
                    f"{len(scoped_matches)} nodes"
                )
            if scoped_matches:
                targets.append(ScopedTarget(scope, scoped_matches[0]))
        return ScopedTargetResolution(
            selector=selector,
            targets=tuple(targets),
            gaps=gaps,
        )

    def _adjacent(
        self,
        scoped_target: ScopedTarget,
        node_id: str,
        predicate: str,
        *,
        outgoing: bool,
    ) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
        endpoint = "edge.object" if outgoing else "edge.subject"
        constraint = "edge.subject" if outgoing else "edge.object"
        rows = self._execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            f"JOIN nodes AS node ON node.id = {endpoint} "
            f"WHERE {constraint} = ? AND edge.predicate = ? "
            "AND edge.profile = ? AND edge.physical_side = ? "
            "AND node.profile = ? AND node.physical_side = ? "
            "ORDER BY edge.id, node.id",
            (
                node_id,
                predicate,
                scoped_target.scope.profile,
                scoped_target.scope.physical_side,
                scoped_target.scope.profile,
                scoped_target.scope.physical_side,
            ),
        ).fetchall()
        return tuple(
            (
                decode_record(encoded_edge, "edge"),
                decode_record(encoded_node, "node"),
            )
            for encoded_edge, encoded_node in rows
        )

    def machine_form_paths(
        self,
        scoped_machine: ScopedTarget,
    ) -> tuple[dict[str, Any], ...]:
        """Return every exact same-scope ``has_form`` path for a machine.

        This domain primitive deliberately does not decide which form roles or
        target kinds are admissible for construction.  Higher layers retain
        every observed path and apply an explicit versioned admission policy.
        """

        if not isinstance(scoped_machine, ScopedTarget):
            raise RuntimeGraphQueryError(
                "machine form query requires a ScopedTarget"
            )
        machine = scoped_machine.node
        if (
            not isinstance(machine, dict)
            or machine.get("record_type") != "node"
            or machine.get("kind") != "machine"
        ):
            raise RuntimeGraphQueryError(
                "machine form query requires an exact machine node"
            )
        machine_scope = machine.get("scope")
        if (
            not isinstance(machine_scope, dict)
            or machine_scope.get("profile")
            != scoped_machine.scope.profile
            or machine_scope.get("physical_side")
            != scoped_machine.scope.physical_side
        ):
            raise RuntimeGraphQueryError(
                "machine form target scope differs from its node record"
            )
        return tuple(
            {
                "scope": scoped_machine.scope.to_dict(),
                "machine": machine,
                "relationship": relationship,
                "form": form,
            }
            for relationship, form in self._adjacent(
                scoped_machine,
                _required_text(machine.get("id"), "machine form machine id"),
                "has_form",
                outgoing=True,
            )
        )

    def condition_candidate_paths(
        self,
        scoped_subject: ScopedTarget,
    ) -> tuple[dict[str, Any], ...]:
        """Return exact condition-shaped paths without interpreting them.

        Direct relationships and grouped slot paths are retained separately.
        This primitive does not decide whether a configuration, constraint,
        reusable requirement, effect, resource, or energy relation is an
        infrastructure requirement.
        """

        if not isinstance(scoped_subject, ScopedTarget):
            raise RuntimeGraphQueryError(
                "condition candidate query requires a ScopedTarget"
            )
        subject = scoped_subject.node
        if (
            not isinstance(subject, dict)
            or subject.get("record_type") != "node"
            or not isinstance(subject.get("id"), str)
            or not isinstance(subject.get("kind"), str)
        ):
            raise RuntimeGraphQueryError(
                "condition candidate query requires an exact node"
            )
        if _node_scope(subject) != scoped_subject.scope:
            raise RuntimeGraphQueryError(
                "condition candidate target scope differs from its node record"
            )
        matches = self.reader.resolve_exact(
            NodeSelector.by_id(
                str(subject["id"]),
                kind=str(subject["kind"]),
                profile=scoped_subject.scope.profile,
                physical_side=scoped_subject.scope.physical_side,
            )
        )
        if len(matches) != 1 or matches[0] != subject:
            raise RuntimeGraphQueryError(
                "condition candidate target differs from the open graph: "
                + str(subject["id"])
            )

        paths: list[dict[str, Any]] = []
        for predicate in DIRECT_CONDITION_PREDICATES:
            for relationship, condition in self._adjacent(
                scoped_subject,
                str(subject["id"]),
                predicate,
                outgoing=True,
            ):
                if (
                    _node_scope(relationship) != scoped_subject.scope
                    or _node_scope(condition) != scoped_subject.scope
                ):
                    raise RuntimeGraphQueryError(
                        "direct condition candidate path escaped its subject "
                        "scope: "
                        + str(subject["id"])
                    )
                paths.append(
                    {
                        "path_kind": "direct",
                        "scope": scoped_subject.scope.to_dict(),
                        "subject": subject,
                        "relationship": relationship,
                        "condition": condition,
                    }
                )

        for row in self.reader.recipe_slots(
            str(subject["id"]),
            SLOT_CONDITION_PREDICATES,
        ):
            relationship = row["relationship"]
            slot = row["slot"]
            alternatives = row["alternatives"]
            if (
                _node_scope(relationship) != scoped_subject.scope
                or _node_scope(slot) != scoped_subject.scope
                or any(
                    _node_scope(alternative["relationship"])
                    != scoped_subject.scope
                    or _node_scope(alternative["node"])
                    != scoped_subject.scope
                    for alternative in alternatives
                )
            ):
                raise RuntimeGraphQueryError(
                    "condition candidate slot path escaped its subject scope: "
                    + str(subject["id"])
                )
            paths.append(
                {
                    "path_kind": "slot",
                    "scope": scoped_subject.scope.to_dict(),
                    "subject": subject,
                    "relationship": relationship,
                    "slot": slot,
                    "alternatives": list(alternatives),
                }
            )

        paths.sort(
            key=lambda row: (
                0 if row["path_kind"] == "direct" else 1,
                str(row["relationship"].get("predicate", "")),
                str(row["relationship"].get("id", "")),
                str(
                    row.get("condition", row.get("slot", {})).get(
                        "id",
                        "",
                    )
                ),
            )
        )
        return tuple(paths)

    def target_match_closure(
        self,
        scoped_target: ScopedTarget,
        direction: str,
        max_nodes: int = MAX_MATCH_CLOSURE_NODES,
    ) -> TargetMatchClosure:
        """Expand only explicit identity relations that slot matching can use."""

        if not isinstance(scoped_target, ScopedTarget):
            raise RuntimeGraphQueryError(
                "target match closure requires a ScopedTarget"
            )
        if direction not in {"producer", "consumer"}:
            raise RuntimeGraphQueryError(
                "target match direction must be producer or consumer"
            )
        if (
            isinstance(max_nodes, bool)
            or not isinstance(max_nodes, int)
            or max_nodes < 1
        ):
            raise RuntimeGraphQueryError(
                "target match max_nodes must be a positive integer"
            )

        matches: dict[str, TargetMatch] = {}
        truncated = False

        def add(
            node: dict[str, Any],
            path: tuple[dict[str, Any], ...],
            role: str,
        ) -> bool:
            nonlocal truncated
            identifier = _required_text(node.get("id"), "matched node id")
            candidate = TargetMatch(node=node, path=path, match_role=role)
            previous = matches.get(identifier)
            if previous is not None:
                if _match_sort_key(candidate) < _match_sort_key(previous):
                    matches[identifier] = candidate
                return False
            if len(matches) >= max_nodes:
                truncated = True
                return False
            matches[identifier] = candidate
            return True

        target = scoped_target.node
        target_id = _required_text(target.get("id"), "target node id")
        target_kind = _required_text(target.get("kind"), "target node kind")
        add(target, (), "exact")

        def add_forms(
            owner_id: str,
            prefix_path: tuple[dict[str, Any], ...],
            role: str,
        ) -> list[TargetMatch]:
            added: list[TargetMatch] = []
            for edge, node in self._adjacent(
                scoped_target,
                owner_id,
                "has_form",
                outgoing=True,
            ):
                path = prefix_path + (edge,)
                add(node, path, role)
                added.append(TargetMatch(node, path, role))
            return added

        def add_variants(
            base: TargetMatch,
            role: str,
        ) -> None:
            if base.node.get("kind") not in {"item", "fluid"}:
                return
            for edge, node in self._adjacent(
                scoped_target,
                str(base.node["id"]),
                "has_variant",
                outgoing=True,
            ):
                add(node, base.path + (edge,), role)

        if target_kind in {"item", "fluid"}:
            add_variants(TargetMatch(target, (), "exact"), "base-variant")
        elif target_kind in {"material", "ore_prefix"}:
            form_role = (
                "material-form" if target_kind == "material" else "prefix-form"
            )
            for form in add_forms(target_id, (), form_role):
                add_variants(form, f"{form_role}-variant")

        if target_kind == "material":
            for edge, node in self._adjacent(
                scoped_target,
                target_id,
                "has_material",
                outgoing=False,
            ):
                if (
                    node.get("kind") in ALTERNATIVE_KINDS
                    and _attributes(edge).get("role") in MATERIAL_IDENTITY_ROLES
                ):
                    add(node, (edge,), "material-identity")

        if target_kind == "ore_dictionary_key":
            for edge, node in self._adjacent(
                scoped_target,
                target_id,
                "member_of_ore_dictionary",
                outgoing=False,
            ):
                add(node, (edge,), "ore-dictionary-member")

        if direction == "consumer":
            variant_matches = [
                match
                for match in tuple(matches.values())
                if match.node.get("kind") == "item_variant"
            ]
            for variant in variant_matches:
                for edge, node in self._adjacent(
                    scoped_target,
                    str(variant.node["id"]),
                    "member_of_ore_dictionary",
                    outgoing=True,
                ):
                    add(
                        node,
                        variant.path + (edge,),
                        "ore-dictionary-membership",
                    )

        return TargetMatchClosure(
            target=scoped_target,
            matches=tuple(sorted(matches.values(), key=_match_sort_key)),
            truncated=truncated,
            reasons=("max-match-nodes",) if truncated else (),
        )

    def _all_slot_alternatives(
        self,
        slot_id: str,
        scope: ProfileScope,
    ) -> tuple[dict[str, Any], ...]:
        rows = self._execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.object "
            "WHERE edge.subject = ? "
            "AND edge.predicate = 'accepts_alternative' "
            "AND edge.profile = ? AND edge.physical_side = ? "
            "AND node.profile = ? AND node.physical_side = ?",
            (
                slot_id,
                scope.profile,
                scope.physical_side,
                scope.profile,
                scope.physical_side,
            ),
        ).fetchall()
        alternatives = [
            {
                "relationship": decode_record(encoded_edge, "edge"),
                "node": decode_record(encoded_node, "node"),
            }
            for encoded_edge, encoded_node in rows
        ]
        for alternative in alternatives:
            alternative["alternative_semantics"] = alternative_semantics(
                alternative["relationship"]
            )
        alternatives.sort(
            key=lambda item: (
                _ordinal(item["relationship"], "alternative_ordinal"),
                str(item["node"].get("kind", "")),
                str(item["node"].get("id", "")),
                str(item["relationship"].get("id", "")),
            )
        )
        return tuple(alternatives)

    def _incoming_edge_nodes(
        self,
        object_id: str,
        predicate: str,
        scope: ProfileScope,
    ) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
        rows = self._execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.subject "
            "WHERE edge.object = ? AND edge.predicate = ? "
            "AND edge.profile = ? AND edge.physical_side = ? "
            "AND node.profile = ? AND node.physical_side = ? "
            "ORDER BY node.id, edge.id",
            (
                object_id,
                predicate,
                scope.profile,
                scope.physical_side,
                scope.profile,
                scope.physical_side,
            ),
        ).fetchall()
        return tuple(
            (
                decode_record(encoded_edge, "edge"),
                decode_record(encoded_node, "node"),
            )
            for encoded_edge, encoded_node in rows
        )

    def _incoming_reconciliations(
        self,
        object_id: str,
    ) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
        """Return the contract's explicit cross-profile presentation links."""

        rows = self._execute(
            "SELECT edge.json, node.json "
            "FROM edges AS edge "
            "JOIN nodes AS node ON node.id = edge.subject "
            "WHERE edge.object = ? AND edge.predicate = 'reconciles_to' "
            "AND node.kind IN ('recipe_wrapper', 'recipe_example', 'recipe_category') "
            "ORDER BY node.profile, node.physical_side, node.kind, node.id, edge.id",
            (object_id,),
        ).fetchall()
        return tuple(
            (
                decode_record(encoded_edge, "edge"),
                decode_record(encoded_node, "node"),
            )
            for encoded_edge, encoded_node in rows
        )

    def _recipe_mechanics(
        self,
        owner: dict[str, Any],
        scope: ProfileScope,
    ) -> dict[str, Any]:
        owner_id = str(owner["id"])
        active_memberships: list[dict[str, Any]] = []
        inactive_memberships: list[dict[str, Any]] = []
        execution: list[dict[str, Any]] = []
        consultation: list[dict[str, Any]] = []
        evidence_ids: set[str] = {owner_id}
        for relationship, recipe_map in self._incoming_edge_nodes(
            owner_id,
            "has_recipe",
            scope,
        ):
            membership = {
                "relationship": relationship,
                "recipe_map": recipe_map,
            }
            evidence_ids.update(
                (str(relationship["id"]), str(recipe_map["id"]))
            )
            if (
                scope.profile == "COMMON_FINAL_STATE"
                and _attributes(relationship).get("lookup_active") is True
            ):
                active_memberships.append(membership)
                for map_edge, machine in self._incoming_edge_nodes(
                    str(recipe_map["id"]),
                    "executes_recipe_map",
                    scope,
                ):
                    execution.append(
                        {
                            "recipe_membership": relationship,
                            "recipe_map": recipe_map,
                            "machine_relationship": map_edge,
                            "machine": machine,
                        }
                    )
                    evidence_ids.update(
                        (str(map_edge["id"]), str(machine["id"]))
                    )
                for map_edge, actor in self._incoming_edge_nodes(
                    str(recipe_map["id"]),
                    "consults_recipe_map",
                    scope,
                ):
                    consultation.append(
                        {
                            "recipe_membership": relationship,
                            "recipe_map": recipe_map,
                            "relationship": map_edge,
                            "actor": actor,
                        }
                    )
                    evidence_ids.update(
                        (str(map_edge["id"]), str(actor["id"]))
                    )
            else:
                inactive_memberships.append(membership)

        presentation: list[dict[str, Any]] = []
        for relationship, source in self._incoming_reconciliations(owner_id):
            presentation.append(
                {"relationship": relationship, "source": source}
            )
            evidence_ids.update((str(relationship["id"]), str(source["id"])))

        if execution:
            classification = "runtime-executable"
        elif active_memberships:
            classification = "lookup-active-without-executable-machine"
        elif inactive_memberships:
            classification = "category-only"
        else:
            classification = "unbound-recipe"
        return {
            "eligible": bool(active_memberships),
            "classification": classification,
            "active_recipe_memberships": tuple(active_memberships),
            "inactive_recipe_memberships": tuple(inactive_memberships),
            "execution": tuple(
                sorted(
                    execution,
                    key=lambda row: (
                        str(row["recipe_map"]["id"]),
                        str(row["machine"]["id"]),
                        str(row["machine_relationship"]["id"]),
                    ),
                )
            ),
            "consultation": tuple(
                sorted(
                    consultation,
                    key=lambda row: (
                        str(row["recipe_map"]["id"]),
                        str(row["actor"]["id"]),
                        str(row["relationship"]["id"]),
                    ),
                )
            ),
            "presentation": tuple(
                sorted(
                    presentation,
                    key=lambda row: (
                        str(row["source"]["id"]),
                        str(row["relationship"]["id"]),
                    ),
                )
            ),
            "evidence_ids": tuple(sorted(evidence_ids)),
        }

    def _procedural_mechanics(
        self,
        owner: dict[str, Any],
        scope: ProfileScope,
    ) -> dict[str, Any]:
        owner_id = str(owner["id"])
        execution: list[dict[str, Any]] = []
        consultation: list[dict[str, Any]] = []
        evidence_ids: set[str] = {owner_id}
        for predicate in ("governed_by_rule", "delegates_to"):
            for relationship, source in self._incoming_edge_nodes(
                owner_id,
                predicate,
                scope,
            ):
                source_kind = source.get("kind")
                if source_kind == "machine":
                    execution.append(
                        {
                            "relationship": relationship,
                            "machine": source,
                        }
                    )
                    evidence_ids.update(
                        (str(relationship["id"]), str(source["id"]))
                    )
                elif source_kind == "recipe_map":
                    for map_edge, machine in self._incoming_edge_nodes(
                        str(source["id"]),
                        "executes_recipe_map",
                        scope,
                    ):
                        execution.append(
                            {
                                "relationship": relationship,
                                "recipe_map": source,
                                "machine_relationship": map_edge,
                                "machine": machine,
                            }
                        )
                        evidence_ids.update(
                            (
                                str(relationship["id"]),
                                str(source["id"]),
                                str(map_edge["id"]),
                                str(machine["id"]),
                            )
                        )
                elif source_kind == "recipe":
                    recipe_context = self._recipe_mechanics(source, scope)
                    for row in recipe_context["execution"]:
                        execution.append(
                            {
                                "relationship": relationship,
                                "recipe": source,
                                **row,
                            }
                        )
                        evidence_ids.update(recipe_context["evidence_ids"])
        if owner.get("kind") == "process_rule":
            for relationship, recipe_map in self._adjacent(
                ScopedTarget(scope, owner),
                owner_id,
                "consults_recipe_map",
                outgoing=True,
            ):
                consultation.append(
                    {
                        "relationship": relationship,
                        "recipe_map": recipe_map,
                    }
                )
                evidence_ids.update(
                    (str(relationship["id"]), str(recipe_map["id"]))
                )
        return {
            "eligible": True,
            "classification": (
                "worldgen"
                if owner.get("kind") == "worldgen_deposit"
                else "procedural"
            ),
            "active_recipe_memberships": (),
            "inactive_recipe_memberships": (),
            "execution": tuple(
                sorted(
                    execution,
                    key=lambda row: (
                        str(row.get("machine", {}).get("id", "")),
                        str(row["relationship"]["id"]),
                    ),
                )
            ),
            "consultation": tuple(
                sorted(
                    consultation,
                    key=lambda row: (
                        str(row["recipe_map"]["id"]),
                        str(row["relationship"]["id"]),
                    ),
                )
            ),
            "presentation": (),
            "evidence_ids": tuple(sorted(evidence_ids)),
        }

    def _mechanics(
        self,
        owner: dict[str, Any],
        scope: ProfileScope,
    ) -> dict[str, Any]:
        kind = owner.get("kind")
        if kind == "recipe":
            return self._recipe_mechanics(owner, scope)
        if kind in {"recipe_rule", "process_rule", "worldgen_deposit"}:
            return self._procedural_mechanics(owner, scope)
        return {
            "eligible": False,
            "classification": (
                "presentation-only"
                if kind in PRESENTATION_OWNER_KINDS
                else "non-mechanical-owner"
            ),
            "active_recipe_memberships": (),
            "inactive_recipe_memberships": (),
            "execution": (),
            "consultation": (),
            "presentation": (),
            "evidence_ids": (str(owner.get("id", "")),),
        }

    def owner_mechanics(
        self,
        owner: dict[str, Any],
        scope: ProfileScope,
    ) -> dict[str, Any]:
        """Return the exact mechanics classification for one scoped owner row."""

        if not isinstance(owner, dict) or not isinstance(scope, ProfileScope):
            raise RuntimeGraphQueryError(
                "owner mechanics requires an owner record and ProfileScope"
            )
        if owner.get("record_type") != "node" or _node_scope(owner) != scope:
            raise RuntimeGraphQueryError(
                "owner mechanics record differs from its requested scope"
            )
        identifier = owner.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeGraphQueryError(
                "owner mechanics record has no stable identifier"
            )
        return _json_tree(self._mechanics(owner, scope))

    @staticmethod
    def _semantics(predicate: str) -> dict[str, Any]:
        if predicate == "requires":
            return {
                "direction": "input",
                "consumed": False,
                "reusable": True,
                "conditional": False,
                "guaranteed": True,
            }
        if predicate == "may_consume":
            return {
                "direction": "input",
                "consumed": True,
                "reusable": False,
                "conditional": True,
                "guaranteed": False,
            }
        if predicate == "consumes":
            return {
                "direction": "input",
                "consumed": True,
                "reusable": False,
                "conditional": False,
                "guaranteed": True,
            }
        return {
            "direction": "output",
            "consumed": False,
            "reusable": False,
            "conditional": predicate == "may_produce",
            "guaranteed": predicate == "produces",
        }

    def _occurrence_join(
        self,
        scoped_target: ScopedTarget,
        closure: TargetMatchClosure,
        predicates: tuple[str, ...],
    ) -> tuple[str, tuple[object, ...]] | None:
        alternative_ids = tuple(
            sorted(str(match.node["id"]) for match in closure.matches)
        )
        if not alternative_ids:
            return None
        alt_placeholders = ",".join("?" for _ in alternative_ids)
        predicate_placeholders = ",".join("?" for _ in predicates)
        statement = (
            " FROM edges AS alternative_edge "
            "JOIN nodes AS alternative ON alternative.id = alternative_edge.object "
            "JOIN nodes AS slot ON slot.id = alternative_edge.subject "
            "JOIN edges AS owner_edge ON owner_edge.object = slot.id "
            "JOIN nodes AS owner ON owner.id = owner_edge.subject "
            "WHERE alternative_edge.predicate = 'accepts_alternative' "
            f"AND alternative_edge.object IN ({alt_placeholders}) "
            f"AND owner_edge.predicate IN ({predicate_placeholders}) "
            "AND alternative_edge.profile = ? "
            "AND alternative_edge.physical_side = ? "
            "AND owner_edge.profile = ? "
            "AND owner_edge.physical_side = ? "
            "AND alternative.profile = ? "
            "AND alternative.physical_side = ? "
            "AND slot.profile = ? AND slot.physical_side = ? "
            "AND owner.profile = ? AND owner.physical_side = ? "
            "AND owner.kind IN "
            "('recipe', 'recipe_rule', 'process_rule', 'worldgen_deposit') "
            "AND (owner.kind != 'recipe' OR ("
            "owner.profile = 'COMMON_FINAL_STATE' AND EXISTS ("
            "SELECT 1 FROM edges AS membership "
            "WHERE membership.object = owner.id "
            "AND membership.predicate = 'has_recipe' "
            "AND membership.profile = owner.profile "
            "AND membership.physical_side = owner.physical_side "
            "AND json_extract("
            "membership.json, '$.attributes.lookup_active'"
            ") = 1)))"
        )
        parameters: tuple[object, ...] = (
            *alternative_ids,
            *predicates,
            scoped_target.scope.profile,
            scoped_target.scope.physical_side,
            scoped_target.scope.profile,
            scoped_target.scope.physical_side,
            scoped_target.scope.profile,
            scoped_target.scope.physical_side,
            scoped_target.scope.profile,
            scoped_target.scope.physical_side,
            scoped_target.scope.profile,
            scoped_target.scope.physical_side,
        )
        return statement, parameters

    @staticmethod
    def _sql_ordinal(value: object) -> int:
        return value if type(value) is int else sys.maxsize

    def _occurrence_refs(
        self,
        scoped_target: ScopedTarget,
        direction: str,
        closure: TargetMatchClosure,
        predicates: tuple[str, ...],
    ) -> tuple[_OccurrenceRef, ...]:
        query = self._occurrence_join(
            scoped_target,
            closure,
            predicates,
        )
        if query is None:
            return ()
        join, parameters = query
        rows = self._execute(
            "SELECT DISTINCT owner_edge.id, owner.kind, owner.id, "
            "json_extract(slot.json, '$.attributes.ordinal'), "
            "json_extract(owner_edge.json, '$.attributes.ordinal'), "
            "owner_edge.predicate, slot.id"
            + join,
            parameters,
        ).fetchall()
        result: list[_OccurrenceRef] = []
        for (
            owner_edge_id,
            owner_kind,
            owner_id,
            slot_ordinal,
            edge_ordinal,
            predicate,
            slot_id,
        ) in rows:
            declared = self._sql_ordinal(slot_ordinal)
            ordinal = (
                declared
                if declared != sys.maxsize
                else self._sql_ordinal(edge_ordinal)
            )
            result.append(
                _OccurrenceRef(
                    target=scoped_target,
                    closure=closure,
                    owner_relationship_id=str(owner_edge_id),
                    order=(
                        scoped_target.scope.profile,
                        scoped_target.scope.physical_side,
                        str(owner_kind),
                        str(owner_id),
                        ordinal,
                        str(predicate),
                        str(slot_id),
                        str(owner_edge_id),
                    ),
                )
            )
        return tuple(sorted(result, key=lambda row: row.order))

    def _occurrences(
        self,
        scoped_target: ScopedTarget,
        direction: str,
        *,
        predicates: Iterable[str] | None = None,
        closure: TargetMatchClosure | None = None,
        owner_relationship_ids: Sequence[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        normalized_predicates = _normalize_occurrence_predicates(
            direction,
            predicates,
        )
        if closure is None:
            closure = self.target_match_closure(scoped_target, direction)
        match_by_id = {
            str(match.node["id"]): match for match in closure.matches
        }
        query = self._occurrence_join(
            scoped_target,
            closure,
            normalized_predicates,
        )
        if query is None:
            return ()
        join, parameters = query
        selection = ""
        selection_parameters: tuple[object, ...] = ()
        if owner_relationship_ids is not None:
            selected_ids = tuple(sorted(set(owner_relationship_ids)))
            if not selected_ids:
                return ()
            placeholders = ",".join("?" for _ in selected_ids)
            selection = f" AND owner_edge.id IN ({placeholders})"
            selection_parameters = selected_ids
        rows = self._execute(
            "SELECT owner_edge.json, owner.json, slot.json, "
            "alternative_edge.json, alternative.json"
            + join
            + selection,
            (*parameters, *selection_parameters),
        ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for (
            encoded_owner_edge,
            encoded_owner,
            encoded_slot,
            encoded_alternative_edge,
            encoded_alternative,
        ) in rows:
            owner_edge = decode_record(encoded_owner_edge, "edge")
            owner = decode_record(encoded_owner, "node")
            slot = decode_record(encoded_slot, "node")
            alternative_edge = decode_record(encoded_alternative_edge, "edge")
            alternative = decode_record(encoded_alternative, "node")
            key = str(owner_edge["id"])
            item = grouped.get(key)
            if item is None:
                alternatives = self._all_slot_alternatives(
                    str(slot["id"]),
                    scoped_target.scope,
                )
                item = {
                    "scope": scoped_target.scope.to_dict(),
                    "target": scoped_target.node,
                    "match_closure": {
                        "truncated": closure.truncated,
                        "reasons": closure.reasons,
                    },
                    "owner_relationship": owner_edge,
                    "owner": owner,
                    "slot": slot,
                    "alternatives": alternatives,
                    "matched_alternatives": [],
                    "semantics": self._semantics(
                        str(owner_edge["predicate"])
                    ),
                    "mechanics": self._mechanics(
                        owner,
                        scoped_target.scope,
                    ),
                }
                grouped[key] = item
            match = match_by_id[str(alternative["id"])]
            item["matched_alternatives"].append(
                {
                    "relationship": alternative_edge,
                    "node": alternative,
                    "alternative_semantics": alternative_semantics(
                        alternative_edge
                    ),
                    "match_role": match.match_role,
                    "match_path": match.path,
                }
            )

        items = list(grouped.values())
        for item in items:
            item["matched_alternatives"] = tuple(
                sorted(
                    item["matched_alternatives"],
                    key=lambda alternative: (
                        _ordinal(
                            alternative["relationship"],
                            "alternative_ordinal",
                        ),
                        str(alternative["node"].get("kind", "")),
                        str(alternative["node"].get("id", "")),
                        str(alternative["relationship"].get("id", "")),
                    ),
                )
            )
            evidence_ids = set(item["mechanics"]["evidence_ids"])
            evidence_ids.update(
                (
                    str(item["target"]["id"]),
                    str(item["owner_relationship"]["id"]),
                    str(item["owner"]["id"]),
                    str(item["slot"]["id"]),
                )
            )
            for alternative in item["alternatives"]:
                evidence_ids.update(
                    (
                        str(alternative["relationship"]["id"]),
                        str(alternative["node"]["id"]),
                    )
                )
            for alternative in item["matched_alternatives"]:
                evidence_ids.update(
                    str(edge["id"]) for edge in alternative["match_path"]
                )
            item["evidence_ids"] = tuple(sorted(evidence_ids))
        items.sort(key=_occurrence_sort_key)
        return tuple(items)

    def producer_occurrences(
        self,
        scoped_target: ScopedTarget,
    ) -> tuple[dict[str, Any], ...]:
        """Return every mechanics-owner output-slot occurrence for a target."""

        return self._occurrences(scoped_target, "producer")

    def consumer_occurrences(
        self,
        scoped_target: ScopedTarget,
        *,
        predicates: Iterable[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return filtered mechanics-owner input-slot occurrences for a target."""

        return self._occurrences(
            scoped_target,
            "consumer",
            predicates=predicates,
        )

    def _query(
        self,
        selector: NodeSelector,
        direction: str,
        page: PageRequest,
        graph_scope: GraphScope | None,
        predicates: Iterable[str] | None = None,
    ) -> DomainQueryResult:
        if not isinstance(page, PageRequest):
            raise RuntimeGraphQueryError(
                "domain occurrence query requires a PageRequest"
            )
        normalized_predicates = _normalize_occurrence_predicates(
            direction,
            predicates,
        )
        resolution = self.resolve_targets(selector, graph_scope)
        references: list[_OccurrenceRef] = []
        for target in resolution.targets:
            closure = self.target_match_closure(target, direction)
            references.extend(
                self._occurrence_refs(
                    target,
                    direction,
                    closure,
                    normalized_predicates,
                )
            )
        references.sort(key=lambda row: row.order)
        total = len(references)
        selected_references = references[
            page.offset : page.offset + page.limit
        ]

        groups: dict[
            tuple[str, str, str],
            tuple[ScopedTarget, TargetMatchClosure, list[str]],
        ] = {}
        for reference in selected_references:
            target_key = (
                reference.target.scope.profile,
                reference.target.scope.physical_side,
                str(reference.target.node["id"]),
            )
            group = groups.get(target_key)
            if group is None:
                group = (reference.target, reference.closure, [])
                groups[target_key] = group
            group[2].append(reference.owner_relationship_id)

        occurrences: list[dict[str, Any]] = []
        for target, closure, relationship_ids in groups.values():
            occurrences.extend(
                self._occurrences(
                    target,
                    direction,
                    predicates=normalized_predicates,
                    closure=closure,
                    owner_relationship_ids=relationship_ids,
                )
            )
        occurrences.sort(key=_occurrence_sort_key)
        selected = tuple(occurrences)
        if len(selected) != len(selected_references):
            raise RuntimeGraphQueryError(
                "runtime graph occurrence page changed while being hydrated"
            )
        return DomainQueryResult(
            resolution=resolution,
            page=QueryPage(
                items=selected,
                total=total,
                limit=page.limit,
                offset=page.offset,
                truncated=page.offset + len(selected) < total,
            ),
        )

    def producers(
        self,
        selector: NodeSelector,
        page: PageRequest = PageRequest(),
        graph_scope: GraphScope | None = None,
    ) -> DomainQueryResult:
        """Find output-slot occurrences whose explicit alternatives match target."""

        return self._query(selector, "producer", page, graph_scope)

    def consumers(
        self,
        selector: NodeSelector,
        page: PageRequest = PageRequest(),
        graph_scope: GraphScope | None = None,
        *,
        predicates: Iterable[str] | None = None,
    ) -> DomainQueryResult:
        """Find a filtered page of matching input-slot occurrences."""

        return self._query(
            selector,
            "consumer",
            page,
            graph_scope,
            predicates,
        )


def find_producers(
    reader: RuntimeGraphReader,
    selector: NodeSelector,
    page: PageRequest = PageRequest(),
    graph_scope: GraphScope | None = None,
) -> DomainQueryResult:
    """Convenience wrapper for one producer query."""

    return RuntimeGraphDomainQuery(reader).producers(
        selector,
        page=page,
        graph_scope=graph_scope,
    )


def find_consumers(
    reader: RuntimeGraphReader,
    selector: NodeSelector,
    page: PageRequest = PageRequest(),
    graph_scope: GraphScope | None = None,
    *,
    predicates: Iterable[str] | None = None,
) -> DomainQueryResult:
    """Convenience wrapper for one predicate-filtered consumer query."""

    return RuntimeGraphDomainQuery(reader).consumers(
        selector,
        page=page,
        graph_scope=graph_scope,
        predicates=predicates,
    )
